"""orchestrate：mock runner 全链路（幂等续跑 + dsh 挂了 run 也不断）。"""
import json

import pytest

from fa.agentline import runner as runner_mod
from fa.agentline.orchestrate import run_line
from fa.db import connect, init_db


@pytest.fixture()
def db(tmp_path):
    init_db(tmp_path / "t.db")
    conn = connect(tmp_path / "t.db")
    conn.execute("PRAGMA foreign_keys=OFF")
    yield conn
    conn.close()


def test_run_line_full_chain(db, tmp_path, monkeypatch):
    info = {"match": {"league": "E0", "season": 2023, "date": "2024-02-01",
                      "home": "Arsenal", "away": "Chelsea"}, "odds": {}}
    (tmp_path / "10.json").write_text(json.dumps(info), encoding="utf-8")
    ok_out = ('{"p_home": 0.4, "p_draw": 0.3, "p_away": 0.3, "p_over25": 0.5,'
              ' "confidence": 0.5, "reasoning_digest": "r", "sources": []}')
    monkeypatch.setattr(runner_mod, "run_headless",
                        lambda p, prof, timeout_s=None: (ok_out, None, 0.1))
    counts = run_line(db, "A_base", tmp_path)
    assert counts["ok"] == 1
    row = db.execute("SELECT * FROM agentline_predictions").fetchone()
    assert row["status"] == "ok" and abs(row["p_home"] - 0.4) < 1e-9
    # 幂等：再跑不再处理已 ok 的场次
    assert run_line(db, "A_base", tmp_path)["ok"] == 0


def test_run_line_skips_non_numeric_json_stems(db, tmp_path, monkeypatch):
    """杂散 JSON（如 agent 写散的 notes.json）不得毁掉整批，也不得当成 match_id。"""
    info = {"match": {"league": "E0", "season": 2023, "date": "2024-02-01",
                      "home": "Arsenal", "away": "Chelsea"}, "odds": {}}
    (tmp_path / "10.json").write_text(json.dumps(info), encoding="utf-8")
    (tmp_path / "notes.json").write_text(json.dumps({"note": "x"}), encoding="utf-8")
    calls = []

    def fake_run(prompt, profile, timeout_s=None):
        calls.append(prompt)
        return ('{"p_home": 0.4, "p_draw": 0.3, "p_away": 0.3, "p_over25": 0.5,'
                ' "confidence": 0.5, "reasoning_digest": "r", "sources": []}',
                None, 0.1)

    monkeypatch.setattr(runner_mod, "run_headless", fake_run)
    counts = run_line(db, "A_base", tmp_path)
    assert counts["ok"] == 1 and len(calls) == 1          # 只处理数字命名的 10.json
    ids = [r["match_id"] for r in db.execute(
        "SELECT match_id FROM agentline_predictions")]
    assert ids == [10]


def test_run_line_degrades_when_dsh_dead(db, tmp_path, monkeypatch):
    (tmp_path / "10.json").write_text(
        json.dumps({"match": {"date": "2024-02-01"}, "odds": {}}),
        encoding="utf-8")
    monkeypatch.setattr(runner_mod, "run_headless",
                        lambda p, prof, timeout_s=None: (None, "可执行不存在：dsh", 0.0))
    counts = run_line(db, "A_base", tmp_path)         # 不抛
    assert counts["error"] == 1
    row = db.execute("SELECT * FROM agentline_predictions").fetchone()
    assert row["status"] == "error"


def test_run_line_crash_leaves_run_row_with_partial_counts(db, tmp_path, monkeypatch):
    """批中崩溃（如 info JSON 损坏致 json.loads 抛错）必须先留 run 台账（中断位
    + 已累计计数）再抛——先例 4a8b05a（retro 同型修复）；否则永远留下
    「predictions>0 且 runs=0」的无痕中断，与正常批中观察无法区分。"""
    info = {"match": {"league": "E0", "season": 2023, "date": "2024-02-01",
                      "home": "Arsenal", "away": "Chelsea"}, "odds": {}}
    (tmp_path / "10.json").write_text(json.dumps(info), encoding="utf-8")
    (tmp_path / "11.json").write_text("{损坏:非 JSON", encoding="utf-8")
    ok_out = ('{"p_home": 0.4, "p_draw": 0.3, "p_away": 0.3, "p_over25": 0.5,'
              ' "confidence": 0.5, "reasoning_digest": "r", "sources": []}')
    monkeypatch.setattr(runner_mod, "run_headless",
                        lambda p, prof, timeout_s=None: (ok_out, None, 0.1))
    with pytest.raises(json.JSONDecodeError):
        run_line(db, "A_base", tmp_path)              # 异常照抛（不吞）
    run_row = db.execute("SELECT * FROM agentline_runs").fetchone()
    assert run_row is not None                        # 台账已留
    assert run_row["n_ok"] == 1                       # 已完成场次如实计数
    summary = json.loads(run_row["summary"])
    assert summary["interrupted_match_id"] == 11      # 中断位可定位续跑


