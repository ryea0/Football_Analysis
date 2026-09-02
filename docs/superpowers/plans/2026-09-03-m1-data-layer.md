# M1 数据层 + 回测基建 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 五大联赛 1993 至今历史数据全量入库（幂等、可对账），并提供 walk-forward 取数框架——spec M1 的全部验收标准。

**Architecture:** 标准分层 `config → db → download → parse → teams → ingest → sync → walkforward → audit`，全部面向 SQLite 单库（WAL）。入库策略为「联赛-赛季分区替换 + 内容哈希跳过」，天然幂等且容忍上游修正赛果。单元测试全部离线（小 CSV 夹具 + monkeypatch 网络），仅 Task 10 E2E 触网。

**Tech Stack:** Python ≥3.11、uv、pandas（CSV 解析）、typer（CLI）、pytest、stdlib `sqlite3` + `urllib.request`。scipy 本里程碑不直接使用，但按 spec §9.1 一次性入依赖。

**Spec:** `spec.md`（v0.4）——本计划实现其 §3（数据层）、§9.2/9.3（目录与 CLI）、§10 M1 行。执行者应同时读 spec。

## Global Constraints

- Python ≥3.11；依赖仅限 `pandas / scipy / typer / pytest` + stdlib；新增任何依赖需用户批准
- 网络访问只允许出现在 `fa/data/download.py`；单元测试必须离线通过（monkeypatch 掉网络）
- DB schema 以 Task 2 为准，后续任务不得私自加列改列
- 队名 canonical 拼写 = football-data.co.uk 原文（spec §3.2）；跨源别名走 `team_aliases`，未知队名进 `unknown_names`，绝不静默丢弃
- CSV 文件编码按 `latin-1` 读（football-data.co.uk 含 é ü 等字符）
- `data/` 与 `.env` 不入库（.gitignore 已覆盖）
- 提交信息用 conventional commits（`feat:` / `test:` / `chore:`），每任务恰好一次提交；CLI 面向用户的输出用中文
- 所有测试命令一律 `uv run pytest ...`，CLI 一律 `uv run fa ...`

---

### Task 1: 项目脚手架与 CLI 骨架

**Files:**
- Create: `pyproject.toml`
- Create: `src/fa/__init__.py`（空）
- Create: `src/fa/cli.py`
- Create: `tests/__init__.py`（空）
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: 无
- Produces: `fa.cli:app`（typer 应用对象，后续任务向其挂命令）；`src/fa/` 可安装包结构

- [ ] **Step 1: 写 pyproject.toml**

```toml
[project]
name = "fa"
version = "0.1.0"
description = "fa — 足球量化分析与投注推荐（spec.md）"
requires-python = ">=3.11"
dependencies = ["pandas>=2.0", "scipy>=1.11", "typer>=0.12"]

[project.scripts]
fa = "fa.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/fa"]

[dependency-groups]
dev = ["pytest>=8.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: 写 CLI 骨架**

`src/fa/cli.py`：

```python
import typer

app = typer.Typer(help="fa — 足球量化分析与投注推荐（设计见 spec.md）")


@app.command()
def version() -> None:
    """显示版本"""
    typer.echo("fa 0.1.0 (M1)")
```

- [ ] **Step 3: 写失败测试**

`tests/test_cli.py`：

```python
from typer.testing import CliRunner

from fa.cli import app

runner = CliRunner()


def test_help_exits_zero():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "fa" in result.output


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output
```

- [ ] **Step 4: 安装并跑测试**

Run: `uv sync && uv run pytest tests/test_cli.py -v`
Expected: 2 PASS（若 FAIL 检查 `[tool.hatch.build.targets.wheel] packages` 路径）

- [ ] **Step 5: 验证入口**

Run: `uv run fa --help`
Expected: 正常输出帮助，退出码 0

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/ tests/ uv.lock
git commit -m "chore: 项目脚手架（uv + typer CLI 骨架）"
```

---

### Task 2: 数据库 schema 与 `fa init`

**Files:**
- Create: `src/fa/config.py`
- Create: `src/fa/db.py`
- Modify: `src/fa/cli.py`（挂 `init` 命令）
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `fa.config`：`LEAGUES: dict[str,str]`、`SEASONS_FROM: int = 1993`、`season_code(start_year:int)->str`、`csv_url(league:str, start_year:int)->str`、`db_path()->Path`（读环境变量 `FA_DB`）、`csv_cache_dir()->Path`
  - `fa.db`：`connect(path:Path|None=None)->sqlite3.Connection`（Row factory、WAL、foreign_keys ON）、`init_db(path:Path|None=None)->None`、`get_meta(conn,key:str)->str|None`、`set_meta(conn,key:str,value:str)->None`
  - 表：`teams / team_aliases / unknown_names / matches / meta / schema_version`（M1 全量 schema，一次到位）

- [ ] **Step 1: 写 config.py**

```python
import os
from pathlib import Path

LEAGUES: dict[str, str] = {
    "E0": "Premier League",
    "SP1": "La Liga",
    "D1": "Bundesliga",
    "I1": "Serie A",
    "F1": "Ligue 1",
}
SEASONS_FROM = 1993
BASE_URL = "https://www.football-data.co.uk/mmz4281"


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def db_path() -> Path:
    return Path(os.environ.get("FA_DB", project_root() / "data" / "fa.db"))


def csv_cache_dir() -> Path:
    return project_root() / "data" / "csv"


def season_code(start_year: int) -> str:
    """2025 -> '2526'；1999 -> '9900'"""
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def csv_url(league: str, start_year: int) -> str:
    return f"{BASE_URL}/{season_code(start_year)}/{league}.csv"
```

