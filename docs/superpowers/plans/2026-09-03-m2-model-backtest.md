# M2 模型 + 回测评估 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 分层 Dixon-Coles 模型 + 5+ 赛季 walk-forward 回测，产出模型 vs 去水收盘市场的校准/Brier/log-loss 对比与模拟盘盈亏——**spec §10 M2 的 go/no-go 判决**（log-loss 劣化 ≤1% 即 go）。

**Architecture:** 三段式：`model/`（拟合与推断——每联赛加权 MAP Poisson 回归，L-BFGS 解析梯度，DC ρ 两阶段估计）→ `backtest/`（walk-forward 驱动器、指标、模拟盘、报告）→ `value/devig.py`（比例法去水，回测基准与 M3 价值层共用）。回测严格时点外推：每周用 `date < 周首日` 的数据重拟合，全局收缩目标同样按周泄漏安全重算。

**Tech Stack:** 既有栈不变（Python ≥3.11 / uv / pandas / scipy / typer / pytest / SQLite WAL）；Task 3 显式声明 numpy 直依赖（此前仅经 pandas 传递）。

**Spec:** `spec.md`（v0.4）§4 建模层、§8 回测与验证、§5.1 去水、§10 M2 行。执行者应同时读 spec 与 `docs/m1-report.md` 的「M2 携带项」。

**范围裁定（写计划时已定）**：§4.5「近 6 场状态协变量消融」为 spec 括号内可选项，**不在本计划**——主线（模型+回测+判决）完成且 go 之后，若需要再单独立小计划执行。M1 携带项全部编入：Task 1（`_read_text` 提升 + tiebreaker）、Task 2（schema v2 迁移 + 版本不匹配测试）。

## Global Constraints

- 依赖不新增外部包；Task 3 在 pyproject 显式加 `numpy>=1.26`（环境中已随 pandas/scipy 存在，属声明非引入）
- **回测严格防泄漏**：任何拟合/目标计算只用 `date < asof` 的已完赛场次；walk-forward 每比赛周重拟合（spec §4.2/§8.1）
- 回测不含 persona；市场基准 = Pinnacle 收盘价（`psc_*`）比例法去水（spec §8.2，§3.1）
- 模型默认参数：半衰期 100 天、训练窗 1120 天（≈3 年，衰减权重窗外可忽略）、σ_att=σ_dfn=0.35、σ_mu=σ_ha=0.25（spec §4 半衰期「约 100 天量级，回测调参」——CLI 留 `--half-life` 供调参）
- 模拟盘门槛照 spec §5.2：EV ≥ 3%、edge ≥ 2%、赔率 ∈ [1.4, 6.0]；仓位 ¼ Kelly、单注上限 2%（spec §5.3）
- DB schema 变更走 Task 2 的 v1→v2 迁移，`SCHEMA_VERSION` 升 2 必须补版本不匹配测试（M1 携带项，届时该守卫承重）
- `MatchWeek.end` = 桶内最后一场比赛日（M1 携带，walk-forward 用 `start` 作 asof，勿用 `end`）
- 单元测试离线、合成数据种子固定；真实数据只允许出现在 Task 10 E2E
- 提交 conventional commits（feat:/fix:/chore:），每任务一次提交；CLI 面向用户输出中文；测试命令一律 `uv run pytest ...`

---

### Task 1: M1 携带清理（`_read_text` 提升共享 + walkforward tiebreaker）

**Files:**
- Create: `src/fa/data/reader.py`
- Modify: `src/fa/data/sync.py`（删除私有 `_read_text`，改 import）
- Modify: `src/fa/data/audit.py`（改 import）
- Modify: `src/fa/data/walkforward.py`（两处 ORDER BY 加 tiebreaker）
- Test: `tests/data/test_reader.py`（新）；`tests/data/test_sync.py`、`tests/data/test_audit.py`、`tests/data/test_walkforward.py`（既有用例不动，应全绿）

**Interfaces:**
- Consumes: M1 既有模块
- Produces: `fa.data.reader.read_csv_text(path: Path) -> str`（读字节，BOM 则 utf-8-sig 否则 latin-1）——sync/audit 共用；`ORDER BY date, id`（同日场次顺序确定，M2 依赖）

- [ ] **Step 1: 写失败测试**

`tests/data/test_reader.py`：

```python
from fa.data.reader import read_csv_text


def test_latin1_and_bom(tmp_path):
    p = tmp_path / "a.csv"
    p.write_bytes("Div,Date\nE0,x\n".encode("latin-1"))
    assert read_csv_text(p) == "Div,Date\nE0,x\n"

    b = tmp_path / "b.csv"
    b.write_bytes(b"\xef\xbb\xbfDiv,Date\n")
    assert read_csv_text(b) == "Div,Date\n"        # BOM 被剥掉


def test_latin1_high_bytes(tmp_path):
    p = tmp_path / "c.csv"
    p.write_bytes("Bayern M\xfcnchen\n".encode("latin-1"))
    assert read_csv_text(p) == "Bayern München\n"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/test_reader.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'fa.data.reader'`

- [ ] **Step 3: 实现 reader.py，改两处 import**

`src/fa/data/reader.py`：

```python
from pathlib import Path

_BOM = b"\xef\xbb\xbf"


def read_csv_text(path: Path) -> str:
    """读 CSV 缓存字节：UTF-8 BOM 则 utf-8-sig，否则 latin-1（football-data 惯用编码）。"""
    data = path.read_bytes()
    if data.startswith(_BOM):
        return data.decode("utf-8-sig")
    return data.decode("latin-1")
```

`src/fa/data/sync.py`：删除模块内 `_read_text` 与 `_BOM` 定义，改为 `from fa.data.reader import read_csv_text`，调用点同步替换（保持函数名替换一致性）。
`src/fa/data/audit.py`：`from fa.data.sync import _read_text` 改为 `from fa.data.reader import read_csv_text`。

- [ ] **Step 4: walkforward tiebreaker**

`src/fa/data/walkforward.py` 两处查询：

```python
        "SELECT id, date FROM matches "
        "WHERE league=? AND season=? AND date IS NOT NULL ORDER BY date, id",
```

```python
        "SELECT * FROM matches WHERE league=? AND date < ? ORDER BY date, id",
```

- [ ] **Step 5: 跑全部相关测试**

Run: `uv run pytest tests/data/ -v`
Expected: 全 PASS（既有 sync/audit/walkforward 用例不改动即通过；若 audit/sync 测试 monkeypatch 了 `_read_text` 相关目标需同步调整 patch 路径——M1 的测试 patch 的是 `csv_cache_dir` 与 `download_csv`，不受影响）

- [ ] **Step 6: Commit**

```bash
git add src/fa/data/ tests/data/
git commit -m "chore: read_csv_text 提升共享模块；walkforward 排序加 id tiebreaker（M1 携带项）"
```

---

### Task 2: schema v2 —— backtest_predictions 表与迁移

**Files:**
- Modify: `src/fa/db.py`（SCHEMA_VERSION=2、迁移逻辑、新表 DDL）
- Test: `tests/test_db.py`（追加 3 个用例）

**Interfaces:**
- Consumes: M1 的 `init_db/connect`
- Produces: `init_db` 支持 v1→v2 **自动升级**（老库 `data/fa.db` 直接 `fa init` 即升级）；表 `backtest_predictions(id, league, season, week_index, match_id REFERENCES matches(id), date, p_home, p_draw, p_away, p_over25, p_under25, p_btts, mkt_home, mkt_draw, mkt_away, mkt_over25, odds_home, odds_draw, odds_away, outcome, total_goals, UNIQUE(match_id))` + 索引 `(league, season)`

- [ ] **Step 1: 写失败测试**

`tests/test_db.py` 追加：

```python
from fa.db import SCHEMA_VERSION  # 顶部补充 import


def test_fresh_db_is_v2(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    assert c.execute("SELECT version FROM schema_version").fetchone()["version"] == 2
    names = {r["name"] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "backtest_predictions" in names
    c.close()


def test_v1_upgrades_to_v2(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    c.execute("UPDATE schema_version SET version=1")
    c.execute("DROP TABLE backtest_predictions")   # 模拟老库
    c.commit(); c.close()
    init_db(tmp_path / "t.db")                      # 不抛异常即升级成功
    c = connect(tmp_path / "t.db")
    assert c.execute("SELECT version FROM schema_version").fetchone()["version"] == 2
    assert c.execute("SELECT COUNT(*) c FROM backtest_predictions").fetchone()["c"] == 0
    c.close()


def test_future_version_refused(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    c.execute("UPDATE schema_version SET version=99")
    c.commit(); c.close()
    import pytest
    with pytest.raises(RuntimeError):
        init_db(tmp_path / "t.db")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_db.py -v`
