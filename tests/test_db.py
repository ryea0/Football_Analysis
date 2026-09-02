import pytest

from fa.db import SCHEMA_VERSION, connect, get_meta, init_db, set_meta


@pytest.fixture
def conn(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


def test_init_creates_tables(conn):
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"teams", "team_aliases", "unknown_names", "matches", "meta"} <= names


def test_init_is_idempotent(tmp_path):
    init_db(tmp_path / "t.db")
    init_db(tmp_path / "t.db")  # 不抛异常即通过


def test_wal_mode(conn):
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_meta_roundtrip(conn):
    assert get_meta(conn, "k") is None
    set_meta(conn, "k", "v1")
    assert get_meta(conn, "k") == "v1"
    set_meta(conn, "k", "v2")
    assert get_meta(conn, "k") == "v2"


def test_fresh_db_is_v2(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    assert c.execute("SELECT version FROM schema_version").fetchone()["version"] == 2
    names = {r["name"] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "backtest_predictions" in names
    c.close()


def test_v1_upgrades_to_v2(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    # 先放数据，验证迁移是纯加法、不搬迁不丢数据
    th = c.execute("INSERT INTO teams (league, name) VALUES ('E0','Arsenal')")
    ta = c.execute("INSERT INTO teams (league, name) VALUES ('E0','Chelsea')")
    c.execute(
        "INSERT INTO matches (league, season, date, home_team_id, away_team_id, "
        "raw_line) VALUES ('E0', 2025, '2025-08-16', ?, ?, '{}')",
        (th.lastrowid, ta.lastrowid))
    c.execute("INSERT INTO meta (key, value) VALUES ('last_sync', '2025-08-17')")
    c.execute("UPDATE schema_version SET version=1")
    c.execute("DROP TABLE backtest_predictions")   # 模拟老库
    c.commit(); c.close()
    init_db(tmp_path / "t.db")                      # 不抛异常即升级成功
    c = connect(tmp_path / "t.db")
    assert c.execute("SELECT version FROM schema_version").fetchone()["version"] == 2
    assert c.execute("SELECT COUNT(*) c FROM backtest_predictions").fetchone()["c"] == 0
    # 原有数据原样保留
    assert c.execute("SELECT COUNT(*) c FROM teams").fetchone()["c"] == 2
    m = c.execute(
        "SELECT league, season, date, raw_line FROM matches").fetchone()
    assert (m["league"], m["season"], m["date"], m["raw_line"]) == \
        ('E0', 2025, '2025-08-16', '{}')
    assert c.execute(
        "SELECT value FROM meta WHERE key='last_sync'").fetchone()["value"] \
        == '2025-08-17'
    c.close()


def test_migrate_and_fresh_schemas_match(tmp_path):
    """新建与迁移两条路径产出的 backtest_predictions 表结构必须逐字一致。"""
    init_db(tmp_path / "a.db")                      # 全新 v2
    init_db(tmp_path / "b.db")                      # 降级到 v1 再升级
    c = connect(tmp_path / "b.db")
    c.execute("UPDATE schema_version SET version=1")
    c.execute("DROP TABLE backtest_predictions")
    c.commit(); c.close()
    init_db(tmp_path / "b.db")

    sql = "SELECT sql FROM sqlite_master WHERE type='table' " \
          "AND name='backtest_predictions'"
    a = connect(tmp_path / "a.db")
    b = connect(tmp_path / "b.db")
    sql_a = a.execute(sql).fetchone()["sql"]
    sql_b = b.execute(sql).fetchone()["sql"]
    a.close(); b.close()
    assert sql_a == sql_b


def test_future_version_refused(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    c.execute("UPDATE schema_version SET version=99")
    c.commit(); c.close()
    import pytest
    with pytest.raises(RuntimeError):
        init_db(tmp_path / "t.db")
