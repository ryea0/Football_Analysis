"""价值层推荐生成测试（T6，spec §5.2/§5.3 + §3.2 recommendations）。

**不触网**：``urlopen`` 打成炸弹；盘口直接种 ``odds_snapshots`` 行（T5 已落库），
价值层只读库。

时间注入缝（**唯一**）：``fa.pipeline.value._now``。52h 窗口与拟合 ``asof``
都从这一个缝取时间，monkeypatch 它即同时钉住两者（派单要求择一注入缝）。

合成库沿用 M2 ``tests/backtest/test_run.py`` 的种子模式（8 队、每周 4 对、主客
**双向**各赛一场，避免纯主场种子把 home_adv 推到极端），目标 fixture 取
``T1`` vs ``T6``——实测 pH≈0.666 / pD≈0.226 / pA≈0.109 / pO2.5≈0.492。

盘口构造的闭式关系（先推导、后下断言，避免「调出来像就对」）：
取 ``q_i = p_i − δ_i`` 为市场隐含真概率、``S`` 为 booksum、``T = Σq``，令
``o_i = 1/(q_i·S)``，则比例去水 ``market_p_i = q_i / T``，且

    edge_i = p_i − q_i/T = (δ_i − p_i·Σδ) / (1 − Σδ)
    EV_i   = p_i·(o_i−1) − (1−p_i) = p_i/(q_i·S) − 1        （与 T 无关）

推论：EV>0 须 ``δ_i > p_i·(q_i·S − 1)/1`` 量级的模型优势——S>1 时若三处 δ 全 0
则 EV 恒负；**正 EV 只能来自模型与市场的真分歧**，这正是 §5.2 的语义。
δ 取负（市场比模型更看好某结果）会同时抬高另外两处的 market_p，故 h2h 的
δ_H 须足够大才能把 edge 顶过 2%。种子取
``H2H_DELTAS = {H:+0.06, D:−0.02, A:−0.02}`` → edge_H≈0.048、EV_H≈0.078、
odds_H≈1.62（带内 14% 余量）、edge_D≈−0.025、edge_A≈−0.023（两处被 edge 剔除，
隔离出「只有 H 出推荐」的单一因子）。
"""
import json
import math
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from fa.backtest.run import global_targets
from fa.db import connect, init_db
from fa.model.fit import FitConfig, fit_league, training_rows
from fa.model.predict import (expected_goals, fit_rho, outcome_probs,
                              over25_probs, score_matrix)
from fa.pipeline import value
from fa.pipeline.value import generate_recommendations
from fa.value.devig import devig_ou, devig_proportional
from fa.value.gates import EDGE_MIN, EV_MIN, ev_of, kelly_fraction

LEAGUE = "E0"
HOME, AWAY = "T1", "T6"
# _NOW 取**真实时钟 + 3 天**（而非写死日历日）：历史种子全部相对 _NOW 回推，测试
# 因此完全密闭（比赛年龄恒定 → 拟合逐位可复现），且 ``asof`` 必须来自注入缝——
# 若实现改用 date.today()，asof 比 _NOW 早 3 天、训练权重整体位移，model_p 的
# 逐位断言即断（突变验证见报告）。
_NOW = (datetime.now(timezone.utc).replace(hour=9, minute=0, second=0,
                                          microsecond=0) + timedelta(days=3))
ASOF = _NOW.date().isoformat()
CFG = FitConfig(window_days=400)
S = 1.02                 # booksum：2% overround（最优价水位量级）
H2H_DELTAS = {"H": 0.06, "D": -0.02, "A": -0.02}
TOTALS_DELTA = 0.05
EARLIER = "2026-09-03T08:30:00Z"
LATER = "2026-09-03T09:30:00Z"


def _boom(*args, **kwargs):
    raise AssertionError("价值层不得触网")


@pytest.fixture
def clock(monkeypatch):
    """唯一时间注入缝 + 离线守卫。"""
    monkeypatch.setattr(value, "_now", lambda: _NOW)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)


