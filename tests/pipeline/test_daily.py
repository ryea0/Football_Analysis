"""结算日课测试（T10，spec §3.4 / §7.3 / §9.5）。

全离线：``sync_history`` / 推送缝（``daily.send`` / ``daily.last_error``）全部换成
替身——``sync_history`` 会真触网（football-data.co.uk 下载），绝不让它跑；
``urlopen`` 打成炸弹兜底。种子沿用 test_paper 模式（fixture + 完赛行 + 推荐 +
pending 注），让 ``settle_paper_bets`` 真结算。

**顺序是本层的语义**：结算依赖新完赛数据 → sync 先行；sync 抛错只降级（用旧数据
结算并在 summary 标注），不阻断。专门有一条测试钉住这个先后（用调用序记录器）。
"""
import json
import urllib.request
from types import SimpleNamespace

import pytest

from fa.data.sync import SyncReport
from fa.db import connect, init_db
from fa.pipeline import daily

LEAGUE = "E0"


def _boom(*args, **kwargs):
    raise AssertionError("daily 不得绕过注入缝触网")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """离线环境：sync / 推送缝换记录器（box.sync_report 可改写、box.ok 可翻转）。"""
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    box = SimpleNamespace(
        conn=c, pushed=[], order=[],
        sync_report=SyncReport(files_ok=5, inserted=7),
        sync_error=None, ok=True, error=None)

    def fake_sync(conn, **kwargs):
        box.order.append("sync")
        if box.sync_error:
            raise box.sync_error
        return box.sync_report

    monkeypatch.setattr(daily, "sync_history", fake_sync)

    real_settle = daily.settle_paper_bets

    def spy_settle(conn):
        box.order.append("settle")                   # 记录「结算」这一步的时点
        return real_settle(conn)

    monkeypatch.setattr(daily, "settle_paper_bets", spy_settle)
    monkeypatch.setattr(daily, "send",
                        lambda text: (box.pushed.append(text), box.ok)[1])
    monkeypatch.setattr(daily, "last_error", lambda: box.error)
    yield box
    c.close()


def _seed_team(c, name):
    return c.execute("INSERT INTO teams (league, name) VALUES (?, ?)",
                     (LEAGUE, name)).lastrowid


def _seed_run(c, started="2026-09-01T03:00:00Z"):
    return c.execute(
        "INSERT INTO runs (type, phase, started_at, status)"
        " VALUES ('matchday','am',?,'ok')", (started,)).lastrowid


def _seed_fixture(c, h, a, event_key="ev1"):
    return c.execute(
        "INSERT INTO fixtures (league, event_key, source, kickoff_utc,"
        " home_team_id, away_team_id, status, created_at)"
        " VALUES (?,?,'oddsapi','2026-09-02T14:00:00Z', ?, ?,"
        " 'scheduled', '2026-09-01T08:00:00Z')",
        (LEAGUE, event_key, h, a)).lastrowid


def _seed_pending_bet(c, fixture_id, run_id, market="H"):
    """一支 pending paper 注（stake 20 @ 2.0），返回 recommendation id。"""
    rec = c.execute(
        "INSERT INTO recommendations (run_id, fixture_id, strategy, market,"
        " phase, model_p, market_p, best_odds, bookmaker, edge, ev,"
        " kelly_stake_frac, created_at)"
        " VALUES (?, ?, 'model_only', ?, 'am', 0.5, 0.4, 2.0, 'pinnacle',"
        " 0.1, 0.3, 0.02, '2026-09-01T03:00:00Z')",
        (run_id, fixture_id, market)).lastrowid
    c.execute(
        "INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker,"
        " odds_taken, stake, status)"
        " VALUES (?, 'paper', '2026-09-01T03:00:00Z', 'pinnacle', 2.0, 20.0,"
        " 'pending')", (rec,))
    return rec


