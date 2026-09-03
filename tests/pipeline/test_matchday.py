"""比赛日 run 编排测试（T9，spec §3.4 / §7.1 / §9.5 / §9.6）。

全离线，三层替身：
- ``fetch_odds`` 在 ``fa.pipeline.fixtures`` 命名空间被替换为回放（同 T5 测试），
  ``urlopen`` 打成炸弹——实现若绕过该命名空间直连真函数会立即炸出而非静默触网；
  ``box.regions`` 顺带记下每次实际发到线上的 region 集（额度节流 §3.4 的断言面）；
- 渲染 / 推送缝（``matchday.render_*`` / ``matchday.send`` / ``matchday.last_error``）
  换成记录器：T8（``fa.report.*``）尚未合流，本文件不依赖它，只钉「渲染 → 推送 →
  降级标注」的编排契约；接缝自身的降级 / 透传另有一节直接测；
- 时间：``matchday._now`` 与 ``value._now`` 钉**同一**固定时刻——窗口判定与拟合
  ``asof`` 同源，种子日期全部相对该时刻回推（比赛年龄恒定 → 拟合逐位可复现）。

合成库沿用 test_value 的种子模式（8 队 × 8 周 × 主客双向 = 64 行 ≥ MIN_TRAIN_ROWS），
盘口按 ``o_i = 1/(q_i·S)`` 反推（δ 沿用 test_value 已验算的那组：H 与 O2.5 过门槛、
D 被 edge 剔除、A 落带外）——测试内用**与实现同一条拟合链**反推价格，门槛可达性
不靠运气（反推价先断言在赔率带内，否则后续断言失真）。
"""
import json
import types
import urllib.request
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from fa.backtest.run import global_targets
from fa.db import connect, get_meta, init_db, set_meta
from fa.model.fit import FitConfig, fit_league, training_rows
from fa.model.predict import (expected_goals, fit_rho, outcome_probs,
                              over25_probs, score_matrix)
from fa.pipeline import fixtures as fixtures_mod
from fa.pipeline import matchday, reporting, runs, value
from fa.pipeline.odds_api import OddsApiError, OddsSnapshot

LEAGUE = "E0"
HOME, AWAY = "T1", "T6"
_NOW = datetime(2026, 9, 3, 9, 0, 0, tzinfo=timezone.utc)
ASOF = _NOW.date().isoformat()
KO = "2026-09-04T05:00:00Z"          # = _NOW + 20h → 52h 窗口内
KO_FAR = "2026-12-01T19:30:00Z"      # 远超窗口 → 不算「当日赛事」
QUOTA = 480
QUOTA_FLOOR = 100
S = 1.02                             # booksum（2% overround，同 test_value）
H2H_DELTAS = {"H": 0.06, "D": -0.02, "A": -0.02}
TOTALS_DELTA = 0.05


def _boom(*args, **kwargs):
    raise AssertionError("比赛日 run 不得绕过 fetch_odds 触网")


def _model_probs(c, home=HOME, away=AWAY):
    """与实现同一条拟合链（M2 公共入口）——反推盘口用的模型概率。"""
    cfg = FitConfig()
    train = training_rows(c, LEAGUE, ASOF, cfg.window_days)
    assert len(train) >= value.MIN_TRAIN_ROWS, len(train)
    mu_g, ha_g = global_targets(c, ASOF, cfg)
    fit = fit_league(train, ASOF, LEAGUE, mu_g, ha_g, cfg)
    rho = fit_rho(train, ASOF, cfg, lambda h, a: expected_goals(fit, h, a))
    matrix = score_matrix(*expected_goals(fit, home, away), rho)
    p_h, p_d, p_a = outcome_probs(matrix)
    p_over, _p_under = over25_probs(matrix)
    return {"H": p_h, "D": p_d, "A": p_a, "O2.5": p_over}


def _prices(c):
    """o_i = 1/(q_i·S)，q_i = p_i − δ_i；H / O2.5 须在赔率带内（前置条件）。"""
    p = _model_probs(c)
    h2h = {m: 1.0 / ((p[m] - H2H_DELTAS[m]) * S) for m in ("H", "D", "A")}
    qo = p["O2.5"] - TOTALS_DELTA
    totals = {"over": 1.0 / (qo * S), "under": 1.0 / ((1.0 - qo) * S)}
    assert 1.4 <= h2h["H"] <= 6.0, h2h
    assert 1.4 <= totals["over"] <= 6.0, totals
    return h2h, totals


def _snap(market, outcomes, kickoff=KO, bookmaker="pinnacle"):
    return OddsSnapshot(LEAGUE, "ev1", kickoff, HOME, AWAY, market, "eu",
                        bookmaker, outcomes)


def _seed_history(c):
    """8 队 × 8 周 × 主客双向 = 64 行（全部 < asof，防泄漏）。"""
    c.executemany("INSERT INTO teams (league, name) VALUES (?, ?)",
                  [(LEAGUE, f"T{i}") for i in range(8)])
    ids = {r["name"]: r["id"] for r in c.execute("SELECT id, name FROM teams")}
    rows = []
    for k in range(8):
        day = (_NOW.date() - timedelta(weeks=8 - k)).isoformat()
        for j in range(4):
            h, a = f"T{j}", f"T{7 - j}"
            rows.append((LEAGUE, 2026, day, ids[h], ids[a], 2,
                         1 if j == 1 else 0))
            rows.append((LEAGUE, 2026, day, ids[a], ids[h], 1, 1))
    c.executemany(
        "INSERT INTO matches (league, season, date, home_team_id, away_team_id,"
        " fthg, ftag, raw_line) VALUES (?,?,?,?,?,?,?,'{}')", rows)


def run_row(c, run_id):
    return dict(c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())


def summary_of(c, run_id):
    return json.loads(run_row(c, run_id)["summary"])


