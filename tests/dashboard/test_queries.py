"""dashboard 查询层测试：只读契约 + 各查询函数（设计 §5/§7）。

种子日期全部用字面量；断言数字均手算钉死。
"""
import sqlite3

import pytest

from fa.db import connect, init_db, set_meta


def test_connect_ro_missing_db_raises(tmp_path):
    from queries import connect_ro

    with pytest.raises(FileNotFoundError):
        connect_ro(tmp_path / "nope.db")


def test_connect_ro_is_readonly(tmp_path):
    from queries import connect_ro

    init_db(tmp_path / "t.db")
    ro = connect_ro(tmp_path / "t.db")
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("INSERT INTO meta (key, value) VALUES ('k', 'v')")
    finally:
        ro.close()


@pytest.fixture
def db(tmp_path):
    """读写连接（供种子写入）；被测函数只 SELECT。"""
    init_db(tmp_path / "t.db")
    conn = connect(tmp_path / "t.db")
    yield conn
    conn.close()


def _seed_bline_base(conn):
    """最小 B 线链：teams→run→fixture→recommendation×2（两轨）；bets 由各用例自定。"""
    conn.execute("INSERT INTO teams (league, name) VALUES ('E0', 'Team A'), ('E0', 'Team B')")
    conn.execute(
        "INSERT INTO runs (id, type, phase, started_at, status, credits_before, credits_after)"
        " VALUES (1, 'matchday', 'am', '2026-09-04T11:00:00', 'ok', 480, 460)")
    conn.execute(
        "INSERT INTO fixtures (id, league, event_key, source, kickoff_utc,"
        " home_team_id, away_team_id, status, created_at)"
        " VALUES (1, 'E0', 'evt1', 'oddsapi', '2026-09-05T19:00:00Z', 1, 2,"
        " 'scheduled', '2026-09-04T11:05:00')")
    conn.execute(
        "INSERT INTO recommendations (id, run_id, fixture_id, strategy, market, phase,"
        " model_p, market_p, best_odds, bookmaker, edge, ev, kelly_stake_frac, created_at)"
        " VALUES (1, 1, 1, 'model_only', 'H', 'am', 0.55, 0.50, 2.1, 'Pinnacle',"
        " 0.05, 0.155, 0.010, '2026-09-04T11:30:00'),"
        " (2, 1, 1, 'model_persona', 'H', 'am', 0.55, 0.50, 2.1, 'Pinnacle',"
        " 0.05, 0.155, 0.008, '2026-09-04T11:30:00')")
    conn.commit()


def test_b_summary(db):
    from queries import b_summary

    _seed_bline_base(db)
    set_meta(db, "paper_bankroll", "1000.0")
    set_meta(db, "odds_quota_remaining", "460")
    db.execute(
        "INSERT INTO recommendations (id, run_id, fixture_id, strategy, market, phase,"
        " model_p, market_p, best_odds, bookmaker, edge, ev, kelly_stake_frac, created_at)"
        " VALUES (3, 1, 1, 'model_only', 'D', 'am', 0.25, 0.30, 3.4, 'Pinnacle',"
        " -0.05, -0.10, 0.000, '2026-09-04T11:30:00')")
    db.execute(
        "INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker, odds_taken,"
        " stake, status, settled_at, return_amt, closing_odds, clv)"
        " VALUES (1, 'paper', '2026-09-04T12:00:00', 'Pinnacle', 2.5, 10.0, 'won',"
        " '2026-09-06T06:30:00', 25.0, 2.2, 0.136364)")            # 已结算：+15.0
    db.execute(
        "INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker, odds_taken,"
        " stake, status, settled_at, return_amt, closing_odds, clv)"
        " VALUES (2, 'paper', '2026-09-04T12:00:00', 'Pinnacle', 3.1, 8.0, 'pending',"
        " NULL, NULL, NULL, NULL)")                                # 未结算：不计 P&L
    db.execute(
        "INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker, odds_taken,"
        " stake, status, settled_at, return_amt, closing_odds, clv)"
        " VALUES (3, 'paper', '2026-09-04T12:00:00', 'Pinnacle', 3.4, 5.0, 'void',"
        " '2026-09-06T06:30:00', 0.0, NULL, NULL)")                # 作废：零损益，双侧不计
    db.commit()

    s = b_summary(db)
    assert s["bankroll"] == 1000.0
    assert s["quota_remaining"] == 460.0
    assert s["n_bets"] == 3
    assert s["n_pending"] == 1
    assert s["pnl"] == pytest.approx(15.0)          # 25 − 10（pending 的 8、void 的 5 都不进）
    assert s["staked"] == pytest.approx(10.0)
    assert s["quota_series"] == [{"id": 1, "type": "matchday", "phase": "am",
                                  "status": "ok", "started_at": "2026-09-04T11:00:00",
                                  "credits_before": 480, "credits_after": 460}]


