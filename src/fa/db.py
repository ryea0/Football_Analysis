import sqlite3
from pathlib import Path

from fa.config import db_path

SCHEMA_VERSION = 3

# backtest_predictions 建表 DDL：新建与迁移共用同一常量，保证两条路径的表结构
# 由构造即一致（否则未来加列只会出现在新库、老库迁移后缺列）。
_BP_TABLE = """
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

# B 线（paper 运营）五表：spec §3.2 / §12.1。表边界硬约束——B 线只写这五张
# （外加 meta），绝不写 backtest_predictions / matches。与 _BP_TABLE 同理，
# 新建与迁移共用同一常量，两条路径的表结构由构造即一致。
# M4 persona 才落的位（verdict / confidence_delta / final_stake_frac）建库即可空。
# 词表用 CHECK 钉死（mode/status/strategy/market/phase/type）——SQLite 无法
# ALTER ADD CHECK，须趁表空时一次到位；fixtures.status 词表仍在演进，暂不加。
_BLINE_TABLE = """
CREATE TABLE IF NOT EXISTS fixtures (
    id           INTEGER PRIMARY KEY,
    league       TEXT NOT NULL,
    event_key    TEXT NOT NULL UNIQUE,        -- Odds API event id（对齐业务键）
    source       TEXT NOT NULL,               -- 'oddsapi'
    kickoff_utc  TEXT NOT NULL,               -- ISO UTC 开球时间
    home_team_id INTEGER REFERENCES teams(id),   -- 未对齐时 NULL（spec §3.3）
    away_team_id INTEGER REFERENCES teams(id),
    -- status 词表（scheduled/finished/…）随 T5 同步与 T7 结算演进，故不加 CHECK
    status       TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS odds_snapshots (
    id         INTEGER PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    fixture_id INTEGER REFERENCES fixtures(id),  -- 对齐完成前可空
    event_key  TEXT NOT NULL,
    market     TEXT NOT NULL,                -- h2h / totals
    region     TEXT NOT NULL,                -- eu / uk
    bookmaker  TEXT NOT NULL,
    outcomes   TEXT NOT NULL,                -- JSON：各 outcome 赔率
    raw        TEXT NOT NULL                 -- JSON：原始响应片段留档
);
CREATE INDEX IF NOT EXISTS idx_odds_snap_event
    ON odds_snapshots (event_key, market, fetched_at);

CREATE TABLE IF NOT EXISTS runs (
    id             INTEGER PRIMARY KEY,
    -- type 词表（spec §3.2）：am/pm 由 phase 承载，故 matchday 不再拆 _am/_pm
    type           TEXT NOT NULL
        CHECK (type IN ('daily', 'matchday', 'backtest', 'manual')),
    phase          TEXT
        CHECK (phase IN ('am', 'pm')),       -- 仅 matchday run 有
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    status         TEXT NOT NULL,            -- ok / skipped / failed / no_key
    credits_before INTEGER,                  -- Odds API 剩余额度（spec §3.4）
    credits_after  INTEGER,
    summary        TEXT                      -- JSON
);

CREATE TABLE IF NOT EXISTS recommendations (
    id               INTEGER PRIMARY KEY,
    run_id           INTEGER NOT NULL REFERENCES runs(id),
    fixture_id       INTEGER NOT NULL REFERENCES fixtures(id),
    strategy         TEXT NOT NULL
        CHECK (strategy IN ('model_only', 'model_persona')),   -- §6.6 A/B 双轨
    market           TEXT NOT NULL
        CHECK (market IN ('H', 'D', 'A', 'O2.5')),             -- 每个结果一行
    phase            TEXT NOT NULL
        CHECK (phase IN ('am', 'pm')),                         -- §9.6 两窗
    model_p          REAL NOT NULL,
    market_p         REAL NOT NULL,
    best_odds        REAL NOT NULL,          -- 可成交最优价
    bookmaker        TEXT NOT NULL,
    edge             REAL NOT NULL,
    ev               REAL NOT NULL,
    kelly_stake_frac REAL NOT NULL,
    verdict          TEXT,                   -- M4 persona 填写
    confidence_delta REAL,                   -- M4
    final_stake_frac REAL,                   -- M4
    created_at       TEXT NOT NULL,
    UNIQUE (fixture_id, market, strategy, phase)
);
CREATE INDEX IF NOT EXISTS idx_recs_run ON recommendations (run_id);

CREATE TABLE IF NOT EXISTS bets (
    id                INTEGER PRIMARY KEY,
    recommendation_id INTEGER NOT NULL REFERENCES recommendations(id),
    mode              TEXT NOT NULL
        CHECK (mode IN ('paper', 'live')),   -- live 仅 fa bet add --live（§12.2）
    placed_at         TEXT NOT NULL,
    bookmaker         TEXT NOT NULL,
    odds_taken        REAL NOT NULL,
    stake             REAL NOT NULL,
    status            TEXT NOT NULL
        CHECK (status IN ('pending', 'won', 'lost', 'void')),
    settled_at        TEXT,
    return_amt        REAL,
    closing_odds      REAL,                  -- CLV 基准（Pinnacle 收盘）
    clv               REAL,
    UNIQUE (recommendation_id, mode)
);
CREATE INDEX IF NOT EXISTS idx_bets_status ON bets (status);
"""

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
""" + _BP_TABLE + _BLINE_TABLE


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
    """顺序升级，逐级纯加法、无数据搬迁：
    v1->v2 新增 backtest_predictions；v2->v3 新增 B 线五表
    （fixtures / odds_snapshots / runs / recommendations / bets）。"""
    if from_v < 2:
        conn.executescript(_BP_TABLE)
    if from_v < 3:
        conn.executescript(_BLINE_TABLE)
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
