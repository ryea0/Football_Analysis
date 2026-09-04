"""PaperExecutionProvider：模拟盘自动落注、结算与 CLV / bankroll（T7，spec §3.2 / §7.2 / §7.3）。

三件事，均为 ``paper`` 模式（``live`` 归人工 ``fa bet add/settle``，§7.2/§12.2）：

1. :func:`place_paper_bets`——把某个 run 的**双轨**（§6.6 A/B，:data:`STRATEGIES`）
   推荐按推荐时最优价自动落成虚拟注：``stake = final_stake_frac（persona 位，
   NULL 则退回 kelly）× 该轨 bankroll``、``odds_taken = best_odds``、
   ``status='pending'``；veto（final 双零）跳过不落注。
2. :func:`settle_paper_bets`——把 ``fixtures`` 与 ``matches`` 配对后按市场判胜，
   写 ``status`` / ``return_amt`` / ``closing_odds`` / ``clv``，并把各轨净额
   记入**各轨** bankroll（D2 分账）。
3. :func:`paper_summary`——``fa status`` 消费的台账汇总，按轨返回。

**bankroll**（spec §7.1「bankroll 快照」）：经 ``meta`` 持久化，键
:func:`bankroll_key`（``paper_bankroll:{strategy}``，D2 分轨）——两轨独立记账、
互不混水；M3 的单键 :data:`LEGACY_BANKROLL_KEY` 由 :func:`ensure_bankroll_migrated`
一次性搬进 ``:model_only`` 轨后删除（M3 的 14 注全 model_only，归属无歧义）。
首次落注时惰性初始化为 :data:`INITIAL_BANKROLL`（未用过不写 meta，保持
「未初始化」可观测）。语义：余额只在**结算**时按净额增减（落注不冻结注金，
注金以 ``bets.stake`` 记账、仓位按当前余额计）——这正是 §7.3 ROI = 净利 / 总投注
额的口径，也是 paper 模式零真金下最简单的可复算账本。

**判胜与 CLV**（§7.3）：H/D/A 用 ``fthg``/``ftag``；O2.5 用 ``fthg+ftag ≥ 3``。
``closing_odds`` 走**基准链**——Pinnacle 收盘列（H/D/A → ``psc_home/psc_draw/
psc_away``；O2.5 → ``over25_psc``）优先，缺失 fallback Betfair 交易所收盘
（``bfe_*``，football-data 自 2025-12 断供 Pinnacle，2026-09-04 裁定）；实际
所用基准记 ``closing_source``（'pinnacle'/'betfair'，双缺 → 两列皆 ``NULL``）。
``clv = odds_taken/closing − 1``，收盘缺失 → ``NULL``（NULL 安全：中位数只吃
非 NULL 者）。事后基准才落库的已结算注由 :func:`backfill_clv` 只填 NULL 地补。

时间：``placed_at`` / ``settled_at`` 都经 :func:`_now`（唯一注入缝，测试
monkeypatch 它）。本模块**不触网**；``matches`` / ``backtest_predictions`` 只读
（表边界 spec §12.1）。事务：每个公开函数恰好一次 ``conn.commit()``——任一环节
上抛即整体无半写（与 fixtures/value 同一纪律）；:func:`ensure_bankroll_migrated`
的 meta 写随该 commit 走（summary 本身只读，迁移动了 meta 时自行补一次单行 commit）。
"""

from __future__ import annotations

import sqlite3
import statistics
from datetime import date, datetime, timezone

from fa.db import get_meta, set_meta
from fa.pipeline.value import STRATEGIES

LEGACY_BANKROLL_KEY = "paper_bankroll"  # M3 单键，仅迁移读（T14 起 render 亦走
                                        # bankroll_key，此常量是全仓唯一读点）
INITIAL_BANKROLL = 1000.0           # 首次落注时惰性初始化（brief 钉死）
MODE = "paper"                      # 本 Provider 的模式（§7.2 双模式的 paper 侧）
STATUS_PENDING = "pending"
SETTLED_STATUSES = ("won", "lost")  # 终态；void = 退款，不进 ROI 的分母/分子
STATUS_FINISHED = "finished"        # fixtures.status：配对并结算完的场次
MAX_MATCH_DAY_GAP = 2               # fixture↔match 配对窗上界（brief：相差 ≤2 天）；
                                    # 下界 0——不认 kickoff **之前**的同配对完赛（T7 审查）

# 各市场 → matches 的收盘列（CLV 基准，spec §7.3）。基准链：Pinnacle 收盘
# （psc_*，与 A 线回测同源）优先；football-data 自 2025-12 起断供 Pinnacle，
# 缺失时 fallback Betfair 交易所收盘（bfe_*，2026-09-04 负责人裁定）。
# bets.closing_source 记账实际用了哪个（'pinnacle'/'betfair'）——两基准混合的
# CLV 序列才可解释（§12.3 结论分账的证据链要求）。
_PINNACLE_CLOSING = {"H": "psc_home", "D": "psc_draw", "A": "psc_away",
                     "O2.5": "over25_psc"}
