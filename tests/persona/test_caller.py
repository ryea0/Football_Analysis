"""caller 测试（C1）：HERMES_BIN 指向 fixture 脚本——真子进程、真超时、真 exit code。"""
from pathlib import Path

import pytest

from fa.config import PERSONA_TIMEOUT_S, hermes_bin
from fa.persona.caller import (
    HermesCallError,
    _resolve_timeout,
    build_command,
    call_hermes,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fix() -> Path:
    return FIXTURES


def test_build_command_shape(monkeypatch):
    """探针 T2 钉死形态：``[bin, -z, prompt, -t, search]``——-z 紧跟 prompt（argparse
    约束），尾部 ``-t search`` 是 -z 无条件 YOLO 下的唯一真实围栏。"""
    monkeypatch.setenv("HERMES_BIN", "/fake/hermes")
    assert build_command("你好") == ["/fake/hermes", "-z", "你好", "-t", "search"]
    monkeypatch.delenv("HERMES_BIN", raising=False)
    assert build_command("x") == ["hermes", "-z", "x", "-t", "search"]  # 默认走 PATH


def test_call_hermes_bin_env_injected(monkeypatch, fix):
    """hermes_bin 可注入（env）——C1 的同一代码路径前提。"""
    monkeypatch.setenv("HERMES_BIN", str(fix / "hermes_ok"))
    assert hermes_bin() == str(fix / "hermes_ok")


def test_call_ok(monkeypatch, fix):
    monkeypatch.setenv("HERMES_BIN", str(fix / "hermes_ok"))
    out = call_hermes("prompt")
    assert '"verdict"' in out


def test_call_bad_json_is_caller_ok(monkeypatch, fix):
    """caller 不做 JSON 校验：围栏噪声照常返回 stdout（提取/校验交下游层）。"""
    monkeypatch.setenv("HERMES_BIN", str(fix / "hermes_bad_json"))
    out = call_hermes("prompt")
    assert "不适合 JSON" in out


def test_call_timeout(monkeypatch, fix):
    monkeypatch.setenv("HERMES_BIN", str(fix / "hermes_timeout"))
    monkeypatch.setenv("FA_PERSONA_TIMEOUT", "0.5")
    with pytest.raises(HermesCallError) as ei:
        call_hermes("prompt")
    assert ei.value.reason == "timeout"


def test_call_nonzero_exit(monkeypatch, fix):
    monkeypatch.setenv("HERMES_BIN", str(fix / "hermes_nonzero"))
    with pytest.raises(HermesCallError) as ei:
        call_hermes("prompt")
    assert ei.value.reason == "exit"
    assert "3" in str(ei.value)            # 退出码可溯源
    assert "no credits" in str(ei.value)   # stderr 尾行可溯源


def test_timeout_env_inf_nan_nonpositive_fall_back(monkeypatch):
    """T3 遗留 Minor：env 为 inf/nan/非正时回退默认，防静默禁用超时。"""
    for bad in ("inf", "-inf", "nan", "0", "-5", "1e400"):
        monkeypatch.setenv("FA_PERSONA_TIMEOUT", bad)
        assert _resolve_timeout(None) == PERSONA_TIMEOUT_S, bad
    monkeypatch.setenv("FA_PERSONA_TIMEOUT", "7.5")
    assert _resolve_timeout(None) == 7.5   # 合法值照常透传


def test_timeout_explicit_overrides_env(monkeypatch):
    monkeypatch.setenv("FA_PERSONA_TIMEOUT", "inf")
    assert _resolve_timeout(2.0) == 2.0    # 显式参数优先于 env
    assert _resolve_timeout(float("inf")) == PERSONA_TIMEOUT_S
    monkeypatch.delenv("FA_PERSONA_TIMEOUT")
    assert _resolve_timeout(None) == PERSONA_TIMEOUT_S
