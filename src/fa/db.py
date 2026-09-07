import shutil
import sqlite3
from pathlib import Path

from fa.config import db_path

SCHEMA_VERSION = 11

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
        CHECK (strategy IN ('model_only', 'model_persona',
                            'model_persona_nokb',
                            'model_persona_kb_self')),  -- §6.6 双轨 + C线nokb + C'线自反思
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
    personas_hash    TEXT,             -- M6：本 run 读取的 personas 树内容 hash（§12.7）
    personas_self_hash TEXT,           -- C' 线：自反思知识库树 hash
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

# M6 C 线进化栈三表（spec §12.7，设计档 §2.2）。物理隔离：evolve 对
# recommendations/bets 只读；自家事件与关卡 Ruling 落此三表。UNIQUE(window_id,
# league) = 防重跑（一次窗口一联赛一次反思）；ruling 的 note 强制非空——裁定
# 必须留理由（人审关卡全量记 Ruling）。
_EVOLUTION_TABLE = """
CREATE TABLE IF NOT EXISTS evolution_windows (
    id           INTEGER PRIMARY KEY,
    idx          INTEGER NOT NULL UNIQUE,
    opened_at    TEXT NOT NULL,
    closes_at    TEXT NOT NULL,
    reflected_at TEXT,
    closed_at    TEXT
);

CREATE TABLE IF NOT EXISTS evolution_runs (
    id               INTEGER PRIMARY KEY,
    window_id        INTEGER NOT NULL REFERENCES evolution_windows(id),
    league           TEXT NOT NULL,
    kb_hash_before   TEXT NOT NULL,
    status           TEXT NOT NULL
        CHECK (status IN ('ok', 'no_change', 'timeout', 'exit',
                          'extract', 'contract', 'error')),
    no_change_reason TEXT,
    proposal_path    TEXT,
    duration_s       REAL NOT NULL,
    created_at       TEXT NOT NULL,
    UNIQUE (window_id, league)
);

CREATE TABLE IF NOT EXISTS evolution_rulings (
    id            INTEGER PRIMARY KEY,
    run_id        INTEGER NOT NULL REFERENCES evolution_runs(id),
    ruling        TEXT NOT NULL
        CHECK (ruling IN ('merged', 'rejected', 'shelved')),
    kb_hash_after TEXT,
    note          TEXT NOT NULL,
    decided_at    TEXT NOT NULL
);
"""

# C' 线（自反思知识库对照线）三表：与 C 线同构但独立命名空间。
# v11 新增，重建 recommendations 时一并扩 strategy 枚举 + 加 personas_self_hash 列。
_EVOLUTION_SELF_TABLE = """
CREATE TABLE IF NOT EXISTS evolution_self_windows (
    id           INTEGER PRIMARY KEY,
    idx          INTEGER NOT NULL UNIQUE,
    opened_at    TEXT NOT NULL,
    closes_at    TEXT NOT NULL,
    reflected_at TEXT,
    closed_at    TEXT
);

CREATE TABLE IF NOT EXISTS evolution_self_runs (
    id               INTEGER PRIMARY KEY,
    window_id        INTEGER NOT NULL REFERENCES evolution_self_windows(id),
    league           TEXT NOT NULL,
    kb_hash_before   TEXT NOT NULL,
    status           TEXT NOT NULL
        CHECK (status IN ('ok', 'no_change', 'timeout', 'exit',
                          'contract', 'sanity', 'error')),
    no_change_reason TEXT,
    proposal_path    TEXT,
    added_chars      INTEGER DEFAULT 0,
    changed_lines    INTEGER DEFAULT 0,
    duration_s       REAL NOT NULL,
    created_at       TEXT NOT NULL,
    UNIQUE (window_id, league)
);

CREATE TABLE IF NOT EXISTS evolution_self_rulings (
    id            INTEGER PRIMARY KEY,
    run_id        INTEGER NOT NULL REFERENCES evolution_self_runs(id),
    ruling        TEXT NOT NULL
        CHECK (ruling IN ('kept', 'rolled_back')),
    kb_hash_after TEXT,
    note          TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
"""

