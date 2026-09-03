# retro ensemble（N-归因者投票）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 retro ensemble：N=3 独立归因者 + 确定性投票聚合 + 一致性测量（关卡 2 正式形态）。

**Architecture:** 新纯函数模块 `src/fa/retro/aggregate.py`（聚合规则）；`pipeline.run_retro_batch` 加 `attributors` 参数（N=1 行为与现行逐字节一致）；schema v5 加 `attributor` 列（ALTER 纯加法）；新 CLI `fa retro consistency`。总纲（multi-agent-charter）第一个实例：Python 指挥官，聚合规则预注册。

**Tech Stack:** Python 3.11+ / uv / typer / pytest / sqlite3（WAL）/ hermes -z headless subprocess。

**Spec:** docs/superpowers/specs/2026-09-04-retro-ensemble-design.md（聚合规则表见其 §4；总纲 docs/superpowers/specs/2026-09-04-multi-agent-charter.md）

## Global Constraints

- **Python 指挥官**：分工/投票/聚合/预算全部预注册确定性代码；agent 仍是有界专家（总纲 §1）
- 聚合行由 Python 构造，**不经 `validate_output`**（spec §4）；契约校验只作用于成员 LLM 输出
- 聚合规则逐字段照 spec §4 表：primary_tag 严格多数（无多数→NULL）；miss_tags 出现≥2 次（TAGS 枚举序，空集→NULL）；tags_confidence 中位数；evidence 按 primary 一致成员（无多数取全部）URL 去重并集；digest 取 primary 一致且 attributor 序最小成员（无多数→固定文案）；k=0 → status='error'（统一值）
- **不新增 status 词表值**（CHECK 已进生产不可后补）；attributor 列无 CHECK，`ADD COLUMN` 安全（spec §3）
- N=1（默认）：不产生聚合行、现有全部测试零改动通过（向后兼容是验收线）
- 台账计数语义（spec 未钉、本计划裁定）：N=1 沿用现行按场计数；N≥2 时 n_ok=聚合可用场数、n_error=全员失败场数、n_parse_fail/n_timeout=0（成员级明细由 `fa retro consistency` 出，不在台账重复）
- 测试全离线、时间注入缝（`pipeline._now`）、日期固定；`tests.retro.test_select._seed` 可导入复用
- hermes 实测形态沿用现行 runner（本计划不动 runner.py）
- 诚实条款（E2E 预写）：N=3 实测的一致性三档如实记录，不得重跑挑结果；主仓活库禁碰（E2E 用快照）

---

### Task 1: schema v5——attributor 列

**Files:**
- Modify: `src/fa/db.py`
- Test: `tests/test_db.py`（追加；并更新既有 version 断言，见 Step 1c）

**Interfaces:**
- Produces: `retro_attributions.attributor INTEGER NOT NULL DEFAULT 1`（成员 1..N、聚合 0）；`SCHEMA_VERSION == 5`

- [ ] **Step 1a: 写失败测试**（追加到 tests/test_db.py；沿用其 tmp_path 惯例与 attr() 辅助）

```python
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
    """v4 库（无 attributor 列）经 init_db ALTER 升 v5，存量行回填 1。"""
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
    conn.commit()
    conn.close()
    init_db(db)                                   # v4 -> v5
    conn = connect(db)
    try:
        assert conn.execute("SELECT version FROM schema_version"
                            ).fetchone()["version"] == 5
        cols = {c["name"] for c in conn.execute(
            "PRAGMA table_info(retro_attributions)")}
        assert "attributor" in cols
    finally:
        conn.close()
```

- [ ] **Step 1b: 跑测试确认失败**

Run: `uv run pytest tests/test_db.py -k "v5 or attributor" -v`
Expected: FAIL——attributor 列不存在（PRAGMA 查空）且 SCHEMA_VERSION 仍 4

- [ ] **Step 1c: 更新既有 version 断言**（计划内涟漪，同上一轮 v3→v4 先例）：在 tests/test_db.py 里搜 `== 4` 与 `_v4` 命名——`test_is_control_column_pinned` 的 `assert SCHEMA_VERSION == 4` 改 5；`test_v3_migrates_to_v4` 断言 version==4 的改 5（函数名可顺手改 `test_v3_migrates_to_v5`，引用处无）；v1/v2 升级用例如断言 version 亦改 5

- [ ] **Step 1d: 实现**——db.py 三处

1. `SCHEMA_VERSION = 4` → `SCHEMA_VERSION = 5`
2. `_RETRO_TABLE` 的 retro_attributions DDL：在 `selector` 行后加：

