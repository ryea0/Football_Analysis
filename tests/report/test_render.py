"""报告渲染测试——合成库（T2 schema + 手插 recommendations/fixtures/teams/meta/bets 种子）。

不 import T5/T6/T7/T9 的任何模块：渲染层只读表（recommendations join fixtures
join teams + meta），与并行落地的管线流解耦（spec §7.1 / 计划 Task 8）。

CLV 预览方向与 spec §7.3 一致：odds_taken / 收盘价 − 1。pm 视角下 am 价是被
咬住的 odds_taken、pm 价是更接近收盘的新价，故价「缩水」（2.10→1.90）给正
CLV，价「走低」（2.10→2.30）给负 CLV。
"""
import json

import pytest

from fa.db import connect, init_db
from fa.report.render import (
    render_matchday_report,
    render_pm_update,
    render_settlement_brief,
)

LG = "E0"
SUMMARY = {"train_n": 1234, "half_life": 100.0}
# D2 分轨 bankroll 键（与 fa.pipeline.paper.bankroll_key("model_only") 同形；
# 字面量书写以维持本文件「不 import 管线模块」的隔离约定）
BANKROLL_MO = "paper_bankroll:model_only"


def _persona_summary(degraded=()):
    """matchday 落 runs.summary 的 persona 块（T13 形状）：degraded 全列表。"""
    return {"train_n": 1234, "half_life": 100.0,
            "persona": {"called": 3, "ok": 3, "veto": 0,
                        "degraded": list(degraded)}}


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
        "INSERT INTO meta (key, value) VALUES (?, '1000.0')", (BANKROLL_MO,))
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
    # §7.1 第 4 条：bankroll 快照（D2 分轨）+ 未结注
    assert "bankroll" in out and "1000.00" in out
    assert "未结" in out and "2" in out
    # §7.1 第 2 条：M4 persona 段——无判决时显式一行，不再有占位文本
    assert "本相位无 persona 判决" in out
    assert "persona 未接入" not in out


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
    assert "本相位无 persona 判决" in out
    assert "bankroll" in out
    assert "未结" in out


def test_am_report_bankroll_uninitialized(conn):
    rid = _am_with_two(conn)
    conn.execute("DELETE FROM meta WHERE key=?", (BANKROLL_MO,))
    conn.commit()
    out = render_matchday_report(conn, rid, "am", SUMMARY, 450, False)
    assert "- model_only：未初始化" in out
    assert "- model_persona：未初始化" in out


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
    assert "本相位无 persona 判决" in out


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
    assert "## 盘口移动（0）" in out
    assert "本相位无 persona 判决" in out


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


def _persona_rec(conn, run_id, fixture_id, market, phase, best_odds, *,
                 verdict=None, delta=None, factors=None, report_md=None):
    """model_persona 行；带 verdict/delta/factors/report_md 即为已判行（T8 产物）。"""
    conn.execute(
        "INSERT INTO recommendations (run_id, fixture_id, strategy, market, phase,"
        " model_p, market_p, best_odds, bookmaker, edge, ev, kelly_stake_frac,"
        " verdict, confidence_delta, key_factors, report_md, created_at)"
        " VALUES (?,?,'model_persona',?,?,?,?,?,'pinnacle',0.07,0.10,"
        "0.020,?,?,?,?, '2026-09-03T03:00:00Z')",
        (run_id, fixture_id, market, phase, 0.550, 0.460, best_odds,
         verdict, delta, factors, report_md))


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
    """分轨 meta 值非数字（脏数据）→ 中性占位，不崩也不渲染原值。"""
    rid = _am_with_two(conn)
    conn.execute("UPDATE meta SET value='abc' WHERE key=?", (BANKROLL_MO,))
    conn.commit()
    out = render_matchday_report(conn, rid, "am", SUMMARY, 450, False)
    assert "- model_only：未初始化" in out
    assert "abc" not in out


def test_am_report_empty_string_bankroll_renders_neutral(conn):
    rid = _am_with_two(conn)
    conn.execute("UPDATE meta SET value='' WHERE key=?", (BANKROLL_MO,))
    conn.commit()
    out = render_matchday_report(conn, rid, "am", SUMMARY, 450, False)
    assert "- model_only：未初始化" in out


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


