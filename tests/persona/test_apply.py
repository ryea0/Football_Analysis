"""apply 测试：§6.4 映射 / 场次广播 / veto 语义 / 阶段编排（fixture 脚本走真子进程）。

种子语义：一 fixture（D1，主客已对齐）的 model_persona 轨两行（H kelly=0.02、
O2.5 kelly=0.01，挂在**同一个 run**——一次管线执行产出一个 phase 的全部行；
``run_persona_phase`` 靠 run_id 选场，``build_input`` 靠 (fixture_id, run_id)
取本窗行）。pm 传播用例另插 phase='pm' 的第二 run。

persona 文件指针：真实 ``personas/*.md`` 由 T9/T10 落盘，本层把
``fa.persona.apply.persona_path`` 指向 tmp——fake 保留 config 的两条真实语义
（league 不在映射 → ValueError、文件缺失 → FileNotFoundError），hermes 仍走
fixture 脚本真子进程（C1：mock 与实跑同一代码路径，不破）。
"""
from pathlib import Path

import pytest

from fa.db import connect, init_db
from fa.persona.apply import apply_verdict, run_persona_phase

FIXTURES = Path(__file__).parent / "fixtures"

LEAGUE = "D1"
HOME = "Bayern Munich"
AWAY = "Borussia Dortmund"
KICKOFF = "2026-09-10T18:45:00Z"

AGREE = {"verdict": "agree", "confidence_delta": 0.1,
         "key_factors": ["a"], "report_md": "x"}
VETO = {"verdict": "veto", "confidence_delta": 0.12,          # 强制置 0
        "key_factors": ["a"], "report_md": "x"}


@pytest.fixture
def fix() -> Path:
    return FIXTURES


# ---------------------------------------------------------------- 种子工具

def team_id(c, name, league=LEAGUE):
    row = c.execute("SELECT id FROM teams WHERE league=? AND name=?",
                    (league, name)).fetchone()
    if row is not None:
        return row["id"]
    return c.execute("INSERT INTO teams (league, name) VALUES (?, ?)",
                     (league, name)).lastrowid


def add_run(c, phase="am"):
    return c.execute(
        "INSERT INTO runs (type, phase, started_at, status)"
        " VALUES ('matchday', ?, '2026-09-10T09:00:00Z', 'ok')", (phase,)).lastrowid


def add_fixture(c, event_key, home, away, league=LEAGUE):
    return c.execute(
        "INSERT INTO fixtures (league, event_key, source, kickoff_utc,"
        " home_team_id, away_team_id, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (league, event_key, "oddsapi", KICKOFF,
         team_id(c, home, league), team_id(c, away, league),
         "scheduled", "2026-09-01T08:00:00Z")).lastrowid


def add_rec(c, run_id, fixture_id, market, kelly=0.02, phase="am"):
    return c.execute(
        "INSERT INTO recommendations (run_id, fixture_id, strategy, market, phase,"
        " model_p, market_p, best_odds, bookmaker, edge, ev, kelly_stake_frac,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, fixture_id, "model_persona", market, phase, 0.5, 0.4, 2.0,
         "pinnacle", 0.1, 0.3, kelly, "2026-09-10T09:00:00Z")).lastrowid


def _seed(c):
    """主 fixture 两行 model_persona（H kelly=0.02 / O2.5 kelly=0.01，同一 run）。"""
    run_id = add_run(c)
    fx = add_fixture(c, "ev-d1-1", HOME, AWAY)
    add_rec(c, run_id, fx, "H", kelly=0.02)
    add_rec(c, run_id, fx, "O2.5", kelly=0.01)
    return fx, run_id


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


@pytest.fixture
def persona_files(tmp_path, monkeypatch):
    """persona 文件指向 tmp：D1 映射到已写盘的人格文件，其余 league 沿 config
    语义抛 ValueError（无映射）。返回目录（用例可删文件造 FileNotFoundError）。"""
    import fa.persona.apply as apply_mod

    d = tmp_path / "personas"
    d.mkdir()
    (d / "bundesliga.md").write_text(
        "# 德甲人格\n\n你是一名谨慎的德甲点评员，只依据本场输入发言。\n",
        encoding="utf-8")

    def fake_persona_path(league):
        if league != LEAGUE:
            raise ValueError(f"无 persona 文件映射：{league!r}")
        return d / "bundesliga.md"

    monkeypatch.setattr(apply_mod, "persona_path", fake_persona_path)
    return d