def bets_of(c):
    return [dict(r) for r in c.execute(
        "SELECT b.*, r.market AS market FROM bets b"
        " JOIN recommendations r ON r.id = b.recommendation_id")]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """离线环境：时间钉死 + 三个缝换记录器 + fetch_odds 回放（box.conn 是库连接）。"""
    monkeypatch.setenv("ODDS_API_KEY", "test-key")
    monkeypatch.setattr(matchday, "_now", lambda: _NOW)
    monkeypatch.setattr(value, "_now", lambda: _NOW)
    monkeypatch.setattr(runs, "_now", lambda: _NOW)   # started_at/finished_at 同源
    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    _seed_history(c)
    c.commit()

    box = SimpleNamespace(
        conn=c, fetch=[], regions=[], events=[], pushed=[], render=[], update=[],
        replay={"h2h": True, "totals": False, "quota": QUOTA,
                "kickoff": KO, "fail": False, "fail_events": False,
                "events": [{"id": "ev-probe", "commence_time": KO,
                            "home_team": HOME, "away_team": AWAY}],
                "events_quota": QUOTA})
    prices: dict = {}

    def fake_fetch_odds(league, *args, **kwargs):
        box.fetch.append(league)
        # 实际发到线上的 region 集（额度节流断言用）：未传＝sync_fixtures 的默认双区
        box.regions.append(kwargs.get("regions", fixtures_mod.DEFAULT_REGIONS))
        if box.replay["fail"]:
            raise OddsApiError("Odds API 请求失败: HTTP 429（额度耗尽或限流）")
        if not prices:
            prices["h2h"], prices["totals"] = _prices(c)
        snaps = []
        if box.replay["h2h"]:
            h2h = prices["h2h"]
            snaps.append(_snap("h2h", {"home": h2h["H"], "draw": h2h["D"],
                                       "away": h2h["A"]},
                               kickoff=box.replay["kickoff"]))
        if box.replay["totals"]:
            snaps.append(_snap("totals", prices["totals"],
                               kickoff=box.replay["kickoff"]))
        return snaps, box.replay["quota"]

    def fake_list_events(league, *args, **kwargs):
        box.events.append(league)
        if box.replay["fail_events"]:
            raise OddsApiError("Odds API 请求失败: HTTP 429（额度耗尽或限流）")
        return box.replay["events"], box.replay["events_quota"]

    monkeypatch.setattr(fixtures_mod, "fetch_odds", fake_fetch_odds)
    monkeypatch.setattr(matchday, "list_events", fake_list_events)
    monkeypatch.setattr(matchday, "send",
                        lambda text: (box.pushed.append(text), True)[1])
    monkeypatch.setattr(matchday, "last_error", lambda: None)
    monkeypatch.setattr(
        matchday, "render_matchday_report",
        lambda conn, run_id, phase, summary, quota_left, degraded:
        (box.render.append({"run_id": run_id, "phase": phase,
                            "summary": summary, "quota_left": quota_left,
                            "degraded": degraded}),
         f"报告 run={run_id} phase={phase}")[1])
    monkeypatch.setattr(
        matchday, "render_pm_update",
        lambda conn, am_run_id, pm_run_id, quota_left, degraded:
        (box.update.append({"am_run_id": am_run_id, "pm_run_id": pm_run_id,
                            "quota_left": quota_left, "degraded": degraded}),
         f"更新版 am={am_run_id} pm={pm_run_id}")[1])
    yield box
    c.close()


# ---------------------------------------------------------------- runs 记录


def test_begin_run_records_running_row_and_credits_before(env):
    c = env.conn
    set_meta(c, "odds_quota_remaining", "321")
    c.commit()
    run_id = matchday.begin_run(c, "matchday", "am")
    row = run_row(c, run_id)
    assert row["type"] == "matchday" and row["phase"] == "am"
    assert row["status"] == "running"           # 占位：崩溃也留痕（§9.5）
    assert row["started_at"] and row["finished_at"] is None
    assert row["credits_before"] == 321         # 进 run 前的水位（spec §3.4）
    assert row["credits_after"] is None


def test_begin_run_daily_has_no_phase_and_no_credits(env):
    """daily 不拉实时盘：phase 为空、额度水位不填（无关字段不制造噪音）。"""
    row = run_row(env.conn, matchday.begin_run(env.conn, "daily", None))
    assert row["phase"] is None and row["credits_before"] is None
    assert row["type"] == "daily"


def test_finish_run_writes_status_summary_and_credits(env):
    c = env.conn
    run_id = matchday.begin_run(c, "matchday", "pm")
    matchday.finish_run(c, run_id, "degraded_ok",
                        {"recs": 2, "unknown": ["X"]}, credits_after=9)
    row = run_row(c, run_id)
    assert row["status"] == "degraded_ok"
    assert row["finished_at"] == row["finished_at"]          # 已落
    assert json.loads(row["summary"]) == {"recs": 2, "unknown": ["X"]}
    assert row["credits_after"] == 9


def test_finish_run_without_summary_keeps_null(env):
    c = env.conn
    run_id = matchday.begin_run(c, "daily", None)
    matchday.finish_run(c, run_id, "ok", None)
    assert run_row(c, run_id)["summary"] is None


# ---------------------------------------------------------------- am 全链


def test_am_full_chain(env):
    """am 全链：同步 → 推荐 → 落注 → run 收尾 → 渲染推送，每一环都留痕。"""
    c = env.conn
    out = matchday.run_matchday(c, "am", [LEAGUE])

    assert out["status"] == "ok" and out["phase"] == "am"
    assert out["fixtures"] == 1 and out["recs"] == 1 and out["bets"] == 1
    assert out["quota_left"] == QUOTA
    assert out["degraded"] is False and out["sent"] is True
    assert out["am_run_id"] is None
    assert env.fetch == [LEAGUE]

    rec = dict(c.execute("SELECT * FROM recommendations WHERE market='H'").fetchone())
    assert rec["run_id"] == out["run_id"] and rec["phase"] == "am"
    assert rec["strategy"] == "model_only"

    bets = bets_of(c)
    assert len(bets) == 1
    assert bets[0]["market"] == "H" and bets[0]["mode"] == "paper"
    assert bets[0]["status"] == "pending" and bets[0]["recommendation_id"] == rec["id"]

    row = run_row(c, out["run_id"])
    assert row["type"] == "matchday" and row["phase"] == "am"
    assert row["status"] == "ok"
    assert row["started_at"] and row["finished_at"]
    assert row["credits_before"] is None            # 进 run 前 meta 无水位
    assert row["credits_after"] == QUOTA

    summary = summary_of(c, out["run_id"])
    assert summary["train_n"] >= 64                 # T8 报告消费的样本量键
    assert summary["half_life"] == FitConfig().half_life_days
    assert summary["fixtures"] == 1 and summary["recs"] == 1 and summary["bets"] == 1
    assert summary["unknown"] == []
    assert summary["telegram"] == {"sent": True, "error": None}
    assert summary["report"] == "matchday"
    assert get_meta(c, "odds_quota_remaining") == str(QUOTA)

    assert env.pushed == [f"报告 run={out['run_id']} phase=am"]
    assert len(env.render) == 1 and env.update == []
    assert env.render[0]["degraded"] is False
    assert env.render[0]["quota_left"] == QUOTA
    assert env.render[0]["run_id"] == out["run_id"]


