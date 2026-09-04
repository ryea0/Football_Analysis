"""run 编排（设计 §7）：信息集 → prompt → dsh → 契约 → 落库 → 台账。

幂等续跑：只处理「有信息集 JSON 且该 line 无 ok 行」的场次；dsh 整体不可
用时逐场记 error 行、run 正常结束（诚实降级——失败可见、可审计，不中断）。
"""
import json
import sqlite3
from pathlib import Path

from fa.agentline import runner as runner_mod
from fa.agentline.contract import parse_prediction
from fa.agentline.debate import run_match_debate
from fa.agentline.runner import _PROFILE_LINE, build_prompt
from fa.agentline.store import save_debate_round, save_prediction, save_run

# 审计常量（设计 §6 harness/model 列）：值来自 Task 2 spike 实测（附录 A）——
# dsh 版本 0.1.1-rc.2（npm 钉死版，见附录 A.1）；模型为 ARK plan 端点的
# ark-code-latest（附录 A.3，与 Hermes 同源同凭证）。
MODEL = "ark-code-latest"
_HARNESS = "dsh 0.1.1-rc.2"


def _status_of(parsed: dict, run_err: str | None) -> tuple[dict, str]:
    """dsh 层失败 → timeout/error；成功但契约不过 → parse_fail。"""
    if run_err is not None:
        st = "timeout" if "超时" in run_err else "error"
        parsed = {**parsed, "status": st,
                  "reasoning_digest": f"dsh 失败：{run_err}"}
    return parsed, parsed["status"]


def run_line(conn: sqlite3.Connection, line: str, info_dir: Path,
             limit: int | None = None) -> dict:
    from fa.agentline.store import _now
    t0 = _now()                       # 台账起点＝批次起点，非批尾落库时刻
    profile = _PROFILE_LINE[line]
    done = {r["match_id"] for r in conn.execute(
        "SELECT match_id FROM agentline_predictions"
        " WHERE line=? AND status='ok'", (line,))}
    # 防呆：目录里混进非 match_id 命名的 JSON（杂散文件/agent 写散的产物）直接
    # 跳过——int() 一次 ValueError 就会让整批跑不下去。
    todo = sorted(int(p.stem) for p in info_dir.glob("*.json")
                  if p.stem.isdigit() and int(p.stem) not in done)
    if limit is not None:
        todo = todo[:limit]
    counts = {"ok": 0, "parse_fail": 0, "timeout": 0, "error": 0}
    summary = {"n_todo": len(todo), "info_dir": str(info_dir)}
    try:
        for mid in todo:
            info = json.loads((info_dir / f"{mid}.json").read_text(
                encoding="utf-8"))
            out, err, dur = runner_mod.run_headless(
                build_prompt(info, line), profile)
            parsed, status = _status_of(parse_prediction(out or ""), err)
            save_prediction(conn, mid, line, parsed, out or "", _HARNESS,
                            MODEL, dur)
            counts[status] += 1
    except Exception:
        # 批中崩溃也必须留台账（已累计计数 + 中断位）再抛——否则留下
        # 「predictions>0 且 runs=0」的无痕中断，与正常批中观察无法区分
        # （先例 4a8b05a，retro 同型修复；幂等续跑凭 ok 行集合 + 中断位定位）。
        save_run(conn, line, profile, MODEL, counts,
                 {**summary, "interrupted_match_id": mid}, started_at=t0)
        raise
    save_run(conn, line, profile, MODEL, counts, summary, started_at=t0)
    return counts


