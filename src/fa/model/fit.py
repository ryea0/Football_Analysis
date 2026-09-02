"""分层 Dixon-Coles 队级评分拟合：加权 MAP Poisson + L-BFGS 解析梯度（spec §4.1/4.2）。

不含 Dixon-Coles 的 ρ 低比分修正项——该项由 Task 4 在两阶段框架下处理。
"""

import sqlite3
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize


@dataclass
class FitConfig:
    half_life_days: float = 100.0
    window_days: int = 1120            # ≈3 年，窗外衰减权重可忽略
    sigma_att: float = 0.35
    sigma_dfn: float = 0.35
    sigma_mu: float = 0.25
    sigma_ha: float = 0.25


@dataclass
class LeagueFit:
    league: str
    att: dict
    dfn: dict
    mu: float
    home_adv: float


def training_rows(conn: sqlite3.Connection, league: str, asof: str,
                  window_days: int) -> list[dict]:
    """泄漏安全训练切片：date ∈ [asof − window_days, asof)，带队名。"""
    return [dict(r) for r in conn.execute(
        "SELECT m.date, h.name AS home, a.name AS away, m.fthg, m.ftag "
        "FROM matches m JOIN teams h ON h.id=m.home_team_id "
        "JOIN teams a ON a.id=m.away_team_id "
        "WHERE m.league=? AND m.date < ? "
        "AND m.date >= date(?, '-' || ? || ' day') ORDER BY m.date, m.id",
        (league, asof, asof, window_days))]


def _design_arrays(rows, asof, cfg):
    from datetime import date
    asof_d = date.fromisoformat(asof)
    teams = sorted({r["home"] for r in rows} | {r["away"] for r in rows})
    idx = {t: i for i, t in enumerate(teams)}
    h = np.array([idx[r["home"]] for r in rows], dtype=np.intp)
    a = np.array([idx[r["away"]] for r in rows], dtype=np.intp)
    yh = np.array([r["fthg"] for r in rows], dtype=float)
    ya = np.array([r["ftag"] for r in rows], dtype=float)
    age = np.array([(asof_d - date.fromisoformat(r["date"])).days
                    for r in rows], dtype=float)
    w = 0.5 ** (age / cfg.half_life_days)
    return h, a, yh, ya, w, len(teams)


def _objective(x, h, a, yh, ya, w, mu_g, ha_g, cfg):
    """返回 (目标值, 解析梯度)。x = [att(n), dfn(n), mu, ha]，n = (len(x)-2)//2。"""
    n = (len(x) - 2) // 2
    att, dfn, mu, ha = x[:n], x[n:2 * n], x[2 * n], x[2 * n + 1]
    zh = np.clip(mu + ha + att[h] - dfn[a], -6.0, 6.0)
    za = np.clip(mu + att[a] - dfn[h], -6.0, 6.0)
    lh, la = np.exp(zh), np.exp(za)
    nll = w @ (lh + la) - (w * yh) @ np.log(lh) - (w * ya) @ np.log(la)
    prior = (att @ att) / (2 * cfg.sigma_att ** 2) \
        + (dfn @ dfn) / (2 * cfg.sigma_dfn ** 2) \
        + (mu - mu_g) ** 2 / (2 * cfg.sigma_mu ** 2) \
        + (ha - ha_g) ** 2 / (2 * cfg.sigma_ha ** 2)
    rh = w * (lh - yh)          # ∂nll/∂zh
    ra = w * (la - ya)          # ∂nll/∂za
    g_att = (np.bincount(h, weights=rh, minlength=n)
             + np.bincount(a, weights=ra, minlength=n)
             + att / cfg.sigma_att ** 2)
    g_dfn = (-np.bincount(h, weights=ra, minlength=n)
             - np.bincount(a, weights=rh, minlength=n)
             + dfn / cfg.sigma_dfn ** 2)
    g_mu = rh.sum() + ra.sum() + (mu - mu_g) / cfg.sigma_mu ** 2
    g_ha = rh.sum() + (ha - ha_g) / cfg.sigma_ha ** 2
    g = np.concatenate([g_att, g_dfn, [g_mu, g_ha]])
    return nll + prior, g


def fit_league(matches, asof, league, mu_global, ha_global,
               cfg: FitConfig = FitConfig()) -> LeagueFit:
    if len(matches) < 30:
        raise ValueError(f"{league} 训练样本不足（{len(matches)} < 30）")
    h, a, yh, ya, w, n = _design_arrays(matches, asof, cfg)
    gpg = (w @ (yh + ya)) / w.sum() / 2.0          # 加权场均
    x0 = np.zeros(2 * n + 2)
    x0[2 * n] = float(np.log(max(gpg, 0.3)))
    x0[2 * n + 1] = float(ha_global)
    res = minimize(lambda x: _objective(x, h, a, yh, ya, w,
                                        mu_global, ha_global, cfg),
                   x0, jac=True, method="L-BFGS-B",
                   options={"maxiter": 300})
    x = res.x
    teams = sorted({r["home"] for r in matches} | {r["away"] for r in matches})
    return LeagueFit(league=league,
                     att=dict(zip(teams, x[:n])),
                     dfn=dict(zip(teams, x[n:2 * n])),
                     mu=float(x[2 * n]), home_adv=float(x[2 * n + 1]))
