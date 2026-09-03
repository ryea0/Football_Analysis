# 双路对比线（agentline）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地设计 `docs/superpowers/specs/2026-09-04-agentline-dual-track-design.md`——线 A（dsh headless agent 当大脑）与线 P（现有管线预测）长期并行、滚动对比评测。

**Architecture:** 新增 `src/fa/agentline/` 包（export 信息集打包 / runner dsh 调用 / contract 契约校验 / store 落库 / compare 评测）+ `fa agentline` CLI 子 app + DB v4 两张新表（物理隔离分账）。线 P 零改动（`backtest_predictions` 与 `backtest/metrics.py`/`simulate.py` 原样复用）。

**Tech Stack:** Python 3.11+ / typer / SQLite（WAL，schema_version 纯加法迁移）/ pytest；dsh（Node.js runtime，headless profile）。

**Spec:** `docs/superpowers/specs/2026-09-04-agentline-dual-track-design.md`（已批准，含 spec.md §12.5 增补草案）

## Global Constraints

- 线 A 产出**只写** `agentline_predictions` / `agentline_runs` 两表，**永不**写 B 线表（fixtures/odds_snapshots/runs/recommendations/bets）与 `backtest_predictions`（设计 §2）
- 诚实降级：dsh 超时/失败/契约不过一律记 status 落库，绝不向调用方抛出、绝不脑补数字（设计 §4，同 `report/telegram.py` 模式）
- 市场是所有评估对照线：评测基准 = `mkt_*` 列（Pinnacle 收盘去水，spec §8.2）
- dsh headless 的确切参数形态**以 Task 2 spike 实测为准**（附录 A 为唯一权威）；本计划中 runner 默认参数是待验证假设
- DB 迁移遵循 db.py 现行模式：DDL 常量新建与迁移共用、逐级纯加法、无数据搬迁
- 测试 hermetic：`tests/conftest.py` 的 `seal_environ` autouse 夹具已存在，沿用；不用真 dsh、不碰真库（`init_db(tmp_path/…)`）
- 注释与文档用中文，风格对齐现有代码（注释讲 why）

---

### Task 1: spec.md §12.5 合入 + CLAUDE.md 状态行

**Files:**
- Modify: `spec.md`（§12 末尾增补 §12.5）
- Modify: `CLAUDE.md`（「当前状态」清单加一行）

**Interfaces:**
- Consumes: 设计文档 §11 的增补草案（已批准）
- Produces: spec 含 §12.5；后续任务的合法性依据

- [ ] **Step 1: spec.md §12 节末尾追加小节**

在 §12.4（决策记录）之后追加：

```markdown
### 12.5 范式对比线（2026-09-04）

A 线新增「agent 当大脑」对比子线（线 A）：dsh headless agent 对同批比赛做
全自主预测，与线 P（确定性管线）共用评测判据长期并行滚动对比。线 A 只消费
导出信息集 JSON、无 DB 写权（产出由 Python 侧落 `agentline_predictions`，
永不进入 B 线推荐与落注流）。基线层 `A_base`（无检索）度量范式差，增强层
`A_enh`（+web 检索）度量定性信息增量，为 M4 persona 设计提供先导数据。
「agent 不当大脑」由公设转为待实证命题。设计文档：
docs/superpowers/specs/2026-09-04-agentline-dual-track-design.md。
```

- [ ] **Step 2: CLAUDE.md「当前状态」列表末尾加一行**

```markdown
- **范式对比线（§12.5）立项**（2026-09-04）：线 A（dsh headless agent 当大脑）与线 P 长期并行滚动对比，不设样本上限；设计 `docs/superpowers/specs/2026-09-04-agentline-dual-track-design.md`，实施中
```

- [ ] **Step 3: Commit**

```bash
git add spec.md CLAUDE.md
git commit -m "docs(spec): §12.5 范式对比线合入 + CLAUDE.md 状态行"
```

---

### Task 2: dsh headless 契约 spike（探针，产出附录 A）

**Files:**
- Create: `docs/superpowers/specs/2026-09-04-agentline-dual-track-design.md` 末尾追加「附录 A：dsh headless 实测契约」
- 无生产代码（spike 结论决定 Task 6 的 runner 参数）

**Interfaces:**
- Produces: 附录 A——dsh 安装方式、headless 确切命令、stdin/stdout 形态、退出码语义、API key 配置位置、profile 清单（`fa-agent-base`/`fa-agent-enh`）、单场耗时与 token 成本。Task 6 的 `_DSH`/`_HEADLESS_ARGS` 常量以附录 A 为准。

- [ ] **Step 1: 安装 dsh 与建 profile**

```bash
node --version                    # 确认 Node.js ≥ 20
npm install -g @deepseek-ai/dsh   # 官方 runtime（awesome 清单 Install 节）
dsh --help | head -30             # 确认子命令面
dsh --version
```

- [ ] **Step 2: 配 DEEPSEEK_API_KEY 并实测 headless 一次性运行**

把 key 写入 dsh 配置（位置以 `dsh --help`/官方文档为准）。逐项实测并记录：

```bash
echo '只输出一个 JSON 对象：{"ok": true}' | dsh --profile headless -   # 形态假设一：stdin
dsh --profile headless "只输出一个 JSON 对象：{ \"ok\": true }"         # 形态假设二：位置参数
dsh --profile headless --help                                          # headless 的真实参数面
```

记录：确切命令、输入怎么给（stdin/参数/文件）、stdout 是裸 JSON 还是带包装、stderr 用途、退出码（0/非 0 语义）、超时行为。

- [ ] **Step 3: 建两个 profile 并装插件**

```bash
dsh plugin --profile fa-agent-base add <dsh-translate 安装源>
dsh plugin --profile fa-agent-enh  add <dsh-translate 安装源>
dsh plugin --profile fa-agent-enh  add <web 检索插件安装源>   # spike 时从 awesome 清单选定（dsh-free-web-search 或 keyless tavily）
```

各跑一次最小会话验证插件生效（翻译修复层无需显式调用；检索插件问一句「今天日期？」观察是否触发检索）。

- [ ] **Step 4: 单场信息集冒烟**

手工组装一场真实比赛的信息集 JSON（ Arsenal 2023-24 赛季任一场，可从 `sqlite3 data/fa.db` 现查），用 fa-agent-base 跑完整预测 prompt（模板见 Task 6 Step 3）。记录：契约成功率（跑 3 次看 JSON 纯净度）、耗时、输入 token 量级（dsh 的 usage 输出或帐面估算）。

- [ ] **Step 5: 写附录 A 并 commit**

附录 A 必含：安装命令、headless 确切命令行、输入/输出契约、退出码表、两个 profile 的插件清单、单场成本三数字（耗时/成功率/token）、**若契约不可行的降级结论**（Python SDK 路径或搁置重议——此时终止本计划，回报用户）。

```bash
git add docs/superpowers/specs/2026-09-04-agentline-dual-track-design.md
git commit -m "docs(agentline): 附录A——dsh headless 实测契约（spike 产出）"
```

---

### Task 3: DB v4——agentline 两表

**Files:**
- Modify: `src/fa/db.py`（`SCHEMA_VERSION` 3→4、新增 `_AL_TABLE` 常量、`_SCHEMA` 拼接、`_migrate_up` 加 v3→v4 分支）
- Test: `tests/agentline/__init__.py`（新建目录）、`tests/agentline/test_db_v4.py`

