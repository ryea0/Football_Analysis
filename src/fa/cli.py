import typer

from fa.data.audit import audit_sample
from fa.data.sync import sync_history
from fa.db import connect, init_db
from fa.pipeline.align import rank_candidates

app = typer.Typer(help="fa — 足球量化分析与投注推荐（设计见 spec.md）")


@app.callback()
def main() -> None:
    """fa — 足球量化分析与投注推荐（设计见 spec.md）"""


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
