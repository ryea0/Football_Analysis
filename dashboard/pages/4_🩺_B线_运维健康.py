"""B 线 · 运维健康：runs 历史 / 降级事件 / 队名隔离（设计 §3 页4）。

中英并列标签 + runs 按日过滤（2026-09-04 增补）——「看过去某一天的跑批记录」。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from loaders import runs, unknown
from queries import by_date, date_choices

st.header("B 线 · 运维健康 Ops Health")
try:
    df = runs()
    unk = unknown()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

n_degraded = 0
if not df.empty:
    n_degraded = int(df["degraded"].fillna(False).astype(bool).sum())
if n_degraded:
    st.warning(f"{n_degraded} 个 run 带降级标记——展开下方 degraded_reasons 核对"
               f"（spec §1.4 诚实降级）/ {n_degraded} run(s) degraded — see "
               "degraded_reasons below")
else:
    st.success("无降级 run / No degraded runs")

st.subheader("runs 历史（summary 已展开）Run History")
if df.empty:
    st.caption("暂无 run 记录 / No runs yet")
else:
    day = st.selectbox("日期（启动日）Date (started)",
                       ["全部 All", *date_choices(df, "started_at")])
    st.dataframe(by_date(df, "started_at", day), use_container_width=True,
                 hide_index=True)

st.subheader("队名隔离表（unknown_names）Quarantined Names")
if unk.empty:
    st.success("隔离表为空——所有实时盘队名均已对齐（spec §3.3）"
               " / Quarantine empty — all names aligned")
else:
    st.dataframe(unk, use_container_width=True, hide_index=True)
    st.caption("处理方式：uv run fa data aliases 逐条看建议，"
               "fa data aliases --confirm \"TEAM_ID=别名\" 确认写入（页面保持只读）"
               " / resolve via `fa data aliases --confirm \"TEAM_ID=alias\"`")