**Interfaces:**
- Produces: 表 `agentline_predictions(id, match_id INTEGER REFERENCES matches(id), line TEXT CHECK(line IN ('A_base','A_enh')), p_home REAL, p_draw REAL, p_away REAL, p_over25 REAL, confidence REAL, reasoning_digest TEXT, sources_json TEXT, raw_output TEXT NOT NULL, status TEXT CHECK(status IN ('ok','parse_fail','timeout','error')), repaired INTEGER, harness TEXT, model TEXT, duration_s REAL, created_at TEXT NOT NULL, UNIQUE(match_id, line))`；表 `agentline_runs(id, line TEXT, profile TEXT, model TEXT, n_ok INTEGER, n_parse_fail INTEGER, n_timeout INTEGER, n_error INTEGER, started_at TEXT NOT NULL, finished_at TEXT, summary TEXT)`。`init_db`/`connect` 签名不变。

- [ ] **Step 1: 写失败测试**

```python
"""agentline 两表（设计 §6）：v4 新建与 v3 迁移路径表结构一致。"""
import sqlite3

from fa.db import connect, init_db, SCHEMA_VERSION


def _cols(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_fresh_db_has_agentline_tables(tmp_path):
    init_db(tmp_path / "t.db")
    conn = connect(tmp_path / "t.db")
    assert _cols(conn, "agentline_predictions") >= {
        "match_id", "line", "p_home", "status", "raw_output", "harness"}
    assert _cols(conn, "agentline_runs") >= {"line", "n_ok", "n_error"}
    assert conn.execute("SELECT version FROM schema_version").fetchone()[
        "version"] == SCHEMA_VERSION
    conn.close()


def test_migrate_v3_to_v4(tmp_path):
    # 先造 v3 库（建表后把版本号钉回 3），再 init_db 触发迁移
    init_db(tmp_path / "t.db")
    conn = connect(tmp_path / "t.db")
    conn.execute("UPDATE schema_version SET version=3")
    conn.commit(); conn.close()
    init_db(tmp_path / "t.db")                      # v3 → v4
    conn = connect(tmp_path / "t.db")
    assert _cols(conn, "agentline_predictions")     # 迁移路径同样有表
    conn.close()


def test_line_check_constraint(tmp_path):
    init_db(tmp_path / "t.db")
    conn = connect(tmp_path / "t.db")
    conn.execute("PRAGMA foreign_keys=OFF")         # 测试只验 CHECK，不造 matches 行
    try:
        conn.execute(
            "INSERT INTO agentline_predictions (match_id, line, raw_output,"
            " status, created_at) VALUES (1, 'B_line', 'x', 'ok', '2026-09-04')")
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("line 词表未生效")
    finally:
        conn.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agentline/test_db_v4.py -v`
Expected: FAIL（`agentline_predictions` 不存在 / SCHEMA_VERSION 仍为 3）

- [ ] **Step 3: 实现——db.py 三处改动**

```python
SCHEMA_VERSION = 4
```

在 `_BLINE_TABLE` 之后新增（同款注释风格）：

```python
# 范式对比线两表（spec §12.5 / 设计 §6）：线 A 专用，与 B 线表物理隔离——
# 本模块的 B 线边界注释同样适用于这里：绝不写 recommendations / bets 等。
_AL_TABLE = """
CREATE TABLE IF NOT EXISTS agentline_predictions (
    id                INTEGER PRIMARY KEY,
    match_id          INTEGER NOT NULL REFERENCES matches(id),
    line              TEXT NOT NULL
        CHECK (line IN ('A_base', 'A_enh')),
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
    UNIQUE (match_id, line)           -- 幂等：一场一line一行
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
```

`_SCHEMA` 拼接改为 `... + _BP_TABLE + _BLINE_TABLE + _AL_TABLE`；`_migrate_up` 加：

```python
    if from_v < 4:
        conn.executescript(_AL_TABLE)
```

docstring 的迁移说明补 `v3->v4 新增范式对比线两表`。

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `uv run pytest tests/agentline/ tests/test_db.py -v`
Expected: 全 PASS（test_db.py 原有用例不受影响——纯加法）

- [ ] **Step 5: Commit**

```bash
git add src/fa/db.py tests/agentline/
git commit -m "feat(db): v4——范式对比线两表（agentline_predictions/runs，纯加法迁移）"
```

---

### Task 4: contract.py 契约校验

**Files:**
- Create: `src/fa/agentline/__init__.py`（空文件）、`src/fa/agentline/contract.py`
- Test: `tests/agentline/test_contract.py`

**Interfaces:**
- Produces: `parse_prediction(raw: str, repaired_ok: bool = False) -> dict`。返回 `{"status": "ok"|"parse_fail", "p_home": float|None, "p_draw": float|None, "p_away": float|None, "p_over25": float|None, "confidence": float|None, "reasoning_digest": str, "sources_json": str, "repaired": bool}`。约定：ok 时四个概率全部非 None 且归一（三项和容差 ±0.05，超差按比例归一；p_over25 只 clip 到 [0,1]）；任何解析/校验失败 → status=parse_fail、概率全 None、reasoning_digest 记失败原因。

- [ ] **Step 1: 写失败测试**

```python
"""契约校验（设计 §4）：约束输出——ok 或 parse_fail，绝不脑补。"""
import json

from fa.agentline.contract import parse_prediction

_OK = json.dumps({"p_home": 0.45, "p_draw": 0.28, "p_away": 0.27,
                  "p_over25": 0.55, "confidence": 0.6,
                  "reasoning_digest": "主场强势", "sources": []})


def test_ok_passthrough():
    r = parse_prediction(_OK)
    assert r["status"] == "ok"
    assert abs(r["p_home"] - 0.45) < 1e-9 and r["repaired"] is False


def test_strips_markdown_fence_and_text_around():
    raw = "分析如下……\n```json\n" + _OK + "\n```\n以上。"
    r = parse_prediction(raw)
    assert r["status"] == "ok"


def test_sums_renormalized():
    r = parse_prediction(json.dumps({"p_home": 0.5, "p_draw": 0.3,
                                     "p_away": 0.3, "p_over25": 0.5,
                                     "confidence": 0.5,
                                     "reasoning_digest": "", "sources": []}))
    assert r["status"] == "ok"
    assert abs(r["p_home"] + r["p_draw"] + r["p_away"] - 1.0) < 1e-9


def test_negative_or_missing_probability_is_parse_fail():
    bad = json.dumps({"p_home": -0.1, "p_draw": 0.9, "p_away": 0.2,
                      "p_over25": 0.5, "confidence": 0.5,
                      "reasoning_digest": "", "sources": []})
    assert parse_prediction(bad)["status"] == "parse_fail"
    assert parse_prediction('{"unexpected": 1}')["status"] == "parse_fail"
    assert parse_prediction("不是 JSON")["status"] == "parse_fail"


def test_parse_fail_carries_reason_not_numbers():
    r = parse_prediction("不是 JSON")
    assert r["p_home"] is None
    assert "原因" in r["reasoning_digest"] or r["reasoning_digest"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agentline/test_contract.py -v`
Expected: FAIL（ModuleNotFoundError: fa.agentline）

- [ ] **Step 3: 实现 contract.py**

