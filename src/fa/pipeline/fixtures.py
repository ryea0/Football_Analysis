"""fixtures 同步（T5，spec §3.1 / §3.3 / §3.4）：实时盘事件 → fixtures upsert + 快照落库。

职责与边界：
- **拉数**：逐联赛调 ``fetch_odds``（T3，管线唯一触网点），默认 h2h + totals 双
  market——T6 求 totals 的 market_p 也要价，故一次拉取两用（brief「一次拉取同时
  供 T6 用」），快照**全部**落 ``odds_snapshots``，窗口筛选留给报告层。
- **fixtures**：按 ``event_key`` upsert。kickoff / 两队 team_id 刷新（Odds API 侧
  可能改期；队名对齐结果随别名表演进而更新）；``status`` / ``created_at`` 属既有
  行生命周期，冲突时**保留**——T7 结算写下的状态不得被一次同步重置。
- **未对齐**：``align_fixture_teams`` 对不上的侧写 ``NULL``（DDL 允许、spec §3.3），
  绝不因此跳过整场；队名进隔离表由 T4 负责，本模块只聚合回传。
- **额度**（spec §3.4）：多联赛取各次响应头的最小值（保守真值）写
  ``meta['odds_quota_remaining']``；全部缺失（refresh_quota=False）则不动 meta。
  **逐联赛即写**（不等全部完成）：下一联赛再抛 :class:`OddsApiError` 时，已观测
  的最小值已在 meta 里，不会随异常蒸发；异常路径上再并入
  ``OddsApiError.quota_remaining``（同一联赛内 region 部分成功的额度，终审
  Important #2）。
- **表边界**（spec §12.1）：只写 fixtures / odds_snapshots / meta（经
  ``align_fixture_teams`` 间接写 team_aliases / unknown_names），绝不写
  backtest_predictions / matches / recommendations / bets。

事务：全程单 ``conn.commit()``（与 fa.data.teams / fa.pipeline.align「写入侧不
commit」同一约定）——任一联赛上抛即整体无半写状态（含额度水印：本模块只写
不提交，由调用方在吞掉异常后的收尾里提交——matchday 的降级路径经 ``finish_run``
恰有这一次 commit，水印随之入库）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from fa.db import set_meta
from fa.pipeline.align import align_fixture_teams
from fa.pipeline.odds_api import OddsApiError, OddsSnapshot, fetch_odds

QUOTA_META_KEY = "odds_quota_remaining"     # spec §3.4 钉死的 meta 键
SOURCE = "oddsapi"                          # fixtures.source / 队名别名 source 同源
STATUS_NEW = "scheduled"                    # 新 fixture 初始状态（词表见 db.py 注释）
DEFAULT_REGIONS = ("eu", "uk")              # 双区全扫（与 odds_api.fetch_odds 默认一致）


def sync_fixtures(conn: sqlite3.Connection, leagues: list[str],
                  regions: tuple[str, ...] = DEFAULT_REGIONS) -> dict:
    """逐联赛拉实时盘，upsert fixtures 并落全部快照；返回同步摘要。

    返回 ``{"fixtures": 本次 upsert 的场次数, "aligned": 双侧都对齐的场次数,
    "unknown": 未对齐队名（去重、字典序）, "quota_left": 剩余额度或 None}``。
    不做 kickoff 窗口筛选——窗口归报告层（brief 明示）。

    **额度节流契约**（spec §3.4「合并 region」）：``regions`` 原样透传给每次
    :func:`fetch_odds`（计费按 region × market，收窄到 ``("eu",)`` 即把单次全扫
    的 credits 减半）。本函数**只透传、不读水位**——降频决策归编排层（matchday
    读 ``meta[QUOTA_META_KEY]`` 的水位判档），保持本模块是纯数据同步；默认双区，
    既有调用方零改动。
    """
    fetched_at = _utc_now_iso()
    unknown: set[str] = set()
    fixtures = 0
    aligned = 0
    quotas: list[int] = []

    for league in leagues:
        try:
            snaps, quota = fetch_odds(league, regions=regions)
        except OddsApiError as err:
            # 该联赛内部已部分成功（前一 region 拿到额度头、后一 region 才炸）：
            # 并入已见最小值并即写 meta，再上抛——观测到的额度头一张都不丢。
            if err.quota_remaining is not None:
                quotas.append(int(err.quota_remaining))
                set_meta(conn, QUOTA_META_KEY, str(min(quotas)))
            raise
        if quota is not None:
            quotas.append(int(quota))
            # 即写而非攒到最后：下一联赛的 fetch_odds 仍可能抛，届时这个已观测的
            # 保守真值已在 meta 里（spec §3.4 的水位是降频判据，宁低勿高）。
            set_meta(conn, QUOTA_META_KEY, str(min(quotas)))
        by_event: dict[str, list[OddsSnapshot]] = {}
        for snap in snaps:
            by_event.setdefault(snap.event_key, []).append(snap)
        for event_key in sorted(by_event):          # 排序 → 插入顺序确定
            group = by_event[event_key]
            head = group[0]                          # 同一事件的队名/kickoff 全组一致
            home_id, away_id = align_fixture_teams(
                conn, league, head.home_name, head.away_name)
            for name, team_id in ((head.home_name, home_id), (head.away_name, away_id)):
                if team_id is None:
                    unknown.add(name)
            fixture_id = _upsert_fixture(
                conn, league, event_key, head, home_id, away_id, fetched_at)
            fixtures += 1
            if home_id is not None and away_id is not None:
                aligned += 1
            for snap in group:
                _insert_snapshot(conn, fixture_id, snap, fetched_at)

    quota_left = min(quotas) if quotas else None      # 最小值 = 保守真值（spec §3.4）
    if quota_left is not None:
        set_meta(conn, QUOTA_META_KEY, str(quota_left))
    conn.commit()
    return {"fixtures": fixtures, "aligned": aligned,
            "unknown": sorted(unknown), "quota_left": quota_left}


# ---------------------------------------------------------------- 内部实现


def _utc_now_iso() -> str:
    """UTC ISO 时间戳（``...Z`` 形态，与 Odds API 的 kickoff_utc 同风格）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _upsert_fixture(conn: sqlite3.Connection, league: str, event_key: str,
                    head: OddsSnapshot, home_id: int | None, away_id: int | None,
                    ts: str) -> int:
    """按 event_key upsert，返回 fixture id。

    冲突侧刷新 league / source / kickoff_utc / 两队 id；``status`` 与
    ``created_at`` 不在 SET 列表里，即冲突时保留库内现值。
    """
    conn.execute(
        "INSERT INTO fixtures (league, event_key, source, kickoff_utc,"
        " home_team_id, away_team_id, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(event_key) DO UPDATE SET"
        "   league=excluded.league, source=excluded.source,"
        "   kickoff_utc=excluded.kickoff_utc,"
        "   home_team_id=excluded.home_team_id, away_team_id=excluded.away_team_id",
        (league, event_key, SOURCE, head.kickoff_utc, home_id, away_id, STATUS_NEW, ts),
    )
    row = conn.execute(
        "SELECT id FROM fixtures WHERE event_key=?", (event_key,)).fetchone()
    assert row is not None                            # upsert 后必在（同一连接内）
    return int(row["id"])


