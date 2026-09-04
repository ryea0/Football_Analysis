"""关卡原子性：暂存渲染/merge 原子一步/重入防护/关窗判定（设计档 §8）。"""
import json
from datetime import date, datetime

import pytest

from fa import config
from fa.db import connect, init_db
from fa.evolve import EvolutionError, apply as A, knowledge as K
from fa.evolve.windows import window_bounds


CONTRACT = {"league": "E0",
            "appends": [{"section": "教训", "text": "新教训", "date": "2026-10-16",
                         "ttl_days": None,
                         "evidence": {"fixtures": [1], "stat": "s"}}],
            "amendments": [], "deprecations": [], "no_change_reason": None}

KB = """<!-- kb: league=E0 generated=2026-09-04 hash=ab12 -->
# E0 知识库

## 结构性认知
- [E0-S01] 升班马主场季初高跑动，盘口惯性低估前 6 轮

## 时效
- [E0-T03|2026-09-04|90d] 某队主力门将伤缺，预计 11 月复出

## 教训
- [E0-L07|2026-10-15] 密集期 downweight 过狠（证据见台账）
"""


@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    """fa.config.project_root → tmp（proposals/personas 全部落 tmp，不碰真仓库）。"""
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def kbfile(tmp_root):
    """预置活知识文件 personas/knowledge/epl.md（E0 = epl.md，config §9.2）。"""
    p = tmp_root / "personas" / "knowledge"
    p.mkdir(parents=True)
    (p / "epl.md").write_text(KB, encoding="utf-8")
    return p / "epl.md"


@pytest.fixture
def conn(tmp_path):
    """临时库（schema v8 含 evolution 三表；本模块自定义，不依赖共享 conftest）。"""
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


def test_stage_proposal_writes_three_artifacts(tmp_root, kbfile):
    ev = {"league": "E0"}
    path = A.stage_proposal(1, "E0", CONTRACT, ev)
    d = tmp_root / "evolution" / "proposals" / "w1"
    assert path == d / "E0.json"
    assert json.loads((d / "E0.json").read_text()) == CONTRACT
    assert "新教训" in (d / "E0.proposed.md").read_text()
    assert (d / "E0.diff").exists() and (d / "E0.evidence.json").exists()
    # 活文件未被触碰（暂存区语义）
    assert "新教训" not in K.kb_path("E0").read_text()


def test_merge_proposal_atomic_and_reentrant_guard(conn, tmp_root, kbfile):
    wid = _mk_window(conn, 1)
    rid = _mk_run(conn, wid, "E0", path="evolution/proposals/w1/E0.json")
    A.stage_proposal(1, "E0", CONTRACT, {"league": "E0"})
    msg = A.merge_proposal(conn, wid, "E0", note="裁定通过")
    assert "新教训" in K.kb_path("E0").read_text()
    ruling = conn.execute("SELECT * FROM evolution_rulings").fetchone()
    assert ruling["ruling"] == "merged" and ruling["note"] == "裁定通过"
    assert ruling["kb_hash_after"] == K.personas_tree_hash(
        tmp_root / "personas")
    assert "w1" in msg and "E0" in msg        # 建议 commit message
    with pytest.raises(EvolutionError):
        A.merge_proposal(conn, wid, "E0", note="二次裁定")   # 重入防护


def test_reject_leaves_file_untouched(conn, tmp_root, kbfile):
    wid = _mk_window(conn, 1)
    rid = _mk_run(conn, wid, "E0")
    A.record_ruling(conn, rid, "rejected", note="证据链不实")
    assert "新教训" not in K.kb_path("E0").read_text()
    assert conn.execute("SELECT ruling FROM evolution_rulings"
                        ).fetchone()["ruling"] == "rejected"


