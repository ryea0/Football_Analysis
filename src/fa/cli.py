import typer

app = typer.Typer(help="fa — 足球量化分析与投注推荐（设计见 spec.md）")


@app.callback()
def main() -> None:
    """fa — 足球量化分析与投注推荐（设计见 spec.md）"""


@app.command()
def version() -> None:
    """显示版本"""
    typer.echo("fa 0.1.0 (M1)")
