"""归因信息集打包（设计 §5）——赛后视角：看赛果合法，但赛前信息的一切查询
以 date < match_date 为界（防泄漏是结构性保障，不靠 prompt 自觉）。

pack 键：match / recent_home / recent_away / h2h / standings / odds /
prediction / outcome / divergence。JSON 落盘 = 重放凭据。
"""
import json
import sqlite3
from pathlib import Path

RECENT_N = 10


def _matches_between(conn: sqlite3.Connection, date: str, t1: int,
                     t2: int, limit: int) -> list[dict]:
    rows = conn.execute(
        "SELECT m.date, th.name home, ta.name away, m.fthg, m.ftag,"
        " m.shots_home, m.shots_away, m.shots_target_home,"
        " m.shots_target_away, m.corners_home, m.corners_away"
        " FROM matches m JOIN teams th ON th.id=m.home_team_id"
        " JOIN teams ta ON ta.id=m.away_team_id"
        " WHERE m.date < ? AND (m.home_team_id IN (?, ?)"
        " OR m.away_team_id IN (?, ?))"
        " ORDER BY m.date DESC LIMIT ?", (date, t1, t2, t1, t2, limit))
    return [dict(r) for r in rows]


def _standings(conn, league: str, season: int, date: str,
               team_ids: tuple[int, int]) -> dict:
    """截 date 前的积分表，只输出所涉两队：{name: {pos, pts, played}}。"""
    rows = conn.execute(
        "SELECT m.home_team_id h, m.away_team_id a, m.fthg, m.ftag,"
        " th.name hn, ta.name an"
        " FROM matches m JOIN teams th ON th.id=m.home_team_id"
        " JOIN teams ta ON ta.id=m.away_team_id"
        " WHERE m.league=? AND m.season=? AND m.date < ?"
        " AND m.fthg IS NOT NULL", (league, season, date)).fetchall()
    pts: dict[int, int] = {}
    played: dict[int, int] = {}
    names: dict[int, str] = {}
    for r in rows:
        for tid, name in ((r["h"], r["hn"]), (r["a"], r["an"])):
            names[tid] = name
            played[tid] = played.get(tid, 0) + 1
            pts[tid] = pts.get(tid, 0)
        hg, ag = r["fthg"], r["ftag"]
        if hg > ag:
            pts[r["h"]] += 3
        elif hg == ag:
            pts[r["h"]] += 1
            pts[r["a"]] += 1
        else:
            pts[r["a"]] += 3
    order = sorted(pts, key=lambda t: (-pts[t], t))
    out = {}
    for tid in team_ids:
        if tid in pts:
            out[names[tid]] = {"pos": order.index(tid) + 1,
                               "pts": pts[tid], "played": played[tid]}
    return out


def build_pack(conn: sqlite3.Connection, cand: dict) -> dict:
    m = conn.execute("SELECT * FROM matches WHERE id=?",
                     (cand["match_id"],)).fetchone()
    home_id, away_id = m["home_team_id"], m["away_team_id"]
    bp = conn.execute("SELECT * FROM backtest_predictions WHERE match_id=?",
                      (cand["match_id"],)).fetchone()
    return {
        "match": {"match_id": cand["match_id"], "league": m["league"],
                  "season": m["season"], "date": m["date"],
                  "home": cand["home"], "away": cand["away"]},
        "recent_home": _matches_between(conn, m["date"], home_id, home_id,
                                        RECENT_N),
        "recent_away": _matches_between(conn, m["date"], away_id, away_id,
                                        RECENT_N),
        "h2h": _matches_between(conn, m["date"], home_id, away_id, 10),
        "standings": _standings(conn, m["league"], m["season"], m["date"],
                                (home_id, away_id)),
        "odds": {"ps_home": m["ps_home"], "ps_draw": m["ps_draw"],
                 "ps_away": m["ps_away"], "psc_home": m["psc_home"],
                 "psc_draw": m["psc_draw"], "psc_away": m["psc_away"]},
        "prediction": {"p_home": bp["p_home"], "p_draw": bp["p_draw"],
                       "p_away": bp["p_away"], "p_over25": bp["p_over25"],
                       "mkt_home": bp["mkt_home"], "mkt_draw": bp["mkt_draw"],
                       "mkt_away": bp["mkt_away"],
                       "mkt_over25": bp["mkt_over25"]},
        "outcome": {"fthg": m["fthg"], "ftag": m["ftag"],
                    "result": bp["outcome"],
                    "total_goals": bp["total_goals"]},
        "divergence": {"div": cand["div"],
                       "note": "div=ln(mkt_p_outcome/model_p_outcome)，"
                               "正=模型比市场差"},
    }


def write_packs(conn: sqlite3.Connection, cands: list[dict],
                out_dir: Path) -> dict[int, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for cand in cands:
        name = f"{cand['match_id']}.json"
        (out_dir / name).write_text(
            json.dumps(build_pack(conn, cand), ensure_ascii=False, indent=1),
            encoding="utf-8")
        paths[cand["match_id"]] = name
    return paths
