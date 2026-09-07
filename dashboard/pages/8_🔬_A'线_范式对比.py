"""A' 线 · 范式对比（DSH agent 当大脑 vs 确定性模型）。

v2（2026-09-07）：顶部统一时间范围筛选器 + 联赛/赛季过滤。

口径 = 同场交集对比（线 P / A_base / A_enh / A_multi / A_debate / A_division
共六条线，在同一批场次上用同一判据——market 为各自子集的去水收盘）。

物理隔离：A' 线只消费导出信息集 JSON，永不进 B 线推荐/落注流（spec §12.5）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from components.time_filter import time_range_filter
from loaders import al_compare, al_divergence, al_runs, al_samples, overview
from queries import COL_BILINGUAL
from time_utils import filter_by_date_range, to_bj_dates

PALETTE = {
    "primary": "#2563eb",
    "good": "#059669",
    "bad": "#dc2626",
    "warn": "#d97706",
    "muted": "#6b7280",
    "grid": "#e5e7eb",
}

LINE_COLORS = {
    "P": "#6b7280",
    "A_base": "#2563eb",
    "A_enh": "#059669",
    "A_multi": "#8b5cf6",
    "A_debate": "#d97706",
    "A_division": "#dc2626",
    "市场 Market": "#374151",
    "市场 Market (Pinnacle)": "#374151",
}


def _chart_template(fig: go.Figure, height: int = 360) -> go.Figure:
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


st.header("A' 线 · 范式对比（DSH agent）Paradigm Comparison")
st.caption("同场交集对比 · agentline_predictions 只读 · 永不进 B 线推荐流（spec §12.5）")

try:
    full = al_compare()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()
if full["empty"]:
    st.info("agentline_predictions 为空——先运行 `fa agentline export` + `fa agentline run --line A_base`")
    st.stop()

# ---- 时间范围筛选器 ----
# 锚点用 A 线最新赛季 6 月末（同 A 线页的口径）
try:
    a_full = overview()
    seasons_all_a = sorted(a_full.get("by_season", {}).keys())
    leagues_all_a = sorted(a_full.get("by_league", {}).keys())
except Exception:
    seasons_all_a = []
    leagues_all_a = []

last_season = max(int(s) for s in seasons_all_a) if seasons_all_a else 2025
anchor_date = pd.Timestamp(f"{last_season}-06-30").date()

start_date, end_date, grain = time_range_filter(
    key="a8_agentline",
    default_preset="all",
    anchor_date=anchor_date,
)

date_from_str = start_date.isoformat() if start_date.year > 2000 else None
date_to_str = end_date.isoformat() if end_date.year < 2090 else None

# ---- 联赛 / 赛季筛选 ----
c1, c2 = st.columns(2)
sel_l = c1.multiselect("联赛 Leagues", leagues_all_a, default=leagues_all_a)
sel_s = c2.multiselect("赛季 Seasons", seasons_all_a, default=seasons_all_a)

cmp = al_compare(sel_l or None, sel_s or None, date_from_str, date_to_str)
if cmp["empty"]:
    st.info("当前筛选条件下无数据——放宽时间范围或联赛/赛季。")
    st.stop()

# ---- 核心对照指标 ----
st.markdown("#### 📊 六线对照 Six-Line Comparison")

# 构造 DataFrame
rows = []
for k in ("P", "A_base", "A_enh", "A_multi", "A_debate", "A_division"):
    e = cmp.get(k, {})
    if not e or not e.get("n"):
        rows.append({"线 Line": k, "n": 0, "log-loss": "—", "Brier": "—",
                     "vs 市场 vs mkt": "—", "ROI": "—"})
        continue
    roi = cmp.get("roi", {}).get(k, {})
    roi_s = f"{roi['roi']:+.1%}" if roi.get("n") else "无候选注"
    rows.append({
        "线 Line": k,
        "n": e["n"],
        "log-loss": f"{e['model_ll']:.4f}",
        "Brier": f"{e['model_brier']:.4f}",
        "vs 市场 vs mkt": f"{e['ratio']:.3f}×",
        "ROI": roi_s,
    })
