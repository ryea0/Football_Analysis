"""OddsProvider 抽象与 The Odds API v4 客户端（spec §3.1 / §3.4 / §9.8）。

管线中唯一的网络模块：`_http_get` 是唯一触网点，测试 monkeypatch 它即可离线回放。
职责止于「拉取 + 解析 + 汇总最优价」——**不写库**：`fetch_odds` 返回
`(snapshots, quota_left)`，快照落 `odds_snapshots` 与额度落 `meta`（key=
`odds_quota_remaining`，spec §3.4）都由调用方（Task 5 / Task 9）持久化。

Provider 抽象（spec §9.8）：`fetch_odds`（别名 `OddsApiProvider`）即 OddsProvider
接口在本期的 B 线形态——二期 in-play / 交易所 Provider 实现同一签名即可替换，
消费方（T5/T6）无感。
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from fa.config import ODDS_SPORT_KEYS, odds_api_key

API_BASE = "https://api.the-odds-api.com/v4/sports"
TIMEOUT = 30                                # 与 fa.data.download 同值
QUOTA_HEADER = "x-requests-remaining"       # 响应头键统一转小写后读取
TARGET_TOTALS_LINE = 2.5                    # 只取 2.5 线（spec §11 风险 4）
LINE_TOL = 1e-9

_H2H_SIDES = {"Draw": "draw"}               # 队名槽位在解析时按 home/away 动态填
_TOTALS_SIDES = {"Over": "over", "Under": "under"}

# 最近一次 fetch_odds 的统计，含被滤掉的非 2.5 线计数（spec §11 风险 4：
# 「一期只取 2.5 线，其余丢弃并计数」）。签名钉死为 (snaps, quota)，故经此
# 模块级字典外漏，Task 8 报告读取并标注。fetch_odds 开头清空、成功结束写回，
# 失败的调用不写（读到空 dict 即「最近一次拉取未完成」）。
LAST_FETCH_STATS: dict[str, int] = {"events": 0, "snapshots": 0, "dropped_totals_outcomes": 0}

# 最近一次「部分成功」的额度快照（终审 Important #2）：region 循环已观测到额度头
# 之后才失败时，把已见最小值记在此（镜像 LAST_FETCH_STATS 的旁路观测口；主出口是
# 异常对象上的 ``OddsApiError.quota_remaining``）。fetch_odds 开头清空、命中部分
# 失败才写——读到空 dict 即「最近一次失败前没见过任何额度头」。
LAST_PARTIAL_QUOTA: dict[str, int] = {}


class OddsApiError(RuntimeError):
    """Odds API 取数失败：无 key / 未知联赛 / HTTP 4xx 5xx（含 429 限流）/ 网络异常 / 非 JSON。

    ``quota_remaining``：失败前已观测到的最小剩余额度（``None``＝没见过任何额度头，
    如无 key / 未知联赛 / 首个 region 就失败）。由 :func:`fetch_odds` 在部分失败
    路径挂上，调用方的 except 路径据此把水位落 meta，不让已消耗的额度观测蒸发。
    """

    quota_remaining: int | None = None

    def __init__(self, message: str, quota_remaining: int | None = None) -> None:
        super().__init__(message)
        self.quota_remaining = quota_remaining


@dataclass(frozen=True)
class OddsSnapshot:
    """单场单 bookmaker 单 market 的实时盘快照（T5 落库、T6 求最优价的最小单元）。

    `outcomes` 语义：
    - h2h    -> ``{"home": 赔率, "draw": 赔率, "away": 赔率}``（队名按响应的
      home_team / away_team 归一，'Draw' 归 draw）
    - totals -> ``{"over": 赔率, "under": 赔率}``，且仅保留 point == 2.5 的线

    `kickoff_utc` 保留 API 原文（ISO-8601 UTC，如 ``2026-09-05T14:00:00Z``），
    不在此处做时区换算；`region` 为单值（``"eu"`` / ``"uk"``，与
    `odds_snapshots.region` 口径一致）。
    """

    league: str                                  # 内部联赛码（E0…），非 sport key
    event_key: str
    kickoff_utc: str
    home_name: str
    away_name: str
    market: str                                  # 'h2h' | 'totals'
    region: str
    bookmaker: str
    outcomes: dict[str, float]


def fetch_odds(
    league: str,
    markets: tuple[str, ...] = ("h2h", "totals"),
    regions: tuple[str, ...] = ("eu", "uk"),
    refresh_quota: bool = True,
) -> tuple[list[OddsSnapshot], int | None]:
    """拉某联赛实时盘，返回 ``(快照列表, 剩余额度)``。

    **每个 region 单独发一次请求**（regions 逐个传入）：The Odds API 的响应
    不带 bookmaker→region 归属，拆开才能让快照的 `region` 落到单值（与
    `odds_snapshots.region` 的 `eu` / `uk` 口径一致），并留出按 region 降频
    （spec §3.4）的抓手；计费按 region × market，拆分与合并**等价**。
    额度只在 ``refresh_quota=True`` 时读各次响应头并取**最小值**（保守真值）
    随返回值交调用方落库；本函数自身不写 DB。任一 region 失败则整次上抛——但
    前面 region 已观测到的最小额度不丢：挂在所抛 :class:`OddsApiError` 的
    ``quota_remaining`` 上（并记入 :data:`LAST_PARTIAL_QUOTA`），调用方在 except
    路径落 meta，否则库内水位停留在旧值、降频判据（spec §3.4）被虚高污染。

    429 / 其他 HTTP 错误 / 网络异常上抛 :class:`OddsApiError`。
    """
    LAST_FETCH_STATS.clear()                        # 任何失败（无 key / 未知联赛 / 网络）都留 {}
    LAST_PARTIAL_QUOTA.clear()                      # 同上，且清掉上次调用残留的部分成功额度
    api_key, sport_key = _resolve_sport_key(league)

    stats = {"events": 0, "snapshots": 0, "dropped_totals_outcomes": 0}
    quotas: list[int] = []
    snaps: list[OddsSnapshot] = []
    try:
        for region in regions:
            payload, headers = _http_get(
                f"{API_BASE}/{sport_key}/odds",
                {
                    "apiKey": api_key,
                    "regions": region,
                    "markets": ",".join(markets),
                    "oddsFormat": "decimal",
                },
            )
            if not isinstance(payload, list):
                raise OddsApiError("Odds API 响应结构异常（应为事件数组）")
            events, region_snaps, dropped = _parse_events(payload, league, region)
            stats["events"] += events
            stats["dropped_totals_outcomes"] += dropped
            snaps.extend(region_snaps)
            quota = _quota_remaining(headers)
            if quota is not None:
                quotas.append(quota)
    except OddsApiError as err:
        _stash_partial_quota(quotas, refresh_quota, err)
        raise

    stats["snapshots"] = len(snaps)
    LAST_FETCH_STATS.update(stats)
    quota_left = min(quotas) if quotas else None    # 最小值 = 保守真值（spec §3.4 降频判据）
    return snaps, (quota_left if refresh_quota else None)


# spec §9.8 的 Provider 命名槽位：OddsApiProvider 即本模块的 fetch_odds
# （get_odds(leagues, markets, regions) 的 B 线单联赛形态，多带 refresh_quota 供
# 额度记账）。命名对齐而非新类型——一期不做类抽象，二期 InPlayProvider /
# ExchangeProvider 实现同一签名即可原位替换；HistoricalCsvProvider 语义由 A 线
# 回测取数路径承担，ExecutionProvider 家族一期仅 PaperExecutionProvider（T7）。
OddsApiProvider = fetch_odds


def list_events(league: str) -> tuple[list[dict], int | None]:
    """拉某联赛的**事件清单**（不带盘口），返回 ``(事件 dict 列表, 剩余额度)``。

    ``GET /v4/sports/{sport_key}/events``：不带 ``regions`` / ``markets``，响应体
    就是事件数组（id / commence_time / home_team / away_team 等字段），**原样返回、
    不做解析过滤**——窗口筛选归调用方（T9 用它做「当日是否有赛事」的免费探测）。

    The Odds API 文档口径该端点**不计费**（quota-free），据此才把「无赛事空跑」的
    判定前置到 :func:`fetch_odds`（计费）之前（spec §9.6「无赛事即空跑退出，不耗
    额度」）；**真伪由 E2E 用 meta 水位差实测验证**——若实测计费，须回退空跑语义
    并在报告标注。响应头若带额度，仍读取并随返回值交调用方落 meta：免费与否，
    记账面一致才可对照。

    签名镜像 :func:`fetch_odds` 的 ``(payload, quota)`` 口径（派单写 ``list[dict]``，
    与「仍返回额度头」不可兼得，取后者——额度记账是本修复的验收面）。
    429 / 其他 HTTP 错误 / 网络异常 / 非 JSON 数组上抛 :class:`OddsApiError`。
    """
    api_key, sport_key = _resolve_sport_key(league)
    payload, headers = _http_get(f"{API_BASE}/{sport_key}/events",
                                 {"apiKey": api_key})
    if not isinstance(payload, list):
        raise OddsApiError("Odds API 事件响应结构异常（应为事件数组）")
    return payload, _quota_remaining(headers)


def best_prices(snaps: list[OddsSnapshot], market: str) -> dict[str, tuple[float, str]]:
    """同一场比赛同一 market 内，各 outcome 的跨 bookmaker 最优（最大）价。

    返回 ``{outcome: (odds, bookmaker)}``：h2h 键 ``home/draw/away``，
    totals 键 ``over/under``。平价时保留先出现的 bookmaker（快照顺序稳定，
    结果确定）。**单场语义：snaps 必须来自同一场比赛**（单一 event_key）——
    不同场次的 home/away 是不同球队，混在一起会得到无意义的「跨场最优价」，
    故混入多个 event_key 时抛 ``ValueError``；调用方（Task 6）按 event_key
    先行过滤。某 outcome 无任何 bookmaker 报价时该键不出现（调用方按缺价处理）。
    """
    if market not in ("h2h", "totals"):
        raise ValueError(f"未知 market: {market}（仅支持 h2h / totals）")
    event_keys = {s.event_key for s in snaps}
    if len(event_keys) > 1:
        raise ValueError(
            f"best_prices 只接受单场比赛的快照，收到 {len(event_keys)} 个 event_key: {sorted(event_keys)}"
        )
    best: dict[str, tuple[float, str]] = {}
    for snap in snaps:
        if snap.market != market:
            continue
        for outcome, odds in snap.outcomes.items():
            current = best.get(outcome)
            if current is None or odds > current[0]:
                best[outcome] = (float(odds), snap.bookmaker)
    return best


# ---------------------------------------------------------------- 内部实现


def _stash_partial_quota(quotas: list[int], refresh_quota: bool, err: OddsApiError) -> None:
    """region 循环部分失败时，把已观测的最小额度挂在异常上并留旁路记录。

    前面 region 的请求已经真实发生（额度已被消耗），其响应头是库内水位的唯一
    依据；整次上抛若不带出，调用方只能沿用旧水位——虚高的 ``meta
    ['odds_quota_remaining']`` 会让「额度 < 阈值即降频/跳过拉盘」（spec §3.4）
    在最需要的时候做出相反判断。``refresh_quota=False`` 的调用方已声明不要额度
    记账，照旧不产生观测。
    """
    if not refresh_quota or not quotas:
        return
    partial = min(quotas)
    if err.quota_remaining is None:                 # 已带值的异常（理论不可达）不回写降级
        err.quota_remaining = partial
    LAST_PARTIAL_QUOTA["quota_remaining"] = partial


def _resolve_sport_key(league: str) -> tuple[str, str]:
    """key 缺失 / 未知联赛的统一守卫（fetch_odds 与 list_events 同一口径）。"""
    api_key = odds_api_key()
    if not api_key:
        raise OddsApiError("ODDS_API_KEY 未配置")
    sport_key = ODDS_SPORT_KEYS.get(league)
    if sport_key is None:
        known = ", ".join(sorted(ODDS_SPORT_KEYS))
        raise OddsApiError(f"未知联赛: {league}（可用: {known}）")
    return api_key, sport_key


def _http_get(url: str, params: dict[str, str]) -> tuple[Any, dict[str, str]]:
    """唯一触网点：GET ``url?params``，返回 ``(解码后的 JSON 体, 小写键响应头)``。

    429 → 额度耗尽/限流；其他 HTTP 错误、网络异常（含连接被截断的
    ``http.client.HTTPException``，如 IncompleteRead/BadStatusLine）、非 JSON
    响应同样上抛 :class:`OddsApiError`。异常文案不含 query（apiKey 不泄漏）。
    """
    full_url = f"{url}?{urlencode(params)}" if params else url
    try:
        with urllib.request.urlopen(full_url, timeout=TIMEOUT) as resp:
            body = resp.read()
            headers = {str(k).lower(): str(v) for k, v in resp.headers.items()}
    except urllib.error.HTTPError as e:          # HTTPError 是 URLError 子类，须先接
        detail = f"HTTP {e.code}（额度耗尽或限流）" if e.code == 429 else f"HTTP {e.code} {e.reason}"
        raise OddsApiError(f"Odds API 请求失败: {detail}") from e
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as e:
        raise OddsApiError(f"Odds API 网络异常: {e}") from e
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise OddsApiError(f"Odds API 响应不是合法 JSON: {e}") from e
    return payload, headers


def _quota_remaining(headers: dict[str, str]) -> int | None:
    """从响应头读剩余额度（键大小写不敏感）；缺失或不可解析 → ``None``。"""
    raw = next((v for k, v in headers.items() if str(k).lower() == QUOTA_HEADER), None)
    if raw is None:
        return None
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return None


def _parse_events(payload: list, league: str, region: str) -> tuple[int, list[OddsSnapshot], int]:
    """解析一个 region 的事件数组，返回 ``(事件数, 快照列表, 被滤掉的非 2.5 线条数)``。

    事件数只计**可解析**事件——缺 id/队名的坏事件不计入（丢弃即不进口径）。
    """
    stats_events = 0
    dropped_total = 0
    snaps: list[OddsSnapshot] = []
    for event in payload:
        home_name = event.get("home_team") or ""
        away_name = event.get("away_team") or ""
        if not event.get("id") or not home_name or not away_name:
            continue                             # 缺 id/队名的事件无法对齐下注，跳过
        stats_events += 1
        for bookmaker in event.get("bookmakers") or []:
            book_key = bookmaker.get("key") or ""
            for market in bookmaker.get("markets") or []:
                outcomes, dropped = _parse_outcomes(
                    market.get("key"), market.get("outcomes") or [], home_name, away_name
                )
                dropped_total += dropped
                if not outcomes:
                    continue                     # 只有被滤掉的线（如仅 3.5 totals）→ 不产快照
                snaps.append(
                    OddsSnapshot(
                        league=league,
                        event_key=event.get("id") or "",
                        kickoff_utc=event.get("commence_time") or "",
                        home_name=home_name,
                        away_name=away_name,
                        market=market["key"],
                        region=region,           # 单 region 请求 → 单值归属
                        bookmaker=book_key,
                        outcomes=outcomes,
                    )
                )
    return stats_events, snaps, dropped_total


def _parse_outcomes(
    market_key: str | None, raw_outcomes: list, home_name: str, away_name: str
) -> tuple[dict[str, float], int]:
    """按 market 解析 outcomes，返回 ``(outcomes, 被非 2.5 线滤掉的条数)``。

    无法归入已知槽位的条目一律丢弃（不猜），不计入丢弃线数。
    """
    if market_key == "h2h":
        slots = {**_H2H_SIDES, home_name: "home", away_name: "away"}
    elif market_key == "totals":
        slots = dict(_TOTALS_SIDES)
    else:
        return {}, 0                             # spreads 等其他 market 不进快照
    parsed: dict[str, float] = {}
    dropped = 0
    for outcome in raw_outcomes:
        side = slots.get(outcome.get("name"))
        if side is None:
            continue
        if market_key == "totals" and not _totals_point_ok(outcome.get("point")):
            dropped += 1                         # 3.5 等其他线丢弃并计数（spec §11 风险 4）
            continue
        price = _price(outcome)
        if price is None:
            continue
        parsed[side] = price
    return parsed, dropped


def _totals_point_ok(point: Any) -> bool:
    """totals 仅保留 2.5 线（2.5 在二进制下精确，容差只为容忍字符串/浮点噪声）。"""
    try:
        return abs(float(point) - TARGET_TOTALS_LINE) <= LINE_TOL
    except (TypeError, ValueError):
        return False


def _price(outcome: dict) -> float | None:
    price = outcome.get("price")
    try:
        return float(price)
    except (TypeError, ValueError):
        return None
