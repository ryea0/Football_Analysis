"""C 线编排：tick（修剪→反思→报告）、手动反思/校准、人审视图与状态。

``_today`` 是唯一时间缝（默认北京时间今天，测试注入）——tick 的全部窗口
判定经它走（诚实条款：断言不依赖真实时钟）。
"""
from __future__ import annotations

import sqlite3
from datetime import date

from fa import config
from fa.evolve import apply as A
from fa.evolve import knowledge as K
from fa.evolve import report as REP
from fa.evolve import windows as W
from fa.evolve.reflect import reflect_league


def _today() -> date:
    return W.beijing_today()


def _ensure_window_row(conn, w: W.Window) -> int:
    row = conn.execute("SELECT id FROM evolution_windows WHERE idx=?",
                       (w.idx,)).fetchone()
    if row is not None:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO evolution_windows (idx, opened_at, closes_at)"
        " VALUES (?, ?, ?)", (w.idx, w.opened.isoformat(), w.closes.isoformat()))
    conn.commit()
    return int(cur.lastrowid)


def _reflect_window(conn, w: W.Window, leagues: list[str],
                    calibrate: bool = False) -> list[dict]:
    outs = []
    for league in leagues:
        out = reflect_league(conn, w, league)
        if calibrate:
            out["calibrate"] = True
            continue
        wid = _ensure_window_row(conn, w)
        conn.execute(
            "INSERT INTO evolution_runs (window_id, league, kb_hash_before,"
            " status, no_change_reason, proposal_path, duration_s, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (wid, out["league"], out["kb_hash_before"], out["status"],
             out["no_change_reason"], out["proposal_path"], out["duration_s"]))
        conn.commit()
        outs.append(out)
    return outs


def run_tick(conn: sqlite3.Connection, today: date | None = None) -> str:
    """周检：TTL 修剪 → 升序反思到期未反思窗口（前窗关卡未关则顺延）。"""
    d = today or _today()
    pruned = K.prune_live_files(d)
    lines = [f"tick @ {d.isoformat()}：TTL 修剪 "
             + ("；".join(f"{lg}×{len(a)}" for lg, a in pruned) if pruned
                else "无")]
    for w in W.due_windows(d):
        row = conn.execute("SELECT id, reflected_at FROM evolution_windows"
                           " WHERE idx=?", (w.idx,)).fetchone()
        if row is not None and row["reflected_at"] is not None:
            continue
        if w.idx > 1 and not A.gate_closed(conn, w.idx - 1):
            lines.append(f"w{w.idx - 1} 关卡未关，w{w.idx} 顺延（下次 tick 再查）")
            break
        outs = _reflect_window(conn, w, list(config.PERSONA_FILES))
        wid = conn.execute("SELECT id FROM evolution_windows WHERE idx=?",
                           (w.idx,)).fetchone()["id"]
        conn.execute("UPDATE evolution_windows SET reflected_at=datetime('now')"
                     " WHERE id=?", (wid,))
        conn.commit()
        REP.write_event_report(conn, wid, pruned=pruned, today=d)
        lines.append(f"w{w.idx} 反思完成：" + "，".join(
            f"{o['league']}={o['status']}" for o in outs))
    if len(lines) == 1:
        lines.append("无到期窗口")
    return "\n".join(lines)


def run_reflect(conn: sqlite3.Connection, w_idx: int | None = None,
                league: str | None = None, calibrate: bool = False,
                today: date | None = None) -> dict:
    """手动反思：默认最新到期窗；calibrate 用部分窗口证据、零 DB 写。"""
    d = today or _today()
    due = W.due_windows(d)
    if calibrate:
        cur = W.window_of(d)                    # 校准 = 当前窗至今的部分证据
        w = W.window_bounds(w_idx) if w_idx else cur
        leagues = [league] if league else list(config.PERSONA_FILES)
        _reflect_calibrate(conn, w, leagues)
        return {"calibrate": True, "window": w.idx, "leagues": leagues}
    if not due:
        raise ValueError("无已收口窗口可反思（--window 显式指定或等窗口收口）")
    w = W.window_bounds(w_idx) if w_idx else due[-1]
    if w not in due:
        raise ValueError(f"w{w.idx} 尚未收口（反射窗口：{[x.idx for x in due]}）")
    leagues = [league] if league else list(config.PERSONA_FILES)
    outs = _reflect_window(conn, w, leagues)
    wid = conn.execute("SELECT id FROM evolution_windows WHERE idx=?",
                       (w.idx,)).fetchone()["id"]
    conn.execute("UPDATE evolution_windows SET reflected_at=datetime('now')"
                 " WHERE id=?", (wid,))
    conn.commit()
    REP.write_event_report(conn, wid, pruned=[], today=d)
    return {"calibrate": False, "window": w.idx, "results": outs}


