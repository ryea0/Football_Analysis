"""B 线 · 推荐与台账：推荐全维度表 + 盈亏分解 + paper 注明细（设计 §3 页2，v2）。

v2 变更（2026-09-07）：新增「盈亏分解」折叠面板（P1）——市场/联赛/按日
三维度 tab，柱图 + 表格双视图。

v3 变更（2026-09-07）：顶部统一时间范围筛选器（日/周/月/三月/半年/年/全部
+ 自定义），粒度自动匹配；替换原有单日 selectbox。盈亏分解"按周期"tab
x 轴随粒度自适应（日/周/月）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from components.time_filter import time_range_filter
from loaders import breakdown, paper_bets, recommendations
from queries import COL_BILINGUAL
from time_utils import (
    aggregate_by_grain,
    anchor_from_series,
    filter_by_date_range,
    to_bj_period,
)

PALETTE = {
    "primary": "#2563eb",
    "good": "#059669",
    "bad": "#dc2626",
    "warn": "#d97706",
    "muted": "#6b7280",
    "grid": "#e5e7eb",
}
STRAT_COLORS = {
    "model_only": "#6b7280",
    "model_persona": "#2563eb",
    "model_persona_nokb": "#8b5cf6",
    "model_persona_kb_self": "#059669",
}


def _chart_template(fig: go.Figure, height: int = 340) -> go.Figure:
    """统一图表模板。"""
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
        bargap=0.25,
    )
    return fig


# —— 辅助函数（页内局部，不归 queries：纯 UI 层渲染）——
def _render_breakdown_table(df: pd.DataFrame, dim_label: str, dim_col: str):
    """market/league 维度的表格渲染 + 柱图。"""
    if df.empty:
        st.info("暂无已结算数据 / No settled bets yet")
        return

    # 柱图：各策略在该维度上的 ROI 对比
    fig = go.Figure()
    for strat in sorted(df["strategy"].unique()):
        sub = df[df["strategy"] == strat]
        color = STRAT_COLORS.get(strat, PALETTE["primary"])
        fig.add_trace(go.Bar(
            x=sub[dim_col], y=sub["roi"], name=strat,
            marker_color=color,
            text=sub["roi"].map(lambda x: "—" if x is None or pd.isna(x) else f"{x:+.1%}"),
            textposition="outside",
            textfont=dict(size=10),
            hovertemplate=f"{strat}<br>{dim_col}: %{{x}}<br>ROI: %{{y:+.1%}}<extra></extra>",
        ))
    fig.update_layout(barmode="group", yaxis_title="ROI（已结算）",
                      yaxis_tickformat=".1%")
    fig.add_hline(y=0, line_dash="dash", line_color=PALETTE["muted"], line_width=1)
    _chart_template(fig, height=340)
    st.plotly_chart(fig, use_container_width=True, config=dict(displayModeBar=False))

    # 表格
    display = df.copy()
    pct_cols = ["win_rate", "roi", "clv_median"]
    for c in pct_cols:
        if c in display.columns:
            display[c] = display[c].map(
                lambda x: "—" if x is None or (isinstance(x, float) and pd.isna(x))
                else f"{x:+.2%}")

    st.dataframe(
        display.rename(columns=COL_BILINGUAL),
        use_container_width=True,
        hide_index=True,
        height=min(420, max(200, len(display) * 36 + 40)),
    )
    st.caption("样本不足 300 注时，ROI/CLV 数字仅供观察，不构成显著性结论。")


def _render_period_breakdown(df: pd.DataFrame, grain: str):
    """按周期（日/周/月）P&L 分解：柱图 + 分轨累计宽表。

    df 为已按时间范围过滤后的 paper 注明细（含 settled_at, strategy, pnl 等）。
    grain ∈ {"D", "W", "M"}。
    """
    if df.empty or "settled_at" not in df.columns:
        st.info("暂无已结算数据 / No settled bets yet")
        return

    grain_label = {"D": "日", "W": "周", "M": "月"}[grain]

    # 只取已结算的（有 settled_at 且 pnl 非 NaN）
    settled = df[df["status"].isin(["won", "lost"])].copy()
    if settled.empty:
        st.info("当前范围内暂无已结算注")
        return

    # 按周期 + 策略聚合
    agg = aggregate_by_grain(
        settled, "settled_at", grain,
        agg_spec={
            "pnl": ("sum", "pnl"),
            "n_settled": ("count", "pnl"),
            "stake": ("sum", "stake"),
        },
        group_cols=["strategy"],
    )

    # 柱图：各策略每周期 P&L（分组）
    fig = go.Figure()
    for strat in sorted(agg["strategy"].unique()):
        sub = agg[agg["strategy"] == strat].sort_values("period")
        color = STRAT_COLORS.get(strat, PALETTE["primary"])
        fig.add_trace(go.Bar(
            x=sub["period"], y=sub["pnl"], name=strat,
            marker_color=color,
            text=sub["pnl"].map(lambda x: f"{x:+.1f}"),
            textposition="outside",
            textfont=dict(size=10),
            hovertemplate=f"{strat}<br>周期: %{{x}}<br>P&L: %{{y:+.2f}}<extra></extra>",
        ))
    fig.update_layout(barmode="group", yaxis_title=f"每{grain_label} P&L Period P&L")
    fig.add_hline(y=0, line_dash="dash", line_color=PALETTE["muted"], line_width=1)
    _chart_template(fig, height=340)
    st.plotly_chart(fig, use_container_width=True, config=dict(displayModeBar=False))

    # 表格：周期 → 每轨 P&L + 累计 P&L（宽表格式）
    strats = sorted(agg["strategy"].unique())
    periods = sorted(agg["period"].unique())

    rows = []
    cum = {s: 0.0 for s in strats}
    for p in periods:
        row = {"period": p}
        for s in strats:
            sub = agg[(agg["period"] == p) & (agg["strategy"] == s)]
            period_pnl = float(sub["pnl"].iloc[0]) if len(sub) else 0.0
            n_bets = int(sub["n_settled"].iloc[0]) if len(sub) else 0
            cum[s] += period_pnl
            row[f"{s}_pnl"] = period_pnl
            row[f"{s}_n"] = n_bets
            row[f"{s}_cum"] = cum[s]
        rows.append(row)

    wide = pd.DataFrame(rows)
    display = pd.DataFrame({f"周期（{grain_label}）Period": wide["period"]})
    for s in strats:
        display[f"{s} 注数"] = wide[f"{s}_n"]
        display[f"{s} 当期P&L"] = wide[f"{s}_pnl"].map(lambda x: f"{x:+.2f}")
        display[f"{s} 累计P&L"] = wide[f"{s}_cum"].map(lambda x: f"{x:+.2f}")

    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
        height=min(420, max(200, len(display) * 36 + 40)),
    )
    st.caption(
        f"周期为北京{grain_label}（spec §9.6 全项目口径）。"
        "累计 P&L 从当前范围起点从零算起。"
        "样本不足 300 注时数字仅供观察。"
    )


def _breakdown_by_dim_from_bets(bets_df: pd.DataFrame, dim: str) -> pd.DataFrame:
    """从 paper 注明细按维度聚合（market/league），与 b_breakdown 口径一致。

    用于按时间范围过滤后重新聚合，避免查全量再全量重新算。
    dim ∈ {"market", "league"}。
    """
    if bets_df.empty:
        return pd.DataFrame(columns=["strategy", dim, "n", "n_settled",
                                     "win_rate", "roi", "clv_median",
                                     "avg_odds", "pnl"])

    df = bets_df.copy()
    settled_mask = df["status"].isin(["won", "lost"])

    def _agg(g: pd.DataFrame) -> pd.Series:
        s = g[settled_mask.loc[g.index]]
        n_settled = len(s)
        n_won = int((s["status"] == "won").sum()) if n_settled else 0
        staked = float(s["stake"].sum()) if n_settled else 0.0
        pnl = float((s["return_amt"].fillna(0) - s["stake"]).sum()) if n_settled else 0.0
        clv_vals = sorted(s["clv"].dropna().tolist())
        avg_odds = float(s["odds_taken"].mean()) if n_settled else 0.0
        return pd.Series({
            "n": len(g),
            "n_settled": n_settled,
            "win_rate": n_won / n_settled if n_settled else None,
            "roi": pnl / staked if staked else None,
            "clv_median": (clv_vals[len(clv_vals) // 2] if clv_vals else None),
            "avg_odds": avg_odds,
            "pnl": pnl,
        })

    result = df.groupby(["strategy", dim]).apply(
        _agg, include_groups=False).reset_index()
    return result.sort_values(["strategy", dim]).reset_index(drop=True)


st.header("B 线 · 推荐与台账 Recommendations & Ledger")
try:
    recs = recommendations()
    bets = paper_bets()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

# ── 时间范围筛选器 ──
anchor = anchor_from_series(recs["created_at"]) if not recs.empty else None
start_date, end_date, grain = time_range_filter(
    key="b2_recommendations",
    default_preset="30d",
    anchor_date=anchor,
)

# —— 推荐表 ——
st.markdown("#### 🎯 推荐 Recommendations")
if recs.empty:
    st.info("暂无推荐——比赛日 matchday run 写入 recommendations（spec §9.6）"
            " / No recommendations yet — written by matchday runs")
else:
    c1, c2 = st.columns(2)
    strategies = c1.multiselect("策略 strategy", sorted(recs["strategy"].unique()),
                                default=sorted(recs["strategy"].unique()))
    phases = c2.multiselect("相位 phase", sorted(recs["phase"].unique()),
                            default=sorted(recs["phase"].unique()))

    # 先按策略/相位筛，再按时间范围筛
    filtered_recs = recs[
        recs["strategy"].isin(strategies) & recs["phase"].isin(phases)
    ]
    filtered_recs = filter_by_date_range(filtered_recs, "created_at",
                                          start_date, end_date)
    st.dataframe(
        filtered_recs.rename(columns=COL_BILINGUAL),
        use_container_width=True,
        hide_index=True,
        height=min(520, max(240, len(filtered_recs) * 36 + 45)),
    )
    st.caption(f"当前范围共 {len(filtered_recs)} 条推荐（创建日口径，北京日）· 按创建时间倒序")

st.divider()

# —— 盈亏分解面板（P1）——
with st.expander("📊 盈亏分解 Breakdown（按市场/联赛/周期）", expanded=True):
    # 盈亏分解用 settled_at 的时间范围
    bets_settled_range = filter_by_date_range(bets, "settled_at",
                                              start_date, end_date)

    tab1, tab2, tab3 = st.tabs(["按市场 Market", "按联赛 League", "按周期 Period"])

    with tab1:
        df_mkt = _breakdown_by_dim_from_bets(bets_settled_range, "market")
        _render_breakdown_table(df_mkt, dim_label="market", dim_col="market")

    with tab2:
        df_league = _breakdown_by_dim_from_bets(bets_settled_range, "league")
        _render_breakdown_table(df_league, dim_label="league", dim_col="league")

    with tab3:
        _render_period_breakdown(bets_settled_range, grain)

# —— paper 注明细 ——
st.markdown("#### 💰 paper 注明细 Bet Ledger")
if bets.empty:
    st.info("暂无 paper 注——M3 起每个比赛日自动落注（spec §7.2）"
            " / No paper bets yet — auto-placed on matchdays")
else:
    c1 = st.columns(1)[0]
    basis = c1.selectbox("日期口径 Date basis",
                         ["开赛日 kickoff", "结算日 settled", "落注日 placed"],
                         help="按哪个时间字段做时间范围过滤")
    col = {"开赛日 kickoff": "kickoff_utc", "结算日 settled": "settled_at",
           "落注日 placed": "placed_at"}[basis]

    view = filter_by_date_range(bets, col, start_date, end_date)

    front = ["id", "status", "market", "home", "away", "odds_taken", "stake",
             "return_amt", "pnl", "clv", "closing_odds", "closing_source",
             "kickoff_utc", "settled_at", "placed_at", "strategy", "phase"]
    ordered = [c for c in front if c in view.columns] + \
              [c for c in view.columns if c not in front]

    st.dataframe(
        view[ordered].rename(columns=COL_BILINGUAL),
        use_container_width=True,
        hide_index=True,
        height=min(520, max(240, len(view) * 36 + 45)),
    )
    st.caption(
        f"当前范围共 {len(view)} 注（{basis}口径，北京日）。"
        "clv = 拿价/收盘价 − 1，正=买在收盘前更优价。收盘基准链（spec §7.3）："
        "Pinnacle 优先、缺失 fallback Betfair 交易所，基准列 source 记账实际"
        "所用。盈亏三态：won/lost=回报−注金、void=0（零损益）、pending=—"
        " / clv = odds_taken / closing − 1 (positive = beat the close); "
        "closing basis: Pinnacle first, Betfair exchange fallback"
    )
