"""五对照评测（设计 §8）：线 P / A_base / A_enh / A_multi / 市场，同一判据。

行适配原则：agentline 的 p_* 覆盖 bp 行拷贝，mkt_*（Pinnacle 收盘去水）、
odds_*、outcome、total_goals 原样保留——evaluate 与 candidates 的入参
schema 完全复用，评测代码零改动（市场是所有评估的对照线，spec §8.2）。
"""
import json
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


def _bp_filter(leagues, seasons) -> tuple[str, list]:
    """bp 侧联赛/赛季过滤子句（线指标取数与成员审计共用，语义单点维护）。"""
    sql, args = "", []
    if leagues:
        sql += f" AND bp.league IN ({','.join('?' * len(leagues))})"
        args += list(leagues)
    if seasons:
        sql += f" AND bp.season IN ({','.join('?' * len(seasons))})"
        args += list(seasons)
    return sql, args


def _fetch_agent(conn, line, leagues, seasons, attributor=None) -> list[dict]:
    sql = ("SELECT * FROM agentline_predictions ap"
           " JOIN backtest_predictions bp ON bp.match_id = ap.match_id"
           " WHERE ap.line=? AND ap.status='ok'")
    args: list = [line]
    if attributor is not None:
        # A_multi 同场多行（聚合 0 + 成员 1..N）：必须钉死归因子——否则
        # merge_rows 的 by_mid 一对一折叠会静默吃进成员概率（AM-T1 审查点）。
        sql += " AND ap.attributor=?"
        args.append(attributor)
    w, a = _bp_filter(leagues, seasons)
    return [dict(r) for r in conn.execute(sql + w, args + a)]


def compare_lines(conn, leagues=None, seasons=None) -> dict:
    # 线 P 行直接复用回测读取器（联赛/赛季过滤语义单点维护）
    from fa.backtest.metrics import fetch_predictions
    bp = fetch_predictions(conn, leagues, seasons)
    e_p = evaluate(bp)                      # 只评一次：market 列与 P 行共用同一份
    cmp = {"n": len(bp), "P": e_p,
           "market": {"ll": e_p["market_ll"]}}
    roi_rows = {"P": bp}
    for line, attr in (("A_base", 1), ("A_enh", 1), ("A_multi", 0)):
        ag = _fetch_agent(conn, line, leagues, seasons, attributor=attr)
        rows = merge_rows(bp, ag)
        # evaluate 自带该子集的 market_ll——报告按行展示，分母不再混用全量值
        cmp[line] = evaluate(rows) if rows else {"n": 0}
        roi_rows[line] = rows
    cmp["roi"] = {k: simulate_flat(candidates(v)) if v else {"n": 0}
                  for k, v in roi_rows.items()}
    enh = _fetch_agent(conn, "A_enh", leagues, seasons, attributor=1)
    cmp["audit"] = {"enh_sources": sum(
        len(json.loads(r["sources_json"] or "[]")) for r in enh)}
    # A_multi 成员级审计：成员分母必须含非 ok 行（parse_fail/timeout 也是成员，
    # 吞掉会把聚合质量虚高）——口径与线指标取数（只看 ok）不同，故不复用
    # _fetch_agent 的 status 过滤，单开计数（成员级行数 + ok 数）。
    w, a = _bp_filter(leagues, seasons)
    multi = [dict(r) for r in conn.execute(
        "SELECT ap.attributor, ap.status FROM agentline_predictions ap"
        " JOIN backtest_predictions bp ON bp.match_id = ap.match_id"
        " WHERE ap.line='A_multi' AND ap.attributor>0" + w, a)]
    cmp["audit"]["multi_members"] = len(multi)
    cmp["audit"]["multi_member_ok"] = sum(
        1 for r in multi if r["status"] == "ok")
    return cmp


_FOOTNOTE = ("> 各行比值在其自身 n 场子集内计算，跨线直比无效；"
             "n<100 的行为链路验证样本，数字无统计意义")


def render_report(cmp: dict, out_path: Path) -> None:
    """滚动对比报告（设计 §8）：诚实标注增强层泄漏限制与小样本边界。"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# 双路对比滚动报告（生成于 {now}）", "",
             f"样本 n={cmp['n']}（线 P 与线 A 交集见各线 n）", "",
             "| 线 | n | log-loss | Brier | 子集市场 ll | vs 子集市场 |",
             "|---|---|---|---|---|---|"]
    for k in ("P", "A_base", "A_enh", "A_multi"):
        e = cmp.get(k)
        if e is None:
            continue          # 旧形态 dict 无该线：宁缺一行，不虚报 n=0
        if e.get("n"):
            # vs 值与市场 ll 都取自同一行 dict（该线自己的 n 场子集），
            # 不与全量市场行并排混排——避免「同一张表两套分母」的误读。
            lines.append(f"| {k} | {e['n']} | {e['model_ll']:.4f} | "
                         f"{e['model_brier']:.4f} | {e['market_ll']:.4f} | "
                         f"{e['ratio']:.3f}× |")
        else:
            lines.append(f"| {k} | 0 | — | — | — | — |")
    lines.append(f"| 市场 | {cmp['n']} | {cmp['market']['ll']:.4f} | — | "
                 f"{cmp['market']['ll']:.4f} | 1.000× |")
    lines += ["", "## 平注 ROI", ""]
    for k, v in cmp["roi"].items():
        lines.append(f"- {k}: n={v['n']} roi={v['roi']:+.1%}"
                     if v.get("n") else f"- {k}: 无候选注")
    lines += ["", "> ⚠️ A_enh（增强层）为历史回放检索，可能受赛后信息泄漏污染"
              "（缓解措施与抽查见设计 §5.2）——其结论不与 A_base 混排。"]
    # 审计数字要渲染出来才算审计：sources 计数为 0 而 A_enh 有样本时，说明增强层
    # 的检索从未触发——两线差异只能是采样噪声，必须自己说破（E2E 实证 10/10 全空）。
    if cmp.get("audit", {}).get("enh_sources") == 0 and cmp["A_enh"].get("n"):
        lines += ["", "> ⚠️ 增强层检索未触发（sources 全空）——"
                     "A_enh 与 A_base 差异为采样噪声，非检索增量。"]
    # 成员披露（A_multi 计划 T4）：聚合行背后的成员构成必须可见——成员行单独
    # 落库、可单独计分；audit 无该线数字（旧形态 dict）时以「—」占位不虚报。
    audit = cmp.get("audit", {})
    mm, mo = audit.get("multi_members"), audit.get("multi_member_ok")
    lines += ["", f"> A_multi 为 3 成员确定性聚合（分量中位数）；成员行单独落库"
                  f"可计分（本批成员 {mm if mm is not None else '—'} 行 / "
                  f"ok {mo if mo is not None else '—'}）"]
    lines += ["", _FOOTNOTE, ""]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
