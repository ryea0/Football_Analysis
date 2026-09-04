# 线 A 形态三 A_debate（辩论修订制）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 A_debate：单场「生成者 v0 → 批评者攻击 → 生成者修订」≤2 轮的确定性编排链，逐轮产物落库、终版入 agentline_predictions、compare 增列并出修订增益段。

**Architecture:** schema v9 重建 `agentline_predictions`（line 词表加 A_debate/A_division 五词一次到位 + `budget_exhausted` 列）；新纯函数模块 `src/fa/agentline/debate.py`（prompt 构建 + 单场引擎）；`contract.py` 加 `parse_attack`（封闭五标签，批评者/质询官共用）；`orchestrate.py` 加 `run_debate`；`compare.py` 增 A_debate 列 + `debate_gain`（v0 vs 终版 + severity-修订幅度相关性）。Python 指挥官：轮次/终止/预算规则全部预注册在代码常量里，agent 是感官。

**Tech Stack:** Python 3.11+ / uv / typer / pytest / sqlite3（WAL）/ scipy（spearmanr，已有依赖）/ dsh headless。

**Spec:** docs/superpowers/specs/2026-09-05-agentline-debate-division-design.md §2（形态三）、§4（放量与成本）、§5（schema）；宿主 docs/superpowers/specs/2026-09-04-agentline-multi-brain-design.md。

## Global Constraints

- **开工前提（撞号协议）**：主干 `src/fa/db.py` 的 `SCHEMA_VERSION == 8`（M6 已合并）。若读到 7，停止并上报——本计划取 v9，M6 未合不可抢号
- 预注册常量（spec §2.2，实施不得静默调，变更走设计档修订）：轮数上限 2；提前终止 ε=0.02（相邻版本 (p_home,p_draw,p_away) max-abs **<** 0.02 停轮，修订版产出后、下轮批评前判定）；预算硬上限每场 5 次调用；批评者失败→不发起该轮修订；终版=**最晚一个 status='ok' 的生成者版本**（v0 即失败则终版为其失败态）
- 批评者契约：label 封闭五词 `overconfidence / missing_context / alt_explanation / internal_inconsistency / evidence_weak`；severity 0-1 有限值；attacks 可为空数组；**出现 p_home/p_draw/p_away/p_over25 字段即 parse_fail**（禁概率数字）
- 成员用 `fa-agent-base` profile（与 A_base/A_multi 成员同源）；生成者输出复用 `parse_prediction` 全契约（三项和 ±0.05 归一口径照旧）
- A_debate 产出永不进 B 线推荐/落注流；真实下注禁止
- 测试全离线（mock call 注入）；E2E 主库直跑（活库为运营库，迁移幂等护栏兜底）
- 诚实条款：E2E 计数如实入台账；不重跑挑结果；n<100 数字只作管线可用性证据披露（spec §4.2 数据齐口径之前不作方向性结论）
- 生成者 v0 prompt = 现有 `build_prompt(info, "A_base")` 原样（同源可比）

---

### Task 1: schema v9——agentline_predictions 二次重建（五词表 + budget_exhausted）

**Files:**
- Modify: `src/fa/db.py`（`SCHEMA_VERSION`、`_AL_TABLE`、`_migrate_up`）
- Test: `tests/agentline/test_db_v9.py`（新建）
- Test: `tests/test_db.py`（`test_migrate_and_fresh_schemas_match` 参数表加 8）

**Interfaces:**
- Consumes: 现有 `_AL_TABLE`（db.py:193）、v7 影子重建模式（db.py `_migrate_up` 的 `if from_v < 7` 块）
- Produces: ①`agentline_predictions` 新形状——line CHECK 五词 `('A_base','A_enh','A_multi','A_debate','A_division')`、新列 `budget_exhausted INTEGER NOT NULL DEFAULT 0`（attributor 列之后）；存量行迁移后 budget_exhausted=0。②新表 `agentline_debate_rounds`（spec §2.4 DDL 原文，常量 `_AL_DEBATE_TABLE`）

- [ ] **Step 1: 写失败测试（新文件 `tests/agentline/test_db_v9.py`）**

