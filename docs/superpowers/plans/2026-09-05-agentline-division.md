# 线 A 形态二 A_division（角色分工制）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 A_division：单场「历史考古官 → 预测者 → 质询官」三跳串行链，逐跳产物落库、质询只落 flag 不改数、compare 增列并出质询标签分层检验段。

**Architecture:** schema v10 纯加法（新表 `agentline_division_jumps`；line 词表五词已在 v9 备齐，无需再重建）；新纯函数模块 `src/fa/agentline/division.py`（三跳 prompt + 单场引擎 + `derive_flags` 单一事实源）；`contract.py` 加 `parse_history_points`（质询官复用 debate 计划已落地的 `parse_attack`）；`orchestrate.py` 加 `run_division`；`compare.py` 增 A_division 列 + `division_stratified`（MWU 双侧，retro 关卡 3 口径）。

**Tech Stack:** Python 3.11+ / uv / typer / pytest / sqlite3（WAL）/ scipy（mannwhitneyu，已有依赖）/ dsh headless。

**Spec:** docs/superpowers/specs/2026-09-05-agentline-debate-division-design.md §3（形态二）、§4（放量与成本）、§5（schema）；宿主 docs/superpowers/specs/2026-09-04-agentline-multi-brain-design.md。

## Global Constraints

- **开工前提（撞号协议 + 串行裁定 D-b）**：主干 `src/fa/db.py` 的 `SCHEMA_VERSION == 9`（A_debate 计划已合入）。若读到 8 或 7，停止并上报——本计划取 v10，不抢号
- 预注册降级规则（spec §3.2，实施不得静默调）：跳1 失败→预测者退化为原始信息集输入；跳2 失败→终版=该失败态（无数字）、**跳3 不发起**；跳3 失败→跳2 预测原样入终版。每场恒 3 次调用（跳2 失败时 2 次）
- 跳1 契约：relevance 封闭三词 `high / medium / low`；两数组均可为空；point ≤100 字；**出现 p_home/p_draw/p_away/p_over25 字段即 parse_fail**（考古官禁数字；禁场外知识——prompt 明示只用信息集内置历史段，v1 不接 retro S3）
- 质询官复用 `parse_attack`（五标签封闭枚举，与 A_debate 批评者同一契约）；**判决应用 v1 只落 flag 不改数**（M4 persona 模式；flags 由 `derive_flags` 从跳行推导，评测/run 摘要共用单一事实源）
- 分层检验分层键 = **五标签任一命中**（jumpN_fail 是管线降级信号，不进分层键，避免污染）；MWU 双侧；任一层 n<5 只报 n 不报 p（小样本诚实，retro 关卡 3 口径）
- 成员用 `fa-agent-base` profile；预测者输出复用 `parse_prediction` 全契约
- A_division 产出永不进 B 线推荐/落注流；真实下注禁止
- 测试全离线（mock call 注入）；E2E 主库直跑；诚实条款同 debate 计划（计数如实、不重跑挑结果、数据齐口径前不作方向性结论）

---

### Task 1: schema v10——新表 agentline_division_jumps（纯加法）

**Files:**
- Modify: `src/fa/db.py`（`SCHEMA_VERSION`、新常量 `_AL_DIV_TABLE`、`_SCHEMA` 组合、`_migrate_up`）
- Test: `tests/agentline/test_db_v10.py`（新建）

**Interfaces:**
- Consumes: v9 已落地的 `_AL_TABLE` 五词表（含 'A_division'）
- Produces: 新表 `agentline_division_jumps`（spec §3.4 DDL 原文）；`SCHEMA_VERSION = 10`

- [ ] **Step 1: 写失败测试（新建 `tests/agentline/test_db_v10.py`）**

```python
"""schema v10（2026-09-05 设计 §3.4）：新表 division_jumps，纯加法无重建。"""
import sqlite3

from fa.db import SCHEMA_VERSION, connect, init_db


def test_fresh_db_has_division_jumps(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    conn = connect(db)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[
        "version"] == SCHEMA_VERSION == 10
    assert "agentline_division_jumps" in {
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.execute("INSERT INTO agentline_division_jumps (match_id, jump,"
                 " role, payload_json, raw_output, status, created_at)"
                 " VALUES (1,1,'archivist','{}','r','ok','2026-09-05')")
    # role CHECK 三词
    try:
        conn.execute("INSERT INTO agentline_division_jumps (match_id, jump,"
                     " role, payload_json, raw_output, status, created_at)"
                     " VALUES (1,2,'haha','{}','r','ok','2026-09-05')")
        raise AssertionError("role CHECK 未生效")
    except sqlite3.IntegrityError:
        pass
    # UNIQUE(match_id, jump)
    try:
        conn.execute("INSERT INTO agentline_division_jumps (match_id, jump,"
                     " role, payload_json, raw_output, status, created_at)"
                     " VALUES (1,1,'archivist','{}','r2','ok','2026-09-05')")
        raise AssertionError("UNIQUE(match_id, jump) 未生效")
    except sqlite3.IntegrityError:
        pass
    conn.close()


def test_v9_migrates_to_v10(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    conn = connect(db)
    conn.execute("UPDATE schema_version SET version=9")
    conn.commit()
    conn.close()
    init_db(db)                       # v9 → v10：IF NOT EXISTS 补表
    conn = connect(db)
    assert "agentline_division_jumps" in {
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()


def test_division_line_value_accepted(tmp_path):
    """词表五词在 v9 已备齐：A_division INSERT 直接过（无需重建验证）。"""
    db = tmp_path / "t.db"
    init_db(db)
    conn = connect(db)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("INSERT INTO agentline_predictions (match_id, line,"
                 " raw_output, status, created_at)"
                 " VALUES (1,'A_division','x','ok','2026-09-05')")
    conn.commit()
    conn.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agentline/test_db_v10.py -v`
