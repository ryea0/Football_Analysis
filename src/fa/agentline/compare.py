"""七对照评测（设计 §8 + 2026-09-05 设计 §2.5/§3.4）：线 P / A_base /
A_enh / A_multi / A_debate / A_division / 市场收盘，同批同判据。

行适配原则：agentline 的 p_* 覆盖 bp 行拷贝，mkt_*（Pinnacle 收盘去水）、
odds_*、outcome、total_goals 原样保留——evaluate 与 candidates 的入参
schema 完全复用，评测代码零改动（市场是所有评估的对照线，spec §8.2）。
"""
import json
import math
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


def _bp_filter(leagues, seasons, date_from=None, date_to=None) -> tuple[str, list]:
    """bp 侧联赛/赛季/日期过滤子句（线指标取数与成员审计共用，语义单点维护）。

    ``date_from`` / ``date_to``：比赛日（YYYY-MM-DD 字符串，含两端），None=不限。
    """
    sql, args = "", []
    if leagues:
        sql += f" AND bp.league IN ({','.join('?' * len(leagues))})"
        args += list(leagues)
    if seasons:
        sql += f" AND bp.season IN ({','.join('?' * len(seasons))})"
        args += list(seasons)
    if date_from:
        sql += " AND bp.date >= ?"
        args.append(date_from)
    if date_to:
        sql += " AND bp.date <= ?"
        args.append(date_to)
    return sql, args


def _fetch_agent(conn, line, leagues, seasons, attributor=None,
                 date_from=None, date_to=None) -> list[dict]:
    sql = ("SELECT * FROM agentline_predictions ap"
           " JOIN backtest_predictions bp ON bp.match_id = ap.match_id"
           " WHERE ap.line=? AND ap.status='ok'")
    args: list = [line]
    if attributor is not None:
        # A_multi 同场多行（聚合 0 + 成员 1..N）：必须钉死归因子——否则
        # merge_rows 的 by_mid 一对一折叠会静默吃进成员概率（AM-T1 审查点）。
        sql += " AND ap.attributor=?"
        args.append(attributor)
    w, a = _bp_filter(leagues, seasons, date_from, date_to)
    return [dict(r) for r in conn.execute(sql + w, args + a)]


def compare_lines(conn, leagues=None, seasons=None,
                  date_from=None, date_to=None) -> dict:
    # 线 P 行直接复用回测读取器（联赛/赛季/日期过滤语义单点维护）
    from fa.backtest.metrics import fetch_predictions
    bp = fetch_predictions(conn, leagues, seasons, date_from, date_to)
    e_p = evaluate(bp)                      # 只评一次：market 列与 P 行共用同一份
    cmp = {"n": len(bp), "P": e_p,
           "market": {"ll": e_p["market_ll"]}}
    roi_rows = {"P": bp}
    for line, attr in (("A_base", 1), ("A_enh", 1), ("A_multi", 0),
                       ("A_debate", 1), ("A_division", 1)):
        ag = _fetch_agent(conn, line, leagues, seasons,
                          attributor=attr, date_from=date_from, date_to=date_to)
        rows = merge_rows(bp, ag)
        # evaluate 自带该子集的 market_ll——报告按行展示，分母不再混用全量值
        cmp[line] = evaluate(rows) if rows else {"n": 0}
        roi_rows[line] = rows
    cmp["roi"] = {k: simulate_flat(candidates(v)) if v else {"n": 0}
                  for k, v in roi_rows.items()}
    enh = _fetch_agent(conn, "A_enh", leagues, seasons, attributor=1,
                       date_from=date_from, date_to=date_to)
    cmp["audit"] = {"enh_sources": sum(
        len(json.loads(r["sources_json"] or "[]")) for r in enh)}
    # A_multi 成员级审计：成员分母必须含非 ok 行（parse_fail/timeout 也是成员，
    # 吞掉会把聚合质量虚高）——口径与线指标取数（只看 ok）不同，故不复用
    # _fetch_agent 的 status 过滤，单开计数（成员级行数 + ok 数）。
    w, a = _bp_filter(leagues, seasons, date_from, date_to)
    multi = [dict(r) for r in conn.execute(
        "SELECT ap.attributor, ap.status FROM agentline_predictions ap"
        " JOIN backtest_predictions bp ON bp.match_id = ap.match_id"
        " WHERE ap.line='A_multi' AND ap.attributor>0" + w, a)]
    cmp["audit"]["multi_members"] = len(multi)
    cmp["audit"]["multi_member_ok"] = sum(
        1 for r in multi if r["status"] == "ok")
    cmp["debate"] = debate_gain(conn, leagues, seasons, date_from, date_to)
    cmp["division"] = division_stratified(conn, leagues, seasons, date_from, date_to)
    return cmp