```sql
    attributor     INTEGER NOT NULL DEFAULT 1,  -- 成员 1..N；聚合行 0（ensemble，spec §3）
```

3. `_migrate_up` 追加（docstring 补「v4->v5 retro_attributions 加 attributor 列」）：

```python
    if from_v < 5:
        # ALTER 纯加法：attributor 无 CHECK 可安全 ADD COLUMN（词表一次到位
        # 教训只约束带 CHECK 的列）；存量行 DEFAULT 1 = 单成员语义不变
        conn.execute(
            "ALTER TABLE retro_attributions ADD COLUMN"
            " attributor INTEGER NOT NULL DEFAULT 1")
```

- [ ] **Step 1e: 跑全量 db 测试**

Run: `uv run pytest tests/test_db.py -v`
Expected: 全 PASS（新 2 例 + 既有涟漪更新后零失败）

- [ ] **Step 2: Commit**

```bash
git add src/fa/db.py tests/test_db.py
git commit -m "feat(retro): schema v5——retro_attributions.attributor 列（ALTER 纯加法，成员1..N/聚合0）"
```

---

### Task 2: aggregate.py——确定性聚合规则

**Files:**
- Create: `src/fa/retro/aggregate.py`
- Test: `tests/retro/test_aggregate.py`

**Interfaces:**
- Consumes: `fa.retro.contract.TAGS`
- Produces: `aggregate_members(members: list[dict]) -> dict`——入参每项 `{"status": str, "parsed": dict | None}`（成员经 run_headless+validate_output 后的结果；失败成员 parsed=None）；返回 `{"status": "ok"|"error", "primary_tag": str|None, "miss_tags": list[str]|None, "tags_confidence": float|None, "model_vs_market": str|None, "evidence": list[dict]|None, "digest": str}`（Task 3 消费，键名不得改）

- [ ] **Step 1: 写失败测试**

```python
"""聚合规则测试——纯函数。规则逐字段对 spec §4 表；成员 = validate_output 结果形态。"""
import pytest

from fa.retro.aggregate import aggregate_members


def _ok(primary, tags=None, conf=0.8, mvm="model_wrong", ev=None, digest="d"):
    return {"status": "ok", "parsed": {
        "miss_tags": tags or [primary], "primary_tag": primary,
        "tags_confidence": conf, "model_vs_market": mvm,
        "evidence": ev or [{"title": "t", "date": "2024-04-01",
                            "url": "https://e.com"}],
        "digest": digest}}


def _fail(status):
    return {"status": status, "parsed": None}


def test_majority_primary_and_tags():
    m = aggregate_members([
        _ok("injury", ["injury", "motivation"], digest="m1"),
        _ok("injury", ["injury"], digest="m2"),
        _ok("motivation", ["motivation"], digest="m3")])
    assert m["status"] == "ok" and m["primary_tag"] == "injury"
    assert m["miss_tags"] == ["injury"]          # motivation 仅 1 票 <2
    assert m["digest"] == "m1"                   # primary 一致且序最小
    assert m["model_vs_market"] == "model_wrong"  # 2/3 多数
    assert m["tags_confidence"] == pytest.approx(0.8)


def test_no_majority_nulls_and_fixed_digest():
    m = aggregate_members([
        _ok("injury"), _ok("motivation"), _ok("news")])
    assert m["status"] == "ok"
    assert m["primary_tag"] is None and m["model_vs_market"] is None
    assert m["miss_tags"] is None                 # 无标签达到 2 次
    assert "无多数" in m["digest"] and "injury" in m["digest"]


def test_evidence_union_dedup_by_url_primary_filtered():
    m = aggregate_members([
        _ok("injury", ev=[{"title": "a", "date": "2024-04-01",
                           "url": "u1"},
                          {"title": "b", "date": "2024-04-02",
                           "url": "u2"}]),
        _ok("injury", ev=[{"title": "a-dup", "date": "2024-04-01",
                           "url": "u1"},
                          {"title": "c", "date": "2024-04-03",
                           "url": "u3"}]),
        _ok("motivation", ev=[{"title": "other", "date": "2024-04-04",
                               "url": "u9"}])])
    urls = [e["url"] for e in m["evidence"]]
    assert urls == ["u1", "u2", "u3"]             # 去重保序；非 primary 成员(u9)不入


def test_no_majority_evidence_takes_all_members():
    m = aggregate_members([
        _ok("injury", ev=[{"title": "a", "date": "d1", "url": "u1"}]),
        _ok("motivation", ev=[{"title": "b", "date": "d2", "url": "u2"}]),
        _ok("news")])
    assert {e["url"] for e in m["evidence"]} == {"u1", "u2"}


def test_median_confidence():
    m = aggregate_members([
        _ok("injury", conf=0.9), _ok("injury", conf=0.5),
        _ok("injury", conf=0.7)])
    assert m["tags_confidence"] == pytest.approx(0.7)


def test_k0_all_failed_is_error():
    m = aggregate_members([_fail("timeout"), _fail("parse_fail"),
                           _fail("timeout")])
    assert m["status"] == "error" and m["primary_tag"] is None
    assert m["evidence"] is None


def test_partial_members_still_aggregate():
    """k=2（1 名 parse_fail）：2 票一致即多数。"""
    m = aggregate_members([_ok("injury"), _ok("injury"), _fail("parse_fail")])
    assert m["status"] == "ok" and m["primary_tag"] == "injury"


def test_two_ok_disagree_is_no_majority():
    m = aggregate_members([_ok("injury"), _ok("news"), _fail("error")])
    assert m["primary_tag"] is None and m["status"] == "ok"


def test_miss_tags_follow_tags_enum_order():
    m = aggregate_members([
        _ok("news", ["news", "injury"]), _ok("injury", ["injury", "news"]),
        _ok("variance")])
    assert m["miss_tags"] == ["injury", "news"]   # TAGS 枚举序，非出现序
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/retro/test_aggregate.py -v`
Expected: FAIL——`ModuleNotFoundError: fa.retro.aggregate`

