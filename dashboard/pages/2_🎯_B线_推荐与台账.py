"""B 线 · 推荐与台账：推荐全维度表 + paper 注明细（设计 §3 页2）。

中英并列标签 + 日期过滤（2026-09-04 增补）：推荐按创建日、注明细按
开赛/结算/落注日（口径可选）——「看过去某一天的收支」从台账进来。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from loaders import paper_bets, recommendations
from queries import COL_BILINGUAL, by_date, date_choices

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
    # 钱列前置（用户实测：盈亏排在 23 列最右端，屏幕内看不到）：盈亏/回报/
    # CLV/收盘紧跟识别列，模型诊断列（概率/edge/EV）殿后；映射外或新增列
    # 自动缀尾，不丢列。
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
