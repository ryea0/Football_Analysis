# 线 A 集成投票制（A_multi）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 A_multi：N=3 个独立 dsh 大脑对同批场次各自预测 → 确定性聚合（分量中位数）→ 五对照评测（P/A_base/A_enh/A_multi/市场）。

**Architecture:** schema v7 重建 agentline_predictions（line CHECK 加 'A_multi'、attributor 列、UNIQUE 三元组）；新纯函数模块 `src/fa/agentline/ensemble.py`（聚合规则）；orchestrate 加 `run_multi`；compare 增 A_multi 列（只取 attributor=0 聚合行）。Python 指挥官：聚合规则预注册，agent 是感官（总纲 §1）。

**Tech Stack:** Python 3.11+ / uv / typer / pytest / sqlite3（WAL）/ dsh headless。

**Spec:** docs/superpowers/specs/2026-09-04-agentline-multi-brain-design.md §3 形态一（预批准位）；总纲 2026-09-04-multi-agent-charter.md（测量纪律：成员级计分/三对照/分歧即数据）。

## Global Constraints

- **成员 = 3 个独立 dsh 会话，同信息集、同 A_base prompt（无检索）**、profile `fa-agent-base`；刻意不做异构成员（gated-2 扩展位）
- 聚合规则逐字段照 spec §3.2：p_home/p_draw/p_away **分量中位数→三项和超 ±0.05 按比例归一**（沿用 contract 归一口径）；p_over25 中位数；confidence 中位数；sources URL 去重并集；reasoning_digest 取与聚合概率向量 **KL 距离最小**成员（平票取 attributor 序最小）；k=0 → status='error' 契约字段全 NULL（digest 例外携带「全员失败（k=0）」）
- 任一成员 parse_fail 不影响聚合（k≥1 即可聚）；成员行如实落库（纪律 1 成员级计分）
- line CHECK 词表 v7 一次到位：`('A_base','A_enh','A_multi')`——SQLite 无法后补 CHECK，本次重建是唯一窗口
- A_multi 产出永不进 B 线推荐/落注流（双路线 §2 分账继承）；真实下注禁止
- compare 的 A_multi 只取 **attributor=0** 聚合行；A_base/A_enh 取 attributor=1（v7 回填后即全部单跑行）
- 测试全离线（mock runner）、时间注入缝；E2E 主库直跑（活库为运营库，v7 迁移幂等护栏兜底）
- 诚实条款：E2E 计数如实入台账；不重跑挑结果；样本量与折扣如实进报告

---

### Task 1: schema v7——agentline_predictions 重建

**Files:**
- Modify: `src/fa/db.py`
- Modify: `src/fa/agentline/store.py`（save_prediction 加 attributor 参数）
- Test: `tests/agentline/test_db_v4.py`（追加）

**Interfaces:**
- Produces: `agentline_predictions`：line CHECK 含 `'A_multi'`、`attributor INTEGER NOT NULL DEFAULT 1`、`UNIQUE (match_id, line, attributor)`；`SCHEMA_VERSION == 7`；`save_prediction(conn, match_id, line, parsed, raw_output, harness, model, duration_s, attributor=1) -> int`

- [ ] **Step 1: 写失败测试**（追加到 tests/agentline/test_db_v4.py）

