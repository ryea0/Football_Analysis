"""B 线 · 总览：bankroll / P&L / 未结注 / 额度水位 / 三轨汇总 / 在途注（设计 §3 页1，v2）。

v2 变更（2026-09-07）：新增三轨汇总条（P0）+ 在途注列表（P1）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # dashboard/ 入 path

from datetime import timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from loaders import pending_bets, runs, summary
from queries import COL_BILINGUAL

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

st.header("B 线 · 总览（paper 运营）")
try:
    s = summary()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

# —— 第一行：核心 KPI 卡片 ——
st.markdown("#### 🎯 核心指标 Key Metrics")
c1, c2, c3, c4 = st.columns(4)
c1.metric("💰 bankroll 余额",
          "—" if s["bankroll"] is None else f"{s['bankroll']:.0f}",
          help="模拟盘总资金池（meta.paper_bankroll）")
c2.metric("📈 累计 P&L（已结算）",
          f"{s['pnl']:+.2f}",
          delta=f"{(s['pnl'] / s['staked'] * 100):+.1f}% ROI" if s["staked"] else None,
          delta_color="normal" if s["pnl"] >= 0 else "inverse",
          help="已结算注的盈亏总和（不含 pending）")
c3.metric("⏳ 在途注 Pending",
          s["n_pending"],
          help="尚未结算的 paper 注")
c4.metric("🎟️ Odds API 额度",
          "—" if s["quota_remaining"] is None else int(s["quota_remaining"]),
          delta="充足" if (s["quota_remaining"] or 0) > 200 else "偏低" if s["quota_remaining"] else None,
          delta_color="normal" if (s["quota_remaining"] or 0) > 200 else "inverse",
          help="oddsapi 剩余调用额度（spec §3.4 节流：<100 跳拉盘）")

# —— 额度曲线 ——
pts = [(r["started_at"], r["credits_after"]) for r in s["quota_series"]
       if r["credits_after"] is not None]
if pts:
    st.markdown("#### 📊 额度水位趋势 Quota Trend")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[p[0] for p in pts], y=[p[1] for p in pts],
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
    st.caption("暂无 run 记录——额度曲线待首个 run 落库后出现 / No runs yet")

st.divider()

# —— 三轨汇总条（P0）——
st.markdown("#### 🛤️ 三轨汇总 Track Summary")
by_strat = s["by_strategy"]
strats = list(by_strat.keys())  # 已按 STRATEGIES 排序

# 每行最多 4 轨，多行自适应
n_per_row = min(len(strats), 4)
for row_start in range(0, len(strats), n_per_row):
    row_strats = strats[row_start:row_start + n_per_row]
    cols = st.columns(len(row_strats))
    for col, strat in zip(cols, row_strats):
        tr = by_strat[strat]
        label = STRAT_LABELS.get(strat, strat)
        color = STRAT_COLORS.get(strat, PALETTE["primary"])
        with col:
            # 轨标题（带色块标记）
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
                      "—" if tr["bankroll"] is None else f"{tr['bankroll']:.0f}")
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
with st.expander(f"⏳ 在途注 Pending Bets（{s['n_pending']} 注）",
                 expanded=s["n_pending"] > 0):
    pend = pending_bets()
    if pend.empty:
        st.info("当前无在途注 / No pending bets")
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

# —— 最近 run ——
st.markdown("#### 🕐 最近 run Recent Runs")
df = runs().head(5)
if df.empty:
    st.caption("暂无 run 记录 / No runs yet")
else:
    st.dataframe(
        df.rename(columns=COL_BILINGUAL),
        use_container_width=True,
        hide_index=True,
        height=min(260, len(df) * 38 + 35),
    )
