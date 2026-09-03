"""contract 输入侧测试：种子库 → build_input 形状 + form/h2h/排名的查询语义。

全离线：种子直插（模式同 tests/pipeline/test_paper.py）——``teams`` /
``matches``（M1 口径）/ ``fixtures`` / ``recommendations``（model_persona 轨）。

时间口径钉死（form / h2h / 排名一律取 kickoff **日历日之前**的完赛行），种子
里故意放两行陷阱：kickoff 当日同配对的完赛行（比分已写也不算历史）与未完赛
行（``fthg`` NULL）——若查询口径写错（``<=`` 或漏 ``IS NOT NULL``），断言即翻。
"""
import pytest

from fa.db import connect, init_db
from fa.persona import PersonaError
from fa.persona.contract import build_input, build_prompt

LEAGUE = "D1"
HOME = "Bayern Munich"
AWAY = "Borussia Dortmund"
KICKOFF = "2026-09-10T18:45:00Z"
NOW = "2026-09-10"          # kickoff 当日；form/h2h 严格早于该日


# ---------------------------------------------------------------- 种子工具

def team_id(c, name, league=LEAGUE):
    row = c.execute("SELECT id FROM teams WHERE league=? AND name=?",
                    (league, name)).fetchone()
    if row is not None:
        return row["id"]
    return c.execute("INSERT INTO teams (league, name) VALUES (?, ?)",
                     (league, name)).lastrowid


def add_match(c, home, away, date, fthg, ftag, league=LEAGUE, season=2026):
    """M1 口径行；fthg/ftag 传 None 表示未完赛（排位/form 都得剔掉）。"""
    return c.execute(
        "INSERT INTO matches (league, season, date, home_team_id, away_team_id,"
        " fthg, ftag, raw_line) VALUES (?,?,?,?,?,?,?, '{}')",
        (league, season, date, team_id(c, home, league), team_id(c, away, league),
         fthg, ftag)).lastrowid


def add_fixture(c, event_key, home, away, kickoff=KICKOFF, league=LEAGUE):
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
        " VALUES ('matchday', ?, '2026-09-10T09:00:00Z', 'ok')", (phase,)).lastrowid


def add_rec(c, fixture_id, market, model_p=0.5, market_p=0.4, best_odds=2.0,
            edge=0.1, ev=0.3, kelly=0.02, strategy="model_persona",
            run_id=None, phase="am"):
    """run_id 缺省时自建一个 run 行；同一次管线执行的行应挂同一 run
    （build_input 按 (fixture_id, run_id) 取本窗行，T6 review 裁定）。"""
    return c.execute(
        "INSERT INTO recommendations (run_id, fixture_id, strategy, market, phase,"
        " model_p, market_p, best_odds, bookmaker, edge, ev, kelly_stake_frac,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (add_run(c, phase) if run_id is None else run_id, fixture_id, strategy,
         market, phase, model_p, market_p, best_odds, "pinnacle", edge, ev,
         kelly, "2026-09-10T09:00:00Z")).lastrowid


def _seed(c):
    """主 fixture（D1，kickoff 2026-09-10T18:45Z，双侧已对齐）+ 其历史：

    - home 近 5 场（date DESC）W W D L W；away 近 5 场 L L D W L
    - 两队上赛季交手 2 次（更近的 2026-04-12 主场 4-2）
    - model_persona 轨两行（H 与 O2.5，H 的 model_p=0.52，同一 run——一次管线
      执行产出本窗全部行）
    - 两行时间边界陷阱（见模块 docstring）

    返回主 fixture id 与该 run id（build_input 需按 run 取候选）。
    """
    # 近 5 场：各自对手不取彼此（避免多出 h2h 行）；两队日期错开便于对账
    add_match(c, HOME, "Freiburg", "2026-09-04", 3, 1)           # W
    add_match(c, HOME, "Union Berlin", "2026-08-29", 2, 0)       # W
    add_match(c, HOME, "Mainz", "2026-08-22", 1, 1)              # D
    add_match(c, "Leverkusen", HOME, "2026-08-15", 2, 0)         # L（客场）
    add_match(c, HOME, "Stuttgart", "2026-08-08", 2, 1)          # W
    add_match(c, "Hoffenheim", AWAY, "2026-09-06", 1, 0)         # L
    add_match(c, AWAY, "Leipzig", "2026-08-29", 0, 3)            # L
    add_match(c, AWAY, "Werder", "2026-08-22", 1, 1)             # D
    add_match(c, AWAY, "Augsburg", "2026-08-15", 2, 0)           # W
    add_match(c, "Frankfurt", AWAY, "2026-08-08", 2, 0)          # L
    # h2h：上赛季两次交手（跨赛季照取）
    add_match(c, HOME, AWAY, "2026-04-12", 4, 2, season=2025)    # 主场 4-2
    add_match(c, AWAY, HOME, "2025-10-04", 1, 1, season=2025)    # 客场 1-1
    # 陷阱一：kickoff 当日同配对已有完赛行 → 不是历史（form/h2h/排名都不得取）
    add_match(c, HOME, AWAY, NOW, 0, 5)
    # 陷阱二：未完赛行（fthg NULL）且是 home 最近一场 → 不得计入，也不得炸 W/D/L
    add_match(c, HOME, "Union Berlin", "2026-09-08", None, None)

    fx = add_fixture(c, "ev-d1-1", HOME, AWAY)
    run = add_run(c)
    add_rec(c, fx, "H", model_p=0.52, market_p=0.44, best_odds=2.05, edge=0.08,
            ev=0.16, kelly=0.01, run_id=run)
    add_rec(c, fx, "O2.5", model_p=0.58, market_p=0.545, best_odds=1.95,
            edge=0.035, ev=0.131, kelly=0.008, run_id=run)
    return fx, run


