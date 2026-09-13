"""备用赛果源 api.football-data.org 客户端（spec v0.13 §3.1/§9.5；
设计 docs/superpowers/specs/2026-09-10-fallback-results-source-design.md）。

免费层：``X-Auth-Token``、10 req/min。传输与 :mod:`fa.pipeline.odds_api`
同语义（urllib、随环境代理、超时上抛）；``FDORG_BASE_URL`` 可 env 覆盖供
测试指向本地桩（沿用 ``HERMES_BIN`` 的 mock 模式先例）。

fallback 编排（逾期集 → 精准拉取 → 三判据配对 → 落行）见下半部分
:func:`results_fallback`。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from fa.config import fdorg_token

# fa 联赛码 → fdorg competition code（v4 competitions 路径用）
LEAGUE_CODES = {"E0": "PL", "SP1": "PD", "D1": "BL1", "I1": "SA", "F1": "FL1"}
FDORG_TIMEOUT = 20


class FdorgError(Exception):
    """fdorg 调用失败。``reason``：no_token / http / network / parse。"""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


@dataclass(frozen=True)
class FdorgResult:
    utc_date: str          # "2026-09-12T14:00:00Z"（与 fixtures.kickoff_utc 同格式）
    home_name: str
    away_name: str
    fthg: int
    ftag: int


def _get(path: str, params: dict[str, str]) -> Any:
    """GET {FDORG_BASE_URL}{path}?{params}，带 X-Auth-Token，返回解析后的 JSON。"""
    token = fdorg_token()
    if not token:
        raise FdorgError("no_token")
    base = os.environ.get("FDORG_BASE_URL", "https://api.football-data.org")
    url = f"{base}{path}?{urlencode(params)}"
    req = urllib.request.Request(url, headers={"X-Auth-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=FDORG_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:            # HTTPError 先于 URLError 接
        raise FdorgError("http", str(e.code)) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise FdorgError("network", str(e)) from e
    except ValueError as e:                        # json 解析失败
        raise FdorgError("parse", str(e)) from e


def fetch_results(league: str, date_from: str, date_to: str) -> list[FdorgResult]:
    """某联赛 ``[date_from, date_to]``（含端点，UTC 日历日）的已完赛结果。

    只留 ``status=="FINISHED"`` 且 fullTime 双侧比分非 None 的行——配对三判据
    之③在源侧预过滤；①（kickoff 严格相等）②（别名方向）由
    :func:`results_fallback` 做。未知联赛 → :class:`FdorgError`。
    """
    code = LEAGUE_CODES.get(league)
    if code is None:
        raise FdorgError("parse", f"未知联赛 {league}")
    data = _get(f"/v4/competitions/{code}/matches",
                {"dateFrom": date_from, "dateTo": date_to})
    out: list[FdorgResult] = []
    for m in data.get("matches", []):
        ft = (m.get("score") or {}).get("fullTime") or {}
        if m.get("status") != "FINISHED":
            continue
        if ft.get("homeTeam") is None or ft.get("awayTeam") is None:
            continue
        out.append(FdorgResult(
            utc_date=str(m["utcDate"]),
            home_name=str((m.get("homeTeam") or {}).get("name", "")),
            away_name=str((m.get("awayTeam") or {}).get("name", "")),
            fthg=int(ft["homeTeam"]), ftag=int(ft["awayTeam"])))
    return out


def fetch_teams(league: str) -> list[str]:
    """某联赛当前赛季队名表（别名提案用；免费层 1 请求）。"""
    code = LEAGUE_CODES.get(league)
    if code is None:
        raise FdorgError("parse", f"未知联赛 {league}")
    data = _get(f"/v4/competitions/{code}/teams", {})
    return sorted(t["name"] for t in data.get("teams", []) if t.get("name"))
