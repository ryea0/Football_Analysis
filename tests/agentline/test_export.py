"""信息集打包（设计 §5.1）：防泄漏是结构性约束——一切查询以 date < match_date 为界。"""
import json

import pytest

from fa.agentline.export import export_info_set, export_batch, pick_sample
from fa.db import connect, init_db


@pytest.fixture()
def db(tmp_path):
    """两支队、三场已完赛（含赛果与收盘盘口）、backtest_predictions 覆盖前两场。

    第三场 date 晚于目标场——若泄漏进信息集即为 bug（防泄漏断言的靶子）。
    """
    init_db(tmp_path / "t.db")
    conn = connect(tmp_path / "t.db")
    conn.execute("INSERT INTO teams (league, name) VALUES ('E0', 'Arsenal')")
    conn.execute("INSERT INTO teams (league, name) VALUES ('E0', 'Chelsea')")
    hid, aid = (r["id"] for r in conn.execute(
        "SELECT id FROM teams ORDER BY id"))
    rows = [  # (id, date, home, away, fthg, ftag, psc 三元组)
        (10, "2024-01-01", hid, aid, 2, 0, (1.5, 4.0, 6.0)),
        (11, "2024-02-01", aid, hid, 1, 1, (2.5, 3.2, 2.8)),
        (12, "2024-03-01", hid, aid, 3, 1, (1.4, 4.2, 7.0)),  # 晚于目标场 11
    ]
    for mid, d, h, a, gf, ga, (oh, od, oa) in rows:
        conn.execute(
            "INSERT INTO matches (id, league, season, date, home_team_id,"
            " away_team_id, fthg, ftag, psc_home, psc_draw, psc_away,"
            " raw_line) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid, "E0", 2023, d, h, a, gf, ga, oh, od, oa, "{}"))
    for mid, outcome in ((10, "H"), (11, "D")):
        conn.execute(
            "INSERT INTO backtest_predictions (league, season, week_index,"
            " match_id, date, p_home, p_draw, p_away, outcome, total_goals)"
            " VALUES ('E0', 2023, 1, ?, (SELECT date FROM matches WHERE id=?),"
            " 0.4, 0.3, 0.3, ?, 2)", (mid, mid, outcome))
    conn.commit()
    yield conn
    conn.close()


def test_info_set_excludes_future_matches(db):
    info = export_info_set(db, 11)
    dates = [m["date"] for m in info["home_recent"] + info["away_recent"]]
    assert all(d < "2024-02-01" for d in dates)      # 防泄漏：只见过去
    assert info["match"]["date"] == "2024-02-01"
    assert info["odds"]["psc_home"] == pytest.approx(2.5)


def test_info_set_h2h_and_standings(db):
    info = export_info_set(db, 11)
    assert len(info["h2h"]) == 1                       # 只有第一场是历史交锋
    assert info["standings"]["home"]["played"] >= 1


def test_recent_and_h2h_resolve_names(db):
    # agent 读不懂裸 id：对手/交锋双方必须解析成队名。
    # 目标场 11 主队是 Chelsea（插入序 id=2）、客队 Arsenal——双方唯一
    # 历史交锋是第 10 场（Arsenal 主场 2-0 Chelsea）。
    info = export_info_set(db, 11)
    assert info["home_recent"][0]["opponent"] == "Arsenal"
    assert info["away_recent"][0]["opponent"] == "Chelsea"
    assert set(info["home_recent"][0]) == {"date", "opponent", "venue",
                                           "gf", "ga", "shots", "corners"}
    assert info["h2h"][0] == {"date": "2024-01-01", "home": "Arsenal",
                              "away": "Chelsea", "gf": 2, "ga": 0}  # gf/ga 恒主队视角


def test_pick_sample_only_backtested(db):
    assert set(pick_sample(db, "E0", 2023, n=10)) == {10, 11}


def test_export_batch_idempotent(db, tmp_path):
    out = tmp_path / "is"
    p1 = export_batch(db, [10, 11], out)
    assert len(p1) == 2 and json.loads(p1[0].read_text())["match"]["league"] == "E0"
    assert export_batch(db, [10, 11], out) == []       # 第二次全跳过
