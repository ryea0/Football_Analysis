"""dashboard 查询层：只读 SQL + fa 纯函数复用。

设计：docs/superpowers/specs/2026-09-04-web-dashboard-design.md（§2/§5）。
本模块不 import streamlit（缓存包在 loaders.py）；不碰任何有副作用的
fa 模块（写库 / hermes / 网络）；指标公式一律复用 fa.backtest.*。
"""
import json
import sqlite3
from datetime import timedelta, timezone
from pathlib import Path

import pandas as pd

from fa.backtest.metrics import by_group, calibration, evaluate, fetch_predictions
from fa.backtest.simulate import (ODDS_MAX, ODDS_MIN, candidates, simulate_flat,
                                  simulate_kelly)
from fa.config import db_path

# 策略轨排序参照（单一事实源：后端 STRATEGIES 常量）；若 import 失败（如未装
# fa 核心依赖），fallback 到固定顺序，避免看板因缺依赖崩。
try:
    from fa.pipeline.value import STRATEGIES as _STRATEGIES
except Exception:  # pragma: no cover
    _STRATEGIES = ("model_only", "model_persona", "model_persona_nokb")

# 北京日界（spec §9.6 全项目口径；固定 +8 无夏令时）——日期过滤/选项统一用它
_BEIJING = timezone(timedelta(hours=8))


