"""B 线报告渲染（spec §7.1）——纯函数：只读 DB，无网络、无子进程。

与并行落地的管线（T5 fixtures / T6 推荐生成 / T9 matchday）解耦：这里只消费表
（recommendations join fixtures join teams、runs、meta、bets）和入参字典。唯一
的跨模块依赖是一个**纯常量**——T7 paper 的 :func:`bankroll_key`（D2 分轨键名），
不带任何 IO / 子进程进来。TG 正文口径 :data:`_TG_STRATEGIES` 是**本地字面量**、
刻意不派生自 value.STRATEGIES 单源：单源扩轨（M6 的 nokb 对照轨）自动进
``fa status`` / weekly / dashboard，但**不**自动进 TG 正文（设计档 R1）。

persona 段（§7.1 第 2 条，M4 T14 真渲染）：model_persona 轨判决按**场次**成行
（✅ agree / ⚠️ downweight（delta）/ ⛔ veto + key_factors 缩进逐条 + report_md
截 200 字引用行），§6.5 降级场给 ⚪ 行并标原因，段尾给汇总行。

CLV 预览方向与 spec §7.3 一致：odds_taken / 收盘价 − 1。pm 视角下 am 价是被
咬住的 odds_taken、pm 价是更接近收盘的新价，故价「缩水」给正 CLV、价「走低」
给负 CLV。
"""
import json
from collections import Counter
from datetime import datetime, timedelta, timezone

from fa.db import get_meta
from fa.model.fit import FitConfig
from fa.pipeline.paper import bankroll_key

# 半衰期缺省取 FitConfig 单源（M2 模型层），报告不再自造常量
_DEFAULT_HALF_LIFE = FitConfig.half_life_days

_MARKET = {"H": "主胜", "D": "平局", "A": "客胜", "O2.5": "大2.5"}

# §6.6 A/B 双轨：recommendations 的 UNIQUE(fixture_id, market, strategy, phase)
# 允许两套 strategy 对同一 (fixture, market) 并存——键与展示都必须区分，
# 否则 M4 上线后 am 全量报告丢行、pm diff 跨轨配价。
_STRATEGY = {"model_only": "纯模型", "model_persona": "模型+persona",
             "model_persona_nokb": "模型+persona·无知识库"}
_PERSONA_STRATEGY = "model_persona"     # persona 判决只落此轨（§6.6 的 B 侧）

# M6（§12.7）：nokb 是 C 线测量对照轨，不进 TG 推送正文（R1）——TG 保持
# §6.6 双轨口径；`fa status` / weekly / dashboard 走 STRATEGIES 单源自动三轨。
_TG_STRATEGIES = ("model_only", "model_persona")

# §6.4 判决词表 → 图标（§7.1 第 2 条）；词表外值不崩，给 ❔ 并不进汇总计数
_VERDICT_ICON = {"agree": "✅", "downweight": "⚠️", "veto": "⛔"}
_VERDICTS = tuple(_VERDICT_ICON)

# §6.5 降级原因 → 中文标注（runs.summary.persona.degraded[].reason）；
# 词表外原因原样透出——降级必须可见，不许静默吞成空串
_REASON_CN = {"timeout": "超时", "exit": "hermes 异常退出",
              "extract": "输出无法解析为 JSON", "contract": "输出不合规",
              "persona_file": "人格文件缺失", "unknown": "未知"}

# 价格比较容差：best_odds 经 JSON/浮点往返，1e-9 级差异视为未变，不产假「盘口移动」
_ODDS_EPS = 1e-9

# §7.1 第 1 条本就是「各联赛候选场次」——按联赛分块是回正（用户反馈：同一联赛
# 应放到一起）。五大联赛给中文名，块序固定；未知代码回退原文、字母序殿后。
_LEAGUE_CN = {"E0": "英超", "SP1": "西甲", "D1": "德甲", "I1": "意甲", "F1": "法甲"}
_LEAGUE_ORDER = ("E0", "SP1", "D1", "I1", "F1")

# 开赛时间统一换算北京时间：+8 固定偏移，不读运行机器的系统时区（跑在哪都对）。
_CN_TZ = timezone(timedelta(hours=8))
_WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# run summary 的样本量键名已由 T9 钉死为 train_n（summary 同时落 half_life）。
# 缺失时给中性占位「样本量：未提供」，报告骨架不缺行。
_TRAIN_KEYS = ("train_n",)

# report_md（契约 ≤500 字，§6.3）在报告里截断引用的长度
_REPORT_MD_CLIP = 200

