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


@pytest.mark.skip(reason=(
    "brief 内部矛盾，待裁决："
    "(1) 该测试取 20 场，必然触发 fit_league 的 <30 守卫 → ValueError；"
    "(2) 即便绕开守卫，断言 |att[T0]|<0.3 在文件序随机流下也不成立"
    "（n=20 时加权 MLE=+0.8231，MAP=+0.3661，偏离真值收缩 58.6%——"
    "收缩机制本身正常，是 0.3 这个绝对阈值对如此噪声水平定得过紧）。"
    "待定：阈值放宽 / 改为相对收缩断言 / 提高样本数，由控制器裁决后落地。"))
def test_fit_weak_data_shrinks_to_prior():
    """只有 20 场时参数应强烈收缩向 0（升班马语义）。"""
    _, _, rows = _synthetic(n_matches=20)
    fit = fit_league(rows, asof="2024-01-01", league="X",
                     mu_global=0.15, ha_global=0.25)
    assert abs(fit.att["T0"]) < 0.3      # 弱数据不得跑飞


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