# 范式对比线两表（spec §12.5 / 设计 §6）：线 A 专用，与 B 线表物理隔离——
# 本模块的 B 线边界注释同样适用于这里：绝不写 recommendations / bets 等。
# v4/v5 分层由并行分支合并产生（2026-09-04）：retro 先占 v4，本线抬 v5。
# v7（A_multi 计划 T1）：line 词表加 'A_multi'、加 attributor 列、UNIQUE 扩成
# 三元组——同一场同一 line 的 N 个成员行（1..N）与聚合行（0）共存；CHECK 无法
# 后补，存量库走 _migrate_up 的重建路径（rename-copy-drop，数据保全）。
# v9（A_debate 计划 T1）：line 词表再加 'A_debate' / 'A_division'（二三形态
# 一次到位免 v10 再重建）、加 budget_exhausted 列（A_debate 轮中断审计位）；
# CHECK 又变，存量库二次重建（影子表名换 _v8，_migrate_up v9 块）。
_AL_TABLE = """
CREATE TABLE IF NOT EXISTS agentline_predictions (
    id                INTEGER PRIMARY KEY,
    match_id          INTEGER NOT NULL REFERENCES matches(id),
    line              TEXT NOT NULL
        CHECK (line IN ('A_base', 'A_enh', 'A_multi', 'A_debate', 'A_division')),
    attributor        INTEGER NOT NULL DEFAULT 1,  -- 成员 1..N；聚合行 0（A_multi）
    budget_exhausted  INTEGER NOT NULL DEFAULT 0,
        -- A_debate 轮中断记 1（v9，2026-09-05 设计 §2.4）；其余线恒 0
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
    UNIQUE (match_id, line, attributor)   -- 幂等：一场一line一归因子一行
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

# A_debate 辩论逐轮审计表（v9，2026-09-05 设计 §2.4 DDL 原文）：轮数本身是难
# 归因变量，逐轮全留档（初版 + 修订版各一行）——聚合判据之外可回放整条链。
# UNIQUE(match_id, round, role) = 幂等重跑锚（同 v7 口径）；终版才入
# agentline_predictions（line='A_debate'、attributor=1，链整体为一个归因单元）。
_AL_DEBATE_TABLE = """
CREATE TABLE IF NOT EXISTS agentline_debate_rounds (
    id           INTEGER PRIMARY KEY,
    match_id     INTEGER NOT NULL REFERENCES matches(id),
    round        INTEGER NOT NULL,            -- 0=初版；1..2=修订版
    role         TEXT NOT NULL CHECK (role IN ('generator','critic')),
    payload_json TEXT NOT NULL,               -- 契约字段或攻击字段的规范化 JSON
    raw_output   TEXT NOT NULL,               -- agent 原始返回全文（审计/重放）
    status       TEXT NOT NULL CHECK (status IN ('ok','parse_fail','timeout','error')),
    duration_s   REAL, harness TEXT, model TEXT, created_at TEXT NOT NULL,
    UNIQUE (match_id, round, role)
);
"""

# A_division 三跳产物表（v10，2026-09-05 设计 §3.4 DDL 原文）：archivist /
# predictor / challenger 每跳一行全留档（跳数本身是难归因变量）；终版才入
# agentline_predictions（line='A_division'、attributor=1）。UNIQUE(match_id,
# jump) = 幂等重跑锚（同 debate_rounds 口径）。纯加法——line 词表五词 v9 已
# 备齐，无需重建。
_AL_DIV_TABLE = """
CREATE TABLE IF NOT EXISTS agentline_division_jumps (
    id           INTEGER PRIMARY KEY,
    match_id     INTEGER NOT NULL REFERENCES matches(id),
    jump         INTEGER NOT NULL CHECK (jump IN (1,2,3)),
    role         TEXT NOT NULL CHECK (role IN ('archivist','predictor','challenger')),
    payload_json TEXT NOT NULL,
    raw_output   TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('ok','parse_fail','timeout','error')),
    duration_s   REAL, harness TEXT, model TEXT, created_at TEXT NOT NULL,
    UNIQUE (match_id, jump)
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
""" + _BP_TABLE + _BLINE_TABLE + _RETRO_TABLE + _AL_TABLE + _AL_DEBATE_TABLE \
    + _AL_DIV_TABLE + _EVOLUTION_TABLE + _EVOLUTION_SELF_TABLE


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
        # 表重建类迁移的保守护栏（设计档 §14）：v8 重建 recommendations、v9 重建
        # agentline_predictions，各在升级前落一份 .bak-vN 快照（同名不覆盖，
        # 二次 init 幂等）；老库跨多个重建版本就多备几份，代价可忽略。
        for guard in (8, 9, 11):
            if row["version"] < guard:
                bak = p.with_name(p.name + f".bak-v{guard}")
                if not bak.exists():
                    shutil.copy2(p, bak)
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
    v6->v7 重建 agentline_predictions——line 词表加 'A_multi'、加 attributor 列、
    UNIQUE 扩成三元组：带 CHECK 的列与约束无法 ALTER 后补，唯一路径是
    rename-copy-drop（存量行全列保全、attributor 回填 DEFAULT 1，A_multi 计划
    T1，2026-09-04）。重建各步被 executescript 隐式提交逐个落盘，半途失败留下
    影子表 agentline_predictions_v6 时，重试弃掉半建活动表、从影子重放恢复
    （影子恢复优先于幂等跳过）；
    v7->v8 重建 recommendations——strategy 扩三轨枚举、加 personas_hash 列（M6）；
    evolution 三表就位；init_db 在 v8 升级前自动备份 fa.db.bak-v8；
    v8->v9 二次重建 agentline_predictions——line 词表加 'A_debate'/'A_division'、
    加 budget_exhausted 列（存量行回填 0）；新表 agentline_debate_rounds 就位
    （A_debate 计划 T1，2026-09-05 设计 §2.4/§5）——同 v7 型 rename-copy-drop，
    影子表名换 _v8，影子恢复仍优先于幂等跳过；
    v9->v10 纯加法——新表 agentline_division_jumps（A_division 三跳产物，
    2026-09-05 设计 §3.4）；line 词表五词 v9 已备齐，无需重建，IF NOT EXISTS
    幂等补表；
    v10->v11 重建 recommendations——strategy 扩四轨枚举（加
    model_persona_kb_self）、加 personas_self_hash 列（C' 线版本戳）；
    evolution_self 三表就位（C' 线自反思对照线）；影子表名 _v10，
    影子恢复优先于幂等跳过；init_db 自动备份 .bak-v11。"""
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
        # v7：agentline_predictions 加 attributor + A_multi 词表 + 三元 UNIQUE。
        # CHECK 无法后补——唯一路径是重建（rename-copy-drop）。重建各步经
        # executescript 的隐式提交逐一落盘，半途失败会把存量困在影子表
        # agentline_predictions_v6——所以**影子恢复优先于一切跳过/幂等判定**：
        # 影子在，就弃掉半建的活动表、从影子重放；只有影子不在且 attributor
        # 已在（重建早已完成）才真正跳过。
        has_shadow = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table'"
            " AND name='agentline_predictions_v6'").fetchone() is not None
        acols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(agentline_predictions)")}
        if has_shadow or (acols and "attributor" not in acols):
            # 前置：idx_alp_line 占名——rename 路径里它随旧表改名后继续占住
            # 该名，恢复路径的崩溃态也可能占名；两条路都在建新表前摘掉（活动
            # 表反正要弃）。autoindex 随表走，无需处理。
            conn.execute("DROP INDEX IF EXISTS idx_alp_line")
            if has_shadow:
                # 影子是唯一数据源：窗 A（rename 后新表未建，init 先兜出的
                # 空壳）、窗 B（新表建好、复制未做）、复制半途——半建的活动表
                # 一律弃掉重放，不做任何幂等短路。
                conn.execute("DROP TABLE IF EXISTS agentline_predictions")
            else:
                conn.executescript(
                    "ALTER TABLE agentline_predictions RENAME TO"
                    " agentline_predictions_v6;")
            conn.executescript(_AL_TABLE)       # 新形状（含 attributor）
            conn.execute(
                "INSERT INTO agentline_predictions (match_id, line, p_home,"
                " p_draw, p_away, p_over25, confidence, reasoning_digest,"
                " sources_json, raw_output, status, repaired, harness, model,"
                " duration_s, created_at)"
                " SELECT match_id, line, p_home, p_draw, p_away, p_over25,"
                " confidence, reasoning_digest, sources_json, raw_output,"
                " status, repaired, harness, model, duration_s, created_at"
                " FROM agentline_predictions_v6;")
            # INSERT..SELECT 在外键开启下逐行校验 match_id→matches：真库存量
            # 全经 save_prediction（FK ON）写入、必然有效；真有脏行就在这里
            # fail-fast——影子表原样保留，重试复现同一错误，不丢数据。
            conn.execute("DROP TABLE agentline_predictions_v6;")
    if from_v < 8:
        # v8：recommendations 重建（CHECK 无法后补，rename-copy-drop 唯一路径，
        # v7 agentline 重建同款）：strategy 扩 model_persona_nokb + personas_hash
        # 列。影子恢复优先于一切跳过判定；INSERT..SELECT 全列显式（新列补 NULL）。
        has_shadow = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table'"
            " AND name='recommendations_v7'").fetchone() is not None
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table'"
            " AND name='recommendations'").fetchone()
        stale = ddl is not None and "model_persona_nokb" not in ddl["sql"]
        if has_shadow or stale:
            # 前置：idx_recs_run 占名——rename 后随旧表改名继续占名，建新表前摘掉
            conn.execute("DROP INDEX IF EXISTS idx_recs_run")
            # 与 v7 先例的关键差异：recommendations 有引用方（bets.recommendation_id），
            # agentline_predictions 没有。SQLite ≥3.26 的 RENAME 在 foreign_keys=ON 时
            # **无条件**把引用方的 REFERENCES 改写到 recommendations_v7——实测
            # legacy_alter_table=ON 关不掉；即便 FK=OFF、legacy=OFF 也仍改写，只是
            # DROP 不当场炸，留下悬空引用、此后每笔落注 no such table。唯一干净的
            # 组合是 FK=OFF + legacy_alter_table=ON：bets 引用原封不动、影子表收尾
            # DROP 顺滑。两个 pragma 只圈住这一个 rename，用完即还原（连接级，不
            # 外泄）；foreign_keys 在事务内是静默 no-op，故先落盘保它真生效。
            if conn.in_transaction:
                conn.commit()
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("PRAGMA legacy_alter_table=ON")
            try:
                if has_shadow:
                    conn.execute("DROP TABLE IF EXISTS recommendations")
                else:
                    conn.executescript(
                        "ALTER TABLE recommendations RENAME TO recommendations_v7;")
            finally:
                conn.execute("PRAGMA legacy_alter_table=OFF")
                conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(_BLINE_TABLE)   # 新形状（三轨枚举 + personas_hash）
            # id 显式随行搬：bets.recommendation_id 挂在推荐 id 上，重建若让
            # id 重排，存量注就静默错账（挂到别的推荐上）——这列必须原值保全。
            # FK 已还原为 ON：本句照 v7 先例在外键开启下逐行校验 run_id/fixture_id，
            # 真有脏行就地 fail-fast、影子表原样保留可重试。
            conn.execute(
                "INSERT INTO recommendations (id, run_id, fixture_id, strategy,"
                " market, phase, model_p, market_p, best_odds, bookmaker, edge,"
                " ev, kelly_stake_frac, final_stake_frac, verdict,"
                " confidence_delta, key_factors, report_md, created_at)"
                " SELECT id, run_id, fixture_id, strategy, market, phase, model_p,"
                " market_p, best_odds, bookmaker, edge, ev, kelly_stake_frac,"
                " final_stake_frac, verdict, confidence_delta, key_factors,"
                " report_md, created_at FROM recommendations_v7;")
            conn.execute("DROP TABLE recommendations_v7;")
        conn.executescript(_EVOLUTION_TABLE)
    if from_v < 9:
        # v9：五词表 + budget_exhausted + 新表 debate_rounds（2026-09-05 设计
        # §2.4/§5）。CHECK 又变了，唯一路径仍是重建（v7 同型）；影子表名换
        # _v8。影子恢复优先于幂等跳过。
        conn.executescript(_AL_DEBATE_TABLE)
        has_shadow = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table'"
            " AND name='agentline_predictions_v8'").fetchone() is not None
        acols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(agentline_predictions)")}
        if has_shadow or (acols and "budget_exhausted" not in acols):
            # 前置：idx_alp_line 占名（v7 块同款）——rename 路径里它随旧表改名
            # 后继续占住该名，建新表前摘掉（autoindex 随表走，无需处理）
            conn.execute("DROP INDEX IF EXISTS idx_alp_line")
            if has_shadow:
                # 影子是唯一数据源（v7 块同款）：窗 A 空壳 / 窗 B 半建活动表
                # 一律弃掉重放，不做任何幂等短路
                conn.execute("DROP TABLE IF EXISTS agentline_predictions")
            else:
                conn.executescript(
                    "ALTER TABLE agentline_predictions RENAME TO"
                    " agentline_predictions_v8;")
            conn.executescript(_AL_TABLE)       # 新形状（五词 + budget_exhausted）
            conn.execute(
                "INSERT INTO agentline_predictions (match_id, line, attributor,"
                " budget_exhausted, p_home, p_draw, p_away, p_over25,"
                " confidence, reasoning_digest, sources_json, raw_output,"
                " status, repaired, harness, model, duration_s, created_at)"
                " SELECT match_id, line, attributor, 0, p_home, p_draw,"
                " p_away, p_over25, confidence, reasoning_digest,"
                " sources_json, raw_output, status, repaired, harness,"
                " model, duration_s, created_at"
                " FROM agentline_predictions_v8;")
            # INSERT..SELECT 在外键开启下逐行校验 match_id→matches（v7 先例）：
            # 真库存量全经 save_prediction（FK ON）写入、必然有效；真有脏行就
            # 在这里 fail-fast——影子表原样保留，重试复现同一错误，不丢数据。
            conn.execute("DROP TABLE agentline_predictions_v8;")
    if from_v < 10:
        # v10：新表 division_jumps（2026-09-05 设计 §3.4）。纯加法——词表
        # 五词已在 v9 备齐，无需重建；IF NOT EXISTS 幂等。
        conn.executescript(_AL_DIV_TABLE)
    if from_v < 11:
        # v11：C' 线（自反思知识库对照线）。
        # 1) 重建 recommendations——strategy 扩四轨枚举（加 model_persona_kb_self）、
        #    加 personas_self_hash 列（C' 线版本戳）。
        #    CHECK 无法 ALTER 后补，唯一路径 = rename-copy-drop（v8 先例）。
        # 2) 建 evolution_self_* 三表（纯加法，IF NOT EXISTS 幂等）。
        # 影子表名取 _v10，与 v7→v8 的 _v7、v8→v9 的 _v8 同惯例。
        cur = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table'"
            " AND name='recommendations'")
        ddl = cur.fetchone()
        stale = ddl is not None and "model_persona_kb_self" not in ddl["sql"]
        if stale:
            # 影子恢复优先于幂等跳过：v10 影子在 = 上一次半途失败 = 从影子重放
            shadow = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name='recommendations_v10'").fetchone()
            if shadow is not None:
                # 弃用半建活动表（一定是不完整的）、从影子恢复
                conn.execute("DROP TABLE IF EXISTS recommendations;")
                conn.execute(
                    "ALTER TABLE recommendations_v10 RENAME TO recommendations;")
            # 前置：idx_recs_run 占名（v8 先例）——rename 路径里它随旧表改名
            conn.execute(
                "DROP INDEX IF EXISTS idx_recs_run_v10;")
            conn.execute(
                "ALTER TABLE recommendations RENAME TO recommendations_v10;")
            # 新形状：四轨枚举 + personas_self_hash 列
            conn.executescript(_BLINE_TABLE)
            # 存量行：personas_self_hash 补 NULL（语义 = 「C'线纪元前」）
            conn.execute(
                "INSERT INTO recommendations (id, run_id, fixture_id, strategy,"
                " market, phase, model_p, market_p, best_odds, bookmaker, edge,"
                " ev, kelly_stake_frac, verdict, confidence_delta, final_stake_frac,"
                " key_factors, report_md, personas_hash, personas_self_hash,"
                " created_at)"
                " SELECT id, run_id, fixture_id, strategy, market, phase,"
                " model_p, market_p, best_odds, bookmaker, edge, ev,"
                " kelly_stake_frac, verdict, confidence_delta, final_stake_frac,"
                " key_factors, report_md, personas_hash, NULL, created_at"
                " FROM recommendations_v10;")
            # 重建索引
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_recs_run"
                " ON recommendations (run_id);")
            conn.execute("DROP TABLE recommendations_v10;")
        # C' 线三表（纯加法，IF NOT EXISTS 幂等）
        conn.executescript(_EVOLUTION_SELF_TABLE)
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
