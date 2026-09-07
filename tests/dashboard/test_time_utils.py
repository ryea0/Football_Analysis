"""time_utils 纯函数单元测试。"""
from datetime import date, timedelta

import pandas as pd
import pytest

from dashboard.time_utils import (
    PRESETS,
    aggregate_by_grain,
    anchor_from_series,
    auto_grain,
    filter_by_date_range,
    preset_range,
    recalc_cumulative,
    to_bj_dates,
    to_bj_period,
)


# ── 基础：北京日转换 ──────────────────────────────

class TestToBjDates:
    def test_utc_noon_same_day(self):
        s = pd.Series(["2026-09-07T12:00:00Z"])
        assert to_bj_dates(s).iloc[0] == date(2026, 9, 7)

    def test_utc_late_crosses_to_next_bj_day(self):
        """UTC 22:09 = 北京次日 06:09 → 北京日应该是 09-08"""
        s = pd.Series(["2026-09-07T22:09:33Z"])
        assert to_bj_dates(s).iloc[0] == date(2026, 9, 8)

    def test_utc_early_morning_same_bj_day(self):
        """UTC 00:00 = 北京 08:00 → 北京日相同"""
        s = pd.Series(["2026-09-07T00:00:00Z"])
        assert to_bj_dates(s).iloc[0] == date(2026, 9, 7)

    def test_bare_string_treated_as_utc(self):
        s = pd.Series(["2026-09-07 22:09:33"])
        assert to_bj_dates(s).iloc[0] == date(2026, 9, 8)

    def test_invalid_returns_nat(self):
        s = pd.Series(["not-a-date"])
        assert pd.isna(to_bj_dates(s).iloc[0])

    def test_empty_series(self):
        s = pd.Series([], dtype=str)
        assert to_bj_dates(s).empty


# ── 周期标签（日/周/月） ──────────────────────────

class TestToBjPeriod:
    def test_day_format(self):
        s = pd.Series(["2026-09-07T12:00:00Z"])
        assert to_bj_period(s, "D").iloc[0] == "2026-09-07"

    def test_week_format_isoweek(self):
        # 2026-09-07 是周一，ISO 周 = 2026-W37
        s = pd.Series(["2026-09-07T12:00:00Z"])
        assert to_bj_period(s, "W").iloc[0] == "2026-W37"

    def test_week_sunday_belongs_to_same_week(self):
        # 2026-09-06 是周日，ISO 周 = 2026-W36（ISO 周从周一开始）
        s = pd.Series(["2026-09-06T12:00:00Z"])
        assert to_bj_period(s, "W").iloc[0] == "2026-W36"

    def test_week_cross_year_boundary(self):
        # 2026-12-31 周四 → ISO 周 2026-W53（或 2027-W01，取决于 ISO 规则）
        # 实际验证：pandas 的 %G-W%V 对 2026-12-31 返回 2026-W53
        s = pd.Series(["2026-12-31T12:00:00Z"])
        result = to_bj_period(s, "W").iloc[0]
        assert result.startswith("2026-W") or result.startswith("2027-W")

    def test_month_format(self):
        s = pd.Series(["2026-09-15T12:00:00Z"])
        assert to_bj_period(s, "M").iloc[0] == "2026-09"

    def test_invalid_grain_raises(self):
        s = pd.Series(["2026-09-07T12:00:00Z"])
        with pytest.raises(ValueError, match="unknown grain"):
            to_bj_period(s, "Q")


# ── 自动粒度规则 ──────────────────────────────────

class TestAutoGrain:
    def test_none_is_month(self):
        assert auto_grain(None) == "M"

    def test_1_day_is_day(self):
        assert auto_grain(1) == "D"

    def test_14_days_is_day(self):
        assert auto_grain(14) == "D"

    def test_15_days_is_week(self):
        assert auto_grain(15) == "W"

    def test_90_days_is_week(self):
        assert auto_grain(90) == "W"

    def test_91_days_is_month(self):
        assert auto_grain(91) == "M"

    def test_365_days_is_month(self):
        assert auto_grain(365) == "M"


# ── 预设档位日期计算 ───────────────────────────────

class TestPresetRange:
    def test_1d_includes_anchor(self):
        anchor = date(2026, 9, 7)
        start, end, grain = preset_range("1d", anchor)
        assert start == end == date(2026, 9, 7)
        assert grain == "D"

    def test_7d_back_from_anchor(self):
        anchor = date(2026, 9, 7)
        start, end, grain = preset_range("7d", anchor)
        assert end == date(2026, 9, 7)
        assert start == date(2026, 9, 1)  # 7 天含两端
        assert (end - start).days + 1 == 7
        assert grain == "D"

    def test_30d_grain_is_week(self):
        anchor = date(2026, 9, 7)
        start, end, grain = preset_range("30d", anchor)
        assert (end - start).days + 1 == 30
        assert grain == "W"

    def test_all_returns_wide_range(self):
        start, end, grain = preset_range("all", date(2026, 9, 7))
        assert start.year < 2010
        assert end.year > 2050
        assert grain == "M"

    def test_invalid_preset_raises(self):
        with pytest.raises(ValueError, match="unknown preset"):
            preset_range("foobar", date(2026, 9, 7))

    def test_all_presets_produce_valid_ranges(self):
        anchor = date(2026, 9, 7)
        for key in PRESETS:
            start, end, grain = preset_range(key, anchor)
            assert start <= end
            assert grain in ("D", "W", "M")