rows.append({
    "线 Line": "市场 Market (Pinnacle)",
    "n": cmp["n"],
    "log-loss": f"{cmp['market']['ll']:.4f}",
    "Brier": "—",
    "vs 市场 vs mkt": "1.000×",
    "ROI": "—",
})
df = pd.DataFrame(rows)
st.dataframe(
    df,
    use_container_width=True,
    hide_index=True,
    height=min(360, len(df) * 38 + 40),
)

# 线解释
with st.expander("📖 各线说明 Line descriptions"):
    st.markdown("""
- **P**：A 线确定性模型（Dixon-Coles + walk-forward），基线对照
- **A_base**：DSH agent 基线层（纯读信息集，无外部检索）
- **A_enh**：DSH agent 增强层（信息集 + web 检索）
  - ⚠️ 历史回放检索可能受赛后信息泄漏污染，结论不与 A_base 混排
- **A_multi**：集成投票制（N 个独立成员 + 分量中位数聚合）
- **A_debate**：辩论修订制（生成者-批评者-修订链，≤2轮）
- **A_division**：角色分工制（考古官-预测者-质询官，三跳）
- **市场**：Pinnacle 收盘去水，所有线的共同基准
""")

st.divider()

# ---- log-loss 柱状图 ----
st.markdown("#### 📊 log-loss 对比 Bar Chart（越低越好）")
bar_data = []
for k in ("P", "A_base", "A_enh", "A_multi", "A_debate", "A_division"):
    e = cmp.get(k, {})
    if e and e.get("n"):
        bar_data.append({"线 Line": k, "log-loss": e["model_ll"], "n": e["n"]})
if bar_data:
    bar_data.append({"线 Line": "市场 Market",
                     "log-loss": cmp["market"]["ll"],
                     "n": cmp["n"]})
    bar_df = pd.DataFrame(bar_data)
    # 用固定颜色映射
    fig = go.Figure()
    for _, row in bar_df.iterrows():
        name = row["线 Line"]
        color = LINE_COLORS.get(name, PALETTE["primary"])
        fig.add_trace(go.Bar(
            x=[name], y=[row["log-loss"]],
            name=name,
            marker_color=color,
            text=f"{row['log-loss']:.4f}",
            textposition="outside",
            textfont=dict(size=11),
            width=0.6,
            hovertemplate=f"{name}<br>log-loss: %{{y:.4f}}<br>n: {row['n']}<extra></extra>",
        ))
    fig.update_layout(
        showlegend=False,
        barmode="overlay",
        yaxis_title="log-loss",
    )
    _chart_template(fig, height=360)
    st.plotly_chart(fig, use_container_width=True, config=dict(displayModeBar=False))

# ---- 平注 ROI ----
st.markdown("#### 💰 平注 ROI Flat-Stake ROI")
roi_rows = []
for k in ("P", "A_base", "A_enh", "A_multi", "A_debate", "A_division"):
    v = cmp.get("roi", {}).get(k, {})
    if v and v.get("n"):
        roi_rows.append({"线 Line": k, "n": v["n"], "ROI": v["roi"]})
if roi_rows:
    roi_df = pd.DataFrame(roi_rows)
    fig_roi = go.Figure()
    for _, row in roi_df.iterrows():
        name = row["线 Line"]
        color = LINE_COLORS.get(name, PALETTE["primary"])
        fig_roi.add_trace(go.Bar(
            x=[name], y=[row["ROI"]],
            name=name,
            marker_color=color,
            text=f"{row['ROI']:+.1%}",
            textposition="outside",
            textfont=dict(size=11),
            width=0.6,
            hovertemplate=f"{name}<br>ROI: %{{y:+.1%}}<br>n: {row['n']}<extra></extra>",
        ))
    fig_roi.add_hline(y=0, line_dash="dash", line_color=PALETTE["muted"], line_width=1)
    fig_roi.update_layout(
        showlegend=False,
        yaxis_title="ROI",
        yaxis_tickformat=".1%",
    )
    _chart_template(fig_roi, height=360)
    st.plotly_chart(fig_roi, use_container_width=True, config=dict(displayModeBar=False))
    st.caption("候选注 = model 概率 vs 收盘去水的 edge > 0；成交价用收盘价（上界估计）")