- [ ] **Step 3: 实现** `src/fa/retro/aggregate.py`

```python
"""ensemble 聚合规则（spec §4，全部确定性/预注册；总纲第一个实例）。

聚合行由 Python 构造、不经 validate_output——契约校验只作用于成员的
LLM 输出；聚合行质量由成员行 + 本规则共同保证。
primary/词表判定用严格多数（票数*2 > 可用成员数）；miss_tags 用固定
阈值「出现 ≥2 次」；空结果一律 None（不用空串/空数组占位）。
"""
import statistics

from fa.retro.contract import TAGS


def _majority(values: list[str]) -> str | None:
    for v in set(values):
        if values.count(v) * 2 > len(values):
            return v
    return None


def aggregate_members(members: list[dict]) -> dict:
    ok = [m["parsed"] for m in members
          if m.get("status") == "ok" and m.get("parsed")]
    if not ok:
        return {"status": "error", "primary_tag": None, "miss_tags": None,
                "tags_confidence": None, "model_vs_market": None,
                "evidence": None, "digest": "全员失败（k=0），见成员行"}
    primary = _majority([p["primary_tag"] for p in ok])
    mvm = _majority([p["model_vs_market"] for p in ok])
    counts = {t: sum(t in p["miss_tags"] for p in ok) for t in TAGS}
    miss = [t for t in TAGS if counts[t] >= 2] or None
    if primary is None:
        digest = ("成员无多数（"
                  + "|".join(p["primary_tag"] for p in ok) + "），见成员行")
        src = ok                                   # 证据取全部可用成员
    else:
        src = [p for p in ok if p["primary_tag"] == primary]
        digest = src[0]["digest"]                  # primary 一致且序最小
    seen, evidence = set(), []
    for p in src:
        for e in p.get("evidence", []):
            if e["url"] not in seen:
                seen.add(e["url"])
                evidence.append(e)
    return {"status": "ok", "primary_tag": primary, "miss_tags": miss,
            "tags_confidence": statistics.median(
                p["tags_confidence"] for p in ok),
            "model_vs_market": mvm, "evidence": evidence, "digest": digest}
```

- [ ] **Step 4: 跑测试**

Run: `uv run pytest tests/retro/test_aggregate.py -v`
Expected: 全 PASS（9 例）

- [ ] **Step 5: Commit**

```bash
git add src/fa/retro/aggregate.py tests/retro/test_aggregate.py
git commit -m "feat(retro): ensemble 聚合规则——严格多数/中位数/URL 去重并集/k=0 归 error"
```

---

### Task 3: pipeline ensemble——attributors 参数与聚合行落库

**Files:**
- Modify: `src/fa/retro/pipeline.py`
- Test: `tests/retro/test_pipeline.py`（追加）

**Interfaces:**
- Consumes: Task 1 `attributor` 列；Task 2 `aggregate_members(members) -> dict`
- Produces: `run_retro_batch(conn, cands, selector, params, out_root, call=None, attributors=1) -> dict`（返回键不变；Task 4 CLI 传 attributors）
- 台账语义（Global Constraints 裁定）：N=1 现行不变；N≥2 时 n_ok=聚合可用场数、n_error=全员失败场数、n_parse_fail/n_timeout=0

