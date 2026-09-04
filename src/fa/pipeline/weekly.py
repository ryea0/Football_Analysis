"""周度小结（M5 §10「周度小结」，2026-09-04 负责人裁定按设计实施）：``fa ops weekly``。

**窗口 = 上个自然周**（北京时间周一 00:00 – 周日 24:00，固定 +8 无夏令时）；
cron 槽定在周一 07:00——daily（06:30 结算）之后、am（11:00 拉盘）之前，
小结读到的是完整结算后的上一周。

**口径**（与 :func:`fa.pipeline.paper.paper_summary` 同族，但按时间窗切）：

- ``placed``：``placed_at`` 落窗的 paper 注数（join recommendations 分轨）
- ``settled`` / ``won`` / ``pnl`` / ``roi``：``settled_at`` 落窗的终态注——
  ROI 的分母是**已结算注的 stake**（在途仓位不进分母，口径同 paper_summary）
- ``clv_median``：窗口内已结算注的 CLV 中位数（缺收盘者不计；基准混合时以
  ``bets.closing_source`` 可溯源，spec §7.3）
- ``bankroll_end``：当前 ``meta`` 分轨余额；``bankroll_start = end − pnl``
  （余额只在结算时变动，期初可由期末倒推——零真金账本的自洽性质）
- ``pending``：**全时**在途注积压（不只窗口内——积压本来就是全时观察项）
- **空周静默**：双轨 ``placed`` 与 ``settled`` 全零 → ``empty=True``，CLI
  不推送（空周推送是噪音）；日志记一行

表边界（§12.1）：只读 bets / recommendations / meta，不写任何表——小结是
观察者，不是管线步骤。
"""

from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fa.db import get_meta
from fa.pipeline.paper import SETTLED_STATUSES, bankroll_key
from fa.pipeline.reporting import send
from fa.pipeline.value import STRATEGIES

BEIJING = timezone(timedelta(hours=8))     # 固定偏移，无夏令时（spec §9.6 同款）


def _now() -> datetime:
    """时间注入缝（唯一）：窗口判定从这里取，测试 monkeypatch 此函数。"""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class WeekWindow:
    start: datetime
    end: datetime


def last_week_window(now: datetime | None = None) -> WeekWindow:
    """上个自然周 ``[周一 00:00, 次周一 00:00)``（北京时间，左闭右开）。"""
    moment = (now or _now()).astimezone(BEIJING)
    this_monday = moment.replace(hour=0, minute=0, second=0, microsecond=0) \
        - timedelta(days=moment.weekday())
    start = this_monday - timedelta(days=7)
    return WeekWindow(start=start, end=this_monday)


def _utc_iso(moment: datetime) -> str:
    """与 bets.placed_at / settled_at 同构的 UTC 串（字典序即时间序）。"""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def weekly_summary(conn: sqlite3.Connection) -> dict:
    """上周 paper 双轨汇总（口径见模块 docstring）。空周 → ``{"empty": True, ...}``。"""
    win = last_week_window()
    ws, we = _utc_iso(win.start), _utc_iso(win.end)
    by_strategy: dict[str, dict] = {}
    total_activity = 0
    for strategy in STRATEGIES:
        placed = conn.execute(
            "SELECT COUNT(*) AS n FROM bets b"
            " JOIN recommendations r ON r.id = b.recommendation_id"
            " WHERE b.mode='paper' AND r.strategy=? AND b.placed_at>=?"
            " AND b.placed_at<?", (strategy, ws, we)).fetchone()["n"]
        row = conn.execute(
            "SELECT COUNT(*) AS settled,"
            " COALESCE(SUM(CASE WHEN b.status='won' THEN 1 ELSE 0 END),0) AS won,"
            " COALESCE(SUM(b.stake), 0.0) AS staked,"
            " COALESCE(SUM(b.return_amt), 0.0) AS returned"
            " FROM bets b"
            " JOIN recommendations r ON r.id = b.recommendation_id"
            " WHERE b.mode='paper' AND r.strategy=? AND b.settled_at>=?"
            " AND b.settled_at<?", (strategy, ws, we)).fetchone()
        clvs = [r["clv"] for r in conn.execute(
            "SELECT b.clv AS clv FROM bets b"
            " JOIN recommendations r ON r.id = b.recommendation_id"
            " WHERE b.mode='paper' AND r.strategy=? AND b.settled_at>=?"
            " AND b.settled_at<? AND b.clv IS NOT NULL", (strategy, ws, we))]
        pnl = float(row["returned"]) - float(row["staked"])
        raw = get_meta(conn, bankroll_key(strategy))
        end = None if raw is None else float(raw)
        by_strategy[strategy] = {
            "placed": int(placed),
            "settled": int(row["settled"]),
            "won": int(row["won"]),
            "pnl": round(pnl, 2),
            "roi": (pnl / float(row["staked"])) if row["staked"] else None,
            "clv_median": (statistics.median(clvs) if clvs else None),
            "bankroll_end": end,
            "bankroll_start": (None if end is None else round(end - pnl, 2)),
        }
        total_activity += int(placed) + int(row["settled"])
    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM bets b"
        " JOIN recommendations r ON r.id = b.recommendation_id"
        " WHERE b.mode='paper' AND b.status='pending'").fetchone()["n"]
    return {
        "empty": total_activity == 0,
        "week": (f"{win.start.strftime('%Y-%m-%d')}"
                 f" ~ {(win.end - timedelta(days=1)).strftime('%Y-%m-%d')}"),
        "by_strategy": by_strategy,
        "pending": int(pending),
    }


def render_weekly(summary: dict) -> str:
    """TG 周报正文：双轨对照行 + 合计 + 在途积压（空周由调用方拦下，不进这里）。"""
    lines = [f"📋 周度小结（{summary['week']}，paper）"]
    for strategy, s in summary["by_strategy"].items():
        roi = "—" if s["roi"] is None else f"{s['roi']:+.1%}"
        clv = "—" if s["clv_median"] is None else f"{s['clv_median']:+.1%}"
        roll = ("—" if s["bankroll_start"] is None
                else f"{s['bankroll_start']:.0f}→{s['bankroll_end']:.0f}")
        lines.append(
            f"· {strategy}：落 {s['placed']} / 结 {s['settled']}"
            f"（中 {s['won']}），ROI {roi}，CLV 中位 {clv}，bankroll {roll}")
    lines.append(f"在途 pending：{summary['pending']} 注")
    lines.append("——fa 周度小结（判据时钟与口径见 spec §12.3/§7.3）")
    return "\n".join(lines)


def run_weekly(conn: sqlite3.Connection) -> dict:
    """判空 + 渲染 + 推送一体：返回 ``{"empty": bool, "sent": bool | None}``。

    ``sent=None`` = 空周静默（区别于「推了但失败」的 ``False``）。
    """
    summary = weekly_summary(conn)
    if summary["empty"]:
        return {"empty": True, "sent": None}
    return {"empty": False, "sent": send(render_weekly(summary))}
