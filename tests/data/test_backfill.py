"""matches 的 BFE 收盘列回填测试（Pinnacle 断供应对，spec §7.3，2026-09-04）。

全离线：只测 ``backfill_bfe_rows`` 的**纯 UPDATE 语义**（按身份列定位、只填
NULL、绝不 DELETE/INSERT——matches.id 被 backtest_predictions /
retro_attributions 外键引用，任何重建都会悬挂引用）。编排在 sync.py 的
``backfill_bfe``（复用 download/parse 管线）以 monkeypatch 掉网络缝另测。
"""
import sqlite3

import pytest

from fa.data.ingest import backfill_bfe_rows
from fa.db import connect, init_db

LEAGUE = "SP1"


@pytest.fixture
def conn(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


def team(c, name):
    row = c.execute("SELECT id FROM teams WHERE league=? AND name=?",
                    (LEAGUE, name)).fetchone()
    if row is not None:
        return row["id"]
    return c.execute("INSERT INTO teams (league, name) VALUES (?, ?)",
                     (LEAGUE, name)).lastrowid


def add_match(c, home, away, date, season=2026, **bfe):
    cols = {"bfe_home": None, "bfe_draw": None, "bfe_away": None,
            "over25_bfe": None}
    cols.update(bfe)
    return c.execute(
        "INSERT INTO matches (league, season, date, home_team_id, away_team_id,"
        " bfe_home, bfe_draw, bfe_away, over25_bfe, raw_line)"
        " VALUES (?,?,?,?,?,?,?,?,?, '{}')",
        (LEAGUE, season, date, team(c, home), team(c, away),
         cols["bfe_home"], cols["bfe_draw"], cols["bfe_away"],
         cols["over25_bfe"])).lastrowid


def rows_for(home, away, date, season=2026, **bfe):
    """构造 MatchRow 形状的轻量替身（backfill 只读这四个字段 + 身份列）。
    用 SimpleNamespace 而非 MatchRow，钉死「回填不依赖 parse 的其余字段」。"""
    from types import SimpleNamespace
    base = {"bfe_home": None, "bfe_draw": None, "bfe_away": None,
            "over25_bfe": None}
    base.update(bfe)
    return [SimpleNamespace(league=LEAGUE, season=season, date=date,
                            home=home, away=away, **base)]


def test_fills_null_bfe_by_identity_without_touching_ids(conn):
    mid = add_match(conn, "Osasuna", "Getafe", "2026-09-03")
    out = backfill_bfe_rows(conn, LEAGUE, 2026,
                            rows_for("Osasuna", "Getafe", "2026-09-03",
                                     bfe_home=2.1, over25_bfe=2.05))
    assert out == 1
    row = conn.execute("SELECT id, bfe_home, bfe_away, over25_bfe FROM matches"
                       " WHERE id=?", (mid,)).fetchone()
    assert row["id"] == mid                      # 原行原 id：UPDATE，不重建
    assert (row["bfe_home"], row["bfe_away"], row["over25_bfe"]) == (2.1, None, 2.05)


def test_never_overwrites_existing_bfe_values(conn):
    add_match(conn, "Osasuna", "Getafe", "2026-09-03", bfe_home=1.9)
    out = backfill_bfe_rows(conn, LEAGUE, 2026,
                            rows_for("Osasuna", "Getafe", "2026-09-03",
                                     bfe_home=2.1))
    assert out == 0                              # 已有值：不动也不计数
    assert conn.execute("SELECT bfe_home FROM matches").fetchone()["bfe_home"] == 1.9


def test_row_without_any_bfe_value_is_skipped(conn):
    add_match(conn, "Osasuna", "Getafe", "2026-09-03")
    out = backfill_bfe_rows(conn, LEAGUE, 2026,
                            rows_for("Osasuna", "Getafe", "2026-09-03"))
    assert out == 0
    assert conn.execute("SELECT bfe_home FROM matches").fetchone()["bfe_home"] is None


def test_unknown_team_row_is_skipped_not_created(conn):
    """回填绝不做 get_or_create——身份对不上的行跳过（计数 skipped），不建队。"""
    add_match(conn, "Osasuna", "Getafe", "2026-09-03")
    out = backfill_bfe_rows(conn, LEAGUE, 2026,
                            rows_for("New Team", "Getafe", "2026-09-03",
                                     bfe_home=2.1))
    assert out == 0
    n_teams = conn.execute("SELECT COUNT(*) c FROM teams").fetchone()["c"]
    assert n_teams == 2                          # 只有种子两队，没建新队


def test_no_matching_match_row_is_skipped(conn):
    add_match(conn, "Osasuna", "Getafe", "2026-09-03")
    out = backfill_bfe_rows(conn, LEAGUE, 2026,
                            rows_for("Osasuna", "Getafe", "2026-09-10",
                                     bfe_home=2.1))
    assert out == 0
