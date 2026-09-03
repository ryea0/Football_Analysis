"""run 编排（设计 §7）：信息集 → prompt → dsh → 契约 → 落库 → 台账。

幂等续跑：只处理「有信息集 JSON 且该 line 无 ok 行」的场次；dsh 整体不可
用时逐场记 error 行、run 正常结束（诚实降级——失败可见、可审计，不中断）。
"""
import json
import sqlite3
from pathlib import Path

from fa.agentline import runner as runner_mod
from fa.agentline.contract import parse_prediction
from fa.agentline.runner import _PROFILE_LINE, build_prompt
from fa.agentline.store import save_prediction, save_run

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
    profile = _PROFILE_LINE[line]
    done = {r["match_id"] for r in conn.execute(
        "SELECT match_id FROM agentline_predictions"
        " WHERE line=? AND status='ok'", (line,))}
    todo = sorted((int(p.stem) for p in info_dir.glob("*.json")
                   if int(p.stem) not in done))
    if limit is not None:
        todo = todo[:limit]
    counts = {"ok": 0, "parse_fail": 0, "timeout": 0, "error": 0}
    for mid in todo:
        info = json.loads((info_dir / f"{mid}.json").read_text(encoding="utf-8"))
        out, err, dur = runner_mod.run_headless(
            build_prompt(info, line), profile)
        parsed, status = _status_of(parse_prediction(out or ""), err)
        save_prediction(conn, mid, line, parsed, out or "", _HARNESS,
                        MODEL, dur)
        counts[status] += 1
    save_run(conn, line, profile, MODEL, counts,
             {"n_todo": len(todo), "info_dir": str(info_dir)})
    return counts
