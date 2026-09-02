import pytest

from fa.data.ingest import ingest_rows
from fa.data.parse import parse_csv
from fa.db import connect, init_db

CSV_A = """Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR
E0,19/08/1995,Arsenal,West Ham,1,1,D
E0,22/08/1995,Liverpool,Everton,2,0,H
"""
CSV_B_CORRECTED = """Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR
E0,19/08/1995,Arsenal,West Ham,1,1,D
E0,22/08/1995,Liverpool,Everton,2,1,H
"""


@pytest.fixture
def conn(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


def count(conn):
    return conn.execute("SELECT COUNT(*) c FROM matches").fetchone()["c"]


def test_ingest_then_rerun_skips(conn):
    rows = parse_csv(CSV_A, "E0", 1995)
    assert ingest_rows(conn, "E0", 1995, rows) == 2
    assert ingest_rows(conn, "E0", 1995, rows) == 0     # 内容未变 -> 跳过
    assert count(conn) == 2


def test_changed_csv_replaces_partition(conn):
    ingest_rows(conn, "E0", 1995, parse_csv(CSV_A, "E0", 1995))
    n = ingest_rows(conn, "E0", 1995, parse_csv(CSV_B_CORRECTED, "E0", 1995))
    assert n == 2
    assert count(conn) == 2                              # 无重复
    row = conn.execute(
        "SELECT ftag FROM matches WHERE date='1995-08-22'").fetchone()
    assert row["ftag"] == 1                              # 修正已生效


def test_other_league_partition_untouched(conn):
    ingest_rows(conn, "E0", 1995, parse_csv(CSV_A, "E0", 1995))
    ingest_rows(conn, "D1", 1995, parse_csv(CSV_A, "D1", 1995))
    ingest_rows(conn, "E0", 1995, parse_csv(CSV_A, "E0", 1995))  # E0 重跑
    assert count(conn) == 4                              # D1 分区不受影响
