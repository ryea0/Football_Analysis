"""A 线 · 校准曲线：十分位分组，分市场（设计 §3 页6）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from loaders import calibration, overview
from queries import COL_BILINGUAL

PALETTE = {
    "primary": "#2563eb",
    "good": "#059669",
    "bad": "#dc2626",
    "warn": "#d97706",
    "muted": "#6b7280",
    "grid": "#e5e7eb",
}

MARKET_COLORS = {
    "H": "#059669",
    "D": "#6b7280",
    "A": "#dc2626",
    "O2.5": "#8b5cf6",
}


def _chart_template(fig: go.Figure, height: int = 400) -> go.Figure:
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

st.caption("理想线 = 完美校准（预测概率 = 实际频率）；点越靠近对角线越好。"
           "气泡大小 = 该桶样本量。")

_MARKETS = [("H", "主胜 Home"), ("D", "平局 Draw"), ("A", "客胜 Away"),
             ("O2.5", "大 2.5 球 Over 2.5")]
tabs = st.tabs([f"{label}（{key}）" for key, label in _MARKETS])

for tab, (key, _) in zip(tabs, _MARKETS):
    buckets = cal.get(key) or []
    color = MARKET_COLORS.get(key, PALETTE["primary"])
    with tab:
        if not buckets:
            st.info("该市场无预测行（如大小球通道未跑）/ No rows for this market")
            continue
        df = pd.DataFrame(buckets)

        fig = go.Figure()
        # 理想线
        fig.add_trace(go.Scatter(
            x=[0, 1], y=[0, 1], mode="lines",
            line=dict(dash="dash", color=PALETTE["muted"], width=1.5),
            name="理想线 Ideal",
            hoverinfo="skip",
        ))
        # 十分位点
        fig.add_trace(go.Scatter(
            x=df["avg_p"], y=df["emp"],
            mode="markers+lines",
            marker=dict(
                size=df["n"].clip(5, 30),
                sizemode="diameter",
                color=color,
                opacity=0.85,
                line=dict(width=1, color="white"),
            ),
            line=dict(color=color, width=2),
            text=[f"n={n}<br>avg_p={p:.3f}<br>emp={e:.3f}"
                  for n, p, e in zip(df["n"], df["avg_p"], df["emp"])],
            hoverinfo="text",
            name="十分位 Deciles",
        ))
        fig.update_xaxes(title_text="平均预测概率 Avg. predicted", range=[0, 1])
        fig.update_yaxes(title_text="实际频率 Empirical rate", range=[0, 1])
        _chart_template(fig, height=400)
        st.plotly_chart(fig, use_container_width=True, config=dict(displayModeBar=False))

        # 表格
        st.dataframe(
            df.rename(columns=COL_BILINGUAL),
            use_container_width=True,
            hide_index=True,
            height=min(380, len(df) * 36 + 40),
        )