Expected: FAIL——表不存在 / SCHEMA_VERSION != 10

- [ ] **Step 3: 实现（`src/fa/db.py`）**

`_AL_DEBATE_TABLE` 之后新增常量（spec §3.4 DDL 原文）：

```python
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
```

`_SCHEMA` 组合行追加 `+ _AL_DIV_TABLE`；`SCHEMA_VERSION = 10`（db.py:6）；`_migrate_up` 在 v9 块之后加：

```python
    if from_v < 10:
        # v10：新表 division_jumps（2026-09-05 设计 §3.4）。纯加法——词表
        # 五词已在 v9 备齐，无需重建；IF NOT EXISTS 幂等。
        conn.executescript(_AL_DIV_TABLE)
```

docstring 版本史注释追加一行。`tests/test_db.py` 的 `test_migrate_and_fresh_schemas_match` 参数表追加 9。

- [ ] **Step 4: 跑测试通过**

Run: `uv run pytest tests/agentline/test_db_v10.py tests/test_db.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/fa/db.py tests/agentline/test_db_v10.py tests/test_db.py
git commit -m "feat(db): schema v10——agentline_division_jumps 三跳产物表"
```

### Task 2: contract.parse_history_points——考古官要点契约

**Files:**
- Modify: `src/fa/agentline/contract.py`（文件尾追加）
- Test: `tests/agentline/test_contract.py`（追加）

**Interfaces:**
- Consumes: 现有 `_extract_json`、`_PROB_FIELDS`（debate 计划 Task 2 已加）
- Produces: `RELEVANCE_LEVELS: tuple[str, ...]`；`parse_history_points(raw: str, repaired_ok: bool = False) -> dict`——ok 态 `{"status": "ok", "h2h_points": [{"point"(≤100字), "relevance"}], "recent_form_points": [...], "repaired": bool}`；失败态 `{"status": "parse_fail", "h2h_points": [], "recent_form_points": [], "error": "<中文原因>", "repaired": bool}`

- [ ] **Step 1: 写失败测试（追加）**

```python
from fa.agentline.contract import (RELEVANCE_LEVELS,
                                   parse_history_points)


def test_parse_history_points_ok():
    raw = json.dumps({
        "h2h_points": [{"point": "近6次交锋主队4胜", "relevance": "high"}],
        "recent_form_points": [{"point": "客队三连败", "relevance": "medium"},
                                {"point": "主队两连平", "relevance": "low"}]})
    got = parse_history_points(raw)
    assert got["status"] == "ok"
    assert got["h2h_points"][0]["relevance"] == "high"
    assert len(got["recent_form_points"]) == 2


def test_parse_history_points_empty_arrays_legal():
    got = parse_history_points('{"h2h_points": [], "recent_form_points": []}')
    assert got["status"] == "ok"


def test_parse_history_points_missing_key_fails():
    got = parse_history_points('{"h2h_points": []}')   # 严格：两键都必须在
    assert got["status"] == "parse_fail"


def test_parse_history_points_bad_relevance_fails():
    got = parse_history_points('{"h2h_points": [{"point": "p",'
                               ' "relevance": "huge"}],'
                               ' "recent_form_points": []}')
    assert got["status"] == "parse_fail"


def test_parse_history_points_probability_guard():
    got = parse_history_points('{"h2h_points": [],'
                               ' "recent_form_points": [], "p_home": 0.5}')
    assert got["status"] == "parse_fail" and "概率" in got["error"]


def test_parse_history_points_truncated_and_repaired():
    got = parse_history_points(
        'x```json\n{"h2h_points": [{"point": "%s", "relevance": "high"}],'
        ' "recent_form_points": []}\n```' % ("字" * 150))
    assert got["status"] == "ok" and got["repaired"] is True
    assert len(got["h2h_points"][0]["point"]) == 100
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agentline/test_contract.py -k history -v`
Expected: FAIL——ImportError

- [ ] **Step 3: 实现（`contract.py` 文件尾追加；与 parse_attack 同风格）**

```python
RELEVANCE_LEVELS = ("high", "medium", "low")