- [ ] **Step 2: 写失败测试**

`tests/test_db.py`：

```python
import pytest

from fa.db import connect, get_meta, init_db, set_meta


@pytest.fixture
def conn(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


def test_init_creates_tables(conn):
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"teams", "team_aliases", "unknown_names", "matches", "meta"} <= names


def test_init_is_idempotent(tmp_path):
    init_db(tmp_path / "t.db")
    init_db(tmp_path / "t.db")  # 不抛异常即通过


def test_wal_mode(conn):
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_meta_roundtrip(conn):
    assert get_meta(conn, "k") is None
    set_meta(conn, "k", "v1")
    assert get_meta(conn, "k") == "v1"
    set_meta(conn, "k", "v2")
    assert get_meta(conn, "k") == "v2"
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'fa.db'`

- [ ] **Step 4: 写 db.py**

注意：football-data.co.uk 的 `PS*` 是赛前采集快照（无真开盘价）、`PSC*` 是收盘价——**回测基准用 `psc_*`**（spec §3.1）。

```python
import sqlite3
from pathlib import Path

from fa.config import db_path

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS teams (
    id     INTEGER PRIMARY KEY,
    league TEXT NOT NULL,
    name   TEXT NOT NULL,
    UNIQUE (league, name)
);

CREATE TABLE IF NOT EXISTS team_aliases (
    team_id INTEGER NOT NULL REFERENCES teams(id),
    source  TEXT NOT NULL,
    alias   TEXT NOT NULL,
    UNIQUE (source, alias)
);

CREATE TABLE IF NOT EXISTS unknown_names (
    source     TEXT NOT NULL,
    name       TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    PRIMARY KEY (source, name)
);

CREATE TABLE IF NOT EXISTS matches (
    id            INTEGER PRIMARY KEY,
    league        TEXT NOT NULL,
    season        INTEGER NOT NULL,          -- 起始年：2025 = 2025-26 赛季
    date          TEXT NOT NULL,            -- ISO YYYY-MM-DD
    home_team_id  INTEGER NOT NULL REFERENCES teams(id),
    away_team_id  INTEGER NOT NULL REFERENCES teams(id),
    fthg INTEGER, ftag INTEGER,
    shots_home INTEGER, shots_away INTEGER,
    shots_target_home INTEGER, shots_target_away INTEGER,
    corners_home INTEGER, corners_away INTEGER,
    ps_home REAL, ps_draw REAL, ps_away REAL,      -- Pinnacle 赛前快照
    psc_home REAL, psc_draw REAL, psc_away REAL,   -- Pinnacle 收盘（回测基准）
    over25_ps REAL, under25_ps REAL,
    over25_psc REAL, under25_psc REAL,
    raw_line TEXT NOT NULL,                         -- 原始 CSV 行 JSON 留档
    UNIQUE (league, season, date, home_team_id, away_team_id)
);
CREATE INDEX IF NOT EXISTS idx_matches_league_date ON matches (league, date);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(path: Path | None = None) -> None:
    p = Path(path) if path else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(p)
    conn.executescript(_SCHEMA)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    elif row["version"] != SCHEMA_VERSION:
        raise RuntimeError(
            f"schema 版本不匹配：库={row['version']}，程序={SCHEMA_VERSION}")
    conn.commit()
    conn.close()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return None if row is None else row["value"]


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
```

- [ ] **Step 5: 挂 `fa init` 命令**

`src/fa/cli.py` 顶部 import 区加 `from fa.db import init_db`，文件追加：

```python
@app.command()
def init() -> None:
    """初始化数据库（data/fa.db，可用 FA_DB 环境变量覆盖）"""
    init_db()
    from fa.config import db_path
    typer.echo(f"数据库就绪：{db_path()}")
```

（命令内惰性取 `db_path()`，保证测试改环境变量即时生效。）

- [ ] **Step 6: 跑测试**

Run: `uv run pytest tests/test_db.py -v`
Expected: 4 PASS

- [ ] **Step 7: Commit**

```bash
git add src/fa/config.py src/fa/db.py src/fa/cli.py tests/test_db.py
git commit -m "feat: SQLite schema（teams/aliases/matches/meta）与 fa init"
```

---

### Task 3: CSV 下载与本地缓存

**Files:**
- Create: `src/fa/data/__init__.py`（空）
- Create: `src/fa/data/download.py`
- Create: `tests/data/__init__.py`（空）
- Test: `tests/data/test_download.py`

**Interfaces:**
- Consumes: `fa.config.csv_url / season_code / csv_cache_dir`
- Produces: `download_csv(league:str, start_year:int, refresh:bool=False) -> Path | None`（命中缓存返回路径；404 返回 `None` 表示该赛季无数据；其他 HTTP 错误上抛）；`csv_cache_path(league:str, start_year:int) -> Path`

- [ ] **Step 1: 写失败测试**

`tests/data/test_download.py`：

```python
import urllib.error

from fa.config import csv_url, season_code
from fa.data.download import csv_cache_path, download_csv


def test_url_construction():
    assert csv_url("E0", 2025) == "https://www.football-data.co.uk/mmz4281/2526/E0.csv"
    assert season_code(1999) == "9900"
    assert season_code(2025) == "2526"


def test_cache_path_format():
    p = csv_cache_path("D1", 2024)
    assert p.name == "D1_2425.csv"


def test_cached_file_no_network(tmp_path, monkeypatch):
    p = csv_cache_path("E0", 2025)
    monkeypatch.setattr("fa.data.download.csv_cache_dir", lambda: tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("Div,Date\n")

    def boom(*a, **k):  # 若触网立即失败
        raise AssertionError("network touched")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert download_csv("E0", 2025) == p


def test_404_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr("fa.data.download.csv_cache_dir", lambda: tmp_path)

    def fake_urlopen(url, timeout):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert download_csv("F1", 1993) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/test_download.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'fa.data'`

