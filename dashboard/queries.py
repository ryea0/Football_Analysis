"""dashboard 查询层：只读 SQL + fa 纯函数复用。

设计：docs/superpowers/specs/2026-09-04-web-dashboard-design.md（§2/§5）。
本模块不 import streamlit（缓存包在 loaders.py）；不碰任何有副作用的
fa 模块（写库 / hermes / 网络）；指标公式一律复用 fa.backtest.*。
"""
import json
import sqlite3
from pathlib import Path

import pandas as pd

from fa.config import db_path


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
    """页1 总览：meta 两键 + paper 注汇总 + 额度序列。

    pnl/staked 只计已结算注（pending 不进任何一侧；void 排除——零损益）；
    meta 键缺失返回 None（页面显示「未记录」而非 0，设计 §6）。
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
    return {"bankroll": meta_float("paper_bankroll"),
            "quota_remaining": meta_float("odds_quota_remaining"),
            "n_bets": agg["n"], "n_pending": agg["n_pending"],
            "pnl": agg["pnl"], "staked": agg["staked"],
            "quota_series": [dict(r) for r in quota]}


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
    """页2 下表：paper/live 注明细 + 推荐维度（market/strategy 等）+ 队名。"""
    return pd.read_sql_query("""
        SELECT b.id, b.mode, b.placed_at, b.status, b.bookmaker,
               b.odds_taken, b.stake, b.settled_at, b.return_amt,
               b.closing_odds, b.clv,
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


def b_ab_tracks(conn: sqlite3.Connection) -> dict:
    """页3：两轨注数/ROI/CLV 中位数 + 按结算日累计 P&L（§6.6/§12.3）。

    样本量语义交给页面展示（进度条 + 警示）；这里只算数，不判结论。
    """
    df = pd.read_sql_query("""
        SELECT r.strategy AS strategy, b.status, b.stake, b.return_amt,
               b.clv, b.settled_at
        FROM bets b JOIN recommendations r ON r.id = b.recommendation_id
        WHERE b.mode = 'paper'""", conn)
    out: dict = {}
    for strat in ("model_only", "model_persona"):
        g = df[df["strategy"] == strat]
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