def parse_history_points(raw: str, repaired_ok: bool = False) -> dict:
    """历史考古官要点契约（2026-09-05 设计 §3.3）。

    严格双键（缺一即 parse_fail，与 parse_prediction 的「绝不脑补」同
    风格）；数组可为空（历史段无信息=合法）；relevance 封闭三词；禁概率
    数字（出现 p_* 字段即弃）。
    """
    repaired = repaired_ok
    try:
        try:
            obj = json.loads(raw.strip())
        except json.JSONDecodeError:
            obj = _extract_json(raw)
            repaired = True
        if any(f in obj for f in _PROB_FIELDS):
            raise ValueError("要点 JSON 出现概率字段（考古官禁数字）")
        out = {}
        for key in ("h2h_points", "recent_form_points"):
            items = obj[key]
            if not isinstance(items, list):
                raise ValueError(f"{key} 必须是数组")
            pts = []
            for it in items:
                if it["relevance"] not in RELEVANCE_LEVELS:
                    raise ValueError(f"relevance 越界：{it['relevance']}")
                pts.append({"point": str(it["point"])[:100],
                            "relevance": it["relevance"]})
            out[key] = pts
        return {"status": "ok", **out, "repaired": repaired}
    except (KeyError, ValueError, TypeError, json.JSONDecodeError,
            AttributeError) as exc:
        return {"status": "parse_fail", "h2h_points": [],
                "recent_form_points": [],
                "error": f"parse_fail 原因：{type(exc).__name__}: {exc}",
                "repaired": repaired}
```

- [ ] **Step 4: 跑测试通过**

Run: `uv run pytest tests/agentline/test_contract.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/fa/agentline/contract.py tests/agentline/test_contract.py
git commit -m "feat(agentline): parse_history_points 考古官契约——三词relevance+禁数字"
```

### Task 3: runner.build_prompt 加 extra_section 参数（单一事实源）

**Files:**
- Modify: `src/fa/agentline/runner.py:33-48`
- Test: `tests/agentline/test_runner.py`（追加）

**Interfaces:**
- Consumes: 现有 `build_prompt`
- Produces: `build_prompt(info_set: dict, line: str, extra_section: str = "") -> str`（默认空串，既有调用零改动；extra 插在 head 之后、enh 后缀之前）

- [ ] **Step 1: 写失败测试（追加）**

```python
def test_build_prompt_extra_section():
    info = {"match": {"date": "2026-05-01", "home": "A", "away": "B"}}
    p = build_prompt(info, "A_base", extra_section="\n# 历史考古官要点\nX\n")
    assert "# 历史考古官要点" in p and "X" in p
    p2 = build_prompt(info, "A_base")
    assert "# 历史考古官要点" not in p2      # 默认零改动
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agentline/test_runner.py -k extra -v`
Expected: FAIL——unexpected keyword argument

- [ ] **Step 3: 实现（runner.py:33 签名与 47-48 返回行改）**

```python
def build_prompt(info_set: dict, line: str,
                 extra_section: str = "") -> str:
```

返回行改：

```python
    date = info_set["match"]["date"]
    return (head + extra_section
            + (_ENH_SUFFIX.format(date=date) if line == "A_enh" else ""))
```

（函数体其余不动。）

- [ ] **Step 4: 跑测试通过**

Run: `uv run pytest tests/agentline/test_runner.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/fa/agentline/runner.py tests/agentline/test_runner.py
git commit -m "refactor(agentline): build_prompt 加 extra_section——division 预测者复用同源 head"
```

### Task 4: division.py——三跳 prompt、引擎与 derive_flags

**Files:**
- Create: `src/fa/agentline/division.py`
- Test: `tests/agentline/test_division.py`（新建）

**Interfaces:**
- Consumes: `parse_prediction`、`parse_attack`、`parse_history_points`、`build_prompt(..., extra_section=)`
- Produces:
  - `build_archivist_prompt(info_set: dict) -> str`；`build_predictor_prompt(info_set: dict, points: dict | None) -> str`（points=None 时无要点段=退化输入）；`build_challenger_prompt(info_set: dict, pred_raw: str) -> str`
  - `run_match_division(call, info: dict) -> dict`——`{"final": <契约 dict>, "jumps": [{"jump": 1|2|3, "role": str, "payload": str, "raw": str, "status": str, "dur": float}], "n_calls": int}`
  - `derive_flags(jump_rows: list[dict]) -> dict`——入参 `[{"jump", "status", "payload"}...]`（payload=攻击/要点 JSON 字符串），返回 `{"jump1_fail"?: 1, "jump2_fail"?: 1, "jump3_fail"?: 1, <五标签>?: 计数}`；跳3 未发起（行缺）**不**记 jump3_fail（跳2_fail 已解释）

- [ ] **Step 1: 写失败测试（新建 `tests/agentline/test_division.py`）**

```python
import json

from fa.agentline.division import (build_archivist_prompt,
                                   build_challenger_prompt,
                                   build_predictor_prompt, derive_flags,
                                   run_match_division)

INFO = {"match": {"date": "2026-05-01", "home": "A", "away": "B"},
        "history": {"h2h": [], "form": []}}
HIST = json.dumps({"h2h_points": [{"point": "p1", "relevance": "high"}],
                   "recent_form_points": []})
PRED = json.dumps({"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2,
                   "p_over25": 0.5, "confidence": 0.6,
                   "reasoning_digest": "d", "sources": []})
ATK = json.dumps({"attacks": [{"label": "overconfidence", "reason": "r",
                               "severity": 0.9}]})


def _call_seq(seq):
    prompts = []

    def call(prompt):
        prompts.append(prompt)
        return seq[len(prompts) - 1], None, 0.01
    return call, prompts


def test_prompts_carry_sections():
    assert "历史数据考古官" in build_archivist_prompt(INFO)
    assert "# 历史考古官要点" in build_predictor_prompt(INFO, json.loads(HIST))
    assert "# 历史考古官要点" not in build_predictor_prompt(INFO, None)
    cp = build_challenger_prompt(INFO, PRED)
    assert "质询" in cp and PRED in cp