@pytest.fixture
def conn(tmp_path, clock):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    c.executemany("INSERT INTO teams (league, name) VALUES ('E0', ?)",
                  [(f"T{i}",) for i in range(8)])
    ids = {r["name"]: r["id"] for r in c.execute("SELECT id, name FROM teams")}
    rows = []
    asof_date = _NOW.date()
    for k in range(8):                                  # 8 周 × 4 对 × 主客双向 = 64 行
        day = (asof_date - timedelta(weeks=7 - k)).isoformat()   # 全在 asof 之前（防泄漏）
        for j in range(4):
            h, a = f"T{j}", f"T{7 - j}"
            rows.append(("E0", 2026, day, ids[h], ids[a], 2, 1 if j == 1 else 0))
            rows.append(("E0", 2026, day, ids[a], ids[h], 1, 1))
    c.executemany(
        "INSERT INTO matches (league, season, date, home_team_id, away_team_id,"
        " fthg, ftag, raw_line) VALUES (?,?,?,?,?,?,?,'{}')", rows)
    c.commit()
    yield c
    c.close()


# ---------------------------------------------------------------- 种子工具


def model_probs(c, league, home, away, asof, cfg=CFG):
    """与实现同一条拟合链（M2 公共入口），返回各 market 的模型概率。"""
    train = training_rows(c, league, asof, cfg.window_days)
    mu_g, ha_g = global_targets(c, asof, cfg)
    fit = fit_league(train, asof, league, mu_g, ha_g, cfg)
    rho = fit_rho(train, asof, cfg, lambda h, a: expected_goals(fit, h, a))
    lh, la = expected_goals(fit, home, away)
    m = score_matrix(lh, la, rho)
    ph, pd, pa = outcome_probs(m)
    po, _pu = over25_probs(m)
    return {"H": ph, "D": pd, "A": pa, "O2.5": po}


def team_id(c, name):
    return c.execute("SELECT id FROM teams WHERE name=?", (name,)).fetchone()["id"]


def add_fixture(c, event_key, home_name, away_name, kickoff, league=LEAGUE):
    c.execute(
        "INSERT INTO fixtures (league, event_key, source, kickoff_utc, home_team_id,"
        " away_team_id, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (league, event_key, "oddsapi", kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
         None if home_name is None else team_id(c, home_name),
         None if away_name is None else team_id(c, away_name),
         "scheduled", "2026-09-03T08:00:00Z"))
    return c.execute("SELECT id FROM fixtures WHERE event_key=?",
                     (event_key,)).fetchone()["id"]


def _insert_snap(c, fixture_id, market, outcomes, fetched_at, bookmaker):
    event_key = c.execute("SELECT event_key FROM fixtures WHERE id=?",
                          (fixture_id,)).fetchone()["event_key"]
    c.execute(
        "INSERT INTO odds_snapshots (fetched_at, fixture_id, event_key, market,"
        " region, bookmaker, outcomes, raw) VALUES (?,?,?,?,?,?,?,?)",
        (fetched_at, fixture_id, event_key, market, "eu", bookmaker,
         json.dumps(outcomes), "{}"))
    return outcomes


def seed_h2h(c, fixture_id, probs, deltas, fetched_at=EARLIER, bookmaker="pinnacle"):
    """按 o_i = 1/(q_i·S) 反推 h2h 盘口（q_i = p_i − δ_i）。"""
    q = {m: probs[m] - deltas.get(m, 0.0) for m in ("H", "D", "A")}
    return _insert_snap(c, fixture_id, "h2h",
                        {"home": 1.0 / (q["H"] * S), "draw": 1.0 / (q["D"] * S),
                         "away": 1.0 / (q["A"] * S)}, fetched_at, bookmaker)


def seed_totals(c, fixture_id, p_over, delta, fetched_at=EARLIER, bookmaker="pinnacle"):
    """totals 的 q_over + q_under ≡ 1（构造即 T=1）→ market_p_over = q_over 精确。"""
    qo = p_over - delta
    return _insert_snap(c, fixture_id, "totals",
                        {"over": 1.0 / (qo * S), "under": 1.0 / ((1.0 - qo) * S)},
                        fetched_at, bookmaker)


def add_run(c, phase="am"):
    return c.execute(
        "INSERT INTO runs (type, phase, started_at, status)"
        " VALUES ('matchday', ?, '2026-09-03T09:00:00Z', 'ok')",
        (phase,)).lastrowid


def recs(c, fixture_id, market=None, strategy="model_only"):
    """取该 fixture 的推荐行。同刻三落后每个 (fixture, market, phase) 有三轨行——
    既有断言锚定的是 model_only 轨（M3 语义），故默认过滤到该轨；跨轨/全轨
    断言显式传 ``strategy=None``（空集断言一律走 None，保持「一行都没有」强度）。"""
    q = "SELECT * FROM recommendations WHERE fixture_id=?"
    args = [fixture_id]
    if market:
        q += " AND market=?"
        args.append(market)
    if strategy:
        q += " AND strategy=?"
        args.append(strategy)
    return [dict(r) for r in c.execute(q + " ORDER BY market, strategy", args)]