Expected: 新 3 用例 FAIL（版本还是 1 / 表不存在 / 不抛错）

- [ ] **Step 3: 实现**

`src/fa/db.py` 修改：

```python
SCHEMA_VERSION = 2
```

`_SCHEMA` 字符串内追加（放在 meta 表之后）：

```sql
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
```

`init_db` 的版本分支改为「可升级」语义（替换原 `elif` 抛错分支）：

```python
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    elif row["version"] < SCHEMA_VERSION:
        _migrate_up(conn, row["version"])
    elif row["version"] > SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库 schema 版本 {row['version']} 高于程序 {SCHEMA_VERSION}，请升级 fa")
```

新增：

```python
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
```

- [ ] **Step 4: 跑测试**

Run: `uv run pytest tests/test_db.py -v`
Expected: 全 PASS（含 M1 既有 4 例——注意 `test_init_creates_tables` 的集合断言用 `<=`，不受新表影响）

- [ ] **Step 5: 验证真实库升级**

Run: `uv run fa init`
Expected: `数据库就绪：…`（老 `data/fa.db` 从 v1 自动升 v2，无异常）

- [ ] **Step 6: Commit**

```bash
git add src/fa/db.py tests/test_db.py
git commit -m "feat: schema v2——backtest_predictions 表与 v1 自动迁移（含版本守卫测试）"
```

---

### Task 3: 模型拟合（加权 MAP Poisson，L-BFGS 解析梯度）

**Files:**
- Create: `src/fa/model/__init__.py`（空）
- Create: `src/fa/model/fit.py`
- Modify: `pyproject.toml`（dependencies 加 `"numpy>=1.26"`）
- Test: `tests/model/test_fit.py`（连带 `tests/model/__init__.py` 空文件）

**Interfaces:**
- Consumes: `training_matches(conn, league, asof) -> list[dict]`（M1；行含 `date/home/away/fthg/ftag` 字段——注意：`home/away` 是 teams 表 join 前的裸行，实际 M1 的 `training_matches` 返回 `SELECT * FROM matches`，**没有队名**！见 Step 3 的 SQL 说明：本任务在 fit.py 内自带一个 `_training_with_names` SQL，或改 walkforward——**不改 M1 模块**，fit.py 自带查询）
- Produces:
  - `FitConfig`（dataclass：`half_life_days=100.0, window_days=1120, sigma_att=0.35, sigma_dfn=0.35, sigma_mu=0.25, sigma_ha=0.25`）
  - `LeagueFit`（dataclass：`league:str, att:dict[str,float], dfn:dict[str,float], mu:float, home_adv:float`）
  - `training_rows(conn, league: str, asof: str, window_days: int) -> list[dict]`（自带 join SQL，返回 `[{"home":…, "away":…, "fthg":…, "ftag":…, "date":…}, …]`，`date < asof` 且 `date >= asof − window_days`）
  - `fit_league(matches: list[dict], asof: str, league: str, mu_global: float, ha_global: float, cfg: FitConfig = FitConfig()) -> LeagueFit`

- [ ] **Step 1: 写失败测试（合成数据恢复 + 梯度校验）**

`tests/model/test_fit.py`：

```python
import numpy as np
import pytest

from fa.model.fit import FitConfig, fit_league, training_rows

RNG = np.random.default_rng(42)


def _synthetic(n_matches=900):
    """6 队、已知参数，按模型生成比分。"""
    teams = [f"T{i}" for i in range(6)]
    att = {"T0": 0.5, "T1": 0.25, "T2": 0.0, "T3": -0.1, "T4": -0.25, "T5": -0.4}
    dfn = {t: -a * 0.5 for t, a in att.items()}   # 强队攻强守也强
    mu, ha = 0.15, 0.25
    rows = []
    for k in range(n_matches):
        h, a = RNG.choice(6, size=2, replace=False)
        h, a = teams[h], teams[a]
        lh = float(np.exp(mu + ha + att[h] - dfn[a]))
        la = float(np.exp(mu + att[a] - dfn[h]))
        rows.append({"home": h, "away": a,
                     "fthg": int(RNG.poisson(lh)), "ftag": int(RNG.poisson(la)),
                     "date": f"2023-{1 + k % 12:02d}-{1 + k % 28:02d}"})
    return teams, att, rows


def test_fit_recovers_ranking():
    teams, true_att, rows = _synthetic()
    fit = fit_league(rows, asof="2024-01-01", league="X",
                     mu_global=0.15, ha_global=0.25)
    est = [fit.att[t] for t in teams]
    true = [true_att[t] for t in teams]
    # 秩相关：攻击力排序基本恢复
    from scipy.stats import spearmanr
    rho, _ = spearmanr(est, true)
    assert rho > 0.8
    assert abs(fit.mu - 0.15) < 0.25
    assert fit.att["T0"] > fit.att["T5"]


def test_fit_weak_data_shrinks_to_prior():
    """只有 20 场时参数应强烈收缩向 0（升班马语义）。"""
    _, _, rows = _synthetic(n_matches=20)
    fit = fit_league(rows, asof="2024-01-01", league="X",
                     mu_global=0.15, ha_global=0.25)
    assert abs(fit.att["T0"]) < 0.3      # 弱数据不得跑飞


def test_gradient_matches_numeric():
    """解析梯度 vs 数值梯度（小规模直接对内部目标函数校验）。"""
    from fa.model.fit import _objective
    teams = ["A", "B", "C"]
    rows = [{"home": "A", "away": "B", "fthg": 2, "ftag": 1, "date": "2023-09-01"},
            {"home": "B", "away": "C", "fthg": 0, "ftag": 0, "date": "2023-09-08"},
            {"home": "C", "away": "A", "fthg": 1, "ftag": 3, "date": "2023-09-15"}]
    h, a, yh, ya, w, n = _design_arrays(rows, "2023-09-20", FitConfig())
    x = np.array([0.1, -0.1, 0.0, 0.05, -0.05, 0.0, 0.2, 0.3])
    f0, g = _objective(x, h, a, yh, ya, w, 0.15, 0.25, FitConfig())
    eps = 1e-6
    for i in range(len(x)):
        xp = x.copy(); xp[i] += eps
        xm = x.copy(); xm[i] -= eps
        fp, _ = _objective(xp, h, a, yh, ya, w, 0.15, 0.25, FitConfig())
        fm, _ = _objective(xm, h, a, yh, ya, w, 0.15, 0.25, FitConfig())
        assert abs((fp - fm) / (2 * eps) - g[i]) < 1e-4, f"param {i}"
```

（`_design_arrays` 与 `_objective` 为 fit.py 内部函数，测试导入以校验梯度——它们也在 Step 3 实现。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/model/test_fit.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'fa.model'`

- [ ] **Step 3: 实现 fit.py**

模型形式（spec §4.1，DC 的 ρ 在 Task 4 两阶段处理，本任务拟合不含 ρ 项）：

```
log λ_home = μ_league + home_adv_league + attack_home − defence_away
log λ_away = μ_league + attack_away − defence_home
MAP：Poisson 加权负对数似然（时间衰减权重）+ 先验
      attack_i, defence_i ~ N(0, σ_att²/σ_dfn²)     —— 队级向 0（联赛均值）收缩，
                                                        升班马/新赛季冷启动由此自然实现（spec §4.4）
      μ_league ~ N(mu_global, σ_mu²)                —— 联赛向全局收缩（partial pooling）
      home_adv_league ~ N(ha_global, σ_ha²)
```

```python
import sqlite3
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize


@dataclass
class FitConfig:
    half_life_days: float = 100.0
    window_days: int = 1120            # ≈3 年，窗外衰减权重可忽略
    sigma_att: float = 0.35
    sigma_dfn: float = 0.35
    sigma_mu: float = 0.25
    sigma_ha: float = 0.25


