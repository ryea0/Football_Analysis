"""S0 分歧报告渲染 + S1 证据审计（设计 §8-1）。

audit 规则：行含赛前成因标签（PREMATCH_CAUSE_TAGS）时，证据须非空
且全部 date 可证早于比赛日——date 先取前 10 字符（容 LLM 输出富格式
如 ISO 时间戳），再经 date.fromisoformat 校验，**不可解析计违规**
（幻觉日期如 0000-00-00 字典序早于比赛日，纯字典序会静默放行）；
解析成功后 ISO 日期的字典序比较即安全。违规率如实输出，不得为通过
放宽规则（诚实条款）。

关卡 3（stratified_analysis）按 miss_tags 分层比标签层 vs 无标签层的
单场 log-loss 差：div 复用 select._divergence（不自写公式），违规判定
复用本模块 _row_violation（audit 与 analyze 单一事实源，两处不分叉）。
"""
import json
import math
import sqlite3
from datetime import date

from fa.backtest.metrics import fetch_predictions
from fa.retro.contract import PREMATCH_CAUSE_TAGS, TAGS
from fa.retro.select import _divergence


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


def _row_violation(tags: set, evidence_json, match_date: str):
    """有赛前成因标签的行：证据非空且全部 date 可解析、早于比赛日。

    None=合规/不适用标签集；str=违规原因。audit 与 analyze 共用
    （§15 裁定行：违规行不得作分层依据——单一事实源，两处判定不得分叉）。
    """
    if not tags & PREMATCH_CAUSE_TAGS:
        return None
    ev = json.loads(evidence_json or "[]")
    if not ev:
        return "零证据"
    for e in ev:
        d = str(e.get("date", ""))[:10]
        try:
            date.fromisoformat(d)
        except ValueError:
            return f"证据日期不可解析: {e.get('date')!r}"
        if d >= match_date:
            return f"证据日期 {e.get('date')!r} 不早于比赛日"
    return None


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
        v = _row_violation(tags, r["evidence_json"], r["date"])
        if v:
            violations.append((r["id"], r["match_id"], v))
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