```python
"""线 A 预测契约校验（设计 §4）：约束输出——合法则归一，不合法则 parse_fail。

绝不脑补：任何字段缺失/越界/非 JSON 一律整场弃用（status=parse_fail），
失败原因写 reasoning_digest。三项概率和容差 ±0.05，超差按比例归一
（容差内不动——避免无谓扰动 agent 的原始判断）。
"""
import json
import re

_SUM_TOL = 0.05
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json(raw: str) -> dict:
    """剥离 markdown 围栏与前后杂文字，取第一个平衡的 {...}。"""
    text = _FENCE_RE.search(raw).group(1) if _FENCE_RE.search(raw) else raw
    start = text.find("{")
    if start < 0:
        raise ValueError("输出中没有 JSON 对象")
    depth = 0
    for i, ch in enumerate(text[start:], start):
        depth += (ch == "{") - (ch == "}")
        if depth == 0:
            return json.loads(text[start:i + 1])
    raise ValueError("JSON 对象未闭合")


def parse_prediction(raw: str, repaired_ok: bool = False) -> dict:
    fail = {"status": "parse_fail", "p_home": None, "p_draw": None,
            "p_away": None, "p_over25": None, "confidence": None,
            "reasoning_digest": "", "sources_json": "[]",
            "repaired": repaired_ok}
    try:
        obj = _extract_json(raw)
        ph, pd, pa = (float(obj["p_home"]), float(obj["p_draw"]),
                      float(obj["p_away"]))
        po = float(obj["p_over25"])
        if min(ph, pd, pa, po) < 0 or max(ph, pd, pa) > 1 or not 0 <= po <= 1:
            raise ValueError("概率越界（负数或 >1）")
        total = ph + pd + pa
        if abs(total - 1.0) > _SUM_TOL:
            ph, pd, pa = ph / total, pd / total, pa / total   # 比例归一
        return {"status": "ok", "p_home": ph, "p_draw": pd, "p_away": pa,
                "p_over25": po,
                "confidence": float(obj.get("confidence", 0.0)),
                "reasoning_digest": str(obj.get("reasoning_digest", ""))[:200],
                "sources_json": json.dumps(obj.get("sources", []),
                                           ensure_ascii=False),
                "repaired": repaired_ok}
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        fail["reasoning_digest"] = f"parse_fail 原因：{type(exc).__name__}: {exc}"
        return fail
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/agentline/test_contract.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add src/fa/agentline/ tests/agentline/test_contract.py
git commit -m "feat(agentline): 契约校验——归一或整场弃用，绝不脑补"
```

---

### Task 5: export.py 信息集打包器

**Files:**
- Create: `src/fa/agentline/export.py`
- Test: `tests/agentline/test_export.py`

**Interfaces:**
- Consumes: `fa.db.connect`、`matches`/`teams` 表（已存在）
- Produces: `pick_sample(conn, league: str, season: int, n: int, seed: int = 42) -> list[int]`（match_id，仅取 `backtest_predictions` 已覆盖且盘口齐全的场次，随机抽样）；`export_info_set(conn, match_id: int) -> dict`（设计 §5.1 的信息集结构）；`export_batch(conn, match_ids: list[int], out_dir: Path) -> list[Path]`（每场一个 `data/agentline/<match_id>.json`，已存在则跳过——幂等）。信息集 dict 结构（Task 6 的 prompt 与 Task 7 编排依赖）：

```python
{"match": {"league", "season", "date", "home", "away"},
 "odds": {"psc_home", "psc_draw", "psc_away", "over25_psc", "under25_psc"},
 "home_recent": [...最近10场 dict(date, opponent, venue, gf, ga, shots, corners)...],
 "away_recent": [...],
 "h2h": [...最近10次 dict(date, home, away, gf, ga)...],
 "standings": {"home": {"pos", "played", "pts"}, "away": {...}}}   # 赛前积分（date < match_date 聚合）
```

- [ ] **Step 1: 写失败测试**

```python
"""信息集打包（设计 §5.1）：防泄漏是结构性约束——一切查询以 date < match_date 为界。"""
import json
import sqlite3

import pytest

from fa.agentline.export import export_info_set, export_batch, pick_sample
from fa.db import connect, init_db


@pytest.fixture()
def db(tmp_path):
    """两支队、三场已完赛（含赛果与收盘盘口）、backtest_predictions 覆盖前两场。

    第三场 date 晚于目标场——若泄漏进信息集即为 bug（防泄漏断言的靶子）。
    """
    init_db(tmp_path / "t.db")
    conn = connect(tmp_path / "t.db")
    conn.execute("INSERT INTO teams (league, name) VALUES ('E0', 'Arsenal')")
    conn.execute("INSERT INTO teams (league, name) VALUES ('E0', 'Chelsea')")
    hid, aid = (r["id"] for r in conn.execute(
        "SELECT id FROM teams ORDER BY id"))
    rows = [  # (id, date, home, away, fthg, ftag, psc 三元组)
        (10, "2024-01-01", hid, aid, 2, 0, (1.5, 4.0, 6.0)),
        (11, "2024-02-01", aid, hid, 1, 1, (2.5, 3.2, 2.8)),
        (12, "2024-03-01", hid, aid, 3, 1, (1.4, 4.2, 7.0)),  # 晚于目标场 11
    ]
    for mid, d, h, a, gf, ga, (oh, od, oa) in rows:
        conn.execute(
            "INSERT INTO matches (id, league, season, date, home_team_id,"
            " away_team_id, fthg, ftag, psc_home, psc_draw, psc_away,"
            " raw_line) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid, "E0", 2023, d, h, a, gf, ga, oh, od, oa, "{}"))
    for mid, outcome in ((10, "H"), (11, "D")):
        conn.execute(
            "INSERT INTO backtest_predictions (league, season, week_index,"
            " match_id, date, p_home, p_draw, p_away, outcome, total_goals)"
            " VALUES ('E0', 2023, 1, ?, (SELECT date FROM matches WHERE id=?),"
            " 0.4, 0.3, 0.3, ?, 2)", (mid, mid, outcome))
    conn.commit()
    yield conn
    conn.close()


def test_info_set_excludes_future_matches(db):
    info = export_info_set(db, 11)
    dates = [m["date"] for m in info["home_recent"] + info["away_recent"]]
    assert all(d < "2024-02-01" for d in dates)      # 防泄漏：只见过去
    assert info["match"]["date"] == "2024-02-01"
    assert info["odds"]["psc_home"] == pytest.approx(2.5)


def test_info_set_h2h_and_standings(db):
    info = export_info_set(db, 11)
    assert len(info["h2h"]) == 1                       # 只有第一场是历史交锋
    assert info["standings"]["home"]["played"] >= 1


def test_pick_sample_only_backtested(db):
    assert set(pick_sample(db, "E0", 2023, n=10)) == {10, 11}


def test_export_batch_idempotent(db, tmp_path):
    out = tmp_path / "is"
    p1 = export_batch(db, [10, 11], out)
    assert len(p1) == 2 and json.loads(p1[0].read_text())["match"]["league"] == "E0"
    assert export_batch(db, [10, 11], out) == []       # 第二次全跳过
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agentline/test_export.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 export.py**

```python
"""信息集打包器（设计 §5.1）：给线 A 的「赛前可见」数据快照。

防泄漏是结构性约束：recent/H2H/standings 的每条 SQL 都带 date < match_date，
评审时请检查这一点——这是对比实验公平性的根基。窗口 N=10：与 Dixon-Coles
训练窗口信息量可比，不偏向任何一线（设计 §13 开放问题在此定为 10）。
"""
import json
import random
import sqlite3
from pathlib import Path

_WINDOW = 10


def _recent(conn: sqlite3.Connection, team_id: int, before: str) -> list[dict]:
    rows = conn.execute(
        "SELECT date, home_team_id, away_team_id, fthg, ftag,"
        " shots_home, shots_away, corners_home, corners_away"
        " FROM matches WHERE (home_team_id=? OR away_team_id=?)"
        " AND date < ? AND fthg IS NOT NULL"
        " ORDER BY date DESC LIMIT ?", (team_id, team_id, before, _WINDOW))
    out = []
    for r in rows:
        home = r["home_team_id"] == team_id
        out.append({"date": r["date"],
                    "opponent_id": r["away_team_id"] if home else r["home_team_id"],
                    "venue": "H" if home else "A",
                    "gf": r["fthg"] if home else r["ftag"],
                    "ga": r["ftag"] if home else r["fthg"],
                    "shots": r["shots_home"] if home else r["shots_away"],
                    "corners": r["corners_home"] if home else r["corners_away"]})
    return out


