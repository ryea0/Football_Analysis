"""send_telegram 测试——monkeypatch subprocess，全离线；真实 hermes 仅 --help 探测。

降级契约（计划 Task 8 / spec §7.1）：任何失败（超时/非零/找不到可执行）一律返回
False，绝不向调用方抛异常。
"""
import shutil
import subprocess

import pytest

from fa.report import telegram
from fa.report.telegram import send_telegram


@pytest.fixture(autouse=True)
def _reset_last_error():
    """LAST_TELEGRAM_ERROR 是模块级状态——逐例清零，避免跨用例泄漏。"""
    telegram.LAST_TELEGRAM_ERROR = None
    yield
    telegram.LAST_TELEGRAM_ERROR = None


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
    assert telegram.LAST_TELEGRAM_ERROR is None  # 成功即清空


def test_success_clears_stale_error(monkeypatch):
    telegram.LAST_TELEGRAM_ERROR = "上一次的陈旧失败"
    monkeypatch.setattr(
        telegram.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", ""))
    assert send_telegram("x") is True
    assert telegram.LAST_TELEGRAM_ERROR is None


def test_timeout_returns_false_not_raises(monkeypatch):
    def fake_run(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw.get("timeout", 60))

    monkeypatch.setattr(telegram.subprocess, "run", fake_run)
    assert send_telegram("x") is False
    assert telegram.LAST_TELEGRAM_ERROR is not None
    assert "60" in telegram.LAST_TELEGRAM_ERROR   # 记录超时时长


def test_nonzero_exit_records_reason_and_stderr_tail(monkeypatch):
    # 头部 HEADMARK + 400 填充 + 尾部 TAILMARK：截尾后头部必须被挤出、尾部保留
    stderr = "HEADMARK" + "A" * 400 + "TAILMARK telegram 401"
    monkeypatch.setattr(
        telegram.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(
            argv, 1, "", stderr))
    assert send_telegram("x") is False
    err = telegram.LAST_TELEGRAM_ERROR
    assert err is not None
    assert "exit 1" in err
    assert "TAILMARK telegram 401" in err         # stderr 尾部保留
    assert "HEADMARK" not in err                  # 头部被截掉
    assert len(err) <= len("exit 1: ") + 200      # 只留尾部 200 字符


def test_nonzero_exit_with_empty_stderr_still_records_code(monkeypatch):
    monkeypatch.setattr(
        telegram.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 2, "", ""))
    assert send_telegram("x") is False
    assert telegram.LAST_TELEGRAM_ERROR == "exit 2: "


def test_nonzero_exit_with_bytes_stderr(monkeypatch):
    """真实 subprocess（capture_output、无 text）给 bytes stderr——不得炸。"""
    monkeypatch.setattr(
        telegram.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(
            argv, 1, b"", b"backend down"))
    assert send_telegram("x") is False
    assert "backend down" in telegram.LAST_TELEGRAM_ERROR


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
    assert "RuntimeError" in telegram.LAST_TELEGRAM_ERROR


def test_non_string_text_raises_rather_than_silent_false():
    """编程期误用（非 str）必须外抛，不得被吞成「推送失败」而掩盖 bug。"""
    with pytest.raises((AttributeError, TypeError)):
        send_telegram(None)          # type: ignore[arg-type]


def test_binary_missing_records_reason(monkeypatch):
    def fake_run(argv, **kw):
        raise FileNotFoundError("hermes")

    monkeypatch.setattr(telegram.subprocess, "run", fake_run)
    assert send_telegram("x") is False
    assert "hermes" in telegram.LAST_TELEGRAM_ERROR


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
