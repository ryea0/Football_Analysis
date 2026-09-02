import math

import pytest

from fa.backtest.metrics import brier, calibration, evaluate, log_loss


def test_log_loss_perfect_vs_uniform():
    assert log_loss([(1.0, 0.0, 0.0)], ["H"]) == pytest.approx(0.0, abs=1e-9)
    ll = log_loss([(1 / 3, 1 / 3, 1 / 3)], ["H"])
    assert ll == pytest.approx(-math.log(1 / 3))


def test_brier_bounds():
    assert brier([(1.0, 0.0, 0.0)], ["H"]) == pytest.approx(0.0)
    assert brier([(0.0, 1.0, 0.0)], ["H"]) == pytest.approx(1.0)


def test_calibration_bins():
    bins = calibration([0.05] * 10 + [0.95] * 10,
                       [False] * 10 + [True] * 10, bins=10)
    first = bins[0]
    assert first["n"] == 10 and first["emp"] == 0.0
    last = [b for b in bins if b["n"] > 0][-1]
    assert last["emp"] == 1.0


def test_evaluate_go_and_nogo():
    rows = [
        {"p_home": 0.6, "p_draw": 0.2, "p_away": 0.2,
         "mkt_home": 0.55, "mkt_draw": 0.25, "mkt_away": 0.20, "outcome": "H"},
        {"p_home": 0.3, "p_draw": 0.3, "p_away": 0.4,
         "mkt_home": 0.30, "mkt_draw": 0.30, "mkt_away": 0.40, "outcome": "A"},
    ]
    ev = evaluate(rows)
    assert ev["n"] == 2
    assert ev["verdict"] in ("GO", "NO-GO")
    # 模型若处处等于市场 → 劣化 0 → GO
    same = evaluate([{**r, "p_home": r["mkt_home"], "p_draw": r["mkt_draw"],
                      "p_away": r["mkt_away"]} for r in rows])
    assert same["degradation_pct"] == pytest.approx(0.0, abs=1e-9)
    assert same["verdict"] == "GO"


def test_verdict_boundary():
    """1.01 判据边界入仓（spec §8.2：劣化 ≤1% 即 GO，端点含入）。

    算术：market=(0.5,.25,.25) 且 outcome="H" ⇒ market_ll = −log(0.5) = ln2。
    令 p_home = 2**(−k)、其余质量均分（p_draw = p_away = (1−p_home)/2，保持归一），
    则 model_ll = −log(p_home) = k·ln2 ⇒ ratio 精确等于 k。
    p_home=0.49654624771851796 即 2**−1.01，实测 ratio 与 1.01 位级相等
    ⇒ 把「<=」改成「<」时此例必失败；p_home=0.4931163522466796 即 2**−1.02
    （1.01 < ratio < 1.1）⇒ 把阈值 1.01 改成 1.1 时此例必失败。
    """
    def rows(p_home):
        rest = (1.0 - p_home) / 2
        return [{"p_home": p_home, "p_draw": rest, "p_away": rest,
                 "mkt_home": 0.5, "mkt_draw": 0.25, "mkt_away": 0.25,
                 "outcome": "H"}]

    at_gate = evaluate(rows(0.49654624771851796))     # 2**-1.01
    assert at_gate["market_ll"] == pytest.approx(math.log(2))
    assert at_gate["ratio"] == pytest.approx(1.01)
    assert at_gate["degradation_pct"] == pytest.approx(1.0, abs=1e-12)
    assert at_gate["verdict"] == "GO"                 # 劣化恰为 1% → 仍 GO

    past = evaluate(rows(0.4931163522466796))         # 2**-1.02
    assert past["ratio"] == pytest.approx(1.02)
    assert past["degradation_pct"] == pytest.approx(2.0, abs=1e-12)
    assert past["verdict"] == "NO-GO"


def test_evaluate_empty_raises():
    """空输入不许以 ZeroDivisionError 崩（Task 9 CLI 另有守卫，此处纵深防御）。"""
    with pytest.raises(ValueError, match="无预测行"):
        evaluate([])


# ---- 以下为本任务补充（brief 原文只有上面 4 个测试）--------------------------
# fetch_predictions 的过滤路径相对 brief 原文修正过（参数绑定），必须有回归测试钉住；
# by_group 是 Task 8/9 直接消费的产出接口，此处钉住分组键与摘要形状。


def test_fetch_predictions_filters(tmp_path):
    from fa.backtest.metrics import fetch_predictions
    from fa.db import connect, init_db

    init_db(tmp_path / "t.db")
    conn = connect(tmp_path / "t.db")
    conn.executemany("INSERT INTO teams (league, name) VALUES (?, ?)",
                     [("E0", "A"), ("E0", "B"), ("SP1", "C"), ("SP1", "D")])
    ids = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM teams")}
    conn.executemany(
        "INSERT INTO matches (id, league, season, date, home_team_id,"
        " away_team_id, raw_line) VALUES (?,?,?,?,?,?, '{}')",
        [(1, "E0", 2023, "2023-08-12", ids["A"], ids["B"]),
         (2, "SP1", 2023, "2023-08-12", ids["C"], ids["D"])])
    conn.executemany(
        "INSERT INTO backtest_predictions (league, season, week_index, match_id,"
        " date, p_home, p_draw, p_away, outcome, total_goals)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        [("E0", 2023, 1, 1, "2023-08-12", 0.5, 0.3, 0.2, "H", 2),
         ("SP1", 2023, 1, 2, "2023-08-12", 0.4, 0.3, 0.3, "A", 1)])
    conn.commit()

    assert len(fetch_predictions(conn)) == 2
    assert [r["league"] for r in fetch_predictions(conn, leagues=["E0"])] == ["E0"]
    assert [r["match_id"] for r in
            fetch_predictions(conn, leagues=("E0", "SP1"))] == [1, 2]
    assert fetch_predictions(conn, seasons=[1999]) == []
    conn.close()


def test_by_group_keys_and_summary():
    from fa.backtest.metrics import by_group

    rows = [
        {"league": "E0", "season": 2023, "p_home": 0.6, "p_draw": 0.2,
         "p_away": 0.2, "mkt_home": 0.55, "mkt_draw": 0.25, "mkt_away": 0.20,
         "outcome": "H"},
        {"league": "SP1", "season": 2023, "p_home": 0.3, "p_draw": 0.3,
         "p_away": 0.4, "mkt_home": 0.30, "mkt_draw": 0.30, "mkt_away": 0.40,
         "outcome": "A"},
    ]
    g = by_group(rows, "league")
    assert sorted(g) == ["E0", "SP1"]
    assert g["E0"]["n"] == 1 and g["SP1"]["n"] == 1
    assert set(g["E0"]) == set(evaluate(rows))