def best_of(c, fixture_id, market):
    """与实现同口径的最优价（跨快照逐 outcome 取 max）。"""
    best = {}
    for r in c.execute("SELECT outcomes FROM odds_snapshots WHERE fixture_id=? AND market=?",
                       (fixture_id, market)):
        for k, v in json.loads(r["outcomes"]).items():
            if k not in best or v > best[k]:
                best[k] = v
    return best


@pytest.fixture
def priced(conn):
    """五个 fixture 共用同一组盘口：只有「窗口 / 对齐」这一个因子在变。"""
    probs = model_probs(conn, LEAGUE, HOME, AWAY, ASOF)
    fx = {
        "in": add_fixture(conn, "ev-in", HOME, AWAY, _NOW + timedelta(hours=20)),
        "at0": add_fixture(conn, "ev-0", HOME, AWAY, _NOW),          # 下端点：恰为 now
        "at52": add_fixture(conn, "ev-52", HOME, AWAY, _NOW + timedelta(hours=52)),
        "at53": add_fixture(conn, "ev-53", HOME, AWAY, _NOW + timedelta(hours=53)),
        "past": add_fixture(conn, "ev-past", HOME, AWAY, _NOW - timedelta(hours=1)),
        "unaligned": add_fixture(conn, "ev-null", HOME, None,
                                 _NOW + timedelta(hours=20)),
    }
    for fid in fx.values():
        o_h2h = seed_h2h(conn, fid, probs, H2H_DELTAS)
        o_t = seed_totals(conn, fid, probs["O2.5"], TOTALS_DELTA)
        # 前置条件（门槛可达性）：反推的最优价须落带内，否则后续断言失真
        assert 1.4 <= o_h2h["home"] <= 6.0, o_h2h
        assert 1.4 <= o_t["over"] <= 6.0, o_t
        # 前置条件：H 过双门槛、D/A 被 edge 剔除（单一因子隔离）
        mk = devig_proportional([o_h2h["home"], o_h2h["draw"], o_h2h["away"]])
        assert probs["H"] - mk[0] > EDGE_MIN, (probs["H"], mk)
        assert probs["D"] - mk[1] < 0 and probs["A"] - mk[2] < 0, mk
        assert probs["O2.5"] - devig_ou(o_t["over"], o_t["under"])[0] > EDGE_MIN
    conn.commit()
    return conn, fx, probs


# ---------------------------------------------------------------- 主路径


def test_in_gate_writes_h_and_o25_recommendations(priced):
    c, fx, probs = priced
    run = add_run(c)
    ids = generate_recommendations(c, [LEAGUE], "am", run)
    mine = recs(c, fx["in"])
    assert {r["market"] for r in mine} == {"H", "O2.5"}     # D/A 被 edge 剔除
    assert {r["id"] for r in mine} <= set(ids)
    assert all(r["strategy"] == "model_only" for r in mine)
    assert all(r["phase"] == "am" for r in mine)
    assert all(r["run_id"] == run for r in mine)
    assert all(r["created_at"] for r in mine)
    assert len(ids) == 18                            # 3 场 × 2 market × 3 轨（同刻三落）


def test_model_p_matches_library_training_data(priced):
    """model_p 必须来自与 M2 相同的拟合链（逐位相等），而非另一套近似。"""
    c, fx, probs = priced
    generate_recommendations(c, [LEAGUE], "am", add_run(c))
    by_market = {r["market"]: r for r in recs(c, fx["in"])}
    assert by_market["H"]["model_p"] == probs["H"]
    assert by_market["O2.5"]["model_p"] == probs["O2.5"]


def test_market_p_is_devig_of_best_prices(priced):
    c, fx, probs = priced
    generate_recommendations(c, [LEAGUE], "am", add_run(c))
    h = recs(c, fx["in"], "H")[0]
    o = best_of(c, fx["in"], "h2h")
    assert h["market_p"] == devig_proportional([o["home"], o["draw"], o["away"]])[0]
    assert h["best_odds"] == o["home"]
    assert h["bookmaker"] == "pinnacle"
    t = recs(c, fx["in"], "O2.5")[0]
    ot = best_of(c, fx["in"], "totals")
    assert t["market_p"] == devig_ou(ot["over"], ot["under"])[0]
    assert t["best_odds"] == ot["over"]
    assert t["bookmaker"] == "pinnacle"