def test_happy_path_three_jumps():
    call, prompts = _call_seq([HIST, PRED, ATK])
    res = run_match_division(call, INFO)
    assert res["n_calls"] == 3
    assert [j["role"] for j in res["jumps"]] == [
        "archivist", "predictor", "challenger"]
    assert res["final"]["status"] == "ok"
    assert abs(res["final"]["p_home"] - 0.5) < 1e-9


def test_jump1_fail_predictor_gets_raw_info():
    call, prompts = _call_seq(["垃圾", PRED, ATK])
    res = run_match_division(call, INFO)
    assert res["jumps"][0]["status"] == "parse_fail"
    assert "# 历史考古官要点" not in prompts[1]   # 退化为原始信息集
    assert res["final"]["status"] == "ok"


def test_jump2_fail_no_jump3_two_calls():
    call, prompts = _call_seq([HIST, "垃圾"])
    res = run_match_division(call, INFO)
    assert res["final"]["status"] == "parse_fail"
    assert res["n_calls"] == 2 and len(res["jumps"]) == 2


def test_jump3_fail_final_unchanged():
    call, prompts = _call_seq([HIST, PRED, "垃圾"])
    res = run_match_division(call, INFO)
    assert res["final"]["status"] == "ok"
    assert abs(res["final"]["p_home"] - 0.5) < 1e-9
    assert res["jumps"][2]["status"] == "parse_fail"


def test_predictor_prompt_includes_archivist_points():
    call, prompts = _call_seq([HIST, PRED, ATK])
    run_match_division(call, INFO)
    assert "p1" in prompts[1]                     # 要点内容进了预测者输入


def test_derive_flags_labels_and_jump_fails():
    rows = [
        {"jump": 1, "status": "ok", "payload": HIST},
        {"jump": 2, "status": "ok", "payload": PRED},
        {"jump": 3, "status": "ok", "payload": ATK},
    ]
    assert derive_flags(rows) == {"overconfidence": 1}
    rows2 = [
        {"jump": 1, "status": "parse_fail", "payload": ""},
        {"jump": 2, "status": "ok", "payload": PRED},
        {"jump": 3, "status": "error", "payload": ""},
    ]
    assert derive_flags(rows2) == {"jump1_fail": 1, "jump3_fail": 1}
    rows3 = [  # 跳2 失败→跳3 未发起：不记 jump3_fail
        {"jump": 1, "status": "ok", "payload": HIST},
        {"jump": 2, "status": "parse_fail", "payload": ""},
    ]
    assert derive_flags(rows3) == {"jump2_fail": 1}


def test_derive_flags_empty_attacks_no_label():
    rows = [
        {"jump": 1, "status": "ok", "payload": HIST},
        {"jump": 2, "status": "ok", "payload": PRED},
        {"jump": 3, "status": "ok",
         "payload": json.dumps({"attacks": []})},
    ]
    assert derive_flags(rows) == {}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agentline/test_division.py -v`
Expected: FAIL——模块不存在

- [ ] **Step 3: 实现 `src/fa/agentline/division.py`**

```python
"""A_division 跳链引擎（2026-09-05 设计 §3）：历史考古官→预测者→质询官。

预注册降级规则（设计 §3.2）：跳1 失败→预测者退化为原始信息集；跳2 失败
→终版=该失败态、跳3 不发起；跳3 失败→跳2 预测原样入终版。每场恒 3 次
调用（跳2 失败时 2 次）。质询官与 A_debate 批评者共用 parse_attack 契约；
判决应用 v1 只落 flag 不改数（flags 单一事实源 = derive_flags）。
"""
import json

from fa.agentline.contract import parse_attack, parse_history_points, \
    parse_prediction
from fa.agentline.runner import build_prompt

RELEVANCE_ENUM = "high|medium|low"
ATTACK_ENUM_HELP = ("overconfidence|missing_context|alt_explanation"
                    "|internal_inconsistency|evidence_weak")


def build_archivist_prompt(info_set: dict) -> str:
    return (
        "你是足球历史数据考古官。基于以下赛前信息集内置的历史段（H2H "
        "交锋与双方近况），提炼与本场预测最相关的历史要点。\n\n"
        "# 赛前信息集\n" + json.dumps(info_set, ensure_ascii=False) + "\n\n"
        "# 要求\n"
        "- 只产定性要点，禁止输出任何概率数字；只用信息集内的信息，"
        "禁止使用信息集之外的知识\n"
        "- 最终必须输出且仅输出一个 JSON 对象（不要 markdown 围栏）：\n"
        '  {"h2h_points": [{"point": "<=100字", "relevance": '
        '"<high|medium|low>"}], "recent_form_points": [同构]}\n'
        f"- relevance 封闭枚举：{RELEVANCE_ENUM}；两数组均可为空"
        "（历史段无信息=合法）\n")


def build_predictor_prompt(info_set: dict, points: dict | None) -> str:
    extra = ""
    if points is not None:
        extra = ("\n# 历史考古官要点（分工上游供给，自主取舍）\n"
                 + json.dumps(points, ensure_ascii=False) + "\n")
    return build_prompt(info_set, "A_base", extra_section=extra)


