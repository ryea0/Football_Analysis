"""窗口计算：与 C 线完全同节奏（EVOLUTION_EPOCH + 42 天/窗）。

C' 线是 C 线的对照实验，窗口节奏必须一致才能公平比较。直接复用
fa.evolve.windows 的全部函数与常量，不重复实现。
"""
from __future__ import annotations

from fa.evolve.windows import (  # noqa: F401  re-export
    Window, beijing_today, window_bounds, window_of, due_windows,
    current_window_idx,
)
