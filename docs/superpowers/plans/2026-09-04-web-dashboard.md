# fa 本地只读看板（dashboard）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 fa 建一个 Streamlit 本地只读看板：B 线运营监控 4 页 + A 线研究可视化 3 页，按 spec §12 双线分账分区。

**Architecture:** `dashboard/queries.py` 纯函数查询层（只读 SQLite + 复用 `fa.backtest.metrics/simulate`，不写任何指标公式）→ `dashboard/loaders.py`（st.cache_data 缓存层，queries 本身无 streamlit 依赖）→ `dashboard/pages/` 7 页。核心管线代码零改动。

**Tech Stack:** Python 3.11+ / uv / streamlit≥1.40 / plotly≥5.24（独立 dependency-group）/ pandas（主依赖已有）/ pytest。

**Spec:** docs/superpowers/specs/2026-09-04-web-dashboard-design.md（本计划的母设计，含页面清单与双线分账约束）

## Global Constraints

- 核心依赖零变动：`[project].dependencies` 不许碰；dashboard 依赖只进 `[dependency-groups].dashboard = ["streamlit>=1.40", "plotly>=5.24"]`
- DB 访问一律 `mode=ro` URI 只读连接；dashboard 不 import `fa.db.connect`（那是读写连接），只用 `fa.config.db_path()` 解析路径
- 指标口径单一事实源：log-loss/Brier/校准 = `fa.backtest.metrics`，模拟盘 = `fa.backtest.simulate`，门槛常量 = `fa.value.gates`（simulate re-export）；`queries.py` 不许出现任何指标公式
- 双线分账（spec §12.1/§12.3）：A 线查询只碰 `backtest_predictions`/`matches`；B 线查询只碰 `recommendations`/`bets`/`runs`/`odds_snapshots`/`fixtures`/`meta`/`unknown_names`/`teams`；不同屏混排
- meta 键名逐字：`paper_bankroll`、`odds_quota_remaining`（值是 TEXT，查询里 float() 转）
- 诚实条款：A/B 双轨页必须常显样本量与「≥300 注」进度及样本不足警示；dashboard 是视图不是证据源，任何任务不得把它产出的数字写进 docs/*.md 当结论
- 测试种子日期一律字面量（如 `'2026-09-04'`），不得用 `date.today()`/`now()`（时间炸弹禁令）
- 注释/文档/commit 主题用中文；commit 用项目风格（`feat(dashboard): …` 等 conventional 前缀）
- 不引入 matplotlib；图表只用 plotly；页面文件不放业务计算（只取数+渲染）

---

### Task 1: 依赖组 + 只读连接 `connect_ro`

**Files:**
- Modify: `pyproject.toml`（`[dependency-groups]` 段）
- Create: `dashboard/queries.py`
- Create: `dashboard/__init__.py`（空文件，仅为包标识——uv 编辑安装的是 `fa`，dashboard 不是包，但留空文件可防 `dashboard` 名与其他顶层模块混淆）
- Test: `tests/dashboard/test_queries.py`（本任务建文件）

**Interfaces:**
- Consumes: `fa.config.db_path() -> Path`（已存在）
- Produces: `dashboard/queries.py::connect_ro(path: Path | None = None) -> sqlite3.Connection`（Row factory，`mode=ro`，路径不存在抛 `FileNotFoundError`）——后续所有任务与 loaders 依赖它

- [ ] **Step 1: pyproject 加 dashboard 依赖组**

`pyproject.toml` 现有 `[dependency-groups]` 段改为：

```toml
[dependency-groups]
dev = ["pytest>=8.0"]
dashboard = ["streamlit>=1.40", "plotly>=5.24"]
```

- [ ] **Step 2: 写失败测试**

创建 `tests/dashboard/test_queries.py`（subdir 不加 `__init__.py`，与 `tests/backtest/` 一致）：

```python
"""dashboard 查询层测试：只读契约 + 各查询函数（设计 §5/§7）。

种子日期全部用字面量；断言数字均手算钉死。
"""
import sqlite3

import pytest

from fa.db import init_db


def test_connect_ro_missing_db_raises(tmp_path):
    from queries import connect_ro

    with pytest.raises(FileNotFoundError):
        connect_ro(tmp_path / "nope.db")


def test_connect_ro_is_readonly(tmp_path):
    from queries import connect_ro

    init_db(tmp_path / "t.db")
    ro = connect_ro(tmp_path / "t.db")
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("INSERT INTO meta (key, value) VALUES ('k', 'v')")
    finally:
        ro.close()
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd /home/ryea0/Project/Football_Analysis && uv run --group dashboard pytest tests/dashboard/test_queries.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'queries'`

- [ ] **Step 4: 最小实现**

创建 `dashboard/__init__.py`（空文件）与 `dashboard/queries.py`：

```python
"""dashboard 查询层：只读 SQL + fa 纯函数复用。

设计：docs/superpowers/specs/2026-09-04-web-dashboard-design.md（§2/§5）。
本模块不 import streamlit（缓存包在 loaders.py）；不碰任何有副作用的
fa 模块（写库 / hermes / 网络）；指标公式一律复用 fa.backtest.*。
"""
import sqlite3
from pathlib import Path

from fa.config import db_path


def connect_ro(path: Path | None = None) -> sqlite3.Connection:
    """只读连接（mode=ro）：dashboard 的任何 bug 都写不了库。

    WAL 下只读读者与管线写入互不阻塞；库不存在时给出可操作的报错
    （页面层捕获后提示先 `fa init`）。
    """
    p = Path(path) if path else db_path()
    if not p.exists():
        raise FileNotFoundError(f"数据库不存在：{p}（先运行 uv run fa init）")
    conn = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run --group dashboard pytest tests/dashboard/test_queries.py -v`
Expected: 2 passed

- [ ] **Step 6: 确认裸环境测试不受影响**

Run: `uv run pytest tests/dashboard -q`
Expected: 同样 2 passed（queries 只依赖主依赖，无需 dashboard 组）

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock dashboard/ tests/dashboard/
git commit -m "feat(dashboard): 只读连接 connect_ro + dashboard 依赖组（设计 §2）"
```

---

### Task 2: B 线查询 `b_summary`（页1 总览数据）

**Files:**
- Modify: `dashboard/queries.py`（追加函数与 import）
- Test: `tests/dashboard/test_queries.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `connect_ro`；`fa.db.init_db`（测试建库）
- Produces: `b_summary(conn: sqlite3.Connection) -> dict`，键：`bankroll: float|None`、`quota_remaining: float|None`、`n_bets: int`、`n_pending: int`、`pnl: float`（仅已结算：`Σ(return_amt)−Σ(stake)`，pending 不计）、`staked: float`（仅已结算）、`quota_series: list[dict]`（runs 的 id/type/phase/status/started_at/credits_before/credits_after）

- [ ] **Step 1: 写失败测试**

`tests/dashboard/test_queries.py` 追加（文件顶部 `from fa.db import init_db` 改为 `from fa.db import connect, init_db, set_meta`）：

```python
@pytest.fixture
def db(tmp_path):
    """读写连接（供种子写入）；被测函数只 SELECT。"""
    init_db(tmp_path / "t.db")
    conn = connect(tmp_path / "t.db")
    yield conn
    conn.close()


def _seed_bline_base(conn):
    """最小 B 线链：teams→run→fixture→recommendation×2（两轨）；bets 由各用例自定。"""
    conn.execute("INSERT INTO teams (league, name) VALUES ('E0', 'Team A'), ('E0', 'Team B')")
    conn.execute(
        "INSERT INTO runs (id, type, phase, started_at, status, credits_before, credits_after)"
        " VALUES (1, 'matchday', 'am', '2026-09-04T11:00:00', 'ok', 480, 460)")
    conn.execute(
        "INSERT INTO fixtures (id, league, event_key, source, kickoff_utc,"
        " home_team_id, away_team_id, status, created_at)"
        " VALUES (1, 'E0', 'evt1', 'oddsapi', '2026-09-05T19:00:00Z', 1, 2,"
        " 'scheduled', '2026-09-04T11:05:00')")
    conn.execute(
        "INSERT INTO recommendations (id, run_id, fixture_id, strategy, market, phase,"
        " model_p, market_p, best_odds, bookmaker, edge, ev, kelly_stake_frac, created_at)"
        " VALUES (1, 1, 1, 'model_only', 'H', 'am', 0.55, 0.50, 2.1, 'Pinnacle',"
        " 0.05, 0.155, 0.010, '2026-09-04T11:30:00'),"
        " (2, 1, 1, 'model_persona', 'H', 'am', 0.55, 0.50, 2.1, 'Pinnacle',"
        " 0.05, 0.155, 0.008, '2026-09-04T11:30:00')")
    conn.commit()


def test_b_summary(db):
    from queries import b_summary

    _seed_bline_base(db)
    set_meta(db, "paper_bankroll", "1000.0")
    set_meta(db, "odds_quota_remaining", "460")
    db.execute(
        "INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker, odds_taken,"
        " stake, status, settled_at, return_amt, closing_odds, clv)"
        " VALUES (1, 'paper', '2026-09-04T12:00:00', 'Pinnacle', 2.5, 10.0, 'won',"
        " '2026-09-06T06:30:00', 25.0, 2.2, 0.136364)")            # 已结算：+15.0
    db.execute(
        "INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker, odds_taken,"
        " stake, status, settled_at, return_amt, closing_odds, clv)"
        " VALUES (2, 'paper', '2026-09-04T12:00:00', 'Pinnacle', 3.1, 8.0, 'pending',"
        " NULL, NULL, NULL, NULL)")                                # 未结算：不计 P&L
    db.commit()

    s = b_summary(db)
    assert s["bankroll"] == 1000.0
    assert s["quota_remaining"] == 460.0
    assert s["n_bets"] == 2
    assert s["n_pending"] == 1
    assert s["pnl"] == pytest.approx(15.0)          # 25 − 10（pending 的 8 不进分母分子）
    assert s["staked"] == pytest.approx(10.0)
    assert s["quota_series"] == [{"id": 1, "type": "matchday", "phase": "am",
                                  "status": "ok", "started_at": "2026-09-04T11:00:00",
                                  "credits_before": 480, "credits_after": 460}]


def test_b_summary_empty_db(db):
    from queries import b_summary

    s = b_summary(db)
    assert s["bankroll"] is None and s["quota_remaining"] is None
    assert s["n_bets"] == 0 and s["n_pending"] == 0
    assert s["pnl"] == 0.0 and s["staked"] == 0.0
    assert s["quota_series"] == []


def test_b_summary_missing_meta_keys(db):
    from queries import b_summary

    _seed_bline_base(db)          # 只有 B 线表数据，meta 业务键一个都没写
    s = b_summary(db)
    assert s["bankroll"] is None  # 「未记录」而非 0（设计 §6）
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --group dashboard pytest tests/dashboard/test_queries.py -v -k b_summary`
Expected: FAIL —— `ImportError: cannot import name 'b_summary'`

- [ ] **Step 3: 实现**

`dashboard/queries.py` 追加：

```python
def b_summary(conn: sqlite3.Connection) -> dict:
    """页1 总览：meta 两键 + paper 注汇总 + 额度序列。

    pnl/staked 只计已结算注（pending 不进任何一侧）；meta 键缺失返回
    None（页面显示「未记录」而非 0，设计 §6）。
    """
    def meta_float(key: str) -> float | None:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return None if row is None else float(row["value"])

    agg = conn.execute("""
        SELECT COUNT(*) AS n,
               COALESCE(SUM(status = 'pending'), 0) AS n_pending,
               COALESCE(SUM(CASE WHEN status != 'pending'
                                 THEN COALESCE(return_amt, 0) - stake ELSE 0 END), 0.0) AS pnl,
               COALESCE(SUM(CASE WHEN status != 'pending'
                                 THEN stake ELSE 0 END), 0.0) AS staked
        FROM bets WHERE mode = 'paper'""").fetchone()
    quota = conn.execute("""
        SELECT id, type, phase, status, started_at, credits_before, credits_after
        FROM runs ORDER BY id""").fetchall()
    return {"bankroll": meta_float("paper_bankroll"),
            "quota_remaining": meta_float("odds_quota_remaining"),
            "n_bets": agg["n"], "n_pending": agg["n_pending"],
            "pnl": agg["pnl"], "staked": agg["staked"],
            "quota_series": [dict(r) for r in quota]}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --group dashboard pytest tests/dashboard/test_queries.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add dashboard/queries.py tests/dashboard/test_queries.py
git commit -m "feat(dashboard): b_summary 总览查询（meta 键缺省 None、P&L 只计已结算）"
```

---

### Task 3: B 线查询 `b_recommendations` + `b_bets`（页2 数据）

**Files:**
- Modify: `dashboard/queries.py`
- Test: `tests/dashboard/test_queries.py`

**Interfaces:**
- Consumes: `_seed_bline_base`（Task 2 定义于同文件）
- Produces:
  - `b_recommendations(conn) -> pd.DataFrame`：列 = recommendations 全字段 + `kickoff_utc`/`event_key` + `home`/`away`（teams LEFT JOIN，未对齐为 NULL）+ `league`（fixtures 的联赛码）；按 `created_at DESC, id DESC` 排序
  - `b_bets(conn) -> pd.DataFrame`：列 = bets 全字段 + `market`/`strategy`/`phase`/`model_p`/`market_p`/`edge`/`ev` + `home`/`away`/`kickoff_utc`；按 `placed_at DESC, id DESC`

- [ ] **Step 1: 写失败测试**

追加（本任务测试不需要新 import）：

```python
def _seed_rec_chain(db):
    """页2 链：2 队、2 fixture（1 个对齐 / 1 个未对齐）、2 run、4 推荐、2 注。"""
    _seed_bline_base(db)                                    # rec 1/2 已在
    db.execute(
        "INSERT INTO runs (id, type, phase, started_at, status)"
        " VALUES (2, 'matchday', 'pm', '2026-09-04T17:00:00', 'ok')")
    db.execute(
        "INSERT INTO fixtures (id, league, event_key, source, kickoff_utc,"
        " home_team_id, away_team_id, status, created_at)"
        " VALUES (2, 'E0', 'evt2', 'oddsapi', '2026-09-05T21:00:00Z', NULL, NULL,"
        " 'scheduled', '2026-09-04T17:05:00')")              # 未对齐 fixture
    db.execute(
        "INSERT INTO recommendations (id, run_id, fixture_id, strategy, market, phase,"
        " model_p, market_p, best_odds, bookmaker, edge, ev, kelly_stake_frac, created_at)"
        " VALUES (3, 2, 2, 'model_only', 'O2.5', 'pm', 0.60, 0.54, 1.95, 'Bet365',"
        " 0.06, 0.17, 0.012, '2026-09-04T17:30:00'),"
        " (4, 2, 2, 'model_persona', 'O2.5', 'pm', 0.60, 0.54, 1.95, 'Bet365',"
        " 0.06, 0.17, 0.010, '2026-09-04T17:30:00')")
    db.execute(
        "INSERT INTO bets (id, recommendation_id, mode, placed_at, bookmaker, odds_taken,"
        " stake, status, settled_at, return_amt, closing_odds, clv)"
        " VALUES (10, 1, 'paper', '2026-09-04T12:00:00', 'Pinnacle', 2.5, 10.0, 'won',"
        " '2026-09-06T06:30:00', 25.0, 2.2, 0.136364),"
        " (11, 3, 'paper', '2026-09-04T18:00:00', 'Bet365', 1.95, 12.0, 'pending',"
        " NULL, NULL, NULL, NULL)")
    db.commit()


def test_b_recommendations(db):
    from queries import b_recommendations

    _seed_rec_chain(db)
    df = b_recommendations(db)
    assert len(df) == 4
    assert list(df["id"]) == [4, 3, 2, 1]                    # created_at DESC
    row_evt1 = df[df["event_key"] == "evt1"].iloc[0]
    assert row_evt1["home"] == "Team A" and row_evt1["away"] == "Team B"
    row_evt2 = df[df["event_key"] == "evt2"].iloc[0]
    assert row_evt2["home"] is None and row_evt2["away"] is None   # 未对齐可空
    assert row_evt2["league"] == "E0"
    assert {"strategy", "phase", "market", "model_p", "market_p", "best_odds",
            "bookmaker", "edge", "ev", "kelly_stake_frac", "kickoff_utc"} <= set(df.columns)


def test_b_bets(db):
    from queries import b_bets

    _seed_rec_chain(db)
    df = b_bets(db)
    assert len(df) == 2
    assert list(df["id"]) == [11, 10]                        # placed_at DESC
    row = df[df["id"] == 10].iloc[0]
    assert row["market"] == "H" and row["strategy"] == "model_only"
    assert row["home"] == "Team A" and row["status"] == "won"
    assert row["clv"] == pytest.approx(0.136364)
    assert {"closing_odds", "return_amt", "settled_at", "phase", "edge"} <= set(df.columns)


def test_b_recommendations_empty(db):
    from queries import b_recommendations, b_bets

    assert b_recommendations(db).empty
    assert b_bets(db).empty
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --group dashboard pytest tests/dashboard/test_queries.py -v -k "b_recommendations or b_bets"`
Expected: FAIL —— ImportError

- [ ] **Step 3: 实现**

`dashboard/queries.py` 顶部 import 区补 `import pandas as pd`，追加：

```python
def b_recommendations(conn: sqlite3.Connection) -> pd.DataFrame:
    """页2 上表：推荐全维度 + fixture 队名/开球时间（未对齐队名为 NULL）。"""
    return pd.read_sql_query("""
        SELECT r.id, r.created_at, r.phase, r.strategy, r.market,
               r.model_p, r.market_p, r.best_odds, r.bookmaker,
               r.edge, r.ev, r.kelly_stake_frac,
               r.verdict, r.confidence_delta, r.final_stake_frac,
               f.league, f.kickoff_utc, f.event_key,
               th.name AS home, ta.name AS away
        FROM recommendations r
        JOIN fixtures f ON f.id = r.fixture_id
        LEFT JOIN teams th ON th.id = f.home_team_id
        LEFT JOIN teams ta ON ta.id = f.away_team_id
        ORDER BY r.created_at DESC, r.id DESC
    """, conn)


def b_bets(conn: sqlite3.Connection) -> pd.DataFrame:
    """页2 下表：paper/live 注明细 + 推荐维度（market/strategy 等）+ 队名。"""
    return pd.read_sql_query("""
        SELECT b.id, b.mode, b.placed_at, b.status, b.bookmaker,
               b.odds_taken, b.stake, b.settled_at, b.return_amt,
               b.closing_odds, b.clv,
               r.strategy, r.phase, r.market, r.model_p, r.market_p,
               r.edge, r.ev,
               f.kickoff_utc, th.name AS home, ta.name AS away
        FROM bets b
        JOIN recommendations r ON r.id = b.recommendation_id
        JOIN fixtures f ON f.id = r.fixture_id
        LEFT JOIN teams th ON th.id = f.home_team_id
        LEFT JOIN teams ta ON ta.id = f.away_team_id
        ORDER BY b.placed_at DESC, b.id DESC
    """, conn)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --group dashboard pytest tests/dashboard/test_queries.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add dashboard/queries.py tests/dashboard/test_queries.py
git commit -m "feat(dashboard): 推荐与台账查询（fixtures/teams LEFT JOIN，未对齐可空）"
```

---

### Task 4: B 线查询 `b_ab_tracks`（页3 A/B 双轨）

**Files:**
- Modify: `dashboard/queries.py`
- Test: `tests/dashboard/test_queries.py`

**Interfaces:**
- Consumes: Task 3 的 `_seed_rec_chain` 所依赖的 `_seed_bline_base` 链
- Produces: `b_ab_tracks(conn) -> dict`，形如
  `{"model_only": {"n": int, "n_settled": int, "roi": float|None, "clv_median": float|None, "cum": {"dates": list[str], "pnl": list[float]}}, "model_persona": {…同…}}`
  ——roi = 已结算 pnl / 已结算 staked（分母 0 → None）；cum 为按 `settled_at` 升序的累计 P&L

- [ ] **Step 1: 写失败测试**

追加：

```python
def _seed_ab_tracks(db):
    """两轨各 2 注（1 won + 1 lost），手算基准见断言。

    model_only     ：won(stake 10, ret 25, clv +0.05, d1) + lost(stake 10, clv −0.02, d2)
    model_persona  ：won(stake 4,  ret 12, clv +0.10, d1) + lost(stake 6,  clv −0.04, d2)
    需 4 条**各带一注**的独立 recommendation（bets UNIQUE(recommendation_id, mode)）：
    base 提供 rec 1/2（两轨各一），本夹具补 rec 5/6（两轨各一）。刻意不基于
    _seed_rec_chain——它已给 rec 1/3 下注，会撞 UNIQUE 约束。
    """
    _seed_bline_base(db)                                     # rec 1/2、run 1、fixture 1 已在
    db.execute(
        "INSERT INTO recommendations (id, run_id, fixture_id, strategy, market, phase,"
        " model_p, market_p, best_odds, bookmaker, edge, ev, kelly_stake_frac, created_at)"
        " VALUES (5, 1, 1, 'model_only', 'A', 'am', 0.40, 0.35, 3.0, 'Pinnacle',"
        " 0.05, 0.20, 0.010, '2026-09-04T11:30:00'),"
        " (6, 1, 1, 'model_persona', 'A', 'am', 0.40, 0.35, 3.0, 'Pinnacle',"
        " 0.05, 0.20, 0.008, '2026-09-04T11:30:00')")
    db.execute(
        "INSERT INTO bets (id, recommendation_id, mode, placed_at, bookmaker, odds_taken,"
        " stake, status, settled_at, return_amt, closing_odds, clv)"
        " VALUES (20, 1, 'paper', '2026-09-04T12:00:00', 'Pinnacle', 2.5, 10.0, 'won',"
        " '2026-09-05T06:30:00', 25.0, NULL, 0.05),"
        " (21, 5, 'paper', '2026-09-04T12:00:00', 'Pinnacle', 3.0, 10.0, 'lost',"
        " '2026-09-06T06:30:00', 0.0, NULL, -0.02),"
        " (22, 2, 'paper', '2026-09-04T12:00:00', 'Pinnacle', 3.0, 4.0, 'won',"
        " '2026-09-05T06:30:00', 12.0, NULL, 0.10),"
        " (23, 6, 'paper', '2026-09-04T12:00:00', 'Pinnacle', 3.0, 6.0, 'lost',"
        " '2026-09-06T06:30:00', 0.0, NULL, -0.04)")
    db.commit()


def test_b_ab_tracks(db):
    from queries import b_ab_tracks

    _seed_ab_tracks(db)
    t = b_ab_tracks(db)
    mo = t["model_only"]
    assert mo["n"] == 2 and mo["n_settled"] == 2
    assert mo["roi"] == pytest.approx((25 - 10) / 20)         # 0.25
    assert mo["clv_median"] == pytest.approx((0.05 - 0.02) / 2)   # 0.015
    assert mo["cum"]["dates"] == ["2026-09-05T06:30:00", "2026-09-06T06:30:00"]
    assert mo["cum"]["pnl"] == [pytest.approx(15.0), pytest.approx(5.0)]
    mp = t["model_persona"]
    assert mp["n"] == 2 and mp["n_settled"] == 2
    assert mp["roi"] == pytest.approx((12 - 10) / 10)         # 0.20
    assert mp["clv_median"] == pytest.approx((0.10 - 0.04) / 2)   # 0.03
    assert mp["cum"]["pnl"] == [pytest.approx(8.0), pytest.approx(2.0)]


def test_b_ab_tracks_empty(db):
    from queries import b_ab_tracks

    t = b_ab_tracks(db)
    for strat in ("model_only", "model_persona"):
        assert t[strat]["n"] == 0
        assert t[strat]["roi"] is None and t[strat]["clv_median"] is None
        assert t[strat]["cum"] == {"dates": [], "pnl": []}


def test_b_ab_tracks_all_pending(db):
    """全是 pending：roi/clv 有 None 路径（分母 0 → None），不抛异常。"""
    from queries import b_ab_tracks

    _seed_bline_base(db)
    db.execute(
        "INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker, odds_taken,"
        " stake, status) VALUES (1, 'paper', '2026-09-04T12:00:00', 'Pinnacle',"
        " 2.5, 10.0, 'pending')")
    db.commit()
    t = b_ab_tracks(db)
    assert t["model_only"]["n"] == 1 and t["model_only"]["n_settled"] == 0
    assert t["model_only"]["roi"] is None
    assert t["model_only"]["clv_median"] is None              # clv 全 NULL
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --group dashboard pytest tests/dashboard/test_queries.py -v -k ab_tracks`
Expected: FAIL —— ImportError

- [ ] **Step 3: 实现**

`dashboard/queries.py` 追加：

```python
def b_ab_tracks(conn: sqlite3.Connection) -> dict:
    """页3：两轨注数/ROI/CLV 中位数 + 按结算日累计 P&L（§6.6/§12.3）。

    样本量语义交给页面展示（进度条 + 警示）；这里只算数，不判结论。
    """
    df = pd.read_sql_query("""
        SELECT r.strategy AS strategy, b.status, b.stake, b.return_amt,
               b.clv, b.settled_at
        FROM bets b JOIN recommendations r ON r.id = b.recommendation_id
        WHERE b.mode = 'paper'""", conn)
    out: dict = {}
    for strat in ("model_only", "model_persona"):
        g = df[df["strategy"] == strat]
        settled = g[g["status"] != "pending"].sort_values("settled_at")
        pnl = float(settled["return_amt"].fillna(0).sum() - settled["stake"].sum()) if len(settled) else 0.0
        staked = float(settled["stake"].sum()) if len(settled) else 0.0
        out[strat] = {
            "n": int(len(g)),
            "n_settled": int(len(settled)),
            "roi": (pnl / staked) if staked else None,
            "clv_median": float(g["clv"].median()) if g["clv"].notna().any() else None,
            "cum": {"dates": list(settled["settled_at"]),
                    "pnl": list((settled["return_amt"].fillna(0) - settled["stake"]).cumsum())},
        }
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --group dashboard pytest tests/dashboard/test_queries.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add dashboard/queries.py tests/dashboard/test_queries.py
git commit -m "feat(dashboard): A/B 双轨查询（roi/clv 中位数/累计 P&L，pending 不进分母）"
```

---

### Task 5: B 线查询 `b_runs` + `b_unknown_names`（页4 运维健康）

**Files:**
- Modify: `dashboard/queries.py`
- Test: `tests/dashboard/test_queries.py`

**Interfaces:**
- Consumes: 无新依赖
- Produces:
  - `b_runs(conn) -> pd.DataFrame`：runs 全字段 + summary JSON 展开列 `fixtures`/`aligned`/`bets`/`degraded`/`degraded_reasons`（缺失键为 None/NaN），按 `id DESC`
  - `b_unknown_names(conn) -> pd.DataFrame`：`source`/`name`/`first_seen`，按 `first_seen, name` 排序

- [ ] **Step 1: 写失败测试**

追加（顶部 import 区补 `import json`）：

```python
def test_b_runs_expands_summary(db):
    from queries import b_runs

    db.execute(
        "INSERT INTO runs (id, type, phase, started_at, finished_at, status,"
        " credits_before, credits_after, summary)"
        " VALUES (1, 'matchday', 'am', '2026-09-04T11:00:00', '2026-09-04T11:02:00',"
        " 'ok', 480, 460, ?)",
        (json.dumps({"fixtures": 102, "aligned": 48, "bets": 14,
                     "degraded": False, "degraded_reasons": []}),))
    db.execute(
        "INSERT INTO runs (id, type, phase, started_at, status, summary)"
        " VALUES (2, 'matchday', 'am', '2026-09-05T11:00:00', 'failed',"
        " ?)",
        (json.dumps({"fixtures": 0, "degraded": True,
                     "degraded_reasons": ["persona 超时"]}),))
    db.commit()

    df = b_runs(db)
    assert list(df["id"]) == [2, 1]                           # id DESC
    r1, r2 = df.iloc[1], df.iloc[0]
    assert r1["fixtures"] == 102 and r1["aligned"] == 48 and r1["bets"] == 14
    assert bool(r1["degraded"]) is False
    assert r2["degraded"] is True and r2["degraded_reasons"] == ["persona 超时"]
    assert r1["credits_before"] == 480 and r1["credits_after"] == 460
    assert r2["aligned"] is None and r2["bets"] is None       # JSON 缺键 → None


def test_b_runs_summary_null(db):
    from queries import b_runs

    db.execute("INSERT INTO runs (id, type, started_at, status, summary)"
               " VALUES (1, 'daily', '2026-09-04T06:30:00', 'ok', NULL)")
    db.commit()
    df = b_runs(db)
    assert df.iloc[0]["degraded"] is None                     # summary 为 NULL 不炸


def test_b_unknown_names(db):
    from queries import b_unknown_names

    db.execute("INSERT INTO unknown_names (source, name, first_seen) VALUES"
               " ('oddsapi', 'FC Koln', '2026-09-04T11:00:00'),"
               " ('oddsapi', 'M\\'Gladbach', '2026-09-03T11:00:00')")
    db.commit()
    df = b_unknown_names(db)
    assert list(df["name"]) == ["M'Gladbach", "FC Koln"]      # first_seen 升序
    assert b_unknown_names(db).source.unique().tolist() == ["oddsapi"]


def test_b_runs_empty(db):
    from queries import b_runs, b_unknown_names

    assert b_runs(db).empty
    assert b_unknown_names(db).empty
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --group dashboard pytest tests/dashboard/test_queries.py -v -k "b_runs or unknown"`
Expected: FAIL —— ImportError

- [ ] **Step 3: 实现**

`dashboard/queries.py` 顶部 import 区补 `import json`，追加：

```python
def b_runs(conn: sqlite3.Connection) -> pd.DataFrame:
    """页4：run 历史 + runs.summary 关键键展开（诚实降级的巡检入口，spec §9.5）。"""
    df = pd.read_sql_query(
        "SELECT id, type, phase, status, started_at, finished_at,"
        " credits_before, credits_after, summary FROM runs ORDER BY id DESC", conn)
    if df.empty:
        return df
    parsed = df["summary"].map(lambda t: json.loads(t) if t else {})
    for key in ("fixtures", "aligned", "bets", "degraded", "degraded_reasons"):
        df[key] = parsed.map(lambda d: d.get(key))
    return df


def b_unknown_names(conn: sqlite3.Connection) -> pd.DataFrame:
    """页4：队名隔离表（spec §3.3——匹配不上的进隔离表，绝不静默丢弃）。"""
    return pd.read_sql_query(
        "SELECT source, name, first_seen FROM unknown_names"
        " ORDER BY first_seen, name", conn)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --group dashboard pytest tests/dashboard/test_queries.py -v`
Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
git add dashboard/queries.py tests/dashboard/test_queries.py
git commit -m "feat(dashboard): run 历史 summary 展开 + 隔离表查询"
```

---

### Task 6: A 线查询 `a_overview` + `a_calibration`（页5/6 数据）

**Files:**
- Modify: `dashboard/queries.py`
- Test: `tests/dashboard/test_queries.py`

**Interfaces:**
- Consumes: `fa.backtest.metrics`（`fetch_predictions(conn, leagues=None, seasons=None) -> list[dict]`、`evaluate(rows) -> dict`、`by_group(rows, key) -> dict`、`calibration(p, hit, bins=10) -> list[dict]`）
- Produces:
  - `a_overview(conn, leagues=None, seasons=None) -> dict`：无行时 `{"empty": True}`；否则 `{"empty": False, "overall": evaluate(rows), "by_season": {season:int → evaluate}, "by_league": {league:str → evaluate}, "by_league_season": {"E0|2024" → evaluate}}`
  - `a_calibration(conn, leagues=None, seasons=None) -> dict`：无行 `{"empty": True}`；否则 `{"empty": False, "H": [...], "D": [...], "A": [...], "O2.5": [...]}`（`calibration()` 的分桶列表；无 p_over25 行时 `O2.5` 为 `[]`）

- [ ] **Step 1: 写失败测试**

追加（顶部 import 区补 `import math`）：

```python
def _seed_bp_rows(db):
    """两行 walk-forward 预测 + 所需 match（FK）。

    行1：模型 (0.8,0.1,0.1) 市场 (0.5,0.25,0.25) 赔率 (2.0,3.5,4.0) 结果 H，3 球
    行2：模型 (0.1,0.2,0.7) 市场 (0.3,0.30,0.40) 赔率 (2.5,3.4,4.0) 结果 A，1 球
    """
    db.execute("INSERT INTO teams (league, name) VALUES ('E0', 'Team A'), ('E0', 'Team B')")
    db.execute(
        "INSERT INTO matches (id, league, season, date, home_team_id, away_team_id,"
        " fthg, ftag, raw_line) VALUES (1, 'E0', 2024, '2025-01-01', 1, 2, 2, 1, '{}'),"
        " (2, 'E0', 2024, '2025-01-08', 1, 2, 0, 1, '{}')")
    db.execute(
        "INSERT INTO backtest_predictions (league, season, week_index, match_id, date,"
        " p_home, p_draw, p_away, p_over25, mkt_home, mkt_draw, mkt_away, mkt_over25,"
        " odds_home, odds_draw, odds_away, outcome, total_goals)"
        " VALUES ('E0', 2024, 1, 1, '2025-01-01', 0.8, 0.1, 0.1, 0.6,"
        " 0.5, 0.25, 0.25, NULL, 2.0, 3.5, 4.0, 'H', 3),"
        " ('E0', 2024, 2, 2, '2025-01-08', 0.1, 0.2, 0.7, 0.3,"
        " 0.3, 0.30, 0.40, NULL, 2.5, 3.4, 4.0, 'A', 1)")
    db.commit()


def test_a_overview_hand_computed(db):
    """手算钉死（方向性：模型优于市场 → degradation < 0）。

    model_ll  = −(ln0.8 + ln0.7)/2 = 0.289909…
    market_ll = −(ln0.5 + ln0.4)/2 = 0.804719…
    degradation_pct ≈ −63.974
    """
    from queries import a_overview

    _seed_bp_rows(db)
    o = a_overview(db)
    assert o["empty"] is False
    ov = o["overall"]
    assert ov["n"] == 2
    assert ov["model_ll"] == pytest.approx(-(math.log(0.8) + math.log(0.7)) / 2)
    assert ov["market_ll"] == pytest.approx(-(math.log(0.5) + math.log(0.4)) / 2)
    assert ov["degradation_pct"] == pytest.approx(
        (math.log(0.8) + math.log(0.7)) / (math.log(0.5) + math.log(0.4)) * 100 - 100)
    assert ov["degradation_pct"] < 0                            # 方向代入检查
    assert ov["verdict"] == "GO"                                # 模型显著更优
    assert o["by_league"]["E0"]["n"] == 2
    assert o["by_season"][2024]["n"] == 2
    assert o["by_league_season"]["E0|2024"]["n"] == 2
    assert sum(v["n"] for v in o["by_league_season"].values()) == 2


def test_a_overview_filter_and_empty(db):
    from queries import a_overview

    _seed_bp_rows(db)
    assert a_overview(db, leagues=["SP1"])["empty"] is True     # 过滤后无行
    assert a_overview(db, seasons=[2025])["empty"] is True
    assert a_overview(db, leagues=["E0"], seasons=[2024])["overall"]["n"] == 2


def test_a_calibration(db):
    from queries import a_calibration

    _seed_bp_rows(db)
    c = a_calibration(db)
    assert c["empty"] is False
    h08 = [b for b in c["H"] if b["lo"] == 0.8][0]
    assert h08 == {"lo": 0.8, "hi": 0.9, "n": 1, "avg_p": pytest.approx(0.8), "emp": 1.0}
    h01 = [b for b in c["H"] if b["lo"] == 0.1][0]
    assert h01["emp"] == 0.0                                    # 行2 的 p_home=0.1 未命中
    o06 = [b for b in c["O2.5"] if b["lo"] == 0.6][0]
    assert o06["emp"] == 1.0                                    # 3 球 ≥ 3 → over 命中
    assert [b for b in c["O2.5"] if b["lo"] == 0.3][0]["emp"] == 0.0
    assert a_calibration(db, leagues=["SP1"])["empty"] is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --group dashboard pytest tests/dashboard/test_queries.py -v -k "a_overview or a_calibration"`
Expected: FAIL —— ImportError

- [ ] **Step 3: 实现**

`dashboard/queries.py` 顶部 import 区补：

```python
from fa.backtest.metrics import by_group, calibration, evaluate, fetch_predictions
```

追加：

```python
def a_overview(conn: sqlite3.Connection, leagues=None, seasons=None) -> dict:
    """页5：全样本对比 + 分赛季/分联赛/交叉分解（evaluate 口径=spec §8.2）。

    metrics.evaluate 对空行抛 ValueError——这里转成 empty 标记，页面走空态
    （设计 §5/§6：查询对空库永不抛）。
    """
    rows = fetch_predictions(conn, leagues, seasons)
    if not rows:
        return {"empty": True}
    cross: dict = {}
    for r in rows:
        cross.setdefault((r["league"], r["season"]), []).append(r)
    return {"empty": False,
            "overall": evaluate(rows),
            "by_season": by_group(rows, "season"),
            "by_league": by_group(rows, "league"),
            "by_league_season": {f"{l}|{s}": evaluate(v)
                                 for (l, s), v in sorted(cross.items())}}


def a_calibration(conn: sqlite3.Connection, leagues=None, seasons=None) -> dict:
    """页6：分市场十分位校准（复用 metrics.calibration，等宽分桶）。

    O2.5 的命中定义 = total_goals ≥ 3（与 simulate._hit 同口径）；无
    p_over25 行的库（大小球通道未跑）返回空列表而非报错。
    """
    rows = fetch_predictions(conn, leagues, seasons)
    if not rows:
        return {"empty": True}

    def cal(pairs):
        return calibration([p for p, _ in pairs], [h for _, h in pairs])

    over_rows = [r for r in rows if r.get("p_over25") is not None]
    return {"empty": False,
            "H": cal([(r["p_home"], r["outcome"] == "H") for r in rows]),
            "D": cal([(r["p_draw"], r["outcome"] == "D") for r in rows]),
            "A": cal([(r["p_away"], r["outcome"] == "A") for r in rows]),
            "O2.5": cal([(r["p_over25"], r["total_goals"] >= 3)
                         for r in over_rows]) if over_rows else []}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --group dashboard pytest tests/dashboard/test_queries.py -v`
Expected: 18 passed

- [ ] **Step 5: Commit**

```bash
git add dashboard/queries.py tests/dashboard/test_queries.py
git commit -m "feat(dashboard): A 线总览与校准查询（复用 metrics，空库转 empty 标记）"
```

---

### Task 7: A 线查询 `a_paper_sim`（页7 模拟盘）

**Files:**
- Modify: `dashboard/queries.py`
- Test: `tests/dashboard/test_queries.py`

**Interfaces:**
- Consumes: `fa.backtest.simulate`（`candidates(rows) -> list[dict]`（含 `market`/`odds`/`p`/`date`/`outcome_hit`，O2.5 因缺收盘赔率被跳过）、`simulate_flat(cands) -> {"n","staked","returned","pnl","roi"}`、`simulate_kelly(cands, bankroll) -> {"n","final_bankroll","roi","max_drawdown_pct"}`）；`ODDS_MIN=1.4`/`ODDS_MAX=6.0`（经 simulate re-export）
- Produces: `a_paper_sim(conn, leagues=None, seasons=None, bankroll: float = 1000.0) -> dict`：无候选 `{"empty": True}`；否则 `{"empty": False, "n_candidates", "flat", "kelly", "flat_curve": [{"date","pnl"}…], "kelly_curve": [{"date","bankroll"}…], "by_market": {market → simulate_flat dict}, "by_band": {"[1.4,2.0)" 等 → simulate_flat dict}}`
  ——曲线点 = 对**日期前缀**调用同一 simulate 函数（口径零复写）

- [ ] **Step 1: 写失败测试**

追加（与 Task 6 同一 `_seed_bp_rows`）：

```python
def test_a_paper_sim_hand_computed(db):
    """手算钉死（含 ¼ Kelly 触顶路径——两注 f 都超 2% 上限被截断）。

    candidates：行1 H（p .8/odds 2.0，edge .3，ev .6，hit）、行2 A（p .7/odds 4.0，
    edge .3，ev 1.8，hit）——D/O2.5 与低 edge 市场全被门槛滤掉
    flat：staked 2、returned 6.0、pnl 4.0、roi 2.0
    kelly（bankroll 1000）：c1 f=.02 截断 → 投 20 赢 +20 → 1020；
                            c2 f=.02 截断 → 投 20.4 赢 +61.2 → 1081.2
    """
    from queries import a_paper_sim

    _seed_bp_rows(db)
    s = a_paper_sim(db)
    assert s["empty"] is False and s["n_candidates"] == 2
    assert s["flat"]["pnl"] == pytest.approx(4.0)
    assert s["flat"]["roi"] == pytest.approx(2.0)
    assert s["kelly"]["final_bankroll"] == pytest.approx(1081.2)
    assert s["kelly"]["max_drawdown_pct"] == pytest.approx(0.0)   # 两注全胜无回撤
    assert [p["pnl"] for p in s["flat_curve"]] == [pytest.approx(1.0), pytest.approx(4.0)]
    assert [p["bankroll"] for p in s["kelly_curve"]] == [pytest.approx(1020.0),
                                                         pytest.approx(1081.2)]
    assert set(s["by_market"]) == {"H", "A"}
    assert s["by_market"]["H"]["pnl"] == pytest.approx(1.0)
    assert s["by_market"]["A"]["pnl"] == pytest.approx(3.0)
    assert s["by_band"]["[1.4,2.0)"]["n"] == 1                    # H odds 2.0
    assert s["by_band"]["[3.0,6.0]"]["n"] == 1                    # A odds 4.0
    assert s["by_band"]["[2.0,3.0)"]["n"] == 0


def test_a_paper_sim_empty(db):
    from queries import a_paper_sim

    assert a_paper_sim(db)["empty"] is True                       # 空库
    _seed_bp_rows(db)
    assert a_paper_sim(db, leagues=["SP1"])["empty"] is True      # 过滤后无候选
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --group dashboard pytest tests/dashboard/test_queries.py -v -k paper_sim`
Expected: FAIL —— ImportError

- [ ] **Step 3: 实现**

`dashboard/queries.py` 顶部 import 区补：

```python
from fa.backtest.simulate import (ODDS_MAX, ODDS_MIN, candidates, simulate_flat,
                                  simulate_kelly)
```

追加：

```python
def _odds_bands() -> list[tuple[float, float, str]]:
    """赔率区间表：成员判定用常量（gates 单一事实源），标签文案按 spec
    §5.2 钉定的 1.4/6.0 书写——gates 改值须先改 spec，届时同步改标签。"""
    return [(ODDS_MIN, 2.0, "[1.4,2.0)"), (2.0, 3.0, "[2.0,3.0)"),
            (3.0, ODDS_MAX, "[3.0,6.0]")]


def a_paper_sim(conn: sqlite3.Connection, leagues=None, seasons=None,
                bankroll: float = 1000.0) -> dict:
    """页7：flat/¼Kelly 模拟（复用 simulate，成交价=收盘价，与 M2 报告同口径）。

    累计曲线用「日期前缀复调」：每个时间点把截至该日的候选前缀喂给同一个
    simulate 函数取汇总值——曲线与终值永远同源，不复制任何算式。
    """
    rows = fetch_predictions(conn, leagues, seasons)
    cands = candidates(rows)
    if not cands:
        return {"empty": True}
    ordered = sorted(cands, key=lambda c: c["date"])
    dates = sorted({c["date"] for c in ordered})
    flat_curve, kelly_curve = [], []
    for d in dates:
        prefix = [c for c in ordered if c["date"] <= d]
        flat_curve.append({"date": d, "pnl": simulate_flat(prefix)["pnl"]})
        kelly_curve.append({"date": d,
                            "bankroll": simulate_kelly(prefix, bankroll=bankroll)["final_bankroll"]})

    def band_label(o: float) -> str | None:
        return next((lab for lo, hi, lab in _odds_bands() if lo <= o <= hi), None)

    return {"empty": False, "n_candidates": len(cands),
            "flat": simulate_flat(cands),
            "kelly": simulate_kelly(cands, bankroll=bankroll),
            "flat_curve": flat_curve, "kelly_curve": kelly_curve,
            "by_market": {m: simulate_flat([c for c in cands if c["market"] == m])
                          for m in sorted({c["market"] for c in cands})},
            "by_band": {lab: simulate_flat([c for c in cands
                                            if band_label(c["odds"]) == lab])
                        for _, _, lab in _odds_bands()}}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --group dashboard pytest tests/dashboard/test_queries.py -v`
Expected: 20 passed

- [ ] **Step 5: Commit**

```bash
git add dashboard/queries.py tests/dashboard/test_queries.py
git commit -m "feat(dashboard): 模拟盘查询（前缀复调 simulate，口径零复写）"
```

---

### Task 8: 缓存层 `loaders.py` + 入口 `app.py`

**Files:**
- Create: `dashboard/loaders.py`
- Create: `dashboard/app.py`

**Interfaces:**
- Consumes: Task 2–7 的全部 `b_*`/`a_*` 查询与 Task 1 的 `connect_ro`
- Produces（loaders 导出，页面唯一取数入口）：`summary() -> dict`、`recommendations() -> pd.DataFrame`、`paper_bets() -> pd.DataFrame`、`ab_tracks() -> dict`、`runs() -> pd.DataFrame`、`unknown() -> pd.DataFrame`、`overview(leagues=None, seasons=None) -> dict`、`calibration(leagues=None, seasons=None) -> dict`、`paper_sim(leagues=None, seasons=None) -> dict`——全部 `@st.cache_data(ttl=300)`，`FileNotFoundError` 原样上抛（页面捕获）

**说明：** 本任务是纯胶水（无新逻辑），不单测——页面冒烟（Task 9/10 的 AppTest）与真实启动（Task 12）即其验证；`loaders` 的存在依据是设计 §2「queries 无 streamlit 依赖」，缓存必须独立成层。

- [ ] **Step 1: 写 `dashboard/loaders.py`**

```python
"""缓存层：st.cache_data(ttl=300) 包住 queries（设计 §5）。

queries.py 无 streamlit 依赖（可独立 pytest），缓存与连接生命周期归这里；
FileNotFoundError 不吞——页面层捕获后提示 `fa init`。
"""
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st

from queries import (a_calibration, a_overview, a_paper_sim, b_ab_tracks,
                     b_bets, b_recommendations, b_runs, b_summary,
                     b_unknown_names, connect_ro)


def _run(fn, *args):
    with closing(connect_ro()) as conn:
        return fn(conn, *args)


@st.cache_data(ttl=300)
def summary() -> dict:
    return _run(b_summary)


@st.cache_data(ttl=300)
def recommendations():
    return _run(b_recommendations)


@st.cache_data(ttl=300)
def paper_bets():
    return _run(b_bets)


@st.cache_data(ttl=300)
def ab_tracks() -> dict:
    return _run(b_ab_tracks)


@st.cache_data(ttl=300)
def runs():
    return _run(b_runs)


@st.cache_data(ttl=300)
def unknown():
    return _run(b_unknown_names)


@st.cache_data(ttl=300)
def overview(leagues=None, seasons=None) -> dict:
    return _run(a_overview, leagues, seasons)


@st.cache_data(ttl=300)
def calibration(leagues=None, seasons=None) -> dict:
    return _run(a_calibration, leagues, seasons)


@st.cache_data(ttl=300)
def paper_sim(leagues=None, seasons=None) -> dict:
    return _run(a_paper_sim, leagues, seasons)
```

- [ ] **Step 2: 写 `dashboard/app.py`**

```python
"""fa 本地只读看板 · 入口（设计：docs/superpowers/specs/2026-09-04-web-dashboard-design.md）。

运行：uv run --group dashboard streamlit run dashboard/app.py
"""
import streamlit as st

st.set_page_config(page_title="fa 看板", page_icon="⚽", layout="wide")
st.title("fa · 本地只读看板")
st.caption("data/fa.db 只读 · B 线=前向运营（paper）· A 线=walk-forward 回测（spec §12 分账）")

with st.sidebar:
    if st.button("🔄 刷新数据", use_container_width=True):
        st.cache_data.clear()
    st.caption("查询缓存 5 分钟（ttl=300），run 落库后点刷新")
```

- [ ] **Step 3: 导入冒烟**

Run: `uv run --group dashboard python -c "import sys; sys.path.insert(0, 'dashboard'); import loaders; print('loaders ok')"`
Expected: 输出 `loaders ok`（无 ImportError）

- [ ] **Step 4: Commit**

```bash
git add dashboard/loaders.py dashboard/app.py
git commit -m "feat(dashboard): st.cache_data 缓存层与看板入口"
```

---

### Task 9: 页面冒烟测试 + B 线 4 页

**Files:**
- Create: `tests/dashboard/test_pages_smoke.py`
- Create: `dashboard/pages/1_📋_B线_总览.py`、`dashboard/pages/2_🎯_B线_推荐与台账.py`、`dashboard/pages/3_⚖️_B线_AB双轨进度.py`、`dashboard/pages/4_🩺_B线_运维健康.py`

**Interfaces:**
- Consumes: loaders 全部导出（Task 8）；`fa.config.LEAGUES`（E0→Premier League 等显示映射）；`fa.db.init_db`（测试）
- Produces: 冒烟测试按 `dashboard/pages/*.py` glob 参数化——后续任务新增页面自动纳入

- [ ] **Step 1: 写失败冒烟测试**

创建 `tests/dashboard/test_pages_smoke.py`：

```python
"""页面冒烟：AppTest 逐页跑空库——页面必须优雅空态、不抛异常（设计 §7）。"""
from pathlib import Path

import pytest

st = pytest.importorskip("streamlit")   # 裸环境（无 dashboard 组）整文件跳过
from fa.db import init_db

PAGES = sorted((Path(__file__).resolve().parents[2] / "dashboard" / "pages").glob("*.py"))
assert PAGES, "dashboard/pages/ 下没有页面文件"   # 空目录即失败信号，不让冒烟静默空跑


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_page_renders_on_empty_db(page, tmp_path, monkeypatch):
    monkeypatch.setenv("FA_DB", str(tmp_path / "t.db"))
    init_db(tmp_path / "t.db")
    at = st.testing.v1.AppTest.from_file(str(page), default_timeout=60)
    at.run()
    assert not at.exception
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --group dashboard pytest tests/dashboard/test_pages_smoke.py -v`
Expected: FAIL —— 模块级 `assert PAGES` 在收集期报错（dashboard/pages/ 尚不存在）

- [ ] **Step 3: 写 4 个 B 线页面**

`dashboard/pages/1_📋_B线_总览.py`：

```python
"""B 线 · 总览：bankroll / P&L / 未结注 / 额度水位 / 最近 run（设计 §3 页1）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # dashboard/ 入 path

import plotly.graph_objects as go
import streamlit as st

from loaders import runs, summary

st.header("B 线 · 总览（paper 运营）")
try:
    s = summary()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("bankroll", "—" if s["bankroll"] is None else f"{s['bankroll']:.0f}")
c2.metric("累计 P&L（已结算）", f"{s['pnl']:+.2f}")
c3.metric("未结注", s["n_pending"])
c4.metric("Odds API 剩余额度",
          "—" if s["quota_remaining"] is None else int(s["quota_remaining"]))

pts = [(r["started_at"], r["credits_after"]) for r in s["quota_series"]
       if r["credits_after"] is not None]
if pts:
    fig = go.Figure(go.Scatter(x=[p[0] for p in pts], y=[p[1] for p in pts],
                               mode="lines+markers", name="credits"))
    fig.add_hline(y=100, line_dash="dash",
                  annotation_text="降频阈值 100（spec §3.4）")
    fig.update_layout(height=300, margin=dict(t=30, b=20))
    st.plotly_chart(fig, use_container_width=True)
else:
    st.caption("暂无 run 记录——额度曲线待首个 run 落库后出现")

st.subheader("最近 run")
df = runs().head(5)
st.dataframe(df, use_container_width=True, hide_index=True)
```

`dashboard/pages/2_🎯_B线_推荐与台账.py`：

```python
"""B 线 · 推荐与台账：推荐全维度表 + paper 注明细（设计 §3 页2）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from loaders import paper_bets, recommendations

st.header("B 线 · 推荐与台账")
try:
    recs = recommendations()
    bets = paper_bets()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

st.subheader("推荐")
if recs.empty:
    st.info("暂无推荐——比赛日 matchday run 写入 recommendations（spec §9.6）")
else:
    c1, c2 = st.columns(2)
    strategies = c1.multiselect("strategy", sorted(recs["strategy"].unique()),
                                default=sorted(recs["strategy"].unique()))
    phases = c2.multiselect("phase", sorted(recs["phase"].unique()),
                            default=sorted(recs["phase"].unique()))
    view = recs[recs["strategy"].isin(strategies) & recs["phase"].isin(phases)]
    st.dataframe(view, use_container_width=True, hide_index=True)

st.subheader("paper 注明细")
if bets.empty:
    st.info("暂无 paper 注——M3 起每个比赛日自动落注（spec §7.2）")
else:
    st.dataframe(bets, use_container_width=True, hide_index=True)
    st.caption("clv = odds_taken / Pinnacle 收盘价 − 1，正=买在收盘前更优价（spec §7.3）")
```

`dashboard/pages/3_⚖️_B线_AB双轨进度.py`：

```python
"""B 线 · A/B 双轨进度：§12.3 预注册判据的可视化（设计 §3 页3）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import plotly.graph_objects as go
import streamlit as st

from loaders import ab_tracks

TARGET = 300  # §12.3：累计 ≥300 注

st.header("B 线 · A/B 双轨进度（§12.3 预注册判据）")
try:
    t = ab_tracks()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

c1, c2 = st.columns(2)
for col, (name, tr) in zip((c1, c2), t.items()):
    with col:
        st.subheader(name)
        st.metric("注数", f"{tr['n']} / {TARGET}")
        st.progress(min(tr["n"] / TARGET, 1.0))
        st.metric("ROI（已结算）", "—" if tr["roi"] is None else f"{tr['roi']:+.1%}")
        st.metric("CLV 中位数", "—" if tr["clv_median"] is None
                  else f"{tr['clv_median']:+.2%}")

fig = go.Figure()
for name, tr in t.items():
    if tr["cum"]["dates"]:
        fig.add_trace(go.Scatter(x=tr["cum"]["dates"], y=tr["cum"]["pnl"],
                                 mode="lines+markers", name=name))
fig.update_layout(height=320, margin=dict(t=30, b=20),
                  yaxis_title="累计 P&L（已结算）")
st.plotly_chart(fig, use_container_width=True)

st.caption("§12.3 判据：累计 ≥300 注且 model_persona 轨 CLV>0 或 ROI 显著优于 "
           "model_only 才算胜出。当前样本量见上方进度条——远未达标时这些数字"
           "仅供观察，不构成前向证据。")
```

`dashboard/pages/4_🩺_B线_运维健康.py`：

```python
"""B 线 · 运维健康：runs 历史 / 降级事件 / 队名隔离（设计 §3 页4）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from loaders import runs, unknown

st.header("B 线 · 运维健康")
try:
    df = runs()
    unk = unknown()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

n_degraded = 0
if not df.empty:
    n_degraded = int(df["degraded"].fillna(False).astype(bool).sum())
if n_degraded:
    st.warning(f"{n_degraded} 个 run 带降级标记——展开下方 degraded_reasons 核对"
               "（spec §1.4 诚实降级）")
else:
    st.success("无降级 run")

st.subheader("runs 历史（summary 已展开）")
st.dataframe(df, use_container_width=True, hide_index=True)

st.subheader("队名隔离表（unknown_names）")
if unk.empty:
    st.success("隔离表为空——所有实时盘队名均已对齐（spec §3.3）")
else:
    st.dataframe(unk, use_container_width=True, hide_index=True)
    st.caption("处理方式：uv run fa data aliases 逐条看建议，"
               "fa data aliases --confirm \"TEAM_ID=别名\" 确认写入（页面保持只读）")
```

- [ ] **Step 4: 跑冒烟确认通过**

Run: `uv run --group dashboard pytest tests/dashboard -v`
Expected: 20 passed + 4 page smoke passed（glob 收集到 4 页）

- [ ] **Step 5: Commit**

```bash
git add dashboard/pages/ tests/dashboard/test_pages_smoke.py
git commit -m "feat(dashboard): B 线 4 页（总览/推荐台账/AB 双轨/运维健康）+ AppTest 冒烟"
```

---

### Task 10: A 线 3 页

**Files:**
- Create: `dashboard/pages/5_📊_A线_回测总览.py`、`dashboard/pages/6_📈_A线_校准曲线.py`、`dashboard/pages/7_💰_A线_模拟盘回测.py`

**Interfaces:**
- Consumes: `overview/calibration/paper_sim` loaders（Task 8）；冒烟 glob（Task 9）自动纳入新页
- Produces: 无（终端页面）

- [ ] **Step 1: 先跑冒烟确认新页缺席**

Run: `uv run --group dashboard pytest tests/dashboard/test_pages_smoke.py -v --collect-only -q`
Expected: 收集到 4 页（无 A 线页）——写完后同一命令应收 7 页

- [ ] **Step 2: 写 3 个 A 线页面**

`dashboard/pages/5_📊_A线_回测总览.py`：

```python
"""A 线 · 回测总览：log-loss/Brier vs 去水收盘（设计 §3 页5）。

口径 = walk-forward 全样本复算（与 m2-verdict 同一判据，spec §8.2）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import plotly.express as px
import streamlit as st

from loaders import overview

st.header("A 线 · 回测总览（walk-forward，历史回测）")
try:
    full = overview()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()
if full["empty"]:
    st.info("backtest_predictions 为空——先运行 uv run fa backtest run --from 2019 --to 2025")
    st.stop()

leagues_all = sorted(full["by_league"])
seasons_all = sorted(full["by_season"])
c1, c2 = st.columns(2)
sel_l = c1.multiselect("联赛", leagues_all, default=leagues_all)
sel_s = c2.multiselect("赛季", seasons_all, default=seasons_all)
o = overview(sel_l or None, sel_s or None)   # 空选 → None → 不过滤，走缓存参数一致性
if o["empty"]:
    st.info("该过滤组合无预测行")
    st.stop()

ov = o["overall"]
c1, c2, c3 = st.columns(3)
c1.metric("模型 log-loss", f"{ov['model_ll']:.5f}")
c2.metric("市场 log-loss（去水收盘）", f"{ov['market_ll']:.5f}")
c3.metric("劣化", f"{ov['degradation_pct']:+.2f}%",
          delta=f"判据 ≤ +1% 为 GO（当前 {ov['verdict']}）",
          delta_color="inverse")
st.caption(f"n = {ov['n']} 场 · Brier：模型 {ov['model_brier']:.5f} / 市场 "
           f"{ov['market_brier']:.5f} · 口径同 spec §8.2 / docs/m2-verdict.md")

cross = pd.DataFrame(
    [{"league": k.split("|")[0], "season": int(k.split("|")[1]), **{
        "degradation_pct": v["degradation_pct"], "n": v["n"]}}
     for k, v in o["by_league_season"].items()])
piv = cross.pivot(index="league", columns="season", values="degradation_pct")
st.subheader("劣化（%）热图：联赛 × 赛季")
fig = px.imshow(piv, text_auto=".2f", aspect="auto",
                color_continuous_midpoint=0,
                title="model_ll/market_ll − 1（负=模型优；判据线 +1%）")
st.plotly_chart(fig, use_container_width=True)

st.subheader("分联赛 / 分赛季")
tab1, tab2 = st.tabs(["by league", "by season"])
tab1.dataframe(pd.DataFrame(o["by_league"]).T, use_container_width=True)
tab2.dataframe(pd.DataFrame(o["by_season"]).T, use_container_width=True)
st.caption("A 线为历史回测证据，与 B 线前向运营结论分账（spec §12.3），互不冒充。")
```

`dashboard/pages/6_📈_A线_校准曲线.py`：

```python
"""A 线 · 校准曲线：十分位分组，分市场（设计 §3 页6）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from loaders import calibration, overview

st.header("A 线 · 校准曲线（十分位，复用 metrics.calibration）")
try:
    full = overview()
    cal = calibration()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()
if full["empty"]:
    st.info("backtest_predictions 为空——先跑回测")
    st.stop()

_MARKETS = [("H", "主胜"), ("D", "平局"), ("A", "客胜"), ("O2.5", "大 2.5 球")]
tabs = st.tabs([f"{label}（{key}）" for key, label in _MARKETS])
for tab, (key, _) in zip(tabs, _MARKETS):
    buckets = cal.get(key) or []
    with tab:
        if not buckets:
            st.info("该市场无预测行（如大小球通道未跑）")
            continue
        df = pd.DataFrame(buckets)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=[0, 1], y=[0, 1], mode="lines",
                                 line=dict(dash="dash"), name="理想"))
        fig.add_trace(go.Scatter(x=df["avg_p"], y=df["emp"], mode="markers+lines",
                                 marker=dict(size=df["n"].clip(4, 40),
                                             sizemode="diameter"),
                                 text=[f"n={n}" for n in df["n"]],
                                 name="十分位"))
        fig.update_layout(height=380, xaxis_title="平均预测概率",
                          yaxis_title="实际频率", xaxis_range=[0, 1],
                          yaxis_range=[0, 1], margin=dict(t=30, b=20))
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df, use_container_width=True, hide_index=True)
```

`dashboard/pages/7_💰_A线_模拟盘回测.py`：

```python
"""A 线 · 模拟盘回测：flat vs ¼ Kelly（设计 §3 页7）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from loaders import paper_sim

st.header("A 线 · 模拟盘回测（flat / ¼ Kelly）")
try:
    s = paper_sim()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()
if s["empty"]:
    st.info("无候选注——回测候选由 §5.2 门槛筛出（EV≥3% 且 edge≥2% 且赔率∈[1.4,6.0]）")
    st.stop()

c1, c2 = st.columns(2)
with c1:
    st.subheader("flat（平注 1 单位）")
    st.metric("n", s["flat"]["n"])
    st.metric("P&L", f"{s['flat']['pnl']:+.1f}")
    st.metric("ROI", f"{s['flat']['roi']:+.1%}")
with c2:
    st.subheader("¼ Kelly（上限 2%）")
    st.metric("终值（起 1000）", f"{s['kelly']['final_bankroll']:.1f}")
    st.metric("ROI", f"{s['kelly']['roi']:+.1%}")
    st.metric("最大回撤", f"{s['kelly']['max_drawdown_pct']:.1%}")

left, right = st.columns(2)
flat_df = pd.DataFrame(s["flat_curve"])
kelly_df = pd.DataFrame(s["kelly_curve"])
f1 = go.Figure(go.Scatter(x=flat_df["date"], y=flat_df["pnl"],
                          mode="lines", name="flat P&L"))
f1.update_layout(height=300, yaxis_title="累计 P&L", margin=dict(t=30, b=20))
left.plotly_chart(f1, use_container_width=True)
f2 = go.Figure(go.Scatter(x=kelly_df["date"], y=kelly_df["bankroll"],
                          mode="lines", name="bankroll"))
f2.update_layout(height=300, yaxis_title="bankroll", margin=dict(t=30, b=20))
right.plotly_chart(f2, use_container_width=True)

c3, c4 = st.columns(2)
with c3:
    st.subheader("分市场")
    st.dataframe(pd.DataFrame(s["by_market"]).T, use_container_width=True)
with c4:
    st.subheader("分赔率区间")
    st.dataframe(pd.DataFrame(s["by_band"]).T, use_container_width=True)

st.caption("成交价 = Pinnacle 收盘价（保守）；O2.5 缺收盘赔率，回测候选不含该市场"
           "（candidates 口径）；与 M2 报告同源同口径。")
```

- [ ] **Step 3: 跑冒烟确认 7 页全过**

Run: `uv run --group dashboard pytest tests/dashboard -v`
Expected: 20 queries + 7 pages smoke 全部 passed

- [ ] **Step 4: Commit**

```bash
git add dashboard/pages/
git commit -m "feat(dashboard): A 线 3 页（回测总览/校准/模拟盘）——热图与校准图"
```

---

### Task 11: spec v0.5 → v0.6 增补 + README

**Files:**
- Modify: `spec.md`（4 处）
- Modify: `README.md`（3 处）

**Interfaces:**
- Consumes: 设计文档 `docs/superpowers/specs/2026-09-04-web-dashboard-design.md`（§8 增补清单）
- Produces: spec v0.6（changelog + §7.4 + §9.1 + §9.2）；README 可运行说明

- [ ] **Step 1: spec.md 头部版本与 changelog**

`spec.md` 第 6–7 行（版本/日期行）改为：

```markdown
| 版本 | v0.6（确认稿） |
| 日期 | 2026-09-04 |
```

第 13 行（v0.4→v0.5 变更行）之后插入一行：

```markdown
> v0.5 → v0.6 变更：新增本地只读看板（§7.4）——`dashboard/`（Streamlit + plotly，独立 dependency-group），B/A 线分区观察出口，只读连库、指标口径复用回测模块（设计：docs/superpowers/specs/2026-09-04-web-dashboard-design.md）。
```

- [ ] **Step 2: spec.md §7 末尾加 §7.4**

在 §7.3「核心指标」表格之后、`---`（§8 之前）插入：

```markdown
### 7.4 本地只读看板（v0.6）

- `dashboard/`（Streamlit 多页应用）：B 线运营监控 4 页 + A 线研究可视化 3 页，页面按 §12 双线分区，表边界同 §12.1（A 线页只读 `backtest_predictions`/`matches`）
- 只读连接（`mode=ro`）+ 独立依赖组（`uv run --group dashboard streamlit run dashboard/app.py`）；指标口径单一来源——复用 `backtest/metrics` 与 `backtest/simulate`，看板不另写公式
- 看板是**视图不是证据源**：结论仍以 runs 落库记录与 m*/判决文档为准
```

- [ ] **Step 3: spec.md §9.1 与 §9.2**

§9.1 技术栈列表（`- pandas + scipy、typer（CLI）、pytest、SQLite（stdlib sqlite3）` 一行）后补一行：

```markdown
- dashboard（可选）：streamlit + plotly（dependency-group `dashboard`，本地只读看板，§7.4）
```

§9.2 目录树中 `│   ├── backtest/          # walk-forward、指标、模拟盘` 行后补：

