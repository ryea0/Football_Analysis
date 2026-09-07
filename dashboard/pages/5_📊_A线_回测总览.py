"""A 线 · 回测总览：log-loss/Brier vs 去水收盘（设计 §3 页5）。

口径 = walk-forward 全样本复算（与 m2-verdict 同一判据，spec §8.2）。
v2（2026-09-07）：顶部统一时间范围筛选器，与联赛/赛季叠加过滤。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from components.time_filter import time_range_filter
from loaders import overview
from queries import COL_BILINGUAL

PALETTE = {
    "primary": "#2563eb",
    "good": "#059669",
    "bad": "#dc2626",
    "warn": "#d97706",
    "muted": "#6b7280",
    "grid": "#e5e7eb",
}


def _chart_template(fig: go.Figure, height: int = 380) -> go.Figure:
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


st.header("A 线 · 回测总览（walk-forward 历史回测）")
try:
    full = overview()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()
if full["empty"]:
    st.info("backtest_predictions 为空——先运行 `uv run fa backtest run --from 2019 --to 2025`")
    st.stop()

leagues_all = sorted(full["by_league"])
seasons_all = sorted(full["by_season"])

# ── 时间范围筛选器 ──
last_season = max(int(s) for s in seasons_all) if seasons_all else 2025
anchor_date = pd.Timestamp(f"{last_season}-06-30").date()

start_date, end_date, grain = time_range_filter(
    key="a5_overview",
    default_preset="all",
    anchor_date=anchor_date,
)

date_from_str = start_date.isoformat() if start_date.year > 2000 else None
date_to_str = end_date.isoformat() if end_date.year < 2090 else None

# 过滤控件
c1, c2 = st.columns(2)
sel_l = c1.multiselect("联赛 Leagues", leagues_all, default=leagues_all)
sel_s = c2.multiselect("赛季 Seasons", seasons_all, default=seasons_all)

o = overview(sel_l or None, sel_s or None, date_from_str, date_to_str)
if o["empty"]:
    st.info("该过滤组合无预测行 / No rows for this filter")
    st.stop()

ov = o["overall"]

# —— 核心指标 ——
st.markdown("#### 📊 总体表现 Overall Performance")
c1, c2, c3 = st.columns(3)
c1.metric("模型 log-loss Model", f"{ov['model_ll']:.5f}",
          help="模型预测的对数损失（越低越好）")
c2.metric("市场 log-loss（去水收盘）Market", f"{ov['market_ll']:.5f}",
          help="Pinnacle 收盘去水后的对数损失（基准）")

degr = ov["degradation_pct"]
verdict_color = "normal" if degr <= 1 else "inverse"
c3.metric("劣化 Degradation", f"{degr:+.2f}%",
          delta=f"判据 ≤ +1%  GO（当前 {ov['verdict']}）",
          delta_color=verdict_color,
          help="model_ll/market_ll − 1；正值=模型劣于市场")

st.caption(
    f"📌 n = **{ov['n']:,}** 场 · Brier：模型 **{ov['model_brier']:.5f}** / 市场 **{ov['market_brier']:.5f}**"
    f" · 口径同 spec §8.2 / docs/m2-verdict.md"
)

st.divider()

# —— 热图 ——
cross = pd.DataFrame(
    [{"league": k.split("|")[0], "season": int(k.split("|")[1]), **{
        "degradation_pct": v["degradation_pct"], "n": v["n"]}}
     for k, v in o["by_league_season"].items()])

if not cross.empty:
    piv = cross.pivot(index="league", columns="season", values="degradation_pct")

    st.markdown("#### 🔥 劣化热图：联赛 × 赛季 Heatmap")
    st.caption("model_ll/market_ll − 1（负 = 模型优；判据线 Gate +1%）"
               " · 按当前时间范围与联赛/赛季筛选交集渲染")
    fig = px.imshow(
        piv,
        text_auto=".2f",
        aspect="auto",
        color_continuous_scale="RdBu_r",
        color_continuous_midpoint=0,
        zmin=max(piv.min().min(), -3) if not piv.empty else -3,
        zmax=min(piv.max().max(), 3) if not piv.empty else 3,
        labels=dict(x="赛季", y="联赛", color="劣化 %"),
    )
    fig.update_traces(
        hovertemplate="联赛: %{y}<br>赛季: %{x}<br>劣化: %{z:.2f}%<extra></extra>",
        textfont=dict(size=11),
    )
    _chart_template(fig, height=max(320, len(piv) * 38 + 80))
    fig.update_layout(
        coloraxis_colorbar=dict(
            title="劣化 %",
            thickness=14,
            len=0.7,
            x=1.02,
        ),
        xaxis_title="赛季 Season",
        yaxis_title="联赛 League",
    )
    st.plotly_chart(fig, use_container_width=True, config=dict(displayModeBar=False))
else:
    st.info("当前筛选条件下无联赛×赛季交叉数据")

st.divider()

# —— 分联赛 / 分赛季 ——
st.markdown("#### 📋 分解表 Breakdown")
tab1, tab2 = st.tabs(["按联赛 League", "按赛季 Season"])
with tab1:
    league_df = pd.DataFrame(o["by_league"]).T.drop(columns=["cal_home"])
    st.dataframe(
        league_df.rename(columns=COL_BILINGUAL),
        use_container_width=True,
        height=min(480, max(200, len(league_df) * 36 + 45)),
    )
with tab2:
    season_df = pd.DataFrame(o["by_season"]).T.drop(columns=["cal_home"])
    st.dataframe(
        season_df.rename(columns=COL_BILINGUAL),
        use_container_width=True,
        height=min(480, max(200, len(season_df) * 36 + 45)),
    )

st.caption("A 线为历史回测证据，与 B 线前向运营结论分账（spec §12.3），互不冒充。")
