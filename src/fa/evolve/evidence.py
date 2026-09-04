"""窗口×联赛证据聚合（设计档 §7）——反思的唯一证据源，只读。

三轨各查各账：kb/model_persona、nokb/model_persona_nokb、对照 ref/model_only。
未结算注不进 ROI、单列计数；误杀对照取 model_only 同 (fixture, market) 已结算
注的实际收益率——双轨天然提供反事实，确定性、无模拟。

窗口归属按北京日期：created_at 是 UTC ISO 'Z' 串，``date(created_at,'+8 hours')``
即北京时间日期，落在 [opened, closes) 才入账。
"""
from __future__ import annotations

import sqlite3
import statistics

from fa.evolve.windows import Window

_SETTLED = ("won", "lost")
_EPS = 1e-9

_WINDOW = ("date(r.created_at, '+8 hours') >= ?"
           " AND date(r.created_at, '+8 hours') < ?")


def _judged(conn, opened: str, closes: str, league: str, strategy: str) -> list[dict]:
    """每场首条判决行（verdict 广播同场同轨必然一致，取 MIN(id) 稳定）。"""
    return [dict(r) for r in conn.execute(
        "SELECT r.fixture_id, r.verdict, r.confidence_delta"
        " FROM recommendations r JOIN fixtures f ON f.id = r.fixture_id"
        f" WHERE f.league=? AND r.strategy=? AND r.verdict IS NOT NULL AND {_WINDOW}"
        " GROUP BY r.fixture_id"
        " HAVING r.id = MIN(r.id)"
        " ORDER BY r.fixture_id", (league, strategy, opened, closes))]


def _bet_stats(conn, opened: str, closes: str, league: str, strategy: str) -> dict:
    rows = conn.execute(
        "SELECT b.status, b.stake, b.return_amt, b.clv"
        " FROM bets b JOIN recommendations r ON r.id = b.recommendation_id"
        " JOIN fixtures f ON f.id = r.fixture_id"
        f" WHERE b.mode='paper' AND f.league=? AND r.strategy=? AND {_WINDOW}",
        (league, strategy, opened, closes)).fetchall()
    settled = [r for r in rows if r["status"] in _SETTLED]
    staked = sum(r["stake"] for r in settled)
    returned = sum(r["return_amt"] or 0.0 for r in settled)
    clvs = [r["clv"] for r in settled if r["clv"] is not None]
    return {"n_bets": len(rows), "n_settled": len(settled),
            "roi": ((returned - staked) / staked) if staked else None,
            "clv_median": (statistics.median(clvs) if clvs else None),
            "clv_mean": (statistics.fmean(clvs) if clvs else None)}


def _track(conn, opened: str, closes: str, league: str, strategy: str) -> dict:
    judged = _judged(conn, opened, closes, league, strategy)
    n_fix = conn.execute(
        "SELECT COUNT(DISTINCT r.fixture_id) AS n FROM recommendations r"
        " JOIN fixtures f ON f.id = r.fixture_id"
        f" WHERE f.league=? AND r.strategy=? AND {_WINDOW}",
        (league, strategy, opened, closes)).fetchone()["n"]
    verdicts = {"agree": 0, "downweight": 0, "veto": 0}
    for j in judged:
        if j["verdict"] in verdicts:
            verdicts[j["verdict"]] += 1
    out = {"n_fixtures": n_fix, "n_judged": len(judged), "verdicts": verdicts}
    out.update(_bet_stats(conn, opened, closes, league, strategy))
    return out


def _mo_ref(conn, opened: str, closes: str, league: str) -> dict:
    stats = _bet_stats(conn, opened, closes, league, "model_only")
    return {"n_bets": stats["n_bets"], "n_settled": stats["n_settled"],
            "roi": stats["roi"]}