def test_running_row_is_visible_before_finish(env, monkeypatch):
    """begin_run 先落 status='running'：run 中途崩溃也留「已启动」痕迹（§9.5）。"""
    c = env.conn
    seen = []
    real = matchday.begin_run

    def spy(conn_, type_, phase=None):
        rid = real(conn_, type_, phase)
        seen.append(run_row(c, rid))
        return rid

    monkeypatch.setattr(matchday, "begin_run", spy)
    out = matchday.run_matchday(c, "am", [LEAGUE])
    assert seen[0]["status"] == "running" and seen[0]["finished_at"] is None
    assert run_row(c, out["run_id"])["status"] == "ok"      # 终态覆盖占位


def test_am_no_window_fixture_skips_without_push(env):
    """库内无窗口 fixture 且 /events 探测也无 → 空跑收尾，**零拉盘**（§9.6）。"""
    c = env.conn
    env.replay["kickoff"] = KO_FAR
    env.replay["events"] = []                        # 探测也证实无赛事
    out = matchday.run_matchday(c, "am", [LEAGUE])

    assert out["status"] == "skipped" and out["sent"] is None
    assert out["recs"] == 0 and out["bets"] == 0
    assert env.pushed == [] and env.render == [] and env.update == []
    assert env.events == [LEAGUE]                    # 免费探测发生了
    assert env.fetch == []                           # 计费拉盘一次都没发生
    assert c.execute("SELECT COUNT(*) c FROM recommendations").fetchone()["c"] == 0
    assert c.execute("SELECT COUNT(*) c FROM bets").fetchone()["c"] == 0
    row = run_row(c, out["run_id"])
    assert row["status"] == "skipped" and row["finished_at"]
    summary = summary_of(c, out["run_id"])
    assert "当日" in summary["skip_reason"]
    assert summary["probe"] == "events"
    assert row["credits_after"] == QUOTA             # 探测读到的额度头照记账


def _seed_fixture(c, event_key="ev-seed", kickoff=KO):
    """直接落一行**已对齐**且在窗口内的 fixture（探测前置路径的「库内已知赛事」）。"""
    ids = {r["name"]: r["id"] for r in c.execute("SELECT id, name FROM teams")}
    return c.execute(
        "INSERT INTO fixtures (league, event_key, source, kickoff_utc,"
        " home_team_id, away_team_id, status, created_at)"
        " VALUES (?,?, 'oddsapi', ?, ?, ?, 'scheduled', '2026-09-01T08:00:00Z')",
        (LEAGUE, event_key, kickoff, ids[HOME], ids[AWAY])).lastrowid


def test_persisted_window_fixture_skips_probe_and_syncs(env):
    """库内已有窗口内 fixture：既不必探测也不必判空，直接常规拉盘刷新价格。"""
    c = env.conn
    _seed_fixture(c)
    out = matchday.run_matchday(c, "am", [LEAGUE])

    assert env.events == []                          # 探测被跳过（零额外请求）
    assert env.fetch == [LEAGUE]
    assert out["status"] == "ok" and out["recs"] == 1 and out["bets"] == 1
    assert "probe" not in summary_of(c, out["run_id"])


def test_probe_finding_event_triggers_full_sync(env):
    """库内空但探测到窗口内事件 → 走常规全流程（拉盘刷新价格 + 新增 fixture）。"""
    c = env.conn
    env.replay["events"] = [{"id": "ev-x", "commence_time": KO,
                             "home_team": HOME, "away_team": AWAY}]
    out = matchday.run_matchday(c, "am", [LEAGUE])

    assert env.events == [LEAGUE]
    assert env.fetch == [LEAGUE]                     # 有赛事 → 照常计费拉盘
    assert out["status"] == "ok" and out["recs"] == 1 and out["bets"] == 1
    assert out["fixtures"] == 1
    assert summary_of(c, out["run_id"])["probe"] == "found"


def test_probe_failure_falls_through_to_sync(env):
    """探测自身失败（429/网络）＝「无法确认当日赛程」，交回常规流程不静默漏跑。"""
    c = env.conn
    env.replay["fail_events"] = True
    out = matchday.run_matchday(c, "am", [LEAGUE])

    assert env.events == [LEAGUE]
    assert env.fetch == [LEAGUE]                     # 宁可贵一次，也不空跑漏掉比赛日
    assert out["status"] == "ok" and out["degraded"] is False   # 数据无损，不算降级
    summary = summary_of(c, out["run_id"])
    assert summary["probe"] == "unavailable"
    assert summary["degraded_reasons"] == []


def test_probe_quota_header_is_recorded_even_when_free(env):
    """探测若真带回额度头：写 meta 并作 credits_after——E2E 据水位差验证「免费」。"""
    c = env.conn
    env.replay["events_quota"] = 77
    env.replay["kickoff"] = KO_FAR
    env.replay["events"] = []                        # 探测证实无赛事 → 空跑
    out = matchday.run_matchday(c, "am", [LEAGUE])

    assert out["status"] == "skipped"
    assert get_meta(c, "odds_quota_remaining") == "77"
    assert run_row(c, out["run_id"])["credits_after"] == 77