def recs(c, fixture_id, run_id=None):
    sql = ("SELECT * FROM recommendations WHERE fixture_id=? AND"
           " strategy='model_persona'")
    if run_id is not None:
        sql += " AND run_id=?"
    return c.execute(sql + " ORDER BY market",
                     (fixture_id, run_id) if run_id is not None
                     else (fixture_id,)).fetchall()


# ---------------------------------------------------------------- apply_verdict


def test_apply_agree_broadcasts_with_per_row_kelly(conn_seeded):
    """§6.4：agree 一次判决广播到该场 model_persona 轨全部行，final 逐行按各自
    kelly 重算（不分 phase——am 判决 pm 行共用，§6.2）。"""
    conn, fx, _ = conn_seeded
    n = apply_verdict(conn, fx, AGREE)
    assert n == 2
    rows = recs(conn, fx)
    assert [r["market"] for r in rows] == ["H", "O2.5"]
    assert rows[0]["final_stake_frac"] == pytest.approx(0.02 * 1.1, abs=1e-9)
    assert rows[1]["final_stake_frac"] == pytest.approx(0.01 * 1.1, abs=1e-9)
    for r in rows:
        assert r["verdict"] == "agree"
        assert r["confidence_delta"] == pytest.approx(0.1, abs=1e-9)
        assert r["key_factors"] == '["a"]'
        assert r["report_md"] == "x"


def test_apply_veto_zeroes_all(conn_seeded):
    """veto：三列全零（调用方传来的 delta 也强制置 0），点评两列照写（记录保留）。"""
    conn, fx, _ = conn_seeded
    assert apply_verdict(conn, fx, VETO) == 2
    for r in recs(conn, fx):
        assert r["final_stake_frac"] == 0.0
        assert r["confidence_delta"] == 0.0
        assert r["verdict"] == "veto"
        assert r["key_factors"] == '["a"]'        # JSON 串落库
        assert r["report_md"] == "x"


def test_apply_unknown_fixture_touches_nothing(conn_seeded):
    conn, _fx, _ = conn_seeded
    assert apply_verdict(conn, 10 ** 9, AGREE) == 0


# ---------------------------------------------------------------- 阶段编排：正常


def test_phase_ok_degraded_and_attempted(conn_seeded, monkeypatch, fix,
                                         persona_files):
    conn, fx, run_id = conn_seeded
    monkeypatch.setenv("HERMES_BIN", str(fix / "hermes_ok"))
    out = run_persona_phase(conn, run_id, ["D1"])
    assert out["called"] == out["ok"] == 1 and out["attempted"] == [fx]
    assert out["veto"] == 0 and out["degraded"] == []
    assert [r["verdict"] for r in recs(conn, fx)] == ["agree", "agree"]