def test_edge_ev_kelly_columns_are_consistent(priced):
    """列间自洽 + 闭式关系 EV = p/(q·S) − 1（推导见模块 docstring）。"""
    c, fx, probs = priced
    generate_recommendations(c, [LEAGUE], "am", add_run(c))
    h = recs(c, fx["in"], "H")[0]
    assert h["edge"] == probs["H"] - h["market_p"]
    assert h["ev"] == ev_of(probs["H"], h["best_odds"])
    q_h = probs["H"] - H2H_DELTAS["H"]
    assert h["ev"] == pytest.approx(probs["H"] / (q_h * S) - 1, abs=1e-12)
    assert 0.0 < h["kelly_stake_frac"] <= 0.02
    assert h["ev"] > EV_MIN and h["edge"] > EDGE_MIN


def test_kelly_stake_is_quarter_kelly_capped(priced):
    """两条 market 分别踩到 ¼ Kelly 的**两段**：H 触上限、O2.5 在 ¼ 段内。"""
    c, fx, probs = priced
    generate_recommendations(c, [LEAGUE], "am", add_run(c))
    h = recs(c, fx["in"], "H")[0]
    t = recs(c, fx["in"], "O2.5")[0]
    # H：p≈0.67、o≈1.62 → f_raw = 0.25·(1.079−1)/0.62 ≈ 0.032 > cap → 截到 2%
    assert h["kelly_stake_frac"] == kelly_fraction(probs["H"], h["best_odds"])
    assert h["kelly_stake_frac"] == 0.02
    # O2.5：p≈0.49、o≈2.22 → f_raw = 0.25·(1.092−1)/1.22 ≈ 0.0189 < cap → ¼ 段生效
    assert t["kelly_stake_frac"] == kelly_fraction(probs["O2.5"], t["best_odds"])
    assert 0.0 < t["kelly_stake_frac"] < 0.02, t["kelly_stake_frac"]
    # ¼ 系数承重（非恒等上限）：满 Kelly 会超出 cap，¼ 后落在段内
    assert kelly_fraction(probs["O2.5"], t["best_odds"]) * 4 > 0.02


def test_market_p_uses_each_outcome_own_best_price(conn):
    """跨 bookmaker **逐 outcome** 取最大价再去水（不是任一单 book 的行价）。"""
    probs = model_probs(conn, LEAGUE, HOME, AWAY, ASOF)
    fid = add_fixture(conn, "ev-xbook", HOME, AWAY, _NOW + timedelta(hours=20))
    # δ 越大 → q 越小 → 报价越高（odds = 1/(q·S)）：δ_H 大者持 home 最优价、
    # δ_D 大者持 draw 最优价。bf 的 δ_H 最大、pin 的 δ_D 最大 → 两家各占一 outcome。
    d_bf = {"H": 0.12, "D": -0.02, "A": -0.06}
    d_pin = {"H": 0.08, "D": 0.01, "A": -0.06}
    o_bf = seed_h2h(conn, fid, probs, d_bf, bookmaker="bf")
    o_pin = seed_h2h(conn, fid, probs, d_pin, bookmaker="pin")
    assert o_bf["home"] > o_pin["home"] and o_pin["draw"] > o_bf["draw"]
    conn.commit()
    generate_recommendations(conn, [LEAGUE], "am", add_run(conn))
    h = recs(conn, fid, "H")
    assert [r["market"] for r in h] == ["H"]
    triple = [max(o_bf["home"], o_pin["home"]),
              max(o_bf["draw"], o_pin["draw"]),
              max(o_bf["away"], o_pin["away"])]
    assert h[0]["best_odds"] == triple[0]                   # home 的价来自 bf
    assert h[0]["bookmaker"] == "bf"
    assert h[0]["market_p"] == devig_proportional(triple)[0]  # draw 份额用了 pin 的价


def test_out_of_gate_no_recommendation(priced):
    """市场比模型更看好 home（δ_H=−0.05）→ edge 与 EV 双双不过 → 不产推荐。"""
    c, fx, probs = priced
    fid = add_fixture(c, "ev-tight", HOME, AWAY, _NOW + timedelta(hours=20))
    deltas = {"H": -0.05, "D": -0.02, "A": -0.02}
    o = seed_h2h(c, fid, probs, deltas)                     # 盘口在，非缺价
    c.commit()
    mk = devig_proportional([o["home"], o["draw"], o["away"]])
    edge = probs["H"] - mk[0]
    assert edge < EDGE_MIN                                  # 阳性对照：确实被 edge 关卡剔除
    assert ev_of(probs["H"], o["home"]) < EV_MIN            # EV 同样不过
    generate_recommendations(c, [LEAGUE], "am", add_run(c))
    assert recs(c, fid) == []
    assert recs(c, fid, strategy=None) == []                # 三轨都没有


