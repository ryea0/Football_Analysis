"""dsh 调用器：与 telegram.py 同构的降级契约——只回 (None, 原因)，不抛。"""
import subprocess
from pathlib import Path

from fa.agentline import runner
from fa.agentline.runner import build_prompt, run_headless


def test_run_headless_ok(monkeypatch):
    def fake_run(cmd, **kw):
        assert cmd[0] == "dsh" and "fa-agent-base" in " ".join(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout='{"p_home": 0.5}',
                                           stderr="")
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    out, err, dur = run_headless("prompt", "fa-agent-base")
    assert out == '{"p_home": 0.5}' and err is None and dur >= 0


def test_run_headless_timeout(monkeypatch):
    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 300)
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    out, err, dur = run_headless("p", "fa-agent-base")
    assert out is None and "超时" in err


def test_run_headless_missing_binary(monkeypatch):
    def fake_run(cmd, **kw):
        raise FileNotFoundError("dsh")
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    out, err, _ = run_headless("p", "fa-agent-base")
    assert out is None and "不存在" in err


def test_run_headless_nonzero_exit(monkeypatch):
    fake = subprocess.CompletedProcess(["dsh"], 1, stdout="", stderr="boom")
    monkeypatch.setattr(runner.subprocess, "run", lambda cmd, **kw: fake)
    out, err, _ = run_headless("p", "fa-agent-base")
    assert out is None and "boom" in err


def test_run_headless_runs_in_isolated_scratch_cwd(monkeypatch):
    """agent 自带文件工具：子进程必须落在一次性 scratch 目录，绝不继承仓库 cwd
    （首批 E2E 实证：不隔离时 prediction.json 落进了项目根）。"""
    seen = {}

    def fake_run(cmd, **kw):
        assert "cwd" in kw, "必须显式传 cwd（否则继承 fa 仓库根）"
        cwd = Path(kw["cwd"])
        seen["cwd"] = cwd
        assert cwd.is_dir() and cwd.name.startswith("fa-agentline-")
        return subprocess.CompletedProcess(cmd, 0, stdout='{"p_home": 0.5}',
                                           stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    out, err, _ = run_headless("prompt", "fa-agent-base")
    assert out == '{"p_home": 0.5}' and err is None
    assert not seen["cwd"].exists()               # 用完即清，不留散目录


def test_run_headless_cwd_isolated_even_on_timeout(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cwd"] = Path(kw["cwd"])
        raise subprocess.TimeoutExpired(cmd, 600)
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    out, err, _ = run_headless("p", "fa-agent-enh")
    assert out is None and "超时" in err
    assert not seen["cwd"].exists()               # 降级路径同样清理 scratch


def test_timeout_cap_is_600s():
    # A_enh 单场实测 198.4s（检索还没真正触发）已占旧上限 300s 的 2/3——
    # 上限提到 600s 留检索触发后的余量（M8），这里钉住防止回退。
    assert runner._TIMEOUT_S == 600


def test_build_prompt_contract():
    import json
    base = build_prompt({"match": {"home": "Arsenal", "date": "2024-02-01"}},
                        "A_base")
    enh = build_prompt({"match": {"home": "Arsenal", "date": "2024-02-01"}},
                       "A_enh")
    assert "Arsenal" in base and "JSON" in base
    assert "严禁使用任何比赛开始后产生的信息" in enh
    assert "严禁使用任何比赛开始后产生的信息" not in base   # 基线层无检索段
    assert "before:2024-02-01" in enh
