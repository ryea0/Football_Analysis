"""tests/evolve 共享夹具——窗口种子唯一事实源（Ruling 4：T7 建、T13 E2E 复用）。

种子 SQL 形态沿 tests/pipeline/test_paper.py 的既有夹具（teams/fixtures/runs/
recommendations/bets 直插），**列清单以 src/fa/db.py 的 DDL 为准**：
- teams(id, league, name)                       UNIQUE(league, name)
- fixtures(id, league, event_key, source, kickoff_utc, home_team_id,
           away_team_id, status, created_at)    event_key UNIQUE
- runs(id, type, phase, started_at, status)     type/phase/status 词表见 DDL
- recommendations(run_id, fixture_id, strategy, market, phase, model_p,
        market_p, best_odds, bookmaker, edge, ev, kelly_stake_frac, verdict,
        confidence_delta, final_stake_frac, personas_hash, created_at)
        UNIQUE(fixture_id, market, strategy, phase)
- bets(recommendation_id, mode, placed_at, bookmaker, odds_taken, stake,
       status, settled_at, return_amt, closing_odds, closing_source, clv)

时间口径：created_at / started_at / placed_at 写 UTC ISO 'Z' 串；落窗判定沿
``date(created_at, '+8 hours')``（北京时间日期，evidence 同口径）——
``{day}T03:00:00Z`` 的北京日期即 day 本身，``{day}T11:30:00Z`` 的 kickoff
是北京时间 19:30 的晚场。
"""
import pytest

from fa import db as dbmod

LEAGUE = "E0"
DAY1 = "2026-09-05"          # 缺省种子日：窗 1 内（[2026-09-04, 2026-10-16)）


@pytest.fixture
def conn(tmp_path):
    """独立 tmp 库（当前 schema v8）；行取值走 sqlite3.Row。"""
    p = tmp_path / "evolve.db"
    dbmod.init_db(p)
    c = dbmod.connect(p)
    yield c
    c.close()


def _mk_run(conn, day=DAY1, phase="am"):
    """一条 matchday run（started_at = day 当日 UTC 03:00）。"""
    return conn.execute(
        "INSERT INTO runs (type, phase, started_at, status)"
        " VALUES ('matchday', ?, ?, 'ok')",
        (phase, f"{day}T03:00:00Z")).lastrowid


def _team_id(conn, league, name):
    row = conn.execute("SELECT id FROM teams WHERE league=? AND name=?",
                       (league, name)).fetchone()
    if row is not None:
        return row["id"]
    return conn.execute("INSERT INTO teams (league, name) VALUES (?, ?)",
                        (league, name)).lastrowid


def _mk_fixture(conn, fixture_id, league=LEAGUE, day=DAY1, aligned=True):
    """一场 fixture：队名与 event_key 随 id 派生（避开 teams 的
    UNIQUE(league,name) 与 fixtures.event_key UNIQUE 的跨调用撞车）；
    aligned=False 主客留 NULL（§3.3 未对齐形态，label 兜底路）。
    返回 fixture_id。"""
    home = away = None
    if aligned:
        home = _team_id(conn, league, f"Home{fixture_id}")
        away = _team_id(conn, league, f"Away{fixture_id}")
    conn.execute(
        "INSERT INTO fixtures (id, league, event_key, source, kickoff_utc,"
        " home_team_id, away_team_id, status, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (fixture_id, league, f"seed-{fixture_id}", "oddsapi",
         f"{day}T11:30:00Z", home, away, "scheduled", f"{day}T03:00:00Z"))
    return fixture_id


def _mk_rec(conn, run_id, fixture_id, strategy, market="H", phase="am",
            verdict=None, confidence_delta=None, final_stake_frac=None,
            day=DAY1):
    """一行推荐：数字字段取固定占位值（本层只关心轨道/判决/落窗）。"""
    return conn.execute(
        "INSERT INTO recommendations (run_id, fixture_id, strategy, market,"
        " phase, model_p, market_p, best_odds, bookmaker, edge, ev,"
        " kelly_stake_frac, verdict, confidence_delta, final_stake_frac,"
        " personas_hash, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, fixture_id, strategy, market, phase, 0.5, 0.4, 2.0,
         "pinnacle", 0.1, 0.3, 0.02, verdict, confidence_delta,
         final_stake_frac, None, f"{day}T03:00:00Z")).lastrowid


def _mk_bet(conn, rec_id, status="pending", stake=10.0, return_amt=None,
            clv=None, odds_taken=2.0, day=DAY1, mode="paper"):
    """一行注：won 必须显式给 return_amt；pending/void/lost 缺省空账。"""
    return conn.execute(
        "INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker,"
        " odds_taken, stake, status, settled_at, return_amt, closing_odds,"
        " closing_source, clv) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (rec_id, mode, f"{day}T03:10:00Z", "pinnacle", odds_taken, stake,
         status, None if status == "pending" else f"{day}T15:00:00Z",
         return_amt, None, None, clv)).lastrowid


