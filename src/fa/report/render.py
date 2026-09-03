"""B 线报告渲染（spec §7.1）——纯函数：只读 DB，无网络、无子进程。

与并行落地的管线（T5 fixtures / T6 推荐生成 / T9 matchday）解耦：这里只消费表
（recommendations join fixtures join teams、runs、meta、bets）和入参字典，不
import 上述任何模块——因此各流可独立演进。唯一的跨模块依赖是 T7 paper 层的
:func:`BANKROLL_KEY`（合流 follow-up：键名单源，不再自声明同名字面量）。

persona 段在 M3 无实现，一律渲染占位一行（spec §6 推迟到 M4）。

CLV 预览方向与 spec §7.3 一致：odds_taken / 收盘价 − 1。pm 视角下 am 价是被
咬住的 odds_taken、pm 价是更接近收盘的新价，故价「缩水」给正 CLV、价「走低」
给负 CLV。
"""
import json

from fa.db import get_meta
from fa.model.fit import FitConfig
from fa.pipeline.paper import BANKROLL_KEY

# 半衰期缺省取 FitConfig 单源（M2 模型层），报告不再自造常量
_DEFAULT_HALF_LIFE = FitConfig.half_life_days

_MARKET = {"H": "主胜", "D": "平局", "A": "客胜", "O2.5": "大2.5"}

# §6.6 A/B 双轨：recommendations 的 UNIQUE(fixture_id, market, strategy, phase)
# 允许两套 strategy 对同一 (fixture, market) 并存——键与展示都必须区分，
# 否则 M4 上线后 am 全量报告丢行、pm diff 跨轨配价。
_STRATEGY = {"model_only": "纯模型", "model_persona": "模型+persona"}

# 价格比较容差：best_odds 经 JSON/浮点往返，1e-9 级差异视为未变，不产假「盘口移动」
_ODDS_EPS = 1e-9

# run summary 的样本量键名已由 T9 钉死为 train_n（summary 同时落 half_life）。
# 缺失时给中性占位「样本量：未提供」，报告骨架不缺行。
_TRAIN_KEYS = ("train_n",)

_PERSONA_LINE = "persona 未接入（M4）"

_REC_SQL = """
SELECT r.id, r.fixture_id, r.market, r.strategy, r.model_p, r.market_p,
       r.best_odds, r.bookmaker, r.ev, r.kelly_stake_frac,
       f.league, f.kickoff_utc, f.event_key,
       th.name AS home, ta.name AS away
FROM recommendations r
JOIN fixtures f ON f.id = r.fixture_id
LEFT JOIN teams th ON th.id = f.home_team_id
LEFT JOIN teams ta ON ta.id = f.away_team_id
WHERE r.run_id = ?
ORDER BY f.kickoff_utc, f.id, r.market
"""


# ------------------------------------------------------------------ 工具


def _recs(conn, run_id):
    return [dict(r) for r in conn.execute(_REC_SQL, (run_id,))]


def _run_summary(conn, run_id):
    """读 runs.summary（JSON）。pm 不重拟合，样本量/半衰期沿用 am 落库值；
    NULL / 非 JSON 一律回退空 dict，风险行给中性占位。"""
    row = conn.execute(
        "SELECT summary FROM runs WHERE id=?", (run_id,)).fetchone()
    raw = row["summary"] if row is not None else None
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _label(rec):
    """场次行：队名缺失（未对齐，spec §3.3）时回退 event_key，不渲染 None。"""
    h, a = rec["home"], rec["away"]
    if h and a:
        return f"{h} vs {a}"
    return f"未对齐（{rec['event_key']}）"


def _pending_count(conn):
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM bets WHERE status='pending'").fetchone()
    return row["n"]


def _bankroll(conn):
    raw = get_meta(conn, BANKROLL_KEY)
    if raw is None:
        return "未初始化"
    try:
        return f"{float(raw):.2f}"
    except (TypeError, ValueError):
        return "未初始化"


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _sample_line(summary):
    for k in _TRAIN_KEYS:
        v = summary.get(k)
        if _is_num(v):
            return f"- 样本量：训练样本 {int(v)} 场"
    return "- 样本量：未提供（run summary 未带训练样本数）"


def _risk_block(quota_left, degraded, summary):
    half = summary.get("half_life")
    half_s = (f"{float(half):.0f}" if _is_num(half)
              else f"{_DEFAULT_HALF_LIFE:.0f}")
    quota_s = "未知（本次未拉到额度头）" if quota_left is None else str(quota_left)
    degraded_s = "是——本窗复用既有快照，价格类字段可能滞后" if degraded else "否"
    return ["## 风险提示", "",
            _sample_line(summary),
            f"- 半衰期：{half_s} 天（时间衰减窗口，越旧权重越低）",
            f"- 降级：{degraded_s}",
            f"- 额度水位：剩余 {quota_s}（Odds API credits）"]


def _persona_block():
    return ["## persona", "", f"- {_PERSONA_LINE}"]


def _mkt(rec):
    return _MARKET.get(rec["market"], rec["market"])


def _strategy(rec):
    return _STRATEGY.get(rec["strategy"], rec["strategy"])