def build_challenger_prompt(info_set: dict, pred_raw: str) -> str:
    return (
        "你是量化预测的独立质询官。以下是赛前信息集与一个待审预测。\n\n"
        "# 赛前信息集\n" + json.dumps(info_set, ensure_ascii=False) + "\n\n"
        "# 待审预测\n" + pred_raw + "\n\n"
        "# 要求\n"
        "- 只产定性判断：质询其过度自信/历史忽视/替代解释/内部矛盾/证据"
        "薄弱，禁止输出任何概率数字，禁止修改预测\n"
        "- 最终必须输出且仅输出一个 JSON 对象（不要 markdown 围栏）：\n"
        '  {"attacks": [{"label": "<枚举词>", "reason": "<=200字", '
        '"severity": <0-1>}]}\n'
        f"- label 封闭枚举：{ATTACK_ENUM_HELP}；"
        '无质询点时输出 {"attacks": []}\n')


def _status_of(parsed: dict, run_err: str | None) -> dict:
    """dsh 层失败 → timeout/error（与 debate._status_of 同式）。"""
    if run_err is not None:
        return {**parsed, "status": "timeout" if "超时" in run_err else "error",
                **({"reasoning_digest": f"dsh 失败：{run_err}"}
                   if "reasoning_digest" in parsed else {})}
    return parsed


def run_match_division(call, info: dict) -> dict:
    """单场三跳（纯编排；call 注入，与 runner.run_headless 同签名）。"""
    jumps: list[dict] = []
    n_calls = 0

    def _record(jump_no: int, role: str, parsed: dict, raw: str,
                dur: float) -> None:
        jumps.append({"jump": jump_no, "role": role,
                      "payload": json.dumps(parsed, ensure_ascii=False),
                      "raw": raw, "status": parsed["status"], "dur": dur})

    out, err, dur = call(build_archivist_prompt(info))
    n_calls += 1
    hist = parse_history_points(out or "")
    if err is not None:
        hist = {**hist, "status": "timeout" if "超时" in err else "error"}
    _record(1, "archivist", hist, out or "", dur)
    points = hist if hist["status"] == "ok" else None

    out, err, dur = call(build_predictor_prompt(info, points))
    n_calls += 1
    pred = _status_of(parse_prediction(out or ""), err)
    _record(2, "predictor", pred, out or "", dur)
    if pred["status"] != "ok":
        return {"final": pred, "jumps": jumps, "n_calls": n_calls}

    out3, err3, dur3 = call(build_challenger_prompt(info, out or ""))
    n_calls += 1
    atk = parse_attack(out3 or "")
    if err3 is not None:
        atk = {**atk, "status": "timeout" if "超时" in err3 else "error"}
    _record(3, "challenger", atk, out3 or "", dur3)
    return {"final": pred, "jumps": jumps, "n_calls": n_calls}


def derive_flags(jump_rows: list[dict]) -> dict:
    """从跳行推导判决 flag（设计 §3.4；评测与 run 摘要共用的单一事实源）。

    jump_rows: [{"jump", "status", "payload"}...]（payload=该跳规范化 JSON）。
    跳3 未发起（行缺）不记 jump3_fail——跳2_fail 已解释缺席原因。
    """
    by_jump = {r["jump"]: r for r in jump_rows}
    flags: dict = {}
    for j in (1, 2):
        row = by_jump.get(j)
        if row is None or row["status"] != "ok":
            flags[f"jump{j}_fail"] = 1
    row3 = by_jump.get(3)
    if row3 is not None and row3["status"] != "ok":
        flags["jump3_fail"] = 1
    if row3 is not None and row3["status"] == "ok":
        for a in json.loads(row3["payload"]).get("attacks", []):
            flags[a["label"]] = flags.get(a["label"], 0) + 1
    return flags
```

- [ ] **Step 4: 跑测试通过**

Run: `uv run pytest tests/agentline/test_division.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/fa/agentline/division.py tests/agentline/test_division.py
git commit -m "feat(agentline): division 三跳引擎+derive_flags 单一事实源"
```

### Task 5: store——save_division_jump

**Files:**
- Modify: `src/fa/agentline/store.py`（文件尾追加）
- Test: `tests/agentline/test_store.py`（追加）

**Interfaces:**
- Consumes: `_now()`
- Produces: `save_division_jump(conn, match_id: int, jump_no: int, role: str, payload_json: str, raw_output: str, status: str, duration_s: float, harness: str, model: str) -> None`（UNIQUE(match_id, jump) upsert，与 save_debate_round 同型）

- [ ] **Step 1: 写失败测试（追加，fixture 照该文件现状）**

```python
def test_save_division_jump_upsert(conn):
    save_division_jump(conn, 1, 1, "archivist", '{"a":1}', "raw", "ok",
                       0.5, "h", "m")
    save_division_jump(conn, 1, 1, "archivist", '{"a":2}', "raw2", "ok",
                       0.6, "h", "m")            # 重跑覆盖
    rows = conn.execute("SELECT payload_json FROM"
                        " agentline_division_jumps").fetchall()
    assert len(rows) == 1 and rows[0]["payload_json"] == '{"a": 2}'
    save_division_jump(conn, 1, 2, "predictor", "{}", "raw3", "ok",
                       0.1, "h", "m")
    assert conn.execute("SELECT COUNT(*) c FROM"
                        " agentline_division_jumps").fetchone()["c"] == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agentline/test_store.py -k division -v`
Expected: FAIL——表/函数不存在

- [ ] **Step 3: 实现（`store.py` 文件尾追加）**

```python
def save_division_jump(conn, match_id: int, jump_no: int, role: str,
                       payload_json: str, raw_output: str, status: str,
                       duration_s: float, harness: str, model: str) -> None:
    """A_division 逐跳产物落库（2026-09-05 设计 §3.4）：UNIQUE(match_id,jump)。"""
    conn.execute(
        "INSERT INTO agentline_division_jumps (match_id, jump, role,"
        " payload_json, raw_output, status, duration_s, harness, model,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(match_id, jump) DO UPDATE SET"
        " payload_json=excluded.payload_json, raw_output=excluded.raw_output,"
        " status=excluded.status, duration_s=excluded.duration_s,"
        " harness=excluded.harness, model=excluded.model,"
        " created_at=excluded.created_at",
        (match_id, jump_no, role, payload_json, raw_output, status,
         duration_s, harness, model, _now()))
    conn.commit()
