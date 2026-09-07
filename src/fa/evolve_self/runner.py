"""C' 线编排：tick（自动反思落账）、手动反思、状态视图。

与 C 线 runner 的差异：
- C 线：反思 → 暂存 → 人审关卡 → merge/reject/shelve
- C' 线：反思 → 暂存 → 自动落账（kept）→ 事后可 rollback
- C 线顺延逻辑：前窗关卡未关则本窗顺延
- C' 线顺延逻辑：同窗口节奏，前窗未关也顺延（保证窗口冻结）
"""
from __future__ import annotations

import sqlite3
from datetime import date

from fa import config
from fa.evolve_self import EvolutionSelfError
from fa.evolve_self import apply as A
from fa.evolve_self import knowledge as K
from fa.evolve_self import windows as W
from fa.evolve_self.reflect import reflect_league_self


def _today() -> date:
    return W.beijing_today()


def _ensure_window_row_self(conn, w: W.Window) -> int:
    row = conn.execute("SELECT id FROM evolution_self_windows WHERE idx=?",
                       (w.idx,)).fetchone()
    if row is not None:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO evolution_self_windows (idx, opened_at, closes_at)"
        " VALUES (?, ?, ?)", (w.idx, w.opened.isoformat(), w.closes.isoformat()))
    conn.commit()
    return int(cur.lastrowid)


def _reflect_window_self(conn, w: W.Window, leagues: list[str]) -> list[dict]:
    """逐联赛反思落 evolution_self_runs；已有反思行的联赛跳过（防重跑）。"""
    wid = _ensure_window_row_self(conn, w)
    outs = []
    for league in leagues:
        if conn.execute("SELECT 1 FROM evolution_self_runs WHERE window_id=?"
                        " AND league=?", (wid, league)).fetchone() is not None:
            outs.append({"league": league, "status": "already"})
            continue
        out = reflect_league_self(conn, w, league)
        conn.execute(
            "INSERT INTO evolution_self_runs (window_id, league, kb_hash_before,"
            " status, no_change_reason, proposal_path, added_chars, changed_lines,"
            " duration_s, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (wid, out["league"], out["kb_hash_before"], out["status"],
             out.get("no_change_reason"), out.get("proposal_path"),
             out.get("added_chars", 0), out.get("changed_lines", 0),
             out["duration_s"]))
        conn.commit()
        outs.append(out)
    return outs


def run_tick_self(conn: sqlite3.Connection, today: date | None = None) -> str:
    """C' 线周检：反思到期窗口 → 自动落账（ok 的直接写活文件 + 记 kept）。

    与 C 线 tick 同款窗口顺延逻辑：前窗未关则本窗顺延。
    """
    d = today or _today()
    lines: list[str] = []
    due = W.due_windows(d)
    acted = False
    for w in due:
        row = conn.execute("SELECT id, reflected_at FROM evolution_self_windows"
                           " WHERE idx=?", (w.idx,)).fetchone()
        if row is not None and row["reflected_at"] is not None:
            continue
        if w.idx > 1 and not A.gate_self_closed(conn, w.idx - 1):
            lines.append(f"w{w.idx - 1} 未关，w{w.idx} 顺延（下次 tick 再查）")
            acted = True
            break
        outs = _reflect_window_self(conn, w, list(config.PERSONA_FILES))
        wid = conn.execute("SELECT id FROM evolution_self_windows WHERE idx=?",
                           (w.idx,)).fetchone()["id"]
        conn.execute("UPDATE evolution_self_windows SET reflected_at=datetime('now')"
                     " WHERE id=?", (wid,))
        conn.commit()
        # 自动落账（ok 的直接应用）
        applied = []
        for o in outs:
            if o["status"] == "ok":
                try:
                    msg = A.auto_apply_self(conn, wid, o["league"])
                    applied.append(f"{o['league']}=auto_applied")
                except EvolutionSelfError as exc:
                    applied.append(f"{o['league']}=apply_failed({exc})")
        lines.append(
            f"w{w.idx} 自反思完成：" + "，".join(
                f"{o['league']}={o['status']}" for o in outs))
        if applied:
            lines.append(f"w{w.idx} 自动落账：" + "，".join(applied))
        acted = True
    lines.insert(0, f"evolve-self tick @ {d.isoformat()}")
    if not due:
        lines.append("无到期窗口")
    elif not acted:
        lines.append("到期窗口均已反思")
    return "\n".join(lines)


def run_reflect_self(conn: sqlite3.Connection, w_idx: int | None = None,
                     league: str | None = None,
                     calibrate: bool = False,
                     today: date | None = None) -> dict:
    """手动反思：默认最新到期窗；calibrate 用当前窗部分证据、零 DB 写。"""
    d = today or _today()
    if calibrate:
        cur = W.window_of(d)
        w = W.window_bounds(w_idx) if w_idx else cur
        leagues = [league] if league else list(config.PERSONA_FILES)
        _reflect_calibrate_self(conn, w, leagues)
        return {"calibrate": True, "window": w.idx, "leagues": leagues}
    due = W.due_windows(d)
    if not due:
        raise ValueError("无已收口窗口可反思（--window 显式指定或等窗口收口）")
    w = W.window_bounds(w_idx) if w_idx else due[-1]
    if w not in due:
        raise ValueError(
            f"w{w.idx} 尚未收口（到期窗口：{[x.idx for x in due]}）")
    leagues = [league] if league else list(config.PERSONA_FILES)
    outs = _reflect_window_self(conn, w, leagues)
    if all(o["status"] == "already" for o in outs):
        raise ValueError("该窗口已全部反思")
    wid = conn.execute("SELECT id FROM evolution_self_windows WHERE idx=?",
                       (w.idx,)).fetchone()["id"]
    conn.execute("UPDATE evolution_self_windows SET reflected_at=datetime('now')"
                 " WHERE id=?", (wid,))
    conn.commit()
    # 自动落账
    for o in outs:
        if o["status"] == "ok":
            try:
                A.auto_apply_self(conn, wid, o["league"])
                o["applied"] = True
            except EvolutionSelfError as exc:
                o["applied"] = False
                o["apply_error"] = str(exc)
    return {"calibrate": False, "window": w.idx, "results": outs}


