import sqlite3

import pytest

from fa.db import (
    SCHEMA_VERSION,
    _BLINE_TABLE,
    _BP_TABLE,
    _migrate_up,
    connect,
    get_meta,
    init_db,
    set_meta,
)


@pytest.fixture
def conn(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


# B 线五表（schema v3）：表边界见 spec §12.1——B 线独占，绝不写 backtest_predictions
_BLINE_TABLES = ("fixtures", "odds_snapshots", "recommendations", "bets", "runs")
# 范式对比线两表（schema v5；v4 为 retro 线）：线 A 专属，物理隔离分账（spec §12.5）
_AGENTLINE_TABLES = ("agentline_predictions", "agentline_runs")


def _table_cols(c, table: str) -> dict:
    """PRAGMA table_info → {列名: (类型, notnull, pk)}，保持声明顺序。"""
    return {r["name"]: (r["type"], r["notnull"], r["pk"])
            for r in c.execute(f"PRAGMA table_info({table})")}


def _unique_columns(c, table: str) -> set:
    """表上所有 UNIQUE 索引的列序集合（含表级 UNIQUE 约束与唯一 CREATE INDEX）。"""
    out = set()
    for ix in c.execute(f"PRAGMA index_list({table})").fetchall():
        if not ix["unique"]:
            continue
        cols = tuple(r["name"] for r in
                     c.execute(f"PRAGMA index_info({ix['name']})").fetchall())
        out.add(cols)
    return out


def _set_version(path, version: int, drop: tuple = ()) -> None:
    """把库降到指定版本并可选删表——模拟老库（迁移测试的通用前置）。

    drop 逆序执行（子表先删、父表后删），外键开启下不会因引用残留而炸。
    """
    c = connect(path)
    for t in reversed(drop):
        c.execute(f"DROP TABLE IF EXISTS {t}")
    c.execute("UPDATE schema_version SET version=?", (version,))
    c.commit()
    c.close()


def _strip_persona_columns(path) -> None:
    """把库退到 v3 形状：摘掉 v4 新增两列并置版本号=3——模拟 M3 上线库。

    两列是普通可空列（无索引/CHECK/外键引用），SQLite ≥3.35 的
    DROP COLUMN 可直接摘除，摘完 recommendations 与 v3 逐列等价。
    """
    c = connect(path)
    c.execute("ALTER TABLE recommendations DROP COLUMN key_factors")
    c.execute("ALTER TABLE recommendations DROP COLUMN report_md")
    c.execute("UPDATE schema_version SET version=3")
    c.commit()
    c.close()


def test_init_creates_tables(conn):
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"teams", "team_aliases", "unknown_names", "matches", "meta",
            "backtest_predictions", *_BLINE_TABLES} <= names


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


def test_fresh_db_is_current(tmp_path):
    """版本断言钉 SCHEMA_VERSION 而非字面量：schema 每次演进（如 v3→v4）无须改用例。"""
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    assert c.execute(
        "SELECT version FROM schema_version").fetchone()["version"] == SCHEMA_VERSION
    names = {r["name"] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"backtest_predictions", *_BLINE_TABLES} <= names
    cols = _table_cols(c, "recommendations")
    assert cols["key_factors"] == ("TEXT", 0, 0)      # 可空、非主键
    assert cols["report_md"] == ("TEXT", 0, 0)
    order = list(cols)
    # 紧跟 final_stake_frac 之后（brief Step 3 的列位），v3 既有列序不动
    assert order[order.index("final_stake_frac") + 1:order.index("created_at")] == \
        ["key_factors", "report_md"]
    c.close()


def _seed_legacy_rows(c):
    """放老库就有的一批数据，用于验证迁移纯加法、不搬迁不丢数据。"""
    th = c.execute("INSERT INTO teams (league, name) VALUES ('E0','Arsenal')")
    ta = c.execute("INSERT INTO teams (league, name) VALUES ('E0','Chelsea')")
    c.execute(
        "INSERT INTO matches (league, season, date, home_team_id, away_team_id, "
        "raw_line) VALUES ('E0', 2025, '2025-08-16', ?, ?, '{}')",
        (th.lastrowid, ta.lastrowid))
    c.execute("INSERT INTO meta (key, value) VALUES ('last_sync', '2025-08-17')")
    c.execute(
        "INSERT INTO backtest_predictions (league, season, week_index, match_id, "
        "date, p_home, p_draw, p_away, outcome, total_goals) "
        "VALUES ('E0', 2025, 1, 1, '2025-08-16', 0.5, 0.3, 0.2, 'H', 3)")
    return th.lastrowid, ta.lastrowid


def _assert_legacy_rows_intact(c, bp_count: int = 1) -> None:
    """v2 库就有的数据原样保留。bp_count=0 用于 v1 库（该表尚不存在，重建为空）。"""
    assert c.execute("SELECT COUNT(*) c FROM teams").fetchone()["c"] == 2
    m = c.execute(
        "SELECT league, season, date, raw_line FROM matches").fetchone()
    assert (m["league"], m["season"], m["date"], m["raw_line"]) == \
        ('E0', 2025, '2025-08-16', '{}')
    assert c.execute(
        "SELECT value FROM meta WHERE key='last_sync'").fetchone()["value"] \
        == '2025-08-17'
    assert c.execute(
        "SELECT COUNT(*) c FROM backtest_predictions").fetchone()["c"] == bp_count
    if bp_count:
        bp = c.execute("SELECT * FROM backtest_predictions").fetchone()
        assert (bp["league"], bp["season"], bp["outcome"],
                bp["total_goals"]) == ('E0', 2025, 'H', 3)