@pytest.fixture
def conn_seeded(tmp_path):
    """每用例独立新库；yield ``(连接, 主 fixture id, run id)``。"""
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    c.commit()
    try:
        yield c, *_seed(c)
    finally:
        c.close()


# ---------------------------------------------------------------- 形状


def test_build_input_shape(conn_seeded):
    conn, fx, run = conn_seeded
    obj = build_input(conn, fx, run)
    assert obj["league"] == "D1"
    assert obj["match"] == {"kickoff_utc": KICKOFF, "home": HOME, "away": AWAY}
    assert [c["market"] for c in obj["candidates"]] == ["H", "O2.5"]
    # 七键一个不多——M4 位（verdict/final_stake_frac/…）与库内其余列不得泄漏
    assert obj["candidates"][0] == {
        "market": "H", "model_p": 0.52, "market_p": 0.44, "best_odds": 2.05,
        "edge": 0.08, "ev": 0.16, "kelly_stake_frac": 0.01}
    assert obj["model_summary"] == {"p_home": pytest.approx(0.52, abs=1e-9)}
    # O2.5 行不进 summary；缺的市场键省略（不补 0、不发明）
    assert "p_draw" not in obj["model_summary"]
    assert "p_away" not in obj["model_summary"]
    assert "exp_goals" not in str(obj)            # λ 不在库内，不发明
    assert obj["form"] == {"home_last5": ["W", "W", "D", "L", "W"],
                           "away_last5": ["L", "L", "D", "W", "L"]}
    assert obj["h2h_recent"] == [
        {"date": "2026-04-12", "score": "4-2", "home": HOME},      # 近的在前
        {"date": "2025-10-04", "score": "1-1", "home": AWAY},
    ]


def test_build_input_omits_empty_sections(conn_seeded):
    conn, _fx, run = conn_seeded
    fx = add_fixture(conn, "ev-it1", "Juventus", "Napoli", league="IT1")
    obj = build_input(conn, fx, run)
    assert obj["candidates"] == []                # 该场无 model_persona 行
    assert obj["form"] == {"home_last5": [], "away_last5": []}
    assert obj["h2h_recent"] == []
    assert "home_pos" not in obj                   # 无本赛季数据 → 两键俱缺
    assert "away_pos" not in obj


def test_build_input_candidates_filtered_by_run(conn_seeded):
    """am/pm 双窗同 market 各一行：candidates/model_summary 按 (fixture_id, run_id)
    取**本窗**行（T6 review 裁定）——不按 run 过滤会把两窗同 market 双行一起塞进
    candidates，persona 一场一次的输入被重复候选污染。"""
    conn, fx, run_am = conn_seeded
    run_pm = add_run(conn, phase="pm")
    add_rec(conn, fx, "H", model_p=0.55, market_p=0.5, kelly=0.02,
            run_id=run_pm, phase="pm")
    am = build_input(conn, fx, run_am)
    pm = build_input(conn, fx, run_pm)
    assert [c["market"] for c in am["candidates"]] == ["H", "O2.5"]
    assert [c["market"] for c in pm["candidates"]] == ["H"]
    assert pm["candidates"][0]["model_p"] == pytest.approx(0.55, abs=1e-9)
    assert pm["model_summary"] == {"p_home": pytest.approx(0.55, abs=1e-9)}
    assert am["model_summary"] == {"p_home": pytest.approx(0.52, abs=1e-9)}


