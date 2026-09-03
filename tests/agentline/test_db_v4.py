"""agentline 两表（设计 §6）：v4 新建与 v3 迁移路径表结构一致。"""
import sqlite3

from fa.db import _migrate_up, connect, init_db, SCHEMA_VERSION


def _cols(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_fresh_db_has_agentline_tables(tmp_path):
    init_db(tmp_path / "t.db")
    conn = connect(tmp_path / "t.db")
    assert _cols(conn, "agentline_predictions") >= {
        "match_id", "line", "p_home", "status", "raw_output", "harness"}
    assert _cols(conn, "agentline_runs") >= {"line", "n_ok", "n_error"}
    assert conn.execute("SELECT version FROM schema_version").fetchone()[
        "version"] == SCHEMA_VERSION
    conn.close()


def test_migrate_v3_to_v4(tmp_path):
    # 先造 v3 库（建表后把版本号钉回 3），再 init_db 触发迁移
    init_db(tmp_path / "t.db")
    conn = connect(tmp_path / "t.db")
    conn.execute("UPDATE schema_version SET version=3")
    conn.commit(); conn.close()
    init_db(tmp_path / "t.db")                      # v3 → v4
    conn = connect(tmp_path / "t.db")
    assert _cols(conn, "agentline_predictions")     # 迁移路径同样有表
    conn.close()


def test_line_check_constraint(tmp_path):
    init_db(tmp_path / "t.db")
    conn = connect(tmp_path / "t.db")
    conn.execute("PRAGMA foreign_keys=OFF")         # 测试只验 CHECK，不造 matches 行
    try:
        conn.execute(
            "INSERT INTO agentline_predictions (match_id, line, raw_output,"
            " status, created_at) VALUES (1, 'B_line', 'x', 'ok', '2026-09-04')")
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("line 词表未生效")
    finally:
        conn.close()


def test_migrate_up_v3_branch_creates_tables(tmp_path):
    # init_db 会先跑 _SCHEMA 把缺口兜掉，上面的 test_migrate_v3_to_v4 测不出
    # 迁移分支死活（表在首次建库时已存在）。这里绕开 _SCHEMA 造最小 v3 库、
    # 单独跑 _migrate_up，实证 v3→v4 分支真的建表（同 test_db.py 的手法）。
    p = tmp_path / "t.db"
    raw = sqlite3.connect(p)
    raw.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    raw.execute("INSERT INTO schema_version (version) VALUES (3)")
    raw.commit(); raw.close()

    conn = connect(p)
    _migrate_up(conn, 3)
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"agentline_predictions", "agentline_runs"} <= names
    assert conn.execute(
        "SELECT version FROM schema_version").fetchone()["version"] == SCHEMA_VERSION
    conn.close()
