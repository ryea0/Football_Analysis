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


def test_alias_branch_is_league_scoped(conn):
    """别名分支按 league 过滤（M4 §7-4 根治）：跨联赛同名别名不再穿透。

    实害案例（M4 E2E veto 首例）：oddsapi 别名 ``Treviso`` 绑在 I1 队上，
    F1 fixture 同名队经旧的全局别名查询被错挂到 I1 的队——候选池出现
    「F1 场次挂 I1 球队」的脏数据。收紧后该侧对不上 → 走归一化/模糊 →
    多数进隔离表人工确认（可见），绝不静默穿透。
    """
    tid = get_or_create_team(conn, "I1", "Treviso")
    conn.execute(
        "INSERT INTO team_aliases (team_id, source, alias)"
        " VALUES (?, 'oddsapi', 'Treviso')", (tid,))
    conn.commit()
    assert resolve_team(conn, "I1", "Treviso", "oddsapi") == tid    # 本联赛照常
    assert resolve_team(conn, "F1", "Treviso", "oddsapi") is None   # 跨联赛：不穿透
