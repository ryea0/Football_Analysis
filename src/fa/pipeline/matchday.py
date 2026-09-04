"""比赛日 run 编排（T9，spec §3.4 / §7.1 / §9.5 / §9.6）：``fa run matchday``。

一次 run = 记录（runs）→ 同步（T5）→ 推荐（T6）→ **persona（T13，§6）** →
落注（T7）→ 渲染 → 推送 → 收尾。
编排层只做**流程与降级判断**，业务语义都在被调方（sync / value / persona /
paper / report）：

- **persona**（am 全量 / pm 只补新增场次，§6.2）：排在落注**之前**——veto 判决
  若迟到，注已按中性 kelly 落下，落注去重在 ``(fixture, market, strategy, mode)``
  级只挡重下、不撤旧注。pm 的 ``attempted`` 名单读当日 am run 的
  ``runs.summary.persona.attempted``；无 am 对照（``am_run_id`` None）则全跑。
  persona 降级只记 :func:`run_persona_phase` 的返回值进 ``summary["persona"]``
  （§6.5 该场回退纯模型、报告标注归 render 层），**不**计入本模块的数据降级
  ``degraded``——那一份语义是「非实时盘」，两路标注互不污染。

- **am**（11:00 全量）：key 缺失 → ``no_key`` 早退（不触网）；照常拉盘；无当日赛事
  → ``skipped`` 空跑（不渲染不推送）；额度低于 :data:`QUOTA_FLOOR` 只**标注**降级
  ——比赛日本就是拉盘窗，§3.4 要降频的是「非比赛日拉取」，这里没有可跳的步骤。
- **空跑零额度**（§9.6「无赛事即空跑退出，不耗额度」）：先查库内 52h 窗口的
  fixture；为空则逐联赛调 :func:`fa.pipeline.odds_api.list_events`（``/events``，
  不带盘口，文档口径**不计费**）探测，全部联赛都无窗口内事件才空跑——计费的
  :func:`sync_fixtures` 在确认有赛事之前一次都不发。库内已有窗口内 fixture 时连
  探测都跳过（零额外请求）；探测自身失败＝「无法确认当日赛程」而非「确认无赛事」，
  交回常规流程（sync 自己还有降级路径，且不算数据降级）。「免费」这一文档口径由
  E2E 用 meta 水位差实测验证，见 :func:`_probe_events`。
- **pm**（17:00 更新版）：额度低于水位 → **跳过拉盘**、复用 am 已落库快照（§9.5
  「额度耗尽 → 跳过拉盘、用最近快照并标注」）；报告走 ``render_pm_update`` 对照
  当日最近一次成功的 am run；当日没有 am run 就回退全量报告（phase='pm'）。
- **额度降频梯子**（§3.4，读 meta 水位三档，判据相互独立）：quota ≥
  ``QUOTA_MERGE_FLOOR`` 双区全扫；<``QUOTA_MERGE_FLOOR`` 拉盘收窄到单 eu（summary
  记 ``region_merged=True`` 并标注，am / 非降级 pm 都生效）；<``QUOTA_FLOOR`` pm
  跳拉盘复用快照（上一条，既有）。合并 region 只**缩范围**、从不跳拉盘。
- **拉盘失败**（:class:`OddsApiError`，§9.5「数据源失败 → 用最近缓存 + 告警」）：
  不中断 run，复用既有快照并标注降级。
- **推送失败**：只把原因（:func:`fa.pipeline.reporting.last_error`）写进
  ``runs.summary``——推荐 / 落注已各自落库，状态不加罪（§9.5 降级不中断）。

**run_id 归因语义（T5/T6 ledger 钉死）**：同 ``(fixture, market, strategy, phase)``
的 UNIQUE 刷新会改 ``recommendations.run_id``，归因属**最后刷新者**。所以 am 的
完整报告必须在 am 流程内**即时**渲染推送（本模块即「单相即渲染」），绝不先跑两相
再统一渲染；bets 归因同理（落注去重在 ``(fixture, market, strategy, mode)`` 级）。

表边界（§12.1）：经 T5/T6/T7 写 fixtures / odds_snapshots / recommendations / bets
/ meta，另写 runs（审计）；不写 matches / backtest_predictions。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

from fa.config import odds_api_key
from fa.db import get_meta, set_meta
from fa.model.fit import FitConfig, training_rows
from fa.pipeline.fixtures import (DEFAULT_REGIONS, QUOTA_META_KEY,
                                  sync_fixtures)
from fa.pipeline.odds_api import OddsApiError, list_events
from fa.pipeline.paper import place_paper_bets
from fa.pipeline.reporting import (last_error, render_matchday_report,
                                   render_pm_update, send)
from fa.pipeline.runs import (RUN_MATCHDAY, STATUS_DEGRADED, STATUS_FAILED,
                              STATUS_NO_KEY, STATUS_OK, STATUS_SKIPPED,
                              begin_run, finish_run)
from fa.pipeline.value import (MIN_TRAIN_ROWS, WINDOW_HOURS,
                               _parse_kickoff, generate_recommendations)
from fa.persona.apply import run_persona_phase

QUOTA_FLOOR = 100        # spec §3.4「低于阈值（如 100）」
# 额度节流第二档（spec §3.4「合并 region」）：500/月档 · 双区全扫≈20/次 →
# ≥200 双区；<200 单 eu（Pinnacle 在 eu，最优价损失极小）；<100 pm 跳拉盘（既有）。
QUOTA_MERGE_FLOOR = 200
MERGE_REGIONS = ("eu",)  # 告急档收窄后的 region 集（Pinnacle 所在，最优价锚点）
PHASES = ("am", "pm")


def run_matchday(conn: sqlite3.Connection, phase: str,
                 leagues: list[str]) -> dict:
    """跑一个比赛日相位，返回摘要 dict（CLI 直接消费的行数 / 判决 / 推送结果）。

    返回键：``status``（ok / degraded_ok / skipped / no_key）、``run_id``、
    ``phase``、``fixtures`` / ``aligned`` / ``unknown``、``recs`` / ``bets``、
    ``quota_left``、``degraded``、``sent``（None = 本次未推送）、``am_run_id``
    （仅 pm 有对照对象）、``persona``（``{"called","ok","veto","degraded"}``，
    未走到 persona 阶段为 None）。``phase`` 非法上抛 :class:`ValueError`。
    额度告急收窄 region（``quota_before < QUOTA_MERGE_FLOOR``）时 ``runs.summary``
    另记 ``region_merged=True`` 与理由串；返回 dict 另带 ``region_merged`` /
    ``snapshot_reused`` 两个布尔（降级三态的本窗事实，CLI 选文案用，均为加键不破契约）。
    """
    if phase not in PHASES:
        raise ValueError(f"phase 须为 am/pm，收到 {phase!r}")
    run_id = begin_run(conn, RUN_MATCHDAY, phase)
    try:
        return _run(conn, phase, list(leagues), run_id)
    except Exception as exc:
        # 先回滚：中途上抛时连接里可能有半写的阶段产物，不得搭 finish_run 的
        # commit 一起入库——'failed' 的 run 行必须只描述失败，不带残局
        conn.rollback()
        finish_run(conn, run_id, STATUS_FAILED,
                   {"error": f"{type(exc).__name__}: {exc}"})
        raise


# ---------------------------------------------------------------- 流程


def _run(conn: sqlite3.Connection, phase: str, leagues: list[str],
         run_id: int) -> dict:
    now = _now()
    quota_before = _quota_left(conn)
    if odds_api_key() is None:
        # 无 key：一行不拉、一场不落，runs 记 no_key（CLI exit 0，§12 冒烟口径）
        finish_run(conn, run_id, STATUS_NO_KEY,
                   {"reason": "ODDS_API_KEY 未配置——未拉盘、未推荐、未推送",
                    "telegram": None})
        return _result(STATUS_NO_KEY, run_id, phase, quota_left=quota_before)

    low = quota_before is not None and quota_before < QUOTA_FLOOR
    # 降频梯子中间档（spec §3.4「合并 region」）：<QUOTA_MERGE_FLOOR 只拉 eu。
    # 只**缩窄拉取范围**、绝不武装 pm 跳拉盘——跳拉盘专属 <QUOTA_FLOOR（``low``，
    # 判据独立），故 100≤quota<200 的 pm 照常拉盘（单区）；合并也计入 degraded，
    # 因为 uk 侧最优价没了属覆盖缩水，须在报告如实标注（§3.4「在报告标注」）。
    merge = (quota_before is not None and quota_before < QUOTA_MERGE_FLOOR
             and not (phase == "pm" and low))
    regions = MERGE_REGIONS if merge else DEFAULT_REGIONS
    reasons: list[str] = []
    if low:
        reasons.append(
            f"额度 {quota_before} < {QUOTA_FLOOR}："
            + ("跳过拉盘，复用最近快照（非实时盘）" if phase == "pm"
               else "比赛日照常拉盘，仅标注水位"))
    if merge:
        reasons.append(
            f"额度 {quota_before} < {QUOTA_MERGE_FLOOR}：额度降频，合并 region（eu）")

    # pm 的报告对照「当日最近一次成功的 am run」；当日没有 am run 就回退全量报告
    am_run_id = _latest_am_run(conn, _today()) if phase == "pm" else None
    report = "pm_update" if am_run_id is not None else "matchday"

    # §9.6「无赛事即空跑退出，不耗额度」：库内窗口为空时先用 /events（文档口径免费）
    # 探测，确认当日真没有赛事才空跑——计费的 sync_fixtures 绝不前置
    probe: str | None = None      # None＝库内已有窗口 fixture，连探测都不必发
    probe_quota: int | None = None
    window_count = _window_fixture_count(conn, leagues, now)
    if window_count == 0 and not leagues:
        # 无联赛可探：探测一次都没发（probe="none" 以别于「探测证实无赛事」的 events）
        return _finish_skipped(conn, run_id, phase, leagues, quota_before,
                               probe="none", probe_quota=None, reasons=[])
    if window_count == 0:
        found, probe_quota, probe_failed = _probe_events(leagues, now)
        if probe_failed:
            # 探测失败（含 commence_time 解析不了）＝「无法确认当日赛程」，不是
            # 「确认无赛事」：交回常规流程（宁可贵一次，也不静默漏掉比赛日；sync
            # 自己还有降级路径）。不算数据降级——质量无损，只是没省到额度，故记
            # probe 而非 degraded_reasons
            probe = "unavailable"
        elif not found:
            # 探测证实无赛事——计费拉盘一次不发，零额度空跑
            quota_left = quota_before if probe_quota is None else probe_quota
            if probe_quota is not None:
                # 探测若真带回额度头，照记账——E2E 据水位差验证该端点是否真免费
                set_meta(conn, QUOTA_META_KEY, str(probe_quota))
            return _finish_skipped(conn, run_id, phase, leagues, quota_left,
                                   probe="events", probe_quota=probe_quota,
                                   reasons=reasons)
        else:
            probe = "found"
            if probe_quota is not None:
                # 证据链：探测额度头先落 meta——若随后拉盘失败/没回额度头，/events
                # 是否免费仍有据可查（sync 成功时 meta 会被更新值覆盖，证据存 summary）
                set_meta(conn, QUOTA_META_KEY, str(probe_quota))

    sync = None
    reused = False                                   # 本窗没有实时盘可依
    if not (phase == "pm" and low):                  # pm 降级才跳过拉盘
        try:
            sync = sync_fixtures(conn, leagues, regions=regions)
        except OddsApiError as exc:
            reasons.append(f"Odds API 拉盘失败：{exc}；复用最近快照（非实时盘）")
            reused = True
    else:
        reused = True                                # 跳拉盘＝本窗非实时盘
    quota_left = (sync["quota_left"] if sync else
                  (probe_quota if probe == "found" else quota_before))

    rec_ids = generate_recommendations(conn, leagues, phase, run_id)
    # persona 必须在落注之前（§6.2 顺序裁定）：veto 判决先落库，paper 才不会按
    # 中性 kelly 给被否场下单——落注去重只挡重下、不撤旧注，倒置即无法挽回。
    # pm 只补新增场次：attempted = 当日 am run 的名单（§6.2「不重跑沿用判决」）；
    # am 全量；无 am 对照则全跑。名单解析失败回退空集＝多跑无害、漏跑有害。
    attempted = (_am_attempted(conn, am_run_id)
                 if phase == "pm" and am_run_id is not None else None)
    persona_summary = run_persona_phase(conn, run_id, leagues, attempted)
    placed = place_paper_bets(conn, run_id)
    summary = {
        "phase": phase,
        "leagues": leagues,
        "fixtures": sync["fixtures"] if sync else 0,
        "aligned": sync["aligned"] if sync else 0,
        "unknown": sync["unknown"] if sync else [],
        "recs": len(rec_ids),
        "bets": placed,
        "quota_before": quota_before,
        "quota_left": quota_left,
        "degraded": bool(reasons),
        "degraded_reasons": reasons,
        "persona": persona_summary,
        "train_n": _train_n(conn, leagues),
        "half_life": FitConfig().half_life_days,
        "window_hours": WINDOW_HOURS,
        "am_run_id": am_run_id,
        "report": report,
    }
    if probe:
        summary["probe"] = probe        # found / unavailable / none；缺省＝库内已有赛事
    # 降级三态的机器可读键（渲染层据此选文案，不解析 degraded_reasons 中文）：
    # 复用快照（本窗没拉盘）与合并 region（本窗有实时盘但缺 uk）是两回事，渲染层
    # 优先看 snapshot_reused——两者并存时（合并档拉盘失败）真话是「没有实时盘」。
    if reused:
        summary["snapshot_reused"] = True
    if merge:
        summary["region_merged"] = True
    if probe_quota is not None:
        summary["probe_quota"] = probe_quota   # /events 额度头存档（meta 会被 sync 覆盖）
    # 报告必须在**本相位内**即时渲染推送（run_id 归因=最后刷新者，见模块 docstring）
    if report == "pm_update":
        text = render_pm_update(conn, am_run_id, run_id, quota_left, bool(reasons))
    else:
        text = render_matchday_report(conn, run_id, phase, summary, quota_left,
                                      bool(reasons))
    sent = send(text)
    summary["telegram"] = ({"sent": True, "error": None} if sent else
                           {"sent": False,
                            "error": last_error() or "推送失败（未记录原因）"})
    finish_run(conn, run_id, STATUS_DEGRADED if reasons else STATUS_OK, summary,
               credits_after=quota_left)
    return _result(STATUS_DEGRADED if reasons else STATUS_OK, run_id, phase,
                   fixtures=summary["fixtures"], aligned=summary["aligned"],
                   unknown=summary["unknown"], recs=len(rec_ids), bets=placed,
                   quota_left=quota_left, degraded=bool(reasons), sent=sent,
                   am_run_id=am_run_id, region_merged=merge,
                   snapshot_reused=reused,
                   persona={"called": persona_summary["called"],
                            "ok": persona_summary["ok"],
                            "veto": persona_summary["veto"],
                            "degraded": persona_summary["degraded"]})


def _result(status: str, run_id: int, phase: str, *, fixtures: int = 0,
            aligned: int = 0, unknown: list[str] | None = None, recs: int = 0,
            bets: int = 0, quota_left: int | None = None,
            degraded: bool = False, sent: bool | None = None,
            am_run_id: int | None = None, region_merged: bool = False,
            snapshot_reused: bool = False,
            persona: dict | None = None) -> dict:
    """统一的返回形状：CLI / 测试只认这一份契约。

    ``persona`` 只带四个结果键；``attempted`` 名单只进 ``runs.summary``（pm 的
    沿用依据），不进调用方返回。``region_merged`` / ``snapshot_reused`` 是降级
    三态的**本窗**事实（与 runs.summary 同源）：CLI 据此选降级文案——只给
    ``degraded`` 布尔，文案就得猜「是缩了范围还是没拉盘」，而猜错一句就是向
    用户谎报价格新鲜度。``degraded`` 仍只表示「有降级」。"""
    return {"status": status, "run_id": run_id, "phase": phase,
            "fixtures": fixtures, "aligned": aligned,
            "unknown": list(unknown or []), "recs": recs, "bets": bets,
            "quota_left": quota_left, "degraded": degraded, "sent": sent,
            "am_run_id": am_run_id, "region_merged": region_merged,
            "snapshot_reused": snapshot_reused, "persona": persona}


def _finish_skipped(conn: sqlite3.Connection, run_id: int, phase: str,
                    leagues: list[str], quota_left: int | None, *, probe: str,
                    probe_quota: int | None, reasons: list[str]) -> dict:
    """空跑收尾：runs 记 ``skipped`` + 原因，返回统一摘要（不渲染不推送）。

    ``degraded`` 一律由 ``reasons`` 推导并**连同理由串**落 summary——空跑也可能带
    降级（pm 低水位跳拉盘），只落布尔不落因，事后无法解释这行为何标降。
    """
    degraded = bool(reasons)
    finish_run(conn, run_id, STATUS_SKIPPED, {
        "phase": phase, "leagues": leagues,
        "fixtures": 0, "quota_left": quota_left,
        "probe": probe, "probe_quota": probe_quota,
        "degraded": degraded,           # 空跑也可能带降级（pm 低水位跳过拉盘）
        "degraded_reasons": list(reasons),
        # events=探测证实无赛事；none=未指定联赛（探测一次都没发）
        "skip_reason": ("52h 窗口内无当日赛事（/events 探测证实）——未拉盘、未推荐、"
                        "未落注、未推送" if probe == "events" else
                        "未指定联赛——未拉盘、未推荐、未落注、未推送"),
        "telegram": None,
    }, credits_after=quota_left)
    return _result(STATUS_SKIPPED, run_id, phase, quota_left=quota_left,
                   degraded=degraded)


# ---------------------------------------------------------------- 查询辅助


def _quota_left(conn: sqlite3.Connection) -> int | None:
    """meta 里的额度水位（None = 从未拉到额度头）。"""
    raw = get_meta(conn, QUOTA_META_KEY)
    return None if raw is None else int(float(raw))


def _window_fixture_count(conn: sqlite3.Connection, leagues: list[str],
                          now: datetime) -> int | None:
    """52h 窗口内的 fixture 数（**不看对齐**：有赛事就该出报告，未对齐也要暴露）。

    窗口判定与 value 层同源（复用 ``WINDOW_HOURS`` / ``_parse_kickoff``），避免两套
    「当日赛事」口径漂移——空跑判据是「真的没有比赛」，不是「没有可下注的推荐」。

    **fail-open**：有行的 kickoff 解析不了时返回 ``None``（=「无法确认窗口是否为
    空」），调用方按「非空」处理走常规流程——绝不因时间解析失败而空跑漏掉比赛日。
    """
    if not leagues:
        return 0
    end = now + timedelta(hours=WINDOW_HOURS)
    placeholders = ",".join("?" * len(leagues))
    n = 0
    for row in conn.execute(
            f"SELECT kickoff_utc FROM fixtures WHERE league IN ({placeholders})",
            list(leagues)):
        kickoff = _parse_kickoff(row["kickoff_utc"])
        if kickoff is None:
            return None                    # 解析不了＝无法确认，fail-open
        if now <= kickoff <= end:
            n += 1
    return n


def _probe_events(leagues: list[str],
                  now: datetime) -> tuple[bool, int | None, bool]:
    """逐联赛调 :func:`list_events`（``/events``，无盘口）确认当日是否真有赛事。

    返回 ``(窗口内是否有事件, 探测读到的额度或 None, 是否有联赛探测失败)``。判空
    须**全部联赛都探测成功且都无窗口内事件**——任何一档失败都算「无法确认」，
    交回常规流程（宁可贵一次拉盘，也不静默漏掉比赛日）。窗口口径与
    :func:`_window_fixture_count` 同一条（``WINDOW_HOURS`` / ``_parse_kickoff``）。

    额度头即使探测端点免费也照读回传：调用方落 meta，E2E 才能用水位差实证
    「/events 不计费」这一文档口径（若实测计费，须回退空跑语义）。

    **fail-open**：请求失败、或某条事件的 ``commence_time`` 解析不了，都归为
    「无法确认」（第三位返回 ``True``）——调用方据此走常规流程，绝不因解析失败
    而判空空跑。
    """
    end = now + timedelta(hours=WINDOW_HOURS)
    quotas: list[int] = []
    found = False
    failed = False
    for league in leagues:
        try:
            events, quota = list_events(league)
        except OddsApiError:
            failed = True
            continue
        if quota is not None:
            quotas.append(int(quota))
        for event in events:
            kickoff = _parse_kickoff((event or {}).get("commence_time"))
            if kickoff is None:
                failed = True            # 时间解析不了＝无法确认，fail-open
                continue
            if now <= kickoff <= end:
                found = True
    return found, (min(quotas) if quotas else None), failed


def _latest_am_run(conn: sqlite3.Connection, day: date) -> int | None:
    """当日最近一次成功的 am matchday run（终态 ok / degraded_ok）；无则 None。

    空跑 / 无 key 的 am run 不作对照基准——它们没有可 diff 的推荐。
    """
    row = conn.execute(
        "SELECT id FROM runs WHERE type=? AND phase='am' AND status IN (?, ?)"
        " AND substr(started_at, 1, 10)=? ORDER BY id DESC LIMIT 1",
        (RUN_MATCHDAY, STATUS_OK, STATUS_DEGRADED, day.isoformat())).fetchone()
    return None if row is None else int(row["id"])


def _am_attempted(conn: sqlite3.Connection, am_run_id: int) -> set[int]:
    """am run summary 里的 ``persona.attempted``（pm 沿用判决的依据，§6.2）。

    解析失败/缺键 → 空 set：fail-open（pm 多跑一次无害，漏判有害）。候选 fixture
    只增不减，故名单缺项的唯一代价是 pm 对该场**补跑**一次 persona——与「无 am
    对照全跑」同一语义，不会出现「该判未判」。
    """
    row = conn.execute("SELECT summary FROM runs WHERE id=?",
                       (am_run_id,)).fetchone()
    if row is None or not row["summary"]:
        return set()
    try:
        parsed = json.loads(row["summary"])
        return set(parsed.get("persona", {}).get("attempted", []))
    except (ValueError, AttributeError, TypeError):
        return set()


def _train_n(conn: sqlite3.Connection, leagues: list[str]) -> int:
    """报告「样本量」风险项（§7.1-3）：与 value 层同一条训练切片口径，但只计达到
    ``MIN_TRAIN_ROWS`` 的联赛——不足者根本不进拟合，计入反而虚高样本量。"""
    cfg = FitConfig()
    asof = _today().isoformat()
    total = 0
    for league in leagues:
        rows = training_rows(conn, league, asof, cfg.window_days)
        if len(rows) >= MIN_TRAIN_ROWS:
            total += len(rows)
    return total


# ---------------------------------------------------------------- 时间缝


def _now() -> datetime:
    """时间注入缝：本模块的「当日赛事」判定从这里取。推荐窗口与拟合 ``asof``
    归 value 层的同款缝——生产中两者是同一时钟，测试里成对 monkeypatch。"""
    return datetime.now(timezone.utc)


def _today() -> date:
    """UTC 日历日。11:00 / 17:00 北京时间 = 03:00 / 09:00 UTC，两个 cron 窗都落在
    同一 UTC 日内，故与「北京日期」口径等价（spec §9.6 Asia/Shanghai）。"""
    return _now().astimezone(timezone.utc).date()