def test_gate_closed_requires_all_leagues_terminal(conn):
    wid = _mk_window(conn, 1)
    for lg in ("E0", "SP1", "D1", "I1", "F1"):
        _mk_run(conn, wid, lg)
    assert not A.gate_closed(conn, 1)
    # E0 提案被 reject（终态），其余四联赛反思结果置 no_change（终态）
    conn.execute("UPDATE evolution_runs SET status='no_change'"
                 " WHERE window_id=? AND league!='E0'", (wid,))
    A.record_ruling(conn, _run_id(conn, wid, "E0"), "rejected", note="证据不足")
    assert A.gate_closed(conn, 1)


def test_merge_rejects_live_drift_since_stage(conn, tmp_root, kbfile):
    """漂移护栏：反思后活文件被动过（TTL 修剪/人工改动）→ 拒合并且零副作用。"""
    wid = _mk_window(conn, 1)
    _mk_run(conn, wid, "E0", path="evolution/proposals/w1/E0.json")
    A.stage_proposal(1, "E0", CONTRACT, {"league": "E0"})
    live = K.kb_path("E0")
    live.write_text(live.read_text(encoding="utf-8").replace(
        "盘口惯性低估前 6 轮", "盘口惯性低估前 6 轮（人工改动）"),
        encoding="utf-8")
    with pytest.raises(EvolutionError):
        A.merge_proposal(conn, wid, "E0", note="裁定通过")
    # 护栏在写活文件前触发：人工改动未被覆盖、无任何 Ruling 行
    assert "（人工改动）" in live.read_text(encoding="utf-8")
    assert conn.execute(
        "SELECT COUNT(*) FROM evolution_rulings").fetchone()[0] == 0


def test_merge_ignores_header_only_staged_drift(conn, tmp_root, kbfile):
    """暂存 proposed.md 头部（generated= 日期是机械字段）与 merge 日不同不算
    漂移——人审关卡天然跨日，护栏只比解析后的条目内容。"""
    wid = _mk_window(conn, 1)
    _mk_run(conn, wid, "E0", path="evolution/proposals/w1/E0.json")
    A.stage_proposal(1, "E0", CONTRACT, {"league": "E0"})
    staged = tmp_root / "evolution" / "proposals" / "w1" / "E0.proposed.md"
    lines = staged.read_text(encoding="utf-8").splitlines(keepends=True)
    lines[0] = "<!-- kb: league=E0 generated=2020-01-01 hash=ab12 -->\n"
    staged.write_text("".join(lines), encoding="utf-8")
    msg = A.merge_proposal(conn, wid, "E0", note="裁定通过")
    assert "w1" in msg and "新教训" in K.kb_path("E0").read_text()


def test_record_ruling_rejects_merged_and_blank_note(conn):
    """record_ruling 只收 rejected/shelved（merged 归 merge_proposal）且 note 强制非空。"""
    wid = _mk_window(conn, 1)
    rid = _mk_run(conn, wid, "E0")
    with pytest.raises(EvolutionError):
        A.record_ruling(conn, rid, "merged", note="merged 只走 merge_proposal")
    with pytest.raises(EvolutionError):
        A.record_ruling(conn, rid, "rejected", note="   ")
    assert conn.execute(
        "SELECT COUNT(*) FROM evolution_rulings").fetchone()[0] == 0


def _mk_window(conn, idx):
    cur = conn.execute("INSERT INTO evolution_windows (idx, opened_at, closes_at)"
                       " VALUES (?, '2026-09-04', '2026-10-16')", (idx,))
    conn.commit()
    return int(cur.lastrowid)


def _mk_run(conn, wid, league, status="ok", path=None):
    cur = conn.execute(
        "INSERT INTO evolution_runs (window_id, league, kb_hash_before, status,"
        " no_change_reason, proposal_path, duration_s, created_at)"
        " VALUES (?, ?, 'h', ?, NULL, ?, 0.1, '2026-10-16T00:00:00Z')",
        (wid, league, status, path))
    conn.commit()
    return int(cur.lastrowid)


def _run_id(conn, wid, league):
    return conn.execute("SELECT id FROM evolution_runs WHERE window_id=? AND"
                        " league=?", (wid, league)).fetchone()["id"]
