import numpy as np

from fa.model.fit import FitConfig, fit_league
from fa.model.predict import (btts_prob, expected_goals, fit_rho, over25_probs,
                              outcome_probs, score_matrix)


def _fit():
    rows = [{"home": "A", "away": "B", "fthg": 2, "ftag": 0, "date": "2023-08-01"},
            {"home": "B", "away": "A", "fthg": 1, "ftag": 1, "date": "2023-08-08"},
            {"home": "A", "away": "B", "fthg": 3, "ftag": 1, "date": "2023-08-15"}] * 15
    return fit_league(rows, asof="2023-09-01", league="X",
                      mu_global=0.1, ha_global=0.2)


def test_matrix_normalized_and_rho_zero_independent():
    m = score_matrix(1.5, 1.1)
    assert m.shape == (11, 11)
    assert abs(m.sum() - 1.0) < 1e-12
    m0 = score_matrix(1.5, 1.1, rho=0.0)
    from scipy.stats import poisson as _ps
    outer = np.outer(_ps.pmf(np.arange(11), 1.5), _ps.pmf(np.arange(11), 1.1))
    np.testing.assert_allclose(m0, outer / outer.sum(), atol=1e-10)


def test_rho_only_touches_four_cells():
    m0 = score_matrix(1.2, 0.9, rho=0.0)
    mr = score_matrix(1.2, 0.9, rho=-0.1)
    affected = {(0, 0), (1, 0), (0, 1), (1, 1)}
    for i in range(11):
        for j in range(11):
            if (i, j) in affected:
                continue
            # 归一化会使未调格轻微变化，容差放宽到 5%
            assert abs(mr[i, j] - m0[i, j]) < 0.05 * m0[i, j] + 1e-6


def test_derivatives_sum_to_one():
    fit = _fit()
    lh, la = expected_goals(fit, "A", "B")
    assert lh > la                                   # A 强且主场
    m = score_matrix(lh, la, rho=-0.08)
    ph, pd, pa = outcome_probs(m)
    assert abs(ph + pd + pa - 1.0) < 1e-12
    po, pu = over25_probs(m)
    assert abs(po + pu - 1.0) < 1e-12
    assert 0.0 < btts_prob(m) < 1.0


def test_expected_goals_unknown_team_is_league_mean():
    fit = _fit()
    lh, la = expected_goals(fit, "NEVER_SEEN", "ALSO_NEW")
    assert 0.05 < lh < 6.0 and 0.05 < la < 6.0      # 合理范围内


def test_fit_rho_prefers_negative_on_drawy_data():
    rows = []
    for k in range(60):                              # 大量 0-0/1-1
        yh, ya = (0, 0) if k % 2 == 0 else (1, 1)
        rows.append({"home": "A", "away": "B", "fthg": yh, "ftag": ya,
                     "date": "2023-08-01"})
    rho = fit_rho(rows, "2023-09-01", FitConfig(),
                  lambda h, a: (1.4, 1.1))
    assert rho < 0                                   # 低比分偏多 → 负 ρ


def test_fit_rho_flat_window_returns_zero():
    rows = [{"home": "A", "away": "B", "fthg": 3, "ftag": 2,
             "date": "2023-08-01"}] * 15              # 无低比分样本 → 似然对 ρ 平坦
    assert fit_rho(rows, "2023-09-01", FitConfig(),
                   lambda h, a: (2.0, 1.8)) == 0.0    # 中性 0.0，非网格端点 −0.12
