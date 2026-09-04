"""Paper 执行层测试（T7，spec §3.2 / §7.2 / §7.3 / §12.3）。

全离线：``urlopen`` 打成炸弹；种子直接插库（模式同 T5/T6 测试）——
``teams`` / ``matches``（M1 口径，含 psc 收盘列）/ ``fixtures``（T5 口径）/
``runs`` + ``recommendations``（T6 口径）。本层只写 ``bets`` / ``fixtures.status``
/ ``meta``，对 ``matches`` / ``backtest_predictions`` 只读（表边界 §12.1）。

时间注入缝（**唯一**）：``fa.pipeline.paper._now``（placed_at / settled_at 取此）。

bankroll 经 ``meta`` 分轨持久化（D2，键 ``paper_bankroll:{strategy}``，旧单键
``paper_bankroll`` 仅迁移读）：落注按**当前**轨余额乘仓位分数，结算把各轨净额
加回各轨余额——测试用 ``set_meta`` 造余额，再用落注断言它被读走。
"""
import urllib.request
from datetime import datetime, timezone

import pytest

from fa.db import connect, get_meta, init_db, set_meta
from fa.persona.apply import apply_verdict
from fa.pipeline import paper
from fa.pipeline.paper import (INITIAL_BANKROLL, LEGACY_BANKROLL_KEY,
                               bankroll_key, ensure_bankroll_migrated,
                               paper_summary, place_paper_bets, settle_paper_bets)

LEAGUE = "E0"
_NOW = datetime(2026, 9, 3, 9, 0, 0, tzinfo=timezone.utc)
_TS = "2026-09-03T09:00:00Z"


def _boom(*args, **kwargs):
    raise AssertionError("Paper 执行层不得触网")


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(paper, "_now", lambda: _NOW)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)


@pytest.fixture
def conn(tmp_path, clock):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    c.commit()
    yield c
    c.close()


# ---------------------------------------------------------------- 种子工具

def team_id(c, name, league=LEAGUE):
    row = c.execute("SELECT id FROM teams WHERE league=? AND name=?",
                    (league, name)).fetchone()
    if row is not None:
        return row["id"]
    return c.execute("INSERT INTO teams (league, name) VALUES (?, ?)",
                     (league, name)).lastrowid


def add_match(c, home, away, date, fthg, ftag, league=LEAGUE, season=2026, **closing):
    """M1 口径完赛行；closing 以 psc_home/.../over25_psc 与 bfe_home/.../over25_bfe 传入。"""
    cols = {"psc_home": None, "psc_draw": None, "psc_away": None,
            "over25_psc": None, "bfe_home": None, "bfe_draw": None,
            "bfe_away": None, "over25_bfe": None}
    cols.update(closing)
    return c.execute(
        "INSERT INTO matches (league, season, date, home_team_id, away_team_id,"
        " fthg, ftag, psc_home, psc_draw, psc_away, over25_psc,"
        " bfe_home, bfe_draw, bfe_away, over25_bfe, raw_line)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, '{}')",
        (league, season, date, team_id(c, home, league), team_id(c, away, league),
         fthg, ftag, cols["psc_home"], cols["psc_draw"], cols["psc_away"],
         cols["over25_psc"], cols["bfe_home"], cols["bfe_draw"],
         cols["bfe_away"], cols["over25_bfe"])).lastrowid


def add_fixture(c, event_key, home, away, kickoff="2026-09-02T14:00:00Z",
                league=LEAGUE):
    return c.execute(
        "INSERT INTO fixtures (league, event_key, source, kickoff_utc,"
        " home_team_id, away_team_id, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (league, event_key, "oddsapi", kickoff,
         None if home is None else team_id(c, home, league),
         None if away is None else team_id(c, away, league),
         "scheduled", "2026-09-01T08:00:00Z")).lastrowid


def add_run(c, phase="am"):
    return c.execute(
        "INSERT INTO runs (type, phase, started_at, status)"
        " VALUES ('matchday', ?, '2026-09-03T09:00:00Z', 'ok')", (phase,)).lastrowid


def add_rec(c, run_id, fixture_id, market, phase="am", kelly=0.02, best_odds=2.0,
            bookmaker="pinnacle", final_stake_frac=None, strategy="model_only"):
    return c.execute(
        "INSERT INTO recommendations (run_id, fixture_id, strategy, market, phase,"
        " model_p, market_p, best_odds, bookmaker, edge, ev, kelly_stake_frac,"
        " final_stake_frac, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, fixture_id, strategy, market, phase, 0.5, 0.4, best_odds,
         bookmaker, 0.1, 0.3, kelly, final_stake_frac,
         "2026-09-03T09:00:00Z")).lastrowid


def bets(c):
    sql = ("SELECT b.*, r.market AS market FROM bets b"
           " JOIN recommendations r ON r.id = b.recommendation_id ORDER BY b.id")
    return [dict(r) for r in c.execute(sql)]


def bet_by_market(c, market):
    rows = [b for b in bets(c) if b["market"] == market]
    assert len(rows) == 1, (market, rows)
    return rows[0]


def fixture_status(c):
    return {r["id"]: r["status"] for r in
            c.execute("SELECT id, status FROM fixtures")}


# ---------------------------------------------------------------- 常量单源


def test_bankroll_key_is_the_canonical_constant():
    """控制器裁定（T14 收尾）：分轨键单源 ``bankroll_key()``——旧单键别名
    ``BANKROLL_KEY`` 已随 render 分轨改造删除，``paper_bankroll`` 只剩迁移读
    这一个合法读点（:data:`LEGACY_BANKROLL_KEY`），不再有任何别名。"""
    assert not hasattr(paper, "BANKROLL_KEY")            # 别名已删，无双源
    assert paper.LEGACY_BANKROLL_KEY == LEGACY_BANKROLL_KEY == "paper_bankroll"
    assert bankroll_key("model_only") != LEGACY_BANKROLL_KEY   # 分轨键 ≠ legacy
    assert INITIAL_BANKROLL == 1000.0


