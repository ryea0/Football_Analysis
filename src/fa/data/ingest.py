import hashlib
import json
import sqlite3

from fa.data.parse import MatchRow
from fa.data.teams import get_or_create_team
from fa.db import get_meta, set_meta


def _rows_hash(rows: list[MatchRow]) -> str:
    """整行指纹：任意列的上游修正（含 ps_draw / shots 等非抽样字段）都改变哈希，重跑即收敛。"""
    payload = "\n".join(
        json.dumps(r.raw, ensure_ascii=False, sort_keys=True) for r in rows)
    return hashlib.sha256(payload.encode()).hexdigest()


def ingest_rows(conn: sqlite3.Connection, league: str, season: int,
                rows: list[MatchRow]) -> int:
    if not rows:
        return 0  # 空输入：不动分区，也绝不写入空内容哈希
    key = f"csv_hash:{league}:{season}"
    h = _rows_hash(rows)
    if get_meta(conn, key) == h:
        return 0
    try:
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
        # commit 一并放进 try：SQLITE_FULL / IO 错误时同样回滚，不留悬挂事务
        conn.commit()
    except BaseException:
        try:
            conn.rollback()
        except Exception:
            pass  # 回滚失败时保住原始异常
        raise
    return inserted