def _mwu(a: list[float], b: list[float]):
    """Mann-Whitney U 双侧；任一侧 n<2 无法检验返回 None；n<8 用 exact。

    全平手（两组值全同）时双侧 U 的 p 值为 nan——如实记 None（无差异
    信息），不让 nan 漏进报告渲染成 p=nan。
    """
    if len(a) < 2 or len(b) < 2:
        return None
    from scipy.stats import mannwhitneyu
    method = "exact" if (len(a) < 8 or len(b) < 8) else "asymptotic"
    p = float(mannwhitneyu(a, b, alternative="two-sided",
                           method=method).pvalue)
    return None if math.isnan(p) else p


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def stratified_analysis(conn: sqlite3.Connection, batch_id=None,
                        include_violations: bool = False) -> dict:
    """关卡 3（设计 §8-3）：按 miss_tags 分层，比标签层 vs 无标签层的
    单场 log-loss 差（div，复用 select._divergence 同源定义）。

    代表行：每 (batch_id, match_id) 取聚合行（attributor=0）优先，无聚合行
    取单成员行（attributor=1）——ensemble 批不被成员行重复计数。audit 违规
    行默认剔除（§15 裁定前不作分层依据），--include-violations 可含并显式
    报 n_excluded。缺 backtest_predictions 的场次跳过计数（div 无从算起）。
    miss_tags_json 为 NULL 的 ok 行不入分层（与 audit_batch 同一过滤——无
    标签集无从分层，宁缺毋崩）。现状（v1 不改行为）：k≥2 ensemble 批若无
    有效聚合行，成员 2..N 行被静默丢弃（attributor=1 兜底只覆盖首个成员行）。
    跨批重复（v1 不去重，如实披露）：全库模式（batch_id=None）代表行按
    (batch_id, match_id) 取，同一场跨多个批会多行入 rows——MWU 单元独立性
    与各层 n 被污染；n_duplicate_matches 记该重复数（= n_rows − 去重后场数），
    读关卡判决请用 --batch-id 限定批（method 串同此说明）。
    诚实条款：不因「想让某层显著」而调整口径；点估计+样本量为主读数，
    p 值仅参考（v1 小样本不装精确）。
    """
    sql = ("SELECT a.batch_id, a.match_id, a.date, a.miss_tags_json,"
           " a.evidence_json, a.is_control FROM retro_attributions a"
           " WHERE a.status='ok' AND a.miss_tags_json IS NOT NULL"
           " AND (a.attributor=0 OR (a.attributor=1"
           " AND NOT EXISTS (SELECT 1 FROM retro_attributions b"
           " WHERE b.batch_id=a.batch_id AND b.match_id=a.match_id"
           " AND b.attributor=0 AND b.status='ok')))")
    args: list = []
    if batch_id is not None:
        sql += " AND a.batch_id=?"
        args.append(batch_id)
    preds = {r["match_id"]: r for r in fetch_predictions(conn)}
    rows, n_excluded, n_no_pred = [], 0, 0
    for r in conn.execute(sql, args):
        tags = set(json.loads(r["miss_tags_json"]))
        if not include_violations and _row_violation(
                tags, r["evidence_json"], r["date"]):
            n_excluded += 1
            continue
        p = preds.get(r["match_id"])
        if p is None:
            n_no_pred += 1
            continue
        rows.append({"match_id": r["match_id"], "tags": tags,
                     "div": _divergence(p), "is_control": r["is_control"]})
    per_tag = {}
    for t in TAGS:
        layer = [r for r in rows if t in r["tags"]]
        rest = [r for r in rows if t not in r["tags"]]
        per_tag[t] = {
            "n": len(layer),
            "n_case": sum(1 for r in layer if not r["is_control"]),
            "n_control": sum(1 for r in layer if r["is_control"]),
            "mean_div": _mean([r["div"] for r in layer]),
            "n_rest": len(rest),
            "mean_div_rest": _mean([r["div"] for r in rest]),
            "p_value": _mwu([r["div"] for r in layer],
                            [r["div"] for r in rest]),
        }
    return {"n_rows": len(rows),
            "n_duplicate_matches": len(rows) - len({r["match_id"]
                                                    for r in rows}),
            "n_excluded": n_excluded,
            "n_no_prediction": n_no_pred, "per_tag": per_tag,
            "method": "Mann-Whitney U 双侧；单元=单场 log-loss 差；"
                      "任一侧 n<8 用 exact；p 值仅参考（点估计+样本量为主）；"
                      "全库模式同场跨批未去重——读关卡判决请用 --batch-id 限定批"}


def render_analysis_report(res: dict) -> str:
    lines = [f"标签分层效度检验（n={res['n_rows']}，"
             f"重复场次 {res['n_duplicate_matches']}"
             f"，剔除违规 {res['n_excluded']}"
             f"，缺预测 {res['n_no_prediction']}）——{res['method']}"]
    for t, e in res["per_tag"].items():
        if e["n"] == 0:
            continue
        m = f"{e['mean_div']:+.4f}" if e["mean_div"] is not None else "—"
        mr = (f"{e['mean_div_rest']:+.4f}"
              if e["mean_div_rest"] is not None else "—")
        # .3g：极小 p（如 1.8e-4 以下）不落成 0.000 被误读为恰零
        pv = f"{e['p_value']:.3g}" if e["p_value"] is not None else "n/a"
        lines.append(f"  {t:16s} n={e['n']:3d}（病例 {e['n_case']}/"
                     f"对照 {e['n_control']}）mean_div={m} vs 无标层 {mr}"
                     f"（n={e['n_rest']}）p={pv}")
    lines.append("  判读：标签层 mean_div 明显大于无标层且有样本量支撑 →"
                 "标签有信息量；各层无差 → 归因是叙事不是科学（如实报告）")
    return "\n".join(lines)