def test_b_summary_empty_db(db):
    from queries import b_summary

    s = b_summary(db)
    assert s["bankroll"] is None and s["quota_remaining"] is None
    assert s["n_bets"] == 0 and s["n_pending"] == 0
    assert s["pnl"] == 0.0 and s["staked"] == 0.0
    assert s["quota_series"] == []


def test_b_summary_excludes_live_mode(db):
    from queries import b_summary

    _seed_bline_base(db)
    db.execute(
        "INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker, odds_taken,"
        " stake, status, settled_at, return_amt, closing_odds, clv)"
        " VALUES (1, 'paper', '2026-09-04T12:00:00', 'Pinnacle', 2.5, 10.0, 'won',"
        " '2026-09-06T06:30:00', 25.0, 2.2, 0.136364)")            # paper：计入
    db.execute(
        "INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker, odds_taken,"
        " stake, status, settled_at, return_amt, closing_odds, clv)"
        " VALUES (1, 'live', '2026-09-04T12:00:00', 'Pinnacle', 2.5, 10.0, 'won',"
        " '2026-09-06T06:30:00', 25.0, 2.2, 0.136364)")            # live：不进任何汇总
    db.commit()

    s = b_summary(db)
    assert s["n_bets"] == 1
    assert s["n_pending"] == 0
    assert s["pnl"] == pytest.approx(15.0)
    assert s["staked"] == pytest.approx(10.0)


def test_b_summary_missing_meta_keys(db):
    from queries import b_summary

    _seed_bline_base(db)          # 只有 B 线表数据，meta 业务键一个都没写
    s = b_summary(db)
    assert s["bankroll"] is None  # 「未记录」而非 0（设计 §6）
    assert s["quota_remaining"] is None


def _seed_rec_chain(db):
    """页2 链：2 队、2 fixture（1 个对齐 / 1 个未对齐）、2 run、4 推荐、2 注。"""
    _seed_bline_base(db)                                    # rec 1/2 已在
    db.execute(
        "INSERT INTO runs (id, type, phase, started_at, status)"
        " VALUES (2, 'matchday', 'pm', '2026-09-04T17:00:00', 'ok')")
    db.execute(
        "INSERT INTO fixtures (id, league, event_key, source, kickoff_utc,"
        " home_team_id, away_team_id, status, created_at)"
        " VALUES (2, 'E0', 'evt2', 'oddsapi', '2026-09-05T21:00:00Z', NULL, NULL,"
        " 'scheduled', '2026-09-04T17:05:00')")              # 未对齐 fixture
    db.execute(
        "INSERT INTO recommendations (id, run_id, fixture_id, strategy, market, phase,"
        " model_p, market_p, best_odds, bookmaker, edge, ev, kelly_stake_frac, created_at)"
        " VALUES (3, 2, 2, 'model_only', 'O2.5', 'pm', 0.60, 0.54, 1.95, 'Bet365',"
        " 0.06, 0.17, 0.012, '2026-09-04T17:30:00'),"
        " (4, 2, 2, 'model_persona', 'O2.5', 'pm', 0.60, 0.54, 1.95, 'Bet365',"
        " 0.06, 0.17, 0.010, '2026-09-04T17:30:00')")
    db.execute(
        "INSERT INTO bets (id, recommendation_id, mode, placed_at, bookmaker, odds_taken,"
        " stake, status, settled_at, return_amt, closing_odds, clv)"
        " VALUES (10, 1, 'paper', '2026-09-04T12:00:00', 'Pinnacle', 2.5, 10.0, 'won',"
        " '2026-09-06T06:30:00', 25.0, 2.2, 0.136364),"
        " (11, 3, 'paper', '2026-09-04T18:00:00', 'Bet365', 1.95, 12.0, 'pending',"
        " NULL, NULL, NULL, NULL)")
    db.commit()