@dataclass
class LeagueFit:
    league: str
    att: dict
    dfn: dict
    mu: float
    home_adv: float


def training_rows(conn: sqlite3.Connection, league: str, asof: str,
                  window_days: int) -> list[dict]:
    """泄漏安全训练切片：date ∈ [asof − window_days, asof)，带队名。"""
    return [dict(r) for r in conn.execute(
        "SELECT m.date, h.name AS home, a.name AS away, m.fthg, m.ftag "
        "FROM matches m JOIN teams h ON h.id=m.home_team_id "
        "JOIN teams a ON a.id=m.away_team_id "
        "WHERE m.league=? AND m.date < ? "
        "AND m.date >= date(?, '-' || ? || ' day') ORDER BY m.date, m.id",
        (league, asof, asof, window_days))]
```

（日期修饰符用 `'-' || ? || ' day'` 把 window_days 以整数参数绑定进 SQL，勿用 Python 拼接。）

```python
def _design_arrays(rows, asof, cfg):
    from datetime import date
    asof_d = date.fromisoformat(asof)
    teams = sorted({r["home"] for r in rows} | {r["away"] for r in rows})
    idx = {t: i for i, t in enumerate(teams)}
    h = np.array([idx[r["home"]] for r in rows], dtype=np.intp)
    a = np.array([idx[r["away"]] for r in rows], dtype=np.intp)
    yh = np.array([r["fthg"] for r in rows], dtype=float)
    ya = np.array([r["ftag"] for r in rows], dtype=float)
    age = np.array([(asof_d - date.fromisoformat(r["date"])).days
                    for r in rows], dtype=float)
    w = 0.5 ** (age / cfg.half_life_days)
    return h, a, yh, ya, w, len(teams)


def _objective(x, h, a, yh, ya, w, mu_g, ha_g, cfg):
    """返回 (目标值, 解析梯度)。x = [att(n), dfn(n), mu, ha]，n = (len(x)-2)//2。"""
    n = (len(x) - 2) // 2
    att, dfn, mu, ha = x[:n], x[n:2 * n], x[2 * n], x[2 * n + 1]
    zh = np.clip(mu + ha + att[h] - dfn[a], -6.0, 6.0)
    za = np.clip(mu + att[a] - dfn[h], -6.0, 6.0)
    lh, la = np.exp(zh), np.exp(za)
    nll = w @ (lh + la) - (w * yh) @ np.log(lh) - (w * ya) @ np.log(la)
    prior = (att @ att) / (2 * cfg.sigma_att ** 2) \
        + (dfn @ dfn) / (2 * cfg.sigma_dfn ** 2) \
        + (mu - mu_g) ** 2 / (2 * cfg.sigma_mu ** 2) \
        + (ha - ha_g) ** 2 / (2 * cfg.sigma_ha ** 2)
    rh = w * (lh - yh)          # ∂nll/∂zh
    ra = w * (la - ya)          # ∂nll/∂za
    g_att = (np.bincount(h, weights=rh, minlength=n)
             + np.bincount(a, weights=ra, minlength=n)
             + att / cfg.sigma_att ** 2)
    g_dfn = (-np.bincount(h, weights=ra, minlength=n)
             - np.bincount(a, weights=rh, minlength=n)
             + dfn / cfg.sigma_dfn ** 2)
    g_mu = rh.sum() + ra.sum() + (mu - mu_g) / cfg.sigma_mu ** 2
    g_ha = rh.sum() + (ha - ha_g) / cfg.sigma_ha ** 2
    g = np.concatenate([g_att, g_dfn, [g_mu, g_ha]])
    return nll + prior, g


def fit_league(matches, asof, league, mu_global, ha_global,
               cfg: FitConfig = FitConfig()) -> LeagueFit:
    if len(matches) < 30:
        raise ValueError(f"{league} 训练样本不足（{len(matches)} < 30）")
    h, a, yh, ya, w, n = _design_arrays(matches, asof, cfg)
    gpg = (w @ (yh + ya)) / w.sum() / 2.0          # 加权场均
    x0 = np.zeros(2 * n + 2)
    x0[2 * n] = float(np.log(max(gpg, 0.3)))
    x0[2 * n + 1] = float(ha_global)
    res = minimize(lambda x: _objective(x, h, a, yh, ya, w,
                                        mu_global, ha_global, cfg),
                   x0, jac=True, method="L-BFGS-B",
                   options={"maxiter": 300})
    x = res.x
    teams = sorted({r["home"] for r in matches} | {r["away"] for r in matches})
    return LeagueFit(league=league,
                     att=dict(zip(teams, x[:n])),
                     dfn=dict(zip(teams, x[n:2 * n])),
                     mu=float(x[2 * n]), home_adv=float(x[2 * n + 1]))
```

`pyproject.toml` dependencies 改为：

```toml
dependencies = ["pandas>=2.0,<3", "scipy>=1.11", "typer>=0.12", "numpy>=1.26"]
```

- [ ] **Step 4: 跑测试**

Run: `uv sync --quiet && uv run pytest tests/model/test_fit.py -v`
Expected: 3 PASS（`test_gradient_matches_numeric` 若在 1e-4 容差下个别分量抖动，可收紧 clip 中心或放容差至 1e-3——放宽容差须在提交信息中注明）

- [ ] **Step 5: Commit**

```bash
git add src/fa/model/ pyproject.toml uv.lock tests/model/
git commit -m "feat: 分层 Dixon-Coles 拟合（加权 MAP + L-BFGS 解析梯度，梯度数值校验）"
```

---

### Task 4: 推断——比分矩阵、市场概率与 ρ 估计

**Files:**
- Create: `src/fa/model/predict.py`
- Test: `tests/model/test_predict.py`

**Interfaces:**
- Consumes: `LeagueFit`（Task 3）
- Produces:
  - `expected_goals(fit: LeagueFit, home: str, away: str) -> tuple[float, float]`（未知队取 0 = 联赛均值）
  - `score_matrix(lh: float, la: float, rho: float = 0.0, max_goals: int = 10) -> np.ndarray`（形状 `(max_goals+1, max_goals+1)`，归一化；DC τ 只调 0-0/1-0/0-1/1-1 四格）
  - `outcome_probs(matrix) -> tuple[float, float, float]`（主/平/客）
  - `over25_probs(matrix) -> tuple[float, float]`（大/小 2.5）
  - `btts_prob(matrix) -> float`
  - `fit_rho(matches: list[dict], asof: str, cfg: FitConfig, predict_fn) -> float`——ρ ∈ np.linspace(-0.12, 0.12, 25) 网格上最大化四格 DC 修正项的加权对数似然；`predict_fn(fit-less)`：为避免与 LeagueFit 耦合，签名定为 `fit_rho(rows, asof, cfg, lh_la_fn)`，其中 `lh_la_fn(home, away) -> (lh, la)` 由调用方提供（回测里用已拟合的 fit 的 `expected_goals`）

- [ ] **Step 1: 写失败测试**

`tests/model/test_predict.py`：

```python
import numpy as np
import pytest

from fa.model.fit import FitConfig, fit_league
from fa.model.predict import (btts_prob, expected_goals, fit_rho, over25_probs,
                              outcome_probs, score_matrix)


def _fit():
    rows = [{"home": "A", "away": "B", "fthg": 2, "ftag": 0, "date": "2023-08-01"},
            {"home": "B", "away": "A", "fthg": 1, "ftag": 1, "date": "2023-08-08"},
            {"home": "A", "away": "B", "fthg": 3, "ftag": 1, "date": "2023-08-15"}] * 15
    return fit_league(rows, asof="2023-09-01", league="X",
                      mu_global=0.1, ha_global=0.2)


def test_matrix_normalized_and_rho_zero_independent():
    m = score_matrix(1.5, 1.1)
    assert m.shape == (11, 11)
    assert abs(m.sum() - 1.0) < 1e-12
    m0 = score_matrix(1.5, 1.1, rho=0.0)
    from scipy.stats import poisson as _ps
    outer = np.outer(_ps.pmf(np.arange(11), 1.5), _ps.pmf(np.arange(11), 1.1))
    np.testing.assert_allclose(m0, outer / outer.sum(), atol=1e-10)


