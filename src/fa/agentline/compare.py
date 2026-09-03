"""三线对比评测（设计 §8）：线 P / A_base / A_enh / 市场，同一判据。

行适配原则：agentline 的 p_* 覆盖 bp 行拷贝，mkt_*（Pinnacle 收盘去水）、
odds_*、outcome、total_goals 原样保留——evaluate 与 candidates 的入参
schema 完全复用，评测代码零改动（市场是所有评估的对照线，spec §8.2）。
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fa.backtest.metrics import evaluate
from fa.backtest.simulate import candidates, simulate_flat


def merge_rows(bp_rows: list[dict], agent_rows: list[dict]) -> list[dict]:
    by_mid = {r["match_id"]: r for r in agent_rows}
    out = []
    for bp in bp_rows:
        ag = by_mid.get(bp["match_id"])
        if ag is None:
            continue
        r = dict(bp)
        for k in ("p_home", "p_draw", "p_away", "p_over25"):
            r[k] = ag[k]
        out.append(r)
    return out


def _fetch_agent(conn, line, leagues, seasons) -> list[dict]:
    sql = ("SELECT * FROM agentline_predictions ap"
           " JOIN backtest_predictions bp ON bp.match_id = ap.match_id"
           " WHERE ap.line=? AND ap.status='ok'")
    args: list = [line]
    if leagues:
        sql += f" AND bp.league IN ({','.join('?' * len(leagues))})"
        args += list(leagues)
    if seasons:
        sql += f" AND bp.season IN ({','.join('?' * len(seasons))})"
        args += list(seasons)
    return [dict(r) for r in conn.execute(sql, args)]


def compare_lines(conn, leagues=None, seasons=None) -> dict:
    # 线 P 行直接复用回测读取器（联赛/赛季过滤语义单点维护）
    from fa.backtest.metrics import fetch_predictions
    bp = fetch_predictions(conn, leagues, seasons)
    cmp = {"n": len(bp), "P": evaluate(bp),
           "market": {"ll": evaluate(bp)["market_ll"]}}
    roi_rows = {"P": bp}
    for line in ("A_base", "A_enh"):
        ag = _fetch_agent(conn, line, leagues, seasons)
        rows = merge_rows(bp, ag)
        cmp[line] = evaluate(rows) if rows else {"n": 0}
        roi_rows[line] = rows
    cmp["roi"] = {k: simulate_flat(candidates(v)) if v else {"n": 0}
                  for k, v in roi_rows.items()}
    enh = _fetch_agent(conn, "A_enh", leagues, seasons)
    cmp["audit"] = {"enh_sources": sum(
        len(json.loads(r["sources_json"] or "[]")) for r in enh)}
    return cmp


def render_report(cmp: dict, out_path: Path) -> None:
    """滚动对比报告（设计 §8）：诚实标注增强层泄漏限制。"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# 双路对比滚动报告（生成于 {now}）", "",
             f"样本 n={cmp['n']}（线 P 与线 A 交集见各线 n）", "",
             "| 线 | n | log-loss | Brier | vs 市场 |",
             "|---|---|---|---|---|"]
    for k in ("P", "A_base", "A_enh"):
        e = cmp[k]
        if e.get("n"):
            lines.append(f"| {k} | {e['n']} | {e['model_ll']:.4f} | "
                         f"{e['model_brier']:.4f} | {e['ratio']:.3f}× |")
        else:
            lines.append(f"| {k} | 0 | — | — | — |")
    lines.append(f"| 市场 | {cmp['n']} | {cmp['market']['ll']:.4f} | — | 1.000× |")
    lines += ["", "## 平注 ROI", ""]
    for k, v in cmp["roi"].items():
        lines.append(f"- {k}: n={v['n']} roi={v['roi']:+.1%}"
                     if v.get("n") else f"- {k}: 无候选注")
    lines += ["", "> ⚠️ A_enh（增强层）为历史回放检索，可能受赛后信息泄漏污染"
              "（缓解措施与抽查见设计 §5.2）——其结论不与 A_base 混排。", ""]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
