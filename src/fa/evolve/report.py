"""进化事件滚动报告（设计档 §10）：descriptive、不设终止判据（同线 A 风格）。

每窗一份 docs/evolution/report-{closes 日期}.md。统计判据为 ≥2 窗口后的
后续注册项——届时先改 spec §12.7 再启用，不回溯套用；校准轮结论不得作为
知识库有效性证据（固定声明常驻尾部）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from fa import config
from fa.evolve import EvolutionError
from fa.evolve import windows as _windows


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt(x):
    if x is None:
        return "—"
    return f"{x:+.2%}"


def write_event_report(conn: sqlite3.Connection, window_id: int, *,
                       pruned: list[tuple[str, list[str]]] = (),
                       today: date | None = None) -> Path:
    win = conn.execute("SELECT * FROM evolution_windows WHERE id=?",
                       (window_id,)).fetchone()
    if win is None:
        raise EvolutionError(f"窗口 {window_id} 不存在")
    closes = win["closes_at"]
    root = config.project_root()
    path = root / "docs" / "evolution" / f"report-{closes}.md"
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [f"# C 线进化事件报告 — 窗口 w{win['idx']}（{win['opened_at']} ~ {closes}）",
             "", f"- 事件时刻：{_now_iso()}（UTC）",
             # 属性访问不打死绑定——monkeypatch windows.beijing_today 才生效
             f"- 报告基准日：{today.isoformat() if today else _windows.beijing_today().isoformat()}",
             "- TTL 修剪：" + ("；".join(f"{lg}×{len(a)}（{', '.join(a)}）"
                                          for lg, a in pruned) if pruned else "无"),
             ""]
    runs = conn.execute(
        "SELECT * FROM evolution_runs WHERE window_id=? ORDER BY league",
        (window_id,)).fetchall()
    n_merged = 0
    for r in runs:
        ruling = conn.execute(
            "SELECT ruling, note FROM evolution_rulings WHERE run_id=?",
            (r["id"],)).fetchone()
        head = f"## {r['league']}：{r['status']}"
        if ruling:
            head += f" → {ruling['ruling']}（{ruling['note']}）"
            n_merged += 1 if ruling["ruling"] == "merged" else 0
        lines.append(head)
        if r["no_change_reason"]:
            lines.append(f"- 说明：{r['no_change_reason']}")
        if r["proposal_path"]:
            pdir = (root / r["proposal_path"]).parent
            cpath = pdir / f"{r['league']}.json"
            epath = pdir / f"{r['league']}.evidence.json"
            if cpath.is_file():
                contract = json.loads(cpath.read_text(encoding="utf-8"))
                n_a = len(contract.get("appends") or [])
                n_m = len(contract.get("amendments") or [])
                n_d = len(contract.get("deprecations") or [])
                lines.append(f"- 变更：+{n_a} / 改{n_m} / 废{n_d}"
                             f"；**被推翻或削弱旧条目 {n_m + n_d}**（健康度）")
            if epath.is_file():
                ev = json.loads(epath.read_text(encoding="utf-8"))
                kb, nokb = ev["kb_track"], ev["nokb_track"]
                lines.append(
                    f"- kb 轨：判 {kb['n_judged']} 场（veto {kb['verdicts']['veto']}"
                    f" / down {kb['verdicts']['downweight']}），ROI {_fmt(kb['roi'])}，"
                    f"CLV 中位 {_fmt(kb['clv_median'])}，误杀 {len(ev['kills'])} 例")
                lines.append(
                    f"- nokb 轨：判 {nokb['n_judged']} 场，ROI {_fmt(nokb['roi'])}，"
                    f"CLV 中位 {_fmt(nokb['clv_median'])}；轨间分歧"
                    f" {len(ev['divergences'])} 场")
                lines.append(f"- 未结算注 {ev['n_pending_settlement']}（不进 ROI）")
        lines.append("")

    lines.append("## 版本戳串联")
    for row in conn.execute(
            "SELECT r.strategy, r.personas_hash, COUNT(*) AS n FROM recommendations r"
            " WHERE date(r.created_at, '+8 hours') >= ?"
            " AND date(r.created_at, '+8 hours') < ?"
            " GROUP BY r.strategy, r.personas_hash ORDER BY n DESC",
            (win["opened_at"], closes)):
        h = row["personas_hash"] or "（纪元前 NULL）"
        lines.append(f"- {row['strategy']} × {row['n']} 行：{h[:12]}…")
    lines.append("")
    if n_merged == 0:
        lines.append("## 基线期标注")
        lines.append("- 本窗口无 merged 版本——kb/nokb 两轨 prompt 逐字相同，"
                     "其差异为 **persona 调用噪声底**，不是知识库效应。")
        lines.append("")
    lines.append("## 判据声明（固定）")
    lines.append("- 统计判据为 ≥2 个窗口数据积累后的**后续注册项**：届时先改"
                 " spec §12.7 再启用，不得回溯套用。")
    lines.append("- 校准轮（calibration-*）结论不得作为知识库有效性证据；"
                 "三轨口径分开表述，禁调参凑结论。")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