# ---------------------------------------------------------------- 落注


def test_places_this_runs_model_only_recs(conn):
    """别 run 的推荐不落；同 run 跨轨落注（D2 双轨）另见分轨一节。"""
    run = add_run(conn)
    fx1 = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    fx2 = add_fixture(conn, "ev2", "Everton", "Spurs")
    r1 = add_rec(conn, run, fx1, "H", kelly=0.02, best_odds=2.0)
    r2 = add_rec(conn, run, fx2, "O2.5", kelly=0.01, best_odds=1.9)
    other = add_run(conn)
    add_rec(conn, other, fx1, "A")                          # 别的 run → 不落

    assert place_paper_bets(conn, run) == 2
    rows = bets(conn)
    assert {b["recommendation_id"] for b in rows} == {r1, r2}
    h = bet_by_market(conn, "H")
    assert h["mode"] == "paper" and h["status"] == "pending"
    assert h["odds_taken"] == 2.0 and h["bookmaker"] == "pinnacle"
    assert h["stake"] == pytest.approx(20.0)                # 0.02 × 1000
    assert h["placed_at"] == _TS
    assert h["return_amt"] is None and h["settled_at"] is None
    assert h["closing_odds"] is None and h["clv"] is None
    o = bet_by_market(conn, "O2.5")
    assert o["stake"] == pytest.approx(10.0) and o["odds_taken"] == 1.9


def test_first_use_initializes_bankroll_meta(conn):
    assert get_meta(conn, bankroll_key("model_only")) is None
    run = add_run(conn)
    add_rec(conn, run, add_fixture(conn, "ev1", "Chelsea", "Arsenal"), "H")
    place_paper_bets(conn, run)
    assert get_meta(conn, bankroll_key("model_only")) == "1000.0"  # 首次使用即初始化


def test_no_placements_leaves_meta_untouched(conn):
    place_paper_bets(conn, add_run(conn))                   # 该 run 无推荐
    assert get_meta(conn, bankroll_key("model_only")) is None      # 未使用 → 不初始化
    assert get_meta(conn, bankroll_key("model_persona")) is None
    assert bets(conn) == []


def test_stake_prefers_final_stake_frac_over_kelly(conn):
    """M4 位：final_stake_frac 非 NULL 即用它（M3 恒 NULL → 退回 kelly）。"""
    run = add_run(conn)
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    add_rec(conn, run, fx, "H", kelly=0.02, final_stake_frac=0.005)
    add_rec(conn, run, fx, "A", kelly=0.02)
    place_paper_bets(conn, run)
    by_market = {b["market"]: b for b in bets(conn)}
    assert by_market["H"]["stake"] == pytest.approx(5.0)
    assert by_market["A"]["stake"] == pytest.approx(20.0)


def test_stake_scales_with_current_bankroll(conn):
    run = add_run(conn)
    add_rec(conn, run, add_fixture(conn, "ev1", "Chelsea", "Arsenal"), "H", kelly=0.02)
    set_meta(conn, bankroll_key("model_only"), "2000.0")
    conn.commit()
    place_paper_bets(conn, run)
    assert bet_by_market(conn, "H")["stake"] == pytest.approx(40.0)


def test_rerunning_same_run_places_zero_new_bets(conn):
    run = add_run(conn)
    add_rec(conn, run, add_fixture(conn, "ev1", "Chelsea", "Arsenal"), "H")
    assert place_paper_bets(conn, run) == 1
    assert place_paper_bets(conn, run) == 0                 # 同 run 重跑：全已下
    assert len(bets(conn)) == 1


def test_pm_rec_of_same_fixture_market_is_not_rebet(conn):
    """am/pm 两窗各有一条推荐（recommendations 的 UNIQUE 含 phase），但落注去重在
    (fixture, market, strategy, mode) 级——pm 不重下；换 market 照常可落。"""
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    am, pm = add_run(conn, "am"), add_run(conn, "pm")
    add_rec(conn, am, fx, "H", phase="am")
    add_rec(conn, pm, fx, "H", phase="pm")
    assert place_paper_bets(conn, am) == 1
    assert place_paper_bets(conn, pm) == 0
    assert len(bets(conn)) == 1
    assert place_paper_bets(conn, add_run(conn, "pm")) == 0  # 该 run 无推荐
    late = add_run(conn, "pm")
    add_rec(conn, late, fx, "O2.5", phase="pm")
    assert place_paper_bets(conn, late) == 1                 # O2.5 未下过
    assert {b["market"] for b in bets(conn)} == {"H", "O2.5"}


def test_live_bet_does_not_block_paper_placement(conn):
    """双模式分账（§7.2）：live 已下不挡 paper 落注。"""
    run = add_run(conn)
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    rec = add_rec(conn, run, fx, "H")
    conn.execute("INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker,"
                 " odds_taken, stake, status) VALUES (?,?,?,?,?,?,?)",
                 (rec, "live", _TS, "pinnacle", 2.0, 1.0, "pending"))
    conn.commit()
    assert place_paper_bets(conn, run) == 1
    assert {b["mode"] for b in bets(conn)} == {"live", "paper"}


# ---------------------------------------------------------------- 结算：配对与判胜


