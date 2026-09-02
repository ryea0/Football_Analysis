import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta


@dataclass
class MatchWeek:
    index: int
    start: str
    end: str
    match_ids: list[int] = field(default_factory=list)


def _week_start(d: date) -> date:
    """比赛周从周四开始（欧陆赛程惯例：周中轮属上一周）。"""
    return d - timedelta(days=(d.weekday() - 3) % 7)


def iter_matchweeks(conn: sqlite3.Connection, league: str,
                    season: int) -> list[MatchWeek]:
    rows = conn.execute(
        "SELECT id, date FROM matches "
        "WHERE league=? AND season=? AND date IS NOT NULL ORDER BY date",
        (league, season)).fetchall()
    weeks: list[MatchWeek] = []
    cur: MatchWeek | None = None
    for r in rows:
        d = date.fromisoformat(r["date"])
        ws = _week_start(d)
        if cur is None or ws.isoformat() != cur.start:
            if cur is not None:
                weeks.append(cur)
            cur = MatchWeek(index=0, start=ws.isoformat(),
                            end=r["date"], match_ids=[r["id"]])
        else:
            cur.match_ids.append(r["id"])
            cur.end = r["date"]
    if cur is not None:
        weeks.append(cur)
    for i, w in enumerate(weeks, start=1):
        w.index = i
    return weeks


def training_matches(conn: sqlite3.Connection, league: str,
                     asof: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM matches WHERE league=? AND date < ? ORDER BY date",
        (league, asof)).fetchall()
    return [dict(r) for r in rows]
