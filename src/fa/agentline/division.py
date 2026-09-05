"""A_division 跳链引擎（2026-09-05 设计 §3）：历史考古官→预测者→质询官。

预注册降级规则（设计 §3.2）：跳1 失败→预测者退化为原始信息集；跳2 失败
→终版=该失败态、跳3 不发起；跳3 失败→跳2 预测原样入终版。每场恒 3 次
调用（跳2 失败时 2 次）。质询官与 A_debate 批评者共用 parse_attack 契约；
判决应用 v1 只落 flag 不改数（flags 单一事实源 = derive_flags）。
"""
import json

from fa.agentline.contract import parse_attack, parse_history_points, \
    parse_prediction
from fa.agentline.runner import build_prompt

RELEVANCE_ENUM = "high|medium|low"
ATTACK_ENUM_HELP = ("overconfidence|missing_context|alt_explanation"
                    "|internal_inconsistency|evidence_weak")


def build_archivist_prompt(info_set: dict) -> str:
    return (
        "你是足球历史数据考古官。基于以下赛前信息集内置的历史段（H2H "
        "交锋与双方近况），提炼与本场预测最相关的历史要点。\n\n"
        "# 赛前信息集\n" + json.dumps(info_set, ensure_ascii=False) + "\n\n"
        "# 要求\n"
        "- 只产定性要点，禁止输出任何概率数字；只用信息集内的信息，"
        "禁止使用信息集之外的知识\n"
        "- 最终必须输出且仅输出一个 JSON 对象（不要 markdown 围栏）：\n"
        '  {"h2h_points": [{"point": "<=100字", "relevance": '
        '"<high|medium|low>"}], "recent_form_points": [同构]}\n'
        f"- relevance 封闭枚举：{RELEVANCE_ENUM}；两数组均可为空"
        "（历史段无信息=合法）\n")


def build_predictor_prompt(info_set: dict, points: dict | None) -> str:
    extra = ""
    if points is not None:
        extra = ("\n# 历史考古官要点（分工上游供给，自主取舍）\n"
                 + json.dumps(points, ensure_ascii=False) + "\n")
    return build_prompt(info_set, "A_base", extra_section=extra)


def build_challenger_prompt(info_set: dict, pred_raw: str) -> str:
    return (
        "你是量化预测的独立质询官。以下是赛前信息集与一个待审预测。\n\n"
        "# 赛前信息集\n" + json.dumps(info_set, ensure_ascii=False) + "\n\n"
        "# 待审预测\n" + pred_raw + "\n\n"
        "# 要求\n"
        "- 只产定性判断：质询其过度自信/历史忽视/替代解释/内部矛盾/证据"
        "薄弱，禁止输出任何概率数字，禁止修改预测\n"
        "- 最终必须输出且仅输出一个 JSON 对象（不要 markdown 围栏）：\n"
        '  {"attacks": [{"label": "<枚举词>", "reason": "<=200字", '
        '"severity": <0-1>}]}\n'
        f"- label 封闭枚举：{ATTACK_ENUM_HELP}；"
        '无质询点时输出 {"attacks": []}\n')


def _status_of(parsed: dict, run_err: str | None) -> dict:
    """dsh 层失败 → timeout/error（与 debate._status_of 同式）。

    division 版对 reasoning_digest 做键存在性守卫（debate 版无条件覆盖——
    其唯一输入 parse_prediction 恒含该键；本版为跨契约复用留守卫）。
    """
    if run_err is not None:
        return {**parsed, "status": "timeout" if "超时" in run_err else "error",
                **({"reasoning_digest": f"dsh 失败：{run_err}"}
                   if "reasoning_digest" in parsed else {})}
    return parsed


def run_match_division(call, info: dict) -> dict:
    """单场三跳（纯编排；call 注入，与 runner.run_headless 同签名）。"""
    jumps: list[dict] = []
    n_calls = 0

    def _record(jump_no: int, role: str, parsed: dict, raw: str,
                dur: float) -> None:
        jumps.append({"jump": jump_no, "role": role,
                      "payload": json.dumps(parsed, ensure_ascii=False),
                      "raw": raw, "status": parsed["status"], "dur": dur})

    out, err, dur = call(build_archivist_prompt(info))
    n_calls += 1
    hist = parse_history_points(out or "")
    if err is not None:
        # 真实失败原因入 payload（与 debate.py 批评者分支同式）——否则审计只能
        # 看到契约解析错误，掩盖 dsh 层失败根因（fix round 1）。
        hist = {**hist, "status": "timeout" if "超时" in err else "error",
                "error": f"dsh 失败：{err}"}
    _record(1, "archivist", hist, out or "", dur)
    points = hist if hist["status"] == "ok" else None

    out, err, dur = call(build_predictor_prompt(info, points))
    n_calls += 1
    pred = _status_of(parse_prediction(out or ""), err)
    _record(2, "predictor", pred, out or "", dur)
    if pred["status"] != "ok":
        return {"final": pred, "jumps": jumps, "n_calls": n_calls}

    out3, err3, dur3 = call(build_challenger_prompt(info, out or ""))
    n_calls += 1
    atk = parse_attack(out3 or "")
    if err3 is not None:
        atk = {**atk, "status": "timeout" if "超时" in err3 else "error",
               "error": f"dsh 失败：{err3}"}   # 根因入账，同跳1（fix round 1）
    _record(3, "challenger", atk, out3 or "", dur3)
    return {"final": pred, "jumps": jumps, "n_calls": n_calls}


def derive_flags(jump_rows: list[dict]) -> dict:
    """从跳行推导判决 flag（设计 §3.4；评测与 run 摘要共用的单一事实源）。

    jump_rows: [{"jump", "status", "payload"}...]（payload=该跳规范化 JSON）。
    跳3 未发起（行缺）不记 jump3_fail——跳2_fail 已解释缺席原因。
    """
    by_jump = {r["jump"]: r for r in jump_rows}
    flags: dict = {}
    for j in (1, 2):
        row = by_jump.get(j)
        if row is None or row["status"] != "ok":
            flags[f"jump{j}_fail"] = 1
    row3 = by_jump.get(3)
    if row3 is not None and row3["status"] != "ok":
        flags["jump3_fail"] = 1
    if row3 is not None and row3["status"] == "ok":
        for a in json.loads(row3["payload"]).get("attacks", []):
            flags[a["label"]] = flags.get(a["label"], 0) + 1
    return flags
