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


def test_run_manual_requires_a_filter(db, tmp_path, monkeypatch):
    """零过滤护栏（额度纪律）：manual 三参全空 = 全库逐场真调 LLM，
    必须在选场前拒绝，exit 1 + 明示原因（实测 11.8s/场 × 59k 场）。"""
    from fa.retro import pipeline
    calls = {"n": 0}

    def must_not_call(prompt):
        calls["n"] += 1
        raise AssertionError("护栏应先退出，不得触达 run_headless")

    monkeypatch.setattr(pipeline, "run_headless", must_not_call)
    result = runner.invoke(
        app, ["retro", "run", "--selector", "manual",
              "--out-root", str(tmp_path / "packs")])
    assert result.exit_code == 1
    assert "--matches" in result.output and "--season" in result.output
    assert calls["n"] == 0


def test_run_divergence_batch_records_league_and_control(db, tmp_path,
                                                         monkeypatch):
    """divergence 台账 params 记 league（与 manual 对齐）；批内病例/对照落库
    且 is_control 可逐行区分（0=病例 / 1=对照）。"""
    from fa.retro import pipeline

    def fake_headless(prompt):
        return {"ok": True, "output": json.dumps({
            "miss_tags": ["variance"], "primary_tag": "variance",
            "tags_confidence": 0.5, "model_vs_market": "variance",
            "evidence": [], "digest": "波动。"}, ensure_ascii=False),
            "error": None, "timeout": False, "duration_s": 0.2}

    monkeypatch.setattr(pipeline, "run_headless", fake_headless)
    r = runner.invoke(
        app, ["retro", "run", "--selector", "divergence", "--top", "1",
              "--control", "1", "--league", "E0",
              "--out-root", str(tmp_path / "p")])
    assert r.exit_code == 0, r.output
    conn = connect(db)
    try:
        run = conn.execute("SELECT params_json FROM retro_runs").fetchone()
        assert json.loads(run["params_json"])["league"] == "E0"
        rows = conn.execute(
            "SELECT match_id, is_control FROM retro_attributions"
            " ORDER BY match_id").fetchall()
        assert len(rows) == 2                       # 1 病例 + 1 对照
        assert [x["is_control"] for x in rows] == [0, 1]
    finally:
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


def _attr(conn, batch_id, match_id, date, evidence, tags=("injury",)):
    """直插一条 status=ok 的归因行（审计用例不依赖 run→audit 全链路）。"""
    conn.execute(
        "INSERT INTO retro_attributions (batch_id, match_id, league, season,"
        " date, selector, miss_tags_json, primary_tag, tags_confidence,"
        " model_vs_market, evidence_json, digest, status, repaired, harness,"
        " model, duration_s, input_pack_path, tag_set_version, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (batch_id, match_id, "E0", 2023, date, "manual",
         json.dumps(list(tags)), tags[0], 0.5, "model_wrong",
         json.dumps(evidence, ensure_ascii=False), "d", "ok", 0, "hermes",
         None, 0.1, "x", "v1", "2026-09-04T00:00:00Z"))


def test_audit_unparseable_date_is_violation(db):
    """spec §8-1：证据须可证早于开球——不可解析一律违规，不靠字典序误打误撞。

    0000-00-00 字典序早于比赛日（旧实现静默放行）；2024/04/19 因 `/`>`-`
    被误判「不早于」（旧实现理由错）。另含富格式 ISO 时间戳应放行的容忍臂。
    """
    conn = connect(db)
    conn.execute(
        "INSERT INTO retro_runs (id, selector, params_json, n_selected, n_ok,"
        " n_parse_fail, n_timeout, n_error, duration_s, created_at)"
        " VALUES (1, 'manual', '{}', 3, 3, 0, 0, 0, 0.3, 'now')")
    _attr(conn, 1, 1, "2024-04-20",
          [{"title": "幻觉日期", "date": "0000-00-00", "url": "https://e/1"}])
    _attr(conn, 1, 2, "2024-04-21",
          [{"title": "斜杠日期", "date": "2024/04/19", "url": "https://e/2"}])
    # 富格式（ISO 时间戳）取前 10 字符可解析且早于比赛日 → 放行
    _attr(conn, 1, 3, "2024-04-22",
          [{"title": "富格式", "date": "2024-04-21T10:00:00Z",
            "url": "https://e/3"}])
    conn.commit()
    conn.close()
    a = runner.invoke(app, ["retro", "audit"])
    assert a.exit_code == 0
    assert "检查 3 行" in a.output and "违规 2" in a.output
    assert "不可解析" in a.output
    assert "0000-00-00" in a.output and "2024/04/19" in a.output


def test_runs_lists_ledger(db, tmp_path, monkeypatch):
    from fa.retro import pipeline
    monkeypatch.setattr(
        pipeline, "run_headless",
        lambda p: {"ok": False, "output": "", "error": "hermes -z 超时（300s）",
                   "timeout": True, "duration_s": 300.0})
    runner.invoke(app, ["retro", "run", "--selector", "manual",
                        "--matches", "1", "--out-root", str(tmp_path / "p")])
    r = runner.invoke(app, ["retro", "runs"])
    assert r.exit_code == 0
    assert "manual" in r.output and "timeout=1" in r.output
