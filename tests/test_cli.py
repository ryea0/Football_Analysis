from pathlib import Path

import pytest
from typer.testing import CliRunner

from fa.cli import app
from fa.db import connect, init_db

runner = CliRunner()

CSV_A = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\nE0,19/08/1995,Arsenal,West Ham,1,1,D\n"


def test_help_exits_zero():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "fa" in result.output


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def _use_tmp_db(tmp_path, monkeypatch) -> Path:
    """FA_DB 指向临时库，测试绝不碰 data/fa.db。"""
    db = tmp_path / "t.db"
    monkeypatch.setenv("FA_DB", str(db))
    init_db(db)
    return db


def test_backtest_help():
    result = runner.invoke(app, ["backtest", "run", "--help"])
    assert result.exit_code == 0
    assert "half-life" in result.output
    assert "--sigma" in result.output


def test_fit_config_sigma_helper():
    """队级先验宽度只落 σ_att/σ_dfn；σ_mu/σ_ha 保持默认（联赛级非瓶颈）。"""
    from fa.cli import _fit_config

    cfg = _fit_config(200.0, 1.5)
    assert (cfg.sigma_att, cfg.sigma_dfn, cfg.sigma_mu, cfg.sigma_ha) \
        == (1.5, 1.5, 0.25, 0.25)
    assert cfg.half_life_days == 200.0


def test_backtest_run_sigma_passthrough(tmp_path, monkeypatch):
    from fa.cli import _fit_config

    _use_tmp_db(tmp_path, monkeypatch)
    seen = {}

    def fake_run_backtest(conn, lgs, seasons, cfg, verbose=False,
                          with_form=False):
        seen["cfg"] = cfg
        return 0                                   # 0 行预测 → CLI 退出码 1

    monkeypatch.setattr("fa.backtest.run.run_backtest", fake_run_backtest)
    result = runner.invoke(app, ["backtest", "run", "--sigma", "0.8"])
    assert result.exit_code == 1, result.output
    assert seen["cfg"] == _fit_config(100.0, 0.8)
    assert seen["cfg"].sigma_att == 0.8 and seen["cfg"].sigma_dfn == 0.8


def test_backtest_run_sigma_default_is_canonical(tmp_path, monkeypatch):
    """不传 --sigma 必须仍是 0.35——钉住主判决（σ=0.35）的复现路径，防默认漂移。"""
    from fa.cli import _fit_config

    _use_tmp_db(tmp_path, monkeypatch)
    seen = {}

    def fake_run_backtest(conn, lgs, seasons, cfg, verbose=False,
                          with_form=False):
        seen["cfg"] = cfg
        seen["form"] = with_form
        return 0

    monkeypatch.setattr("fa.backtest.run.run_backtest", fake_run_backtest)
    result = runner.invoke(app, ["backtest", "run"])
    assert result.exit_code == 1, result.output
    assert seen["form"] is False                     # 默认关闭 §4.5 消融（主判决路径）
    assert seen["cfg"] == _fit_config(100.0, 0.35)
    assert seen["cfg"].sigma_att == 0.35 and seen["cfg"].sigma_dfn == 0.35
    assert seen["cfg"].half_life_days == 100.0


def test_backtest_run_form_flag_passthrough(tmp_path, monkeypatch):
    """--form 直通 run_backtest(with_form=True)（spec §4.5 消融入口）。"""
    _use_tmp_db(tmp_path, monkeypatch)
    seen = {}

    def fake_run_backtest(conn, lgs, seasons, cfg, verbose=False,
                          with_form=False):
        seen["form"] = with_form
        return 0

    monkeypatch.setattr("fa.backtest.run.run_backtest", fake_run_backtest)
    result = runner.invoke(app, ["backtest", "run", "--form"])
    assert result.exit_code == 1, result.output
    assert seen["form"] is True


def test_data_subapp_help():
    result = runner.invoke(app, ["data", "--help"])
    assert result.exit_code == 0
    assert "sync-history" in result.output
    assert "status" in result.output


