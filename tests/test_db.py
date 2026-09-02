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
    c.execute("UPDATE schema_version SET version=1")
    c.execute("DROP TABLE backtest_predictions")   # 模拟老库
    c.commit(); c.close()
    init_db(tmp_path / "t.db")                      # 不抛异常即升级成功
    c = connect(tmp_path / "t.db")
    assert c.execute("SELECT version FROM schema_version").fetchone()["version"] == 2
    assert c.execute("SELECT COUNT(*) c FROM backtest_predictions").fetchone()["c"] == 0
    c.close()


def test_future_version_refused(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    c.execute("UPDATE schema_version SET version=99")
    c.commit(); c.close()
    import pytest
    with pytest.raises(RuntimeError):
        init_db(tmp_path / "t.db")
