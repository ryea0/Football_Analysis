"""进化事件滚动报告（设计档 §10）——**Task 10 的空桩**。

runner 在 Task 10 就 import 本模块（``REP.write_event_report``），真实现归
Task 12（docs/evolution/report-YYYY-MM-DD.md 逐窗滚动）；桩只保 import 不断，
期间 tick/reflect 的报告产物为空（无文件落盘）——功能缺口由 T12 在 T13 E2E
前补齐（计划 Task 10 Step 4 实现顺序注 / SDD Ruling 2）。
"""
from __future__ import annotations

import sqlite3


def write_event_report(conn: sqlite3.Connection, window_id: int, *,
                       pruned: tuple = (), today=None) -> None:
    """T10 桩：签名即 T12 的契约，暂不产生任何输出。"""
    return None
