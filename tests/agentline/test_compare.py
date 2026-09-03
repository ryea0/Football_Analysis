"""三线对比：复用 evaluate/simulate，行 schema 适配正确性是核心。"""
import pytest

from fa.agentline.compare import compare_lines, merge_rows, render_report
from fa.db import connect, init_db


def _bp(mid, ph=0.4):
    return {"match_id": mid, "league": "E0", "season": 2023,
            "date": "2024-02-01", "p_home": ph, "p_draw": 0.3,
            "p_away": 1 - ph - 0.3, "p_over25": 0.5, "p_under25": 0.5,
            "mkt_home": 0.45, "mkt_draw": 0.28, "mkt_away": 0.27,
            "odds_home": 2.2, "odds_draw": 3.5, "odds_away": 3.6,
            "outcome": "H", "total_goals": 3}


@pytest.fixture()
def db_three(tmp_path):
    """1 场 bp 行 + A_base/A_enh 各一 ok 行（p_* 与线 P 略有差异）。"""
    init_db(tmp_path / "t.db")
    conn = connect(tmp_path / "t.db")
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        "INSERT INTO backtest_predictions (league, season, week_index,"
        " match_id, date, p_home, p_draw, p_away, p_over25, p_under25,"
        " mkt_home, mkt_draw, mkt_away, mkt_over25, odds_home, odds_draw,"
        " odds_away, outcome, total_goals)"
        " VALUES ('E0', 2023, 1, 1, '2024-02-01', 0.4, 0.3, 0.3, 0.5, 0.5,"
        " 0.45, 0.28, 0.27, 0.55, 2.2, 3.5, 3.6, 'H', 3)")
    for line, ph in (("A_base", 0.6), ("A_enh", 0.5)):
        conn.execute(
            "INSERT INTO agentline_predictions (match_id, line, p_home,"
            " p_draw, p_away, p_over25, confidence, reasoning_digest,"
            " sources_json, raw_output, status, repaired, created_at)"
            " VALUES (1, ?, ?, 0.2, ?, 0.6, 0.5, 'r', '[]', 'raw', 'ok',"
            " 0, '2026-09-04')", (line, ph, 1 - ph - 0.2))
    conn.commit()
    yield conn
    conn.close()


def _fake_cmp():
    from fa.backtest.metrics import evaluate
    rows = [_bp(1), _bp(2)]
    e = evaluate(rows)
    return {"n": 2, "P": e, "A_base": e, "A_enh": {"n": 0},
            "market": {"ll": e["market_ll"]},
            "roi": {"P": {"n": 0, "roi": 0.0}, "A_base": {"n": 0, "roi": 0.0},
                    "A_enh": {"n": 0, "roi": 0.0}},
            "audit": {"enh_sources": 0}}


def test_merge_overwrites_probs_keeps_market():
    ag = [{"match_id": 1, "line": "A_base", "p_home": 0.6, "p_draw": 0.2,
           "p_away": 0.2, "p_over25": 0.7, "outcome": "H", "total_goals": 3}]
    rows = merge_rows([_bp(1)], ag)
    assert rows[0]["p_home"] == 0.6 and rows[0]["mkt_home"] == 0.45
    assert rows[0]["odds_home"] == 2.2 and rows[0]["outcome"] == "H"


def test_compare_lines_three_tracks(db_three):        # fixture 见 Step 3 注
    cmp = compare_lines(db_three)
    assert set(cmp) >= {"P", "A_base", "A_enh", "market", "roi", "n"}
    assert cmp["n"] >= 1
    assert cmp["P"]["model_ll"] > 0 and cmp["A_base"]["model_ll"] > 0


def test_render_report_mentions_leakage_caveat(tmp_path):
    from tests.agentline.test_compare import _fake_cmp
    out = tmp_path / "r.md"
    render_report(_fake_cmp(), out)
    text = out.read_text(encoding="utf-8")
    assert "泄漏" in text and "A_base" in text and "A_enh" in text
