"""PaperExecutionProvider：模拟盘自动落注、结算与 CLV / bankroll（T7，spec §3.2 / §7.2 / §7.3）。

三件事，均为 ``paper`` 模式（``live`` 归人工 ``fa bet add/settle``，§7.2/§12.2）：

1. :func:`place_paper_bets`——把某个 run 的 ``model_only`` 推荐按推荐时最优价
   自动落成虚拟注：``stake = final_stake_frac（M4 位，NULL 则退回 kelly）× 当前
   bankroll``、``odds_taken = best_odds``、``status='pending'``。
2. :func:`settle_paper_bets`——把 ``fixtures`` 与 ``matches`` 配对后按市场判胜，
   写 ``status`` / ``return_amt`` / ``closing_odds`` / ``clv``，并把净额记入
   bankroll。
3. :func:`paper_summary`——``fa status`` 消费的台账汇总。

**bankroll**（spec §7.1「bankroll 快照」）：经 ``meta`` 持久化，键
:func:`BANKROLL_KEY` 是**单一事实源**（T8 的 render 曾自声明同名字面量，合流后
已改指此处）。首次落注时惰性初始化为 :func:`INITIAL_BANKROLL`（未用过不写 meta，
保持「未初始化」可观测）。语义：余额只在**结算**时按净额增减（落注不冻结注金，
注金以 ``bets.stake`` 记账、仓位按当前余额计）——这正是 §7.3 ROI = 净利 / 总投注
额的口径，也是 paper 模式零真金下最简单的可复算账本。

**判胜与 CLV**（§7.3）：H/D/A 用 ``fthg``/``ftag``；O2.5 用 ``fthg+ftag ≥ 3``。
``closing_odds`` 取 ``matches`` 的 Pinnacle 收盘列（H/D/A → ``psc_home/psc_draw/
psc_away``；O2.5 → ``over25_psc``），缺失则 ``NULL``；``clv = odds_taken/closing
− 1``，收盘缺失或非正 → ``NULL``（NULL 安全：中位数只吃非 NULL 者）。

时间：``placed_at`` / ``settled_at`` 都经 :func:`_now`（唯一注入缝，测试
monkeypatch 它）。本模块**不触网**；``matches`` / ``backtest_predictions`` 只读
（表边界 spec §12.1）。事务：每个公开函数恰好一次 ``conn.commit()``——任一环节
上抛即整体无半写（与 fixtures/value 同一纪律）。
"""

from __future__ import annotations

import sqlite3
import statistics
from datetime import date, datetime, timezone

from fa.db import get_meta, set_meta

BANKROLL_KEY = "paper_bankroll"     # 单一事实源（fa.report.render 已改指此处）
INITIAL_BANKROLL = 1000.0           # 首次落注时惰性初始化（brief 钉死）
STRATEGY = "model_only"             # M3 只落 model_only 轨（model_persona 归 M4，§6.6）
MODE = "paper"                      # 本 Provider 的模式（§7.2 双模式的 paper 侧）
STATUS_PENDING = "pending"
SETTLED_STATUSES = ("won", "lost")  # 终态；void = 退款，不进 ROI 的分母/分子
STATUS_FINISHED = "finished"        # fixtures.status：配对并结算完的场次
MAX_MATCH_DAY_GAP = 2               # fixture↔match 配对窗上界（brief：相差 ≤2 天）；
                                    # 下界 0——不认 kickoff **之前**的同配对完赛（T7 审查）

# 各市场 → matches 的收盘列（CLV 基准，spec §7.3「Pinnacle 收盘价」）
_CLOSING_COLUMN = {"H": "psc_home", "D": "psc_draw", "A": "psc_away",
                   "O2.5": "over25_psc"}

# 待落注推荐：该 run 的 model_only，且 (fixture_id, market, strategy, mode=paper)
# 级未下过（bets join recommendations——pm 窗对同场同市场不重下，UNIQUE 含 phase）。
_UNPLACED_SQL = (
    "SELECT r.id, r.market, r.bookmaker, r.best_odds, r.kelly_stake_frac,"
    " r.final_stake_frac"
    " FROM recommendations r"
    " WHERE r.run_id=? AND r.strategy=?"
    " AND NOT EXISTS ("
    "   SELECT 1 FROM bets b JOIN recommendations r2 ON r2.id = b.recommendation_id"
    "   WHERE b.mode=? AND r2.fixture_id = r.fixture_id"
    "     AND r2.market = r.market AND r2.strategy = r.strategy)"
    " ORDER BY r.id")


