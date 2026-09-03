import sqlite3

import typer

from fa.data.audit import audit_sample
from fa.data.sync import sync_history
from fa.config import load_env
from fa.db import connect, init_db
from fa.pipeline.align import rank_candidates

app = typer.Typer(help="fa — 足球量化分析与投注推荐（设计见 spec.md）")


@app.callback()
def main() -> None:
    """fa — 足球量化分析与投注推荐（设计见 spec.md）"""
    # spec §9.4「ODDS_API_KEY 走 .env」的唯一接线点：CLI 每次启动读 project_root/.env
    # 进 os.environ。setdefault 语义 → shell 已导出的键优先，重复加载幂等。
    # 没有这行，.env 是死配置——hermes cron（§9.6）的裸环境永远拿不到 key，
    # 每日 run 全部 no_key（T12 E2E 实测发现的缺陷）。
    load_env()


@app.command()
def version() -> None:
    """显示版本"""
    typer.echo("fa 0.1.0 (M1)")


@app.command()
def init() -> None:
    """初始化数据库（data/fa.db，可用 FA_DB 环境变量覆盖）"""
    init_db()
    from fa.config import db_path
    typer.echo(f"数据库就绪：{db_path()}")


data_app = typer.Typer(help="数据层")
app.add_typer(data_app, name="data")


@data_app.command("sync-history")
def sync_history_cmd(refresh: bool = typer.Option(
        False, "--refresh", help="忽略本地缓存强制重新下载")) -> None:
    """下载并入库五大联赛历史 CSV（幂等，可重跑）"""
    conn = connect()
    rep = sync_history(conn, refresh=refresh)
    conn.close()
    typer.echo(
        f"完成：下载 {rep.files_ok} 个赛季文件，新入库 {rep.inserted} 场，"
        f"跳过（内容未变）{rep.skipped_seasons} 个赛季，"
        f"无数据 {rep.files_missing} 个")
    if rep.missing:
        for lg, y in rep.missing[:20]:
            typer.echo(f"  无数据：{lg} {y}-{(y + 1) % 100:02d} 赛季")
        if len(rep.missing) > 20:
            typer.echo(f"  ……共 {len(rep.missing)} 项")
    if rep.rows_no_date:
        typer.echo(f"无日期行（已跳过，未入库）：{rep.rows_no_date} 场")
    if rep.file_errors:
        typer.echo(f"出错文件：{len(rep.file_errors)} 个（其余文件不受影响）")
        for lg, y, msg in rep.file_errors[:20]:
            typer.echo(f"  出错：{lg} {y}-{(y + 1) % 100:02d} 赛季：{msg}")
        if len(rep.file_errors) > 20:
            typer.echo(f"  ……共 {len(rep.file_errors)} 项")


@data_app.command("status")
def status() -> None:
    """各联赛入库概况与未知队名数"""
    conn = connect()
    for r in conn.execute(
            "SELECT league, COUNT(DISTINCT season) seasons, COUNT(*) n "
            "FROM matches GROUP BY league ORDER BY league"):
        typer.echo(f"{r['league']}: {r['seasons']} 个赛季，{r['n']} 场")
    unknown = conn.execute(
        "SELECT COUNT(*) c FROM unknown_names").fetchone()["c"]
    typer.echo(f"未知队名（隔离表）：{unknown} 条")
    conn.close()


@data_app.command("audit")
def audit_cmd(sample: int = typer.Option(50, "--sample"),
              seed: int = typer.Option(42, "--seed")) -> None:
    """抽样对账：DB vs 缓存 CSV（spec M1 验收：抽样 50 场一致）"""
    conn = connect()
    mismatches = audit_sample(conn, sample=sample, seed=seed)
    conn.close()
    if not mismatches:
        typer.echo(f"对账通过：抽样 {sample} 场，0 不一致")
        return
    for x in mismatches:
        typer.echo(f"  [不一致] match={x.match_id} field={x.field} "
                   f"db={x.db_value} csv={x.csv_value}")
    typer.echo(f"共 {len(mismatches)} 处不一致（抽样 {sample} 场）")
    raise typer.Exit(code=1)


