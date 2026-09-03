"""报告渲染测试——合成库（T2 schema + 手插 recommendations/fixtures/teams/meta/bets 种子）。

不 import T5/T6/T7/T9 的任何模块：渲染层只读表（recommendations join fixtures
join teams + meta），与并行落地的管线流解耦（spec §7.1 / 计划 Task 8）。

CLV 预览方向与 spec §7.3 一致：odds_taken / 收盘价 − 1。pm 视角下 am 价是被
咬住的 odds_taken、pm 价是更接近收盘的新价，故价「缩水」（2.10→1.90）给正
CLV，价「走低」（2.10→2.30）给负 CLV。
"""
import pytest

from fa.db import connect, init_db
from fa.report.render import (
    render_matchday_report,
    render_pm_update,
    render_settlement_brief,
)

LG = "E0"
SUMMARY = {"train_n": 1234, "half_life": 100.0}


# ---------------------------------------------------------------- 种子助手


def _team(conn, name):
    conn.execute("INSERT INTO teams (league, name) VALUES (?, ?)", (LG, name))
    return conn.execute("SELECT id FROM teams WHERE name=?", (name,)).fetchone()["id"]


def _fixture(conn, event_key, home_id, away_id, kickoff="2026-09-04T14:00:00Z"):
    cur = conn.execute(
        "INSERT INTO fixtures (league, event_key, source, kickoff_utc,"
        " home_team_id, away_team_id, status, created_at)"
        " VALUES (?,?,?,?,?,?, 'scheduled', '2026-09-03T03:00:00Z')",
        (LG, event_key, "oddsapi", kickoff, home_id, away_id))
    return cur.lastrowid


def _run(conn, phase):
    cur = conn.execute(
        "INSERT INTO runs (type, phase, started_at, status) VALUES"
        " ('matchday', ?, '2026-09-03T03:00:00Z', 'ok')", (phase,))
    return cur.lastrowid


def _rec(conn, run_id, fixture_id, market, phase, best_odds, model_p=0.520,
         market_p=0.460, ev=0.092, kelly=0.012):
    cur = conn.execute(
        "INSERT INTO recommendations (run_id, fixture_id, strategy, market, phase,"
        " model_p, market_p, best_odds, bookmaker, edge, ev, kelly_stake_frac,"
        " created_at) VALUES (?,?,'model_only',?,?,?,?,?,'pinnacle',0.060,?,?,"
        "'2026-09-03T03:00:00Z')",
        (run_id, fixture_id, market, phase, model_p, market_p, best_odds, ev, kelly))
    return cur.lastrowid


def _pending_bet(conn, rec_id, odds=2.10, stake=10.0):
    conn.execute(
        "INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker,"
        " odds_taken, stake, status) VALUES (?,'paper','2026-09-03T03:05:00Z',"
        "'pinnacle',?,?,'pending')", (rec_id, odds, stake))


def _fid(conn, event_key):
    return conn.execute(
        "SELECT id FROM fixtures WHERE event_key=?", (event_key,)).fetchone()["id"]


def _am_with_two(conn):
    """标准 am 场景：run + 2 条候选 + 各一笔 pending 注 + bankroll。"""
    rid = _run(conn, "am")
    r1 = _rec(conn, rid, _fid(conn, "ev-1"), "H", "am", 2.10)
    r2 = _rec(conn, rid, _fid(conn, "ev-2"), "O2.5", "am", 1.85)
    _pending_bet(conn, r1)
    _pending_bet(conn, r2)
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('paper_bankroll', '1000.0')")
    conn.commit()
    return rid