def _candidate_table(recs):
    """§7.1 第 1 条：市场/赔率/模型 p vs 市场 p/EV/仓位（+ 策略列，A/B 双轨）。"""
    header = ("| 联赛 | 场次 | 市场 | 策略 | 最优价 | 模型 p | 市场 p | EV "
              "| 仓位 | 博彩商 |")
    sep = "|---|---|---|---|---|---|---|---|---|---|"
    rows = [
        f"| {r['league']} | {_label(r)} | {_mkt(r)} | {_strategy(r)} "
        f"| {r['best_odds']:.2f} | {r['model_p']:.3f} | {r['market_p']:.3f} "
        f"| {r['ev']:+.2%} | {r['kelly_stake_frac']:.2%} | {r['bookmaker']} |"
        for r in recs]
    return [header, sep] + rows


def _key(rec):
    """pm diff 配对键：必须含 strategy——model_only 与 model_persona 各自成轨，
    跨轨不得互相配价（M4 落 model_persona 行后尤其如此）。"""
    return (rec["fixture_id"], rec["market"], rec["strategy"])


def _clv_note(am_odds, pm_odds):
    """pm 价作收盘近似的 CLV 预览；pm 价非正（脏数据）→ 跳过，不除零。"""
    if pm_odds > 0:
        return f"（CLV 预览 {am_odds / pm_odds - 1:+.1%}）"
    return f"（无效价：pm 价 {pm_odds:.2f} ≤ 0，不计算 CLV）"


def _heading(title, n):
    return [f"## {title}（{n}）", ""]


# ---------------------------------------------------------- matchday 全量


def render_matchday_report(conn, run_id, phase, summary, quota_left, degraded):
    """比赛日完整报告（spec §7.1 第 1-4 条 + persona 占位）。"""
    recs = _recs(conn, run_id)
    phase_cn = "上午（am 全量）" if phase == "am" else "下午（pm）"
    lines = [f"# 比赛日报告 — {phase_cn}", ""]

    lines += _heading("候选场次", len(recs))
    lines += _candidate_table(recs) if recs else ["- 无候选（门槛未过或无赛程）"]
    lines.append("")

    lines += _risk_block(quota_left, degraded, summary or {})
    lines.append("")

    lines += ["## bankroll", "",
              f"- bankroll：{_bankroll(conn)}（paper，meta `{BANKROLL_KEY}`）",
              f"- 未结注：{_pending_count(conn)} 注（paper，status=pending）"]
    lines.append("")

    lines += _persona_block()
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------- pm 更新版（diff）


def render_pm_update(conn, am_run_id, pm_run_id, quota_left, degraded):
    """17:00 更新版（spec §7.1 第 5 条 / §9.6）：只推与 11:00 的差异。

    - 盘口移动：am 已推候选在 pm 有对应行且价格变化（CLV 预览 = am价/pm价 − 1）
    - 已消失：am 已推但 pm 无对应行（pm 未过门槛 / 报价撤除）
    - 新增候选：pm 才出现的候选
    未变候选只计数，不成行——去重不重发全量。
    样本量/半衰期沿用 am run 落库 summary（pm 不重拟合）。
    """
    am, pm = _recs(conn, am_run_id), _recs(conn, pm_run_id)
    am_by, pm_by = {_key(r): r for r in am}, {_key(r): r for r in pm}

    moved, gone, unchanged = [], [], []
    for k, r in am_by.items():
        p = pm_by.get(k)
        if p is None:
            gone.append(r)
        elif abs(p["best_odds"] - r["best_odds"]) >= _ODDS_EPS:
            moved.append((r, p))
        else:
            unchanged.append(r)
    added = [r for k, r in pm_by.items() if k not in am_by]

    lines = ["# 比赛日更新（pm）", ""]

    lines += _heading("盘口移动", len(moved))
    lines += [
        f"- {r['league']} {_label(r)} · {_mkt(r)} · {_strategy(r)}："
        f"{r['best_odds']:.2f} → {p['best_odds']:.2f} "
        f"{_clv_note(r['best_odds'], p['best_odds'])}"
        for r, p in moved] or ["- 无"]
    lines.append("")

    lines += _heading("新增候选", len(added))
    lines += [
        f"- {r['league']} {_label(r)} · {_mkt(r)} · {_strategy(r)} "
        f"@ {r['best_odds']:.2f}，EV {r['ev']:+.2%}，"
        f"仓位 {r['kelly_stake_frac']:.2%}"
        for r in added] or ["- 无"]
    lines.append("")

    lines += _heading("已消失", len(gone))
    lines += [
        f"- {r['league']} {_label(r)} · {_mkt(r)} · {_strategy(r)}"
        f"（am @ {r['best_odds']:.2f}）——已消失"
        for r in gone] or ["- 无"]
    lines.append("")

    lines += ["## 未变", "",
              f"- 未变候选 {len(unchanged)} 条，不重发（去重）"]
    lines.append("")

    lines += _risk_block(quota_left, degraded, _run_summary(conn, am_run_id))
    lines.append("")
    lines += _persona_block()
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------- 结算简报


def render_settlement_brief(settle):
    """daily 一行式结算简报（T10 消费，spec §7.3）：settled / won / pnl / clv。"""
    settled = settle.get("settled", 0)
    won = settle.get("won", 0)
    pnl = settle.get("pnl", 0.0)
    clv = settle.get("clv_median")
    clv_s = (f"CLV 中位 {clv:+.2%}" if _is_num(clv) else "CLV 无收盘价基准")
    return f"结算简报：结算 {settled} 注，中 {won}，P&L {pnl:+.2f}，{clv_s}"