_BFE_CLOSING = {"H": "bfe_home", "D": "bfe_draw", "A": "bfe_away",
                "O2.5": "over25_bfe"}
CLOSING_SOURCE_PINNACLE = "pinnacle"
CLOSING_SOURCE_BETFAIR = "betfair"


def _closing(match: sqlite3.Row, market: str) -> tuple[float | None, str | None]:
    """ ``(收盘价, 基准来源)``：psc 优先、缺失 fallback bfe；双基准皆缺 →
    ``(None, None)``（closing_source 同为 NULL，绝不猜基准）。"""
    for columns, source in ((_PINNACLE_CLOSING, CLOSING_SOURCE_PINNACLE),
                            (_BFE_CLOSING, CLOSING_SOURCE_BETFAIR)):
        value = match[columns[market]]
        if value is not None and value > 0:
            return value, source
    return None, None

# 待落注推荐：该 run 指定轨，且 (fixture_id, market, strategy, mode=paper)
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

# 分轨台账聚合（paper_summary 用；join recommendations 才能按 strategy 切轨）
_SUMMARY_SQL = (
    "SELECT COUNT(*) AS n,"
    " COALESCE(SUM(CASE WHEN b.status=? THEN 1 ELSE 0 END), 0) AS pending,"
    " COALESCE(SUM(CASE WHEN b.status IN (?, ?) THEN b.stake END), 0.0) AS staked,"
    " COALESCE(SUM(CASE WHEN b.status IN (?, ?) THEN b.return_amt END), 0.0)"
    "   AS returned"
    " FROM bets b JOIN recommendations r ON r.id = b.recommendation_id"
    " WHERE b.mode=? AND r.strategy=?")
_CLV_SQL = (
    "SELECT b.clv AS clv FROM bets b"
    " JOIN recommendations r ON r.id = b.recommendation_id"
    " WHERE b.mode=? AND r.strategy=? AND b.clv IS NOT NULL")


def bankroll_key(strategy: str) -> str:
    """分轨 bankroll 的 meta 键（D2）：``paper_bankroll:{strategy}``。"""
    return f"paper_bankroll:{strategy}"


def ensure_bankroll_migrated(conn: sqlite3.Connection) -> None:
    """M3 旧单键 → ``:model_only`` 轨（M3 的 14 注全 model_only，归属无歧义）。

    幂等：旧键缺席即空操作（绝大多数调用立刻返回）；新轨已有账则只删旧键，
    绝不倒灌覆盖新账。不 commit——place/settle 随外层唯一 commit 入库。
    place / settle / summary 三个入口都调（老库第一次落注、第一次结算、
    第一次 ``fa status`` 都能见到旧账）。
    """
    legacy = get_meta(conn, LEGACY_BANKROLL_KEY)
    if legacy is None:
        return
    if get_meta(conn, bankroll_key("model_only")) is None:
        set_meta(conn, bankroll_key("model_only"), legacy)
    conn.execute("DELETE FROM meta WHERE key=?", (LEGACY_BANKROLL_KEY,))


def place_paper_bets(conn: sqlite3.Connection, run_id: int) -> int:
    """把 *run_id* 的双轨推荐（§6.6 A/B）落成 paper 注，返回本次新下的注数。

    逐轨循环：各查各轨未落推荐（:data:`_UNPLACED_SQL` 参数化 strategy）、各读
    各轨 bankroll（键缺失惰性初始化，仅当该轨确有落注）。``final_stake_frac``
    为 NULL → 退回 kelly（M3 语义）；≤ 0（veto 双零）→ 跳过不落注——
    未落的推荐不产生 bets 行，后续窗口 persona 若翻案仍可落。
    幂等：同一 run 重跑、或 am 已下后 pm 对同场同市场再触发，都返回 0。
    """
    ensure_bankroll_migrated(conn)
    placed_at = _iso(_now())
    placed = 0
    for strategy in STRATEGIES:                 # 双轨分账：各查各轨、各读各轨余额
        bankroll, initialized = _bankroll(conn, strategy)
        n_track = 0
        for row in conn.execute(_UNPLACED_SQL,
                                (run_id, strategy, MODE)).fetchall():
            frac = row["final_stake_frac"]
            if frac is None:                    # persona 位缺失 → 退回 kelly 仓位
                frac = row["kelly_stake_frac"]
            if frac is None or frac <= 0:       # veto 双零（或无仓位）→ 不落注
                continue
            conn.execute(
                "INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker,"
                " odds_taken, stake, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (row["id"], MODE, placed_at, row["bookmaker"], row["best_odds"],
                 _money(frac * bankroll), STATUS_PENDING),
            )
            placed += 1
            n_track += 1
        if n_track and not initialized:         # 该轨首次使用：初始化余额并落 meta
            set_meta(conn, bankroll_key(strategy), str(INITIAL_BANKROLL))
    conn.commit()
    return placed