@pytest.mark.parametrize("market,fthg,ftag,won", [
    ("H", 2, 1, True), ("H", 1, 1, False), ("H", 0, 1, False),
    ("D", 1, 1, True), ("D", 2, 1, False), ("D", 0, 2, False),
    ("A", 0, 1, True), ("A", 2, 1, False),
    ("O2.5", 2, 1, True),          # 恰 3 球：>= 钉死（含端点）
    ("O2.5", 2, 0, False),         # 2 球
])
def test_four_market_outcome_rules(conn, market, fthg, ftag, won):
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    add_match(conn, "Chelsea", "Arsenal", "2026-09-02", fthg, ftag)
    run = add_run(conn)
    add_rec(conn, run, fx, market)
    place_paper_bets(conn, run)
    out = settle_paper_bets(conn)
    assert out["settled"] == 1
    row = bet_by_market(conn, market)
    assert row["status"] == ("won" if won else "lost")
    assert row["settled_at"] == _TS
    assert row["return_amt"] == (pytest.approx(40.0) if won else 0.0)  # 20 × 2.0


def test_settlement_pairs_fixture_to_match_within_two_days(conn):
    """配对键 (league, home, away) + kickoff 当日起 ≤2 天；上端点含、差 3 天出局。"""
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    add_match(conn, "Chelsea", "Arsenal", "2026-09-04", 2, 1)      # 差 2 天（含端点）
    run = add_run(conn)
    add_rec(conn, run, fx, "H")
    place_paper_bets(conn, run)
    assert settle_paper_bets(conn)["settled"] == 1

    fx2 = add_fixture(conn, "ev2", "Everton", "Spurs")
    add_match(conn, "Everton", "Spurs", "2026-09-05", 2, 1)        # 差 3 天
    run2 = add_run(conn)
    add_rec(conn, run2, fx2, "H")
    place_paper_bets(conn, run2)
    assert settle_paper_bets(conn)["settled"] == 0
    late = [b for b in bets(conn) if b["recommendation_id"]
            == conn.execute("SELECT id FROM recommendations WHERE run_id=?",
                            (run2,)).fetchone()["id"]]
    assert [b["status"] for b in late] == ["pending"]


def test_pre_kickoff_match_does_not_latch_settlement(conn):
    """kickoff **之前**的同配对完赛（杯赛/首回合，fixture 推迟时 kickoff 后移）
    不得拿来结算：只认 kickoff 当日或之后，赛前 1–2 天同样出局（T7 审查加固）。"""
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal", kickoff="2026-09-02T14:00:00Z")
    add_match(conn, "Chelsea", "Arsenal", "2026-08-31", 0, 0)      # 差 −2 天
    add_match(conn, "Chelsea", "Arsenal", "2026-09-01", 3, 0)      # 差 −1 天
    run = add_run(conn)
    add_rec(conn, run, fx, "H")
    place_paper_bets(conn, run)
    out = settle_paper_bets(conn)
    assert out["settled"] == 0 and out["won"] == 0 and out["pnl"] == 0.0
    assert out["clv_median"] is None
    assert bet_by_market(conn, "H")["status"] == "pending"
    assert fixture_status(conn)[fx] == "scheduled"                 # 未 latch 成 finished


def test_ambiguous_pairing_takes_closest_post_kickoff_date(conn):
    """同键多场：先剔赛前场，再取日期差最小者，平手取日期早、id 小——确定性。"""
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal", kickoff="2026-09-02T14:00:00Z")
    add_match(conn, "Chelsea", "Arsenal", "2026-08-31", 0, 0)      # 赛前 −2 天：出局
    add_match(conn, "Chelsea", "Arsenal", "2026-09-04", 0, 0)      # 差 +2 天
    add_match(conn, "Chelsea", "Arsenal", "2026-09-03", 3, 0)      # 差 +1 天 ← 胜出
    run = add_run(conn)
    add_rec(conn, run, fx, "H")
    place_paper_bets(conn, run)
    out = settle_paper_bets(conn)
    assert out["settled"] == 1 and out["won"] == 1
    # 若赛前那场（0-0）或 +2 天那场（0-0）被取走，H 会判负 → return 0.0
    assert bet_by_market(conn, "H")["return_amt"] == pytest.approx(40.0)


def test_unpaired_fixture_stays_pending(conn):
    """无论无完赛行、主客互换、别的队、未对齐侧、还是缺比分：一律保持 pending（不猜）。"""
    run = add_run(conn)
    fx_none = add_fixture(conn, "ev-none", "Chelsea", "Arsenal")
    fx_unaligned = add_fixture(conn, "ev-null", "Chelsea", None)
    add_match(conn, "Everton", "Spurs", "2026-09-02", 1, 0)          # 别的队
    add_match(conn, "Chelsea", "Arsenal", "2026-09-02", None, None)  # 无比分
    add_match(conn, "Arsenal", "Chelsea", "2026-09-02", 1, 0)        # 主客互换（T7 审查）
    add_rec(conn, run, fx_none, "H")
    add_rec(conn, run, fx_unaligned, "A")
    assert place_paper_bets(conn, run) == 2
    out = settle_paper_bets(conn)
    assert out["settled"] == 0 and out["won"] == 0 and out["pnl"] == 0.0
    assert out["clv_median"] is None
    assert all(b["status"] == "pending" for b in bets(conn))
    assert get_meta(conn, bankroll_key("model_only")) == "1000.0"    # 余额不动
    assert set(fixture_status(conn).values()) == {"scheduled"}