def test_edge_gate_inclusive_at_exact_boundary(priced, monkeypatch):
    """把 EDGE_MIN monkeypatch 到**逐位等于**该边界的值，钉死 >=（含端点）。"""
    c, fx, probs = priced
    o = best_of(c, fx["in"], "h2h")
    edge = probs["H"] - devig_proportional([o["home"], o["draw"], o["away"]])[0]
    monkeypatch.setattr(value, "EDGE_MIN", edge)
    generate_recommendations(c, [LEAGUE], "am", add_run(c))
    assert [r["market"] for r in recs(c, fx["in"], "H")] == ["H"]
    monkeypatch.setattr(value, "EDGE_MIN", math.nextafter(edge, math.inf))
    c.execute("DELETE FROM recommendations")
    generate_recommendations(c, [LEAGUE], "am", add_run(c))
    assert recs(c, fx["in"], "H", strategy=None) == []      # 阈值高 1 ulp 即剔除（三轨皆无）


def test_ev_gate_inclusive_at_exact_boundary(priced, monkeypatch):
    c, fx, probs = priced
    o = best_of(c, fx["in"], "h2h")
    ev = ev_of(probs["H"], o["home"])
    monkeypatch.setattr(value, "EV_MIN", ev)
    generate_recommendations(c, [LEAGUE], "am", add_run(c))
    assert [r["market"] for r in recs(c, fx["in"], "H")] == ["H"]
    monkeypatch.setattr(value, "EV_MIN", math.nextafter(ev, math.inf))
    c.execute("DELETE FROM recommendations")
    generate_recommendations(c, [LEAGUE], "am", add_run(c))
    assert recs(c, fx["in"], "H", strategy=None) == []      # 三轨皆无


def test_odds_band_gates_isolated(priced, monkeypatch):
    """赔率带是独立关卡：EV 关卡放开后，1.4/6.0 入选、1.39/6.01 仍被带剔除。"""
    c, fx, probs = priced
    monkeypatch.setattr(value, "EV_MIN", -1.0)              # 只留带与 edge 两关
    deltas = {"H": 0.06, "D": -0.05, "A": -0.05}            # edge_H≈0.083，必然过 edge 关

    def probe(tag, home_odds):
        fid = add_fixture(c, f"ev-{tag}", HOME, AWAY, _NOW + timedelta(hours=20))
        q = {m: probs[m] - deltas[m] for m in ("H", "D", "A")}
        mk = {"home": home_odds, "draw": 1.0 / (q["D"] * S), "away": 1.0 / (q["A"] * S)}
        _insert_snap(c, fid, "h2h", mk, EARLIER, "band_probe")   # 单 book 单批：best 即报价
        c.commit()
        generate_recommendations(c, [LEAGUE], "am", add_run(c))
        assert probs["H"] - devig_proportional(
            [mk["home"], mk["draw"], mk["away"]])[0] > EDGE_MIN, tag
        return sorted({r["market"] for r in recs(c, fid, "H", strategy=None)})

    assert probe("lo-in", 1.4) == ["H"]
    assert probe("lo-out", 1.39) == []
    assert probe("hi-in", 6.0) == ["H"]
    assert probe("hi-out", 6.01) == []


# ---------------------------------------------------------------- 窗口与对齐


def test_52h_window_inclusive_lower_and_upper(priced):
    c, fx, probs = priced
    generate_recommendations(c, [LEAGUE], "am", add_run(c))
    assert recs(c, fx["in"])                                # 窗口内
    assert recs(c, fx["at0"])                               # 恰 now（下端点，含）
    assert recs(c, fx["at52"])                              # 恰 52h（上端点，含）
    assert recs(c, fx["at53"], strategy=None) == []         # 53h → 出窗（三轨皆无）
    assert recs(c, fx["past"], strategy=None) == []         # 已开球（now−1h）→ 出窗