def _h2h(conn, a: int, b: int, before: str) -> list[dict]:
    rows = conn.execute(
        "SELECT date, home_team_id, fthg, ftag FROM matches"
        " WHERE ((home_team_id=? AND away_team_id=?) OR (home_team_id=? AND away_team_id=?))"
        " AND date < ? AND fthg IS NOT NULL"
        " ORDER BY date DESC LIMIT ?", (a, b, b, a, before, _WINDOW))
    return [{"date": r["date"],
             "home_id": r["home_team_id"],
             "gf": r["fthg"], "ga": r["ftag"]} for r in rows]


def _standing(conn, league: str, season: int, team_id: int, before: str) -> dict:
    played = won = drawn = 0
    for r in conn.execute(
            "SELECT home_team_id, away_team_id, fthg, ftag FROM matches"
            " WHERE league=? AND season=? AND date < ? AND fthg IS NOT NULL",
            (league, season, before)):
        if r["home_team_id"] == team_id:
            gf, ga = r["fthg"], r["ftag"]
        elif r["away_team_id"] == team_id:
            gf, ga = r["ftag"], r["fthg"]
        else:
            continue
        played += 1
        won += gf > ga
        drawn += gf == ga
    return {"played": played, "pts": 3 * won + drawn}


def export_info_set(conn: sqlite3.Connection, match_id: int) -> dict:
    m = conn.execute(
        "SELECT m.league, m.season, m.date, m.home_team_id, m.away_team_id,"
        " m.psc_home, m.psc_draw, m.psc_away, m.over25_psc, m.under25_psc,"
        " h.name AS home, a.name AS away"
        " FROM matches m JOIN teams h ON h.id=m.home_team_id"
        " JOIN teams a ON a.id=m.away_team_id WHERE m.id=?",
        (match_id,)).fetchone()
    if m is None:
        raise ValueError(f"match {match_id} 不存在")
    return {
        "match": {"league": m["league"], "season": m["season"],
                  "date": m["date"], "home": m["home"], "away": m["away"]},
        "odds": {k: m[k] for k in ("psc_home", "psc_draw", "psc_away",
                                   "over25_psc", "under25_psc")},
        "home_recent": _recent(conn, m["home_team_id"], m["date"]),
        "away_recent": _recent(conn, m["away_team_id"], m["date"]),
        "h2h": _h2h(conn, m["home_team_id"], m["away_team_id"], m["date"]),
        "standings": {
            "home": _standing(conn, m["league"], m["season"],
                              m["home_team_id"], m["date"]),
            "away": _standing(conn, m["league"], m["season"],
                              m["away_team_id"], m["date"])},
    }


def pick_sample(conn, league: str, season: int, n: int,
                seed: int = 42) -> list[int]:
    """仅取线 P 已覆盖（backtest_predictions 有行）且收盘盘口齐全的场次。"""
    rows = [r["match_id"] for r in conn.execute(
        "SELECT bp.match_id FROM backtest_predictions bp"
        " JOIN matches m ON m.id = bp.match_id"
        " WHERE bp.league=? AND bp.season=?"
        " AND m.psc_home IS NOT NULL AND m.psc_draw IS NOT NULL"
        " AND m.psc_away IS NOT NULL ORDER BY bp.match_id",
        (league, season))]
    random.Random(seed).shuffle(rows)
    return sorted(rows[:n])


def export_batch(conn, match_ids: list[int], out_dir: Path) -> list[Path]:
    """每场一个 <match_id>.json；已存在跳过（幂等，续跑不重写）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for mid in match_ids:
        p = out_dir / f"{mid}.json"
        if p.exists():
            continue
        p.write_text(json.dumps(export_info_set(conn, mid),
                                ensure_ascii=False, indent=1), encoding="utf-8")
        written.append(p)
    return written
```

**注意**：`_standing` 的两分支视角别写反——主队视角取 `(fthg, ftag)`，客队视角取 `(ftag, fthg)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/agentline/test_export.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/fa/agentline/export.py tests/agentline/test_export.py
git commit -m "feat(agentline): 信息集打包器——date<match_date 结构性防泄漏"
```

---

### Task 6: runner.py dsh headless 调用器

**Files:**
- Create: `src/fa/agentline/runner.py`
- Test: `tests/agentline/test_runner.py`

**Interfaces:**
- Consumes: Task 2 附录 A 的实测契约（`_DSH`/`_HEADLESS_ARGS`/`_TIMEOUT_S` 以附录 A 修订）
- Produces: `run_headless(prompt: str, profile: str, timeout_s: int | None = None) -> tuple[str | None, str | None, float]`——返回 `(stdout | None, error | None, duration_s)`；任何失败（超时/非零退出/FileNotFoundError/异常）stdout 为 None、error 为中文原因串，**绝不抛出**（诚实降级，同 `report/telegram.py`）。模块常量：`_DSH = "dsh"`、`_TIMEOUT_S = 300`、`LAST_DSH_ERROR: str | None`。`build_prompt(info_set: dict, line: str) -> str`（prompt 全文模板，A_enh 带检索附加段）。

- [ ] **Step 1: 写失败测试**

```python
"""dsh 调用器：与 telegram.py 同构的降级契约——只回 (None, 原因)，不抛。"""
import subprocess

import pytest

from fa.agentline import runner
from fa.agentline.runner import build_prompt, run_headless


def test_run_headless_ok(monkeypatch):
    def fake_run(cmd, **kw):
        assert cmd[0] == "dsh" and "fa-agent-base" in " ".join(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout='{"p_home": 0.5}',
                                           stderr="")
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    out, err, dur = run_headless("prompt", "fa-agent-base")
    assert out == '{"p_home": 0.5}' and err is None and dur >= 0


def test_run_headless_timeout(monkeypatch):
    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 300)
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    out, err, dur = run_headless("p", "fa-agent-base")
    assert out is None and "超时" in err


def test_run_headless_missing_binary(monkeypatch):
    def fake_run(cmd, **kw):
        raise FileNotFoundError("dsh")
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    out, err, _ = run_headless("p", "fa-agent-base")
    assert out is None and "不存在" in err


def test_run_headless_nonzero_exit(monkeypatch):
    fake = subprocess.CompletedProcess(["dsh"], 1, stdout="", stderr="boom")
    monkeypatch.setattr(runner.subprocess, "run", lambda cmd, **kw: fake)
    out, err, _ = run_headless("p", "fa-agent-base")
    assert out is None and "boom" in err


def test_build_prompt_contract():
    import json
    base = build_prompt({"match": {"home": "Arsenal", "date": "2024-02-01"}},
                        "A_base")
    enh = build_prompt({"match": {"home": "Arsenal", "date": "2024-02-01"}},
                       "A_enh")
    assert "Arsenal" in base and "JSON" in base
    assert "严禁使用任何比赛开始后产生的信息" in enh
    assert "严禁使用任何比赛开始后产生的信息" not in base   # 基线层无检索段
    assert "before:2024-02-01" in enh
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agentline/test_runner.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 runner.py**

```python
"""dsh headless 调用器（设计 §9）：与 report/telegram.py 同构的 subprocess 触点。