def run_multi(conn: sqlite3.Connection, info_dir: Path, members: int = 3,
              limit: int | None = None) -> dict:
    """A_multi（multi-brain spec §3.1）：N 个独立会话同信息集各自预测 → 聚合。

    成员用 A_base prompt/profile（无检索——异构/检索成员属 gated-2）。
    幂等：聚合行已 ok 的场次跳过（成员行不判重——重跑会覆盖，ON CONFLICT
    三元 upsert 语义与单跑一致）。
    """
    from fa.agentline.ensemble import aggregate_predictions
    profile = _PROFILE_LINE["A_base"]
    done = {r["match_id"] for r in conn.execute(
        "SELECT match_id FROM agentline_predictions"
        " WHERE line='A_multi' AND attributor=0 AND status='ok'")}
    todo = sorted(int(p.stem) for p in info_dir.glob("*.json")
                  if p.stem.isdigit() and int(p.stem) not in done)
    if limit is not None:
        todo = todo[:limit]
    counts = {"ok": 0, "parse_fail": 0, "timeout": 0, "error": 0}
    for mid in todo:
        info = json.loads((info_dir / f"{mid}.json").read_text(encoding="utf-8"))
        prompt = build_prompt(info, "A_base")
        member_results = []
        total_dur = 0.0
        for i in range(members):
            out, err, dur = runner_mod.run_headless(prompt, profile)
            total_dur += dur
            parsed = parse_prediction(out or "")
            parsed, status = _status_of(parsed, err)
            save_prediction(conn, mid, "A_multi", parsed, out or "",
                            _HARNESS, MODEL, dur, attributor=i + 1)
            member_results.append(parsed if status == "ok" else None)
        agg = aggregate_predictions(member_results)
        save_prediction(conn, mid, "A_multi", agg, "", _HARNESS, MODEL,
                        total_dur, attributor=0)
        counts["ok" if agg["status"] == "ok" else "error"] += 1
    save_run(conn, "A_multi", profile, MODEL, counts,
             {"n_todo": len(todo), "members": members,
              "info_dir": str(info_dir)})
    return counts


def run_debate(conn: sqlite3.Connection, info_dir: Path,
               limit: int | None = None) -> dict:
    """A_debate（2026-09-05 设计 §2）：单场生成者-批评者-修订链 ≤2 轮。

    幂等：line='A_debate' 且 attributor=1 已 ok 的场次跳过（rounds 行不判重
    ——重跑覆盖，与 run_multi 成员行语义一致）。批中崩溃也留台账再抛
    （run_line 同型：防「predictions>0 且 runs=0」无痕中断）。
    """
    profile = _PROFILE_LINE["A_base"]
    done = {r["match_id"] for r in conn.execute(
        "SELECT match_id FROM agentline_predictions"
        " WHERE line='A_debate' AND attributor=1 AND status='ok'")}
    todo = sorted(int(p.stem) for p in info_dir.glob("*.json")
                  if p.stem.isdigit() and int(p.stem) not in done)
    if limit is not None:
        todo = todo[:limit]
    counts = {"ok": 0, "parse_fail": 0, "timeout": 0, "error": 0}
    agg = {"n_calls": 0, "budget_exhausted": 0, "early_stop": 0}
    from fa.agentline.store import _now
    t0 = _now()
    summary = {"n_todo": len(todo), "info_dir": str(info_dir)}
    try:
        for mid in todo:
            info = json.loads((info_dir / f"{mid}.json").read_text(
                encoding="utf-8"))
            res = run_match_debate(
                lambda p: runner_mod.run_headless(p, profile), info)
            for row in res["rounds"]:
                save_debate_round(conn, mid, row["round"], row["role"],
                                  row["payload"], row["raw"], row["status"],
                                  row["dur"], _HARNESS, MODEL)
            save_prediction(conn, mid, "A_debate", res["final"], "",
                            _HARNESS, MODEL,
                            sum(r["dur"] for r in res["rounds"]),
                            attributor=1,
                            budget_exhausted=res["budget_exhausted"])
            counts[res["final"]["status"]] += 1
            agg["n_calls"] += res["n_calls"]
            agg["budget_exhausted"] += res["budget_exhausted"]
            agg["early_stop"] += res["early_stop"]
    except Exception:
        save_run(conn, "A_debate", profile, MODEL, counts,
                 {**summary, **agg, "interrupted_match_id": mid},
                 started_at=t0)
        raise
    save_run(conn, "A_debate", profile, MODEL, counts, {**summary, **agg},
             started_at=t0)
    return counts