def test_window_lower_endpoint_is_exactly_now(priced):
    """kickoff == now 必须入选：钉死区间是闭区间 ``[now, now+52h]``（杀 ``now <`` 突变）。"""
    c, fx, probs = priced
    ids = generate_recommendations(c, [LEAGUE], "am", add_run(c))
    assert recs(c, fx["at0"])                               # 有盘口、对齐、恰在下端点
    assert {r["market"] for r in recs(c, fx["at0"])} == {"H", "O2.5"}
    assert {r["id"] for r in recs(c, fx["at0"])} <= set(ids)
    # 阳性对照：出窗侧在完全相同的盘口下确实为空（排除「靠缺价误绿」，三轨皆无）
    assert recs(c, fx["at53"], strategy=None) == []
    assert recs(c, fx["past"], strategy=None) == []


def test_unaligned_fixture_never_recommended(priced):
    c, fx, probs = priced
    generate_recommendations(c, [LEAGUE], "am", add_run(c))
    assert recs(c, fx["unaligned"], strategy=None) == []    # 有盘口也不推荐（三轨皆无）


def test_league_without_training_data_is_skipped(priced):
    c, fx, probs = priced
    c.executemany("INSERT INTO teams (league, name) VALUES ('SP1', ?)",
                  [("Barcelona",), ("Real Madrid",)])       # 无 matches → 训练样本不足
    c.commit()
    ids = generate_recommendations(c, [LEAGUE, "SP1"], "am", add_run(c))
    assert ids                                              # E0 照常出推荐
    assert c.execute(
        "SELECT COUNT(*) c FROM recommendations WHERE fixture_id IN"
        " (SELECT id FROM fixtures WHERE league='SP1')").fetchone()["c"] == 0


def test_phase_is_validated(priced):
    c, fx, probs = priced
    with pytest.raises(ValueError, match="phase"):
        generate_recommendations(c, [LEAGUE], "xx", add_run(c))


def test_model_is_fitted_once_per_league_per_run(priced, monkeypatch):
    """同联赛多场候选只拟合**一次**/run（把拟合提到 fixture 循环外的守卫）。"""
    c, fx, probs = priced
    assert len({k for k in ("in", "at0", "at52")}) == 3     # 前置：窗口内 ≥3 场同联赛
    calls = []
    orig = value.fit_league

    def counting(matches, asof, league, *args, **kwargs):
        calls.append(league)
        return orig(matches, asof, league, *args, **kwargs)

    monkeypatch.setattr(value, "fit_league", counting)
    ids = generate_recommendations(c, [LEAGUE], "am", add_run(c))
    assert calls == [LEAGUE]                                # 恰 1 次，且是该联赛
    assert len(ids) == 18                      # 拟合结果被全部候选复用（3 场×2 市场×3 轨）


# ---------------------------------------------------------------- UNIQUE 刷新


def test_unique_conflict_refreshes_prices_and_keeps_persona(priced):
    c, fx, probs = priced
    run1 = add_run(c)
    generate_recommendations(c, [LEAGUE], "am", run1)
    first = recs(c, fx["in"], "H")[0]
    c.execute("UPDATE recommendations SET verdict='keep', confidence_delta=0.1,"
              " final_stake_frac=0.015 WHERE id=?", (first["id"],))
    c.commit()

    # 新批次快照（fetched_at 更晚）+ 新 run → 价格类字段刷新
    seed_h2h(c, fx["in"], probs, {"H": 0.08, "D": -0.02, "A": -0.02},
             fetched_at=LATER, bookmaker="betfair")
    run2 = add_run(c)
    generate_recommendations(c, [LEAGUE], "am", run2)
    rows = recs(c, fx["in"], "H")
    assert len(rows) == 1                                   # UNIQUE 键不产第二行
    second = rows[0]
    assert second["id"] == first["id"]                      # 原地 UPDATE
    assert second["best_odds"] == pytest.approx(
        1.0 / ((probs["H"] - 0.08) * S))                    # 新批次的最优价
    assert second["best_odds"] > first["best_odds"]
    assert second["bookmaker"] == "betfair"
    assert second["run_id"] == run2                         # 归属刷新它的 run
    # kelly 重算的有效 pin（控制器裁定）：best_odds 刷新前后确已变化（上一行断言
    # second > first），故 kelly 必须等于「新价」的重算值——若 DO UPDATE 漏刷该列，
    # 这里读到的是旧价的 kelly，恒真假象被拆穿
    assert second["kelly_stake_frac"] == kelly_fraction(
        second["model_p"], second["best_odds"])
    # M4 persona 三列**不被** M3 的刷新清掉
    assert second["verdict"] == "keep"
    assert second["confidence_delta"] == 0.1
    assert second["final_stake_frac"] == 0.015


