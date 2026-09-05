import json

from fa.agentline.division import (build_archivist_prompt,
                                   build_challenger_prompt,
                                   build_predictor_prompt, derive_flags,
                                   run_match_division)

INFO = {"match": {"date": "2026-05-01", "home": "A", "away": "B"},
        "history": {"h2h": [], "form": []}}
HIST = json.dumps({"h2h_points": [{"point": "p1", "relevance": "high"}],
                   "recent_form_points": []})
PRED = json.dumps({"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2,
                   "p_over25": 0.5, "confidence": 0.6,
                   "reasoning_digest": "d", "sources": []})
ATK = json.dumps({"attacks": [{"label": "overconfidence", "reason": "r",
                               "severity": 0.9}]})


def _call_seq(seq):
    prompts = []

    def call(prompt):
        prompts.append(prompt)
        return seq[len(prompts) - 1], None, 0.01
    return call, prompts


def _call_triples(seq):
    """dsh 层失败用例：call 返回 (out, err, dur) 三元组序列。"""
    prompts = []

    def call(prompt):
        prompts.append(prompt)
        return seq[len(prompts) - 1]
    return call, prompts


def test_prompts_carry_sections():
    assert "历史数据考古官" in build_archivist_prompt(INFO)
    assert "# 历史考古官要点" in build_predictor_prompt(INFO, json.loads(HIST))
    assert "# 历史考古官要点" not in build_predictor_prompt(INFO, None)
    cp = build_challenger_prompt(INFO, PRED)
    assert "质询" in cp and PRED in cp


def test_happy_path_three_jumps():
    call, prompts = _call_seq([HIST, PRED, ATK])
    res = run_match_division(call, INFO)
    assert res["n_calls"] == 3
    assert [j["role"] for j in res["jumps"]] == [
        "archivist", "predictor", "challenger"]
    assert res["final"]["status"] == "ok"
    assert abs(res["final"]["p_home"] - 0.5) < 1e-9


def test_jump1_fail_predictor_gets_raw_info():
    call, prompts = _call_seq(["垃圾", PRED, ATK])
    res = run_match_division(call, INFO)
    assert res["jumps"][0]["status"] == "parse_fail"
    assert "# 历史考古官要点" not in prompts[1]   # 退化为原始信息集
    assert res["final"]["status"] == "ok"


def test_jump2_fail_no_jump3_two_calls():
    call, prompts = _call_seq([HIST, "垃圾"])
    res = run_match_division(call, INFO)
    assert res["final"]["status"] == "parse_fail"
    assert res["n_calls"] == 2 and len(res["jumps"]) == 2


def test_jump3_fail_final_unchanged():
    call, prompts = _call_seq([HIST, PRED, "垃圾"])
    res = run_match_division(call, INFO)
    assert res["final"]["status"] == "ok"
    assert abs(res["final"]["p_home"] - 0.5) < 1e-9
    assert res["jumps"][2]["status"] == "parse_fail"


def test_predictor_prompt_includes_archivist_points():
    call, prompts = _call_seq([HIST, PRED, ATK])
    run_match_division(call, INFO)
    assert "p1" in prompts[1]                     # 要点内容进了预测者输入


def test_derive_flags_labels_and_jump_fails():
    rows = [
        {"jump": 1, "status": "ok", "payload": HIST},
        {"jump": 2, "status": "ok", "payload": PRED},
        {"jump": 3, "status": "ok", "payload": ATK},
    ]
    assert derive_flags(rows) == {"overconfidence": 1}
    rows2 = [
        {"jump": 1, "status": "parse_fail", "payload": ""},
        {"jump": 2, "status": "ok", "payload": PRED},
        {"jump": 3, "status": "error", "payload": ""},
    ]
    assert derive_flags(rows2) == {"jump1_fail": 1, "jump3_fail": 1}
    rows3 = [  # 跳2 失败→跳3 未发起：不记 jump3_fail
        {"jump": 1, "status": "ok", "payload": HIST},
        {"jump": 2, "status": "parse_fail", "payload": ""},
    ]
    assert derive_flags(rows3) == {"jump2_fail": 1}


def test_jump1_dsh_timeout_degrades_predictor_and_records_error():
    """dsh 层失败（err≠None）：status=timeout 且 payload 记真实根因——
    不得残留 parse_fail 的误导性 error（review fix round 1）。"""
    call, prompts = _call_triples([
        (None, "dsh headless 超时（600s）", 0.01),
        (PRED, None, 0.01),
        (ATK, None, 0.01),
    ])
    res = run_match_division(call, INFO)
    assert res["jumps"][0]["status"] == "timeout"
    payload = json.loads(res["jumps"][0]["payload"])
    assert payload["error"] == "dsh 失败：dsh headless 超时（600s）"
    assert "# 历史考古官要点" not in prompts[1]   # dsh 失败同走退化路径
    assert res["final"]["status"] == "ok"


def test_jump3_dsh_error_final_unchanged():
    call, _ = _call_triples([
        (HIST, None, 0.01),
        (PRED, None, 0.01),
        (None, "dsh 退出码 1：boom", 0.01),
    ])
    res = run_match_division(call, INFO)
    assert res["jumps"][2]["status"] == "error"
    payload = json.loads(res["jumps"][2]["payload"])
    assert "dsh 失败" in payload["error"] and "boom" in payload["error"]
    assert res["final"]["status"] == "ok"          # 跳3 失败不动终版
    assert abs(res["final"]["p_home"] - 0.5) < 1e-9


def test_derive_flags_empty_attacks_no_label():
    rows = [
        {"jump": 1, "status": "ok", "payload": HIST},
        {"jump": 2, "status": "ok", "payload": PRED},
        {"jump": 3, "status": "ok",
         "payload": json.dumps({"attacks": []})},
    ]
    assert derive_flags(rows) == {}


def test_derive_flags_same_label_accumulates():
    atk2 = json.dumps({"attacks": [
        {"label": "overconfidence", "reason": "r1", "severity": 0.5},
        {"label": "overconfidence", "reason": "r2", "severity": 0.4},
    ]})
    rows = [
        {"jump": 1, "status": "ok", "payload": HIST},
        {"jump": 2, "status": "ok", "payload": PRED},
        {"jump": 3, "status": "ok", "payload": atk2},
    ]
    assert derive_flags(rows) == {"overconfidence": 2}