def _median(vals: list[float]) -> float | None:
    """纯 Python 中位数（避免 numpy/pandas 依赖 + 小列表零开销）。"""
    if not vals:
        return None
    n = len(vals)
    if n % 2 == 1:
        return float(vals[n // 2])
    return float((vals[n // 2 - 1] + vals[n // 2]) / 2.0)


# 全站表列名双语映射（2026-09-04 用户实测反馈：推荐表列名全英文——补全为
# 中英并列）。页面在**展示前**统一 ``df.rename(columns=COL_BILINGUAL)``；
# 过滤/透视仍用原列名（先滤后改名）。完备性由
# test_col_bilingual_covers_all_table_columns 钉死：查询输出的每一列都必须
# 在此映射内，漏译即测试红。
COL_BILINGUAL = {
    # 通用键
    "id": "编号 id", "status": "状态 status", "mode": "模式 mode",
    "type": "类型 type", "phase": "相位 phase", "strategy": "策略 strategy",
    "market": "市场 mkt", "league": "联赛 league",
    "created_at": "创建 created", "started_at": "启动 started",
    "finished_at": "完成 finished", "kickoff_utc": "开赛 kickoff",
    "placed_at": "落注 placed", "settled_at": "结算 settled",
    "bookmaker": "庄家 book", "home": "主队 home", "away": "客队 away",
    "event_key": "事件 event", "source": "来源 source",
    "name": "名称 name", "first_seen": "首见 first seen",
    # 推荐列
    "model_p": "模型概率 model_p", "market_p": "市场概率 market_p",
    "best_odds": "最优价 best odds", "edge": "优势 edge", "ev": "EV",
    "kelly_stake_frac": "kelly 仓位 kelly", "verdict": "判决 verdict",
    "confidence_delta": "信心增量 Δconf", "final_stake_frac": "终仓 final stake",
    "key_factors": "关键因素 factors", "report_md": "点评 report",
    # 注列
    "odds_taken": "拿价 odds", "stake": "注金 stake",
    "return_amt": "回报 return", "pnl": "盈亏 pnl",
    "closing_odds": "收盘 close", "closing_source": "基准 source", "clv": "CLV",
    # runs 展开键
    "credits_before": "额度前 credits before",
    "credits_after": "额度后 credits after", "summary": "摘要 summary",
    "fixtures": "场次 fixtures", "aligned": "对齐 aligned", "bets": "落注 bets",
    "degraded": "降级 degraded", "degraded_reasons": "降级原因 reasons",
    # 页5 回测指标键（evaluate）
    "n": "样本 n", "model_ll": "模型LL model_ll", "market_ll": "市场LL market_ll",
    "ratio": "比值 ratio", "degradation_pct": "劣化% degr.",
    "model_brier": "模型Brier", "market_brier": "市场Brier",
    "cal_home": "校准cal_home",
    # 页6 校准桶键
    "lo": "下界 lo", "hi": "上界 hi", "avg_p": "平均概率 avg_p",
    "emp": "实际频率 emp",
    # 页7 模拟键（simulate_flat / simulate_kelly）
    "staked": "投注额 staked", "returned": "回收 returned", "roi": "ROI",
    "final_bankroll": "终值 final", "max_drawdown_pct": "最大回撤 max DD",
    # v2 分解表新增列（2026-09-07）
    "win_rate": "胜率 win rate", "avg_odds": "平均赔率 avg odds",
    "settled_date": "结算日 settled", "pnl": "盈亏 pnl",
    "n_settled": "已结算 settled", "clv_median": "CLV中位 clv med",
}


def connect_ro(path: Path | None = None) -> sqlite3.Connection:
    """只读连接（mode=ro）：dashboard 的任何 bug 都写不了库。

    WAL 下只读读者与管线写入互不阻塞；库不存在时给出可操作的报错
    （页面层捕获后提示先 `fa init`）。
    """
    p = Path(path) if path else db_path()
    if not p.exists():
        raise FileNotFoundError(f"数据库不存在：{p}（先运行 uv run fa init）")
    conn = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def b_summary(conn: sqlite3.Connection) -> dict:
    """页1 总览：meta 两键 + paper 注汇总 + 额度序列 + 分轨汇总。

    pnl/staked 只计已结算注（pending 不进任何一侧；void 排除——零损益）；
    meta 键缺失返回 None（页面显示「未记录」而非 0，设计 §6）。
    ``by_strategy`` 按策略轨分账：bankroll 取 meta 分轨键，其余指标从
    bets 聚合，轨序同 ``_STRATEGIES``，缺数据的轨补零值。
    """
    def meta_float(key: str) -> float | None:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return None if row is None else float(row["value"])

    agg = conn.execute("""
        SELECT COUNT(*) AS n,
               COALESCE(SUM(status = 'pending'), 0) AS n_pending,
               COALESCE(SUM(CASE WHEN status IN ('won', 'lost')
                                 THEN COALESCE(return_amt, 0) - stake ELSE 0 END), 0.0) AS pnl,
               COALESCE(SUM(CASE WHEN status IN ('won', 'lost')
                                 THEN stake ELSE 0 END), 0.0) AS staked
        FROM bets WHERE mode = 'paper'""").fetchone()
    quota = conn.execute("""
        SELECT id, type, phase, status, started_at, credits_before, credits_after
        FROM runs ORDER BY id""").fetchall()

    # 分轨汇总：bankroll 取 meta 分轨键，其余从 bets 按 strategy 聚合
    by_strat_df = pd.read_sql_query("""
        SELECT r.strategy AS strategy,
               COUNT(*) AS n,
               COALESCE(SUM(b.status = 'pending'), 0) AS n_pending,
               COALESCE(SUM(b.status IN ('won', 'lost')), 0) AS n_settled,
               COALESCE(SUM(b.status = 'won'), 0) AS n_won,
               COALESCE(SUM(CASE WHEN b.status IN ('won', 'lost')
                                 THEN COALESCE(b.return_amt, 0) - b.stake ELSE 0 END), 0.0) AS pnl,
               COALESCE(SUM(CASE WHEN b.status IN ('won', 'lost')
                                 THEN b.stake ELSE 0 END), 0.0) AS staked
        FROM bets b JOIN recommendations r ON r.id = b.recommendation_id
        WHERE b.mode = 'paper'
        GROUP BY r.strategy""", conn)

    by_strategy: dict = {}
    for strat in _STRATEGIES:
        row = by_strat_df[by_strat_df["strategy"] == strat]
        if len(row):
            r = row.iloc[0]
            n_settled = int(r["n_settled"])
            staked = float(r["staked"])
            n_won = int(r["n_won"])
            win_rate = (n_won / n_settled) if n_settled else None
            roi = (float(r["pnl"]) / staked) if staked else None
        else:
            n_settled = 0
            win_rate = None
            roi = None

        # CLV 中位：该轨所有已结算且有 clv 的注
        clv_row = conn.execute("""
            SELECT b.clv FROM bets b
            JOIN recommendations r ON r.id = b.recommendation_id
            WHERE b.mode = 'paper' AND r.strategy = ?
              AND b.status IN ('won', 'lost') AND b.clv IS NOT NULL
            ORDER BY b.clv""", (strat,)).fetchall()
        clv_vals = [r["clv"] for r in clv_row]
        clv_median = _median(clv_vals)

        by_strategy[strat] = {
            "bankroll": meta_float(f"paper_bankroll:{strat}"),
            "n": int(row.iloc[0]["n"]) if len(row) else 0,
            "n_settled": n_settled,
            "n_pending": int(row.iloc[0]["n_pending"]) if len(row) else 0,
            "win_rate": win_rate,
            "roi": roi,
            "clv_median": clv_median,
        }

    return {"bankroll": meta_float("paper_bankroll"),
            "quota_remaining": meta_float("odds_quota_remaining"),
            "n_bets": agg["n"], "n_pending": agg["n_pending"],
            "pnl": agg["pnl"], "staked": agg["staked"],
            "quota_series": [dict(r) for r in quota],
            "by_strategy": by_strategy}


def b_recommendations(conn: sqlite3.Connection) -> pd.DataFrame:
    """页2 上表：推荐全维度 + fixture 队名/开球时间（未对齐队名为 NULL）。"""
    return pd.read_sql_query("""
        SELECT r.id, r.created_at, r.phase, r.strategy, r.market,
               r.model_p, r.market_p, r.best_odds, r.bookmaker,
               r.edge, r.ev, r.kelly_stake_frac,
               r.verdict, r.confidence_delta, r.final_stake_frac,
               f.league, f.kickoff_utc, f.event_key,
               th.name AS home, ta.name AS away
        FROM recommendations r
        JOIN fixtures f ON f.id = r.fixture_id
        LEFT JOIN teams th ON th.id = f.home_team_id
        LEFT JOIN teams ta ON ta.id = f.away_team_id
        ORDER BY r.created_at DESC, r.id DESC
    """, conn)


def b_bets(conn: sqlite3.Connection) -> pd.DataFrame:
    """页2 下表：paper/live 注明细 + 推荐维度（market/strategy 等）+ 队名。

    ``closing_source`` 透出 CLV 实际所用基准（spec §7.3 基准链：pinnacle /
    betfair / NULL）；``pnl`` 盈亏三态——won/lost = return−stake、void = 0
    （零损益）、pending = NaN（在途无账）。
    """
    df = pd.read_sql_query("""
        SELECT b.id, b.mode, b.placed_at, b.status, b.bookmaker,
               b.odds_taken, b.stake, b.settled_at, b.return_amt,
               b.closing_odds, b.closing_source, b.clv,
               r.strategy, r.phase, r.market, r.model_p, r.market_p,
               r.edge, r.ev,
               f.kickoff_utc, th.name AS home, ta.name AS away
        FROM bets b
        JOIN recommendations r ON r.id = b.recommendation_id
        JOIN fixtures f ON f.id = r.fixture_id
        LEFT JOIN teams th ON th.id = f.home_team_id
        LEFT JOIN teams ta ON ta.id = f.away_team_id
        ORDER BY b.placed_at DESC, b.id DESC
    """, conn)
    if df.empty:
        df["pnl"] = pd.Series(dtype=float)
        return df
    settled = df["status"].isin(["won", "lost"])
    df["pnl"] = (df["return_amt"].fillna(0.0) - df["stake"]).where(settled, 0.0)
    df.loc[df["status"] == "pending", "pnl"] = float("nan")
    return df


def by_date(df: pd.DataFrame, column: str, choice) -> pd.DataFrame:
    """页 2/4 的日期过滤纯函数：``choice`` 为 None /「全部…」→ 全量；否则按
    ``column``（ISO 串）的**北京日**（spec §9.6 全项目口径）命中。

    日界按北京时间而非存储的 UTC 串——实害案例（2026-09-04）：4 注结算时间
    ``2026-09-03T22:09:33Z`` 是北京 09-04 06:09，UTC 前缀桶会把它分到 09-03，
    「结算日 09-04」找不到当天收支。页内 selectbox 选项由 :func:`date_choices`
    生成，口径同源（「全部」前缀即全量，容「全部 All」）。"""
    if choice is None or str(choice).startswith("全部"):
        return df
    if column not in df.columns or df.empty:
        return df.iloc[0:0]
    return df[_bj_days(df, column) == choice]


def date_choices(df: pd.DataFrame, column: str) -> list[str]:
    """``column`` 的去重**北京日**选项（降序——最近在前），供 selectbox。"""
    if column not in df.columns or df.empty:
        return []
    return sorted(_bj_days(df, column).dropna().unique(), reverse=True)


def _bj_days(df: pd.DataFrame, column: str) -> pd.Series:
    """ISO 串列（带 Z 或裸——裸按 UTC）→ 北京日 ``YYYY-MM-DD``；解析失败 NaT。"""
    parsed = pd.to_datetime(df[column], errors="coerce", utc=True)
    return parsed.dt.tz_convert(_BEIJING).dt.strftime("%Y-%m-%d")


def b_ab_tracks(conn: sqlite3.Connection) -> dict:
    """页3：三轨注数/ROI/CLV 中位数 + 按结算日累计 P&L（§6.6/§12.3 + C 线对照）。

    策略轨从数据动态发现，排序参照后端 STRATEGIES（单一事实源）；空库
    仍返回三轨的零值骨架（保持 UI 三列布局稳定）。
    样本量语义交给页面展示（进度条 + 警示）；这里只算数，不判结论。
    """
    df = pd.read_sql_query("""
        SELECT r.strategy AS strategy, b.status, b.stake, b.return_amt,
               b.clv, b.settled_at
        FROM bets b JOIN recommendations r ON r.id = b.recommendation_id
        WHERE b.mode = 'paper'""", conn)
    out: dict = {}
    # 以 STRATEGIES 为骨架：缺数据的轨补零值，保证 UI 三列稳定
    present = set(df["strategy"].unique()) if not df.empty else set()
    strats = [s for s in _STRATEGIES if s in present] + \
             [s for s in _STRATEGIES if s not in present]
    for strat in strats:
        g = df[df["strategy"] == strat] if not df.empty else df.iloc[0:0]
        settled = g[g["status"].isin(["won", "lost"])].sort_values("settled_at")
        pnl = float(settled["return_amt"].fillna(0).sum() - settled["stake"].sum()) if len(settled) else 0.0
        staked = float(settled["stake"].sum()) if len(settled) else 0.0
        out[strat] = {
            "n": int(len(g)),
            "n_settled": int(len(settled)),
            "roi": (pnl / staked) if staked else None,
            "clv_median": float(g["clv"].median()) if g["clv"].notna().any() else None,
            "cum": {"dates": list(settled["settled_at"]),
                    "pnl": list((settled["return_amt"].fillna(0) - settled["stake"]).cumsum())},
        }
    return out


def b_breakdown(conn: sqlite3.Connection, dim: str) -> pd.DataFrame:
    """按 dim 维度聚合 paper 注的分轨统计（页2 盈亏分解面板）。

    dim ∈ {"market", "league", "settled_date"}
    返回列：strategy, <dim>, n, n_settled, win_rate, roi, clv_median, avg_odds

    口径与 ``b_ab_tracks`` / ``settle_paper_bets`` 一致：
    - settled = status IN ('won', 'lost')（void 不计入胜率/ROI 分母）
    - ROI = pnl_sum / staked_sum（staked = 已结算注的 stake 合计）
    - CLV 中位 = 已结算注（有 closing_odds 的）的 CLV 中位数
    - settled_date 维度用**北京日**（spec §9.6 全项目口径）
    """
    if dim not in ("market", "league", "settled_date"):
        raise ValueError(f"b_breakdown: dim 必须是 market/league/settled_date，got {dim!r}")

    if dim == "market":
        dim_col = "r.market"
        dim_alias = "market"
    elif dim == "league":
        dim_col = "f.league"
        dim_alias = "league"
    else:  # settled_date
        dim_col = "DATE(b.settled_at)"
        dim_alias = "settled_date"

    df = pd.read_sql_query(f"""
        SELECT r.strategy AS strategy,
               {dim_col} AS {dim_alias},
               COUNT(*) AS n,
               COALESCE(SUM(b.status IN ('won', 'lost')), 0) AS n_settled,
               COALESCE(SUM(b.status = 'won'), 0) AS n_won,
               COALESCE(SUM(CASE WHEN b.status IN ('won', 'lost')
                                 THEN COALESCE(b.return_amt, 0) - b.stake ELSE 0 END), 0.0) AS pnl,
               COALESCE(SUM(CASE WHEN b.status IN ('won', 'lost')
                                 THEN b.stake ELSE 0 END), 0.0) AS staked,
               COALESCE(AVG(CASE WHEN b.status IN ('won', 'lost')
                                 THEN b.odds_taken END), 0.0) AS avg_odds
        FROM bets b
        JOIN recommendations r ON r.id = b.recommendation_id
        JOIN fixtures f ON f.id = r.fixture_id
        WHERE b.mode = 'paper'
        GROUP BY r.strategy, {dim_alias}
        ORDER BY r.strategy, {dim_alias}""", conn)

    if df.empty:
        # 空库契约：返回空 DataFrame 但列齐
        return pd.DataFrame(columns=["strategy", dim_alias, "n", "n_settled",
                                     "win_rate", "roi", "clv_median", "avg_odds",
                                     "pnl"])

    # 衍生列：胜率、ROI
    df["win_rate"] = df.apply(
        lambda r: r["n_won"] / r["n_settled"] if r["n_settled"] else None, axis=1)
    df["roi"] = df.apply(
        lambda r: r["pnl"] / r["staked"] if r["staked"] else None, axis=1)

    # CLV 中位：按 strategy + dim 维度算
    clv_df = pd.read_sql_query(f"""
        SELECT r.strategy AS strategy,
               {dim_col} AS {dim_alias},
               b.clv
        FROM bets b
        JOIN recommendations r ON r.id = b.recommendation_id
        JOIN fixtures f ON f.id = r.fixture_id
        WHERE b.mode = 'paper'
          AND b.status IN ('won', 'lost')
          AND b.clv IS NOT NULL
        ORDER BY r.strategy, {dim_alias}, b.clv""", conn)

    if not clv_df.empty:
        clv_med = clv_df.groupby(["strategy", dim_alias])["clv"].median().reset_index()
        clv_med.rename(columns={"clv": "clv_median"}, inplace=True)
        df = df.merge(clv_med, on=["strategy", dim_alias], how="left")
    else:
        df["clv_median"] = None

    # settled_date 维度：UTC 日转北京日
    if dim == "settled_date" and not df.empty and df["settled_date"].notna().any():
        # SQL DATE() 返回 UTC 日期串；转北京日用 _bj_days
        # （这里只有日期没有时间，先用 UTC 日期近似——实际上
        # 结算事件发生在 UTC 某日，其北京日可能偏移。精确起见我们
        # 重新用 settled_at 时间戳算北京日。）
        # 注：上面 SQL 用 DATE(b.settled_at) 聚合是按 UTC 日桶，
        # 这与 _bj_days 口径不一致。修正：重新按北京日聚合。
        df = _breakdown_by_beijing_day(conn)
        dim_alias = "settled_date"

    # 策略轨排序（参照 STRATEGIES）
    df["strategy"] = pd.Categorical(df["strategy"], categories=list(_STRATEGIES),
                                    ordered=True)
    df = df.sort_values(["strategy", dim_alias]).reset_index(drop=True)
    df["strategy"] = df["strategy"].astype(str)

    # 输出列对齐
    keep = ["strategy", dim_alias, "n", "n_settled", "win_rate", "roi",
            "clv_median", "avg_odds", "pnl"]
    return df[keep]


def _breakdown_by_beijing_day(conn: sqlite3.Connection) -> pd.DataFrame:
    """settled_date 维度的北京日版本（b_breakdown 内部调用）。

    从数据库读取全部已结算 paper 注的 settled_at（带时间），
    在 Python 侧转北京日再聚合——避免 SQL 侧 DATE() 按 UTC 日桶与
    全站北京日口径不一致。
    """
    df = pd.read_sql_query("""
        SELECT r.strategy AS strategy, b.status, b.stake, b.return_amt,
               b.clv, b.odds_taken, b.settled_at
        FROM bets b
        JOIN recommendations r ON r.id = b.recommendation_id
        WHERE b.mode = 'paper'""", conn)

    if df.empty:
        return pd.DataFrame(columns=["strategy", "settled_date", "n", "n_settled",
                                     "win_rate", "roi", "clv_median", "avg_odds"])

    df["settled_date"] = _bj_days(df, "settled_at")
    # 未结算的注 settled_at 为空 → 日期为 NaT → 排除
    df = df[df["settled_date"].notna() & df["settled_at"].notna()].copy()

    if df.empty:
        return pd.DataFrame(columns=["strategy", "settled_date", "n", "n_settled",
                                     "win_rate", "roi", "clv_median", "avg_odds"])

    def agg(g):
        settled = g[g["status"].isin(["won", "lost"])]
        n_settled = len(settled)
        n_won = int((settled["status"] == "won").sum())
        staked = float(settled["stake"].sum()) if n_settled else 0.0
        pnl = float((settled["return_amt"].fillna(0) - settled["stake"]).sum()) if n_settled else 0.0
        clv_vals = sorted(settled["clv"].dropna().tolist())
        return pd.Series({
            "n": len(g),
            "n_settled": n_settled,
            "win_rate": n_won / n_settled if n_settled else None,
            "roi": pnl / staked if staked else None,
            "clv_median": _median(clv_vals),
            "avg_odds": float(settled["odds_taken"].mean()) if n_settled else 0.0,
            "pnl": pnl,
        })

    result = df.groupby(["strategy", "settled_date"]).apply(
        agg, include_groups=False).reset_index()
    # 输出列对齐（与 market/league 维度一致）
    keep = ["strategy", "settled_date", "n", "n_settled", "win_rate", "roi",
            "clv_median", "avg_odds", "pnl"]
    result = result[[c for c in keep if c in result.columns]]
    return result


def b_pending(conn: sqlite3.Connection) -> pd.DataFrame:
    """页1 在途注列表：paper pending 注 + fixture/strategy 信息，按开赛时间升序。"""
    return pd.read_sql_query("""
        SELECT b.id, r.strategy, r.market, f.league,
               f.kickoff_utc, th.name AS home, ta.name AS away,
               b.odds_taken, b.stake, b.placed_at
        FROM bets b
        JOIN recommendations r ON r.id = b.recommendation_id
        JOIN fixtures f ON f.id = r.fixture_id
        LEFT JOIN teams th ON th.id = f.home_team_id
        LEFT JOIN teams ta ON ta.id = f.away_team_id
        WHERE b.mode = 'paper' AND b.status = 'pending'
        ORDER BY f.kickoff_utc ASC, b.id ASC""", conn)


def b_runs(conn: sqlite3.Connection) -> pd.DataFrame:
    """页4：run 历史 + runs.summary 关键键展开（诚实降级的巡检入口，spec §9.5）。"""
    df = pd.read_sql_query(
        "SELECT id, type, phase, status, started_at, finished_at,"
        " credits_before, credits_after, summary FROM runs ORDER BY id DESC", conn)
    if df.empty:
        return df
    parsed = df["summary"].map(lambda t: json.loads(t) if t else {})
    for key in ("fixtures", "aligned", "bets", "degraded", "degraded_reasons"):
        # object dtype 直建：缺键保持 None、布尔保持 Python bool（不落 NaN/np.bool_）
        df[key] = pd.Series([d.get(key) for d in parsed], index=df.index, dtype=object)
    return df


def b_unknown_names(conn: sqlite3.Connection) -> pd.DataFrame:
    """页4：队名隔离表（spec §3.3——匹配不上的进隔离表，绝不静默丢弃）。"""
    return pd.read_sql_query(
        "SELECT source, name, first_seen FROM unknown_names"
        " ORDER BY first_seen, name", conn)


def a_overview(conn: sqlite3.Connection, leagues=None, seasons=None) -> dict:
    """页5：全样本对比 + 分赛季/分联赛/交叉分解（evaluate 口径=spec §8.2）。

    metrics.evaluate 对空行抛 ValueError——这里转成 empty 标记，页面走空态
    （设计 §5/§6：查询对空库永不抛）。
    """
    rows = fetch_predictions(conn, leagues, seasons)
    if not rows:
        return {"empty": True}
    cross: dict = {}
    for r in rows:
        cross.setdefault((r["league"], r["season"]), []).append(r)
    return {"empty": False,
            "overall": evaluate(rows),
            "by_season": by_group(rows, "season"),
            "by_league": by_group(rows, "league"),
            "by_league_season": {f"{l}|{s}": evaluate(v)
                                 for (l, s), v in sorted(cross.items())}}


def a_calibration(conn: sqlite3.Connection, leagues=None, seasons=None) -> dict:
    """页6：分市场十分位校准（复用 metrics.calibration，等宽分桶）。

    O2.5 的命中定义 = total_goals ≥ 3（与 simulate._hit 同口径）；无
    p_over25 行的库（大小球通道未跑）返回空列表而非报错。
    """
    rows = fetch_predictions(conn, leagues, seasons)
    if not rows:
        return {"empty": True}

    def cal(pairs):
        return calibration([p for p, _ in pairs], [h for _, h in pairs])

    over_rows = [r for r in rows if r.get("p_over25") is not None]
    return {"empty": False,
            "H": cal([(r["p_home"], r["outcome"] == "H") for r in rows]),
            "D": cal([(r["p_draw"], r["outcome"] == "D") for r in rows]),
            "A": cal([(r["p_away"], r["outcome"] == "A") for r in rows]),
            "O2.5": cal([(r["p_over25"], r["total_goals"] >= 3)
                         for r in over_rows]) if over_rows else []}


def _odds_bands() -> list[tuple[float, float, str]]:
    """赔率区间表：成员判定用常量（gates 单一事实源），标签文案按 spec
    §5.2 钉定的 1.4/6.0 书写——gates 改值须先改 spec，届时同步改标签。
    成员判定与标签语义对齐：非末档半开（lo ≤ o < hi），末档含上限。"""
    return [(ODDS_MIN, 2.0, "[1.4,2.0)"), (2.0, 3.0, "[2.0,3.0)"),
            (3.0, ODDS_MAX, "[3.0,6.0]")]


def a_paper_sim(conn: sqlite3.Connection, leagues=None, seasons=None,
                bankroll: float = 1000.0) -> dict:
    """页7：flat/¼Kelly 模拟（复用 simulate，成交价=收盘价，与 M2 报告同口径）。

    累计曲线用「日期前缀复调」：每个时间点把截至该日的候选前缀喂给同一个
    simulate 函数取汇总值——曲线与终值永远同源，不复制任何算式。
    """
    rows = fetch_predictions(conn, leagues, seasons)
    cands = candidates(rows)
    if not cands:
        return {"empty": True}
    ordered = sorted(cands, key=lambda c: c["date"])
    dates = sorted({c["date"] for c in ordered})
    flat_curve, kelly_curve = [], []
    for d in dates:
        prefix = [c for c in ordered if c["date"] <= d]
        flat_curve.append({"date": d, "pnl": simulate_flat(prefix)["pnl"]})
        kelly_curve.append({"date": d,
                            "bankroll": simulate_kelly(prefix, bankroll=bankroll)["final_bankroll"]})

    def band_label(o: float) -> str | None:
        for i, (lo, hi, lab) in enumerate(_odds_bands()):
            last = i == len(_odds_bands()) - 1
            if lo <= o < hi or (last and o == hi):
                return lab
        return None

    return {"empty": False, "n_candidates": len(cands),
            "flat": simulate_flat(cands),
            "kelly": simulate_kelly(cands, bankroll=bankroll),
            "flat_curve": flat_curve, "kelly_curve": kelly_curve,
            "by_market": {m: simulate_flat([c for c in cands if c["market"] == m])
                          for m in sorted({c["market"] for c in cands})},
            "by_band": {lab: simulate_flat([c for c in cands
                                            if band_label(c["odds"]) == lab])
                        for _, _, lab in _odds_bands()}}
