"""B 线 · 运维健康：runs 历史 / 降级事件 / 队名隔离（设计 §3 页4）。

中英并列标签 + runs 按日过滤（2026-09-04 增补）——「看过去某一天的跑批记录」。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import streamlit as st

from loaders import runs, unknown
from queries import COL_BILINGUAL, by_date, date_choices

st.header("B 线 · 运维健康 Ops Health")
try:
    df = runs()
    unk = unknown()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

# —— 顶部健康概览卡片 ——
n_total = len(df) if not df.empty else 0
n_degraded = 0
n_success = 0
if not df.empty:
    n_degraded = int(df["degraded"].fillna(False).astype(bool).sum())
    n_success = int((df["status"] == "success").sum())

st.markdown("#### 🩺 健康概览 Health Overview")
c1, c2, c3, c4 = st.columns(4)
c1.metric("总 run 数 Total", n_total, help="历史全部 run 记录")
c2.metric("成功 Success", n_success,
          delta=f"{n_success/n_total:.0%}" if n_total else None,
          delta_color="normal")
c3.metric("降级 Degraded", n_degraded,
          delta=f"{n_degraded/n_total:.0%}" if n_total else None,
          delta_color="inverse",
          help="spec §1.4 诚实降级——部分功能失败但流程继续")
if unk.empty:
    c4.metric("队名隔离表", "清空 ✓", delta_color="normal",
              help="所有实时盘队名均已对齐（spec §3.3）")
else:
    c4.metric("队名隔离表", f"{len(unk)} 条", delta_color="inverse",
              help="未对齐的队名——需人工确认别名")

if n_degraded:
    st.warning(
        f"⚠️ {n_degraded} 个 run 带降级标记——展开下方 **degraded_reasons** 核对"
        f"（spec §1.4 诚实降级）",
        icon="⚠️")
else:
    if n_total > 0:
        st.success("✅ 无降级 run — 全部 run 完整执行 / All runs completed")

st.divider()

# —— runs 历史 ——
st.markdown("#### 📋 runs 历史 Run History（summary 已展开）")
if df.empty:
    st.info("暂无 run 记录 / No runs yet")
else:
    day = st.selectbox("日期（启动日）Date (started)",
                       ["全部 All", *date_choices(df, "started_at")])
    view = by_date(df, "started_at", day)
    st.dataframe(
        view.rename(columns=COL_BILINGUAL),
        use_container_width=True,
        hide_index=True,
        height=min(560, max(260, len(view) * 36 + 45)),
    )
    st.caption(f"当前显示 {len(view)} 条 run · 按启动时间倒序")

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
