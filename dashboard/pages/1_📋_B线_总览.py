"""B 线 · 总览：bankroll / P&L / 未结注 / 额度水位 / 三轨汇总 / 在途注（设计 §3 页1，v2）。

v2 变更（2026-09-07）：新增三轨汇总条（P0）+ 在途注列表（P1）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # dashboard/ 入 path

import plotly.graph_objects as go
import streamlit as st

from loaders import pending_bets, runs, summary
from queries import COL_BILINGUAL

# 三轨显示标签
STRAT_LABELS = {
    "model_only": "A 轨 model_only",
    "model_persona": "B 轨 model_persona",
    "model_persona_nokb": "C 轨 nokb（对照）",
}

st.header("B 线 · 总览（paper 运营）Overview")
try:
    s = summary()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

# —— 第一行：合计 metric（保留 v1 布局）——
c1, c2, c3, c4 = st.columns(4)
c1.metric("bankroll 余额", "—" if s["bankroll"] is None else f"{s['bankroll']:.0f}")
c2.metric("累计 P&L（已结算）Cum. P&L", f"{s['pnl']:+.2f}")
c3.metric("未结注 Pending", s["n_pending"])
c4.metric("Odds API 剩余额度 Quota",
          "—" if s["quota_remaining"] is None else int(s["quota_remaining"]))

pts = [(r["started_at"], r["credits_after"]) for r in s["quota_series"]
       if r["credits_after"] is not None]
if pts:
    fig = go.Figure(go.Scatter(x=[p[0] for p in pts], y=[p[1] for p in pts],
                               mode="lines+markers", name="credits"))
    fig.add_hline(y=100, line_dash="dash",
                  annotation_text="降频阈值 Throttle 100（spec §3.4）")
    fig.update_layout(height=300, margin=dict(t=30, b=20))
    st.plotly_chart(fig, use_container_width=True)
else:
    st.caption("暂无 run 记录——额度曲线待首个 run 落库后出现 / No runs yet")

# —— v2 新增：三轨汇总条（P0）——
st.subheader("三轨汇总 Track Summary")
by_strat = s["by_strategy"]
strats = list(by_strat.keys())  # 已按 STRATEGIES 排序
cols = st.columns(len(strats))
for col, strat in zip(cols, strats):
    tr = by_strat[strat]
    label = STRAT_LABELS.get(strat, strat)
    with col:
        st.markdown(f"**{label}**")
        c1, c2 = st.columns(2)
        c1.metric("Bankroll",
                  "—" if tr["bankroll"] is None else f"{tr['bankroll']:.0f}")
        c2.metric("已结注 Settled", tr["n_settled"])
        c3, c4 = st.columns(2)
        c3.metric("胜率 Win Rate",
                  "—" if tr["win_rate"] is None else f"{tr['win_rate']:.1%}")
        c4.metric("ROI（已结）",
                  "—" if tr["roi"] is None else f"{tr['roi']:+.1%}")
        c5, c6 = st.columns(2)
        c5.metric("CLV 中位",
                  "—" if tr["clv_median"] is None else f"{tr['clv_median']:+.2%}")
        c6.metric("在途 Pending", tr["n_pending"])
        if tr["n_settled"] < 300:
            st.caption("⚠️ 样本不足 300 注，数字仅供观察")
        st.divider()

# —— v2 新增：在途注列表（P1）——
with st.expander(f"在途注 Pending Bets（{s['n_pending']}）",
                 expanded=s["n_pending"] > 0):
    pend = pending_bets()
    if pend.empty:
        st.info("当前无在途注 / No pending bets")
    else:
        # 开赛时间转北京时显示（原列是 UTC ISO 串）
        pend["kickoff_bj"] = pend["kickoff_utc"].map(
            lambda x: pd_bj_time(x) if x else "—")
        front = ["kickoff_bj", "league", "home", "away", "market", "strategy",
                 "odds_taken", "stake"]
        ordered = [c for c in front if c in pend.columns] + \
                  [c for c in pend.columns if c not in front]
        display_cols = [c for c in ordered if c not in ("id", "placed_at")]
        st.dataframe(pend[display_cols].rename(columns=COL_BILINGUAL),
                     use_container_width=True, hide_index=True)

st.subheader("最近 run Recent Runs")
df = runs().head(5)
if df.empty:
    st.caption("暂无 run 记录 / No runs yet")
st.dataframe(df.rename(columns=COL_BILINGUAL), use_container_width=True,
             hide_index=True)


def pd_bj_time(utc_str: str) -> str:
    """UTC ISO 串 → 北京时字符串（YYYY-MM-DD HH:MM）。"""
    import pandas as pd
    try:
        dt = pd.to_datetime(utc_str, utc=True)
        from datetime import timedelta, timezone
        return dt.tz_convert(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
    except Exception:
        return str(utc_str)