降级契约：任何失败（超时/非零退出/找不到可执行/其它异常）返回
(stdout=None, 中文原因, duration)，绝不向调用方抛出——调用方按 status
落库（§6），run 不中断（诚实降级，设计 §4）。
参数形态以设计文档附录 A（spike 实测）为唯一权威；若附录 A 与本文件的
默认假设不符，改这里并保持测试同步。
"""
import subprocess
import time

_DSH = "dsh"
_TIMEOUT_S = 300          # 单场 headless 会话上限；附录 A 的实测耗时应远小于此
LAST_DSH_ERROR: str | None = None

_PROFILE_LINE = {"A_base": "fa-agent-base", "A_enh": "fa-agent-enh"}

_ENH_SUFFIX = """

# 网络检索（仅增强层）
你可以检索网络获取伤停 / 新闻 / 动机信息。严禁使用任何比赛开始后产生的
信息；检索词必须限定赛前日期（before:{date}）；sources 中每条的 date
必须早于比赛日 {date}。
"""


def build_prompt(info_set: dict, line: str) -> str:
    import json
    head = (
        "你是职业足球量化分析师。基于以下赛前信息，独立完成对这场比赛的"
        "胜平负与大小球 2.5 预测。\n\n"
        "# 赛前信息集\n" + json.dumps(info_set, ensure_ascii=False) + "\n\n"
        "# 要求\n"
        "- 不约束你的分析方法：自主决定如何使用以上信息，可多轮推理、交叉验证\n"
        "- 最终必须输出且仅输出一个 JSON 对象（不要 markdown 围栏、不要多余文字）：\n"
        '  {"p_home": <0-1>, "p_draw": <0-1>, "p_away": <0-1>, "p_over25": <0-1>,\n'
        '   "confidence": <0-1>, "reasoning_digest": "<=200字", "sources": []}\n'
        "- p_home + p_draw + p_away 之和应接近 1\n"
        "- sources：若使用了信息集之外的信息逐条列出 {title, date, url}；"
        "否则为空数组\n")
    date = info_set["match"]["date"]
    return head + (_ENH_SUFFIX.format(date=date) if line == "A_enh" else "")


def run_headless(prompt: str, profile: str,
                 timeout_s: int | None = None) -> tuple[str | None, str | None, float]:
    """跑一次 headless 会话。返回 (stdout, error, duration_s)，失败不抛。"""
    global LAST_DSH_ERROR
    cmd = [_DSH, "--profile", profile, prompt]    # ← 附录 A 若非此形态，改这里
    t0 = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout_s or _TIMEOUT_S)
    except subprocess.TimeoutExpired:
        LAST_DSH_ERROR = f"dsh headless 超时（{timeout_s or _TIMEOUT_S}s）"
        return None, LAST_DSH_ERROR, time.monotonic() - t0
    except FileNotFoundError:
        LAST_DSH_ERROR = f"可执行不存在：{_DSH}"
        return None, LAST_DSH_ERROR, time.monotonic() - t0
    except Exception as exc:
        LAST_DSH_ERROR = f"{type(exc).__name__}: {exc}"
        return None, LAST_DSH_ERROR, time.monotonic() - t0
    dur = time.monotonic() - t0
    if proc.returncode != 0:
        LAST_DSH_ERROR = f"dsh 退出码 {proc.returncode}：{proc.stderr[:200]}"
        return None, LAST_DSH_ERROR, dur
    LAST_DSH_ERROR = None
    return proc.stdout, None, dur
```

（`_PROFILE_LINE` 常量在 Task 7 编排里消费：line → profile 映射单点维护。）

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/agentline/test_runner.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add src/fa/agentline/runner.py tests/agentline/test_runner.py
git commit -m "feat(agentline): dsh headless 调用器——超时/失败降级不抛"
```

---

### Task 7: store.py 落库 + run 编排 + CLI

**Files:**
- Create: `src/fa/agentline/store.py`
- Create: `src/fa/agentline/orchestrate.py`
- Modify: `src/fa/cli.py`（挂 `agentline_app` 子 app）
- Test: `tests/agentline/test_store.py`、`tests/agentline/test_orchestrate.py`

**Interfaces:**
- Consumes: Task 3 的两表、Task 4 `parse_prediction`、Task 5 `pick_sample`/`export_batch`/`export_info_set`、Task 6 `run_headless`/`build_prompt`/`_PROFILE_LINE`
- Produces:
  - `save_prediction(conn, match_id: int, line: str, parsed: dict, raw_output: str, harness: str, model: str, duration_s: float) -> int`（UNIQUE 冲突时 UPDATE——幂等重跑覆盖）
  - `save_run(conn, line: str, profile: str, model: str | None, counts: dict[str, int], summary: dict) -> int`
  - `run_line(conn, line: str, info_dir: Path, limit: int | None = None) -> dict`——编排：取「有信息集 JSON 且该 line 未有 ok 行」的场次 → `build_prompt` → `run_headless` → `parse_prediction` → `save_prediction`，末尾 `save_run`；返回 counts 摘要。dsh 不可用时全部场次记 `error` 行——**run 仍正常结束**（降级不中断）
  - CLI：`fa agentline export --league E0 --season 2023 --n 30 [--seed 42]`、`fa agentline run --line A_base [--limit N]`、`fa agentline runs`

- [ ] **Step 1: 写失败测试（store + orchestrate）**

```python
"""store：UNIQUE(match_id,line) 幂等覆盖；orchestrate：mock runner 全链路。"""
import json
from pathlib import Path

import pytest

from fa.agentline import runner as runner_mod
from fa.agentline.contract import parse_prediction
from fa.agentline.orchestrate import run_line
from fa.agentline.store import save_prediction, save_run
from fa.db import connect, init_db


@pytest.fixture()
def db(tmp_path):
    init_db(tmp_path / "t.db")
    conn = connect(tmp_path / "t.db")
    conn.execute("PRAGMA foreign_keys=OFF")
    yield conn
    conn.close()


def _parsed(raw='{"p_home": 0.4, "p_draw": 0.3, "p_away": 0.3,'
                ' "p_over25": 0.5, "confidence": 0.5,'
                ' "reasoning_digest": "x", "sources": []}'):
    return parse_prediction(raw)


def test_save_prediction_upsert(db):
    assert save_prediction(db, 10, "A_base", _parsed(), "raw", "dsh 0.1.2",
                           "flash", 1.5) > 0
    n = save_prediction(db, 10, "A_base", _parsed(), "raw2", "dsh 0.1.2",
                        "flash", 1.6)          # 同 (match,line) → UPDATE
    rows = db.execute("SELECT * FROM agentline_predictions").fetchall()
    assert len(rows) == 1 and rows[0]["raw_output"] == "raw2"


def test_save_run_counts(db):
    rid = save_run(db, "A_base", "fa-agent-base", "flash",
                   {"ok": 3, "parse_fail": 1, "timeout": 0, "error": 0},
                   {"league": "E0"})
    row = db.execute("SELECT * FROM agentline_runs WHERE id=?", (rid,)).fetchone()
    assert row["n_ok"] == 3 and row["n_parse_fail"] == 1


def test_run_line_full_chain(db, tmp_path, monkeypatch):
    info = {"match": {"league": "E0", "season": 2023, "date": "2024-02-01",
                      "home": "Arsenal", "away": "Chelsea"}, "odds": {}}
    (tmp_path / "10.json").write_text(json.dumps(info), encoding="utf-8")
    ok_out = ('{"p_home": 0.4, "p_draw": 0.3, "p_away": 0.3, "p_over25": 0.5,'
              ' "confidence": 0.5, "reasoning_digest": "r", "sources": []}')
    monkeypatch.setattr(runner_mod, "run_headless",
                        lambda p, prof, timeout_s=None: (ok_out, None, 0.1))
    counts = run_line(db, "A_base", tmp_path)
    assert counts["ok"] == 1
    row = db.execute("SELECT * FROM agentline_predictions").fetchone()
    assert row["status"] == "ok" and abs(row["p_home"] - 0.4) < 1e-9
    # 幂等：再跑不再处理已 ok 的场次
    assert run_line(db, "A_base", tmp_path)["ok"] == 0


def test_run_line_degrades_when_dsh_dead(db, tmp_path, monkeypatch):
    (tmp_path / "10.json").write_text(
        json.dumps({"match": {"date": "2024-02-01"}, "odds": {}}),
        encoding="utf-8")
    monkeypatch.setattr(runner_mod, "run_headless",
                        lambda p, prof, timeout_s=None: (None, "可执行不存在：dsh", 0.0))
    counts = run_line(db, "A_base", tmp_path)         # 不抛
    assert counts["error"] == 1
    row = db.execute("SELECT * FROM agentline_predictions").fetchone()
    assert row["status"] == "error"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agentline/test_store.py tests/agentline/test_orchestrate.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 store.py**

```python
"""线 A 落库（设计 §6）：UNIQUE(match_id, line) 幂等 upsert。"""
import json
import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_prediction(conn: sqlite3.Connection, match_id: int, line: str,
                    parsed: dict, raw_output: str, harness: str,
                    model: str, duration_s: float) -> int:
    conn.execute(
        "INSERT INTO agentline_predictions (match_id, line, p_home, p_draw,"
        " p_away, p_over25, confidence, reasoning_digest, sources_json,"
        " raw_output, status, repaired, harness, model, duration_s, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(match_id, line) DO UPDATE SET"
        " p_home=excluded.p_home, p_draw=excluded.p_draw,"
        " p_away=excluded.p_away, p_over25=excluded.p_over25,"
        " confidence=excluded.confidence,"
        " reasoning_digest=excluded.reasoning_digest,"
        " sources_json=excluded.sources_json, raw_output=excluded.raw_output,"
        " status=excluded.status, repaired=excluded.repaired,"
        " harness=excluded.harness, model=excluded.model,"
        " duration_s=excluded.duration_s, created_at=excluded.created_at",
        (match_id, line, parsed["p_home"], parsed["p_draw"], parsed["p_away"],
         parsed["p_over25"], parsed["confidence"], parsed["reasoning_digest"],
         parsed["sources_json"], raw_output, parsed["status"],
         int(parsed["repaired"]), harness, model, duration_s, _now()))
    conn.commit()
    row = conn.execute(
        "SELECT id FROM agentline_predictions"
        " WHERE match_id=? AND line=?", (match_id, line)).fetchone()
    return row["id"]


def save_run(conn, line: str, profile: str, model: str | None,
             counts: dict, summary: dict) -> int:
    cur = conn.execute(
        "INSERT INTO agentline_runs (line, profile, model, n_ok,"
        " n_parse_fail, n_timeout, n_error, started_at, finished_at, summary)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (line, profile, model, counts.get("ok", 0), counts.get("parse_fail", 0),
         counts.get("timeout", 0), counts.get("error", 0), _now(), _now(),
         json.dumps(summary, ensure_ascii=False)))
    conn.commit()
    return cur.lastrowid
```

（`save_run` 的 started_at/finished_at 暂同值；编排若需真实起止再从参数传入——YAGNI，台账用途是计数与筛选条件留档。）

- [ ] **Step 4: 实现 orchestrate.py**

```python
"""run 编排（设计 §7）：信息集 → prompt → dsh → 契约 → 落库 → 台账。