- [ ] **Step 3: 写 download.py**

```python
import urllib.error
import urllib.request
from pathlib import Path

from fa.config import csv_url, csv_cache_dir, season_code


def csv_cache_path(league: str, start_year: int) -> Path:
    return csv_cache_dir() / f"{league}_{season_code(start_year)}.csv"


def download_csv(league: str, start_year: int, refresh: bool = False) -> Path | None:
    """下载 football-data.co.uk 赛季 CSV 到本地缓存。404 -> None（该赛季无数据）。"""
    path = csv_cache_path(league, start_year)
    if path.exists() and not refresh:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(csv_url(league, start_year), timeout=30) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    path.write_bytes(data)
    return path
```

- [ ] **Step 4: 跑测试**

Run: `uv run pytest tests/data/test_download.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/fa/data/ tests/data/
git commit -m "feat: football-data.co.uk CSV 下载与缓存（404 容忍）"
```

---

### Task 4: CSV 解析（跨 30 年列差异）

**Files:**
- Create: `src/fa/data/parse.py`
- Test: `tests/data/test_parse.py`

**Interfaces:**
- Consumes: 无（纯函数）
- Produces:
  - `MatchRow`（dataclass）字段：`league:str, season:int, date:str|None, home:str, away:str, fthg:int|None, ftag:int|None, shots_home, shots_away, shots_target_home, shots_target_away, corners_home, corners_away`（全 `int|None`），`ps_home, ps_draw, ps_away, psc_home, psc_draw, psc_away, over25_ps, under25_ps, over25_psc, under25_psc`（全 `float|None`），`raw:dict`
  - `parse_csv(content:str, league:str, season:int) -> list[MatchRow]`——丢弃无队名或无比分的行（赛季文件尾部的未赛 fixture）；解析失败的日期置 `None` 保留该行

- [ ] **Step 1: 写夹具与失败测试**

`tests/data/test_parse.py`：

```python
from fa.data.parse import parse_csv

# 90 年代格式：无射门/角球/赔率列，日期 d/m/yyyy
CSV_90S = """Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR
E0,19/08/1995,Arsenal,West Ham,1,1,D
E0,22/08/1995,Liverpool,Everton,2,0,H
"""

# 现代格式：射门/角球 + Pinnacle 快照/收盘 + 大小球 2.5
CSV_MODERN = """Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HS,AS,HST,AST,HC,AC,PSH,PSD,PSA,PSCH,PSCD,PSCA,P>2.5,P<2.5,PC>2.5,PC<2.5
D1,13/09/2024,Bayern Munich,Hoffenheim,4,0,H,18,7,9,2,8,2,1.25,6.5,15.0,1.22,7.0,17.0,1.20,4.80,1.18,5.10
"""

CSV_WITH_JUNK = """Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR
E0,19/08/1995,Arsenal,West Ham,1,1,D
E0,xx/xx/xxxx,Chelsea,Spurs,2,2,D
E0,,Fulham,Leeds,,
"""


def test_parse_90s_basic():
    rows = parse_csv(CSV_90S, "E0", 1995)
    assert len(rows) == 2
    r = rows[0]
    assert (r.home, r.away, r.fthg, r.ftag) == ("Arsenal", "West Ham", 1, 1)
    assert r.date == "1995-08-19"
    assert r.ps_home is None and r.corners_home is None   # 老赛季列缺失 -> None


def test_parse_modern_all_fields():
    rows = parse_csv(CSV_MODERN, "D1", 2024)
    r = rows[0]
    assert r.shots_home == 18 and r.shots_away == 7
    assert r.corners_home == 8 and r.corners_away == 2
    assert r.ps_home == 1.25 and r.psc_away == 17.0
    assert r.over25_ps == 1.20 and r.under25_psc == 5.10
    assert r.raw["FTR"] == "H"                            # 原始行留档


def test_junk_rows_dropped_or_kept_correctly():
    rows = parse_csv(CSV_WITH_JUNK, "E0", 1995)
    assert len(rows) == 2          # 未赛行（无比分）丢弃；坏日期行保留但 date=None
    assert rows[1].date is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/test_parse.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 写 parse.py**

```python
import io
from dataclasses import dataclass, field

import pandas as pd

INT_COLS = {
    "FTHG": "fthg", "FTAG": "ftag",
    "HS": "shots_home", "AS": "shots_away",
    "HST": "shots_target_home", "AST": "shots_target_away",
    "HC": "corners_home", "AC": "corners_away",
}
FLOAT_COLS = {
    "PSH": "ps_home", "PSD": "ps_draw", "PSA": "ps_away",
    "PSCH": "psc_home", "PSCD": "psc_draw", "PSCA": "psc_away",
    "P>2.5": "over25_ps", "P<2.5": "under25_ps",
    "PC>2.5": "over25_psc", "PC<2.5": "under25_psc",
}


