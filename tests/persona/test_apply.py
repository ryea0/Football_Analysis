"""apply 测试：§6.4 映射 / 场次广播 / veto 语义 / 阶段编排（fixture 脚本走真子进程）。

种子语义：一 fixture（D1，主客已对齐）的 model_persona 轨两行（H kelly=0.02、
O2.5 kelly=0.01，挂在**同一个 run**——一次管线执行产出一个 phase 的全部行；
``run_persona_phase`` 靠 run_id 选场，``build_input`` 靠 (fixture_id, run_id)
取本窗行）。pm 传播用例另插 phase='pm' 的第二 run。

M6（§12.7）双轨：``run_persona_phase`` 每场两轨两次调用（kb→nokb 顺序固定），
nokb 轨行由 ``add_rec(..., strategy=NOKB_STRATEGY)`` 种下（model_only 与 persona
阶段无关，不种）；知识快照走 ``config.project_root()`` 属性访问，autouse 夹具
把 root 指到 tmp（同 test_matchday._isolate_root 的缝），测试产物不落真仓库。

persona 文件指针：真实 ``personas/*.md`` 由 T9/T10 落盘，本层把
``fa.persona.apply.persona_path`` 指向 tmp——fake 保留 config 的两条真实语义
（league 不在映射 → ValueError、文件缺失 → FileNotFoundError），hermes 仍走
fixture 脚本真子进程（C1：mock 与实跑同一代码路径，不破）。
"""
from datetime import date
from pathlib import Path

import pytest

from fa.db import connect, init_db
from fa.evolve import windows as _windows
from fa.persona import apply as apply_mod
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


_FIXTURE_OK = FIXTURES / "hermes_ok"


@pytest.fixture(autouse=True)
def _isolated_root(tmp_path, monkeypatch):
    """M6 起 ``run_persona_phase`` 一进来就写本窗知识快照（``config.project_root()``
    定位）——指到 tmp，测试产物不落真仓库（knowledge 缝：属性访问，打一点即全隔离）。"""
    from fa import config

    monkeypatch.setattr(config, "project_root", lambda: tmp_path)


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


