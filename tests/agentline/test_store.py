"""store：UNIQUE(match_id,line) 幂等覆盖。"""
import pytest

from fa.agentline.contract import parse_prediction
from fa.agentline.store import save_prediction, save_run
from fa.db import connect, init_db


@pytest.fixture()
def db(tmp_path):
    init_db(tmp_path / "t.db")
    conn = connect(tmp_path / "t.db")
    conn.execute("PRAGMA foreign_keys=OFF")
    yield conn
    conn.close()


def _parsed(raw='{"p_home": 0.4, "p_draw": 0.3, "p_away": 0.3,'
                ' "p_over25": 0.5, "confidence": 0.5,'
                ' "reasoning_digest": "x", "sources": []}'):
    return parse_prediction(raw)


def test_save_prediction_upsert(db):
    assert save_prediction(db, 10, "A_base", _parsed(), "raw", "dsh 0.1.2",
                           "flash", 1.5) > 0
    n = save_prediction(db, 10, "A_base", _parsed(), "raw2", "dsh 0.1.2",
                        "flash", 1.6)          # 同 (match,line) → UPDATE
    rows = db.execute("SELECT * FROM agentline_predictions").fetchall()
    assert len(rows) == 1 and rows[0]["raw_output"] == "raw2"


def test_save_run_counts(db):
    rid = save_run(db, "A_base", "fa-agent-base", "flash",
                   {"ok": 3, "parse_fail": 1, "timeout": 0, "error": 0},
                   {"league": "E0"})
    row = db.execute("SELECT * FROM agentline_runs WHERE id=?", (rid,)).fetchone()
    assert row["n_ok"] == 3 and row["n_parse_fail"] == 1