def test_run_started_at_is_batch_start_not_end(db, tmp_path, monkeypatch):
    """台账 started_at 须为批次起点而非批尾 save_run 时刻（2026-09-04 实测：
    04:25 启动的 100 场批次会把 ~05:07 的批尾时刻记成 started_at，起点丢失）。"""
    import time as _time
    from datetime import datetime, timezone
    info = {"match": {"league": "E0", "season": 2023, "date": "2024-02-01",
                      "home": "Arsenal", "away": "Chelsea"}, "odds": {}}
    for mid in (10, 11, 12):
        (tmp_path / f"{mid}.json").write_text(json.dumps(info), encoding="utf-8")
    ok_out = ('{"p_home": 0.4, "p_draw": 0.3, "p_away": 0.3, "p_over25": 0.5,'
              ' "confidence": 0.5, "reasoning_digest": "r", "sources": []}')
    first_call = {}

    def fake_run(prompt, profile, timeout_s=None):
        first_call.setdefault("t", _time.time())
        if len(first_call) == 1:               # 首场拉出 >1s 的确定性秒差：
            _time.sleep(1.05)                  # _now() 秒级截断下，批尾时刻必然
        return (ok_out, None, 0.1)             # 落在首调用的下一秒及以后

    monkeypatch.setattr(runner_mod, "run_headless", fake_run)
    run_line(db, "A_base", tmp_path)
    row = db.execute("SELECT * FROM agentline_runs").fetchone()
    started = datetime.fromisoformat(row["started_at"])
    fc = datetime.fromtimestamp(first_call["t"], tz=timezone.utc)
    assert started <= fc                    # 起点早于第一次 dsh 调用，非批尾时刻


def test_cli_run_rejects_bad_line():
    from typer.testing import CliRunner
    from fa.cli import app
    res = CliRunner().invoke(app, ["agentline", "run", "--line", "B_line"])
    assert res.exit_code == 2


# ---- A_multi（multi-brain）：run_multi 编排 ---------------------------------
# 夹具沿用本文件既有 db 夹具的造数方式（foreign_keys=OFF，无需 backtest 行）。


@pytest.fixture()
def conn(tmp_path):
    init_db(tmp_path / "multi.db")
    c = connect(tmp_path / "multi.db")
    c.execute("PRAGMA foreign_keys=OFF")
    yield c
    c.close()


@pytest.fixture()
def info_dir(tmp_path):
    d = tmp_path / "info"
    d.mkdir()
    info = {"match": {"league": "E0", "season": 2023, "date": "2024-02-01",
                      "home": "Arsenal", "away": "Chelsea"}, "odds": {}}
    (d / "10.json").write_text(json.dumps(info), encoding="utf-8")
    return d


_AM_OK = json.dumps({"p_home": 0.4, "p_draw": 0.3, "p_away": 0.3,
                     "p_over25": 0.5, "confidence": 0.6,
                     "reasoning_digest": "m", "sources": []})
_AM_OK2 = json.dumps({"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2,
                      "p_over25": 0.6, "confidence": 0.7,
                      "reasoning_digest": "n", "sources": []})