def add_rec(c, run_id, fixture_id, market, kelly=0.02, phase="am",
            strategy="model_persona"):
    return c.execute(
        "INSERT INTO recommendations (run_id, fixture_id, strategy, market, phase,"
        " model_p, market_p, best_odds, bookmaker, edge, ev, kelly_stake_frac,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, fixture_id, strategy, market, phase, 0.5, 0.4, 2.0,
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
    """persona 文件指向 tmp：映射语义照 config（league 不在映射 → ValueError），
    已映射联赛指向 tmp 里的同名文件——用例写盘哪个联赛，哪个联赛就能点评
    （D1 预置；E0 等由双轨用例自写）。返回目录（用例可删文件造 FileNotFoundError）。"""
    from fa.config import PERSONA_FILES
    import fa.persona.apply as apply_mod

    d = tmp_path / "personas"
    d.mkdir()
    (d / "bundesliga.md").write_text(
        "# 德甲人格\n\n你是一名谨慎的德甲点评员，只依据本场输入发言。\n",
        encoding="utf-8")

    def fake_persona_path(league):
        if league not in PERSONA_FILES:
            raise ValueError(f"无 persona 文件映射：{league!r}")
        return d / PERSONA_FILES[league]

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


def test_phase_degrades_unaligned_fixture_per_track(conn_seeded, monkeypatch, fix,
                                                    persona_files):
    """``build_input`` 的 §6.3 契约缺口（主客未对齐）＝场次级降级（§6.5）：两轨
    同降该场（reason unknown，同 pre-M6 语义）、不炸 phase、不吞 commit——
    余下场两轨照常判决落库。"""
    conn, fx, run_id = conn_seeded
    _seed_nokb(conn, run_id, fx)
    bad = conn.execute(
        "INSERT INTO fixtures (league, event_key, source, kickoff_utc,"
        " home_team_id, away_team_id, status, created_at)"
        " VALUES ('D1','ev-d1-bad','oddsapi',?,NULL,NULL,'scheduled',"
        " '2026-09-01T08:00:00Z')", (KICKOFF,)).lastrowid
    add_rec(conn, run_id, bad, "H", kelly=0.01)                  # kb 轨
    _seed_nokb(conn, run_id, bad, kelly=0.01)
    monkeypatch.setenv("HERMES_BIN", str(fix / "hermes_ok"))
    out = run_persona_phase(conn, run_id, ["D1"])
    assert out["ok"] == 1 and out["nokb_ok"] == 1                # 健康场两轨照判
    assert out["degraded"] == [{"fixture_id": bad, "reason": "unknown"}]
    assert out["nokb_degraded"] == [{"fixture_id": bad, "reason": "unknown"}]
    assert out["attempted"] == sorted([fx, bad])
    assert all(r["verdict"] is None for r in recs(conn, bad))    # 中性保持
    assert _track_row(conn, fx, run_id, apply_mod.STRATEGY)["verdict"] == "agree"
    assert _track_row(conn, fx, run_id,
                      apply_mod.NOKB_STRATEGY)["verdict"] == "agree"  # 已落库


def test_phase_degrades_unmapped_league_as_persona_file(conn_seeded, monkeypatch,
                                                        fix, persona_files):
    """fixtures 表里 league 不在 PERSONA_FILES 映射 → config 抛 ValueError，
    归 persona_file 类降级（人格文件缺失的一种），不得炸整个 phase。"""
    conn, fx, run_id = conn_seeded
    other = add_fixture(conn, "ev-it1-x", "Juventus", "Napoli", league="IT1")
    add_rec(conn, run_id, other, "H", kelly=0.01)
    monkeypatch.setenv("HERMES_BIN", str(fix / "hermes_ok"))
    out = run_persona_phase(conn, run_id, ["D1", "IT1"])
    assert out["ok"] == 1 and out["degraded"] == [{"fixture_id": other,
                                                  "reason": "persona_file"}]
    assert out["attempted"] == sorted([fx, other])


def test_phase_degrades_all_when_snapshot_unbuildable(conn_seeded, monkeypatch,
                                                      fix, persona_files):
    """M6 终审 F3（相级缝）：``ensure_current_snapshot`` 抛 OSError → 该相全部
    待判场两轨同降 reason='kb_snapshot'，verdict 保持 NULL、零调用、不抛——
    matchday phase 不被一个知识快照 IO 故障炸掉（§6.5 降级语义）。"""
    conn, fx, run_id = conn_seeded
    _seed_nokb(conn, run_id, fx)

    def boom():
        raise OSError("disk gone")

    monkeypatch.setattr(apply_mod, "ensure_current_snapshot", boom)
    monkeypatch.setenv("HERMES_BIN", str(fix / "hermes_ok"))
    out = run_persona_phase(conn, run_id, ["D1"])
    assert out["called"] == 0 and out["nokb_called"] == 0     # 未触达调用
    assert out["degraded"] == [{"fixture_id": fx, "reason": "kb_snapshot"}]
    assert out["nokb_degraded"] == [{"fixture_id": fx, "reason": "kb_snapshot"}]
    assert out["attempted"] == [fx]                            # 记账契约不变
    assert all(r["verdict"] is None for r in recs(conn, fx))
    assert all(r["verdict"] is None for r in conn.execute(
        "SELECT verdict FROM recommendations"
        " WHERE strategy='model_persona_nokb'").fetchall())


def test_phase_degrades_fixture_when_snapshot_unreadable(conn_seeded, monkeypatch,
                                                         fix, persona_files):
    """M6 终审 F3（场级缝）：``window_kb_text`` 抛 OSError → 该场两轨同降
    reason='kb_snapshot'，其余逻辑照走、不抛（快照目录被外力动过的真实形态）。"""
    conn, fx, run_id = conn_seeded
    _seed_nokb(conn, run_id, fx)

    def boom(idx, league):
        raise OSError("snapshot vanished")

    monkeypatch.setattr(apply_mod, "window_kb_text", boom)
    monkeypatch.setenv("HERMES_BIN", str(fix / "hermes_ok"))
    out = run_persona_phase(conn, run_id, ["D1"])
    assert out["called"] == 0 and out["nokb_called"] == 0
    assert out["degraded"] == [{"fixture_id": fx, "reason": "kb_snapshot"}]
    assert out["nokb_degraded"] == [{"fixture_id": fx, "reason": "kb_snapshot"}]
    assert all(r["verdict"] is None for r in recs(conn, fx))
    assert all(r["verdict"] is None for r in conn.execute(
        "SELECT verdict FROM recommendations"
        " WHERE strategy='model_persona_nokb'").fetchall())


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


# ------------------------------------------------- 双轨调用与知识注入（M6 §12.7）

def _dual_track_hermes(tmp_path):
    """可编程 HERMES_BIN：按**调用序**分档（kb→nokb 顺序固定），prompt 落盘、
    回固定合法输出。用序不用内容分档——无知识库时期两条 prompt 都无 KB 段。
    prompt 在 ``$2``（命令形态 ``hermes -z <prompt> -t search``，T2 钉死）。
    仅单被处理场用例适用（n=1 即 kb 轨）；多场需改 ``n % 2`` 奇偶分档。"""
    script = tmp_path / "hermes_dual.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'n=$(cat "$SEQ_FILE" 2>/dev/null || echo 0); n=$((n+1));'
        ' echo $n > "$SEQ_FILE"\n'
        'if [ "$n" = 1 ]; then printf \'%s\' "$2" > "$KB_FILE";'
        ' else printf \'%s\' "$2" > "$NOKB_FILE"; fi\n'
        'sh "' + str(_FIXTURE_OK) + '"\n')
    script.chmod(0o755)
    return script