def debate_gain(conn, leagues=None, seasons=None,
                date_from=None, date_to=None) -> dict:
    """修订增益（2026-09-05 设计 §2.5）：v0 vs 终版同场对比 + 分歧相关性。

    v0 取 rounds 表 round=0 的 generator ok 行 payload；终版取 predictions
    的 A_debate ok 行。rho = 每场最大攻击 severity 与修订幅度（三项 L1）的
    Spearman 相关（n<3 或无可比对 → None）——高攻击低修订=固执、低攻击高
    修订=无主见，两向都如实报。零方差（如整批提前终止 → revs 全 0）时
    Spearman 未定义返回 nan，如实记 None，不渲染 ρ=nan（仿 retro._mwu_p）。
    """
    from fa.backtest.metrics import fetch_predictions
    bp = fetch_predictions(conn, leagues, seasons, date_from, date_to)
    finals = _fetch_agent(conn, "A_debate", leagues, seasons,
                          attributor=1, date_from=date_from, date_to=date_to)
    by_mid = {r["match_id"]: r for r in finals}
    if not by_mid:
        return {"n": 0}
    v0 = {r["match_id"]: json.loads(r["payload_json"]) for r in conn.execute(
        "SELECT match_id, payload_json FROM agentline_debate_rounds"
        " WHERE round=0 AND role='generator' AND status='ok'")}
    v0_rows = []
    for m, f in by_mid.items():
        if m in v0:
            r = dict(f)
            for k in ("p_home", "p_draw", "p_away", "p_over25"):
                r[k] = v0[m][k]
            v0_rows.append(r)
    n = len(v0_rows)
    if n == 0:
        return {"n": 0}
    e_final = evaluate(merge_rows(bp, list(by_mid.values())))
    e_v0 = evaluate(merge_rows(bp, v0_rows))
    sevs, revs = [], []
    for m, f in by_mid.items():
        if m not in v0:
            continue
        atks = [a for r in conn.execute(
            "SELECT payload_json FROM agentline_debate_rounds"
            " WHERE match_id=? AND role='critic' AND status='ok'", (m,))
                for a in json.loads(r["payload_json"]).get("attacks", [])]
        if not atks:
            continue
        sevs.append(max(a["severity"] for a in atks))
        revs.append(sum(abs(f[k] - v0[m][k])
                        for k in ("p_home", "p_draw", "p_away")))
    rho = None
    if len(sevs) >= 3:
        from scipy.stats import spearmanr
        rho_raw = float(spearmanr(sevs, revs).statistic)
        # 全平手/单侧零方差（如整批提前终止 → revs 全 0）时 Spearman 未定义
        # 返回 nan——仿 retro._mwu_p 口径如实记 None，不让 nan 漏进报告渲染
        # 成 ρ=nan；n_rho 照报（ρ=—（n=5）= 有可比对但向量退化，完整披露）。
        rho = None if math.isnan(rho_raw) else rho_raw
    return {"n": n, "v0_ll": e_v0["model_ll"], "final_ll": e_final["model_ll"],
            "rho": rho, "n_rho": len(sevs)}


def _mwu_p(a: list, b: list):
    """MWU 双侧 p 值（retro 关卡3 口径单点维护）：全平手（零方差）时
    scipy 返回 nan，如实记 None，不让 nan 穿透到报告渲染成 p=nan。"""
    from scipy.stats import mannwhitneyu
    p_raw = float(mannwhitneyu(a, b, alternative="two-sided").pvalue)
    return None if math.isnan(p_raw) else p_raw


# severity 高烈度割点（2026-09-05 设计 §3.4 增补）：severity 契约 [0,1] 的
# 中点，预注册——不得按数据事后调整（改阈值须走设计档修订并记决策）。
_SEVERITY_HIGH = 0.5


