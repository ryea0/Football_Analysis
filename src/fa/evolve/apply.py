"""提案暂存与关卡原子操作（设计档 §8，R3 暂存区）。

提案永不触碰 personas/knowledge/ 真实路径——merge 是唯一写入口，且为
原子落账：先写文件、后单事务记 Ruling + 关窗刷新。失败语义：文件已写而
事务失败 → 无 Ruling 行，快照保证本窗不受影响；`fa evolve status` 以活树
hash 与 Ruling 不一致披露。
"""
from __future__ import annotations

import difflib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fa import config
from fa.evolve import EvolutionError
from fa.evolve import knowledge as K

_LEAGUES = tuple(config.PERSONA_FILES)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def proposals_dir(w_idx: int) -> Path:
    return config.project_root() / "evolution" / "proposals" / f"w{w_idx}"


def stage_proposal(w_idx: int, league: str, contract: dict,
                   evidence: dict) -> Path:
    """契约 + 证据 → 暂存区三件套（json / proposed.md / diff）+ 证据留档。"""
    d = proposals_dir(w_idx)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{league}.json").write_text(
        json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / f"{league}.evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    live = K.kb_path(league)
    old_text = live.read_text(encoding="utf-8") if live.is_file() else ""
    kb = K.parse_kb(old_text, league)
    new_text = K.render_kb(K.apply_contract(kb, contract),
                           generated=_now_iso()[:10],
                           digest=K._text_digest(old_text))
    (d / f"{league}.proposed.md").write_text(new_text, encoding="utf-8")
    diff = "".join(difflib.unified_diff(
        old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
        fromfile=f"personas/knowledge/{config.PERSONA_FILES[league]}",
        tofile=f"evolution/proposals/w{w_idx}/{league}.proposed.md"))
    (d / f"{league}.diff").write_text(diff, encoding="utf-8")
    return d / f"{league}.json"


def _run_row(conn, window_id: int, league: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM evolution_runs WHERE window_id=? AND league=?",
        (window_id, league)).fetchone()
    if row is None:
        raise EvolutionError(f"无反思记录：window={window_id} league={league}")
    return row


def _require_unruled(conn, run_id: int) -> None:
    if conn.execute("SELECT 1 FROM evolution_rulings WHERE run_id=?",
                    (run_id,)).fetchone() is not None:
        raise EvolutionError(f"run {run_id} 已裁定——拒绝二次裁定（重入防护）")


def close_window_if_terminal(conn: sqlite3.Connection, window_id: int) -> None:
    """全联赛终态（有 Ruling 或 status != 'ok'）→ 记 closed_at。幂等。"""
    row = conn.execute("SELECT idx FROM evolution_windows WHERE id=?",
                       (window_id,)).fetchone()
    if row is None:
        return
    if gate_closed(conn, row["idx"]):
        conn.execute(
            "UPDATE evolution_windows SET closed_at=COALESCE(closed_at, ?)"
            " WHERE id=?", (_now_iso(), window_id))


def gate_closed(conn: sqlite3.Connection, w_idx: int) -> bool:
    row = conn.execute("SELECT id FROM evolution_windows WHERE idx=?",
                       (w_idx,)).fetchone()
    if row is None:
        return False
    for lg in _LEAGUES:
        r = conn.execute(
            "SELECT er.id FROM evolution_runs ru"
            " LEFT JOIN evolution_rulings er ON er.run_id = ru.id"
            " WHERE ru.window_id=? AND ru.league=?",
            (row["id"], lg)).fetchone()
        if r is None or r["id"] is None:
            ruled = conn.execute(
                "SELECT status FROM evolution_runs WHERE window_id=? AND league=?",
                (row["id"], lg)).fetchone()
            if ruled is None or ruled["status"] == "ok":
                return False        # 未反思/待裁定 = 未终态
    return True


def record_ruling(conn: sqlite3.Connection, run_id: int, ruling: str,
                  note: str) -> None:
    if ruling not in ("rejected", "shelved"):
        raise EvolutionError(f"record_ruling 只收 rejected/shelved：{ruling!r}")
    if not note.strip():
        raise EvolutionError("裁定必须留理由（note 强制非空）")
    _require_unruled(conn, run_id)
    wid = conn.execute("SELECT window_id FROM evolution_runs WHERE id=?",
                       (run_id,)).fetchone()["window_id"]
    with conn:                                        # 单事务：Ruling + 关窗刷新
        conn.execute(
            "INSERT INTO evolution_rulings (run_id, ruling, kb_hash_after, note,"
            " decided_at) VALUES (?, ?, NULL, ?, ?)",
            (run_id, ruling, note, _now_iso()))
        close_window_if_terminal(conn, wid)


def merge_proposal(conn: sqlite3.Connection, window_id: int, league: str,
                   note: str) -> str:
    """原子落账：先写活文件 → 单事务 Ruling(merged) + 关窗刷新。"""
    if not note.strip():
        raise EvolutionError("裁定必须留理由（note 强制非空）")
    run = _run_row(conn, window_id, league)
    if run["status"] != "ok" or not run["proposal_path"]:
        raise EvolutionError(f"{league} 无待裁提案（status={run['status']!r}）")
    _require_unruled(conn, run["id"])
    contract = json.loads(
        (config.project_root() / run["proposal_path"]).read_text(encoding="utf-8"))
    live = K.kb_path(league)
    old_text = live.read_text(encoding="utf-8") if live.is_file() else ""
    kb = K.parse_kb(old_text, league)
    new_text = K.render_kb(K.apply_contract(kb, contract),
                           generated=_now_iso()[:10],
                           digest=K._text_digest(old_text))
    live.parent.mkdir(parents=True, exist_ok=True)
    # 漂移护栏（先比后写）：暂存 .proposed.md 是人审 `fa evolve review` 看到的
    # 唯一内容——活文件在反思后被动过（TTL 修剪/人工改动）则现渲染必偏离暂存，
    # 拒绝合并。按解析后条目比对而非裸文本：头部 generated= 日期是机械字段，
    # 人审关卡天然跨日（tick 反思日暂存、人隔日合并），裸文本比对会误伤。
    staged = ((config.project_root() / run["proposal_path"]).parent
              / f"{league}.proposed.md")
    if staged.is_file():
        staged_kb = K.parse_kb(staged.read_text(encoding="utf-8"), league)
        if staged_kb.entries != K.parse_kb(new_text, league).entries:
            raise EvolutionError(
                f"{league} 活文件在反思后已变更——TTL 修剪或人工改动；"
                "请核查后重新走关卡")
    live.write_text(new_text, encoding="utf-8")       # 先写文件
    hash_after = K.personas_tree_hash(config.project_root() / "personas")
    with conn:                                        # 后原子落账（单事务）
        conn.execute(
            "INSERT INTO evolution_rulings (run_id, ruling, kb_hash_after, note,"
            " decided_at) VALUES (?, 'merged', ?, ?, ?)",
            (run["id"], hash_after, note, _now_iso()))
        close_window_if_terminal(conn, window_id)
    n_a = len(contract.get("appends") or [])
    n_m = len(contract.get("amendments") or [])
    n_d = len(contract.get("deprecations") or [])
    idx = conn.execute("SELECT idx FROM evolution_windows WHERE id=?",
                       (window_id,)).fetchone()["idx"]
    return (f"feat(kb): w{idx} 合并 {league} 反思提案（{n_a}增/{n_m}改/{n_d}废）"
            f"——建议随后 git add personas/knowledge/ evolution/ 并提交")