def _seq_hermes(tmp_path, second):
    """按调用序换输出的 HERMES_BIN：第 1 次（kb 轨）走 hermes_ok，第 2 次
    （nokb 轨）跑 ``second`` fixture 脚本（timeout / veto 等分档场景）。"""
    script = tmp_path / "hermes_seq.sh"
    script.write_text("#!/usr/bin/env bash\n"
                      'n=$(cat "$SEQ_FILE" 2>/dev/null || echo 0); n=$((n+1));'
                      ' echo $n > "$SEQ_FILE"\n'
                      'if [ "$n" = 1 ]; then sh "' + str(_FIXTURE_OK) + '";'
                      " else " + second + "; fi\n")
    script.chmod(0o755)
    return script


_VETO_JSON = ('{"verdict":"veto","confidence_delta":0.1,'
              '"key_factors":["k"],"report_md":"r"}')


def _seed_nokb(c, run_id, fixture_id, kelly=0.02, phase="am", markets=("H",)):
    """给 nokb 轨种行（数字与 kb 轨同——value 三轨落行，数字全同）。"""
    for m in markets:
        add_rec(c, run_id, fixture_id, m, kelly=kelly, phase=phase,
                strategy=apply_mod.NOKB_STRATEGY)


def _track_row(c, fixture_id, run_id, strategy, market="H"):
    return c.execute(
        "SELECT * FROM recommendations WHERE fixture_id=? AND run_id=?"
        " AND strategy=? AND market=?", (fixture_id, run_id, strategy,
                                         market)).fetchone()


def test_run_persona_phase_dual_track_kb_only_in_kb_prompt(conn_seeded, tmp_path,
                                                           monkeypatch,
                                                           persona_files):
    """kb 轨 prompt 含知识库段、nokb 轨不含；两轨各得判决行；
    summary 原键 = kb 轨计数 + nokb_* 镜像键。"""
    conn, _fx, run_id = conn_seeded
    fid = add_fixture(conn, "ev-e0-kb", "Chelsea", "Arsenal", league="E0")
    (persona_files / "epl.md").write_text(
        "# 英超人格\n\n你是一名谨慎的英超点评员。\n", encoding="utf-8")
    add_rec(conn, run_id, fid, "H", kelly=0.02)                  # kb 轨
    add_rec(conn, run_id, fid, "O2.5", kelly=0.01)
    _seed_nokb(conn, run_id, fid, kelly=0.02, markets=("H", "O2.5"))
    # 预置 w1 知识快照（含一条 E0 条目）；today 钉在 2026-09-10 → 窗 1（锚点 09-04）
    snap = tmp_path / "evolution" / "snapshots" / "w1"
    snap.mkdir(parents=True)
    (snap / "epl.md").write_text("## 教训\n- [E0-L01|x]\n", encoding="utf-8")
    monkeypatch.setattr(_windows, "beijing_today", lambda: date(2026, 9, 10))
    monkeypatch.setenv("HERMES_BIN", str(_dual_track_hermes(tmp_path)))
    monkeypatch.setenv("SEQ_FILE", str(tmp_path / "seq"))
    monkeypatch.setenv("KB_FILE", str(tmp_path / "kb.prompt"))
    monkeypatch.setenv("NOKB_FILE", str(tmp_path / "nokb.prompt"))
    out = run_persona_phase(conn, run_id, ["E0"])
    kb_prompt = (tmp_path / "kb.prompt").read_text(encoding="utf-8")
    nokb_prompt = (tmp_path / "nokb.prompt").read_text(encoding="utf-8")
    assert "联赛知识库" in kb_prompt and "E0-L01" in kb_prompt
    assert "联赛知识库" not in nokb_prompt
    assert out["called"] == 1 and out["nokb_called"] == 1
    # 两轨 verdict 均落库（各自 strategy 行）
    for strategy in ("model_persona", "model_persona_nokb"):
        row = conn.execute(
            "SELECT verdict FROM recommendations WHERE fixture_id=? AND strategy=?",
            (fid, strategy)).fetchone()
        assert row["verdict"] == "agree"


