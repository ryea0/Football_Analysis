import sqlite3
from pathlib import Path

from fa.config import db_path

SCHEMA_VERSION = 7

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
# M4 persona 才落的位（verdict / confidence_delta / final_stake_frac /
# key_factors / report_md）建库即可空。
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
    key_factors      TEXT,                   -- M4 persona：JSON 数组串（§6.3，1–5 条）
    report_md        TEXT,                   -- M4 persona：点评 ≤500 字（§6.3）
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
    closing_odds      REAL,                  -- CLV 基准（psc 优先，缺失 fallback bfe）
    -- closing_source 诚实记账用了哪个基准：'pinnacle' / 'betfair'；NULL=无基准
    closing_source    TEXT,
    clv               REAL,
    UNIQUE (recommendation_id, mode)
);
CREATE INDEX IF NOT EXISTS idx_bets_status ON bets (status);
"""

# A 线复盘归因子线两表（spec docs/superpowers/specs/2026-09-04-retro-attribution-design.md
# §7）。物理隔离：retro 对 recommendations/bets 无任何代码通路。selector 词表含
# S2/S3 的 paper_t1 / agentline_aligned——SQLite 无法后补 CHECK，趁表空一次到位。
# is_control 是后补列（2026-09-04 终审）：纯加列 + DEFAULT 0——生产库经
# fa init 逐级迁移（_migrate_up），本表随版本演进纯加列（v5 attributor
# 列同法：新建 DDL 含列、迁移走 ALTER，见 _migrate_up 幂等护栏）；CREATE
# TABLE IF NOT EXISTS 对已存在的库不生效，存量库加列一律经 ALTER 路径
# （旧 worktree 快照库与现表结构不匹配属预期）。
# 契约字段（miss_tags 等）在 status != 'ok' 的行上为 NULL（parse_fail 只留审计
# 字段；原始输出留档属下个计划，spec §15 待办）。
_RETRO_TABLE = """
CREATE TABLE IF NOT EXISTS retro_runs (
    id           INTEGER PRIMARY KEY,
    selector     TEXT NOT NULL
        CHECK (selector IN ('divergence', 'manual', 'paper_t1', 'agentline_aligned')),
    params_json  TEXT NOT NULL,
    n_selected   INTEGER NOT NULL,
    n_ok         INTEGER NOT NULL,
    n_parse_fail INTEGER NOT NULL,
    n_timeout    INTEGER NOT NULL,
    n_error      INTEGER NOT NULL,
    duration_s   REAL NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS retro_attributions (
    id              INTEGER PRIMARY KEY,
    batch_id        INTEGER NOT NULL REFERENCES retro_runs(id),
    match_id        INTEGER NOT NULL REFERENCES matches(id),
    league          TEXT NOT NULL,
    season          INTEGER NOT NULL,
    date            TEXT NOT NULL,
    selector        TEXT NOT NULL
        CHECK (selector IN ('divergence', 'manual', 'paper_t1', 'agentline_aligned')),
    attributor     INTEGER NOT NULL DEFAULT 1,  -- 成员 1..N；聚合行 0（ensemble，spec §3）
    is_control      INTEGER NOT NULL DEFAULT 0,  -- 病例=0/对照=1（divergence 选择器产出）
    miss_tags_json  TEXT,            -- JSON 数组；status != ok 时 NULL
    primary_tag     TEXT,
    tags_confidence REAL,
    model_vs_market TEXT
        CHECK (model_vs_market IN ('model_wrong', 'market_wrong', 'both_off',
                                   'variance')),
    evidence_json   TEXT,
    digest          TEXT,
    status          TEXT NOT NULL CHECK (status IN ('ok', 'parse_fail',
                                                    'timeout', 'error')),
    repaired        INTEGER NOT NULL DEFAULT 0,
    harness         TEXT NOT NULL,
    model           TEXT,
    duration_s      REAL,
    input_pack_path TEXT NOT NULL,
    tag_set_version TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_retro_attr_batch ON retro_attributions (batch_id);
CREATE INDEX IF NOT EXISTS idx_retro_attr_match ON retro_attributions (match_id);
"""

# 范式对比线两表（spec §12.5 / 设计 §6）：线 A 专用，与 B 线表物理隔离——
# 本模块的 B 线边界注释同样适用于这里：绝不写 recommendations / bets 等。
# v4/v5 分层由并行分支合并产生（2026-09-04）：retro 先占 v4，本线抬 v5。
# v7 补二期多脑预留（multi-brain 附录 §6 欠账）：line CHECK 加 'A_multi'、
# attributor 列（成员 1..N / 聚合 0）、UNIQUE 抬三元组 (match_id, line,
# attributor)——A_multi 同场四行并存的前提；A_base/A_enh 恒 attributor=1，
# 语义不变。CHECK 与 UNIQUE 无法 ALTER，v7 走重建表迁移（见 _migrate_up）。
_AL_TABLE = """
CREATE TABLE IF NOT EXISTS agentline_predictions (
    id                INTEGER PRIMARY KEY,
    match_id          INTEGER NOT NULL REFERENCES matches(id),
    line              TEXT NOT NULL
        CHECK (line IN ('A_base', 'A_enh', 'A_multi')),
    attributor        INTEGER NOT NULL DEFAULT 1,  -- 成员 1..N；聚合行 0（二期）
    p_home REAL, p_draw REAL, p_away REAL, p_over25 REAL,   -- parse_fail 时 NULL
    confidence        REAL,
    reasoning_digest  TEXT,
    sources_json      TEXT,           -- 增强层引用来源（基线层 '[]'）
    raw_output        TEXT NOT NULL,  -- agent 原始返回全文（审计/重放）
    status            TEXT NOT NULL
        CHECK (status IN ('ok', 'parse_fail', 'timeout', 'error')),
    repaired          INTEGER,        -- 是否经 JSON 修复（0/1）
    harness           TEXT,           -- dsh 版本审计
    model             TEXT,
    duration_s        REAL,
    created_at        TEXT NOT NULL,
    UNIQUE (match_id, line, attributor)   -- 幂等：一场一line一attributor一行
);
CREATE INDEX IF NOT EXISTS idx_alp_line ON agentline_predictions (line);

CREATE TABLE IF NOT EXISTS agentline_runs (
    id          INTEGER PRIMARY KEY,
    line        TEXT NOT NULL,
    profile     TEXT NOT NULL,
    model       TEXT,
    n_ok INTEGER NOT NULL DEFAULT 0, n_parse_fail INTEGER NOT NULL DEFAULT 0,
    n_timeout  INTEGER NOT NULL DEFAULT 0, n_error INTEGER NOT NULL DEFAULT 0,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    summary     TEXT                          -- JSON（样本筛选条件等）
);
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
    -- Betfair 交易所收盘（BFECH/BFECD/BFECA/BFEC>2.5）：Pinnacle 断供
    -- （football-data 2025-12 起）后的 CLV fallback 基准，spec §7.3
    bfe_home REAL, bfe_draw REAL, bfe_away REAL, over25_bfe REAL,
    raw_line TEXT NOT NULL,                         -- 原始 CSV 行 JSON 留档
    UNIQUE (league, season, date, home_team_id, away_team_id)
);
CREATE INDEX IF NOT EXISTS idx_matches_league_date ON matches (league, date);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
""" + _BP_TABLE + _BLINE_TABLE + _RETRO_TABLE + _AL_TABLE


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
    （fixtures / odds_snapshots / runs / recommendations / bets）；v3->v4 新增
    retro 两表（retro_runs / retro_attributions，A 线复盘归因子线）；
    v4->v5 新增范式对比线两表（agentline_predictions / agentline_runs）并给
    retro_attributions 加 attributor 列——v5 分层由 2026-09-04 并行分支合并产生
    （retro ensemble 与 agentline 同日各自 bump v4，合并时统一为单一 v5）；
    v5->v6 纯加列——matches 增 BFE 收盘四列、bets 增 closing_source（Pinnacle
    断供应对，spec §7.3）；recommendations 补 persona 两列 key_factors /
    report_md（§6.3；M4 分支基于旧 main 原占 v4，集成时并入 v6——第三个
    并行撞号位，2026-09-04）；
    v6->v7 agentline_predictions 重建表补二期多脑预留（line CHECK 加
    'A_multi' + attributor 列 + UNIQUE 三元组）——本层例外非纯加法（CHECK/
    UNIQUE 无法 ALTER），走建新表搬数据，存量行 attributor 回填 1。"""
    if from_v < 2:
        conn.executescript(_BP_TABLE)
    if from_v < 3:
        conn.executescript(_BLINE_TABLE)
    if from_v < 4:
        conn.executescript(_RETRO_TABLE)
    if from_v < 5:
        conn.executescript(_AL_TABLE)
        # ALTER 纯加法：attributor 无 CHECK 可安全 ADD COLUMN（词表一次到位
        # 教训只约束带 CHECK 的列）；存量行 DEFAULT 1 = 单成员语义不变。
        # 幂等护栏（必要）：init_db 先跑 _SCHEMA（_RETRO_TABLE 新 DDL 已含该
        # 列），v3 及更老的库此时经 IF NOT EXISTS 重建即带列，无条件 ALTER 会
        # 炸 duplicate column；v4 真库无此列，ALTER 照走。
        has_col = any(r["name"] == "attributor" for r in conn.execute(
            "PRAGMA table_info(retro_attributions)"))
        if not has_col:
            conn.execute(
                "ALTER TABLE retro_attributions ADD COLUMN"
                " attributor INTEGER NOT NULL DEFAULT 1")
    if from_v < 6:
        # 防重入：版本号与表形状在历史上出现过错位（is_control 先例——加列未
        # bump 版本），列已存在就跳过，别让 ALTER 炸在「旧版本号 × 新形状表」上；
        # 表本身缺失（极简合成库 / 分支级 _migrate_up 单测）同样跳过——真实库
        # 自 v1 起 matches/bets 必在
        mcols = {r["name"] for r in conn.execute("PRAGMA table_info(matches)")}
        if mcols:
            for col in ("bfe_home", "bfe_draw", "bfe_away", "over25_bfe"):
                if col not in mcols:
                    conn.execute(f"ALTER TABLE matches ADD COLUMN {col} REAL")
        bcols = {r["name"] for r in conn.execute("PRAGMA table_info(bets)")}
        if bcols and "closing_source" not in bcols:
            conn.execute("ALTER TABLE bets ADD COLUMN closing_source TEXT")
        # persona 两列（M4，§6.3）：列级加法只能 ALTER——老库已有数据不得重建
        # 表；按列存在性判定只补真缺的，半途断掉的迁移可续跑（ADD COLUMN 非事务
        # 原子）。与 _BLINE_TABLE 同名列同语义，形状一致性由
        # test_migrate_and_fresh_schemas_match 钉住。
        rcols = {r["name"] for r in
                 conn.execute("PRAGMA table_info(recommendations)")}
        if rcols:
            if "key_factors" not in rcols:
                conn.execute(
                    "ALTER TABLE recommendations ADD COLUMN key_factors TEXT")
            if "report_md" not in rcols:
                conn.execute(
                    "ALTER TABLE recommendations ADD COLUMN report_md TEXT")
    if from_v < 7:
        # agentline A_multi 预留（二期 multi-brain 附录 §6 欠账，v7）：CHECK 与
        # UNIQUE 无法 ALTER → 建新表（_AL_TABLE 同一常量的形状）搬数据、改名。
        # 幂等护栏（必要，同 v5 attributor 护卫理据）：init_db 先跑 _SCHEMA，
        # v6 及更老的库此时表已存在 IF NOT EXISTS 不动它，照走重建；但迁移
        # 半途断掉重跑（表已新形状、版本号未抬）时无条件重建会丢数据——按
        # DDL 是否已含 'A_multi' 判定跳过。本表无入边 FK（叶子表），重建安全。
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table'"
            " AND name='agentline_predictions'").fetchone()
        if row is not None and "'A_multi'" not in row["sql"]:
            conn.execute("DROP INDEX IF EXISTS idx_alp_line")
            conn.execute(
                "CREATE TABLE agentline_predictions_v7 ("
                " id                INTEGER PRIMARY KEY,"
                " match_id          INTEGER NOT NULL REFERENCES matches(id),"
                " line              TEXT NOT NULL"
                "     CHECK (line IN ('A_base', 'A_enh', 'A_multi')),"
                " attributor        INTEGER NOT NULL DEFAULT 1,"
                " p_home REAL, p_draw REAL, p_away REAL, p_over25 REAL,"
                " confidence        REAL,"
                " reasoning_digest  TEXT,"
                " sources_json      TEXT,"
                " raw_output        TEXT NOT NULL,"
                " status            TEXT NOT NULL"
                "     CHECK (status IN ('ok', 'parse_fail', 'timeout',"
                " 'error')),"
                " repaired          INTEGER,"
                " harness           TEXT,"
                " model             TEXT,"
                " duration_s        REAL,"
                " created_at        TEXT NOT NULL,"
                " UNIQUE (match_id, line, attributor))")
            conn.execute(
                "INSERT INTO agentline_predictions_v7 (id, match_id, line,"
                " attributor, p_home, p_draw, p_away, p_over25, confidence,"
                " reasoning_digest, sources_json, raw_output, status,"
                " repaired, harness, model, duration_s, created_at)"
                " SELECT id, match_id, line, 1, p_home, p_draw, p_away,"
                " p_over25, confidence, reasoning_digest, sources_json,"
                " raw_output, status, repaired, harness, model, duration_s,"
                " created_at FROM agentline_predictions")
            conn.execute("DROP TABLE agentline_predictions")
            conn.execute(
                "ALTER TABLE agentline_predictions_v7"
                " RENAME TO agentline_predictions")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_alp_line"
                " ON agentline_predictions (line)")
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
