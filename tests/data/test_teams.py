import pytest

from fa.data.teams import get_or_create_team, record_unknown, resolve_team
from fa.db import connect, init_db


@pytest.fixture
def conn(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


def test_get_or_create_reuses_id(conn):
    a = get_or_create_team(conn, "D1", "Bayern Munich")
    b = get_or_create_team(conn, "D1", "Bayern Munich")
    assert a == b
    assert get_or_create_team(conn, "D1", "Dortmund") != a


def test_resolve_by_canonical_then_alias(conn):
    tid = get_or_create_team(conn, "D1", "Bayern Munich")
    assert resolve_team(conn, "D1", "Bayern Munich", "oddsapi") == tid
    conn.execute(
        "INSERT INTO team_aliases (team_id, source, alias)"
        " VALUES (?, 'oddsapi', 'Bayern München')",
        (tid,))
    conn.commit()
    assert resolve_team(conn, "D1", "Bayern München", "oddsapi") == tid
    assert resolve_team(conn, "D1", "FC Hollywood", "oddsapi") is None


def test_record_unknown_idempotent(conn):
    record_unknown(conn, "oddsapi", "München Bayern")
    record_unknown(conn, "oddsapi", "München Bayern")
    n = conn.execute("SELECT COUNT(*) c FROM unknown_names").fetchone()["c"]
    assert n == 1