st.divider()

# ---- A_multi 成员分歧 ----
st.markdown("#### 🔀 A_multi · 成员分歧 Member Divergence")
div = al_divergence(sel_l or None, sel_s or None, date_from_str, date_to_str)
if div["n_matches"] == 0:
    st.info("A_multi 无成员数据（或成员数 < 2）")
else:
    c1, c2 = st.columns(2)
    c1.metric("有分歧数据的场次", f"{div['n_matches']} 场")
    c2.metric("平均最大对称 KL",
              f"{div['avg_kl']:.4f}" if div["avg_kl"] is not None else "—",
              help="成员间意见分歧的平均程度")
    if div["divergences"]:
        div_df = pd.DataFrame(div["divergences"])
        fig_div = px.histogram(
            div_df, x="max_sym_kl", nbins=20,
            title="每场成员间最大对称 KL 分布",
            color_discrete_sequence=[PALETTE["primary"]],
        )
        fig_div.update_layout(
            xaxis_title="max symmetric KL",
            yaxis_title="场次",
            showlegend=False,
        )
        _chart_template(fig_div, height=320)
        st.plotly_chart(fig_div, use_container_width=True, config=dict(displayModeBar=False))
    st.caption("分歧越大 = 成员间意见越不一致 = 可能是困难场（多 agent 总纲 §2）。"
               "成员为同一模型独立采样，一致性数值受温度采样相关性影响。")

# ---- A_debate 修订增益 ----
st.divider()
st.markdown("#### 🗣️ A_debate · 修订增益 Revision Gain")
deb = cmp.get("debate", {})
if not deb.get("n"):
    st.info("A_debate 暂无数据")
else:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("可评场次", f"{deb['n']}")
    c2.metric("v0 log-loss", f"{deb['v0_ll']:.4f}")
    c3.metric("终版 log-loss", f"{deb['final_ll']:.4f}")
    delta = deb["final_ll"] - deb["v0_ll"]
    c4.metric("修订增益", f"{delta:+.4f}",
              delta="负=改进 negative=better",
              delta_color="inverse")
    if deb.get("rho") is not None:
        st.metric("攻击-修订相关性 ρ", f"{deb['rho']:+.2f}（n={deb['n_rho']}）",
                  help="高攻击低修订=固执 / 低攻击高修订=无主见，两向都是实测信号")
    elif deb.get("n_rho", 0) >= 3:
        st.info("ρ = MWU 未定义（两组 log-loss 全平手）")
    else:
        st.caption(f"无可比对 ρ（n_rho={deb.get('n_rho', 0)}）")
    st.caption("允许结论为「修订无增益」——不做单边解读。")

# ---- A_division 质询分层 ----
st.divider()
st.markdown("#### 🔍 A_division · 质询分层 Interrogation Stratification")
dv = cmp.get("division", {})
if not dv.get("n_flagged") and not dv.get("n_unflagged"):
    st.info("A_division 暂无数据")