def test_v2_upgrades_to_current(tmp_path):
    """v2→当前：纯加法。B 线五表与 retro/agentline 各两表新出现且为空，既有表与数据一字不动。"""
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    _seed_legacy_rows(c)
    c.commit(); c.close()
    _set_version(tmp_path / "t.db", 2, drop=_BLINE_TABLES)   # 模拟 v2 老库
    init_db(tmp_path / "t.db")                               # 不抛异常即升级成功

    c = connect(tmp_path / "t.db")
    assert c.execute(
        "SELECT version FROM schema_version").fetchone()["version"] == SCHEMA_VERSION
    for t in (*_BLINE_TABLES, "retro_runs", "retro_attributions", *_AGENTLINE_TABLES):
        assert c.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"] == 0
    _assert_legacy_rows_intact(c)
    assert {"key_factors", "report_md"} <= set(_table_cols(c, "recommendations"))
    c.close()


def test_v1_upgrades_to_current(tmp_path):
    """v1→当前 跨级升级：backtest_predictions 与 B 线五表一并补齐。"""
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    _seed_legacy_rows(c)
    c.commit(); c.close()
    _set_version(tmp_path / "t.db", 1,
                 drop=("backtest_predictions", *_BLINE_TABLES,
                       "retro_runs", "retro_attributions", *_AGENTLINE_TABLES))
    init_db(tmp_path / "t.db")

    c = connect(tmp_path / "t.db")
    assert c.execute(
        "SELECT version FROM schema_version").fetchone()["version"] == SCHEMA_VERSION
    assert c.execute(
        "SELECT COUNT(*) c FROM backtest_predictions").fetchone()["c"] == 0
    for t in (*_BLINE_TABLES, "retro_runs", "retro_attributions",
              *_AGENTLINE_TABLES):
        assert c.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"] == 0
    _assert_legacy_rows_intact(c, bp_count=0)
    assert {"key_factors", "report_md"} <= set(_table_cols(c, "recommendations"))
    c.close()


# 手工造的 v1 库：只含 v1 就有的表（形状最小化）。迁移是纯加法，老表形状与本组
# 测试无关——关键是这些表**不经 _SCHEMA** 就存在，使 _migrate_up 的各级分支真正
# 可观测（init_db 会先跑 _SCHEMA，把缺口兜掉，单看 init_db 测不出迁移分支死活）。
_V1_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY, league TEXT NOT NULL, name TEXT NOT NULL,
    UNIQUE (league, name));
CREATE TABLE IF NOT EXISTS team_aliases (
    team_id INTEGER NOT NULL REFERENCES teams(id), source TEXT NOT NULL,
    alias TEXT NOT NULL, UNIQUE (source, alias));
CREATE TABLE IF NOT EXISTS unknown_names (
    source TEXT NOT NULL, name TEXT NOT NULL, first_seen TEXT NOT NULL,
    PRIMARY KEY (source, name));
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY, league TEXT NOT NULL, season INTEGER NOT NULL,
    date TEXT NOT NULL, home_team_id INTEGER NOT NULL REFERENCES teams(id),
    away_team_id INTEGER NOT NULL REFERENCES teams(id), raw_line TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _legacy_db(path, version: int, with_bp: bool) -> None:
    c = sqlite3.connect(path)
    c.executescript(_V1_DDL)
    if with_bp:
        c.executescript(_BP_TABLE)
    c.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
    c.commit()
    c.close()


def test_bline_vocab_check_constraints(conn, bline_ids):
    """词表 CHECK（§12.2 硬性质 + §6.6/§9.6 语义）：违例插入必被拒。"""
    run_id, fx_id = bline_ids

    def rec(**over):
        cols = {"run_id": run_id, "fixture_id": fx_id,
                "strategy": "model_only", "market": "H", "phase": "am",
                "model_p": 0.5, "market_p": 0.45, "best_odds": 2.0,
                "bookmaker": "Pinnacle", "edge": 0.05, "ev": 0.1,
                "kelly_stake_frac": 0.01, "created_at": "2026-09-03T11:00:00Z"}
        cols.update(over)
        sql = "INSERT INTO recommendations ({}) VALUES ({})".format(
            ", ".join(cols), ", ".join("?" * len(cols)))
        return conn.execute(sql, tuple(cols.values()))

    # recommendations 三个词表列
    for bad in ({"strategy": "model_personae"}, {"strategy": ""},
                {"market": "O1.5"}, {"market": "h2h"},
                {"phase": "AM"}, {"phase": "pm "}):
        with pytest.raises(sqlite3.IntegrityError):
            rec(**bad)
    # bets 两个词表列
    rid = rec().lastrowid
    for mode, status in (("simulated", "pending"), ("", "won"),
                         ("paper", "push"), ("paper", "PENDING")):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker, "
                "odds_taken, stake, status) VALUES (?, ?, ?, 'Pinnacle', 2.0, "
                "10.0, ?)", (rid, mode, "2026-09-03T11:00:05Z", status))
    # runs.type：'matchday_am' 是 T9 最易误写的值——am/pm 归 phase，不拆 _am/_pm
    for bad in ("matchday_am", "matchday_pm", "daily_pm", ""):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO runs (type, started_at, status) VALUES (?, ?, 'ok')",
                (bad, "2026-09-03T11:00:00Z"))
    conn.rollback()


