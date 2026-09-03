"""runner 测试——monkeypatch subprocess 全离线；真实 hermes 只 --help 探测。"""
import shutil
import subprocess

import pytest

from fa.retro import runner
from fa.retro.runner import build_prompt, run_headless

PACK = {"match": {"home": "Arsenal", "away": "West Ham",
                  "date": "2024-04-20"},
        "prediction": {"p_home": 0.45}, "outcome": {"result": "H"},
        "divergence": {"div": 0.14}}


def test_build_prompt_contains_role_contract_and_pack():
    p = build_prompt(PACK)
    assert "复盘分析师" in p
    assert "Arsenal" in p and '"p_home": 0.45' in p
    for tag in ("injury", "variance", "market_info"):
        assert tag in p                          # 枚举进 prompt
    assert "只输出" in p and "JSON" in p
    assert "不得" in p and ("早于" in p or "早于比赛日" in p)  # 证据日期规则
    assert "反事实" in p                          # 禁 counterfactual


def test_ok_path(monkeypatch):
    calls = {}

    def fake_run(argv, **kw):
        calls["argv"], calls["timeout"] = argv, kw.get("timeout")
        # hermes -z 结果走 stdout（hermes_cli/oneshot.py 实测：final text
        # to stdout）；brief 草稿把载荷误放 stderr 位，此处修正参数位。
        return subprocess.CompletedProcess(argv, 0, '{"x": 1}', "")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    out = run_headless("prompt")
    assert out["ok"] is True and out["error"] is None
    assert out["output"] == '{"x": 1}' and out["duration_s"] >= 0
    assert calls["argv"][:2] == ["hermes", "-z"]      # -z 吃 prompt 参数
    assert calls["argv"][2] == "prompt"
    assert calls["timeout"] == 300


def test_timeout_not_raises(monkeypatch):
    def fake_run(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw.get("timeout"))

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    out = run_headless("p")
    assert out["ok"] is False and "300" in out["error"] and out["output"] == ""


def test_usage_error_exit_2(monkeypatch):
    monkeypatch.setattr(
        runner.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 2, "", "usage"))
    out = run_headless("p")
    assert out["ok"] is False and "exit 2" in out["error"]


def test_binary_missing(monkeypatch):
    def fake_run(argv, **kw):
        raise FileNotFoundError("hermes")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    out = run_headless("p")
    assert out["ok"] is False and "hermes" in out["error"]


def test_never_raises(monkeypatch):
    monkeypatch.setattr(
        runner.subprocess, "run",
        lambda argv, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    out = run_headless("p")
    assert out["ok"] is False and "RuntimeError" in out["error"]


@pytest.mark.skipif(shutil.which("hermes") is None,
                    reason="本机未安装 hermes")
def test_real_hermes_oneshot_flag_exists():
    """真实探测（不真跑 agent）：-z 存在于 --help（2026-09-04 已实测，
    此处固化防版本漂移）。"""
    r = subprocess.run(["hermes", "--help"], capture_output=True, text=True,
                       timeout=30)
    assert r.returncode == 0
    assert "-z PROMPT" in r.stdout
