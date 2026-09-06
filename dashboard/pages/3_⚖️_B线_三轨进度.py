"""B 线 · 三轨进度：§12.3 预注册判据 + C 线 nokb 对照（设计 §3 页3，v2）。

v2 变更（2026-09-07）：两轨 → 三轨（+ model_persona_nokb），nokb 为 C 线
消融对照轨，不参与 §12.3 胜出判决。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # dashboard/ 入 path

import plotly.graph_objects as go
import streamlit as st

from loaders import ab_tracks

TARGET = 300  # §12.3：累计 ≥300 注

# 三轨显示名（含 C 线标注）
STRAT_LABELS = {
    "model_only": "A 轨 · model_only",
    "model_persona": "B 轨 · model_persona",
    "model_persona_nokb": "C 轨 · model_persona_nokb（消融对照）",
}

st.header("B 线 · 三轨进度（§12.3 + C 线对照）Three Tracks")
try:
    t = ab_tracks()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

strats = list(t.keys())  # 已按 STRATEGIES 排序

cols = st.columns(len(strats))
for col, strat in zip(cols, strats):
    tr = t[strat]
    label = STRAT_LABELS.get(strat, strat)
    with col:
        st.subheader(label)
        st.metric("注数 Bets", f"{tr['n']} / {TARGET}")
        st.progress(min(tr["n"] / TARGET, 1.0))
        st.metric("ROI（已结算）Settled",
                  "—" if tr["roi"] is None else f"{tr['roi']:+.1%}")
        st.metric("CLV 中位数 Median",
                  "—" if tr["clv_median"] is None else f"{tr['clv_median']:+.2%}")

if any(tr["cum"]["dates"] for tr in t.values()):
    fig = go.Figure()
    for strat in strats:
        tr = t[strat]
        if tr["cum"]["dates"]:
            fig.add_trace(go.Scatter(x=tr["cum"]["dates"], y=tr["cum"]["pnl"],
                                     mode="lines+markers",
                                     name=STRAT_LABELS.get(strat, strat)))
    fig.update_layout(height=320, margin=dict(t=30, b=20),
                      yaxis_title="累计 P&L（已结算）Cum. P&L",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                  xanchor="right", x=1))
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("三轨均无已结算注——累计 P&L 曲线待首个结算日落库后出现 / No settled bets yet")

st.caption(
    "§12.3 判据：累计 ≥300 注且 model_persona 轨 CLV>0 或 ROI 显著优于 "
    "model_only 才算胜出。"
    "C 轨（model_persona_nokb）为 C 线消融对照——人格无知识库版本，"
    "用于量化 KB 增益，**不参与 §12.3 胜出判决**。"
    "当前样本量见上方进度条——远未达标时这些数字仅供观察，不构成前向证据。"
)
