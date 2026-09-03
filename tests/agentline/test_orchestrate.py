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


def test_cli_run_rejects_bad_line():
    from typer.testing import CliRunner
    from fa.cli import app
    res = CliRunner().invoke(app, ["agentline", "run", "--line", "B_line"])
    assert res.exit_code == 2
