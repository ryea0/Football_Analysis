"""报告渲染 / 推送门面（T9 的稳定调用面，单源指向 T8 实现）。

T8（``fa.report.render`` / ``fa.report.telegram``）曾落在**并行分支**上，本模块
当时用惰性缝 + ``ImportError`` 降级兜底，让无 T8 的主线与合流后走同一条调用路径。
T8 已合流，**降级分支已摘除**：这里只剩模块级直引 + 透传（形参顺序与 T8 签名
一一对应），``fa.pipeline.matchday`` / ``fa.pipeline.daily`` / ``fa.cli`` 的调用面
与测试 monkeypatch 点（``reporting._render`` / ``reporting._telegram``）不变。
"""

import time

from fa.report import render as _render
from fa.report import telegram as _telegram


def render_matchday_report(conn, run_id, phase, summary, quota_left, degraded) -> str:
    return _render.render_matchday_report(
        conn, run_id, phase, summary, quota_left, degraded)


def render_pm_update(conn, am_run_id, pm_run_id, quota_left, degraded) -> str:
    return _render.render_pm_update(
        conn, am_run_id, pm_run_id, quota_left, degraded)


def render_settlement_brief(settle) -> str:
    return _render.render_settlement_brief(settle)


# 推送重试（M4 §7-3「瞬态失败的廉价吸收」）：失败等 _RETRY_DELAY_S 秒重试
# **恰好一次**——hermes/TG 的瞬态抖动（M4 E2E 实测遇过 1 次）就地吸收；
# 二次仍败走既有降级（False + LAST_TELEGRAM_ERROR 记第二次原因）。
# telegram 层保持零改动（subprocess 细节与降级测试原样），重试收在门面。
_RETRY_DELAY_S = 1.5


def send(text: str) -> bool:
    """推送 Telegram（失败重试恰好一次）。真实现自身不抛、只回 bool。"""
    if _telegram.send_telegram(text):
        return True
    time.sleep(_RETRY_DELAY_S)
    return _telegram.send_telegram(text)


def last_error() -> str | None:
    """最近一次推送失败原因（重试成功后为 ``None``，可溯源进 runs.summary）。"""
    return _telegram.LAST_TELEGRAM_ERROR
