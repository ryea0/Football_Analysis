"""fdorg 客户端与 fallback 编排测试（全离线：_get 打补丁，odds_api 测试同款先例）。

fixture JSON 形状 = 2026-09-10 stopgap 实拉的 api.football-data.org v4 真实响应
（设计档 §1 实证基础；金案例断言见 results_fallback 组）。
"""
import json
from datetime import datetime, timezone

import pytest

from fa.data.fdorg import (LEAGUE_CODES, FdorgError, FdorgResult,
                           fetch_results, fetch_teams)
from fa.data.ingest import get_or_create_team
from fa.db import connect, init_db


@pytest.fixture
def conn(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


def _mk_pending(conn, *, league="E0", kickoff="2026-09-12T14:00:00Z",
                home="Liverpool", away="Fulham", market="H") -> int:
    """造一笔 pending paper 注的完整上游链（teams→run→fixture→rec→bet），
    返回 fixture_id。"""
    hid = get_or_create_team(conn, league, home)
    aid = get_or_create_team(conn, league, away)
    cur = conn.execute(
        "INSERT INTO runs (type, phase, started_at, status)"
        " VALUES ('matchday', 'am', '2026-09-12T03:00:00Z', 'ok')")
    run_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO fixtures (league, event_key, source, kickoff_utc,"
        " home_team_id, away_team_id, status, created_at)"
        " VALUES (?, ?, 'oddsapi', ?, ?, ?, 'scheduled', '2026-09-12T03:00:00Z')",
        (league, f"evt{kickoff}{home}", kickoff, hid, aid))
    fixture_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO recommendations (run_id, fixture_id, strategy, market,"
        " phase, model_p, market_p, best_odds, bookmaker, edge, ev,"
        " kelly_stake_frac, final_stake_frac, created_at)"
        " VALUES (?, ?, 'model_only', ?, 'am', 0.5, 0.45, 2.1, 'Pinnacle',"
        " 0.05, 0.05, 0.1, 0.1, '2026-09-12T03:00:01Z')",
        (run_id, fixture_id, market))
    conn.execute(
        "INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker,"
        " odds_taken, stake, status) VALUES (?, 'paper',"
        " '2026-09-12T03:02:00Z', 'Pinnacle', 2.1, 20.0, 'pending')",
        (cur.lastrowid,))
    conn.commit()
    return fixture_id


def _alias(conn, name: str, team: str, league="E0"):
    conn.execute(
        "INSERT OR IGNORE INTO team_aliases (team_id, source, alias)"
        " VALUES (?, 'fdorg', ?)",
        (get_or_create_team(conn, league, team), name))
    conn.commit()


def _fb(monkeypatch, results, token="k"):
    monkeypatch.setattr("fa.data.fdorg.fetch_results", results)
    if token is None:
        monkeypatch.delenv("FOOTBALL_DATA_ORG_KEY", raising=False)
    else:
        monkeypatch.setenv("FOOTBALL_DATA_ORG_KEY", token)


NOW = datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc)   # kickoff +20h


# ---------------------------------------------------------------- results_fallback

def test_results_fallback_pairs_and_fills(conn, monkeypatch):
    """金案例形态（stopgap 6/6 实证）：时间戳严格相等 + 别名方向一致 → 落行。"""
    from fa.data.fdorg import results_fallback
    fid = _mk_pending(conn)
    _alias(conn, "Liverpool FC", "Liverpool")
    _alias(conn, "Fulham FC", "Fulham")
    _fb(monkeypatch, lambda lg, df, dt: [
        FdorgResult("2026-09-12T14:00:00Z", "Liverpool FC", "Fulham FC", 2, 1)])
    out = results_fallback(conn, now=NOW)
    assert out["triggered"] is True and out["fixtures"] == 1
    assert out["filled"] == 1 and out["unmatched"] == [] and out["error"] == []
    row = conn.execute(
        "SELECT m.fthg, m.ftag, m.date, m.season, m.raw_line FROM matches m"
        " JOIN teams t ON t.id = m.home_team_id WHERE t.name='Liverpool'"
    ).fetchone()
    assert (row["fthg"], row["ftag"]) == (2, 1)
    assert row["date"] == "2026-09-12" and row["season"] == 2026
    raw = json.loads(row["raw_line"])
    assert raw["source"] == "api.football-data.org"
    assert raw["fallback"] is True and raw["fixture_id"] == fid
    assert "Liverpool FC 2-1 Fulham FC" == raw["match"]


