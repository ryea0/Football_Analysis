"""B 线 · 总览：bankroll / P&L / 未结注 / 额度水位 / 三轨汇总 / 在途注（设计 §3 页1，v2）。

v2 变更（2026-09-07）：新增三轨汇总条（P0）+ 在途注列表（P1）。
v3 变更（2026-09-07）：顶部统一时间范围筛选器，指标量按范围重算，状态量保留全量标注。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # dashboard/ 入 path

from datetime import timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from components.time_filter import time_range_filter
from loaders import pending_bets, paper_bets, runs, summary
from queries import COL_BILINGUAL
from time_utils import (anchor_from_series, filter_by_date_range,
                        to_bj_dates)

_BEIJING = timezone(timedelta(hours=8))

# ── 统一配色（深/浅通用的品牌中性色）──
PALETTE = {
    "primary": "#2563eb",
    "good": "#059669",
    "bad": "#dc2626",
    "warn": "#d97706",
    "muted": "#6b7280",
    "grid": "#e5e7eb",
    "bg": "#f9fafb",
}

STRAT_COLORS = {
    "model_only": "#6b7280",
    "model_persona": "#2563eb",
    "model_persona_nokb": "#8b5cf6",
    "model_persona_kb_self": "#059669",
}


def _bj_time(utc_str: str) -> str:
    """UTC ISO 串 → 北京时字符串（MM-DD HH:MM）。"""
    try:
        dt = pd.to_datetime(utc_str, utc=True)
        return dt.tz_convert(_BEIJING).strftime("%m-%d %H:%M")
    except Exception:
        return str(utc_str)


def _chart_template(fig: go.Figure, height: int = 320) -> go.Figure:
    """统一图表模板：去外框、细网格、淡色轴、紧凑边距。"""
    fig.update_layout(
        height=height,
        margin=dict(t=10, b=10, l=10, r=10),
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(size=12, family="-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Segoe UI', sans-serif"),
        xaxis=dict(
            showgrid=True,
            gridcolor=PALETTE["grid"],
            gridwidth=0.5,
            zeroline=False,
            showline=True,
            linecolor="#d1d5db",
            linewidth=0.5,
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor=PALETTE["grid"],
            gridwidth=0.5,
            zeroline=False,
            showline=True,
            linecolor="#d1d5db",
            linewidth=0.5,
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=11),
        ),
    )
    return fig


# ── 三轨显示标签 ──
STRAT_LABELS = {
    "model_only": "A 轨 · 模型基线",
    "model_persona": "B 轨 · 模型+人格",
    "model_persona_nokb": "C 轨 · nokb 对照",
    "model_persona_kb_self": "C' 轨 · 自反思知识",
}


def _calc_strat_summary(bets_df: pd.DataFrame) -> dict:
    """从 bets 明细按 strategy 计算汇总指标。"""
    if bets_df.empty:
        return {}
    out = {}
    for strat, g in bets_df.groupby("strategy"):
        settled = g[g["status"] == "settled"]
        pending = g[g["status"] == "pending"]
        n_settled = len(settled)
        pnl = settled["pnl"].sum() if n_settled else 0.0
        staked = settled["stake"].sum() if n_settled else 0.0
        roi = pnl / staked if staked else None
        n_won = int((settled["result"] == "won").sum()) if n_settled else 0
        win_rate = n_won / n_settled if n_settled else None
        clv = settled["clv"].dropna()
        clv_med = float(clv.median()) if len(clv) else None
        out[strat] = {
            "n_settled": n_settled,
            "n_pending": len(pending),
            "pnl": pnl,
            "roi": roi,
            "win_rate": win_rate,
            "clv_median": clv_med,
            "bankroll": None,  # 状态量不重算
        }
    return out


st.header("B 线 · 总览（paper 运营）")
try:
    s = summary()
    all_bets = paper_bets()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

# ── 时间范围筛选器 ──
anchor = anchor_from_series(all_bets["settled_at"]) if not all_bets.empty else None
start_date, end_date, grain = time_range_filter(
    key="b1_overview",
    default_preset="30d",
    anchor_date=anchor,
)

# 按结算日过滤 bets（指标量口径）
bets = filter_by_date_range(all_bets, "settled_at", start_date, end_date) \
    if not all_bets.empty else all_bets

# 按落注日过滤 pending（在途注口径）
pend_all = pending_bets()
pend = filter_by_date_range(pend_all, "placed_at", start_date, end_date) \
    if not pend_all.empty else pend_all

# 额度曲线按 started_at 过滤
quota_all = s["quota_series"]
if quota_all:
    quota_df = pd.DataFrame(quota_all)
    quota_df = filter_by_date_range(quota_df, "started_at", start_date, end_date)
    quota_pts = [(r["started_at"], r["credits_after"])
                 for _, r in quota_df.iterrows()
                 if r["credits_after"] is not None]
else:
    quota_pts = []

# 计算过滤后的全局指标
if not bets.empty:
    settled_bets = bets[bets["status"] == "settled"]
    n_settled = len(settled_bets)
    total_pnl = float(settled_bets["pnl"].sum()) if n_settled else 0.0
    total_staked = float(settled_bets["stake"].sum()) if n_settled else 0.0
    range_roi = total_pnl / total_staked if total_staked else 0.0
else:
    n_settled = 0
    total_pnl = 0.0
    range_roi = 0.0

n_pending_range = len(pend) if not pend.empty else 0

# —— 第一行：核心 KPI 卡片 ——
st.markdown("#### 🎯 核心指标 Key Metrics")
c1, c2, c3, c4 = st.columns(4)
c1.metric("💰 bankroll 余额",
          "—" if s["bankroll"] is None else f"{s['bankroll']:.0f}",
          help="模拟盘总资金池（meta.paper_bankroll）· 当前状态量，不受时间范围影响")
c2.metric(f"📈 累计 P&L（已结算）",
          f"{total_pnl:+.2f}",
          delta=f"{range_roi:+.1%} ROI" if total_staked > 0 else None,
          delta_color="normal" if total_pnl >= 0 else "inverse",
          help=f"当前时间范围内已结算注的盈亏总和（不含 pending）· 共 {n_settled} 注")
c3.metric("⏳ 在途注 Pending",
          n_pending_range,
          help="当前时间范围内落注、尚未结算的 paper 注")
c4.metric("🎟️ Odds API 额度",
          "—" if s["quota_remaining"] is None else int(s["quota_remaining"]),
          delta="充足" if (s["quota_remaining"] or 0) > 200 else "偏低" if s["quota_remaining"] else None,
          delta_color="normal" if (s["quota_remaining"] or 0) > 200 else "inverse",
          help="oddsapi 剩余调用额度（spec §3.4 节流：<100 跳拉盘）· 当前状态量，不受时间范围影响")

# —— 额度曲线 ——
if quota_pts:
    st.markdown("#### 📊 额度水位趋势 Quota Trend")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[p[0] for p in quota_pts], y=[p[1] for p in quota_pts],
        mode="lines+markers",
        name="剩余额度",
        line=dict(color=PALETTE["primary"], width=2),
        marker=dict(size=5, color=PALETTE["primary"]),
        fill="tozeroy",
        fillcolor="rgba(37, 99, 235, 0.08)",
    ))
    fig.add_hline(y=100, line_dash="dash", line_color=PALETTE["bad"],
                  annotation_text="节流阈值 100",
                  annotation_position="bottom right",
                  annotation_font_size=11)
    fig.update_yaxes(title_text="剩余额度 credits")
    _chart_template(fig, height=260)
    st.plotly_chart(fig, use_container_width=True, config=dict(displayModeBar=False))
else:
    st.caption("当前时间范围内无 run 记录——额度曲线待首个 run 落库后出现 / No runs in range")

st.divider()

# —— 三轨汇总条（P0）——
st.markdown("#### 🛤️ 三轨汇总 Track Summary")
strat_sum = _calc_strat_summary(bets)
# 用全量的 strategy 顺序（保证排序稳定）
strats = list(s["by_strategy"].keys())
# 过滤掉范围内没有数据的轨
strats_in_range = [st_name for st_name in strats if st_name in strat_sum]

if not strats_in_range:
    st.info("当前时间范围内无已结算注 / No settled bets in range")
else:
    n_per_row = min(len(strats_in_range), 4)
    for row_start in range(0, len(strats_in_range), n_per_row):
        row_strats = strats_in_range[row_start:row_start + n_per_row]
        cols = st.columns(len(row_strats))
        for col, strat in zip(cols, row_strats):
            tr = strat_sum[strat]
            label = STRAT_LABELS.get(strat, strat)
            color = STRAT_COLORS.get(strat, PALETTE["primary"])
            full_br = s["by_strategy"].get(strat, {}).get("bankroll")
            with col:
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">'
                    f'<div style="width:4px;height:18px;background:{color};border-radius:2px;"></div>'
                    f'<span style="font-weight:600;font-size:0.92rem;">{label}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                st.caption(f"`{strat}`")

                c1, c2 = st.columns(2)
                c1.metric("Bankroll",
                          "—" if full_br is None else f"{full_br:.0f}",
                          help="当前余额，不受时间范围影响")
                c2.metric("已结注", tr["n_settled"])
                c3, c4 = st.columns(2)
                c3.metric("胜率",
                          "—" if tr["win_rate"] is None else f"{tr['win_rate']:.1%}")
                roi_val = tr["roi"]
                c4.metric("ROI",
                          "—" if roi_val is None else f"{roi_val:+.1%}",
                          delta_color="normal" if (roi_val or 0) >= 0 else "inverse")
                c5, c6 = st.columns(2)
                clv_val = tr["clv_median"]
                c5.metric("CLV 中位",
                          "—" if clv_val is None else f"{clv_val:+.2%}",
                          delta_color="normal" if (clv_val or 0) >= 0 else "inverse")
                c6.metric("在途", tr["n_pending"])

                if tr["n_settled"] < 300:
                    st.caption("⚠️ 样本 < 300 注，仅供观察")

st.divider()

# —— 在途注列表（P1）——
with st.expander(f"⏳ 在途注 Pending Bets（{n_pending_range} 注）",
                 expanded=n_pending_range > 0):
    if pend.empty:
        st.info("当前时间范围内无在途注 / No pending bets in range")
    else:
        pend["kickoff_bj"] = pend["kickoff_utc"].map(
            lambda x: _bj_time(x) if x else "—")
        front = ["kickoff_bj", "league", "home", "away", "market", "strategy",
                 "odds_taken", "stake"]
        ordered = [c for c in front if c in pend.columns] + \
                  [c for c in pend.columns if c not in front]
        display_cols = [c for c in ordered if c not in ("id", "placed_at")]
        st.dataframe(
            pend[display_cols].rename(columns=COL_BILINGUAL),
            use_container_width=True,
            hide_index=True,
            height=min(420, max(200, len(pend) * 36 + 40)),
        )
    st.caption("在途注按落注日（placed_at）的北京日落在范围内筛选。")

# —— 最近 run ——
st.markdown("#### 🕐 最近 run Recent Runs")
runs_all = runs()
runs_filtered = filter_by_date_range(runs_all, "started_at", start_date, end_date) \
    if not runs_all.empty else runs_all
df = runs_filtered.head(5)
if df.empty:
    st.caption("当前时间范围内无 run 记录 / No runs in range")
else:
    st.dataframe(
        df.rename(columns=COL_BILINGUAL),
        use_container_width=True,
        hide_index=True,
        height=min(260, len(df) * 38 + 35),
    )
