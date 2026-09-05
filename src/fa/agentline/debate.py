"""A_debate 轮次引擎（2026-09-05 设计 §2）：生成者-批评者-修订，≤2 轮。

预注册常量（变更走设计档修订，不得实施期静默调）：轮数上限 2、预算硬上限
每场 5 次调用、提前终止 ε=0.02（相邻版本胜平负 max-abs < 0.02，修订版
产出后、下轮批评前判定）。批评者失败→不发起该轮修订；终版=最晚 ok 版。
"""
import json

from fa.agentline.contract import ATTACK_LABELS, parse_attack, parse_prediction
from fa.agentline.runner import build_prompt

# 枚举串 join 自契约常量（单一事实源，终审 Minor#1）：改词表只动 contract.py
ATTACK_ENUM_HELP = "|".join(ATTACK_LABELS)


def build_critic_prompt(info_set: dict, prev_pred: str,
                        prev_attack: str | None = None) -> str:
    prev = ("# 前轮攻击（你上一轮的评审，勿重复）\n" + prev_attack + "\n\n"
            if prev_attack else "")
    return (
        "你是量化预测的独立评审。以下是赛前信息集与一个待审预测。\n\n"
        "# 赛前信息集\n" + json.dumps(info_set, ensure_ascii=False) + "\n\n"
        "# 待审预测\n" + prev_pred + "\n\n" + prev +
        "# 要求\n"
        "- 只产定性判断：攻击其弱点/替代解释/内部矛盾/过度自信/证据薄弱，"
        "禁止输出任何概率数字\n"
        "- 最终必须输出且仅输出一个 JSON 对象（不要 markdown 围栏）：\n"
        '  {"attacks": [{"label": "<枚举词>", "reason": "<=200字", '
        '"severity": <0-1>}]}\n'
        f"- label 封闭枚举：{ATTACK_ENUM_HELP}；"
        '无攻击点时输出 {"attacks": []}\n')


def build_revision_prompt(info_set: dict, prev_pred: str, attack: str) -> str:
    return (
        "你是职业足球量化分析师。基于以下赛前信息、你自己的上一版预测与"
        "独立评审的攻击，输出修订版预测。\n\n"
        "# 赛前信息集\n" + json.dumps(info_set, ensure_ascii=False) + "\n\n"
        "# 上一版预测（你的初版）\n" + prev_pred + "\n\n"
        "# 独立评审的攻击\n" + attack + "\n\n"
        "# 要求\n"
        "- 吸收合理批评、拒绝不合理批评，自主判断\n"
        "- 最终必须输出且仅输出一个 JSON 对象（不要 markdown 围栏）：\n"
        '  {"p_home": <0-1>, "p_draw": <0-1>, "p_away": <0-1>, '
        '"p_over25": <0-1>, "confidence": <0-1>, "reasoning_digest": '
        '"<=200字", "sources": []}\n'
        "- p_home + p_draw + p_away 之和应接近 1\n"
        "- sources：若使用了信息集之外的信息逐条列出 {title, date, url}；"
        "否则为空数组\n")


def max_abs_delta(a: dict, b: dict) -> float:
    """相邻版本胜平负 max-abs（取绝对值故方向无关；入参均须 status='ok'）。"""
    return max(abs(a[k] - b[k]) for k in ("p_home", "p_draw", "p_away"))


_MAX_ROUNDS = 2
_EPS = 0.02


def _status_of(parsed: dict, run_err: str | None) -> dict:
    """dsh 层失败 → timeout/error（与 orchestrate._status_of 同式，返回 dict）。"""
    if run_err is not None:
        return {**parsed, "status": "timeout" if "超时" in run_err else "error",
                "reasoning_digest": f"dsh 失败：{run_err}"}
    return parsed


def run_match_debate(call, info: dict) -> dict:
    """单场辩论（纯编排；call 注入，与 runner.run_headless 同签名）。

    预注册语义（设计 §2.2）：批评者失败→本轮不发起修订、budget_exhausted=1；
    修订失败→取上一 ok 版、budget_exhausted=1；ε 提前终止→early_stop=1。
    轮 r 批评者输入含 r-1 轮攻击原文（设计 §2.1，勿重复出题；初版轮为 None）。
    """
    rounds: list[dict] = []
    n_calls = 0
    budget_exhausted = 0
    early_stop = 0

    def _record(round_no: int, role: str, parsed: dict, raw: str,
                dur: float) -> None:
        rounds.append({"round": round_no, "role": role,
                       "payload": json.dumps(parsed, ensure_ascii=False),
                       "raw": raw, "status": parsed["status"], "dur": dur})

    out, err, dur = call(build_prompt(info, "A_base"))
    n_calls += 1
    v0 = _status_of(parse_prediction(out or ""), err)
    _record(0, "generator", v0, out or "", dur)
    last_ok = v0 if v0["status"] == "ok" else None
    last_ok_raw = (out or "") if last_ok else None
    last_attack_raw: str | None = None

    for r in range(1, _MAX_ROUNDS + 1):
        if last_ok is None:
            break                        # v0 失败：无版可辩
        out, err, dur = call(build_critic_prompt(info, last_ok_raw,
                                                 last_attack_raw))
        n_calls += 1
        atk = parse_attack(out or "")
        if err is not None:
            # 真实失败原因入 payload（生成者侧 _status_of 同式）——否则审计只
            # 能看到契约解析错误，掩盖 dsh 层失败根因。
            atk = {**atk, "status": "timeout" if "超时" in err else "error",
                   "error": f"dsh 失败：{err}"}
        _record(r, "critic", atk, out or "", dur)
        if atk["status"] != "ok":
            budget_exhausted = 1
            break
        prev_ok = last_ok
        # 本轮攻击原文留给下轮批评者（设计 §2.1：轮 r 批评者输入含 r-1 轮攻击，
        # 勿重复出题；初版轮为 None）。brief 原稿此行误写为清空，与设计档矛盾。
        last_attack_raw = out or ""
        out, err, dur = call(build_revision_prompt(info, last_ok_raw,
                                                   last_attack_raw))
        n_calls += 1
        parsed = _status_of(parse_prediction(out or ""), err)
        _record(r, "generator", parsed, out or "", dur)
        if parsed["status"] != "ok":
            budget_exhausted = 1
            break
        if max_abs_delta(prev_ok, parsed) < _EPS:
            early_stop = 1
        last_ok, last_ok_raw = parsed, out or ""
        if early_stop:
            break
    final = last_ok if last_ok is not None else v0
    return {"final": final, "rounds": rounds, "n_calls": n_calls,
            "budget_exhausted": budget_exhausted, "early_stop": early_stop}