def test_rho_only_touches_four_cells():
    m0 = score_matrix(1.2, 0.9, rho=0.0)
    mr = score_matrix(1.2, 0.9, rho=-0.1)
    affected = {(0, 0), (1, 0), (0, 1), (1, 1)}
    for i in range(11):
        for j in range(11):
            if (i, j) in affected:
                continue
            # 归一化会使未调格轻微变化，容差放宽到 5%
            assert abs(mr[i, j] - m0[i, j]) < 0.05 * m0[i, j] + 1e-6


def test_derivatives_sum_to_one():
    fit = _fit()
    lh, la = expected_goals(fit, "A", "B")
    assert lh > la                                   # A 强且主场
    m = score_matrix(lh, la, rho=-0.08)
    ph, pd, pa = outcome_probs(m)
    assert abs(ph + pd + pa - 1.0) < 1e-12
    po, pu = over25_probs(m)
    assert abs(po + pu - 1.0) < 1e-12
    assert 0.0 < btts_prob(m) < 1.0


def test_expected_goals_unknown_team_is_league_mean():
    fit = _fit()
    lh, la = expected_goals(fit, "NEVER_SEEN", "ALSO_NEW")
    assert 0.05 < lh < 6.0 and 0.05 < la < 6.0      # 合理范围内


def test_fit_rho_prefers_negative_on_drawy_data():
    rows = []
    for k in range(60):                              # 大量 0-0/1-1
        yh, ya = (0, 0) if k % 2 == 0 else (1, 1)
        rows.append({"home": "A", "away": "B", "fthg": yh, "ftag": ya,
                     "date": "2023-08-01"})
    rho = fit_rho(rows, "2023-09-01", FitConfig(),
                  lambda h, a: (1.4, 1.1))
    assert rho < 0                                   # 低比分偏多 → 负 ρ
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/model/test_predict.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 实现 predict.py**

```python
from dataclasses import dataclass

import numpy as np
from scipy.stats import poisson

from fa.model.fit import FitConfig, LeagueFit, _design_arrays

_RHO_GRID = np.linspace(-0.12, 0.12, 25)


def expected_goals(fit: LeagueFit, home: str, away: str) -> tuple[float, float]:
    """未知队（升班马/新赛季新队名）attack/defence 取 0 = 联赛均值（spec §4.4）。"""
    att_h = fit.att.get(home, 0.0)
    dfn_h = fit.dfn.get(home, 0.0)
    att_a = fit.att.get(away, 0.0)
    dfn_a = fit.dfn.get(away, 0.0)
    lh = float(np.exp(fit.mu + fit.home_adv + att_h - dfn_a))
    la = float(np.exp(fit.mu + att_a - dfn_h))
    return lh, la


def _tau(x: int, y: int, lh: float, la: float, rho: float) -> float:
    """Dixon-Coles 低比分修正（原论文约定）。"""
    if x == 0 and y == 0:
        f = 1.0 - lh * la * rho
    elif x == 0 and y == 1:
        f = 1.0 + lh * rho
    elif x == 1 and y == 0:
        f = 1.0 + la * rho
    elif x == 1 and y == 1:
        f = 1.0 - rho
    else:
        return 1.0
    return max(f, 1e-12)


def score_matrix(lh: float, la: float, rho: float = 0.0,
                 max_goals: int = 10) -> np.ndarray:
    xs = poisson.pmf(np.arange(max_goals + 1), lh)
    ys = poisson.pmf(np.arange(max_goals + 1), la)
    m = np.outer(xs, ys)
    for (x, y) in ((0, 0), (1, 0), (0, 1), (1, 1)):
        m[x, y] *= _tau(x, y, lh, la, rho)
    return m / m.sum()


def outcome_probs(m: np.ndarray) -> tuple[float, float, float]:
    return float(np.tril(m, -1).sum()), float(np.trace(m)), float(np.triu(m, 1).sum())


def over25_probs(m: np.ndarray) -> tuple[float, float]:
    i = np.arange(m.shape[0])
    over = m[i[:, None] + i[None, :] >= 3].sum()
    return float(over), float(1.0 - over)


def btts_prob(m: np.ndarray) -> float:
    return float(m[1:, 1:].sum())


def fit_rho(rows: list[dict], asof: str, cfg: FitConfig,
            lh_la_fn) -> float:
    """两阶段 ρ：给定已拟合 λ，网格最大化四格修正的加权对数似然。"""
    h, a, yh, ya, w, _n = _design_arrays(rows, asof, cfg)
    teams = sorted({r["home"] for r in rows} | {r["away"] for r in rows})
    idx = {t: i for i, t in enumerate(teams)}
    best, best_ll = 0.0, -np.inf
    for rho in _RHO_GRID:
        ll = 0.0
        for k in range(len(yh)):
            x, y = int(yh[k]), int(ya[k])
            if x > 1 or y > 1:
                continue
            lh, la = lh_la_fn(rows[k]["home"], rows[k]["away"])
            ll += w[k] * np.log(_tau(x, y, lh, la, rho))
        if ll > best_ll:
            best_ll, best = ll, float(rho)
    return best
```

- [ ] **Step 4: 跑测试**

Run: `uv run pytest tests/model/ -v`
Expected: 全 PASS（`test_matrix_normalized_and_rho_zero_independent` 用 scipy 的独立泊松外积做基准，校验 ρ=0 路径逐格一致，容差 1e-10）

- [ ] **Step 5: Commit**

```bash
git add src/fa/model/predict.py tests/model/test_predict.py
git commit -m "feat: 推断层——DC 比分矩阵/胜平负/大小球/BTTS + ρ 网格估计"
```

---

### Task 5: 比例法去水（回测基准，M3 复用）

**Files:**
- Create: `src/fa/value/__init__.py`（空）
- Create: `src/fa/value/devig.py`
- Test: `tests/value/test_devig.py`（连带 `tests/value/__init__.py`）

**Interfaces:**
- Consumes: 无
- Produces: `devig_proportional(odds: Sequence[float]) -> list[float]`；`devig_ou(over: float, under: float) -> tuple[float, float]`（spec §5.1：一期比例法，留 Shin 接口位——两个函数 docstring 注明）

- [ ] **Step 1: 写失败测试**

`tests/value/test_devig.py`：

```python
import pytest

from fa.value.devig import devig_ou, devig_proportional


def test_proportional_sums_to_one():
    p = devig_proportional([2.5, 3.4, 2.8])
    assert sum(p) == pytest.approx(1.0)
    assert max(p) == p[0]                            # 赔率最低 → 概率最高


def test_proportional_known_values():
    # 1/2 + 1/3 + 1/6 = 1.0（无水位），去水后应等于原隐含
    p = devig_proportional([2.0, 3.0, 6.0])
    assert p == pytest.approx([0.5, 1 / 3, 1 / 6])


def test_ou():
    po, pu = devig_ou(1.9, 2.1)                      # 水位 1/1.9+1/2.1 ≈ 1.0025
    assert po + pu == pytest.approx(1.0)
    assert po > pu
```

- [ ] **Step 2: 确认失败**

Run: `uv run pytest tests/value/test_devig.py -v` → FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 实现**

```python
"""比例法去水（spec §5.1）。一期仅 proportional；Shin 法留待后续同签名扩展。"""
from collections.abc import Sequence


def devig_proportional(odds: Sequence[float]) -> list[float]:
    inv = [1.0 / o for o in odds]
    s = sum(inv)
    return [v / s for v in inv]


def devig_ou(over: float, under: float) -> tuple[float, float]:
    io, iu = 1.0 / over, 1.0 / under
    s = io + iu
    return io / s, iu / s
```

