"""窗口计算（设计档 §1）：锚点 EVOLUTION_EPOCH、42 天/窗连续切分。

口径统一为北京时间日期（cron 调度时区 Asia/Shanghai）。窗口 W_idx 覆盖
[opened, closes)——closes 当日 00:00 起属下一窗；早于锚点的日期 clamp 到
1 号窗（M6 上线前不存在更早窗口，收口判定不得因负序号炸掉）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fa.config import EVOLUTION_EPOCH, EVOLUTION_WINDOW_DAYS
from fa.evolve import EvolutionError


@dataclass(frozen=True)
class Window:
    idx: int
    opened: date
    closes: date


def beijing_today() -> date:
    """北京时间今天（唯一时间缝，测试 monkeypatch 本函数）。"""
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def window_bounds(idx: int) -> Window:
    if idx < 1:
        raise EvolutionError(f"窗口序号从 1 起，收到 {idx!r}")
    opened = EVOLUTION_EPOCH + timedelta(days=EVOLUTION_WINDOW_DAYS * (idx - 1))
    return Window(idx, opened, opened + timedelta(days=EVOLUTION_WINDOW_DAYS))


def window_of(d: date) -> Window:
    """d 所在窗口；d 早于锚点 → 1 号窗（clamp，见模块 docstring）。"""
    idx = max(1, (d - EVOLUTION_EPOCH).days // EVOLUTION_WINDOW_DAYS + 1)
    return window_bounds(idx)


def due_windows(today: date) -> list[Window]:
    """已收口（today >= closes）的窗口，升序——tick 的反思候选。"""
    m = window_of(today).idx
    return [window_bounds(i) for i in range(1, m)]


def current_window_idx() -> int:
    """当前窗序号（快照/判决语境用）。"""
    return window_of(beijing_today()).idx