```

- [ ] **Step 4: 跑测试通过**

Run: `uv run pytest tests/agentline/test_store.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/fa/agentline/store.py tests/agentline/test_store.py
git commit -m "feat(agentline): save_division_jump 逐跳落库"
```

### Task 6: orchestrate——run_division 批编排

**Files:**
- Modify: `src/fa/agentline/orchestrate.py`
- Test: `tests/agentline/test_orchestrate.py`（追加）

**Interfaces:**
- Consumes: `run_match_division`、`derive_flags`、`save_division_jump`、`save_prediction`、`save_run`
- Produces: `run_division(conn: sqlite3.Connection, info_dir: Path, limit: int | None = None) -> dict`；summary 增 `n_calls` 与 `n_flagged`（有任一 flag 的场次数）

- [ ] **Step 1: 写失败测试（追加；mock 方式照 debate 计划 Task 6——monkeypatch `fa.agentline.runner.run_headless`）**

```python
def test_run_division_happy_and_flagged_summary(tmp_path, monkeypatch):
    info = {"match": {"date": "2026-05-01", "home": "A", "away": "B"}}
    (tmp_path / "1.json").write_text(json.dumps(info), encoding="utf-8")
    conn = _mkdb(tmp_path)      # 该文件既有建库 helper（debate 计划已补）
    seq = [HIST, PRED, ATK]
    monkeypatch.setattr(
        "fa.agentline.runner.run_headless",
        lambda p, prof: (seq.pop(0), None, 0.01))
    counts = run_division(conn, tmp_path)
    assert counts == {"ok": 1, "parse_fail": 0, "timeout": 0, "error": 0}
    row = conn.execute("SELECT p_home FROM agentline_predictions"
                       " WHERE line='A_division' AND attributor=1").fetchone()
    assert abs(row["p_home"] - 0.5) < 1e-9
    assert conn.execute("SELECT COUNT(*) c FROM"
                        " agentline_division_jumps").fetchone()["c"] == 3
    import json as _json
    summary = _json.loads(conn.execute(
        "SELECT summary FROM agentline_runs WHERE line='A_division'"
        " ORDER BY id DESC LIMIT 1").fetchone()["summary"])
    assert summary["n_calls"] == 3 and summary["n_flagged"] == 1
    # 幂等：ok 已在 → 重跑零调用
    counts2 = run_division(conn, tmp_path)
    assert counts2["ok"] == 0
```

（HIST/PRED/ATK 字面量从 `tests/agentline/test_division.py` 复制；`_mkdb` 沿用 debate 计划 Task 6 的 helper。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agentline/test_orchestrate.py -k division -v`
Expected: FAIL——`run_division` 不存在

- [ ] **Step 3: 实现（`orchestrate.py` 追加，import 行补 `save_division_jump` 与 `from fa.agentline.division import derive_flags, run_match_division`）**

```python
def run_division(conn: sqlite3.Connection, info_dir: Path,
                 limit: int | None = None) -> dict:
    """A_division（2026-09-05 设计 §3）：三跳串行，质询只落 flag 不改数。

    幂等：line='A_division' 且 attributor=1 已 ok 的场次跳过（jumps 行不
    判重——重跑覆盖）。批中崩溃留台账再抛（run_line/run_debate 同型）。
    """
    profile = _PROFILE_LINE["A_base"]
    done = {r["match_id"] for r in conn.execute(
        "SELECT match_id FROM agentline_predictions"
        " WHERE line='A_division' AND attributor=1 AND status='ok'")}
    todo = sorted(int(p.stem) for p in info_dir.glob("*.json")
                  if p.stem.isdigit() and int(p.stem) not in done)
    if limit is not None:
        todo = todo[:limit]
    counts = {"ok": 0, "parse_fail": 0, "timeout": 0, "error": 0}
    agg = {"n_calls": 0, "n_flagged": 0}
    from fa.agentline.store import _now
    t0 = _now()
    summary = {"n_todo": len(todo), "info_dir": str(info_dir)}
    try:
        for mid in todo:
            info = json.loads((info_dir / f"{mid}.json").read_text(
                encoding="utf-8"))
            res = run_match_division(
                lambda p: runner_mod.run_headless(p, profile), info)
            for row in res["jumps"]:
                save_division_jump(conn, mid, row["jump"], row["role"],
                                   row["payload"], row["raw"], row["status"],
                                   row["dur"], _HARNESS, MODEL)
            save_prediction(conn, mid, "A_division", res["final"], "",
                            _HARNESS, MODEL,
                            sum(r["dur"] for r in res["jumps"]),
                            attributor=1)
            counts[res["final"]["status"]] += 1
            agg["n_calls"] += res["n_calls"]
            agg["n_flagged"] += 1 if derive_flags(
                [{"jump": j["jump"], "status": j["status"],
                  "payload": j["payload"]} for j in res["jumps"]]) else 0
    except Exception:
        save_run(conn, "A_division", profile, MODEL, counts,
                 {**summary, **agg, "interrupted_match_id": mid},
                 started_at=t0)
        raise
    save_run(conn, "A_division", profile, MODEL, counts, {**summary, **agg},
             started_at=t0)
    return counts
```

