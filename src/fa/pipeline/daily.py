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
 Provider 一致：``bets`` / ``fixtures.status`` / ``meta``，另写 runs（审计）；
复盘步（Stage 2）经 ``fa.retro.pipeline`` 写 ``retro_runs`` / ``retro_attributions``。
"""

from __future__ import annotations

import sqlite3

from fa.config import project_root
from fa.data.sync import SyncReport, sync_history
from fa.pipeline.paper import settle_paper_bets
from fa.pipeline.reporting import last_error, render_settlement_brief, send
from fa.pipeline.runs import (RUN_DAILY, STATUS_DEGRADED, STATUS_FAILED,
                              STATUS_OK, begin_run, finish_run)
from fa.report.render import render_retro_brief
from fa.retro.pipeline import run_retro_batch
from fa.retro.select import select_paper_t1


def _yesterday() -> str:
    """昨日（kickoff 日历日）——独立函数供测试 monkeypatch（防时间炸弹）。"""
    from datetime import date, timedelta
    return (date.today() - timedelta(days=1)).isoformat()


def run_daily(conn: sqlite3.Connection) -> dict:
    """跑一次日课，返回摘要 dict：``status`` / ``run_id`` / ``settled`` / ``won``
    / ``pnl`` / ``clv_median`` / ``sent``（None = 未推送）/ ``sync``（同步摘要或
    None = 同步失败降级）/ ``retro``（昨日推荐复盘批摘要，None = 昨日无场次或
    未跑）/ ``retro_error``（批整体异常降级原因，str 或 None）。"""
    run_id = begin_run(conn, RUN_DAILY, None)
    try:
        return _run(conn, run_id)
    except Exception as exc:
        # 先回滚：半写的阶段产物不得搭 finish_run 的 commit 一起入库
        conn.rollback()
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
    # Stage 2：paper T+1 复盘（设计 §4/§10）——sync 已把完赛行入库，昨日推荐
    # 场次可归因。选场为空则静默跳过（非事件）；批整体异常只记账不中断日课
    # （§9——单场失败在批内逐场降级，这里兜的是编排层异常）。attributors=1
    # （v1 单跑；ensemble 属 A_multi 侧演化，daily 不上量）。
    retro = None
    retro_error = None
    try:
        day = _yesterday()
        t1c, t1meta = select_paper_t1(conn, day)
        if t1c:
            retro = run_retro_batch(
                conn, t1c, "paper_t1", {"date": day, **t1meta},
                project_root() / "data" / "retro" / "inputs")
    except Exception as exc:
        retro = None
        retro_error = f"{type(exc).__name__}: {exc}"
    # 降级判据看 SyncReport 而非「是否抛错」：sync_history 对单文件失败自记账不外溢
    # （§3.4 容错设计），所以「抛错」几乎不发生；真用了旧数据结算是「一个文件都没成
    # 却报了错」——files_ok==0 且 file_errors>0。部分成功（files_ok>0）仍算拿到新完
    # 赛，不算降级
    sync_degraded = bool(sync_error) or bool(
        rep is not None and rep.file_errors and not rep.files_ok)
    summary = {
        "settled": settle["settled"],
        "won": settle["won"],
        "pnl": settle["pnl"],
        "clv_median": settle["clv_median"],
        "sync": (None if rep is None else
                 {"files_ok": rep.files_ok, "inserted": rep.inserted,
                  "file_errors": len(rep.file_errors)}),
        "sync_error": sync_error,
        "sync_degraded": sync_degraded,
        "retro": retro,
        "retro_error": retro_error,
        "telegram": None,
    }

    sent: bool | None = None
    if settle["settled"] > 0:
        brief = render_settlement_brief(settle)
        if retro is not None and retro["n_ok"]:
            rows = conn.execute(
                "SELECT date, league, digest, primary_tag, tags_confidence"
                " FROM retro_attributions WHERE batch_id=? AND status='ok'"
                " AND attributor=1 ORDER BY date, match_id",
                (retro["batch_id"],)).fetchall()
            brief += "\n" + render_retro_brief(rows, retro)
        sent = send(brief)
        summary["telegram"] = ({"sent": True, "error": None} if sent else
                               {"sent": False,
                                "error": last_error() or "推送失败（未记录原因）"})

    status = STATUS_DEGRADED if sync_degraded else STATUS_OK
    finish_run(conn, run_id, status, summary)
    return {"status": status, "run_id": run_id, "settled": settle["settled"],
            "won": settle["won"], "pnl": settle["pnl"],
            "clv_median": settle["clv_median"], "sent": sent,
            "sync": summary["sync"], "retro": summary["retro"],
            "retro_error": summary["retro_error"]}