```markdown
├── dashboard/            # 本地只读看板（§7.4）：app.py / loaders.py / queries.py / pages/
```

（注意保持树形图的目录层级一致——`dashboard/` 与 `personas/`、`src/` 平级。）

- [ ] **Step 4: README 三处**

4a. 状态表 M3 行整行（原文：

```markdown
| M3–M5 价值层 / persona / 实盘 | 🚫 不启动 | 项目按 **spec §10 M2 关卡止步**，本仓库仅为可复现的研究结论存档 |
```

——已与 spec v0.5 §12 / CLAUDE.md 的现状脱节，本次随 spec v0.6 一并最小修正）替换为：

```markdown
| M3 B 线运营栈 | ✅ 完成（2026-09-04） | paper 模式真跑实测（[docs/m3-report.md](./docs/m3-report.md)）；M4/M5 paper 运行中（spec §12 双线并存） |
```

4b. 关键文档列表 spec 一行的 `（v0.4 确认稿）` 改为 `（v0.6 确认稿）`。

4c. 在 `## 仓库结构` 标题之前插入一节：

```markdown
## 本地只读看板（v0.6）

```bash
uv run --group dashboard streamlit run dashboard/app.py   # localhost:8501
```

B 线运营监控（bankroll / paper 台账 / CLV / A/B 双轨 / 额度）+ A 线研究可视化
（log-loss 对比 / 校准 / 模拟盘），按 spec §12 双线分区；只读连库，不影响管线。
```

