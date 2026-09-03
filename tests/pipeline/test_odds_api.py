"""OddsProvider / OddsApiProvider 测试（全离线：_http_get 与 urlopen 均打补丁）。

夹具 tests/pipeline/fixtures/odds_epl.json 为手工构造的 The Odds API v4 最小合法
响应：2 events × 2 本可下注 bookmaker × (h2h + totals)，外加
- pinnacle(ev1) 的 totals 同时给 2.5 与 3.5 两条线（证 3.5 被滤且计数）
- pinnacle(ev1) 额外带 spreads market、ev2 的 spreads_only_book 只有 spreads
  （证非 h2h/totals 的 market 被忽略，无可下注 market 的 book 不产快照）

因此解析快照总数 = 每 region 2 events × 2 books × 2 markets = 8（默认 eu+uk 双请求共 16）。
"""
import dataclasses
import json
import urllib.error
from pathlib import Path

import pytest

from fa.pipeline.odds_api import OddsApiError, OddsSnapshot, fetch_odds

FIXTURE = Path(__file__).parent / "fixtures" / "odds_epl.json"
QUOTA = "487"


def _fixture_events():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def replay(monkeypatch):
    """把 _http_get 换成夹具回放，并记录 (url, params) 供断言。

    额度头两次取不同值且**最小值不在末位**（483 → 487），以钉死「多 region 取
    最小值」而非「取最后一次」。
    """
    calls = []

    def fake_http_get(url, params):
        calls.append((url, dict(params)))
        remaining = "483" if len(calls) == 1 else QUOTA
        return _fixture_events(), {"X-Requests-Remaining": remaining, "X-Requests-Used": "13"}

    monkeypatch.setattr("fa.pipeline.odds_api._http_get", fake_http_get)
    monkeypatch.setenv("ODDS_API_KEY", "test-key")
    return calls


def _snaps_of(snaps, event_key, market, bookmaker):
    return [
        s
        for s in snaps
        if s.event_key == event_key
        and s.market == market
        and s.bookmaker == bookmaker
    ]


# ---------------------------------------------------------------- 解析


def test_fetch_odds_parses_all_snapshots(replay):
    snaps, quota = fetch_odds("E0", regions=("eu",))
    assert len(snaps) == 8                              # 2 events × 2 books × 2 markets
    assert quota == 483
    assert all(isinstance(s, OddsSnapshot) for s in snaps)
    assert {s.league for s in snaps} == {"E0"}          # 内部联赛码，非 sport key
    assert {s.event_key for s in snaps} == {"ev1", "ev2"}
    assert {s.region for s in snaps} == {"eu"}          # 单 region 请求 → 单值归属
    assert {s.bookmaker for s in snaps} == {"pinnacle", "betfair_ex_eu", "williamhill"}


def test_fetch_odds_splits_request_per_region(replay):
    """控制器裁定：逐 region 发请求——响应不带 bookmaker→region 归属，拆开
    才有单值 region（DDL `-- eu / uk` 口径），计费按 region × market 等价。"""
    from fa.pipeline.odds_api import LAST_FETCH_STATS

    snaps, quota = fetch_odds("E0")                     # 默认 eu + uk
    assert len(replay) == 2                             # 每 region 一次请求
    assert [params["regions"] for _, params in replay] == ["eu", "uk"]
    assert all(url == replay[0][0] for url, _ in replay)
    assert len(snaps) == 16                             # 每 region 各 8 条
    assert {s.region for s in snaps} == {"eu", "uk"}
    assert sum(s.region == "eu" for s in snaps) == 8
    assert quota == 483                                 # min(483, 487)：最小值非末位 → 取最小而非取最后
    assert LAST_FETCH_STATS == {                        # 统计跨 region 累加
        "events": 4,
        "snapshots": 16,
        "dropped_totals_outcomes": 4,
    }


def test_non_target_lines_dropped_and_counted(replay):
    """spec §11 风险 4：一期只取 2.5 线，其余丢弃并计数。"""
    from fa.pipeline.odds_api import LAST_FETCH_STATS

    snaps, _ = fetch_odds("E0", regions=("eu",))
    assert LAST_FETCH_STATS == {
        "events": 2,                                    # 夹具两个事件
        "snapshots": len(snaps),
        "dropped_totals_outcomes": 2,                   # pinnacle(ev1) 的 3.5 线 Over+Under
    }


