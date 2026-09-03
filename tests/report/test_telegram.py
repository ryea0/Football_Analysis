"""send_telegram 测试——monkeypatch subprocess，全离线；真实 hermes 仅 --help 探测。

降级契约（计划 Task 8 / spec §7.1）：任何失败（超时/非零/找不到可执行）一律返回
False，绝不向调用方抛异常。
"""
import shutil
import subprocess

import pytest

from fa.report import telegram
from fa.report.telegram import send_telegram


def test_success_returns_true(monkeypatch):
    calls = {}

    def fake_run(argv, **kw):
        calls["argv"] = argv
        calls["input"] = kw.get("input")
        calls["timeout"] = kw.get("timeout")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(telegram.subprocess, "run", fake_run)
    assert send_telegram("比赛日报告\n候选 2 条") is True
    # 实测参数形态：hermes send --to telegram，正文走 stdin
    assert calls["argv"][:3] == ["hermes", "send", "--to"]
    assert "telegram" in calls["argv"]
    assert "--message" not in calls["argv"]      # --help 实测无此参数
    assert "比赛日报告" in calls["input"].decode("utf-8")
    assert calls["timeout"] == 60


def test_timeout_returns_false_not_raises(monkeypatch):
    def fake_run(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw.get("timeout", 60))

    monkeypatch.setattr(telegram.subprocess, "run", fake_run)
    assert send_telegram("x") is False


def test_nonzero_exit_returns_false(monkeypatch):
    monkeypatch.setattr(
        telegram.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "boom"))
    assert send_telegram("x") is False


def test_binary_missing_returns_false(monkeypatch):
    def fake_run(argv, **kw):
        raise FileNotFoundError("hermes")

    monkeypatch.setattr(telegram.subprocess, "run", fake_run)
    assert send_telegram("x") is False


def test_os_error_returns_false(monkeypatch):
    def fake_run(argv, **kw):
        raise OSError("exec format error")

    monkeypatch.setattr(telegram.subprocess, "run", fake_run)
    assert send_telegram("x") is False


def test_value_error_returns_false(monkeypatch):
    """空字节串等 ValueError 路径同样不外抛。"""
    def fake_run(argv, **kw):
        raise ValueError("embedded null byte")

    monkeypatch.setattr(telegram.subprocess, "run", fake_run)
    assert send_telegram("x") is False


def test_never_raises_on_unexpected_error(monkeypatch):
    """兜底：任意未知异常也降级为 False（报告失败不得中断管线）。"""
    def fake_run(argv, **kw):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(telegram.subprocess, "run", fake_run)
    assert send_telegram("x") is False


@pytest.mark.skipif(shutil.which("hermes") is None,
                    reason="本机未安装 hermes")
def test_real_hermes_send_interface_matches_probe():
    """真实探测（不真发消息）：`hermes send --help` 应确认 --to 存在且
    无 --message 参数——send_telegram 的参数形态以本机实测为准。"""
    r = subprocess.run(["hermes", "send", "--help"], capture_output=True,
                       text=True, timeout=30)
    assert r.returncode == 0
    assert "--to" in r.stdout
    assert "--message" not in r.stdout
