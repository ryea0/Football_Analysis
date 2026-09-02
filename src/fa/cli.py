import typer

from fa.data.audit import audit_sample
from fa.data.sync import sync_history
from fa.db import connect, init_db

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


backtest_app = typer.Typer(help="回测")
app.add_typer(backtest_app, name="backtest")


@backtest_app.command("run")
def backtest_run(
    from_season: int = typer.Option(2019, "--from", help="起始赛季（含）"),
    to_season: int = typer.Option(2025, "--to", help="结束赛季（含）"),
    half_life: float = typer.Option(100.0, "--half-life", help="衰减半衰期（天）"),
    leagues: str = typer.Option("", "--leagues", help="逗号分隔联赛码，空=全部"),
    no_refit: bool = typer.Option(False, "--no-refit", help="跳过拟合，用表内预测出报告"),
) -> None:
    """跑 walk-forward 回测并写 docs/m2-report.md（spec §8）"""
    from datetime import datetime
    from pathlib import Path as _P
    from fa.backtest.report import render_report
    from fa.backtest.metrics import fetch_predictions
    from fa.config import LEAGUES, project_root
    from fa.model.fit import FitConfig
    lgs = [s.strip() for s in leagues.split(",") if s.strip()] or list(LEAGUES)
    conn = connect()
    if not no_refit:
        from fa.backtest.run import run_backtest
        cfg = FitConfig(half_life_days=half_life)
        t0 = datetime.now()
        n = run_backtest(conn, lgs, range(from_season, to_season + 1), cfg,
                         verbose=True)
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
