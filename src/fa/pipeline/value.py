"""价值层：模型拟合复用与推荐生成（T6，spec §5 / §3.2）。

对每个**已对齐**且 kickoff 落在 ``[now, now + 52h]`` 的 fixture：

1. 按联赛拟合一次/run（``asof`` = 今天；全联赛加权目标用 ``global_targets``，
   ρ 用两阶段 ``fit_rho``）——同一 run 内同联赛只拟合一次，逐 fixture 复用；
2. λ → ``score_matrix`` → p(H/D/A) 与 p_over25（与 M2 回测同一拟合链）；
3. 从 ``odds_snapshots`` 取该 fixture **最新一批**快照，``best_prices`` 得跨
   bookmaker 可成交最优价；h2h 以三最优价 ``devig_proportional`` 得 market_p
   （H/D/A 各取自己的份额），totals 以 ``devig_ou`` 取 over 侧；
4. ``edge`` / ``EV`` / 赔率带三道门槛（``fa/value/gates.py`` 单一事实源）全过
   才写 ``recommendations``，``kelly_stake_frac = kelly_fraction(...)``。

UNIQUE ``(fixture_id, market, strategy, phase)`` 冲突 → ``DO UPDATE`` 刷新
run_id 与全部价格类字段，**不触碰** M4 persona 的三列（verdict /
confidence_delta / final_stake_frac）——这是不用「先 DELETE 再 INSERT」的原因。

时间：所有「现在」都经 :func:`_now`（唯一注入缝，测试 monkeypatch 它）。
本模块**不触网**（价格来自 T5 已落库的 ``odds_snapshots``）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from fa.backtest.run import global_targets
from fa.model.fit import FitConfig, fit_league, training_rows
from fa.model.predict import (expected_goals, fit_rho, over25_probs,
                              outcome_probs, score_matrix)
from fa.pipeline.odds_api import OddsSnapshot, best_prices
from fa.value.devig import devig_ou, devig_proportional
from fa.value.gates import (EDGE_MIN, EV_MIN, ODDS_MAX, ODDS_MIN, ev_of,
                            kelly_fraction)

STRATEGY = "model_only"             # M4 的 model_persona 轨由后续任务落（§6.6）
WINDOW_HOURS = 52                   # 比赛日窗口（spec §9.6 两窗之间）
MIN_TRAIN_ROWS = 30                 # 与 fit_league 的下限一致（数据不足 → 跳过该联赛）
_H2H_SLOTS = (("H", "home"), ("D", "draw"), ("A", "away"))
_H2H_KEYS = ("home", "draw", "away")


def _now() -> datetime:
    """时间注入缝：窗口下界与拟合 ``asof`` 都从这里取（测试 monkeypatch 此函数）。"""
    return datetime.now(timezone.utc)


def generate_recommendations(conn: sqlite3.Connection, leagues: list[str],
                             phase: str, run_id: int,
                             cfg: FitConfig = FitConfig()) -> list[int]:
    """对窗口内已对齐 fixture 生成/刷新推荐，返回受影响 ``recommendations.id``。

    无训练样本的联赛跳过（不中断 run）；无盘口/未对齐/出窗的 fixture 无推荐。
    全程单 ``conn.commit()``。
    """
    if phase not in ("am", "pm"):
        raise ValueError(f"phase 须为 am/pm，收到 {phase!r}")
    now = _now()
    ids: list[int] = []
    if not leagues:
        return ids
    asof = now.date().isoformat()
    fits = _fit_leagues(conn, leagues, asof, cfg)

    for fixture in _window_fixtures(conn, leagues, now):
        fitted = fits.get(fixture["league"])
        if fitted is None:
            continue
        fit, rho = fitted
        lh, la = expected_goals(fit, fixture["home_name"], fixture["away_name"])
        matrix = score_matrix(lh, la, rho)
        p_h, p_d, p_a = outcome_probs(matrix)
        p_over, _p_under = over25_probs(matrix)
        model_p = {"H": p_h, "D": p_d, "A": p_a, "O2.5": p_over}

        for market, odds, bookmaker, market_p in _quoted_markets(conn, fixture):
            p = model_p[market]
            edge = p - market_p
            if not ODDS_MIN <= odds <= ODDS_MAX:
                continue                        # 赔率带：独立关卡（spec §5.2）
            ev = ev_of(p, odds)
            if ev < EV_MIN or edge < EDGE_MIN:
                continue
            ids.append(_upsert_recommendation(
                conn, run_id=run_id, fixture_id=fixture["id"], market=market,
                phase=phase, model_p=p, market_p=market_p, best_odds=odds,
                bookmaker=bookmaker, edge=edge, ev=ev,
                kelly=kelly_fraction(p, odds), created_at=_iso(now)))
    conn.commit()
    return ids


# ---------------------------------------------------------------- 拟合（每联赛一次）


def _fit_leagues(conn: sqlite3.Connection, leagues: list[str], asof: str,
                 cfg: FitConfig) -> dict[str, tuple]:
    """逐联赛拟合 ``(LeagueFit, rho)``；训练样本不足的联赛跳过（键缺席）。"""
    out: dict[str, tuple] = {}
    for league in leagues:
        train = training_rows(conn, league, asof, cfg.window_days)
        if len(train) < MIN_TRAIN_ROWS:
            continue
        mu_g, ha_g = global_targets(conn, asof, cfg)
        fit = fit_league(train, asof, league, mu_g, ha_g, cfg)
        rho = fit_rho(train, asof, cfg,
                      lambda h, a, _fit=fit: expected_goals(_fit, h, a))
        out[league] = (fit, rho)
    return out


# ---------------------------------------------------------------- 候选 fixture


def _window_fixtures(conn: sqlite3.Connection, leagues: list[str],
                     now: datetime) -> list[dict]:
    """窗口内且**双侧已对齐**的 fixture（按 id 稳定序）。

    内连接 ``teams`` 使任一侧 ``team_id`` 为 NULL 的行自动出局——未对齐即不推荐
    （spec §3.3：对齐是下注前提）。kickoff 解析不了的行同样出局（不猜时区）。
    """
    end = now + timedelta(hours=WINDOW_HOURS)
    placeholders = ",".join("?" * len(leagues))
    sql = (
        "SELECT f.id, f.league, f.event_key, f.kickoff_utc,"
        " h.name AS home_name, a.name AS away_name"
        " FROM fixtures f"
        " JOIN teams h ON h.id = f.home_team_id"
        " JOIN teams a ON a.id = f.away_team_id"
        f" WHERE f.league IN ({placeholders}) ORDER BY f.id")
    out = []
    for row in conn.execute(sql, list(leagues)):
        kickoff = _parse_kickoff(row["kickoff_utc"])
        if kickoff is None or not now <= kickoff <= end:
            continue
        out.append(dict(row))
    return out


def _parse_kickoff(text) -> datetime | None:
    """``...Z`` / 带偏移的 ISO 串 → aware UTC datetime；不可解析 → None。"""
    try:
        parsed = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)    # 裸时间按 UTC 读（Odds API 口径）
    return parsed.astimezone(timezone.utc)


# ---------------------------------------------------------------- 实时盘（最新一批）


def _quoted_markets(conn: sqlite3.Connection,
                    fixture: dict) -> list[tuple[str, float, str, float]]:
    """该 fixture 各 market 的 ``(recommendations.market, best_odds, bookmaker, market_p)``。

    只看**最新一批**快照（``fetched_at`` 取该 fixture+market 的最大值）——最新
    一次拉取才是当下可成交的盘口；批内再跨 bookmaker/region 取各 outcome 最大价。
    某侧无报价、或 h2h 三价不全 → 该 market 缺席（不猜价）。
    """
    out: list[tuple[str, float, str, float]] = []
    h2h = _latest_prices(conn, fixture, "h2h")
    if all(k in h2h for k in _H2H_KEYS):
        devigged = devig_proportional([h2h[k][0] for k in _H2H_KEYS])
        for (market, key), market_p in zip(_H2H_SLOTS, devigged):
            price, bookmaker = h2h[key]
            out.append((market, price, bookmaker, market_p))

    totals = _latest_prices(conn, fixture, "totals")
    if {"over", "under"} <= set(totals):
        over_price, over_book = totals["over"]
        p_over, _p_under = devig_ou(over_price, totals["under"][0])
        out.append(("O2.5", over_price, over_book, p_over))
    return out


def _latest_prices(conn: sqlite3.Connection, fixture: dict,
                   market: str) -> dict[str, tuple[float, str]]:
    """最新一批快照 → ``best_prices``（单 event_key，符合其单场语义）。

    ``OddsSnapshot`` 在此只是 best_prices 的**载价容器**：identity 字段（league /
    kickoff / 两队名）按 fixture 回填，使重放的快照自描述且与 fixtures 一致
    （T5 对齐即「快照 home == fixture home」），均不参与最优价计算。
    """
    event_key = fixture["event_key"]
    rows = conn.execute(
        "SELECT region, bookmaker, outcomes FROM odds_snapshots"
        " WHERE event_key=? AND market=?"
        " AND fetched_at=(SELECT MAX(fetched_at) FROM odds_snapshots"
        "                 WHERE event_key=? AND market=?)"
        " ORDER BY id",                                 # 稳定序：平价保留先落库者
        (event_key, market, event_key, market)).fetchall()
    if not rows:
        return {}
    snaps = [
        OddsSnapshot(league=fixture["league"], event_key=event_key,
                     kickoff_utc=fixture["kickoff_utc"],
                     home_name=fixture["home_name"], away_name=fixture["away_name"],
                     market=market, region=row["region"],
                     bookmaker=row["bookmaker"],
                     outcomes=json.loads(row["outcomes"]))
        for row in rows
    ]
    return best_prices(snaps, market)


# ---------------------------------------------------------------- 落库


def _upsert_recommendation(conn: sqlite3.Connection, *, run_id: int, fixture_id: int,
                           market: str, phase: str, model_p: float, market_p: float,
                           best_odds: float, bookmaker: str, edge: float, ev: float,
                           kelly: float, created_at: str) -> int:
    """UNIQUE 冲突 → DO UPDATE 刷新价格类字段（persona 三列留给 M4，不清）。"""
    conn.execute(
        "INSERT INTO recommendations (run_id, fixture_id, strategy, market, phase,"
        " model_p, market_p, best_odds, bookmaker, edge, ev, kelly_stake_frac,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(fixture_id, market, strategy, phase) DO UPDATE SET"
        "   run_id=excluded.run_id, model_p=excluded.model_p,"
        "   market_p=excluded.market_p, best_odds=excluded.best_odds,"
        "   bookmaker=excluded.bookmaker, edge=excluded.edge, ev=excluded.ev,"
        "   kelly_stake_frac=excluded.kelly_stake_frac, created_at=excluded.created_at",
        (run_id, fixture_id, STRATEGY, market, phase, model_p, market_p, best_odds,
         bookmaker, edge, ev, kelly, created_at),
    )
    row = conn.execute(
        "SELECT id FROM recommendations WHERE fixture_id=? AND market=? AND strategy=?"
        " AND phase=?", (fixture_id, market, STRATEGY, phase)).fetchone()
    return int(row["id"])


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