def test_run_persona_phase_degraded_per_track(conn_seeded, tmp_path, monkeypatch,
                                              persona_files):
    """nokb 轨超时只降 nokb（degraded 按 track 分账），kb 轨照常。"""
    conn, fid, run_id = conn_seeded
    _seed_nokb(conn, run_id, fid)
    script = tmp_path / "sleep_30"
    script.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("HERMES_BIN",
                       str(_seq_hermes(tmp_path, 'sh "' + str(script) + '"')))
    monkeypatch.setenv("SEQ_FILE", str(tmp_path / "seq"))
    monkeypatch.setenv("FA_PERSONA_TIMEOUT", "0.5")
    out = run_persona_phase(conn, run_id, ["D1"])
    assert out["ok"] == 1 and out["nokb_ok"] == 0
    assert out["nokb_degraded"][0]["reason"] == "timeout"
    assert out["degraded"] == []                     # kb 轨完好，不陪葬
    assert _track_row(conn, fid, run_id, apply_mod.STRATEGY)["verdict"] == "agree"
    assert _track_row(conn, fid, run_id,
                      apply_mod.NOKB_STRATEGY)["verdict"] is None


def test_nokb_veto_zeroes_only_nokb_rows(conn_seeded, tmp_path, monkeypatch,
                                         persona_files):
    """nokb 轨 veto 双零**按轨**生效：nokb 行 final=0（paper 层 frac<=0 不落注），
    kb 轨 agree 行照常乘 delta，不陪葬。"""
    conn, fid, run_id = conn_seeded
    _seed_nokb(conn, run_id, fid)
    monkeypatch.setenv("HERMES_BIN",
                       str(_seq_hermes(tmp_path, "echo '" + _VETO_JSON + "'")))
    monkeypatch.setenv("SEQ_FILE", str(tmp_path / "seq"))
    out = run_persona_phase(conn, run_id, ["D1"])
    assert out["veto"] == 0 and out["nokb_veto"] == 1
    kb = _track_row(conn, fid, run_id, apply_mod.STRATEGY)
    assert kb["verdict"] == "agree"
    assert kb["final_stake_frac"] == pytest.approx(0.02 * 0.95, abs=1e-9)
    nokb = _track_row(conn, fid, run_id, apply_mod.NOKB_STRATEGY)
    assert nokb["verdict"] == "veto"
    assert nokb["final_stake_frac"] == 0.0
    assert nokb["confidence_delta"] == 0.0


def test_propagate_verdict_per_strategy(conn_seeded, monkeypatch, fix,
                                        persona_files):
    """pm 沿用 am 按轨传播：kb 判决传播 kb 行、nokb 传播 nokb 行，互不串轨。"""
    conn, fx, run_am = conn_seeded
    _seed_nokb(conn, run_am, fx, kelly=0.02, markets=("H", "O2.5"))
    assert apply_verdict(conn, fx, AGREE) == 2                       # kb 轨 agree
    assert apply_verdict(conn, fx, VETO,
                         strategy=apply_mod.NOKB_STRATEGY) == 2      # nokb 轨 veto
    run_pm = add_run(conn, phase="pm")
    add_rec(conn, run_pm, fx, "H", kelly=0.03, phase="pm")
    _seed_nokb(conn, run_pm, fx, kelly=0.03, phase="pm")

    monkeypatch.setenv("HERMES_BIN", str(fix / "hermes_ok"))
    out = run_persona_phase(conn, run_pm, ["D1"], attempted={fx})
    assert out["called"] == 0 and out["nokb_called"] == 0            # 零调用

    kb = _track_row(conn, fx, run_pm, apply_mod.STRATEGY)
    assert kb["verdict"] == "agree" and kb["report_md"] == "x"
    assert kb["final_stake_frac"] == pytest.approx(0.03 * 1.1, abs=1e-9)
    nokb = _track_row(conn, fx, run_pm, apply_mod.NOKB_STRATEGY)
    assert nokb["verdict"] == "veto"                             # 不串轨：nokb 不吃 agree
    assert nokb["confidence_delta"] == 0.0
    assert nokb["final_stake_frac"] == 0.0
    assert nokb["key_factors"] == '["a"]' and nokb["report_md"] == "x"