def test_bline_vocab_accepts_all_legal_values(conn, bline_ids):
    """合法词表全部可写入——CHECK 不得误伤。"""
    run_id, fx_id = bline_ids
    # recommendations：2 strategy × 4 market × 2 phase = 16 行，UNIQUE 不撞
    for strategy in ("model_only", "model_persona"):
        for market in ("H", "D", "A", "O2.5"):
            for phase in ("am", "pm"):
                conn.execute(
                    "INSERT INTO recommendations (run_id, fixture_id, strategy, "
                    "market, phase, model_p, market_p, best_odds, bookmaker, "
                    "edge, ev, kelly_stake_frac, created_at) VALUES "
                    "(?, ?, ?, ?, ?, 0.5, 0.45, 2.0, 'Pinnacle', 0.05, 0.1, "
                    "0.01, '2026-09-03T11:00:00Z')",
                    (run_id, fx_id, strategy, market, phase))
    assert conn.execute(
        "SELECT COUNT(*) c FROM recommendations").fetchone()["c"] == 16

    # runs：4 种 type；matchday 带 phase，其余不带
    for t in ("daily", "matchday", "backtest", "manual"):
        conn.execute(
            "INSERT INTO runs (type, phase, started_at, status) VALUES (?, ?, "
            "'2026-09-03T11:00:00Z', 'ok')",
            (t, "am" if t == "matchday" else None))
    assert conn.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"] == 5

    # bets：4 条 rec × 2 mode = 8 注，覆盖全部 4 种 status
    # （用第二场 fixture，避开上面 16 行已占满的 UNIQUE 四元组）
    fx2 = conn.execute(
        "INSERT INTO fixtures (league, event_key, source, kickoff_utc, status, "
        "created_at) VALUES ('E0', 'ev-2', 'oddsapi', '2026-09-05T14:00:00Z', "
        "'scheduled', '2026-09-03T11:00:00Z')").lastrowid
    statuses = ("pending", "won", "lost", "void")
    for i, market in enumerate(("H", "D", "A", "O2.5")):
        r = conn.execute(
            "INSERT INTO recommendations (run_id, fixture_id, strategy, market, "
            "phase, model_p, market_p, best_odds, bookmaker, edge, ev, "
            "kelly_stake_frac, created_at) VALUES "
            "(?, ?, 'model_only', ?, 'am', 0.5, 0.45, 2.0, 'Pinnacle', 0.05, "
            "0.1, 0.01, '2026-09-03T11:00:00Z')", (run_id, fx2, market))
        for mode in ("paper", "live"):
            conn.execute(
                "INSERT INTO bets (recommendation_id, mode, placed_at, "
                "bookmaker, odds_taken, stake, status) VALUES "
                "(?, ?, '2026-09-03T11:00:05Z', 'Pinnacle', 2.0, 10.0, ?)",
                (r.lastrowid, mode, statuses[i]))
    assert conn.execute("SELECT COUNT(*) c FROM bets").fetchone()["c"] == 8
    assert conn.execute(
        "SELECT COUNT(DISTINCT mode) c FROM bets").fetchone()["c"] == 2
    assert conn.execute(
        "SELECT COUNT(DISTINCT status) c FROM bets").fetchone()["c"] == 4
    # fixtures.status 词表未约束——任意标记都收，由 T5/T7 演进
    conn.execute(
        "INSERT INTO fixtures (league, event_key, source, kickoff_utc, status, "
        "created_at) VALUES ('E0', 'ev-x', 'oddsapi', '2026-09-04T14:00:00Z', "
        "'some-future-state', '2026-09-03T11:00:00Z')")


def test_migrate_up_v1_adds_everything(tmp_path):
    """_migrate_up 单独跑就能把 v1 库补齐到当前版本（不依赖 _SCHEMA 兜底）。"""


    """_migrate_up 单独跑就能把 v1 库补齐到 v4（不依赖 _SCHEMA 兜底）。"""
    p = tmp_path / "v1.db"
    _legacy_db(p, version=1, with_bp=False)
    c = connect(p)
    _migrate_up(c, 1)
    names = {r["name"] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"backtest_predictions", *_BLINE_TABLES} <= names
    assert {"key_factors", "report_md"} <= set(_table_cols(c, "recommendations"))
    assert c.execute(
        "SELECT version FROM schema_version").fetchone()["version"] == SCHEMA_VERSION
    c.close()