- [ ] **Step 4: 跑测试通过**

Run: `uv run pytest tests/agentline/test_orchestrate.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/fa/agentline/orchestrate.py tests/agentline/test_orchestrate.py
git commit -m "feat(agentline): run_division 批编排——跳产物落库+flag摘要+幂等"
```

### Task 7: CLI——`fa agentline run --line A_division` 放行

**Files:**
- Modify: `src/fa/cli.py:201-222`
- Test: `tests/test_cli.py`（追加）

**Interfaces:**
- Consumes: `run_division`
- Produces: `--line` 合法值集合 = 四词 + A_division（五线齐）

- [ ] **Step 1: 写失败测试（追加）**

```python
def test_agentline_run_division_dispatch(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    import fa.agentline.orchestrate as orch
    monkeypatch.setattr(orch, "run_division",
                        lambda conn, d, limit=None: {"ok": 1, "parse_fail": 0,
                                                     "timeout": 0, "error": 0})
    result = runner.invoke(app, ["agentline", "run", "--line", "A_division"])
    assert result.exit_code == 0 and "ok" in result.output
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_cli.py -k division_dispatch -v`
Expected: FAIL——exit_code 2

- [ ] **Step 3: 实现（cli.py 现行块上改三处）**

1. `help="A_base、A_enh、A_multi 或 A_debate"` → `help="A_base、A_enh、A_multi、A_debate 或 A_division"`
2. import 行：`from fa.agentline.orchestrate import run_debate, run_division, run_line, run_multi`
3. 校验元组加 `"A_division"`；`elif line == "A_debate":` 块之后加：

```python
    elif line == "A_division":
        counts = run_division(conn, project_root() / "data" / "agentline",
                              limit=limit)
```

- [ ] **Step 4: 跑测试通过**

Run: `uv run pytest tests/test_cli.py -v`
Expected: 全 PASS（debate 计划的 unknown-line 拒绝用例仍绿——A_nonsense 不在五词内）

- [ ] **Step 5: Commit**

```bash
git add src/fa/cli.py tests/test_cli.py
git commit -m "feat(cli): fa agentline run --line A_division 接线（五线齐）"
```

### Task 8: compare——A_division 列 + 质询标签分层检验段

**Files:**
- Modify: `src/fa/agentline/compare.py`
- Test: `tests/agentline/test_compare.py`（追加）

**Interfaces:**
- Consumes: `_fetch_agent`、`merge_rows`、`metrics.log_loss`（单场复用）、`scipy.stats.mannwhitneyu`、`derive_flags`
- Produces: `compare_lines` cmp 增 `A_division` 键与 `cmp["division"]`；`division_stratified(conn, leagues=None, seasons=None) -> dict`——`{"n_flagged": int, "n_unflagged": int, "ll_flagged"?: float, "ll_unflagged"?: float, "mwu_p"?: float}`；`render_report` 表增 A_division 行 + 分层检验脚注段

- [ ] **Step 1: 写失败测试（追加；造库照该文件既有 helper，jumps 行连带插入）**

```python
def test_compare_lines_includes_division(conn_with_div_rows):
    cmp = compare_lines(conn_with_div_rows)
    assert "A_division" in cmp and cmp["A_division"]["n"] >= 1
    assert "division" in cmp


def test_division_stratified_flag_vs_unflag(conn_with_div_rows):
    s = division_stratified(conn_with_div_rows)
    assert s["n_flagged"] >= 1 and s["n_unflagged"] >= 1
    # 小样本（每层 n<5）不出 mwu_p——诚实边界
    if s["n_flagged"] < 5 or s["n_unflagged"] < 5:
        assert "mwu_p" not in s


def test_division_stratified_empty(conn_empty):
    s = division_stratified(conn_empty)
    assert s == {"n_flagged": 0, "n_unflagged": 0}
```