@data_app.command("aliases")
def aliases_cmd(
    confirm: str = typer.Option("", "--confirm",
                                help='确认别名并移出隔离表：--confirm "TEAM_ID=别名"'),
    source: str = typer.Option("oddsapi", "--source", help="别名 / 隔离表的 source 维度"),
    league: str = typer.Option("", "--league", help="建议只在该联赛内找（空=全部联赛）"),
    top: int = typer.Option(3, "--top", help="每条未知队名给出的建议条数"),
) -> None:
    """未对齐队名清单与建议（spec §3.3），--confirm 人工确认后写入 team_aliases。

    不带 --confirm：列出隔离表（unknown_names）逐条给出 top-N 候选（canonical /
    既有别名 + 相似度），供人工判断；隔离表为空时明说（spec：新增 _unknown_ 条目
    每日可见）。带 --confirm：把 `TEAM_ID=别名` 写入 team_aliases 并把该别名移出
    隔离表（同 source）；别名已有绑定时人工确认覆盖旧绑定并回显。
    """
    conn = connect()
    try:
        if confirm:
            _confirm_alias(conn, confirm, source)
        else:
            _list_unknown(conn, source, league or None, top)
    finally:
        conn.close()


def _confirm_alias(conn, spec: str, source: str) -> None:
    """解析并执行 `TEAM_ID=别名`：写 team_aliases + 移出隔离表（同 source）。"""
    team_id_text, sep, alias = spec.partition("=")
    team_id_text, alias = team_id_text.strip(), alias.strip()
    if not sep or not team_id_text or not alias:
        typer.echo(f'格式错误：应为 "TEAM_ID=别名"，收到 "{spec}"')
        raise typer.Exit(code=1)
    try:
        team_id = int(team_id_text)
    except ValueError:
        typer.echo(f'格式错误：TEAM_ID 须为整数，收到 "{team_id_text}"')
        raise typer.Exit(code=1)
    team = conn.execute(
        "SELECT id, league, name FROM teams WHERE id=?", (team_id,)).fetchone()
    if team is None:
        typer.echo(f"team_id={team_id} 不存在（teams 表无此行）")
        raise typer.Exit(code=1)
    previous = conn.execute(
        "SELECT team_id FROM team_aliases WHERE source=? AND alias=?",
        (source, alias)).fetchone()
    conn.execute(
        "INSERT INTO team_aliases (team_id, source, alias) VALUES (?, ?, ?) "
        "ON CONFLICT(source, alias) DO UPDATE SET team_id=excluded.team_id",
        (team_id, source, alias))
    removed = conn.execute(
        "DELETE FROM unknown_names WHERE source=? AND name=?", (source, alias)).rowcount
    conn.commit()
    note = f"（覆盖旧绑定 team_id={previous['team_id']}）" if previous else ""
    typer.echo(
        f"别名已写入：{alias!r} → team_id={team_id} {team['name']}（{team['league']}）{note}；"
        f"移出隔离表 {removed} 条")


def _list_unknown(conn, source: str, league: str | None, top: int) -> None:
    """逐条列出隔离表条目与 top-N 建议；空隔离表明说（不静默）。"""
    rows = conn.execute(
        "SELECT name, first_seen FROM unknown_names WHERE source=? ORDER BY first_seen, name",
        (source,)).fetchall()
    typer.echo(f"未对齐队名（隔离表，source={source}）：{len(rows)} 条")
    if not rows:
        return
    for r in rows:
        typer.echo(f"  {r['name']!r}（首次出现 {r['first_seen']}）")
        cands = rank_candidates(conn, r["name"], league=league, source=source, top=top)
        if not cands:
            typer.echo("      （无建议——联赛内无相近名，需人工建队或补别名）")
            continue
        for c in cands:
            typer.echo(
                f"      team_id={c.team_id} {c.name}（{c.league}）ratio={c.ratio:.4f}")
    scope = f"league={league}" if league else "全部联赛"
    typer.echo(f"确认方式：fa data aliases --confirm \"TEAM_ID=别名\"（建议范围：{scope}）")


# ---- 线 A：dsh agent 当大脑（spec §12.5）------------------------------------

