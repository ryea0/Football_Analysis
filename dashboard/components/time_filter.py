"""时间范围筛选器组件。

一行式 UI：预设档位按钮（今日/近 7 天/近 30 天/近 3 月/近 6 月/近 1 年/全部）
+ 右侧自定义日期起/止 picker。遵循 dataviz interaction 规范：
过滤器一行左对齐、置于内容上方、scope 所有下方图表。

用法::

    from dashboard.components.time_filter import time_range_filter

    start, end, grain = time_range_filter(
        key="b2_recommendations",
        default_preset="30d",
        anchor_date=latest_date,
    )
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from time_utils import PRESETS, PRESET_KEYS, auto_grain, preset_range

_BEIJING = timezone(timedelta(hours=8))


def time_range_filter(
    key: str,
    default_preset: str = "30d",
    anchor_date: date | None = None,
    show_grain_hint: bool = True,
) -> tuple[date, date, str]:
    """渲染时间范围筛选器，返回 ``(start_date, end_date, grain)``。

    参数
    ----
    key: str
        session_state 命名空间前缀，每页不同，避免冲突。
    default_preset: str
        默认预设档位 key，见 ``PRESETS``。
    anchor_date: date | None
        锚点日期（通常是数据最新北京日）。None 则用今天北京日。
    show_grain_hint: bool
        是否在筛选器下方显示当前范围 + 粒度的 caption。

    返回
    ----
    (start_date, end_date, grain) — 两端包含，grain ∈ {"D", "W", "M"}。
    """
    if default_preset not in PRESETS:
        raise ValueError(f"default_preset {default_preset!r} not in PRESETS")

    # session_state 状态初始化
    state_key = f"_tf_{key}"
    if state_key not in st.session_state:
        st.session_state[state_key] = {
            "preset": default_preset,
            "custom_start": None,
            "custom_end": None,
        }
    state = st.session_state[state_key]

    today = anchor_date or _today_bj()

    # ── 布局：预设档位按钮（7 个）+ 自定义起/止（2 个 date_input）
    n_presets = len(PRESETS)
    # 7 个预设 + 1 个"自定义"标签 + 2 个 date picker = 10 列
    # 用比例分配：预设各 1fr、自定义标签 0.6fr、日期各 1.2fr
    cols = st.columns([1] * n_presets + [0.4, 1.3, 1.3], gap="small")

    # 预设档位按钮
    for i, preset_key in enumerate(PRESET_KEYS):
        label = PRESETS[preset_key]["label"]
        is_active = state["preset"] == preset_key
        if cols[i].button(
            label,
            key=f"{key}_btn_{preset_key}",
            use_container_width=True,
            type="primary" if is_active else "secondary",
        ):
            state["preset"] = preset_key
            state["custom_start"] = None
            state["custom_end"] = None
            st.rerun()

    # 自定义范围 picker
    # 计算当前显示的 start/end（用于 date_input 默认值）
    cur_start, cur_end, _ = _resolve_range(state, today)

    with cols[n_presets + 1]:
        custom_start = st.date_input(
            "起",
            value=cur_start,
            key=f"{key}_custom_start",
            label_visibility="collapsed",
        )
    with cols[n_presets + 2]:
        custom_end = st.date_input(
            "止",
            value=cur_end,
            key=f"{key}_custom_end",
            label_visibility="collapsed",
        )

    # 检测到用户手动改了自定义日期 → 切到 custom 模式
    # （比较 date 对象，注意 date_input 返回的是 datetime.date）
    if (custom_start != cur_start) or (custom_end != cur_end):
        state["preset"] = "custom"
        state["custom_start"] = custom_start
        state["custom_end"] = custom_end

    # 重新解析（可能因上面的切换而变）
    start, end, grain = _resolve_range(state, today)

    # 保证 start <= end
    if start > end:
        start, end = end, start
        state["custom_start"] = start
        state["custom_end"] = end

    if show_grain_hint:
        grain_label = {"D": "日", "W": "周", "M": "月"}[grain]
        preset_label = "自定义" if state["preset"] == "custom" else PRESETS[state["preset"]]["label"]
        st.caption(
            f"时间范围：{start.isoformat()} ~ {end.isoformat()} · "
            f"聚合粒度：{grain_label} · （{preset_label}，锚点 {today.isoformat()}）"
        )

    return start, end, grain


def _resolve_range(state: dict, today: date) -> tuple[date, date, str]:
    """根据当前 state 解析出 (start, end, grain)。"""
    preset = state["preset"]
    if preset == "custom":
        cs = state.get("custom_start")
        ce = state.get("custom_end")
        start = cs if cs else today
        end = ce if ce else today
        range_days = (end - start).days + 1
        grain = auto_grain(range_days)
        return start, end, grain

    # 预设档位
    return preset_range(preset, today)


def _today_bj() -> date:
    """当前北京日。"""
    return datetime.now(tz=_BEIJING).date()