（`conn_with_div_rows`：≥6 场——其中 ≥1 场跳3 落 overconfidence 攻击、其余空 attacks；bp 行与 A_division ok 终版行齐。`conn_empty`：空表库。fixture 名按该文件现状落。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agentline/test_compare.py -k division -v`
Expected: FAIL——键不存在

- [ ] **Step 3: 实现（`compare.py`）**

线循环元组改 `("A_base", 1), ("A_enh", 1), ("A_multi", 0), ("A_debate", 1), ("A_division", 1)`；`cmp["debate"] = ...` 行后加 `cmp["division"] = division_stratified(conn, leagues, seasons)`。新增：

```python
def division_stratified(conn, leagues=None, seasons=None) -> dict:
    """质询标签分层检验（2026-09-05 设计 §3.4，retro 关卡3 口径）。

    分层键 = 五标签任一命中（jumpN_fail 是管线降级信号不进分层键）；
    各层 per-match log-loss（metrics.log_loss 单场调用——公式单一事实源，
    不在本文件重写）；MWU 双侧；任一层 n<5 只报 n 不报 p（小样本诚实）。
    非 ok 终版行默认剔除（audit 口径：分层只在可评行上做）。
    """
    from fa.backtest.metrics import fetch_predictions, log_loss
    from fa.agentline.division import derive_flags
    bp = fetch_predictions(conn, leagues, seasons)
    rows = merge_rows(bp, _fetch_agent(conn, "A_division", leagues, seasons,
                                       attributor=1))
    flagged, unflagged = [], []
    for r in rows:
        jumps = [{"jump": j["jump"], "status": j["status"],
                  "payload": j["payload_json"]} for j in conn.execute(
            "SELECT jump, status, payload_json FROM agentline_division_jumps"
            " WHERE match_id=?", (r["match_id"],))]
        fl = {k for k in derive_flags(jumps) if not k.startswith("jump")}
        ll = log_loss([(r["p_home"], r["p_draw"], r["p_away"])],
                      [r["outcome"]])
        (flagged if fl else unflagged).append(ll)
    out = {"n_flagged": len(flagged), "n_unflagged": len(unflagged)}
    if flagged:
        out["ll_flagged"] = sum(flagged) / len(flagged)
    if unflagged:
        out["ll_unflagged"] = sum(unflagged) / len(unflagged)
    if len(flagged) >= 5 and len(unflagged) >= 5:
        from scipy.stats import mannwhitneyu
        out["mwu_p"] = float(mannwhitneyu(flagged, unflagged,
                                          alternative="two-sided").pvalue)
    return out
```

`render_report`：主表循环元组加 `"A_division"`；`_FOOTNOTE` 前追加（与 debate 增益段并排）：

```python
    dv = cmp.get("division", {})
    if dv.get("n_flagged") or dv.get("n_unflagged"):
        p_s = (f"{dv['mwu_p']:.3f}" if "mwu_p" in dv
               else "n<5/层不报（小样本诚实）")
        lines += ["", f"> A_division 质询分层：有标签 n={dv['n_flagged']}"
                      f"（ll={dv.get('ll_flagged', float('nan')):.4f}）vs "
                      f"无标签 n={dv['n_unflagged']}"
                      f"（ll={dv.get('ll_unflagged', float('nan')):.4f}）；"
                      f"MWU 双侧 p={p_s}——允许结论为「质询无信息量」"]
```

- [ ] **Step 4: 跑测试通过**

Run: `uv run pytest tests/agentline/test_compare.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/fa/agentline/compare.py tests/agentline/test_compare.py
git commit -m "feat(agentline): compare 增 A_division 列 + 质询分层检验（MWU 双侧）"
```

### Task 9: E2E——主库小批 10 场真跑

**Files:**
- 无代码改动；产物为库内数据 + `docs/agentline/compare-YYYYMMDD.md` 滚动报告

**Interfaces:**
- Consumes: Task 1-8 全部
- Produces: 活库 A_division 首批真实数据（管线可用性证据口径）

- [ ] **Step 1: 前置检查**

Run: `uv run python -c "from fa.db import SCHEMA_VERSION; print(SCHEMA_VERSION)" && dsh --version`
Expected: `10` 且 dsh 可执行。dsh 不可用 → **如实报告并停在本步**，不 mock 凑数。

- [ ] **Step 2: 跑 10 场**

Run: `uv run fa agentline run --line A_division --limit 10`
Expected: counts 输出；任一跳失败逐场降级不中断

- [ ] **Step 3: 落库核验**

```bash
uv run python - <<'EOF'
from fa.db import connect
c = connect()
print("preds:", [dict(r) for r in c.execute(
    "SELECT status, COUNT(*) n FROM agentline_predictions"
    " WHERE line='A_division' GROUP BY status")])
print("jumps by role:", [dict(r) for r in c.execute(
    "SELECT role, status, COUNT(*) n FROM agentline_division_jumps"
    " GROUP BY role, status")])
print("runs:", c.execute("SELECT summary FROM agentline_runs WHERE"
    " line='A_division' ORDER BY id DESC LIMIT 1").fetchone()[0])
EOF
```

Expected: 状态计数 + jumps 按 role 计数（archivist/predictor/challenger）+ run 行 summary 含 n_calls/n_flagged

- [ ] **Step 4: 出滚动报告并核对分层段**

Run: `uv run fa agentline compare`
Expected: 报告含 A_division 行、质询分层脚注（n<5/层时如实标注不报 p）

- [ ] **Step 5: 提交报告 + 如实记录**

```bash
git add docs/agentline/
git commit -m "docs(agentline): A_division 首批 E2E 10 场——管线可用性证据（n=10 无统计意义）"
```

如实写：计数、跳失败分布、n_flagged——**不重跑挑结果；允许结论为「质询无信息量」**（spec §3.4）；数据齐口径（A_multi n≥300 且 ≥2 窗口）之前不作方向性结论。