def settle_paper_bets(conn: sqlite3.Connection) -> dict:
    """结算所有已可判定的 pending paper 注，**分轨**返回：

    ``{"by_strategy": {strategy: {"settled","won","pnl","clv_median"}},
    "settled","won","pnl","clv_median"}``——顶层为双轨合计（daily 简报 /
    :func:`fa.report.render.render_settlement_brief` 消费），``by_strategy``
    恒含 :data:`STRATEGIES` 全部键（空轨零值），形状与顶层同。

    配对：``fixtures`` 与 ``matches`` 按 ``(league, home_team_id, away_team_id)``
    且比赛日期落在 ``[kickoff 日期, kickoff 日期 + MAX_MATCH_DAY_GAP 天]`` 对上
    （多场候选取日期差最小者；无完赛行 / 未对齐侧 / 缺比分 / kickoff 不可解析
    → 不配对）。``settled``/``won`` 按注数计；``pnl`` = 已结算注的
    ``return − stake``；``clv_median`` = 本次结算注 CLV 的中位数（缺收盘者不计，
    全缺 → ``None``）。bankroll 分轨回写：pnl 按 ``recommendations.strategy``
    累计，各轨结算前只读一次余额、只写一次 meta；本窗没有结算的轨不动账
    （保持「未初始化/原值」可观测）。
    """
    ensure_bankroll_migrated(conn)
    pending = conn.execute(
        "SELECT b.id AS bet_id, b.stake, b.odds_taken,"
        " r.market, r.strategy, r.fixture_id, f.league, f.home_team_id,"
        " f.away_team_id, f.kickoff_utc"
        " FROM bets b"
        " JOIN recommendations r ON r.id = b.recommendation_id"
        " JOIN fixtures f ON f.id = r.fixture_id"
        " WHERE b.mode=? AND b.status=?"
        " ORDER BY b.id", (MODE, STATUS_PENDING)).fetchall()

    settled = won = 0
    pnl = 0.0
    clvs: list[float] = []
    tracks: dict[str, dict] = {}          # 各轨累计器（含空轨，返回形状恒完整）
    for strategy in STRATEGIES:
        tracks[strategy] = {"settled": 0, "won": 0, "pnl": 0.0, "clvs": []}
    settled_at = _iso(_now())             # 一次结算一个时间戳（同 T5 纪律）
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
            track = tracks.setdefault(                  # 未知轨也不丢账（防御）
                bet["strategy"], {"settled": 0, "won": 0, "pnl": 0.0, "clvs": []})
            is_won = _is_won(bet["market"], match["fthg"], match["ftag"])
            closing, source = _closing(match, bet["market"])
            clv = (bet["odds_taken"] / closing - 1
                   if closing is not None else None)
            return_amt = _money(bet["stake"] * bet["odds_taken"]) if is_won else 0.0
            conn.execute(
                "UPDATE bets SET status=?, settled_at=?, return_amt=?,"
                " closing_odds=?, closing_source=?, clv=? WHERE id=?",
                ("won" if is_won else "lost", settled_at, return_amt,
                 closing, source, clv, bet["bet_id"]),
            )
            settled += 1
            won += 1 if is_won else 0
            pnl += return_amt - bet["stake"]
            track["settled"] += 1
            track["won"] += 1 if is_won else 0
            track["pnl"] += return_amt - bet["stake"]
            if clv is not None:
                clvs.append(clv)
                track["clvs"].append(clv)
        conn.execute("UPDATE fixtures SET status=? WHERE id=?",
                     (STATUS_FINISHED, fixture_id))

    by_strategy: dict[str, dict] = {}
    for strategy in list(STRATEGIES) + sorted(set(tracks) - set(STRATEGIES)):
        track = tracks[strategy]
        if track["settled"]:
            bankroll, _initialized = _bankroll(conn, strategy)   # 各轨只读一次
            set_meta(conn, bankroll_key(strategy),
                     str(_money(bankroll + track["pnl"])))
        by_strategy[strategy] = {
            "settled": track["settled"], "won": track["won"],
            "pnl": _money(track["pnl"]),
            "clv_median": (statistics.median(track["clvs"])
                           if track["clvs"] else None)}
    conn.commit()
    return {"by_strategy": by_strategy,
            "settled": settled, "won": won, "pnl": _money(pnl),
            "clv_median": statistics.median(clvs) if clvs else None}


