"""契约校验测试——纯函数，无 IO。数值口径：digest 以 len() 计（中文字符 1 个/字）。"""
import json

from fa.retro.contract import (TAGS, TAG_SET_VERSION, PREMATCH_CAUSE_TAGS,
                               validate_output)

VALID = json.dumps({
    "miss_tags": ["injury", "motivation"],
    "primary_tag": "injury",
    "tags_confidence": 0.7,
    "model_vs_market": "model_wrong",
    "evidence": [{"title": "Arsenal injury news",
                  "date": "2024-04-28", "url": "https://example.com/a"}],
    "digest": "模型高估主胜：主队三名主力伤停，市场赛前已计入。"}, ensure_ascii=False)


def test_valid_payload_passes():
    out = validate_output(VALID)
    assert out["status"] == "ok" and out["parsed"]["primary_tag"] == "injury"
    assert out["repaired"] is False and out["reason"] is None
    assert set(out["parsed"]["miss_tags"]) == {"injury", "motivation"}


def test_junk_around_json_is_repaired():
    """agent 常在 JSON 前后裹话——首 { 到末 } 截取，repaired=True。"""
    out = validate_output("好的，以下是归因结果：\n" + VALID + "\n以上。")
    assert out["status"] == "ok" and out["repaired"] is True


def test_broken_json_is_parse_fail():
    out = validate_output('{"miss_tags": ["injury", ')
    assert out["status"] == "parse_fail" and out["parsed"] is None
    assert out["reason"]


def test_tag_outside_enum_fails():
    bad = json.loads(VALID); bad["miss_tags"] = ["weather"]
    out = validate_output(json.dumps(bad))
    assert out["status"] == "parse_fail"


def test_empty_tags_fails():
    bad = json.loads(VALID); bad["miss_tags"] = []
    out = validate_output(json.dumps(bad))
    assert out["status"] == "parse_fail"


def test_primary_not_in_tags_fails():
    bad = json.loads(VALID); bad["primary_tag"] = "news"
    out = validate_output(json.dumps(bad))
    assert out["status"] == "parse_fail"


def test_confidence_out_of_range_fails():
    bad = json.loads(VALID); bad["tags_confidence"] = 1.3
    assert validate_output(json.dumps(bad))["status"] == "parse_fail"
    bad["tags_confidence"] = -0.1
    assert validate_output(json.dumps(bad))["status"] == "parse_fail"


def test_bad_model_vs_market_fails():
    bad = json.loads(VALID); bad["model_vs_market"] = "both_wrong"
    assert validate_output(json.dumps(bad))["status"] == "parse_fail"


def test_long_digest_fails():
    bad = json.loads(VALID); bad["digest"] = "长" * 201
    assert validate_output(json.dumps(bad))["status"] == "parse_fail"


def test_digest_boundary_200_passes():
    bad = json.loads(VALID); bad["digest"] = "长" * 200
    assert validate_output(json.dumps(bad))["status"] == "ok"


def test_evidence_entry_missing_date_fails():
    bad = json.loads(VALID)
    bad["evidence"] = [{"title": "x", "url": "https://e.com"}]
    assert validate_output(json.dumps(bad))["status"] == "parse_fail"


def test_evidence_may_be_empty_list():
    """model_limitation/variance 允许零证据（设计 §8 关卡 1）。"""
    bad = json.loads(VALID); bad["evidence"] = []
    assert validate_output(json.dumps(bad))["status"] == "ok"


def test_vocab_constants():
    assert TAGS == ("injury", "rotation", "motivation", "congestion", "news",
                    "market_info", "model_limitation", "variance")
    assert TAG_SET_VERSION == "v1"


def test_prematch_cause_tags():
    """清单 #5 命名契约：生产常量名逐一核对。"""
    assert PREMATCH_CAUSE_TAGS == frozenset(
        {"injury", "rotation", "motivation", "congestion", "news"})
