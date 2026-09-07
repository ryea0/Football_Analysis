"""C' 线提案暂存与自动落账（无人审，事后可回滚）。

与 C 线 apply.py 的差异：
- C 线：提案暂存 → 人审关卡 → merge/reject/shelve
- C' 线：提案暂存 → 自动落账 → 记 Ruling(kept) → 事后可 rollback

落账原则：先写文件，后记 ruling 行（单事务）。rollback = git revert +
ruling(rolled_back)。失败语义同 C 线：文件写了但 ruling 行没写 = 不一致，
status 命令披露。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fa import config
from fa.evolve_self import EvolutionSelfError
from fa.evolve_self.knowledge import (
    init_kb_self, kb_path_self, parse_kb_self, personas_self_tree_hash,
    _text_digest,
)

_LEAGUES = tuple(config.PERSONA_FILES)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def proposals_self_dir(w_idx: int) -> Path:
    return config.project_root() / "evolution" / "proposals_self" / f"w{w_idx}"


def stage_proposal_self(w_idx: int, league: str, new_text: str,
                        evidence: dict, diff: str) -> Path:
    """新 markdown + 证据 + diff → 暂存区三件套。返回提案文件路径。"""
    d = proposals_self_dir(w_idx)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{league}.md").write_text(new_text, encoding="utf-8")
    (d / f"{league}.evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / f"{league}.diff").write_text(diff, encoding="utf-8")
    return d / f"{league}.md"


def _run_row_self(conn, window_id: int, league: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM evolution_self_runs WHERE window_id=? AND league=?",
        (window_id, league)).fetchone()
    if row is None:
        raise EvolutionSelfError(
            f"无 C' 线反思记录：window={window_id} league={league}")
    return row


def _require_unruled_self(conn, run_id: int) -> None:
    if conn.execute("SELECT 1 FROM evolution_self_rulings WHERE run_id=?",
                    (run_id,)).fetchone() is not None:
        raise EvolutionSelfError(f"run {run_id} 已裁定——拒绝二次裁定（重入防护）")


def close_window_self_if_terminal(conn: sqlite3.Connection,
                                  window_id: int) -> None:
    row = conn.execute("SELECT idx FROM evolution_self_windows WHERE id=?",
                       (window_id,)).fetchone()
    if row is None:
        return
    if gate_self_closed(conn, row["idx"]):
        conn.execute(
            "UPDATE evolution_self_windows SET closed_at=COALESCE(closed_at, ?)"
            " WHERE id=?", (_now_iso(), window_id))


def gate_self_closed(conn: sqlite3.Connection, w_idx: int) -> bool:
    row = conn.execute("SELECT id FROM evolution_self_windows WHERE idx=?",
                       (w_idx,)).fetchone()
    if row is None:
        return False
    for lg in _LEAGUES:
        r = conn.execute(
            "SELECT esr.id FROM evolution_self_runs ru"
            " LEFT JOIN evolution_self_rulings esr ON esr.run_id = ru.id"
            " WHERE ru.window_id=? AND ru.league=?",
            (row["id"], lg)).fetchone()
        if r is None or r["id"] is None:
            # 未反思 或 status='ok' 但未裁定 = 未终态
            ruled = conn.execute(
                "SELECT status FROM evolution_self_runs WHERE window_id=? AND league=?",
                (row["id"], lg)).fetchone()
            if ruled is None or ruled["status"] == "ok":
                return False
    return True


def auto_apply_self(conn: sqlite3.Connection, window_id: int,
                    league: str) -> str:
    """自动落账：把暂存提案写入活文件 + 记 Ruling(kept)，单事务。

    C' 线 v1 无人审——反思通过 contract 校验后直接落账。事后可 rollback。
    """
    run = _run_row_self(conn, window_id, league)
    if run["status"] != "ok" or not run["proposal_path"]:
        raise EvolutionSelfError(
            f"{league} 无待落账提案（status={run['status']!r}）")
    _require_unruled_self(conn, run["id"])
    init_kb_self(league)
    live = kb_path_self(league)
    old_text = live.read_text(encoding="utf-8") if live.is_file() else ""
    proposed_file = config.project_root() / run["proposal_path"]
    if not proposed_file.is_file():
        raise EvolutionSelfError(f"{league} 暂存提案文件缺失：{proposed_file}")
    new_text = proposed_file.read_text(encoding="utf-8")
    # 最终校验（写入前最后一道防线）
    try:
        parse_kb_self(new_text, league)
    except EvolutionSelfError as exc:
        raise EvolutionSelfError(f"{league} 提案写入前校验失败：{exc}") from exc
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text(new_text, encoding="utf-8")
    hash_after = personas_self_tree_hash(
        config.project_root() / "personas" / "knowledge_self")
    with conn:  # 单事务：Ruling + 关窗刷新
        conn.execute(
            "INSERT INTO evolution_self_rulings (run_id, ruling, kb_hash_after,"
            " note, created_at) VALUES (?, 'kept', ?, ?, ?)",
            (run["id"], hash_after,
             "自动落账（C' 线 v1 无人审）", _now_iso()))
        close_window_self_if_terminal(conn, window_id)
    idx = conn.execute("SELECT idx FROM evolution_self_windows WHERE id=?",
                       (window_id,)).fetchone()["idx"]
    return f"feat(kb_self): w{idx} {league} 自反思自动落账（建议随后 git add personas/knowledge_self/ 提交）"


def rollback_self(conn: sqlite3.Connection, window_id: int, league: str,
                  note: str) -> str:
    """回滚：把知识库恢复到反思前版本 + 记 Ruling(rolled_back)。

    用 kb_hash_before 定位旧版本——但 v1 没存每个版本的完整快照，
    所以回滚的实现是：从 git 历史里找回滚点；如果 git 不可用，
    就记 ruling + 告警，文件手动处理。
    """
    if not note.strip():
        raise EvolutionSelfError("回滚必须留理由（note 强制非空）")
    run = _run_row_self(conn, window_id, league)
    if run["status"] != "ok":
        raise EvolutionSelfError(
            f"{league} 非 ok 状态不可回滚（status={run['status']!r}）")
    # 找到最新一条 kept ruling
    ruling_row = conn.execute(
        "SELECT * FROM evolution_self_rulings WHERE run_id=? ORDER BY id DESC",
        (run["id"],)).fetchone()
    if ruling_row is None:
        raise EvolutionSelfError(f"{league} 无 ruling 记录，不可回滚")
    if ruling_row["ruling"] != "kept":
        raise EvolutionSelfError(
            f"{league} 当前 ruling={ruling_row['ruling']!r}，不可回滚")
    # 尝试从暂存区找回旧内容（用 diff 反推）—— 简单实现：把旧版本用
    # kb_hash_before 对应的内容从 git 取；git 不可用就报错让手动处理。
    import subprocess
    try:
        result = subprocess.run(
            ["git", "show", f"HEAD:personas/knowledge_self/{config.PERSONA_FILES[league]}"],
            capture_output=True, text=True, timeout=5,
            cwd=config.project_root())
        if result.returncode != 0:
            raise EvolutionSelfError(
                f"git 取不到 {league} 历史版本：{result.stderr.strip()}")
        old_content = result.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvolutionSelfError(
            f"回滚失败：git 不可用或出错：{exc}") from exc
    # 写回旧内容
    live = kb_path_self(league)
    live.write_text(old_content, encoding="utf-8")
    hash_after = personas_self_tree_hash(
        config.project_root() / "personas" / "knowledge_self")
    with conn:
        conn.execute(
            "INSERT INTO evolution_self_rulings (run_id, ruling, kb_hash_after,"
            " note, created_at) VALUES (?, 'rolled_back', ?, ?, ?)",
            (run["id"], hash_after, note, _now_iso()))
        close_window_self_if_terminal(conn, window_id)
    idx = conn.execute("SELECT idx FROM evolution_self_windows WHERE id=?",
                       (window_id,)).fetchone()["idx"]
    return f"kb_self rollback: w{idx} {league} 已回滚（{note[:50]}）"