def test_settlement_is_idempotent_and_bankroll_not_double_counted(conn):
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    add_match(conn, "Chelsea", "Arsenal", "2026-09-02", 2, 1,
              psc_home=1.8, psc_draw=3.4)
    run = add_run(conn)
    add_rec(conn, run, fx, "H", kelly=0.02, best_odds=2.0)   # won → 40.0, clv +0.1111
    add_rec(conn, run, fx, "D", kelly=0.01, best_odds=3.5)   # lost → 0.0, clv +0.0294
    place_paper_bets(conn, run)
    first = settle_paper_bets(conn)
    assert first["settled"] == 2 and first["won"] == 1
    assert first["pnl"] == pytest.approx(10.0)
    assert first["clv_median"] == pytest.approx((2.0 / 1.8 - 1 + 3.5 / 3.4 - 1) / 2)
    assert get_meta(conn, bankroll_key("model_only")) == "1010.0"
    again = settle_paper_bets(conn)
    assert again["settled"] == 0 and again["won"] == 0       # 幂等
    assert again["by_strategy"]["model_only"]["settled"] == 0
    assert get_meta(conn, bankroll_key("model_only")) == "1010.0"   # 不重复计入
    assert len(bets(conn)) == 2


def test_loss_drains_bankroll(conn):
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    add_match(conn, "Chelsea", "Arsenal", "2026-09-02", 0, 1)
    run = add_run(conn)
    add_rec(conn, run, fx, "H", kelly=0.02, best_odds=2.0)
    place_paper_bets(conn, run)
    out = settle_paper_bets(conn)
    assert out["won"] == 0 and out["pnl"] == pytest.approx(-20.0)
    assert get_meta(conn, bankroll_key("model_only")) == "980.0"


def test_live_bets_are_not_settled_by_paper_provider(conn):
    """live 归人工 `fa bet settle`（§7.2/§12.2）：本结算只动 paper。"""
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    add_match(conn, "Chelsea", "Arsenal", "2026-09-02", 2, 1)
    run = add_run(conn)
    rec = add_rec(conn, run, fx, "H")
    conn.execute("INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker,"
                 " odds_taken, stake, status) VALUES (?,?,?,?,?,?,?)",
                 (rec, "live", _TS, "pinnacle", 2.0, 1.0, "pending"))
    conn.commit()
    place_paper_bets(conn, run)
    assert settle_paper_bets(conn)["settled"] == 1
    rows = {b["mode"]: b for b in bets(conn)}
    assert rows["live"]["status"] == "pending"
    assert rows["paper"]["status"] == "won"


def test_settle_with_nothing_settled_does_not_touch_bankroll_meta(conn):
    """空台账、或 pending 但配不上：结算不落 meta——「未初始化」保持可观测，
    且每日空跑不产生 meta 写。"""
    out = settle_paper_bets(conn)
    assert out["settled"] == 0 and out["won"] == 0 and out["pnl"] == 0.0
    assert out["clv_median"] is None
    assert out["by_strategy"]["model_only"]["settled"] == 0
    assert get_meta(conn, bankroll_key("model_only")) is None
    assert get_meta(conn, LEGACY_BANKROLL_KEY) is None
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")      # 无完赛行 → 配不上
    rec = add_rec(conn, add_run(conn), fx, "H")
    conn.execute("INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker,"
                 " odds_taken, stake, status) VALUES (?,?,?,?,?,?,?)",
                 (rec, "paper", _TS, "pinnacle", 2.0, 20.0, "pending"))
    conn.commit()
    assert settle_paper_bets(conn)["settled"] == 0
    assert get_meta(conn, bankroll_key("model_only")) is None  # 未被「顺手」初始化


def test_settled_fixture_is_marked_finished(conn):
    """fixtures.status 由结算演进（db.py 词表注 + T5「结算态不被同步重置」）。"""
    fx_paired = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    fx_open = add_fixture(conn, "ev2", "Everton", "Spurs")
    add_match(conn, "Chelsea", "Arsenal", "2026-09-02", 2, 1)
    run = add_run(conn)
    add_rec(conn, run, fx_paired, "H")
    add_rec(conn, run, fx_open, "H")
    place_paper_bets(conn, run)
    settle_paper_bets(conn)
    st = fixture_status(conn)
    assert st[fx_paired] == "finished"
    assert st[fx_open] == "scheduled"                        # 未配对 → 不改状态


# ---------------------------------------------------------------- 结算：CLV


def test_clv_uses_pinnacle_closing_and_skips_missing(conn):
    """clv = odds_taken/closing − 1；收盘缺失 → NULL 且不进中位数（NULL 安全）。"""
    fx1 = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    add_match(conn, "Chelsea", "Arsenal", "2026-09-02", 2, 1, psc_home=1.6)
    fx2 = add_fixture(conn, "ev2", "Everton", "Spurs")
    add_match(conn, "Everton", "Spurs", "2026-09-02", 0, 0, psc_away=2.0)
    fx3 = add_fixture(conn, "ev3", "Chelsea", "Everton")
    add_match(conn, "Chelsea", "Everton", "2026-09-02", 1, 1, psc_draw=2.5)
    fx4 = add_fixture(conn, "ev4", "Arsenal", "Spurs")
    add_match(conn, "Arsenal", "Spurs", "2026-09-02", 3, 0)   # 收盘缺失
    run = add_run(conn)
    add_rec(conn, run, fx1, "H", best_odds=2.0)               # won, 2/1.6−1 = +0.25
    add_rec(conn, run, fx2, "A", best_odds=2.0)               # lost, 2/2.0−1 = 0.0
    add_rec(conn, run, fx3, "D", best_odds=2.0)               # won, 2/2.5−1 = −0.2
    add_rec(conn, run, fx4, "H", best_odds=2.0)               # won, 无收盘 → NULL
    place_paper_bets(conn, run)
    out = settle_paper_bets(conn)
    assert out["settled"] == 4 and out["won"] == 3
    got = {(b["market"], b["closing_odds"]): b["clv"] for b in bets(conn)}
    assert got[("H", 1.6)] == pytest.approx(0.25)
    assert got[("A", 2.0)] == pytest.approx(0.0)
    assert got[("D", 2.5)] == pytest.approx(-0.2)
    assert got[("H", None)] is None                # 缺收盘：NULL，不硬算、不炸
    assert out["clv_median"] == pytest.approx(0.0)            # median([.25, 0, −.2])


