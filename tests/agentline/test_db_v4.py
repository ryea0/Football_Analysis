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


# ---------------------------------------------------------------------------
# schema v7：A_multi 词表位 + attributor 列 + UNIQUE 三元组（A_multi 计划 T1）
# ---------------------------------------------------------------------------

def test_v7_attributor_and_amulti_vocab(tmp_path):
    """v7：attributor 列 + A_multi 词表位 + UNIQUE 三元组（成员可同场共存）。"""
    from fa.db import connect, init_db
    init_db(tmp_path / "t.db")
    conn = connect(tmp_path / "t.db")
    conn.execute("PRAGMA foreign_keys=OFF")    # 假 match_id（同 test_store 的 db fixture）
    try:
        cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(agentline_predictions)")}
        assert "attributor" in cols
        ok = {"status": "ok", "p_home": 0.4, "p_draw": 0.3, "p_away": 0.3,
              "p_over25": 0.5, "confidence": 0.6, "reasoning_digest": "d",
              "sources_json": "[]", "repaired": False}
        from fa.agentline.store import save_prediction
        save_prediction(conn, 1, "A_multi", ok, "raw", "dsh", "m", 1.0, 1)
        save_prediction(conn, 1, "A_multi", ok, "raw", "dsh", "m", 1.0, 2)
        save_prediction(conn, 1, "A_multi", ok, "raw", "dsh", "m", 1.0, 0)  # 聚合
        n = conn.execute("SELECT COUNT(*) c FROM agentline_predictions"
                         ).fetchone()["c"]
        assert n == 3                       # 同 match 同 line 三行共存
    finally:
        conn.close()


def test_v6_migrates_to_v7_rebuild(tmp_path):
    """v6 库（无 attributor、旧 UNIQUE）经 init_db 重建升 v7，数据保全。"""
    from fa.db import connect, init_db
    db = tmp_path / "v6.db"
    init_db(db)                                     # v7 新建
    conn = connect(db)
    old = {r["name"] for r in conn.execute(
        "PRAGMA table_info(agentline_predictions)")}
    assert "attributor" in old
    # 造 v6 形状：去掉 attributor 列 + 改回二元 UNIQUE（重建路径必须应对）。
    # SQLite ≥3.35 仍禁止 DROP 参与 UNIQUE 约束的列——UNIQUE 三元组含
    # attributor，ALTER DROP COLUMN 直接报 cannot drop，故只能 DROP TABLE 后
    # 按 v6 DDL 原样重建（与 v5 迁移分支当年建出的形状逐列一致，含
    # idx_alp_line——重建迁移必须把命名索引一并接回来）。
    conn.executescript(
        "DROP TABLE agentline_predictions;"
        "CREATE TABLE agentline_predictions ("
        "    id                INTEGER PRIMARY KEY,"
        "    match_id          INTEGER NOT NULL REFERENCES matches(id),"
        "    line              TEXT NOT NULL"
        "        CHECK (line IN ('A_base', 'A_enh')),"
        "    p_home REAL, p_draw REAL, p_away REAL, p_over25 REAL,"
        "    confidence        REAL,"
        "    reasoning_digest  TEXT,"
        "    sources_json      TEXT,"
        "    raw_output        TEXT NOT NULL,"
        "    status            TEXT NOT NULL"
        "        CHECK (status IN ('ok', 'parse_fail', 'timeout', 'error')),"
        "    repaired          INTEGER,"
        "    harness           TEXT,"
        "    model             TEXT,"
        "    duration_s        REAL,"
        "    created_at        TEXT NOT NULL,"
        "    UNIQUE (match_id, line));"
        "CREATE INDEX IF NOT EXISTS idx_alp_line"
        " ON agentline_predictions (line);")
    conn.execute("UPDATE schema_version SET version=6")
    # 存量行外键必须有效（真库 200 行全经 save_prediction 在 FK ON 下写入），
    # 否则重建的 INSERT..SELECT 会撞 FOREIGN KEY——先备好 matches 父行
    conn.execute(
        "INSERT INTO teams (id, league, name) VALUES (1, 'E0', 'Arsenal')")
    conn.execute(
        "INSERT INTO matches (id, league, season, date, home_team_id,"
        " away_team_id, raw_line) VALUES (1, 'E0', 2025, '2025-08-16', 1, 1,"
        " '{}'), (2, 'E0', 2025, '2025-08-17', 1, 1, '{}')")
    # 塞两行存量数据，迁移后必须保全
    for i in (1, 2):
        conn.execute(
            "INSERT INTO agentline_predictions (match_id, line, p_home,"
            " p_draw, p_away, p_over25, confidence, reasoning_digest,"
            " sources_json, raw_output, status, repaired, harness, model,"
            " duration_s, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (i, "A_base", 0.4, 0.3, 0.3, 0.5, 0.6, "d", "[]", "r", "ok",
             0, "dsh", "m", 1.0, "2026-09-04T00:00:00Z"))
    conn.commit()
    conn.close()
    init_db(db)                                     # v6 -> v7 重建
    conn = connect(db)
    try:
        assert conn.execute("SELECT version FROM schema_version"
                            ).fetchone()["version"] == 7
        cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(agentline_predictions)")}
        assert "attributor" in cols
        rows = conn.execute("SELECT match_id, attributor FROM"
                            " agentline_predictions ORDER BY match_id").fetchall()
        assert [(r["match_id"], r["attributor"]) for r in rows] \
            == [(1, 1), (2, 1)]                     # 存量保全 + 回填 1
        # 重建不得丢命名索引（rename 后旧表占住 idx_alp_line 名，若不先摘，
        # _AL_TABLE 的 IF NOT EXISTS 会静默跳过、DROP 旧表时随之消失）
        idx = {r["name"] for r in conn.execute("PRAGMA index_list("
                                               "agentline_predictions)")}
        assert "idx_alp_line" in idx
    finally:
        conn.close()