@dataclass
class MatchRow:
    league: str
    season: int
    date: str | None
    home: str
    away: str
    fthg: int | None = None
    ftag: int | None = None
    shots_home: int | None = None
    shots_away: int | None = None
    shots_target_home: int | None = None
    shots_target_away: int | None = None
    corners_home: int | None = None
    corners_away: int | None = None
    ps_home: float | None = None
    ps_draw: float | None = None
    ps_away: float | None = None
    psc_home: float | None = None
    psc_draw: float | None = None
    psc_away: float | None = None
    over25_ps: float | None = None
    under25_ps: float | None = None
    over25_psc: float | None = None
    under25_psc: float | None = None
    raw: dict = field(default_factory=dict)


def _clean(v) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s or None


def _to_int(v) -> int | None:
    s = _clean(v)
    if s is None:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _to_float(v) -> float | None:
    s = _clean(v)
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _raw_dict(rec: dict) -> dict:
    """numpy 标量转 Python 原生类型，NaN 转 None，保证可 json.dumps。"""
    out = {}
    for k, v in rec.items():
        if v is None or v is pd.NA:
            out[k] = None
        elif isinstance(v, float) and pd.isna(v):
            out[k] = None
        elif hasattr(v, "item"):
            out[k] = v.item()
        else:
            out[k] = v
    return out


def parse_csv(content: str, league: str, season: int) -> list[MatchRow]:
    df = pd.read_csv(io.StringIO(content))
    df.columns = [str(c).strip() for c in df.columns]
    rows: list[MatchRow] = []
    for rec in df.to_dict("records"):
        home = _clean(rec.get("HomeTeam"))
        away = _clean(rec.get("AwayTeam"))
        fthg = _to_int(rec.get("FTHG"))
        ftag = _to_int(rec.get("FTAG"))
        if not home or not away or fthg is None or ftag is None:
            continue  # 空行或未赛 fixture
        date_s = _clean(rec.get("Date"))
        date = None
        if date_s:
            ts = pd.to_datetime(date_s, dayfirst=True, format="mixed",
                                errors="coerce")
            date = None if pd.isna(ts) else ts.strftime("%Y-%m-%d")
        row = MatchRow(league=league, season=season, date=date,
                       home=home, away=away, fthg=fthg, ftag=ftag,
                       raw=_raw_dict(rec))
        for col, attr in INT_COLS.items():
            setattr(row, attr, _to_int(rec.get(col)))
        for col, attr in FLOAT_COLS.items():
            setattr(row, attr, _to_float(rec.get(col)))
        rows.append(row)
    return rows
```

- [ ] **Step 4: 跑测试**

Run: `uv run pytest tests/data/test_parse.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/fa/data/parse.py tests/data/test_parse.py
git commit -m "feat: CSV 解析（跨年代列差异、未赛行过滤、原始行留档）"
```

---

### Task 5: 队名注册、别名解析与隔离表

**Files:**
- Create: `src/fa/data/teams.py`
- Test: `tests/data/test_teams.py`

**Interfaces:**
- Consumes: `fa.db.connect`
- Produces:
  - `get_or_create_team(conn, league:str, name:str) -> int`（返回 team id；同队复用）
  - `resolve_team(conn, league:str, name:str, source:str) -> int | None`（canonical 名 → 别名 → None）
  - `record_unknown(conn, source:str, name:str) -> None`（幂等入 `unknown_names`）

- [ ] **Step 1: 写失败测试**

`tests/data/test_teams.py`：

```python
import pytest

from fa.data.teams import get_or_create_team, record_unknown, resolve_team
from fa.db import connect, init_db


