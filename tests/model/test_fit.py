import numpy as np
import pytest
from datetime import date, timedelta

from fa.model.fit import FitConfig, _design_arrays, fit_league, training_rows

RNG = np.random.default_rng(42)


def _synthetic(n_matches=900):
    """6 队、已知参数，按模型生成比分。"""
    teams = [f"T{i}" for i in range(6)]
    att = {"T0": 0.5, "T1": 0.25, "T2": 0.0, "T3": -0.1, "T4": -0.25, "T5": -0.4}
    dfn = {t: -a * 0.5 for t, a in att.items()}   # 强队攻强守也强
    mu, ha = 0.15, 0.25
    rows = []
    for k in range(n_matches):
        h, a = RNG.choice(6, size=2, replace=False)
        h, a = teams[h], teams[a]
        lh = float(np.exp(mu + ha + att[h] - dfn[a]))
        la = float(np.exp(mu + att[a] - dfn[h]))
        rows.append({"home": h, "away": a,
                     "fthg": int(RNG.poisson(lh)), "ftag": int(RNG.poisson(la)),
                     "date": f"2023-{1 + k % 12:02d}-{1 + k % 28:02d}"})
    return teams, att, rows


def test_fit_recovers_ranking():
    teams, true_att, rows = _synthetic()
    fit = fit_league(rows, asof="2024-01-01", league="X",
                     mu_global=0.15, ha_global=0.25)
    est = [fit.att[t] for t in teams]
    true = [true_att[t] for t in teams]
    # 秩相关：攻击力排序基本恢复
    from scipy.stats import spearmanr
    rho, _ = spearmanr(est, true)
    assert rho > 0.8
    assert abs(fit.mu - 0.15) < 0.25
    assert fit.att["T0"] > fit.att["T5"]


def test_weak_data_shrinks_toward_prior():
    """弱数据收缩机制：默认先验下估计显著弱于弱先验（趋近 MLE）版本，且幅度有界。"""
    _, _, rows = _synthetic(n_matches=40)          # 高于 <30 守卫
    default = FitConfig()                          # sigma_att=0.35
    loose = FitConfig(sigma_att=5.0, sigma_dfn=5.0)  # 近似无正则 → 逼近 MLE
    f_def = fit_league(rows, asof="2024-01-01", league="X",
                       mu_global=0.15, ha_global=0.25, cfg=default)
    f_loose = fit_league(rows, asof="2024-01-01", league="X",
                         mu_global=0.15, ha_global=0.25, cfg=loose)
    # 机制：先验越强，估计越被拉向 0（联赛均值）
    assert abs(f_def.att["T0"]) < abs(f_loose.att["T0"])
    # 幅度有界：强队估计不会因弱数据跑飞
    assert f_def.att["T0"] < 0.7


def test_gradient_matches_numeric():
    """解析梯度 vs 数值梯度（小规模直接对内部目标函数校验）。"""
    from fa.model.fit import _objective
    teams = ["A", "B", "C"]
    rows = [{"home": "A", "away": "B", "fthg": 2, "ftag": 1, "date": "2023-09-01"},
            {"home": "B", "away": "C", "fthg": 0, "ftag": 0, "date": "2023-09-08"},
            {"home": "C", "away": "A", "fthg": 1, "ftag": 3, "date": "2023-09-15"}]
    h, a, yh, ya, w, n = _design_arrays(rows, "2023-09-20", FitConfig())
    x = np.array([0.1, -0.1, 0.0, 0.05, -0.05, 0.0, 0.2, 0.3])
    f0, g = _objective(x, h, a, yh, ya, w, 0.15, 0.25, FitConfig())
    eps = 1e-6
    for i in range(len(x)):
        xp = x.copy(); xp[i] += eps
        xm = x.copy(); xm[i] -= eps
        fp, _ = _objective(xp, h, a, yh, ya, w, 0.15, 0.25, FitConfig())
        fm, _ = _objective(xm, h, a, yh, ya, w, 0.15, 0.25, FitConfig())
        assert abs((fp - fm) / (2 * eps) - g[i]) < 1e-4, f"param {i}"


# ---- 以下两条覆盖 brief 未及的接口契约（训练切片防泄漏 / 最小样本守卫） ----

@pytest.fixture
def conn(tmp_path):
    from fa.db import connect, init_db
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    c.execute("INSERT INTO teams (league, name) VALUES ('E0','A'), ('E0','B')")
    ids = {r["name"]: r["id"] for r in
           c.execute("SELECT id, name FROM teams WHERE league='E0'")}
    asof = "2023-01-01"
    lo = (date.fromisoformat(asof) - timedelta(days=1120)).isoformat()
    c.executemany(
        "INSERT INTO matches (league, season, date, home_team_id, away_team_id,"
        " fthg, ftag, raw_line) VALUES (?, 2022, ?, ?, ?, 1, 1, '{}')",
        [("E0", lo, ids["A"], ids["B"]),               # 窗口下界当天 → 保留
         ("E0", (date.fromisoformat(lo) - timedelta(days=1)).isoformat(),
          ids["A"], ids["B"]),                          # 下界前一天 → 排除
         ("E0", "2022-12-31", ids["B"], ids["A"]),      # asof 前一天 → 保留
         ("E0", asof, ids["A"], ids["B"]),              # asof 当天 → 排除（防泄漏）
         ("D1", "2022-10-01", ids["A"], ids["B"])])     # 他联赛 → 排除
    c.commit()
    yield c, asof
    c.close()