def _insert_snapshot(conn: sqlite3.Connection, fixture_id: int, snap: OddsSnapshot,
                     fetched_at: str) -> None:
    """追加一条快照（含留档 JSON）。

    ``outcomes`` 是 T6 求最优价的消费字段；``raw`` 留档 Odds API 侧原文要点
    （队名 / kickoff / 归属）——``OddsSnapshot`` 不携带 HTTP 原始响应体（T3 的
    frozen dataclass 无 raw 位），故取「可回读的解析片段」为留档口径，fixtures
    表不存 Odds API 原文名，此处是唯一留痕。
    """
    raw = {
        "league": snap.league,
        "event_key": snap.event_key,
        "kickoff_utc": snap.kickoff_utc,
        "home_name": snap.home_name,
        "away_name": snap.away_name,
        "market": snap.market,
        "region": snap.region,
        "bookmaker": snap.bookmaker,
        "outcomes": snap.outcomes,
    }
    conn.execute(
        "INSERT INTO odds_snapshots (fetched_at, fixture_id, event_key, market,"
        " region, bookmaker, outcomes, raw) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (fetched_at, fixture_id, snap.event_key, snap.market, snap.region,
         snap.bookmaker, json.dumps(snap.outcomes, sort_keys=True),
         json.dumps(raw, sort_keys=True)),
    )