def place_paper_bets(conn: sqlite3.Connection, run_id: int) -> int:
    """把 *run_id* 的 model_only 推荐落成 paper 注，返回本次新下的注数。

    幂等：同一 run 重跑、或 am 已下后 pm 对同场同市场再触发，都返回 0。
    bankroll 键缺失时以 :func:`INITIAL_BANKROLL` 初始化（仅当确有落注）。
    """
    placed_at = _iso(_now())
    bankroll, initialized = _bankroll(conn)
    placed = 0
    for row in conn.execute(_UNPLACED_SQL, (run_id, STRATEGY, MODE)).fetchall():
        frac = row["final_stake_frac"]
        if frac is None:                       # M3 无 persona → 退回 kelly 仓位
            frac = row["kelly_stake_frac"]
        conn.execute(
            "INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker,"
            " odds_taken, stake, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (row["id"], MODE, placed_at, row["bookmaker"], row["best_odds"],
             _money(frac * bankroll), STATUS_PENDING),
        )
        placed += 1
    if placed and not initialized:              # 首次使用：初始化余额并落 meta
        set_meta(conn, BANKROLL_KEY, str(INITIAL_BANKROLL))
    conn.commit()
    return placed


def settle_paper_bets(conn: sqlite3.Connection) -> dict:
    """结算所有已可判定的 pending paper 注，返回 ``{"settled","won","pnl","clv_median"}``。

    配对：``fixtures`` 与 ``matches`` 按 ``(league, home_team_id, away_team_id)``
    且比赛日期落在 ``[kickoff 日期, kickoff 日期 + MAX_MATCH_DAY_GAP 天]`` 对上
    （多场候选取日期差最小者；无完赛行 / 未对齐侧 / 缺比分 / kickoff 不可解析
    → 不配对）。``settled``/``won`` 按注数计；``pnl`` = 已结算注的
    ``return − stake``；``clv_median`` = 本次结算注 CLV 的中位数（缺收盘者不计，
    全缺 → ``None``）。
    """
    pending = conn.execute(
        "SELECT b.id AS bet_id, b.stake, b.odds_taken,"
        " r.market, r.fixture_id, f.league, f.home_team_id, f.away_team_id,"
        " f.kickoff_utc"
        " FROM bets b"
        " JOIN recommendations r ON r.id = b.recommendation_id"
        " JOIN fixtures f ON f.id = r.fixture_id"
        " WHERE b.mode=? AND b.status=?"
        " ORDER BY b.id", (MODE, STATUS_PENDING)).fetchall()

    settled = won = 0
    pnl = 0.0
    clvs: list[float] = []
    bankroll, _initialized = _bankroll(conn)        # 只读：未初始化时不写 meta
    settled_at = _iso(_now())                       # 一次结算一个时间戳（同 T5 纪律）
    by_fixture: dict[int, list[sqlite3.Row]] = {}
    for row in pending:
        by_fixture.setdefault(row["fixture_id"], []).append(row)

    for fixture_id in sorted(by_fixture):               # 稳定序：fixture id
        group = by_fixture[fixture_id]
        head = group[0]
        match = _paired_match(conn, head["league"], head["home_team_id"],
                              head["away_team_id"], _kickoff_date(head["kickoff_utc"]))
        if match is None:
            continue                                    # 无从判定 → 保持 pending
        for bet in group:
            is_won = _is_won(bet["market"], match["fthg"], match["ftag"])
            closing = match[_CLOSING_COLUMN[bet["market"]]]
            clv = (bet["odds_taken"] / closing - 1
                   if closing is not None and closing > 0 else None)
            return_amt = _money(bet["stake"] * bet["odds_taken"]) if is_won else 0.0
            conn.execute(
                "UPDATE bets SET status=?, settled_at=?, return_amt=?,"
                " closing_odds=?, clv=? WHERE id=?",
                ("won" if is_won else "lost", settled_at, return_amt,
                 closing, clv, bet["bet_id"]),
            )
            settled += 1
            won += 1 if is_won else 0
            pnl += return_amt - bet["stake"]
            if clv is not None:
                clvs.append(clv)
        conn.execute("UPDATE fixtures SET status=? WHERE id=?",
                     (STATUS_FINISHED, fixture_id))

    if settled:
        set_meta(conn, BANKROLL_KEY, str(_money(bankroll + pnl)))
    conn.commit()
    return {"settled": settled, "won": won, "pnl": _money(pnl),
            "clv_median": statistics.median(clvs) if clvs else None}


