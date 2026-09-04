"""schema v9（2026-09-05 设计 §2.4/§5）：五词表 + budget_exhausted，v8→v9 影子重建。"""
import sqlite3

from fa.db import SCHEMA_VERSION, _AL_TABLE, connect, init_db


def _cols(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _pin(conn, version: int):
    conn.execute("UPDATE schema_version SET version=?", (version,))
    conn.commit()
    conn.close()


def _old_shape_db(path, n_rows=2):
    """手造 v8 形状的库：三词 CHECK、无 budget_exhausted、有存量行。"""
    init_db(path)
    conn = connect(path)
    conn.executescript("DROP INDEX IF EXISTS idx_alp_line;"
                       "DROP TABLE IF EXISTS agentline_predictions;")
    conn.executescript("""
        CREATE TABLE agentline_predictions (
            id INTEGER PRIMARY KEY, match_id INTEGER NOT NULL,
            line TEXT NOT NULL CHECK (line IN ('A_base','A_enh','A_multi')),
            attributor INTEGER NOT NULL DEFAULT 1,
            p_home REAL, p_draw REAL, p_away REAL, p_over25 REAL,
            confidence REAL, reasoning_digest TEXT, sources_json TEXT,
            raw_output TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('ok','parse_fail','timeout','error')),
            repaired INTEGER, harness TEXT, model TEXT, duration_s REAL,
            created_at TEXT NOT NULL,
            UNIQUE (match_id, line, attributor));""")
    conn.execute("CREATE INDEX idx_alp_line ON agentline_predictions (line);")
    # 存量行外键必须有效（迁移的 INSERT..SELECT 在 FK ON 下逐行校验，v7 先例
    # _seed_shadow 同款）——否则重建会撞 FOREIGN KEY 而非测到词表/回填本身
    conn.execute(
        "INSERT INTO teams (id, league, name) VALUES (1, 'E0', 'Arsenal')")
    conn.execute(
        "INSERT INTO matches (id, league, season, date, home_team_id,"
        " away_team_id, raw_line) VALUES (1, 'E0', 2025, '2025-08-16', 1, 1,"
        " '{}'), (2, 'E0', 2025, '2025-08-17', 1, 1, '{}')")
    for i in range(1, n_rows + 1):
        conn.execute(
            "INSERT INTO agentline_predictions (match_id, line, attributor,"
            " raw_output, status, created_at) VALUES (?,?,?,?,'ok',?)",
            (i, "A_multi" if i % 2 else "A_base", i % 2, "x", "2026-09-05"))
    _pin(conn, 8)
    return path


def test_v8_old_shape_migrates_to_v9(tmp_path):
    db = _old_shape_db(tmp_path / "t.db")
    init_db(db)
    conn = connect(db)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[
        "version"] == SCHEMA_VERSION
    rows = conn.execute("SELECT match_id, line, budget_exhausted FROM"
                        " agentline_predictions ORDER BY match_id").fetchall()
    assert [(r["match_id"], r["budget_exhausted"]) for r in rows] == [(1, 0), (2, 0)]
    conn.execute("PRAGMA foreign_keys=OFF")   # 只验 CHECK，不造 matches 行
    conn.execute("INSERT INTO agentline_predictions (match_id, line,"
                 " raw_output, status, created_at)"
                 " VALUES (99,'A_debate','x','ok','2026-09-05')")
    conn.commit()
    conn.close()


def test_v9_shadow_recovery(tmp_path):
    """半途迁移留下影子表 agentline_predictions_v8：重试从影子重放。"""
    db = _old_shape_db(tmp_path / "t.db")
    conn = connect(db)
    # 模拟窗 B：rename 完成、新表未建（索引随表走、保持原名 idx_alp_line，
    # 迁移代码里的 DROP INDEX IF EXISTS idx_alp_line 负责摘除——勿臆造新名）
    conn.executescript("ALTER TABLE agentline_predictions RENAME TO"
                       " agentline_predictions_v8;")
    _pin(conn, 8)
    init_db(db)
    conn = connect(db)
    assert conn.execute("SELECT COUNT(*) c FROM agentline_predictions"
                        " WHERE status='ok'").fetchone()["c"] == 2
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name="
                        "'agentline_predictions_v8'").fetchone() is None
    conn.close()


def test_v9_guard_skips_rebuilt_table(tmp_path):
    """已是新形状（budget_exhausted 在）而版本号钉在 8：跳过不炸。"""
    db = tmp_path / "t.db"
    init_db(db)
    conn = connect(db)
    _pin(conn, 8)
    conn.close()
    init_db(db)                               # fresh 形状 + v8 → 守卫跳过
    conn = connect(db)
    assert "budget_exhausted" in _cols(conn, "agentline_predictions")
    conn.close()


def test_fresh_word_list_accepts_debate_and_division(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    conn = connect(db)
    conn.execute("PRAGMA foreign_keys=OFF")
    for line in ("A_debate", "A_division"):
        conn.execute("INSERT INTO agentline_predictions (match_id, line,"
                     " raw_output, status, created_at)"
                     f" VALUES (1,'{line}','x','ok','2026-09-05')")
    conn.commit()
    conn.close()


def test_fresh_and_migrated_have_debate_rounds(tmp_path):
    for db in (tmp_path / "fresh.db",):
        init_db(db)
    conn = connect(tmp_path / "fresh.db")
    assert "agentline_debate_rounds" in {
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    # 先备好 matches 父行（connect 内 FK ON）：否则下面的插入炸在外键上，
    # 目标 UNIQUE 三元根本没被测到（v5/v7 迁移用例同款前置）
    conn.execute(
        "INSERT INTO teams (id, league, name) VALUES (1, 'E0', 'Arsenal')")
    conn.execute(
        "INSERT INTO matches (id, league, season, date, home_team_id,"
        " away_team_id, raw_line) VALUES (1, 'E0', 2025, '2025-08-16', 1, 1,"
        " '{}')")
    # UNIQUE(match_id, round, role)：同三元第二次插入必须炸
    conn.execute("INSERT INTO agentline_debate_rounds (match_id, round,"
                 " role, payload_json, raw_output, status, created_at)"
                 " VALUES (1,0,'generator','{}','r','ok','2026-09-05')")
    try:
        conn.execute("INSERT INTO agentline_debate_rounds (match_id, round,"
                     " role, payload_json, raw_output, status, created_at)"
                     " VALUES (1,0,'generator','{}','r2','ok','2026-09-05')")
        raise AssertionError("UNIQUE 三元未生效")
    except sqlite3.IntegrityError:
        pass
    conn.close()
    # 迁移路径同样有表
    db2 = _old_shape_db(tmp_path / "mig.db")
    init_db(db2)
    conn2 = connect(db2)
    assert "agentline_debate_rounds" in {
        r["name"] for r in conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    conn2.close()