def test_stats_cleared_when_fetch_fails(replay, monkeypatch):
    """失败的调用不写统计：读到空 dict 即「最近一次拉取未完成」。"""
    from fa.pipeline.odds_api import LAST_FETCH_STATS

    fetch_odds("E0", regions=("eu",))
    assert LAST_FETCH_STATS["snapshots"] == 8

    def boom(url, params):
        raise OddsApiError("Odds API 请求失败: HTTP 429")

    monkeypatch.setattr("fa.pipeline.odds_api._http_get", boom)
    with pytest.raises(OddsApiError):
        fetch_odds("E0")
    assert LAST_FETCH_STATS == {}


def test_odds_api_provider_is_fetch_odds():
    """spec §9.8 命名落点：OddsApiProvider 即 fetch_odds（B 线形态）。"""
    from fa.pipeline.odds_api import OddsApiProvider

    assert OddsApiProvider is fetch_odds


def test_event_fields_carried_from_response(replay):
    snaps, _ = fetch_odds("E0")
    ev1 = _snaps_of(snaps, "ev1", "h2h", "pinnacle")[0]
    assert ev1.kickoff_utc == "2026-09-05T14:00:00Z"
    assert ev1.home_name == "Chelsea"
    assert ev1.away_name == "Arsenal"


def test_h2h_outcomes_mapped_to_home_draw_away(replay):
    snaps, _ = fetch_odds("E0")
    assert _snaps_of(snaps, "ev1", "h2h", "pinnacle")[0].outcomes == {
        "home": 2.1,
        "draw": 3.5,
        "away": 3.6,
    }
    # 队名大小写/全名匹配：Bayern Munich -> home，而非误判
    assert _snaps_of(snaps, "ev2", "h2h", "williamhill")[0].outcomes == {
        "home": 1.75,
        "draw": 3.9,
        "away": 4.75,
    }


def test_totals_keeps_only_point_25(replay):
    snaps, _ = fetch_odds("E0", regions=("eu",))
    pin = _snaps_of(snaps, "ev1", "totals", "pinnacle")
    assert len(pin) == 1                                # 2.5 与 3.5 两线只留 2.5
    assert pin[0].outcomes == {"over": 1.95, "under": 1.95}
    # 3.5 线的价格（2.6 / 1.5）不得混进任何快照
    all_prices = {p for s in snaps for p in s.outcomes.values()}
    assert all_prices.isdisjoint({2.6, 1.5})


def test_only_h2h_and_totals_markets_produce_snapshots(replay):
    snaps, _ = fetch_odds("E0")
    assert {s.market for s in snaps} == {"h2h", "totals"}
    assert all(s.bookmaker != "spreads_only_book" for s in snaps)   # 无可下注 market 的 book 不产快照


def test_snapshot_is_frozen(replay):
    snaps, _ = fetch_odds("E0")
    with pytest.raises(dataclasses.FrozenInstanceError):
        snaps[0].market = "spreads"


# ---------------------------------------------------------------- 最优价


def test_best_prices_h2h_takes_max_per_outcome(replay):
    from fa.pipeline.odds_api import best_prices

    snaps, _ = fetch_odds("E0")
    ev1 = [s for s in snaps if s.event_key == "ev1"]
    best = best_prices(ev1, "h2h")
    assert best == {
        "home": (2.15, "betfair_ex_eu"),               # 2.10 pinnacle < 2.15 betfair
        "draw": (3.5, "pinnacle"),                     # 3.50 pinnacle > 3.40 betfair
        "away": (3.75, "betfair_ex_eu"),
    }


def test_best_prices_totals_takes_max_per_side(replay):
    from fa.pipeline.odds_api import best_prices

    snaps, _ = fetch_odds("E0")
    ev1 = [s for s in snaps if s.event_key == "ev1"]
    assert best_prices(ev1, "totals") == {
        "over": (1.95, "pinnacle"),
        "under": (2.0, "betfair_ex_eu"),
    }


def test_best_prices_tie_keeps_first_bookmaker():
    from fa.pipeline.odds_api import best_prices

    snaps = [
        OddsSnapshot("E0", "ev", "2026-09-05T14:00:00Z", "A", "B", "h2h", "eu", "first", {"home": 2.0}),
        OddsSnapshot("E0", "ev", "2026-09-05T14:00:00Z", "A", "B", "h2h", "uk", "second", {"home": 2.0}),
    ]
    assert best_prices(snaps, "h2h") == {"home": (2.0, "first")}


