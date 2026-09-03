"""信息集打包器（设计 §5.1）：给线 A 的「赛前可见」数据快照。

防泄漏是结构性约束：recent/H2H/standings 的每条 SQL 都带 date < match_date，
评审时请检查这一点——这是对比实验公平性的根基。窗口 N=10：与 Dixon-Coles
训练窗口信息量可比，不偏向任何一线（设计 §13 开放问题在此定为 10）。
"""
import json
import random
import sqlite3
from pathlib import Path

_WINDOW = 10


def _recent(conn: sqlite3.Connection, team_id: int, before: str) -> list[dict]:
    rows = conn.execute(
        "SELECT m.date, m.home_team_id, m.away_team_id, m.fthg, m.ftag,"
        " m.shots_home, m.shots_away, m.corners_home, m.corners_away,"
        " h.name AS home_name, a.name AS away_name"
        " FROM matches m JOIN teams h ON h.id=m.home_team_id"
        " JOIN teams a ON a.id=m.away_team_id"
        " WHERE (m.home_team_id=? OR m.away_team_id=?)"
        " AND m.date < ? AND m.fthg IS NOT NULL"
        " ORDER BY m.date DESC LIMIT ?", (team_id, team_id, before, _WINDOW))
    out = []
    for r in rows:
        home = r["home_team_id"] == team_id
        out.append({"date": r["date"],
                    # agent 读不懂裸 id（opponent_id=5 无强度信息）——对手一律解析成队名
                    "opponent": r["away_name"] if home else r["home_name"],
                    "venue": "H" if home else "A",
                    "gf": r["fthg"] if home else r["ftag"],
                    "ga": r["ftag"] if home else r["fthg"],
                    "shots": r["shots_home"] if home else r["shots_away"],
                    "corners": r["corners_home"] if home else r["corners_away"]})
    return out


def _h2h(conn, a: int, b: int, before: str) -> list[dict]:
    rows = conn.execute(
        "SELECT m.date, m.fthg, m.ftag, h.name AS home, x.name AS away"
        " FROM matches m JOIN teams h ON h.id=m.home_team_id"
        " JOIN teams x ON x.id=m.away_team_id"
        " WHERE ((m.home_team_id=? AND m.away_team_id=?)"
        " OR (m.home_team_id=? AND m.away_team_id=?))"
        " AND m.date < ? AND m.fthg IS NOT NULL"
        " ORDER BY m.date DESC LIMIT ?", (a, b, b, a, before, _WINDOW))
    # gf/ga 恒取主队视角（=库中 fthg/ftag 的存储方向），不随目标队翻转：
    # 同一行对不同目标场语义不漂移，对账时可与库值一一对应；
    # 定向由消费方按随行的 home/away 队名自行判断。
    return [{"date": r["date"], "home": r["home"], "away": r["away"],
             "gf": r["fthg"], "ga": r["ftag"]} for r in rows]


def _standing(conn, league: str, season: int, team_id: int, before: str) -> dict:
    played = won = drawn = 0
    for r in conn.execute(
            "SELECT home_team_id, away_team_id, fthg, ftag FROM matches"
            " WHERE league=? AND season=? AND date < ? AND fthg IS NOT NULL",
            (league, season, before)):
        if r["home_team_id"] == team_id:
            gf, ga = r["fthg"], r["ftag"]
        elif r["away_team_id"] == team_id:
            gf, ga = r["ftag"], r["fthg"]
        else:
            continue
        played += 1
        won += gf > ga
        drawn += gf == ga
    return {"played": played, "pts": 3 * won + drawn}


def export_info_set(conn: sqlite3.Connection, match_id: int) -> dict:
    m = conn.execute(
        "SELECT m.league, m.season, m.date, m.home_team_id, m.away_team_id,"
        " m.psc_home, m.psc_draw, m.psc_away, m.over25_psc, m.under25_psc,"
        " h.name AS home, a.name AS away"
        " FROM matches m JOIN teams h ON h.id=m.home_team_id"
        " JOIN teams a ON a.id=m.away_team_id WHERE m.id=?",
        (match_id,)).fetchone()
    if m is None:
        raise ValueError(f"match {match_id} 不存在")
    return {
        "match": {"league": m["league"], "season": m["season"],
                  "date": m["date"], "home": m["home"], "away": m["away"]},
        "odds": {k: m[k] for k in ("psc_home", "psc_draw", "psc_away",
                                   "over25_psc", "under25_psc")},
        "home_recent": _recent(conn, m["home_team_id"], m["date"]),
        "away_recent": _recent(conn, m["away_team_id"], m["date"]),
        "h2h": _h2h(conn, m["home_team_id"], m["away_team_id"], m["date"]),
        "standings": {
            "home": _standing(conn, m["league"], m["season"],
                              m["home_team_id"], m["date"]),
            "away": _standing(conn, m["league"], m["season"],
                              m["away_team_id"], m["date"])},
    }


def pick_sample(conn, league: str, season: int, n: int,
                seed: int = 42) -> list[int]:
    """仅取线 P 已覆盖（backtest_predictions 有行）且收盘盘口齐全的场次。"""
    rows = [r["match_id"] for r in conn.execute(
        "SELECT bp.match_id FROM backtest_predictions bp"
        " JOIN matches m ON m.id = bp.match_id"
        " WHERE bp.league=? AND bp.season=?"
        " AND m.psc_home IS NOT NULL AND m.psc_draw IS NOT NULL"
        " AND m.psc_away IS NOT NULL ORDER BY bp.match_id",
        (league, season))]
    random.Random(seed).shuffle(rows)
    return sorted(rows[:n])


def export_batch(conn, match_ids: list[int], out_dir: Path) -> list[Path]:
    """每场一个 <match_id>.json；已存在跳过（幂等，续跑不重写）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for mid in match_ids:
        p = out_dir / f"{mid}.json"
        if p.exists():
            continue
        p.write_text(json.dumps(export_info_set(conn, mid),
                                ensure_ascii=False, indent=1), encoding="utf-8")
        written.append(p)
    return written
