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
st.markdown("#### 🛤️ 多轨全景 All Tracks")

n_tracks = len(t)
labels = list(t.keys())

# 每行最多 4 轨，多行自适应
n_per_row = min(n_tracks, 4)
for row_start in range(0, n_tracks, n_per_row):
    row_labels = labels[row_start:row_start + n_per_row]
    cols = st.columns(len(row_labels))
    for col, name in zip(cols, row_labels):
        tr = t[name]
        strat = tr["strategy"]
        color = STRAT_COLORS.get(strat, PALETTE["primary"])
        progress = min(tr["n"] / TARGET, 1.0)
        with col:
            # 轨标题 + 色块
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:2px;">'
                f'<div style="width:4px;height:20px;background:{color};border-radius:2px;"></div>'
                f'<span style="font-weight:600;font-size:0.95rem;">{name}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
            st.caption(f"`{strat}`")

            # 进度条（自定义样式：用 st.progress + caption 说明）
            st.markdown(
                f'<div style="font-size:0.78rem;margin-bottom:4px;">'
                f'<span style="font-weight:600;">{tr["n"]}</span> / {TARGET} 注'
                f' <span style="float:right;color:{PALETTE["muted"]};">{progress:.0%}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
            # 自定义进度条（HTML 版，颜色与轨匹配）
            st.markdown(
                f'<div style="width:100%;height:6px;background:#e5e7eb;border-radius:3px;'
                f'margin-bottom:0.75rem;">'
                f'<div style="width:{progress * 100}%;height:100%;background:{color};'
                f'border-radius:3px;transition:width 0.3s;"></div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            c1, c2 = st.columns(2)
            c1.metric("ROI（已结）",
                      "—" if tr["roi"] is None else f"{tr['roi']:+.1%}",
                      delta_color="normal" if (tr["roi"] or 0) >= 0 else "inverse")
            c2.metric("CLV 中位",
                      "—" if tr["clv_median"] is None else f"{tr['clv_median']:+.2%}",
                      delta_color="normal" if (tr["clv_median"] or 0) >= 0 else "inverse")

# 累计 P&L 对比图
if any(tr["cum"]["dates"] for tr in t.values()):
    st.markdown("#### 📈 累计 P&L 对比 Cumulative P&L")
    fig = go.Figure()
    for name, tr in t.items():
        if tr["cum"]["dates"]:
            color = STRAT_COLORS.get(tr["strategy"], PALETTE["primary"])
            fig.add_trace(go.Scatter(
                x=tr["cum"]["dates"], y=tr["cum"]["pnl"],
                mode="lines+markers",
                name=name,
                line=dict(color=color, width=2),
                marker=dict(size=5, color=color),
                hovertemplate=f"{name}<br>%{{x}}<br>P&L: %{{y:+.2f}}<extra></extra>",
            ))
    fig.update_layout(yaxis_title="累计 P&L（已结算）Cum. P&L")
    fig.add_hline(y=0, line_dash="dash", line_color=PALETTE["muted"], line_width=1)
    _chart_template(fig, height=360)
    st.plotly_chart(fig, use_container_width=True, config=dict(displayModeBar=False))
else:
    st.info("无已结算注——累计 P&L 曲线待首个结算日落库后出现 / No settled bets yet")

st.divider()

# ------------------------------------------------------------ C 线家族对比
st.markdown("#### 🔬 C 线家族对比 C-family Comparison")

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
                  help="结构化 JSON 契约 + 三段式锚点知识 + 人审关卡",
                  delta_color="normal" if (kb["roi"] or 0) >= 0 else "inverse")
    with c2:
        st.metric("C' 线 ROI",
                  "—" if self_t["roi"] is None else f"{self_t['roi']:+.1%}",
                  help="自由式球探笔记 + 时间线知识 + 自动落账（无人审）",
                  delta_color="normal" if (self_t["roi"] or 0) >= 0 else "inverse")
    with c3:
        diff = (None if kb["roi"] is None or self_t["roi"] is None
                else self_t["roi"] - kb["roi"])
        st.metric("差异 C' − C",
                  "—" if diff is None else f"{diff:+.1%}",
                  help="正值 = C' 自反思更好（样本量低时噪声主导）",
                  delta_color="normal" if (diff or 0) >= 0 else "inverse")

    # 两条线 P&L 叠加
    if kb["cum"]["dates"] or self_t["cum"]["dates"]:
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(
            x=kb["cum"]["dates"], y=kb["cum"]["pnl"],
            mode="lines+markers", name=kb_label,
            line=dict(color=STRAT_COLORS["model_persona"], width=2),
            marker=dict(size=5),
        ))
        fig2.add_trace(go.Scatter(
            x=self_t["cum"]["dates"], y=self_t["cum"]["pnl"],
            mode="lines+markers", name=self_label,
            line=dict(color=STRAT_COLORS["model_persona_kb_self"], width=2),
            marker=dict(size=5),
        ))
        fig2.update_layout(yaxis_title="累计 P&L Cum. P&L")
        fig2.add_hline(y=0, line_dash="dash", line_color=PALETTE["muted"], line_width=1)
        _chart_template(fig2, height=300)
        st.plotly_chart(fig2, use_container_width=True, config=dict(displayModeBar=False))

    # 多轨对照（nokb + baseline）
    if nokb or baseline:
        parts = []
        if nokb:
            nokb_roi = "—" if nokb["roi"] is None else f"{nokb['roi']:+.1%}"
            parts.append(f"nokb 对照轨 ROI：**{nokb_roi}**")
        if baseline:
            base_roi = ("—" if baseline["roi"] is None
                        else f"{baseline['roi']:+.1%}")
            parts.append(f"基线 model_only ROI：**{base_roi}**")
        st.caption("  ·  ".join(parts))
else:
    st.info("C 线或 C' 线暂无数据——待首批对应轨的 paper 注落库后出现。")

st.caption(
    "§12.3 判据：累计 ≥300 注且 model_persona 轨 CLV>0 或 ROI 显著优于 "
    "model_only 才算胜出。§12.7 C 线家族对比为**探索性观察**，v1 不设统计"
    "门槛——样本量远未达标时这些数字仅供观察，不构成前向证据。"
)

# 重命名旧文件提示（保留向后兼容：新文件顶替页3，旧文件不再展示）