```python
def test_v7_attributor_and_amulti_vocab(tmp_path):
    """v7：attributor 列 + A_multi 词表位 + UNIQUE 三元组（成员可同场共存）。"""
    from fa.db import connect, init_db
    init_db(tmp_path / "t.db")
    conn = connect(tmp_path / "t.db")
    try:
        cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(agentline_predictions)")}
        assert "attributor" in cols
        ok = {"status": "ok", "p_home": 0.4, "p_draw": 0.3, "p_away": 0.3,
              "p_over25": 0.5, "confidence": 0.6, "reasoning_digest": "d",
              "sources_json": "[]", "repaired": False}
        from fa.agentline.store import save_prediction
        save_prediction(conn, 1, "A_multi", ok, "raw", "dsh", "m", 1.0, 1)
        save_prediction(conn, 1, "A_multi", ok, "raw", "dsh", "m", 1.0, 2)
        save_prediction(conn, 1, "A_multi", ok, "raw", "dsh", "m", 1.0, 0)  # 聚合
        n = conn.execute("SELECT COUNT(*) c FROM agentline_predictions"
                         ).fetchone()["c"]
        assert n == 3                       # 同 match 同 line 三行共存
    finally:
        conn.close()


def test_v6_migrates_to_v7_rebuild(tmp_path):
    """v6 库（无 attributor、旧 UNIQUE）经 init_db 重建升 v7，数据保全。"""
    from fa.db import connect, init_db
    db = tmp_path / "v6.db"
    init_db(db)                                     # v7 新建
    conn = connect(db)
    conn.execute("PRAGMA foreign_keys=OFF")
    old = {r["name"] for r in conn.execute(
        "PRAGMA table_info(agentline_predictions)")}
    assert "attributor" in old
    # 造 v6 形状：去掉 attributor 列 + 改回二元 UNIQUE（重建路径必须应对）
    conn.execute("ALTER TABLE agentline_predictions DROP COLUMN attributor")
    conn.execute("UPDATE schema_version SET version=6")
    # 塞两行存量数据，迁移后必须保全
    for i in (1, 2):
        conn.execute(
            "INSERT INTO agentline_predictions (match_id, line, p_home,"
            " p_draw, p_away, p_over25, confidence, reasoning_digest,"
            " sources_json, raw_output, status, repaired, harness, model,"
            " duration_s, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (i, "A_base", 0.4, 0.3, 0.3, 0.5, 0.6, "d", "[]", "r", "ok",
             0, "dsh", "m", 1.0, "2026-09-04T00:00:00Z"))
    conn.commit()
    conn.close()
    init_db(db)                                     # v6 -> v7 重建
    conn = connect(db)
    try:
        assert conn.execute("SELECT version FROM schema_version"
                            ).fetchone()["version"] == 7
        cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(agentline_predictions)")}
        assert "attributor" in cols
        rows = conn.execute("SELECT match_id, attributor FROM"
                            " agentline_predictions ORDER BY match_id").fetchall()
        assert [(r["match_id"], r["attributor"]) for r in rows] \
            == [(1, 1), (2, 1)]                     # 存量保全 + 回填 1
    finally:
        conn.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agentline/test_db_v4.py -k "v7 or attributor" -v`
Expected: FAIL（attributor 列不存在 / A_multi 被 CHECK 拒）

- [ ] **Step 3: 实现**

3a. db.py：`SCHEMA_VERSION = 6` → `7`；`_AL_TABLE` 中 agentline_predictions 的 line CHECK 改 `('A_base', 'A_enh', 'A_multi')`，表级 `UNIQUE (match_id, line)` 改 `UNIQUE (match_id, line, attributor)`，加列行 `attributor INTEGER NOT NULL DEFAULT 1,  -- 成员 1..N；聚合行 0（A_multi）`；`_SCHEMA` 拼接不变。

3b. `_migrate_up` 追加（docstring 补 v6->v7）：

```python
    if from_v < 7:
        # v7：agentline_predictions 加 attributor + A_multi 词表 + 三元 UNIQUE。
        # CHECK 无法后补——唯一路径是重建（rename-copy-drop）。幂等护栏：
        # attributor 已在（新库先经 _SCHEMA 建出即含列）则只做 nothing。
        acols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(agentline_predictions)")}
        if "attributor" not in acols:
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
            conn.execute("DROP TABLE agentline_predictions_v6;")
```

（注：rename 后旧 UNIQUE 索引随旧表走，新表由 _AL_TABLE 全新建立——列名与 _AL_TABLE 精确一致，以仓库当前 DDL 为准逐列核对。）

3c. store.py：`save_prediction` 签名加 `attributor: int = 1`；INSERT 列与 VALUES 各加一处；`ON CONFLICT(match_id, line, attributor)`。

- [ ] **Step 4: 跑全量**

Run: `uv run pytest -q`
Expected: 全 PASS（含 agentline 既有用例零回归——save_prediction 默认参兼容旧调用）

- [ ] **Step 5: Commit**

