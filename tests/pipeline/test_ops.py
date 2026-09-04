"""运维告警测试（M5 ops，spec 风险 #6：跑批失败要有 TG 告警）。

全离线：推送缝（``ops.send``）换记录器；runs 表用真库（init_db）直接 SQL 种子
——watchdog 判据是「对 runs 表的查询契约」，种子即契约的另一半。

判据设计为**纯查询、不依赖时钟**：取最近两次成功（ok / degraded_ok）daily 的
``started_at`` 间隔，间隔即漏跑 / 连续失败的证据——测试无需注入 now。
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from fa.cli import app
from fa.db import connect, init_db
from fa.pipeline import ops

runner = CliRunner()

T0 = datetime(2026, 9, 3, 6, 30, tzinfo=timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed(conn, type_: str, phase: str | None, started_at: str, status: str):
    conn.execute(
        "INSERT INTO runs (type, phase, started_at, status)"
        " VALUES (?, ?, ?, ?)", (type_, phase, started_at, status))
    conn.commit()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """离线环境：真库（FA_DB 指过去，CLI 与直调同库）+ 推送缝记录器。"""
    monkeypatch.setenv("FA_DB", str(tmp_path / "t.db"))
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    box = SimpleNamespace(conn=c, pushed=[], ok=True)
    monkeypatch.setattr(ops, "send",
                        lambda text: (box.pushed.append(text), box.ok)[1])
    yield box
    c.close()


# ---------------------------------------------------------------- send_alert


def test_send_alert_passes_text_to_reporting_send(env):
    assert ops.send_alert("hello") is True
    assert env.pushed == ["hello"]


def test_send_alert_returns_false_when_push_fails_without_raising(
        env, monkeypatch):
    env.ok = False
    assert ops.send_alert("hello") is False


# ---------------------------------------------------------------- check_daily


def test_check_daily_silent_when_consecutive_successes_within_24h(env):
    _seed(env.conn, "daily", None, _iso(T0), "ok")
    _seed(env.conn, "daily", None, _iso(T0 + timedelta(hours=24)), "degraded_ok")
    assert ops.check_daily(env.conn) is None


def test_check_daily_threshold_25h_is_exclusive(env):
    """间隔恰 25h 不告警（严格大于才告警，与额度梯子「严格小于」同款钉法）。"""
    _seed(env.conn, "daily", None, _iso(T0), "ok")
    _seed(env.conn, "daily", None, _iso(T0 + timedelta(hours=25)), "ok")
    assert ops.check_daily(env.conn) is None


def test_check_daily_alerts_when_gap_exceeds_threshold(env):
    _seed(env.conn, "daily", None, _iso(T0), "ok")
    _seed(env.conn, "daily", None, _iso(T0 + timedelta(hours=30)), "ok")
    text = ops.check_daily(env.conn)
    assert text is not None
    assert "30.0h" in text
    assert "阈值 25h" in text


def test_check_daily_silent_with_only_one_successful_daily(env):
    _seed(env.conn, "daily", None, _iso(T0), "ok")
    assert ops.check_daily(env.conn) is None


def test_check_daily_alerts_when_no_successful_daily_ever(env):
    _seed(env.conn, "daily", None, _iso(T0), "failed")
    text = ops.check_daily(env.conn)
    assert text is not None
    assert "没有任何成功" in text


def test_check_daily_ignores_failed_running_and_matchday_rows(env):
    """failed / running / matchday 行都不算「成功 daily」——只剩一次成功即静默。"""
    _seed(env.conn, "daily", None, _iso(T0), "ok")
    _seed(env.conn, "daily", None, _iso(T0 + timedelta(hours=26)), "failed")
    _seed(env.conn, "daily", None, _iso(T0 + timedelta(hours=27)), "running")
    _seed(env.conn, "matchday", "am", _iso(T0 + timedelta(hours=28)), "ok")
    assert ops.check_daily(env.conn) is None


# ---------------------------------------------------------------- watchdog


def test_watchdog_pushes_alert_text_when_gap_breached(env):
    _seed(env.conn, "daily", None, _iso(T0), "ok")
    _seed(env.conn, "daily", None, _iso(T0 + timedelta(hours=30)), "ok")
    out = ops.run_watchdog(env.conn)
    assert out["sent"] is True
    assert out["alert"] is not None
    assert env.pushed == [out["alert"]]


def test_watchdog_silent_and_no_push_when_healthy(env):
    _seed(env.conn, "daily", None, _iso(T0), "ok")
    _seed(env.conn, "daily", None, _iso(T0 + timedelta(hours=24)), "ok")
    out = ops.run_watchdog(env.conn)
    assert out == {"alert": None, "sent": None}
    assert env.pushed == []


# ---------------------------------------------------------------- CLI


def test_cli_ops_alert_sends_and_reports(env):
    result = runner.invoke(app, ["ops", "alert", "测试告警"])
    assert result.exit_code == 0
    assert "已发" in result.output


def test_cli_ops_alert_push_failure_exits_nonzero(env):
    env.ok = False
    result = runner.invoke(app, ["ops", "alert", "测试告警"])
    assert result.exit_code == 1
    assert "失败" in result.output


def test_cli_ops_watchdog_prints_alert_when_breached(env):
    _seed(env.conn, "daily", None, _iso(T0), "ok")
    _seed(env.conn, "daily", None, _iso(T0 + timedelta(hours=30)), "ok")
    result = runner.invoke(app, ["ops", "watchdog"])
    assert result.exit_code == 0
    assert "30.0h" in result.output
    assert len(env.pushed) == 1


def test_cli_ops_watchdog_quiet_when_healthy(env):
    _seed(env.conn, "daily", None, _iso(T0), "ok")
    _seed(env.conn, "daily", None, _iso(T0 + timedelta(hours=24)), "ok")
    result = runner.invoke(app, ["ops", "watchdog"])
    assert result.exit_code == 0
    assert env.pushed == []


def test_cli_ops_watchdog_exit_1_when_alert_push_fails(env):
    _seed(env.conn, "daily", None, _iso(T0), "ok")
    _seed(env.conn, "daily", None, _iso(T0 + timedelta(hours=30)), "ok")
    env.ok = False
    result = runner.invoke(app, ["ops", "watchdog"])
    assert result.exit_code == 1
    assert "30.0h" in result.output


# ---------------------------------------------------------------- M6 evolve job


def test_cron_wrapper_accepts_evolve_job():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    script = (root / "scripts" / "fa_cron.sh").read_text()
    assert '"$JOB" != evolve' in script
    assert "evolve)  CMD=(fa evolve tick) ;;" in script


def test_cron_jobs_file_has_evolve_weekly_slot():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    jobs = (root / "scripts" / "cron_jobs.txt").read_text()
    line = next(l for l in jobs.splitlines() if l.startswith("evolve\t"))
    assert line.split("\t")[1] == "17 3 * * 0"


def test_cron_wrapper_evolve_branch_bash_syntax():
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(["bash", "-n", str(root / "scripts" / "fa_cron.sh")],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