def test_migrate_up_v2_adds_bline_retro_and_persona(tmp_path):
    """v2 库走 _migrate_up 补 B 线五表、retro 两表与 persona 两列，不动 backtest_predictions。"""
    p = tmp_path / "v2.db"
    _legacy_db(p, version=2, with_bp=True)
    c = connect(p)
    bp_before = c.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='backtest_predictions'").fetchone()["sql"]
    _migrate_up(c, 2)
    names = {r["name"] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(_BLINE_TABLES) | {"retro_runs", "retro_attributions"} <= names
    assert c.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='backtest_predictions'").fetchone()["sql"] == bp_before
    assert {"key_factors", "report_md"} <= set(_table_cols(c, "recommendations"))
    assert c.execute(
        "SELECT version FROM schema_version").fetchone()["version"] == SCHEMA_VERSION
    c.close()


@pytest.mark.parametrize("from_v,drop", [
    (1, ("backtest_predictions", *_BLINE_TABLES)),   # v1 跨级升级
    (3, ()),                                         # v3 就地升级（M3 真库路径）
], ids=["from_v1", "from_v3"])
def test_migrate_and_fresh_schemas_match(tmp_path, from_v, drop):
    """新建与迁移两条路径产出的表形状必须一致（M2 教训的推广）。

    from_v=3 腿先摘掉 v4 两列再升，等价于真实 M3 库的 v3 形状。
    """
    init_db(tmp_path / "a.db")                      # 全新 v4
    init_db(tmp_path / "b.db")                      # 降到 from_v 再升级回 v4
    if from_v == 3:
        _strip_persona_columns(tmp_path / "b.db")
    _set_version(tmp_path / "b.db", from_v, drop=drop)
    init_db(tmp_path / "b.db")

    tables = ("backtest_predictions", *_BLINE_TABLES,
              "retro_runs", "retro_attributions", *_AGENTLINE_TABLES)
    a = connect(tmp_path / "a.db")
    b = connect(tmp_path / "b.db")
    for t in tables:
        # 列级形状（名/类型/非空/主键）逐列一致
        assert _table_cols(a, t) == _table_cols(b, t), t
        # 唯一约束同样一致（recommendations 的四元组 UNIQUE 不因迁移丢失）
        assert _unique_columns(a, t) == _unique_columns(b, t), t
        # 索引（含 UNIQUE 自动索引之外的命名索引）也须一致
        sql = "SELECT name, sql FROM sqlite_master " \
              "WHERE type='index' AND tbl_name=? AND sql IS NOT NULL ORDER BY name"
        assert a.execute(sql, (t,)).fetchall() == \
            b.execute(sql, (t,)).fetchall(), t
        # DDL 文本逐字一致。from_v1 腿的表全部由常量 executescript 建出，无列级
        # ALTER，恒与新建一致；from_v3 腿的 ALTER 补列表（recommendations 补
        # persona 两列、matches 补 BFE 四列、bets 补 closing_source——列序缀尾）
        # DDL 文本必异，跳过逐字比较：其形状一致性由上面的 _table_cols（dict
        # 相等无视列序）+ _unique_columns + 独立的
        # test_v3_shape_matches_fresh_on_persona_columns 无序比对覆盖。
        if not (from_v == 3 and t in ("recommendations", "matches", "bets")):
            sql = "SELECT sql FROM sqlite_master WHERE type='table' AND name=?"
            assert a.execute(sql, (t,)).fetchone()["sql"] == \
                b.execute(sql, (t,)).fetchone()["sql"], t
    a.close(); b.close()


# ---------------------------------------------------------------------------
# B 线五表 DDL 语义（brief 逐条）
# ---------------------------------------------------------------------------

@pytest.fixture
def bline_ids(conn):
    """一条合法 run + fixture 的 id，供词表 CHECK 用例做合法父引用。"""
    run = conn.execute(
        "INSERT INTO runs (type, phase, started_at, status) VALUES "
        "('matchday', 'am', '2026-09-03T11:00:00Z', 'ok')")
    fx = conn.execute(
        "INSERT INTO fixtures (league, event_key, source, kickoff_utc, status, "
        "created_at) VALUES ('E0', 'ev-1', 'oddsapi', '2026-09-04T14:00:00Z', "
        "'scheduled', '2026-09-03T11:00:00Z')")
    conn.commit()
    return run.lastrowid, fx.lastrowid


def test_backtest_predictions_schema_unchanged(conn):
    """B 线边界烟测：A 线独占表的列集/约束在历次 schema 升级下不得有任何变动。"""
    cols = _table_cols(conn, "backtest_predictions")
    assert list(cols) == ["id", "league", "season", "week_index", "match_id",
                          "date", "p_home", "p_draw", "p_away", "p_over25",
                          "p_under25", "p_btts", "mkt_home", "mkt_draw",
                          "mkt_away", "mkt_over25", "odds_home", "odds_draw",
                          "odds_away", "outcome", "total_goals"]
    assert cols["id"] == ("INTEGER", 0, 1)          # 单列整型主键
    notnull = {n for n, (_, nn, _) in cols.items() if nn}
    assert notnull == {"league", "season", "week_index", "match_id", "date",
                       "p_home", "p_draw", "p_away", "outcome", "total_goals"}
    assert "UNIQUE (match_id)" in conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='backtest_predictions'").fetchone()["sql"]


def test_bline_unique_constraints(conn):
    """三处去重键（brief）：fixtures.event_key、recommendations 四元组、bets 二元组。"""
    assert ("event_key",) in _unique_columns(conn, "fixtures")
    assert ("fixture_id", "market", "strategy", "phase") in \
        _unique_columns(conn, "recommendations")
    # 注级跨 run 去重（fixture+market+strategy+mode）是逻辑层（T7）的事，
    # DDL 只钉 (recommendation_id, mode)。
    assert ("recommendation_id", "mode") in _unique_columns(conn, "bets")


def test_bline_nullability(conn):
    """M4 才填的位与可空外键必须可空；业务键必须非空。"""
    nullable = {
        "fixtures": ("home_team_id", "away_team_id"),
        "odds_snapshots": ("fixture_id",),
        "recommendations": ("verdict", "confidence_delta", "final_stake_frac",
                            "key_factors", "report_md"),
        "bets": ("settled_at", "return_amt", "closing_odds", "clv"),
        "runs": ("phase", "finished_at", "credits_before", "credits_after",
                 "summary"),
    }
    for t, cols in nullable.items():
        info = _table_cols(conn, t)
        for n in cols:
            assert n in info, f"{t}.{n} 缺列"
            assert info[n][1] == 0, f"{t}.{n} 应可空"

    required = {
        "fixtures": ("league", "event_key", "source", "kickoff_utc", "status",
                     "created_at"),
        "odds_snapshots": ("fetched_at", "event_key", "market", "region",
                           "bookmaker", "outcomes", "raw"),
        "recommendations": ("run_id", "fixture_id", "strategy", "market",
                            "phase", "model_p", "market_p", "best_odds",
                            "bookmaker", "edge", "ev", "kelly_stake_frac",
                            "created_at"),
        "bets": ("recommendation_id", "mode", "placed_at", "bookmaker",
                 "odds_taken", "stake", "status"),
        "runs": ("type", "started_at", "status"),
    }
    for t, cols in required.items():
        info = _table_cols(conn, t)
        for n in cols:
            assert info[n][1] == 1, f"{t}.{n} 应 NOT NULL"


def test_bline_fk_chain_roundtrip(conn):
    """runs → fixtures → odds_snapshots / recommendations → bets 外键链可用，
    且坏引用被外键开启时的连接拒绝。"""
    run = conn.execute(
        "INSERT INTO runs (type, phase, started_at, status, credits_before, "
        "credits_after, summary) VALUES "
        "('matchday', 'am', '2026-09-03T11:00:00Z', 'ok', 500, 490, '{}')")
    fx = conn.execute(
        "INSERT INTO fixtures (league, event_key, source, kickoff_utc, "
        "home_team_id, away_team_id, status, created_at) VALUES "
        "('E0', 'ev-1', 'oddsapi', '2026-09-04T14:00:00Z', NULL, NULL, "
        "'scheduled', '2026-09-03T11:00:00Z')")
    conn.execute(
        "INSERT INTO odds_snapshots (fetched_at, fixture_id, event_key, market, "
        "region, bookmaker, outcomes, raw) VALUES "
        "('2026-09-03T11:00:00Z', ?, 'ev-1', 'h2h', 'eu', 'Pinnacle', "
        "'{\"home\":2.1}', '{}')", (fx.lastrowid,))
    rec = conn.execute(
        "INSERT INTO recommendations (run_id, fixture_id, strategy, market, "
        "phase, model_p, market_p, best_odds, bookmaker, edge, ev, "
        "kelly_stake_frac, verdict, confidence_delta, final_stake_frac, "
        "created_at) VALUES (?, ?, 'model_only', 'H', 'am', 0.55, 0.50, 2.10, "
        "'Pinnacle', 0.05, 0.155, 0.01, NULL, NULL, NULL, "
        "'2026-09-03T11:00:00Z')", (run.lastrowid, fx.lastrowid))
    conn.execute(
        "INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker, "
        "odds_taken, stake, status) VALUES "
        "(?, 'paper', '2026-09-03T11:00:05Z', 'Pinnacle', 2.10, 10.0, 'pending')",
        (rec.lastrowid,))
    conn.commit()

    assert conn.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"] == 1
    assert conn.execute(
        "SELECT COUNT(*) c FROM odds_snapshots").fetchone()["c"] == 1
    assert conn.execute(
        "SELECT COUNT(*) c FROM recommendations").fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM bets").fetchone()["c"] == 1

    # 坏 run_id 被外键拒绝（connect 已 PRAGMA foreign_keys=ON）
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO recommendations (run_id, fixture_id, strategy, market, "
            "phase, model_p, market_p, best_odds, bookmaker, edge, ev, "
            "kelly_stake_frac, created_at) VALUES "
            "(999999, ?, 'model_only', 'A', 'am', 0.4, 0.3, 3.0, 'x', 0.1, "
            "0.3, 0.01, '2026-09-03T11:00:00Z')", (fx.lastrowid,))

    # bets 的 (recommendation_id, mode) 唯一键生效
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker, "
            "odds_taken, stake, status) VALUES "
            "(?, 'paper', '2026-09-03T11:10:00Z', 'Pinnacle', 2.10, 10.0, "
            "'pending')", (rec.lastrowid,))
    conn.rollback()