@pytest.fixture
def conn(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


def test_get_or_create_reuses_id(conn):
    a = get_or_create_team(conn, "D1", "Bayern Munich")
    b = get_or_create_team(conn, "D1", "Bayern Munich")
    assert a == b
    assert get_or_create_team(conn, "D1", "Dortmund") != a


def test_resolve_by_canonical_then_alias(conn):
    tid = get_or_create_team(conn, "D1", "Bayern Munich")
    assert resolve_team(conn, "D1", "Bayern Munich", "oddsapi") == tid
    conn.execute(
        "INSERT INTO team_aliases (team_id, source, alias)"
        " VALUES (?, 'oddsapi', 'Bayern München')",
        (tid,))
    conn.commit()
    assert resolve_team(conn, "D1", "Bayern München", "oddsapi") == tid
    assert resolve_team(conn, "D1", "FC Hollywood", "oddsapi") is None


def test_record_unknown_idempotent(conn):
    record_unknown(conn, "oddsapi", "München Bayern")
    record_unknown(conn, "oddsapi", "München Bayern")
    n = conn.execute("SELECT COUNT(*) c FROM unknown_names").fetchone()["c"]
    assert n == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/test_teams.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 写 teams.py**

```python
import sqlite3


def get_or_create_team(conn: sqlite3.Connection, league: str, name: str) -> int:
    row = conn.execute(
        "SELECT id FROM teams WHERE league=? AND name=?", (league, name)
    ).fetchone()
    if row is not None:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO teams (league, name) VALUES (?, ?)", (league, name))
    return cur.lastrowid


def resolve_team(conn: sqlite3.Connection, league: str, name: str,
                 source: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM teams WHERE league=? AND name=?", (league, name)
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT team_id FROM team_aliases WHERE source=? AND alias=?",
            (source, name)).fetchone()
    return None if row is None else row["id"]


def record_unknown(conn: sqlite3.Connection, source: str, name: str) -> None:
    conn.execute(
        "INSERT INTO unknown_names (source, name, first_seen) "
        "VALUES (?, ?, date('now')) "
        "ON CONFLICT(source, name) DO NOTHING",
        (source, name),
    )
```

- [ ] **Step 4: 跑测试**

Run: `uv run pytest tests/data/test_teams.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/fa/data/teams.py tests/data/test_teams.py
git commit -m "feat: 队名注册/别名解析/未知名隔离表"
```

---

### Task 6: 幂等入库（分区替换 + 内容哈希）

**Files:**
- Create: `src/fa/data/ingest.py`
- Test: `tests/data/test_ingest.py`

**Interfaces:**
- Consumes: `MatchRow`（Task 4）、`get_or_create_team`（Task 5）、`get_meta/set_meta`（Task 2）
- Produces: `ingest_rows(conn, league:str, season:int, rows:list[MatchRow]) -> int`——内容与上次相同返回 0 跳过；否则删除该 `(league, season)` 分区后整体重插，返回插入数。天然幂等，且上游修正历史赛果后重跑即收敛。

- [ ] **Step 1: 写失败测试**

`tests/data/test_ingest.py`：

```python
import pytest

from fa.data.ingest import ingest_rows
from fa.data.parse import parse_csv
from fa.db import connect, init_db

CSV_A = """Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR
E0,19/08/1995,Arsenal,West Ham,1,1,D
E0,22/08/1995,Liverpool,Everton,2,0,H
"""
CSV_B_CORRECTED = """Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR
E0,19/08/1995,Arsenal,West Ham,1,1,D
E0,22/08/1995,Liverpool,Everton,2,1,H
"""


@pytest.fixture
def conn(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


def count(conn):
    return conn.execute("SELECT COUNT(*) c FROM matches").fetchone()["c"]


def test_ingest_then_rerun_skips(conn):
    rows = parse_csv(CSV_A, "E0", 1995)
    assert ingest_rows(conn, "E0", 1995, rows) == 2
    assert ingest_rows(conn, "E0", 1995, rows) == 0     # 内容未变 -> 跳过
    assert count(conn) == 2


def test_changed_csv_replaces_partition(conn):
    ingest_rows(conn, "E0", 1995, parse_csv(CSV_A, "E0", 1995))
    n = ingest_rows(conn, "E0", 1995, parse_csv(CSV_B_CORRECTED, "E0", 1995))
    assert n == 2
    assert count(conn) == 2                              # 无重复
    row = conn.execute(
        "SELECT ftag FROM matches WHERE date='1995-08-22'").fetchone()
    assert row["ftag"] == 1                              # 修正已生效


def test_other_league_partition_untouched(conn):
    ingest_rows(conn, "E0", 1995, parse_csv(CSV_A, "E0", 1995))
    ingest_rows(conn, "D1", 1995, parse_csv(CSV_A, "D1", 1995))
    ingest_rows(conn, "E0", 1995, parse_csv(CSV_A, "E0", 1995))  # E0 重跑
    assert count(conn) == 4                              # D1 分区不受影响
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/test_ingest.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 写 ingest.py**

```python
import hashlib
import json
import sqlite3

from fa.data.parse import MatchRow
from fa.data.teams import get_or_create_team
from fa.db import get_meta, set_meta


def _rows_hash(rows: list[MatchRow]) -> str:
    payload = "\n".join(
        f"{r.date}|{r.home}|{r.away}|{r.fthg}-{r.ftag}|{r.ps_home}|{r.psc_home}"
        for r in rows)
    return hashlib.sha256(payload.encode()).hexdigest()


def ingest_rows(conn: sqlite3.Connection, league: str, season: int,
                rows: list[MatchRow]) -> int:
    key = f"csv_hash:{league}:{season}"
    h = _rows_hash(rows)
    if get_meta(conn, key) == h:
        return 0
    conn.execute("DELETE FROM matches WHERE league=? AND season=?",
                 (league, season))
    inserted = 0
    for r in rows:
        home_id = get_or_create_team(conn, league, r.home)
        away_id = get_or_create_team(conn, league, r.away)
        conn.execute(
            "INSERT INTO matches (league, season, date, home_team_id, away_team_id,"
            " fthg, ftag, shots_home, shots_away, shots_target_home, shots_target_away,"
            " corners_home, corners_away, ps_home, ps_draw, ps_away,"
            " psc_home, psc_draw, psc_away, over25_ps, under25_ps,"
            " over25_psc, under25_psc, raw_line)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r.league, r.season, r.date, home_id, away_id,
             r.fthg, r.ftag, r.shots_home, r.shots_away,
             r.shots_target_home, r.shots_target_away,
             r.corners_home, r.corners_away,
             r.ps_home, r.ps_draw, r.ps_away,
             r.psc_home, r.psc_draw, r.psc_away,
             r.over25_ps, r.under25_ps, r.over25_psc, r.under25_psc,
             json.dumps(r.raw, ensure_ascii=False)))
        inserted += 1
    set_meta(conn, key, h)
    conn.commit()
    return inserted
```

- [ ] **Step 4: 跑测试**

Run: `uv run pytest tests/data/test_ingest.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/fa/data/ingest.py tests/data/test_ingest.py
git commit -m "feat: 幂等入库（分区替换 + 内容哈希跳过）"
```

---

### Task 7: sync 编排与 CLI（`fa data sync-history` / `fa data status`）

**Files:**
- Create: `src/fa/data/sync.py`
- Modify: `src/fa/cli.py`（挂 `data` 子应用）
- Test: `tests/data/test_sync.py`

**Interfaces:**
- Consumes: `download_csv`（Task 3）、`parse_csv`（Task 4）、`ingest_rows`（Task 6）
- Produces:
  - `SyncReport`（dataclass）：`files_ok:int, files_missing:int, inserted:int, skipped_seasons:int, missing:list[tuple[str,int]]`
  - `sync_history(conn, seasons_from:int=1993, refresh:bool=False) -> SyncReport`
  - CLI：`fa data sync-history [--refresh]`、`fa data status`

- [ ] **Step 1: 写失败测试**

`tests/data/test_sync.py`：

```python
import pytest