agentline_app = typer.Typer(help="范式对比线（spec §12.5）——线 A：dsh agent 当大脑")
app.add_typer(agentline_app, name="agentline")


@agentline_app.command("export")
def agentline_export(league: str = typer.Option(...), season: int = typer.Option(...),
                     n: int = typer.Option(30), seed: int = typer.Option(42)) -> None:
    """抽样并导出信息集 JSON（幂等，已存在跳过）"""
    from fa.agentline.export import export_batch, pick_sample
    from fa.config import project_root
    conn = connect()
    ids = pick_sample(conn, league, season, n, seed)
    written = export_batch(conn, ids, project_root() / "data" / "agentline")
    conn.close()
    typer.echo(f"样本 {len(ids)} 场，新导出 {len(written)} 个信息集"
               f"（data/agentline/）")


@agentline_app.command("run")
def agentline_run(line: str = typer.Option(..., help="A_base 或 A_enh"),
                  limit: int = typer.Option(None)) -> None:
    """跑一批线 A 预测（幂等续跑：只补无 ok 行的场次）"""
    from fa.agentline.orchestrate import run_line
    from fa.config import project_root
    if line not in ("A_base", "A_enh"):
        typer.echo(f"line 必须是 A_base 或 A_enh，收到 {line}")
        raise typer.Exit(2)
    conn = connect()
    counts = run_line(conn, line, project_root() / "data" / "agentline", limit)
    conn.close()
    typer.echo(f"line={line} 完成：{counts}")


@agentline_app.command()
def runs() -> None:
    """线 A 运行台账"""
    conn = connect()
    for r in conn.execute(
            "SELECT id, line, n_ok, n_parse_fail, n_timeout, n_error,"
            " started_at, summary FROM agentline_runs"
            " ORDER BY id DESC LIMIT 20"):
        typer.echo(f"#{r['id']} {r['line']} ok={r['n_ok']} "
                   f"parse_fail={r['n_parse_fail']} timeout={r['n_timeout']} "
                   f"error={r['n_error']} @{r['started_at']}")
    conn.close()


@agentline_app.command()
def compare(league: str = typer.Option(None), season: int = typer.Option(None),
            out: str = typer.Option(None)) -> None:
    """三线对比滚动报告（docs/agentline/compare-YYYYMMDD.md）"""
    from datetime import datetime, timezone
    from pathlib import Path

    from fa.agentline.compare import compare_lines, render_report
    from fa.config import project_root
    conn = connect()
    try:
        cmp = compare_lines(conn,
                            [league] if league else None,
                            [season] if season else None)
    except (ValueError, sqlite3.OperationalError) as exc:
        # 空表（evaluate 抛 ValueError）与未迁移（表不存在 → OperationalError）
        # 都是可预期的初装状态，给友好出口而非 traceback（先例：_a_line_summary）。
        conn.close()
        typer.echo(f"对比报告未生成：无预测行——先跑回测，或数据库未迁移"
                   f"（先 fa init）（{exc}）")
        raise typer.Exit(code=1)
    conn.close()
    # 文件名与报告内时间戳统一 UTC：compare-YYYYMMDD 不随本机时区漂移，
    # 报告首行的「生成于 … UTC」才能对得上同一个文件名。
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    p = Path(out) if out else (
        project_root() / "docs" / "agentline" / f"compare-{stamp}.md")
    render_report(cmp, p)
    typer.echo(f"报告已写：{p}")


backtest_app = typer.Typer(help="回测")
app.add_typer(backtest_app, name="backtest")


def _fit_config(half_life: float, sigma: float) -> "FitConfig":
    """回测拟合配置。σ 只透传到队级先验（sigma_att=sigma_dfn）；σ_mu/σ_ha 保持
    默认——联赛级（均值/主场优势）收缩不是 M2 判决诊断出的瓶颈（过度收缩发生在
    队级 att/dfn，判决书「机理诊断」节）。"""
    from fa.model.fit import FitConfig
    return FitConfig(half_life_days=half_life, sigma_att=sigma, sigma_dfn=sigma)