def test_future_version_refused(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    c.execute("UPDATE schema_version SET version=99")
    c.commit(); c.close()
    import pytest
    with pytest.raises(RuntimeError):
        init_db(tmp_path / "t.db")


# ---------------------------------------------------------------------------
# schema v4：A 线复盘归因两表（retro_runs / retro_attributions）
# ---------------------------------------------------------------------------

def test_v4_creates_retro_tables(tmp_path):
    """v4 新建库即含 retro 两表；词表 CHECK 一次到位（SQLite 无法 ALTER 补 CHECK）。"""
    from fa.db import connect, init_db
    db = tmp_path / "v4.db"
    init_db(db)
    conn = connect(db)
    try:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"retro_runs", "retro_attributions"} <= tables
        # 词表钉死：非法 selector / status / model_vs_market 须被 CHECK 拒绝
        # （CHECK 在 execute 即抛、非 commit——与上方 B 线词表用例同款写法）
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO retro_runs (selector, params_json, n_selected, n_ok,"
                " n_parse_fail, n_timeout, n_error, duration_s, created_at)"
                " VALUES ('bogus', '{}', 0, 0, 0, 0, 0, 0.0, '2026-09-04T00:00:00Z')")
        # retro_attributions 外键开启，负例须先备好合法父行——否则 IntegrityError
        # 来自外键而非 CHECK，负例就测空了
        team = conn.execute(
            "INSERT INTO teams (league, name) VALUES ('E0','Arsenal')")
        match_id = conn.execute(
            "INSERT INTO matches (league, season, date, home_team_id, away_team_id,"
            " raw_line) VALUES ('E0', 2025, '2025-08-16', ?, ?, '{}')",
            (team.lastrowid, team.lastrowid)).lastrowid
        batch = conn.execute(
            "INSERT INTO retro_runs (selector, params_json, n_selected, n_ok,"
            " n_parse_fail, n_timeout, n_error, duration_s, created_at)"
            " VALUES ('divergence', '{}', 1, 0, 0, 0, 0, 1.0,"
            " '2026-09-04T00:00:00Z')").lastrowid

        def attr(status="ok", model_vs_market=None):
            conn.execute(
                "INSERT INTO retro_attributions (batch_id, match_id, league, season,"
                " date, selector, status, model_vs_market, harness, input_pack_path,"
                " tag_set_version, created_at) VALUES "
                "(?, ?, 'E0', 2025, '2025-08-16', 'divergence', ?, ?, 'hermes',"
                " 'packs/x.json', 'v1', '2026-09-04T00:00:00Z')",
                (batch, match_id, status, model_vs_market))

        with pytest.raises(sqlite3.IntegrityError):
            attr(status="bogus")
        with pytest.raises(sqlite3.IntegrityError):
            attr(model_vs_market="bogus")
        attr()                      # 合法组合不得被 CHECK 误伤
        conn.rollback()
    finally:
        conn.close()


