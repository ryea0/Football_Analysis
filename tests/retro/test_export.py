"""export 测试——防泄漏是结构性断言：date < match_date 的一切查询不得含未来行。"""
import json

import pytest

from fa.db import connect, init_db
from fa.retro.export import RECENT_N, build_pack, write_packs


def _seed(conn):
    # 与 tests/retro/test_select.py 相同的三队三场（M1=2024-04-20 Arsenal vs
    # West Ham）——测试间复制种子是本仓惯例（pipeline 测试同做法），不抽公共
    # conftest 以免跨文件耦合种子演化。
    conn.execute("INSERT INTO teams VALUES (1, 'E0', 'Arsenal')")
    conn.execute("INSERT INTO teams VALUES (2, 'E0', 'West Ham')")
    conn.execute("INSERT INTO teams VALUES (3, 'E0', 'Chelsea')")
    # Arsenal 的历史场（4 月每周一场）+ 一场「未来场」（05-01，M1 之后）——泄漏探针
    hist = [
        (10, "2024-03-02", 1, 2, 1, 0), (11, "2024-03-09", 2, 1, 1, 1),
        (12, "2024-03-16", 1, 2, 3, 1), (13, "2024-03-23", 3, 1, 0, 2),
        (14, "2024-03-30", 1, 2, 2, 2), (15, "2024-04-06", 2, 1, 0, 0),
        (16, "2024-04-13", 1, 3, 1, 1),
    ]
    for mid, d, h, a, hg, ag in hist:
        conn.execute(
            "INSERT INTO matches (id, league, season, date, home_team_id,"
            " away_team_id, fthg, ftag, raw_line)"
            " VALUES (?, 'E0', 2023, ?, ?, ?, ?, ?, '{}')",
            (mid, d, h, a, hg, ag))
    # 审查 Important 补种：第三方场（West Ham vs Chelsea，04-03）+ 真交锋
    # （Arsenal vs West Ham，04-10）——h2h 只能含后者，不得被前者填槽
    conn.execute(
        "INSERT INTO matches (id, league, season, date, home_team_id,"
        " away_team_id, fthg, ftag, raw_line)"
        " VALUES (17, 'E0', 2023, '2024-04-03', 2, 3, 1, 2, '{}')")
    conn.execute(
        "INSERT INTO matches (id, league, season, date, home_team_id,"
        " away_team_id, fthg, ftag, raw_line)"
        " VALUES (18, 'E0', 2023, '2024-04-10', 1, 2, 2, 1, '{}')")
    # 泄漏探针：M1（id=1, 2024-04-20）之后才踢的 Arsenal 场（05-01）
    conn.execute(
        "INSERT INTO matches (id, league, season, date, home_team_id,"
        " away_team_id, fthg, ftag, raw_line)"
        " VALUES (20, 'E0', 2023, '2024-05-01', 1, 3, 4, 0, '{}')")
    conn.execute(
        "INSERT INTO matches (id, league, season, date, home_team_id,"
        " away_team_id, fthg, ftag, raw_line)"
        " VALUES (1, 'E0', 2023, '2024-04-20', 1, 2, 2, 0, '{}')")
    conn.execute(
        "INSERT INTO backtest_predictions (match_id, league, season,"
        " week_index, date, p_home, p_draw, p_away, p_over25, mkt_home,"
        " mkt_draw, mkt_away, mkt_over25, odds_home, odds_draw, odds_away,"
        " outcome, total_goals)"
        " VALUES (1, 'E0', 2023, 1, '2024-04-20', 0.45, 0.28, 0.27, 0.55,"
        " 0.52, 0.26, 0.22, 0.55, 2.10, 3.40, 3.90, 'H', 2)")
    conn.execute(
        "UPDATE matches SET ps_home=2.05, ps_draw=3.5, ps_away=4.0,"
        " psc_home=2.1, psc_draw=3.4, psc_away=3.9 WHERE id=1")
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    c = connect(db)
    _seed(c)
    yield c
    c.close()


@pytest.fixture
def cand(conn):
    from fa.retro.select import select_manual
    return select_manual(conn, match_ids=[1])[0]


def test_pack_shape(cand, conn):
    pack = build_pack(conn, cand)
    assert pack["match"]["date"] == "2024-04-20"
    assert pack["match"]["home"] == "Arsenal"
    assert pack["prediction"]["p_home"] == 0.45
    assert pack["prediction"]["mkt_home"] == 0.52
    assert pack["outcome"] == {"fthg": 2, "ftag": 0, "result": "H",
                               "total_goals": 2}
    assert pack["divergence"]["div"] == pytest.approx(
        __import__("math").log(0.52 / 0.45))
    assert pack["odds"]["psc_home"] == 2.1
    assert 0 <= len(pack["recent_home"]) <= RECENT_N


def test_no_leak_future_match_excluded(cand, conn):
    """2024-05-01 的 Arsenal 场（id=20）不得出现在任何近况列表。"""
    pack = build_pack(conn, cand)
    for key in ("recent_home", "recent_away"):
        assert all(m["date"] < "2024-04-20" for m in pack[key])
    assert all(m["date"] < "2024-04-20" for m in pack["h2h"])
    ids = {m["date"] for m in pack["recent_home"]}
    assert "2024-05-01" not in ids


def test_recent_capped_at_n(cand, conn):
    pack = build_pack(conn, cand)
    assert len(pack["recent_home"]) <= RECENT_N == 10


def test_h2h_strict_both_teams(cand, conn):
    """h2h 只含两队真交锋（spec §5 H2H），不含各自对第三方的场。

    旧的「任一队」谓词（home IN (t1,t2) OR away IN (t1,t2)）会把 Arsenal vs
    Chelsea / Chelsea vs Arsenal / West Ham vs Chelsea 一并填进 LIMIT 10
    槽位——真数据上每季仅 ~2 次真交锋，h2h 会被错标证据占满。
    """
    pack = build_pack(conn, cand)
    dates = [m["date"] for m in pack["h2h"]]
    assert "2024-04-10" in dates             # 真交锋：Arsenal vs West Ham
    # 反向真交锋必须双臂都在场：id=11（2 主 1 客）、id=15（2 主 1 客）——
    # 参数绑定若第二臂误绑 (t1,t2) 而非 (t2,t1)，这两场会被静默丢弃（复审 R1）
    assert "2024-04-06" in dates             # West Ham 2-2 Arsenal（反向）
    assert "2024-03-09" in dates             # West Ham 1-1 Arsenal（反向）
    for third in ("2024-03-23",              # Chelsea vs Arsenal（第三方）
                  "2024-04-13",              # Arsenal vs Chelsea（第三方）
                  "2024-04-03"):             # West Ham vs Chelsea（第三方）
        assert third not in dates
    assert all(d < "2024-04-20" for d in dates)


def test_standings_before_date(cand, conn):
    pack = build_pack(conn, cand)
    st = pack["standings"]            # {team_name: {pos, pts, played}}
    assert set(st) == {"Arsenal", "West Ham"}
    # 手算：截 2024-04-20 前，Arsenal 已赛场 = hist 含队 1 的 7 场 + 真交锋
    # id=18（04-10 Arsenal vs West Ham），第三方场不含队 1 不计
    assert st["Arsenal"]["played"] == 8


def test_write_packs_roundtrip(cand, conn, tmp_path):
    out = tmp_path / "packs"
    paths = write_packs(conn, [cand], out)
    data = json.loads((out / paths[1]).read_text(encoding="utf-8"))
    assert data["match"]["home"] == "Arsenal"
    assert paths[1].endswith("1.json")     # 文件名 = match_id，值 = 相对 out_dir 文件名
