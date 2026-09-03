"""场次选择器（设计 §4）：divergence（病例-对照）与 manual。

div = ln(mkt_p_outcome / model_p_outcome)，正=模型比市场差（log-loss 差的
单场等价形式：−ln p_model − (−ln p_mkt) = ln(p_mkt/p_model)）。
对照池 = div ≤ 中位数的行（模型未显著跑输市场者），按联赛比例分层、
random.Random(seed) 抽样——同 seed 必同结果（可复现是验收口径）。
"""
import math
import random
import sqlite3

from fa.backtest.metrics import fetch_predictions

_IDX = {"H": "p_home", "D": "p_draw", "A": "p_away"}
_MKT_IDX = {"H": "mkt_home", "D": "mkt_draw", "A": "mkt_away"}


def _divergence(r: dict) -> float:
    """单场模型 vs 市场的 log-loss 差；概率缺失返回 0（不参与 top 排序）。"""
    o = r["outcome"]
    pm, pk = r[_IDX[o]], r[_MKT_IDX[o]]
    if not pm or not pk:
        return 0.0
    return math.log(pk / pm)


def _with_teams(conn: sqlite3.Connection, rows: list[dict]) -> list[dict]:
    names = {r["id"]: r["name"] for r in conn.execute(
        "SELECT id, name FROM teams")}
    out = []
    for r in rows:
        m = conn.execute(
            "SELECT home_team_id h, away_team_id a FROM matches WHERE id=?",
            (r["match_id"],)).fetchone()
        r["home"] = names.get(m["h"])
        r["away"] = names.get(m["a"])
        r["div"] = _divergence(r)
        out.append(r)
    return out


def divergence_rows(conn, date_from=None, date_to=None, leagues=None) -> list[dict]:
    rows = fetch_predictions(conn, leagues=leagues)
    rows = [r for r in rows
            if (date_from is None or r["date"] >= date_from)
            and (date_to is None or r["date"] <= date_to)]
    return sorted(_with_teams(conn, rows), key=lambda r: -r["div"])


def select_divergence(conn, date_from=None, date_to=None, leagues=None,
                      top_k=20, control_k=10, seed=42) -> list[dict]:
    rows = divergence_rows(conn, date_from, date_to, leagues)
    cases = rows[:top_k]
    for r in cases:
        r["is_control"] = False
    pool = rows[top_k:]
    if pool and control_k > 0:
        divs = sorted(r["div"] for r in pool)
        median = divs[len(divs) // 2]
        below = [r for r in pool if r["div"] <= median]
        by_lg: dict[str, list[dict]] = {}
        for r in below:
            by_lg.setdefault(r["league"], []).append(r)
        rng = random.Random(seed)
        ctrls = []
        for lg in sorted(by_lg):                     # 联赛序稳定 → 抽样可复现
            take = max(1, round(control_k * len(by_lg[lg]) / max(len(below), 1)))
            take = min(take, len(by_lg[lg]), control_k - len(ctrls))
            if take > 0:
                ctrls += rng.sample(by_lg[lg], take)
        for r in ctrls:
            r["is_control"] = True
        cases += ctrls
    return cases


def select_manual(conn, match_ids=None, league=None, season=None) -> list[dict]:
    rows = fetch_predictions(
        conn, leagues=[league] if league else None,
        seasons=[season] if season else None)
    if match_ids:
        want = set(match_ids)
        rows = [r for r in rows if r["match_id"] in want]
    out = _with_teams(conn, rows)
    for r in out:
        r["is_control"] = False
    return sorted(out, key=lambda r: r["match_id"])