def test_results_fallback_direction_mismatch_unmatched(conn, monkeypatch):
    """判据②方向：fdorg 主客反了（home=Fulham FC）→ 不落、unmatched。"""
    from fa.data.fdorg import results_fallback
    _mk_pending(conn)                                    # home=Liverpool
    _alias(conn, "Liverpool FC", "Liverpool")
    _alias(conn, "Fulham FC", "Fulham")
    _fb(monkeypatch, lambda lg, df, dt: [
        FdorgResult("2026-09-12T14:00:00Z", "Fulham FC", "Liverpool FC", 1, 2)])
    out = results_fallback(conn, now=NOW)
    assert out["filled"] == 0 and len(out["unmatched"]) == 1
    assert out["unmatched"][0]["reason"] == "not_found"  # 方向不齐 = 配不上
    assert conn.execute("SELECT COUNT(*) c FROM matches"
                        ).fetchone()["c"] == 0


def test_results_fallback_alias_missing_reason(conn, monkeypatch):
    from fa.data.fdorg import results_fallback
    _mk_pending(conn)
    _alias(conn, "Liverpool FC", "Liverpool")            # Fulham FC 别名缺
    _fb(monkeypatch, lambda lg, df, dt: [
        FdorgResult("2026-09-12T14:00:00Z", "Liverpool FC", "Fulham FC", 2, 1)])
    out = results_fallback(conn, now=NOW)
    assert out["unmatched"][0]["reason"] == "alias_missing:Fulham FC"


def test_results_fallback_kickoff_mismatch_reason(conn, monkeypatch):
    """判据①：同一对阵但 utcDate 差 1 分钟 → kickoff_mismatch，绝不模糊对上。"""
    from fa.data.fdorg import results_fallback
    _mk_pending(conn)
    _alias(conn, "Liverpool FC", "Liverpool")
    _alias(conn, "Fulham FC", "Fulham")
    _fb(monkeypatch, lambda lg, df, dt: [
        FdorgResult("2026-09-12T14:00:01Z", "Liverpool FC", "Fulham FC", 2, 1)])
    out = results_fallback(conn, now=NOW)
    assert out["unmatched"][0]["reason"] == "kickoff_mismatch"
    assert conn.execute("SELECT COUNT(*) c FROM matches"
                        ).fetchone()["c"] == 0


def test_results_fallback_dry_run_no_write(conn, monkeypatch):
    from fa.data.fdorg import results_fallback
    _mk_pending(conn)
    _alias(conn, "Liverpool FC", "Liverpool")
    _alias(conn, "Fulham FC", "Fulham")
    _fb(monkeypatch, lambda lg, df, dt: [
        FdorgResult("2026-09-12T14:00:00Z", "Liverpool FC", "Fulham FC", 2, 1)])
    out = results_fallback(conn, dry_run=True, now=NOW)
    assert out["would_fill"] == 1 and out["filled"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM matches"
                        ).fetchone()["c"] == 0


def test_results_fallback_not_yet_finished_not_triggered(conn, monkeypatch):
    """kickoff+3h < FINISHED_HOURS(4)：比赛可能还没完 → 不进逾期集。"""
    from fa.data.fdorg import FINISHED_HOURS, results_fallback
    assert FINISHED_HOURS == 4
    _mk_pending(conn)
    _fb(monkeypatch, lambda lg, df, dt: [])
    soon = datetime(2026, 9, 12, 16, 30, tzinfo=timezone.utc)  # +2.5h
    out = results_fallback(conn, now=soon)
    assert out == {"triggered": False, "fixtures": 0, "filled": 0,
                   "unmatched": [], "error": []}


def test_results_fallback_settled_fixture_not_overdue(conn, monkeypatch):
    """_paired_match 已配上官方行 → 不算逾期（触发与结算同锚）。"""
    from fa.data.fdorg import results_fallback
    from fa.data.ingest import ingest_rows
    from fa.data.parse import parse_csv
    _mk_pending(conn)
    csv = ("Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n"
           "E0,12/09/2026,Liverpool,Fulham,3,0,H\n")
    ingest_rows(conn, "E0", 2026, parse_csv(csv, "E0", 2026))
    _fb(monkeypatch, lambda lg, df, dt: [])
    out = results_fallback(conn, now=NOW)
    assert out["triggered"] is False and out["fixtures"] == 0


def test_results_fallback_no_token_skipped(conn, monkeypatch):
    from fa.data.fdorg import results_fallback
    _mk_pending(conn)
    _fb(monkeypatch, lambda lg, df, dt: [], token=None)
    out = results_fallback(conn, now=NOW)
    assert out["triggered"] is False and out["skipped"] == "no_token"
    assert conn.execute("SELECT COUNT(*) c FROM matches"
                        ).fetchone()["c"] == 0