- [ ] **Step 1: 写失败测试**（追加到 tests/retro/test_pipeline.py；沿用其 conn/clock 夹具与 `from tests.retro.test_select import _seed`）

```python
_ENS_OK_A = json.dumps({
    "miss_tags": ["injury", "motivation"], "primary_tag": "injury",
    "tags_confidence": 0.8, "model_vs_market": "model_wrong",
    "evidence": [{"title": "t", "date": "2024-04-01",
                  "url": "https://e.com/1"}],
    "digest": "伤停为主因。"}, ensure_ascii=False)
_ENS_OK_B = json.dumps({
    "miss_tags": ["motivation"], "primary_tag": "motivation",
    "tags_confidence": 0.6, "model_vs_market": "variance",
    "evidence": [{"title": "u", "date": "2024-04-02",
                  "url": "https://e.com/2"}],
    "digest": "动机为主因。"}, ensure_ascii=False)


def _call_ok(output):
    def fake(prompt):
        return {"ok": True, "output": output, "error": None,
                "duration_s": 0.5, "timeout": False}
    return fake


def test_ensemble_majority_writes_members_and_aggregate(conn, clock, tmp_path):
    out = run_retro_batch(conn, _cands(conn)[:1], "manual",
                          {"attributors": 3}, tmp_path / "p",
                          call=_sequence(_ENS_OK_A, _ENS_OK_A, _ENS_OK_B),
                          attributors=3)
    assert (out["n_ok"], out["n_error"]) == (1, 0)
    rows = conn.execute(
        "SELECT attributor, status, primary_tag, miss_tags_json, digest"
        " FROM retro_attributions ORDER BY attributor").fetchall()
    assert [r["attributor"] for r in rows] == [0, 1, 2, 3]   # 聚合 0 在前
    agg = rows[0]
    assert agg["status"] == "ok" and agg["primary_tag"] == "injury"
    assert json.loads(agg["miss_tags_json"]) == ["injury"]   # motivation 1 票
    assert agg["digest"] == "伤停为主因。"                     # 序最小一致成员
    assert rows[1]["primary_tag"] == "injury"
    assert rows[3]["primary_tag"] == "motivation"


def test_ensemble_all_failed_aggregate_error(conn, clock, tmp_path):
    def all_timeout(prompt):
        return {"ok": False, "output": "", "error": "hermes -z 超时（300s）",
                "duration_s": 300.0, "timeout": True}
    out = run_retro_batch(conn, _cands(conn)[:1], "manual", {},
                          tmp_path / "p", call=all_timeout, attributors=3)
    assert (out["n_ok"], out["n_error"], out["n_timeout"]) == (0, 1, 0)
    rows = conn.execute(
        "SELECT attributor, status FROM retro_attributions"
        " ORDER BY attributor").fetchall()
    assert [r["attributor"] for r in rows] == [0, 1, 2, 3]
    assert rows[0]["status"] == "error"            # k=0 统一 error（spec §4）
    assert all(r["status"] == "timeout" for r in rows[1:])


def test_single_attributor_unchanged_no_aggregate(conn, clock, tmp_path):
    """N=1 默认路径：行为与现行一致，仅多 attributor=1 值，无聚合行。"""
    out = run_retro_batch(conn, _cands(conn)[:1], "manual", {},
                          tmp_path / "p", call=_call_ok(_ENS_OK_A))
    assert (out["n_ok"], out["n_parse_fail"], out["n_timeout"],
            out["n_error"]) == (1, 0, 0, 0)
    rows = conn.execute("SELECT attributor, status FROM retro_attributions"
                        ).fetchall()
    assert len(rows) == 1 and rows[0]["attributor"] == 1
```

（`_sequence(*outputs)` 与 `_cands` 若文件里尚无，补在本测试文件——`_cands` 已有；`_sequence` 写法：）

```python
def _sequence(*outputs):
    it = iter(outputs)
    def fake(prompt):
        return {"ok": True, "output": next(it), "error": None,
                "duration_s": 0.5, "timeout": False}
    return fake
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/retro/test_pipeline.py -k ensemble -v`
Expected: FAIL——`run_retro_batch() got an unexpected keyword argument 'attributors'`

- [ ] **Step 3: 实现**——pipeline.py 改造（保持既有结构，循环内改多调用）

3a. import 区加：`from fa.retro.aggregate import aggregate_members`

3b. 签名与成员调用（替换现行「单次 call → 单行 INSERT」主体；N=1 路径语义不变）。成员结果用 `(status, parsed, repaired)` 三元组保存——两键 dict 会丢 validate_output 的 repaired 标记：

