"""resend_report——`fa report send` 内核（spec §9.3，v0.14 补实现）。

按 runs 行重渲染并重推报告：matchday（am 全量 / pm_update）与 daily 结算简报
（含 retro 段重查）。渲染是 DB + summary 的确定函数，重发不写库——原 run 的
telegram 记录是历史事实，重推只出 stdout。
"""
import pytest

from fa.db import connect, init_db
from fa.pipeline import reporting
from fa.pipeline.runs import STATUS_OK, begin_run, finish_run


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    init_db(path)
    conn = connect(path)
    yield conn
    conn.close()


def _seed_matchday(conn, phase="am", report="full", **extra):
    run_id = begin_run(conn, "matchday", phase)
    summary = {"phase": phase, "report": report, "quota_left": 100,
               "degraded": False}
    summary.update(extra)
    finish_run(conn, run_id, STATUS_OK, summary)
    return run_id


def _seed_daily(conn, settled=3, **extra):
    run_id = begin_run(conn, "daily")
    summary = {"settled": settled, "won": 1, "pnl": 12.5,
               "clv_median": 0.029, "telegram": {"sent": True, "error": None}}
    summary.update(extra)
    finish_run(conn, run_id, STATUS_OK, summary)
    return run_id


def _capture_send(monkeypatch, ok=True):
    sent = []

    def fake(text):
        sent.append(text)
        return ok

    monkeypatch.setattr(reporting, "send", fake)
    return sent


class TestMatchdayResend:
    def test_默认重发最近_matchday_报告(self, db, monkeypatch):
        sent = _capture_send(monkeypatch)
        rid = _seed_matchday(db, "am")
        out = reporting.resend_report(db)
        assert out == {"run_id": rid, "type": "matchday", "sent": True,
                       "error": None}
        assert len(sent) == 1 and "比赛日报告" in sent[0]

    def test_pm_update_走更新版渲染(self, db, monkeypatch):
        sent = _capture_send(monkeypatch)
        am = _seed_matchday(db, "am")
        pm = _seed_matchday(db, "pm", report="pm_update", am_run_id=am)
        out = reporting.resend_report(db)
        assert out["run_id"] == pm and out["sent"] is True
        assert sent and sent[0]          # 空场也能渲染出更新版正文

    def test_指定_run_id_重发旧行(self, db, monkeypatch):
        sent = _capture_send(monkeypatch)
        old = _seed_matchday(db, "am")
        _seed_daily(conn=db)             # 更新的 daily 不抢默认位外的指定重发
        out = reporting.resend_report(db, run_id=old)
        assert out["run_id"] == old and out["sent"] is True


class TestDailyResend:
    def test_daily_有结算_重发简报(self, db, monkeypatch):
        sent = _capture_send(monkeypatch)
        rid = _seed_daily(db, settled=3)
        out = reporting.resend_report(db)
        assert out == {"run_id": rid, "type": "daily", "sent": True,
                       "error": None}
        assert "结算简报" in sent[0] and "结算 3 注" in sent[0]

    def test_daily_零结算_原即无报告(self, db):
        _seed_daily(db, settled=0)
        out = reporting.resend_report(db)
        assert out["sent"] is False
        assert "无报告" in out["error"]

    def test_daily_带_retro_批_重查追加段落(self, db, monkeypatch):
        sent = _capture_send(monkeypatch)
        _seed_daily(db, retro={"batch_id": 1, "n_selected": 2, "n_ok": 2})
        out = reporting.resend_report(db)
        assert out["sent"] is True        # retro 行不存在时段落为空、简报仍可发


class TestErrors:
    def test_无_runs_可重发(self, db):
        out = reporting.resend_report(db)
        assert out["sent"] is False and "无可重发" in out["error"]

    def test_run_id_不存在(self, db):
        out = reporting.resend_report(db, run_id=999)
        assert out["sent"] is False and "999" in out["error"]

    def test_不支持的_run_类型被跳过(self, db):
        rid = begin_run(db, "backtest")
        finish_run(db, rid, STATUS_OK, {"n": 1})
        out = reporting.resend_report(db)
        assert out["sent"] is False and "无可重发" in out["error"]

    def test_推送失败_带上原因(self, db, monkeypatch):
        _capture_send(monkeypatch, ok=False)
        monkeypatch.setattr(reporting, "last_error",
                            lambda: "exit 1: boom")
        _seed_matchday(db, "am")
        out = reporting.resend_report(db)
        assert out["sent"] is False and "boom" in out["error"]