```python
"""schema v9（2026-09-05 设计 §2.4/§5）：五词表 + budget_exhausted，v8→v9 影子重建。"""
import sqlite3

from fa.db import SCHEMA_VERSION, _AL_TABLE, connect, init_db


def _cols(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _pin(conn, version: int):
    conn.execute("UPDATE schema_version SET version=?", (version,))
    conn.commit()
    conn.close()


def _old_shape_db(path, n_rows=2):
    """手造 v8 形状的库：三词 CHECK、无 budget_exhausted、有存量行。"""
    init_db(path)
    conn = connect(path)
    conn.executescript("DROP INDEX IF EXISTS idx_alp_line;"
                       "DROP TABLE IF EXISTS agentline_predictions;")
    conn.executescript("""
        CREATE TABLE agentline_predictions (
            id INTEGER PRIMARY KEY, match_id INTEGER NOT NULL,
            line TEXT NOT NULL CHECK (line IN ('A_base','A_enh','A_multi')),
            attributor INTEGER NOT NULL DEFAULT 1,
            p_home REAL, p_draw REAL, p_away REAL, p_over25 REAL,
            confidence REAL, reasoning_digest TEXT, sources_json TEXT,
            raw_output TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('ok','parse_fail','timeout','error')),
            repaired INTEGER, harness TEXT, model TEXT, duration_s REAL,
            created_at TEXT NOT NULL,
            UNIQUE (match_id, line, attributor));""")
    conn.execute("CREATE INDEX idx_alp_line ON agentline_predictions (line);")
    conn.execute("PRAGMA foreign_keys=OFF")      # 测试不造 matches 行（先例
    # test_line_check_constraint 同款）
    for i in range(1, n_rows + 1):
        conn.execute(
            "INSERT INTO agentline_predictions (match_id, line, attributor,"
            " raw_output, status, created_at) VALUES (?,?,?,?,'ok',?)",
            (i, "A_multi" if i % 2 else "A_base", i % 2, "x", "2026-09-05"))
    _pin(conn, 8)
    return path


def test_v8_old_shape_migrates_to_v9(tmp_path):
    db = _old_shape_db(tmp_path / "t.db")
    init_db(db)
    conn = connect(db)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[
        "version"] == SCHEMA_VERSION
    rows = conn.execute("SELECT match_id, line, budget_exhausted FROM"
                        " agentline_predictions ORDER BY match_id").fetchall()
    assert [(r["match_id"], r["budget_exhausted"]) for r in rows] == [(1, 0), (2, 0)]
    conn.execute("PRAGMA foreign_keys=OFF")   # 只验 CHECK，不造 matches 行
    conn.execute("INSERT INTO agentline_predictions (match_id, line,"
                 " raw_output, status, created_at)"
                 " VALUES (99,'A_debate','x','ok','2026-09-05')")
    conn.commit()
    conn.close()


def test_v9_shadow_recovery(tmp_path):
    """半途迁移留下影子表 agentline_predictions_v8：重试从影子重放。"""
    db = _old_shape_db(tmp_path / "t.db")
    conn = connect(db)
    # 模拟窗 B：rename 完成、新表未建（索引随表走、保持原名 idx_alp_line，
    # 迁移代码里的 DROP INDEX IF EXISTS idx_alp_line 负责摘除——勿臆造新名）
    conn.executescript("ALTER TABLE agentline_predictions RENAME TO"
                       " agentline_predictions_v8;")
    _pin(conn, 8)
    init_db(db)
    conn = connect(db)
    assert conn.execute("SELECT COUNT(*) c FROM agentline_predictions"
                        " WHERE status='ok'").fetchone()["c"] == 2
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name="
                        "'agentline_predictions_v8'").fetchone() is None
    conn.close()


def test_v9_guard_skips_rebuilt_table(tmp_path):
    """已是新形状（budget_exhausted 在）而版本号钉在 8：跳过不炸。"""
    db = tmp_path / "t.db"
    init_db(db)
    conn = connect(db)
    _pin(conn, 8)
    conn.close()
    init_db(db)                               # fresh 形状 + v8 → 守卫跳过
    conn = connect(db)
    assert "budget_exhausted" in _cols(conn, "agentline_predictions")
    conn.close()


def test_fresh_word_list_accepts_debate_and_division(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    conn = connect(db)
    conn.execute("PRAGMA foreign_keys=OFF")
    for line in ("A_debate", "A_division"):
        conn.execute("INSERT INTO agentline_predictions (match_id, line,"
                     " raw_output, status, created_at)"
                     f" VALUES (1,'{line}','x','ok','2026-09-05')")
    conn.commit()
    conn.close()


def test_fresh_and_migrated_have_debate_rounds(tmp_path):
    for db in (tmp_path / "fresh.db",):
        init_db(db)
    conn = connect(tmp_path / "fresh.db")
    assert "agentline_debate_rounds" in {
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    # UNIQUE(match_id, round, role)：同三元第二次插入必须炸
    conn.execute("INSERT INTO agentline_debate_rounds (match_id, round,"
                 " role, payload_json, raw_output, status, created_at)"
                 " VALUES (1,0,'generator','{}','r','ok','2026-09-05')")
    try:
        conn.execute("INSERT INTO agentline_debate_rounds (match_id, round,"
                     " role, payload_json, raw_output, status, created_at)"
                     " VALUES (1,0,'generator','{}','r2','ok','2026-09-05')")
        raise AssertionError("UNIQUE 三元未生效")
    except sqlite3.IntegrityError:
        pass
    conn.close()
    # 迁移路径同样有表
    db2 = _old_shape_db(tmp_path / "mig.db")
    init_db(db2)
    conn2 = connect(db2)
    assert "agentline_debate_rounds" in {
        r["name"] for r in conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    conn2.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agentline/test_db_v9.py -v`
Expected: FAIL——`budget_exhausted` 列不存在 / CHECK 拒绝 A_debate

- [ ] **Step 3: 改 `_AL_TABLE`（db.py:193 起）**

line CHECK 行改为：

```sql
        CHECK (line IN ('A_base', 'A_enh', 'A_multi', 'A_debate', 'A_division')),
```

`attributor` 行之后加一列：

```sql
        budget_exhausted INTEGER NOT NULL DEFAULT 0,  -- A_debate 轮中断记 1（v9，2026-09-05 设计 §2.4）；其余线恒 0
```

`_AL_TABLE` 字符串闭合后新增常量（`_AL_TABLE` 与 `_BLINE_TABLE` 之间，spec §2.4 DDL 原文）：

```python
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
```

`_SCHEMA` 组合行（db.py:277）改为：

```python
""" + _BP_TABLE + _BLINE_TABLE + _RETRO_TABLE + _AL_TABLE + _AL_DEBATE_TABLE
```

- [ ] **Step 4: `_migrate_up` 加 v8→v9 块（v7 块之后，UPDATE schema_version 之前）**

```python
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
            conn.execute("DROP INDEX IF EXISTS idx_alp_line")
            if has_shadow:
                conn.execute("DROP TABLE IF EXISTS agentline_predictions")
            else:
                conn.executescript(
                    "ALTER TABLE agentline_predictions RENAME TO"
                    " agentline_predictions_v8;")
            conn.executescript(_AL_TABLE)
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
            conn.execute("DROP TABLE agentline_predictions_v8;")
```

`SCHEMA_VERSION = 9`（db.py:6）。docstring 的 `_migrate_up` 版本史注释追加一行 `v8->v9 重建……`。

- [ ] **Step 5: `tests/test_db.py` 的 `test_migrate_and_fresh_schemas_match` 参数表加 `8`**

找到该测试的 `@pytest.mark.parametrize("from_v", [...])`，追加 8（v8 形状=本计划 `_old_shape_db` 的手造形状；该测试自建旧形状，加号后照跑）。

- [ ] **Step 6: 全量 db 测试通过**

Run: `uv run pytest tests/agentline/test_db_v9.py tests/test_db.py -v`
Expected: 全 PASS

- [ ] **Step 7: Commit**

```bash
git add src/fa/db.py tests/agentline/test_db_v9.py tests/test_db.py
git commit -m "feat(db): schema v9——agentline_predictions 五词表+budget_exhausted（A_debate 前置）"
```

### Task 2: contract.parse_attack——批评者攻击契约

**Files:**
- Modify: `src/fa/agentline/contract.py`（文件尾追加）
- Test: `tests/agentline/test_contract.py`（追加）

**Interfaces:**
- Consumes: 现有 `_extract_json`（contract.py:15）
- Produces: `ATTACK_LABELS: tuple[str, ...]`；`parse_attack(raw: str, repaired_ok: bool = False) -> dict`——ok 态 `{"status": "ok", "attacks": [{"label", "reason"(≤200字), "severity"}], "repaired": bool}`；失败态 `{"status": "parse_fail", "attacks": [], "error": "<中文原因>", "repaired": bool}`（形态二质询官复用同一函数）

- [ ] **Step 1: 写失败测试（追加到 `tests/agentline/test_contract.py`）**

