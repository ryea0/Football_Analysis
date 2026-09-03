"""契约校验（设计 §4）：约束输出——ok 或 parse_fail，绝不脑补。"""
import json

from fa.agentline.contract import parse_prediction

_OK = json.dumps({"p_home": 0.45, "p_draw": 0.28, "p_away": 0.27,
                  "p_over25": 0.55, "confidence": 0.6,
                  "reasoning_digest": "主场强势", "sources": []})


def test_ok_passthrough():
    r = parse_prediction(_OK)
    assert r["status"] == "ok"
    assert abs(r["p_home"] - 0.45) < 1e-9 and r["repaired"] is False


def test_strips_markdown_fence_and_text_around():
    raw = "分析如下……\n```json\n" + _OK + "\n```\n以上。"
    r = parse_prediction(raw)
    assert r["status"] == "ok"
    # 救援路径（直解失败→提取成功）必须如实记 repaired=True：救援率是设计标注的
    # 实验性观测数据，此前恒为 0 等于把指标测死（spike：60%→100% 的差异被抹掉）。
    assert r["repaired"] is True


def test_wrapped_in_prose_without_fence_is_repaired_too():
    r = parse_prediction("结论：" + _OK + " 以上。")
    assert r["status"] == "ok" and r["repaired"] is True


def test_bare_json_is_not_repaired():
    # 直解一次成功 = 模型输出了纯净 JSON，不得记成救援（否则指标虚高）。
    r = parse_prediction(_OK)
    assert r["repaired"] is False
    # 显式覆盖（repaired_ok=True）仍然生效——调用方可以强制标记。
    assert parse_prediction(_OK, repaired_ok=True)["repaired"] is True


def test_sums_renormalized():
    r = parse_prediction(json.dumps({"p_home": 0.5, "p_draw": 0.3,
                                     "p_away": 0.3, "p_over25": 0.5,
                                     "confidence": 0.5,
                                     "reasoning_digest": "", "sources": []}))
    assert r["status"] == "ok"
    assert abs(r["p_home"] + r["p_draw"] + r["p_away"] - 1.0) < 1e-9


def test_negative_or_missing_probability_is_parse_fail():
    bad = json.dumps({"p_home": -0.1, "p_draw": 0.9, "p_away": 0.2,
                      "p_over25": 0.5, "confidence": 0.5,
                      "reasoning_digest": "", "sources": []})
    assert parse_prediction(bad)["status"] == "parse_fail"
    assert parse_prediction('{"unexpected": 1}')["status"] == "parse_fail"
    assert parse_prediction("不是 JSON")["status"] == "parse_fail"


def test_nan_and_infinity_are_parse_fail():
    for bad in ('{"p_home": NaN, "p_draw": 0.3, "p_away": 0.3, "p_over25": 0.5,'
                ' "confidence": 0.5, "reasoning_digest": "", "sources": []}',
                '{"p_home": Infinity, "p_draw": 0.3, "p_away": 0.3,'
                ' "p_over25": 0.5, "confidence": 0.5,'
                ' "reasoning_digest": "", "sources": []}'):
        r = parse_prediction(bad)
        assert r["status"] == "parse_fail", bad
        assert r["p_home"] is None


def test_all_zero_probs_is_parse_fail():
    r = parse_prediction('{"p_home": 0, "p_draw": 0, "p_away": 0,'
                         ' "p_over25": 0.5, "confidence": 0.5,'
                         ' "reasoning_digest": "", "sources": []}')
    assert r["status"] == "parse_fail" and r["p_home"] is None


def test_parse_fail_carries_reason_not_numbers():
    r = parse_prediction("不是 JSON")
    assert r["p_home"] is None
    assert "原因" in r["reasoning_digest"] or r["reasoning_digest"]
