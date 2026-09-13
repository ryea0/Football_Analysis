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


# ---------------------------------------------------------------- v0.13 恢复闭环

FALLBACK_RAW = '{"source": "api.football-data.org", "fallback": true}'


def _fb_conn_ready(conn):
    """公共布置：E0/1995 分区入库 CSV_A（Arsenal/West Ham 队已建）。"""
    from fa.data.ingest import ingest_rows
    from fa.data.parse import parse_csv
    ingest_rows(conn, "E0", 1995, parse_csv(CSV_A, "E0", 1995))


def _insert_fallback_row(conn, date="1995-08-25", fthg=3, ftag=1):
    hid = conn.execute("SELECT id FROM teams WHERE name='Arsenal'").fetchone()["id"]
    aid = conn.execute("SELECT id FROM teams WHERE name='West Ham'").fetchone()["id"]
    conn.execute(
        "INSERT INTO matches (league, season, date, home_team_id, away_team_id,"
        " fthg, ftag, raw_line) VALUES ('E0', 1995, ?, ?, ?, ?, ?, ?)",
        (date, hid, aid, fthg, ftag, FALLBACK_RAW))
    conn.commit()
    return f"{date}|{hid}|{aid}"


def test_recovery_diff_equal_is_silent(conn):
    from fa.data.sync import _diff_official, _fallback_snapshot
    _fb_conn_ready(conn)
    key = _insert_fallback_row(conn, fthg=3, ftag=1)   # fallback 行 3-1
    snap = _fallback_snapshot(conn, "E0", 1995)
    assert snap == {key: (3, 1)}
    # 官方行同比分（raw 换成官方行形态）→ 无 diff
    conn.execute(
        "UPDATE matches SET fthg=3, ftag=1, raw_line='{\"Div\":1}'"
        " WHERE date='1995-08-25'")
    conn.commit()
    assert _diff_official(conn, "E0", 1995, snap) == []


def test_recovery_diff_mismatch_reported(conn):
    from fa.data.sync import _diff_official
    _fb_conn_ready(conn)
    key = _insert_fallback_row(conn, fthg=2, ftag=1)   # fallback 2-1
    snap = {key: (2, 1)}
    conn.execute(
        "UPDATE matches SET fthg=1, ftag=2, raw_line='{\"Div\":1}'"
        " WHERE date='1995-08-25'")                    # 官方 1-2
    conn.commit()
    diffs = _diff_official(conn, "E0", 1995, snap)
    assert len(diffs) == 1
    league, season, _key_desc, fb, official = diffs[0]
    assert (league, season) == ("E0", 1995)
    assert fb == "2-1" and official == "1-2"


def test_recovery_diff_missing_official_skipped(conn):
    """官方内容还没覆盖该场（行缺席）→ 跳过不告警（下轮再比）。"""
    from fa.data.sync import _diff_official
    _fb_conn_ready(conn)
    hid = conn.execute("SELECT id FROM teams WHERE name='Arsenal'").fetchone()["id"]
    aid = conn.execute("SELECT id FROM teams WHERE name='West Ham'").fetchone()["id"]
    snap = {f"1999-01-01|{hid}|{aid}": (1, 0)}         # 无此行
    assert _diff_official(conn, "E0", 1995, snap) == []


def _current_season_1995(monkeypatch):
    monkeypatch.setattr("fa.data.sync._current_season_start", lambda: 1995)


def test_fail_streak_counts_current_season_and_resets(conn, monkeypatch):
    from fa.data.sync import FAIL_STREAK_META_KEY, _update_fail_streak
    _current_season_1995(monkeypatch)
    assert _update_fail_streak(conn, [("E0", 1995, "boom")]) == 1
    assert _update_fail_streak(conn, [("E0", 1995, "boom")]) == 2   # 连 2
    # 历史赛季失败不算当前赛季失败；成功日归零
    assert _update_fail_streak(conn, [("E0", 1994, "boom")]) == 0
    raw = conn.execute("SELECT value FROM meta WHERE key=?",
                       (FAIL_STREAK_META_KEY,)).fetchone()["value"]
    assert raw == "0"


def test_sync_history_wraps_current_season_recovery(conn, monkeypatch, tmp_path):
    """集成：当前赛季分区含 fallback 行 → sync 后官方行覆盖、一致则 diff 静默。"""
    from fa.data.sync import FAIL_STREAK_META_KEY
    _current_season_1995(monkeypatch)
    _fb_conn_ready(conn)
    _insert_fallback_row(conn, fthg=3, ftag=1)         # fallback 3-1
    csv = CSV_A + "E0,25/08/1995,Arsenal,West Ham,3,1,H\n"   # 官方同比分
    _serve(monkeypatch, {("E0", 1995): _cached(tmp_path, "E0", 1995,
                                                csv.encode("utf-8"))})
    rep = sync_history(conn, seasons_from=1995, refresh=True)
    assert rep.fallback_diffs == []                    # 比分一致 → 静默覆盖
    row = conn.execute("SELECT raw_line FROM matches WHERE date='1995-08-25'"
                       ).fetchone()
    assert "fallback" not in row["raw_line"]           # 官方行已覆盖
    assert rep.fail_streak == 0
    meta = conn.execute("SELECT value FROM meta WHERE key=?",
                        (FAIL_STREAK_META_KEY,)).fetchone()
    assert meta is None or meta["value"] == "0"


def test_sync_history_recovery_mismatch_alerted(conn, monkeypatch, tmp_path):
    """比分不一致 → fallback_diffs 记账（告警归 daily/CLI 层）。"""
    _current_season_1995(monkeypatch)
    _fb_conn_ready(conn)
    _insert_fallback_row(conn, fthg=2, ftag=0)         # fallback 2-0
    csv = CSV_A + "E0,25/08/1995,Arsenal,West Ham,1,1,H\n"   # 官方 1-1
    _serve(monkeypatch, {("E0", 1995): _cached(tmp_path, "E0", 1995,
                                                csv.encode("utf-8"))})
    rep = sync_history(conn, seasons_from=1995, refresh=True)
    assert len(rep.fallback_diffs) == 1
    assert rep.fallback_diffs[0][3] == "2-0" and rep.fallback_diffs[0][4] == "1-1"