_REC_SQL = """
SELECT r.id, r.fixture_id, r.market, r.strategy, r.model_p, r.market_p,
       r.best_odds, r.bookmaker, r.ev, r.kelly_stake_frac, r.verdict,
       f.league, f.kickoff_utc, f.event_key,
       th.name AS home, ta.name AS away
FROM recommendations r
JOIN fixtures f ON f.id = r.fixture_id
LEFT JOIN teams th ON th.id = f.home_team_id
LEFT JOIN teams ta ON ta.id = f.away_team_id
WHERE r.run_id = ? AND r.strategy IN ('model_only', 'model_persona')
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
    """未结注计数——TG 双轨口径（§12.7）：bets 经 recommendations join 过滤到
    ``_TG_STRATEGIES``（nokb 对照轨不进 TG 正文，也不进这个计数；M6 终审 F5）。"""
    placeholders = ",".join("?" * len(_TG_STRATEGIES))
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM bets b JOIN recommendations r"
        f" ON r.id = b.recommendation_id WHERE b.status='pending'"
        f" AND r.strategy IN ({placeholders})", list(_TG_STRATEGIES)).fetchone()
    return row["n"]


def _bankroll_lines(conn):
    """bankroll 快照（§7.3 D2 分轨）：两轨各一行，键名随行回显可对账。

    TG 双轨口径（nokb 不进正文，§12.7）——遍历 :data:`_TG_STRATEGIES` 而非
    value.STRATEGIES 单源。未初始化 / meta 值非数字（脏数据）都给「未初始化」
    ——余额缺失不冒充 0，脏值也不原样透出。
    """
    out = []
    for strategy in _TG_STRATEGIES:
        raw = get_meta(conn, bankroll_key(strategy))
        try:
            value = "未初始化" if raw is None else f"{float(raw):.2f}"
        except (TypeError, ValueError):
            value = "未初始化"
        out.append(f"- {strategy}：{value}（meta `{bankroll_key(strategy)}`）")
    return out


def _factors(raw):
    """key_factors JSON 串 → 列表；NULL / 非 JSON / 非数组一律回退空列表
    （脏数据不崩渲染，也不把坏串透给读者）。"""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _persona_degraded(summary):
    """runs.summary.persona.degraded（T13：完整列表）→ 安全取用。"""
    persona = (summary or {}).get("persona")
    degraded = persona.get("degraded") if isinstance(persona, dict) else None
    return degraded if isinstance(degraded, list) else []


def _persona_block(conn, run_id, summary):
    """§7.1 第 2 条 persona 段：判决按场次成行 + §6.5 降级行 + 段尾汇总行。

    判决行按 ``GROUP BY r.fixture_id`` 取每场一行（apply 把判决广播到该场全部
    行，同场判决必然一致；SQLite bare-column 语义下取该组某行，无碍）。降级
    信息不查库——它只存在于 runs.summary（``summary``），am 由调用方传入本
    相位的 summary，pm 由 :func:`render_pm_update` 内部读 pm run 的。
    """
    rows = conn.execute(
        "SELECT r.fixture_id, r.verdict, r.confidence_delta, r.key_factors,"
        " r.report_md, f.event_key, th.name AS home, ta.name AS away"
        " FROM recommendations r"
        " JOIN fixtures f ON f.id=r.fixture_id"
        " LEFT JOIN teams th ON th.id=f.home_team_id"
        " LEFT JOIN teams ta ON ta.id=f.away_team_id"
        " WHERE r.run_id=? AND r.strategy=? AND r.verdict IS NOT NULL"
        " GROUP BY r.fixture_id ORDER BY f.kickoff_utc, r.fixture_id",
        (run_id, _PERSONA_STRATEGY)).fetchall()
    degraded = _persona_degraded(summary)
    counts = dict.fromkeys(_VERDICTS, 0)
    lines = ["## persona", ""]
    for row in rows:
        verdict = row["verdict"]
        if verdict in counts:
            counts[verdict] += 1
        delta = ""
        if verdict == "downweight" and _is_num(row["confidence_delta"]):
            delta = f"（{row['confidence_delta']:+.2f}）"
        lines.append(f"- {_VERDICT_ICON.get(verdict, '❔')} {_label(dict(row))} "
                     f"{verdict}{delta}")
        for factor in _factors(row["key_factors"]):
            lines.append(f"  - {factor}")
        if row["report_md"]:
            lines.append(f"  > {row['report_md'][:_REPORT_MD_CLIP]}")
    for item in degraded:                            # 降级行（§6.5 必须可见）
        fid = item.get("fixture_id")
        reason = item.get("reason", "unknown")
        row = conn.execute(
            "SELECT f.event_key, th.name AS home, ta.name AS away FROM fixtures f"
            " LEFT JOIN teams th ON th.id=f.home_team_id"
            " LEFT JOIN teams ta ON ta.id=f.away_team_id WHERE f.id=?",
            (fid,)).fetchone()
        label = (_label(dict(row)) if row is not None
                 else f"未知场次（fixture_id={fid}）")
        lines.append(f"- ⚪ {label}：persona 未生效"
                     f"（{_REASON_CN.get(reason, reason)}）")
    total = len(rows) + len(degraded)
    if total:
        lines.insert(2, f"persona {total} 场：✅{counts['agree']}"
                     f" ⚠️{counts['downweight']} ⛔{counts['veto']}"
                     f" · 未生效 {len(degraded)}")
    else:
        lines.append("- 本相位无 persona 判决")
    return lines


def _verdict_icons(recs):
    """该 run 的 model_persona 判决 → ``{fixture_id: 图标}``（pm 新增候选行尾
    标识用；model_only 轨 / 未判行不进映射）。"""
    return {r["fixture_id"]: _VERDICT_ICON[r["verdict"]]
            for r in recs
            if r["strategy"] == _PERSONA_STRATEGY and r["verdict"] in _VERDICT_ICON}


def _persona_note(rec, icons):
    """新增候选行尾的判决标识——**只标 model_persona 行**。

    图标是处理效应标记：persona 只对 model_persona 轨下调仓位/否决，model_only
    是 persona 盲视的 A/B 对照轨（§6.6/§12.3），把标记挂到对照行上既悬空（该轨
    仓位实际未动）又污染分账归因的可读性。未判 → 空串（不渲染悬空标点）。
    """
    icon = icons.get(rec["fixture_id"])
    return (f"，persona {icon}"
            if icon and rec["strategy"] == _PERSONA_STRATEGY else "")


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _sample_line(summary):
    for k in _TRAIN_KEYS:
        v = summary.get(k)
        if _is_num(v):
            return f"- 样本量：训练样本 {int(v)} 场"
    return "- 样本量：未提供（run summary 未带训练样本数）"


def _degraded_s(degraded, state):
    """降级三态各说各话：文案必须与「本窗到底有没有实时盘」一致。

    - 快照复用（本窗没拉盘 / 拉盘失败）→ 价格确实滞后
    - region 合并（本窗拉了实时盘，只是按额度收窄到 eu）→ **不是**滞后，是 uk
      侧最优价缺失——绝不能套「复用既有快照」的句子
    - 都不是却 degraded=True → 不猜原因，指向落库的理由串
    状态读 runs.summary 的机器可读键（matchday 落库），不解析中文理由串。
    """
    if state.get("snapshot_reused"):
        return "是——本窗复用既有快照，价格类字段可能滞后"
    if state.get("region_merged"):
        return "是——本窗为实时盘，但已按额度收窄到单 eu（uk 侧最优价缺失）"
    if degraded:
        return "是（原因见 runs.summary 的 degraded_reasons）"
    return "否"


def _risk_block(quota_left, degraded, summary, state=None):
    """风险提示块。``state`` 是**本窗** run 的 summary：pm_update 的样本量/半衰期
    沿用 am 落库值（``summary``），但降级三态描述的是 pm 自己的拉取形态。"""
    state = summary if state is None else state
    half = summary.get("half_life")
    half_s = (f"{float(half):.0f}" if _is_num(half)
              else f"{_DEFAULT_HALF_LIFE:.0f}")
    quota_s = "未知（本次未拉到额度头）" if quota_left is None else str(quota_left)
    return ["## 风险提示", "",
            _sample_line(summary),
            f"- 半衰期：{half_s} 天（时间衰减窗口，越旧权重越低）",
            f"- 降级：{_degraded_s(degraded, state or {})}",
            f"- 额度水位：剩余 {quota_s}（Odds API credits）"]


def _mkt(rec):
    return _MARKET.get(rec["market"], rec["market"])


def _strategy(rec):
    return _STRATEGY.get(rec["strategy"], rec["strategy"])


def _kickoff_cn(iso, *, ref=None):
    """开赛时刻（北京时间）：当日 → ``周X HH:MM``，跨日 → ``MM-DD 周X HH:MM``。

    ``fixtures.kickoff_utc`` 落的是 Odds API 原文（ISO-8601 UTC，``Z`` 结尾）；
    换算用 +8 固定偏移。带不带日期以**北京日历日**为准——不是当天的都补 MM-DD，
    否则次日凌晨（欧洲晚间场）会被误读成当天。``ref`` 仅供测试注入「当前时刻」，
    缺省取真实的北京时间现在。NULL / 空串（Odds API 无 commence_time）/ 不可解析
    → ``—``，脏数据不得崩掉整张表。
    """
    if not iso:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(iso).strip().replace("Z", "+00:00"))
    except ValueError:
        return "—"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)   # 裸时间按 UTC 读（同 value 层）
    loc = parsed.astimezone(_CN_TZ)
    now = (ref if ref is not None else datetime.now(timezone.utc)).astimezone(_CN_TZ)
    stamp = f"{_WEEKDAY_CN[loc.weekday()]} {loc:%H:%M}"
    return stamp if loc.date() == now.date() else f"{loc:%m-%d} {stamp}"


def _league_cn(code):
    """五大联赛给中文名，其余原样回退（未知代码不失真、也不渲染 None）。"""
    return _LEAGUE_CN.get(code) or code or "未知联赛"


def _league_sort_key(code):
    """块序：五大联赛按固定序，未知代码按字母序殿后（不挤进五大之间）。"""
    if code in _LEAGUE_ORDER:
        return (0, _LEAGUE_ORDER.index(code))
    return (1, str(code or ""))


def _league_blocks(items, league_of=lambda r: r["league"],
                   ev_of=lambda r: r["ev"]):
    """[(联赛代码, [item…])]：块间按联赛固定序，块内按 EV 降序（高置信先看）。

    ``sorted`` 稳定——EV 打平时保持入参序（am 为 SQL 的 kickoff 序，pm 为 diff
    配对序），分组本身不额外打乱行。
    """
    buckets: dict = {}
    for it in items:
        buckets.setdefault(league_of(it), []).append(it)
    return [(code, sorted(group, key=lambda it: -float(ev_of(it))))
            for code, group in sorted(buckets.items(),
                                      key=lambda kv: _league_sort_key(kv[0]))]


def _league_header(code, n):
    return f"### {_league_cn(code)}（{n}）"


def _kickoff_cell(rec):
    return f"· 开赛 {_kickoff_cn(rec['kickoff_utc'])}"


def _candidate_table(recs):
    """§7.1 第 1 条：市场/赔率/模型 p vs 市场 p/EV/仓位（+ 策略列，A/B 双轨）
    + 开赛（北京时间，紧跟场次）。"""
    header = ("| 联赛 | 场次 | 开赛 | 市场 | 策略 | 最优价 | 模型 p | 市场 p "
              "| EV | 仓位 | 博彩商 |")
    sep = "|---|---|---|---|---|---|---|---|---|---|---|"
    rows = [
        f"| {r['league']} | {_label(r)} | {_kickoff_cn(r['kickoff_utc'])} "
        f"| {_mkt(r)} | {_strategy(r)} "
        f"| {r['best_odds']:.2f} | {r['model_p']:.3f} | {r['market_p']:.3f} "
        f"| {r['ev']:+.2%} | {r['kelly_stake_frac']:.2%} | {r['bookmaker']} |"
        for r in recs]
    return [header, sep] + rows


def _candidate_section(recs):
    """候选场次按联赛分块：每块 `### 中文名（n）` + 完整子表。

    每块都带表头——Telegram 里块与块隔得远，单独截看一块也对得上列。
    """
    if not recs:
        return ["- 无候选（门槛未过或无赛程）"]
    lines = []
    for i, (code, group) in enumerate(_league_blocks(recs)):
        if i:
            lines.append("")
        lines += [_league_header(code, len(group)), ""]
        lines += _candidate_table(group)
    return lines


def _grouped_lines(items, league_of, fmt, ev_of=lambda r: r["ev"]):
    """pm diff 段的联赛分块：块间空行，空列表 → 显式「- 无」不塌段。"""
    if not items:
        return ["- 无"]
    lines = []
    for i, (code, group) in enumerate(_league_blocks(items, league_of, ev_of)):
        if i:
            lines.append("")
        lines += [_league_header(code, len(group)), ""]
        lines += [fmt(it) for it in group]
    return lines


def _pm_league(item):
    return item[0]["league"]      # 盘口移动的元素是 (am_rec, pm_rec)


def _pm_ev(item):
    return item[0]["ev"]


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
    """比赛日完整报告（spec §7.1 第 1-4 条，persona 段真渲染）。"""
    recs = _recs(conn, run_id)
    phase_cn = "上午（am 全量）" if phase == "am" else "下午（pm）"
    lines = [f"# 比赛日报告 — {phase_cn}", ""]

    lines += _heading("候选场次", len(recs))
    lines += _candidate_section(recs)
    lines.append("")

    lines += _risk_block(quota_left, degraded, summary or {})
    lines.append("")

    lines += ["## bankroll", ""]
    lines += _bankroll_lines(conn)
    lines.append(f"- 未结注：{_pending_count(conn)} 注（paper，status=pending）")
    lines.append("")

    lines += _persona_block(conn, run_id, summary or {})
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------- pm 更新版（diff）


def render_pm_update(conn, am_run_id, pm_run_id, quota_left, degraded):
    """17:00 更新版（spec §7.1 第 5 条 / §9.6）：只推与 11:00 的差异。

    - 盘口移动：am 已推候选在 pm 有对应行且价格变化（CLV 预览 = am价/pm价 − 1）
    - 已消失：am 已推但 pm 无对应行（pm 未过门槛 / 报价撤除）
    - 新增候选：pm 才出现的候选
    未变候选只计数，不成行——去重不重发全量。
    样本量/半衰期沿用 am run 落库 summary（pm 不重拟合）；降级三态取 **pm run**
    自己的 summary——本窗的拉取形态（双区 / 单 eu / 复用快照）以 pm 为准，am 的
    降级不该污染 pm 的风险行。
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
    verdict_icon = _verdict_icons(pm)      # pm 补判的新增场次才带判决标识

    lines = ["# 比赛日更新（pm）", ""]

    lines += _heading("盘口移动", len(moved))
    lines += _grouped_lines(
        moved, _pm_league,
        lambda rp: (f"- {_label(rp[0])} {_kickoff_cell(rp[0])} · {_mkt(rp[0])} "
                    f"· {_strategy(rp[0])}：{rp[0]['best_odds']:.2f} → "
                    f"{rp[1]['best_odds']:.2f} "
                    f"{_clv_note(rp[0]['best_odds'], rp[1]['best_odds'])}"),
        ev_of=_pm_ev)
    lines.append("")

    lines += _heading("新增候选", len(added))
    lines += _grouped_lines(
        added, lambda r: r["league"],
        lambda r: (f"- {_label(r)} {_kickoff_cell(r)} · {_mkt(r)} "
                   f"· {_strategy(r)} @ {r['best_odds']:.2f}，EV {r['ev']:+.2%}，"
                   f"仓位 {r['kelly_stake_frac']:.2%}"
                   f"{_persona_note(r, verdict_icon)}"))
    lines.append("")

    lines += _heading("已消失", len(gone))
    lines += _grouped_lines(
        gone, lambda r: r["league"],
        lambda r: (f"- {_label(r)} {_kickoff_cell(r)} · {_mkt(r)} "
                   f"· {_strategy(r)}（am @ {r['best_odds']:.2f}）——已消失"))
    lines.append("")

    lines += ["## 未变", "",
              f"- 未变候选 {len(unchanged)} 条，不重发（去重）"]
    lines.append("")

    lines += _risk_block(quota_left, degraded, _run_summary(conn, am_run_id),
                         _run_summary(conn, pm_run_id))
    lines.append("")
    # persona 段看 pm 相位自身：判决行是 am 广播/本窗补判后的库内行，降级信息
    # 取 pm run 落库的 runs.summary（§6.2「pm 不重跑、仅新增场次补跑」）。
    lines += _persona_block(conn, pm_run_id, _run_summary(conn, pm_run_id))
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


def render_retro_brief(rows, batch):
    """daily 简报的复盘归因段落（Stage 2，设计 §3「日报摘要段落」）。

    rows 为空返回空串——调用方据此不附加（昨日无可归因场次不是事件）。
    digest 截 40 字：段落是摘要不是全文，全文在 retro_attributions。
    """
    if not rows:
        return ""
    lines = [f"复盘归因（paper T+1，批 #{batch['batch_id']}）："
             f"昨日推荐 {batch['n_selected']} 场，归因 ok {batch['n_ok']} 场"]
    for r in rows:
        digest = (r["digest"] or "")[:40]
        lines.append(f"  {r['date']} {r['league']} {r['primary_tag']}"
                     f"（conf {r['tags_confidence']:.2f}）：{digest}")
    parts = [f"{t}×{n}" for t, n in sorted(
        Counter(r["primary_tag"] for r in rows).items())]
    lines.append("  标签分布：" + "，".join(parts))
    return "\n".join(lines)