```python
def run_retro_batch(conn: sqlite3.Connection, cands: list[dict],
                    selector: str, params: dict, out_root: Path,
                    call=None, attributors: int = 1) -> dict:
    import time
    if call is None:
        call = run_headless
    if attributors < 1:
        raise ValueError(f"attributors 须 ≥1，收到 {attributors}")
    t0 = time.monotonic()
    pack_dir = Path(out_root) / f"batch-{_ts().replace(':', '').replace('-', '')}"
    paths = write_packs(conn, cands, pack_dir)
    counts = {"ok": 0, "parse_fail": 0, "timeout": 0, "error": 0}
    ledger = conn.execute(
        "INSERT INTO retro_runs (selector, params_json, n_selected, n_ok,"
        " n_parse_fail, n_timeout, n_error, duration_s, created_at)"
        " VALUES (?, ?, ?, 0, 0, 0, 0, 0.0, ?)",
        (selector, json.dumps(params, ensure_ascii=False), len(cands),
         _ts()))
    batch_id = ledger.lastrowid
    for cand in cands:
        pack = build_pack(conn, cand)
        prompt = build_prompt(pack)
        members: list[tuple] = []          # (status, parsed, repaired)
        member_durations: list[float] = []
        for i in range(attributors):
            res = call(prompt)
            member_durations.append(res["duration_s"])
            if not res["ok"]:
                member = ("timeout" if res.get("timeout") else "error",
                          None, False)
            else:
                v = validate_output(res["output"])
                member = (("ok", v["parsed"], v["repaired"])
                          if v["status"] == "ok" else ("parse_fail", None, False))
            members.append(member)
            _insert_member(conn, batch_id, cand, selector, i + 1, member,
                           res["duration_s"], pack_dir, paths)
        if attributors == 1:
            counts[members[0][0]] += 1     # 现行按场计数，逐字节不变
        else:
            agg = aggregate_members(
                [{"status": s, "parsed": p} for s, p, _ in members])
            _insert_aggregate(conn, batch_id, cand, selector, agg,
                              sum(member_durations), pack_dir, paths)
            counts["ok" if agg["status"] == "ok" else "error"] += 1
    duration = time.monotonic() - t0
    conn.execute(
        "UPDATE retro_runs SET n_ok=?, n_parse_fail=?, n_timeout=?, n_error=?,"
        " duration_s=? WHERE id=?",
        (counts["ok"], counts["parse_fail"], counts["timeout"],
         counts["error"], duration, batch_id))
    conn.commit()
    return {"batch_id": batch_id, "n_selected": len(cands), "n_ok": counts["ok"],
            "n_parse_fail": counts["parse_fail"], "n_timeout": counts["timeout"],
            "n_error": counts["error"], "duration_s": duration}
```

3c. 两个行插入辅助（模块级私有；列集对齐现行 INSERT 的契约/审计字段拆分）：

```python
def _insert_member(conn, batch_id, cand, selector, attributor, member,
                   duration_s, pack_dir, paths) -> None:
    status, parsed, repaired = member
    conn.execute(
        "INSERT INTO retro_attributions (batch_id, match_id, league, season,"
        " date, selector, attributor, miss_tags_json, primary_tag,"
        " tags_confidence, model_vs_market, evidence_json, digest, status,"
        " repaired, harness, model, duration_s, input_pack_path,"
        " tag_set_version, created_at, is_control)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (batch_id, cand["match_id"], cand["league"], cand["season"],
         cand["date"], selector, attributor,
         json.dumps(parsed["miss_tags"], ensure_ascii=False) if parsed else None,
         parsed["primary_tag"] if parsed else None,
         parsed["tags_confidence"] if parsed else None,
         parsed["model_vs_market"] if parsed else None,
         json.dumps(parsed["evidence"], ensure_ascii=False) if parsed else None,
         parsed["digest"] if parsed else None,
         status, 1 if repaired else 0, HARNESS, None, duration_s,
         str(pack_dir / paths[cand["match_id"]]), TAG_SET_VERSION, _ts(),
         1 if cand.get("is_control") else 0))


def _insert_aggregate(conn, batch_id, cand, selector, agg, duration_s,
                      pack_dir, paths) -> None:
    conn.execute(
        "INSERT INTO retro_attributions (batch_id, match_id, league, season,"
        " date, selector, attributor, miss_tags_json, primary_tag,"
        " tags_confidence, model_vs_market, evidence_json, digest, status,"
        " repaired, harness, model, duration_s, input_pack_path,"
        " tag_set_version, created_at, is_control)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (batch_id, cand["match_id"], cand["league"], cand["season"],
         cand["date"], selector, 0,
         json.dumps(agg["miss_tags"], ensure_ascii=False)
         if agg["miss_tags"] is not None else None,
         agg["primary_tag"], agg["tags_confidence"], agg["model_vs_market"],
         json.dumps(agg["evidence"], ensure_ascii=False)
         if agg["evidence"] is not None else None,
         agg["digest"], agg["status"], 0, HARNESS, None, duration_s,
         str(pack_dir / paths[cand["match_id"]]), TAG_SET_VERSION, _ts(),
         1 if cand.get("is_control") else 0))
```

