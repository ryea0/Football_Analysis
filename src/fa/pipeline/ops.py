"""运维告警（M5 运营基建，spec 风险 #6：「跑批失败要有 TG 告警，daily 失败超 1 天
即告警」）：``fa ops alert`` / ``fa ops watchdog``。

cron 载体（system crontab 与 hermes cron，2026-09-04 裁定双载体可切换）只在
``scripts/fa_cron.sh`` 里调这两个命令——**告警语义全部收在 fa 内**，换载体零改动：

- **alert**：薄封装复用报告推送路径（:func:`fa.pipeline.reporting.send`），失败只回
  ``False`` 不抛——告警不因推送失败加罪（调用方 ``|| true`` 兜底，见 wrapper）。
- **watchdog**：查 runs 表的**纯查询判据、不依赖时钟**——取最近两次成功
  （ok / degraded_ok）daily 的 ``started_at`` 间隔，间隔即漏跑 / 连续失败的证据；
  间隔 > :data:`DAILY_GAP_ALERT_HOURS` 才告警。覆盖两种形态：连续失败（failed 行
  不算成功，间隔自然拉大）与机器宕机漏跑（缺的那天没有行）。只在 daily wrapper
  的 job 之后调一次，天然每日至多一条。
- 零 / 单次成功 daily 不比间隔（单次＝刚装配首轮，无从判漏跑；零次＝告警一次
  「从未有成功」）。matchday 行与 daily 无关，不进判据。

表边界（§12.1）：本模块**只读** runs，不写任何表——watchdog 自己不落 run 行，
它是运维观察者不是管线步骤。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from fa.pipeline.reporting import send
from fa.pipeline.runs import RUN_DAILY, STATUS_DEGRADED, STATUS_OK

# spec 风险 #6「daily 失败超 1 天即告警」：24h 节律 + 1h cron 漂移余量；严格大于
# 才告警（与额度梯子「严格小于」同款边界钉法，恰值不触发）。
DAILY_GAP_ALERT_HOURS = 25


def send_alert(text: str) -> bool:
    """推送一条告警文本（复用报告推送路径；真实现自身不抛、只回 bool）。"""
    return send(text)


def check_daily(conn: sqlite3.Connection) -> str | None:
    """纯查询：daily 健康判据。返回告警文本或 ``None``（健康），**不推送**。

    判据＝最近两次成功 daily 的 ``started_at`` 间隔 >
    :data:`DAILY_GAP_ALERT_HOURS`。``started_at`` 是 runs._iso 的固定格式
    （``%Y-%m-%dT%H:%M:%SZ``），字典序即时间序，字符串排序即最新在前。
    """
    rows = conn.execute(
        "SELECT id, started_at FROM runs WHERE type=? AND status IN (?, ?)"
        " ORDER BY started_at DESC, id DESC LIMIT 2",
        (RUN_DAILY, STATUS_OK, STATUS_DEGRADED)).fetchall()
    if not rows:
        return ("⚠️ fa watchdog：runs 表中没有任何成功的 daily run——"
                "若 cron 已装配，请检查跑批")
    if len(rows) < 2:
        return None                       # 单次成功＝刚装配首轮，无从判漏跑
    latest, prev = rows[0], rows[1]
    gap_h = ((datetime_from_iso(latest["started_at"])
              - datetime_from_iso(prev["started_at"])).total_seconds() / 3600)
    if gap_h <= DAILY_GAP_ALERT_HOURS:
        return None
    return (f"⚠️ fa watchdog：daily 疑似漏跑/连续失败——最近两次成功 daily 间隔 "
            f"{gap_h:.1f}h（阈值 {DAILY_GAP_ALERT_HOURS}h）。"
            f"最近成功 #{latest['id']} {latest['started_at']}；"
            f"上一次 #{prev['id']} {prev['started_at']}")


def run_watchdog(conn: sqlite3.Connection) -> dict:
    """判据 + 推送一体：返回 ``{"alert": str | None, "sent": bool | None}``。

    ``sent=None`` 表示无告警可推（区别于「推了但失败」的 ``False``）。
    """
    alert = check_daily(conn)
    if alert is None:
        return {"alert": None, "sent": None}
    return {"alert": alert, "sent": send_alert(alert)}


def datetime_from_iso(text: str) -> datetime:
    """runs.started_at（UTC ISO 带 Z）→ aware datetime。格式归 runs._iso 所有。"""
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
