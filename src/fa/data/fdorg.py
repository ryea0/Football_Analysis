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
import sqlite3
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


# ---------------------------------------------------------------- fallback 编排
#
# 注意：本模块属数据层，但逾期判定必须与结算判据**同锚**（paper._paired_match
# 的同一套窗语义），故在函数体内延迟 import pipeline 层——避免模块级反向
# 依赖（paper 不感知 fdorg，无环）。

FINISHED_HOURS = 4   # kickoff+4h 视为必已完赛（90min+中场+补时+缓冲）


def _normalize_name(name: str) -> str:
    """队名归一（别名提案的 auto 判据）：NFKD 去变音符、只留字母数字、小写。"""
    import unicodedata
    txt = unicodedata.normalize("NFKD", name)
    return "".join(c for c in txt if c.isalnum()).lower()


def _overdue_fixtures(conn, now: datetime) -> list[dict]:
    """已完赛但配不到完赛行的 pending 注对应 fixture（去重）。

    修正裁定（2026-09-13）：纯结果驱动——不要求主源当日失败。
    配对语义与结算判据同锚（``paper._paired_match`` 同一套窗），
    不会出现「触发了但结算仍配不上」的口径分裂。
    """
    from fa.pipeline.paper import _paired_match
    from fa.pipeline.value import _parse_kickoff
    rows = conn.execute(
        "SELECT DISTINCT f.id, f.league, f.kickoff_utc, f.home_team_id,"
        " f.away_team_id FROM bets b"
        " JOIN recommendations r ON r.id = b.recommendation_id"
        " JOIN fixtures f ON f.id = r.fixture_id"
        " WHERE b.mode='paper' AND b.status='pending'").fetchall()
    out = []
    for f in rows:
        kickoff = _parse_kickoff(f["kickoff_utc"])
        if kickoff is None or kickoff + timedelta(hours=FINISHED_HOURS) > now:
            continue                      # 未到「必已完赛」时刻：不碰
        if _paired_match(conn, f["league"], f["home_team_id"],
                         f["away_team_id"], kickoff.date()) is not None:
            continue                      # 官方行已配上：不算逾期
        out.append({"id": f["id"], "league": f["league"],
                    "kickoff_utc": f["kickoff_utc"], "kickoff": kickoff,
                    "home_team_id": f["home_team_id"],
                    "away_team_id": f["away_team_id"]})
    return out


def _alias_map(conn) -> dict[str, int]:
    """``team_aliases(source='fdorg')`` → {alias: team_id}（一次载入）。"""
    return {r["alias"]: r["team_id"] for r in conn.execute(
        "SELECT alias, team_id FROM team_aliases WHERE source='fdorg'")}


def _season_of(kickoff: datetime) -> int:
    """kickoff → 赛季起始年（7 月及以后属新赛季；与 sync._current_season_start 同型）。"""
    return kickoff.year if kickoff.month >= 7 else kickoff.year - 1


def _pair_one(fixture: dict, cand: list[FdorgResult],
              aliases: dict[str, int]) -> tuple[FdorgResult | None, str]:
    """三判据配对（设计 §4）：① utcDate 严格相等 ② 别名方向一致 ③ FINISHED
    （fetch_results 源侧已过滤）。返回 ``(命中行, ""`` 或 ``(None, reason)``；
    reason ∈ kickoff_mismatch / alias_missing:<名,...> / not_found。"""
    same_kickoff = [m for m in cand if m.utc_date == fixture["kickoff_utc"]]
    if not same_kickoff:
        # 同一对阵只差时间戳 → 明确点名（绝不能模糊对上，stopgap 实证靠的就是
        # 时间戳严格相等；差一分钟也是另一场/数据错误）
        for m in cand:
            h, a = aliases.get(m.home_name), aliases.get(m.away_name)
            if (h == fixture["home_team_id"] and a == fixture["away_team_id"]
                    and h is not None and a is not None):
                return None, "kickoff_mismatch"
        return None, "not_found"
    for m in same_kickoff:                # 同刻多场由判据②方向校验消歧
        h, a = aliases.get(m.home_name), aliases.get(m.away_name)
        if h is not None and a is not None and h == fixture["home_team_id"] \
                and a == fixture["away_team_id"]:
            return m, ""
    missing = sorted({n for m in same_kickoff
                      for n in (m.home_name, m.away_name)
                      if aliases.get(n) is None})
    if missing:
        return None, "alias_missing:" + ",".join(missing)
    return None, "not_found"