def paper_summary(conn: sqlite3.Connection) -> dict:
    """paper 台账汇总（``fa status`` 消费）。

    ``n`` = 全部 paper 注；``pending`` = 未结注数；``staked``/``returned``/``roi``
    只计终态为 won/lost 的注（在途仓位不进分母，否则 ROI 被在途注拖成假负；
    void = 退款，净额为 0，同样不进）；``roi`` 无已结算注金时为 ``None``；
    ``bankroll`` 读 meta（未初始化 → ``None``）；``clv_median`` 为已结算注 CLV
    中位数（缺收盘者不计）。
    """
    row = conn.execute(
        "SELECT COUNT(*) AS n,"
        " COALESCE(SUM(CASE WHEN status=? THEN 1 ELSE 0 END), 0) AS pending,"
        " COALESCE(SUM(CASE WHEN status IN (?, ?) THEN stake END), 0.0) AS staked,"
        " COALESCE(SUM(CASE WHEN status IN (?, ?) THEN return_amt END), 0.0)"
        "   AS returned"
        " FROM bets WHERE mode=?",
        (STATUS_PENDING, *SETTLED_STATUSES, *SETTLED_STATUSES, MODE)).fetchone()
    clvs = [r["clv"] for r in conn.execute(
        "SELECT clv FROM bets WHERE mode=? AND clv IS NOT NULL", (MODE,))]
    raw = get_meta(conn, BANKROLL_KEY)
    staked = float(row["staked"])
    return {
        "n": int(row["n"]),
        "staked": staked,
        "returned": float(row["returned"]),
        "roi": (float(row["returned"]) - staked) / staked if staked else None,
        "pending": int(row["pending"]),
        "bankroll": None if raw is None else float(raw),
        "clv_median": statistics.median(clvs) if clvs else None,
    }


# ---------------------------------------------------------------- 内部实现


def _now() -> datetime:
    """时间注入缝：placed_at / settled_at 都从这里取（测试 monkeypatch 此函数）。"""
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _money(amount: float) -> float:
    """金额两位小数（注金 / 回报 / 余额都是钱；CLV 是比率，不在此列）。"""
    return round(amount, 2)


def _bankroll(conn: sqlite3.Connection) -> tuple[float, bool]:
    """读当前余额 → ``(值, 是否已初始化)``；键缺失返回默认值且**不写** meta。"""
    raw = get_meta(conn, BANKROLL_KEY)
    return (INITIAL_BANKROLL, False) if raw is None else (float(raw), True)


def _kickoff_date(kickoff_utc: str) -> date | None:
    """``...Z`` / 带偏移 ISO 串 → UTC 日历日；不可解析 → None（不猜时区）。"""
    try:
        parsed = datetime.fromisoformat(str(kickoff_utc).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)   # 裸时间按 UTC 读（同 value 层）
    return parsed.astimezone(timezone.utc).date()


def _match_date(text: str) -> date | None:
    """``matches.date``（``YYYY-MM-DD``）→ ``date``；不可解析 → None（该行出局）。"""
    try:
        return date.fromisoformat(str(text))
    except ValueError:
        return None


def _paired_match(conn: sqlite3.Connection, league: str, home_team_id: int | None,
                  away_team_id: int | None, kickoff: date | None) -> sqlite3.Row | None:
    """该 fixture 对应的完赛行；配不上返回 ``None``（注保持 pending，绝不硬猜）。

    窗口 = ``[kickoff 的 UTC 日历日, kickoff 日历日 + MAX_MATCH_DAY_GAP 天]``：
    **只认 kickoff 当日或之后**的完赛（T7 审查加固）。理由——同一配对可能在
    1–2 天前已赛过另一场（杯赛 / 一周双赛），而 fixture 因故推迟时 kickoff 会
    后移；拿赛前那场的比分来结算，等于用错误的比赛结果 latch 掉注（写
    ``finished`` + 动 bankroll），且事后几乎无从察觉。推迟只会把 kickoff 往后
    推，故真完赛必在同日或之后；上界 +2 天覆盖跨日深夜开球与完赛数据晚到
    （brief「相差 ≤2 天」的上端点语义保留）。

    多场候选取 ``(日期差, 日期, id)`` 最小者——确定性；经上面的下界过滤后
    ``日期差`` 非负，即「离 kickoff 最近的赛后完赛」胜出，平手偏向更早且更先
    入库的一行。
    """
    if kickoff is None or home_team_id is None or away_team_id is None:
        return None
    best = None
    best_key = None
    for row in conn.execute(
            "SELECT * FROM matches WHERE league=? AND home_team_id=?"
            " AND away_team_id=? AND fthg IS NOT NULL AND ftag IS NOT NULL"
            " ORDER BY date, id", (league, home_team_id, away_team_id)).fetchall():
        played = _match_date(row["date"])
        if played is None:
            continue
        gap = (played - kickoff).days
        if gap < 0 or gap > MAX_MATCH_DAY_GAP:      # 赛前同配对 → 出局（不得 latch）
            continue
        key = (gap, row["date"], row["id"])
        if best_key is None or key < best_key:
            best, best_key = row, key
    return best


def _is_won(market: str, fthg: int, ftag: int) -> bool:
    """按市场判胜（spec §7.2）：H/D/A 看比分，O2.5 看总球数 ≥3。"""
    if market == "H":
        return fthg > ftag
    if market == "D":
        return fthg == ftag
    if market == "A":
        return fthg < ftag
    if market == "O2.5":
        return fthg + ftag >= 3
    raise ValueError(f"未知市场 {market!r}")     # CHECK 约束下不可达，防御性上抛