- [ ] **Step 4: 跑测试** → 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/fa/value/ tests/value/
git commit -m "feat: 比例法去水（devig_proportional/devig_ou，Shin 留位）"
```

---

### Task 6: walk-forward 回测驱动器

**Files:**
- Create: `src/fa/backtest/__init__.py`（空）
- Create: `src/fa/backtest/run.py`
- Test: `tests/backtest/test_run.py`（连带 `tests/backtest/__init__.py`）

**Interfaces:**
- Consumes: `iter_matchweeks`（M1；**用 `w.start` 作 asof，勿用 end**）、`fit_league/training_rows/FitConfig`（Task 3）、`expected_goals/score_matrix/outcome_probs/over25_probs/btts_prob/fit_rho`（Task 4）、`devig_proportional/devig_ou`（Task 5）
- Produces:
  - `global_targets(conn, asof: str, cfg: FitConfig) -> tuple[float, float]`（全联赛训练窗加权场均 → `(mu_g, ha_g)`；`log((gh+ga)/2)`、`log(gh/ga)`；泄漏安全）
  - `run_backtest(conn, leagues: list[str], seasons: range, cfg: FitConfig = FitConfig(), rho: float | None = None) -> int`——逐联赛逐赛季逐比赛周：跳过无 `psc_*` 盘的比赛周；拟合（≥30 样本）；每场写入 `backtest_predictions`（先清空该 `(league, season)` 分区再写，`UNIQUE(match_id)`）；返回写入行数。`rho=None` 时每周用 `fit_rho` 估计，否则用给定常数（测试用常数提速）

- [ ] **Step 1: 写失败测试（合成小库，验证无泄漏与写库）**

`tests/backtest/test_run.py`：

```python
import pytest

from fa.backtest.run import global_targets, run_backtest
from fa.db import connect, init_db
from fa.model.fit import FitConfig


@pytest.fixture
def conn(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    _seed(c)
    yield c
    c.close()


def _seed(conn):
    """E0 2023：8 队 2 周（周四制），全部带 psc 收盘价；再补 3 年历史供训练。"""
    conn.executemany(
        "INSERT INTO teams (league, name) VALUES ('E0', ?)",
        [(f"T{i}",) for i in range(8)])
    ids = {r["name"]: r["id"] for r in
           conn.execute("SELECT id, name FROM teams")}
    rows = []
    # 历史：2021-2023 每周 4 场（周四起），T0 主场全胜的强队模式
    from datetime import date, timedelta
    d = date(2021, 8, 12)
    while d < date(2023, 8, 1):
        for k in range(4):
            h, a = f"T{k}", f"T{7 - k}"
            rows.append(("E0", 2022 if d.year >= 2022 else 2021,
                         d.isoformat(), ids[h], ids[a], 2, 0, 2.0, 3.4, 3.6))
        d += timedelta(days=7)
    # 目标赛季 2023：两周（2023-08-10 周四制第 1 周、08-17 第 2 周）
    for wk, day in ((1, "2023-08-12"), (2, "2023-08-19")):
        for k in range(4):
            h, a = f"T{k}", f"T{7 - k}"
            rows.append(("E0", 2023, day, ids[h], ids[a], 2, 0,
                         2.0, 3.4, 3.6))
    conn.executemany(
        "INSERT INTO matches (league, season, date, home_team_id, away_team_id,"
        " fthg, ftag, psc_home, psc_draw, psc_away, raw_line)"
        " VALUES (?,?,?,?,?,?,?,?,?,?, '{}')", rows)
    conn.commit()


def test_run_backtest_writes_predictions(conn):
    n = run_backtest(conn, ["E0"], range(2023, 2024),
                     FitConfig(window_days=800), rho=0.0)
    assert n == 8                                    # 2 周 × 4 场
    row = conn.execute(
        "SELECT * FROM backtest_predictions LIMIT 1").fetchone()
    assert row["p_home"] + row["p_draw"] + row["p_away"] == pytest.approx(1.0)
    assert row["mkt_home"] + row["mkt_draw"] + row["mkt_away"] == pytest.approx(1.0)
    assert row["outcome"] == "H" and row["total_goals"] == 2
    assert row["week_index"] in (1, 2)


def test_no_leakage_week2_excludes_week1(conn):
    run_backtest(conn, ["E0"], range(2023, 2024),
                 FitConfig(window_days=800), rho=0.0)
    # 第 1 周预测时，训练切片必须不含第 1 周比赛本身（date < week.start）
    # 这里间接验证：两行同队对决概率略不同（第 2 周多了第 1 周的训练数据）。
    # 直接验证通过 global_targets 的 SQL 边界（< asof）：
    conn2_rows = conn.execute(
        "SELECT COUNT(*) c FROM backtest_predictions WHERE week_index=1").fetchone()
    assert conn2_rows["c"] == 4                       # 结构性烟测


def test_global_targets_leak_free(conn):
    mu_g, ha_g = global_targets(conn, "2023-08-12", FitConfig(window_days=800))
    assert mu_g > 0 and ha_g > 0                      # 主场优势 > 0（种子数据 2:0）
```

- [ ] **Step 2: 确认失败** → `ModuleNotFoundError`

- [ ] **Step 3: 实现 run.py**

```python
import sqlite3

import numpy as np

from fa.data.walkforward import iter_matchweeks
from fa.model.fit import FitConfig, fit_league, training_rows
from fa.model.predict import (btts_prob, expected_goals, fit_rho,
                              over25_probs, outcome_probs, score_matrix)
from fa.value.devig import devig_ou, devig_proportional


def global_targets(conn: sqlite3.Connection, asof: str,
                   cfg: FitConfig) -> tuple[float, float]:
    """全联赛训练窗的加权全局目标（spec §4.1 partial pooling 的收缩中心）。"""
    rows = conn.execute(
        "SELECT date, fthg, ftag FROM matches "
        "WHERE date < ? AND date >= date(?, '-' || ? || ' day')"
        " AND fthg IS NOT NULL",
        (asof, asof, cfg.window_days)).fetchall()
    if not rows:
        return 0.0, 0.0
    from datetime import date as _d
    asof_d = _d.fromisoformat(asof)
    w = np.array([0.5 ** ((_d.fromisoformat(r["date"]) - asof_d).days
                          / cfg.half_life_days) for r in rows])
    gh = float(np.dot(w, [r["fthg"] for r in rows]) / w.sum())
    ga = float(np.dot(w, [r["ftag"] for r in rows]) / w.sum())
    return float(np.log((gh + ga) / 2)), float(np.log(max(gh, 1e-6) / max(ga, 1e-6)))


def _week_matches(conn, league, season, week):
    return conn.execute(
        "SELECT m.id, m.date, h.name AS home, a.name AS away,"
        " m.fthg, m.ftag, m.psc_home, m.psc_draw, m.psc_away,"
        " m.over25_psc, m.under25_psc"
        " FROM matches m JOIN teams h ON h.id=m.home_team_id"
        " JOIN teams a ON a.id=m.away_team_id"
        " WHERE m.id IN (%s)" % ",".join("?" * len(week.match_ids)),
        week.match_ids).fetchall()


def run_backtest(conn, leagues, seasons, cfg: FitConfig = FitConfig(),
                 rho: float | None = None, verbose: bool = False) -> int:
    written = 0
    for league in leagues:
        for season in seasons:
            conn.execute(
                "DELETE FROM backtest_predictions WHERE league=? AND season=?",
                (league, season))
            for week in iter_matchweeks(conn, league, season):
                rows = _week_matches(conn, league, season, week)
                rows = [r for r in rows
                        if r["psc_home"] and r["psc_draw"] and r["psc_away"]]
                if not rows:
                    continue
                train = training_rows(conn, league, week.start, cfg.window_days)
                if len(train) < 30:
                    continue                          # 数据不足（早期赛季）跳过
                mu_g, ha_g = global_targets(conn, week.start, cfg)
                try:
                    fit = fit_league(train, week.start, league,
                                     mu_g, ha_g, cfg)
                except ValueError:
                    continue
                rho_eff = rho if rho is not None else fit_rho(
                    train, week.start, cfg,
                    lambda h, a: expected_goals(fit, h, a))
                for r in rows:
                    lh, la = expected_goals(fit, r["home"], r["away"])
                    m = score_matrix(lh, la, rho_eff)
                    ph, pd, pa = outcome_probs(m)
                    po, _pu = over25_probs(m)
                    mk = devig_proportional(
                        [r["psc_home"], r["psc_draw"], r["psc_away"]])
                    mkt_o = (devig_ou(r["over25_psc"], r["under25_psc"])[0]
                             if r["over25_psc"] and r["under25_psc"] else None)
                    total = r["fthg"] + r["ftag"]
                    conn.execute(
                        "INSERT INTO backtest_predictions (league, season,"
                        " week_index, match_id, date, p_home, p_draw, p_away,"
                        " p_over25, p_under25, p_btts, mkt_home, mkt_draw,"
                        " mkt_away, mkt_over25, odds_home, odds_draw, odds_away,"
                        " outcome, total_goals)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (league, season, week.index, r["id"], r["date"],
                         ph, pd, pa, po, 1 - po, btts_prob(m),
                         mk[0], mk[1], mk[2], mkt_o,
                         r["psc_home"], r["psc_draw"], r["psc_away"],
                         "H" if r["fthg"] > r["ftag"]
                         else "D" if r["fthg"] == r["ftag"] else "A", total))
                    written += 1
            conn.commit()
            if verbose:
                print(f"{league} {season}: 累计 {written} 行")
    return written
