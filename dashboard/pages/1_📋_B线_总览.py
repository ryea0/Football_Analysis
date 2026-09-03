"""B 线 · 总览：bankroll / P&L / 未结注 / 额度水位 / 最近 run（设计 §3 页1）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # dashboard/ 入 path

import plotly.graph_objects as go
import streamlit as st

from loaders import runs, summary

st.header("B 线 · 总览（paper 运营）")
try:
    s = summary()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("bankroll", "—" if s["bankroll"] is None else f"{s['bankroll']:.0f}")
c2.metric("累计 P&L（已结算）", f"{s['pnl']:+.2f}")
c3.metric("未结注", s["n_pending"])
c4.metric("Odds API 剩余额度",
          "—" if s["quota_remaining"] is None else int(s["quota_remaining"]))

pts = [(r["started_at"], r["credits_after"]) for r in s["quota_series"]
       if r["credits_after"] is not None]
if pts:
    fig = go.Figure(go.Scatter(x=[p[0] for p in pts], y=[p[1] for p in pts],
                               mode="lines+markers", name="credits"))
    fig.add_hline(y=100, line_dash="dash",
                  annotation_text="降频阈值 100（spec §3.4）")
    fig.update_layout(height=300, margin=dict(t=30, b=20))
    st.plotly_chart(fig, use_container_width=True)
else:
    st.caption("暂无 run 记录——额度曲线待首个 run 落库后出现")

st.subheader("最近 run")
df = runs().head(5)
st.dataframe(df, use_container_width=True, hide_index=True)