def division_stratified(conn, leagues=None, seasons=None,
                       date_from=None, date_to=None) -> dict:
    """质询标签分层检验（2026-09-05 设计 §3.4，retro 关卡3 口径）。

    分层键 = 五标签任一命中；jumpN_fail 是管线降级信号、不进分层键（判决
    只落 flag 不改数——管线失败≠质询有话可说，混进「有标签」层会把降级
    场误算成质询起效）。各层 per-match log-loss（metrics.log_loss 单场
    调用——公式单一事实源，不在本文件重写）；两层各 n≥5 才跑 MWU 双侧。
    非 ok 终版行默认剔除（audit 口径：分层只在可评行上做）。
    两种「p 缺席」分开记，渲染各自诚实占位：任一层 n<5 → 不出 mwu_p 键
    （小样本不出 p）；两组 log-loss 全平手（层内零方差）→ MWU 未定义返回
    nan，仿 retro._mwu / debate_gain.rho 口径如实记 None，不让 nan 穿透到
    报告渲染成 p=nan。

    severity 加权视角（§3.4 增补，2026-09-05 负责人裁定）：首批判读实证
    标签饱和（有攻击/无攻击二分退化），另按**场次最大攻击 severity** 分
    三组——≥0.5 高烈度 / <0.5 低烈度 / attacks 空（含跳3 非 ok）单列计数
    不进检验。MWU 双侧只比较高 vs 低，两侧各 n≥5 才出 mwu_p_sev（nan→
    None 口径同 mwu_p）。阈值 0.5 = severity 契约 [0,1] 的中点，预注册、
    不得按数据事后调割点；二分标签键保留不动（跨批连续性）。
    """
    from fa.backtest.metrics import fetch_predictions, log_loss
    from fa.agentline.division import derive_flags
    bp = fetch_predictions(conn, leagues, seasons, date_from, date_to)
    rows = merge_rows(bp, _fetch_agent(
        conn, "A_division", leagues, seasons,
        attributor=1, date_from=date_from, date_to=date_to))
    flagged, unflagged = [], []
    high, low = [], []
    n_no_attack = 0
    for r in rows:
        jumps = [{"jump": j["jump"], "status": j["status"],
                  "payload": j["payload_json"]} for j in conn.execute(
            "SELECT jump, status, payload_json FROM agentline_division_jumps"
            " WHERE match_id=?", (r["match_id"],))]
        fl = {k for k in derive_flags(jumps) if not k.startswith("jump")}
        ll = log_loss([(r["p_home"], r["p_draw"], r["p_away"])],
                      [r["outcome"]])
        (flagged if fl else unflagged).append(ll)
        # severity 只取跳3 ok payload 的 attacks（同一份 jumps，零额外查询）
        atks = [a for j in jumps if j["jump"] == 3 and j["status"] == "ok"
                for a in json.loads(j["payload"]).get("attacks", [])]
        if not atks:
            n_no_attack += 1
        elif max(a["severity"] for a in atks) >= _SEVERITY_HIGH:
            high.append(ll)
        else:
            low.append(ll)
    out = {"n_flagged": len(flagged), "n_unflagged": len(unflagged),
           "n_high": len(high), "n_low": len(low), "n_no_attack": n_no_attack}
    if flagged:
        out["ll_flagged"] = sum(flagged) / len(flagged)
    if unflagged:
        out["ll_unflagged"] = sum(unflagged) / len(unflagged)
    if len(flagged) >= 5 and len(unflagged) >= 5:
        out["mwu_p"] = _mwu_p(flagged, unflagged)
    if high:
        out["ll_high"] = sum(high) / len(high)
    if low:
        out["ll_low"] = sum(low) / len(low)
    if len(high) >= 5 and len(low) >= 5:
        out["mwu_p_sev"] = _mwu_p(high, low)
    return out


_FOOTNOTE = ("> 各行比值在其自身 n 场子集内计算，跨线直比无效；"
             "n<100 的行为链路验证样本，数字无统计意义")


def _p_placeholder(d: dict, key: str) -> str:
    """MWU p 的三种诚实占位（二分标签与 severity 视角共用，措辞单点维护）：
    有值报值；键在而值为 None → 向量退化（两组 log-loss 全平手）；键不在 →
    任一侧 n<5 小样本不出 p——「算不出」不得被误读成「样本不够」。"""
    if d.get(key) is not None:
        return f"{d[key]:.3f}"
    if key in d:
        return "MWU 未定义（两组 log-loss 全平手）"
    return "n<5/层不报（小样本诚实）"