（聚合行 `repaired` 恒 0——聚合由 Python 构造无「修复」语义；duration_s = 本场全部成员调用耗时之和，由调用方传入。）

- [ ] **Step 4: 跑 ensemble 与全量 retro 测试**

Run: `uv run pytest tests/retro/ -v`
Expected: 全 PASS（新增 3 例 + 既有零回归——特别注意既有 `flaky`/`empty_batch` 用例在重构后仍绿，语义未变）

- [ ] **Step 5: Commit**

```bash
git add src/fa/retro/pipeline.py tests/retro/test_pipeline.py
git commit -m "feat(retro): ensemble 批跑——N 成员循环+聚合行(attributor=0)，N=1 逐字节兼容"
```

---

### Task 4: CLI——--attributors 与 fa retro consistency

**Files:**
- Modify: `src/fa/cli.py`（retro 段）
- Modify: `src/fa/retro/analyze.py`（追加 consistency_report）
- Test: `tests/retro/test_cli_retro.py`（追加）

**Interfaces:**
- Consumes: Task 3 `run_retro_batch(..., attributors=N)`
- Produces:
  - `analyze.consistency_report(conn, batch_id=None) -> dict`——`{"n_matches", "unanimous", "majority", "none", "member_failures", "agreement_rate"}`（agreement_rate = (unanimous+majority)/n_matches；member_failures = status≠ok 的成员行数）
  - CLI：`fa retro run --attributors`、`fa retro consistency [--batch-id]`

- [ ] **Step 1: 写失败测试**

```python
def test_run_with_attributors_records_params(db, tmp_path, monkeypatch):
    from fa.retro import pipeline
    outs = iter([_E1, _E1, _E2])                  # 2 票 injury + 1 票 motivation
    monkeypatch.setattr(pipeline, "run_headless",
                        lambda p: {"ok": True, "output": next(outs),
                                   "error": None, "duration_s": 0.1,
                                   "timeout": False})
    r = runner.invoke(app, ["retro", "run", "--selector", "manual",
                            "--matches", "1", "--attributors", "3",
                            "--out-root", str(tmp_path / "p")])
    assert r.exit_code == 0, r.output
    conn = connect(db)
    params = json.loads(conn.execute(
        "SELECT params_json FROM retro_runs").fetchone()["params_json"])
    conn.close()
    assert params["attributors"] == 3


def test_consistency_three_tiers(db, tmp_path, monkeypatch):
    """3 场各 3 成员：一场全同、一场 2:1、一场三票各异 + 1 场全失败。"""
    from fa.retro import pipeline
    seq = iter([_E1, _E1, _E1,        # match1 全同
                _E1, _E1, _E2,        # match2 多数
                _E1, _E2, _E3,        # match3 无多数
               ])                      # match4 全 timeout 在下一个替身
    monkeypatch.setattr(pipeline, "run_headless",
                        lambda p: {"ok": True, "output": next(seq),
                                   "error": None, "duration_s": 0.1,
                                   "timeout": False})
    r = runner.invoke(app, ["retro", "run", "--selector", "manual",
                            "--matches", "1,2,3", "--attributors", "3",
                            "--out-root", str(tmp_path / "p")])
    assert r.exit_code == 0, r.output
    monkeypatch.setattr(pipeline, "run_headless",
                        lambda p: {"ok": False, "output": "",
                                   "error": "hermes -z 超时（300s）",
                                   "duration_s": 1.0, "timeout": True})
    r = runner.invoke(app, ["retro", "run", "--selector", "manual",
                            "--matches", "1", "--attributors", "3",
                            "--out-root", str(tmp_path / "p2")])
    assert r.exit_code == 0, r.output
    c = runner.invoke(app, ["retro", "consistency"])
    assert c.exit_code == 0, c.output
    assert "全同" in c.output and "多数" in c.output and "无多数" in c.output
    assert "成员失败" in c.output


def test_consistency_report_values(db, tmp_path, monkeypatch):
    from fa.retro import pipeline
    from fa.retro.analyze import consistency_report
    seq = iter([_E1, _E1, _E1, _E1, _E1, _E2])
    monkeypatch.setattr(pipeline, "run_headless",
                        lambda p: {"ok": True, "output": next(seq),
                                   "error": None, "duration_s": 0.1,
                                   "timeout": False})
    runner.invoke(app, ["retro", "run", "--selector", "manual",
                        "--matches", "1,2", "--attributors", "3",
                        "--out-root", str(tmp_path / "p")])
    conn = connect(db)
    rep = consistency_report(conn)
    conn.close()
    assert rep["n_matches"] == 2
    assert (rep["unanimous"], rep["majority"], rep["none"]) == (1, 1, 0)
    assert rep["member_failures"] == 0
```