from fa.data.sync import sync_history
from fa.db import connect, init_db

CSV_A = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\nE0,19/08/1995,Arsenal,West Ham,1,1,D\n"


@pytest.fixture
def conn(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


def test_sync_report_and_idempotent(conn, monkeypatch, tmp_path):
    calls = []

    def fake_download(league, start_year, refresh=False):
        calls.append((league, start_year))
        if start_year < 1995 or league != "E0":
            return None                        # 模拟老赛季/其他联赛无数据
        p = tmp_path / f"{league}_{start_year}.csv"
        p.write_text(CSV_A)
        return p

    monkeypatch.setattr("fa.data.sync.download_csv", fake_download)
    rep = sync_history(conn, seasons_from=1995, refresh=True)
    assert rep.files_ok == 1 and rep.inserted == 1
    assert ("E0", 1995) in calls

    rep2 = sync_history(conn, seasons_from=1995)   # 再跑：内容未变
    assert rep2.inserted == 0
    n = conn.execute("SELECT COUNT(*) c FROM matches").fetchone()["c"]
    assert n == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/test_sync.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 写 sync.py**

```python
import sqlite3
from dataclasses import dataclass, field
from datetime import date

from fa.config import LEAGUES, SEASONS_FROM
from fa.data.download import download_csv
from fa.data.ingest import ingest_rows
from fa.data.parse import parse_csv


@dataclass
class SyncReport:
    files_ok: int = 0
    files_missing: int = 0
    inserted: int = 0
    skipped_seasons: int = 0
    missing: list[tuple[str, int]] = field(default_factory=list)


def _current_season_start() -> int:
    t = date.today()
    return t.year if t.month >= 8 else t.year - 1


def sync_history(conn: sqlite3.Connection, seasons_from: int = SEASONS_FROM,
                 refresh: bool = False) -> SyncReport:
    rep = SyncReport()
    to_year = _current_season_start()
    for league in LEAGUES:
        for year in range(seasons_from, to_year + 1):
            path = download_csv(league, year, refresh=refresh)
            if path is None:
                rep.files_missing += 1
                rep.missing.append((league, year))
                continue
            rep.files_ok += 1
            rows = parse_csv(path.read_text(encoding="latin-1"), league, year)
            n = ingest_rows(conn, league, year, rows)
            if n:
                rep.inserted += n
            else:
                rep.skipped_seasons += 1
    return rep
```

- [ ] **Step 4: 挂 CLI 子应用**

`src/fa/cli.py` 顶部 import 区加 `from fa.data.sync import sync_history` 与 `from fa.db import connect`，文件追加：

```python
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
```

- [ ] **Step 5: 跑测试**

Run: `uv run pytest tests/data/test_sync.py tests/test_cli.py -v`
Expected: 全 PASS

- [ ] **Step 6: Commit**

```bash
git add src/fa/data/sync.py src/fa/cli.py tests/data/test_sync.py
git commit -m "feat: sync-history 编排与 fa data CLI（sync-history/status）"
```

---

### Task 8: walk-forward 取数框架

**Files:**
- Create: `src/fa/data/walkforward.py`
- Test: `tests/data/test_walkforward.py`

**Interfaces:**
- Consumes: `matches` 表（Task 2/6）
- Produces:
  - `MatchWeek`（dataclass）：`index:int, start:str, end:str, match_ids:list[int]`
  - `iter_matchweeks(conn, league:str, season:int) -> list[MatchWeek]`——按「周四至周三」自然比赛周分桶，`index` 从 1 递增
  - `training_matches(conn, league:str, asof:str) -> list[dict]`——该联赛 `date < asof` 的全部已完赛场次（跨赛季），按日期升序；M2 的衰减加权在此之上叠加

- [ ] **Step 1: 写失败测试**

`tests/data/test_walkforward.py`：

```python
import pytest

from fa.data.walkforward import iter_matchweeks, training_matches
from fa.db import connect, init_db


def _seed(conn):
    """两周比赛：1995-08-17(周四)~08-23(周三) 与 08-24(周四)~08-30(周三)"""
    conn.executemany(
        "INSERT INTO teams (league, name) VALUES ('E0', ?)",
        [("A",), ("B",), ("C",), ("D",)])
    ids = [r["id"] for r in conn.execute("SELECT id FROM teams")]
    rows = [
        ("1995-08-19", ids[0], ids[1]),   # 第 1 周（周六）
        ("1995-08-23", ids[2], ids[3]),   # 第 1 周（周三）
        ("1995-08-24", ids[0], ids[2]),   # 第 2 周（周四，新桶开始）
        ("1995-08-27", ids[1], ids[3]),   # 第 2 周（周日）
    ]
    conn.executemany(
        "INSERT INTO matches (league, season, date, home_team_id, away_team_id,"
        " fthg, ftag, raw_line) VALUES ('E0', 1995, ?, ?, ?, 0, 0, '{}')", rows)
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    _seed(c)
    yield c
    c.close()


def test_matchweeks_thursday_buckets(conn):
    weeks = iter_matchweeks(conn, "E0", 1995)
    assert len(weeks) == 2
    assert weeks[0].index == 1 and weeks[0].start == "1995-08-17"
    assert weeks[0].end == "1995-08-23"
    assert len(weeks[0].match_ids) == 2
    assert weeks[1].start == "1995-08-24" and weeks[1].end == "1995-08-27"


def test_training_matches_strict_cutoff(conn):
    rows = training_matches(conn, "E0", "1995-08-24")
    assert [r["date"] for r in rows] == ["1995-08-19", "1995-08-23"]


def test_empty_league(conn):
    assert iter_matchweeks(conn, "SP1", 1995) == []
    assert training_matches(conn, "SP1", "1995-08-24") == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/test_walkforward.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 写 walkforward.py**

```python
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta


@dataclass
class MatchWeek:
    index: int
    start: str
    end: str
    match_ids: list[int] = field(default_factory=list)


def _week_start(d: date) -> date:
    """比赛周从周四开始（欧陆赛程惯例：周中轮属上一周）。"""
    return d - timedelta(days=(d.weekday() - 3) % 7)


def iter_matchweeks(conn: sqlite3.Connection, league: str,
                    season: int) -> list[MatchWeek]:
    rows = conn.execute(
        "SELECT id, date FROM matches "
        "WHERE league=? AND season=? AND date IS NOT NULL ORDER BY date",
        (league, season)).fetchall()
    weeks: list[MatchWeek] = []
    cur: MatchWeek | None = None
    for r in rows:
        d = date.fromisoformat(r["date"])
        ws = _week_start(d)
        if cur is None or ws.isoformat() != cur.start:
            if cur is not None:
                weeks.append(cur)
            cur = MatchWeek(index=0, start=ws.isoformat(),
                            end=r["date"], match_ids=[r["id"]])
        else:
            cur.match_ids.append(r["id"])
            cur.end = r["date"]
    if cur is not None:
        weeks.append(cur)
    for i, w in enumerate(weeks, start=1):
        w.index = i
    return weeks


def training_matches(conn: sqlite3.Connection, league: str,
                     asof: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM matches WHERE league=? AND date < ? ORDER BY date",
        (league, asof)).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: 跑测试**

Run: `uv run pytest tests/data/test_walkforward.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/fa/data/walkforward.py tests/data/test_walkforward.py
git commit -m "feat: walk-forward 取数框架（周四制比赛周分桶 + as-of 切片）"
```

---

### Task 9: 对账抽样（`fa data audit`）

**Files:**
- Create: `src/fa/data/audit.py`
- Modify: `src/fa/cli.py`（挂 `audit` 命令）
- Test: `tests/data/test_audit.py`

**Interfaces:**
- Consumes: `csv_cache_dir / season_code`（Task 2/3）、`parse_csv`（Task 4）、`matches/teams` 表
- Produces:
  - `AuditMismatch`（dataclass）：`match_id:int, field:str, db_value:object, csv_value:object`
  - `audit_sample(conn, sample:int=50, seed:int=42) -> list[AuditMismatch]`——随机抽 N 场，用缓存 CSV 重新解析比对 `fthg/ftag/ps_home/ps_draw/ps_away/psc_home/psc_draw/psc_away`；CSV 行找不到记 `field="row_missing"`；缓存文件缺失记 `field="cache_missing"`
  - CLI：`fa data audit [--sample 50] [--seed 42]`，输出逐条不一致 + 总结，有不一致时退出码 1

- [ ] **Step 1: 写失败测试**

`tests/data/test_audit.py`：

```python
import pytest

from fa.data.audit import audit_sample
from fa.data.ingest import ingest_rows
from fa.data.parse import parse_csv
from fa.db import connect, init_db

CSV_GOOD = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\nE0,19/08/1995,Arsenal,West Ham,1,1,D\n"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    init_db(tmp_path / "t.db")
    conn = connect(tmp_path / "t.db")
    cache = tmp_path / "csv"
    cache.mkdir()
    monkeypatch.setattr("fa.data.audit.csv_cache_dir", lambda: cache)
    (cache / "E0_1995.csv").write_text(CSV_GOOD)
    ingest_rows(conn, "E0", 1995, parse_csv(CSV_GOOD, "E0", 1995))
    yield conn, cache
    conn.close()


def test_no_mismatch(setup):
    conn, _ = setup
    assert audit_sample(conn, sample=10) == []


def test_detects_corrupted_db_row(setup):
    conn, _ = setup
    conn.execute("UPDATE matches SET fthg=7")     # 模拟入库 bug
    conn.commit()
    mismatches = audit_sample(conn, sample=10)
    assert len(mismatches) == 1
    assert mismatches[0].field == "fthg"
    assert mismatches[0].db_value == 7 and mismatches[0].csv_value == 1


def test_cache_missing_reported(setup):
    conn, cache = setup
    (cache / "E0_1995.csv").unlink()
    mismatches = audit_sample(conn, sample=10)
    assert mismatches and mismatches[0].field == "cache_missing"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/test_audit.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 写 audit.py**

```python
import random
import sqlite3
from dataclasses import dataclass

from fa.config import csv_cache_dir, season_code
from fa.data.parse import parse_csv

COMPARE_FIELDS = ["fthg", "ftag", "ps_home", "ps_draw", "ps_away",
                  "psc_home", "psc_draw", "psc_away"]


@dataclass
class AuditMismatch:
    match_id: int
    field: str
    db_value: object
    csv_value: object


def audit_sample(conn: sqlite3.Connection, sample: int = 50,
                 seed: int = 42) -> list[AuditMismatch]:
    ids = [r["id"] for r in conn.execute("SELECT id FROM matches ORDER BY id")]
    if not ids:
        return []
    picked = random.Random(seed).sample(ids, min(sample, len(ids)))
    csv_cache: dict[tuple[str, int], dict[tuple[str, str, str], object]] = {}
    out: list[AuditMismatch] = []
    for mid in picked:
        m = conn.execute(
            "SELECT m.*, h.name home, a.name away FROM matches m "
            "JOIN teams h ON h.id=m.home_team_id "
            "JOIN teams a ON a.id=m.away_team_id WHERE m.id=?", (mid,)).fetchone()
        key = (m["league"], m["season"])
        if key not in csv_cache:
            path = csv_cache_dir() / f"{m['league']}_{season_code(m['season'])}.csv"
            if not path.exists():
                out.append(AuditMismatch(mid, "cache_missing", None, str(path)))
                continue
            rows = parse_csv(path.read_text(encoding="latin-1"),
                             m["league"], m["season"])
            csv_cache[key] = {(r.date, r.home, r.away): r for r in rows}
        row = csv_cache[key].get((m["date"], m["home"], m["away"]))
        if row is None:
            out.append(AuditMismatch(mid, "row_missing", None, None))
            continue
        for f in COMPARE_FIELDS:
            if m[f] != getattr(row, f):
                out.append(AuditMismatch(mid, f, m[f], getattr(row, f)))
    return out
```

- [ ] **Step 4: 挂 CLI**

`src/fa/cli.py` 顶部 import 区加 `from fa.data.audit import audit_sample`，`data_app` 下追加：

```python
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
```

- [ ] **Step 5: 跑测试**

Run: `uv run pytest tests/data/test_audit.py -v`
Expected: 3 PASS

- [ ] **Step 6: Commit**

```bash
git add src/fa/data/audit.py src/fa/cli.py tests/data/test_audit.py
git commit -m "feat: 抽样对账 fa data audit（DB vs 缓存 CSV）"
```

---

### Task 10: E2E 验收跑（全量入库 + 对账）

**Files:**
- Create: `docs/m1-report.md`（验收记录）
- 无新测试；跑真实验收

**Interfaces:**
- Consumes: 全部前序任务
- Produces: M1 验收证据（spec §10 M1 行四条验收标准全绿）

- [ ] **Step 1: 全量拉取入库**

Run: `uv run fa init && uv run fa data sync-history`
Expected: 下载约 5 联赛 × 30+ 赛季（部分 90 年代赛季 404 属正常，报告 missing）；总场次约 10 万量级。首次跑需数分钟。

- [ ] **Step 2: 幂等验证**

Run: `uv run fa data sync-history`（立即重跑）
Expected: `新入库 0 场`——spec 验收「sync-history 幂等」。

- [ ] **Step 3: 对账抽样**

Run: `uv run fa data audit --sample 50`
Expected: `对账通过：抽样 50 场，0 不一致`——spec 验收「抽样 50 场与 CSV 对账一致」。

- [ ] **Step 4: 概况检查**

Run: `uv run fa data status`
Expected: 5 个联赛各有 25–33 个赛季、场均数合理（E0 ~380 场/赛季、D1/F1 ~306、SP1/I1 ~380，90 年代老赛季偏差属正常）；未知队名 0 条（M1 数据源单一，隔离表机制已在 Task 5 测试覆盖）。

- [ ] **Step 5: 记录验收报告**

`docs/m1-report.md`（`<...>` 为执行时实测值）：

```markdown
# M1 验收报告

- 日期：<执行日>
- 全量入库：E0 <n> 场 / SP1 <n> / D1 <n> / I1 <n> / F1 <n>（共 <total>）
- 幂等重跑：新入库 0 场 ✔
- 对账抽样 50 场：0 不一致 ✔
- 无数据赛季清单：<missing 摘要，属正常覆盖差异>
- 全量测试：`uv run pytest` <N> passed
```

- [ ] **Step 6: 全量测试 + Commit**

Run: `uv run pytest`
Expected: 全部 PASS（若真实数据暴露边角问题——新列、怪日期——修复 parse/ingest 并先补失败测试再修，遵循 TDD）

```bash
git add docs/m1-report.md
git commit -m "chore: M1 验收报告（全量入库 + 幂等 + 对账 50 场通过）"
```

---

## Self-Review 记录

- **Spec 覆盖**：§3.1 历史源全量入库 ✔（Odds API 属 M3）；§3.2 表结构 ✔（Task 2 一次到位；`odds_snapshots/recommendations/bets/runs` 留待对应里程碑建表——执行顺序说明，非偏离 spec）；§3.3 队名对齐机制 ✔（Task 5；别名自动生成工具在 M3 接入 Odds API 后才有输入，M3 补）；§3.4「每日更新完赛」由 `sync-history` 幂等重跑实现，调度接入在 M3 hermes cron ✔；§10 M1 四条验收 → Task 10 ✔
- **占位符扫描**：全部代码块为完整可执行实现，无 TBD/TODO；Task 10 报告模板中 `<n>` 为执行时填充的实测值，属预期
- **类型一致性**：`ingest_rows(conn, league, season, rows)` Task 6 定义、Task 7 消费一致；`MatchRow` 属性名与 Task 2 schema 列一一对应（`raw` ↔ `raw_line`）；monkeypatch 的模块路径（`fa.data.download.csv_cache_dir` / `fa.data.audit.csv_cache_dir` / `fa.data.sync.download_csv`）已逐一核对与实现内引用方式一致