def test_o25_closing_column_is_over25_psc(conn):
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    add_match(conn, "Chelsea", "Arsenal", "2026-09-02", 2, 1, over25_psc=1.75)
    run = add_run(conn)
    add_rec(conn, run, fx, "O2.5", best_odds=1.9)
    place_paper_bets(conn, run)
    out = settle_paper_bets(conn)
    assert out["won"] == 1
    row = bet_by_market(conn, "O2.5")
    assert row["closing_odds"] == 1.75
    assert row["clv"] == pytest.approx(1.9 / 1.75 - 1)


def test_hda_closing_columns_follow_market(conn):
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    add_match(conn, "Chelsea", "Arsenal", "2026-09-02", 1, 1,
              psc_home=2.1, psc_draw=3.3, psc_away=3.7)
    run = add_run(conn)
    add_rec(conn, run, fx, "H", best_odds=2.2)
    add_rec(conn, run, fx, "D", best_odds=3.6)
    add_rec(conn, run, fx, "A", best_odds=4.0)
    place_paper_bets(conn, run)
    settle_paper_bets(conn)
    by_market = {b["market"]: b for b in bets(conn)}
    assert by_market["H"]["closing_odds"] == 2.1
    assert by_market["D"]["closing_odds"] == 3.3
    assert by_market["A"]["closing_odds"] == 3.7
    assert by_market["D"]["clv"] == pytest.approx(3.6 / 3.3 - 1)


# ---------------------------------------------------------------- 纪律


def test_exception_mid_settlement_leaves_no_partial_commit(conn, tmp_path, monkeypatch):
    """单 commit 纪律：任一 fixture 上抛 → 整体无半写（用**另一连接**验证提交面）。"""
    fx1 = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    fx2 = add_fixture(conn, "ev2", "Everton", "Spurs")
    add_match(conn, "Chelsea", "Arsenal", "2026-09-02", 2, 1)
    add_match(conn, "Everton", "Spurs", "2026-09-02", 0, 0)
    run = add_run(conn)
    add_rec(conn, run, fx1, "H")
    add_rec(conn, run, fx2, "H")
    place_paper_bets(conn, run)

    real, calls = paper._paired_match, []

    def flaky(conn_, league, home_id, away_id, kickoff_date):
        calls.append(league)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return real(conn_, league, home_id, away_id, kickoff_date)

    monkeypatch.setattr(paper, "_paired_match", flaky)
    with pytest.raises(RuntimeError):
        settle_paper_bets(conn)

    reader = connect(tmp_path / "t.db")                 # 只见已提交状态
    try:
        assert all(b["status"] == "pending" and b["return_amt"] is None
                   and b["settled_at"] is None for b in bets(reader))
        assert set(fixture_status(reader).values()) == {"scheduled"}
        assert get_meta(reader, bankroll_key("model_only")) == "1000.0"
    finally:
        reader.close()


def test_provider_never_writes_a_line_tables(conn):
    """表边界（§12.1）：matches 只读，backtest_predictions 不碰。"""
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    add_match(conn, "Chelsea", "Arsenal", "2026-09-02", 2, 1, psc_home=1.8)
    run = add_run(conn)
    add_rec(conn, run, fx, "H")
    place_paper_bets(conn, run)
    query = "SELECT id, fthg, ftag, psc_home FROM matches"
    before = [dict(r) for r in conn.execute(query)]
    settle_paper_bets(conn)
    assert [dict(r) for r in conn.execute(query)] == before
    assert conn.execute(
        "SELECT COUNT(*) c FROM backtest_predictions").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"] == 1


# ---------------------------------------------------------------- paper_summary


def test_summary_keys_are_exactly_the_brief_contract(conn):
    """D2：paper_summary 分轨——键 = strategy（STRATEGIES 序），值为原形状 dict。"""
    assert list(paper_summary(conn)) == ["model_only", "model_persona"]
    for track in paper_summary(conn).values():
        assert set(track) == {"n", "staked", "returned", "roi",
                              "pending", "bankroll", "clv_median"}


def test_summary_of_empty_ledger(conn):
    empty = {"n": 0, "staked": 0.0, "returned": 0.0, "roi": None, "pending": 0,
             "bankroll": None, "clv_median": None}
    assert paper_summary(conn) == {"model_only": empty, "model_persona": empty}


def test_summary_counts_money_and_clv(conn):
    fx1 = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    add_match(conn, "Chelsea", "Arsenal", "2026-09-02", 2, 1, psc_home=1.8)
    fx2 = add_fixture(conn, "ev2", "Everton", "Spurs")
    add_match(conn, "Everton", "Spurs", "2026-09-02", 3, 0, psc_away=2.0)
    fx3 = add_fixture(conn, "ev3", "Chelsea", "Everton")      # 未完赛 → pending
    run = add_run(conn)
    add_rec(conn, run, fx1, "H", kelly=0.02, best_odds=2.0)   # won   → 40.0
    add_rec(conn, run, fx2, "A", kelly=0.01, best_odds=2.5)   # lost  → 0.0
    add_rec(conn, run, fx3, "D", kelly=0.02, best_odds=3.4)   # pending
    place_paper_bets(conn, run)
    assert settle_paper_bets(conn)["settled"] == 2
    s = paper_summary(conn)["model_only"]                     # 种子全在 model_only 轨
    assert s["n"] == 3 and s["pending"] == 1
    assert s["staked"] == pytest.approx(30.0)                 # 已结算两注的注金
    assert s["returned"] == pytest.approx(40.0)
    assert s["roi"] == pytest.approx(10.0 / 30.0)
    assert s["bankroll"] == pytest.approx(1010.0)
    assert s["clv_median"] == pytest.approx((2.0 / 1.8 - 1 + 2.5 / 2.0 - 1) / 2)
    other = paper_summary(conn)["model_persona"]              # 未用轨：全零 + 未初始化
    assert other["n"] == 0 and other["bankroll"] is None and other["roi"] is None