def backfill_clv(conn: sqlite3.Connection) -> dict:
    """补齐已结算 paper 注缺失的收盘基准，返回 ``{"filled": n}``。

    场景：结算时双基准皆缺（Pinnacle 断供初期、BFE 尚未回填），事后基准落库
    （``fa data backfill-bfe``）——这里按结算同款配对与基准链补
    ``closing_odds`` / ``closing_source`` / ``clv``。**只填 ``closing_odds IS
    NULL`` 的 won/lost 注**：已有基准的注不碰（哪怕新基准更优——CLV 序列不能
    事后换基准），pending 注不碰（正常结算自会带 fallback），状态与损益永不改。
    """
    pending = conn.execute(
        "SELECT b.id AS bet_id, b.odds_taken, r.market, r.fixture_id,"
        " f.league, f.home_team_id, f.away_team_id, f.kickoff_utc"
        " FROM bets b"
        " JOIN recommendations r ON r.id = b.recommendation_id"
        " JOIN fixtures f ON f.id = r.fixture_id"
        " WHERE b.mode=? AND b.status IN (?, ?) AND b.closing_odds IS NULL"
        " ORDER BY b.id", (MODE, *SETTLED_STATUSES)).fetchall()
    by_fixture: dict[int, list[sqlite3.Row]] = {}
    for row in pending:
        by_fixture.setdefault(row["fixture_id"], []).append(row)

    filled = 0
    for fixture_id in sorted(by_fixture):           # 稳定序：fixture id（同结算）
        group = by_fixture[fixture_id]
        head = group[0]
        match = _paired_match(conn, head["league"], head["home_team_id"],
                              head["away_team_id"], _kickoff_date(head["kickoff_utc"]))
        if match is None:
            continue
        for bet in group:
            closing, source = _closing(match, bet["market"])
            if closing is None:
                continue
            conn.execute(
                "UPDATE bets SET closing_odds=?, closing_source=?, clv=?"
                " WHERE id=? AND closing_odds IS NULL",
                (closing, source, bet["odds_taken"] / closing - 1,
                 bet["bet_id"]))
            filled += 1
    conn.commit()
    return {"filled": filled}


def paper_summary(conn: sqlite3.Connection) -> dict[str, dict]:
    """paper 台账汇总，**分轨**返回（D2，``fa status`` 消费）。

    返回 ``{strategy: 汇总}``，键序 = :data:`STRATEGIES`；每轨的形状：
    ``n`` = 该轨全部 paper 注；``pending`` = 未结注数；``staked``/``returned``/
    ``roi`` 只计终态为 won/lost 的注（在途仓位不进分母，否则 ROI 被在途注拖成
    假负；void = 退款，净额为 0，同样不进）；``roi`` 无已结算注金时为 ``None``；
    ``bankroll`` 读该轨 meta（未初始化 → ``None``）；``clv_median`` 为已结算注
    CLV 中位数（缺收盘者不计）。
    """
    ensure_bankroll_migrated(conn)
    if conn.in_transaction:     # 迁移写后自持提交（meta 单行写，安全）；若未来
        conn.commit()           # 管线内调用需改显式信号，别靠读路径顺手 commit
    out: dict[str, dict] = {}
    for strategy in STRATEGIES:
        row = conn.execute(_SUMMARY_SQL,
                           (STATUS_PENDING, *SETTLED_STATUSES, *SETTLED_STATUSES,
                            MODE, strategy)).fetchone()
        clvs = [r["clv"] for r in conn.execute(_CLV_SQL, (MODE, strategy))]
        raw = get_meta(conn, bankroll_key(strategy))
        staked = float(row["staked"])
        out[strategy] = {
            "n": int(row["n"]),
            "staked": staked,
            "returned": float(row["returned"]),
            "roi": (float(row["returned"]) - staked) / staked if staked else None,
            "pending": int(row["pending"]),
            "bankroll": None if raw is None else float(raw),
            "clv_median": statistics.median(clvs) if clvs else None,
        }
    return out


# ---------------------------------------------------------------- 内部实现


def _now() -> datetime:
    """时间注入缝：placed_at / settled_at 都从这里取（测试 monkeypatch 此函数）。"""
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _money(amount: float) -> float:
    """金额两位小数（注金 / 回报 / 余额都是钱；CLV 是比率，不在此列）。"""
    return round(amount, 2)


def _bankroll(conn: sqlite3.Connection, strategy: str) -> tuple[float, bool]:
    """读该轨当前余额 → ``(值, 是否已初始化)``；键缺失返回默认值且**不写** meta。"""
    raw = get_meta(conn, bankroll_key(strategy))
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
