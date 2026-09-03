"""select 测试——种子直插库（模式同 tests/pipeline/test_paper.py）；
divergence 数值已预验算（见计划 Task 3 表格）。"""
import math

import pytest

from fa.db import connect, init_db
from fa.retro.select import (divergence_rows, select_divergence,
                             select_manual)

LEAGUE = "E0"
SEASON = 2023


def _seed(conn):
    """三队两队+三场预测；matches 需真实行（FK：backtest_predictions.match_id）。"""
    teams = [("E0", "Arsenal"), ("E0", "Chelsea"), ("E0", "West Ham"),
             ("E0", "Liverpool")]
    for lg, name in teams:
        conn.execute("INSERT INTO teams (league, name) VALUES (?, ?)", (lg, name))
    ids = {name: i + 1 for i, (lg, name) in enumerate(teams)}  # 1..4
    # 三场：id 1/2/3（自增），日期错开保证「近 N 场」可测（Task 4 复用本种子）
    matches = [
        (1, LEAGUE, SEASON, "2024-04-20", ids["Arsenal"], ids["West Ham"],
         2, 0, "H"),
        (2, LEAGUE, SEASON, "2024-04-21", ids["Chelsea"], ids["Liverpool"],
         0, 1, "A"),
        (3, LEAGUE, SEASON, "2024-04-22", ids["Arsenal"], ids["Chelsea"],
         1, 1, "D"),
    ]
    for mid, lg, se, d, h, a, hg, ag, o in matches:
        conn.execute(
            "INSERT INTO matches (id, league, season, date, home_team_id,"
            " away_team_id, fthg, ftag, raw_line) VALUES (?,?,?,?,?,?,?,?,?)",
            (mid, lg, se, d, h, a, hg, ag, "{}"))
    # backtest_predictions：列序对齐 db.py _BP_TABLE（p_btts 可 NULL）
    # ——计划稿的 preds 元组多写了一个重复的场 id（21 元素对 20 占位符，无法
    # 绑定），此处按其注释声明的 _BP_TABLE 列序落定：league, season,
    # week_index, match_id, date, ...；全部概率/赔率/进球数值逐字保留。
    preds = [
        # M1: H，model_home .45 / mkt_home .52 → div ≈ +0.1446
        (LEAGUE, SEASON, 1, 1, "2024-04-20", 0.45, 0.28, 0.27,
         0.55, 0.45, None, 0.52, 0.26, 0.22, 0.55,
         2.10, 3.40, 3.90, "H", 2),
        # M2: A，model_away .27 / mkt_away .22 → div ≈ −0.2049
        (LEAGUE, SEASON, 1, 2, "2024-04-21", 0.33, 0.40, 0.27,
         0.48, 0.52, None, 0.36, 0.42, 0.22, 0.48,
         2.60, 2.20, 4.10, "A", 1),
        # M3: D，model_draw .28 / mkt_draw .26 → div ≈ −0.0741
        (LEAGUE, SEASON, 1, 3, "2024-04-22", 0.36, 0.28, 0.36,
         0.50, 0.50, None, 0.38, 0.26, 0.36, 0.50,
         2.40, 3.50, 2.55, "D", 2),
    ]
    for (lg, se, wk, m, d, ph, pd, pa, po, pu, pb,
         mh, md, ma, mo, oh, od, oa, out, tg) in preds:
        conn.execute(
            "INSERT INTO backtest_predictions (league, season, week_index,"
            " match_id, date, p_home, p_draw, p_away, p_over25, p_under25,"
            " p_btts, mkt_home, mkt_draw, mkt_away, mkt_over25,"
            " odds_home, odds_draw, odds_away, outcome, total_goals)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (lg, se, wk, m, d, ph, pd, pa, po, pu, pb,
             mh, md, ma, mo, oh, od, oa, out, tg))
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    c = connect(db)
    _seed(c)
    yield c
    c.close()


def test_divergence_values_and_direction(conn):
    """div = ln(mkt_p_outcome / model_p_outcome)：正=模型比市场差（预验算表）。"""
    rows = {r["match_id"]: r for r in divergence_rows(conn)}
    assert math.isclose(rows[1]["div"], math.log(0.52 / 0.45), abs_tol=1e-9)
    assert math.isclose(rows[2]["div"], math.log(0.22 / 0.27), abs_tol=1e-9)
    assert rows[1]["div"] > 0 and rows[2]["div"] < 0   # 方向代入断言
    assert rows[1]["home"] == "Arsenal" and rows[1]["away"] == "West Ham"


def test_select_divergence_top_and_control(conn):
    sel = select_divergence(conn, top_k=1, control_k=1, seed=7)
    cases = [r for r in sel if not r["is_control"]]
    ctrls = [r for r in sel if r["is_control"]]
    assert [r["match_id"] for r in cases] == [1]          # 唯一正 div
    assert len(ctrls) == 1 and ctrls[0]["match_id"] in (2, 3)  # 池={M2,M3}


def test_select_divergence_reproducible(conn):
    a = select_divergence(conn, top_k=1, control_k=1, seed=7)
    b = select_divergence(conn, top_k=1, control_k=1, seed=7)
    assert [(r["match_id"], r["is_control"]) for r in a] == \
           [(r["match_id"], r["is_control"]) for r in b]


def test_date_window_filters(conn):
    rows = divergence_rows(conn, date_from="2024-04-21",
                           date_to="2024-04-21")
    assert [r["match_id"] for r in rows] == [2]


def test_select_manual(conn):
    rows = select_manual(conn, league=LEAGUE, season=SEASON)
    assert {r["match_id"] for r in rows} == {1, 2, 3}
    assert all(not r["is_control"] for r in rows)
    rows2 = select_manual(conn, match_ids=[3])
    assert [r["match_id"] for r in rows2] == [3]
