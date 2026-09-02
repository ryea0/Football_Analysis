import sqlite3

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
CSV_PSD = """Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,PSD
E0,19/08/1995,Arsenal,West Ham,1,1,D,3.2
E0,22/08/1995,Liverpool,Everton,2,0,H,3.1
"""
CSV_PSD_CORRECTED = """Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,PSD
E0,19/08/1995,Arsenal,West Ham,1,1,D,3.4
E0,22/08/1995,Liverpool,Everton,2,0,H,3.1
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


def test_non_sampled_field_change_converges(conn):
    """ps_draw 不在旧抽样指纹内 -> 整行指纹下必须重插收敛，而非误判未变而跳过。"""
    ingest_rows(conn, "E0", 1995, parse_csv(CSV_PSD, "E0", 1995))
    n = ingest_rows(conn, "E0", 1995, parse_csv(CSV_PSD_CORRECTED, "E0", 1995))
    assert n == 2                                        # 未被跳过，全量重插
    row = conn.execute(
        "SELECT ps_draw FROM matches WHERE date='1995-08-19'").fetchone()
    assert row["ps_draw"] == pytest.approx(3.4)          # 修正已落库


def test_empty_rows_wipe_nothing(conn):
    ingest_rows(conn, "E0", 1995, parse_csv(CSV_A, "E0", 1995))
    assert ingest_rows(conn, "E0", 1995, []) == 0        # 空输入 = 不做事
    assert count(conn) == 2                              # 分区未被清空


def test_failed_ingest_leaves_prior_partition(conn, tmp_path):
    ingest_rows(conn, "E0", 1995, parse_csv(CSV_A, "E0", 1995))
    rows = parse_csv(CSV_B_CORRECTED, "E0", 1995)        # 哈希必与 CSV_A 不同
    rows[1].date = None                                  # 第 2 行触发 NOT NULL 失败
    with pytest.raises(sqlite3.IntegrityError):
        ingest_rows(conn, "E0", 1995, rows)
    assert conn.in_transaction is False                  # 已回滚，无悬挂事务
    c2 = connect(tmp_path / "t.db")                      # 全新连接看持久化真相
    got = c2.execute("SELECT COUNT(*) c FROM matches").fetchone()["c"]
    assert got == 2                                      # 旧分区完好
    ftag = c2.execute(
        "SELECT ftag FROM matches WHERE date='1995-08-22'").fetchone()["ftag"]
    assert ftag == 0                                     # 仍是 CSV_A 数据，未被半替换
    c2.close()