def seed_window_rows(conn, run_id=None, day=DAY1, fixture_id=1, *,
                     verdicts=True):
    """一场三轨种子（T7 证据测试与 T13 E2E 共享的最小场景，Ruling 4）。

    - ``run_id=None`` 新建 matchday/am run，否则挂到给定 run
    - 行 created_at / kickoff 取 ``day``（北京时间日期即 day，落窗判定沿
      evidence 的 ``'+8 hours'`` 口径）
    - ``verdicts=True``：kb 轨 verdict='veto'（delta -0.25、final_frac 0.0）、
      nokb 轨 'agree'（delta 0.0）——kb 误杀 / nokb 不误杀的对照样板；
      model_only 行落**已结算 won** 注（stake 10 / return 22 / clv +0.05，
      ROI = (22-10)/10 = 1.2）
    - ``verdicts=False``：三轨判决留 NULL（供 run_persona_phase 现场填写），
      model_only 行与对照注照落

    返回 ``{"run_id","fixture_id","kb","nokb","mo","mo_bet"}``（后四者为行 id）。
    """
    run_id = _mk_run(conn, day=day) if run_id is None else run_id
    _mk_fixture(conn, fixture_id, day=day)
    kb = _mk_rec(conn, run_id, fixture_id, "model_persona",
                 verdict="veto" if verdicts else None,
                 confidence_delta=-0.25 if verdicts else None,
                 final_stake_frac=0.0 if verdicts else None, day=day)
    nokb = _mk_rec(conn, run_id, fixture_id, "model_persona_nokb",
                   verdict="agree" if verdicts else None,
                   confidence_delta=0.0 if verdicts else None,
                   final_stake_frac=0.02 if verdicts else None, day=day)
    mo = _mk_rec(conn, run_id, fixture_id, "model_only", day=day)
    # 对照注：odds 2.2 → return 22（stake 10，ROI = (22-10)/10 = 1.2）；clv 直记
    mo_bet = _mk_bet(conn, mo, status="won", stake=10.0, return_amt=22.0,
                     clv=0.05, odds_taken=2.2, day=day)
    conn.commit()
    return {"run_id": run_id, "fixture_id": fixture_id, "kb": kb,
            "nokb": nokb, "mo": mo, "mo_bet": mo_bet}


def seed_kill_with_double_cover(conn, fixture_id, run_id=None, day=DAY1,
                                market="H", *, am=("won", 10.0, 22.0),
                                pm=("lost", 10.0, 0.0)):
    """误杀场景 + am/pm **双窗覆盖**的 model_only 已结算对照注对（T7 修复轮
    增量，Ruling 4 的第二块事实源；不触碰 seed_window_rows 既有语义）。

    recommendations 的 UNIQUE(fixture_id, market, strategy, phase) 允许同一
    (fixture, market) 的 am/pm 两行并存 → 同场同市场两张已结算 model_only 注。
    evidence 的误杀对照按计划口径取**全部**已结算注聚合：
    (Σreturn−Σstake)/Σstake —— 缺省 am won(stake 10/return 22) +
    pm lost(stake 10/return 0) → (22−20)/20 = 0.1，与行序/query plan 无关。

    am/pm 各为 ``(status, stake, return_amt)`` 三元组；传 ``None`` 跳过该腿
    （单腿 = 只有一张已结算注）。fixture_id 必填（调用方自选，避免与
    seed_window_rows 缺省的 fixture 1 撞 UNIQUE）。

    返回 ``{"run_id","fixture_id","kb","am","pm","am_bet","pm_bet"}``
    （am/pm 可能为 None）。
    """
    run_id = _mk_run(conn, day=day) if run_id is None else run_id
    _mk_fixture(conn, fixture_id, day=day)
    kb = _mk_rec(conn, run_id, fixture_id, "model_persona", market=market,
                 verdict="veto", confidence_delta=-0.25,
                 final_stake_frac=0.0, day=day)
    out = {"run_id": run_id, "fixture_id": fixture_id, "kb": kb,
           "am": None, "pm": None, "am_bet": None, "pm_bet": None}
    for leg, spec in (("am", am), ("pm", pm)):      # spec None = 跳过该腿
        if spec is None:
            continue
        rec = _mk_rec(conn, run_id, fixture_id, "model_only", market=market,
                      phase=leg, day=day)
        bet = _mk_bet(conn, rec, status=spec[0], stake=spec[1],
                      return_amt=spec[2], odds_taken=2.2, day=day)
        out[leg], out[f"{leg}_bet"] = rec, bet
    conn.commit()
    return out