def _reflect_calibrate_self(conn, w, leagues) -> None:
    """校准：真调用、真证据、产物留 calibration 目录、零 DB 行。"""
    import json
    from fa.evolve_self.evidence import window_evidence
    from fa.evolve_self.reflect import (
        build_reflect_self_prompt, call_reflect_self,
    )
    from fa.evolve_self.knowledge import kb_path_self
    cal_root = (config.project_root() / "evolution" / "proposals_self"
                / f"calibration-{_today().isoformat()}")
    cal_root.mkdir(parents=True, exist_ok=True)
    for league in leagues:
        ev = window_evidence(conn, w, league)
        kb_file = kb_path_self(league)
        kb_text = (kb_file.read_text(encoding="utf-8")
                   if kb_file.is_file() else f"# {league} 知识库（自反思·时间线）\n\n## 总原则\n\n（空模板）\n")
        try:
            out = call_reflect_self(build_reflect_self_prompt(league, kb_text, ev))
        except Exception as exc:
            (cal_root / f"{league}.error.txt").write_text(str(exc))
            continue
        (cal_root / f"{league}.raw.txt").write_text(out)
        (cal_root / f"{league}.evidence.json").write_text(
            json.dumps(ev, ensure_ascii=False, indent=2))


def evolve_self_status_text(conn: sqlite3.Connection) -> str:
    cur = W.window_of(_today())
    self_root = config.project_root() / "personas" / "knowledge_self"
    lines = [
        f"当前窗：w{cur.idx}（{cur.opened} ~ {cur.closes}，北京时间）",
        f"自反思树 hash：{K.personas_self_tree_hash(self_root)[:12]}…",
        f"本窗快照：{'已建' if K.snapshot_dir_self(cur.idx).exists() else '未建'}",
        "各联赛 活文件 vs 快照：",
    ]
    for lg in config.PERSONA_FILES:
        live = K.kb_path_self(lg)
        live_h = (K._text_digest(live.read_text(encoding="utf-8"))
                  if live.is_file() else "（无活文件）")
        snap_text = K.window_kb_text_self(cur.idx, lg)
        snap_h = (K._text_digest(snap_text) if snap_text is not None
                  else "（快照无此联赛）")
        mark = "一致" if live_h == snap_h else "不一致（自反思落账但快照钉版）"
        lines.append(f"  {lg}：活 {live_h} / 快照 {snap_h} —— {mark}")
    rows = conn.execute(
        "SELECT ew.idx, er.league, er.status, er.added_chars, er.changed_lines,"
        " er.id AS run_id FROM evolution_self_runs er"
        " JOIN evolution_self_windows ew ON ew.id = er.window_id"
        " ORDER BY ew.idx DESC, er.league LIMIT 20").fetchall()
    if rows:
        lines.append("最近自反思：")
        for r in rows:
            ruled = conn.execute("SELECT ruling FROM evolution_self_rulings"
                                 " WHERE run_id=?",
                                 (r["run_id"],)).fetchone()
            lines.append(
                f"  w{r['idx']} {r['league']} {r['status']}"
                + (f"→{ruled['ruling']}" if ruled
                   else "→待落账" if r["status"] == "ok" else ""))
    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM evolution_self_runs er"
        " LEFT JOIN evolution_self_rulings el ON el.run_id = er.id"
        " WHERE er.status='ok' AND el.id IS NULL").fetchone()["n"]
    lines.append(f"待落账：{pending} 个（tick/reflect 时自动落账）")
    return "\n".join(lines)


def diff_self_text(conn: sqlite3.Connection, w_idx: int | None = None,
                   league: str | None = None) -> str:
    """查看 C' 线反思 diff。"""
    q = ("SELECT ew.idx, er.league, er.status, er.proposal_path, er.id AS run_id"
         " FROM evolution_self_runs er"
         " JOIN evolution_self_windows ew ON ew.id = er.window_id"
         " WHERE 1=1")
    args: list = []
    if w_idx:
        q += " AND ew.idx=?"
        args.append(w_idx)
    if league:
        q += " AND er.league=?"
        args.append(league)
    q += " ORDER BY ew.idx DESC, er.league LIMIT 20"
    rows = conn.execute(q, args).fetchall()
    if not rows:
        return "（无自反思记录）"
    lines = []
    for r in rows:
        lines.append(f"## w{r['idx']} {r['league']}：{r['status']}")
        if r["status"] == "ok" and r["proposal_path"]:
            staged = (config.project_root() / r["proposal_path"]).parent
            diff = staged / f"{r['league']}.diff"
            if diff.is_file():
                lines.append("```diff")
                lines.append(diff.read_text(encoding="utf-8").rstrip())
                lines.append("```")
        lines.append("")
    return "\n".join(lines)
