import pytest

from fa.data.walkforward import iter_matchweeks, training_matches
from fa.db import connect, init_db


def _seed(conn):
    """两周比赛：1995-08-17(周四)~08-23(周三) 与 08-24(周四)~08-30(周三)"""
    conn.executemany(
        "INSERT INTO teams (league, name) VALUES ('E0', ?)",
        [("A",), ("B",), ("C",), ("D",)])
    ids = [r["id"] for r in conn.execute("SELECT id FROM teams")]
    rows = [
        ("1995-08-19", ids[0], ids[1]),   # 第 1 周（周六）
        ("1995-08-23", ids[2], ids[3]),   # 第 1 周（周三）
        ("1995-08-24", ids[0], ids[2]),   # 第 2 周（周四，新桶开始）
        ("1995-08-27", ids[1], ids[3]),   # 第 2 周（周日）
    ]
    conn.executemany(
        "INSERT INTO matches (league, season, date, home_team_id, away_team_id,"
        " fthg, ftag, raw_line) VALUES ('E0', 1995, ?, ?, ?, 0, 0, '{}')", rows)
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    _seed(c)
    yield c
    c.close()


def test_matchweeks_thursday_buckets(conn):
    weeks = iter_matchweeks(conn, "E0", 1995)
    assert len(weeks) == 2
    assert weeks[0].index == 1 and weeks[0].start == "1995-08-17"
    assert weeks[0].end == "1995-08-23"
    assert len(weeks[0].match_ids) == 2
    assert weeks[1].start == "1995-08-24" and weeks[1].end == "1995-08-27"


def test_training_matches_strict_cutoff(conn):
    rows = training_matches(conn, "E0", "1995-08-24")
    assert [r["date"] for r in rows] == ["1995-08-19", "1995-08-23"]


def test_empty_league(conn):
    assert iter_matchweeks(conn, "SP1", 1995) == []
    assert training_matches(conn, "SP1", "1995-08-24") == []
