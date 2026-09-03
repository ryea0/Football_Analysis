"""归因输出契约：封闭标签枚举 + 校验 + JSON 修复 + parse_fail 判定。

设计文档 §6：约束输出、不约束过程。校验失败一律 parse_fail（诚实降级），
绝不脑补字段；破损 JSON 先做「首 { 到末 }」截取修复（repaired=True），
仍不契约则弃用。digest ≤200 字（len() 计，中文 1 字）。恒不抛异常。
"""
import json

TAGS = ("injury", "rotation", "motivation", "congestion", "news",
        "market_info", "model_limitation", "variance")
TAG_SET_VERSION = "v1"
_MODEL_VS_MARKET = ("model_wrong", "market_wrong", "both_off", "variance")
# 赛前成因类标签：证据日期必须早于开球日（audit 关卡用，设计 §8-1）
PREMATCH_CAUSE_TAGS = frozenset(
    {"injury", "rotation", "motivation", "congestion", "news"})
_MAX_DIGEST = 200


def _fail(reason: str) -> dict:
    return {"status": "parse_fail", "repaired": False, "parsed": None,
            "reason": reason}


def validate_output(raw: str) -> dict:
    """校验 agent 原始返回。恒不抛；返回
    {status: ok|parse_fail, repaired, parsed, reason}。"""
    if not isinstance(raw, str) or not raw.strip():
        return _fail("空输出")
    text, repaired = raw, False
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        lo, hi = text.find("{"), text.rfind("}")
        if lo < 0 or hi <= lo:
            return _fail("输出中找不到 JSON 对象")
        try:
            obj = json.loads(text[lo:hi + 1])
            repaired = True
        except json.JSONDecodeError as exc:
            return _fail(f"JSON 修复后仍不可解析: {exc}")
    if not isinstance(obj, dict):
        return _fail("JSON 顶层不是对象")

    tags = obj.get("miss_tags")
    if not isinstance(tags, list) or not tags:
        return _fail("miss_tags 须为非空数组")
    if any(t not in TAGS for t in tags):
        return _fail(f"miss_tags 含枚举外标签: {tags}")
    primary = obj.get("primary_tag")
    if primary not in tags:
        return _fail(f"primary_tag {primary!r} 不在 miss_tags 内")
    conf = obj.get("tags_confidence")
    if not isinstance(conf, (int, float)) or isinstance(conf, bool) \
            or not 0.0 <= conf <= 1.0:
        return _fail(f"tags_confidence 须在 [0,1]，收到 {conf!r}")
    if obj.get("model_vs_market") not in _MODEL_VS_MARKET:
        return _fail(f"model_vs_market 须为 {'/'.join(_MODEL_VS_MARKET)}")
    digest = obj.get("digest")
    if not isinstance(digest, str) or not digest:
        return _fail("digest 缺失或为空")
    if len(digest) > _MAX_DIGEST:
        return _fail(f"digest {len(digest)} 字 > {_MAX_DIGEST}")
    ev = obj.get("evidence")
    if not isinstance(ev, list):
        return _fail("evidence 须为数组（可为空）")
    for i, e in enumerate(ev):
        if not isinstance(e, dict) or not all(
                e.get(k) for k in ("title", "date", "url")):
            return _fail(f"evidence[{i}] 须含 title/date/url 且非空")
    return {"status": "ok", "repaired": repaired, "parsed": obj,
            "reason": None}