def _seed_settleable(c, closing=1.8):
    """一支已下注且已完赛（可配对）的 paper 注：won、回报 40、CLV 2/1.8−1。"""
    h, a = _seed_team(c, "Chelsea"), _seed_team(c, "Arsenal")
    c.execute(
        "INSERT INTO matches (league, season, date, home_team_id, away_team_id,"
        " fthg, ftag, psc_home, raw_line)"
        " VALUES (?, 2026, '2026-09-02', ?, ?, 2, 1, ?, '{}')",
        (LEAGUE, h, a, closing))
    rec = _seed_pending_bet(c, _seed_fixture(c, h, a), _seed_run(c))
    c.commit()
    return rec


def run_row(c, run_id):
    return dict(c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())


def summary_of(c, run_id):
    return json.loads(run_row(c, run_id)["summary"])


# ---------------------------------------------------------------- 结算 → 简报


def test_settled_bets_push_brief_and_record_daily_run(env):
    """有完赛可结：sync → 结算 → 一行简报推送，runs 记 type='daily'。"""
    c = env.conn
    _seed_settleable(c)

    out = daily.run_daily(c)

    assert env.order == ["sync", "settle"]           # sync 先行（结算要新完赛）
    assert out["status"] == "ok" and out["sent"] is True
    assert out["settled"] == 1 and out["won"] == 1
    assert out["pnl"] == pytest.approx(20.0)
    assert out["clv_median"] == pytest.approx(2.0 / 1.8 - 1)
    assert len(env.pushed) == 1
    assert "结算" in env.pushed[0]

    row = run_row(c, out["run_id"])
    assert row["type"] == "daily" and row["phase"] is None
    assert row["status"] == "ok" and row["finished_at"]
    assert row["credits_after"] is None              # daily 不拉实时盘
    summary = summary_of(c, out["run_id"])
    assert summary["settled"] == 1 and summary["won"] == 1
    assert summary["pnl"] == pytest.approx(20.0)
    assert summary["sync"] == {"files_ok": 5, "inserted": 7, "file_errors": 0}
    assert summary["sync_error"] is None
    assert summary["telegram"] == {"sent": True, "error": None}


def test_no_settlement_stays_silent(env):
    """无可结注：静默（不推送、不造噪音），run 照常收尾。"""
    out = daily.run_daily(env.conn)

    assert out["status"] == "ok" and out["sent"] is None
    assert out["settled"] == 0 and env.pushed == []
    assert run_row(env.conn, out["run_id"])["status"] == "ok"
    assert summary_of(env.conn, out["run_id"])["telegram"] is None


def test_pending_but_unplayable_also_stays_silent(env):
    """有 pending 注但配不上完赛（settled=0）：同样不推简报。"""
    c = env.conn
    h, a = _seed_team(c, "Chelsea"), _seed_team(c, "Arsenal")
    _seed_pending_bet(c, _seed_fixture(c, h, a), _seed_run(c))
    c.commit()

    out = daily.run_daily(c)
    assert out["settled"] == 0 and env.pushed == []


def test_all_files_failed_is_degraded_but_settlement_still_runs(env):
    """降级判据看 SyncReport：一个文件都没成（file_errors>0 且 files_ok==0）
    ＝用了旧数据结算 → degraded_ok，且结算照常。"""
    c = env.conn
    _seed_settleable(c)
    env.sync_report = SyncReport(
        files_ok=0, file_errors=[("E0", 2026, "HTTP 403"), ("SP1", 2026, "超时")])

    out = daily.run_daily(c)

    assert env.order == ["sync", "settle"]           # 顺序语义不变
    assert out["status"] == "degraded_ok"
    assert out["settled"] == 1 and out["won"] == 1   # 降级不阻断结算
    summary = summary_of(c, out["run_id"])
    assert summary["sync_degraded"] is True
    assert summary["sync"] == {"files_ok": 0, "inserted": 0, "file_errors": 2}
    assert summary["sync_error"] is None             # 没抛错，是报告口径判定
    assert run_row(c, out["run_id"])["status"] == "degraded_ok"