def test_summary_money_columns_are_settled_only(conn):
    """staked/returned/roi 只计已结算注：未结仓位不得把 ROI 拖成假负（§7.3）。"""
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")       # 无完赛行 → 永远 pending
    run = add_run(conn)
    add_rec(conn, run, fx, "H", kelly=0.02, best_odds=2.0)
    place_paper_bets(conn, run)
    s = paper_summary(conn)["model_only"]
    assert s["n"] == 1 and s["pending"] == 1
    assert s["staked"] == 0.0 and s["returned"] == 0.0
    assert s["roi"] is None
    assert s["bankroll"] == pytest.approx(1000.0)


def test_summary_is_scoped_to_paper_mode(conn):
    run = add_run(conn)
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    rec = add_rec(conn, run, fx, "H", kelly=0.02, best_odds=2.0)
    conn.execute("INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker,"
                 " odds_taken, stake, status) VALUES (?,?,?,?,?,?,?)",
                 (rec, "live", _TS, "pinnacle", 2.0, 500.0, "pending"))
    conn.commit()
    place_paper_bets(conn, run)
    s = paper_summary(conn)["model_only"]
    assert s["n"] == 1 and s["staked"] == 0.0                 # live 的 500 不入 paper 账


# ------------------------------------------------- 收盘基准 fallback 链（§7.3）
# Pinnacle 断供（football-data 2025-12 起）后：psc 优先，缺失 fallback BFE，
# 并以 bets.closing_source 诚实记账用了哪个基准（'pinnacle' / 'betfair' / NULL）

def test_closing_prefers_pinnacle_over_betfair(conn):
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    add_match(conn, "Chelsea", "Arsenal", "2026-09-02", 2, 1,
              psc_home=1.6, bfe_home=2.2)
    run = add_run(conn)
    add_rec(conn, run, fx, "H", best_odds=2.0)
    place_paper_bets(conn, run)
    settle_paper_bets(conn)
    row = bet_by_market(conn, "H")
    assert row["closing_odds"] == 1.6
    assert row["closing_source"] == "pinnacle"
    assert row["clv"] == pytest.approx(2.0 / 1.6 - 1)


def test_closing_falls_back_to_betfair_when_pinnacle_missing(conn):
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    add_match(conn, "Chelsea", "Arsenal", "2026-09-02", 2, 1, bfe_home=2.2)
    run = add_run(conn)
    add_rec(conn, run, fx, "H", best_odds=2.0)
    place_paper_bets(conn, run)
    settle_paper_bets(conn)
    row = bet_by_market(conn, "H")
    assert row["closing_odds"] == 2.2
    assert row["closing_source"] == "betfair"
    assert row["clv"] == pytest.approx(2.0 / 2.2 - 1)


def test_both_benchmarks_missing_leaves_null_closing_and_source(conn):
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    add_match(conn, "Chelsea", "Arsenal", "2026-09-02", 2, 1)   # 双基准皆缺
    run = add_run(conn)
    add_rec(conn, run, fx, "H", best_odds=2.0)
    place_paper_bets(conn, run)
    out = settle_paper_bets(conn)
    row = bet_by_market(conn, "H")
    assert row["closing_odds"] is None
    assert row["closing_source"] is None
    assert row["clv"] is None
    assert out["clv_median"] is None


def test_o25_closing_falls_back_to_over25_bfe(conn):
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    add_match(conn, "Chelsea", "Arsenal", "2026-09-02", 2, 1, over25_bfe=1.7)
    run = add_run(conn)
    add_rec(conn, run, fx, "O2.5", best_odds=1.9)
    place_paper_bets(conn, run)
    settle_paper_bets(conn)
    row = bet_by_market(conn, "O2.5")
    assert row["closing_odds"] == 1.7
    assert row["closing_source"] == "betfair"
    assert row["clv"] == pytest.approx(1.9 / 1.7 - 1)


# ------------------------------------------------- backfill_clv（台账回填）

def test_backfill_clv_fills_settled_bets_after_bfe_arrives(conn):
    """结算时无基准（双缺）→ 注已 settled、closing NULL；BFE 落库后回填补上。"""
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    mid = add_match(conn, "Chelsea", "Arsenal", "2026-09-02", 2, 1)
    run = add_run(conn)
    add_rec(conn, run, fx, "H", best_odds=2.0)
    place_paper_bets(conn, run)
    settle_paper_bets(conn)
    assert bet_by_market(conn, "H")["closing_odds"] is None

    conn.execute("UPDATE matches SET bfe_home=2.2 WHERE id=?", (mid,))
    conn.commit()
    out = paper.backfill_clv(conn)
    assert out["filled"] == 1
    row = bet_by_market(conn, "H")
    assert row["closing_odds"] == 2.2
    assert row["closing_source"] == "betfair"
    assert row["clv"] == pytest.approx(2.0 / 2.2 - 1)


