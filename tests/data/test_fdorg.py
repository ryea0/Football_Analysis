"""fdorg 客户端与 fallback 编排测试（全离线：_get 打补丁，odds_api 测试同款先例）。

fixture JSON 形状 = 2026-09-10 stopgap 实拉的 api.football-data.org v4 真实响应
（设计档 §1 实证基础；金案例断言见 results_fallback 组）。
"""
import pytest

from fa.data.fdorg import (LEAGUE_CODES, FdorgError, FdorgResult,
                           fetch_results, fetch_teams)


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