def test_b_recommendations(db):
    from queries import b_recommendations

    _seed_rec_chain(db)
    df = b_recommendations(db)
    assert len(df) == 4
    assert list(df["id"]) == [4, 3, 2, 1]                    # created_at DESC
    row_evt1 = df[df["event_key"] == "evt1"].iloc[0]
    assert row_evt1["home"] == "Team A" and row_evt1["away"] == "Team B"
    row_evt2 = df[df["event_key"] == "evt2"].iloc[0]
    assert row_evt2["home"] is None and row_evt2["away"] is None   # 未对齐可空
    assert row_evt2["league"] == "E0"
    assert {"strategy", "phase", "market", "model_p", "market_p", "best_odds",
            "bookmaker", "edge", "ev", "kelly_stake_frac", "kickoff_utc"} <= set(df.columns)


def test_b_bets(db):
    from queries import b_bets

    _seed_rec_chain(db)
    df = b_bets(db)
    assert len(df) == 2
    assert list(df["id"]) == [11, 10]                        # placed_at DESC
    row = df[df["id"] == 10].iloc[0]
    assert row["market"] == "H" and row["strategy"] == "model_only"
    assert row["home"] == "Team A" and row["status"] == "won"
    assert row["clv"] == pytest.approx(0.136364)
    assert {"closing_odds", "return_amt", "settled_at", "phase", "edge"} <= set(df.columns)


def test_b_recommendations_empty(db):
    from queries import b_recommendations, b_bets

    assert b_recommendations(db).empty
    assert b_bets(db).empty


def _seed_ab_tracks(db):
    """两轨各 2 注（1 won + 1 lost），手算基准见断言。

    model_only     ：won(stake 10, ret 25, clv +0.05, d1) + lost(stake 10, clv −0.02, d2)
    model_persona  ：won(stake 4,  ret 12, clv +0.10, d1) + lost(stake 6,  clv −0.04, d2)
    需 4 条**各带一注**的独立 recommendation（bets UNIQUE(recommendation_id, mode)）：
    base 提供 rec 1/2（两轨各一），本夹具补 rec 5/6（两轨各一）。刻意不基于
    _seed_rec_chain——它已给 rec 1/3 下注，会撞 UNIQUE 约束。
    """
    _seed_bline_base(db)                                     # rec 1/2、run 1、fixture 1 已在
    db.execute(
        "INSERT INTO recommendations (id, run_id, fixture_id, strategy, market, phase,"
        " model_p, market_p, best_odds, bookmaker, edge, ev, kelly_stake_frac, created_at)"
        " VALUES (5, 1, 1, 'model_only', 'A', 'am', 0.40, 0.35, 3.0, 'Pinnacle',"
        " 0.05, 0.20, 0.010, '2026-09-04T11:30:00'),"
        " (6, 1, 1, 'model_persona', 'A', 'am', 0.40, 0.35, 3.0, 'Pinnacle',"
        " 0.05, 0.20, 0.008, '2026-09-04T11:30:00')")
    db.execute(
        "INSERT INTO bets (id, recommendation_id, mode, placed_at, bookmaker, odds_taken,"
        " stake, status, settled_at, return_amt, closing_odds, clv)"
        " VALUES (20, 1, 'paper', '2026-09-04T12:00:00', 'Pinnacle', 2.5, 10.0, 'won',"
        " '2026-09-05T06:30:00', 25.0, NULL, 0.05),"
        " (21, 5, 'paper', '2026-09-04T12:00:00', 'Pinnacle', 3.0, 10.0, 'lost',"
        " '2026-09-06T06:30:00', 0.0, NULL, -0.02),"
        " (22, 2, 'paper', '2026-09-04T12:00:00', 'Pinnacle', 3.0, 4.0, 'won',"
        " '2026-09-05T06:30:00', 12.0, NULL, 0.10),"
        " (23, 6, 'paper', '2026-09-04T12:00:00', 'Pinnacle', 3.0, 6.0, 'lost',"
        " '2026-09-06T06:30:00', 0.0, NULL, -0.04)")
    db.commit()


