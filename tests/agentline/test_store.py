"""store：UNIQUE(match_id,line) 幂等覆盖。"""
import pytest

from fa.agentline.contract import parse_prediction
from fa.agentline.store import (save_debate_round, save_division_jump,
                                save_prediction, save_run)
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


def test_save_prediction_attributor_members_coexist(db):
    """v7 三元组 UNIQUE + attributor 参数（A_multi 计划 Task 1 接口）：同 match
    同 line 下聚合 0 与成员 1..3 四行共存，幂等覆盖按 attributor 分道。"""
    for a in (1, 2, 3, 0):
        assert save_prediction(db, 10, "A_multi", _parsed(), f"raw{a}",
                               "dsh 0.1.2", "flash", 1.5, attributor=a) > 0
    rows = db.execute("SELECT attributor, raw_output FROM agentline_predictions"
                      " ORDER BY attributor").fetchall()
    assert [(r["attributor"], r["raw_output"]) for r in rows] == \
        [(0, "raw0"), (1, "raw1"), (2, "raw2"), (3, "raw3")]
    # 幂等覆盖只动自己 attributor 道上的行
    save_prediction(db, 10, "A_multi", _parsed(), "raw1b", "dsh 0.1.2",
                    "flash", 1.6, attributor=1)
    rows = db.execute("SELECT attributor, raw_output FROM agentline_predictions"
                      " ORDER BY attributor").fetchall()
    assert len(rows) == 4 and rows[1]["raw_output"] == "raw1b"


def test_save_prediction_budget_exhausted_flag(db):
    parsed = {"status": "ok", "p_home": 0.5, "p_draw": 0.3, "p_away": 0.2,
              "p_over25": 0.5, "confidence": 0.6, "reasoning_digest": "d",
              "sources_json": "[]", "repaired": False}
    save_prediction(db, 1, "A_debate", parsed, "raw", "h", "m", 1.0,
                    attributor=1, budget_exhausted=1)
    row = db.execute("SELECT budget_exhausted FROM agentline_predictions"
                     " WHERE line='A_debate'").fetchone()
    assert row["budget_exhausted"] == 1


def test_save_debate_round_upsert(db):
    save_debate_round(db, 1, 0, "generator", '{"a":1}', "raw", "ok",
                      0.5, "h", "m")
    save_debate_round(db, 1, 0, "generator", '{"a":2}', "raw2", "ok",
                      0.6, "h", "m")            # 重跑覆盖
    rows = db.execute("SELECT payload_json, raw_output FROM"
                      " agentline_debate_rounds").fetchall()
    assert len(rows) == 1 and rows[0]["payload_json"] == '{"a":2}'
    save_debate_round(db, 1, 1, "critic", "[]", "raw3", "ok", 0.1, "h", "m")
    assert db.execute("SELECT COUNT(*) c FROM"
                      " agentline_debate_rounds").fetchone()["c"] == 2


def test_save_division_jump_upsert(db):
    save_division_jump(db, 1, 1, "archivist", '{"a":1}', "raw", "ok",
                       0.5, "h", "m")
    save_division_jump(db, 1, 1, "archivist", '{"a":2}', "raw2", "ok",
                       0.6, "h", "m")            # 重跑覆盖
    rows = db.execute("SELECT payload_json FROM"
                      " agentline_division_jumps").fetchall()
    assert len(rows) == 1 and rows[0]["payload_json"] == '{"a":2}'
    save_division_jump(db, 1, 2, "predictor", "{}", "raw3", "ok",
                       0.1, "h", "m")
    assert db.execute("SELECT COUNT(*) c FROM"
                      " agentline_division_jumps").fetchone()["c"] == 2


def test_save_run_counts(db):
    rid = save_run(db, "A_base", "fa-agent-base", "flash",
                   {"ok": 3, "parse_fail": 1, "timeout": 0, "error": 0},
                   {"league": "E0"})
    row = db.execute("SELECT * FROM agentline_runs WHERE id=?", (rid,)).fetchone()
    assert row["n_ok"] == 3 and row["n_parse_fail"] == 1