def test_results_fallback_single_league_error_tolerated(conn, monkeypatch):
    """单联赛拉取失败只记 error，其他联赛照落（T5 单文件容错同构）。"""
    from fa.data.fdorg import results_fallback

    def flaky(league, df, dt):
        if league == "SP1":
            raise FdorgError("http", "429")
        return [FdorgResult("2026-09-12T14:00:00Z", "Liverpool FC",
                            "Fulham FC", 2, 1)]

    _mk_pending(conn)                                    # E0
    _mk_pending(conn, league="SP1", home="Getafe", away="Elche")
    _alias(conn, "Liverpool FC", "Liverpool")
    _alias(conn, "Fulham FC", "Fulham")
    _fb(monkeypatch, flaky)
    out = results_fallback(conn, now=NOW)
    assert out["filled"] == 1
    assert len(out["error"]) == 1 and out["error"][0].startswith("SP1")
    assert len(out["unmatched"]) == 1                    # SP1 的 fixture 配不上
    assert out["unmatched"][0]["league"] == "SP1"


def test_results_fallback_official_row_not_overwritten(conn, monkeypatch):
    """官方行已在（INSERT OR IGNORE）→ fallback 不覆盖——官方为尊。"""
    from fa.data.fdorg import results_fallback
    from fa.data.ingest import ingest_rows
    from fa.data.parse import parse_csv
    _mk_pending(conn)
    csv = ("Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n"
           "E0,12/09/2026,Liverpool,Fulham,1,0,H\n")     # 官方 1-0（配得上→不触发）
    ingest_rows(conn, "E0", 2026, parse_csv(csv, "E0", 2026))
    _alias(conn, "Liverpool FC", "Liverpool")
    _alias(conn, "Fulham FC", "Fulham")
    _fb(monkeypatch, lambda lg, df, dt: [
        FdorgResult("2026-09-12T14:00:00Z", "Liverpool FC", "Fulham FC", 2, 1)])
    out = results_fallback(conn, now=NOW)
    assert out["triggered"] is False                     # 官方行配上了，无逾期


def test_league_codes_cover_five():
    assert LEAGUE_CODES == {"E0": "PL", "SP1": "PD", "D1": "BL1",
                            "I1": "SA", "F1": "FL1"}


def _payload(*matches):
    return {"matches": [
        {"utcDate": m[0], "status": m[1],
         "score": {"fullTime": {"homeTeam": m[5], "awayTeam": m[6]}},
         "homeTeam": {"name": m[3]}, "awayTeam": {"name": m[4]}}
        for m in matches]}


def test_fetch_results_filters_finished(monkeypatch):
    seen = {}

    def fake_get(path, params):
        seen["path"], seen["params"] = path, params
        return _payload(
            ("2026-09-12T14:00:00Z", "FINISHED", None,
             "Aston Villa FC", "Nottingham Forest FC", 2, 1),
            ("2026-09-12T16:30:00Z", "IN_PLAY", None, "X", "Y", None, None),
            ("2026-09-12T16:45:00Z", "TIMED", None, "X", "Y", None, None),
            ("2026-09-12T19:00:00Z", "FINISHED", None, "A", "B", 0, 0),
        )

    monkeypatch.setattr("fa.data.fdorg._get", fake_get)
    monkeypatch.setenv("FOOTBALL_DATA_ORG_KEY", "k")
    out = fetch_results("E0", "2026-09-12", "2026-09-12")
    assert out == [FdorgResult("2026-09-12T14:00:00Z", "Aston Villa FC",
                               "Nottingham Forest FC", 2, 1),
                   FdorgResult("2026-09-12T19:00:00Z", "A", "B", 0, 0)]
    assert seen["path"] == "/v4/competitions/PL/matches"
    assert seen["params"] == {"dateFrom": "2026-09-12", "dateTo": "2026-09-12"}


def test_fetch_results_unknown_league():
    with pytest.raises(FdorgError) as ei:
        fetch_results("XX", "2026-09-12", "2026-09-12")
    assert ei.value.reason == "parse"


def test_fetch_results_http_error_surfaces(monkeypatch):
    def fake_get(path, params):
        raise FdorgError("http", "429")

    monkeypatch.setattr("fa.data.fdorg._get", fake_get)
    with pytest.raises(FdorgError) as ei:
        fetch_results("E0", "2026-09-12", "2026-09-12")
    assert ei.value.reason == "http"


def test_get_without_token_raises_no_token(monkeypatch):
    from fa.data import fdorg
    monkeypatch.delenv("FOOTBALL_DATA_ORG_KEY", raising=False)
    with pytest.raises(FdorgError) as ei:
        fdorg._get("/v4/x", {})
    assert ei.value.reason == "no_token"


def test_fetch_teams_sorted_names(monkeypatch):
    def fake_get(path, params):
        assert path == "/v4/competitions/PL/teams"
        return {"teams": [{"name": "Chelsea"}, {"name": "Arsenal"},
                          {"id": 5}, {"name": None}]}

    monkeypatch.setattr("fa.data.fdorg._get", fake_get)
    monkeypatch.setenv("FOOTBALL_DATA_ORG_KEY", "k")
    assert fetch_teams("E0") == ["Arsenal", "Chelsea"]
