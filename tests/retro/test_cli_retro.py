"""retro CLI 测试——CliRunner + FA_DB 临时库；网络打桩在 runner 层之上
（CLI 不传 call，run_retro_batch 的 call=None 缝在**调用时**解析模块属性，
故 monkeypatch fa.retro.pipeline.run_headless 生效；patch
fa.retro.runner.run_headless 动不到 pipeline 命名空间里已 import 的绑定）。"""
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fa.cli import app
from fa.db import connect, init_db

runner = CliRunner()


@pytest.fixture
def db(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("FA_DB", str(db))
    init_db(db)
    conn = connect(db)
    from tests.retro.test_select import _seed
    _seed(conn)
    conn.close()
    return db


def test_report_renders_divergence(db):
    """S0 验收：纯 SQL 报告，零 LLM 调用（不触网即可跑）。"""
    result = runner.invoke(app, ["retro", "report", "--top", "2"])
    assert result.exit_code == 0
    assert "Arsenal" in result.output
    assert "div" in result.output or "分歧" in result.output


def test_run_manual_batch(db, tmp_path, monkeypatch):
    from fa.retro import pipeline

    def fake_headless(prompt):
        assert "Arsenal" in prompt            # prompt 确含信息集
        return {"ok": True, "output": json.dumps({
            "miss_tags": ["injury"], "primary_tag": "injury",
            "tags_confidence": 0.8, "model_vs_market": "model_wrong",
            "evidence": [{"title": "t", "date": "2024-04-01",
                          "url": "https://e.com"}],
            "digest": "伤停致模型高估主胜。"}, ensure_ascii=False),
            "error": None, "duration_s": 0.5}

    monkeypatch.setattr(pipeline, "run_headless", fake_headless)
    result = runner.invoke(
        app, ["retro", "run", "--selector", "manual",
              "--matches", "1", "--out-root", str(tmp_path / "packs")])
    assert result.exit_code == 0, result.output
    assert "ok=1" in result.output or "ok 1" in result.output
    conn = connect(db)
    assert conn.execute(
        "SELECT COUNT(*) c FROM retro_attributions").fetchone()["c"] == 1
    conn.close()


def test_audit_flags_late_evidence(db, tmp_path, monkeypatch):
    """证据日期晚于比赛日（2024-04-20）→ 违规；早于 → 通过。"""
    from fa.retro import pipeline

    late = json.dumps({
        "miss_tags": ["injury"], "primary_tag": "injury",
        "tags_confidence": 0.9, "model_vs_market": "model_wrong",
        "evidence": [{"title": "赛后复盘文", "date": "2024-04-25",
                      "url": "https://e.com/late"}],
        "digest": "x" * 10}, ensure_ascii=False)

    def fake_headless(prompt):
        return {"ok": True, "output": late, "error": None, "duration_s": 0.1}

    monkeypatch.setattr(pipeline, "run_headless", fake_headless)
    r = runner.invoke(
        app, ["retro", "run", "--selector", "manual", "--matches", "1,2",
              "--out-root", str(tmp_path / "p")])
    assert r.exit_code == 0
    a = runner.invoke(app, ["retro", "audit"])
    assert a.exit_code == 0
    assert "违规" in a.output and "2024-04-25" in a.output


def test_audit_passes_early_evidence(db, tmp_path, monkeypatch):
    early = json.dumps({
        "miss_tags": ["injury"], "primary_tag": "injury",
        "tags_confidence": 0.9, "model_vs_market": "model_wrong",
        "evidence": [{"title": "赛前伤停名单", "date": "2024-04-19",
                      "url": "https://e.com/ok"}],
        "digest": "y" * 10}, ensure_ascii=False)
    from fa.retro import pipeline
    monkeypatch.setattr(pipeline, "run_headless",
                        lambda p: {"ok": True, "output": early,
                                   "error": None, "duration_s": 0.1})
    runner.invoke(app, ["retro", "run", "--selector", "manual",
                        "--matches", "1", "--out-root", str(tmp_path / "p")])
    a = runner.invoke(app, ["retro", "audit"])
    assert a.exit_code == 0
    assert "违规 0" in a.output


def test_runs_lists_ledger(db, tmp_path, monkeypatch):
    from fa.retro import pipeline
    monkeypatch.setattr(
        pipeline, "run_headless",
        lambda p: {"ok": False, "output": "", "error": "hermes -z 超时（300s）",
                   "duration_s": 300.0})
    runner.invoke(app, ["retro", "run", "--selector", "manual",
                        "--matches", "1", "--out-root", str(tmp_path / "p")])
    r = runner.invoke(app, ["retro", "runs"])
    assert r.exit_code == 0
    assert "manual" in r.output and "timeout=1" in r.output
