from pathlib import Path

from fa.backtest.metrics import by_group, evaluate
from fa.backtest.simulate import candidates, simulate_flat, simulate_kelly


def render_report(rows: list[dict], out_path: Path) -> dict:
    ev = evaluate(rows)
    by_lg, by_sn = by_group(rows, "league"), by_group(rows, "season")
    flat = simulate_flat(candidates(rows))
    kel = simulate_kelly(candidates(rows))
    lines = ["# M2 回测报告", "",
             f"- 样本：{ev['n']} 场",
             f"- 模型 log-loss：{ev['model_ll']:.5f}；市场（去水收盘）：{ev['market_ll']:.5f}",
             f"- 劣化：{ev['degradation_pct']:+.2f}%（判据：≤ +1.00% 即 GO）",
             f"- Brier：模型 {ev['model_brier']:.5f} / 市场 {ev['market_brier']:.5f}",
             "",
             f"## 判决：{'✅ GO' if ev['verdict'] == 'GO' else '⛔ NO-GO'}", "",
             "## 校准（p_home 十分位）", "",
             "| 区间 | n | 平均预测 | 实际主场胜率 |", "|---|---|---|---|"]
    for b in ev["cal_home"]:
        lines.append(f"| {b['lo']:.1f}–{b['hi']:.1f} | {b['n']} | "
                     f"{b['avg_p']:.3f} | {b['emp']:.3f} |")
    lines += ["", "## 分联赛", "", "| 联赛 | n | 劣化% | 判决 |", "|---|---|---|---|"]
    for lg, e in by_lg.items():
        lines.append(f"| {lg} | {e['n']} | {e['degradation_pct']:+.2f}% "
                     f"| {e['verdict']} |")
    lines += ["", "## 分赛季", "", "| 赛季 | n | 劣化% | 判决 |", "|---|---|---|---|"]
    for sn, e in by_sn.items():
        lines.append(f"| {sn} | {e['n']} | {e['degradation_pct']:+.2f}% "
                     f"| {e['verdict']} |")
    lines += ["", "## 模拟盘（收盘价成交，保守）", "",
              f"- 平注：{flat['n']} 注，ROI {flat['roi']:+.1%}（P&L {flat['pnl']:+.1f} 单位）",
              f"- ¼ Kelly（本金 1000，单注 ≤2%）：终值 {kel['final_bankroll']:.1f}，"
              f"ROI {kel['roi']:+.1%}，最大回撤 {kel['max_drawdown_pct']:.1%}", ""]
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return ev
