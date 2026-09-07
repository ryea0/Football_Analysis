"""B 线 · 运维健康：runs 历史 / 降级事件 / 队名隔离（设计 §3 页4）。

v2（2026-09-07）：顶部统一时间范围筛选器，替换原有单日 selectbox。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import streamlit as st

from components.time_filter import time_range_filter
from loaders import runs, unknown
from queries import COL_BILINGUAL
from time_utils import anchor_from_series, filter_by_date_range

st.header("B 线 · 运维健康 Ops Health")
try:
    df = runs()
    unk = unknown()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

# ── 时间范围筛选器 ──
anchor = anchor_from_series(df["started_at"]) if not df.empty else None
start_date, end_date, grain = time_range_filter(
    key="b4_ops",
    default_preset="30d",
    anchor_date=anchor,
)

# 过滤后的 runs
filtered = filter_by_date_range(df, "started_at", start_date, end_date) \
    if not df.empty else df

# —— 顶部健康概览卡片 ——
n_total = len(filtered) if not filtered.empty else 0
n_degraded = 0
n_success = 0
n_failed = 0
if not filtered.empty:
    n_degraded = int(filtered["degraded"].fillna(False).astype(bool).sum())
    n_success = int((filtered["status"] == "success").sum())
    n_failed = int((filtered["status"] == "failed").sum())

st.markdown("#### 🩺 健康概览 Health Overview")
c1, c2, c3, c4 = st.columns(4)
c1.metric("总 run 数 Total", n_total, help="当前时间范围内 run 记录数")
c2.metric("成功 Success", n_success,
          delta=f"{n_success/n_total:.0%}" if n_total else None,
          delta_color="normal")
c3.metric("失败 Failed", n_failed,
          delta=f"{n_failed/n_total:.0%}" if n_total else None,
          delta_color="inverse")
c4.metric("降级 Degraded", n_degraded,
          delta=f"{n_degraded/n_total:.0%}" if n_total else None,
          delta_color="inverse",
          help="spec §1.4 诚实降级——部分功能失败但流程继续")

if n_degraded:
    st.warning(
        f"⚠️ {n_degraded} 个 run 带降级标记——展开下方 **degraded_reasons** 核对"
        f"（spec §1.4 诚实降级）",
        icon="⚠️")
elif n_total > 0:
    st.success("✅ 当前范围内无降级 run — 全部 run 完整执行 / All runs completed")
else:
    st.info("当前范围内无 run 记录 / No runs in range")

st.divider()

# —— runs 历史 ——
st.markdown("#### 📋 runs 历史 Run History（summary 已展开）")
if filtered.empty:
    st.info("当前时间范围内无 run 记录 / No runs in range")
else:
    st.dataframe(
        filtered.rename(columns=COL_BILINGUAL),
        use_container_width=True,
        hide_index=True,
        height=min(560, max(260, len(filtered) * 36 + 45)),
    )
    st.caption(f"当前显示 {len(filtered)} 条 run · 按启动时间倒序 · 北京日口径")

st.divider()

# —— 队名隔离表 ——
st.markdown("#### 🏷️ 队名隔离表 Quarantined Names（unknown_names）")
if unk.empty:
    st.success("✅ 隔离表为空——所有实时盘队名均已对齐（spec §3.3）")
else:
    st.dataframe(
        unk.rename(columns=COL_BILINGUAL),
        use_container_width=True,
        hide_index=True,
        height=min(360, max(180, len(unk) * 36 + 40)),
    )
    st.info(
        "**处理方式**：`uv run fa data aliases` 逐条看建议，"
        "`fa data aliases --confirm \"TEAM_ID=别名\"` 确认写入（页面保持只读）",
        icon="ℹ️",
    )
    st.caption("隔离表为累计快照，不受上方时间范围影响。")
