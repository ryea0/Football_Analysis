import hashlib
import json
import sqlite3

from fa.data.parse import MatchRow
from fa.data.teams import get_or_create_team
from fa.db import get_meta, set_meta


def _rows_hash(rows: list[MatchRow]) -> str:
    payload = "\n".join(
        f"{r.date}|{r.home}|{r.away}|{r.fthg}-{r.ftag}|{r.ps_home}|{r.psc_home}"
        for r in rows)
    return hashlib.sha256(payload.encode()).hexdigest()


def ingest_rows(conn: sqlite3.Connection, league: str, season: int,
                rows: list[MatchRow]) -> int:
    key = f"csv_hash:{league}:{season}"
    h = _rows_hash(rows)
    if get_meta(conn, key) == h:
        return 0
    conn.execute("DELETE FROM matches WHERE league=? AND season=?",
                 (league, season))
    inserted = 0
    for r in rows:
        home_id = get_or_create_team(conn, league, r.home)
        away_id = get_or_create_team(conn, league, r.away)
        conn.execute(
            "INSERT INTO matches (league, season, date, home_team_id, away_team_id,"
            " fthg, ftag, shots_home, shots_away, shots_target_home, shots_target_away,"
            " corners_home, corners_away, ps_home, ps_draw, ps_away,"
            " psc_home, psc_draw, psc_away, over25_ps, under25_ps,"
            " over25_psc, under25_psc, raw_line)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r.league, r.season, r.date, home_id, away_id,
             r.fthg, r.ftag, r.shots_home, r.shots_away,
             r.shots_target_home, r.shots_target_away,
             r.corners_home, r.corners_away,
             r.ps_home, r.ps_draw, r.ps_away,
             r.psc_home, r.psc_draw, r.psc_away,
             r.over25_ps, r.under25_ps, r.over25_psc, r.under25_psc,
             json.dumps(r.raw, ensure_ascii=False)))
        inserted += 1
    set_meta(conn, key, h)
    conn.commit()
    return inserted