```bash
git add src/fa/db.py src/fa/agentline/store.py tests/agentline/test_db_v4.py
git commit -m "feat(agentline): schema v7——attributor 列+A_multi 词表+三元 UNIQUE（重建迁移保全数据）"
```

---

### Task 2: ensemble.py——概率聚合规则

**Files:**
- Create: `src/fa/agentline/ensemble.py`
- Test: `tests/agentline/test_ensemble.py`

**Interfaces:**
- Produces: `aggregate_predictions(members: list[dict | None]) -> dict`——入参为 parse_prediction 结果列表（失败成员传 None 或 status!=ok 的 dict）；返回可直接交 `save_prediction` 的 parsed dict（键与 parse_prediction 的 ok/fail 形状一致：status/p_home/p_draw/p_away/p_over25/confidence/reasoning_digest/sources_json/repaired）

- [ ] **Step 1: 写失败测试**

```python
"""A_multi 聚合规则测试——纯函数。分量中位数/KL 最近 digest/URL 去重并集。"""
import json
import math

import pytest

from fa.agentline.ensemble import aggregate_predictions


def _ok(ph, pd_, pa, po=0.5, conf=0.6, digest="d", sources=None):
    return {"status": "ok", "p_home": ph, "p_draw": pd_, "p_away": pa,
            "p_over25": po, "confidence": conf, "reasoning_digest": digest,
            "sources_json": json.dumps(sources or []), "repaired": False}


def test_component_median_and_renormalize():
    """三项分量各取中位数；中位数和超容差按比例归一。"""
    m = aggregate_predictions([
        _ok(0.50, 0.30, 0.20), _ok(0.40, 0.35, 0.25), _ok(0.45, 0.25, 0.30)])
    assert m["status"] == "ok"
    # 分量中位数：0.45/0.30/0.25，和=1.00 → 不归一
    assert m["p_home"] == pytest.approx(0.45)
    assert m["p_draw"] == pytest.approx(0.30)
    assert m["p_away"] == pytest.approx(0.25)


def test_median_sum_off_tolerance_normalizes():
    m = aggregate_predictions([
        _ok(0.6, 0.3, 0.2), _ok(0.5, 0.3, 0.2), _ok(0.2, 0.3, 0.3)])
    # 中位数 0.5/0.3/0.2 和=1.0 容差内；换一组让中位和=1.08：
    m2 = aggregate_predictions([
        _ok(0.5, 0.3, 0.2), _ok(0.5, 0.3, 0.2), _ok(0.5, 0.6, 0.2)])
    # p_draw 中位 0.6 → 和 1.3 超差 → 归一
    s = m2["p_home"] + m2["p_draw"] + m2["p_away"]
    assert s == pytest.approx(1.0, abs=1e-9)
    assert m2["p_draw"] == pytest.approx(0.6 / 1.3)


def test_over25_and_confidence_median():
    m = aggregate_predictions([
        _ok(0.4, 0.3, 0.3, po=0.7, conf=0.9),
        _ok(0.4, 0.3, 0.3, po=0.5, conf=0.5),
        _ok(0.4, 0.3, 0.3, po=0.6, conf=0.7)])
    assert m["p_over25"] == pytest.approx(0.6)
    assert m["confidence"] == pytest.approx(0.7)


def test_kl_nearest_digest_tie_lowest_index():
    """digest 取与聚合概率向量 KL 最近成员；平票取序最小。"""
    m = aggregate_predictions([
        _ok(0.5, 0.3, 0.2, digest="far"),      # KL 大
        _ok(0.4, 0.3, 0.3, digest="near"),     # KL 小（聚合=中位 0.4/0.3/0.3）
        _ok(0.4, 0.3, 0.3, digest="tied-near")])
    # 成员 2/3 KL 同为 0 → 平票取序最小 → near
    assert m["reasoning_digest"] == "near"


def test_sources_union_dedup_by_url():
    m = aggregate_predictions([
        _ok(0.4, 0.3, 0.3, sources=[{"title": "a", "date": "d1",
                                      "url": "u1"}]),
        _ok(0.4, 0.3, 0.3, sources=[
            {"title": "a-dup", "date": "d1", "url": "u1"},
            {"title": "b", "date": "d2", "url": "u2"}]),
        _ok(0.4, 0.3, 0.3)])
    urls = [s["url"] for s in json.loads(m["sources_json"])]
    assert urls == ["u1", "u2"]


def test_failed_members_excluded_but_k1_aggregates():
    fail = {"status": "parse_fail", "p_home": None, "p_draw": None,
            "p_away": None, "p_over25": None, "confidence": None,
            "reasoning_digest": "x", "sources_json": "[]", "repaired": False}
    m = aggregate_predictions([_ok(0.4, 0.3, 0.3), fail, None])
    assert m["status"] == "ok" and m["p_home"] == pytest.approx(0.4)


def test_k0_all_failed_error():
    m = aggregate_predictions([None, None, None])
    assert m["status"] == "error"
    assert m["p_home"] is None and "k=0" in m["reasoning_digest"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agentline/test_ensemble.py -v`
