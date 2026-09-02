import pytest

from fa.data.audit import audit_sample
from fa.data.ingest import ingest_rows
from fa.data.parse import parse_csv
from fa.db import connect, init_db

CSV_GOOD = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\nE0,19/08/1995,Arsenal,West Ham,1,1,D\n"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    init_db(tmp_path / "t.db")
    conn = connect(tmp_path / "t.db")
    cache = tmp_path / "csv"
    cache.mkdir()
    monkeypatch.setattr("fa.data.audit.csv_cache_dir", lambda: cache)
    # 缓存文件名与 download.csv_cache_path 同一约定：f"{league}_{season_code(year)}.csv"，
    # 即 season_code(1995) == "9596"（不是原始年份 1995——真缓存目录里没有 E0_1995.csv）
    (cache / "E0_9596.csv").write_text(CSV_GOOD)
    ingest_rows(conn, "E0", 1995, parse_csv(CSV_GOOD, "E0", 1995))
    yield conn, cache
    conn.close()


def test_no_mismatch(setup):
    conn, _ = setup
    assert audit_sample(conn, sample=10) == []


def test_detects_corrupted_db_row(setup):
    conn, _ = setup
    conn.execute("UPDATE matches SET fthg=7")     # 模拟入库 bug
    conn.commit()
    mismatches = audit_sample(conn, sample=10)
    assert len(mismatches) == 1
    assert mismatches[0].field == "fthg"
    assert mismatches[0].db_value == 7 and mismatches[0].csv_value == 1


def test_cache_missing_reported(setup):
    conn, cache = setup
    (cache / "E0_9596.csv").unlink()
    mismatches = audit_sample(conn, sample=10)
    assert mismatches and mismatches[0].field == "cache_missing"