def test_best_prices_rejects_mixed_events(replay):
    from fa.pipeline.odds_api import best_prices

    snaps, _ = fetch_odds("E0")                        # 两场不同比赛混在一起
    with pytest.raises(ValueError, match="event_key"):
        best_prices(snaps, "h2h")


def test_best_prices_unknown_market_raises(replay):
    from fa.pipeline.odds_api import best_prices

    snaps, _ = fetch_odds("E0")
    with pytest.raises(ValueError, match="market"):
        best_prices(snaps, "spreads")


def test_best_prices_omits_unquoted_outcome():
    """某侧无任何 bookmaker 报价时该键缺席，调用方按缺价处理。"""
    from fa.pipeline.odds_api import best_prices

    snaps = [
        OddsSnapshot("E0", "ev", "2026-09-05T14:00:00Z", "A", "B", "totals", "eu", "b1", {"over": 1.9}),
        OddsSnapshot("E0", "ev", "2026-09-05T14:00:00Z", "A", "B", "h2h", "eu", "b1", {"home": 2.0}),
    ]
    assert best_prices(snaps, "totals") == {"over": (1.9, "b1")}   # h2h 快照不混入
    assert best_prices(snaps, "h2h") == {"home": (2.0, "b1")}


def test_malformed_event_is_skipped(monkeypatch):
    """缺 id / 队名的事件无法对齐下注，跳过且不影响其余事件解析。"""
    events = [
        {"id": "ev_bad", "commence_time": "2026-09-05T14:00:00Z", "bookmakers": []},
        _fixture_events()[0],
    ]
    monkeypatch.setattr(
        "fa.pipeline.odds_api._http_get",
        lambda url, params: (events, {"x-requests-remaining": QUOTA}),
    )
    monkeypatch.setenv("ODDS_API_KEY", "test-key")
    snaps, _ = fetch_odds("E0", regions=("eu",))
    assert {s.event_key for s in snaps} == {"ev1"}
    assert len(snaps) == 4


# ---------------------------------------------------------------- 额度记账（调用方落库）


def test_quota_absent_header_returns_none(monkeypatch):
    monkeypatch.setattr(
        "fa.pipeline.odds_api._http_get", lambda url, params: (_fixture_events(), {})
    )
    monkeypatch.setenv("ODDS_API_KEY", "test-key")
    snaps, quota = fetch_odds("E0", regions=("eu",))
    assert quota is None
    assert len(snaps) == 8                              # 额度缺失不影响解析


def test_quota_non_numeric_header_returns_none(monkeypatch):
    monkeypatch.setattr(
        "fa.pipeline.odds_api._http_get",
        lambda url, params: (_fixture_events(), {"x-requests-remaining": "n/a"}),
    )
    monkeypatch.setenv("ODDS_API_KEY", "test-key")
    _, quota = fetch_odds("E0")
    assert quota is None


def test_refresh_quota_false_skips_quota(monkeypatch):
    """refresh_quota=False：不读额度头、返回 None，落库由调用方决定。"""
    monkeypatch.setattr(
        "fa.pipeline.odds_api._http_get",
        lambda url, params: (_fixture_events(), {"x-requests-remaining": QUOTA}),
    )
    monkeypatch.setenv("ODDS_API_KEY", "test-key")
    snaps, quota = fetch_odds("E0", regions=("eu",), refresh_quota=False)
    assert quota is None
    assert len(snaps) == 8


def test_fetch_odds_never_persists_quota(replay, monkeypatch):
    """控制器裁定：fetch_odds 只取数，meta 落库归调用方（Task 5/9）。"""

    def boom(*args, **kwargs):
        raise AssertionError("fetch_odds 不得触碰 DB")

    monkeypatch.setattr("fa.db.set_meta", boom)
    monkeypatch.setattr("fa.db.get_meta", boom)
    _, quota = fetch_odds("E0", regions=("eu",))
    assert quota == 483                                 # 落库归调用方，此处只交出数值


# ---------------------------------------------------------------- 错误面


def test_missing_key_raises_without_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("无 key 不得触网")

    monkeypatch.setattr("fa.pipeline.odds_api._http_get", boom)
    monkeypatch.delenv("ODDS_API_KEY", raising=False)  # 宿主机可能导出真 key
    with pytest.raises(OddsApiError, match="ODDS_API_KEY 未配置"):
        fetch_odds("E0")


def test_unknown_league_raises(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "test-key")
    with pytest.raises(OddsApiError, match="Z0"):
        fetch_odds("Z0")


