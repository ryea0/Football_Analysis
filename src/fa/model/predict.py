import numpy as np
from scipy.stats import poisson

from fa.model.fit import FitConfig, LeagueFit, _design_arrays

_RHO_GRID = np.linspace(-0.12, 0.12, 25)


def expected_goals(fit: LeagueFit, home: str, away: str,
                   f_home: float = 0.0, f_away: float = 0.0) -> tuple[float, float]:
    """未知队（升班马/新赛季新队名）attack/defence 取 0 = 联赛均值（spec §4.4）。

    fit.beta_form 非 None 时再叠加近 6 场状态协变量（log 域线性项）；
    f_home/f_away 缺省 0.0 → 未传时与无 form 通路逐位一致。
    """
    att_h = fit.att.get(home, 0.0)
    dfn_h = fit.dfn.get(home, 0.0)
    att_a = fit.att.get(away, 0.0)
    dfn_a = fit.dfn.get(away, 0.0)
    b = fit.beta_form
    bh = 0.0 if b is None else b * f_home
    ba = 0.0 if b is None else b * f_away
    lh = float(np.exp(fit.mu + fit.home_adv + att_h - dfn_a + bh))
    la = float(np.exp(fit.mu + att_a - dfn_h + ba))
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
    """两阶段 ρ：给定已拟合 λ，网格最大化四格修正的加权对数似然。

    以 ρ=0 为种子：窗口内无低比分样本时似然对 ρ 完全平坦（恒为 0），
    此时返回中性 0.0 而非网格端点 −0.12。
    """
    _h, _a, yh, ya, w, _n = _design_arrays(rows, asof, cfg)

    def _ll(rho: float) -> float:
        ll = 0.0
        for k in range(len(yh)):
            x, y = int(yh[k]), int(ya[k])
            if x > 1 or y > 1:
                continue
            lh, la = lh_la_fn(rows[k]["home"], rows[k]["away"])
            ll += w[k] * np.log(_tau(x, y, lh, la, rho))
        return ll

    best, best_ll = 0.0, _ll(0.0)
    for rho in _RHO_GRID:
        ll = _ll(rho)
        if ll > best_ll:
            best_ll, best = ll, float(rho)
    return best