- [ ] **Step 5: 校验与提交**

Run: `uv run pytest -q`
Expected: 全绿（spec/README 改动不碰代码）

```bash
git add spec.md README.md
git commit -m "docs: spec v0.6——新增 §7.4 本地只读看板；README 状态与看板运行说明"
```

---

### Task 12: E2E 验收（真实库 + 双模式测试 + 启动冒烟）

**Files:**
- Create: 无新文件（验证任务；若发现缺陷，修复归入对应文件并在本任务内提交）

**Interfaces:**
- Consumes: 全部前序产物；真实库 `data/fa.db`（只读）
- Produces: 验收记录（作为 commit message 附件说明，不新增 docs 结论文档——诚实条款：dashboard 数字不进 docs 当证据）

- [ ] **Step 1: 裸依赖模式全量测试（依赖隔离证明）**

Run: `uv run pytest -q`
Expected: 全绿；`tests/dashboard/test_pages_smoke.py` 整文件 skip（streamlit 缺席），`test_queries.py` 20 项全跑（不依赖 dashboard 组）

- [ ] **Step 2: dashboard 组全量测试**

Run: `uv run --group dashboard pytest -q`
Expected: 全绿，含 7 页 AppTest 冒烟（27 项 dashboard + 原有全部）

- [ ] **Step 3: 真实库 AppTest 全页**