def test_older_snapshot_batch_is_ignored(priced):
    """价格取「最新一批」快照：更晚 fetched_at 的批次生效，更早的不再回读。"""
    c, fx, probs = priced
    seed_h2h(c, fx["in"], probs, {"H": 0.08, "D": -0.02, "A": -0.02},
             fetched_at=LATER, bookmaker="fresh_book")
    generate_recommendations(c, [LEAGUE], "am", add_run(c))
    h = recs(c, fx["in"], "H")[0]
    assert h["bookmaker"] == "fresh_book"                   # EARLIER 的 pinnacle 被弃
    assert h["best_odds"] == pytest.approx(1.0 / ((probs["H"] - 0.08) * S))


def test_quotes_within_same_batch_still_compete(priced):
    """对照组：同一 fetched_at 批次内跨 bookmaker 仍取最大价。"""
    c, fx, probs = priced
    seed_h2h(c, fx["in"], probs, {"H": 0.08, "D": -0.02, "A": -0.02},
             fetched_at=EARLIER, bookmaker="b_high")
    generate_recommendations(c, [LEAGUE], "am", add_run(c))
    assert recs(c, fx["in"], "H")[0]["bookmaker"] == "b_high"   # 高于 pinnacle 的价
    assert recs(c, fx["in"], "H")[0]["best_odds"] == pytest.approx(
        1.0 / ((probs["H"] - 0.08) * S))


# ---------------------------------------------------------------- 双轨同刻双落（A1，§6.6）


def test_generate_writes_three_tracks_single_commit(priced):
    """每个过门槛候选写三轨行（§6.6 双轨 + §12.7 nokb 对照轨）：数字全同、
    persona 两轨中性初始（final = kelly）、model_only 退回 kelly（NULL）。"""
    c, fx, probs = priced
    run = add_run(c)
    ids = generate_recommendations(c, [LEAGUE], "am", run)
    rows = c.execute(
        "SELECT * FROM recommendations WHERE run_id=? ORDER BY strategy, market",
        (run,)).fetchall()
    mo = [r for r in rows if r["strategy"] == "model_only"]
    mp = [r for r in rows if r["strategy"] == "model_persona"]
    nk = [r for r in rows if r["strategy"] == "model_persona_nokb"]
    assert len(mo) == len(mp) == len(nk) > 0 and len(rows) == 3 * len(mo)
    assert {r["id"] for r in rows} == set(ids)              # 返回 ids 含三轨
    for a, b, n in zip(mo, mp, nk):                         # 同数字、三轨
        for col in ("fixture_id", "market", "phase", "run_id", "model_p",
                    "market_p", "best_odds", "bookmaker", "edge", "ev",
                    "kelly_stake_frac"):
            assert a[col] == b[col] == n[col], col
        assert a["final_stake_frac"] is None                # model_only：退回 kelly（M3 语义）
    for r in mp + nk:                                       # persona 两轨中性初始
        assert r["final_stake_frac"] == r["kelly_stake_frac"]
        assert r["verdict"] is None and r["confidence_delta"] is None
        assert r["key_factors"] is None and r["report_md"] is None


def test_rerun_refreshes_prices_keeps_verdict(priced):
    """重跑刷新价格列但不清 persona 位：verdict/confidence_delta/final 三列保留。"""
    c, fx, probs = priced
    generate_recommendations(c, [LEAGUE], "am", add_run(c))
    before = c.execute(
        "SELECT id, kelly_stake_frac FROM recommendations"
        " WHERE strategy='model_persona' ORDER BY id").fetchall()
    c.execute("UPDATE recommendations SET verdict='veto', confidence_delta=0,"
              " final_stake_frac=0 WHERE strategy='model_persona'")
    c.commit()
    generate_recommendations(c, [LEAGUE], "am", add_run(c))     # pm 重跑语义
    rows = c.execute(
        "SELECT id, verdict, confidence_delta, final_stake_frac,"
        " kelly_stake_frac FROM recommendations"
        " WHERE strategy='model_persona' ORDER BY id").fetchall()
    assert [r["id"] for r in rows] == [r["id"] for r in before]  # 无第二行，原地刷新
    assert all(r["verdict"] == "veto" for r in rows)             # 判决三列不清（M3 接缝语义）
    assert all(r["confidence_delta"] == 0 for r in rows)
    assert all(r["final_stake_frac"] == 0.0 for r in rows)
    assert all(r["kelly_stake_frac"] == b["kelly_stake_frac"]
               for r, b in zip(rows, before))                    # kelly 照常刷新计算