# ------------------------------------------------- persona 段（M4 T14 真渲染）


def test_am_report_persona_block_renders_all_verdicts_and_degraded(conn):
    """§7.1 第 2 条：判决按场次成行（✅/⚠️（delta）/⛔）+ key_factors 逐条 +
    report_md 引用行；降级场给 ⚪ 行并标中文原因；段尾给汇总行。"""
    rid = _run(conn, "am")
    f3 = _fixture(conn, "ev-3", _team(conn, "Newc"), _team(conn, "Everton"))
    f4 = _fixture(conn, "ev-4", _team(conn, "Leeds"), _team(conn, "Burnley"))
    conn.commit()
    _persona_rec(conn, rid, _fid(conn, "ev-1"), "H", "am", 2.05, verdict="agree",
                 delta=0.05, factors=json.dumps(["主队中卫伤停确认"],
                                                ensure_ascii=False),
                 report_md="模型对主胜的信心略保守。")
    _persona_rec(conn, rid, _fid(conn, "ev-2"), "O2.5", "am", 1.85,
                 verdict="downweight", delta=-0.05,
                 factors=json.dumps(["客队周中欧战"], ensure_ascii=False),
                 report_md="该市场仓位应下调。")
    _persona_rec(conn, rid, f3, "A", "am", 3.60, verdict="veto", delta=0.0,
                 factors="[]", report_md="放弃该市场。")
    conn.commit()
    text = render_matchday_report(conn, rid, "am", _persona_summary(
        [{"fixture_id": f4, "reason": "timeout"}]), 480, False)
    assert "✅" in text and "⚠️" in text and "⛔" in text and "⚪" in text
    assert "persona 4 场：✅1 ⚠️1 ⛔1 · 未生效 1" in text
    assert "persona 未生效（超时）" in text
    assert "downweight（-0.05）" in text                  # 降权必须带 delta
    assert "  - 主队中卫伤停确认" in text                # key_factors 缩进逐条
    assert len([l for l in text.splitlines()
                if l.strip().startswith("> ")]) >= 1     # report_md 引用行


def test_am_report_truncates_report_md_to_200(conn):
    """report_md（契约 ≤500 字）截 200 字引用——TG 推送不留长文。"""
    rid = _run(conn, "am")
    _persona_rec(conn, rid, _fid(conn, "ev-1"), "H", "am", 2.05, verdict="agree",
                 delta=0.0, factors="[]", report_md="字" * 300)
    conn.commit()
    text = render_matchday_report(conn, rid, "am", {}, 480, False)
    md_lines = [l for l in text.splitlines() if l.startswith("  > ")]
    assert md_lines and len(md_lines[0]) <= 4 + 200       # 「  > 」+ 200 字


def test_bankroll_block_lists_both_tracks(conn):
    """D2 分轨：bankroll 块两轨各一行，model_persona 未初始化不冒充 0。"""
    rid = _am_with_two(conn)
    out = render_matchday_report(conn, rid, "am", SUMMARY, 480, False)
    assert "- model_only：1000.00" in out
    assert "- model_persona：未初始化" in out


def test_am_report_persona_degraded_only(conn):
    """只有降级、无判决 → 汇总行零判决三格 + ⚪ 行，不出现占位文本。"""
    rid = _am_with_two(conn)
    out = render_matchday_report(conn, rid, "am", _persona_summary(
        [{"fixture_id": _fid(conn, "ev-1"), "reason": "extract"}]), 450, False)
    assert "persona 1 场：✅0 ⚠️0 ⛔0 · 未生效 1" in out
    assert "persona 未生效（输出无法解析为 JSON）" in out


def test_am_report_persona_degraded_unknown_reason_passthrough(conn):
    """词表外的原因原样透出（不静默吞，也不渲染成 None）。"""
    rid = _am_with_two(conn)
    out = render_matchday_report(conn, rid, "am", _persona_summary(
        [{"fixture_id": _fid(conn, "ev-1"), "reason": "warp_core_breach"}]),
        450, False)
    assert "persona 未生效（warp_core_breach）" in out