幂等续跑：只处理「有信息集 JSON 且该 line 无 ok 行」的场次；dsh 整体不可
用时逐场记 error 行、run 正常结束（诚实降级——失败可见、可审计，不中断）。
"""
import json
import sqlite3
from pathlib import Path

from fa.agentline.contract import parse_prediction
from fa.agentline.runner import _PROFILE_LINE, build_prompt, run_headless
from fa.agentline.store import save_prediction, save_run

_HARNESS = "dsh (版本见附录 A)"      # spike 后可换成 dsh --version 的实测值


def _status_of(parsed: dict, run_err: str | None) -> tuple[dict, str]:
    """dsh 层失败 → timeout/error；成功但契约不过 → parse_fail。"""
    if run_err is not None:
        st = "timeout" if "超时" in run_err else "error"
        parsed = {**parsed, "status": st,
                  "reasoning_digest": f"dsh 失败：{run_err}"}
    return parsed, parsed["status"]


def run_line(conn: sqlite3.Connection, line: str, info_dir: Path,
             limit: int | None = None) -> dict:
    profile = _PROFILE_LINE[line]
    done = {r["match_id"] for r in conn.execute(
        "SELECT match_id FROM agentline_predictions"
        " WHERE line=? AND status='ok'", (line,))}
    todo = sorted((int(p.stem) for p in info_dir.glob("*.json")
                   if int(p.stem) not in done))
    if limit is not None:
        todo = todo[:limit]
    counts = {"ok": 0, "parse_fail": 0, "timeout": 0, "error": 0}
    for mid in todo:
        info = json.loads((info_dir / f"{mid}.json").read_text(encoding="utf-8"))
        out, err, dur = run_headless(build_prompt(info, line), profile)
        parsed, status = _status_of(parse_prediction(out or ""), err)
        save_prediction(conn, mid, line, parsed, out or "", _HARNESS,
                        "flash", dur)
        counts[status] += 1
    save_run(conn, line, profile, "flash", counts,
             {"n_todo": len(todo), "info_dir": str(info_dir)})
    return counts
```

- [ ] **Step 5: 跑 store/orchestrate 测试确认通过**

Run: `uv run pytest tests/agentline/ -v`
Expected: 全 PASS

- [ ] **Step 6: cli.py 挂 agentline 子 app（同 data_app 模式）**

在 cli.py 现有 `data_app` 定义之后加：

```python
agentline_app = typer.Typer(help="范式对比线（spec §12.5）——线 A：dsh agent 当大脑")
app.add_typer(agentline_app, name="agentline")


@agentline_app.command("export")
def agentline_export(league: str = typer.Option(...), season: int = typer.Option(...),
                     n: int = typer.Option(30), seed: int = typer.Option(42)) -> None:
    """抽样并导出信息集 JSON（幂等，已存在跳过）"""
    from fa.agentline.export import export_batch, pick_sample
    from fa.config import project_root
    conn = connect()
    ids = pick_sample(conn, league, season, n, seed)
    written = export_batch(conn, ids, project_root() / "data" / "agentline")
    conn.close()
    typer.echo(f"样本 {len(ids)} 场，新导出 {len(written)} 个信息集"
               f"（data/agentline/）")


@agentline_app.command("run")
def agentline_run(line: str = typer.Option(..., help="A_base 或 A_enh"),
                  limit: int = typer.Option(None)) -> None:
    """跑一批线 A 预测（幂等续跑：只补无 ok 行的场次）"""
    from fa.agentline.orchestrate import run_line
    from fa.config import project_root
    if line not in ("A_base", "A_enh"):
        typer.echo(f"line 必须是 A_base 或 A_enh，收到 {line}")
        raise typer.Exit(2)
    conn = connect()
    counts = run_line(conn, line, project_root() / "data" / "agentline", limit)
    conn.close()
    typer.echo(f"line={line} 完成：{counts}")


@agentline_app.command()
def runs() -> None:
    """线 A 运行台账"""
    conn = connect()
    for r in conn.execute(
            "SELECT id, line, n_ok, n_parse_fail, n_timeout, n_error,"
            " started_at, summary FROM agentline_runs"
            " ORDER BY id DESC LIMIT 20"):
        typer.echo(f"#{r['id']} {r['line']} ok={r['n_ok']} "
                   f"parse_fail={r['n_parse_fail']} timeout={r['n_timeout']} "
                   f"error={r['n_error']} @{r['started_at']}")
    conn.close()
```

CLI 测试（追加到 `tests/agentline/test_orchestrate.py`，`CliRunner` 模式对齐 `tests/test_cli.py`）：

```python
def test_cli_run_rejects_bad_line():
    from typer.testing import CliRunner
    from fa.cli import app
    res = CliRunner().invoke(app, ["agentline", "run", "--line", "B_line"])
    assert res.exit_code == 2
```

- [ ] **Step 7: 全量测试 + Commit**

Run: `uv run pytest -q`
Expected: 全 PASS

```bash
git add src/fa/agentline/store.py src/fa/agentline/orchestrate.py \
        src/fa/cli.py tests/agentline/test_store.py \
        tests/agentline/test_orchestrate.py
git commit -m "feat(agentline): 落库/编排/CLI——幂等续跑，dsh 挂了 run 也不断"
```

---

### Task 8: compare.py 三线对比 + 报告

**Files:**
- Create: `src/fa/agentline/compare.py`
- Modify: `src/fa/cli.py`（agentline_app 加 `compare` 命令）
- Test: `tests/agentline/test_compare.py`

**Interfaces:**
- Consumes: `backtest/metrics.py` 的 `evaluate`/`by_group`、`backtest/simulate.py` 的 `candidates`/`simulate_flat`；Task 3 两表
- Produces: `merge_rows(bp_rows: list[dict], agent_rows: list[dict]) -> list[dict]`（agentline 的 p_* 覆盖到 bp 行拷贝，保留 mkt_*/odds_*/outcome/total_goals——直接可喂 evaluate/candidates）；`compare_lines(conn, leagues=None, seasons=None) -> dict`——返回 `{"P": {...evaluate...}, "A_base": {...}, "A_enh": {...}, "market": {"ll": ...}, "roi": {"P": {...simulate_flat...}, "A_base": ..., "A_enh": ...}, "n": 总场次, "audit": {"enh_sources": 来源日期抽查统计}}`；`render_report(cmp: dict, out_path: Path) -> None`（markdown 滚动报告，含增强层泄漏标注固定段落）。

- [ ] **Step 1: 写失败测试**

```python
"""三线对比：复用 evaluate/simulate，行 schema 适配正确性是核心。"""
from fa.agentline.compare import compare_lines, merge_rows, render_report


def _bp(mid, ph=0.4):
    return {"match_id": mid, "league": "E0", "season": 2023,
            "date": "2024-02-01", "p_home": ph, "p_draw": 0.3,
            "p_away": 1 - ph - 0.3, "p_over25": 0.5, "p_under25": 0.5,
            "mkt_home": 0.45, "mkt_draw": 0.28, "mkt_away": 0.27,
            "odds_home": 2.2, "odds_draw": 3.5, "odds_away": 3.6,
            "outcome": "H", "total_goals": 3}


def test_merge_overwrites_probs_keeps_market():
    ag = [{"match_id": 1, "line": "A_base", "p_home": 0.6, "p_draw": 0.2,
           "p_away": 0.2, "p_over25": 0.7, "outcome": "H", "total_goals": 3}]
    rows = merge_rows([_bp(1)], ag)
    assert rows[0]["p_home"] == 0.6 and rows[0]["mkt_home"] == 0.45
    assert rows[0]["odds_home"] == 2.2 and rows[0]["outcome"] == "H"


def test_compare_lines_three_tracks(db_three):        # fixture 见 Step 3 注
    cmp = compare_lines(db_three)
    assert set(cmp) >= {"P", "A_base", "A_enh", "market", "roi", "n"}
    assert cmp["n"] >= 1
    assert cmp["P"]["model_ll"] > 0 and cmp["A_base"]["model_ll"] > 0


def test_render_report_mentions_leakage_caveat(tmp_path):
    from tests.agentline.test_compare import _fake_cmp
    out = tmp_path / "r.md"
    render_report(_fake_cmp(), out)
    text = out.read_text(encoding="utf-8")
    assert "泄漏" in text and "A_base" in text and "A_enh" in text
```

`db_three` fixture 与 `_fake_cmp()` 全文（放同测试文件顶部，勿引用不存在的名字）：

```python
@pytest.fixture()
def db_three(tmp_path):
    """1 场 bp 行 + A_base/A_enh 各一 ok 行（p_* 与线 P 略有差异）。"""
    init_db(tmp_path / "t.db")
    conn = connect(tmp_path / "t.db")
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        "INSERT INTO backtest_predictions (league, season, week_index,"
        " match_id, date, p_home, p_draw, p_away, p_over25, p_under25,"
        " mkt_home, mkt_draw, mkt_away, mkt_over25, odds_home, odds_draw,"
        " odds_away, outcome, total_goals)"
        " VALUES ('E0', 2023, 1, 1, '2024-02-01', 0.4, 0.3, 0.3, 0.5, 0.5,"
        " 0.45, 0.28, 0.27, 0.55, 2.2, 3.5, 3.6, 'H', 3)")
    for line, ph in (("A_base", 0.6), ("A_enh", 0.5)):
        conn.execute(
            "INSERT INTO agentline_predictions (match_id, line, p_home,"
            " p_draw, p_away, p_over25, confidence, reasoning_digest,"
            " sources_json, raw_output, status, repaired, created_at)"
            " VALUES (1, ?, ?, 0.2, ?, 0.6, 0.5, 'r', '[]', 'raw', 'ok',"
            " 0, '2026-09-04')", (line, ph, 1 - ph - 0.2))
    conn.commit()
    yield conn
    conn.close()


def _fake_cmp():
    from fa.backtest.metrics import evaluate
    rows = [_bp(1), _bp(2)]
    e = evaluate(rows)
    return {"n": 2, "P": e, "A_base": e, "A_enh": {"n": 0},
            "market": {"ll": e["market_ll"]},
            "roi": {"P": {"n": 0, "roi": 0.0}, "A_base": {"n": 0, "roi": 0.0},
                    "A_enh": {"n": 0, "roi": 0.0}},
            "audit": {"enh_sources": 0}}
```

（`_bp` 里已含 `p_over25` 键——`evaluate` 不读它、`candidates` 读，两处都兼容。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agentline/test_compare.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 compare.py**

```python
"""三线对比评测（设计 §8）：线 P / A_base / A_enh / 市场，同一判据。

