"""A_multi 聚合规则（multi-brain spec §3.2，确定性/预注册）。

聚合行由 Python 构造、不经 parse_prediction 的救援路径——救援语义只属于
成员的 LLM 输出。KL 用自然对数、对三项概率向量计算。零质量规则
（预注册 2026-09-04，multi-brain 设计 §3.2）：聚合向量某分量为 0 而成员
该分量 >0 时 KL=inf（永不当选最近）；三中位全 0 → error 行——聚合层
镜像契约侧对零和的拒绝语义，绝不抛异常。
"""
import json
import math
import statistics

_SUM_TOL = 0.05      # 与 contract.parse_prediction 同口径（单源：契约侧常量
                     # 为私有，此处复制并注释锚定——两处容差语义必须同步改）


def _kl(p: tuple[float, ...], q: tuple[float, ...]) -> float:
    if any(pi > 0 and qi <= 0 for pi, qi in zip(p, q)):
        return math.inf                       # 零质量规则：该成员不当选最近
    return sum(pi * math.log(pi / qi) for pi, qi in zip(p, q) if pi > 0)


def aggregate_predictions(members: list) -> dict:
    ok = [m for m in members
          if m is not None and m.get("status") == "ok"]
    if not ok:
        return {"status": "error", "p_home": None, "p_draw": None,
                "p_away": None, "p_over25": None, "confidence": None,
                "reasoning_digest": "全员失败（k=0），见成员行",
                "sources_json": "[]", "repaired": False}
    ph = statistics.median(m["p_home"] for m in ok)
    pd_ = statistics.median(m["p_draw"] for m in ok)
    pa = statistics.median(m["p_away"] for m in ok)
    total = ph + pd_ + pa
    if total == 0:                                 # 聚合退化：中位和为 0 → error
        return {"status": "error", "p_home": None, "p_draw": None,
                "p_away": None, "p_over25": None, "confidence": None,
                "reasoning_digest": "聚合退化（k>0，中位和为 0），见成员行",
                "sources_json": "[]", "repaired": False}
    if abs(total - 1.0) > _SUM_TOL and total > 0:
        ph, pd_, pa = ph / total, pd_ / total, pa / total
    agg_vec = (ph, pd_, pa)
    best, best_kl = None, None
    for m in ok:                                   # 序即 attributor 序，平票取先
        k = _kl((m["p_home"], m["p_draw"], m["p_away"]), agg_vec)
        if best is None or k < best_kl:
            best, best_kl = m, k
    seen, sources = set(), []
    for m in ok:
        for s in json.loads(m["sources_json"] or "[]"):
            if s.get("url") not in seen:
                seen.add(s.get("url"))
                sources.append(s)
    return {"status": "ok", "p_home": ph, "p_draw": pd_, "p_away": pa,
            "p_over25": statistics.median(m["p_over25"] for m in ok),
            "confidence": statistics.median(m["confidence"] for m in ok),
            "reasoning_digest": best["reasoning_digest"],
            "sources_json": json.dumps(sources, ensure_ascii=False),
            "repaired": False}