def test_is_control_column_pinned(tmp_path):
    """is_control 后补列（2026-09-04 终审）：病例=0/对照=1。钉死两件事——
    建表即含列、NOT NULL DEFAULT 0（直插不给值不炸且落 0）。
    原「SCHEMA_VERSION 保持 4」断言在 agentline 分支抬 v5（2026-09-04 并行
    合并）后不再成立：retro 的 is_control 加列本身仍不 bump，但库版本随
    v5 整体上移，故此断言改为不依赖具体版本值。"""
    from fa.db import SCHEMA_VERSION, connect, init_db
    assert SCHEMA_VERSION >= 4
    db = tmp_path / "ic.db"
    init_db(db)
    conn = connect(db)
    try:
        cols = {r["name"]: r for r in conn.execute(
            "PRAGMA table_info(retro_attributions)")}
        assert "is_control" in cols
        assert cols["is_control"]["notnull"] == 1
        assert cols["is_control"]["dflt_value"] == "0"
        team = conn.execute(
            "INSERT INTO teams (league, name) VALUES ('E0','Arsenal')")
        match_id = conn.execute(
            "INSERT INTO matches (league, season, date, home_team_id,"
            " away_team_id, raw_line) VALUES ('E0', 2025, '2025-08-16', ?, ?, '{}')",
            (team.lastrowid, team.lastrowid)).lastrowid
        batch = conn.execute(
            "INSERT INTO retro_runs (selector, params_json, n_selected, n_ok,"
            " n_parse_fail, n_timeout, n_error, duration_s, created_at)"
            " VALUES ('divergence', '{}', 1, 0, 0, 0, 0, 0.0,"
            " '2026-09-04T00:00:00Z')").lastrowid
        conn.execute(
            "INSERT INTO retro_attributions (batch_id, match_id, league,"
            " season, date, selector, is_control, status, harness,"
            " input_pack_path, tag_set_version, created_at)"
            " VALUES (?, ?, 'E0', 2025, '2025-08-16', 'divergence', 1, 'ok',"
            " 'hermes', 'p.json', 'v1', '2026-09-04T00:00:00Z')",
            (batch, match_id))
        conn.commit()
        assert conn.execute("SELECT is_control FROM retro_attributions"
                            ).fetchone()["is_control"] == 1
        conn.execute("DELETE FROM retro_attributions")
        conn.execute(
            "INSERT INTO retro_attributions (batch_id, match_id, league,"
            " season, date, selector, status, harness, input_pack_path,"
            " tag_set_version, created_at)"
            " VALUES (?, ?, 'E0', 2025, '2025-08-16', 'manual', 'ok',"
            " 'hermes', 'p.json', 'v1', '2026-09-04T00:00:00Z')",
            (batch, match_id))
        conn.commit()
        assert conn.execute("SELECT is_control FROM retro_attributions"
                            ).fetchone()["is_control"] == 0
    finally:
        conn.close()