def test_probe_quota_carries_through_full_run_as_evidence(env):
    """探测到赛事也把额度头存档：meta 先落探测值，summary 存档不被 sync 覆盖。"""
    c = env.conn
    env.replay["events_quota"] = 77                   # 探测读到的头
    out = matchday.run_matchday(c, "am", [LEAGUE])

    assert out["status"] == "ok"
    summary = summary_of(c, out["run_id"])
    assert summary["probe"] == "found"
    assert summary["probe_quota"] == 77               # 证据存档（sync 会覆盖 meta）
    assert out["quota_left"] == QUOTA                 # credits_after 取拉盘后的真值
    assert get_meta(c, "odds_quota_remaining") == str(QUOTA)


def test_empty_leagues_skips_without_probe(env):
    """leagues 为空：没发探测，probe='none'（非 'events'），仍空跑收尾。"""
    c = env.conn
    out = matchday.run_matchday(c, "am", [])

    assert out["status"] == "skipped"
    assert env.events == [] and env.fetch == []
    assert summary_of(c, out["run_id"])["probe"] == "none"


def test_unparseable_kickoff_in_fixtures_fails_open(env):
    """库内 fixture 的 kickoff 解析不了：无法确认窗口为空 → 走常规流程，不空跑。"""
    c = env.conn
    _seed_fixture(c, kickoff="not-a-timestamp")
    out = matchday.run_matchday(c, "am", [LEAGUE])

    assert env.events == []                           # 窗口判定 fail-open，未判空
    assert env.fetch == [LEAGUE]                      # 照常拉盘
    assert out["status"] == "ok"


def test_unparseable_commence_time_in_probe_fails_open(env):
    """探测响应里有解析不了的时间：无法确认 → 走常规流程，绝不据此空跑。"""
    c = env.conn
    env.replay["events"] = [{"id": "ev-bad", "commence_time": "???"}]
    out = matchday.run_matchday(c, "am", [LEAGUE])

    assert env.events == [LEAGUE]                     # 探测发生了
    assert env.fetch == [LEAGUE]                      # 但没据此空跑
    assert out["status"] == "ok"
    assert summary_of(c, out["run_id"])["probe"] == "unavailable"


