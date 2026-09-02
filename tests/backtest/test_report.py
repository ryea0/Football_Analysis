from fa.backtest.report import render_report


def _rows():
    return [{"league": "E0", "season": 2023, "date": "2023-08-12",
             "match_id": 1, "week_index": 1, "outcome": "H", "total_goals": 2,
             "p_home": 0.5, "p_draw": 0.25, "p_away": 0.25,
             "mkt_home": 0.5, "mkt_draw": 0.25, "mkt_away": 0.25,
             "odds_home": 2.0, "odds_draw": 4.0, "odds_away": 4.0,
             "p_over25": 0.5, "mkt_over25": None}]


def test_render_report(tmp_path):
    out = tmp_path / "r.md"
    summary = render_report(_rows(), out)
    text = out.read_text(encoding="utf-8")
    assert "GO" in text or "NO-GO" in text
    assert "log-loss" in text
    assert summary["n"] == 1