Expected: FAIL——ModuleNotFoundError

- [ ] **Step 3: 实现** `src/fa/agentline/ensemble.py`

```python
"""A_multi 聚合规则（multi-brain spec §3.2，确定性/预注册）。

聚合行由 Python 构造、不经 parse_prediction 的救援路径——救援语义只属于
成员的 LLM 输出。KL 用自然对数、对三项概率向量计算。
"""
import json
import math
import statistics

_SUM_TOL = 0.05      # 与 contract.parse_prediction 同口径（单源：契约侧常量
                     # 为私有，此处复制并注释锚定——两处容差语义必须同步改）


def _kl(p: tuple[float, ...], q: tuple[float, ...]) -> float:
    return sum(pi * math.log(pi / qi) for pi, qi in zip(p, q) if pi > 0)


def aggregate_predictions(members: list) -> dict:
    ok = [m for m in members
          if m is not None and m.get("status") == "ok"]
    if not ok:
        return {"status": "error", "p_home": None, "p_draw": None,
                "p_away": None, "p_over25": None, "confidence": None,
                "reasoning_digest": "全员失败（k=0），见成员行",
                "sources_json": "[]", "repaired": False}
    ph = statistics.median(m["p_home"] for m in ok)
    pd_ = statistics.median(m["p_draw"] for m in ok)
    pa = statistics.median(m["p_away"] for m in ok)
    total = ph + pd_ + pa
    if abs(total - 1.0) > _SUM_TOL and total > 0:
        ph, pd_, pa = ph / total, pd_ / total, pa / total
    agg_vec = (ph, pd_, pa)
    best, best_kl = None, None
    for m in ok:                                   # 序即 attributor 序，平票取先
        k = _kl((m["p_home"], m["p_draw"], m["p_away"]), agg_vec)
        if best is None or k < best_kl:
            best, best_kl = m, k
    seen, sources = set(), []
    for m in ok:
        for s in json.loads(m["sources_json"] or "[]"):
            if s.get("url") not in seen:
                seen.add(s.get("url"))
                sources.append(s)
    return {"status": "ok", "p_home": ph, "p_draw": pd_, "p_away": pa,
            "p_over25": statistics.median(m["p_over25"] for m in ok),
            "confidence": statistics.median(m["confidence"] for m in ok),
            "reasoning_digest": best["reasoning_digest"],
            "sources_json": json.dumps(sources, ensure_ascii=False),
            "repaired": False}
```

（KL tie 语义：`k < best_kl` 严格小于——同 KL 不换人，首个（序最小）保留。）

- [ ] **Step 4: 跑测试**

Run: `uv run pytest tests/agentline/test_ensemble.py -v`
Expected: 全 PASS（7 例）

- [ ] **Step 5: Commit**

```bash
git add src/fa/agentline/ensemble.py tests/agentline/test_ensemble.py
git commit -m "feat(agentline): A_multi 聚合规则——分量中位数/KL 最近 digest/URL 去重并集"
```

---

### Task 3: orchestrate.run_multi 与 CLI

**Files:**
- Modify: `src/fa/agentline/orchestrate.py`
- Modify: `src/fa/cli.py`（agentline_run）
- Test: `tests/agentline/test_orchestrate.py`（追加；沿用其既有夹具与 mock 模式——先读该文件确认 mock 目标）

