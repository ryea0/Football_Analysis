"""页面冒烟：AppTest 逐页跑空库——页面必须优雅空态、不抛异常（设计 §7）。"""
from pathlib import Path

import pytest

st = pytest.importorskip("streamlit")   # 裸环境（无 dashboard 组）整文件跳过
import streamlit.testing.v1   # noqa: F401  子包懒加载，import streamlit 不带出 st.testing
from fa.db import init_db

PAGES = sorted((Path(__file__).resolve().parents[2] / "dashboard" / "pages").glob("*.py"))
assert PAGES, "dashboard/pages/ 下没有页面文件"   # 空目录即失败信号，不让冒烟静默空跑


@pytest.fixture(autouse=True)
def _clear_cache():
    """loaders 的 st.cache_data 键不含库路径（FA_DB 在 env）——每例前清缓存，
    防止同进程跨 AppTest 串台（终审 Important #1）。"""
    st.cache_data.clear()


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_page_renders_on_empty_db(page, tmp_path, monkeypatch):
    monkeypatch.setenv("FA_DB", str(tmp_path / "t.db"))
    init_db(tmp_path / "t.db")
    at = st.testing.v1.AppTest.from_file(str(page), default_timeout=60)
    at.run()
    assert not at.exception
    assert len(at.header) == 1      # 页面真的渲染了（防退化成只 early-stop）


def test_page_b2_renders_with_settled_bets(tmp_path, monkeypatch):
    """带注数据的页 2 冒烟（2026-09-29 回归）：空库冒烟走
    _breakdown_by_dim_from_bets 的 empty 早退分支绕过 groupby——b_bets 缺
    league 列的 KeyError 只有真实注数据才触发（按联赛/按周期双 tab 炸）。"""
    from fa.db import connect

    monkeypatch.setenv("FA_DB", str(tmp_path / "t.db"))
    init_db(tmp_path / "t.db")
    conn = connect(tmp_path / "t.db")
    try:
        conn.execute("INSERT INTO teams (league, name) VALUES ('E0', 'Team A'), ('E0', 'Team B')")
        conn.execute(
            "INSERT INTO runs (id, type, phase, started_at, status)"
            " VALUES (1, 'matchday', 'am', '2026-09-04T11:00:00', 'ok')")
        conn.execute(
            "INSERT INTO fixtures (id, league, event_key, source, kickoff_utc,"
            " home_team_id, away_team_id, status, created_at)"
            " VALUES (1, 'E0', 'evt1', 'oddsapi', '2026-09-05T19:00:00Z', 1, 2,"
            " 'scheduled', '2026-09-04T11:05:00')")
        conn.execute(
            "INSERT INTO recommendations (id, run_id, fixture_id, strategy, market, phase,"
            " model_p, market_p, best_odds, bookmaker, edge, ev, kelly_stake_frac, created_at)"
            " VALUES (1, 1, 1, 'model_only', 'H', 'am', 0.55, 0.50, 2.1, 'Pinnacle',"
            " 0.05, 0.155, 0.010, '2026-09-04T11:30:00')")
        # settled_at 北京日 09-04 须落在 30d 档范围内（anchor=rec 创建日 09-04），
        # 否则 filter_by_date_range 滤空 → empty 早退测不到 groupby
        conn.execute(
            "INSERT INTO bets (id, recommendation_id, mode, placed_at, bookmaker,"
            " odds_taken, stake, status, settled_at, return_amt, closing_odds, clv)"
            " VALUES (10, 1, 'paper', '2026-09-04T12:00:00', 'Pinnacle', 2.5, 10.0,"
            " 'won', '2026-09-04T06:30:00', 25.0, 2.2, 0.136364)")
        conn.commit()
    finally:
        conn.close()

    page = next(p for p in PAGES if p.name.startswith("2_"))
    at = st.testing.v1.AppTest.from_file(str(page), default_timeout=60)
    at.run()
    assert not at.exception
    assert len(at.header) == 1