真实库在主 checkout（`data/` gitignore，worktree 里没有）。用绝对路径指过去（只读，安全）：

```bash
FA_DB=/home/ryea0/Project/Football_Analysis/data/fa.db \
  uv run --group dashboard pytest tests/dashboard/test_pages_smoke.py -v
```

Expected: 7 passed（真实 59k 场 / 11,605 预测 / 28 推荐 / 14 注的渲染路径；`default_timeout=60`——若超时，先查全量前缀复调是否卡在 11k 行：曲线点数 = 唯一日期数 ≈ 几百，单点 O(n)，应秒级完成）

- [ ] **Step 4: 真实启动冒烟**

```bash
uv run --group dashboard streamlit run dashboard/app.py \
  --server.headless true --server.port 8599 &
SRV=$!
sleep 8
curl -sf http://localhost:8599/_stcore/health && echo " HEALTH_OK"
kill $SRV
```

Expected: 输出 `ok HEALTH_OK`（health 端点只证明进程与服务起得来；页面级验证是 Step 3 的职责）；进程干净退出

- [ ] **Step 5: 断言双线分账最后一遍人检**

人检清单（逐页看 Task 9/10 页面代码的 import 与查询）：
- 页 5/6/7（A 线）只调 `overview/calibration/paper_sim` → 底层只碰 `backtest_predictions`（+`matches` FK）✓
- 页 1/2/3/4（B 线）只调 `summary/recommendations/paper_bets/ab_tracks/runs/unknown` → 只碰 B 线表 ✓
- 无任何页面把 A 线与 B 线数字合成单一指标 ✓