def test_v3_migrates_to_current(tmp_path):
    """老库（v3）经 init_db 升级到当前版本，retro 表出现且 version==SCHEMA_VERSION
    （v4=retro 两表、v5=agentline 两表+retro attributor 列，见 _migrate_up docstring）。"""
    from fa.db import SCHEMA_VERSION, connect, init_db
    db = tmp_path / "old.db"
    init_db(db)
    conn = connect(db)
    conn.execute("UPDATE schema_version SET version=3")
    conn.execute("DROP TABLE retro_runs")
    conn.execute("DROP TABLE retro_attributions")
    conn.commit()
    conn.close()
    init_db(db)                      # 触发 _migrate_up(3 -> 5)
    conn = connect(db)
    try:
        assert conn.execute(
            "SELECT version FROM schema_version").fetchone()["version"] == SCHEMA_VERSION
        # v4=retro 层已并入（agentline 分支抬 v5），钉下限而非具体值
        assert SCHEMA_VERSION >= 4
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"retro_runs", "retro_attributions"} <= names
    finally:
        conn.close()


def test_migrate_up_v3_adds_retro(tmp_path):
    """_migrate_up 单独跑就能给 v3 库补上 retro 两表（不经 _SCHEMA 兜底）——
    init_db 会先跑 _SCHEMA 把缺口兜掉，单看 init_db 测不出迁移分支死活。"""
    p = tmp_path / "v3.db"
    _legacy_db(p, version=3, with_bp=True)
    c = connect(p)
    c.executescript(_BLINE_TABLE)
    c.commit()
    _migrate_up(c, 3)
    names = {r["name"] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"retro_runs", "retro_attributions"} <= names
    assert c.execute(
        "SELECT version FROM schema_version").fetchone()["version"] == SCHEMA_VERSION
    c.close()


# ---------------------------------------------------------------------------
# schema v5：ensemble 成员归因（retro_attributions.attributor 列）
# ---------------------------------------------------------------------------

def test_v5_attributor_column_defaults_and_values(tmp_path):
    """v5：attributor 列存在、DEFAULT 1、显式 0（聚合行）可写。"""
    from fa.db import connect, init_db
    db = tmp_path / "v5.db"
    init_db(db)
    conn = connect(db)
    try:
        info = conn.execute("PRAGMA table_info(retro_attributions)").fetchall()
        col = [c for c in info if c["name"] == "attributor"]
        assert col and col[0]["notnull"] == 1 and col[0]["dflt_value"] == "1"
        conn.execute(
            "INSERT INTO retro_runs (selector, params_json, n_selected, n_ok,"
            " n_parse_fail, n_timeout, n_error, duration_s, created_at)"
            " VALUES ('manual', '{}', 1, 1, 0, 0, 0, 0.1, '2026-09-04T00:00:00Z')")
        rid = conn.execute("SELECT id FROM retro_runs").fetchone()["id"]
        # FK 开启（connect 内 PRAGMA foreign_keys=ON）：先备好父行，matches
        # 的 home/away_team_id 引用才合法（落库后恰为 id 1、2）
        conn.execute("INSERT INTO teams (league, name) VALUES ('E0','Arsenal')")
        conn.execute("INSERT INTO teams (league, name) VALUES ('E0','Chelsea')")
        conn.execute("INSERT INTO matches (id, league, season, date,"
            " home_team_id, away_team_id, raw_line)"
            " VALUES (1, 'E0', 2023, '2024-04-20', 1, 2, '{}')")
        for v in (0, 1, 3):                      # 聚合 0 / 成员 1 / 成员 3
            conn.execute(
                "INSERT INTO retro_attributions (batch_id, match_id, league,"
                " season, date, selector, status, harness, input_pack_path,"
                " tag_set_version, created_at, attributor)"
                " VALUES (?, 1, 'E0', 2023, '2024-04-20', 'manual', 'ok',"
                " 'hermes', 'p.json', 'v1', '2026-09-04T00:00:00Z', ?)",
                (rid, v))
        conn.commit()
        vals = sorted(r["attributor"] for r in conn.execute(
            "SELECT attributor FROM retro_attributions"))
        assert vals == [0, 1, 3]
    finally:
        conn.close()