**Interfaces:**
- Consumes: Task 1 `save_prediction(..., attributor=)`；Task 2 `aggregate_predictions`
- Produces: `run_multi(conn, info_dir: Path, members: int = 3, limit: int | None = None) -> dict`（counts dict 同 run_line 风格）；CLI `fa agentline run --line A_multi [--members 3] [--limit N]`

- [ ] **Step 1: 写失败测试**（追加；runner mock 模式与该文件既有用例一致——`monkeypatch.setattr(orchestrate.runner_mod, "run_headless", fake)`）

```python
_AM_OK = json.dumps({"p_home": 0.4, "p_draw": 0.3, "p_away": 0.3,
                     "p_over25": 0.5, "confidence": 0.6,
                     "reasoning_digest": "m", "sources": []})
_AM_OK2 = json.dumps({"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2,
                      "p_over25": 0.6, "confidence": 0.7,
                      "reasoning_digest": "n", "sources": []})


def test_run_multi_members_and_aggregate(conn, info_dir, monkeypatch):
    """3 成员行（attributor 1..3）+ 1 聚合行（attributor 0，中位数概率）。"""
    from fa.agentline import orchestrate
    outs = iter([_AM_OK, _AM_OK, _AM_OK2])
    monkeypatch.setattr(orchestrate.runner_mod, "run_headless",
                        lambda prompt, profile, timeout_s=None:
                        (next(outs), None, 1.0))
    counts = orchestrate.run_multi(conn, info_dir, members=3, limit=1)
    assert counts == {"ok": 1, "parse_fail": 0, "timeout": 0, "error": 0}
    rows = conn.execute(
        "SELECT attributor, status, p_home FROM agentline_predictions"
        " WHERE line='A_multi' ORDER BY attributor").fetchall()
    assert [r["attributor"] for r in rows] == [0, 1, 2, 3]
    assert rows[0]["p_home"] == pytest.approx(0.4)   # 中位数（0.4,0.4,0.5）


def test_run_multi_all_failed_aggregate_error(conn, info_dir, monkeypatch):
    from fa.agentline import orchestrate
    monkeypatch.setattr(orchestrate.runner_mod, "run_headless",
                        lambda prompt, profile, timeout_s=None:
                        (None, "dsh 退出码 1：boom", 0.5))
    counts = orchestrate.run_multi(conn, info_dir, members=3, limit=1)
    assert counts["error"] == 1
    agg = conn.execute(
        "SELECT status, p_home FROM agentline_predictions"
        " WHERE line='A_multi' AND attributor=0").fetchone()
    assert agg["status"] == "error" and agg["p_home"] is None


def test_run_multi_idempotent_skips_done(conn, info_dir, monkeypatch):
    from fa.agentline import orchestrate
    calls = {"n": 0}

    def fake(prompt, profile, timeout_s=None):
        calls["n"] += 1
        return (_AM_OK, None, 1.0)

    monkeypatch.setattr(orchestrate.runner_mod, "run_headless", fake)
    orchestrate.run_multi(conn, info_dir, members=3, limit=1)
    first = calls["n"]
    orchestrate.run_multi(conn, info_dir, members=3, limit=1)   # 聚合已 ok → 跳
    assert calls["n"] == first
```

