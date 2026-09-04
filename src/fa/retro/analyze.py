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


def consistency_report(conn: sqlite3.Connection, batch_id=None) -> dict:
    """关卡 2 正式形态：成员 primary 三档一致性 + 成员失败计数。

    只统计成员行（attributor≥1）中 status='ok' 者；一场全失败不计入
    三档（该场聚合 status='error' 已在台账），但失败成员计入 member_failures。
    n_k1 = 恰 1 个 ok 成员的场数：单成员恒计「全同」，会把一致率虚高
    （k=1 批与 ensemble 批并存时默认读数被推高）——如实披露，不剔除
    （剔除属解读策略，由读数人对照批 params 的 attributors 判断）。
    """
    sql = ("SELECT batch_id, match_id, attributor, primary_tag, status"
           " FROM retro_attributions WHERE attributor >= 1")
    args: list = []
    if batch_id is not None:
        sql += " AND batch_id=?"
        args.append(batch_id)
    by_match: dict = {}
    failures = 0
    for r in conn.execute(sql, args):
        g = by_match.setdefault((r["batch_id"], r["match_id"]), [])
        if r["status"] == "ok":
            g.append(r["primary_tag"])
        else:
            failures += 1
    tiers = {"unanimous": 0, "majority": 0, "none": 0}
    n_k1 = 0
    for votes in by_match.values():
        if not votes:
            continue                               # 全失败场不入三档
        if len(votes) == 1:
            n_k1 += 1
        top = max(set(votes), key=votes.count)
        if votes.count(top) == len(votes):
            tiers["unanimous"] += 1
        elif votes.count(top) * 2 > len(votes):
            tiers["majority"] += 1
        else:
            tiers["none"] += 1
    n = sum(tiers.values())
    return {"n_matches": n, **tiers, "n_k1": n_k1,
            "member_failures": failures,
            "agreement_rate": (tiers["unanimous"] + tiers["majority"]) / n
            if n else 0.0}