def _reflect_calibrate(conn, w, leagues) -> None:
    """校准：真调用、真证据、产物落 calibration 目录、零 DB 行（R2）。"""
    import json
    from fa.evolve.evidence import window_evidence
    from fa.evolve.reflect import build_reflect_prompt, call_reflect
    proposals_root = (config.project_root() / "evolution" / "proposals"
                      / f"calibration-{_today().isoformat()}")
    proposals_root.mkdir(parents=True, exist_ok=True)
    for league in leagues:
        ev = window_evidence(conn, w, league)
        kb_file = K.kb_path(league)
        kb_text = kb_file.read_text(encoding="utf-8") if kb_file.is_file() else None
        try:
            out = call_reflect(build_reflect_prompt(league, kb_text, ev))
        except Exception as exc:                      # 校准连失败都要留痕
            (proposals_root / f"{league}.error.txt").write_text(str(exc))
            continue
        (proposals_root / f"{league}.raw.txt").write_text(out)
        (proposals_root / f"{league}.evidence.json").write_text(
            json.dumps(ev, ensure_ascii=False, indent=2))


def run_review(conn: sqlite3.Connection, w_idx: int | None = None,
               league: str | None = None) -> str:
    """人审视图：状态 + diff 全文 + 证据摘要。"""
    q = ("SELECT ew.idx, er.league, er.status, er.no_change_reason,"
         " er.proposal_path, er.id AS run_id FROM evolution_runs er"
         " JOIN evolution_windows ew ON ew.id = er.window_id"
         " WHERE 1=1")
    args: list = []
    if w_idx:
        q += " AND ew.idx=?"
        args.append(w_idx)
    if league:
        q += " AND er.league=?"
        args.append(league)
    rows = conn.execute(q + " ORDER BY ew.idx, er.league", args).fetchall()
    if not rows:
        return "（无反思记录）"
    lines = []
    for r in rows:
        ruled = conn.execute("SELECT ruling, note FROM evolution_rulings"
                             " WHERE run_id=?", (r["run_id"],)).fetchone()
        head = (f"## w{r['idx']} {r['league']}：{r['status']}"
                + (f"（已裁定 {ruled['ruling']}：{ruled['note']}）" if ruled else ""))
        lines.append(head)
        if r["no_change_reason"]:
            lines.append(f"  理由：{r['no_change_reason']}")
        if r["proposal_path"]:
            diff = (config.project_root() / r["proposal_path"]).parent \
                / f"{r['league']}.diff"
            if diff.is_file():
                lines.append("```diff")
                lines.append(diff.read_text(encoding="utf-8").rstrip())
                lines.append("```")
        lines.append("")
    return "\n".join(lines)


def evolve_status_text(conn: sqlite3.Connection) -> str:
    cur = W.window_of(_today())
    lines = [f"当前窗：w{cur.idx}（{cur.opened} ~ {cur.closes}，北京时间）",
             f"活树 personas_hash：{K.personas_tree_hash(config.project_root() / 'personas')[:12]}…",
             f"本窗快照：{'已建' if K.snapshot_dir(cur.idx).exists() else '未建（首个 run 时建）'}",
             "各联赛 活文件 vs 本窗快照（设计档 §8）："]
    for lg in config.PERSONA_FILES:
        live = K.kb_path(lg)
        live_h = (K._text_digest(live.read_text(encoding="utf-8"))
                  if live.is_file() else "（无活文件）")
        snap_text = K.window_kb_text(cur.idx, lg)
        snap_h = (K._text_digest(snap_text) if snap_text is not None
                  else "（快照无此联赛）")
        mark = "一致" if live_h == snap_h else "不一致（合并待下窗生效/活文件有未裁定变更）"
        lines.append(f"  {lg}：活 {live_h} / 快照 {snap_h} —— {mark}")
    rows = conn.execute("SELECT ew.idx, er.league, er.status, er.proposal_path,"
                        " er.id AS run_id FROM evolution_runs er"
                        " JOIN evolution_windows ew ON ew.id = er.window_id"
                        " ORDER BY ew.idx DESC, er.league LIMIT 20").fetchall()
    if rows:
        lines.append("最近反思：")
        for r in rows:
            ruled = conn.execute("SELECT ruling FROM evolution_rulings"
                                 " WHERE run_id=?", (r["run_id"],)).fetchone()
            lines.append(f"  w{r['idx']} {r['league']} {r['status']}"
                         + (f"→{ruled['ruling']}" if ruled else "→待裁定"
                            if r["status"] == "ok" else ""))
    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM evolution_runs er"
        " LEFT JOIN evolution_rulings el ON el.run_id = er.id"
        " WHERE er.status='ok' AND el.id IS NULL").fetchone()["n"]
    lines.append(f"待裁提案：{pending} 个（fa evolve review 查看）")
    return "\n".join(lines)
