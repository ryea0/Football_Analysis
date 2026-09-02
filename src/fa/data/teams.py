import sqlite3


def get_or_create_team(conn: sqlite3.Connection, league: str, name: str) -> int:
    """按 (league, name) 取队 id，不存在则注册；canonical 名以 football-data.co.uk 原文为准。"""
    row = conn.execute(
        "SELECT id FROM teams WHERE league=? AND name=?", (league, name)
    ).fetchone()
    if row is not None:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO teams (league, name) VALUES (?, ?)", (league, name))
    return cur.lastrowid


def resolve_team(conn: sqlite3.Connection, league: str, name: str,
                 source: str) -> int | None:
    """canonical 名优先，其次该 source 的别名；都对不上则返回 None（调用方须 record_unknown）。"""
    # 两分支列名统一别名为 team_id，供同一 return 表达式读取
    row = conn.execute(
        "SELECT id AS team_id FROM teams WHERE league=? AND name=?",
        (league, name)).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT team_id FROM team_aliases WHERE source=? AND alias=?",
            (source, name)).fetchone()
    return None if row is None else row["team_id"]


def record_unknown(conn: sqlite3.Connection, source: str, name: str) -> None:
    """未知队名入隔离表（幂等）；绝不静默丢弃。"""
    conn.execute(
        "INSERT INTO unknown_names (source, name, first_seen) "
        "VALUES (?, ?, date('now')) "
        "ON CONFLICT(source, name) DO NOTHING",
        (source, name),
    )
