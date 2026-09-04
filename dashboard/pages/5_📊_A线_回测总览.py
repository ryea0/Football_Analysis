"""A 线 · 回测总览：log-loss/Brier vs 去水收盘（设计 §3 页5）。

口径 = walk-forward 全样本复算（与 m2-verdict 同一判据，spec §8.2）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import plotly.express as px
import streamlit as st

from loaders import overview
from queries import COL_BILINGUAL

st.header("A 线 · 回测总览（walk-forward，历史回测）Backtest Overview")
try:
    full = overview()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()
if full["empty"]:
    st.info("backtest_predictions 为空——先运行 uv run fa backtest run --from 2019 --to 2025")
    st.stop()

leagues_all = sorted(full["by_league"])
seasons_all = sorted(full["by_season"])
c1, c2 = st.columns(2)
sel_l = c1.multiselect("联赛 Leagues", leagues_all, default=leagues_all)
sel_s = c2.multiselect("赛季 Seasons", seasons_all, default=seasons_all)
o = overview(sel_l or None, sel_s or None)   # 空选 → None → 不过滤，走缓存参数一致性
if o["empty"]:
    st.info("该过滤组合无预测行 / No rows for this filter")
    st.stop()

ov = o["overall"]
c1, c2, c3 = st.columns(3)
c1.metric("模型 log-loss Model", f"{ov['model_ll']:.5f}")
c2.metric("市场 log-loss（去水收盘）Market", f"{ov['market_ll']:.5f}")
c3.metric("劣化 Degradation", f"{ov['degradation_pct']:+.2f}%",
          delta=f"判据 Gate ≤ +1% GO（当前 {ov['verdict']}）",
          delta_color="inverse")
st.caption(f"n = {ov['n']} 场 · Brier：模型 {ov['model_brier']:.5f} / 市场 "
           f"{ov['market_brier']:.5f} · 口径同 spec §8.2 / docs/m2-verdict.md")

cross = pd.DataFrame(
    [{"league": k.split("|")[0], "season": int(k.split("|")[1]), **{
        "degradation_pct": v["degradation_pct"], "n": v["n"]}}
     for k, v in o["by_league_season"].items()])
piv = cross.pivot(index="league", columns="season", values="degradation_pct")
st.subheader("劣化（%）热图：联赛 × 赛季 Heatmap")
fig = px.imshow(piv, text_auto=".2f", aspect="auto",
                color_continuous_midpoint=0,
                title="model_ll/market_ll − 1（负=模型优 Negative=model better；判据线 Gate +1%）")
st.plotly_chart(fig, use_container_width=True)

st.subheader("分联赛 / 分赛季 By League / Season")
tab1, tab2 = st.tabs(["联赛 league", "赛季 season"])
tab1.dataframe(pd.DataFrame(o["by_league"]).T.drop(columns=["cal_home"])
                 .rename(columns=COL_BILINGUAL), use_container_width=True)
tab2.dataframe(pd.DataFrame(o["by_season"]).T.drop(columns=["cal_home"])
                 .rename(columns=COL_BILINGUAL), use_container_width=True)
st.caption("A 线为历史回测证据，与 B 线前向运营结论分账（spec §12.3），互不冒充。")
