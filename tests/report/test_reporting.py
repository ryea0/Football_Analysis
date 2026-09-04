"""reporting 门面推送重试测试（M4 §7-3：瞬态失败重试 1 次的廉价吸收）。

重试落在 :func:`fa.pipeline.reporting.send` 门面（telegram 层零改动——
subprocess 细节与既有 10 例降级测试原样）。缝沿门面既有约定
``reporting._telegram``（模块位替身）。契约：失败 → 等待 → 重试
**恰好一次**；二次成功 → True；二次皆败 → False（走既有降级，
``last_error`` 反映**第二次**的失败原因）。
"""
from types import SimpleNamespace

import pytest

from fa.pipeline import reporting
from fa.pipeline.reporting import send


@pytest.fixture
def no_sleep(monkeypatch):
    """重试间隔打掉（真实 1.5s 只属于生产路径，测试不吃墙钟）。"""
    monkeypatch.setattr(reporting, "_RETRY_DELAY_S", 0)


def _wire(monkeypatch, behavior):
    calls = []

    def flaky(text):
        calls.append(text)
        return behavior(calls)

    monkeypatch.setattr(reporting, "_telegram",
                        SimpleNamespace(send_telegram=flaky))
    return calls


def test_transient_failure_recovers_on_retry(monkeypatch, no_sleep):
    """第一次失败、第二次成功 → True 且恰好调了 2 次。"""
    calls = _wire(monkeypatch, lambda c: len(c) > 1)    # 首败次成
    assert send("瞬态吸收") is True
    assert calls == ["瞬态吸收", "瞬态吸收"]


def test_persistent_failure_returns_false_after_exactly_one_retry(monkeypatch, no_sleep):
    calls = _wire(monkeypatch, lambda c: False)
    assert send("x") is False
    assert len(calls) == 2                          # 恰好 2 次：不无限重试


def test_success_first_try_does_not_retry(monkeypatch, no_sleep):
    calls = _wire(monkeypatch, lambda c: True)
    assert send("一发即中") is True
    assert len(calls) == 1                          # 成功不重试
