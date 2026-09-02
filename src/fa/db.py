import sqlite3
from pathlib import Path

from fa.config import db_path

SCHEMA_VERSION = 2

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

CREATE TABLE IF NOT EXISTS backtest_predictions (
    id INTEGER PRIMARY KEY,
    league TEXT NOT NULL,
    season INTEGER NOT NULL,
    week_index INTEGER NOT NULL,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    date TEXT NOT NULL,
    p_home REAL NOT NULL, p_draw REAL NOT NULL, p_away REAL NOT NULL,
    p_over25 REAL, p_under25 REAL, p_btts REAL,
    mkt_home REAL, mkt_draw REAL, mkt_away REAL, mkt_over25 REAL,
    odds_home REAL, odds_draw REAL, odds_away REAL,
    outcome TEXT NOT NULL,
    total_goals INTEGER NOT NULL,
    UNIQUE (match_id)
);
CREATE INDEX IF NOT EXISTS idx_bp_league_season
    ON backtest_predictions (league, season);
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
    elif row["version"] < SCHEMA_VERSION:
        _migrate_up(conn, row["version"])
    elif row["version"] > SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库 schema 版本 {row['version']} 高于程序 {SCHEMA_VERSION}，请升级 fa")
    conn.commit()
    conn.close()


def _migrate_up(conn: sqlite3.Connection, from_v: int) -> None:
    """顺序升级。v1->v2：仅新增 backtest_predictions 表（加法，无数据搬迁）。"""
    if from_v < 2:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS backtest_predictions (
            id INTEGER PRIMARY KEY,
            league TEXT NOT NULL,
            season INTEGER NOT NULL,
            week_index INTEGER NOT NULL,
            match_id INTEGER NOT NULL REFERENCES matches(id),
            date TEXT NOT NULL,
            p_home REAL NOT NULL, p_draw REAL NOT NULL, p_away REAL NOT NULL,
            p_over25 REAL, p_under25 REAL, p_btts REAL,
            mkt_home REAL, mkt_draw REAL, mkt_away REAL, mkt_over25 REAL,
            odds_home REAL, odds_draw REAL, odds_away REAL,
            outcome TEXT NOT NULL,
            total_goals INTEGER NOT NULL,
            UNIQUE (match_id)
        );
        CREATE INDEX IF NOT EXISTS idx_bp_league_season
            ON backtest_predictions (league, season);
        """)
    conn.execute("UPDATE schema_version SET version=?", (SCHEMA_VERSION,))


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return None if row is None else row["value"]


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
