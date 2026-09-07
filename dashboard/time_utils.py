"""时间维度工具函数：北京日/周/月转换、日期范围过滤、粒度聚合、累计重算。

纯函数模块，无 streamlit 依赖，可独立 pytest。
所有日期口径均为**北京日（UTC+8）**，与 spec §9.6 全项目一致。
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone

import pandas as pd

# 北京时区（固定 +8，无夏令时）
_BEIJING = timezone(timedelta(hours=8))

# ─────────────────────────────────────────────────────────────
# 预设档位：key → (显示标签, 向前天数 None=全部, 锁定粒度 None=auto)
# ─────────────────────────────────────────────────────────────
PRESETS: OrderedDict[str, dict] = OrderedDict([
    ("1d",    {"label": "今日",   "days": 1,    "grain": "D"}),
    ("7d",    {"label": "近 7 天", "days": 7,    "grain": "D"}),
    ("30d",   {"label": "近 30 天", "days": 30,   "grain": "W"}),
    ("90d",   {"label": "近 3 月",  "days": 90,   "grain": "W"}),
    ("180d",  {"label": "近 6 月",  "days": 180,  "grain": "M"}),
    ("365d",  {"label": "近 1 年",  "days": 365,  "grain": "M"}),
    ("all",   {"label": "全部",    "days": None, "grain": "M"}),
])

PRESET_KEYS = list(PRESETS.keys())


def auto_grain(range_days: int | None) -> str:
    """根据时间范围天数自动选择 x 轴聚合粒度。

    - ``range_days=None`` 表示全量 → 按月
    - ≤ 14 天 → 按日
    - ≤ 90 天 → 按周
    - 其余 → 按月
    """
    if range_days is None:
        return "M"
    if range_days <= 14:
        return "D"
    if range_days <= 90:
        return "W"
    return "M"


def preset_range(preset_key: str, anchor: date | None = None) -> tuple[date, date, str]:
    """根据预设档位 + 锚点日期，返回 ``(start_date, end_date, grain)``。

    参数
    ----
    preset_key: str
        ``PRESETS`` 的 key；``"custom"`` 非法，应由调用方自行处理。
    anchor: date | None
        锚点日期（通常是数据最新北京日）。None 则用今天北京日。

    返回
    ----
    (start_date, end_date, grain) — 两端包含。
    """
    if preset_key not in PRESETS:
        raise ValueError(f"unknown preset: {preset_key!r}; expected one of {PRESET_KEYS}")

    today = anchor or _today_bj()
    p = PRESETS[preset_key]
    days = p["days"]
    grain = p["grain"]

    if days is None:
        # 全部 → 给一个足够宽的范围（由调用方决定是否再用数据 min/max 收紧）
        return date(2000, 1, 1), date(2099, 12, 31), grain

    end = today
    start = end - timedelta(days=days - 1)  # 含两端，所以减 days-1
    return start, end, grain


def _today_bj() -> date:
    """当前北京日。"""
    return datetime.now(tz=_BEIJING).date()


# ─────────────────────────────────────────────────────────────
# 北京时区转换
# ─────────────────────────────────────────────────────────────

def to_bj_dates(series: pd.Series) -> pd.Series:
    """ISO 串列（UTC，带 Z 或裸）→ 北京日 ``date`` 对象序列。

    解析失败为 NaT → 输出为 ``NaT``（date 类列里是 pd.NaT）。
    """
    parsed = pd.to_datetime(series, errors="coerce", utc=True)
    return parsed.dt.tz_convert(_BEIJING).dt.date


def to_bj_period(series: pd.Series, grain: str) -> pd.Series:
    """ISO 串列 → 北京时区周期标签串。

    grain ∈ {"D", "W", "M"}:
        - D → ``"2026-09-07"``
        - W → ``"2026-W36"``（ISO 周，周一为一周起点）
        - M → ``"2026-09"``
    """
    parsed = pd.to_datetime(series, errors="coerce", utc=True)
    bj = parsed.dt.tz_convert(_BEIJING)
    if grain == "D":
        return bj.dt.strftime("%Y-%m-%d")
    if grain == "W":
        # ISO 周：%G = ISO 周年，%V = ISO 周号
        return bj.dt.strftime("%G-W%V")
    if grain == "M":
        return bj.dt.strftime("%Y-%m")
    raise ValueError(f"unknown grain: {grain!r}; expected 'D', 'W', or 'M'")


# ─────────────────────────────────────────────────────────────
# 日期范围过滤
# ─────────────────────────────────────────────────────────────

def filter_by_date_range(df: pd.DataFrame, time_col: str,
                         start: date, end: date) -> pd.DataFrame:
    """按 ``time_col`` 的**北京日**落在 [start, end] 区间过滤行。

    ``time_col`` 应为 UTC ISO 串列；两端包含。空 df 直接返回。
    """
    if df.empty or time_col not in df.columns:
        return df
    bj_date = to_bj_dates(df[time_col])
    mask = (bj_date >= start) & (bj_date <= end)
    return df[mask].copy()


# ─────────────────────────────────────────────────────────────
# 按粒度聚合
# ─────────────────────────────────────────────────────────────

def aggregate_by_grain(df: pd.DataFrame, time_col: str, grain: str,
                       agg_spec: dict[str, tuple[str, str]],
                       group_cols: list[str] | None = None) -> pd.DataFrame:
    """通用周期聚合：按 ``time_col`` 的北京周期 + ``group_cols`` 分组。

    参数
    ----
    df: DataFrame
        原始数据。
    time_col: str
        时间列名（UTC ISO 串）。
    grain: str
        ``"D"`` / ``"W"`` / ``"M"``。
    agg_spec: dict[str, tuple[str, str]]
        ``{输出列名: (聚合函数名, 源列名)}``。聚合函数名是 pandas 可识别的
        字符串，如 ``"sum"``、``"mean"``、``"median"``、``"count"``、``"first"``。
    group_cols: list[str] | None
        额外的分组列（如 ``["strategy", "market"]``）。

    返回
    ----
    DataFrame，列 = ``["period", *group_cols, *agg_spec.keys()]``，
    按 period 升序、group_cols 依次排序。
    """
    if df.empty or time_col not in df.columns:
        cols = ["period"] + (group_cols or []) + list(agg_spec.keys())
        return pd.DataFrame(columns=cols)

    work = df.copy()
    work["period"] = to_bj_period(work[time_col], grain)

    group_keys = ["period"] + (group_cols or [])
    # 用 pd.NamedAgg 构造（pandas 2.x 推荐写法，避免嵌套 dict）
    named_aggs = {
        out_col: pd.NamedAgg(column=src_col, aggfunc=func_name)
        for out_col, (func_name, src_col) in agg_spec.items()
    }
    grouped = work.groupby(group_keys, dropna=False).agg(**named_aggs).reset_index()

    # 排序：period 按自然时序（字符串排序对 D/M 格式也成立；W 的 %G-W%V 也能正确排序）
    grouped = grouped.sort_values(group_keys).reset_index(drop=True)
    return grouped


# ─────────────────────────────────────────────────────────────
# 累计曲线截断重算
# ─────────────────────────────────────────────────────────────

def recalc_cumulative(df: pd.DataFrame, time_col: str, value_col: str,
                      start: date, end: date,
                      group_col: str | None = None,
                      cum_col_name: str = "cum_value") -> pd.DataFrame:
    """事件驱动累计曲线的时间范围截断重算。

    1. 按 [start, end] 北京日过滤；
    2. 按时间升序排序；
    3. 在范围内重新 cumsum（窗口起点从 0 开始）。

    参数
    ----
    df: DataFrame
        原始数据，每行一个事件（如一个已结算注），含时间列 + 增量值列。
    time_col: str
        时间列名（UTC ISO 串）。
    value_col: str
        增量值列名（如单笔 P&L）。
    start, end: date
        北京日范围（两端包含）。
    group_col: str | None
        分组列（如 ``"strategy"``），每组独立累计。
    cum_col_name: str
        输出的累计列名。

    返回
    ----
    DataFrame，包含原始列 + ``cum_col_name`` 列，按 ``time_col`` 升序。
    """
    filtered = filter_by_date_range(df, time_col, start, end)
    if filtered.empty:
        result = filtered.copy()
        result[cum_col_name] = pd.Series(dtype=float)
        return result

    filtered = filtered.sort_values(time_col).reset_index(drop=True)

    if group_col:
        filtered[cum_col_name] = filtered.groupby(group_col)[value_col].cumsum()
    else:
        filtered[cum_col_name] = filtered[value_col].cumsum()

    return filtered


# ─────────────────────────────────────────────────────────────
# 辅助：从数据列推导锚点日期
# ─────────────────────────────────────────────────────────────

def anchor_from_series(series: pd.Series) -> date | None:
    """从一个 ISO 时间列推导锚点日期（数据中最新北京日）。

    空列或全 NaT → 返回 None。
    """
    if series.empty:
        return None
    bj_dates = to_bj_dates(series).dropna()
    if bj_dates.empty:
        return None
    return bj_dates.max()
