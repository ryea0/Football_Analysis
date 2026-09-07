"""A 线 · 模拟盘回测：flat vs ¼ Kelly（设计 §3 页7）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from loaders import paper_sim
from queries import COL_BILINGUAL

PALETTE = {
    "primary": "#2563eb",
    "good": "#059669",
    "bad": "#dc2626",
    "warn": "#d97706",
    "muted": "#6b7280",
    "grid": "#e5e7eb",
}


def _chart_template(fig: go.Figure, height: int = 320) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(t=10, b=10, l=10, r=10),
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(size=12, family="-apple-system, BlinkMacSystemFont, 'PingFang SC', sans-serif"),
        xaxis=dict(showgrid=True, gridcolor=PALETTE["grid"], gridwidth=0.5,
                   showline=True, linecolor="#d1d5db", linewidth=0.5, zeroline=False),
        yaxis=dict(showgrid=True, gridcolor=PALETTE["grid"], gridwidth=0.5,
                   showline=True, linecolor="#d1d5db", linewidth=0.5, zeroline=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    xanchor="right", x=1, font=dict(size=11)),
    )
    return fig


st.header("A 线 · 模拟盘回测（flat / ¼ Kelly）")
try:
    s = paper_sim()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()
if s["empty"]:
    st.info("无候选注——回测候选由 §5.2 门槛筛出（EV≥3% 且 edge≥2% 且赔率∈[1.4,6.0]）"
            " / No candidates — gated by §5.2 thresholds")
    st.stop()

# —— 两种策略 KPI 对比 ——
st.markdown("#### 💰 策略对比 Strategy Comparison")
c1, c2 = st.columns(2)

with c1:
    st.markdown(
        '<div style="padding:0.75rem 1rem;border-radius:10px;'
        'background:#f3f4f6;border:1px solid #e5e7eb;">'
        '<span style="font-weight:600;font-size:0.95rem;">📊 flat（平注 1 单位）</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.write("")  # 间距
    m1, m2, m3 = st.columns(3)
    m1.metric("n 候选注", s["flat"]["n"])
    m2.metric("P&L", f"{s['flat']['pnl']:+.1f}",
              delta_color="normal" if s["flat"]["pnl"] >= 0 else "inverse")
    m3.metric("ROI", f"{s['flat']['roi']:+.1%}",
              delta_color="normal" if s["flat"]["roi"] >= 0 else "inverse")

with c2:
    st.markdown(
        '<div style="padding:0.75rem 1rem;border-radius:10px;'
        'background:#eff6ff;border:1px solid #bfdbfe;">'
        '<span style="font-weight:600;font-size:0.95rem;color:#1d4ed8;">'
        '🎯 ¼ Kelly（上限 2%）</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.write("")
    m1, m2, m3 = st.columns(3)
    m1.metric("终值（起 1000）Final", f"{s['kelly']['final_bankroll']:.1f}")
    m2.metric("ROI", f"{s['kelly']['roi']:+.1%}",
              delta_color="normal" if s["kelly"]["roi"] >= 0 else "inverse")
    m3.metric("最大回撤 Max DD", f"{s['kelly']['max_drawdown_pct']:.1%}",
              delta_color="inverse")

st.divider()

# —— 累计曲线 ——
st.markdown("#### 📈 累计曲线 Cumulative Curves")
left, right = st.columns(2)
flat_df = pd.DataFrame(s["flat_curve"])
kelly_df = pd.DataFrame(s["kelly_curve"])

with left:
    st.caption("**flat P&L 曲线**")
    f1 = go.Figure()
    f1.add_trace(go.Scatter(
        x=flat_df["date"], y=flat_df["pnl"],
        mode="lines", name="flat P&L",
        line=dict(color=PALETTE["muted"], width=2),
        fill="tozeroy",
        fillcolor="rgba(107, 114, 128, 0.1)",
    ))
    f1.add_hline(y=0, line_dash="dash", line_color=PALETTE["muted"], line_width=1)
    f1.update_layout(yaxis_title="累计 P&L Cum. P&L")
    _chart_template(f1, height=300)
    st.plotly_chart(f1, use_container_width=True, config=dict(displayModeBar=False))

with right:
    st.caption("**Kelly bankroll 曲线**")
    f2 = go.Figure()
    f2.add_trace(go.Scatter(
        x=kelly_df["date"], y=kelly_df["bankroll"],
        mode="lines", name="bankroll",
        line=dict(color=PALETTE["primary"], width=2),
        fill="tozeroy",
        fillcolor="rgba(37, 99, 235, 0.08)",
    ))
    f2.add_hline(y=1000, line_dash="dash", line_color=PALETTE["muted"],
                 line_width=1, annotation_text="本金 1000",
                 annotation_position="bottom right")
    f2.update_layout(yaxis_title="bankroll 余额")
    _chart_template(f2, height=300)
    st.plotly_chart(f2, use_container_width=True, config=dict(displayModeBar=False))

st.divider()

# —— 分市场 / 分赔率区间 ——
st.markdown("#### 📋 分解明细 Breakdown")
c3, c4 = st.columns(2)
with c3:
    st.markdown("**按市场 By Market**")
    mkt_df = pd.DataFrame(s["by_market"]).T
    st.dataframe(
        mkt_df.rename(columns=COL_BILINGUAL),
        use_container_width=True,
        height=min(320, len(mkt_df) * 36 + 40),
    )
with c4:
    st.markdown("**按赔率区间 By Odds Band**")
    band_df = pd.DataFrame(s["by_band"]).T
    st.dataframe(
        band_df.rename(columns=COL_BILINGUAL),
        use_container_width=True,
        height=min(320, len(band_df) * 36 + 40),
    )

st.caption(
    "成交价 = Pinnacle 收盘价（保守）；O2.5 缺收盘赔率，回测候选不含该市场"
    "（candidates 口径）；与 M2 报告同源同口径。"
    " / Fill price = Pinnacle closing (conservative); O2.5 excluded "
    "(no closing odds in backtest candidates); same basis as M2 report."
)
