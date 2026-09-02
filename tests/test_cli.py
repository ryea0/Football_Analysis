from pathlib import Path

from typer.testing import CliRunner

from fa.cli import app
from fa.db import connect, init_db

runner = CliRunner()

CSV_A = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\nE0,19/08/1995,Arsenal,West Ham,1,1,D\n"


def test_help_exits_zero():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "fa" in result.output


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def _use_tmp_db(tmp_path, monkeypatch) -> Path:
    """FA_DB 指向临时库，测试绝不碰 data/fa.db。"""
    db = tmp_path / "t.db"
    monkeypatch.setenv("FA_DB", str(db))
    init_db(db)
    return db


def test_data_subapp_help():
    result = runner.invoke(app, ["data", "--help"])
    assert result.exit_code == 0
    assert "sync-history" in result.output
    assert "status" in result.output


def test_data_status(tmp_path, monkeypatch):
    db = _use_tmp_db(tmp_path, monkeypatch)
    conn = connect(db)
    conn.execute("INSERT INTO teams (league, name) VALUES ('E0', 'Arsenal')")
    conn.execute(
        "INSERT INTO matches (league, season, date, home_team_id, away_team_id,"
        " fthg, ftag, raw_line) VALUES ('E0', 1995, '1995-08-19', 1, 1, 1, 1, '{}')")
    conn.commit()
    conn.close()

    result = runner.invoke(app, ["data", "status"])
    assert result.exit_code == 0, result.output
    assert "E0" in result.output and "1 个赛季" in result.output
    assert "未知队名（隔离表）：0 条" in result.output


def test_data_sync_history(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    p = tmp_path / "E0_9495.csv"
    p.write_text(CSV_A)

    def fake_download(league, start_year, refresh=False):
        return p if (league, start_year) == ("E0", 1994) else None

    monkeypatch.setattr("fa.data.sync.download_csv", fake_download)

    result = runner.invoke(app, ["data", "sync-history"])
    assert result.exit_code == 0, result.output
    assert "新入库 1 场" in result.output
    assert "无数据" in result.output

    again = runner.invoke(app, ["data", "sync-history"])
    assert again.exit_code == 0, again.output
    assert "新入库 0 场" in again.output
    assert "跳过（内容未变）1 个赛季" in again.output


def test_data_sync_history_refresh_flag(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    p = tmp_path / "E0_9495.csv"
    p.write_text(CSV_A)
    seen = []

    def fake_download(league, start_year, refresh=False):
        seen.append(refresh)
        return p if (league, start_year) == ("E0", 1994) else None

    monkeypatch.setattr("fa.data.sync.download_csv", fake_download)
    result = runner.invoke(app, ["data", "sync-history", "--refresh"])
    assert result.exit_code == 0, result.output
    assert seen and all(seen)          # --refresh 透传到下载层