（`_E1/_E2/_E3` 为三份不同 primary 的合法契约 JSON 常量，写在测试文件顶部——`_E1` primary=injury、`_E2`=motivation、`_E3`=news，格式同 Task 3 的 `_ENS_OK_A/B`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/retro/test_cli_retro.py -k "attributors or consistency" -v`
Expected: FAIL——`--attributors` no such option / `consistency` UnknownCommand / `consistency_report` 不存在

- [ ] **Step 3: 实现**

3a. `analyze.py` 追加（module 顶部已有 json/sqlite3/PREMATCH_CAUSE_TAGS）：

```python
def consistency_report(conn: sqlite3.Connection, batch_id=None) -> dict:
    """关卡 2 正式形态：成员 primary 三档一致性 + 成员失败计数。

    只统计成员行（attributor≥1）中 status='ok' 者；一场全失败不计入
    三档（该场聚合 status='error' 已在台账），但失败成员计入 member_failures。
    """
    sql = ("SELECT batch_id, match_id, attributor, primary_tag, status"
           " FROM retro_attributions WHERE attributor >= 1")
    args: list = []
    if batch_id is not None:
        sql += " AND batch_id=?"
        args.append(batch_id)
    by_match: dict = {}
    failures = 0
    for r in conn.execute(sql, args):
        g = by_match.setdefault((r["batch_id"], r["match_id"]), [])
        if r["status"] == "ok":
            g.append(r["primary_tag"])
        else:
            failures += 1
    tiers = {"unanimous": 0, "majority": 0, "none": 0}
    for votes in by_match.values():
        if not votes:
            continue                               # 全失败场不入三档
        top = max(set(votes), key=votes.count)
        if votes.count(top) == len(votes):
            tiers["unanimous"] += 1
        elif votes.count(top) * 2 > len(votes):
            tiers["majority"] += 1
        else:
            tiers["none"] += 1
    n = sum(tiers.values())
    return {"n_matches": n, **tiers, "member_failures": failures,
            "agreement_rate": (tiers["unanimous"] + tiers["majority"]) / n
            if n else 0.0}
```

3b. `cli.py` retro_run 加 option 并传参、params 记录：

```python
    attributors: int = typer.Option(
        1, "--attributors", help="独立归因者数（ensemble；1=现行单跑，≥2 产生投票聚合行）"),
```

selector 分支后统一校验：`if attributors < 1: echo+Exit(1)`；divergence 与 manual 的 params 字典各补 `"attributors": attributors`；调用改 `run_retro_batch(conn, cands, selector, params, root, attributors=attributors)`。

3c. 新命令（挂在 retro_app，风格同 audit/runs）：

```python
@retro_app.command("consistency")
def retro_consistency(
    batch_id: int = typer.Option(None, "--batch-id", help="空=全部批"),
) -> None:
    """成员一致性三档（关卡 2：全同/多数/无多数 + 成员失败计数）"""
    from fa.retro.analyze import consistency_report
    conn = connect()
    try:
        rep = consistency_report(conn, batch_id)
    finally:
        conn.close()
    typer.echo(f"一致性（n={rep['n_matches']} 场）：全同 {rep['unanimous']}"
               f"  多数 {rep['majority']}  无多数 {rep['none']}"
               f"  一致率 {rep['agreement_rate']:.0%}（全同+多数）")
    typer.echo(f"成员失败行：{rep['member_failures']}（成员级明细见"
               " fa retro runs 台账与成员行 status）")
    typer.echo("解读规则：全同率 <50% → 归因线维持「假设生成器」降格"
               "（spec §5 预写）")
