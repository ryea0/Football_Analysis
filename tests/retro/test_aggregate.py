"""聚合规则测试——纯函数。规则逐字段对 spec §4 表；成员 = validate_output 结果形态。"""
import pytest

from fa.retro.aggregate import aggregate_members


def _ok(primary, tags=None, conf=0.8, mvm="model_wrong", ev=None, digest="d"):
    return {"status": "ok", "parsed": {
        "miss_tags": tags or [primary], "primary_tag": primary,
        "tags_confidence": conf, "model_vs_market": mvm,
        "evidence": ev or [{"title": "t", "date": "2024-04-01",
                            "url": "https://e.com"}],
        "digest": digest}}


def _fail(status):
    return {"status": status, "parsed": None}


def test_majority_primary_and_tags():
    m = aggregate_members([
        _ok("injury", ["injury", "motivation"], digest="m1"),
        _ok("injury", ["injury"], digest="m2"),
        _ok("motivation", ["motivation"], digest="m3")])
    assert m["status"] == "ok" and m["primary_tag"] == "injury"
    assert m["miss_tags"] == ["injury"]          # motivation 仅 1 票 <2
    assert m["digest"] == "m1"                   # primary 一致且序最小
    assert m["model_vs_market"] == "model_wrong"  # 2/3 多数
    assert m["tags_confidence"] == pytest.approx(0.8)


def test_no_majority_nulls_and_fixed_digest():
    m = aggregate_members([
        _ok("injury"), _ok("motivation"), _ok("news")])
    assert m["status"] == "ok"
    assert m["primary_tag"] is None and m["model_vs_market"] is None
    assert m["miss_tags"] is None                 # 无标签达到 2 次
    assert "无多数" in m["digest"] and "injury" in m["digest"]


def test_evidence_union_dedup_by_url_primary_filtered():
    m = aggregate_members([
        _ok("injury", ev=[{"title": "a", "date": "2024-04-01",
                           "url": "u1"},
                          {"title": "b", "date": "2024-04-02",
                           "url": "u2"}]),
        _ok("injury", ev=[{"title": "a-dup", "date": "2024-04-01",
                           "url": "u1"},
                          {"title": "c", "date": "2024-04-03",
                           "url": "u3"}]),
        _ok("motivation", ev=[{"title": "other", "date": "2024-04-04",
                               "url": "u9"}])])
    urls = [e["url"] for e in m["evidence"]]
    assert urls == ["u1", "u2", "u3"]             # 去重保序；非 primary 成员(u9)不入


def test_no_majority_evidence_takes_all_members():
    m = aggregate_members([
        _ok("injury", ev=[{"title": "a", "date": "d1", "url": "u1"}]),
        _ok("motivation", ev=[{"title": "b", "date": "d2", "url": "u2"}]),
        _ok("news")])
    # 全部成员（含 _ok 默认证据 https://e.com）都入并集——spec §4「无多数时取全部成员」
    assert {e["url"] for e in m["evidence"]} == {"u1", "u2", "https://e.com"}


def test_median_confidence():
    m = aggregate_members([
        _ok("injury", conf=0.9), _ok("injury", conf=0.5),
        _ok("injury", conf=0.7)])
    assert m["tags_confidence"] == pytest.approx(0.7)


def test_k0_all_failed_is_error():
    m = aggregate_members([_fail("timeout"), _fail("parse_fail"),
                           _fail("timeout")])
    assert m["status"] == "error" and m["primary_tag"] is None
    assert m["evidence"] is None


def test_partial_members_still_aggregate():
    """k=2（1 名 parse_fail）：2 票一致即多数。"""
    m = aggregate_members([_ok("injury"), _ok("injury"), _fail("parse_fail")])
    assert m["status"] == "ok" and m["primary_tag"] == "injury"


def test_two_ok_disagree_is_no_majority():
    m = aggregate_members([_ok("injury"), _ok("news"), _fail("error")])
    assert m["primary_tag"] is None and m["status"] == "ok"


def test_miss_tags_follow_tags_enum_order():
    m = aggregate_members([
        _ok("news", ["news", "injury"]), _ok("injury", ["injury", "news"]),
        _ok("variance")])
    assert m["miss_tags"] == ["injury", "news"]   # TAGS 枚举序，非出现序