def _insert_fallback_row(conn, fixture: dict, m: FdorgResult) -> None:
    """落行与 T5 同约定（设计 §5）：对齐 team_id、date=kickoff UTC 日、season=
    起始年、fthg/ftag、raw_line 记来源；同 UNIQUE key 已有行（官方或既有
    fallback）→ INSERT OR IGNORE 不覆盖——官方为尊。"""
    raw = {"source": "api.football-data.org", "fallback": True,
           "fetched_at": datetime.now(timezone.utc).strftime(
               "%Y-%m-%dT%H:%M:%SZ"),
           "fixture_id": fixture["id"],
           "match": f"{m.home_name} {m.fthg}-{m.ftag} {m.away_name}"}
    conn.execute(
        "INSERT OR IGNORE INTO matches (league, season, date, home_team_id,"
        " away_team_id, fthg, ftag, raw_line) VALUES (?,?,?,?,?,?,?,?)",
        (fixture["league"], _season_of(fixture["kickoff"]),
         fixture["kickoff"].date().isoformat(), fixture["home_team_id"],
         fixture["away_team_id"], m.fthg, m.ftag,
         json.dumps(raw, ensure_ascii=False)))


def results_fallback(conn, *, dry_run: bool = False,
                     now: datetime | None = None) -> dict:
    """备用源结算编排（spec v0.13 §9.5；与 daily 自动步 / ``fa data
    sync-fallback`` 同一代码路径）。

    逾期集 → 按 (联赛, 日期窗) 每联赛 1 次精准拉取（dateFrom/dateTo 夹紧）
    → 三判据配对 → 落 ``matches``。返回形状恒含 ``triggered / fixtures /
    filled / unmatched / error``；未触发零值；token 缺失 ``skipped:
    "no_token"``（静默禁用，不告警——主源 N=2 告警已把人叫来）；``dry_run``
    算与拉照常、不落库（审计/应急，额外带 ``would_fill``）。单联赛失败只记
    ``error`` 不中断（T5 单文件容错同构）。**不 commit**——commit 归编排层
    （daily / CLI）。
    """
    now = now or datetime.now(timezone.utc)
    res: dict = {"triggered": False, "fixtures": 0, "filled": 0,
                 "unmatched": [], "error": []}
    overdue = _overdue_fixtures(conn, now)
    res["fixtures"] = len(overdue)
    if not overdue:
        return res
    if not fdorg_token():
        res["skipped"] = "no_token"
        return res
    res["triggered"] = True
    aliases = _alias_map(conn)
    by_league: dict[str, list[dict]] = {}
    for f in overdue:
        by_league.setdefault(f["league"], []).append(f)
    fetched: dict[str, list[FdorgResult]] = {}
    for league, fixtures in sorted(by_league.items()):
        days = sorted(f["kickoff"].date().isoformat() for f in fixtures)
        try:
            fetched[league] = fetch_results(league, days[0], days[-1])
        except FdorgError as exc:
            res["error"].append(f"{league}: {exc}")
    for f in sorted(overdue, key=lambda x: x["id"]):
        hit, reason = _pair_one(f, fetched.get(f["league"], []), aliases)
        if hit is None:
            res["unmatched"].append({"fixture_id": f["id"],
                                     "league": f["league"],
                                     "kickoff": f["kickoff_utc"],
                                     "reason": reason})
            continue
        if not dry_run:
            _insert_fallback_row(conn, f, hit)
        res["filled"] += 1
    if dry_run:
        res["would_fill"] = res["filled"]
    return res


def propose_aliases(conn) -> list[dict]:
    """fdorg 当前赛季队名 × teams 全表的别名提案（配对判据②的物料）。

    每条 ``{"league","fdorg_name","team_id","team_name","auto"}``；auto =
    归一化严格同名**且唯一命中**（跨联赛同名人多于一个 → 不自动，进人工
    清单）。编辑距离候选有意不做——五大联赛队名差异大（缩写/官方全称），
    机械近似易错，人工对照成本可接受（M3 oddsapi 44 条先例）。单联赛拉取
    失败跳过（提案是巡览工具，不 fail-fast）。
    """
    norm_index: dict[str, list[sqlite3.Row]] = {}
    for r in conn.execute("SELECT id, name FROM teams"):
        norm_index.setdefault(_normalize_name(r["name"]), []).append(r)
    out = []
    for league in sorted(LEAGUE_CODES):
        try:
            names = fetch_teams(league)
        except FdorgError:
            continue
        for n in names:
            hits = norm_index.get(_normalize_name(n), [])
            auto = len(hits) == 1
            out.append({"league": league, "fdorg_name": n,
                        "team_id": hits[0]["id"] if auto else None,
                        "team_name": hits[0]["name"] if auto else None,
                        "auto": auto})
    return out