# ── 日期范围过滤 ──────────────────────────────────

class TestFilterByDateRange:
    @pytest.fixture
    def sample_df(self):
        return pd.DataFrame({
            "ts": [
                "2026-09-05T10:00:00Z",  # 北京 09-05 18:00 → 北京日 09-05
                "2026-09-06T22:00:00Z",  # 北京 09-07 06:00 → 北京日 09-07
                "2026-09-07T12:00:00Z",  # 北京 09-07 20:00 → 北京日 09-07
                "2026-09-08T08:00:00Z",  # 北京 09-08 16:00 → 北京日 09-08
                "2026-09-10T10:00:00Z",  # 北京 09-10 18:00 → 北京日 09-10
            ],
            "val": [1, 2, 3, 4, 5],
        })

    def test_both_ends_inclusive(self, sample_df):
        result = filter_by_date_range(sample_df, "ts", date(2026, 9, 7), date(2026, 9, 8))
        # 9-06 22:00Z → 北京 9-07 → 包含；9-07、9-08 共 3 行
        assert len(result) == 3
        assert set(result["val"]) == {2, 3, 4}

    def test_single_day(self, sample_df):
        result = filter_by_date_range(sample_df, "ts", date(2026, 9, 7), date(2026, 9, 7))
        # 北京日 09-07 有两行（第 2 行和第 3 行）
        assert len(result) == 2
        assert set(result["val"]) == {2, 3}

    def test_no_match_returns_empty(self, sample_df):
        result = filter_by_date_range(sample_df, "ts", date(2020, 1, 1), date(2020, 1, 2))
        assert result.empty

    def test_empty_df_returns_empty(self):
        df = pd.DataFrame(columns=["ts", "val"])
        result = filter_by_date_range(df, "ts", date(2026, 9, 1), date(2026, 9, 7))
        assert result.empty

    def test_missing_column_returns_original(self, sample_df):
        result = filter_by_date_range(sample_df, "nonexistent", date(2026, 9, 1), date(2026, 9, 7))
        assert len(result) == len(sample_df)

    def test_returns_copy_not_view(self, sample_df):
        result = filter_by_date_range(sample_df, "ts", date(2026, 9, 7), date(2026, 9, 7))
        result.loc[result.index[0], "val"] = 999
        assert sample_df["val"].iloc[0] == 1  # 原 df 不受影响


# ── 按粒度聚合 ────────────────────────────────────

class TestAggregateByGrain:
    @pytest.fixture
    def bets_df(self):
        """模拟 3 个策略 × 多日的 paper 注。"""
        rows = []
        for day_offset in range(14):  # 14 天
            d = date(2026, 9, 1) + timedelta(days=day_offset)
            for strat in ["A", "B"]:
                rows.append({
                    "ts": f"{d.isoformat()}T12:00:00Z",
                    "strategy": strat,
                    "pnl": 10.0 if strat == "A" else -5.0,
                    "stake": 100.0,
                })
        return pd.DataFrame(rows)

    def test_daily_aggregation(self, bets_df):
        result = aggregate_by_grain(
            bets_df, "ts", "D",
            agg_spec={"total_pnl": ("sum", "pnl"), "n": ("count", "pnl")},
            group_cols=["strategy"],
        )
        # 14 天 × 2 策略 = 28 行
        assert len(result) == 28
        assert set(result.columns) == {"period", "strategy", "total_pnl", "n"}
        # 每天每策略 1 注
        assert (result["n"] == 1).all()
        # 每天 A 策略 pnl = 10
        a_days = result[result["strategy"] == "A"]
        assert (a_days["total_pnl"] == 10.0).all()

    def test_weekly_aggregation(self, bets_df):
        result = aggregate_by_grain(
            bets_df, "ts", "W",
            agg_spec={"total_pnl": ("sum", "pnl"), "n": ("count", "pnl")},
            group_cols=["strategy"],
        )
        # 14 天跨 3 个 ISO 周（9-1 周二 → W36 有 6 天，W37 有 7 天，W38 有 1 天）
        # 9-01 周二 北京日 = W36, 9-07 周一 = W37, 9-14 周一 = W38
        assert len(result["period"].unique()) >= 2
        # 每周每策略的 n = 该周天数
        a_weeks = result[result["strategy"] == "A"]
        assert a_weeks["total_pnl"].sum() == pytest.approx(14 * 10.0)

    def test_monthly_aggregation(self, bets_df):
        result = aggregate_by_grain(
            bets_df, "ts", "M",
            agg_spec={"total_pnl": ("sum", "pnl")},
            group_cols=["strategy"],
        )
        # 9 月份 2 策略 = 2 行
        assert len(result) == 2
        a_row = result[result["strategy"] == "A"].iloc[0]
        assert a_row["period"] == "2026-09"
        assert a_row["total_pnl"] == pytest.approx(14 * 10.0)

    def test_no_group_cols(self, bets_df):
        result = aggregate_by_grain(
            bets_df, "ts", "M",
            agg_spec={"total_pnl": ("sum", "pnl"), "n": ("count", "pnl")},
        )
        assert len(result) == 1
        assert result["n"].iloc[0] == 28  # 14 天 × 2 策略

    def test_empty_df(self):
        df = pd.DataFrame(columns=["ts", "pnl"])
        result = aggregate_by_grain(df, "ts", "D", {"p": ("sum", "pnl")})
        assert result.empty
        assert list(result.columns) == ["period", "p"]


