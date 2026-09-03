"""B 线 · 运维健康：runs 历史 / 降级事件 / 队名隔离（设计 §3 页4）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from loaders import runs, unknown

st.header("B 线 · 运维健康")
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
               "（spec §1.4 诚实降级）")
else:
    st.success("无降级 run")

st.subheader("runs 历史（summary 已展开）")
st.dataframe(df, use_container_width=True, hide_index=True)

st.subheader("队名隔离表（unknown_names）")
if unk.empty:
    st.success("隔离表为空——所有实时盘队名均已对齐（spec §3.3）")
else:
    st.dataframe(unk, use_container_width=True, hide_index=True)
    st.caption("处理方式：uv run fa data aliases 逐条看建议，"
               "fa data aliases --confirm \"TEAM_ID=别名\" 确认写入（页面保持只读）")