行适配原则：agentline 的 p_* 覆盖 bp 行拷贝，mkt_*（Pinnacle 收盘去水）、
odds_*、outcome、total_goals 原样保留——evaluate 与 candidates 的入参
schema 完全复用，评测代码零改动（市场是所有评估的对照线，spec §8.2）。
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fa.backtest.metrics import evaluate
from fa.backtest.simulate import candidates, simulate_flat


def merge_rows(bp_rows: list[dict], agent_rows: list[dict]) -> list[dict]:
    by_mid = {r["match_id"]: r for r in agent_rows}
    out = []
    for bp in bp_rows:
        ag = by_mid.get(bp["match_id"])
        if ag is None:
            continue
        r = dict(bp)
        for k in ("p_home", "p_draw", "p_away", "p_over25"):
            r[k] = ag[k]
        out.append(r)
    return out


def _fetch_agent(conn, line, leagues, seasons) -> list[dict]:
    sql = ("SELECT * FROM agentline_predictions ap"
           " JOIN backtest_predictions bp ON bp.match_id = ap.match_id"
           " WHERE ap.line=? AND ap.status='ok'")
    args: list = [line]
    if leagues:
        sql += f" AND bp.league IN ({','.join('?' * len(leagues))})"
        args += list(leagues)
    if seasons:
        sql += f" AND bp.season IN ({','.join('?' * len(seasons))})"
        args += list(seasons)
    return [dict(r) for r in conn.execute(sql, args)]


