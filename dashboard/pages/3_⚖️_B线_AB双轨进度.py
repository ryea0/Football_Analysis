"""B 线 · A/B 双轨进度：§12.3 预注册判据的可视化（设计 §3 页3）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import plotly.graph_objects as go
import streamlit as st

from loaders import ab_tracks

TARGET = 300  # §12.3：累计 ≥300 注

st.header("B 线 · A/B 双轨进度（§12.3 预注册判据）")
try:
    t = ab_tracks()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

c1, c2 = st.columns(2)
for col, (name, tr) in zip((c1, c2), t.items()):
    with col:
        st.subheader(name)
        st.metric("注数", f"{tr['n']} / {TARGET}")
        st.progress(min(tr["n"] / TARGET, 1.0))
        st.metric("ROI（已结算）", "—" if tr["roi"] is None else f"{tr['roi']:+.1%}")
        st.metric("CLV 中位数", "—" if tr["clv_median"] is None
                  else f"{tr['clv_median']:+.2%}")

if any(tr["cum"]["dates"] for tr in t.values()):
    fig = go.Figure()
    for name, tr in t.items():
        if tr["cum"]["dates"]:
            fig.add_trace(go.Scatter(x=tr["cum"]["dates"], y=tr["cum"]["pnl"],
                                     mode="lines+markers", name=name))
    fig.update_layout(height=320, margin=dict(t=30, b=20),
                      yaxis_title="累计 P&L（已结算）")
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("两轨均无已结算注——累计 P&L 曲线待首个结算日落库后出现")

st.caption("§12.3 判据：累计 ≥300 注且 model_persona 轨 CLV>0 或 ROI 显著优于 "
           "model_only 才算胜出。当前样本量见上方进度条——远未达标时这些数字"
           "仅供观察，不构成前向证据。")
