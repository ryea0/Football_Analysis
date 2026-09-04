"""周度小结测试（M5 §10「周度小结」，2026-09-04 裁定按设计实施）。

全离线：种子直接插库（沿 test_paper 模式），时间经 ``weekly._now`` 注入缝。
窗口＝**上个自然周**（北京时间周一 00:00 – 周日 24:00，固定 +8 无夏令时）；
空周（双轨 0 落注且 0 结算）→ ``empty=True``，CLI 静默不推送。
"""
import urllib.request
from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from fa.cli import app
from fa.db import connect, init_db, set_meta
from fa.pipeline import weekly

runner = CliRunner()

LEAGUE = "E0"
# 周期语境 = cron 槽：2026-09-07 是周一，北京时间 09:00 → 上周 = 08-31 ~ 09-06
_NOW = datetime(2026, 9, 7, 1, 0, tzinfo=timezone.utc)
WS, WE = "2026-08-31T00:00:00+08:00", "2026-09-07T00:00:00+08:00"
ISO_WS, ISO_WE = "2026-08-30T16:00:00Z", "2026-09-06T16:00:00Z"   # UTC 界
ISO_MID = "2026-09-04T12:00:00Z"                              # 窗口内种子时间


def _boom(*args, **kwargs):
    raise AssertionError("周度小结不得触网")


@pytest.fixture
def env(tmp_path, monkeypatch):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    box = type("B", (), {})()
    box.conn = c
    box.pushed = []
    box.ok = True
    monkeypatch.setattr(weekly, "_now", lambda: _NOW)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setenv("FA_DB", str(tmp_path / "t.db"))
    yield box
    c.close()


def team(c, name):
    return c.execute("INSERT INTO teams (league, name) VALUES (?, ?)",
                     (LEAGUE, name)).lastrowid


def add_fixture(c, key):
    return c.execute(
        "INSERT INTO fixtures (league, event_key, source, kickoff_utc,"
        " home_team_id, away_team_id, status, created_at)"
        " VALUES (?, ?, 'oddsapi', '2026-09-02T14:00:00Z', ?, ?, 'scheduled',"
        " '2026-09-01T00:00:00Z')",
        (LEAGUE, key, team(c, "H" + key), team(c, "A" + key))).lastrowid


def add_run(c):
    return c.execute(
        "INSERT INTO runs (type, phase, started_at, status)"
        " VALUES ('matchday', 'am', '2026-09-02T03:00:00Z', 'ok')").lastrowid


def add_bet(c, strategy, market, *, placed_at, settled_at=None,
            stake=20.0, odds=2.0, return_amt=None, clv=None,
            status="pending", fixture_key="ev1"):
    fx = c.execute("SELECT id FROM fixtures WHERE event_key=?",
                   (fixture_key,)).fetchone()
    fx_id = fx["id"] if fx is not None else add_fixture(c, fixture_key)
    run = add_run(c)
    rid = c.execute(
        "INSERT INTO recommendations (run_id, fixture_id, strategy, market,"
        " phase, model_p, market_p, best_odds, bookmaker, edge, ev,"
        " kelly_stake_frac, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run, fx_id, strategy, market, "am", 0.5, 0.4, 2.0, "pinnacle",
         0.1, 0.2, 0.01, placed_at)).lastrowid
    return c.execute(
        "INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker,"
        " odds_taken, stake, status, settled_at, return_amt, clv)"
        " VALUES (?, 'paper', ?, 'pinnacle', ?, ?, ?, ?, ?, ?)",
        (rid, placed_at, odds, stake, status, settled_at, return_amt,
         clv)).lastrowid


# ---------------------------------------------------------------- 窗口判定


def test_window_is_previous_natural_week_beijing(env):
    win = weekly.last_week_window()
    assert win.start.isoformat() == WS and win.end.isoformat() == WE


def test_window_on_monday_morning_still_refers_last_week(env, monkeypatch):
    """周一北京时间 06:00（ cron 槽 07:00 之前）仍指上一个完整周。"""
    monkeypatch.setattr(weekly, "_now",
                        lambda: datetime(2026, 9, 6, 22, 0,
                                         tzinfo=timezone.utc))   # 周一 06:00 +8
    win = weekly.last_week_window()
    assert win.start.isoformat() == WS and win.end.isoformat() == WE