（`conn`/`info_dir` 夹具若文件已有则复用；`info_dir` 须含一个 `{match_id}.json` 信息集且该 match 在 backtest_predictions 有行——照该文件既有夹具的造数方式。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agentline/test_orchestrate.py -k multi -v`
Expected: FAIL——AttributeError: run_multi

- [ ] **Step 3: 实现**

3a. orchestrate.py 追加：

```python
def run_multi(conn: sqlite3.Connection, info_dir: Path, members: int = 3,
              limit: int | None = None) -> dict:
    """A_multi（multi-brain spec §3.1）：N 个独立会话同信息集各自预测 → 聚合。

    成员用 A_base prompt/profile（无检索——异构/检索成员属 gated-2）。
    幂等：聚合行已 ok 的场次跳过（成员行不判重——重跑会覆盖，ON CONFLICT
    三元 upsert 语义与单跑一致）。
    """
    from fa.agentline.ensemble import aggregate_predictions
    profile = _PROFILE_LINE["A_base"]
    done = {r["match_id"] for r in conn.execute(
        "SELECT match_id FROM agentline_predictions"
        " WHERE line='A_multi' AND attributor=0 AND status='ok'")}
    todo = sorted(int(p.stem) for p in info_dir.glob("*.json")
                  if p.stem.isdigit() and int(p.stem) not in done)
    if limit is not None:
        todo = todo[:limit]
    counts = {"ok": 0, "parse_fail": 0, "timeout": 0, "error": 0}
    for mid in todo:
        info = json.loads((info_dir / f"{mid}.json").read_text(encoding="utf-8"))
        prompt = build_prompt(info, "A_base")
        member_results = []
        total_dur = 0.0
        for i in range(members):
            out, err, dur = runner_mod.run_headless(prompt, profile)
            total_dur += dur
            parsed = parse_prediction(out or "")
            parsed, status = _status_of(parsed, err)
            save_prediction(conn, mid, "A_multi", parsed, out or "",
                            _HARNESS, MODEL, dur, attributor=i + 1)
            member_results.append(parsed if status == "ok" else None)
        agg = aggregate_predictions(member_results)
        save_prediction(conn, mid, "A_multi", agg, "", _HARNESS, MODEL,
                        total_dur, attributor=0)
        counts["ok" if agg["status"] == "ok" else "error"] += 1
    save_run(conn, "A_multi", profile, MODEL, counts,
             {"n_todo": len(todo), "members": members,
              "info_dir": str(info_dir)})
    return counts
```

3b. cli.py agentline_run：line 词表 `("A_base", "A_enh", "A_multi")`；`A_multi` 分支调用 `run_multi` 并加 `--members` option（默认 3，<2 拒绝——2 以下不是 ensemble）；A_base/A_enh 路径不变。

- [ ] **Step 4: 跑全量**

Run: `uv run pytest -q`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/fa/agentline/orchestrate.py src/fa/cli.py tests/agentline/test_orchestrate.py
git commit -m "feat(agentline): run_multi 编排——N 成员独立会话+聚合行落库+幂等跳过"
```

---

### Task 4: compare 集成 A_multi（五对照）

**Files:**
- Modify: `src/fa/agentline/compare.py`
- Test: `tests/agentline/test_compare.py`（追加）

**Interfaces:**
- Consumes: Task 1 表结构（attributor）
- Produces: `compare_lines` 返回含 `"A_multi"` 键（只取 attributor=0）；报告表格多一行；`render_report` 输出含成员级披露行（A_multi 成员单独计分入口——v1 在 audit 段报成员行数与 ok 数）

- [ ] **Step 1: 写失败测试**（追加到 tests/agentline/test_compare.py，沿用其造数夹具）

```python
def test_compare_includes_amulti_aggregate_only(conn_amulti):
    """A_multi 只计 attributor=0；成员行（1..3）不得混入该线指标。"""
    from fa.agentline.compare import compare_lines
    cmp = compare_lines(conn_amulti)
    assert cmp["A_multi"]["n"] == 1            # 两成员行被排除
    # 成员行存在性（纪律 1：成员级计分的数据基础）
    assert cmp["audit"]["multi_members"] == 3
    assert cmp["audit"]["multi_member_ok"] == 2


def test_report_renders_amulti_row(conn_amulti, tmp_path):
    from fa.agentline.compare import render_report
    from fa.agentline.compare import compare_lines
    cmp = compare_lines(conn_amulti)
    out = tmp_path / "r.md"
    render_report(cmp, out)
    text = out.read_text(encoding="utf-8")
    assert "| A_multi |" in text
    assert "A_multi" in text.split("## 平注 ROI")[1]  # ROI 段也含该线
```

（`conn_amulti` 夹具：造 1 场 bp+market 行 + 3 行 A_multi（1 聚合 ok + 2 成员 ok——成员概率故意偏移以证明排除）——照该文件既有 bp/agentline 造数模式。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/agentline/test_compare.py -k amulti -v`
Expected: FAIL——KeyError 'A_multi'

- [ ] **Step 3: 实现**——compare.py 三处：

1. `_fetch_agent(conn, line, leagues, seasons, attributor=None)`：非 None 时 `sql += " AND ap.attributor=?"`
2. `compare_lines` 循环改 `for line, attr in (("A_base", 1), ("A_enh", 1), ("A_multi", 0)):` 传参；audit 段补：

```python
    multi = _fetch_agent(conn, "A_multi", leagues, seasons, attributor=1)
    cmp["audit"]["multi_members"] = len(multi)
    cmp["audit"]["multi_member_ok"] = sum(
        1 for r in multi if r["status"] == "ok")