def test_phase_veto_counts(conn_seeded, tmp_path, monkeypatch, persona_files):
    conn, fx, run_id = conn_seeded
    script = tmp_path / "hermes_veto"
    script.write_text('#!/bin/sh\n'
                      'echo \'{"verdict":"veto","confidence_delta":0.1,'
                      '"key_factors":["k"],"report_md":"r"}\'\n',
                      encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("HERMES_BIN", str(script))
    out = run_persona_phase(conn, run_id, ["D1"])
    assert out["ok"] == 1 and out["veto"] == 1
    assert all(r["final_stake_frac"] == 0.0 for r in recs(conn, fx))


# ---------------------------------------------------------------- 阶段编排：降级


@pytest.mark.parametrize("script, reason", [
    ("hermes_bad_json", "extract"),
    ("hermes_timeout", "timeout"),
    ("hermes_nonzero", "exit"),
])
def test_phase_degrades_per_reason(conn_seeded, monkeypatch, fix, persona_files,
                                   script, reason):
    """caller/contract 的四种 reason 全词表钉死；降级也算已尝试（当日不重试）。"""
    conn, fx, run_id = conn_seeded
    monkeypatch.setenv("HERMES_BIN", str(fix / script))
    if reason == "timeout":
        monkeypatch.setenv("FA_PERSONA_TIMEOUT", "0.5")
    out = run_persona_phase(conn, run_id, ["D1"])
    assert out["ok"] == 0 and out["degraded"] == [{"fixture_id": fx,
                                                   "reason": reason}]
    assert out["attempted"] == [fx]
    assert all(r["verdict"] is None for r in recs(conn, fx))     # 中性保持


def test_phase_degrades_on_contract_violation(conn_seeded, tmp_path,
                                              monkeypatch, persona_files):
    """超值域 → contract（与 extract 分流，§6.5 报「persona 未生效 + 原因」）。"""
    conn, fx, run_id = conn_seeded
    script = tmp_path / "hermes_overrange"
    script.write_text('#!/bin/sh\n'
                      'echo \'{"verdict":"agree","confidence_delta":0.9,'
                      '"key_factors":["k"],"report_md":"r"}\'\n',
                      encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("HERMES_BIN", str(script))
    out = run_persona_phase(conn, run_id, ["D1"])
    assert out["degraded"][0]["reason"] == "contract"


def test_phase_degrades_when_persona_file_missing(conn_seeded, monkeypatch, fix,
                                                  persona_files):
    """人格文件缺失 → persona_file（T9/T10 落盘前的真实缺口，只降该场）。"""
    conn, fx, run_id = conn_seeded
    (persona_files / "bundesliga.md").unlink()
    monkeypatch.setenv("HERMES_BIN", str(fix / "hermes_ok"))
    out = run_persona_phase(conn, run_id, ["D1"])
    assert out["degraded"][0]["reason"] == "persona_file"
    assert out["called"] == 0                                    # 没到调用那步


def test_phase_degrades_unmapped_league_as_persona_file(conn_seeded, monkeypatch,
                                                        fix, persona_files):
    """fixtures 表里 league 不在 PERSONA_FILES 映射 → config 抛 ValueError，
    归 persona_file 类降级（人格文件缺失的一种），不得炸整个 phase。"""
    conn, fx, run_id = conn_seeded
    other = add_fixture(conn, "ev-e0-1", "Chelsea", "Arsenal", league="E0")
    add_rec(conn, run_id, other, "H", kelly=0.01)
    monkeypatch.setenv("HERMES_BIN", str(fix / "hermes_ok"))
    out = run_persona_phase(conn, run_id, ["D1", "E0"])
    assert out["ok"] == 1 and out["degraded"] == [{"fixture_id": other,
                                                  "reason": "persona_file"}]
    assert out["attempted"] == sorted([fx, other])


def test_phase_degradation_isolates_fixtures(conn_seeded, tmp_path, monkeypatch,
                                             persona_files):
    """§6.5 粒度裁定：一场失败不撤销同联赛已成功判决——按 prompt 分流，主 fixture
    坏 JSON、次 fixture veto，两场各自落位。"""
    conn, fx, run_id = conn_seeded
    other = add_fixture(conn, "ev-d1-2", "Leverkusen", "Leipzig")
    add_rec(conn, run_id, other, "H", kelly=0.01)
    script = tmp_path / "hermes_mixed"
    script.write_text('#!/bin/sh\ncase "$2" in\n  *Bayern*) echo "拒绝 JSON";;\n'
                      '  *) echo \'{"verdict":"veto","confidence_delta":0.1,'
                      '"key_factors":["k"],"report_md":"r"}\';;\nesac\n',
                      encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("HERMES_BIN", str(script))
    out = run_persona_phase(conn, run_id, ["D1"])
    assert out["called"] == 2 and out["ok"] == 1 and out["veto"] == 1
    assert out["degraded"] == [{"fixture_id": fx, "reason": "extract"}]
    assert out["attempted"] == sorted([fx, other])
    assert all(r["verdict"] is None for r in recs(conn, fx))
    assert all(r["verdict"] == "veto" for r in recs(conn, other))


def test_phase_league_outside_list_is_seen_not_called(conn_seeded, monkeypatch,
                                                      fix, persona_files):
    """不在本 phase 联赛清单的场：不调用、但记入 attempted（am 见过即记）。"""
    conn, fx, run_id = conn_seeded
    other = add_fixture(conn, "ev-it1", "Juventus", "Napoli", league="IT1")
    add_rec(conn, run_id, other, "H", kelly=0.01)
    monkeypatch.setenv("HERMES_BIN", str(fix / "hermes_ok"))
    out = run_persona_phase(conn, run_id, ["D1"])
    assert out["called"] == 1 and out["attempted"] == sorted([fx, other])
    assert all(r["verdict"] is None for r in recs(conn, other))


# ---------------------------------------------------------------- 判决传播（§6.2）


def test_phase_skips_attempted(conn_seeded, monkeypatch, fix, persona_files):
    """无已有判决的 attempted 场：零调用、中性保持（传播无源可取，不动库）。"""
    conn, fx, run_id = conn_seeded
    monkeypatch.setenv("HERMES_BIN", str(fix / "hermes_ok"))
    out = run_persona_phase(conn, run_id, ["D1"], attempted={fx})
    assert out["called"] == 0 and out["attempted"] == [fx]
    assert all(r["verdict"] is None for r in recs(conn, fx))


def test_phase_propagates_am_verdict_to_pm_rows(conn_seeded, monkeypatch, fix,
                                                persona_files):
    """pm 沿用 am 判决（§6.2）：am 广播时 pm 行尚不存在，pm run 新落的 NULL 行
    由传播补齐——verdict/delta/点评照抄，final 按 pm 各行 kelly 重算，零调用。"""
    conn, fx, run_am = conn_seeded
    assert apply_verdict(conn, fx, AGREE) == 2
    run_pm = add_run(conn, phase="pm")
    add_rec(conn, run_pm, fx, "H", kelly=0.03, phase="pm")
    add_rec(conn, run_pm, fx, "O2.5", kelly=0.004, phase="pm")

    monkeypatch.setenv("HERMES_BIN", str(fix / "hermes_ok"))
    out = run_persona_phase(conn, run_pm, ["D1"], attempted={fx})
    assert out["called"] == 0 and out["attempted"] == [fx]

    rows = {r["market"]: r for r in recs(conn, fx, run_pm)}
    assert set(rows) == {"H", "O2.5"}
    for r in rows.values():
        assert r["verdict"] == "agree"
        assert r["confidence_delta"] == pytest.approx(0.1, abs=1e-9)
        assert r["key_factors"] == '["a"]' and r["report_md"] == "x"
    assert rows["H"]["final_stake_frac"] == pytest.approx(0.03 * 1.1, abs=1e-9)
    assert rows["O2.5"]["final_stake_frac"] == pytest.approx(0.004 * 1.1,
                                                             abs=1e-9)
    am = {r["market"]: r for r in recs(conn, fx, run_am)}
    assert am["H"]["final_stake_frac"] == pytest.approx(0.02 * 1.1, abs=1e-9)


def test_phase_propagates_veto_zeroes_and_never_overwrites(conn_seeded,
                                                           monkeypatch, fix,
                                                           persona_files):
    """veto 源传播：delta/final 双零、点评照传；已判行（pm 自己早判的）绝不被覆盖。"""
    conn, fx, _run_am = conn_seeded
    assert apply_verdict(conn, fx, VETO) == 2
    run_pm = add_run(conn, phase="pm")
    add_rec(conn, run_pm, fx, "H", kelly=0.03, phase="pm")
    pre = add_rec(conn, run_pm, fx, "O2.5", kelly=0.02, phase="pm")
    conn.execute("UPDATE recommendations SET verdict='agree',"
                 " confidence_delta=0.05, final_stake_frac=0.021,"
                 " key_factors='[\"早判\"]', report_md='早' WHERE id=?", (pre,))

    monkeypatch.setenv("HERMES_BIN", str(fix / "hermes_ok"))
    out = run_persona_phase(conn, run_pm, ["D1"], attempted={fx})
    assert out["called"] == 0

    rows = {r["market"]: r for r in recs(conn, fx, run_pm)}
    assert rows["H"]["verdict"] == "veto"
    assert rows["H"]["confidence_delta"] == 0.0
    assert rows["H"]["final_stake_frac"] == 0.0
    assert rows["H"]["key_factors"] == '["a"]' and rows["H"]["report_md"] == "x"
    assert rows["O2.5"]["verdict"] == "agree"                    # 已判行不动
    assert rows["O2.5"]["final_stake_frac"] == 0.021
    assert rows["O2.5"]["key_factors"] == '["早判"]'
