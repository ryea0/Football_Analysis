"""报告渲染 / 推送门面（T9 的稳定调用面，单源指向 T8 实现）。

T8（``fa.report.render`` / ``fa.report.telegram``）曾落在**并行分支**上，本模块
当时用惰性缝 + ``ImportError`` 降级兜底，让无 T8 的主线与合流后走同一条调用路径。
T8 已合流，**降级分支已摘除**：这里只剩模块级直引 + 透传（形参顺序与 T8 签名
一一对应），``fa.pipeline.matchday`` / ``fa.pipeline.daily`` / ``fa.cli`` 的调用面
与测试 monkeypatch 点（``reporting._render`` / ``reporting._telegram``）不变。
"""

import json
import time

from fa.report import render as _render
from fa.report import telegram as _telegram


def render_matchday_report(conn, run_id, phase, summary, quota_left, degraded) -> str:
    return _render.render_matchday_report(
        conn, run_id, phase, summary, quota_left, degraded)


def render_pm_update(conn, am_run_id, pm_run_id, quota_left, degraded) -> str:
    return _render.render_pm_update(
        conn, am_run_id, pm_run_id, quota_left, degraded)


def render_settlement_brief(settle) -> str:
    return _render.render_settlement_brief(settle)


# 推送重试（M4 §7-3「瞬态失败的廉价吸收」）：失败等 _RETRY_DELAY_S 秒重试
# **恰好一次**——hermes/TG 的瞬态抖动（M4 E2E 实测遇过 1 次）就地吸收；
# 二次仍败走既有降级（False + LAST_TELEGRAM_ERROR 记第二次原因）。
# telegram 层保持零改动（subprocess 细节与降级测试原样），重试收在门面。
_RETRY_DELAY_S = 1.5


def send(text: str) -> bool:
    """推送 Telegram（失败重试恰好一次）。真实现自身不抛、只回 bool。"""
    if _telegram.send_telegram(text):
        return True
    time.sleep(_RETRY_DELAY_S)
    return _telegram.send_telegram(text)


def last_error() -> str | None:
    """最近一次推送失败原因（重试成功后为 ``None``，可溯源进 runs.summary）。"""
    return _telegram.LAST_TELEGRAM_ERROR


def resend_report(conn, run_id: int | None = None) -> dict:
    """按 run 重渲染并重推报告（``fa report send`` 内核，spec §9.3 v0.14）。

    报告正文不落库（即时渲染即时推），但渲染是 DB + ``runs.summary`` 的
    确定函数，故重发 = 按原 run 行重走同一渲染路径再推。支持 matchday
    （am 全量 / pm 更新版）与 daily 结算简报（retro 段按 batch_id 重查，
    行已不在则段落为空）。**重发不写库**——原 run 的 telegram 记录是历史
    事实，重推结果只回给调用方。返回
    ``{"run_id": int|None, "type": str|None, "sent": bool, "error": str|None}``。
    """
    if run_id is not None:
        row = conn.execute(
            "SELECT id, type, summary FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return {"run_id": None, "type": None, "sent": False,
                    "error": f"run {run_id} 不存在"}
    else:
        row = conn.execute(
            "SELECT id, type, summary FROM runs"
            " WHERE type IN ('matchday', 'daily') ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return {"run_id": None, "type": None, "sent": False,
                    "error": "无可重发的报告（仅支持 matchday / daily run）"}
    rid, rtype = row["id"], row["type"]
    summary = (json.loads(row["summary"]) if row["summary"] else {})

    if rtype == "matchday":
        degraded = bool(summary.get("degraded"))
        quota = summary.get("quota_left")
        if summary.get("report") == "pm_update":
            text = render_pm_update(conn, summary.get("am_run_id"), rid,
                                    quota, degraded)
        else:
            text = render_matchday_report(
                conn, rid, summary.get("phase") or "am", summary, quota,
                degraded)
    else:
        if not summary.get("settled"):
            return {"run_id": rid, "type": rtype, "sent": False,
                    "error": f"run {rid}（daily）当日零结算、原即无报告"}
        settle = {k: summary.get(k) for k in
                  ("settled", "won", "pnl", "clv_median")}
        text = render_settlement_brief(settle)
        retro = summary.get("retro")
        if retro and retro.get("n_ok"):
            rows = conn.execute(
                "SELECT date, league, digest, primary_tag, tags_confidence"
                " FROM retro_attributions WHERE batch_id=? AND status='ok'"
                " AND attributor=1 ORDER BY date, match_id",
                (retro["batch_id"],)).fetchall()
            text += "\n" + _render.render_retro_brief(rows, retro)

    sent = send(text)
    return {"run_id": rid, "type": rtype, "sent": sent,
            "error": (None if sent
                      else last_error() or "推送失败（未记录原因）")}