def test_v4_migrates_to_v5(tmp_path):
    """v4 库（无 attributor 列）经 init_db ALTER 升级（v6 链下升到当前版），
    存量行回填 1。"""
    from fa.db import connect, init_db
    db = tmp_path / "v4.db"
    init_db(db)                                   # v5 新建
    conn = connect(db)
    conn.execute("ALTER TABLE retro_attributions DROP COLUMN attributor")
    conn.execute("UPDATE schema_version SET version=4")
    conn.execute(
        "INSERT INTO retro_runs (selector, params_json, n_selected, n_ok,"
        " n_parse_fail, n_timeout, n_error, duration_s, created_at)"
        " VALUES ('manual', '{}', 0, 0, 0, 0, 0, 0.0, '2026-09-04T00:00:00Z')")
    # 放一条存量归因行（列已 DROP，只能按 v4 形状插）——验证 ALTER 的
    # DEFAULT 1 真把它回填成单成员语义
    conn.execute("INSERT INTO teams (league, name) VALUES ('E0','Arsenal')")
    conn.execute("INSERT INTO teams (league, name) VALUES ('E0','Chelsea')")
    conn.execute("INSERT INTO matches (id, league, season, date,"
        " home_team_id, away_team_id, raw_line)"
        " VALUES (1, 'E0', 2023, '2024-04-20', 1, 2, '{}')")
    conn.execute(
        "INSERT INTO retro_attributions (batch_id, match_id, league,"
        " season, date, selector, status, harness, input_pack_path,"
        " tag_set_version, created_at)"
        " VALUES (1, 1, 'E0', 2023, '2024-04-20', 'manual', 'ok',"
        " 'hermes', 'p.json', 'v1', '2026-09-04T00:00:00Z')")
    conn.commit()
    conn.close()
    init_db(db)                                   # v4 -> v5
    conn = connect(db)
    try:
        assert conn.execute("SELECT version FROM schema_version"
                            ).fetchone()["version"] == SCHEMA_VERSION
        cols = {c["name"] for c in conn.execute(
            "PRAGMA table_info(retro_attributions)")}
        assert "attributor" in cols
        assert conn.execute("SELECT attributor FROM retro_attributions"
                            ).fetchone()["attributor"] == 1
    finally:
        conn.close()


# ------------------------------------------------- v5：BFE 收盘列 + closing_source
# Pinnacle 断供应对（2026-09-04 裁定 Betfair 交易所为 fallback 基准，spec §7.3）

_V4_MATCHES = """
CREATE TABLE matches (
    id INTEGER PRIMARY KEY,
    league TEXT NOT NULL,
    season INTEGER NOT NULL,
    date TEXT NOT NULL,
    home_team_id INTEGER NOT NULL REFERENCES teams(id),
    away_team_id INTEGER NOT NULL REFERENCES teams(id),
    fthg INTEGER, ftag INTEGER,
    psc_home REAL, psc_draw REAL, psc_away REAL,
    over25_psc REAL, under25_psc REAL, raw_line TEXT,
    UNIQUE (league, season, date, home_team_id, away_team_id)
);
"""

_V4_BETS = """
CREATE TABLE bets (
    id INTEGER PRIMARY KEY,
    recommendation_id INTEGER NOT NULL REFERENCES recommendations(id),
    mode TEXT NOT NULL CHECK (mode IN ('paper','live')),
    placed_at TEXT NOT NULL,
    bookmaker TEXT NOT NULL,
    odds_taken REAL NOT NULL,
    stake REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','won','lost','void')),
    settled_at TEXT, return_amt REAL, closing_odds REAL, clv REAL,
    UNIQUE (recommendation_id, mode)
);
"""


def _v4_db(path):
    """v4 形状老库：matches/bets 无 BFE 列与 closing_source，带一行种子。"""
    init_db(path)
    c = connect(path)
    c.execute("DROP TABLE matches")
    c.execute("DROP TABLE bets")
    c.executescript(_V4_MATCHES)
    c.executescript(_V4_BETS)
    c.execute("INSERT INTO teams (league, name) VALUES ('E0', 'Chelsea')")
    tid = c.execute("SELECT id FROM teams").fetchone()["id"]
    c.execute("INSERT INTO matches (league, season, date, home_team_id,"
              " away_team_id, fthg, ftag, psc_home)"
              " VALUES ('E0', 2026, '2026-09-03', ?, ?, 2, 1, 1.6)", (tid, tid))
    c.execute("UPDATE schema_version SET version=4")
    c.commit()
    c.close()


def test_v4_migrates_to_v6_adds_bfe_and_closing_source(tmp_path):
    db = tmp_path / "v4.db"
    _v4_db(db)
    init_db(db)                     # 触发 _migrate_up(4 -> 5)
    c = connect(db)
    try:
        assert c.execute(
            "SELECT version FROM schema_version").fetchone()["version"] == SCHEMA_VERSION
        mcols = {r["name"] for r in c.execute("PRAGMA table_info(matches)")}
        assert {"bfe_home", "bfe_draw", "bfe_away", "over25_bfe"} <= mcols
        bcols = {r["name"] for r in c.execute("PRAGMA table_info(bets)")}
        assert "closing_source" in bcols
        # 数据保留：既有 psc 不动、bfe 为 NULL
        row = c.execute("SELECT psc_home, bfe_home, fthg FROM matches").fetchone()
        assert (row["psc_home"], row["bfe_home"], row["fthg"]) == (1.6, None, 2)
    finally:
        c.close()


def test_fresh_db_has_bfe_columns_at_v6(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    c = connect(db)
    try:
        assert c.execute(
            "SELECT version FROM schema_version").fetchone()["version"] == SCHEMA_VERSION
        mcols = {r["name"] for r in c.execute("PRAGMA table_info(matches)")}
        assert {"bfe_home", "bfe_draw", "bfe_away", "over25_bfe"} <= mcols
        assert "closing_source" in {
            r["name"] for r in c.execute("PRAGMA table_info(bets)")}
    finally:
        c.close()