def test_run_multi_members_and_aggregate(conn, info_dir, monkeypatch):
    """3 成员行（attributor 1..3）+ 1 聚合行（attributor 0，中位数概率）。"""
    from fa.agentline import orchestrate
    outs = iter([_AM_OK, _AM_OK, _AM_OK2])
    calls = []

    def fake_run(prompt, profile, timeout_s=None):
        calls.append((prompt, profile))
        return (next(outs), None, 1.0)

    monkeypatch.setattr(orchestrate.runner_mod, "run_headless", fake_run)
    # 台账起点＝批起点（终审 Important #2 微任务，run_line 同式）：递增时钟
    # 钉死 started_at 取 run_multi 开头时刻，而非批尾 save_run 落库时刻。
    from fa.agentline import store as store_mod
    clock = iter(f"2026-09-05T00:00:{i:02d}+00:00" for i in range(99))
    monkeypatch.setattr(store_mod, "_now", lambda: next(clock))
    counts = orchestrate.run_multi(conn, info_dir, members=3, limit=1)
    assert counts == {"ok": 1, "parse_fail": 0, "timeout": 0, "error": 0}
    rows = conn.execute(
        "SELECT attributor, status, p_home FROM agentline_predictions"
        " WHERE line='A_multi' ORDER BY attributor").fetchall()
    assert [r["attributor"] for r in rows] == [0, 1, 2, 3]
    assert rows[0]["p_home"] == pytest.approx(0.4)   # 中位数（0.4,0.4,0.5）
    # spec 不变量（AM-T3 审查裁定顺入）：成员必须 A_base profile 且 prompt 无检索
    # ——两者同源（A_enh profile 会带「网络检索」后缀），一并钉死防回归。
    assert len(calls) == 3
    assert {profile for _, profile in calls} == {"fa-agent-base"}
    assert all("网络检索" not in prompt for prompt, _ in calls)
    run_row = conn.execute("SELECT started_at, finished_at FROM agentline_runs"
                           " WHERE line='A_multi'").fetchone()
    assert run_row["started_at"] == "2026-09-05T00:00:00+00:00"   # 批首时钟
    assert run_row["finished_at"] > run_row["started_at"]          # 批尾≠起点


def test_run_multi_all_failed_aggregate_error(conn, info_dir, monkeypatch):
    from fa.agentline import orchestrate
    monkeypatch.setattr(orchestrate.runner_mod, "run_headless",
                        lambda prompt, profile, timeout_s=None:
                        (None, "dsh 退出码 1：boom", 0.5))
    counts = orchestrate.run_multi(conn, info_dir, members=3, limit=1)
    assert counts["error"] == 1
    agg = conn.execute(
        "SELECT status, p_home FROM agentline_predictions"
        " WHERE line='A_multi' AND attributor=0").fetchone()
    assert agg["status"] == "error" and agg["p_home"] is None


def test_run_multi_idempotent_skips_done(conn, info_dir, monkeypatch):
    from fa.agentline import orchestrate
    calls = {"n": 0}

    def fake(prompt, profile, timeout_s=None):
        calls["n"] += 1
        return (_AM_OK, None, 1.0)

    monkeypatch.setattr(orchestrate.runner_mod, "run_headless", fake)
    orchestrate.run_multi(conn, info_dir, members=3, limit=1)
    first = calls["n"]
    orchestrate.run_multi(conn, info_dir, members=3, limit=1)   # 聚合已 ok → 跳
    assert calls["n"] == first


def test_cli_run_rejects_members_lt_two():
    """A_multi 低于两员不是 ensemble——入口即拒（不碰库）。"""
    from typer.testing import CliRunner
    from fa.cli import app
    res = CliRunner().invoke(app, ["agentline", "run", "--line", "A_multi",
                                   "--members", "1"])
    assert res.exit_code == 2


# ---- A_debate（生成者-批评者-修订）：run_debate 批编排 -----------------------
# 夹具沿用本文件既有 conn/info_dir（run_multi 段）；OK0/ATK 字面量与
# tests/agentline/test_debate.py 同源复制（同一份 ok 预测/攻击语义）。


_DEB_OK0 = json.dumps({"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2,
                       "p_over25": 0.5, "confidence": 0.6,
                       "reasoning_digest": "d", "sources": []})
_DEB_ATK = json.dumps({"attacks": [{"label": "overconfidence", "reason": "r",
                                    "severity": 0.8}]})


