import json

import pytest

from fa.agentline.debate import (ATTACK_ENUM_HELP, build_critic_prompt,
                                 build_revision_prompt, max_abs_delta,
                                 run_match_debate)

INFO = {"match": {"date": "2026-05-01", "home": "A", "away": "B"}}


def test_critic_prompt_carries_info_and_prev():
    p = build_critic_prompt(INFO, '{"p_home": 0.5}')
    assert "A" in p and '{"p_home": 0.5}' in p
    assert all(w in p for w in ATTACK_ENUM_HELP.split("|"))


def test_critic_prompt_second_round_includes_prev_attack():
    p = build_critic_prompt(INFO, "v1", prev_attack='{"attacks": []}')
    assert '# 前轮攻击' in p                     # 标题段（payload 子串同时出现在静态指令行，单独断言无效）
    assert '{"attacks": []}' in p


def test_revision_prompt_carries_all_three_sections():
    p = build_revision_prompt(INFO, "PREV", "ATK")
    assert "PREV" in p and "ATK" in p and "2026-05-01" in p


def test_max_abs_delta_direction_and_value():
    a = {"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2, "p_over25": 0.99}
    b = {"p_home": 0.52, "p_draw": 0.30, "p_away": 0.18}
    # brief 原文为精确 == 0.02，但 0.52-0.5 在 IEEE-754 下=0.020000000000000018，
    # 依本仓 float 断言惯例（tests/test_cli.py 等）改 approx；对称性断言保持精确。
    assert max_abs_delta(a, b) == pytest.approx(0.02)   # 0.02 与 0.02 并列取 max
    assert max_abs_delta(b, a) == max_abs_delta(a, b)   # 对称性：方向无关
    # 三键口径：p_over25 差 0.99 不入 max——结果仍是胜平负三键的最大差


OK0 = json.dumps({"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2,
                  "p_over25": 0.5, "confidence": 0.6, "reasoning_digest": "d",
                  "sources": []})
OK1 = json.dumps({"p_home": 0.55, "p_draw": 0.28, "p_away": 0.17,
                  "p_over25": 0.5, "confidence": 0.6, "reasoning_digest": "d",
                  "sources": []})
OK2 = json.dumps({"p_home": 0.6, "p_draw": 0.25, "p_away": 0.15,
                  "p_over25": 0.5, "confidence": 0.6, "reasoning_digest": "d",
                  "sources": []})
ATK = json.dumps({"attacks": [{"label": "overconfidence", "reason": "r",
                               "severity": 0.8}]})


def _call_seq(seq):
    it = iter(seq)
    def call(prompt):
        out = next(it)
        return out, None, 0.01
    return call


def test_v0_fail_short_circuits():
    res = run_match_debate(_call_seq(["不是JSON"]), INFO)
    assert res["final"]["status"] == "parse_fail"
    assert res["n_calls"] == 1 and len(res["rounds"]) == 1
    assert res["budget_exhausted"] == 0 and res["early_stop"] == 0


def test_critic_fail_aborts_round_with_budget_exhausted():
    res = run_match_debate(_call_seq([OK0, "垃圾"]), INFO)
    assert res["final"]["status"] == "ok"          # 终版=v0（最晚 ok）
    assert abs(res["final"]["p_home"] - 0.5) < 1e-9
    assert res["budget_exhausted"] == 1 and res["early_stop"] == 0
    assert res["n_calls"] == 2
    assert [r["role"] for r in res["rounds"]] == ["generator", "critic"]


def test_critic_dsh_fail_cause_lands_in_payload():
    """批评者 dsh 层失败：真实根因入 payload（生成者侧 _status_of 同式），
    不能只剩契约解析错误掩盖 dsh 失败。"""
    seq = iter([(OK0, None, 0.01), (None, "dsh 退出码 1：boom", 0.5)])
    res = run_match_debate(lambda p: next(seq), INFO)
    critic = next(r for r in res["rounds"] if r["role"] == "critic")
    assert critic["status"] == "error"
    assert "dsh 失败" in critic["payload"] and "boom" in critic["payload"]
    assert res["budget_exhausted"] == 1 and res["early_stop"] == 0


def test_revision_fail_takes_last_ok():
    res = run_match_debate(_call_seq([OK0, ATK, "垃圾"]), INFO)
    assert abs(res["final"]["p_home"] - 0.5) < 1e-9
    assert res["budget_exhausted"] == 1
    assert res["n_calls"] == 3


def test_early_stop_when_delta_below_eps():
    ok1_eps = json.dumps({"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2,
                          "p_over25": 0.5, "confidence": 0.6,
                          "reasoning_digest": "d", "sources": []})
    # v1==v0：delta 0 < 0.02 → 轮1后停
    res = run_match_debate(_call_seq([OK0, ATK, ok1_eps]), INFO)
    assert res["early_stop"] == 1 and res["n_calls"] == 3
    assert res["budget_exhausted"] == 0
    # v2 对 v1（OK1=0.55/0.28/0.17）delta = max(0.005,0,0.005)=0.005 < 0.02：轮2后停
    ok2_eps = json.dumps({"p_home": 0.555, "p_draw": 0.28, "p_away": 0.165,
                          "p_over25": 0.5, "confidence": 0.6,
                          "reasoning_digest": "d", "sources": []})
    res2 = run_match_debate(_call_seq([OK0, ATK, OK1, ATK, ok2_eps]), INFO)
    assert res2["early_stop"] == 1 and res2["n_calls"] == 5
    assert abs(res2["final"]["p_home"] - 0.555) < 1e-9


def test_delta_exactly_eps_continues():
    # v1 对 v0 的 delta 恰为 0.02（0.52/0.30/0.18）："< ε" 才停，等号继续
    ok_eq = json.dumps({"p_home": 0.52, "p_draw": 0.30, "p_away": 0.18,
                        "p_over25": 0.5, "confidence": 0.6,
                        "reasoning_digest": "d", "sources": []})
    res = run_match_debate(_call_seq([OK0, ATK, ok_eq, ATK, OK2]), INFO)
    assert res["early_stop"] == 0 and res["n_calls"] == 5


def test_full_two_rounds_five_calls():
    res = run_match_debate(_call_seq([OK0, ATK, OK1, ATK, OK2]), INFO)
    assert res["n_calls"] == 5 and res["early_stop"] == 0
    assert res["budget_exhausted"] == 0
    assert abs(res["final"]["p_home"] - 0.6) < 1e-9
    assert [r["role"] for r in res["rounds"]] == [
        "generator", "critic", "generator", "critic", "generator"]


def test_round2_critic_sees_prev_attack():
    seen: list[str] = []   # brief 原稿以 count 为 dict 键会撞键（轮1 各 prompt 均为 0），改按调用序号
    def call(prompt):
        seen.append(prompt)
        nxt = [OK0, ATK, OK1, ATK, OK2][len(seen) - 1]
        return nxt, None, 0.01
    run_match_debate(call, INFO)
    assert seen[0].count("# 前轮攻击") == 0    # v0 生成者：无攻击段
    assert seen[1].count("# 前轮攻击") == 0    # 轮1 批评者：无前轮攻击可看
    assert seen[3].count("# 前轮攻击") == 1    # 轮2 批评者带前轮攻击段（设计 §2.1）
    assert '"label": "overconfidence"' in seen[3]   # 攻击原文全文透传，非空标题
