"""fixtures 同步测试（T5，spec §3.1/§3.3/§3.4）。

全离线：``fetch_odds`` 在 ``fa.pipeline.fixtures`` 命名空间被替换为回放表；
同时把 ``_http_get`` 与 ``urllib.request.urlopen`` 打成炸弹——若实现绕过该
命名空间直连真函数，网络守卫会立即炸出（而非静默触网）。

种子（M1 canonical 名 → Odds API 风格名）：
- E0 ``Chelsea`` / ``Arsenal`` / ``Everton``：原文精确命中（ev1/ev2 全对齐）
- E0 ``Mystery United``：三段式全败 → 隔离 + 该侧 team_id NULL（**不跳过整场**）
- D1 ``Bayern München``（Odds 全称+变音符）→ 模糊 0.88 自动别名 → 对齐；
  ``Dortmund`` 原文精确命中
"""
import inspect
import json
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from fa.data.teams import get_or_create_team
from fa.db import connect, get_meta, init_db
from fa.pipeline.fixtures import DEFAULT_REGIONS, sync_fixtures
from fa.pipeline.odds_api import OddsApiError, OddsSnapshot, fetch_odds

KO_IN = "2026-09-05T14:00:00Z"          # 距 _NOW 约 43h，落在任何窗口讨论之外
KO_FAR = "2026-12-01T19:30:00Z"         # 远超 48h 窗口 → 也必须入库（不做窗口过滤）
_NOW = datetime(2026, 9, 3, 19, 0, 0, tzinfo=timezone.utc)


def _snap(league, event_key, market, bookmaker, outcomes, region="eu",
          kickoff=KO_IN, home="Chelsea", away="Arsenal"):
    return OddsSnapshot(league, event_key, kickoff, home, away, market, region,
                        bookmaker, outcomes)


def _replay():
    """league -> (快照列表, 剩余额度)。额度取 D1 的 90（多联赛取最小值的判别场景）。"""
    return {
        "E0": ([
            _snap("E0", "ev1", "h2h", "pinnacle", {"home": 2.10, "draw": 3.50, "away": 3.60}),
            _snap("E0", "ev1", "h2h", "betfair", {"home": 2.15, "draw": 3.40, "away": 3.75},
                  region="uk"),
            _snap("E0", "ev1", "totals", "pinnacle", {"over": 1.95, "under": 1.95}),
            _snap("E0", "ev2", "h2h", "pinnacle", {"home": 1.80, "draw": 3.60, "away": 4.20},
                  home="Chelsea", away="Everton"),
            _snap("E0", "ev3", "h2h", "pinnacle", {"home": 1.50, "draw": 4.00, "away": 6.00},
                  home="Chelsea", away="Mystery United"),
        ], 100),
        "D1": ([
            _snap("D1", "ev-b", "h2h", "pinnacle", {"home": 1.60, "draw": 4.10, "away": 5.20},
                  home="Bayern München", away="Dortmund"),
            _snap("D1", "ev-b", "totals", "pinnacle", {"over": 1.80, "under": 2.05},
                  home="Bayern München", away="Dortmund"),
        ], 90),
    }


class _Calls(list):
    """league 调用顺序 + ``regions`` 侧信道（额度节流断言用）。

    仍是 list：既有测试的 ``replay == ["E0", "D1"]`` 形状一字不改。
    """


@pytest.fixture
def replay(monkeypatch):
    """替换 fixtures 命名空间的 fetch_odds，并记录调用顺序供断言。"""
    calls = _Calls()
    calls.regions = []

    def fake_fetch(league, markets=("h2h", "totals"), regions=("eu", "uk"),
                   refresh_quota=True):
        assert set(markets) == {"h2h", "totals"}          # 一次拉取须两 market 供 T6
        calls.append(league)
        calls.regions.append(regions)
        snaps, quota = _replay()[league]
        return list(snaps), quota

    def boom(*args, **kwargs):
        raise AssertionError("sync_fixtures 不得触网（fetch_odds 须可注入）")

    monkeypatch.setattr("fa.pipeline.fixtures.fetch_odds", fake_fetch)
    monkeypatch.setattr("fa.pipeline.odds_api._http_get", boom)   # 绕过注入点的兜底
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    return calls


