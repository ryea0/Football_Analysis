"""A 线 · 校准曲线：十分位分组，分市场（设计 §3 页6）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from loaders import calibration, overview

st.header("A 线 · 校准曲线（十分位）Calibration")
try:
    full = overview()
    cal = calibration()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()
if full["empty"]:
    st.info("backtest_predictions 为空——先跑回测")
    st.stop()

_MARKETS = [("H", "主胜 Home"), ("D", "平局 Draw"), ("A", "客胜 Away"),
             ("O2.5", "大 2.5 球 Over 2.5")]
tabs = st.tabs([f"{label}（{key}）" for key, label in _MARKETS])
for tab, (key, _) in zip(tabs, _MARKETS):
    buckets = cal.get(key) or []
    with tab:
        if not buckets:
            st.info("该市场无预测行（如大小球通道未跑）/ No rows for this market")
            continue
        df = pd.DataFrame(buckets)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=[0, 1], y=[0, 1], mode="lines",
                                 line=dict(dash="dash"), name="理想 Ideal"))
        fig.add_trace(go.Scatter(x=df["avg_p"], y=df["emp"], mode="markers+lines",
                                 marker=dict(size=df["n"].clip(4, 40),
                                             sizemode="diameter"),
                                 text=[f"n={n}" for n in df["n"]],
                                 name="十分位 Deciles"))
        fig.update_layout(height=380, xaxis_title="平均预测概率 Avg. predicted",
                          yaxis_title="实际频率 Empirical rate", xaxis_range=[0, 1],
                          yaxis_range=[0, 1], margin=dict(t=30, b=20))
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df, use_container_width=True, hide_index=True)