@backtest_app.command("run")
def backtest_run(
    from_season: int = typer.Option(2019, "--from", help="起始赛季（含）"),
    to_season: int = typer.Option(2025, "--to", help="结束赛季（含）"),
    half_life: float = typer.Option(100.0, "--half-life", help="衰减半衰期（天）"),
    sigma: float = typer.Option(0.35, "--sigma", help="队级先验宽度（sigma_att=sigma_dfn）"),
    leagues: str = typer.Option("", "--leagues", help="逗号分隔联赛码，空=全部"),
    no_refit: bool = typer.Option(False, "--no-refit", help="跳过拟合，用表内预测出报告"),
    form: bool = typer.Option(False, "--form",
                              help="启用近 6 场净胜球状态协变量（spec §4.5 消融）"),
) -> None:
    """跑 walk-forward 回测并写 docs/m2-report.md（spec §8）"""
    from datetime import datetime
    from pathlib import Path as _P
    from fa.backtest.report import render_report
    from fa.backtest.metrics import fetch_predictions
    from fa.config import LEAGUES, project_root
    lgs = [s.strip() for s in leagues.split(",") if s.strip()] or list(LEAGUES)
    conn = connect()
    if not no_refit:
        from fa.backtest.run import run_backtest
        cfg = _fit_config(half_life, sigma)
        t0 = datetime.now()
        n = run_backtest(conn, lgs, range(from_season, to_season + 1), cfg,
                         verbose=True, with_form=form)
        typer.echo(f"回测完成：{n} 行预测，耗时 {datetime.now() - t0}")
    rows = fetch_predictions(conn, leagues=lgs,
                             seasons=list(range(from_season, to_season + 1)))
    conn.close()
    if not rows:
        typer.echo("无预测行——请先跑拟合（去掉 --no-refit）")
        raise typer.Exit(code=1)
    ev = render_report(rows, _P(project_root() / "docs" / "m2-report.md"))
    typer.echo(f"判决：{ev['verdict']}（劣化 {ev['degradation_pct']:+.2f}%，"
               f"判据 ≤ +1.00%）——报告见 docs/m2-report.md")


# ---- 双线 status 与 bet 台账（T11）-----------------------------------------
# spec §12.1：一个程序、一个库、两条线并存、结论分账——`fa status` 把两线的证据
# 各占一屏、字样钉死（「A 线·研究评测」「B 线·运营模拟（paper）」），两线数字绝不
# 互相冒充（A 线只读 backtest_predictions，B 线只读 paper 台账 + runs/fixtures 侧
# 的运行水位）。`fa bet` 是台账的人工入口：live 是唯一的真实下单通道，故必须显式
# 确认旗标（§12.2「真实下注依然禁止」下的最小豁免面）。

_RUNS_SHOWN = 3                       # fa status 的「最近 runs」条数（brief 钉死）
_BET_MODES = ("paper", "live")        # db.py bets.mode CHECK 词表
_SETTLE_STATUSES = ("won", "lost", "void")   # db.py bets.status CHECK 的人工可写子集


def _pct(value: float | None) -> str:
    """比率 → 带符号百分比；None（无可算样本）→ 「—」，不冒充 0。"""
    return "—" if value is None else f"{value:+.2f}%"


def _money(value: float | None) -> str:
    """金额两位小数；None → 「—」（区别于真实的 0.00）。"""
    return "—" if value is None else f"{value:.2f}"