```

- [ ] **Step 4: 跑测试与全量**

Run: `uv run pytest tests/retro/ -v && uv run pytest -q`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/fa/cli.py src/fa/retro/analyze.py tests/retro/test_cli_retro.py
git commit -m "feat(retro): --attributors 与 fa retro consistency——关卡2 三档一致性测量"
```

---

### Task 5: E2E 真机校准（N=3 一场）

**Files:**
- 无代码改动（结果记入 spec §15 追加行 + 台账）；若实测暴露聚合/落库缺陷，修复走 review

**Interfaces:**
- Consumes: 全部前序
- Produces: 真机 N=3 首批数据（一致性三档、耗时、契约成功率）

- [ ] **Step 1: 快照主库到 worktree**（主仓活库禁碰，同上一轮约束）

```bash
mkdir -p data && python3 -c "import sqlite3; s=sqlite3.connect('/home/ryea0/Project/Football_Analysis/data/fa.db'); d=sqlite3.connect('data/fa.db'); s.backup(d)"
```

（注意：若主库已被负责人 `fa init` 迁到 v4，快照后 `FA_DB=data/fa.db uv run fa init` 升 v5；若仍是 v3 同样一次 init 直升 v5——两条路径都已测）

- [ ] **Step 2: 挑一场 + N=3 真机跑**

```bash
FA_DB=data/fa.db uv run fa retro report --top 5      # 挑一个 match_id
FA_DB=data/fa.db uv run fa retro run --selector manual --matches <id> \
    --attributors 3 --out-root data/retro/inputs
```

Expected: 正常退出，输出 `ok=1`（或 `error=1`——两者都合法）；3 真实调用 ≈35s。

- [ ] **Step 3: 一致性与审计核对**

```bash
FA_DB=data/fa.db uv run fa retro consistency
FA_DB=data/fa.db uv run fa retro audit
FA_DB=data/fa.db uv run fa retro runs
```

- [ ] **Step 4: 诚实记录 + spec 追加**

- 一致性三档、单场耗时、契约成功率如实写入 spec `2026-09-04-retro-ensemble-design.md` 末尾「§11 E2E 实测」小节；**不得重跑挑结果**（重跑仅限验证参数修正，且失败行保留）
- 若全同率为 0 且三票各异（LLM 归因随机性的极端信号）：如实记录，这是关卡 2 要的数据，**不修代码不调 prompt**
- 全量 `uv run pytest -q` 回归后提交并 `git push`

```bash
uv run pytest -q
git add docs/superpowers/specs/2026-09-04-retro-ensemble-design.md
git commit -m "docs: retro ensemble E2E 首批实测（N=3 一致性/耗时/成功率）"
git push -u origin worktree-retro-ensemble-design
```

---

## Self-Review 记录

1. **Spec 覆盖**：§3 迁移 = T1；§4 聚合规则表逐字段 = T2（9 例测试覆盖每行规则）；§2 形态与 §7 CLI/成本 = T3/T4；§5 一致性测量+预写解读 = T4 consistency；§6 audit 扩展 = 零改动（聚合行天然被现行 SQL 覆盖——miss_tags 非空含赛前成因标签即受检，T4 不需新逻辑，T5 E2E 实测验证）；§8 测试策略逐条落位；§9 非目标未越界（无并行调用/无异构成员/不动选择器）
2. **占位符扫描**：无 TBD/TODO；全部代码单一正确版本（自审期清除了初稿两处示范性笔误：成员两键 dict 丢 repaired、聚合 duration_s 占位 sum——已合并为成员三元组 + 调用方传参的正确版）
3. **类型一致性**：`aggregate_members` 返回键 = T3 `_insert_aggregate` 取键逐一核对（status/primary_tag/miss_tags/tags_confidence/model_vs_market/evidence/digest）；成员三元 `(status, parsed, repaired)` 与 `_insert_member` 解包一致；`consistency_report` 返回键与 T4 命令输出一致；`attributors` 参数名 T3/T4/CLI 一致
4. **九条自检**：#1 测试日期全固定；#2 多数判定方向代入（2/3 严格多数、k=2 全同=unanimous、2 票不同=none）；#3 阈值先验算（三档测试值 1/1/0 手工推演）；#4 mock 先 patch 后调用、目标 `pipeline.run_headless`（调用时解析缝，T7 已验证）；#5 `_E1/_E2/_E3` 常量名与说明一致；#6 attributor 列定义在 DDL 与 ALTER 两处——以注释锚定单源（SQLite ALTER 无法引用 DDL 常量，属平台限制，测试 v4→v5 与新建同构保证一致）；#7 conftest seal_environ 兜底；#8 笔误处全部内联正确版；#9 T5 诚实条款预写（不重跑挑结果/不修码凑数）