@pytest.fixture
def conn(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    for lg, names in (("E0", ("Chelsea", "Arsenal", "Everton")),
                      ("D1", ("Bayern Munich", "Dortmund"))):
        for n in names:
            get_or_create_team(c, lg, n)
    c.commit()
    yield c
    c.close()


def _fixtures(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM fixtures ORDER BY id")]


def _snapshots(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM odds_snapshots ORDER BY id")]


def _fixture_by_event(conn, event_key):
    row = conn.execute("SELECT * FROM fixtures WHERE event_key=?", (event_key,)).fetchone()
    assert row is not None, f"fixture {event_key} 缺失"
    return dict(row)


# ---------------------------------------------------------------- 主路径


def test_sync_creates_fixtures_with_aligned_ids(conn, replay):
    out = sync_fixtures(conn, ["E0", "D1"])
    assert replay == ["E0", "D1"]                     # 逐联赛各拉一次
    assert out["fixtures"] == 4                       # ev1/ev2/ev3/ev-b
    rows = {r["event_key"]: r for r in _fixtures(conn)}
    assert set(rows) == {"ev1", "ev2", "ev3", "ev-b"}
    chelsea = conn.execute(
        "SELECT id FROM teams WHERE league='E0' AND name='Chelsea'").fetchone()["id"]
    arsenal = conn.execute(
        "SELECT id FROM teams WHERE league='E0' AND name='Arsenal'").fetchone()["id"]
    ev1 = rows["ev1"]
    assert ev1["league"] == "E0"
    assert ev1["source"] == "oddsapi"
    assert ev1["kickoff_utc"] == KO_IN
    assert ev1["home_team_id"] == chelsea and ev1["away_team_id"] == arsenal
    assert ev1["status"] == "scheduled"
    assert ev1["created_at"]


def test_diacritic_name_auto_aligns_via_fuzzy_alias(conn, replay):
    sync_fixtures(conn, ["D1"])
    bayern = conn.execute(
        "SELECT id FROM teams WHERE league='D1' AND name='Bayern Munich'").fetchone()["id"]
    dortmund = conn.execute(
        "SELECT id FROM teams WHERE league='D1' AND name='Dortmund'").fetchone()["id"]
    row = _fixture_by_event(conn, "ev-b")
    assert (row["home_team_id"], row["away_team_id"]) == (bayern, dortmund)
    # 自动别名留痕（T4 裁定）：下次 sync 原文直取
    alias = conn.execute(
        "SELECT team_id FROM team_aliases WHERE source='oddsapi' AND alias='Bayern München'"
    ).fetchone()
    assert alias is not None and alias["team_id"] == bayern


def test_unaligned_side_is_null_and_fixture_still_stored(conn, replay):
    """T4 派单硬约束：对不上的侧写 NULL，绝不跳过整场。"""
    out = sync_fixtures(conn, ["E0"])
    row = _fixture_by_event(conn, "ev3")
    assert row["home_team_id"] is not None            # 主侧照常对齐
    assert row["away_team_id"] is None                # 未知侧 NULL
    assert out["unknown"] == ["Mystery United"]
    assert conn.execute(
        "SELECT name FROM unknown_names WHERE source='oddsapi' AND name='Mystery United'"
    ).fetchone() is not None                          # 绝不静默丢弃（spec §3.3）
    assert out["aligned"] == 2                        # ev1/ev2 双侧对齐；ev3 不计
    # 该场的快照也照常落库（未对齐≠跳过整场，快照仍属该 fixture）
    fid = row["id"]
    assert len([s for s in _snapshots(conn) if s["fixture_id"] == fid]) == 1


def test_unknown_names_deduped_and_sorted(conn, replay):
    """两个事件各自带同一未知名 → 返回列表去重；多未知名按字典序。"""
    out = sync_fixtures(conn, ["E0"])
    assert out["unknown"] == sorted(set(out["unknown"]))
    assert out["unknown"].count("Mystery United") == 1


def test_snapshot_rows_written_with_fixture_link(conn, replay):
    sync_fixtures(conn, ["E0", "D1"])
    snaps = _snapshots(conn)
    assert len(snaps) == 7                            # 5（E0）+ 2（D1）
    by_event = {}
    for s in snaps:
        by_event.setdefault(s["event_key"], []).append(s)
    assert len(by_event["ev1"]) == 3                  # 2 book h2h + 1 totals
    ev1 = {(s["bookmaker"], s["market"]): s for s in by_event["ev1"]}
    assert set(ev1) == {("pinnacle", "h2h"), ("betfair", "h2h"), ("pinnacle", "totals")}
    assert ev1[("pinnacle", "h2h")]["region"] == "eu"
    assert ev1[("betfair", "h2h")]["region"] == "uk"
    assert json.loads(ev1[("pinnacle", "h2h")]["outcomes"]) == {
        "home": 2.10, "draw": 3.50, "away": 3.60}
    # 快照挂在所属 fixture 上
    fid = _fixture_by_event(conn, "ev1")["id"]
    assert all(s["fixture_id"] == fid for s in by_event["ev1"])
    # raw 留档可回读队名/kickoff（fixtures 表不存 Odds API 原文名）
    raw = json.loads(ev1[("pinnacle", "h2h")]["raw"])
    assert (raw["home_name"], raw["away_name"], raw["kickoff_utc"]) == (
        "Chelsea", "Arsenal", KO_IN)


def test_fetched_at_is_utc_and_uniform_within_a_run(conn, replay):
    sync_fixtures(conn, ["E0"])
    stamps = {s["fetched_at"] for s in _snapshots(conn)}
    assert len(stamps) == 1                           # 一次同步一个时间戳
    ts = datetime.fromisoformat(stamps.pop().replace("Z", "+00:00"))
    assert ts.tzinfo is not None                      # 必须带时区（UTC 口径）
    assert abs(ts - datetime.now(timezone.utc)) < timedelta(minutes=5)


# ---------------------------------------------------------------- upsert 幂等


def test_repeated_sync_is_idempotent_for_fixtures(conn, replay):
    sync_fixtures(conn, ["E0"])
    n_rows = len(_fixtures(conn))
    out = sync_fixtures(conn, ["E0"])
    assert out["fixtures"] == 3
    assert len(_fixtures(conn)) == n_rows == 3        # 不重复建行
    # 快照是历史轴：第二次拉取追加（fetched_at 区分批次），不覆盖
    assert len(_snapshots(conn)) == 10


def test_upsert_refreshes_kickoff(conn, replay, monkeypatch):
    sync_fixtures(conn, ["E0"])
    replay_map = _replay()

    def shifted(league, markets=("h2h", "totals"), regions=("eu", "uk"),
                refresh_quota=True):
        snaps, quota = replay_map[league]
        moved = []
        for s in snaps:
            if s.event_key != "ev1":
                moved.append(s)
                continue
            ko = (datetime.fromisoformat(KO_IN.replace("Z", "+00:00"))
                  + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
            moved.append(OddsSnapshot(s.league, s.event_key, ko, s.home_name,
                                      s.away_name, s.market, s.region, s.bookmaker,
                                      s.outcomes))
        return moved, quota

    monkeypatch.setattr("fa.pipeline.fixtures.fetch_odds", shifted)
    sync_fixtures(conn, ["E0"])
    assert len(_fixtures(conn)) == 3
    assert _fixture_by_event(conn, "ev1")["kickoff_utc"] == "2026-09-05T17:00:00Z"


def test_upsert_keeps_team_ids_stable_across_runs(conn, replay):
    """别名已写入后重跑，对齐结果稳定（不会因重跑退化为 NULL 或换队）。"""
    sync_fixtures(conn, ["D1"])
    first = _fixture_by_event(conn, "ev-b")
    sync_fixtures(conn, ["D1"])
    second = _fixture_by_event(conn, "ev-b")
    assert (second["home_team_id"], second["away_team_id"]) == (
        first["home_team_id"], first["away_team_id"])
    assert second["home_team_id"] is not None


def test_upsert_preserves_status_and_created_at(conn, replay, monkeypatch):
    """status / created_at 属既有行生命周期：T7 结算改的状态不得被同步重置。"""
    sync_fixtures(conn, ["E0"])
    conn.execute("UPDATE fixtures SET status='finished' WHERE event_key='ev1'")
    conn.commit()
    sync_fixtures(conn, ["E0"])
    row = _fixture_by_event(conn, "ev1")
    assert row["status"] == "finished"


def test_new_fixture_created_at_survives_as_identity(conn, replay):
    sync_fixtures(conn, ["E0"])
    created = {r["event_key"]: r["created_at"] for r in _fixtures(conn)}
    sync_fixtures(conn, ["E0"])
    after = {r["event_key"]: r["created_at"] for r in _fixtures(conn)}
    assert created == after                           # created_at 不随 upsert 漂移


# ---------------------------------------------------------------- 窗口 / 额度


def test_no_kickoff_window_filtering(conn, replay, monkeypatch):
    """48h 窗口过滤归报告层：窗口外的 kickoff 也入库（brief 明示）。"""
    replay_map = _replay()

    def far(league, markets=("h2h", "totals"), regions=("eu", "uk"), refresh_quota=True):
        snaps, quota = replay_map[league]
        out = [OddsSnapshot(s.league, s.event_key, KO_FAR, s.home_name, s.away_name,
                            s.market, s.region, s.bookmaker, s.outcomes) for s in snaps]
        return out, quota

    monkeypatch.setattr("fa.pipeline.fixtures.fetch_odds", far)
    sync_fixtures(conn, ["E0"])
    assert _fixture_by_event(conn, "ev1")["kickoff_utc"] == KO_FAR


def test_quota_min_across_leagues_persisted_to_meta(conn, replay):
    """额度纪律（spec §3.4）：多联赛取最小值（保守真值）并写 meta。"""
    out = sync_fixtures(conn, ["E0", "D1"])
    assert out["quota_left"] == 90                    # min(100, 90)
    assert get_meta(conn, "odds_quota_remaining") == "90"


def test_quota_single_league_persisted(conn, replay):
    out = sync_fixtures(conn, ["E0"])
    assert out["quota_left"] == 100
    assert get_meta(conn, "odds_quota_remaining") == "100"


def test_quota_none_leaves_meta_untouched(conn, replay, monkeypatch):
    """refresh_quota=False（T3 裁定返回 None）→ 不写 meta、返回 None。"""
    replay_map = _replay()
    monkeypatch.setattr(
        "fa.pipeline.fixtures.fetch_odds",
        lambda league, markets=("h2h", "totals"), regions=("eu", "uk"),
        refresh_quota=True: (list(replay_map[league][0]), None))
    out = sync_fixtures(conn, ["E0"])
    assert out["quota_left"] is None
    assert get_meta(conn, "odds_quota_remaining") is None


def test_quota_zero_is_still_persisted(conn, replay, monkeypatch):
    """0 是合法额度（耗尽），不得被真值判断误吞。"""
    replay_map = _replay()
    monkeypatch.setattr(
        "fa.pipeline.fixtures.fetch_odds",
        lambda league, markets=("h2h", "totals"), regions=("eu", "uk"),
        refresh_quota=True: (list(replay_map[league][0]), 0))
    out = sync_fixtures(conn, ["E0"])
    assert out["quota_left"] == 0
    assert get_meta(conn, "odds_quota_remaining") == "0"


def test_empty_league_yield_writes_nothing(conn, replay, monkeypatch):
    monkeypatch.setattr(
        "fa.pipeline.fixtures.fetch_odds",
        lambda league, markets=("h2h", "totals"), regions=("eu", "uk"),
        refresh_quota=True: ([], 77))
    out = sync_fixtures(conn, ["E0"])
    assert out == {"fixtures": 0, "aligned": 0, "unknown": [],
                   "league_mismatch": 0, "quota_left": 77}
    assert _fixtures(conn) == [] and _snapshots(conn) == []


def test_regions_thread_verbatim_to_fetch_odds(conn, replay):
    """regions 原样透传每次 fetch_odds（spec §3.4 额度节流的抓手）。

    默认双区不改既有调用方；收窄到单 eu 时逐联赛仍各传一次——节流决策归
    matchday，本函数只透传、不读水位（见 sync_fixtures docstring 的契约）。
    走 ``replay`` 替身：触网炸弹与「一次两 market」守卫照常生效。
    """
    sync_fixtures(conn, ["E0", "D1"])
    sync_fixtures(conn, ["E0"], regions=("eu",))

    assert replay == ["E0", "D1", "E0"]               # 逐联赛各拉一次
    assert replay.regions == [("eu", "uk"), ("eu", "uk"), ("eu",)]


def test_default_regions_match_fetch_odds_signature():
    """漂移守卫：``DEFAULT_REGIONS`` 须与 ``fetch_odds`` 的默认双区一致。

    降频梯子的「另一档」就是这份默认——两处字面量一旦分叉，quota≥200 的全扫
    基线会悄悄移动，而任何行为测试都测不出（它们只见其中一份）。
    """
    default = inspect.signature(fetch_odds).parameters["regions"].default
    assert DEFAULT_REGIONS == default


# ---------------------------------------------------------------- 边界 / 表纪律


def test_quota_watermark_written_before_a_later_league_fails(conn, replay, monkeypatch):
    """逐联赛即写水位（终审 Important #2）：下一联赛再抛，已观测的最小值不随异常蒸发。

    攒到全部联赛完成才 set_meta 的话，E0 的额度已真实消耗、其响应头是唯一证据，
    异常一路抛到调用方时 meta 仍停在进入 run 前的旧值——水位虚高会让「额度 <
    阈值即降频/跳过拉盘」（spec §3.4）在额度最紧的时候做出相反判断。
    """
    calls = []

    def flaky(league, **kwargs):
        calls.append(league)
        if league == "D1":
            raise OddsApiError("Odds API 请求失败: HTTP 429")
        snaps, quota = _replay()[league]
        return list(snaps), quota

    monkeypatch.setattr("fa.pipeline.fixtures.fetch_odds", flaky)

    with pytest.raises(OddsApiError):
        sync_fixtures(conn, ["E0", "D1"])

    assert calls == ["E0", "D1"]                      # E0 已成功返回后才轮到 D1 炸
    assert get_meta(conn, "odds_quota_remaining") == "100"


def test_league_partial_quota_from_error_is_folded_into_meta(conn, replay, monkeypatch):
    """联赛内 region 部分成功（eu 见 483、uk 才 429）→ 异常携带的额度并入水位。

    fetch_odds 把已见最小额度挂在 ``OddsApiError.quota_remaining``（本文件上方
    odds_api 侧同主题测试），sync 的 except 路径消费它——否则该联赛的消耗完全
    不进账。取 min(490, 483) = 483：既证并入、也证仍是「多源取最小」。
    """

    def flaky(league, markets=("h2h", "totals"), regions=("eu", "uk"),
              refresh_quota=True):
        if league == "E0":
            return [_snap("E0", "ev1", "h2h", "pinnacle",
                          {"home": 2.10, "draw": 3.50, "away": 3.60})], 490
        raise OddsApiError("Odds API 请求失败: HTTP 429（额度耗尽或限流）", 483)

    monkeypatch.setattr("fa.pipeline.fixtures.fetch_odds", flaky)

    with pytest.raises(OddsApiError):
        sync_fixtures(conn, ["E0", "D1"])

    assert get_meta(conn, "odds_quota_remaining") == "483"


def test_provider_error_propagates_without_partial_commit(conn, replay, monkeypatch):
    """任一联赛失败 → 上抛；单 commit 语义下不留半写状态。"""

    def boom(league, **kwargs):
        raise OddsApiError("Odds API 请求失败: HTTP 429")

    sync_fixtures(conn, ["E0"])                       # 先建基线（3 fixtures）
    baseline = len(_fixtures(conn))
    monkeypatch.setattr("fa.pipeline.fixtures.fetch_odds", boom)
    with pytest.raises(OddsApiError):
        sync_fixtures(conn, ["E0", "D1"])
    assert len(_fixtures(conn)) == baseline           # 无半写


def test_sync_never_writes_a_line_tables(conn, replay):
    """表边界（spec §12.1）：B 线同步只写 fixtures/odds_snapshots/meta。"""
    sync_fixtures(conn, ["E0", "D1"])
    assert conn.execute("SELECT COUNT(*) c FROM matches").fetchone()["c"] == 0
    assert conn.execute(
        "SELECT COUNT(*) c FROM backtest_predictions").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM bets").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM recommendations").fetchone()["c"] == 0


def test_return_keys_are_exactly_the_brief_contract(conn, replay):
    out = sync_fixtures(conn, ["E0"])
    assert set(out) == {"fixtures", "aligned", "unknown", "league_mismatch",
                        "quota_left"}


# ------------------------------------------------- 联赛自洽（M4 §7-4 纵深防御）
# 落库前校验：align 给出的 team id 若不属于该 fixture 的联赛 → 该侧置 NULL
# （不跳过整场）、队名进隔离表、摘要计 league_mismatch——根治在 resolve_team
# 的别名收域，这里防人工确认错队与未来新路径再漏。

def test_league_mismatch_side_nulls_quarantines_and_counts(conn, replay, monkeypatch):
    """align 返回错联赛 team id（模拟别名错绑/人工确认错队）→ 校验拦下。"""
    from fa.pipeline import fixtures as fx

    wrong_id = get_or_create_team(conn, "I1", "Treviso")   # I1 的队
    real_align = fx.align_fixture_teams

    def hijacked(c, league, home, away, source="oddsapi"):
        h, a = real_align(c, league, home, away, source)
        return (wrong_id if h is not None else None), a    # 主队侧劫持为错联赛 id

    monkeypatch.setattr(fx, "align_fixture_teams", hijacked)
    out = sync_fixtures(conn, ["E0"], regions=("eu",))

    row = conn.execute(
        "SELECT home_team_id, away_team_id FROM fixtures WHERE event_key='ev1'"
    ).fetchone()
    assert row["home_team_id"] is None                # 错联赛侧拦下：NULL 不落脏 id
    assert row["away_team_id"] is not None            # 对的一侧不受牵连
    assert out["league_mismatch"] == 3                # ev1/ev2/ev3 主队各计一次
    quarantined = {r["name"] for r in conn.execute(
        "SELECT name FROM unknown_names")}
    assert "Chelsea" in quarantined                   # 被拦队名进隔离表（可见）
    assert out["aligned"] == 0                        # 拦下后双侧齐的场景为 0
