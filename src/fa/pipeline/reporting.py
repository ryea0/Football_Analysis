"""报告渲染 / 推送接缝（T9 的内部最小实现，主线单源）。

T8（``fa.report.render`` / ``fa.report.telegram``）落在**并行分支**上、尚未合流。
本模块把「渲染」「推送」各收敛成一个**惰性**缝：运行时 import 得到 T8 就原样
透传（形参顺序与 T8 签名一一对应），import 不到就回占位串 / ``False``。于是
无 T8 的主线与合流后走的是**同一条调用路径**，T9/T10 的编排代码零改动。

合流后的 follow-up：摘掉 :func:`_load_render` / :func:`_load_telegram` 的降级分支，
改成模块级 ``from fa.report.render import ...``（函数签名不变）。
"""

import importlib

_UNMERGED = "fa.report 未合流（T8）：报告正文省略"


def _load_render(name: str):
    """惰性取 ``fa.report.render.<name>``；T8 未合流 → ``None``（调用方走占位）。"""
    try:
        return getattr(importlib.import_module("fa.report.render"), name)
    except ImportError:
        return None


def _load_telegram():
    """惰性取 ``fa.report.telegram`` 模块；T8 未合流 → ``None``（推送不可用）。"""
    try:
        return importlib.import_module("fa.report.telegram")
    except ImportError:
        return None


def render_matchday_report(conn, run_id, phase, summary, quota_left, degraded) -> str:
    fn = _load_render("render_matchday_report")
    if fn is None:
        return f"[{_UNMERGED}（run_id={run_id} phase={phase}）]"
    return fn(conn, run_id, phase, summary, quota_left, degraded)


def render_pm_update(conn, am_run_id, pm_run_id, quota_left, degraded) -> str:
    fn = _load_render("render_pm_update")
    if fn is None:
        return f"[{_UNMERGED}（am_run_id={am_run_id} pm_run_id={pm_run_id}）]"
    return fn(conn, am_run_id, pm_run_id, quota_left, degraded)


def render_settlement_brief(settle) -> str:
    fn = _load_render("render_settlement_brief")
    return f"[{_UNMERGED}（结算简报）]" if fn is None else fn(settle)


def send(text: str) -> bool:
    """推送 Telegram；T8 未合流 → ``False``（不发）。真实现自身不抛、只回 bool。"""
    telegram = _load_telegram()
    return False if telegram is None else telegram.send_telegram(text)


def last_error() -> str | None:
    """最近一次推送失败原因；T8 未合流 → 固定说明串（降级同样可溯源）。"""
    telegram = _load_telegram()
    return _UNMERGED if telegram is None else telegram.LAST_TELEGRAM_ERROR
