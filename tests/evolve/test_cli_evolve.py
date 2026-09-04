"""fa evolve 七命令：参数校验/输出形态/关卡操作接线。

夹具与 ``test_runner.py`` 同一套（``root``/``conn`` 就地定义、有意盖过
conftest 同名 ``conn``——CLI 进程级隔离靠 ``FA_DB``，CLI 的 ``connect()`` /
app callback 的 ``init_db()`` 都读它；``project_root`` 重定向防
``evolve status`` 摸真仓库的 personas 树）。
"""
import json

import pytest
from typer.testing import CliRunner

from fa import config
from fa.cli import app
from fa.db import connect, init_db
from fa.evolve import EvolutionError

CONTRACT = {"league": "E0",
            "appends": [{"section": "时效",
                         "text": "某队主力门将复出在即，前场压迫强度回升",
                         "date": "2026-10-16", "ttl_days": 90,
                         "evidence": {"fixtures": [1],
                                      "stat": "误杀对照 ROI +1.2（1 注）"}}],
            "amendments": [], "deprecations": [], "no_change_reason": None}


@pytest.fixture
def root(tmp_path, monkeypatch):
    """``fa.config.project_root`` → tmp（status 读树/merge 写文件均落 tmp）。"""
    (tmp_path / "personas" / "knowledge").mkdir(parents=True)
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def conn(root, tmp_path, monkeypatch):
    """tmp 库 + FA_DB 指向它（CLI 的 connect()/init_db() 都走 FA_DB）。"""
    p = tmp_path / "cli.db"
    monkeypatch.setenv("FA_DB", str(p))
    init_db(p)
    c = connect(p)
    yield c
    c.close()


@pytest.fixture
def smoke_db(root, tmp_path, monkeypatch):
    """空库（仅 schema）+ FA_DB——status 的空态与 app callback 自迁移路径。"""
    p = tmp_path / "smoke.db"
    monkeypatch.setenv("FA_DB", str(p))
    init_db(p)
    return p


@pytest.fixture
def conn_with_proposal(smoke_db, root):
    """w1 窗行 + E0 ok 反思行 + 已暂存提案（merge/reject/shelve 的完整前置）。"""
    prop_dir = root / "evolution" / "proposals" / "w1"
    prop_dir.mkdir(parents=True)
    (prop_dir / "E0.json").write_text(json.dumps(CONTRACT, ensure_ascii=False),
                                      encoding="utf-8")
    c = connect(smoke_db)
    try:
        wid = c.execute(
            "INSERT INTO evolution_windows (idx, opened_at, closes_at)"
            " VALUES (1, '2026-09-04', '2026-10-16')").lastrowid
        c.execute(
            "INSERT INTO evolution_runs (window_id, league, kb_hash_before,"
            " status, no_change_reason, proposal_path, duration_s, created_at)"
            " VALUES (?, 'E0', 'h', 'ok', NULL, ?, 0.1, '2026-10-16T00:00:00Z')",
            (wid, "evolution/proposals/w1/E0.json"))
        c.commit()
    finally:
        c.close()
    return smoke_db


def test_evolve_status_empty_db(smoke_db):
    result = CliRunner().invoke(app, ["evolve", "status"])
    assert result.exit_code == 0
    assert "当前窗" in result.output


def test_evolve_merge_requires_note(conn_with_proposal):
    result = CliRunner().invoke(
        app, ["evolve", "merge", "--window", "1", "--league", "E0",
              "--note", " "])
    assert result.exit_code != 0 and "理由" in result.output


def test_evolve_tick_command_wires_runner(conn, monkeypatch):
    monkeypatch.setattr("fa.evolve.runner.run_tick", lambda c: "tick 摘要")
    result = CliRunner().invoke(app, ["evolve", "tick"])
    assert result.exit_code == 0 and "tick 摘要" in result.output


def test_evolve_tick_evolution_error_is_exit_1(conn, monkeypatch):
    """M6 终审 F1：tick 的确定性失败（EvolutionError）→ 人读一行 + Exit(1)，
    cron 告警靠退出码（§9.6），不裸抛 traceback。"""

    def boom(c):
        raise EvolutionError("w2 关卡状态矛盾（注入）")

    monkeypatch.setattr("fa.evolve.runner.run_tick", boom)
    result = CliRunner().invoke(app, ["evolve", "tick"])
    assert result.exit_code == 1
    assert "tick 失败" in result.output and "w2 关卡状态矛盾" in result.output
    assert "Traceback" not in result.output


def test_evolve_merge_missing_staged_proposed_is_exit_1(conn_with_proposal, root):
    """M6 终审 F6：暂存 .proposed.md 缺失（proposal_path 在）→ 关卡拒绝走
    EvolutionError 人读出口，Exit(1) 且无 traceback。夹具只暂存了 E0.json，
    ``.proposed.md`` 本就不在盘上——正是护栏失效关闭要拦的形态。"""
    assert not (root / "evolution" / "proposals" / "w1" / "E0.proposed.md").exists()
    result = CliRunner().invoke(
        app, ["evolve", "merge", "--window", "1", "--league", "E0",
              "--note", "裁定通过"])
    assert result.exit_code == 1
    assert "失效关闭" in result.output
    assert "Traceback" not in result.output


def test_evolve_merge_missing_staged_json_is_exit_1(conn_with_proposal, root):
    """M6 终审 F6：proposal_path 指着的 json 被外力删除 → FileNotFoundError
    不再裸抛——人读一行 + Exit(1)。"""
    (root / "evolution" / "proposals" / "w1" / "E0.json").unlink()
    result = CliRunner().invoke(
        app, ["evolve", "merge", "--window", "1", "--league", "E0",
              "--note", "裁定通过"])
    assert result.exit_code == 1
    assert "暂存工件不可读" in result.output
    assert "Traceback" not in result.output