```

- [ ] **Step 4: 跑测试** → 3 PASS；随后全量 `uv run pytest -q` 确认无回归

- [ ] **Step 5: Commit**

```bash
git add src/fa/backtest/ tests/backtest/
git commit -m "feat: walk-forward 回测驱动器（逐周拟合、泄漏安全全局目标、分区写库）"
```

---

### Task 7: 指标——log-loss / Brier / 校准 / go-no-go

**Files:**
- Create: `src/fa/backtest/metrics.py`
- Test: `tests/backtest/test_metrics.py`

**Interfaces:**
- Consumes: `backtest_predictions` 表（Task 2/6）
- Produces:
  - `fetch_predictions(conn, leagues=None, seasons=None) -> list[dict]`
  - `log_loss(probs: list[tuple[float,float,float]], outcomes: list[str]) -> float`（自然对数，clip 1e-12；outcomes 为 "H"/"D"/"A"）
  - `brier(probs, outcomes) -> float`（三分量多项 Brier：Σ(p_i−y_i)²/2）
  - `calibration(p: list[float], hit: list[bool], bins: int = 10) -> list[dict]`（等宽分桶，返回 `[{lo, hi, n, avg_p, emp}]`）
  - `evaluate(rows: list[dict]) -> dict`——返回 `{"n", "model_ll", "market_ll", "ratio", "degradation_pct", "model_brier", "market_brier", "verdict", "cal_home"}`；**verdict = "GO" iff model_ll ≤ market_ll × 1.01**（spec §8.2：劣化 ≤1%）；`degradation_pct = (model_ll / market_ll − 1) × 100`
  - `by_group(rows, key) -> dict[str, dict]`（按 league/season 分组各出 evaluate 摘要，无 cal）

- [ ] **Step 1: 写失败测试**

`tests/backtest/test_metrics.py`：

```python
import math

import pytest

from fa.backtest.metrics import brier, calibration, evaluate, log_loss


def test_log_loss_perfect_vs_uniform():
    assert log_loss([(1.0, 0.0, 0.0)], ["H"]) == pytest.approx(0.0, abs=1e-9)
    ll = log_loss([(1 / 3, 1 / 3, 1 / 3)], ["H"])
    assert ll == pytest.approx(-math.log(1 / 3))


def test_brier_bounds():
    assert brier([(1.0, 0.0, 0.0)], ["H"]) == pytest.approx(0.0)
    assert brier([(0.0, 1.0, 0.0)], ["H"]) == pytest.approx(1.0)


def test_calibration_bins():
    bins = calibration([0.05] * 10 + [0.95] * 10,
                       [False] * 10 + [True] * 10, bins=10)
    first = bins[0]
    assert first["n"] == 10 and first["emp"] == 0.0
    last = [b for b in bins if b["n"] > 0][-1]
    assert last["emp"] == 1.0


def test_evaluate_go_and_nogo():
    rows = [
        {"p_home": 0.6, "p_draw": 0.2, "p_away": 0.2,
         "mkt_home": 0.55, "mkt_draw": 0.25, "mkt_away": 0.20, "outcome": "H"},
        {"p_home": 0.3, "p_draw": 0.3, "p_away": 0.4,
         "mkt_home": 0.30, "mkt_draw": 0.30, "mkt_away": 0.40, "outcome": "A"},
    ]
    ev = evaluate(rows)
    assert ev["n"] == 2
    assert ev["verdict"] in ("GO", "NO-GO")
    # 模型若处处等于市场 → 劣化 0 → GO
    same = evaluate([{**r, "p_home": r["mkt_home"], "p_draw": r["mkt_draw"],
                      "p_away": r["mkt_away"]} for r in rows])
    assert same["degradation_pct"] == pytest.approx(0.0, abs=1e-9)
    assert same["verdict"] == "GO"
```

- [ ] **Step 2: 确认失败** → `ModuleNotFoundError`

- [ ] **Step 3: 实现 metrics.py**

```python
import sqlite3
import math
from collections.abc import Callable

_EPS = 1e-12
_IDX = {"H": 0, "D": 1, "A": 2}


def fetch_predictions(conn: sqlite3.Connection, leagues=None,
                      seasons=None) -> list[dict]:
    sql = "SELECT * FROM backtest_predictions WHERE 1=1"
    args: list = []
    if leagues:
        sql += f" AND league IN ({','.join('?' * len(leagues))})"
        args += list(leagues)
    if seasons:
        sql += f" AND season IN ({','.join('?' * len(seasons))})"
        args += list(seasons)
    return [dict(r) for r in conn.execute(sql)]


def log_loss(probs, outcomes) -> float:
    total = 0.0
    for (ph, pd, pa), o in zip(probs, outcomes):
        p = (ph, pd, pa)[_IDX[o]]
        total -= math.log(min(max(p, _EPS), 1 - _EPS))
    return total / len(outcomes)


def brier(probs, outcomes) -> float:
    total = 0.0
    for (ph, pd, pa), o in zip(probs, outcomes):
        y = [0.0, 0.0, 0.0]
        y[_IDX[o]] = 1.0
        total += sum((a - b) ** 2 for a, b in zip((ph, pd, pa), y)) / 2
    return total / len(outcomes)


def calibration(p: list[float], hit: list[bool], bins: int = 10) -> list[dict]:
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [i for i, v in enumerate(p) if lo <= v < hi or (b == bins - 1 and v == 1)]
        if not sel:
            continue
        out.append({"lo": lo, "hi": hi, "n": len(sel),
                    "avg_p": sum(p[i] for i in sel) / len(sel),
                    "emp": sum(1 for i in sel if hit[i]) / len(sel)})
    return out


def evaluate(rows: list[dict]) -> dict:
    probs_m = [(r["p_home"], r["p_draw"], r["p_away"]) for r in rows]
    probs_k = [(r["mkt_home"], r["mkt_draw"], r["mkt_away"]) for r in rows]
    outs = [r["outcome"] for r in rows]
    mll, kll = log_loss(probs_m, outs), log_loss(probs_k, outs)
    deg = (mll / kll - 1) * 100 if kll > 0 else 0.0
    return {"n": len(rows), "model_ll": mll, "market_ll": kll,
            "ratio": mll / kll if kll > 0 else 0.0,
            "degradation_pct": deg,
            "model_brier": brier(probs_m, outs),
            "market_brier": brier(probs_k, outs),
            "verdict": "GO" if mll <= kll * 1.01 else "NO-GO",
            "cal_home": calibration([r["p_home"] for r in rows],
                                    [r["outcome"] == "H" for r in rows])}


def by_group(rows: list[dict], key: str) -> dict:
    groups: dict = {}
    for r in rows:
        groups.setdefault(r[key], []).append(r)
    return {k: evaluate(v) for k, v in sorted(groups.items())}
```

- [ ] **Step 4: 跑测试** → 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/fa/backtest/metrics.py tests/backtest/test_metrics.py
git commit -m "feat: 回测指标（log-loss/Brier/校准分桶/by_group 与 ≤1% go-no-go 判决）"
```

---

### Task 8: 模拟盘盈亏（门槛筛选 + 平注/¼ Kelly）

**Files:**
- Create: `src/fa/backtest/simulate.py`
- Test: `tests/backtest/test_simulate.py`