def _label(conn, fixture_id: int) -> dict:
    row = conn.execute(
        "SELECT f.kickoff_utc, th.name AS home, ta.name AS away"
        " FROM fixtures f LEFT JOIN teams th ON th.id=f.home_team_id"
        " LEFT JOIN teams ta ON ta.id=f.away_team_id WHERE f.id=?",
        (fixture_id,)).fetchone()
    if row is None:
        return {"date": None, "home": None, "away": None}
    return {"date": (row["kickoff_utc"] or "")[:10], "home": row["home"],
            "away": row["away"]}


def _kills(conn, opened: str, closes: str, league: str) -> list[dict]:
    # nokb 首判决按场备查（设计档 §7 kills 项须带 nokb_verdict；
    # 该场 nokb 轨无行时如实落 None）
    nokb = {j["fixture_id"]: j for j in
            _judged(conn, opened, closes, league, "model_persona_nokb")}
    rows = conn.execute(
        "SELECT r.fixture_id, r.market, r.verdict, r.final_stake_frac"
        " FROM recommendations r JOIN fixtures f ON f.id = r.fixture_id"
        f" WHERE f.league=? AND r.strategy='model_persona'"
        " AND r.verdict IN ('veto','downweight') AND {_WINDOW}"
        " ORDER BY r.fixture_id, r.market", (league, opened, closes)).fetchall()
    out = []
    for r in rows:
        mo = conn.execute(
            "SELECT b.return_amt, b.stake FROM bets b"
            " JOIN recommendations r2 ON r2.id = b.recommendation_id"
            " WHERE r2.fixture_id=? AND r2.market=? AND r2.strategy='model_only'"
            " AND b.mode='paper' AND b.status IN ('won','lost')",
            (r["fixture_id"], r["market"])).fetchone()
        item = {"fixture_id": r["fixture_id"], "market": r["market"],
                "kb_verdict": r["verdict"],
                "nokb_verdict": (nokb.get(r["fixture_id"]) or {}).get("verdict"),
                "kb_final_frac": r["final_stake_frac"],
                "mo_return_on_stake": None}
        if mo is not None and mo["stake"]:
            item["mo_return_on_stake"] = ((mo["return_amt"] or 0.0)
                                          - mo["stake"]) / mo["stake"]
        item.update(_label(conn, r["fixture_id"]))
        out.append(item)
    return out


def _divergences(conn, opened: str, closes: str, league: str) -> list[dict]:
    kb = {j["fixture_id"]: j for j in
          _judged(conn, opened, closes, league, "model_persona")}
    nokb = {j["fixture_id"]: j for j in
            _judged(conn, opened, closes, league, "model_persona_nokb")}
    out = []
    for fid in sorted(set(kb) & set(nokb)):
        a, b = kb[fid], nokb[fid]
        da = a["confidence_delta"] or 0.0
        db = b["confidence_delta"] or 0.0
        if a["verdict"] != b["verdict"] or abs(da - db) > _EPS:
            out.append({"fixture_id": fid, "kb_verdict": a["verdict"],
                        "nokb_verdict": b["verdict"],
                        "kb_conf_delta": da, "nokb_conf_delta": db})
    return out


def window_evidence(conn: sqlite3.Connection, w: Window, league: str) -> dict:
    opened, closes = w.opened.isoformat(), w.closes.isoformat()
    kb = _track(conn, opened, closes, league, "model_persona")
    nokb = _track(conn, opened, closes, league, "model_persona_nokb")
    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM bets b"
        " JOIN recommendations r ON r.id = b.recommendation_id"
        " JOIN fixtures f ON f.id = r.fixture_id"
        f" WHERE b.mode='paper' AND b.status='pending' AND f.league=?"
        " AND r.strategy IN ('model_persona','model_persona_nokb') AND {_WINDOW}",
        (league, opened, closes)).fetchone()["n"]
    return {"league": league,
            "window": {"idx": w.idx, "from": opened, "to": closes},
            "kb_track": kb, "nokb_track": nokb,
            "model_only_ref": _mo_ref(conn, opened, closes, league),
            "kills": _kills(conn, opened, closes, league),
            "divergences": _divergences(conn, opened, closes, league),
            "n_pending_settlement": pending}