# ---------------------------------------------------------------- 查询语义


def test_sameday_and_unplayed_rows_are_not_history(conn_seeded):
    """kickoff 当日同配对（0-5）与未完赛行（fthg NULL）都不是历史：取走任一行，
    form 串 / h2h 立即变形（当日 0-5 会顶到 h2h 首位、None 行会挤掉最末一个 W）。"""
    conn, fx, run = conn_seeded
    obj = build_input(conn, fx, run)
    assert obj["form"]["home_last5"] == ["W", "W", "D", "L", "W"]
    assert obj["h2h_recent"][0]["date"] == "2026-04-12"
    assert len(obj["h2h_recent"]) == 2
    # 排名同样只看本赛季 kickoff 前完赛行：Bayern 10 分独居榜首；Dortmund 4 分
    # 压过四支 3 分队 → 第 2（当日 0-5 / None 行计入也不改名次，故由上两断言反证）
    assert obj["home_pos"] == 1 and obj["away_pos"] == 2


def test_positions_come_from_current_season_before_kickoff(conn_seeded):
    """E0 四队小联赛，主客两队本赛季**不交手**（h2h 不被积分行污染）：映射按
    team_id 不按主客顺序；上赛季行（date 在前、season≠推断值）与当日行都不得
    计入积分——任一口径写错，Chelsea 都会窜到第 1、home_pos 变 1。"""
    conn, _fx, run = conn_seeded
    e0 = "E0"
    add_match(conn, "Chelsea", "Spurs", "2026-08-20", 3, 0, league=e0)
    add_match(conn, "Arsenal", "Spurs", "2026-08-24", 1, 0, league=e0)
    add_match(conn, "Everton", "Chelsea", "2026-08-27", 2, 1, league=e0)
    add_match(conn, "Everton", "Arsenal", "2026-08-30", 1, 2, league=e0)
    # 上赛季交手（h2h 跨赛季照取）；season=2025 → 不入本赛季积分
    add_match(conn, "Chelsea", "Arsenal", "2026-05-02", 4, 0, league=e0, season=2025)
    # kickoff 当日同配对 → 非历史，积分与 h2h 都不得取
    add_match(conn, "Chelsea", "Arsenal", NOW, 3, 0, league=e0)
    # Arsenal 6 分(+2) > Chelsea 3 分(+2, 3 净胜球压 Everton 0) > Everton 3 > Spurs 0
    fx = add_fixture(conn, "ev-e0", "Chelsea", "Arsenal", league=e0)
    obj = build_input(conn, fx, run)
    assert obj["home_pos"] == 2 and obj["away_pos"] == 1
    assert obj["h2h_recent"] == [{"date": "2026-05-02", "score": "4-0",
                                  "home": "Chelsea"}]


def test_unaligned_or_missing_fixture_raises(conn_seeded):
    """任一侧未对齐（§3.3 NULL）或 fixture 不存在 → PersonaError（§6.6 可降级，
    该场按纯模型处理，不猜队名）。"""
    conn, _fx, run = conn_seeded
    unaligned = add_fixture(conn, "ev-null-side", HOME, None)
    with pytest.raises(PersonaError):
        build_input(conn, unaligned, run)
    with pytest.raises(PersonaError):
        build_input(conn, 10 ** 9, run)


# ---------------------------------------------------------------- prompt 拼装


def test_build_prompt_layout():
    prompt = build_prompt("PERSONA 全文", {"league": "D1"})
    assert prompt.startswith("PERSONA 全文")
    assert '"league": "D1"' in prompt
    # 次序：persona 全文 → 输入 JSON → 契约说明（§6.2）
    assert (prompt.index("PERSONA 全文") < prompt.index('"league"')
            < prompt.index("## 输出契约"))
    assert "verdict" in prompt and "confidence_delta" in prompt   # 契约说明在场
    assert "key_factors" in prompt and "report_md" in prompt
    assert "宁保守勿越界" in prompt


def test_build_prompt_strips_persona_tail_and_keeps_non_ascii():
    prompt = build_prompt("PERSONA 全文\n\n", {"league": "D1", "h2h": "拜人 vs 多人"})
    assert "PERSONA 全文\n\n## 本场输入" in prompt     # 尾部空白剥平，不堆空行
    assert '"h2h": "拜人 vs 多人"' in prompt           # ensure_ascii=False，中文不转义
