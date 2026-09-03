"""结算日课（T10，spec §3.4 / §7.3 / §9.5 / §9.6）：``fa run daily``。

每日 06:30 的一条 job，三步：**完赛同步 → 结算 → （有结算才）一行简报**。

**sync 先行**：结算依赖新完赛数据（bets 的判胜与 ``closing_odds`` 都来自
``matches`` 的新行，§3.4「每日：更新已完赛场次，含收盘赔率，供 CLV 结算」），
故 ``sync_history`` 必须在 ``settle_paper_bets`` 之前跑；``sync_history`` 抛错时
捕获降级，用库内旧数据照常结算，并在 ``runs.summary['sync_error']`` 标注
（§9.5「数据源失败 → 用最近缓存 + 告警」）——单文件级失败由 ``sync_history``
自己记账（``SyncReport.file_errors``），不外溢。

**静默规则**：``settled == 0`` 时不推送——每日空结算推送是噪音；推送失败只把
原因写进 summary，不改变状态（结算已落库，§9.5 降级不中断）。

**表边界（§12.1）**：``fa.data.sync_history`` 写 ``matches`` 是 A 线表的共享维护
步（本模块是 B 线唯一触达 ``matches`` 的入口，且是复用 A 线既有函数、非 B 线自写）；
B 线实时侧（fixtures / value / paper / matchday）依旧不碰它。其余写入与 paper
 Provider 一致：``bets`` / ``fixtures.status`` / ``meta``，另写 runs（审计）。
"""

from __future__ import annotations

import sqlite3

from fa.data.sync import SyncReport, sync_history
from fa.pipeline.paper import settle_paper_bets
from fa.pipeline.reporting import last_error, render_settlement_brief, send
from fa.pipeline.runs import (RUN_DAILY, STATUS_DEGRADED, STATUS_FAILED,
                              STATUS_OK, begin_run, finish_run)


def run_daily(conn: sqlite3.Connection) -> dict:
    """跑一次日课，返回摘要 dict：``status`` / ``run_id`` / ``settled`` / ``won``
    / ``pnl`` / ``clv_median`` / ``sent``（None = 未推送）/ ``sync``（同步摘要或
    None = 同步失败降级）。"""
    run_id = begin_run(conn, RUN_DAILY, None)
    try:
        return _run(conn, run_id)
    except Exception as exc:
        finish_run(conn, run_id, STATUS_FAILED,
                   {"error": f"{type(exc).__name__}: {exc}"})
        raise


def _run(conn: sqlite3.Connection, run_id: int) -> dict:
    # 结算依赖新完赛数据故 sync 先行；失败降级用旧数据并标注（brief 钉死的顺序理由）
    rep: SyncReport | None = None
    sync_error: str | None = None
    try:
        rep = sync_history(conn)                     # 静默：不打日志，摘要进 runs
    except Exception as exc:                         # 整体性失败才到这（单文件已内记账）
        sync_error = f"{type(exc).__name__}: {exc}"

    settle = settle_paper_bets(conn)
    summary = {
        "settled": settle["settled"],
        "won": settle["won"],
        "pnl": settle["pnl"],
        "clv_median": settle["clv_median"],
        "sync": (None if rep is None else
                 {"files_ok": rep.files_ok, "inserted": rep.inserted,
                  "file_errors": len(rep.file_errors)}),
        "sync_error": sync_error,
        "telegram": None,
    }

    sent: bool | None = None
    if settle["settled"] > 0:
        sent = send(render_settlement_brief(settle))
        summary["telegram"] = ({"sent": True, "error": None} if sent else
                               {"sent": False,
                                "error": last_error() or "推送失败（未记录原因）"})

    status = STATUS_DEGRADED if sync_error else STATUS_OK
    finish_run(conn, run_id, status, summary)
    return {"status": status, "run_id": run_id, "settled": settle["settled"],
            "won": settle["won"], "pnl": settle["pnl"],
            "clv_median": settle["clv_median"], "sent": sent,
            "sync": summary["sync"]}
