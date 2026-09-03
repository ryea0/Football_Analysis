"""ensemble 聚合规则（spec §4，全部确定性/预注册；总纲第一个实例）。

聚合行由 Python 构造、不经 validate_output——契约校验只作用于成员的
LLM 输出；聚合行质量由成员行 + 本规则共同保证。
primary 与 model_vs_market 用严格多数（票数*2 > 可用成员数），且同进退：
无 primary 多数时 mvm 一并 NULL（无多数由 NULL 字段表达）。miss_tags 用
固定阈值「出现 ≥2 次」，计数域与证据域一致 = src（primary 一致成员；
无多数时全部可用成员）；空结果一律 None（不用空串/空数组占位）。
"""
import statistics

from fa.retro.contract import TAGS


def _majority(values: list[str]) -> str | None:
    for v in set(values):
        if values.count(v) * 2 > len(values):
            return v
    return None


def aggregate_members(members: list[dict]) -> dict:
    ok = [m["parsed"] for m in members
          if m.get("status") == "ok" and m.get("parsed")]
    if not ok:
        return {"status": "error", "primary_tag": None, "miss_tags": None,
                "tags_confidence": None, "model_vs_market": None,
                "evidence": None, "digest": "全员失败（k=0），见成员行"}
    primary = _majority([p["primary_tag"] for p in ok])
    if primary is None:
        digest = ("成员无多数（"
                  + "|".join(p["primary_tag"] for p in ok) + "），见成员行")
        src = ok                                   # 证据/计数取全部可用成员
        mvm = None                                 # 无多数 → mvm 一并 NULL
    else:
        src = [p for p in ok if p["primary_tag"] == primary]
        digest = src[0]["digest"]                  # primary 一致且序最小
        mvm = _majority([p["model_vs_market"] for p in ok])
    counts = {t: sum(t in p["miss_tags"] for p in src) for t in TAGS}
    miss = [t for t in TAGS if counts[t] >= 2] or None
    seen, evidence = set(), []
    for p in src:
        for e in p.get("evidence", []):
            if e["url"] not in seen:
                seen.add(e["url"])
                evidence.append(e)
    return {"status": "ok", "primary_tag": primary, "miss_tags": miss,
            "tags_confidence": statistics.median(
                p["tags_confidence"] for p in ok),
            "model_vs_market": mvm, "evidence": evidence, "digest": digest}
