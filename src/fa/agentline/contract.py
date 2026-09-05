"""线 A 预测契约校验（设计 §4）：约束输出——合法则归一，不合法则 parse_fail。

绝不脑补：任何字段缺失/越界/非 JSON 一律整场弃用（status=parse_fail），
失败原因写 reasoning_digest。三项概率和容差 ±0.05，超差按比例归一
（容差内不动——避免无谓扰动 agent 的原始判断）。
"""
import json
import math
import re

_SUM_TOL = 0.05
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json(raw: str) -> dict:
    """剥离 markdown 围栏与前后杂文字，取第一个平衡的 {...}。"""
    text = _FENCE_RE.search(raw).group(1) if _FENCE_RE.search(raw) else raw
    start = text.find("{")
    if start < 0:
        raise ValueError("输出中没有 JSON 对象")
    depth = 0
    for i, ch in enumerate(text[start:], start):
        depth += (ch == "{") - (ch == "}")
        if depth == 0:
            return json.loads(text[start:i + 1])
    raise ValueError("JSON 对象未闭合")


def parse_prediction(raw: str, repaired_ok: bool = False) -> dict:
    fail = {"status": "parse_fail", "p_home": None, "p_draw": None,
            "p_away": None, "p_over25": None, "confidence": None,
            "reasoning_digest": "", "sources_json": "[]",
            "repaired": repaired_ok}
    repaired = repaired_ok                   # 显式覆盖优先；默认路径自行判定
    try:
        try:
            obj = json.loads(raw.strip())    # 直解优先：纯净输出不算救援
        except json.JSONDecodeError:
            # 直解失败才走救援提取（剥围栏/前后杂文字）。救援成功 = 模型输出了
            # 非纯净 JSON，必须记 repaired=True——救援率是设计标注的实验性观测
            # 数据，之前恒记 0 等于把 60%→100% 的差异抹掉（spike 附录 A）。
            obj = _extract_json(raw)
            repaired = True
        ph, pd, pa = (float(obj["p_home"]), float(obj["p_draw"]),
                      float(obj["p_away"]))
        po = float(obj["p_over25"])
        # json.loads 接受 NaN/Infinity 字面量，且与 nan 的一切比较均为 False，
        # 越界检查会空过——故先显式排除非有限值，再查范围。
        if (any(math.isnan(v) or math.isinf(v) for v in (ph, pd, pa, po))
                or min(ph, pd, pa, po) < 0 or max(ph, pd, pa) > 1
                or not 0 <= po <= 1):
            raise ValueError("概率非有限值或越界")
        total = ph + pd + pa
        if total == 0:
            raise ValueError("三项概率和为零")
        if abs(total - 1.0) > _SUM_TOL:
            ph, pd, pa = ph / total, pd / total, pa / total   # 比例归一
        return {"status": "ok", "p_home": ph, "p_draw": pd, "p_away": pa,
                "p_over25": po,
                "confidence": float(obj.get("confidence", 0.0)),
                "reasoning_digest": str(obj.get("reasoning_digest", ""))[:200],
                "sources_json": json.dumps(obj.get("sources", []),
                                           ensure_ascii=False),
                "repaired": repaired}
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        fail["reasoning_digest"] = f"parse_fail 原因：{type(exc).__name__}: {exc}"
        return fail


ATTACK_LABELS = ("overconfidence", "missing_context", "alt_explanation",
                 "internal_inconsistency", "evidence_weak")
_PROB_FIELDS = ("p_home", "p_draw", "p_away", "p_over25")


def parse_attack(raw: str, repaired_ok: bool = False) -> dict:
    """批评者/质询官攻击契约（2026-09-05 设计 §2.3/§3.3）。

    封闭五标签 + reason≤200 字 + severity∈[0,1] 有限值；attacks 可为空
    （无攻击点=合法）。禁概率数字：obj 出现任一 p_* 字段即 parse_fail
    （守卫与 parse_prediction 的「绝不脑补」同一风格）。
    """
    repaired = repaired_ok
    try:
        try:
            obj = json.loads(raw.strip())
        except json.JSONDecodeError:
            obj = _extract_json(raw)
            repaired = True
        if any(f in obj for f in _PROB_FIELDS):
            raise ValueError("攻击 JSON 出现概率字段（批评者禁数字）")
        items = obj.get("attacks")
        if not isinstance(items, list):
            raise ValueError("attacks 必须是数组")
        out = []
        for it in items:
            label = it["label"]
            if label not in ATTACK_LABELS:
                raise ValueError(f"label 越界：{label}")
            sev = float(it["severity"])
            if math.isnan(sev) or math.isinf(sev) or not 0 <= sev <= 1:
                raise ValueError("severity 非有限值或越界")
            out.append({"label": label,
                        "reason": str(it.get("reason", ""))[:200],
                        "severity": sev})
        return {"status": "ok", "attacks": out, "repaired": repaired}
    except (KeyError, ValueError, TypeError, json.JSONDecodeError,
            AttributeError) as exc:
        return {"status": "parse_fail", "attacks": [],
                "error": f"parse_fail 原因：{type(exc).__name__}: {exc}",
                "repaired": repaired}
