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
    """canonical 名优先，其次该 source 的别名；都对不上则返回 None（调用方须 record_unknown）。

    两分支都按 ``league`` 收域（M4 §7-4 根治）：别名经 team_id 回连 teams 过滤
    联赛——跨联赛的同名别名不再穿透（实害：oddsapi 别名 ``Treviso`` 绑 I1 队，
    F1 fixture 同名队曾被错挂到 I1 的队、混进候选池，靠 persona veto 才暴露）。
    收域后查不到 → 调用方走归一化/模糊路径，多数进隔离表人工确认（可见），
    绝不静默穿透。已知边界：``UNIQUE(source, alias)`` 是全局键，两联赛真有完全
    同拼写的 oddsapi 名时第二个联赛绑不上（DO NOTHING 让位现值）——该侧会持续
    隔离直至人工起别名，属可见成本而非静默错挂。
    """
    # 两分支列名统一别名为 team_id，供同一 return 表达式读取
    row = conn.execute(
        "SELECT id AS team_id FROM teams WHERE league=? AND name=?",
        (league, name)).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT a.team_id FROM team_aliases a"
            " JOIN teams t ON t.id = a.team_id"
            " WHERE a.source=? AND a.alias=? AND t.league=?",
            (source, name, league)).fetchone()
    return None if row is None else row["team_id"]


def record_unknown(conn: sqlite3.Connection, source: str, name: str) -> None:
    """未知队名入隔离表（幂等）；绝不静默丢弃。"""
    conn.execute(
        "INSERT INTO unknown_names (source, name, first_seen) "
        "VALUES (?, ?, date('now')) "
        "ON CONFLICT(source, name) DO NOTHING",
        (source, name),
    )