def test_backfill_clv_is_idempotent_and_never_overwrites(conn):
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    add_match(conn, "Chelsea", "Arsenal", "2026-09-02", 2, 1, psc_home=1.6)
    run = add_run(conn)
    add_rec(conn, run, fx, "H", best_odds=2.0)
    place_paper_bets(conn, run)
    settle_paper_bets(conn)                     # 结算即有 pinnacle 收盘
    assert paper.backfill_clv(conn)["filled"] == 0   # 已有收盘：不碰
    row = bet_by_market(conn, "H")
    assert row["closing_source"] == "pinnacle"


def test_backfill_clv_ignores_pending_bets(conn):
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")   # 无完赛行 → 注停留 pending
    add_match(conn, "Chelsea", "Arsenal", "2026-09-10", 2, 1, bfe_home=2.2)
    run = add_run(conn)
    add_rec(conn, run, fx, "H", best_odds=2.0)
    place_paper_bets(conn, run)
    assert paper.backfill_clv(conn)["filled"] == 0
    assert bet_by_market(conn, "H")["status"] == "pending"


# ---------------------------------------------------------------- D2 分轨 bankroll

MO, MP = bankroll_key("model_only"), bankroll_key("model_persona")


def _run_id(c):
    """种子里唯一 run 的 id（分轨用例都只造一个 run）。"""
    return c.execute("SELECT run_id FROM recommendations LIMIT 1").fetchone()[0]


def test_bankroll_key_shape():
    assert bankroll_key("model_only") == "paper_bankroll:model_only"
    assert bankroll_key("model_persona") == "paper_bankroll:model_persona"


def test_migration_copies_legacy_to_model_only(conn):
    """M3 旧单键 → ``:model_only`` 轨（M3 的 14 注全 model_only，归属无歧义）；
    旧键删除（无双源），且幂等——重入不再改写。"""
    set_meta(conn, LEGACY_BANKROLL_KEY, "990.5")
    conn.commit()
    ensure_bankroll_migrated(conn)
    conn.commit()
    assert get_meta(conn, MO) == "990.5"
    assert get_meta(conn, LEGACY_BANKROLL_KEY) is None       # 旧键删除，无双源
    ensure_bankroll_migrated(conn)
    conn.commit()                                            # 幂等
    assert get_meta(conn, MO) == "990.5"
    assert get_meta(conn, LEGACY_BANKROLL_KEY) is None


def test_migration_keeps_new_track_value_if_already_written(conn):
    """新轨已有账（人先写）→ 只删旧键，不覆盖新账（防迁移倒灌）。"""
    set_meta(conn, MO, "1234.0")
    set_meta(conn, LEGACY_BANKROLL_KEY, "990.5")
    conn.commit()
    ensure_bankroll_migrated(conn)
    conn.commit()
    assert get_meta(conn, MO) == "1234.0"
    assert get_meta(conn, LEGACY_BANKROLL_KEY) is None


def test_place_migrates_legacy_key_first(conn):
    """落注入口也迁移：M3 老库的首笔 M4 落注按迁移后的轨余额计仓。"""
    set_meta(conn, LEGACY_BANKROLL_KEY, "2000.0")
    conn.commit()
    run = add_run(conn)
    add_rec(conn, run, add_fixture(conn, "ev1", "Chelsea", "Arsenal"), "H", kelly=0.02)
    assert place_paper_bets(conn, run) == 1
    assert bet_by_market(conn, "H")["stake"] == pytest.approx(40.0)   # 0.02 × 2000
    assert get_meta(conn, MO) == "2000.0"
    assert get_meta(conn, LEGACY_BANKROLL_KEY) is None


@pytest.fixture
def conn_two_recs(conn):
    """同 fixture 同市场双轨各一行，final 均为中性 0.01（brief 已验算：两轨各
    kelly=0.01 × bankroll 1000 → stake 10.00）。"""
    run = add_run(conn)
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    add_rec(conn, run, fx, "H", kelly=0.01, final_stake_frac=0.01,
            strategy="model_only")
    add_rec(conn, run, fx, "H", kelly=0.01, final_stake_frac=0.01,
            strategy="model_persona")
    return conn


def test_place_double_track_separate_bankrolls(conn_two_recs):
    placed = place_paper_bets(conn_two_recs, _run_id(conn_two_recs))
    assert placed == 2                                       # 双轨各一注
    stakes = conn_two_recs.execute(
        "SELECT b.stake, r.strategy FROM bets b JOIN recommendations r"
        " ON r.id=b.recommendation_id").fetchall()
    assert all(s["stake"] == 10.0 for s in stakes)           # 各轨 1000 × 0.01
    assert get_meta(conn_two_recs, MO) == "1000.0"
    assert get_meta(conn_two_recs, MP) == "1000.0"           # 各轨独立惰性初始化


def test_place_double_track_scales_on_own_balance_only(conn):
    """A 轨余额不影响 B 轨仓位：persona 轨 3000 × 0.01 = 30，model_only 仍 20。"""
    run = add_run(conn)
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    add_rec(conn, run, fx, "H", kelly=0.02, strategy="model_only")
    add_rec(conn, run, fx, "A", kelly=0.01, strategy="model_persona")
    set_meta(conn, MP, "3000.0")
    conn.commit()
    assert place_paper_bets(conn, run) == 2
    by_market = {b["market"]: b for b in bets(conn)}
    assert by_market["H"]["stake"] == pytest.approx(20.0)     # model_only 轨 1000 × 0.02
    assert by_market["A"]["stake"] == pytest.approx(30.0)     # persona 轨 3000 × 0.01


