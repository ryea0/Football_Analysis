import pytest

from fa.value.devig import devig_ou, devig_proportional


def test_proportional_sums_to_one():
    p = devig_proportional([2.5, 3.4, 2.8])
    assert sum(p) == pytest.approx(1.0)
    assert max(p) == p[0]                            # 赔率最低 → 概率最高


def test_proportional_known_values():
    # 1/2 + 1/3 + 1/6 = 1.0（无水位），去水后应等于原隐含
    p = devig_proportional([2.0, 3.0, 6.0])
    assert p == pytest.approx([0.5, 1 / 3, 1 / 6])


def test_ou():
    po, pu = devig_ou(1.9, 2.1)                      # 水位 1/1.9+1/2.1 ≈ 1.0025
    assert po + pu == pytest.approx(1.0)
    assert po > pu
