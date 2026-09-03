"""B 线 · 推荐与台账：推荐全维度表 + paper 注明细（设计 §3 页2）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from loaders import paper_bets, recommendations

st.header("B 线 · 推荐与台账")
try:
    recs = recommendations()
    bets = paper_bets()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

st.subheader("推荐")
if recs.empty:
    st.info("暂无推荐——比赛日 matchday run 写入 recommendations（spec §9.6）")
else:
    c1, c2 = st.columns(2)
    strategies = c1.multiselect("strategy", sorted(recs["strategy"].unique()),
                                default=sorted(recs["strategy"].unique()))
    phases = c2.multiselect("phase", sorted(recs["phase"].unique()),
                            default=sorted(recs["phase"].unique()))
    view = recs[recs["strategy"].isin(strategies) & recs["phase"].isin(phases)]
    st.dataframe(view, use_container_width=True, hide_index=True)

st.subheader("paper 注明细")
if bets.empty:
    st.info("暂无 paper 注——M3 起每个比赛日自动落注（spec §7.2）")
else:
    st.dataframe(bets, use_container_width=True, hide_index=True)
    st.caption("clv = odds_taken / Pinnacle 收盘价 − 1，正=买在收盘前更优价（spec §7.3）")
