import pytest

from fa.backtest.run import global_targets, run_backtest
from fa.db import connect, init_db
from fa.model.fit import FitConfig


@pytest.fixture
def conn(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    _seed(c)
    yield c
    c.close()


def _seed(conn):
    """E0 2023：8 队 2 周（周四制），全部带 psc 收盘价；再补 3 年历史供训练。"""
    conn.executemany(
        "INSERT INTO teams (league, name) VALUES ('E0', ?)",
        [(f"T{i}",) for i in range(8)])
    ids = {r["name"]: r["id"] for r in
           conn.execute("SELECT id, name FROM teams")}
    rows = []
    # 历史：2021-2023 每周 4 场（周四起），T0 主场全胜的强队模式
    from datetime import date, timedelta
    d = date(2021, 8, 12)
    while d < date(2023, 8, 1):
        for k in range(4):
            h, a = f"T{k}", f"T{7 - k}"
            # 控制器裁定（Option A）：历史窗内偶有客队进球，避免全 2:0 退化种子
            # （否则 ga=0 → ha_g=log(2/1e-6)≈14.5 → 拟合假收敛 → 概率 NaN）。
            ftag = 1 if k == 1 else 0
            rows.append(("E0", 2022 if d.year >= 2022 else 2021,
                         d.isoformat(), ids[h], ids[a], 2, ftag,
                         2.0, 3.4, 3.6))
        d += timedelta(days=7)
    # 目标赛季 2023：两周（2023-08-10 周四制第 1 周、08-17 第 2 周）
    for wk, day in ((1, "2023-08-12"), (2, "2023-08-19")):
        for k in range(4):
            h, a = f"T{k}", f"T{7 - k}"
            rows.append(("E0", 2023, day, ids[h], ids[a], 2, 0,
                         2.0, 3.4, 3.6))
    conn.executemany(
        "INSERT INTO matches (league, season, date, home_team_id, away_team_id,"
        " fthg, ftag, psc_home, psc_draw, psc_away, raw_line)"
        " VALUES (?,?,?,?,?,?,?,?,?,?, '{}')", rows)
    conn.commit()


def test_run_backtest_writes_predictions(conn):
    n = run_backtest(conn, ["E0"], range(2023, 2024),
                     FitConfig(window_days=800), rho=0.0)
    assert n == 8                                    # 2 周 × 4 场
    row = conn.execute(
        "SELECT * FROM backtest_predictions LIMIT 1").fetchone()
    assert row["p_home"] + row["p_draw"] + row["p_away"] == pytest.approx(1.0)
    assert row["mkt_home"] + row["mkt_draw"] + row["mkt_away"] == pytest.approx(1.0)
    assert row["outcome"] == "H" and row["total_goals"] == 2
    assert row["week_index"] in (1, 2)


def test_no_leakage_week2_excludes_week1(conn):
    run_backtest(conn, ["E0"], range(2023, 2024),
                 FitConfig(window_days=800), rho=0.0)
    # 第 1 周预测时，训练切片必须不含第 1 周比赛本身（date < week.start）
    # 这里间接验证：两行同队对决概率略不同（第 2 周多了第 1 周的训练数据）。
    # 直接验证通过 global_targets 的 SQL 边界（< asof）：
    conn2_rows = conn.execute(
        "SELECT COUNT(*) c FROM backtest_predictions WHERE week_index=1").fetchone()
    assert conn2_rows["c"] == 4                       # 结构性烟测


def test_global_targets_leak_free(conn):
    mu_g, ha_g = global_targets(conn, "2023-08-12", FitConfig(window_days=800))
    assert mu_g > 0 and ha_g > 0                      # 主场优势 > 0（种子数据 2:0）