def test_training_rows_window_is_leak_safe(conn):
    c, asof = conn
    rows = training_rows(c, "E0", asof, window_days=1120)
    got = [(r["home"], r["away"]) for r in rows]
    assert got == [("A", "B"), ("B", "A")]       # 下界当天 + asof 前一天，按日期序
    assert rows[0]["date"] == (date.fromisoformat(asof) - timedelta(days=1120)).isoformat()
    assert all(r["date"] < asof for r in rows)


def test_fit_league_rejects_thin_sample():
    _, _, rows = _synthetic(n_matches=10)
    with pytest.raises(ValueError, match="训练样本不足"):
        fit_league(rows, asof="2024-01-01", league="X",
                   mu_global=0.15, ha_global=0.25)


# ---- §4.5 近 6 场状态协变量（可选通路；不传时必须与扩展前逐位一致） ----

def test_fit_without_form_unchanged():
    """form_pairs=None（默认）时与扩展前的拟合输出逐位一致——向后兼容回归守卫。"""
    # 私有 RNG：不受同进程其它测试消耗模块级 RNG 的影响，数值可复现
    rng = np.random.default_rng(123)
    teams = [f"T{i}" for i in range(6)]
    att = {"T0": 0.5, "T1": 0.25, "T2": 0.0, "T3": -0.1, "T4": -0.25, "T5": -0.4}
    dfn = {t: -a * 0.5 for t, a in att.items()}
    rows = []
    for k in range(40):
        h, a = rng.choice(6, size=2, replace=False)
        h, a = teams[h], teams[a]
        lh = float(np.exp(0.15 + 0.25 + att[h] - dfn[a]))
        la = float(np.exp(0.15 + att[a] - dfn[h]))
        rows.append({"home": h, "away": a, "fthg": int(rng.poisson(lh)),
                     "ftag": int(rng.poisson(la)),
                     "date": f"2023-{1 + k % 12:02d}-{1 + k % 28:02d}"})

    f = fit_league(rows, asof="2024-01-01", league="X",
                   mu_global=0.15, ha_global=0.25, cfg=FitConfig())
    assert f.beta_form is None                      # 未启用 → 无 β
    # 以下数值钉死自扩展前代码（git HEAD）在同一数据上的输出，防回归
    assert f.att == pytest.approx({
        "T0": 0.1907996114144802, "T1": 0.04153347698524865,
        "T2": -0.04283672761153019, "T3": 0.07845948948890745,
        "T4": -0.05433635130931519, "T5": -0.05565980023841156})
    assert f.dfn == pytest.approx({
        "T0": -0.2569177542103923, "T1": -0.008932174517124363,
        "T2": 0.0639433544561719, "T3": -0.02150988109048491,
        "T4": 0.03933165430601891, "T5": 0.02612510232643144})
    assert f.mu == pytest.approx(0.23059487557509703)
    assert f.home_adv == pytest.approx(0.29140297790251357)
    from fa.model.predict import expected_goals
    assert expected_goals(f, "T0", "T5") == pytest.approx(
        (1.987092196943918, 1.5401084400249272))


def test_fit_with_form_moves_lambda():
    """强正自相关（动量）序列：β > 0，且状态好的队 λ 上升、状态差的下降。"""
    def _momentum(n_rounds=80, phi=0.9, seed=7):
        """每轮 2 场 4 队循环；λ 由 AR(1) 动量驱动 → 近况与进球强正相关。"""
        rng = np.random.default_rng(seed)
        teams = ["A", "B", "C", "D"]
        m = dict.fromkeys(teams, 0.0)
        rows, d = [], date(2023, 1, 1)
        for k in range(n_rounds):
            order = teams[k % 4:] + teams[:k % 4]
            for h, a in ((order[0], order[1]), (order[2], order[3])):
                # 先按当前动量出比分，再更新动量（本场不进自身历史）
                lh, la = np.exp(0.15 + 0.25 + m[h]), np.exp(0.15 + m[a])
                rows.append({"home": h, "away": a,
                             "fthg": int(rng.poisson(lh)), "ftag": int(rng.poisson(la)),
                             "date": d.isoformat()})
                for t in (h, a):
                    m[t] = phi * m[t] + rng.normal(0, 0.5)
            d += timedelta(days=7)
        return rows

    rows = _momentum()
    from fa.model.form import current_form, form_features
    from fa.model.predict import expected_goals
    fp = form_features(rows)
    f0 = fit_league(rows, asof="2025-01-01", league="X",
                    mu_global=0.15, ha_global=0.25)
    f1 = fit_league(rows, asof="2025-01-01", league="X",
                    mu_global=0.15, ha_global=0.25, form_pairs=fp)
    assert f0.beta_form is None and f1.beta_form is not None
    assert f1.beta_form > 0, f1.beta_form
    # 单独隔离 form 通道：同一拟合下，近况为正 → λ 高于协变量置 0；为负 → 更低
    cf = current_form(rows)
    best = max(cf, key=lambda t: cf[t])
    worst = min(cf, key=lambda t: cf[t])
    assert cf[best] > 0 > cf[worst], cf
    on_best = expected_goals(f1, best, worst, cf[best], cf[worst])
    off_best = expected_goals(f1, best, worst, 0.0, 0.0)
    assert on_best[0] > off_best[0] and on_best[1] < off_best[1], (on_best, off_best)
