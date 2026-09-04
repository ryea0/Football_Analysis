"""A_debate 轮次引擎（2026-09-05 设计 §2）：生成者-批评者-修订，≤2 轮。

预注册常量（变更走设计档修订，不得实施期静默调）：轮数上限 2、预算硬上限
每场 5 次调用、提前终止 ε=0.02（相邻版本胜平负 max-abs < 0.02，修订版
产出后、下轮批评前判定）。批评者失败→不发起该轮修订；终版=最晚 ok 版。
"""
import json

from fa.agentline.runner import build_prompt  # noqa: F401  (Task 4 引擎复用)

ATTACK_ENUM_HELP = ("overconfidence|missing_context|alt_explanation"
                    "|internal_inconsistency|evidence_weak")


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