def test_fetch_odds_propagates_provider_error(monkeypatch):
    """429 等网络错误由 _http_get 包成 OddsApiError 后原样上抛。"""

    def boom(url, params):
        raise OddsApiError("Odds API HTTP 429")

    monkeypatch.setattr("fa.pipeline.odds_api._http_get", boom)
    monkeypatch.setenv("ODDS_API_KEY", "test-key")
    with pytest.raises(OddsApiError):
        fetch_odds("E0")


# ---------------------------------------------------------------- _http_get（唯一触网点）


class _FakeResp:
    def __init__(self, body, headers=None):
        self._body = body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _patch_urlopen(monkeypatch, fn):
    monkeypatch.setattr("urllib.request.urlopen", fn)


def test_http_get_builds_url_and_lowercases_headers(monkeypatch):
    seen = {}

    def fake_urlopen(url, timeout):
        seen["url"] = url
        seen["timeout"] = timeout
        return _FakeResp(b"{}", {"X-Requests-Remaining": "487"})

    _patch_urlopen(monkeypatch, fake_urlopen)
    from fa.pipeline.odds_api import _http_get

    payload, headers = _http_get(
        "https://api.the-odds-api.com/v4/sports/soccer_epl/odds",
        {"apiKey": "k", "regions": "eu,uk", "markets": "h2h,totals", "oddsFormat": "decimal"},
    )
    assert payload == {}
    assert headers == {"x-requests-remaining": "487"}   # 键统一小写，便于稳定读取
    assert seen["timeout"] == 30
    # urlencode：逗号编码进 query，key 只出现在 query（不落异常文案，见下）
    assert seen["url"].startswith("https://api.the-odds-api.com/v4/sports/soccer_epl/odds?")
    assert "apiKey=k" in seen["url"]
    assert "regions=eu%2Cuk" in seen["url"]
    assert "markets=h2h%2Ctotals" in seen["url"]
    assert "oddsFormat=decimal" in seen["url"]


def test_http_get_429_raises_quota_error(monkeypatch):
    def fake_urlopen(url, timeout):
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)

    _patch_urlopen(monkeypatch, fake_urlopen)
    from fa.pipeline.odds_api import _http_get

    with pytest.raises(OddsApiError, match="429"):
        _http_get("https://api.the-odds-api.com/v4/sports/soccer_epl/odds", {})


def test_http_get_other_http_error_raises_without_key_leak(monkeypatch):
    def fake_urlopen(url, timeout):
        raise urllib.error.HTTPError(url + "?apiKey=SECRET", 401, "Unauthorized", {}, None)

    _patch_urlopen(monkeypatch, fake_urlopen)
    from fa.pipeline.odds_api import _http_get

    with pytest.raises(OddsApiError) as ei:
        _http_get("https://api.the-odds-api.com/v4/x", {"apiKey": "SECRET"})
    assert "SECRET" not in str(ei.value)                # key 不进异常文案


def test_http_get_network_error_raises(monkeypatch):
    def fake_urlopen(url, timeout):
        raise urllib.error.URLError("name resolution failed")

    _patch_urlopen(monkeypatch, fake_urlopen)
    from fa.pipeline.odds_api import _http_get

    with pytest.raises(OddsApiError, match="网络异常"):
        _http_get("https://api.the-odds-api.com/v4/x", {})


def test_http_get_non_json_body_raises(monkeypatch):
    _patch_urlopen(monkeypatch, lambda url, timeout: _FakeResp(b"<html>gateway error</html>"))
    from fa.pipeline.odds_api import _http_get

    with pytest.raises(OddsApiError, match="JSON"):
        _http_get("https://api.the-odds-api.com/v4/x", {})


def test_fetch_odds_requests_expected_query(replay):
    """逐 region 单值请求；regions 不再逗号并参，markets 仍合并（额度按 region×market 计）。"""
    from fa.config import ODDS_SPORT_KEYS

    fetch_odds("E0")
    expected_url = f"https://api.the-odds-api.com/v4/sports/{ODDS_SPORT_KEYS['E0']}/odds"
    assert len(replay) == 2
    for region, (url, params) in zip(("eu", "uk"), replay):
        assert url == expected_url
        assert params == {
            "apiKey": "test-key",
            "regions": region,                          # 单 region，无逗号
            "markets": "h2h,totals",
            "oddsFormat": "decimal",
        }
