"""报告渲染测试——合成库（T2 schema + 手插 recommendations/fixtures/teams/meta/bets 种子）。

不 import T5/T6/T7/T9 的任何模块：渲染层只读表（recommendations join fixtures
join teams + meta），与并行落地的管线流解耦（spec §7.1 / 计划 Task 8）。

CLV 预览方向与 spec §7.3 一致：odds_taken / 收盘价 − 1。pm 视角下 am 价是被
咬住的 odds_taken、pm 价是更接近收盘的新价，故价「缩水」（2.10→1.90）给正
CLV，价「走低」（2.10→2.30）给负 CLV。
"""
import json
from datetime import datetime, timezone

import pytest

from fa.db import connect, init_db
from fa.report.render import (
    _LEAGUE_CN,
    _kickoff_cn,
    render_matchday_report,
    render_pm_update,
    render_settlement_brief,
)

LG = "E0"
SUMMARY = {"train_n": 1234, "half_life": 100.0}

# 「现在」冻结在北京 2026-09-05 14:00（UTC 06:00）：_kickoff_cn 的当日/跨日分支
# 用注入时刻判定，测试不依赖墙钟，也不会踩到周边界。
FROZEN_NOW = datetime(2026, 9, 5, 6, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- 种子助手


def _team(conn, name):
    conn.execute("INSERT INTO teams (league, name) VALUES (?, ?)", (LG, name))
    return conn.execute("SELECT id FROM teams WHERE name=?", (name,)).fetchone()["id"]


def _fixture(conn, event_key, home_id, away_id, kickoff="2026-09-04T14:00:00Z",
             league=LG):
    cur = conn.execute(
        "INSERT INTO fixtures (league, event_key, source, kickoff_utc,"
        " home_team_id, away_team_id, status, created_at)"
        " VALUES (?,?,?,?,?,?, 'scheduled', '2026-09-03T03:00:00Z')",
        (league, event_key, "oddsapi", kickoff, home_id, away_id))
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
    assert "| 联赛 | 场次 | 开赛 | 市场 |" in out
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
    assert "| 联赛 | 场次 | 开赛 |" not in out
    assert "### 英超" not in out
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


# ------------------------------------------------- 降级三态（额度节流 §3.4）


def test_degraded_wording_live_dual_region_says_no(conn):
    """live-dual：无降级 → 「否」。"""
    rid = _am_with_two(conn)
    out = render_matchday_report(conn, rid, "am", SUMMARY, 450, False)
    assert "- 降级：否" in out


def test_degraded_wording_snapshot_reuse_keeps_stale_warning(conn):
    """snapshot-reuse：本窗没拉盘 → 价格确实滞后，保留「复用既有快照」文案。"""
    rid = _am_with_two(conn)
    out = render_matchday_report(
        conn, rid, "am", {**SUMMARY, "snapshot_reused": True}, 80, True)
    assert "- 降级：是——本窗复用既有快照，价格类字段可能滞后" in out


def test_degraded_wording_region_merge_does_not_claim_stale(conn):
    """live-single-eu：拉了实时盘只是收窄范围 → **不得**谎称「复用既有快照」。

    quota=150 的 run 走的就是这态：价格是新的，缺的只是 uk 侧最优价。
    """
    rid = _am_with_two(conn)
    out = render_matchday_report(
        conn, rid, "am", {**SUMMARY, "region_merged": True}, 150, True)
    assert "- 降级：是——本窗为实时盘，但已按额度收窄到单 eu" in out
    assert "复用既有快照" not in out
    assert "uk 侧最优价缺失" in out


def test_degraded_wording_unknown_reason_points_at_summary(conn):
    """有降级但 summary 没带状态键 → 不猜原因，指向落库的理由串。"""
    rid = _am_with_two(conn)
    out = render_matchday_report(conn, rid, "am", SUMMARY, 450, True)
    assert "- 降级：是（原因见 runs.summary 的 degraded_reasons）" in out


def test_pm_update_degraded_state_reads_pm_summary_not_am(conn):
    """pm_update 的降级三态取**本窗（pm）**的 summary：am 的降级不污染 pm 行。

    样本量/半衰期仍沿用 am（pm 不重拟合）——两份 summary 各取所需。
    """
    am = _am_with_two(conn)
    pm = _run(conn, "pm")
    _rec(conn, pm, _fid(conn, "ev-1"), "H", "pm", 2.10)
    conn.execute("UPDATE runs SET summary=? WHERE id=?",
                 (json.dumps({"region_merged": True, "train_n": 7}), pm))
    conn.commit()

    out = render_pm_update(conn, am, pm, quota_left=150, degraded=True)

    assert "- 降级：是——本窗为实时盘，但已按额度收窄到单 eu" in out
    assert "复用既有快照" not in out
    # 对照：am 才带 region_merged、pm 无 summary → am 的状态**不泄漏**进 pm 行，
    # 但 degraded=True 仍在 → 落到「不猜原因」的中性句（而非照搬 am 的合并文案）
    conn.execute("UPDATE runs SET summary=NULL WHERE id=?", (pm,))
    conn.execute("UPDATE runs SET summary=? WHERE id=?",
                 (json.dumps({"region_merged": True}), am))
    conn.commit()
    out = render_pm_update(conn, am, pm, quota_left=150, degraded=True)
    assert "- 降级：是（原因见 runs.summary 的 degraded_reasons）" in out
    assert "收窄到单 eu" not in out and "复用既有快照" not in out


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
    assert "## 盘口移动（0）" in out
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
    assert "## 盘口移动（0）" in out
    assert "## 新增候选（0）" in out
    assert "## 已消失（0）" in out


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


# ------------------------------------------------- A/B 双轨（M4 前向）


def _persona_rec(conn, run_id, fixture_id, market, phase, best_odds):
    conn.execute(
        "INSERT INTO recommendations (run_id, fixture_id, strategy, market, phase,"
        " model_p, market_p, best_odds, bookmaker, edge, ev, kelly_stake_frac,"
        " created_at) VALUES (?,?,'model_persona',?,?,?,?,?,'pinnacle',0.07,0.10,"
        "0.020,'2026-09-03T03:00:00Z')",
        (run_id, fixture_id, market, phase, 0.550, 0.460, best_odds))


def test_am_report_two_strategies_same_market_render_distinctly(conn):
    """M4 前向：同 (fixture, market) 两套 strategy 必须各自成行，不得互相覆盖。

    DDL UNIQUE(fixture_id, market, strategy, phase) 允许 model_only 与
    model_persona 并存——报告若按 (fixture_id, market) 键去重会丢行。
    """
    rid = _run(conn, "am")
    fid = _fid(conn, "ev-1")
    _rec(conn, rid, fid, "H", "am", 2.10)
    _persona_rec(conn, rid, fid, "H", "am", 2.05)
    conn.commit()
    out = render_matchday_report(conn, rid, "am", SUMMARY, 450, False)
    assert "候选场次" in out and "（2）" in out
    assert out.count("Arsenal vs Chelsea") == 2      # 两行都在，未覆盖
    assert "纯模型" in out and "模型+persona" in out
    assert "2.10" in out and "2.05" in out


def test_pm_diff_does_not_pair_across_strategies(conn):
    """am=model_only / pm=model_persona 不得互相配对：应表现为 已消失+新增。"""
    am = _am_with_two(conn)                          # ev-1 H model_only @2.10
    pm = _run(conn, "pm")
    _persona_rec(conn, pm, _fid(conn, "ev-1"), "H", "pm", 2.20)
    conn.commit()
    out = render_pm_update(conn, am, pm, 430, False)
    assert "## 盘口移动（0）" in out                  # 不得跨 strategy 配成「移动」
    assert "2.10 → 2.20" not in out                  # 无跨轨 CLV 配对
    assert "已消失" in out and "Arsenal" in out      # am model_only 无 pm 对应
    assert "## 新增候选（1）" in out                  # pm model_persona 是新增
    assert "2.20" in out


# ------------------------------------------------- 降级分支（T4/T7 交互）


def test_am_report_unaligned_fixture_never_renders_none(conn):
    """fixtures.team_id 双 NULL（spec §3.3 未对齐）→ 回退 event_key 标注。"""
    rid = _run(conn, "am")
    fx = _fixture(conn, "ev-raw", None, None)
    conn.commit()
    _rec(conn, rid, fx, "H", "am", 2.10)
    conn.commit()
    out = render_matchday_report(conn, rid, "am", SUMMARY, 450, False)
    assert "None" not in out
    assert "未对齐（ev-raw）" in out


def test_am_report_non_numeric_bankroll_renders_neutral(conn):
    """meta.paper_bankroll 非数字（脏数据）→ 中性占位，不崩也不渲染原值。"""
    rid = _am_with_two(conn)
    conn.execute("UPDATE meta SET value='abc' WHERE key='paper_bankroll'")
    conn.commit()
    out = render_matchday_report(conn, rid, "am", SUMMARY, 450, False)
    assert "bankroll" in out and "未初始化" in out
    assert "abc" not in out


def test_am_report_empty_string_bankroll_renders_neutral(conn):
    rid = _am_with_two(conn)
    conn.execute("UPDATE meta SET value='' WHERE key='paper_bankroll'")
    conn.commit()
    out = render_matchday_report(conn, rid, "am", SUMMARY, 450, False)
    assert "未初始化" in out


# ------------------------------------------------- pm diff 数值守卫


def test_pm_update_zero_pm_price_skips_clv(conn):
    """pm 价 0（脏数据）→ 不除零，显式「无效价」标注。"""
    am = _am_with_two(conn)
    pm = _run(conn, "pm")
    _rec(conn, pm, _fid(conn, "ev-1"), "H", "pm", best_odds=0.0)
    conn.commit()
    out = render_pm_update(conn, am, pm, 430, False)
    assert "盘口移动" in out and "Arsenal vs Chelsea" in out
    assert "无效价" in out
    assert "CLV 预览" not in out


def test_pm_update_negative_pm_price_skips_clv(conn):
    am = _am_with_two(conn)
    pm = _run(conn, "pm")
    _rec(conn, pm, _fid(conn, "ev-1"), "H", "pm", best_odds=-1.0)
    conn.commit()
    out = render_pm_update(conn, am, pm, 430, False)
    assert "无效价" in out
    assert "CLV 预览" not in out


def test_pm_update_sub_epsilon_price_change_counts_as_unchanged(conn):
    """浮点噪声（abs diff < 1e-9）→ 视为未变，不产假「盘口移动」。"""
    am = _am_with_two(conn)
    pm = _run(conn, "pm")
    _rec(conn, pm, _fid(conn, "ev-1"), "H", "pm", best_odds=2.10 + 1e-12)
    conn.commit()
    out = render_pm_update(conn, am, pm, 430, False)
    assert "## 盘口移动（0）" in out
    assert "未变" in out and "2" in out


# ------------------------------------- 开赛时间列（北京时间） + 按联赛分组

# 用户反馈：候选散着看不通——同一联赛放到一起；开赛时间要北京时间。
# spec §7.1 第 1 条本就是「各联赛候选场次」，分组是回正而不是加戏。


def test_kickoff_cn_utc_afternoon_is_beijing_same_evening():
    """UTC 14:00 → 北京 22:00（+8 固定偏移，不依赖系统时区）。"""
    assert _kickoff_cn("2026-09-05T14:00:00Z", ref=FROZEN_NOW) == "周六 22:00"
    assert _kickoff_cn("2026-09-05T11:30:00Z", ref=FROZEN_NOW) == "周六 19:30"


def test_kickoff_cn_cross_day_prefixes_date():
    """跨日（如美州的「今晚」已是北京次日）→ 带 MM-DD，不让人误以为是当天。"""
    assert _kickoff_cn("2026-09-06T14:00:00Z", ref=FROZEN_NOW) == "09-06 周日 22:00"
    late = datetime(2026, 9, 3, 6, 0, tzinfo=timezone.utc)
    assert _kickoff_cn("2026-09-05T14:00:00Z", ref=late) == "09-05 周六 22:00"


def test_kickoff_cn_null_or_garbage_renders_dash_never_crashes():
    """kickoff_utc 缺失 / 空串（odds_api 无 commence_time）/ 垃圾 → 一律 —。"""
    for bad in (None, "", "   ", "not-a-date", "2026-13-99T99:00:00Z"):
        assert _kickoff_cn(bad, ref=FROZEN_NOW) == "—"


def test_league_cn_covers_big_five():
    assert _LEAGUE_CN["E0"] == "英超"
    assert _LEAGUE_CN["SP1"] == "西甲"
    assert _LEAGUE_CN["D1"] == "德甲"
    assert _LEAGUE_CN["I1"] == "意甲"
    assert _LEAGUE_CN["F1"] == "法甲"


def test_am_report_groups_candidates_by_league(conn):
    """多联赛 → 每联赛一块（中文标题 + 子表），同联赛行相邻，块序按固定联赛序。

    同联赛两行给**不同 EV**（0.12 / 0.05），且把 kickoff **反着排**——ev-2 更早开赛
    但 EV 更低。SQL 本按 kickoff 升序（会先出 ev-2），报告按 EV 降序（先出 ev-1）：
    两条序互相矛盾，这条断言才真的钉住「块内 EV 降序」，而非碰巧同序。
    """
    conn.execute(
        "UPDATE fixtures SET kickoff_utc='2026-09-04T18:00:00Z'"
        " WHERE event_key='ev-1'")
    conn.execute(
        "UPDATE fixtures SET kickoff_utc='2026-09-04T11:00:00Z'"
        " WHERE event_key='ev-2'")
    sp1 = _fixture(conn, "ev-sp1", _team(conn, "Real"), _team(conn, "Barca"),
                   league="SP1")
    conn.commit()
    rid = _run(conn, "am")
    _rec(conn, rid, _fid(conn, "ev-1"), "H", "am", 2.10, ev=0.12)      # E0 高
    _rec(conn, rid, _fid(conn, "ev-2"), "O2.5", "am", 1.85, ev=0.05)   # E0 低
    _rec(conn, rid, sp1, "A", "am", 3.60)                              # SP1
    conn.commit()
    out = render_matchday_report(conn, rid, "am", SUMMARY, 450, False)

    assert "### 英超（2）" in out and "### 西甲（1）" in out
    e0, es1 = out.index("### 英超"), out.index("### 西甲")
    assert e0 < es1                       # 固定联赛序：E0 在 SP1 前
    e0_block = out[e0:es1]
    assert "Real vs Barca" not in e0_block            # 西甲不混进英超块
    assert "Arsenal vs Chelsea" in e0_block
    assert "Bayern vs Dortmund" in e0_block
    assert e0_block.index("Arsenal vs Chelsea") < e0_block.index(
        "Bayern vs Dortmund")                          # 块内 EV 降序：0.12 在 0.05 前


def test_am_report_unknown_league_falls_back_to_raw_code(conn):
    """_LEAGUE_CN 没有的代码 → 标题原样回退，不丢块。"""
    ppl = _fixture(conn, "ev-ppl", _team(conn, "Urawa"), _team(conn, "Kashima"),
                   league="PPL")
    conn.commit()
    rid = _run(conn, "am")
    _rec(conn, rid, ppl, "H", "am", 2.40)
    conn.commit()
    out = render_matchday_report(conn, rid, "am", SUMMARY, 450, False)
    assert "### PPL（1）" in out
    assert "Urawa vs Kashima" in out


def test_am_report_kickoff_column_in_beijing_time(conn):
    """开赛列紧跟场次列，北京时间渲染（默认种子 14:00Z → 22:00）。"""
    rid = _am_with_two(conn)
    out = render_matchday_report(conn, rid, "am", SUMMARY, 450, False)
    assert "| 联赛 | 场次 | 开赛 | 市场 | 策略 |" in out
    assert "|---|---|---|---|---|---|---|---|---|---|---|" in out
    assert "22:00" in out
    assert "None" not in out


def test_pm_update_sections_grouped_by_league_with_kickoff(conn):
    """pm 三个 diff 段同样按联赛分块，行内带北京时间开赛；未知代码原样回退。"""
    sp1 = _fixture(conn, "ev-sp1", _team(conn, "Real"), _team(conn, "Barca"),
                   league="SP1")
    conn.commit()
    am = _run(conn, "am")
    _rec(conn, am, _fid(conn, "ev-1"), "H", "am", 2.10)
    _rec(conn, am, _fid(conn, "ev-2"), "O2.5", "am", 1.85)
    _rec(conn, am, sp1, "A", "am", 3.60)
    conn.commit()
    # pm 才出现的两联赛新增（E0 常规 + PPL 未知代码），新增段才会分出多块
    new_e0 = _fixture(conn, "ev-new0", _team(conn, "Newc"), _team(conn, "Everton"))
    new_ppl = _fixture(conn, "ev-new1", _team(conn, "Urawa"), _team(conn, "Kashima"),
                       league="PPL")
    conn.commit()
    pm = _run(conn, "pm")
    _rec(conn, pm, _fid(conn, "ev-1"), "H", "pm", 1.90)     # E0 移动
    _rec(conn, pm, sp1, "A", "pm", 3.40)                    # SP1 移动
    _rec(conn, pm, new_e0, "D", "pm", 3.10)                 # E0 新增
    _rec(conn, pm, new_ppl, "H", "pm", 2.40)                # PPL 新增（未知代码）
    conn.commit()                                            # ev-2 无 pm 行 → 已消失
    out = render_pm_update(conn, am, pm, 430, False)

    moved = out.split("## 新增候选")[0]
    assert "### 英超（1）" in moved and "### 西甲（1）" in moved
    assert moved.index("### 英超") < moved.index("### 西甲")
    assert "开赛" in moved and "22:00" in moved

    added = out.split("## 新增候选")[1].split("## 已消失")[0]
    assert "### 英超（1）" in added and "### PPL（1）" in added   # 未知代码原样回退
    assert added.index("### 英超") < added.index("### PPL")       # 五大在未知代码前
    assert "Newc vs Everton" in added and "Urawa vs Kashima" in added

    gone = out.split("## 已消失")[1]
    assert "### 英超（1）" in gone
    assert "Bayern vs Dortmund" in gone