def test_data_status(tmp_path, monkeypatch):
    db = _use_tmp_db(tmp_path, monkeypatch)
    conn = connect(db)
    conn.execute("INSERT INTO teams (league, name) VALUES ('E0', 'Arsenal')")
    conn.execute(
        "INSERT INTO matches (league, season, date, home_team_id, away_team_id,"
        " fthg, ftag, raw_line) VALUES ('E0', 1995, '1995-08-19', 1, 1, 1, 1, '{}')")
    conn.commit()
    conn.close()

    result = runner.invoke(app, ["data", "status"])
    assert result.exit_code == 0, result.output
    assert "E0" in result.output and "1 个赛季" in result.output
    assert "未知队名（隔离表）：0 条" in result.output


def test_data_sync_history(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    p = tmp_path / "E0_9495.csv"
    p.write_text(CSV_A)

    def fake_download(league, start_year, refresh=False):
        return p if (league, start_year) == ("E0", 1994) else None

    monkeypatch.setattr("fa.data.sync.download_csv", fake_download)

    result = runner.invoke(app, ["data", "sync-history"])
    assert result.exit_code == 0, result.output
    assert "新入库 1 场" in result.output
    assert "无数据" in result.output

    again = runner.invoke(app, ["data", "sync-history"])
    assert again.exit_code == 0, again.output
    assert "新入库 0 场" in again.output
    assert "跳过（内容未变）1 个赛季" in again.output


def test_data_sync_history_refresh_flag(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    p = tmp_path / "E0_9495.csv"
    p.write_text(CSV_A)
    seen = []

    def fake_download(league, start_year, refresh=False):
        seen.append(refresh)
        return p if (league, start_year) == ("E0", 1994) else None

    monkeypatch.setattr("fa.data.sync.download_csv", fake_download)
    result = runner.invoke(app, ["data", "sync-history", "--refresh"])
    assert result.exit_code == 0, result.output
    assert seen and all(seen)          # --refresh 透传到下载层


# ---- 双线 status 与 bet 台账（T11）----------------------------------------

from fa.pipeline.paper import BANKROLL_KEY, place_paper_bets  # noqa: E402


def _seed_prediction(conn, league="E0", season=2025, market=True):
    """A 线预测行（backtest_predictions，UNIQUE(match_id) → 每行自配一场完赛）。

    market=False → mkt_* 写 NULL（去水收盘基准缺失的残缺行，A 线 evaluate 不可评）。
    """
    def team(name):
        row = conn.execute("SELECT id FROM teams WHERE league=? AND name=?",
                           (league, name)).fetchone()
        if row is not None:
            return row["id"]
        return conn.execute("INSERT INTO teams (league, name) VALUES (?, ?)",
                            (league, name)).lastrowid

    home, away = team("Arsenal"), team("West Ham")
    match_id = conn.execute(
        "INSERT INTO matches (league, season, date, home_team_id, away_team_id,"
        " raw_line) VALUES (?, ?, '2025-08-16', ?, ?, '{}')",
        (league, season, home, away)).lastrowid
    mkt = "0.45,0.30,0.25" if market else "NULL,NULL,NULL"
    return conn.execute(
        "INSERT INTO backtest_predictions (league, season, week_index, match_id,"
        " date, p_home, p_draw, p_away, mkt_home, mkt_draw, mkt_away, odds_home,"
        " odds_draw, odds_away, outcome, total_goals)"
        f" VALUES (?,?,1,?,'2025-08-16',0.5,0.3,0.2,{mkt},2.0,3.2,3.8,'H',2)",
        (league, season, match_id)).lastrowid


def _seed_fixture_row(conn, event_key="ev1", league="E0"):
    return conn.execute(
        "INSERT INTO fixtures (league, event_key, source, kickoff_utc, status,"
        " created_at) VALUES (?,?,'oddsapi','2026-09-06T14:00:00Z','scheduled',"
        " '2026-09-03T08:00:00Z')", (league, event_key)).lastrowid


def _seed_run_row(conn, type_="matchday", phase="am", status="ok",
                  credits_after=None, started_at="2026-09-03T09:00:00Z"):
    return conn.execute(
        "INSERT INTO runs (type, phase, started_at, finished_at, status,"
        " credits_before, credits_after) VALUES (?,?,?,NULL,?,NULL,?)",
        (type_, phase, started_at, status, credits_after)).lastrowid


def _seed_rec_row(conn, run_id, fixture_id, market="H", kelly=0.02, best_odds=2.0,
                  bookmaker="pinnacle", final_stake_frac=None):
    return conn.execute(
        "INSERT INTO recommendations (run_id, fixture_id, strategy, market, phase,"
        " model_p, market_p, best_odds, bookmaker, edge, ev, kelly_stake_frac,"
        " final_stake_frac, created_at)"
        " VALUES (?,?,'model_only',?,'am',0.5,0.4,?,?,0.1,0.3,?,?,'2026-09-03T09:00:00Z')",
        (run_id, fixture_id, market, best_odds, bookmaker, kelly,
         final_stake_frac)).lastrowid


def _seed_rec_for_bet(tmp_path, monkeypatch, n=1, market="H"):
    """最小 B 线种子：一个 run + n 条 model_only 推荐，返回 (db 路径, 推荐 id 列表)。"""
    db = _use_tmp_db(tmp_path, monkeypatch)
    conn = connect(db)
    fx = _seed_fixture_row(conn)
    run = _seed_run_row(conn)
    recs = [_seed_rec_row(conn, run, fx, market=market)]
    for i in range(1, n):                       # 同 fixture 换 market（UNIQUE 约束）
        recs.append(_seed_rec_row(conn, run, fx, market=("D" if market == "H" else "H")))
    conn.commit()
    conn.close()
    return db, recs


def _bets(db):
    conn = connect(db)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM bets ORDER BY id")]
    finally:
        conn.close()


def test_status_empty_db_renders_both_sections(tmp_path, monkeypatch):
    db = _use_tmp_db(tmp_path, monkeypatch)
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    assert "A 线·研究评测" in result.output
    assert "B 线·运营模拟（paper）" in result.output
    assert "未运行" in result.output                       # backtest_predictions 空
    assert "未初始化" in result.output                     # bankroll meta 缺
    assert "额度水位" in result.output and "未记录" in result.output
    assert "未对齐队名（隔离表）：0 条" in result.output
    assert "无 run 记录" in result.output
    from fa.db import get_meta
    conn = connect(db)
    try:
        assert get_meta(conn, BANKROLL_KEY) is None        # status 只读，不落 meta
    finally:
        conn.close()


def test_status_a_line_aggregates_backtest_predictions(tmp_path, monkeypatch):
    db = _use_tmp_db(tmp_path, monkeypatch)
    conn = connect(db)
    _seed_prediction(conn)
    _seed_prediction(conn, season=2024)                    # UNIQUE(match_id) → 各配一场
    conn.commit()
    conn.close()

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    assert "n=2" in result.output
    assert "劣化=" in result.output and "判决=" in result.output
    assert "判决=GO" in result.output                      # 模型优于市场 → GO（本次种子）
    assert "未运行" not in result.output


def test_status_b_line_shows_ledger_quota_runs_unknown(tmp_path, monkeypatch):
    db = _use_tmp_db(tmp_path, monkeypatch)
    conn = connect(db)
    fx = _seed_fixture_row(conn)
    run1 = _seed_run_row(conn, credits_after=88)
    _seed_rec_row(conn, run1, fx, "H", kelly=0.02, best_odds=2.0)
    _seed_run_row(conn, type_="daily", phase=None, status="ok",
                  started_at="2026-09-02T09:00:00Z")
    conn.execute("INSERT INTO unknown_names (source, name, first_seen)"
                 " VALUES ('oddsapi', 'M Gladbah', '2026-09-03T09:00:00Z')")
    conn.execute("INSERT INTO meta (key, value) VALUES ('odds_quota_remaining', '90')")
    conn.commit()
    assert place_paper_bets(conn, run1) == 1
    conn.close()

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    assert "注数=1" in result.output and "pending 1" in result.output
    assert "bankroll=1000.00" in result.output             # 落注即惰性初始化
    assert "额度水位：余 90" in result.output
    assert "matchday" in result.output and "daily" in result.output
    assert "88" in result.output                           # run 行回显剩余额度
    assert "未对齐队名（隔离表）：1 条" in result.output


def test_status_limits_runs_to_three(tmp_path, monkeypatch):
    db = _use_tmp_db(tmp_path, monkeypatch)
    conn = connect(db)
    for i in range(5):
        _seed_run_row(conn, started_at=f"2026-09-0{i + 1}T09:00:00Z")
    conn.commit()
    conn.close()
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    assert "#5" in result.output and "#4" in result.output and "#3" in result.output
    assert "#2" not in result.output and "#1" not in result.output   # 只取最近 3 条


def test_bet_help_lists_three_subcommands():
    result = runner.invoke(app, ["bet", "--help"])
    assert result.exit_code == 0, result.output
    for name in ("add", "list", "settle"):
        assert name in result.output


def test_bet_add_defaults_to_paper_with_recommendation_values(tmp_path, monkeypatch):
    db, (rec,) = _seed_rec_for_bet(tmp_path, monkeypatch)
    result = runner.invoke(app, ["bet", "add", str(rec)])
    assert result.exit_code == 0, result.output
    assert "已登记" in result.output
    (row,) = _bets(db)
    assert row["recommendation_id"] == rec
    assert row["mode"] == "paper" and row["status"] == "pending"
    assert row["stake"] == pytest.approx(20.0)             # kelly 0.02 × bankroll 1000
    assert row["odds_taken"] == 2.0 and row["bookmaker"] == "pinnacle"
    assert row["placed_at"] and row["settled_at"] is None and row["return_amt"] is None


def test_bet_add_explicit_stake_and_odds_win(tmp_path, monkeypatch):
    db, (rec,) = _seed_rec_for_bet(tmp_path, monkeypatch)
    result = runner.invoke(app, ["bet", "add", str(rec), "--stake", "5.5",
                                 "--odds", "1.95"])
    assert result.exit_code == 0, result.output
    (row,) = _bets(db)
    assert row["stake"] == pytest.approx(5.5)
    assert row["odds_taken"] == pytest.approx(1.95)


def test_bet_add_live_requires_confirmation_flag(tmp_path, monkeypatch):
    db, (rec,) = _seed_rec_for_bet(tmp_path, monkeypatch)
    result = runner.invoke(app, ["bet", "add", str(rec), "--mode", "live"])
    assert result.exit_code == 1, result.output
    assert "真实下单需显式确认" in result.output
    assert "--i-know-mode-live" in result.output
    assert _bets(db) == []                                 # 拒绝时不留半行

    ok = runner.invoke(app, ["bet", "add", str(rec), "--mode", "live",
                             "--i-know-mode-live"])
    assert ok.exit_code == 0, ok.output
    (row,) = _bets(db)
    assert row["mode"] == "live" and row["status"] == "pending"


def test_bet_add_unknown_recommendation_id(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    result = runner.invoke(app, ["bet", "add", "424242"])
    assert result.exit_code == 1, result.output
    assert "424242" in result.output and "不存在" in result.output


def test_bet_add_duplicate_recommendation_mode_refused(tmp_path, monkeypatch):
    db, (rec,) = _seed_rec_for_bet(tmp_path, monkeypatch)
    assert runner.invoke(app, ["bet", "add", str(rec)]).exit_code == 0
    again = runner.invoke(app, ["bet", "add", str(rec)])
    assert again.exit_code == 1, again.output
    assert len(_bets(db)) == 1                             # UNIQUE(recommendation_id, mode)


def test_bet_add_rejects_bad_mode_and_stake(tmp_path, monkeypatch):
    db, (rec,) = _seed_rec_for_bet(tmp_path, monkeypatch)
    for args in (["--mode", "real"], ["--stake=0"], ["--stake=-1"]):
        result = runner.invoke(app, ["bet", "add", str(rec), *args])
        assert result.exit_code == 1, (args, result.output)
    assert _bets(db) == []


def test_bet_add_refuses_derived_zero_stake(tmp_path, monkeypatch):
    """派生注金与显式 --stake 走同一道 >0 闸：kelly=0 / M4 final_stake_frac=0
    必须拒，不得写成 0 注金行（审查 fix round 1 #1）。"""
    db = _use_tmp_db(tmp_path, monkeypatch)
    conn = connect(db)
    fx = _seed_fixture_row(conn)
    run = _seed_run_row(conn)
    kelly0 = _seed_rec_row(conn, run, fx, market="H", kelly=0.0)
    final0 = _seed_rec_row(conn, run, fx, market="D", kelly=0.02,
                           final_stake_frac=0.0)
    conn.commit()
    conn.close()

    for rec, frac in ((kelly0, "0.0"), (final0, "0.0")):
        result = runner.invoke(app, ["bet", "add", str(rec)])
        assert result.exit_code == 1, (rec, result.output)
        assert "须 > 0" in result.output, result.output
        assert "收到 0.0" in result.output, result.output   # 回显派生结果，操作者可见
        assert "推导" not in result.output.splitlines()[0]  # 推导行不再先于拒绝打印
    assert _bets(db) == []


def test_bet_add_refuses_odds_at_or_below_one(tmp_path, monkeypatch):
    """--odds ≤ 1 拒绝（无利润可言的赔率），0.8 与边界 1.0 都算违例。"""
    db, (rec,) = _seed_rec_for_bet(tmp_path, monkeypatch)
    result = runner.invoke(app, ["bet", "add", str(rec), "--odds", "0.8"])
    assert result.exit_code == 1, result.output
    assert "--odds" in result.output and "0.8" in result.output
    assert _bets(db) == []
    boundary = runner.invoke(app, ["bet", "add", str(rec), "--odds", "1.0"])
    assert boundary.exit_code == 1, boundary.output
    assert _bets(db) == []


def test_bet_list_rejects_bad_status_filter(tmp_path, monkeypatch):
    """--status 词表外 → 中文友好报错并回显合法词表（审查 fix round 1 #2c）。"""
    _use_tmp_db(tmp_path, monkeypatch)
    result = runner.invoke(app, ["bet", "list", "--status", "bogus"])
    assert result.exit_code == 1, result.output
    assert "--status" in result.output
    assert "pending|won|lost|void" in result.output


def test_status_a_line_null_market_probs_degrades_without_traceback(tmp_path, monkeypatch):
    """mkt_* 缺收盘价的残缺行 → 明说「不可评」，exit 0（不 traceback、不给假判决）。"""
    db = _use_tmp_db(tmp_path, monkeypatch)
    conn = connect(db)
    _seed_prediction(conn, market=False)
    conn.commit()
    conn.close()

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.output          # 无 traceback
    assert "不可评" in result.output and "mkt_*" in result.output
    assert "判决=" not in result.output                  # 缺基准 → 不冒充判决
    assert "B 线·运营模拟（paper）" in result.output     # A 线降级不拖垮 B 线渲染


def test_bet_list_round_trip_with_filters(tmp_path, monkeypatch):
    db, recs = _seed_rec_for_bet(tmp_path, monkeypatch, n=2)
    assert runner.invoke(app, ["bet", "add", str(recs[0])]).exit_code == 0
    assert runner.invoke(app, ["bet", "add", str(recs[1]), "--mode", "live",
                               "--i-know-mode-live"]).exit_code == 0

    all_rows = runner.invoke(app, ["bet", "list"])
    assert all_rows.exit_code == 0, all_rows.output
    assert "2 条" in all_rows.output and "paper" in all_rows.output and "live" in all_rows.output

    live = runner.invoke(app, ["bet", "list", "--mode", "live"])
    assert live.exit_code == 0, live.output
    assert "1 条" in live.output and "mode=live" in live.output
    assert "paper" not in live.output                      # 过滤生效：paper 行不出现

    pending = runner.invoke(app, ["bet", "list", "--status", "pending"])
    assert pending.exit_code == 0 and "2 条" in pending.output

    conn = connect(db)
    bet_id = conn.execute("SELECT id FROM bets ORDER BY id LIMIT 1").fetchone()["id"]
    conn.close()
    assert runner.invoke(app, ["bet", "settle", str(bet_id), "--status", "won"]).exit_code == 0
    won = runner.invoke(app, ["bet", "list", "--status", "won"])
    assert won.exit_code == 0 and "1 条" in won.output
    empty = runner.invoke(app, ["bet", "list", "--status", "void"])
    assert empty.exit_code == 0 and "无记录" in empty.output


def test_bet_list_rejects_bad_filter(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    result = runner.invoke(app, ["bet", "list", "--mode", "fantasy"])
    assert result.exit_code == 1, result.output
    assert "--mode" in result.output


def test_bet_settle_won_default_return_and_bankroll_untouched(tmp_path, monkeypatch):
    db, (rec,) = _seed_rec_for_bet(tmp_path, monkeypatch)
    assert runner.invoke(app, ["bet", "add", str(rec)]).exit_code == 0
    (row,) = _bets(db)

    result = runner.invoke(app, ["bet", "settle", str(row["id"]), "--status", "won"])
    assert result.exit_code == 0, result.output
    assert "不改动 bankroll" in result.output              # --help/回显都要说清口径
    (after,) = _bets(db)
    assert after["status"] == "won"
    assert after["return_amt"] == pytest.approx(40.0)      # 20.00 × 2.0
    assert after["settled_at"]

    from fa.db import get_meta
    conn = connect(db)
    try:
        assert get_meta(conn, BANKROLL_KEY) is None        # 手工结算不记账（T7 自动路径才动）
    finally:
        conn.close()


def test_bet_settle_lost_and_void_default_zero_return(tmp_path, monkeypatch):
    db, recs = _seed_rec_for_bet(tmp_path, monkeypatch, n=2)
    assert runner.invoke(app, ["bet", "add", str(recs[0])]).exit_code == 0
    assert runner.invoke(app, ["bet", "add", str(recs[1])]).exit_code == 0
    first, second = _bets(db)
    assert runner.invoke(app, ["bet", "settle", str(first["id"]),
                               "--status", "lost"]).exit_code == 0
    assert runner.invoke(app, ["bet", "settle", str(second["id"]),
                               "--status", "void"]).exit_code == 0
    rows = _bets(db)
    assert [r["status"] for r in rows] == ["lost", "void"]
    assert all(r["return_amt"] == pytest.approx(0.0) for r in rows)


def test_bet_settle_explicit_return_wins(tmp_path, monkeypatch):
    db, (rec,) = _seed_rec_for_bet(tmp_path, monkeypatch)
    assert runner.invoke(app, ["bet", "add", str(rec), "--stake", "10"]).exit_code == 0
    (row,) = _bets(db)
    result = runner.invoke(app, ["bet", "settle", str(row["id"]), "--status", "won",
                                 "--return", "12.5"])
    assert result.exit_code == 0, result.output
    (after,) = _bets(db)
    assert after["return_amt"] == pytest.approx(12.5)      # 人工指定优先于 stake×odds


def test_bet_settle_unknown_bet_and_bad_status(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    missing = runner.invoke(app, ["bet", "settle", "77", "--status", "won"])
    assert missing.exit_code == 1, missing.output
    assert "77" in missing.output and "不存在" in missing.output

    bad = runner.invoke(app, ["bet", "settle", "77", "--status", "pushed"])
    assert bad.exit_code == 1, bad.output
    assert "--status" in bad.output and "won|lost|void" in bad.output


def test_bet_settle_refuses_double_settlement(tmp_path, monkeypatch):
    db, (rec,) = _seed_rec_for_bet(tmp_path, monkeypatch)
    assert runner.invoke(app, ["bet", "add", str(rec)]).exit_code == 0
    (row,) = _bets(db)
    assert runner.invoke(app, ["bet", "settle", str(row["id"]),
                               "--status", "won"]).exit_code == 0
    again = runner.invoke(app, ["bet", "settle", str(row["id"]), "--status", "lost"])
    assert again.exit_code == 1, again.output
    assert "已结算" in again.output
    (after,) = _bets(db)
    assert after["status"] == "won"                        # 不被第二次改写


def test_bet_help_documents_live_flag_and_bankroll_rule(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    add_help = runner.invoke(app, ["bet", "add", "--help"])
    assert add_help.exit_code == 0, add_help.output
    assert "--i-know-mode-live" in add_help.output

    settle_help = runner.invoke(app, ["bet", "settle", "--help"])
    assert settle_help.exit_code == 0, settle_help.output
    assert "bankroll" in settle_help.output                # 手工结算不记账，写在帮助里