# ---------------------------------------------------------------- 汇总口径


def test_empty_week_is_marked_empty(env):
    assert weekly.weekly_summary(env.conn)["empty"] is True


def test_summary_aggregates_per_strategy_within_window(env):
    add_bet(env.conn, "model_only", "H", placed_at=ISO_WS, settled_at=ISO_MID,
            status="won", return_amt=40.0, clv=0.10)          # +20 净
    add_bet(env.conn, "model_only", "D", placed_at=ISO_MID,
            status="pending")                                  # 窗口内落注在途
    add_bet(env.conn, "model_persona", "A", placed_at=ISO_WS,
            settled_at=ISO_MID, status="lost", return_amt=0.0, clv=-0.05)
    add_bet(env.conn, "model_only", "A", placed_at="2026-08-29T00:00:00Z",
            status="pending", fixture_key="ev_old")            # 窗口前：不计
    env.conn.commit()

    out = weekly.weekly_summary(env.conn)
    assert out["empty"] is False
    assert out["week"] == "2026-08-31 ~ 2026-09-06"   # 北京日期展示
    mo = out["by_strategy"]["model_only"]
    assert mo["placed"] == 2 and mo["settled"] == 1 and mo["won"] == 1
    assert mo["pnl"] == pytest.approx(20.0)
    assert mo["roi"] == pytest.approx(1.0)             # 20/20（settled stake）
    assert mo["clv_median"] == pytest.approx(0.10)
    mp = out["by_strategy"]["model_persona"]
    assert mp["placed"] == 1 and mp["settled"] == 1 and mp["pnl"] == pytest.approx(-20.0)
    assert mp["clv_median"] == pytest.approx(-0.05)
    assert out["pending"] == 2                          # 含窗口外在途注


def test_bankroll_start_derived_from_end_minus_pnl(env):
    set_meta(env.conn, "paper_bankroll:model_only", "1020.0")
    env.conn.commit()
    add_bet(env.conn, "model_only", "H", placed_at=ISO_WS, settled_at=ISO_MID,
            status="won", return_amt=40.0)
    env.conn.commit()
    mo = weekly.weekly_summary(env.conn)["by_strategy"]["model_only"]
    assert mo["bankroll_end"] == pytest.approx(1020.0)
    assert mo["bankroll_start"] == pytest.approx(1000.0)   # 1020 − 20


# ---------------------------------------------------------------- 渲染与 CLI


def test_render_contains_both_tracks_and_rois(env):
    add_bet(env.conn, "model_only", "H", placed_at=ISO_WS, settled_at=ISO_MID,
            status="won", return_amt=40.0, clv=0.10)
    add_bet(env.conn, "model_persona", "A", placed_at=ISO_WS, settled_at=ISO_MID,
            status="lost", return_amt=0.0, clv=-0.05)
    env.conn.commit()
    text = weekly.render_weekly(weekly.weekly_summary(env.conn))
    assert "model_only" in text and "model_persona" in text
    assert "+100.0%" in text and "-100.0%" in text           # ROI 双轨
    assert "2026-08-31 ~ 2026-09-06" in text


def test_cli_weekly_empty_week_pushes_nothing(env, monkeypatch):
    from fa.pipeline import weekly as w
    monkeypatch.setattr(w, "send",
                        lambda text: env.pushed.append(text) or True)
    result = runner.invoke(app, ["ops", "weekly"])
    assert result.exit_code == 0
    assert env.pushed == []
    assert "静默" in result.output


def test_cli_weekly_active_week_sends(env, monkeypatch):
    add_bet(env.conn, "model_only", "H", placed_at=ISO_WS, settled_at=ISO_MID,
            status="won", return_amt=40.0, clv=0.10)
    env.conn.commit()
    from fa.pipeline import weekly as w
    monkeypatch.setattr(w, "send",
                        lambda text: env.pushed.append(text) or True)
    result = runner.invoke(app, ["ops", "weekly"])
    assert result.exit_code == 0
    assert len(env.pushed) == 1 and "周度小结" in env.pushed[0]
