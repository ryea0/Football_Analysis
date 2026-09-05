"""schema v10（2026-09-05 设计 §3.4）：新表 division_jumps，纯加法无重建。"""
import sqlite3

from fa.db import SCHEMA_VERSION, connect, init_db


def test_fresh_db_has_division_jumps(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    conn = connect(db)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[
        "version"] == SCHEMA_VERSION == 10
    assert "agentline_division_jumps" in {
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    # 先备好 matches 父行（connect 内 FK ON）：否则下面的插入炸在外键上，
    # 目标 role CHECK / UNIQUE 二元根本没被测到（test_db_v9 同款前置）
    conn.execute(
        "INSERT INTO teams (id, league, name) VALUES (1, 'E0', 'Arsenal')")
    conn.execute(
        "INSERT INTO matches (id, league, season, date, home_team_id,"
        " away_team_id, raw_line) VALUES (1, 'E0', 2025, '2025-08-16', 1, 1,"
        " '{}')")
    conn.execute("INSERT INTO agentline_division_jumps (match_id, jump,"
                 " role, payload_json, raw_output, status, created_at)"
                 " VALUES (1,1,'archivist','{}','r','ok','2026-09-05')")
    # role CHECK 三词
    try:
        conn.execute("INSERT INTO agentline_division_jumps (match_id, jump,"
                     " role, payload_json, raw_output, status, created_at)"
                     " VALUES (1,2,'haha','{}','r','ok','2026-09-05')")
        raise AssertionError("role CHECK 未生效")
    except sqlite3.IntegrityError:
        pass
    # jump CHECK 1-3（终审 F2）：jump=4 的非法行必须被拒
    try:
        conn.execute("INSERT INTO agentline_division_jumps (match_id, jump,"
                     " role, payload_json, raw_output, status, created_at)"
                     " VALUES (1,4,'archivist','{}','r','ok','2026-09-05')")
        raise AssertionError("jump CHECK 未生效")
    except sqlite3.IntegrityError:
        pass
    # UNIQUE(match_id, jump)
    try:
        conn.execute("INSERT INTO agentline_division_jumps (match_id, jump,"
                     " role, payload_json, raw_output, status, created_at)"
                     " VALUES (1,1,'archivist','{}','r2','ok','2026-09-05')")
        raise AssertionError("UNIQUE(match_id, jump) 未生效")
    except sqlite3.IntegrityError:
        pass
    conn.close()


def test_v9_migrates_to_v10(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    conn = connect(db)
    conn.execute("UPDATE schema_version SET version=9")
    conn.commit()
    conn.close()
    init_db(db)                       # v9 → v10：IF NOT EXISTS 补表
    conn = connect(db)
    # 终审 F3：补表之外还须把版本号钉回 10——否则 init_db 下次启动又走迁移
    assert conn.execute("SELECT version FROM schema_version").fetchone()[
        "version"] == SCHEMA_VERSION == 10
    assert "agentline_division_jumps" in {
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()


def test_division_line_value_accepted(tmp_path):
    """词表五词在 v9 已备齐：A_division INSERT 直接过（无需重建验证）。"""
    db = tmp_path / "t.db"
    init_db(db)
    conn = connect(db)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("INSERT INTO agentline_predictions (match_id, line,"
                 " raw_output, status, created_at)"
                 " VALUES (1,'A_division','x','ok','2026-09-05')")
    conn.commit()
    conn.close()
