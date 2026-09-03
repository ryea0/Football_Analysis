"""gates：门槛与仓位常量的单一事实源 + 公式落点（spec §5.2 / §5.3）。

边界两侧都给出**手工验算**（M2 教训：阈值可达性自己算）：

- EV：``ev_of(0.515, 2.0) = 0.515·1 − 0.485 = 0.030000000000000027 ≥ EV_MIN``；
  ``ev_of(0.5149, 2.0) = 0.02980000000000005 < EV_MIN``。位级等于 float(0.03)
  不可达（两个操作数比值 < 2 → 末次减法精确 → 落在 2^-52 网格，而 0.03 在
  2^-57 网格），故用「最近可达值夹住常量」的口径。
- Kelly 上限：``odds=2.0`` 时 ``f = 0.25·(2p−1)/1 = 0.5p − 0.25``，``p = 0.54``
  给 ``0.27 − 0.25 = 0.02``，与 STAKE_CAP **位级相等**（乘 0.5 是精确的指数
  缩放）——真·可达上限，不是近似夹逼。
"""
import inspect
import math

import pytest

import fa.backtest.simulate as sim
from fa.backtest.simulate import candidates, simulate_kelly
from fa.value import gates
from fa.value.gates import (EDGE_MIN, EV_MIN, KELLY_FRAC, ODDS_MAX, ODDS_MIN,
                            STAKE_CAP, ev_of, kelly_fraction)


def _bare(**kw):
    """最小候选行：未测市场全 0（edge=0 且赔率 0 出带）永不入选（同 test_simulate）。"""
    base = {"match_id": 9, "league": "E0", "date": "2026-09-05", "outcome": "H",
            "total_goals": 2, "mkt_over25": None,
            "p_home": 0.0, "p_draw": 0.0, "p_away": 0.0,
            "mkt_home": 0.0, "mkt_draw": 0.0, "mkt_away": 0.0,
            "odds_home": 0.0, "odds_draw": 0.0, "odds_away": 0.0}
    base.update(kw)
    return base


# ---------------------------------------------------------------- 常量单源


def test_constants_are_spec_verbatim():
    """spec §5.2/§5.3 逐字：EV≥3%、edge≥2%、赔率带 [1.4,6.0]、¼ Kelly、上限 2%。"""
    assert (EV_MIN, EDGE_MIN) == (0.03, 0.02)
    assert (ODDS_MIN, ODDS_MAX) == (1.4, 6.0)
    assert (KELLY_FRAC, STAKE_CAP) == (0.25, 0.02)


def test_simulate_reexports_the_same_objects():
    """re-export 必须**同一对象**（is），不是同值拷贝——改 gates 一处两处生效。"""
    for name in ("EV_MIN", "EDGE_MIN", "ODDS_MIN", "ODDS_MAX", "KELLY_FRAC", "STAKE_CAP"):
        assert getattr(sim, name) is getattr(gates, name), name


def test_simulate_sources_its_constants_from_gates():
    src = inspect.getsource(sim)
    assert "from fa.value.gates import" in src


# ---------------------------------------------------------------- ev_of


@pytest.mark.parametrize("p,odds", [(0.5, 2.0), (0.55, 2.0), (0.3, 3.4), (0.75, 1.4),
                                    (0.25, 6.0), (0.1, 5.0)])
def test_ev_of_is_the_spec_formula_bit_for_bit(p, odds):
    """EV = p·(o−1) − (1−p)（spec §5.2），与回测路径同一算式、逐位一致。"""
    assert ev_of(p, odds) == p * (odds - 1) - (1 - p)


def test_ev_of_matches_candidates_output():
    """回测 candidates 的 ev 字段改由 ev_of 供给 → 逐位相等（单源化的回归锚）。"""
    row = _bare(p_home=0.55, mkt_home=0.50, odds_home=2.0)
    (cand,) = candidates([row])
    assert cand["ev"] == ev_of(0.55, 2.0)


def test_ev_of_boundary_both_sides():
    assert ev_of(0.515, 2.0) >= EV_MIN                  # 0.030000000000000027
    assert ev_of(0.5149, 2.0) < EV_MIN                  # 0.02980000000000005


def test_ev_of_zero_stake_no_edge_is_negative():
    assert ev_of(0.5, 2.0) == 0.0                       # 公平盘 EV=0，过不了 3% 关


# ---------------------------------------------------------------- kelly_fraction


def test_kelly_cap_boundary_is_bit_reachable():
    """p=0.54、o=2.0 → f = 0.5·0.54 − 0.25 = 0.02，与 STAKE_CAP 位级相等。"""
    assert kelly_fraction(0.54, 2.0) == STAKE_CAP
    assert kelly_fraction(0.5399, 2.0) < STAKE_CAP      # 0.019950000000000023，未触上限


def test_kelly_quarter_factor_and_capping():
    assert kelly_fraction(0.53, 2.0) == pytest.approx(0.015)   # ¼ 段，未触上限
    assert kelly_fraction(0.9, 5.0) == STAKE_CAP               # f_raw=0.21875 → 截 0.02
    assert kelly_fraction(0.4, 3.0) == STAKE_CAP               # f_raw=0.025 → 截 0.02
    assert kelly_fraction(0.75, 1.4) == STAKE_CAP              # f_raw=0.2321…→ 截 0.02


def test_kelly_no_edge_returns_zero():
    """无优势（f* ≤ 0）不得给负仓位：截到 0。"""
    assert kelly_fraction(0.2, 2.0) == 0.0              # f_raw = 0.25·(−0.6)/1 < 0
    assert kelly_fraction(0.0, 3.0) == 0.0
    assert kelly_fraction(1.0, 3.0) == STAKE_CAP        # 必中 → 上限封顶


def test_kelly_never_divides_by_zero_below_evens():
    """odds ≤ 1 无盈利空间：直接 0（公式分母 o−1 会除零，须显式守卫）。"""
    assert kelly_fraction(0.9, 1.0) == 0.0
    assert kelly_fraction(0.9, 0.5) == 0.0


def test_kelly_fraction_is_the_stake_simulate_kelly_takes():
    """gates.kelly_fraction 与回测 simulate_kelly 的逐注仓位**等价**（两处算式的锚）。"""
    for p, o in ((0.9, 5.0), (0.53, 2.0), (0.4, 3.0), (0.6, 1.8), (0.25, 6.0)):
        cands = candidates([_bare(p_home=p, mkt_home=p - 0.05, odds_home=o)])
        assert [c["market"] for c in cands] == ["H"]    # 前置：候选须先过门槛
        got = simulate_kelly(cands, bankroll=1000.0)["final_bankroll"]
        assert got == pytest.approx(1000.0 * (1 + kelly_fraction(p, o) * (o - 1))), (p, o)


def test_kelly_is_monotone_in_edge_within_quarter_segment():
    """同赔率下 p 越高仓位越高（¼ 段内单调），触上限后平台。"""
    fs = [kelly_fraction(p, 2.0) for p in (0.51, 0.52, 0.53, 0.54, 0.60, 0.90)]
    assert fs == sorted(fs)
    assert fs[-1] == fs[-2] == STAKE_CAP                # 平台段


def test_thresholds_are_not_accidentally_mutated_by_callers():
    """常量是模块级真值：读用不写。调用面拿到的值与 spec 一致（防 monkeypatch 残留）。"""
    assert math.isclose(EV_MIN, 0.03, rel_tol=0, abs_tol=0)
    assert gates.STAKE_CAP * gates.KELLY_FRAC == 0.005
