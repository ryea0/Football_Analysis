from dataclasses import dataclass

import numpy as np
from scipy.stats import poisson

from fa.model.fit import FitConfig, LeagueFit, _design_arrays

_RHO_GRID = np.linspace(-0.12, 0.12, 25)


def expected_goals(fit: LeagueFit, home: str, away: str) -> tuple[float, float]:
    """未知队（升班马/新赛季新队名）attack/defence 取 0 = 联赛均值（spec §4.4）。"""
    att_h = fit.att.get(home, 0.0)
    dfn_h = fit.dfn.get(home, 0.0)
    att_a = fit.att.get(away, 0.0)
    dfn_a = fit.dfn.get(away, 0.0)
    lh = float(np.exp(fit.mu + fit.home_adv + att_h - dfn_a))
    la = float(np.exp(fit.mu + att_a - dfn_h))
    return lh, la


def _tau(x: int, y: int, lh: float, la: float, rho: float) -> float:
    """Dixon-Coles 低比分修正（原论文约定）。"""
    if x == 0 and y == 0:
        f = 1.0 - lh * la * rho
    elif x == 0 and y == 1:
        f = 1.0 + lh * rho
    elif x == 1 and y == 0:
        f = 1.0 + la * rho
    elif x == 1 and y == 1:
        f = 1.0 - rho
    else:
        return 1.0
    return max(f, 1e-12)


def score_matrix(lh: float, la: float, rho: float = 0.0,
                 max_goals: int = 10) -> np.ndarray:
    xs = poisson.pmf(np.arange(max_goals + 1), lh)
    ys = poisson.pmf(np.arange(max_goals + 1), la)
    m = np.outer(xs, ys)
    for (x, y) in ((0, 0), (1, 0), (0, 1), (1, 1)):
        m[x, y] *= _tau(x, y, lh, la, rho)
    return m / m.sum()


def outcome_probs(m: np.ndarray) -> tuple[float, float, float]:
    return float(np.tril(m, -1).sum()), float(np.trace(m)), float(np.triu(m, 1).sum())


def over25_probs(m: np.ndarray) -> tuple[float, float]:
    i = np.arange(m.shape[0])
    over = m[i[:, None] + i[None, :] >= 3].sum()
    return float(over), float(1.0 - over)


def btts_prob(m: np.ndarray) -> float:
    return float(m[1:, 1:].sum())


def fit_rho(rows: list[dict], asof: str, cfg: FitConfig,
            lh_la_fn) -> float:
    """两阶段 ρ：给定已拟合 λ，网格最大化四格修正的加权对数似然。"""
    h, a, yh, ya, w, _n = _design_arrays(rows, asof, cfg)
    teams = sorted({r["home"] for r in rows} | {r["away"] for r in rows})
    idx = {t: i for i, t in enumerate(teams)}
    best, best_ll = 0.0, -np.inf
    for rho in _RHO_GRID:
        ll = 0.0
        for k in range(len(yh)):
            x, y = int(yh[k]), int(ya[k])
            if x > 1 or y > 1:
                continue
            lh, la = lh_la_fn(rows[k]["home"], rows[k]["away"])
            ll += w[k] * np.log(_tau(x, y, lh, la, rho))
        if ll > best_ll:
            best_ll, best = ll, float(rho)
    return best