- [ ] **Step 6: 收尾提交（如有人检/实测产生的修正）**

```bash
git add -A
git commit -m "chore(dashboard): E2E 验收——真实库 7 页冒烟 + 双模式 pytest + headless 启动"
```

（无修正则跳过本步，不造空提交。）

---

## 计划自查记录（写完即查；发现 6 处缺陷已就地修复）

1. **时间炸弹**：全部种子日期为字面量（`'2026-09-04T11:00:00'` 等），无 `today()/now()` ✓
2. **方向性代入**：`a_overview` 断言 `degradation_pct < 0`（模型优→负）；`b_summary` pnl 手算 25−10=+15；CLV 正=好价，页面 caption 已写方向语义 ✓
3. **阈值可达性**：Kelly 手算两注 f≈0.15 均触 2% 顶（`0.25×0.6=0.15>0.02`）→ 1081.2 先验算；AB 判据 300 注、降频线 100 credits 与 spec §3.4/§12.3 一致 ✓
4. **mock/补丁顺序**：全程真实临时库，无 mock；`monkeypatch.setenv("FA_DB", …)` 在 `AppTest.from_file` 之前，`db_path()` 调用时读 env ✓
5. **命名契约一致**：loaders 导出名与 7 页 import 逐一核对；meta 键 `paper_bankroll`/`odds_quota_remaining` 逐字 ✓
6. **单一事实源**：指标全复用 `metrics`/`simulate`/`gates`；曲线用前缀复调不复制算式；测试库用 `init_db` 不重写 DDL；`_odds_bands` 标签与常量关系已注明 ✓
7. **环境敏感性**：AppTest headless 不涉终端高亮；conftest 的 env 密封与 `monkeypatch.setenv` 不冲突（密封在 teardown 还原）✓
8. **防呆段落禁用**：全部步骤直接给正确版本，无「先错后改」双版本 ✓
9. **诚实条款预写**：页3 常显进度条与样本不足警示；Task 12 明确「dashboard 数字不写进 docs 当结论」；A 线页 caption 标注与 B 线分账 ✓

**本轮自查实际修复的缺陷**（对照上方条款）：
- Task 4 种子冲突：`_seed_ab_tracks` 原基于 `_seed_rec_chain`，rec 1/3 已有注，再插 bets 撞 `UNIQUE(recommendation_id, mode)`——改为基于 `_seed_bline_base` 并补 rec 5/6（条款 5/3）
- Task 12 Step 3 原写了不存在的 pytest 旗标 `--cd .`，且 worktree 无 `data/`（gitignore）——改为绝对路径 `FA_DB`（条款 5）
- Task 12 Step 4 `kill %1` 在非交互 shell 不可靠——改为 `$!` 捕获 PID（条款 3）
- Task 9 冒烟测试漏写模块级 `assert PAGES`，空目录会静默 0 收集通过——已补（条款 3）
- Task 2/3 的测试 import 指令有歧义（重复 import / 无用 import）——改为精确的整行替换与「无需新 import」（条款 5）
- Task 11 README 4a/4c 原引用行不完整、插入位置含糊——补全原文行与锚点（条款 5）
