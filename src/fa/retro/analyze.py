"""S0 分歧报告渲染 + S1 证据审计（设计 §8-1）。

audit 规则：行含赛前成因标签（PREMATCH_CAUSE_TAGS）时，证据须非空
且全部 date 可证早于比赛日——date 先取前 10 字符（容 LLM 输出富格式
如 ISO 时间戳），再经 date.fromisoformat 校验，**不可解析计违规**
（幻觉日期如 0000-00-00 字典序早于比赛日，纯字典序会静默放行）；
解析成功后 ISO 日期的字典序比较即安全。违规率如实输出，不得为通过
放宽规则（诚实条款）。
"""
import json
import sqlite3
from datetime import date

from fa.retro.contract import PREMATCH_CAUSE_TAGS


def render_divergence_report(rows: list[dict]) -> str:
    lines = [f"模型 vs 市场分歧报告（n={len(rows)}，div=ln(mkt_p/model_p)，"
             f"正=模型差）"]
    for r in rows:
        lines.append(
            f"  {r['date']} {r['league']} {r['home']} vs {r['away']}"
            f"  {r['outcome']}  div={r['div']:+.4f}"
            f"  model=({r['p_home']:.2f},{r['p_draw']:.2f},{r['p_away']:.2f})"
            f" mkt=({r['mkt_home']:.2f},{r['mkt_draw']:.2f},"
            f"{r['mkt_away']:.2f})")
    return "\n".join(lines)


def audit_batch(conn: sqlite3.Connection, batch_id=None) -> dict:
    sql = ("SELECT a.id, a.match_id, a.date, a.miss_tags_json, a.evidence_json"
           " FROM retro_attributions a WHERE a.status='ok'"
           " AND a.miss_tags_json IS NOT NULL")
    args: list = []
    if batch_id is not None:
        sql += " AND a.batch_id=?"
        args.append(batch_id)
    violations, checked = [], 0
    for r in conn.execute(sql, args):
        tags = set(json.loads(r["miss_tags_json"]))
        if not tags & PREMATCH_CAUSE_TAGS:
            continue                      # 非赛前成因标签：无日期规则
        checked += 1
        ev = json.loads(r["evidence_json"] or "[]")
        if not ev:
            violations.append((r["id"], r["match_id"], "零证据"))
            continue
        for e in ev:
            d = str(e.get("date", ""))[:10]
            try:
                date.fromisoformat(d)
            except ValueError:
                violations.append((r["id"], r["match_id"],
                                   f"证据日期不可解析: {e.get('date')!r}"))
                break
            if d >= r["date"]:
                violations.append((r["id"], r["match_id"],
                                   f"证据日期 {e.get('date')!r} 不早于比赛日"))
                break
    return {"n_checked": checked, "n_violation": len(violations),
            "violations": violations,
            "rate": (len(violations) / checked) if checked else 0.0}