```python
from fa.agentline.contract import ATTACK_LABELS, parse_attack


def test_parse_attack_ok_all_labels():
    items = [{"label": lb, "reason": "r", "severity": 0.5}
             for lb in ATTACK_LABELS]
    raw = json.dumps({"attacks": items})
    got = parse_attack(raw)
    assert got["status"] == "ok"
    assert [a["label"] for a in got["attacks"]] == list(ATTACK_LABELS)


def test_parse_attack_empty_attacks_legal():
    got = parse_attack('{"attacks": []}')
    assert got["status"] == "ok" and got["attacks"] == []


def test_parse_attack_unknown_label_fails():
    got = parse_attack('{"attacks": [{"label": "haha", "reason": "r",'
                       ' "severity": 0.5}]}')
    assert got["status"] == "parse_fail" and got["attacks"] == []


def test_parse_attack_severity_bounds():
    for bad in ("-0.1", "1.1", "NaN", "Infinity"):
        got = parse_attack('{"attacks": [{"label": "overconfidence",'
                           f' "reason": "r", "severity": {bad}}]}}'.replace("}}", "}"))
        assert got["status"] == "parse_fail", bad


def test_parse_attack_probability_field_guard():
    got = parse_attack('{"attacks": [], "p_home": 0.5}')
    assert got["status"] == "parse_fail"
    assert "概率" in got["error"]


def test_parse_attack_fence_extraction_marks_repaired():
    got = parse_attack('前言```json\n{"attacks": []}\n```后语')
    assert got["status"] == "ok" and got["repaired"] is True


def test_parse_attack_reason_truncated_to_200():
    got = parse_attack('{"attacks": [{"label": "overconfidence",'
                       ' "reason": "%s", "severity": 0.1}]}' % ("字" * 300))
    assert len(got["attacks"][0]["reason"]) == 200
```

（文件头若未 import json 则补 `import json`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agentline/test_contract.py -k attack -v`
Expected: FAIL——`ATTACK_LABELS` 不存在（ImportError）

- [ ] **Step 3: 实现（`src/fa/agentline/contract.py` 文件尾追加）**

```python
ATTACK_LABELS = ("overconfidence", "missing_context", "alt_explanation",
                 "internal_inconsistency", "evidence_weak")
_PROB_FIELDS = ("p_home", "p_draw", "p_away", "p_over25")


def parse_attack(raw: str, repaired_ok: bool = False) -> dict:
    """批评者/质询官攻击契约（2026-09-05 设计 §2.3/§3.3）。

    封闭五标签 + reason≤200 字 + severity∈[0,1] 有限值；attacks 可为空
    （无攻击点=合法）。禁概率数字：obj 出现任一 p_* 字段即 parse_fail
    （守卫与 parse_prediction 的「绝不脑补」同一风格）。
    """
    repaired = repaired_ok
    try:
        try:
            obj = json.loads(raw.strip())
        except json.JSONDecodeError:
            obj = _extract_json(raw)
            repaired = True
        if any(f in obj for f in _PROB_FIELDS):
            raise ValueError("攻击 JSON 出现概率字段（批评者禁数字）")
        items = obj.get("attacks")
        if not isinstance(items, list):
            raise ValueError("attacks 必须是数组")
        out = []
        for it in items:
            label = it["label"]
            if label not in ATTACK_LABELS:
                raise ValueError(f"label 越界：{label}")
            sev = float(it["severity"])
            if math.isnan(sev) or math.isinf(sev) or not 0 <= sev <= 1:
                raise ValueError("severity 非有限值或越界")
            out.append({"label": label,
                        "reason": str(it.get("reason", ""))[:200],
                        "severity": sev})
        return {"status": "ok", "attacks": out, "repaired": repaired}
    except (KeyError, ValueError, TypeError, json.JSONDecodeError,
            AttributeError) as exc:
        return {"status": "parse_fail", "attacks": [],
                "error": f"parse_fail 原因：{type(exc).__name__}: {exc}",
                "repaired": repaired}
```

- [ ] **Step 4: 跑测试通过**

Run: `uv run pytest tests/agentline/test_contract.py -v`
Expected: 全 PASS（含既有用例）

- [ ] **Step 5: Commit**

```bash
git add src/fa/agentline/contract.py tests/agentline/test_contract.py
git commit -m "feat(agentline): parse_attack 攻击契约——五标签封闭枚举+禁概率数字"
```

### Task 3: debate.py——prompt 构建与 max_abs_delta

**Files:**
- Create: `src/fa/agentline/debate.py`
- Test: `tests/agentline/test_debate.py`（新建）

**Interfaces:**
- Consumes: `build_prompt(info_set, line)`（runner.py:33）
- Produces: `build_critic_prompt(info_set: dict, prev_pred: str, prev_attack: str | None = None) -> str`；`build_revision_prompt(info_set: dict, prev_pred: str, attack: str) -> str`；`max_abs_delta(a: dict, b: dict) -> float`（只比三项胜平负，入参须均 status='ok'）

- [ ] **Step 1: 写失败测试（新建 `tests/agentline/test_debate.py`）**

```python
import json

from fa.agentline.debate import (ATTACK_ENUM_HELP, build_critic_prompt,
                                 build_revision_prompt, max_abs_delta)

INFO = {"match": {"date": "2026-05-01", "home": "A", "away": "B"}}


def test_critic_prompt_carries_info_and_prev():
    p = build_critic_prompt(INFO, '{"p_home": 0.5}')
    assert "A" in p and '{"p_home": 0.5}' in p
    assert all(w in p for w in ATTACK_ENUM_HELP.split("|"))


def test_critic_prompt_second_round_includes_prev_attack():
    p = build_critic_prompt(INFO, "v1", prev_attack='{"attacks": []}')
    assert '{"attacks": []}' in p


def test_revision_prompt_carries_all_three_sections():
    p = build_revision_prompt(INFO, "PREV", "ATK")
    assert "PREV" in p and "ATK" in p and "2026-05-01" in p