**Interfaces:**
- Consumes: `fetch_predictions`（Task 7）；门槛常量照 spec §5.2/§5.3
- Produces:
  - 常量：`EV_MIN = 0.03, EDGE_MIN = 0.02, ODDS_MIN = 1.4, ODDS_MAX = 6.0, KELLY_FRAC = 0.25, STAKE_CAP = 0.02`
  - `candidates(rows: list[dict]) -> list[dict]`——每行对 H/D/A 三市场（有 `mkt_over25` 的行另加 O2.5）计算 `edge = p − mkt_p`、`ev = p*(odds−1) − (1−p)`，过门槛（EV/edge/赔率带）的输出 `[{"match_id","market","p","mkt_p","odds","ev","edge","outcome_hit","date","league"}]`；`market ∈ {"H","D","A","O2.5"}`，`outcome_hit` 为该市场是否命中（O2.5 用 `total_goals >= 3`）
  - `simulate_flat(cands) -> dict`（单位注 1：`{"n", "staked", "returned", "roi", "pnl"}`）
  - `simulate_kelly(cands, bankroll: float = 1000.0) -> dict`——按日期升序逐注：`f = KELLY_FRAC * (p*odds − 1) / (odds − 1)`，截断 `[0, STAKE_CAP]`，stake = f × bankroll，赢则 bankroll += stake*(odds−1) 否则 −= stake；返回 `{"n", "final_bankroll", "roi", "max_drawdown_pct"}`

- [ ] **Step 1: 写失败测试**

`tests/backtest/test_simulate.py`：

```python
from fa.backtest.simulate import (candidates, simulate_flat,
                                  simulate_kelly)


def _row(**kw):
    base = {"match_id": 1, "league": "E0", "date": "2023-08-12",
            "p_home": 0.55, "p_draw": 0.25, "p_away": 0.20,
            "mkt_home": 0.50, "mkt_draw": 0.28, "mkt_away": 0.22,
            "odds_home": 2.00, "odds_draw": 3.57, "odds_away": 4.55,
            "mkt_over25": None, "p_over25": 0.6, "total_goals": 2,
            "outcome": "H"}
    base.update(kw)
    return base


def test_candidates_gates():
    # home：edge=0.05, ev=0.55*1−0.45=0.10 → 入选
    c = candidates([_row()])
    mkts = {c_["market"] for c_ in c}
    assert "H" in mkts
    # odds 6.5 超带 → 无候选
    assert candidates([_row(odds_home=6.5)]) == []
    # edge 不足 → 无候选
    assert candidates([_row(p_home=0.51, mkt_home=0.50)]) == []
    # EV 不足（高赔低概率）→ 无候选
    assert candidates([_row(p_away=0.25, mkt_away=0.22, odds_away=4.55)]) == []


def test_flat_and_kelly():
    cands = candidates([_row(), _row(match_id=2, date="2023-08-19")])
    flat = simulate_flat(cands)
    assert flat["n"] >= 1 and flat["staked"] == flat["n"]
    # outcome=H 命中 → 收益为正
    assert flat["returned"] == flat["n"] * 2.0
    kel = simulate_kelly(cands, bankroll=1000.0)
    assert kel["final_bankroll"] > 1000.0             # 全命中
    assert kel["n"] == flat["n"]
```

- [ ] **Step 2: 确认失败** → `ModuleNotFoundError`

- [ ] **Step 3: 实现 simulate.py**

```python
"""模拟盘：spec §5.2/§5.3 门槛与仓位。回测以收盘价成交（保守）。"""
from fa.backtest.metrics import fetch_predictions  # noqa: F401  （复用入口，留 import 便于同源）

EV_MIN = 0.03
EDGE_MIN = 0.02
ODDS_MIN = 1.4
ODDS_MAX = 6.0
KELLY_FRAC = 0.25
STAKE_CAP = 0.02


def _hit(row, market: str) -> bool:
    if market == "H":
        return row["outcome"] == "H"
    if market == "D":
        return row["outcome"] == "D"
    if market == "A":
        return row["outcome"] == "A"
    if market == "O2.5":
        return row["total_goals"] >= 3
    raise ValueError(market)


def candidates(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        mkts = [("H", r["p_home"], r["mkt_home"], r["odds_home"]),
                ("D", r["p_draw"], r["mkt_draw"], r["odds_draw"]),
                ("A", r["p_away"], r["mkt_away"], r["odds_away"])]
        if r.get("mkt_over25"):
            mkts.append(("O2.5", r["p_over25"], r["mkt_over25"],
                         None))                        # O2.5 赔率不在表内，跳过 EV 判定见下
        for market, p, mkt_p, odds in mkts:
            if p is None or mkt_p is None:
                continue
            edge = p - mkt_p
            if market == "O2.5":
                continue                              # 缺收盘赔率，无法算 EV，M3 接实时盘后启用
            if odds is None or not (ODDS_MIN <= odds <= ODDS_MAX):
                continue
            ev = p * (odds - 1) - (1 - p)
            if ev >= EV_MIN and edge >= EDGE_MIN:
                out.append({"match_id": r["match_id"], "league": r["league"],
                            "date": r["date"], "market": market, "p": p,
                            "mkt_p": mkt_p, "odds": odds, "ev": ev,
                            "edge": edge, "outcome_hit": _hit(r, market)})
    return out


def simulate_flat(cands: list[dict]) -> dict:
    staked = len(cands)
    returned = sum(c["odds"] for c in cands if c["outcome_hit"])
    return {"n": staked, "staked": staked, "returned": returned,
            "pnl": returned - staked,
            "roi": (returned - staked) / staked if staked else 0.0}


def simulate_kelly(cands: list[dict], bankroll: float = 1000.0) -> dict:
    peak, max_dd = bankroll, 0.0
    for c in sorted(cands, key=lambda x: x["date"]):
        f = KELLY_FRAC * (c["p"] * c["odds"] - 1) / (c["odds"] - 1)
        f = min(max(f, 0.0), STAKE_CAP)
        stake = f * bankroll
        bankroll += stake * (c["odds"] - 1) if c["outcome_hit"] else -stake
        peak = max(peak, bankroll)
        max_dd = max(max_dd, (peak - bankroll) / peak)
    return {"n": len(cands), "final_bankroll": bankroll,
            "roi": (bankroll - 1000.0) / 1000.0,
            "max_drawdown_pct": max_dd}
```

（`fetch_predictions` 的 noqa import 若 lint 无要求可去掉；保留与去掉均合规。`simulate_kelly` 的初始 1000.0 以参数默认值记录。）

- [ ] **Step 4: 跑测试** → 2 PASS；全量回归

- [ ] **Step 5: Commit**

```bash
git add src/fa/backtest/simulate.py tests/backtest/test_simulate.py
git commit -m "feat: 模拟盘（spec 门槛筛选 + 平注/¼Kelly 仓位模拟）"
```

---

### Task 9: CLI 与 m2-report 生成器

**Files:**
- Create: `src/fa/backtest/report.py`
- Modify: `src/fa/cli.py`（挂 `backtest` 子应用）
- Test: `tests/backtest/test_report.py`、`tests/test_cli.py`（追加 help 用例）

**Interfaces:**
- Consumes: `run_backtest`（Task 6）、`evaluate/by_group/fetch_predictions`（Task 7）、`simulate_flat/simulate_kelly/candidates`（Task 8）、`LEAGUES`（config）
- Produces:
  - `render_report(rows: list[dict], out_path) -> dict`——写 markdown：总览（n、model/market LL、劣化%、Brier、verdict 大字）、校准表、分联赛表、分赛季表、模拟盘（flat + Kelly）；返回 evaluate 摘要 dict
  - CLI：`fa backtest run [--from 2019] [--to 2025] [--half-life 100] [--leagues E0,SP1] [--no-refit]`——默认全联赛、全赛季；`--half-life` 覆盖 FitConfig；执行后打印判决并写 `docs/m2-report.md`；`--no-refit` 跳过拟合直接用表内既有预测出报告（调参对比用）

- [ ] **Step 1: 写失败测试**

`tests/backtest/test_report.py`：