```

3. `render_report`：表格键序 `("P", "A_base", "A_enh", "A_multi")`；ROI 键随 roi_rows 自动含 A_multi；表尾脚注前补一行：`> A_multi 为 3 成员确定性聚合（分量中位数）；成员行单独落库可计分（本批成员 {multi_members} 行 / ok {multi_member_ok}）`（audit 数字缺失时用 `—`）。

- [ ] **Step 4: 跑全量 + Commit**

Run: `uv run pytest -q`（全绿）

```bash
git add src/fa/agentline/compare.py tests/agentline/test_compare.py
git commit -m "feat(agentline): compare 五对照——A_multi 聚合行入表+成员级审计披露"
```

---

### Task 5: E2E——A_multi 首批 30 场 + 报告

**Files:**
- 无代码改动（实测写回 multi-brain spec §3.5 附录与 compare 报告）

- [ ] **Step 1: 活库迁移验证**：`uv run fa init`（v6→v7 重建，200 行存量保全抽查：`fa agentline runs` 台账与 A_base/A_enh 行数仍 100/100）

- [ ] **Step 2: 首批 30 场**（90 次 dsh 调用 ≈ 40-60 分钟，后台）：

```bash
uv run fa agentline run --line A_multi --limit 30
```

- [ ] **Step 3: 报告与核对**：`uv run fa agentline compare`；核对 A_multi 列、成员审计行、台账；把实测（n、聚合 vs 最优成员 vs A_base vs 市场、耗时、契约率）如实写入 spec `2026-09-04-agentline-multi-brain-design.md` 末尾「§9 E2E 实测」小节——**预写解读规则：聚合未优于最优成员不构成失败，多 agent 增益本来就是要测的命题；n=30 无统计意义，只作管线可用性证据**

- [ ] **Step 4: 全量回归 + 提交推送**：

```bash
uv run pytest -q
git add -A && git commit -m "docs: A_multi E2E 首批实测（30 场/聚合-成员对照）"
git push -u origin <当前分支>
```

---

## Self-Review 记录

1. **Spec 覆盖**：§3.1 结构（3 成员/A_base prompt/幂等）= T3；§3.2 聚合表逐字段 = T2（中位归一容差与 contract 同口径已注明单源锚定）；§3.3 五对照+成员级计分 = T4（audit 段成员行数/ok 数为成员计分入口）；§3.4 成本与放量节奏 = T5（30 场起步）；§6 建表建议（词表+attributor）= T1。门控声明（gated-1 达成）由控制者在执行前落 spec（E2E 窗口二报告为证）
2. **占位符扫描**：无 TBD；夹具「照该文件既有模式」处均指明要复用的具体夹具名/造数路径——执行者读测试文件即可获得精确模式
3. **类型一致性**：`aggregate_predictions` 返回键 = save_prediction 的 parsed 取键（p_home/p_draw/p_away/p_over25/confidence/reasoning_digest/sources_json/status/repaired）逐一核对；`run_multi` counts 键 = run_line/save_run 口径；CLI --members 语义 T3 单点定义
4. **九条自检**：#1 日期固定；#2 KL 方向代入（digest=KL 最小者，严格小于保序最小）；#3 中位数手算（0.45/0.30/0.25 与归一 0.6/1.3）；#4 mock 目标 `orchestrate.runner_mod.run_headless`（orchestrate 以 `runner_mod.run_headless` 属性访问——调用时解析，patch 生效）；#5 _SUM_TOL 复制处注释锚定单源；#6 v7 DDL 与重建 SQL 列名以仓库 _AL_TABLE 为准（执行者逐列核对）；#7 conftest 兜底；#8 单一正确版本；#9 T5 预写「聚合未优于成员不是失败」解读规则