def test_max_abs_delta_direction_and_value():
    a = {"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2}
    b = {"p_home": 0.52, "p_draw": 0.30, "p_away": 0.18}
    assert max_abs_delta(a, b) == 0.02          # 0.02 与 0.02 并列取 max
    assert max_abs_delta(b, a) == max_abs_delta(a, b)   # 对称性：方向无关
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agentline/test_debate.py -v`
Expected: FAIL——模块不存在

- [ ] **Step 3: 实现 `src/fa/agentline/debate.py`（本任务只写 prompt 与 delta；引擎 Task 4 追加）**

```python
"""A_debate 轮次引擎（2026-09-05 设计 §2）：生成者-批评者-修订，≤2 轮。

预注册常量（变更走设计档修订，不得实施期静默调）：轮数上限 2、预算硬上限
每场 5 次调用、提前终止 ε=0.02（相邻版本胜平负 max-abs < 0.02，修订版
产出后、下轮批评前判定）。批评者失败→不发起该轮修订；终版=最晚 ok 版。
"""
import json

from fa.agentline.runner import build_prompt  # noqa: F401  (Task 4 引擎复用)

ATTACK_ENUM_HELP = ("overconfidence|missing_context|alt_explanation"
                    "|internal_inconsistency|evidence_weak")


def build_critic_prompt(info_set: dict, prev_pred: str,
                        prev_attack: str | None = None) -> str:
    prev = ("# 前轮攻击（你上一轮的评审，勿重复）\n" + prev_attack + "\n\n"
            if prev_attack else "")
    return (
        "你是量化预测的独立评审。以下是赛前信息集与一个待审预测。\n\n"
        "# 赛前信息集\n" + json.dumps(info_set, ensure_ascii=False) + "\n\n"
        "# 待审预测\n" + prev_pred + "\n\n" + prev +
        "# 要求\n"
        "- 只产定性判断：攻击其弱点/替代解释/内部矛盾/过度自信/证据薄弱，"
        "禁止输出任何概率数字\n"
        "- 最终必须输出且仅输出一个 JSON 对象（不要 markdown 围栏）：\n"
        '  {"attacks": [{"label": "<枚举词>", "reason": "<=200字", '
        '"severity": <0-1>}]}\n'
        f"- label 封闭枚举：{ATTACK_ENUM_HELP}；"
        '无攻击点时输出 {"attacks": []}\n')


def build_revision_prompt(info_set: dict, prev_pred: str, attack: str) -> str:
    return (
        "你是职业足球量化分析师。基于以下赛前信息、你自己的上一版预测与"
        "独立评审的攻击，输出修订版预测。\n\n"
        "# 赛前信息集\n" + json.dumps(info_set, ensure_ascii=False) + "\n\n"
        "# 上一版预测（你的初版）\n" + prev_pred + "\n\n"
        "# 独立评审的攻击\n" + attack + "\n\n"
        "# 要求\n"
        "- 吸收合理批评、拒绝不合理批评，自主判断\n"
        "- 最终必须输出且仅输出一个 JSON 对象（不要 markdown 围栏）：\n"
        '  {"p_home": <0-1>, "p_draw": <0-1>, "p_away": <0-1>, '
        '"p_over25": <0-1>, "confidence": <0-1>, "reasoning_digest": '
        '"<=200字", "sources": []}\n'
        "- p_home + p_draw + p_away 之和应接近 1\n"
        "- sources：若使用了信息集之外的信息逐条列出 {title, date, url}；"
        "否则为空数组\n")


def max_abs_delta(a: dict, b: dict) -> float:
    """相邻版本胜平负 max-abs（取绝对值故方向无关；入参均须 status='ok'）。"""
    return max(abs(a[k] - b[k]) for k in ("p_home", "p_draw", "p_away"))
```

- [ ] **Step 4: 跑测试通过**

Run: `uv run pytest tests/agentline/test_debate.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/fa/agentline/debate.py tests/agentline/test_debate.py
git commit -m "feat(agentline): debate prompt 构建（评审/修订）+ max_abs_delta"
```

### Task 4: debate.py——run_match_debate 单场引擎

**Files:**
- Modify: `src/fa/agentline/debate.py`（追加引擎）
- Test: `tests/agentline/test_debate.py`（追加）

**Interfaces:**
- Consumes: `parse_prediction`、`parse_attack`、Task 3 的 prompt 函数
- Produces: `run_match_debate(call, info: dict) -> dict`——`call(prompt) -> (stdout|None, error|None, duration_s)`（与 `runner.run_headless` 同签名，测试注入）；返回 `{"final": <契约 dict>, "rounds": [{"round": int, "role": "generator"|"critic", "payload": str, "raw": str, "status": str, "dur": float}], "n_calls": int, "budget_exhausted": 0|1, "early_stop": 0|1}`

- [ ] **Step 1: 写失败测试（追加）**

```python
OK0 = json.dumps({"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2,
                  "p_over25": 0.5, "confidence": 0.6, "reasoning_digest": "d",
                  "sources": []})
OK1 = json.dumps({"p_home": 0.55, "p_draw": 0.28, "p_away": 0.17,
                  "p_over25": 0.5, "confidence": 0.6, "reasoning_digest": "d",
                  "sources": []})
OK2 = json.dumps({"p_home": 0.6, "p_draw": 0.25, "p_away": 0.15,
                  "p_over25": 0.5, "confidence": 0.6, "reasoning_digest": "d",
                  "sources": []})
ATK = json.dumps({"attacks": [{"label": "overconfidence", "reason": "r",
                               "severity": 0.8}]})


def _call_seq(seq):
    it = iter(seq)
    def call(prompt):
        out = next(it)
        return out, None, 0.01
    return call


def test_v0_fail_short_circuits():
    res = run_match_debate(_call_seq(["不是JSON"]), INFO)
    assert res["final"]["status"] == "parse_fail"
    assert res["n_calls"] == 1 and len(res["rounds"]) == 1
    assert res["budget_exhausted"] == 0 and res["early_stop"] == 0


def test_critic_fail_aborts_round_with_budget_exhausted():
    res = run_match_debate(_call_seq([OK0, "垃圾"]), INFO)
    assert res["final"]["status"] == "ok"          # 终版=v0（最晚 ok）
    assert abs(res["final"]["p_home"] - 0.5) < 1e-9
    assert res["budget_exhausted"] == 1 and res["early_stop"] == 0
    assert res["n_calls"] == 2
    assert [r["role"] for r in res["rounds"]] == ["generator", "critic"]


def test_revision_fail_takes_last_ok():
    res = run_match_debate(_call_seq([OK0, ATK, "垃圾"]), INFO)
    assert abs(res["final"]["p_home"] - 0.5) < 1e-9
    assert res["budget_exhausted"] == 1
    assert res["n_calls"] == 3


def test_early_stop_when_delta_below_eps():
    ok1_eps = json.dumps({"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2,
                          "p_over25": 0.5, "confidence": 0.6,
                          "reasoning_digest": "d", "sources": []})
    # v1==v0：delta 0 < 0.02 → 轮1后停
    res = run_match_debate(_call_seq([OK0, ATK, ok1_eps]), INFO)
    assert res["early_stop"] == 1 and res["n_calls"] == 3
    assert res["budget_exhausted"] == 0
    # v2 对 v1（OK1=0.55/0.28/0.17）delta = max(0.005,0,0.005)=0.005 < 0.02：轮2后停
    ok2_eps = json.dumps({"p_home": 0.555, "p_draw": 0.28, "p_away": 0.165,
                          "p_over25": 0.5, "confidence": 0.6,
                          "reasoning_digest": "d", "sources": []})
    res2 = run_match_debate(_call_seq([OK0, ATK, OK1, ATK, ok2_eps]), INFO)
    assert res2["early_stop"] == 1 and res2["n_calls"] == 5
    assert abs(res2["final"]["p_home"] - 0.555) < 1e-9


def test_delta_exactly_eps_continues():
    # v1 对 v0 的 delta 恰为 0.02（0.52/0.30/0.18）："< ε" 才停，等号继续
    ok_eq = json.dumps({"p_home": 0.52, "p_draw": 0.30, "p_away": 0.18,
                        "p_over25": 0.5, "confidence": 0.6,
                        "reasoning_digest": "d", "sources": []})
    res = run_match_debate(_call_seq([OK0, ATK, ok_eq, ATK, OK2]), INFO)
    assert res["early_stop"] == 0 and res["n_calls"] == 5


def test_full_two_rounds_five_calls():
    res = run_match_debate(_call_seq([OK0, ATK, OK1, ATK, OK2]), INFO)
    assert res["n_calls"] == 5 and res["early_stop"] == 0
    assert res["budget_exhausted"] == 0
    assert abs(res["final"]["p_home"] - 0.6) < 1e-9
    assert [r["role"] for r in res["rounds"]] == [
        "generator", "critic", "generator", "critic", "generator"]


def test_round2_critic_sees_prev_attack():
    seen = {}
    def call(prompt):
        seen[ prompt.count("# 前轮攻击") ] = prompt
        nxt = [OK0, ATK, OK1, ATK, OK2][len(seen) - 1]
        return nxt, None, 0.01
    run_match_debate(call, INFO)
    assert 1 in seen                       # 轮2 批评者带前轮攻击段
```

（文件头 import 行改为 `from fa.agentline.debate import (ATTACK_ENUM_HELP, build_critic_prompt, build_revision_prompt, max_abs_delta, run_match_debate)`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agentline/test_debate.py -v`
Expected: FAIL——`run_match_debate` 不存在

- [ ] **Step 3: 实现（`debate.py` 追加）**

```python
_MAX_ROUNDS = 2
_EPS = 0.02
_PROB_KEYS = ("p_home", "p_draw", "p_away")


def _status_of(parsed: dict, run_err: str | None) -> dict:
    """dsh 层失败 → timeout/error（与 orchestrate._status_of 同式，返回 dict）。"""
    if run_err is not None:
        return {**parsed, "status": "timeout" if "超时" in run_err else "error",
                "reasoning_digest": f"dsh 失败：{run_err}"}
    return parsed


def run_match_debate(call, info: dict) -> dict:
    """单场辩论（纯编排；call 注入，与 runner.run_headless 同签名）。

    预注册语义（设计 §2.2）：批评者失败→本轮不发起修订、budget_exhausted=1；
    修订失败→取上一 ok 版、budget_exhausted=1；ε 提前终止→early_stop=1。
    """
    rounds: list[dict] = []
    n_calls = 0
    budget_exhausted = 0
    early_stop = 0

    def _record(round_no: int, role: str, parsed: dict, raw: str,
                dur: float) -> None:
        rounds.append({"round": round_no, "role": role,
                       "payload": json.dumps(parsed, ensure_ascii=False),
                       "raw": raw, "status": parsed["status"], "dur": dur})

    out, err, dur = call(build_prompt(info, "A_base"))
    n_calls += 1
    v0 = _status_of(parse_prediction(out or ""), err)
    _record(0, "generator", v0, out or "", dur)
    last_ok = v0 if v0["status"] == "ok" else None
    last_ok_raw = (out or "") if last_ok else None
    last_attack_raw: str | None = None

    for r in range(1, _MAX_ROUNDS + 1):
        if last_ok is None:
            break                        # v0 失败：无版可辩
        out, err, dur = call(build_critic_prompt(info, last_ok_raw,
                                                 last_attack_raw))
        n_calls += 1
        atk = parse_attack(out or "")
        if err is not None:
            atk = {**atk, "status": "timeout" if "超时" in err else "error"}
        _record(r, "critic", atk, out or "", dur)
        if atk["status"] != "ok":
            budget_exhausted = 1
            break
        prev_ok = last_ok
        out, err, dur = call(build_revision_prompt(info, last_ok_raw,
                                                   out or ""))
        n_calls += 1
        parsed = _status_of(parse_prediction(out or ""), err)
        _record(r, "generator", parsed, out or "", dur)
        if parsed["status"] != "ok":
            budget_exhausted = 1
            break
        if max_abs_delta(prev_ok, parsed) < _EPS:
            early_stop = 1
        last_ok, last_ok_raw = parsed, out or ""
        last_attack_raw = None           # 新版对应新攻击：批评者重新出题
        if early_stop:
            break
    final = last_ok if last_ok is not None else v0
    return {"final": final, "rounds": rounds, "n_calls": n_calls,
            "budget_exhausted": budget_exhausted, "early_stop": early_stop}
```

（`last_attack_raw` 语义：轮 r 的批评者看 r-1 轮攻击原文；修订成功后清空——新版对应新攻击。）

- [ ] **Step 4: 跑测试通过**

Run: `uv run pytest tests/agentline/test_debate.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/fa/agentline/debate.py tests/agentline/test_debate.py
git commit -m "feat(agentline): run_match_debate 单场引擎——轮次/终止/预算预注册"
```

### Task 5: store——save_debate_round 与 budget_exhausted 落列

**Files:**
- Modify: `src/fa/agentline/store.py`
- Test: `tests/agentline/test_store.py`（追加）

**Interfaces:**
- Consumes: `_now()`（store.py:7）、现有 `save_prediction`
- Produces: `save_prediction(..., budget_exhausted: int = 0)`（新尾参，默认 0 向后兼容）；`save_debate_round(conn, match_id: int, round_no: int, role: str, payload_json: str, raw_output: str, status: str, duration_s: float, harness: str, model: str) -> None`（UNIQUE(match_id, round, role) upsert）

- [ ] **Step 1: 写失败测试（追加到 `tests/agentline/test_store.py`，沿用该文件现有的库 fixture/建库方式）**

```python
def test_save_prediction_budget_exhausted_flag(conn):
    parsed = {"status": "ok", "p_home": 0.5, "p_draw": 0.3, "p_away": 0.2,
              "p_over25": 0.5, "confidence": 0.6, "reasoning_digest": "d",
              "sources_json": "[]", "repaired": False}
    save_prediction(conn, 1, "A_debate", parsed, "raw", "h", "m", 1.0,
                    attributor=1, budget_exhausted=1)
    row = conn.execute("SELECT budget_exhausted FROM agentline_predictions"
                       " WHERE line='A_debate'").fetchone()
    assert row["budget_exhausted"] == 1


def test_save_debate_round_upsert(conn):
    save_debate_round(conn, 1, 0, "generator", '{"a":1}', "raw", "ok",
                      0.5, "h", "m")
    save_debate_round(conn, 1, 0, "generator", '{"a":2}', "raw2", "ok",
                      0.6, "h", "m")            # 重跑覆盖
    rows = conn.execute("SELECT payload_json, raw_output FROM"
                        " agentline_debate_rounds").fetchall()
    assert len(rows) == 1 and rows[0]["payload_json"] == '{"a": 2}'
    save_debate_round(conn, 1, 1, "critic", "[]", "raw3", "ok", 0.1, "h", "m")
    assert conn.execute("SELECT COUNT(*) c FROM"
                        " agentline_debate_rounds").fetchone()["c"] == 2
```

（`conn` fixture 若现有文件是别的名字/形态，照该文件既有 fixture 用——先读文件头再落笔；match_id=1 需先插 matches 行则照现有测试的造行方式补。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agentline/test_store.py -v`
Expected: FAIL——表/函数不存在

- [ ] **Step 3: 实现（`store.py`）**

`save_prediction` 签名与 SQL 改为（列清单/upsert SET 各加 budget_exhausted）：

```python
def save_prediction(conn: sqlite3.Connection, match_id: int, line: str,
                    parsed: dict, raw_output: str, harness: str,
                    model: str, duration_s: float, attributor: int = 1,
                    budget_exhausted: int = 0) -> int:
    conn.execute(
        "INSERT INTO agentline_predictions (match_id, line, attributor,"
        " budget_exhausted, p_home, p_draw, p_away, p_over25, confidence,"
        " reasoning_digest, sources_json, raw_output, status, repaired,"
        " harness, model, duration_s, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(match_id, line, attributor) DO UPDATE SET"
        " budget_exhausted=excluded.budget_exhausted,"
        " p_home=excluded.p_home, p_draw=excluded.p_draw,"
        " p_away=excluded.p_away, p_over25=excluded.p_over25,"
        " confidence=excluded.confidence,"
        " reasoning_digest=excluded.reasoning_digest,"
        " sources_json=excluded.sources_json, raw_output=excluded.raw_output,"
        " status=excluded.status, repaired=excluded.repaired,"
        " harness=excluded.harness, model=excluded.model,"
        " duration_s=excluded.duration_s, created_at=excluded.created_at",
        (match_id, line, attributor, budget_exhausted,
         parsed["p_home"], parsed["p_draw"], parsed["p_away"],
         parsed["p_over25"], parsed["confidence"],
         parsed["reasoning_digest"], parsed["sources_json"], raw_output,
         parsed["status"], int(parsed["repaired"]), harness, model,
         duration_s, _now()))
```

（SELECT id 回读段保持不变。）文件尾追加：

```python
def save_debate_round(conn, match_id: int, round_no: int, role: str,
                      payload_json: str, raw_output: str, status: str,
                      duration_s: float, harness: str, model: str) -> None:
    """A_debate 逐轮产物落库（2026-09-05 设计 §2.4）：UNIQUE 三元 upsert。"""
    conn.execute(
        "INSERT INTO agentline_debate_rounds (match_id, round, role,"
        " payload_json, raw_output, status, duration_s, harness, model,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(match_id, round, role) DO UPDATE SET"
        " payload_json=excluded.payload_json, raw_output=excluded.raw_output,"
        " status=excluded.status, duration_s=excluded.duration_s,"
        " harness=excluded.harness, model=excluded.model,"
        " created_at=excluded.created_at",
        (match_id, round_no, role, payload_json, raw_output, status,
         duration_s, harness, model, _now()))
    conn.commit()
```

- [ ] **Step 4: 跑 store 全量测试**

Run: `uv run pytest tests/agentline/test_store.py tests/agentline/test_db_v4.py -v`
Expected: 全 PASS（默认参 0 不破坏既有调用）

- [ ] **Step 5: Commit**

```bash
git add src/fa/agentline/store.py tests/agentline/test_store.py
git commit -m "feat(agentline): save_debate_round + save_prediction budget_exhausted"
```

### Task 6: orchestrate——run_debate 批编排

**Files:**
- Modify: `src/fa/agentline/orchestrate.py`
- Test: `tests/agentline/test_orchestrate.py`（追加）

**Interfaces:**
- Consumes: `run_match_debate`、`save_debate_round`、`save_prediction(..., budget_exhausted=)`、`save_run(..., started_at=)`
- Produces: `run_debate(conn: sqlite3.Connection, info_dir: Path, limit: int | None = None) -> dict`（返回 counts dict；summary 含 `n_calls / budget_exhausted / early_stop` 计数）

- [ ] **Step 1: 写失败测试（追加；沿用该文件既有 mock 方式——先读文件头确认 fixture 名，以下按「monkeypatch `fa.agentline.runner.run_headless`」写，orchestrate 经 `runner_mod.run_headless` 间接调用，patch 模块属性即生效）**

```python
def test_run_debate_happy_and_idempotent(tmp_path, monkeypatch):
    info = {"match": {"date": "2026-05-01", "home": "A", "away": "B"}}
    (tmp_path / "1.json").write_text(json.dumps(info), encoding="utf-8")
    conn = _mkdb(tmp_path)          # 该文件既有建库 helper；无则照 run_multi 测试的方式建
    seq = [OK0, ATK, "垃圾"]        # v0 ok → 批评 ok → 修订失败 → 终版=v0
    monkeypatch.setattr(
        "fa.agentline.runner.run_headless",
        lambda p, prof: (seq.pop(0), None, 0.01))
    counts = run_debate(conn, tmp_path, limit=None)
    assert counts == {"ok": 1, "parse_fail": 0, "timeout": 0, "error": 0}
    row = conn.execute("SELECT budget_exhausted, p_home FROM"
                       " agentline_predictions WHERE line='A_debate'"
                       " AND attributor=1").fetchone()
    assert row["budget_exhausted"] == 1 and abs(row["p_home"] - 0.5) < 1e-9
    assert conn.execute("SELECT COUNT(*) c FROM"
                        " agentline_debate_rounds").fetchone()["c"] == 3
    run2 = conn.execute("SELECT COUNT(*) c FROM agentline_runs"
                        " WHERE line='A_debate'").fetchone()["c"]
    # 幂等重跑：ok 行已存在 → 无新调用、无新轮行
    counts2 = run_debate(conn, tmp_path)
    assert counts2["ok"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM"
                        " agentline_debate_rounds").fetchone()["c"] == 3
    assert conn.execute("SELECT COUNT(*) c FROM agentline_runs"
                        " WHERE line='A_debate'").fetchone()["c"] == run2 + 1
```

（OK0/ATK 字面量从 `tests/agentline/test_debate.py` 复制过来；`_mkdb` 若不存在，用 `init_db(tmp_path/"t.db")` + 插一行 matches（match_id=1）替代——照本文件其他测试的造库方式统一。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agentline/test_orchestrate.py -k debate -v`
Expected: FAIL——`run_debate` 不存在

- [ ] **Step 3: 实现（`orchestrate.py` 追加，import 行补 `save_debate_round` 与 `from fa.agentline.debate import run_match_debate`）**

```python
def run_debate(conn: sqlite3.Connection, info_dir: Path,
               limit: int | None = None) -> dict:
    """A_debate（2026-09-05 设计 §2）：单场生成者-批评者-修订链 ≤2 轮。

    幂等：line='A_debate' 且 attributor=1 已 ok 的场次跳过（rounds 行不判重
    ——重跑覆盖，与 run_multi 成员行语义一致）。批中崩溃也留台账再抛
    （run_line 同型：防「predictions>0 且 runs=0」无痕中断）。
    """
    profile = _PROFILE_LINE["A_base"]
    done = {r["match_id"] for r in conn.execute(
        "SELECT match_id FROM agentline_predictions"
        " WHERE line='A_debate' AND attributor=1 AND status='ok'")}
    todo = sorted(int(p.stem) for p in info_dir.glob("*.json")
                  if p.stem.isdigit() and int(p.stem) not in done)
    if limit is not None:
        todo = todo[:limit]
    counts = {"ok": 0, "parse_fail": 0, "timeout": 0, "error": 0}
    agg = {"n_calls": 0, "budget_exhausted": 0, "early_stop": 0}
    from fa.agentline.store import _now
    t0 = _now()
    summary = {"n_todo": len(todo), "info_dir": str(info_dir)}
    try:
        for mid in todo:
            info = json.loads((info_dir / f"{mid}.json").read_text(
                encoding="utf-8"))
            res = run_match_debate(
                lambda p: runner_mod.run_headless(p, profile), info)
            for row in res["rounds"]:
                save_debate_round(conn, mid, row["round"], row["role"],
                                  row["payload"], row["raw"], row["status"],
                                  row["dur"], _HARNESS, MODEL)
            save_prediction(conn, mid, "A_debate", res["final"], "",
                            _HARNESS, MODEL,
                            sum(r["dur"] for r in res["rounds"]),
                            attributor=1,
                            budget_exhausted=res["budget_exhausted"])
            counts[res["final"]["status"]] += 1
            agg["n_calls"] += res["n_calls"]
            agg["budget_exhausted"] += res["budget_exhausted"]
            agg["early_stop"] += res["early_stop"]
    except Exception:
        save_run(conn, "A_debate", profile, MODEL, counts,
                 {**summary, **agg, "interrupted_match_id": mid},
                 started_at=t0)
        raise
    save_run(conn, "A_debate", profile, MODEL, counts, {**summary, **agg},
             started_at=t0)
    return counts
```

- [ ] **Step 4: 跑测试通过**

Run: `uv run pytest tests/agentline/test_orchestrate.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/fa/agentline/orchestrate.py tests/agentline/test_orchestrate.py
git commit -m "feat(agentline): run_debate 批编排——轮产物落库+幂等+中断台账"
```

### Task 7: CLI——`fa agentline run --line A_debate`

**Files:**
- Modify: `src/fa/cli.py:201-222`
- Test: `tests/test_cli.py`（追加）

**Interfaces:**
- Consumes: `run_debate`
- Produces: CLI 子命令面扩展（`--line` 合法值 + A_base/A_enh/A_multi/A_debate）

- [ ] **Step 1: 写失败测试（追加到 `tests/test_cli.py`，沿用文件头 `runner = CliRunner()` 与 `_use_tmp_db` helper）**

```python
def test_agentline_run_debate_dispatch(tmp_path, monkeypatch):
    db = _use_tmp_db(tmp_path, monkeypatch)
    called = {}
    import fa.agentline.orchestrate as orch
    monkeypatch.setattr(orch, "run_debate",
                        lambda conn, d, limit=None: {"ok": 1, "parse_fail": 0,
                                                     "timeout": 0, "error": 0})
    result = runner.invoke(app, ["agentline", "run", "--line", "A_debate"])
    assert result.exit_code == 0
    assert "ok" in result.output


def test_agentline_run_rejects_unknown_line(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    result = runner.invoke(app, ["agentline", "run", "--line", "A_nonsense"])
    assert result.exit_code == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_cli.py -k agentline_run -v`
Expected: FAIL——exit_code 2（校验拒绝 A_debate）

- [ ] **Step 3: 实现（`src/fa/cli.py:201-222` 改）**

```python
@agentline_app.command("run")
def agentline_run(line: str = typer.Option(
        ..., help="A_base、A_enh、A_multi 或 A_debate"),
        limit: int = typer.Option(None),
        members: int = typer.Option(3, help="A_multi 独立成员数（≥2）")) -> None:
    """跑一批线 A 预测（幂等续跑：只补无 ok 行的场次）"""
    from fa.agentline.orchestrate import run_debate, run_line, run_multi
    from fa.config import project_root
    if line not in ("A_base", "A_enh", "A_multi", "A_debate"):
        typer.echo(f"line 必须是 A_base/A_enh/A_multi/A_debate，收到 {line}")
        raise typer.Exit(2)
    if line == "A_multi" and members < 2:
        typer.echo(f"--members 必须 ≥2（少于两员不是 ensemble），收到 {members}")
        raise typer.Exit(2)
    conn = connect()
    if line == "A_multi":
        counts = run_multi(conn, project_root() / "data" / "agentline",
                           members=members, limit=limit)
    elif line == "A_debate":
        counts = run_debate(conn, project_root() / "data" / "agentline",
                            limit=limit)
    else:
        counts = run_line(conn, line, project_root() / "data" / "agentline",
                          limit)
    conn.close()
    typer.echo(f"line={line} 完成：{counts}")
```

（patch 目标注意：cli 在函数体内 `from fa.agentline.orchestrate import run_debate`——monkeypatch 打在 `fa.agentline.orchestrate.run_debate` 属性上，函数体 import 时取到的即 patched 值。）

- [ ] **Step 4: 跑测试通过**

Run: `uv run pytest tests/test_cli.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/fa/cli.py tests/test_cli.py
git commit -m "feat(cli): fa agentline run --line A_debate 接线"
```

### Task 8: compare——A_debate 列 + 修订增益段

**Files:**
- Modify: `src/fa/agentline/compare.py`
- Test: `tests/agentline/test_compare.py`（追加）

**Interfaces:**
- Consumes: `_fetch_agent`、`merge_rows`、`evaluate`、`metrics.log_loss`、`scipy.stats.spearmanr`
- Produces: `compare_lines` 的 cmp 增 `A_debate` 键与 `cmp["debate"]`；`debate_gain(conn, leagues=None, seasons=None) -> dict`——`{"n": int, "v0_ll": float, "final_ll": float, "rho": float|None, "n_rho": int}`（n=0 时其余键缺省）；`render_report` 表增 A_debate 行 + 修订增益脚注段

- [ ] **Step 1: 写失败测试（追加；造库方式照该文件既有 helper——插 bp 行 + agentline 行 + rounds 行）**

```python
def test_compare_lines_includes_debate(conn_with_rows):
    cmp = compare_lines(conn_with_rows)
    assert "A_debate" in cmp and cmp["A_debate"]["n"] >= 1
    assert "debate" in cmp and cmp["debate"]["n"] >= 1


def test_debate_gain_v0_vs_final_and_rho(conn_with_rows):
    g = debate_gain(conn_with_rows)
    assert g["n"] >= 1 and "v0_ll" in g and "final_ll" in g
    # 无批评 ok 行 → rho None；有 ≥3 对 → float
    assert g["rho"] is None or isinstance(g["rho"], float)


def test_debate_gain_empty(conn_empty):
    assert debate_gain(conn_empty) == {"n": 0}
```

（`conn_with_rows`：最小构造——1-3 场，每场 bp 行（含 mkt_*/outcome）、A_debate 终版 ok 行、rounds 的 round=0 generator ok 行 + 若干 critic ok 行；`conn_empty`：空表库。fixture 名按该文件现状落。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agentline/test_compare.py -k debate -v`
Expected: FAIL——键不存在

- [ ] **Step 3: 实现（`compare.py`）**

`compare_lines` 的线循环与 audit 段改：

```python
    for line, attr in (("A_base", 1), ("A_enh", 1), ("A_multi", 0),
                       ("A_debate", 1)):
```

函数 return 前加：

```python
    cmp["debate"] = debate_gain(conn, leagues, seasons)
```

新增模块级函数（`compare_lines` 之后）：

```python
def debate_gain(conn, leagues=None, seasons=None) -> dict:
    """修订增益（2026-09-05 设计 §2.5）：v0 vs 终版同场对比 + 分歧相关性。

    v0 取 rounds 表 round=0 的 generator ok 行 payload；终版取 predictions
    的 A_debate ok 行。rho = 每场最大攻击 severity 与修订幅度（三项 L1）的
    Spearman 相关（n<3 或无可比对 → None）——高攻击低修订=固执、低攻击高
    修订=无主见，两向都如实报。
    """
    from fa.backtest.metrics import fetch_predictions
    bp = fetch_predictions(conn, leagues, seasons)
    finals = _fetch_agent(conn, "A_debate", leagues, seasons, attributor=1)
    by_mid = {r["match_id"]: r for r in finals}
    if not by_mid:
        return {"n": 0}
    v0 = {r["match_id"]: json.loads(r["payload_json"]) for r in conn.execute(
        "SELECT match_id, payload_json FROM agentline_debate_rounds"
        " WHERE round=0 AND role='generator' AND status='ok'")}
    v0_rows = []
    for m, f in by_mid.items():
        if m in v0:
            r = dict(f)
            for k in ("p_home", "p_draw", "p_away", "p_over25"):
                r[k] = v0[m][k]
            v0_rows.append(r)
    n = len(v0_rows)
    if n == 0:
        return {"n": 0}
    e_final = evaluate(merge_rows(bp, list(by_mid.values())))
    e_v0 = evaluate(merge_rows(bp, v0_rows))
    sevs, revs = [], []
    for m, f in by_mid.items():
        if m not in v0:
            continue
        atks = [a for r in conn.execute(
            "SELECT payload_json FROM agentline_debate_rounds"
            " WHERE match_id=? AND role='critic' AND status='ok'", (m,))
                for a in json.loads(r["payload_json"]).get("attacks", [])]
        if not atks:
            continue
        sevs.append(max(a["severity"] for a in atks))
        revs.append(sum(abs(f[k] - v0[m][k])
                        for k in ("p_home", "p_draw", "p_away")))
    rho = None
    if len(sevs) >= 3:
        from scipy.stats import spearmanr
        rho = float(spearmanr(sevs, revs).statistic)
    return {"n": n, "v0_ll": e_v0["model_ll"], "final_ll": e_final["model_ll"],
            "rho": rho, "n_rho": len(sevs)}
```

`render_report`：主表循环元组改 `("P", "A_base", "A_enh", "A_multi", "A_debate")`；`_FOOTNOTE` 前追加：

```python
    d = cmp.get("debate", {})
    if d.get("n"):
        rho_s = f"{d['rho']:+.2f}" if d.get("rho") is not None else "—"
        lines += ["", f"> A_debate 修订增益：v0 ll={d['v0_ll']:.4f} → "
                      f"终版 ll={d['final_ll']:.4f}（n={d['n']}）；"
                      f"攻击-修订相关性 ρ={rho_s}（n={d.get('n_rho', 0)}，"
                      f"高攻击低修订=固执 / 低攻击高修订=无主见，均为实测信号）"]
```

- [ ] **Step 4: 跑测试通过**

Run: `uv run pytest tests/agentline/test_compare.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/fa/agentline/compare.py tests/agentline/test_compare.py
git commit -m "feat(agentline): compare 增 A_debate 列 + debate_gain 修订增益段"
```

### Task 9: E2E——主库小批 10 场真跑

**Files:**
- 无代码改动；产物为库内数据 + `docs/agentline/compare-YYYYMMDD.md` 滚动报告

**Interfaces:**
- Consumes: Task 1-8 全部
- Produces: 活库 A_debate 首批真实数据（管线可用性证据口径）

- [ ] **Step 1: 前置检查**

Run: `uv run python -c "from fa.db import SCHEMA_VERSION; print(SCHEMA_VERSION)" && dsh --version`
Expected: `9` 且 dsh 可执行。dsh 不可用 → **如实报告并停在本步**，不 mock 凑数（诚实条款）。

- [ ] **Step 2: 跑 10 场**

Run: `uv run fa agentline run --line A_debate --limit 10`
Expected: 输出 counts；timeout/error 逐场降级不中断

- [ ] **Step 3: 落库核验**

```bash
uv run python - <<'EOF'
from fa.db import connect
c = connect()
print("preds:", [dict(r) for r in c.execute(
    "SELECT status, COUNT(*) n FROM agentline_predictions"
    " WHERE line='A_debate' GROUP BY status")])
print("rounds:", c.execute("SELECT COUNT(*) FROM agentline_debate_rounds"
    ).fetchone()[0])
print("runs:", [dict(r) for r in c.execute(
    "SELECT n_ok, n_parse_fail, n_timeout, n_error, summary FROM"
    " agentline_runs WHERE line='A_debate' ORDER BY id DESC LIMIT 1")])
EOF
```

Expected: 10 场各状态计数 + rounds 行数 ≥ 场数 + run 行 1 条（summary 含 n_calls/budget_exhausted/early_stop）

- [ ] **Step 4: 出滚动报告并核对修订增益段**

Run: `uv run fa agentline compare`
Expected: `docs/agentline/compare-YYYYMMDD.md` 含 A_debate 行与修订增益脚注；n<100 披露口径在脚注

- [ ] **Step 5: 提交报告 + 如实记录**

```bash
git add docs/agentline/
git commit -m "docs(agentline): A_debate 首批 E2E 10 场——管线可用性证据（n=10 无统计意义）"
```

报告或 commit message 里如实写：计数、budget_exhausted/early_stop 发生数、parse_fail 数——**不重跑挑结果；数字不构成方向性结论**（数据齐口径 A_multi n≥300 且 ≥2 窗口之前，spec §4.2）。