def _now_iso() -> str:
    """台账时间戳：UTC ISO-Z，与 paper 层 placed_at / settled_at 同一格式。"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_positive_stake(stake: float, source: str) -> None:
    """注金闸：显式 ``--stake`` 与派生（仓位 × bankroll）走**同一道**闸——

    kelly 为 0 或 M4 的 ``final_stake_frac=0`` 会派生出 0 注金，同样必须拒，
    不得写成 0 注行（0 注金会让 ROI/CLV 的分母虚增、台账失真）。
    """
    if stake > 0:
        return
    typer.echo(f"{source} 须 > 0，收到 {stake}")
    raise typer.Exit(code=1)


def _a_line_summary(conn) -> str:
    """A 线一行：全量 backtest_predictions 聚合 evaluate 的 n / 劣化 / 判决。

    措辞用「backtest 全量聚合」而非「最近 backtest」——实现是**整张表**聚合，
    不是「最近一次 run」（schema 没有 runs↔predictions 关联列，勿误导）。
    """
    from fa.backtest.metrics import evaluate, fetch_predictions
    rows = fetch_predictions(conn)                    # 全量表，不带联赛/赛季过滤
    if not rows:
        return "未运行（backtest_predictions 空表——fa backtest run 生成预测）"
    try:
        ev = evaluate(rows)
    except TypeError:                                 # 残缺行（mkt_* 缺市场收盘价）
        return (f"n={len(rows)} 行预测不可评（mkt_* 缺市场收盘价）——"
                "A 线判据需要去水收盘基准，先补齐回测输入")
    return (f"backtest 全量聚合：n={ev['n']}  模型 log-loss={ev['model_ll']:.4f}"
            f"  市场 log-loss={ev['market_ll']:.4f}"
            f"  劣化={ev['degradation_pct']:+.2f}%  判决={ev['verdict']}"
            f"（判据：劣化 ≤ +1.00%）")


def _b_line_summary(conn) -> list[str]:
    """B 线各行：paper 台账汇总 + 额度水位 + 最近 runs + 隔离队名计数。"""
    from fa.db import get_meta
    from fa.pipeline.fixtures import QUOTA_META_KEY
    from fa.pipeline.paper import BANKROLL_KEY, INITIAL_BANKROLL, paper_summary

    s = paper_summary(conn)
    bankroll = ("未初始化（首次落注时按 "
                f"{INITIAL_BANKROLL:.0f} 写 meta {BANKROLL_KEY}）"
                if s["bankroll"] is None else _money(s["bankroll"]))
    lines = [f"注数={s['n']}（pending {s['pending']}）  "
             f"已结算注金={_money(s['staked'])}  回报={_money(s['returned'])}"
             f"  ROI={_pct(s['roi'])}  bankroll={bankroll}"
             f"  CLV 中位数={_pct(s['clv_median'])}"]

    quota = get_meta(conn, QUOTA_META_KEY)
    if quota is None:
        lines.append(f"额度水位：未记录（meta {QUOTA_META_KEY} 缺——尚未成功拉过实时盘）")
    else:
        lines.append(f"额度水位：余 {quota} 次（meta {QUOTA_META_KEY}）")

    runs = conn.execute(
        "SELECT id, type, phase, started_at, status, credits_after FROM runs"
        " ORDER BY id DESC LIMIT ?", (_RUNS_SHOWN,)).fetchall()
    lines.append(f"最近 runs（最多 {_RUNS_SHOWN} 条）：")
    if not runs:
        lines.append("  （无 run 记录——fa run matchday / daily 生成）")
    for r in runs:
        phase = f"/{r['phase']}" if r["phase"] else ""
        credits = "—" if r["credits_after"] is None else f"{r['credits_after']}"
        lines.append(f"  #{r['id']} {r['type']}{phase}  {r['started_at']}  "
                     f"{r['status']}  额度 {credits}")

    unknown = conn.execute("SELECT COUNT(*) c FROM unknown_names").fetchone()["c"]
    line = f"未对齐队名（隔离表）：{unknown} 条"
    if unknown:
        line += "（fa data aliases 逐条给建议，--confirm 写别名）"
    lines.append(line)
    return lines


@app.command("status")
def status_cmd() -> None:
    """双线总览：A 线研究评测 + B 线运营模拟（paper 台账，spec §12.1）"""
    conn = connect()
    try:
        typer.echo("== A 线·研究评测 ==")
        typer.echo(_a_line_summary(conn))
        typer.echo("")
        typer.echo("== B 线·运营模拟（paper） ==")
        for line in _b_line_summary(conn):
            typer.echo(line)
    finally:
        conn.close()


bet_app = typer.Typer(help="投注台账（paper 模拟 / live 实盘，spec §7.2 / §12.2）")
app.add_typer(bet_app, name="bet")


def _bet_row(conn, bet_id: int) -> sqlite3.Row:
    """bets 行或退出（未知 id 是操作失误，须显式报错而非静默空转）。"""
    row = conn.execute("SELECT * FROM bets WHERE id=?", (bet_id,)).fetchone()
    if row is None:
        typer.echo(f"bet #{bet_id} 不存在（bets 表无此行）")
        raise typer.Exit(code=1)
    return row


def _require_pending(conn, bet_id: int) -> sqlite3.Row:
    """只允许结算 pending 注——台账终态不可被二次改写。"""
    row = _bet_row(conn, bet_id)
    if row["status"] != "pending":
        typer.echo(f"bet #{bet_id} 已结算（status={row['status']}），拒绝重复结算")
        raise typer.Exit(code=1)
    return row


@bet_app.command("add")
def bet_add(
    recommendation_id: int = typer.Argument(..., help="recommendations.id"),
    stake: float | None = typer.Option(
        None, "--stake", help="注金；缺省按仓位分数 × 当前 bankroll"
                              "（final_stake_frac 优先，否则 kelly_stake_frac）"),
    odds: float | None = typer.Option(
        None, "--odds", help="成交赔率；缺省用推荐时最优价 best_odds"),
    mode: str = typer.Option("paper", "--mode", help="paper（默认，模拟盘）/ live（实盘）"),
    i_know_mode_live: bool = typer.Option(
        False, "--i-know-mode-live",
        help="live 显式确认旗标：真实下单必须带上，缺则拒绝（§12.2）"),
) -> None:
    """登记一笔注（写 bets，status=pending）。live 是唯一人工实盘入口，需确认旗标。"""
    if mode not in _BET_MODES:
        typer.echo(f"--mode 须为 {'|'.join(_BET_MODES)}，收到 {mode!r}")
        raise typer.Exit(code=1)
    if mode == "live" and not i_know_mode_live:
        typer.echo("拒绝：真实下单需显式确认——live 是真金。加 --i-know-mode-live 才放行"
                   "（spec §12.2：真实下注一期禁止，此旗标即最小豁免面）")
        raise typer.Exit(code=1)
    if stake is not None:
        _require_positive_stake(stake, "--stake")
    if odds is not None and odds <= 1:
        typer.echo(f"--odds 须 > 1（赔率下限），收到 {odds}")
        raise typer.Exit(code=1)

    from fa.pipeline.paper import BANKROLL_KEY, INITIAL_BANKROLL
    conn = connect()
    try:
        rec = conn.execute("SELECT * FROM recommendations WHERE id=?",
                           (recommendation_id,)).fetchone()
        if rec is None:
            typer.echo(f"recommendation_id={recommendation_id} 不存在"
                       "（recommendations 表无此行）")
            raise typer.Exit(code=1)
        if conn.execute(
                "SELECT 1 FROM bets WHERE recommendation_id=? AND mode=?",
                (recommendation_id, mode)).fetchone() is not None:
            typer.echo(f"拒绝：recommendation_id={recommendation_id} 在 mode={mode}"
                       " 下已有注（UNIQUE(recommendation_id, mode)）——换 mode 或直接结算")
            raise typer.Exit(code=1)

        if stake is None:                     # 缺省仓位：persona 位缺失则退回 kelly
            frac = (rec["final_stake_frac"] if rec["final_stake_frac"] is not None
                    else rec["kelly_stake_frac"])
            raw = conn.execute("SELECT value FROM meta WHERE key=?",
                               (BANKROLL_KEY,)).fetchone()
            bankroll = INITIAL_BANKROLL if raw is None else float(raw["value"])
            stake = round(frac * bankroll, 2)
            _require_positive_stake(stake, f"派生注金（仓位 {frac} × bankroll）")
            typer.echo(f"注金未指定：按仓位 {frac} × bankroll {bankroll:.2f}"
                       f" = {stake:.2f}")
        if odds is None:
            odds = rec["best_odds"]
        try:
            bet_id = conn.execute(
                "INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker,"
                " odds_taken, stake, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (recommendation_id, mode, _now_iso(), rec["bookmaker"], odds,
                 stake, "pending")).lastrowid
            conn.commit()
        except sqlite3.IntegrityError as exc:   # 兜底：上面的预查与写入之间的竞争
            typer.echo(f"拒绝：该推荐在此模式下已有注"
                       f"（UNIQUE(recommendation_id, mode)）：{exc}")
            raise typer.Exit(code=1)
        typer.echo(f"已登记 bet #{bet_id}：mode={mode} status=pending  "
                   f"stake={stake:.2f} @ {odds}（{rec['bookmaker']}，"
                   f"market={rec['market']}）")
    finally:
        conn.close()


@bet_app.command("list")
def bet_list(
    mode: str = typer.Option("", "--mode", help="过滤 paper|live，空=全部"),
    status: str = typer.Option("", "--status", help="过滤 pending|won|lost|void，空=全部"),
) -> None:
    """列出台账注（可按 mode / status 过滤）。"""
    for label, value, allowed in (("--mode", mode, _BET_MODES),
                                  ("--status", status,
                                   ("pending", "won", "lost", "void"))):
        if value and value not in allowed:
            typer.echo(f"{label} 须为 {'|'.join(allowed)}（空=全部），收到 {value!r}")
            raise typer.Exit(code=1)

    sql = ("SELECT b.*, r.market, r.strategy, f.league FROM bets b"
           " JOIN recommendations r ON r.id = b.recommendation_id"
           " LEFT JOIN fixtures f ON f.id = r.fixture_id WHERE 1=1")
    args: list = []
    if mode:
        sql += " AND b.mode=?"
        args.append(mode)
    if status:
        sql += " AND b.status=?"
        args.append(status)
    sql += " ORDER BY b.id"

    conn = connect()
    try:
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()
    scope = f"mode={mode or '全部'}, status={status or '全部'}"
    typer.echo(f"投注台账：{len(rows)} 条（{scope}）")
    if not rows:
        typer.echo("（无记录）")
        return
    for r in rows:
        settled = (f"结算 {r['settled_at']}  回报 {_money(r['return_amt'])}"
                   if r["settled_at"] else "（未结）")
        clv = "—" if r["clv"] is None else f"{r['clv']:+.4f}"
        league = r["league"] if r["league"] else "—"
        typer.echo(f"  #{r['id']} {r['mode']} {r['status']} {r['market']}"
                   f"  stake={_money(r['stake'])} @ {r['odds_taken']}"
                   f"（{r['bookmaker']}）  CLV {clv}  {settled}"
                   f"  下 {r['placed_at']}  {league}/{r['strategy']}")


@bet_app.command("settle")
def bet_settle(
    bet_id: int = typer.Argument(..., help="bets.id"),
    status: str = typer.Option(..., "--status", help="won|lost|void"),
    return_amt: float | None = typer.Option(
        None, "--return", help="回报金额；缺省 won=stake×odds_taken，lost/void=0"),
) -> None:
    """人工结算一笔注（终态不可改写）。

    注意：**不改动 paper bankroll**——余额只由 paper 自动结算路径记账
    （fa run daily → settle_paper_bets）。paper 注被手工结算后即脱离该自动
    路径，其盈亏**不会**进余额，只留在台账数字里；paper 注请优先让日课结算，
    本命令主用场是 live（§7.2：live 归人工登记与结算）。
    """
    if status not in _SETTLE_STATUSES:
        typer.echo(f"--status 须为 {'|'.join(_SETTLE_STATUSES)}，收到 {status!r}")
        raise typer.Exit(code=1)

    conn = connect()
    try:
        row = _require_pending(conn, bet_id)
        if return_amt is None:
            return_amt = (round(row["stake"] * row["odds_taken"], 2)
                          if status == "won" else 0.0)
        conn.execute(
            "UPDATE bets SET status=?, settled_at=?, return_amt=? WHERE id=?",
            (status, _now_iso(), return_amt, bet_id))
        conn.commit()
    finally:
        conn.close()
    typer.echo(f"已结算 bet #{bet_id}：status={status}  回报 {_money(return_amt)}"
               f"（注金 {_money(row['stake'])} @ {row['odds_taken']}）")
    if row["mode"] == "paper":
        typer.echo("注意：paper 注手工结算不改动 bankroll（余额只由 fa run daily"
                   " 的自动结算记账），且该注已脱离自动结算路径。")

# ---- B 线 run 命令（T9/T10）----

run_app = typer.Typer(help="运营 run（比赛日 / 结算日课，spec §9.6 调度）")
app.add_typer(run_app, name="run")

# runs.status 的中文判决位（词表单源在 fa.pipeline.runs）
_STATUS_CN = {
    "ok": "ok（正常）",
    "degraded_ok": "degraded_ok（降级完成）",
    "skipped": "skipped（空跑）",
    "no_key": "no_key（无密钥）",
    "failed": "failed（失败）",
}


@run_app.command("matchday")
def run_matchday_cmd(
    phase: str = typer.Option("am", "--phase",
                              help="am=11:00 全量报告 / pm=17:00 更新版"),
    leagues: str = typer.Option("", "--leagues", help="逗号分隔联赛码，空=全部"),
) -> None:
    """比赛日 run：拉盘 → 推荐 → 落注 → TG 报告（无当日赛事则空跑退出）"""
    from fa.config import LEAGUES
    from fa.pipeline.matchday import run_matchday
    from fa.pipeline.reporting import last_error

    if phase not in ("am", "pm"):
        typer.echo(f"参数错误：--phase 须为 am/pm，收到 {phase!r}")
        raise typer.Exit(code=1)
    lgs = [s.strip() for s in leagues.split(",") if s.strip()] or list(LEAGUES)

    conn = connect()
    try:
        out = run_matchday(conn, phase, lgs)
    finally:
        conn.close()

    typer.echo(f"比赛日 run（{out['phase']}）判决：{_STATUS_CN[out['status']]}")
    if out["status"] == "no_key":
        typer.echo("原因：ODDS_API_KEY 未配置（.env 或环境变量）——未拉盘、未推荐")
    typer.echo(
        f"  行数：fixture 同步 {out['fixtures']} 场（双侧对齐 {out['aligned']}，"
        f"未对齐 {len(out['unknown'])}），推荐 {out['recs']} 条，"
        f"落注 {out['bets']} 注")
    if out["quota_left"] is not None:
        typer.echo(f"  额度：剩余 {out['quota_left']} credits（Odds API）")
    if out["degraded"]:
        if out["status"] == "skipped":
            # 空跑 + 降级只可能是「额度低于水位且当日无赛事」：没拉盘，别写复用快照
            typer.echo("  降级：额度低于水位——本次空跑未拉盘")
        else:
            typer.echo("  降级：非实时盘（复用最近快照）——价格类字段可能滞后")
    if out["sent"] is None:
        typer.echo("  推送：未推送（空跑 / 无密钥）")
    elif out["sent"]:
        typer.echo("  推送：已发 Telegram")
    else:
        typer.echo(f"  推送：失败（{last_error()}）——推荐与落注已落库")


@run_app.command("daily")
def run_daily_cmd() -> None:
    """结算日课：完赛同步 → 结算 → 有结算才发一行简报（spec §3.4 / §7.3）"""
    from fa.pipeline.daily import run_daily
    from fa.pipeline.reporting import last_error

    conn = connect()
    try:
        out = run_daily(conn)
    finally:
        conn.close()

    typer.echo(f"日课判决：{_STATUS_CN[out['status']]}")
    sync = out["sync"]
    if sync is None:
        typer.echo("  完赛同步：失败降级（用库内旧数据结算）")
    else:
        typer.echo(f"  完赛同步：{sync['files_ok']} 个赛季文件，"
                   f"新入库 {sync['inserted']} 场，出错 {sync['file_errors']} 个")
    line = f"  结算 {out['settled']} 注（中 {out['won']}），净额 {out['pnl']:+.2f}"
    line += ("，CLV 中位 "
             f"{out['clv_median']:+.2%}" if out["clv_median"] is not None
             else "，CLV 无收盘价基准")
    typer.echo(line)
    if out["sent"] is None:
        typer.echo("  推送：静默（无可结注）")
    elif out["sent"]:
        typer.echo("  推送：已发 Telegram（结算简报）")
    else:
        typer.echo(f"  推送：失败（{last_error()}）——结算已落库")