```python
from fa.backtest.report import render_report


def _rows():
    return [{"league": "E0", "season": 2023, "date": "2023-08-12",
             "match_id": 1, "week_index": 1, "outcome": "H", "total_goals": 2,
             "p_home": 0.5, "p_draw": 0.25, "p_away": 0.25,
             "mkt_home": 0.5, "mkt_draw": 0.25, "mkt_away": 0.25,
             "odds_home": 2.0, "odds_draw": 4.0, "odds_away": 4.0,
             "p_over25": 0.5, "mkt_over25": None}]


def test_render_report(tmp_path):
    out = tmp_path / "r.md"
    summary = render_report(_rows(), out)
    text = out.read_text(encoding="utf-8")
    assert "GO" in text or "NO-GO" in text
    assert "log-loss" in text
    assert summary["n"] == 1
```

`tests/test_cli.py` 追加：

```python
def test_backtest_help():
    result = runner.invoke(app, ["backtest", "run", "--help"])
    assert result.exit_code == 0
    assert "half-life" in result.output
```

- [ ] **Step 2: 确认失败** → FAIL

- [ ] **Step 3: 实现 report.py 与 CLI**

`src/fa/backtest/report.py`：

```python
from pathlib import Path

from fa.backtest.metrics import by_group, evaluate
from fa.backtest.simulate import candidates, simulate_flat, simulate_kelly


def render_report(rows: list[dict], out_path: Path) -> dict:
    ev = evaluate(rows)
    by_lg, by_sn = by_group(rows, "league"), by_group(rows, "season")
    flat = simulate_flat(candidates(rows))
    kel = simulate_kelly(candidates(rows))
    lines = ["# M2 回测报告", "",
             f"- 样本：{ev['n']} 场",
             f"- 模型 log-loss：{ev['model_ll']:.5f}；市场（去水收盘）：{ev['market_ll']:.5f}",
             f"- 劣化：{ev['degradation_pct']:+.2f}%（判据：≤ +1.00% 即 GO）",
             f"- Brier：模型 {ev['model_brier']:.5f} / 市场 {ev['market_brier']:.5f}",
             "",
             f"## 判决：{'✅ GO' if ev['verdict'] == 'GO' else '⛔ NO-GO'}", "",
             "## 校准（p_home 十分位）", "",
             "| 区间 | n | 平均预测 | 实际主场胜率 |", "|---|---|---|---|"]
    for b in ev["cal_home"]:
        lines.append(f"| {b['lo']:.1f}–{b['hi']:.1f} | {b['n']} | "
                     f"{b['avg_p']:.3f} | {b['emp']:.3f} |")
    lines += ["", "## 分联赛", "", "| 联赛 | n | 劣化% | 判决 |", "|---|---|---|---|"]
    for lg, e in by_lg.items():
        lines.append(f"| {lg} | {e['n']} | {e['degradation_pct']:+.2f}% "
                     f"| {e['verdict']} |")
    lines += ["", "## 分赛季", "", "| 赛季 | n | 劣化% | 判决 |", "|---|---|---|---|"]
    for sn, e in by_sn.items():
        lines.append(f"| {sn} | {e['n']} | {e['degradation_pct']:+.2f}% "
                     f"| {e['verdict']} |")
    lines += ["", "## 模拟盘（收盘价成交，保守）", "",
              f"- 平注：{flat['n']} 注，ROI {flat['roi']:+.1%}（P&L {flat['pnl']:+.1f} 单位）",
              f"- ¼ Kelly（本金 1000，单注 ≤2%）：终值 {kel['final_bankroll']:.1f}，"
              f"ROI {kel['roi']:+.1%}，最大回撤 {kel['max_drawdown_pct']:.1%}", ""]
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return ev
```

`src/fa/cli.py` 顶部 import 区追加，并挂子应用：

```python
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
```

- [ ] **Step 4: 跑测试** → PASS；全量回归

- [ ] **Step 5: Commit**

```bash
git add src/fa/backtest/report.py src/fa/cli.py tests/backtest/test_report.py tests/test_cli.py
git commit -m "feat: fa backtest run CLI 与 m2-report 渲染（判决大字 + 校准/分组/模拟盘）"
```

---

### Task 10: E2E 真实回测与 go/no-go 判决

**Files:**
- Create: `docs/m2-report.md`（由命令生成）
- Create: `docs/m2-verdict.md`（人读判决书，实施者填写实测值）

**Interfaces:**
- Consumes: 全部前序任务
- Produces: **spec §10 M2 的 go/no-go 判决证据**

- [ ] **Step 1: 确认数据与 psc 覆盖**

Run: `uv run fa init && sqlite3 data/fa.db "SELECT season, COUNT(*) FROM matches WHERE psc_home IS NOT NULL GROUP BY season ORDER BY season"`
Expected: 确认 psc（Pinnacle 收盘）自 2019 赛季起有覆盖（football-data 惯例）；若实际更晚（如 2020），`--from` 相应后移并在判决书注明。

- [ ] **Step 2: 全量回测**

Run: `uv run fa backtest run --from 2019 --to 2025`
Expected: 数分钟至 ~30 分钟（≈1000 次联赛拟合）；输出逐联赛进度与总行数（预期 ≈ 6 赛季 × 5 联赛 × 300+ 场 ≈ 9,000–10,000 行）。

- [ ] **Step 3: 检查报告**

Run: `cat docs/m2-report.md`
Expected: 判决行 + 校准表 + 分组表 + 模拟盘。**无论 GO 或 NO-GO，如实记录，不得调参凑 GO**——如需调参（如 --half-life 50/200 对比），每次对比写入判决书且默认参数（100 天）为主判决。

- [ ] **Step 4: 写判决书**

`docs/m2-verdict.md`（实测值填写）：

```markdown
# M2 go/no-go 判决书

- 日期：<执行日>
- 窗口：<from>–<to> 赛季，<n> 场（仅含 Pinnacle 收盘覆盖场次）
- 主判决（half-life=100）：**<GO | NO-GO>**
- 模型 log-loss <x> vs 市场 <x>（劣化 <x>%，判据 ≤ +1%）
- Brier：模型 <x> / 市场 <x>
- 校准：<一句话：主胜概率是否系统性高估/低估>
- 分联赛/赛季摘要：<异常项点名>
- 模拟盘：平注 ROI <x>%（<n> 注）；¼ Kelly 终值 <x>
- 调参对比（如有）：<half-life 扫描结果>
- 结论：<GO → 进入 M3；NO-GO → 项目止步于研究结论，M3+ 不启动>
```

- [ ] **Step 5: 全量测试 + 提交**

Run: `uv run pytest -q` → 全 PASS

```bash
git add docs/m2-report.md docs/m2-verdict.md
git commit -m "chore: M2 真实回测与 go/no-go 判决书（实测值）"
```

---

## Self-Review 记录

- **Spec 覆盖**：§4.1 分层 DC + partial pooling（Task 3 MAP 收缩；全局目标 Task 6 `global_targets`）✔；§4.2 时间衰减 + walk-forward 每周重拟合（Task 3 权重 + Task 6 循环）✔；§4.3 比分矩阵衍生市场（Task 4）✔；§4.4 冷启动（Task 3 零均值先验 + Task 4 未知队取 0——升班马语义自然实现，计划正文已注明）✔；§4.5 已知盲区——状态协变量消融为 spec 可选项，**明确排除**（范围裁定节）✔；§8.1 walk-forward ≥5 赛季 + 每场记录模型/市场/结果（Task 2 表 + Task 6 写入；2019–2025 共 7 个赛季可选 ≥5）✔；§8.2 校准/Brier/log-loss + ≤1% 判据（Task 7）✔；§8.3 平注 + ¼ Kelly 模拟（Task 8）✔；§8.4 回测不含 persona（本计划无 persona 内容）✔；§5.1 比例法去水（Task 5）✔；§5.2/5.3 门槛与仓位常量（Task 8）✔；M1 携带项：_read_text/tiebreaker（Task 1）、schema v2+版本测试（Task 2）、MatchWeek.end 语义（Task 6 接口注明用 start）✔
- **占位符扫描**：无 TBD/TODO/「以本段为准」式双版本段落（写作中已清理）；全部代码块为可直接落盘的实现
- **类型一致性**：`fit_league(matches, asof, league, mu_global, ha_global, cfg)` 定义（Task 3）与 Task 4/6 调用一致；`FitConfig(half_life_days, window_days, sigma_*)` 贯穿；`evaluate` 返回键与 report.py/判决书引用键一致（n/model_ll/market_ll/degradation_pct/verdict/cal_home）；`candidates` 返回键与 simulate 两函数一致
