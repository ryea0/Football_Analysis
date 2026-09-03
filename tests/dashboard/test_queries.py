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
