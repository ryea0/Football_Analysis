"""A' 线 · 范式对比（DSH agent 当大脑 vs 确定性模型）。

口径 = 同场交集对比（线 P / A_base / A_enh / A_multi / A_debate / A_division
共六条线，在同一批场次上用同一判据——market 为各自子集的去水收盘）。

物理隔离：A' 线只消费导出信息集 JSON，永不进 B 线推荐/落注流（spec §12.5）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import plotly.express as px
import streamlit as st

from loaders import al_compare, al_divergence, al_runs, al_samples
from queries import COL_BILINGUAL

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

# ---- 小样本警告（诚实性约束，n<100 必须醒目）----
n_max = max((full.get(k, {}).get("n", 0) for k in
             ("A_base", "A_enh", "A_multi", "A_debate", "A_division")),
            default=0)
if n_max < 100:
    st.warning(
        "⚠️ 本页为链路验证样本（各线 n < 100），数字**无统计意义**，"
        "仅用于验证管线可用性。结论以 100 场以上的滚动报告为准。",
        icon="⚠️")

# ---- 过滤控件（联赛/赛季）----
# 从 backtest_predictions 取可用联赛/赛季；这里复用 compare 的全量 n
# 作为是否有数据的指示，过滤直接传给 al_compare
# 为避免额外查询，直接用 A_base 的场次来推断可用联赛——
# 实际上 compare_lines 已经跑过全量了，但没带 by_league 分解。
# 先放简单版：不过滤；后续可以加 by_league/by_season。
st.caption("当前为全量对比（所有有 agentline 预测的场次）。"
           "按联赛/赛季过滤待后续版本加入。")

cmp = full

# ---- 核心对照指标 ----
st.subheader("六线对照 Six-Line Comparison")

# 构造 DataFrame：线 / n / log-loss / Brier / 子集市场 ll / vs 市场 / ROI
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
# 市场行（基准）
rows.append({
    "线 Line": "市场 Market (Pinnacle)",
    "n": cmp["n"],
    "log-loss": f"{cmp['market']['ll']:.4f}",
    "Brier": "—",
    "vs 市场 vs mkt": "1.000×",
    "ROI": "—",
})
df = pd.DataFrame(rows)
st.dataframe(df, use_container_width=True, hide_index=True)

# 线解释
with st.expander("各线说明 Line descriptions"):
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

# ---- log-loss 柱状图 ----
st.subheader("log-loss 对比 Bar Chart")
bar_data = []
for k in ("P", "A_base", "A_enh", "A_multi", "A_debate", "A_division"):
    e = cmp.get(k, {})
    if e and e.get("n"):
        bar_data.append({"线 Line": k, "log-loss": e["model_ll"],
                         "n": e["n"]})
if bar_data:
    bar_data.append({"线 Line": "市场 Market",
                     "log-loss": cmp["market"]["ll"],
                     "n": cmp["n"]})
    fig = px.bar(pd.DataFrame(bar_data), x="线 Line", y="log-loss",
                 text_auto=".4f",
                 color="log-loss",
                 color_continuous_scale="RdBu_r",
                 title="各线 log-loss（越低越好 Lower is better）")
    fig.update_layout(showlegend=False)
    st.plotly_chart(fig, use_container_width=True)

# ---- 平注 ROI ----
st.subheader("平注 ROI Flat-Stake ROI")
roi_rows = []
for k in ("P", "A_base", "A_enh", "A_multi", "A_debate", "A_division"):
    v = cmp.get("roi", {}).get(k, {})
    if v and v.get("n"):
        roi_rows.append({"线 Line": k, "n": v["n"], "ROI": v["roi"]})
if roi_rows:
    roi_df = pd.DataFrame(roi_rows)
    fig_roi = px.bar(roi_df, x="线 Line", y="ROI",
                     text=roi_df["ROI"].map(lambda x: f"{x:+.1%}"),
                     color="ROI", color_continuous_scale="RdYlGn",
                     color_continuous_midpoint=0,
                     title="候选注平注 ROI（收盘价模拟）")
    fig_roi.add_hline(y=0, line_dash="dash", line_color="gray")
    st.plotly_chart(fig_roi, use_container_width=True)
    st.caption("候选注 = model 概率 vs 收盘去水的 edge > 0；成交价用收盘价（上界估计）")

# ---- A_multi 成员分歧 ----
st.subheader("A_multi · 成员分歧 Member Divergence")
div = al_divergence()
if div["n_matches"] == 0:
    st.info("A_multi 无成员数据（或成员数 < 2）")
else:
    c1, c2 = st.columns(2)
    c1.metric("有分歧数据的场次", f"{div['n_matches']} 场")
    c2.metric("平均最大对称 KL",
              f"{div['avg_kl']:.4f}" if div["avg_kl"] is not None else "—")
    if div["divergences"]:
        div_df = pd.DataFrame(div["divergences"])
        fig_div = px.histogram(div_df, x="max_sym_kl", nbins=20,
                               title="每场成员间最大对称 KL 分布")
        fig_div.update_layout(xaxis_title="max symmetric KL",
                              yaxis_title="场次")
        st.plotly_chart(fig_div, use_container_width=True)
    st.caption("分歧越大 = 成员间意见越不一致 = 可能是困难场（多 agent 总纲 §2）。"
               "成员为同一模型独立采样，一致性数值受温度采样相关性影响。")

# ---- A_debate 修订增益 ----
st.subheader("A_debate · 修订增益 Revision Gain")
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
        st.info(f"无可比对 ρ（n_rho={deb.get('n_rho', 0)}）")
    st.caption("允许结论为「修订无增益」——不做单边解读。")

# ---- A_division 质询分层 ----
st.subheader("A_division · 质询分层 Interrogation Stratification")
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
st.subheader("运行台账 Run Log")
runs_list = al_runs()
if not runs_list:
    st.info("暂无运行记录")
else:
    runs_df = pd.DataFrame(runs_list)
    # 只展示关键列
    show_cols = ["id", "line", "n_ok", "n_parse_fail", "n_timeout",
                 "n_error", "started_at", "finished_at"]
    # 只保留存在的列
    show_cols = [c for c in show_cols if c in runs_df.columns]
    st.dataframe(runs_df[show_cols].rename(columns=COL_BILINGUAL),
                 use_container_width=True, hide_index=True)

# ---- 示例比赛 ----
st.subheader("示例比赛 · agent 推理 Sample Predictions")
samples = al_samples(limit=3)
if not samples:
    st.info("暂无 A_base 样本")
else:
    for s in samples:
        with st.expander(
            f"{s['league']} {s['season']} · {s['home']} vs {s['away']}"
            f" · {s['date']}"):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("主胜 H", f"{s['p_home']:.3f}")
            c2.metric("平 D", f"{s['p_draw']:.3f}")
            c3.metric("客胜 A", f"{s['p_away']:.3f}")
            c4.metric("大2.5 O2.5",
                      f"{s['p_over25']:.3f}" if s["p_over25"] else "—")
            st.markdown(f"**置信度 Confidence:** {s['confidence']}"
                        if s["confidence"] else "**置信度:** —")
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
    "无 DB 写权，永不进入 B 线推荐/落注流（spec §12.5）。")