else:
    # 二分标签视角
    st.markdown("**二分标签（五标签任一命中）**")
    c1, c2, c3 = st.columns(3)
    c1.metric("有标签", f"{dv['n_flagged']} 场",
              f"{dv['ll_flagged']:.4f} ll" if "ll_flagged" in dv else "—")
    c2.metric("无标签", f"{dv['n_unflagged']} 场",
              f"{dv['ll_unflagged']:.4f} ll" if "ll_unflagged" in dv else "—")
    if "mwu_p" in dv:
        if dv["mwu_p"] is not None:
            c3.metric("MWU 双侧 p", f"{dv['mwu_p']:.3f}")
        else:
            c3.metric("MWU 双侧 p", "未定义",
                      help="两组 log-loss 全平手，MWU 未定义")
    else:
        c3.metric("MWU 双侧 p", "n<5/层不报",
                  help="小样本诚实，不出 p 值")

    # severity 加权视角
    if dv.get("n_high") or dv.get("n_low") or dv.get("n_no_attack"):
        st.markdown("**severity 加权（高烈度 ≥0.5 / 低烈度 <0.5 / 无攻击）**")
        c1, c2, c3 = st.columns(3)
        c1.metric("高烈度", f"{dv['n_high']} 场",
                  f"{dv['ll_high']:.4f} ll" if "ll_high" in dv else "—")
        c2.metric("低烈度", f"{dv['n_low']} 场",
                  f"{dv['ll_low']:.4f} ll" if "ll_low" in dv else "—")
        c3.metric("无攻击", f"{dv['n_no_attack']} 场")
        if "mwu_p_sev" in dv:
            if dv["mwu_p_sev"] is not None:
                st.metric("MWU 双侧 p（高 vs 低）", f"{dv['mwu_p_sev']:.3f}")
            else:
                st.info("MWU 未定义（两组 log-loss 全平手）")
        else:
            st.caption("MWU 双侧 p：n<5/层不报（小样本诚实）")
        st.caption("阈值 0.5 = severity 契约 [0,1] 的中点，预注册，不得事后调割点。")

    st.caption("允许结论为「质询无信息量」——不做单边解读。"
               "质询只落 flag 不改数（管线失败≠质询有话可说）。")

# ---- 运行台账 ----
st.divider()
st.markdown("#### 📋 运行台账 Run Log")
runs_list = al_runs()
if not runs_list:
    st.info("暂无运行记录")
else:
    runs_df = pd.DataFrame(runs_list)
    # 按时间范围过滤（started_at 的北京日）
    runs_df = filter_by_date_range(runs_df, "started_at", start_date, end_date)
    if runs_df.empty:
        st.info("当前时间范围内无运行记录")
    else:
        show_cols = ["id", "line", "n_ok", "n_parse_fail", "n_timeout",
                     "n_error", "started_at", "finished_at"]
        show_cols = [c for c in show_cols if c in runs_df.columns]
        st.dataframe(
            runs_df[show_cols].rename(columns=COL_BILINGUAL),
            use_container_width=True,
            hide_index=True,
            height=min(420, max(200, len(runs_df) * 36 + 40)),
        )

# ---- 示例比赛 ----
st.divider()
st.markdown("#### 🎯 示例比赛 · agent 推理 Sample Predictions")
samples = al_samples(sel_l or None, sel_s or None, date_from_str, date_to_str, limit=3)
if not samples:
    st.info("暂无 A_base 样本")
else:
    for s in samples:
        with st.expander(
            f"{s['league']} {s['season']} · {s['home']} vs {s['away']}"
            f" · {s['date']}"
        ):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("主胜 H", f"{s['p_home']:.3f}")
            c2.metric("平 D", f"{s['p_draw']:.3f}")
            c3.metric("客胜 A", f"{s['p_away']:.3f}")
            c4.metric("大2.5 O2.5",
                      f"{s['p_over25']:.3f}" if s['p_over25'] else "—")
            st.markdown(f"**置信度 Confidence:** {s['confidence']}"
                        if s['confidence'] else "**置信度:** —")
            st.markdown(f"**模型 Model:** {s.get('model', '—')} · "
                        f"**耗时 Duration:** {s.get('duration_s', '—'):.1f}s"
                        if isinstance(s.get('duration_s'), (int, float))
                        else f"**模型 Model:** {s.get('model', '—')}")
            if s.get("reasoning_digest"):
                st.markdown("**推理摘要 Reasoning:**")
                st.write(s["reasoning_digest"])
            else:
                st.caption("无推理摘要")

# ---- 页脚说明 ----
st.divider()
st.caption(
    "各线比值在其自身 n 场子集内计算，跨线直比无效；"
    "n<100 的行为链路验证样本，数字无统计意义。\n\n"
    "物理隔离：agentline 只消费 `data/agentline/*.json` 信息集，"
    "无 DB 写权，永不进入 B 线推荐/落注流（spec §12.5）。"
)
