"""A 线 · 模拟盘回测：flat vs ¼ Kelly（设计 §3 页7）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from loaders import paper_sim

st.header("A 线 · 模拟盘回测（flat / ¼ Kelly）Backtest Simulation")
try:
    s = paper_sim()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()
if s["empty"]:
    st.info("无候选注——回测候选由 §5.2 门槛筛出（EV≥3% 且 edge≥2% 且赔率∈[1.4,6.0]）"
            " / No candidates — gated by §5.2 thresholds")
    st.stop()

c1, c2 = st.columns(2)
with c1:
    st.subheader("flat（平注 1 单位）Flat Stakes")
    st.metric("n", s["flat"]["n"])
    st.metric("P&L", f"{s['flat']['pnl']:+.1f}")
    st.metric("ROI", f"{s['flat']['roi']:+.1%}")
with c2:
    st.subheader("¼ Kelly（上限 2%）Kelly Capped")
    st.metric("终值（起 1000）Final", f"{s['kelly']['final_bankroll']:.1f}")
    st.metric("ROI", f"{s['kelly']['roi']:+.1%}")
    st.metric("最大回撤 Max DD", f"{s['kelly']['max_drawdown_pct']:.1%}")

left, right = st.columns(2)
flat_df = pd.DataFrame(s["flat_curve"])
kelly_df = pd.DataFrame(s["kelly_curve"])
f1 = go.Figure(go.Scatter(x=flat_df["date"], y=flat_df["pnl"],
                          mode="lines", name="flat P&L"))
f1.update_layout(height=300, yaxis_title="累计 P&L Cum. P&L", margin=dict(t=30, b=20))
left.plotly_chart(f1, use_container_width=True)
f2 = go.Figure(go.Scatter(x=kelly_df["date"], y=kelly_df["bankroll"],
                          mode="lines", name="bankroll"))
f2.update_layout(height=300, yaxis_title="bankroll 余额", margin=dict(t=30, b=20))
right.plotly_chart(f2, use_container_width=True)

c3, c4 = st.columns(2)
with c3:
    st.subheader("分市场 By Market")
    st.dataframe(pd.DataFrame(s["by_market"]).T, use_container_width=True)
with c4:
    st.subheader("分赔率区间 By Odds Band")
    st.dataframe(pd.DataFrame(s["by_band"]).T, use_container_width=True)

st.caption("成交价 = Pinnacle 收盘价（保守）；O2.5 缺收盘赔率，回测候选不含该市场"
           "（candidates 口径）；与 M2 报告同源同口径。"
           " / Fill price = Pinnacle closing (conservative); O2.5 excluded "
           "(no closing odds in backtest candidates); same basis as M2 report.")
