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
    # audit 经 download.csv_cache_path 解析路径，而它读的是 download.csv_cache_dir，
    # 故补丁必须打在 download 上（audit 自身不再持有文件名约定）
    monkeypatch.setattr("fa.data.download.csv_cache_dir", lambda: cache)
    # season_code(1995) == "9596"，不是原始年份 1995——真缓存目录里没有 E0_1995.csv
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


class _CachePathSentinel(Exception):
    """audit 若自拼缓存路径（绕过 download.csv_cache_path）就永远不会触发它。"""


def test_audit_uses_download_cache_path(setup, monkeypatch):
    """audit 必须复用 download.csv_cache_path，文件名约定只此一处。"""
    conn, _ = setup
    seen = []

    def sentinel(league, year):
        seen.append((league, year))
        raise _CachePathSentinel

    monkeypatch.setattr("fa.data.download.csv_cache_path", sentinel)
    with pytest.raises(_CachePathSentinel):
        audit_sample(conn, sample=10)
    assert seen == [("E0", 1995)]                 # 按 (league, season) 恰好调用一次