@pytest.fixture
def conn(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    for n, (h, a) in enumerate((("Arsenal", "Chelsea"),
                                ("Bayern", "Dortmund")), start=1):
        _fixture(c, f"ev-{n}", _team(c, h), _team(c, a))
    c.commit()
    yield c
    c.close()


# ------------------------------------------------------ matchday（am 全要素）


def test_am_report_renders_all_spec_elements(conn):
    rid = _am_with_two(conn)
    out = render_matchday_report(conn, rid, "am", SUMMARY, quota_left=450,
                                 degraded=False)
    # §7.1 第 1 条：市场/赔率/模型 p vs 市场 p/EV/仓位 + 场次
    assert "候选场次" in out and "（2）" in out
    assert "Arsenal vs Chelsea" in out and "Bayern vs Dortmund" in out
    assert "主胜" in out and "大2.5" in out
    assert "2.10" in out and "1.85" in out
    assert "0.520" in out and "0.460" in out
    assert "+9.20%" in out                      # EV
    assert "1.20%" in out                       # 仓位（kelly_stake_frac）
    # §7.1 第 3 条：风险提示（样本量、降级标注、额度水位）
    assert "风险提示" in out and "样本量" in out and "1234" in out
    assert "半衰期" in out
    assert "额度" in out and "450" in out
    assert "降级" in out
    # §7.1 第 4 条：bankroll 快照 + 未结注
    assert "bankroll" in out and "1000.00" in out
    assert "未结" in out and "2" in out
    # §7.1 第 2 条：M3 无 persona → 占位一行
    assert "persona 未接入（M4）" in out


def test_am_report_markdown_structure(conn):
    rid = _am_with_two(conn)
    out = render_matchday_report(conn, rid, "am", SUMMARY, 450, False)
    assert out.startswith("# ")
    assert out.endswith("\n")
    assert "| 联赛 | 场次 | 市场 |" in out
    assert "|---|" in out


def test_am_report_degraded_flag_visible(conn):
    rid = _am_with_two(conn)
    out = render_matchday_report(conn, rid, "am", SUMMARY, quota_left=80,
                                 degraded=True)
    assert "降级" in out and "是" in out
    assert "额度" in out and "80" in out


def test_am_report_empty_candidates(conn):
    """无候选（门槛未过或无赛程）→ 明确一行，不产出空表；骨架其余仍在。"""
    rid = _run(conn, "am")
    conn.commit()
    out = render_matchday_report(conn, rid, "am", SUMMARY, 450, False)
    assert "无候选" in out
    assert "| 联赛 | 场次 |" not in out
    assert "persona 未接入（M4）" in out
    assert "bankroll" in out
    assert "未结" in out


def test_am_report_bankroll_uninitialized(conn):
    rid = _am_with_two(conn)
    conn.execute("DELETE FROM meta WHERE key='paper_bankroll'")
    conn.commit()
    out = render_matchday_report(conn, rid, "am", SUMMARY, 450, False)
    assert "bankroll" in out and "未初始化" in out


def test_am_report_scoped_to_run(conn):
    """只渲染该 run_id 的行——别的 run 的候选不得混入。"""
    rid = _am_with_two(conn)
    other = _run(conn, "am")
    conn.commit()
    fx = _fixture(conn, "ev-3", _team(conn, "Alice"), _team(conn, "Bob"))
    conn.commit()
    _rec(conn, other, fx, "D", "am", 3.40)
    conn.commit()
    out = render_matchday_report(conn, rid, "am", SUMMARY, 450, False)
    assert "Alice vs Bob" not in out
    assert out.count(" vs ") == 2


def test_am_report_sample_size_missing_degrades_neutral(conn):
    """summary 未带样本量 → 报告仍完整，风险行给中性占位而非崩溃。"""
    rid = _am_with_two(conn)
    out = render_matchday_report(conn, rid, "am", {}, 450, False)
    assert "样本量" in out and "未提供" in out
    assert "persona 未接入（M4）" in out


def test_am_report_quota_unknown(conn):
    """额度未拉到（None）→ 显式「未知」，不渲染成 None。"""
    rid = _am_with_two(conn)
    out = render_matchday_report(conn, rid, "am", SUMMARY, None, False)
    assert "None" not in out
    assert "额度" in out and "未知" in out


# --------------------------------------------------------------- pm 更新版


def test_pm_update_price_move_shortening_is_positive_clv(conn):
    am = _am_with_two(conn)
    pm = _run(conn, "pm")
    _rec(conn, pm, _fid(conn, "ev-1"), "H", "pm", best_odds=1.90)
    conn.commit()
    out = render_pm_update(conn, am, pm, quota_left=430, degraded=False)
    assert "盘口移动" in out
    assert "Arsenal vs Chelsea" in out
    assert "2.10" in out and "1.90" in out
    assert "+10.5%" in out                       # CLV 预览：am 价/pm 价 − 1
    assert "CLV" in out


def test_pm_update_price_drifting_out_is_negative_clv(conn):
    am = _am_with_two(conn)
    pm = _run(conn, "pm")
    _rec(conn, pm, _fid(conn, "ev-1"), "H", "pm", best_odds=2.30)
    conn.commit()
    out = render_pm_update(conn, am, pm, 430, False)
    assert "-8.7%" in out


def test_pm_update_new_candidate(conn):
    """pm 才过门槛的新候选 → 新增段。"""
    am = _am_with_two(conn)
    pm = _run(conn, "pm")
    _rec(conn, pm, _fid(conn, "ev-1"), "H", "pm", 2.10)
    conn.commit()
    fx = _fixture(conn, "ev-3", _team(conn, "Newc"), _team(conn, "Everton"))
    conn.commit()
    _rec(conn, pm, fx, "A", "pm", 3.60)
    conn.commit()
    out = render_pm_update(conn, am, pm, 430, False)
    assert "新增候选" in out
    assert "Newc vs Everton" in out
    assert "3.60" in out


def test_pm_update_disappeared_candidate(conn):
    """am 已推但 pm 无对应行 → 已消失。"""
    am = _am_with_two(conn)
    pm = _run(conn, "pm")
    _rec(conn, pm, _fid(conn, "ev-1"), "H", "pm", 2.10)   # ev-2 无 pm 行
    conn.commit()
    out = render_pm_update(conn, am, pm, 430, False)
    assert "已消失" in out
    assert "Bayern vs Dortmund" in out and "大2.5" in out


def test_pm_update_does_not_resend_full_list(conn):
    """去重不重发全量：无 am 全量候选表，未变候选不重复成行。"""
    am = _am_with_two(conn)
    pm = _run(conn, "pm")
    _rec(conn, pm, _fid(conn, "ev-1"), "H", "pm", 2.10)   # 价未变
    _rec(conn, pm, _fid(conn, "ev-2"), "O2.5", "pm", 1.85)
    conn.commit()
    out = render_pm_update(conn, am, pm, 430, False)
    assert "候选场次（" not in out                # am 全量表头不出现
    assert out.count("Arsenal vs Chelsea") == 0   # 未变候选不成行
    assert "未变" in out and "2" in out
    assert "盘口移动" in out and "无" in out
    assert "persona 未接入（M4）" in out


def test_pm_update_all_three_cases_together(conn):
    am = _am_with_two(conn)
    pm = _run(conn, "pm")
    _rec(conn, pm, _fid(conn, "ev-1"), "H", "pm", 2.30)   # 移动（走低）
    conn.commit()
    fx = _fixture(conn, "ev-3", _team(conn, "Newc"), _team(conn, "Everton"))
    conn.commit()
    _rec(conn, pm, fx, "A", "pm", 3.60)                   # 新增
    conn.commit()                                          # 消失：ev-2 无 pm 行
    out = render_pm_update(conn, am, pm, 430, False)
    assert "盘口移动" in out and "2.30" in out and "-8.7%" in out
    assert "新增候选" in out and "Newc vs Everton" in out
    assert "已消失" in out and "Bayern vs Dortmund" in out
    assert "Arsenal vs Chelsea" in out and "2.10" in out


def test_pm_update_no_change(conn):
    """am/pm 完全一致 → 三个 diff 段都显式「无」。"""
    am = _am_with_two(conn)
    pm = _run(conn, "pm")
    _rec(conn, pm, _fid(conn, "ev-1"), "H", "pm", 2.10)
    _rec(conn, pm, _fid(conn, "ev-2"), "O2.5", "pm", 1.85)
    conn.commit()
    out = render_pm_update(conn, am, pm, 430, False)
    assert "盘口移动" in out and "无" in out
    assert "新增候选" in out and "无" in out
    assert "已消失" in out and "无" in out


def test_pm_update_quota_and_degraded(conn):
    am = _am_with_two(conn)
    pm = _run(conn, "pm")
    _rec(conn, pm, _fid(conn, "ev-1"), "H", "pm", 2.10)
    conn.commit()
    out = render_pm_update(conn, am, pm, quota_left=60, degraded=True)
    assert "额度" in out and "60" in out
    assert "降级" in out and "是" in out


# --------------------------------------------------------------- 结算简报


def test_settlement_brief_one_line_all_metrics():
    out = render_settlement_brief(
        {"settled": 3, "won": 2, "pnl": 12.5, "clv_median": 0.012})
    assert out.strip().count("\n") == 0          # 一行式
    assert "结算" in out
    assert "3" in out and "2" in out
    assert "+12.50" in out
    assert "CLV" in out and "+1.20%" in out


def test_settlement_brief_no_clv_and_negative_pnl():
    out = render_settlement_brief(
        {"settled": 1, "won": 0, "pnl": -5.0, "clv_median": None})
    assert "-5.00" in out
    assert "无收盘" in out                        # CLV 缺失显式标注
    assert out.strip().count("\n") == 0


def test_settlement_brief_tolerates_missing_keys():
    out = render_settlement_brief({})
    assert "结算" in out
    assert out.strip().count("\n") == 0
    assert "None" not in out


def test_pm_update_reuses_am_run_summary_sample_size(conn):
    """pm 不重拟合：样本量/半衰期沿用 am run 落库的 summary，而非报「未提供」。"""
    am = _am_with_two(conn)
    conn.execute("UPDATE runs SET summary=? WHERE id=?",
                 ('{"train_n": 4321, "half_life": 90.0}', am))
    pm = _run(conn, "pm")
    _rec(conn, pm, _fid(conn, "ev-1"), "H", "pm", 2.10)
    conn.commit()
    out = render_pm_update(conn, am, pm, 430, False)
    assert "4321" in out
    assert "90" in out
    assert "未提供" not in out


def test_pm_update_survives_unreadable_am_summary(conn):
    """am run 的 summary 为 NULL / 非 JSON → 风险行中性占位，不崩。"""
    am = _am_with_two(conn)
    conn.execute("UPDATE runs SET summary='not-json' WHERE id=?", (am,))
    pm = _run(conn, "pm")
    _rec(conn, pm, _fid(conn, "ev-1"), "H", "pm", 2.10)
    conn.commit()
    out = render_pm_update(conn, am, pm, 430, False)
    assert "样本量" in out
    assert "盘口移动" in out
