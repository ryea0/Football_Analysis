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
    beta_form: float | None = None    # 近 6 场状态协变量系数（spec §4.5，None = 未启用）


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


def _objective(x, h, a, yh, ya, w, mu_g, ha_g, cfg, fh=None, fa=None):
    """返回 (目标值, 解析梯度)。

    x = [att(n), dfn(n), mu, ha]，n = (len(x)-2)//2；传入 fh/fa（近 6 场状态
    协变量，spec §4.5）时末尾再追加一个 β：log λ += β·f。β 无先验项。
    """
    n = (len(x) - 2) // 2
    att, dfn, mu, ha = x[:n], x[n:2 * n], x[2 * n], x[2 * n + 1]
    use_form = fh is not None
    beta = float(x[2 * n + 2]) if use_form else 0.0
    zh = mu + ha + att[h] - dfn[a]
    za = mu + att[a] - dfn[h]
    if use_form:
        zh = zh + beta * fh
        za = za + beta * fa
    zh = np.clip(zh, -6.0, 6.0)
    za = np.clip(za, -6.0, 6.0)
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
    tail = [g_mu, g_ha]
    if use_form:
        tail.append(float(rh @ fh + ra @ fa))     # ∂nll/∂β（无先验项）
    g = np.concatenate([g_att, g_dfn, tail])
    return nll + prior, g


def fit_league(matches, asof, league, mu_global, ha_global,
               cfg: FitConfig = FitConfig(),
               form_pairs: list[tuple[float, float]] | None = None) -> LeagueFit:
    if len(matches) < 30:
        raise ValueError(f"{league} 训练样本不足（{len(matches)} < 30）")
    h, a, yh, ya, w, n = _design_arrays(matches, asof, cfg)
    fh = fa = None
    if form_pairs is not None:
        if len(form_pairs) != len(matches):
            raise ValueError(f"form_pairs 长度 {len(form_pairs)} 与 matches "
                             f"{len(matches)} 不一致")
        fh = np.array([p[0] for p in form_pairs], dtype=float)
        fa = np.array([p[1] for p in form_pairs], dtype=float)
    gpg = (w @ (yh + ya)) / w.sum() / 2.0          # 加权场均
    x0 = np.zeros(2 * n + 2 + (1 if form_pairs is not None else 0))
    x0[2 * n] = float(np.log(max(gpg, 0.3)))
    x0[2 * n + 1] = float(ha_global)
    res = minimize(lambda x: _objective(x, h, a, yh, ya, w,
                                        mu_global, ha_global, cfg, fh=fh, fa=fa),
                   x0, jac=True, method="L-BFGS-B",
                   options={"maxiter": 300})
    x = res.x
    teams = sorted({r["home"] for r in matches} | {r["away"] for r in matches})
    return LeagueFit(league=league,
                     att=dict(zip(teams, x[:n])),
                     dfn=dict(zip(teams, x[n:2 * n])),
                     mu=float(x[2 * n]), home_adv=float(x[2 * n + 1]),
                     beta_form=(float(x[2 * n + 2])
                                if form_pairs is not None else None))
