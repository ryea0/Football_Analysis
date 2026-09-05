import json
import sqlite3

import pytest

from fa.data.sync import sync_history
from fa.db import connect, init_db

CSV_A = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\nE0,19/08/1995,Arsenal,West Ham,1,1,D\n"


@pytest.fixture
def conn(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


def _count(conn) -> int:
    return conn.execute("SELECT COUNT(*) c FROM matches").fetchone()["c"]


def _cached(tmp_path, league: str, year: int, data: bytes):
    p = tmp_path / f"{league}_{year}.csv"
    p.write_bytes(data)
    return p


def _serve(monkeypatch, table: dict[tuple[str, int], object],
           boom: frozenset[str] = frozenset()) -> None:
    """把 download_csv 换成离线假件：table 命中 -> 返回已缓存 Path，否则 None。"""

    def fake_download(league, start_year, refresh=False):
        if league in boom:
            raise RuntimeError("boom")
        hit = table.get((league, start_year))
        return hit

    monkeypatch.setattr("fa.data.sync.download_csv", fake_download)


def test_sync_report_and_idempotent(conn, monkeypatch, tmp_path):
    calls = []

    def fake_download(league, start_year, refresh=False):
        calls.append((league, start_year))
        # 只给 E0 1995-96 一个文件，其余赛季/联赛一律 404——不能按
        # “start_year < 1995” 之类的年份区间放行，否则断言随当前日期漂移
        if (league, start_year) != ("E0", 1995):
            return None
        p = tmp_path / f"{league}_{start_year}.csv"
        p.write_text(CSV_A)
        return p

    monkeypatch.setattr("fa.data.sync.download_csv", fake_download)
    rep = sync_history(conn, seasons_from=1995, refresh=True)
    assert rep.files_ok == 1 and rep.inserted == 1
    assert ("E0", 1995) in calls

    rep2 = sync_history(conn, seasons_from=1995)   # 再跑：内容未变
    assert rep2.inserted == 0
    n = conn.execute("SELECT COUNT(*) c FROM matches").fetchone()["c"]
    assert n == 1


def test_empty_file_is_file_error_not_crash(conn, monkeypatch, tmp_path):
    # Task 3 可能缓存 0 字节的 200 响应：不得让 EmptyDataError 冒出 sync
    p = _cached(tmp_path, "E0", 1995, b"")
    _serve(monkeypatch, {("E0", 1995): p})
    rep = sync_history(conn, seasons_from=1995)
    assert len(rep.file_errors) == 1
    league, year, msg = rep.file_errors[0]
    assert (league, year) == ("E0", 1995)
    assert "空" in msg          # 明确判为空文件，而不是解析层 EmptyDataError
    assert _count(conn) == 0        # matches 表未被触碰
    assert rep.inserted == 0
    assert rep.rows_no_date == 0


def test_bom_csv_dates_parse(conn, monkeypatch, tmp_path):
    # UTF-8 带 BOM 的缓存文件：BOM 必须被剥掉，首列表头仍是 Div，日期正确解析
    p = _cached(tmp_path, "E0", 1995, b"\xef\xbb\xbf" + CSV_A.encode("utf-8"))
    assert p.read_bytes().startswith(b"\xef\xbb\xbf")
    _serve(monkeypatch, {("E0", 1995): p})
    rep = sync_history(conn, seasons_from=1995)
    assert rep.file_errors == []
    assert rep.files_ok == 1 and rep.inserted == 1
    row = conn.execute(
        "SELECT date, raw_line FROM matches").fetchone()
    assert row["date"] == "1995-08-19"
    raw = json.loads(row["raw_line"])
    assert raw["Div"] == "E0"          # BOM 未污染首列
    assert "ï»¿Div" not in raw


def test_no_date_rows_skipped_and_counted(conn, monkeypatch, tmp_path):
    csv_bad_date = (
        "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n"
        "E0,19/08/1995,Arsenal,West Ham,1,1,D\n"
        "E0,xx/xx/xxxx,Liverpool,Everton,2,0,H\n")
    p = _cached(tmp_path, "E0", 1995, csv_bad_date.encode("utf-8"))
    _serve(monkeypatch, {("E0", 1995): p})
    rep = sync_history(conn, seasons_from=1995)
    assert rep.file_errors == []
    assert rep.inserted == 1
    assert rep.rows_no_date == 1
    assert _count(conn) == 1
    row = conn.execute("SELECT date FROM matches").fetchone()
    assert row["date"] == "1995-08-19"


def test_one_file_error_does_not_stop_sync(conn, monkeypatch, tmp_path):
    p = _cached(tmp_path, "E0", 1995, CSV_A.encode("utf-8"))
    _serve(monkeypatch, {("E0", 1995): p}, boom=frozenset({"SP1"}))
    rep = sync_history(conn, seasons_from=1995)      # 不抛异常
    assert _count(conn) == 1
    assert conn.execute(
        "SELECT COUNT(*) c FROM matches WHERE league='E0'").fetchone()["c"] == 1
    # SP1 每个赛季都炸，但只记账、不外溢；错误全部属于 SP1
    assert rep.file_errors
    assert {lg for lg, _, _ in rep.file_errors} == {"SP1"}
    assert all("boom" in msg for _, _, msg in rep.file_errors)
    assert ("SP1", 1995) in [(lg, y) for lg, y, _ in rep.file_errors]
    assert rep.files_ok == 1
    assert rep.inserted == 1


def test_sync_report_dataclass_shape():
    from dataclasses import fields

    from fa.data.sync import SyncReport
    names = {f.name for f in fields(SyncReport)}
    assert {"files_ok", "files_missing", "inserted", "skipped_seasons",
            "missing", "file_errors", "rows_no_date"} <= names


def test_current_season_always_refreshed(conn, monkeypatch, tmp_path):
    """当前赛季分区无条件强制重下（spec v0.12 §9.5）——daily 不传 refresh 也能拿到新赛果。"""
    flags: dict[tuple[str, int], bool] = {}
    monkeypatch.setattr("fa.data.sync._current_season_start", lambda: 1996)

    def fake_download(league, start_year, refresh=False):
        flags[(league, start_year)] = refresh
        if (league, start_year) not in (("E0", 1995), ("E0", 1996)):
            return None
        p = tmp_path / f"{league}_{start_year}.csv"
        p.write_text(CSV_A.replace("1995", str(start_year)))
        return p

    monkeypatch.setattr("fa.data.sync.download_csv", fake_download)
    sync_history(conn, seasons_from=1995)             # 不传 refresh
    assert flags[("E0", 1996)] is True                # 当前赛季：强制重下
    assert flags[("E0", 1995)] is False               # 历史赛季：走缓存


def test_shrink_guard_lands_in_file_errors_not_crash(conn, monkeypatch, tmp_path):
    """收缩保护经 sync 记 file_errors（run#9 实况防线）：不抛、分区原样。"""
    csv_two = (CSV_A.splitlines()[0] + "\n" + CSV_A.splitlines()[1] + "\n"
               + "E0,22/08/1995,Liverpool,Everton,2,0,H\n")          # 2 行
    big = _cached(tmp_path, "E0", 1995, csv_two.encode("utf-8"))
    _serve(monkeypatch, {("E0", 1995): big})
    sync_history(conn, seasons_from=1995)                             # 入库 2 行
    small = _cached(tmp_path, "E0", 1995, CSV_A.encode("utf-8"))      # 1 行
    _serve(monkeypatch, {("E0", 1995): small})
    rep = sync_history(conn, seasons_from=1995)                       # 哈希变了→重建→收缩
    assert len(rep.file_errors) == 1
    lg, y, msg = rep.file_errors[0]
    assert (lg, y) == ("E0", 1995) and "收缩保护" in msg
    assert _count(conn) == 2 and rep.inserted == 0


def test_ingest_failure_is_recorded_not_raised(conn, monkeypatch, tmp_path):
    """R1：入库层异常（如 IntegrityError）只记账；连接回滚后，后面的文件照常入库。"""
    from fa.data.ingest import ingest_rows as real_ingest

    def season_csv(league, year):
        return CSV_A.replace("E0", league).replace("1995", str(year)).encode("utf-8")

    files = {(lg, 1994): _cached(tmp_path, lg, 1994, season_csv(lg, 1994))
             for lg in ("E0", "SP1", "D1")}
    _serve(monkeypatch, files)          # E0 -> SP1(炸) -> D1 的处理顺序

    def flaky_ingest(conn_, league, season, rows):
        if league == "SP1":
            raise sqlite3.IntegrityError("UNIQUE constraint failed: matches.date")
        return real_ingest(conn_, league, season, rows)

    monkeypatch.setattr("fa.data.sync.ingest_rows", flaky_ingest)
    rep = sync_history(conn, seasons_from=1994, refresh=True)
    assert len(rep.file_errors) == 1
    lg, y, msg = rep.file_errors[0]
    assert (lg, y) == ("SP1", 1994)
    assert "IntegrityError" in msg
    # SP1 失败被隔离，E0 / D1 两个分区照常入库
    assert rep.inserted == 2 and _count(conn) == 2
    assert sorted(r["league"] for r in
                  conn.execute("SELECT DISTINCT league FROM matches")) == ["D1", "E0"]