@pytest.fixture
def conn_veto(conn):
    """persona 轨被 veto（final=0）：只 model_only 该落。"""
    run = add_run(conn)
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    add_rec(conn, run, fx, "H", kelly=0.02, strategy="model_only")
    add_rec(conn, run, fx, "H", kelly=0.02, final_stake_frac=0.0,
            strategy="model_persona")
    return conn


def test_place_skips_veto(conn_veto):
    placed = place_paper_bets(conn_veto, _run_id(conn_veto))
    assert placed == 1                                       # 只落 model_only
    row = conn_veto.execute(
        "SELECT r.strategy FROM bets b JOIN recommendations r"
        " ON r.id=b.recommendation_id").fetchone()
    assert row["strategy"] == "model_only"
    assert get_meta(conn_veto, MP) is None                   # veto 轨未用 → 不初始化


@pytest.fixture
def conn_played(conn):
    """双轨各落一注且已完赛（brief 已验算的数字）：model_only 的 H 注中
    （odds 2.0、stake 10 → 回报 20）、model_persona 的 A 注负（stake 10）。"""
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")      # 2-1 主胜
    add_match(conn, "Chelsea", "Arsenal", "2026-09-02", 2, 1)
    run = add_run(conn)
    add_rec(conn, run, fx, "H", kelly=0.01, best_odds=2.0, strategy="model_only")
    add_rec(conn, run, fx, "A", kelly=0.01, best_odds=2.0, strategy="model_persona")
    assert place_paper_bets(conn, run) == 2
    return conn


def test_settle_writes_per_strategy_bankroll(conn_played):
    out = settle_paper_bets(conn_played)
    assert get_meta(conn_played, MO) == "1010.0"             # won: 1000 + (20−10)
    assert get_meta(conn_played, MP) == "990.0"              # lost: 1000 − 10
    assert out["by_strategy"]["model_only"]["pnl"] == 10.0
    assert out["by_strategy"]["model_persona"]["pnl"] == -10.0
    assert out["pnl"] == 0.0                                 # 顶层合计（daily 简报消费）
    assert out["settled"] == 2 and out["won"] == 1
    assert get_meta(conn_played, LEGACY_BANKROLL_KEY) is None


def test_settle_by_strategy_shape_has_all_tracks(conn_played):
    """分轨形状同顶层四键，且**两轨恒在**（空轨零值）——T14 渲染可无脑遍历。"""
    by = settle_paper_bets(conn_played)["by_strategy"]
    assert set(by) == {"model_only", "model_persona"}
    for track in by.values():
        assert set(track) == {"settled", "won", "pnl", "clv_median"}


def test_settle_unsettled_track_keeps_its_bankroll_untouched(conn):
    """本窗没有可结注的轨不回写——原余额保持可观测（不造 1000 假账）。"""
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")      # 无完赛行 → 配不上
    run = add_run(conn)
    add_rec(conn, run, fx, "H", kelly=0.01, strategy="model_only")
    add_rec(conn, run, fx, "D", kelly=0.01, strategy="model_persona")
    assert place_paper_bets(conn, run) == 2
    out = settle_paper_bets(conn)
    assert out["settled"] == 0 and out["pnl"] == 0.0
    assert get_meta(conn, MO) == "1000.0"
    assert get_meta(conn, MP) == "1000.0"                    # 落注时初始化过 → 原值不动


def test_summary_by_strategy(conn_played):
    settle_paper_bets(conn_played)                   # conn_played 只种到「已落注」
    s = paper_summary(conn_played)
    assert set(s) == {"model_only", "model_persona"}
    assert s["model_only"]["roi"] is not None                # 1 中 1 未结 → (20−10)/10
    assert s["model_persona"]["n"] == 1
    assert s["model_persona"]["roi"] == pytest.approx(-1.0)
    assert s["model_only"]["bankroll"] == pytest.approx(1010.0)
    assert s["model_persona"]["bankroll"] == pytest.approx(990.0)


def test_summary_reads_migrated_legacy_value(conn):
    """summary 入口也迁移：M3 老库第一次 `fa status` 即见旧账（挂到 model_only 轨）。"""
    set_meta(conn, LEGACY_BANKROLL_KEY, "990.5")
    conn.commit()
    s = paper_summary(conn)
    assert s["model_only"]["bankroll"] == pytest.approx(990.5)
    assert get_meta(conn, LEGACY_BANKROLL_KEY) is None


# ---------------------------------------------------------------- T8 遗留补钉


def test_apply_verdict_touches_model_persona_rows_only(conn):
    """T8 遗留补钉（控制器裁定）：判决只写 model_persona 轨——同 fixture 的
    model_only 行（paper 落注源）的 verdict / final_stake_frac 不得被触碰。"""
    fx = add_fixture(conn, "ev1", "Chelsea", "Arsenal")
    run = add_run(conn)
    add_rec(conn, run, fx, "H", kelly=0.02, strategy="model_only")
    add_rec(conn, run, fx, "H", kelly=0.02, strategy="model_persona")
    conn.commit()

    agree = {"verdict": "agree", "confidence_delta": 0.1,
             "key_factors": ["a"], "report_md": "x"}
    assert apply_verdict(conn, fx, agree) == 1
    rows = {r["strategy"]: r for r in conn.execute(
        "SELECT strategy, verdict, confidence_delta, final_stake_frac"
        " FROM recommendations WHERE fixture_id=?", (fx,)).fetchall()}
    assert rows["model_persona"]["verdict"] == "agree"
    assert rows["model_persona"]["final_stake_frac"] == pytest.approx(0.02 * 1.1)
    assert rows["model_only"]["verdict"] is None             # 纯模型轨不被判决波及
    assert rows["model_only"]["final_stake_frac"] is None
