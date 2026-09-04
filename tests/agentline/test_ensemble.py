"""A_multi 聚合规则测试——纯函数。分量中位数/KL 最近 digest/URL 去重并集。"""
import json
import math

import pytest

from fa.agentline.ensemble import aggregate_predictions


def _ok(ph, pd_, pa, po=0.5, conf=0.6, digest="d", sources=None):
    return {"status": "ok", "p_home": ph, "p_draw": pd_, "p_away": pa,
            "p_over25": po, "confidence": conf, "reasoning_digest": digest,
            "sources_json": json.dumps(sources or []), "repaired": False}


def test_component_median_and_renormalize():
    """三项分量各取中位数；中位数和超容差按比例归一。"""
    m = aggregate_predictions([
        _ok(0.50, 0.30, 0.20), _ok(0.40, 0.35, 0.25), _ok(0.45, 0.25, 0.30)])
    assert m["status"] == "ok"
    # 分量中位数：0.45/0.30/0.25，和=1.00 → 不归一
    assert m["p_home"] == pytest.approx(0.45)
    assert m["p_draw"] == pytest.approx(0.30)
    assert m["p_away"] == pytest.approx(0.25)


def test_median_sum_off_tolerance_normalizes():
    m = aggregate_predictions([
        _ok(0.6, 0.3, 0.2), _ok(0.5, 0.3, 0.2), _ok(0.2, 0.3, 0.3)])
    # 中位数 0.5/0.3/0.2 和=1.0 容差内；换一组让中位和=1.3（brief 原稿此处
    # 第二成员 p_draw 误写 0.3，中位实为 0.6 需两席 ≥0.6——按断言语义修正）：
    m2 = aggregate_predictions([
        _ok(0.5, 0.3, 0.2), _ok(0.5, 0.6, 0.2), _ok(0.5, 0.6, 0.2)])
    # p_draw 中位 0.6 → 和 1.3 超差 → 归一
    s = m2["p_home"] + m2["p_draw"] + m2["p_away"]
    assert s == pytest.approx(1.0, abs=1e-9)
    assert m2["p_draw"] == pytest.approx(0.6 / 1.3)


def test_over25_and_confidence_median():
    m = aggregate_predictions([
        _ok(0.4, 0.3, 0.3, po=0.7, conf=0.9),
        _ok(0.4, 0.3, 0.3, po=0.5, conf=0.5),
        _ok(0.4, 0.3, 0.3, po=0.6, conf=0.7)])
    assert m["p_over25"] == pytest.approx(0.6)
    assert m["confidence"] == pytest.approx(0.7)


def test_kl_nearest_digest_tie_lowest_index():
    """digest 取与聚合概率向量 KL 最近成员；平票取序最小。"""
    m = aggregate_predictions([
        _ok(0.5, 0.3, 0.2, digest="far"),      # KL 大
        _ok(0.4, 0.3, 0.3, digest="near"),     # KL 小（聚合=中位 0.4/0.3/0.3）
        _ok(0.4, 0.3, 0.3, digest="tied-near")])
    # 成员 2/3 KL 同为 0 → 平票取序最小 → near
    assert m["reasoning_digest"] == "near"


def test_sources_union_dedup_by_url():
    m = aggregate_predictions([
        _ok(0.4, 0.3, 0.3, sources=[{"title": "a", "date": "d1",
                                      "url": "u1"}]),
        _ok(0.4, 0.3, 0.3, sources=[
            {"title": "a-dup", "date": "d1", "url": "u1"},
            {"title": "b", "date": "d2", "url": "u2"}]),
        _ok(0.4, 0.3, 0.3)])
    urls = [s["url"] for s in json.loads(m["sources_json"])]
    assert urls == ["u1", "u2"]


def test_failed_members_excluded_but_k1_aggregates():
    fail = {"status": "parse_fail", "p_home": None, "p_draw": None,
            "p_away": None, "p_over25": None, "confidence": None,
            "reasoning_digest": "x", "sources_json": "[]", "repaired": False}
    m = aggregate_predictions([_ok(0.4, 0.3, 0.3), fail, None])
    assert m["status"] == "ok" and m["p_home"] == pytest.approx(0.4)


def test_k0_all_failed_error():
    m = aggregate_predictions([None, None, None])
    assert m["status"] == "error"
    assert m["p_home"] is None and "k=0" in m["reasoning_digest"]
