"""B 线 · 推荐与台账：推荐全维度表 + 盈亏分解 + paper 注明细（设计 §3 页2，v2）。

v2 变更（2026-09-07）：新增「盈亏分解」折叠面板（P1）——市场/联赛/按日
三维度 tab，柱图 + 表格双视图。

中英并列标签 + 日期过滤（2026-09-04 增补）：推荐按创建日、注明细按
开赛/结算/落注日（口径可选）——「看过去某一天的收支」从台账进来。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from loaders import breakdown, paper_bets, recommendations
from queries import COL_BILINGUAL, by_date, date_choices


# —— 辅助函数（页内局部，不归 queries：纯 UI 层渲染）——
def _render_breakdown_table(df: pd.DataFrame, dim_label: str, dim_col: str):
    """market/league 维度的表格渲染：正 ROI 绿色、负 ROI 红色。"""
    if df.empty:
        st.info("暂无已结算数据 / No settled bets yet")
        return

    display = df.copy()
    # 百分比列格式化
    pct_cols = ["win_rate", "roi", "clv_median"]
    for c in pct_cols:
        if c in display.columns:
            display[c] = display[c].map(
                lambda x: "—" if x is None or (isinstance(x, float) and pd.isna(x))
                else f"{x:+.2%}")

    st.dataframe(display.rename(columns=COL_BILINGUAL),
                 use_container_width=True, hide_index=True)

    # 柱图：各策略在该维度上的 ROI 对比
    fig = go.Figure()
    for strat in sorted(df["strategy"].unique()):
        sub = df[df["strategy"] == strat]
        fig.add_trace(go.Bar(x=sub[dim_col], y=sub["roi"], name=strat,
                             text=sub["roi"].map(
                                 lambda x: "—" if x is None or pd.isna(x)
                                 else f"{x:+.1%}"),
                             textposition="outside"))
    fig.update_layout(barmode="group", height=360,
                      yaxis_title="ROI（已结算）",
                      yaxis_tickformat=".1%",
                      margin=dict(t=30, b=20))
    fig.add_hline(y=0, line_dash="dash", line_color="gray")
    st.plotly_chart(fig, use_container_width=True)
    st.caption("样本不足 300 注时，ROI/CLV 数字仅供观察，不构成显著性结论。")


def _render_daily_breakdown(df: pd.DataFrame):
    """settled_date 维度：柱图（每日 P&L）+ 表格（分轨 P&L + 累计）。"""
    if df.empty:
        st.info("暂无已结算数据 / No settled bets yet")
        return

    # 柱图：各策略每日 P&L（分组）
    fig = go.Figure()
    for strat in sorted(df["strategy"].unique()):
        sub = df[df["strategy"] == strat].sort_values("settled_date")
        fig.add_trace(go.Bar(x=sub["settled_date"], y=sub["pnl"], name=strat,
                             text=sub["pnl"].map(lambda x: f"{x:+.1f}"),
                             textposition="outside"))
    fig.update_layout(barmode="group", height=360,
                      yaxis_title="当日 P&L Daily P&L",
                      margin=dict(t=30, b=20))
    fig.add_hline(y=0, line_dash="dash", line_color="gray")
    st.plotly_chart(fig, use_container_width=True)

    # 表格：日期 → 每轨当日 P&L + 累计 P&L（宽表格式）
    strats = sorted(df["strategy"].unique())
    dates = sorted(df["settled_date"].unique())

    rows = []
    cum = {s: 0.0 for s in strats}
    for d in dates:
        row = {"date": d}
        for s in strats:
            sub = df[(df["settled_date"] == d) & (df["strategy"] == s)]
            daily_pnl = float(sub["pnl"].iloc[0]) if len(sub) else 0.0
            n_bets = int(sub["n_settled"].iloc[0]) if len(sub) else 0
            cum[s] += daily_pnl
            row[f"{s}_daily"] = daily_pnl
            row[f"{s}_n"] = n_bets
            row[f"{s}_cum"] = cum[s]
        rows.append(row)

    wide = pd.DataFrame(rows)
    display = pd.DataFrame({"日期 Date": wide["date"]})
    for s in strats:
        display[f"{s} 注数"] = wide[f"{s}_n"]
        display[f"{s} 当日P&L"] = wide[f"{s}_daily"].map(lambda x: f"{x:+.2f}")
        display[f"{s} 累计P&L"] = wide[f"{s}_cum"].map(lambda x: f"{x:+.2f}")

    st.dataframe(display, use_container_width=True, hide_index=True)
    st.caption("日期为北京日（spec §9.6 全项目口径）。样本不足 300 注时数字仅供观察。")


st.header("B 线 · 推荐与台账 Recommendations & Ledger")
try:
    recs = recommendations()
    bets = paper_bets()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

st.subheader("推荐 Recommendations")
if recs.empty:
    st.info("暂无推荐——比赛日 matchday run 写入 recommendations（spec §9.6）"
            " / No recommendations yet — written by matchday runs")
else:
    c1, c2, c3 = st.columns(3)
    strategies = c1.multiselect("策略 strategy", sorted(recs["strategy"].unique()),
                                default=sorted(recs["strategy"].unique()))
    phases = c2.multiselect("相位 phase", sorted(recs["phase"].unique()),
                            default=sorted(recs["phase"].unique()))
    day = c3.selectbox("日期（创建日）Date (created)",
                       ["全部 All", *date_choices(recs, "created_at")])
    view = by_date(
        recs[recs["strategy"].isin(strategies) & recs["phase"].isin(phases)],
        "created_at", day)
    st.dataframe(view.rename(columns=COL_BILINGUAL), use_container_width=True,
                 hide_index=True)

# —— v2 新增：盈亏分解面板（P1）——
with st.expander("盈亏分解 Breakdown（按市场/联赛/日）", expanded=True):
    tab1, tab2, tab3 = st.tabs(["按市场 Market", "按联赛 League", "按日 Daily"])

    with tab1:
        df_mkt = breakdown("market")
        _render_breakdown_table(df_mkt, dim_label="market", dim_col="market")

    with tab2:
        df_league = breakdown("league")
        _render_breakdown_table(df_league, dim_label="league", dim_col="league")

    with tab3:
        df_day = breakdown("settled_date")
        _render_daily_breakdown(df_day)

st.subheader("paper 注明细 Bet Ledger")
if bets.empty:
    st.info("暂无 paper 注——M3 起每个比赛日自动落注（spec §7.2）"
            " / No paper bets yet — auto-placed on matchdays")
else:
    c1, c2 = st.columns(2)
    basis = c1.selectbox("日期口径 Date basis",
                         ["开赛日 kickoff", "结算日 settled", "落注日 placed"])
    col = {"开赛日 kickoff": "kickoff_utc", "结算日 settled": "settled_at",
           "落注日 placed": "placed_at"}[basis]
    day = c2.selectbox("日期 Date", ["全部 All", *date_choices(bets, col)])
    view = by_date(bets, col, day)
    front = ["id", "status", "market", "home", "away", "odds_taken", "stake",
             "return_amt", "pnl", "clv", "closing_odds", "closing_source",
             "kickoff_utc", "settled_at", "placed_at", "strategy", "phase"]
    ordered = [c for c in front if c in view.columns] + \
              [c for c in view.columns if c not in front]
    st.dataframe(view[ordered].rename(columns=COL_BILINGUAL),
                 use_container_width=True, hide_index=True)
    st.caption("clv = 拿价/收盘价 − 1，正=买在收盘前更优价。收盘基准链（spec §7.3）："
               "Pinnacle 优先、缺失 fallback Betfair 交易所，基准列 source 记账实际"
               "所用。盈亏三态：won/lost=回报−注金、void=0（零损益）、pending=—"
               " / clv = odds_taken / closing − 1 (positive = beat the close); "
               "closing basis: Pinnacle first, Betfair exchange fallback")