def test_partial_sync_success_is_not_degraded(env):
    """部分赛季失败（files_ok>0）：拿到了新完赛，不算降级，也不标注。"""
    c = env.conn
    _seed_settleable(c)
    env.sync_report = SyncReport(files_ok=168, inserted=59000,
                                 file_errors=[("F1", 1997, "空文件")])

    out = daily.run_daily(c)

    assert out["status"] == "ok"
    assert summary_of(c, out["run_id"])["sync_degraded"] is False


def test_sync_runs_before_settlement(env, monkeypatch):
    """顺序钉死：结算依赖新完赛数据，sync 必须先行（哪怕真实 sync 内部容错）。"""
    def stub_settle(conn):
        env.order.append("settle")                   # 仍记时点，只是不真结算
        return {"settled": 0, "won": 0, "pnl": 0.0, "clv_median": None}

    monkeypatch.setattr(daily, "settle_paper_bets", stub_settle)
    out = daily.run_daily(env.conn)
    assert env.order == ["sync", "settle"]
    assert out["settled"] == 0


def test_sync_failure_degrades_but_settlement_proceeds(env):
    """sync 整体抛错：捕获降级用旧数据结算并在 summary 标注（不阻断日课）。"""
    c = env.conn
    _seed_settleable(c)
    env.sync_error = RuntimeError("网络不可达")

    out = daily.run_daily(c)

    assert env.order == ["sync", "settle"]           # 失败也不改变先后语义
    assert out["status"] == "degraded_ok"
    assert out["settled"] == 1 and out["won"] == 1
    assert out["sync"] is None
    summary = summary_of(c, out["run_id"])
    assert "RuntimeError" in summary["sync_error"]
    assert run_row(c, out["run_id"])["status"] == "degraded_ok"


def test_push_failure_is_recorded_but_does_not_break_daily(env):
    """推送失败（T8 的 hermes 非零退出）：降级标注进 summary，run 仍收尾。"""
    c = env.conn
    _seed_settleable(c)
    env.ok, env.error = False, "exit 1: send failed"

    out = daily.run_daily(c)

    assert out["status"] == "ok" and out["sent"] is False
    assert len(env.pushed) == 1
    assert summary_of(c, out["run_id"])["telegram"] == {
        "sent": False, "error": "exit 1: send failed"}


def test_daily_run_still_recorded_when_settlement_raises(env, monkeypatch):
    """结算上抛：run 记 'failed' 后原样外抛——日课不留「无 audit」的悬空。"""
    def boom(conn):
        raise ValueError("台账炸了")

    monkeypatch.setattr(daily, "settle_paper_bets", boom)
    with pytest.raises(ValueError):
        daily.run_daily(env.conn)
    row = env.conn.execute(
        "SELECT status, summary FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "failed"
    assert "台账炸了" in row["summary"]


# ---------------------------------------------------------------- CLI 面


def test_cli_daily_reports_settlement(tmp_path, monkeypatch):
    """中文输出：结算行数 / 判决位 / 推送结果。"""
    from typer.testing import CliRunner
    from fa.cli import app

    db = tmp_path / "t.db"
    monkeypatch.setenv("FA_DB", str(db))
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    init_db(db)
    c = connect(db)
    try:
        _seed_settleable(c)
        monkeypatch.setattr(
            daily, "sync_history",
            lambda conn, **kw: SyncReport(files_ok=5, inserted=7))
        monkeypatch.setattr(daily, "send", lambda text: True)
        result = CliRunner().invoke(app, ["run", "daily"])
        assert result.exit_code == 0, result.output
        assert "结算 1 注" in result.output
        assert "推送" in result.output
    finally:
        c.close()


def test_cli_daily_silent_when_nothing_settled(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from fa.cli import app

    db = tmp_path / "t.db"
    monkeypatch.setenv("FA_DB", str(db))
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    init_db(db)
    monkeypatch.setattr(daily, "sync_history", lambda conn, **kw: SyncReport())
    monkeypatch.setattr(daily, "send",
                        lambda text: (_ for _ in ()).throw(AssertionError("不应推送")))
    result = CliRunner().invoke(app, ["run", "daily"])
    assert result.exit_code == 0, result.output
    assert "结算 0 注" in result.output
