import pytest

from fa.db import connect, get_meta, init_db, set_meta


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
