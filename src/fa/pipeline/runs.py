"""runs 表记录辅助（T9，spec §3.2 / §3.4 / §9.5）：每次 run 一行审计。

:func:`begin_run` 先落一行 ``status='running'`` 占位——run 中途崩溃也留下「已启动」
的痕迹（§9.5 可观测性：没有这行，失败只存在于 stderr）；:func:`finish_run` 补
``finished_at`` / 终态 ``status`` / ``summary`` / ``credits_after``。

- **credits_before** 只对 matchday 填：读 ``meta['odds_quota_remaining']``，即进入
  本次 run **前**的水位（spec §3.4「额度写 runs/meta」的「runs」侧）；daily 不拉
  实时盘，该字段填了反而是噪音，留 NULL。
- **status 词表**：runs.status 无 CHECK（T2 裁定「词表随任务演进」），故以本模块
  常量单源——``running``（占位）/ ``ok`` / ``degraded_ok`` / ``skipped`` /
  ``no_key`` / ``failed``。
- **summary**：dict → JSON 串（``ensure_ascii=False``，库里直接可读；T8 报告按键名
  消费，如 ``train_n`` / ``half_life``）。``None`` 存 NULL。
- **事务**：两个函数各自恰好一次 ``conn.commit()``——审计行必须独立存活，不被
  后续管线（甚至本次 run 自己）的成败吞掉。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from fa.db import get_meta
from fa.pipeline.fixtures import QUOTA_META_KEY

RUN_DAILY = "daily"
RUN_MATCHDAY = "matchday"

STATUS_RUNNING = "running"      # 占位：已启动未收尾
STATUS_OK = "ok"
STATUS_DEGRADED = "degraded_ok"  # 降级完成（复用快照 / 数据源失败等）
STATUS_SKIPPED = "skipped"      # 空跑（无当日赛事）
STATUS_NO_KEY = "no_key"        # 未配置 ODDS_API_KEY，未触网即退出
STATUS_FAILED = "failed"


def _now() -> datetime:
    """时间注入缝：started_at / finished_at 都从这里取（测试 monkeypatch 此函数）。"""
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _quota(text: str | None) -> int | None:
    return None if text is None else int(float(text))


def begin_run(conn: sqlite3.Connection, type: str,
              phase: str | None = None) -> int:
    """落一行「已启动」审计并返回 run id（status 暂为 :data:`STATUS_RUNNING`）。

    ``type`` 用 runs.type 词表（daily/matchday/backtest/manual，CHECK 执法）；
    ``phase`` 仅 matchday 有（am/pm，daily 为 NULL）。
    """
    credits_before = (_quota(get_meta(conn, QUOTA_META_KEY))
                      if type == RUN_MATCHDAY else None)
    cur = conn.execute(
        "INSERT INTO runs (type, phase, started_at, status, credits_before)"
        " VALUES (?, ?, ?, ?, ?)",
        (type, phase, _iso(_now()), STATUS_RUNNING, credits_before))
    conn.commit()
    return int(cur.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, status: str,
               summary: dict | None, credits_after: int | None = None) -> None:
    """补终态：``finished_at`` / ``status`` / ``summary`` / ``credits_after``。

    status 是自由文本（无 CHECK），调用方用 :data:`STATUS_*` 常量保证词表一致。
    """
    payload = (None if summary is None
               else json.dumps(summary, sort_keys=True, ensure_ascii=False))
    conn.execute(
        "UPDATE runs SET finished_at=?, status=?, summary=?, credits_after=?"
        " WHERE id=?",
        (_iso(_now()), status, payload, credits_after, run_id))
    conn.commit()