def test_am_report_persona_block_scoped_to_run(conn):
    """别的 run 的判决不得混入本相位的 persona 段。"""
    rid = _am_with_two(conn)
    other = _run(conn, "am")
    _persona_rec(conn, other, _fid(conn, "ev-1"), "H", "am", 2.05,
                 verdict="veto", delta=0.0, factors="[]", report_md="x")
    conn.commit()
    out = render_matchday_report(conn, rid, "am", SUMMARY, 450, False)
    assert "⛔" not in out
    assert "本相位无 persona 判决" in out


def test_pm_update_persona_block_reads_pm_run_summary(conn):
    """render_pm_update 签名不变：persona 段从 pm run 自身 summary 读降级。"""
    am = _am_with_two(conn)
    pm = _run(conn, "pm")
    _rec(conn, pm, _fid(conn, "ev-1"), "H", "pm", 2.10)
    conn.commit()
    conn.execute("UPDATE runs SET summary=? WHERE id=?", (json.dumps(
        {"persona": {"called": 1, "ok": 0, "veto": 0, "degraded": [
            {"fixture_id": _fid(conn, "ev-2"), "reason": "exit"}]}}), pm))
    conn.commit()
    out = render_pm_update(conn, am, pm, 430, False)
    assert "persona 未生效（hermes 异常退出）" in out
    assert "persona 1 场：✅0 ⚠️0 ⛔0 · 未生效 1" in out


def test_pm_update_added_candidate_carries_verdict_icon(conn):
    """pm 新增候选行尾附判决标识；盘口移动行不带点评。"""
    am = _am_with_two(conn)
    pm = _run(conn, "pm")
    fx = _fixture(conn, "ev-3", _team(conn, "Newc"), _team(conn, "Everton"))
    conn.commit()
    _rec(conn, pm, _fid(conn, "ev-1"), "H", "pm", 1.90)   # 移动（am 2.10 → 1.90）
    _rec(conn, pm, fx, "A", "pm", 3.60)                   # model_only 新增
    _persona_rec(conn, pm, fx, "A", "pm", 3.55, verdict="downweight",
                 delta=-0.05, factors="[]", report_md="x")
    conn.commit()
    out = render_pm_update(conn, am, pm, 430, False)
    added = [l for l in out.splitlines()
             if "Newc vs Everton" in l and "模型+persona" in l]
    assert added and "⚠️" in added[0]                     # 新增行带判决标识
    moved = [l for l in out.splitlines() if "2.10 → 1.90" in l]
    assert moved and "persona" not in moved[0]            # 移动行不带点评


def test_pm_update_added_candidate_without_verdict_has_no_icon(conn):
    """pm 新增但 persona 未判（如降级）→ 行尾无标识，不渲染空括号。"""
    am = _am_with_two(conn)
    pm = _run(conn, "pm")
    fx = _fixture(conn, "ev-3", _team(conn, "Newc"), _team(conn, "Everton"))
    conn.commit()
    _persona_rec(conn, pm, fx, "A", "pm", 3.60)           # verdict 仍 NULL
    conn.commit()
    out = render_pm_update(conn, am, pm, 430, False)
    added = [l for l in out.splitlines() if "Newc vs Everton" in l]
    # 「，persona ⚠️」标识不出现（注意别和策略标签「模型+persona」混淆）
    assert added and "，persona" not in added[0] and "None" not in added[0]


def test_am_report_ignores_non_persona_verdict_and_bad_json_factors(conn):
    """防御：model_only 轨的判决不进 persona 段；key_factors 非 JSON → 当空。"""
    rid = _run(conn, "am")
    fx = _fid(conn, "ev-1")
    _rec(conn, rid, fx, "H", "am", 2.10)
    _persona_rec(conn, rid, fx, "H", "am", 2.05, verdict="agree", delta=0.0,
                 factors="not-json{", report_md="x")
    _persona_rec(conn, rid, _fid(conn, "ev-2"), "O2.5", "am", 1.85,
                 verdict="agree", delta=0.0, factors=None, report_md=None)
    conn.commit()
    out = render_matchday_report(conn, rid, "am", SUMMARY, 450, False)
    assert "persona 2 场：✅2 ⚠️0 ⛔0 · 未生效 0" in out
    assert "not-json" not in out                          # 脏 factors 不透出