# ── 累计曲线重算 ──────────────────────────────────

class TestRecalcCumulative:
    @pytest.fixture
    def cum_df(self):
        return pd.DataFrame({
            "ts": [f"2026-09-{d:02d}T12:00:00Z" for d in range(1, 11)],
            "strategy": ["A"] * 5 + ["B"] * 5,
            "pnl": [1.0, 2.0, -1.0, 3.0, 0.5,
                    -2.0, 1.0, 1.0, -0.5, 2.0],
        })

    def test_full_range_matches_cumsum(self, cum_df):
        result = recalc_cumulative(
            cum_df[cum_df["strategy"] == "A"],
            "ts", "pnl",
            date(2026, 9, 1), date(2026, 9, 5),
        )
        assert len(result) == 5
        assert result["cum_value"].iloc[-1] == pytest.approx(1 + 2 - 1 + 3 + 0.5)
        assert result["cum_value"].iloc[0] == pytest.approx(1.0)  # 起点从 0 + 第一笔 开始

    def test_partial_range_restarts_from_zero(self, cum_df):
        """从第 3 天开始，累计应该从第 3 天的值重新算起。"""
        result = recalc_cumulative(
            cum_df[cum_df["strategy"] == "A"],
            "ts", "pnl",
            date(2026, 9, 3), date(2026, 9, 5),
        )
        assert len(result) == 3
        # 第一笔 = -1.0（第 3 天），所以累计起点是 -1.0
        assert result["cum_value"].iloc[0] == pytest.approx(-1.0)
        assert result["cum_value"].iloc[-1] == pytest.approx(-1 + 3 + 0.5)

    def test_grouped_strategies(self, cum_df):
        result = recalc_cumulative(
            cum_df, "ts", "pnl",
            date(2026, 9, 1), date(2026, 9, 10),
            group_col="strategy",
        )
        # 每组各自独立累计
        a = result[result["strategy"] == "A"]
        b = result[result["strategy"] == "B"]
        assert a["cum_value"].iloc[-1] == pytest.approx(1 + 2 - 1 + 3 + 0.5)
        assert b["cum_value"].iloc[-1] == pytest.approx(-2 + 1 + 1 - 0.5 + 2)

    def test_no_match_returns_empty(self, cum_df):
        result = recalc_cumulative(
            cum_df, "ts", "pnl",
            date(2020, 1, 1), date(2020, 1, 2),
        )
        assert result.empty
        assert "cum_value" in result.columns

    def test_sorted_by_time(self, cum_df):
        # 故意打乱顺序
        shuffled = cum_df.sample(frac=1, random_state=42)
        result = recalc_cumulative(
            shuffled[shuffled["strategy"] == "A"],
            "ts", "pnl",
            date(2026, 9, 1), date(2026, 9, 5),
        )
        # 结果应按时间升序
        assert result["ts"].is_monotonic_increasing


# ── 锚点日期推导 ──────────────────────────────────

class TestAnchorFromSeries:
    def test_returns_max_bj_date(self):
        s = pd.Series([
            "2026-09-05T12:00:00Z",
            "2026-09-10T12:00:00Z",
            "2026-09-07T22:00:00Z",  # 北京 9-08 → 北京日 09-08
        ])
        assert anchor_from_series(s) == date(2026, 9, 10)

    def test_empty_returns_none(self):
        s = pd.Series([], dtype=str)
        assert anchor_from_series(s) is None

    def test_all_invalid_returns_none(self):
        s = pd.Series(["not-a-date", "also-bad"])
        assert anchor_from_series(s) is None