def test_run_debate_happy_and_idempotent(conn, info_dir, monkeypatch):
    from fa.agentline.orchestrate import run_debate
    seq = [_DEB_OK0, _DEB_ATK, "垃圾"]   # v0 ok → 批评 ok → 修订失败 → 终版=v0
    profiles = []

    def fake_run(prompt, profile, timeout_s=None):
        profiles.append(profile)
        return (seq.pop(0), None, 0.01)

    monkeypatch.setattr(runner_mod, "run_headless", fake_run)
    counts = run_debate(conn, info_dir, limit=None)
    assert counts == {"ok": 1, "parse_fail": 0, "timeout": 0, "error": 0}
    # profile 透传（run_multi 同款不变量）：生成/批评/修订三次调用全走 A_base
    assert len(profiles) == 3
    assert set(profiles) == {"fa-agent-base"}
    row = conn.execute("SELECT budget_exhausted, p_home FROM"
                       " agentline_predictions WHERE line='A_debate'"
                       " AND attributor=1").fetchone()
    assert row["budget_exhausted"] == 1 and abs(row["p_home"] - 0.5) < 1e-9
    assert conn.execute("SELECT COUNT(*) c FROM"
                        " agentline_debate_rounds").fetchone()["c"] == 3
    run2 = conn.execute("SELECT COUNT(*) c FROM agentline_runs"
                        " WHERE line='A_debate'").fetchone()["c"]
    # 幂等重跑：ok 行已存在 → 无新调用、无新轮行（run 台账照记）
    counts2 = run_debate(conn, info_dir)
    assert counts2["ok"] == 0
    assert len(seq) == 0                # 首跑恰好消费 3 次调用，无多余 dsh 调用
    assert conn.execute("SELECT COUNT(*) c FROM"
                        " agentline_debate_rounds").fetchone()["c"] == 3
    assert conn.execute("SELECT COUNT(*) c FROM agentline_runs"
                        " WHERE line='A_debate'").fetchone()["c"] == run2 + 1


def test_run_debate_summary_carries_budget_and_early_stop(conn, info_dir,
                                                          monkeypatch):
    """runs.summary 须含批级 n_calls / budget_exhausted / early_stop 计数。"""
    from fa.agentline import orchestrate
    seq = [_DEB_OK0, _DEB_ATK, _DEB_OK0]     # v1==v0：delta 0 < ε → 提前终止
    monkeypatch.setattr(orchestrate.runner_mod, "run_headless",
                        lambda p, prof, timeout_s=None: (seq.pop(0), None, 0.01))
    counts = orchestrate.run_debate(conn, info_dir, limit=None)
    assert counts == {"ok": 1, "parse_fail": 0, "timeout": 0, "error": 0}
    run_row = conn.execute("SELECT * FROM agentline_runs"
                           " WHERE line='A_debate'").fetchone()
    summary = json.loads(run_row["summary"])
    assert summary["n_calls"] == 3 and summary["early_stop"] == 1
    assert summary["budget_exhausted"] == 0
    assert summary["n_todo"] == 1
    pred = conn.execute("SELECT budget_exhausted FROM agentline_predictions"
                        " WHERE line='A_debate'").fetchone()
    assert pred["budget_exhausted"] == 0


def test_run_debate_crash_leaves_run_row_with_partial_counts(conn, info_dir,
                                                             monkeypatch):
    """批中崩溃（info JSON 损坏致 json.loads 抛错）也必须先留 run 台账（中断位
    + 已累计计数与批级 n_calls）再抛——run_line 同型（先例 4a8b05a），否则留下
    「predictions>0 且 runs=0」的无痕中断。"""
    from fa.agentline.orchestrate import run_debate
    (info_dir / "11.json").write_text("{损坏:非 JSON", encoding="utf-8")
    seq = [_DEB_OK0, _DEB_ATK, _DEB_OK0]     # 场 10 完整走完一轮辩论链
    monkeypatch.setattr(runner_mod, "run_headless",
                        lambda p, prof, timeout_s=None: (seq.pop(0), None, 0.01))
    with pytest.raises(json.JSONDecodeError):
        run_debate(conn, info_dir)                    # 异常照抛（不吞）
    assert seq == []                                  # 场 10 的 3 次调用已发生
    run_row = conn.execute("SELECT * FROM agentline_runs"
                           " WHERE line='A_debate'").fetchone()
    assert run_row is not None                        # 台账已留
    assert run_row["n_ok"] == 1                       # 已完成场次如实计数
    summary = json.loads(run_row["summary"])
    assert summary["interrupted_match_id"] == 11      # 中断位可定位续跑
    assert summary["n_calls"] == 3 and summary["n_todo"] == 2   # 批级计数照记