def test_b_ab_tracks(db):
    from queries import b_ab_tracks

    _seed_ab_tracks(db)
    db.execute(
        "INSERT INTO recommendations (id, run_id, fixture_id, strategy, market, phase,"
        " model_p, market_p, best_odds, bookmaker, edge, ev, kelly_stake_frac, created_at)"
        " VALUES (7, 1, 1, 'model_only', 'D', 'am', 0.30, 0.25, 3.6, 'Pinnacle',"
        " 0.05, 0.15, 0.009, '2026-09-04T11:30:00')")
    db.execute(
        "INSERT INTO bets (id, recommendation_id, mode, placed_at, bookmaker, odds_taken,"
        " stake, status, settled_at, return_amt, closing_odds, clv)"
        " VALUES (24, 7, 'paper', '2026-09-04T12:00:00', 'Pinnacle', 3.6, 5.0, 'void',"
        " '2026-09-06T06:30:00', 0.0, NULL, NULL)")
    db.commit()
    t = b_ab_tracks(db)
    mo = t["model_only"]
    assert mo["n"] == 3 and mo["n_settled"] == 2          # void 计入 n、不计入已结算
    assert mo["roi"] == pytest.approx((25 - 20) / 20)     # 0.25（void 不进分子分母）
    assert mo["clv_median"] == pytest.approx((0.05 - 0.02) / 2)   # 0.015
    assert mo["cum"]["dates"] == ["2026-09-05T06:30:00", "2026-09-06T06:30:00"]
    assert mo["cum"]["pnl"] == [pytest.approx(15.0), pytest.approx(5.0)]
    mp = t["model_persona"]
    assert mp["n"] == 2 and mp["n_settled"] == 2
    assert mp["roi"] == pytest.approx((12 - 10) / 10)         # 0.20
    assert mp["clv_median"] == pytest.approx((0.10 - 0.04) / 2)   # 0.03
    assert mp["cum"]["pnl"] == [pytest.approx(8.0), pytest.approx(2.0)]


def test_b_ab_tracks_empty(db):
    from queries import b_ab_tracks

    t = b_ab_tracks(db)
    for strat in ("model_only", "model_persona"):
        assert t[strat]["n"] == 0
        assert t[strat]["roi"] is None and t[strat]["clv_median"] is None
        assert t[strat]["cum"] == {"dates": [], "pnl": []}


def test_b_ab_tracks_all_pending(db):
    """全是 pending：roi/clv 有 None 路径（分母 0 → None），不抛异常。"""
    from queries import b_ab_tracks

    _seed_bline_base(db)
    db.execute(
        "INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker, odds_taken,"
        " stake, status) VALUES (1, 'paper', '2026-09-04T12:00:00', 'Pinnacle',"
        " 2.5, 10.0, 'pending')")
    db.commit()
    t = b_ab_tracks(db)
    assert t["model_only"]["n"] == 1 and t["model_only"]["n_settled"] == 0
    assert t["model_only"]["roi"] is None
    assert t["model_only"]["clv_median"] is None              # clv 全 NULL
