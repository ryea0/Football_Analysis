"""场次选择器（设计 §4）：divergence（病例-对照）、manual 与 paper_t1。

div = ln(mkt_p_outcome / model_p_outcome)，正=模型比市场差（log-loss 差的
单场等价形式：−ln p_model − (−ln p_mkt) = ln(p_mkt/p_model)）。
对照池 = div ≤ 中位数的行（模型未显著跑输市场者），按联赛比例分层、
random.Random(seed) 抽样——同 seed 必同结果（可复现是验收口径）。
"""
import math
import random
import sqlite3
from datetime import date

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


# ---- paper_t1（Stage 2，设计 §4）-----------------------------------------
# 前一比赛日 recommendations 所涉场次：fixture 按 kickoff UTC 日历日命中，
# 回连 matches 拿赛果与 A 线预测行。配对查询与 paper._paired_match 同模式
# （窗 [kickoff, kickoff+2 天]、(日期差, 日期, id) 最小者）——设计 §15 开放
# 问题同款处置：先独立实现同模式，两边都稳后再提取共享，不跨模块 import
# 私有名。缺 backtest_predictions 行的场次跳过并计数（v1 无该场模型概率
# 则信息集不完整，§5「三方预测」缺一不可——诚实计数，不硬跑）。

def _kick_day(kickoff_utc):
    try:
        return date.fromisoformat(kickoff_utc[:10])
    except (TypeError, ValueError):
        return None


def _pair_match(conn, league, home_id, away_id, kick):
    if kick is None or home_id is None or away_id is None:
        return None
    best, best_key = None, None
    for row in conn.execute(
            "SELECT * FROM matches WHERE league=? AND home_team_id=?"
            " AND away_team_id=? AND fthg IS NOT NULL AND ftag IS NOT NULL"
            " ORDER BY date, id", (league, home_id, away_id)).fetchall():
        try:
            played = date.fromisoformat(row["date"][:10])
        except (TypeError, ValueError):
            continue
        gap = (played - kick).days
        if gap < 0 or gap > 2:               # MAX_MATCH_DAY_GAP 同值（paper.py:54）
            continue
        key = (gap, row["date"], row["id"])
        if best_key is None or key < best_key:
            best, best_key = row, key
    return best


def select_paper_t1(conn, target_date: str) -> tuple[list[dict], dict]:
    """target_date = 比赛日（kickoff UTC 日历日，'YYYY-MM-DD'）。"""
    preds = {r["match_id"]: r for r in fetch_predictions(conn)}
    names = {r["id"]: r["name"] for r in conn.execute(
        "SELECT id, name FROM teams")}
    fixtures = conn.execute(
        "SELECT DISTINCT f.league, f.home_team_id, f.away_team_id,"
        " f.kickoff_utc FROM recommendations r"
        " JOIN fixtures f ON f.id = r.fixture_id"
        " WHERE substr(f.kickoff_utc, 1, 10) = ?", (target_date,)).fetchall()
    meta = {"n_fixtures": len(fixtures), "n_unpaired": 0,
            "n_no_prediction": 0}
    cands, seen = [], set()
    for f in fixtures:
        m = _pair_match(conn, f["league"], f["home_team_id"],
                        f["away_team_id"], _kick_day(f["kickoff_utc"]))
        if m is None:
            meta["n_unpaired"] += 1
            continue
        p = preds.get(m["id"])
        if p is None:
            meta["n_no_prediction"] += 1
            continue
        if m["id"] in seen:                  # 同 match 多 fixture（罕见）只取一次
            continue
        seen.add(m["id"])
        row = dict(p)
        row["home"] = names.get(m["home_team_id"])
        row["away"] = names.get(m["away_team_id"])
        # build_pack 契约：信息集「分歧摘要」（设计 §5.3）读 cand['div']——
        # 复用 divergence 同一 _divergence 定义（单一事实源），缺概率记 0
        row["div"] = _divergence(p)
        row["is_control"] = False
        cands.append(row)
    cands.sort(key=lambda r: r["match_id"])
    return cands, meta