def render_report(cmp: dict, out_path: Path) -> None:
    """滚动对比报告（设计 §8）：诚实标注增强层泄漏限制与小样本边界。"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# 多线对比滚动报告（生成于 {now}）", "",
             f"样本 n={cmp['n']}（线 P 与线 A 交集见各线 n）", "",
             "| 线 | n | log-loss | Brier | 子集市场 ll | vs 子集市场 |",
             "|---|---|---|---|---|---|"]
    for k in ("P", "A_base", "A_enh", "A_multi", "A_debate", "A_division"):
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
    # 无任何被采纳的检索引用——可能是检索未发生，也可能是检索发生了但结果不可用
    # （首批 E2E 即后者：检索实际发生、引擎返回垃圾，附录 A.6）。两线差异只能是
    # 采样噪声，必须自己说破。
    if cmp.get("audit", {}).get("enh_sources") == 0 and cmp["A_enh"].get("n"):
        lines += ["", "> ⚠️ 增强层 sources 全空（无被采纳的检索引用）——检索未发生"
                     "或检索结果不可用（判定方法与首例复盘见附录 A.6）；"
                     "A_enh 与 A_base 差异为采样噪声，非检索增量。"]
    # 成员披露（A_multi 计划 T4）：聚合行背后的成员构成必须可见——成员行单独
    # 落库、可单独计分；audit 无该线数字（旧形态 dict）时以「—」占位不虚报。
    audit = cmp.get("audit", {})
    mm, mo = audit.get("multi_members"), audit.get("multi_member_ok")
    lines += ["", f"> A_multi 为多成员确定性聚合（分量中位数）；成员行单独落库"
                  f"可计分（本批成员 {mm if mm is not None else '—'} 行 / "
                  f"ok {mo if mo is not None else '—'}）"]
    # 修订增益段（A_debate 计划 T8，2026-09-05 设计 §2.5）：v0→终版的 log-loss
    # 变化与「攻击强度-修订幅度」相关性都要可见——rho=None（无可比对）不是缺失
    # 而是如实占位，固执/无主见两个方向的信号都必须自己说破，不做单边解读。
    d = cmp.get("debate", {})
    if d.get("n"):
        rho_s = f"{d['rho']:+.2f}" if d.get("rho") is not None else "—"
        lines += ["", f"> A_debate 修订增益：v0 ll={d['v0_ll']:.4f} → "
                      f"终版 ll={d['final_ll']:.4f}（n={d['n']}）；"
                      f"攻击-修订相关性 ρ={rho_s}（n={d.get('n_rho', 0)}，"
                      f"高攻击低修订=固执 / 低攻击高修订=无主见，均为实测信号）"]
    # 质询分层段（A_division 计划 T8，2026-09-05 设计 §3.4）：有标签层 vs
    # 无标签层的 log-loss 对照必须可见，且允许结论为「质询无信息量」——
    # 不做单边解读。p 的三种占位各自诚实：有值报值、任一层 n<5 不报
    # （小样本）、两组 ll 全平手不报（向量退化）——后两者措辞分开，
    # 「算不出」不得被误读成「样本不够」；单侧层缺 ll 以「—」占位不虚报。
    dv = cmp.get("division", {})
    if dv.get("n_flagged") or dv.get("n_unflagged"):
        p_s = _p_placeholder(dv, "mwu_p")
        ll_f = f"{dv['ll_flagged']:.4f}" if "ll_flagged" in dv else "—"
        ll_u = f"{dv['ll_unflagged']:.4f}" if "ll_unflagged" in dv else "—"
        lines += ["", f"> A_division 质询分层：有标签 n={dv['n_flagged']}"
                      f"（ll={ll_f}）vs 无标签 n={dv['n_unflagged']}"
                      f"（ll={ll_u}）；MWU 双侧 p={p_s}——"
                      f"允许结论为「质询无信息量」"]
        # severity 加权视角（§3.4 增补）：标签饱和时二分退化的替补读法。
        # 无攻击组只计数不进检验；空层 ll 以「—」占位；p 占位与上行同源。
        if dv.get("n_high") or dv.get("n_low") or dv.get("n_no_attack"):
            ps = _p_placeholder(dv, "mwu_p_sev")
            ll_h = f"{dv['ll_high']:.4f}" if "ll_high" in dv else "—"
            ll_l = f"{dv['ll_low']:.4f}" if "ll_low" in dv else "—"
            lines.append(f"> severity 加权：高烈度 n={dv['n_high']}"
                         f"（ll={ll_h}）vs 低烈度 n={dv['n_low']}"
                         f"（ll={ll_l}）vs 无攻击 n={dv['n_no_attack']}；"
                         f"MWU 双侧 p={ps}——阈值 0.5 预注册")
    lines += ["", _FOOTNOTE, ""]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