# ---------------------------------------------------------------- 纪律


def test_generate_does_not_touch_a_line_tables(priced):
    c, fx, probs = priced
    before = c.execute("SELECT COUNT(*) c FROM matches").fetchone()["c"]
    generate_recommendations(c, [LEAGUE], "am", add_run(c))
    assert c.execute("SELECT COUNT(*) c FROM matches").fetchone()["c"] == before
    assert c.execute(
        "SELECT COUNT(*) c FROM backtest_predictions").fetchone()["c"] == 0
    assert c.execute("SELECT COUNT(*) c FROM bets").fetchone()["c"] == 0


def test_no_fixtures_yields_no_recommendations(conn):
    assert generate_recommendations(conn, [LEAGUE], "am", add_run(conn)) == []
    assert conn.execute(
        "SELECT COUNT(*) c FROM recommendations").fetchone()["c"] == 0


def test_empty_league_list_yields_nothing(conn):
    assert generate_recommendations(conn, [], "am", add_run(conn)) == []
    assert conn.execute(
        "SELECT COUNT(*) c FROM recommendations").fetchone()["c"] == 0


# ---------------------------------------------------------------- M6 三轨 + hash


def test_three_tracks_same_numbers_and_nokb_kelly_init(priced):
    """同刻三落数字全同；nokb final 中性初始 = kelly（同 model_persona 语义）。"""
    c, fx, probs = priced
    generate_recommendations(c, [LEAGUE], "am", add_run(c))
    fid = fx["in"]
    rows = c.execute(
        "SELECT strategy, model_p, best_odds, edge, ev, kelly_stake_frac,"
        " final_stake_frac FROM recommendations WHERE fixture_id=? AND market='H'",
        (fid,)).fetchall()
    by = {r["strategy"]: r for r in rows}
    assert set(by) == {"model_only", "model_persona", "model_persona_nokb"}
    nums = ("model_p", "best_odds", "edge", "ev", "kelly_stake_frac")
    assert all(by["model_persona"][k] == by["model_persona_nokb"][k] == by["model_only"][k]
               for k in nums)
    assert by["model_persona"]["final_stake_frac"] == by["model_persona"]["kelly_stake_frac"]
    assert by["model_persona_nokb"]["final_stake_frac"] == by["model_persona_nokb"]["kelly_stake_frac"]
    assert by["model_only"]["final_stake_frac"] is None


def test_personas_hash_stamped_on_all_rows(priced):
    c, fx, probs = priced
    run = add_run(c)
    generate_recommendations(c, [LEAGUE], "am", run, personas_hash="a" * 64)
    rows = c.execute("SELECT DISTINCT personas_hash, strategy FROM recommendations"
                     " WHERE run_id=?", (run,)).fetchall()
    assert {r["personas_hash"] for r in rows} == {"a" * 64}
    assert len(rows) == 3   # 三轨都带戳


def test_personas_hash_not_stamped_by_default(priced):
    c, fx, probs = priced
    run = add_run(c)
    generate_recommendations(c, [LEAGUE], "am", run)   # 无 hash（默认）
    row = c.execute("SELECT DISTINCT personas_hash FROM recommendations"
                    " WHERE run_id=?", (run,)).fetchone()
    assert row["personas_hash"] is None


def test_upsert_refresh_keeps_personas_hash(priced):
    """DO UPDATE 刷新价格字段时不动 personas_hash——判决与判决语境同源
    （run A 判的决，hash 留 A 的；新 run 刷价不冒充新语境，设计档 §3.1）。"""
    c, fx, probs = priced
    fid = fx["in"]
    run1 = add_run(c)
    generate_recommendations(c, [LEAGUE], "am", run1, personas_hash="a" * 64)
    # 同 fixture/market/phase 再跑一个 run（新价格、不同 hash）
    seed_h2h(c, fid, probs, {"H": 0.08, "D": -0.02, "A": -0.02},
             fetched_at=LATER, bookmaker="betfair")
    run2 = add_run(c)
    generate_recommendations(c, [LEAGUE], "am", run2, personas_hash="b" * 64)
    rows = c.execute(
        "SELECT personas_hash, run_id FROM recommendations WHERE fixture_id=?"
        " AND market='H' AND strategy='model_persona'", (fid,)).fetchall()
    assert len(rows) == 1 and rows[0]["personas_hash"] == "a" * 64
    assert rows[0]["run_id"] == run2
