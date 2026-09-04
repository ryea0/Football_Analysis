import json

import pytest

from fa.agentline.debate import (ATTACK_ENUM_HELP, build_critic_prompt,
                                 build_revision_prompt, max_abs_delta)

INFO = {"match": {"date": "2026-05-01", "home": "A", "away": "B"}}


def test_critic_prompt_carries_info_and_prev():
    p = build_critic_prompt(INFO, '{"p_home": 0.5}')
    assert "A" in p and '{"p_home": 0.5}' in p
    assert all(w in p for w in ATTACK_ENUM_HELP.split("|"))


def test_critic_prompt_second_round_includes_prev_attack():
    p = build_critic_prompt(INFO, "v1", prev_attack='{"attacks": []}')
    assert '{"attacks": []}' in p


def test_revision_prompt_carries_all_three_sections():
    p = build_revision_prompt(INFO, "PREV", "ATK")
    assert "PREV" in p and "ATK" in p and "2026-05-01" in p


def test_max_abs_delta_direction_and_value():
    a = {"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2}
    b = {"p_home": 0.52, "p_draw": 0.30, "p_away": 0.18}
    # brief 原文为精确 == 0.02，但 0.52-0.5 在 IEEE-754 下=0.020000000000000018，
    # 依本仓 float 断言惯例（tests/test_cli.py 等）改 approx；对称性断言保持精确。
    assert max_abs_delta(a, b) == pytest.approx(0.02)   # 0.02 与 0.02 并列取 max
    assert max_abs_delta(b, a) == max_abs_delta(a, b)   # 对称性：方向无关
