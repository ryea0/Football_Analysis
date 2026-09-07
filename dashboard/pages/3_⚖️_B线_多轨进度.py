"""B 线 · 多轨进度：§12.3/§12.7 预注册判据的可视化（设计 §3 页3）。

自动发现所有 strategy 轨：模型基线 / C 线人格 / nokb 对照 / C' 自反思对照……
新增轨零配置上线。分两块：
  1. 多轨全景：所有轨并排（注数/ROI/CLV）+ 累计 P&L 对比
  2. C 线家族对比：C 线 vs C' 线（两条进化线的效果差异）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import plotly.graph_objects as go
import streamlit as st

from loaders import multi_tracks

TARGET = 300  # §12.3：累计 ≥300 注（B 线 paper 判据）

st.header("B 线 · 多轨进度 Multi-Track Progress")
try:
    t = multi_tracks()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

if not t:
    st.info("暂无 paper 注数据 / No paper bets yet")
    st.stop()

# ------------------------------------------------------------ 多轨全景
st.subheader("多轨全景 All Tracks")

n_tracks = len(t)
cols = st.columns(min(n_tracks, 4))
labels = list(t.keys())
for col, name in zip(cols * ((n_tracks + 3) // 4), labels):
    tr = t[name]
    with col:
        st.markdown(f"**{name}**")
        st.caption(f"`{tr['strategy']}`")
        st.metric("注数 Bets", f"{tr['n']} / {TARGET}")
        st.progress(min(tr["n"] / TARGET, 1.0))
        st.metric("ROI（已结算）Settled",
                  "—" if tr["roi"] is None else f"{tr['roi']:+.1%}")
        st.metric("CLV 中位数 Median",
                  "—" if tr["clv_median"] is None
                  else f"{tr['clv_median']:+.2%}")

# 累计 P&L 对比图
if any(tr["cum"]["dates"] for tr in t.values()):
    fig = go.Figure()
    for name, tr in t.items():
        if tr["cum"]["dates"]:
            fig.add_trace(go.Scatter(x=tr["cum"]["dates"], y=tr["cum"]["pnl"],
                                     mode="lines+markers", name=name))
    fig.update_layout(height=360, margin=dict(t=30, b=20),
                      yaxis_title="累计 P&L（已结算）Cum. P&L",
                      legend=dict(orientation="h", y=-0.15))
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("无已结算注——累计 P&L 曲线待首个结算日落库后出现 / No settled bets yet")

# ------------------------------------------------------------ C 线家族对比
st.divider()
st.subheader("C 线家族对比 C-family Comparison")

kb_label = "模型 + 人格（C 线知识）"
self_label = "模型 + 人格（C' 自反思知识）"
nokb_label = "模型 + 人格（无知识库）"
baseline_label = "模型-only 基线"

kb = t.get(kb_label)
self_t = t.get(self_label)
nokb = t.get(nokb_label)
baseline = t.get(baseline_label)

if kb and self_t:
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("C 线 ROI",
                  "—" if kb["roi"] is None else f"{kb['roi']:+.1%}",
                  help="结构化 JSON 契约 + 三段式锚点知识 + 人审关卡")
    with c2:
        st.metric("C' 线 ROI",
                  "—" if self_t["roi"] is None else f"{self_t['roi']:+.1%}",
                  help="自由式球探笔记 + 时间线知识 + 自动落账（无人审）")
    with c3:
        diff = (None if kb["roi"] is None or self_t["roi"] is None
                else self_t["roi"] - kb["roi"])
        st.metric("差异 C' − C",
                  "—" if diff is None else f"{diff:+.1%}",
                  help="正值 = C' 自反思更好（样本量低时噪声主导）")

    # 两条线 P&L 叠加
    if kb["cum"]["dates"] or self_t["cum"]["dates"]:
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(x=kb["cum"]["dates"], y=kb["cum"]["pnl"],
                                  mode="lines+markers", name=kb_label))
        fig2.add_trace(go.Scatter(x=self_t["cum"]["dates"], y=self_t["cum"]["pnl"],
                                  mode="lines+markers", name=self_label))
        fig2.update_layout(height=300, margin=dict(t=20, b=20),
                           yaxis_title="累计 P&L Cum. P&L",
                           legend=dict(orientation="h", y=-0.2))
        st.plotly_chart(fig2, use_container_width=True)

    # 多轨对照（nokb + baseline）
    if nokb:
        nokb_roi = "—" if nokb["roi"] is None else f"{nokb['roi']:+.1%}"
        base_roi = ("—" if baseline is None or baseline["roi"] is None
                    else f"{baseline['roi']:+.1%}")
        st.caption(
            f"nokb 对照轨 ROI：{nokb_roi}"
            f"  ·  基线 model_only ROI：{base_roi}")
else:
    st.info("C 线或 C' 线暂无数据——待首批对应轨的 paper 注落库后出现。")

st.caption("§12.3 判据：累计 ≥300 注且 model_persona 轨 CLV>0 或 ROI 显著优于 "
           "model_only 才算胜出。§12.7 C 线家族对比为**探索性观察**，v1 不设统计"
           "门槛——样本量远未达标时这些数字仅供观察，不构成前向证据。")

# 重命名旧文件提示（保留向后兼容：新文件顶替页3，旧文件不再展示）