def compare_lines(conn, leagues=None, seasons=None) -> dict:
    # 线 P 行直接复用回测读取器（联赛/赛季过滤语义单点维护）
    from fa.backtest.metrics import fetch_predictions
    bp = fetch_predictions(conn, leagues, seasons)
    cmp = {"n": len(bp), "P": evaluate(bp),
           "market": {"ll": evaluate(bp)["market_ll"]}}
    roi_rows = {"P": bp}
    for line in ("A_base", "A_enh"):
        ag = _fetch_agent(conn, line, leagues, seasons)
        rows = merge_rows(bp, ag)
        cmp[line] = evaluate(rows) if rows else {"n": 0}
        roi_rows[line] = rows
    cmp["roi"] = {k: simulate_flat(candidates(v)) if v else {"n": 0}
                  for k, v in roi_rows.items()}
    enh = _fetch_agent(conn, "A_enh", leagues, seasons)
    cmp["audit"] = {"enh_sources": sum(
        len(json.loads(r["sources_json"] or "[]")) for r in enh)}
    return cmp


def render_report(cmp: dict, out_path: Path) -> None:
    """滚动对比报告（设计 §8）：诚实标注增强层泄漏限制。"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# 双路对比滚动报告（生成于 {now}）", "",
             f"样本 n={cmp['n']}（线 P 与线 A 交集见各线 n）", "",
             "| 线 | n | log-loss | Brier | vs 市场 |",
             "|---|---|---|---|---|"]
    for k in ("P", "A_base", "A_enh"):
        e = cmp[k]
        if e.get("n"):
            lines.append(f"| {k} | {e['n']} | {e['model_ll']:.4f} | "
                         f"{e['model_brier']:.4f} | {e['ratio']:.3f}× |")
        else:
            lines.append(f"| {k} | 0 | — | — | — |")
    lines.append(f"| 市场 | {cmp['n']} | {cmp['market']['ll']:.4f} | — | 1.000× |")
    lines += ["", "## 平注 ROI", ""]
    for k, v in cmp["roi"].items():
        lines.append(f"- {k}: n={v['n']} roi={v['roi']:+.1%}"
                     if v.get("n") else f"- {k}: 无候选注")
    lines += ["", "> ⚠️ A_enh（增强层）为历史回放检索，可能受赛后信息泄漏污染"
              "（缓解措施与抽查见设计 §5.2）——其结论不与 A_base 混排。", ""]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
```

- [ ] **Step 4: cli.py 加 compare 命令**

```python
@agentline_app.command()
def compare(league: str = typer.Option(None), season: int = typer.Option(None),
            out: str = typer.Option(None)) -> None:
    """三线对比滚动报告（docs/agentline/compare-YYYYMMDD.md）"""
    from datetime import date
    from pathlib import Path

    from fa.agentline.compare import compare_lines, render_report
    from fa.config import project_root
    conn = connect()
    cmp = compare_lines(conn,
                        [league] if league else None,
                        [season] if season else None)
    conn.close()
    p = Path(out) if out else (
        project_root() / "docs" / "agentline" / f"compare-{date.today():%Y%m%d}.md")
    render_report(cmp, p)
    typer.echo(f"报告已写：{p}")
```

（`Path` 用函数内 import——对齐 cli.py 现行模式（如 203 行 `from pathlib import Path as _P`），顶部不加。）

- [ ] **Step 5: 跑测试 + 全量回归 + Commit**

Run: `uv run pytest tests/agentline/ -v && uv run pytest -q`
Expected: 全 PASS

```bash
git add src/fa/agentline/compare.py src/fa/cli.py tests/agentline/test_compare.py
git commit -m "feat(agentline): 三线对比评测与滚动报告——复用 evaluate/simulate"
```

---

### Task 9: E2E 首批真跑 + 报告回填

**Files:**
- Create: `docs/agentline/compare-<当日>.md`（命令产出）
- Modify: `docs/superpowers/specs/2026-09-04-agentline-dual-track-design.md` 附录 A（回填首批真实成本/成功率三数字）

**Interfaces:**
- Consumes: Task 2 的 dsh 环境、Task 7/8 的 CLI
- Produces: 首批真实数据 + 首份对比报告（后续扩批是常规操作，非里程碑——设计 §7）

- [ ] **Step 1: 抽样导出首批信息集**

```bash
uv run fa agentline export --league E0 --season 2023 --n 10
ls data/agentline/ | head    # 确认 10 个 JSON
```

- [ ] **Step 2: 基线层真跑**

```bash
uv run fa agentline run --line A_base --limit 3   # 先 3 场验证全链路
uv run fa agentline runs                            # 看 ok/parse_fail 比例
sqlite3 data/fa.db "SELECT status, COUNT(*) FROM agentline_predictions GROUP BY status"
```

3 场里 ≥2 场 ok 才继续；全 parse_fail → 回附录 A 检查输出形态，修 runner 参数再跑（Task 2 的附录是权威）。

- [ ] **Step 3: 补满首批 + 增强层**

```bash
uv run fa agentline run --line A_base              # 幂等补满 10 场
uv run fa agentline run --line A_enh               # 增强层同批
```

- [ ] **Step 4: 首份对比报告 + 人工 sanity**

```bash
uv run fa agentline compare
```

人工核对报告三件事：① 线 P 的 log-loss 与 M2 报告同量级（样本小有波动正常）② A_base 的 n>0 且 vs 市场比值在合理量级（如 <3×）③ 泄漏警示段存在。

- [ ] **Step 5: 附录 A 回填真实成本与成功率，commit 全部产出**

```bash
git add docs/agentline/ docs/superpowers/specs/2026-09-04-agentline-dual-track-design.md
git commit -m "chore(agentline): E2E 首批真跑——10 场双线数据与首份滚动报告"
```

---

## 自审记录（writing-plans Self-Review）

1. **Spec 覆盖**：设计 §4（契约/prompt/降级）→ Task 4/6/7；§5.1 信息集 → Task 5；§5.2 泄漏缓解（before: 日期限定 + sources 落库 + 报告警示）→ Task 6 prompt/Task 8 audit+报告；§6 落库 → Task 3/7；§7 长期并行/幂等续跑 → Task 7/9；§8 评测 → Task 8；§9 CLI → Task 7/8；§10 spike → Task 2；§11 spec 合入 → Task 1。§12.5 报告输出目录 `docs/agentline/` 在 Task 8/9 落地。无缺口。
2. **占位符扫描**：Task 2 的「<安装源>」是 spike 本身的探求对象（任务指令就是把它变成实测值），非计划占位；`_standing` 两分支与 `compare_lines` 直用 `fetch_predictions` 均已写成最终形态；测试辅助（`db_three`/`_fake_cmp`）全文给出。其余步骤均有完整代码。
3. **类型一致性**：`parse_prediction(raw, repaired_ok=False) -> dict`（Task 4 定义、Task 7 消费）；`run_headless(prompt, profile, timeout_s=None) -> (stdout, error, duration)`（Task 6 定义、Task 7 mock 同签名）；`merge_rows(bp, agent)`（Task 8 内自洽）；`_PROFILE_LINE`（Task 6 定义、Task 7 消费）。已核对。
