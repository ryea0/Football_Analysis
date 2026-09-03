"""批跑编排测试——runner 打桩（call 参数注入）；DB 断言逐列。

单场失败不中断批；parse_fail/timeout 行只留审计字段；raw 不落库
（设计 §7 无 raw_output 列——输入留档走 input_pack_path，输出可在
digest/evidence 为 NULL 时经日志追查，重放凭据是输入侧）。
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from fa.db import connect, init_db
from fa.retro import pipeline
from fa.retro.pipeline import run_retro_batch

_NOW = datetime(2026, 9, 4, 8, 0, 0, tzinfo=timezone.utc)


def _ok_call(prompt):
    return {"ok": True, "output": json.dumps({
        "miss_tags": ["injury"], "primary_tag": "injury",
        "tags_confidence": 0.8, "model_vs_market": "model_wrong",
        "evidence": [{"title": "t", "date": "2024-04-01",
                      "url": "https://e.com"}],
        "digest": "主队伤停致模型高估。"}, ensure_ascii=False),
        "error": None, "duration_s": 1.5}


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    c = connect(db)
    # 种子：复用 test_select 的三场（导入其 _seed——同仓测试间导入惯例见
    # tests/pipeline 各文件；此处直接 from tests.retro.test_select import _seed）
    from tests.retro.test_select import _seed
    _seed(c)
    yield c
    c.close()


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(pipeline, "_now", lambda: _NOW)


def _cands(conn):
    from fa.retro.select import select_manual
    return select_manual(conn, match_ids=[1, 2, 3])


def test_batch_all_ok(conn, clock, tmp_path):
    out = run_retro_batch(conn, _cands(conn), "manual", {"match_ids": [1, 2, 3]},
                          tmp_path / "packs", call=_ok_call)
    assert (out["n_ok"], out["n_parse_fail"], out["n_timeout"],
            out["n_error"]) == (3, 0, 0, 0)
    rows = conn.execute(
        "SELECT * FROM retro_attributions WHERE batch_id=?",
        (out["batch_id"],)).fetchall()
    assert len(rows) == 3
    r1 = [r for r in rows if r["match_id"] == 1][0]
    assert r1["status"] == "ok" and r1["primary_tag"] == "injury"
    assert r1["harness"] == "hermes" and r1["tag_set_version"] == "v1"
    assert r1["created_at"] == "2026-09-04T08:00:00Z"
    assert Path(r1["input_pack_path"]).exists()      # 输入留档真实落盘
    run = conn.execute("SELECT * FROM retro_runs WHERE id=?",
                       (out["batch_id"],)).fetchone()
    assert run["selector"] == "manual" and run["n_ok"] == 3
    assert all(r["is_control"] == 0 for r in rows)   # manual 批全病例


def test_batch_persists_is_control(conn, clock, tmp_path):
    """divergence 批：对照行 is_control=1、病例行=0——病例-对照标记必须
    可从 DB 逐行查（关卡 3 分层与 analyze 的必需字段）。"""
    from fa.retro.select import select_divergence
    cands = select_divergence(conn, top_k=1, control_k=1, seed=7)
    assert len(cands) == 2
    out = run_retro_batch(conn, cands, "divergence",
                          {"top": 1, "control": 1, "seed": 7},
                          tmp_path / "packs", call=_ok_call)
    assert out["n_ok"] == 2
    rows = {r["match_id"]: r["is_control"] for r in conn.execute(
        "SELECT match_id, is_control FROM retro_attributions")}
    assert rows[1] == 0                              # 唯一正 div → 病例
    ctrl_id = next(m for m in rows if m != 1)
    assert rows[ctrl_id] == 1                        # 对照池抽出 → 对照


def test_batch_persists_is_control_from_candidate_flag(conn, clock, tmp_path):
    """落库值取自 cand 的 is_control 标记本身（不只对 divergence 选择器成立）。"""
    cands = _cands(conn)[:1]
    cands[0]["is_control"] = True
    out = run_retro_batch(conn, cands, "manual", {}, tmp_path / "packs",
                          call=_ok_call)
    assert out["n_ok"] == 1
    assert conn.execute("SELECT is_control FROM retro_attributions"
                        ).fetchone()["is_control"] == 1


def test_single_failure_does_not_abort_batch(conn, clock, tmp_path):
    calls = {"n": 0}

    def flaky(prompt):
        calls["n"] += 1
        if calls["n"] == 2:
            return {"ok": False, "output": "",
                    "error": "hermes -z 超时（300s）", "timeout": True,
                    "duration_s": 300.0}
        if calls["n"] == 3:
            return {"ok": True, "output": "不是 JSON",
                    "error": None, "duration_s": 2.0}
        return _ok_call(prompt)

    out = run_retro_batch(conn, _cands(conn), "manual", {},
                          tmp_path / "packs", call=flaky)
    assert (out["n_ok"], out["n_parse_fail"], out["n_timeout"],
            out["n_error"]) == (1, 1, 1, 0)
    statuses = {r["match_id"]: r["status"] for r in conn.execute(
        "SELECT match_id, status FROM retro_attributions")}
    assert statuses == {1: "ok", 2: "timeout", 3: "parse_fail"}
    # parse_fail 行契约字段 NULL、审计字段在
    r3 = conn.execute("SELECT * FROM retro_attributions WHERE match_id=3"
                      ).fetchone()
    assert r3["primary_tag"] is None and r3["duration_s"] == 2.0


def test_empty_batch_writes_ledger_only(conn, clock, tmp_path):
    out = run_retro_batch(conn, [], "manual", {}, tmp_path / "packs",
                          call=_ok_call)
    assert out["n_selected"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM retro_attributions"
                        ).fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM retro_runs"
                        ).fetchone()["c"] == 1


def test_timeout_classified_by_flag_not_message(conn, clock, tmp_path):
    """钉死：timeout 分类只认 runner 的结构化 timeout 键——error 文案改动
    （如去掉「超时」二字）不得让超时静默落成 error。"""
    def timed_out(prompt):
        return {"ok": False, "output": "", "error": "deadline exceeded",
                "timeout": True, "duration_s": 300.0}

    out = run_retro_batch(conn, _cands(conn)[:1], "manual", {},
                          tmp_path / "packs", call=timed_out)
    assert out["n_timeout"] == 1 and out["n_error"] == 0
    row = conn.execute("SELECT status FROM retro_attributions").fetchone()
    assert row["status"] == "timeout"