def test_exception_mid_run_rolls_back_stage_writes(env, monkeypatch):
    """中途上抛：先回滚半写的阶段产物，'failed' 的 run 行不带残局。"""
    c = env.conn

    def boom(conn_, run_id):
        # 模拟「半写」：阶段产物已插但未提交，随后上抛
        c.execute(
            "INSERT INTO fixtures (league, event_key, source, kickoff_utc,"
            " home_team_id, away_team_id, status, created_at)"
            " VALUES ('E0','half-written','oddsapi','2026-09-04T05:00:00Z',"
            " NULL, NULL, 'scheduled', '2026-09-03T09:00:00Z')")
        raise RuntimeError("落注炸了")

    monkeypatch.setattr(matchday, "place_paper_bets", boom)
    with pytest.raises(RuntimeError):
        matchday.run_matchday(c, "am", [LEAGUE])

    reader = c
    # 半写（未提交）被回滚——'failed' 的 run 行不带残局
    assert reader.execute("SELECT COUNT(*) c FROM fixtures"
                          " WHERE event_key='half-written'").fetchone()["c"] == 0
    # 已提交的阶段产物不受回滚影响（sync/推荐各自 commit 过）
    assert reader.execute("SELECT COUNT(*) c FROM fixtures").fetchone()["c"] == 1
    assert reader.execute(
        "SELECT COUNT(*) c FROM recommendations").fetchone()["c"] == 1
    row = reader.execute(
        "SELECT status, summary FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "failed" and "落注炸了" in row["summary"]


def test_no_key_returns_before_any_fetch(env, monkeypatch):
    """无 key 早退：不拉盘、不落 fixture、不推送，runs 记 status='no_key'。"""
    c = env.conn
    monkeypatch.delenv("ODDS_API_KEY")
    env.replay["fail"] = True                        # 实现若触网 → 立即炸出
    out = matchday.run_matchday(c, "am", [LEAGUE])

    assert out["status"] == "no_key"
    assert out["sent"] is None and out["recs"] == 0 and out["fixtures"] == 0
    assert env.fetch == [] and env.pushed == []
    assert c.execute("SELECT COUNT(*) c FROM fixtures").fetchone()["c"] == 0
    row = run_row(c, out["run_id"])
    assert row["status"] == "no_key" and row["type"] == "matchday"
    assert row["finished_at"] is not None


def test_am_quota_below_floor_is_degraded_but_still_syncs(env):
    """am 低于水位：照常拉盘（比赛日本身就是拉盘窗），报告与 run 都标注降级。"""
    c = env.conn
    set_meta(c, "odds_quota_remaining", str(QUOTA_FLOOR - 1))
    c.commit()
    out = matchday.run_matchday(c, "am", [LEAGUE])

    assert env.fetch == [LEAGUE]
    assert out["status"] == "degraded_ok"
    assert out["degraded"] is True
    assert env.render[0]["degraded"] is True
    assert summary_of(c, out["run_id"])["degraded_reasons"]
    assert run_row(c, out["run_id"])["credits_before"] == QUOTA_FLOOR - 1


# ---------------------------------------------------------------- 额度节流（合并 region）


def test_am_quota_above_merge_floor_keeps_both_regions(env):
    """quota ≥ QUOTA_MERGE_FLOOR：双区全扫（默认），无合并标注。"""
    c = env.conn
    set_meta(c, "odds_quota_remaining", "250")
    c.commit()
    out = matchday.run_matchday(c, "am", [LEAGUE])

    assert env.fetch == [LEAGUE]
    assert env.regions == [fixtures_mod.DEFAULT_REGIONS]     # 双区全扫
    summary = summary_of(c, out["run_id"])
    assert "region_merged" not in summary
    assert summary["degraded_reasons"] == []
    assert out["status"] == "ok" and out["degraded"] is False


def test_am_quota_at_merge_floor_is_exclusive_threshold(env):
    """quota 恰为 QUOTA_MERGE_FLOOR：不收窄（判据是严格小于，与 250 同档）。"""
    c = env.conn
    set_meta(c, "odds_quota_remaining", str(matchday.QUOTA_MERGE_FLOOR))
    c.commit()
    out = matchday.run_matchday(c, "am", [LEAGUE])

    assert env.regions == [fixtures_mod.DEFAULT_REGIONS]
    assert "region_merged" not in summary_of(c, out["run_id"])
    assert out["status"] == "ok"


def test_am_quota_below_merge_floor_scans_eu_only(env):
    """quota < QUOTA_MERGE_FLOOR：照常拉盘但收窄到单 eu，summary 记 region_merged。"""
    c = env.conn
    set_meta(c, "odds_quota_remaining", "150")
    c.commit()
    out = matchday.run_matchday(c, "am", [LEAGUE])

    assert env.fetch == [LEAGUE]                     # 缩范围，不跳拉盘
    assert env.regions == [matchday.MERGE_REGIONS]
    summary = summary_of(c, out["run_id"])
    assert summary["region_merged"] is True
    assert "snapshot_reused" not in summary          # 本窗确实拉了实时盘
    assert any("合并 region" in r for r in summary["degraded_reasons"])
    assert out["status"] == "degraded_ok" and out["degraded"] is True
    assert out["region_merged"] is True and out["snapshot_reused"] is False
    assert env.render[0]["degraded"] is True
    assert run_row(c, out["run_id"])["credits_before"] == 150


def test_pm_quota_between_floors_pulls_single_region_instead_of_skipping(env):
    """梯子不越档：QUOTA_FLOOR ≤ quota < QUOTA_MERGE_FLOOR 的 pm **照常拉盘**。

    合并 region 只缩范围——「跳拉盘复用快照」专属 <QUOTA_FLOOR，判据独立，
    不得因理由串进了 degraded_reasons 而被武装（否则 150 也白白丢一窗实时盘）。
    """
    c = env.conn
    am = matchday.run_matchday(c, "am", [LEAGUE])
    env.fetch.clear()
    env.regions.clear()
    env.pushed.clear()
    env.render.clear()
    env.update.clear()
    set_meta(c, "odds_quota_remaining", str(matchday.QUOTA_MERGE_FLOOR - 50))
    c.commit()

    out = matchday.run_matchday(c, "pm", [LEAGUE])

    assert env.fetch == [LEAGUE]                     # 未跳拉盘
    assert env.regions == [matchday.MERGE_REGIONS]   # 只收窄到单 eu
    summary = summary_of(c, out["run_id"])
    assert summary["region_merged"] is True
    assert not any("跳过拉盘" in r for r in summary["degraded_reasons"])
    assert out["am_run_id"] == am["run_id"]
    assert env.update[0]["am_run_id"] == am["run_id"]


def test_below_quota_floor_pm_skips_entirely_and_never_merges(env):
    """quota < QUOTA_FLOOR 的 pm：整次不拉盘（最严档），region_merged 不出现。"""
    c = env.conn
    assert matchday.run_matchday(c, "am", [LEAGUE])["status"] == "ok"
    env.fetch.clear()
    env.regions.clear()
    set_meta(c, "odds_quota_remaining", str(QUOTA_FLOOR - 1))
    c.commit()

    out = matchday.run_matchday(c, "pm", [LEAGUE])

    assert env.fetch == [] and env.regions == []     # 第三档：一次盘都不拉
    summary = summary_of(c, out["run_id"])
    assert "region_merged" not in summary
    assert summary["snapshot_reused"] is True        # 本窗确实没有实时盘
    assert any("快照" in r for r in summary["degraded_reasons"])
    assert out["status"] == "degraded_ok"
    assert out["snapshot_reused"] is True and out["region_merged"] is False


def test_am_below_quota_floor_stacks_both_lever_rungs(env):
    """am 在最严档：拉盘照发（比赛日是拉盘窗）且单 eu——两档理由并存。"""
    c = env.conn
    set_meta(c, "odds_quota_remaining", str(QUOTA_FLOOR - 1))
    c.commit()
    out = matchday.run_matchday(c, "am", [LEAGUE])

    assert env.fetch == [LEAGUE]
    assert env.regions == [matchday.MERGE_REGIONS]
    summary = summary_of(c, out["run_id"])
    assert summary["region_merged"] is True
    joined = " | ".join(summary["degraded_reasons"])
    assert "仅标注水位" in joined and "合并 region" in joined


def test_no_quota_watermark_defaults_to_dual_region(env):
    """meta 从未记过水位（quota=None）：无从判档 → 默认双区，不节流。"""
    c = env.conn
    out = matchday.run_matchday(c, "am", [LEAGUE])

    assert env.regions == [fixtures_mod.DEFAULT_REGIONS]
    assert "region_merged" not in summary_of(c, out["run_id"])
    assert "snapshot_reused" not in summary_of(c, out["run_id"])
    assert out["status"] == "ok"
    assert out["region_merged"] is False and out["snapshot_reused"] is False


def test_skipped_run_carries_degraded_reasons_not_just_flag(env):
    """空跑也可能带降级（pm 低水位 + 无赛事）：summary 必须连理由一起落。

    只落 ``degraded=True`` 不落因，事后无法解释这行为何标降。
    """
    c = env.conn
    env.replay["kickoff"] = KO_FAR                   # 库内 fixture 全在窗外
    env.replay["events"] = []                        # 探测也证实无赛事
    set_meta(c, "odds_quota_remaining", str(QUOTA_FLOOR - 1))
    c.commit()

    out = matchday.run_matchday(c, "pm", [LEAGUE])

    assert out["status"] == "skipped" and out["degraded"] is True
    summary = summary_of(c, out["run_id"])
    assert summary["degraded"] is True and summary["degraded_reasons"]
    assert any("跳过拉盘" in r for r in summary["degraded_reasons"])
    assert "snapshot_reused" not in summary          # 空跑是「未拉盘」，不是「复用」


def test_clean_skip_records_empty_degraded_reasons(env):
    """真·零额度空跑（无降级）：``degraded_reasons`` 仍在（空列表），键形稳定。"""
    c = env.conn
    env.replay["kickoff"] = KO_FAR
    env.replay["events"] = []

    out = matchday.run_matchday(c, "am", [LEAGUE])

    assert out["status"] == "skipped" and out["degraded"] is False
    assert summary_of(c, out["run_id"])["degraded_reasons"] == []


# ---------------------------------------------------------------- pm 两窗


def test_pm_degraded_reuses_am_snapshots_and_skips_fetch(env):
    """pm 低于水位：跳过拉盘复用 am 快照，报告走 pm 更新版，旧注不重下。"""
    c = env.conn
    am = matchday.run_matchday(c, "am", [LEAGUE])
    assert am["status"] == "ok"
    env.fetch.clear()
    env.pushed.clear()
    env.render.clear()
    env.update.clear()
    set_meta(c, "odds_quota_remaining", "80")
    c.commit()

    out = matchday.run_matchday(c, "pm", [LEAGUE])

    assert env.fetch == []                           # 降级：一次盘都不拉
    assert out["status"] == "degraded_ok" and out["degraded"] is True
    assert out["fixtures"] == 0 and out["bets"] == 0
    assert out["am_run_id"] == am["run_id"]
    assert len(bets_of(c)) == 1                      # 同场同市场不重下（T7 去重）
    assert env.update == [{"am_run_id": am["run_id"], "pm_run_id": out["run_id"],
                           "quota_left": 80, "degraded": True}]
    assert env.render == []
    summary = summary_of(c, out["run_id"])
    assert summary["am_run_id"] == am["run_id"]
    assert any("快照" in r for r in summary["degraded_reasons"])


def test_pm_full_chain_diffs_against_am_and_places_new_market(env):
    """pm 正常全链：拉盘 → 更新版报告（对照当日最近 am run）→ 新市场落注。"""
    c = env.conn
    am = matchday.run_matchday(c, "am", [LEAGUE])
    assert am["status"] == "ok"
    env.replay["totals"] = True                      # pm 才出现的市场
    env.fetch.clear()
    env.regions.clear()
    env.pushed.clear()
    env.render.clear()
    env.update.clear()

    out = matchday.run_matchday(c, "pm", [LEAGUE])

    assert env.fetch == [LEAGUE]
    assert env.regions == [fixtures_mod.DEFAULT_REGIONS]   # 正常档 pm＝双区全扫
    assert out["status"] == "ok" and out["degraded"] is False
    assert out["region_merged"] is False and out["snapshot_reused"] is False
    assert out["am_run_id"] == am["run_id"]
    assert out["bets"] == 1                          # 只落新增市场
    assert {b["market"] for b in bets_of(c)} == {"H", "O2.5"}
    assert env.update == [{"am_run_id": am["run_id"], "pm_run_id": out["run_id"],
                           "quota_left": QUOTA, "degraded": False}]
    assert env.render == []


def test_pm_without_am_run_today_falls_back_to_full_report(env):
    """当日无 am run：pm 没有对照基准 → 回退全量报告（phase='pm'），不炸。"""
    c = env.conn
    out = matchday.run_matchday(c, "pm", [LEAGUE])

    assert out["status"] == "ok" and out["am_run_id"] is None
    assert len(env.render) == 1 and env.update == []
    assert env.render[0]["phase"] == "pm"
    assert env.pushed == [f"报告 run={out['run_id']} phase=pm"]


def test_am_run_id_lookup_scopes_to_today_and_success_status(env):
    """am_run_id 只认**当日**、终态 ok/degraded_ok 的 am run；昨日 / 空跑不算。"""
    c = env.conn
    c.execute(
        "INSERT INTO runs (type, phase, started_at, finished_at, status)"
        " VALUES ('matchday','am','2026-09-02T03:00:00Z',"
        " '2026-09-02T03:01:00Z','ok')")                        # 昨日
    c.execute(
        "INSERT INTO runs (type, phase, started_at, finished_at, status)"
        " VALUES ('matchday','am','2026-09-03T03:00:00Z',"
        " '2026-09-03T03:01:00Z','skipped')")                   # 当日但空跑
    c.commit()
    am = matchday.run_matchday(c, "am", [LEAGUE])
    env.replay["totals"] = True
    out = matchday.run_matchday(c, "pm", [LEAGUE])
    assert out["am_run_id"] == am["run_id"]


def test_same_phase_rerun_refreshes_run_id_attribution(env):
    """UNIQUE(fixture,market,strategy,phase) 刷新 run_id——归因=最后刷新者；
    落注去重在 (fixture,market,strategy,mode) 级，同相重跑不产生第二注。"""
    c = env.conn
    first = matchday.run_matchday(c, "am", [LEAGUE])
    env.fetch.clear()
    second = matchday.run_matchday(c, "am", [LEAGUE])

    assert second["status"] == "ok" and second["recs"] == 1
    rows = [dict(r) for r in c.execute(
        "SELECT id, run_id FROM recommendations WHERE phase='am'")]
    assert len(rows) == 1
    assert rows[0]["run_id"] == second["run_id"] != first["run_id"]
    bets = bets_of(c)
    assert len(bets) == 1
    assert bets[0]["recommendation_id"] == rows[0]["id"]


# ---------------------------------------------------------------- 降级与失败


def test_sync_failure_degrades_to_existing_snapshots(env):
    """拉盘失败（§9.5「数据源失败 → 用最近缓存」）：继续出推荐并标注降级。"""
    c = env.conn
    assert matchday.run_matchday(c, "am", [LEAGUE])["status"] == "ok"
    env.fetch.clear()
    env.replay["fail"] = True

    out = matchday.run_matchday(c, "pm", [LEAGUE])

    assert out["status"] == "degraded_ok"
    assert out["degraded"] is True and out["fixtures"] == 0
    assert out["recs"] == 1                          # 旧快照照常出推荐
    assert out["snapshot_reused"] is True            # 拉盘失败＝本窗无实时盘
    assert any("Odds API" in r
               for r in summary_of(c, out["run_id"])["degraded_reasons"])


def test_partial_failure_quota_header_reaches_meta(env, monkeypatch):
    """部分失败的额度头不丢（终审 Important #2）：sync 即写 meta，降级 run 提交入库。

    league 内 eu 已见 483、uk 才 429 → fetch_odds 把已见最小值挂在
    ``OddsApiError.quota_remaining``，sync 的 except 路径并入并即写 meta；
    matchday 吞掉 OddsApiError 走降级，``finish_run`` 的恰好一次 commit 让水印
    落库。若丢失，meta 停在上一 run 的 480，下一 run 的降频判据（QUOTA_FLOOR）
    就读着虚高水位做决定（spec §3.4）。
    """
    c = env.conn
    assert matchday.run_matchday(c, "am", [LEAGUE])["status"] == "ok"
    assert get_meta(c, "odds_quota_remaining") == str(QUOTA)

    def flaky(league, *args, **kwargs):
        raise OddsApiError("Odds API 请求失败: HTTP 429（额度耗尽或限流）", 483)

    monkeypatch.setattr(fixtures_mod, "fetch_odds", flaky)
    out = matchday.run_matchday(c, "am", [LEAGUE])

    assert out["status"] == "degraded_ok"            # 复用快照路径不中断
    assert get_meta(c, "odds_quota_remaining") == "483"   # min(480, 已见 483) 已入库


def test_exception_mid_run_records_failed_and_reraises(env, monkeypatch):
    """管线中途上抛：run 记 status='failed'（含原因）后原样外抛，不吞错。"""
    c = env.conn

    def boom(conn_, run_id):
        raise RuntimeError("落注炸了")

    monkeypatch.setattr(matchday, "place_paper_bets", boom)
    with pytest.raises(RuntimeError):
        matchday.run_matchday(c, "am", [LEAGUE])
    row = c.execute(
        "SELECT status, summary FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "failed"
    assert "落注炸了" in row["summary"]
    assert env.pushed == []                          # 未走到渲染推送


def test_tg_failure_does_not_lose_recs_or_bets(env, monkeypatch):
    """推送失败（§9.5 降级不中断）：推荐 / 落注已落库，原因进 summary，状态不加罪。"""
    c = env.conn
    monkeypatch.setattr(matchday, "send", lambda text: False)
    monkeypatch.setattr(matchday, "last_error", lambda: "exit 1: send failed")

    out = matchday.run_matchday(c, "am", [LEAGUE])

    assert out["status"] == "ok" and out["sent"] is False
    assert out["recs"] == 1 and out["bets"] == 1
    assert c.execute(
        "SELECT COUNT(*) c FROM recommendations").fetchone()["c"] == 1
    assert len(bets_of(c)) == 1
    assert summary_of(c, out["run_id"])["telegram"] == {
        "sent": False, "error": "exit 1: send failed"}
    assert run_row(c, out["run_id"])["status"] == "ok"
    assert len(env.render) == 1                      # 渲染照常发生（正文已备好）


def test_invalid_phase_is_rejected(env):
    with pytest.raises(ValueError):
        matchday.run_matchday(env.conn, "noon", [LEAGUE])
    assert env.conn.execute(
        "SELECT COUNT(*) c FROM runs").fetchone()["c"] == 0


# ---------------------------------------------------------------- 报告接缝


def test_reporting_binds_real_t8_modules_after_merge():
    """T8 已合流：接缝降级分支已摘除——门面直引真实现，不再有 _load_* 兜底。"""
    from fa.report import render as render_mod, telegram as telegram_mod

    assert reporting._render is render_mod
    assert reporting._telegram is telegram_mod
    assert not hasattr(reporting, "_load_render")       # 惰性缝已消失
    assert not hasattr(reporting, "_load_telegram")
    assert not hasattr(reporting, "_UNMERGED")          # 占位串已消失
    # 公共调用面仍是那三个渲染函数 + send/last_error（matchday/daily/cli 依赖）
    assert callable(reporting.render_matchday_report)
    assert callable(reporting.render_pm_update)
    assert callable(reporting.render_settlement_brief)
    assert callable(reporting.send) and callable(reporting.last_error)


def test_reporting_render_passes_arguments_in_t8_order(monkeypatch):
    """合流后按 T8 签名透传——参数顺序错了报告会**静默**错位（合流才暴露）。"""
    seen = {}

    def fake_render(conn, run_id, phase, summary, quota_left, degraded):
        seen.update(conn=conn, run_id=run_id, phase=phase, summary=summary,
                    quota_left=quota_left, degraded=degraded)
        return "ok"

    monkeypatch.setattr(reporting, "_render",
                        types.SimpleNamespace(render_matchday_report=fake_render))
    conn, summary = object(), {"train_n": 3}
    assert reporting.render_matchday_report(
        conn, 7, "pm", summary, 12, True) == "ok"
    assert seen == {"conn": conn, "run_id": 7, "phase": "pm",
                    "summary": summary, "quota_left": 12, "degraded": True}

    def fake_update(conn, am_run_id, pm_run_id, quota_left, degraded):
        seen["args"] = (conn, am_run_id, pm_run_id, quota_left, degraded)
        return "upd"

    monkeypatch.setattr(reporting, "_render",
                        types.SimpleNamespace(render_pm_update=fake_update))
    assert reporting.render_pm_update("c", 1, 2, 3, False) == "upd"
    assert seen["args"] == ("c", 1, 2, 3, False)

    def fake_brief(settle):
        seen["settle"] = settle
        return "brief"

    monkeypatch.setattr(reporting, "_render",
                        types.SimpleNamespace(render_settlement_brief=fake_brief))
    assert reporting.render_settlement_brief({"settled": 2}) == "brief"
    assert seen["settle"] == {"settled": 2}


def test_reporting_send_and_last_error_delegate_to_t8(monkeypatch):
    """合流后：send 透传返回值，last_error 读 LAST_TELEGRAM_ERROR。"""
    tg = types.SimpleNamespace(send_telegram=lambda text: text == "good",
                               LAST_TELEGRAM_ERROR=None)
    monkeypatch.setattr(reporting, "_telegram", tg)
    assert reporting.send("good") is True
    assert reporting.send("bad") is False
    tg.LAST_TELEGRAM_ERROR = "exit 1: boom"
    assert reporting.last_error() == "exit 1: boom"


# ---------------------------------------------------------------- CLI 面


def test_cli_skipped_and_degraded_prints_empty_run_reason(tmp_path, monkeypatch):
    """空跑 + 降级：只说「未拉盘」，不出现「复用最近快照」的误导文案。"""
    from typer.testing import CliRunner
    from fa.cli import app

    db = tmp_path / "t.db"
    monkeypatch.setenv("FA_DB", str(db))
    monkeypatch.setenv("ODDS_API_KEY", "test-key")
    init_db(db)
    # 低于水位：pm 才会走「跳过拉盘」→ 空跑且 degraded=True，命中新增文案分支
    c = connect(db)
    try:
        set_meta(c, "odds_quota_remaining", str(QUOTA_FLOOR - 1))
        c.commit()
    finally:
        c.close()
    monkeypatch.setattr(fixtures_mod, "fetch_odds",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("触网")))
    monkeypatch.setattr(matchday, "list_events", lambda *a, **k: ([], None))
    result = CliRunner().invoke(
        app, ["run", "matchday", "--phase", "pm", "--leagues", LEAGUE])

    assert result.exit_code == 0, result.output
    assert "skipped（空跑）" in result.output         # 前置：确实走的是空跑
    assert "本次空跑未拉盘" in result.output          # 新分支的文案
    assert "复用最近快照" not in result.output        # 旧措辞不得出现
    conn = connect(db)
    try:
        row = conn.execute(
            "SELECT status, summary FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        assert row["status"] == "skipped"
        summary = json.loads(row["summary"])
        assert summary["degraded"] is True            # 确实进了降级分支
        assert summary["probe"] == "events"
    finally:
        conn.close()


def test_cli_matchday_no_key_exits_zero(tmp_path, monkeypatch):
    """无 key：优雅输出 + exit 0（T12 冒烟口径），runs 记 'no_key'。"""
    import fa.config as config

    from typer.testing import CliRunner
    from fa.cli import app

    db = tmp_path / "t.db"
    monkeypatch.setenv("FA_DB", str(db))
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    # CLI 启动会读 project_root/.env（spec §9.4）——指到 tmp_path，否则真机上
    # 仓库根恰好有 .env 时会把 key 重新装回环境，无 key 分支永远测不到
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    init_db(db)
    monkeypatch.setattr(fixtures_mod, "fetch_odds",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("触网")))

    result = CliRunner().invoke(app, ["run", "matchday", "--phase", "am"])

    assert result.exit_code == 0, result.output
    assert "ODDS_API_KEY" in result.output
    conn = connect(db)
    try:
        assert conn.execute(
            "SELECT status FROM runs").fetchone()["status"] == "no_key"
    finally:
        conn.close()


def test_cli_matchday_reports_counts_and_push(tmp_path, monkeypatch):
    """中文输出：行数 / 判决位 / 推送结果。"""
    from typer.testing import CliRunner
    from fa.cli import app

    db = tmp_path / "t.db"
    monkeypatch.setenv("FA_DB", str(db))
    monkeypatch.setenv("ODDS_API_KEY", "test-key")
    monkeypatch.setattr(matchday, "_now", lambda: _NOW)
    monkeypatch.setattr(value, "_now", lambda: _NOW)
    monkeypatch.setattr(runs, "_now", lambda: _NOW)   # started_at/finished_at 同源
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    init_db(db)
    c = connect(db)
    try:
        _seed_history(c)
        c.commit()
        prices = _prices(c)

        def fake_fetch(league, *a, **k):
            if league != LEAGUE:
                return [], QUOTA
            h2h = prices[0]
            return [_snap("h2h", {"home": h2h["H"], "draw": h2h["D"],
                                  "away": h2h["A"]})], QUOTA

        monkeypatch.setattr(fixtures_mod, "fetch_odds", fake_fetch)
        monkeypatch.setattr(matchday, "list_events",
                            lambda league, *a, **k: ([{"id": "ev-probe",
                                                       "commence_time": KO}],
                                                     QUOTA))
        monkeypatch.setattr(matchday, "send", lambda text: True)
        monkeypatch.setattr(
            matchday, "render_matchday_report",
            lambda conn, run_id, phase, summary, ql, dg: f"报告 run={run_id}")

        result = CliRunner().invoke(app, ["run", "matchday", "--phase", "am"])

        assert result.exit_code == 0, result.output
        assert "am" in result.output and "ok" in result.output
        assert "推荐 1 条" in result.output and "落注 1 注" in result.output
        assert "推送" in result.output
    finally:
        c.close()


def test_cli_degraded_wording_matches_actual_fetch_state(tmp_path, monkeypatch):
    """CLI 降级文案三态真实化：合并档拉的是实时盘，不得说「复用最近快照」。

    quota=150 的 am：实时盘照拉（单 eu）——旧文案「非实时盘（复用最近快照）」
    会向用户谎报价格新鲜度；渲染层同款三态已由 test_render 钉住。
    """
    from typer.testing import CliRunner
    from fa.cli import app

    db = tmp_path / "t.db"
    monkeypatch.setenv("FA_DB", str(db))
    monkeypatch.setenv("ODDS_API_KEY", "test-key")
    monkeypatch.setattr(matchday, "_now", lambda: _NOW)
    monkeypatch.setattr(value, "_now", lambda: _NOW)
    monkeypatch.setattr(runs, "_now", lambda: _NOW)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    init_db(db)
    c = connect(db)
    try:
        _seed_history(c)
        set_meta(c, "odds_quota_remaining",
                 str(matchday.QUOTA_MERGE_FLOOR - 50))     # 150：合并档
        c.commit()
        prices = _prices(c)

        def fake_fetch(league, *a, **k):
            assert k.get("regions") == matchday.MERGE_REGIONS   # 确实只拉 eu
            h2h = prices[0]
            return [_snap("h2h", {"home": h2h["H"], "draw": h2h["D"],
                                  "away": h2h["A"]})], 150

        monkeypatch.setattr(fixtures_mod, "fetch_odds", fake_fetch)
        monkeypatch.setattr(matchday, "list_events",
                            lambda league, *a, **k: ([{"id": "ev-probe",
                                                       "commence_time": KO}],
                                                     150))
        monkeypatch.setattr(matchday, "send", lambda text: True)
        monkeypatch.setattr(
            matchday, "render_matchday_report",
            lambda conn, run_id, phase, summary, ql, dg: f"报告 run={run_id}")

        result = CliRunner().invoke(
            app, ["run", "matchday", "--phase", "am", "--leagues", LEAGUE])

        assert result.exit_code == 0, result.output
        assert "degraded_ok" in result.output
        assert "复用最近快照" not in result.output        # 旧文案不得再现
        assert "收窄到单 eu" in result.output             # 新文案：实时盘，只是缩范围
    finally:
        c.close()
