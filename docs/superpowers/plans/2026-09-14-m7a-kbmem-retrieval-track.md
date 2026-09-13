# M7a kbmem 检索轨实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** mem0 作 C 线知识库派生检索索引，B 线新增第五轨 `model_persona_kbmem`（检索 top-k 注入），markdown 权威链路与既有四轨零改动。

**Architecture:** 权威源 = `personas/knowledge/*.md`（人审产物，只读）；JSONL 为规范派生表示，窗口冻结进 git（`evolution/snapshots_mem/w{idx}/mem-{league}.jsonl`）；运行时索引 = JSONL hash 键控缓存目录（`data/mem_cache/<hash>/<league>/`，gitignore，mem0+chroma 本地目录，`add(infer=False)` 零生成式 LLM）；读取 = 每场 `search` top-k 注入 persona prompt，故障降级整文件内联。Retriever 协议 + `FA_MEM_RETRIEVER` env 切换（生产 mem0 / 测试 fake），mock 与实跑同一调用路径。

**Tech Stack:** Python 3.11+ / uv / pytest / SQLite（schema v12）/ mem0ai + chromadb（新主依赖）/ ark OpenAI 兼容 embedder（`doubao-embedding`，env `ARK_API_KEY`）。

**Spec:** `docs/superpowers/specs/2026-09-13-m7a-mem0-kbmem-design.md`（决策 A1–A8、Gate 0 §7、降级 §8、测试 §9）。

## Global Constraints

- league 键与文件映射：`config.PERSONA_FILES = {"E0": "epl.md", "SP1": "laliga.md", "D1": "bundesliga.md", "I1": "seriea.md", "F1": "ligue1.md"}`；锚点语法 `{LG}-{S|T|L}{两位}`。
- `project_root` 一律 `config.project_root()` **属性访问**（from-import 绑定会让 `monkeypatch.setattr(config, "project_root", …)` 失效）。
- 测试环境隔离：conftest `seal_environ` 已密封 `os.environ`；fakе 切换一律 `monkeypatch.setenv("FA_MEM_RETRIEVER", "fake")`，绝不 monkeypatch 模块内符号。
- DDL 单一事实源 = `_BLINE_TABLE`（db.py）；迁移只 re-exec 它，禁止另抄副本（仅测试 fixture 允许手写旧形状）。
- 测试不得依赖当前日期：窗口相关测试一律直接调 `ensure_window_snapshot_mem(字面 idx)`，不走 `current_window_idx()`。
- 生成式 LLM 调用数恒为 0（A3）：`Mem0Retriever` 的 llm config 指向 `http://127.0.0.1:1/`（结构性保证——任何 LLM 调用即时失败，而非静默烧钱）。
- 提交信息中文、逐任务一 commit；**不得为凑绿修改判据语义**；计数类断言更新（4 轨→5 轨）须在 commit 里列明文件。
- 已知既有缺口（**不在本计划修复**，报告中向负责人呈报）：`personas_self_hash` 列无任何写入者（C' 线版本戳未机器落账）。

---

### Task 1: Gate 0 spike——ark × mem0 兼容性实证

**Files:**
- Create: `scripts/spike/m7a_gate0.py`（throwaway，不进主链路）
- Create: `docs/m7a-gate0-report.md`
- Modify: `pyproject.toml`（主依赖加 `mem0ai`、`chromadb`）

**Interfaces:**
- Produces: 依赖就位（`uv run python -c "import mem0, chromadb"` 可用）；结论文档（五项判定 go/no-go）；ark embedder 实测参数（base_url/model 实际可用值写进报告，Task 3 的 env 默认值以此为准）。

- [ ] **Step 1: 取得 ark API key**

优先级：① 环境已有 `ARK_API_KEY` 则直接用；② `arkcli auth apikey` 生成（写入 `~/.ark/` 或按 CLI 指引）；③ 都不可得 → 报告记 BLOCKED，Task 3 起仍按 fake 检索器实施（机制全测），ark 实测留待 key 到位后补跑（诚实呈报，不冒充已验证）。key 只进环境变量，不进代码与提交。

- [ ] **Step 2: 安装依赖**

```bash
uv add mem0ai chromadb
uv run python -c "import mem0, chromadb; print(mem0.__version__ if hasattr(mem0,'__version__') else 'n/a', chromadb.__version__)"
```

记录实际解析版本进报告。

- [ ] **Step 3: 写 spike 脚本**

```python
# scripts/spike/m7a_gate0.py — M7a Gate 0（设计档 §7）。跑法：
#   ARK_API_KEY=... uv run python scripts/spike/m7a_gate0.py
"""五项检查：1) 依赖导入与版本；2) ark embedder add(infer=False)/search 实跑；
3) 零 LLM 调用结构性证明（llm 指向 :1 端口仍成功）；4) export 往返字节相等；
5) chroma 目录跨进程重开持久。"""
import json, shutil, sys, tempfile
from pathlib import Path

ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"
ARK_MODEL = "doubao-embedding"          # 报告记录实际可用模型名（必要时换 large/文本向量版）

ENTRIES = [
    {"anchor": "E0-S01", "league": "E0", "section": "结构性认知", "date": None,
     "ttl_days": None, "text": "阿森纳（枪手）主场控球压制，对中下游队让球盘偏软"},
    {"anchor": "E0-S02", "league": "E0", "section": "结构性认知", "date": None,
     "ttl_days": None, "text": "英超升班马客场普遍保守，大球率低"},
    {"anchor": "D1-S01", "league": "D1", "section": "结构性认知", "date": None,
     "ttl_days": None, "text": "拜仁（Bayern）主场对保级队常出大比分"},
]
def line(e): return json.dumps(e, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def main() -> int:
    from mem0 import Memory
    store = Path(tempfile.mkdtemp(prefix="m7a-gate0-"))
    cfg = {
        "vector_store": {"provider": "chroma",
                         "config": {"path": str(store), "collection_name": "spike"}},
        "embedder": {"provider": "openai",
                     "config": {"openai_base_url": ARK_BASE,
                                "api_key": __import__("os").environ["ARK_API_KEY"],
                                "model": ARK_MODEL}},
        # A3 结构性保证：任何 LLM 调用会即时 Connection refused
        "llm": {"provider": "openai",
                "config": {"openai_base_url": "http://127.0.0.1:1/", "api_key": "unused"}},
    }
    m = Memory.from_config(cfg)
    for e in ENTRIES:
        m.add(line(e), infer=False)                       # 检查 2/3：零 LLM 实证
    results = {q: [r["memory"] for r in m.search(query=q, limit=2)["results"]]
               for q in ("Arsenal 主场风格", "枪手 让球", "拜仁 大比分", "Bayern home")}
    exported = sorted(r["memory"] for r in m.get_all()["results"])
    derived = sorted(line(e) for e in ENTRIES)
    ok_recall = all(any("E0-S01" in x for x in results[q] if isinstance(x, str))
                    for q in ("Arsenal 主场风格", "枪手 让球")) \
        and any("D1-S01" in x for x in results["拜仁 大比分"] + results["Bayern home"])
    ok_roundtrip = exported == derived                    # 检查 4
    m2 = Memory.from_config(cfg)                          # 检查 5：重开同目录
    ok_reopen = sorted(r["memory"] for r in m2.get_all()["results"]) == derived
    print(json.dumps({"recall": results, "ok_recall": ok_recall,
                      "ok_roundtrip": ok_roundtrip, "ok_reopen": ok_reopen},
                     ensure_ascii=False, indent=1))
    shutil.rmtree(store, ignore_errors=True)
    return 0 if (ok_recall and ok_roundtrip and ok_reopen) else 1

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 跑 spike，写报告**

```bash
ARK_API_KEY=... uv run python scripts/spike/m7a_gate0.py; echo "exit=$?"
```

`docs/m7a-gate0-report.md` 记录：五项判定、实际模型名、召回明细、结论（go / 部分 go 附限制 / no-go→回方案重议）。若 mem0 `from_config` 拒绝缺 llm 或 metadata 行为异常，报告记录实际 API 形态与绕法（**只改 Task 3 的 Mem0Retriever，不改协议**）。

- [ ] **Step 5: Commit**

```bash
git add scripts/spike/m7a_gate0.py docs/m7a-gate0-report.md pyproject.toml uv.lock
git commit -m "spike(m7a): Gate 0 ark×mem0 兼容性实证——五项判定与依赖就位"
```

---

### Task 2: evolve_mem.snapshot——JSONL 派生 / 窗口冻结 / 版本戳

**Files:**
- Create: `src/fa/evolve_mem/__init__.py`、`src/fa/evolve_mem/snapshot.py`
- Test: `tests/evolve_mem/__init__.py`、`tests/evolve_mem/conftest.py`、`tests/evolve_mem/test_snapshot.py`

**Interfaces:**
- Consumes: `fa.evolve.knowledge.kb_path / parse_kb`（签名见 src/fa/evolve/knowledge.py:62,231）、`fa.evolve.windows.current_window_idx`。
- Produces（后续任务依赖的精确签名）:
  - `entry_dicts(league: str, text: str) -> list[dict]`（键：anchor/league/section/date/ttl_days/text）
  - `jsonl_bytes(entries: list[dict]) -> bytes`；`parse_jsonl_bytes(b: bytes) -> list[dict]`
  - `derive_live_jsonl() -> dict[str, bytes]`；`snapshot_dir_mem(idx: int) -> Path`；`window_jsonl_path(idx: int, league: str) -> Path`（文件名 `mem-{league}.jsonl`）
  - `ensure_window_snapshot_mem(idx: int) -> Path`；`ensure_current_snapshot_mem() -> int`
  - `window_mem_bytes(idx: int, league: str) -> bytes | None`
  - `jsonl_map_hash(jsonl_map: dict[str, bytes]) -> str`；`personas_mem_consumed_hash(kb_mem_idx: int) -> str`

- [ ] **Step 1: 写 conftest 与失败测试**

```python
# tests/evolve_mem/conftest.py
"""evolve_mem 测试基线：隔离 root + fake 检索器（env 切换，同路径不同实现）。"""
import pytest
from fa import config

@pytest.fixture
def mem_root(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    monkeypatch.setenv("FA_MEM_RETRIEVER", "fake")
    (tmp_path / "personas").mkdir()
    return tmp_path

KB_E0 = """<!-- kb: league=E0 -->
# E0 知识库

## 结构性认知
- [E0-S01] 阿森纳主场控球压制
- [E0-S02] 升班马客场保守

## 时效
- [E0-T01|2026-09-01|90d] 某队伤停潮

## 教训
- [E0-L01|2026-09-10] 某教训条目
"""

def write_kb(root, league="E0", text=KB_E0):
    from fa.config import PERSONA_FILES
    d = root / "personas" / "knowledge"
    d.mkdir(parents=True, exist_ok=True)
    (d / PERSONA_FILES[league]).write_text(text, encoding="utf-8")
```

```python
# tests/evolve_mem/test_snapshot.py
import json

from fa.evolve import EvolutionError
from fa.evolve_mem import snapshot as S
from tests.evolve_mem.conftest import write_kb

def test_entry_dicts_and_jsonl_roundtrip(mem_root):
    write_kb(mem_root)
    entries = S.entry_dicts("E0", (mem_root / "personas/knowledge/epl.md").read_text())
    assert [e["anchor"] for e in entries] == ["E0-L01", "E0-S01", "E0-S02", "E0-T01"]  # 排序键=anchor
    b = S.jsonl_bytes(entries)
    assert S.parse_jsonl_bytes(b) == entries            # 字节往返无损
    assert b.count(b"\n") == 4

def test_jsonl_bytes_empty():
    assert S.jsonl_bytes([]) == b""

def test_derive_live_jsonl_only_existing(mem_root):
    write_kb(mem_root)
    live = S.derive_live_jsonl()
    assert set(live) == {"E0"}                          # 无文件的联赛不出现
    assert b"E0-S01" in live["E0"]

def test_derive_raises_on_broken_syntax(mem_root):
    write_kb(mem_root, text="## 结构性认知\n- [bad anchor] x\n")
    try:
        S.derive_live_jsonl()
        raise AssertionError("应抛 EvolutionError")
    except EvolutionError:
        pass

def test_ensure_window_snapshot_freezes_and_idempotent(mem_root):
    write_kb(mem_root)
    dest = S.ensure_window_snapshot_mem(3)
    f = dest / "mem-E0.jsonl"
    assert f.is_file() and b"E0-S01" in f.read_bytes()
    f.write_bytes(f.read_bytes() + b'{"anchor":"E0-S99"}\n')   # 篡改冻结件
    assert S.ensure_window_snapshot_mem(3) == dest              # 已存在即复用，不覆盖
    assert b"E0-S99" in f.read_bytes()
    # 新窗口重新派生（不受上窗篡改影响——源是活 markdown）
    dest4 = S.ensure_window_snapshot_mem(4)
    assert b"E0-S99" not in (dest4 / "mem-E0.jsonl").read_bytes()

def test_snapshot_no_kb_is_empty_dir(mem_root):
    dest = S.ensure_window_snapshot_mem(1)
    assert dest.is_dir() and list(dest.iterdir()) == []
    assert S.window_mem_bytes(1, "E0") is None

def test_personas_mem_consumed_hash_covers_personas_and_snapshot(mem_root):
    write_kb(mem_root)
    (mem_root / "personas" / "analyst.md").write_text("p", encoding="utf-8")
    S.ensure_window_snapshot_mem(2)
    h1 = S.personas_mem_consumed_hash(2)
    (mem_root / "personas" / "analyst.md").write_text("p2", encoding="utf-8")
    assert S.personas_mem_consumed_hash(2) != h1        # 人格文件变化→戳变
    S.ensure_window_snapshot_mem(5)                     # 新快照内容同→按内容定
    (mem_root / "evolution/snapshots_mem/w5/mem-E0.jsonl").write_bytes(b"x")
    assert S.personas_mem_consumed_hash(5) != S.personas_mem_consumed_hash(2)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/evolve_mem/test_snapshot.py -v` → Expected: FAIL（ModuleNotFoundError: fa.evolve_mem）

- [ ] **Step 3: 实现**

```python
# src/fa/evolve_mem/__init__.py
"""M7a kbmem：mem0 派生检索索引（设计档 2026-09-13-m7a-mem0-kbmem-design.md）。

markdown 权威源不动；本包只做三件事：JSONL 规范派生与窗口冻结（snapshot）、
hash 键控索引与对账（index）、按场检索与内联降级（retrieve）。agent 无状态
（M6 D1）——mem0 是外置检索视图，不是 agent 记忆。
"""


class EvolveMemError(Exception):
    """kbmem 检索链路错误（调用方降级，绝不炸 run——设计档 §8）。"""
```

```python
# src/fa/evolve_mem/snapshot.py
"""JSONL 派生、窗口冻结、版本戳（设计档 §4/A4/A5）。

权威源 = 活 ``personas/knowledge/*.md``（只读）。JSONL = 规范派生表示：
条目按 anchor 排序、每行紧凑 JSON（sort_keys、ensure_ascii=False）——
既是窗口冻结载体（git 版本化，回滚 = git revert），也是索引对账基准。
冻结机械化与 C 线同款：tmp 目录 + ``os.replace``（M6 终审 F4）。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil

from fa import config
from fa.evolve import windows as _windows
from fa.evolve.knowledge import kb_path, parse_kb


def jsonl_name(league: str) -> str:
    return f"mem-{league}.jsonl"


def entry_dicts(league: str, text: str) -> list[dict]:
    kb = parse_kb(text, league)
    return [{"anchor": e.anchor, "league": league, "section": e.section,
             "date": e.date, "ttl_days": e.ttl_days, "text": e.text}
            for e in sorted(kb.entries, key=lambda e: e.anchor)]


def jsonl_bytes(entries: list[dict]) -> bytes:
    lines = [json.dumps(e, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":")) for e in entries]
    return ("\n".join(lines) + "\n").encode("utf-8") if lines else b""


def parse_jsonl_bytes(b: bytes) -> list[dict]:
    return [json.loads(ln) for ln in b.decode("utf-8").splitlines() if ln.strip()]


def derive_live_jsonl() -> dict[str, bytes]:
    """活 knowledge 树 → {league: JSONL bytes}。语法破损抛 EvolutionError
    （写入侧 sync 拦截；读取侧调用方按该轨快照失败降级——设计档 §8）。"""
    out: dict[str, bytes] = {}
    for league in config.PERSONA_FILES:
        p = kb_path(league)
        if p.is_file():
            out[league] = jsonl_bytes(
                entry_dicts(league, p.read_text(encoding="utf-8")))
    return out


def snapshot_dir_mem(idx: int):
    return config.project_root() / "evolution" / "snapshots_mem" / f"w{idx}"


def window_jsonl_path(idx: int, league: str):
    return snapshot_dir_mem(idx) / jsonl_name(league)


def ensure_window_snapshot_mem(idx: int):
    """本窗首调用者把「活 markdown 派生 JSONL」原子拷入（幂等；已存在即复用）。

    残留 tmp（含上次中断的）一律先清；派生失败（语法破损）上抛由调用方降级。
    无知识文件 → 目录空 = 空知识库（合法态，与 C 线同）。
    """
    dest = snapshot_dir_mem(idx)
    if dest.exists():
        return dest
    for stale in dest.parent.glob(f"w{idx}.tmp-*"):
        shutil.rmtree(stale, ignore_errors=True)
    tmp = dest.parent / f"w{idx}.tmp-{os.getpid()}"
    tmp.mkdir(parents=True)
    try:
        for league, b in sorted(derive_live_jsonl().items()):
            (tmp / jsonl_name(league)).write_bytes(b)
        if dest.exists():
            return dest
        os.replace(tmp, dest)
        return dest
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ensure_current_snapshot_mem() -> int:
    idx = _windows.current_window_idx()
    ensure_window_snapshot_mem(idx)
    return idx


def window_mem_bytes(idx: int, league: str) -> bytes | None:
    p = window_jsonl_path(idx, league)
    return p.read_bytes() if p.is_file() else None


def jsonl_map_hash(jsonl_map: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for league, b in sorted(jsonl_map.items()):
        h.update(league.encode())
        h.update(b"\0")
        h.update(b)
        h.update(b"\x1e")
    return h.hexdigest()


def personas_mem_consumed_hash(kb_mem_idx: int) -> str:
    """run 实际消费的 mem 工件 hash（D6）：活人格文件 + 本窗 mem JSONL 快照。
    与 personas_consumed_hash 同方案（稳定键排序 ``键\\0全文\\x1e``），
    同「先快照后取 hash」纪律——调用方保证顺序。"""
    root = config.project_root()
    virtual: list[tuple[str, object]] = []
    personas_dir = root / "personas"
    if personas_dir.is_dir():
        virtual.extend((f"personas/{p.name}", p)
                       for p in sorted(personas_dir.glob("*.md")) if p.is_file())
    snap = snapshot_dir_mem(kb_mem_idx)
    if snap.is_dir():
        virtual.extend((f"knowledge_mem@w{kb_mem_idx}/{p.name}", p)
                       for p in sorted(snap.glob("mem-*.jsonl")) if p.is_file())
    h = hashlib.sha256()
    for key, p in sorted(virtual):
        h.update(key.encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\x1e")
    return h.hexdigest()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/evolve_mem/test_snapshot.py -v` → Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/fa/evolve_mem/ tests/evolve_mem/
git commit -m "feat(evolve_mem): JSONL 规范派生+窗口冻结+mem 版本戳（M7a Task2）"
```

---

### Task 3: evolve_mem.index——Retriever 协议 / hash 缓存 / 对账 / sync

**Files:**
- Create: `src/fa/evolve_mem/index.py`
- Test: `tests/evolve_mem/test_index.py`

**Interfaces:**
- Consumes: Task 2 全部函数。
- Produces:
  - `TOP_K_DEFAULT = 8`
  - `Retriever` 协议：`index_entries(entries: list[dict], store_dir: Path) -> None`；`search(store_dir: Path, query: str, top_k: int) -> list[dict]`；`export_entries(store_dir: Path) -> list[dict]`
  - `FakeRetriever` / `Mem0Retriever`；`get_retriever() -> Retriever`（env `FA_MEM_RETRIEVER=fake|mem0`，默认 mem0）
  - `index_store_dir(hash_: str, league: str) -> Path`；`ensure_index(jsonl_map: dict[str, bytes]) -> str`（返回 hash）
  - `reconcile(derived: bytes, exported: list[dict]) -> bool`
  - `sync_all(retriever: Retriever | None = None) -> dict`（形如 `{"ok": bool, "error": str | None, "leagues": {lg: {"entries": int, "ok": bool, "error": str | None}}}`）

- [ ] **Step 1: 写失败测试**

```python
# tests/evolve_mem/test_index.py
from pathlib import Path

from fa.evolve_mem import index as I
from fa.evolve_mem.snapshot import entry_dicts, jsonl_bytes, parse_jsonl_bytes
from tests.evolve_mem.conftest import KB_E0, write_kb

E = lambda a, t, **kw: {"anchor": a, "league": "E0", "section": "结构性认知",
                        "date": None, "ttl_days": None, "text": t, **kw}

def test_fake_retriever_roundtrip_and_topk(tmp_path):
    r = I.FakeRetriever()
    entries = [E("E0-S01", "阿森纳主场压制"), E("E0-S02", "升班马保守"), E("E0-S03", "裁判尺度")]
    r.index_entries(entries, tmp_path)
    assert r.export_entries(tmp_path) == entries
    got = r.search(tmp_path, "E0 阿森纳 home", 2)
    assert len(got) == 2 and got[0]["anchor"] == "E0-S01"   # 词法命中排前
    assert [e["anchor"] for e in r.search(tmp_path, "zzz", 8)] == \
        ["E0-S01", "E0-S02", "E0-S03"]                      # 无命中→锚点稳定序

def test_ensure_index_cache_hit_and_rebuild(mem_root):
    write_kb(mem_root)
    live = {"E0": jsonl_bytes(entry_dicts("E0", KB_E0))}
    h1 = I.ensure_index(live)
    store = I.index_store_dir(h1, "E0")
    assert (store / "READY").is_file()
    (store / "entries.jsonl").write_bytes(b"tampered")     # 缓存内容被篡改
    assert I.ensure_index(live) == h1                       # READY 即复用（不重建）
    live2 = {**live, "E0": jsonl_bytes(entry_dicts("E0", KB_E0 + "- [E0-S09] 新条目\n"))}
    h2 = I.ensure_index(live2)
    assert h2 != h1 and (I.index_store_dir(h2, "E0") / "READY").is_file()

def test_reconcile_detects_drift():
    derived = jsonl_bytes([E("E0-S01", "a"), E("E0-S02", "b")])
    assert I.reconcile(derived, [E("E0-S01", "a"), E("E0-S02", "b")])
    assert not I.reconcile(derived, [E("E0-S01", "a 被改")])       # 文本漂移
    assert not I.reconcile(derived, [E("E0-S01", "a")])            # 条目缺失

def test_sync_all_ok_and_drift(mem_root, monkeypatch):
    write_kb(mem_root)
    class Tamper:
        def __init__(self, inner): self.inner = inner
        def index_entries(self, entries, store_dir):
            self.inner.index_entries([replace_text(entries[0], "被污染")], store_dir)
        def search(self, *a): return self.inner.search(*a)
        def export_entries(self, store_dir): return self.inner.export_entries(store_dir)
    def replace_text(e, t): return {**e, "text": t}
    good = I.sync_all(retriever=I.FakeRetriever())
    assert good["ok"] and good["leagues"]["E0"]["ok"] and good["leagues"]["E0"]["entries"] == 4
    bad = I.sync_all(retriever=Tamper(I.FakeRetriever()))
    assert not bad["ok"] and not bad["leagues"]["E0"]["ok"]

def test_sync_all_retriever_init_failure_reported(mem_root, monkeypatch):
    write_kb(mem_root)
    class Broken:
        def __init__(self): raise RuntimeError("ARK_API_KEY 未配置")
    monkeypatch.setattr(I, "get_retriever", Broken)          # 单点切换缝
    out = I.sync_all()
    assert out["ok"] is False and "ARK_API_KEY" in out["error"]

def test_get_retriever_env_switch(monkeypatch):
    monkeypatch.setenv("FA_MEM_RETRIEVER", "fake")
    assert isinstance(I.get_retriever(), I.FakeRetriever)
    monkeypatch.setenv("FA_MEM_RETRIEVER", "MEM0")
    try:
        I.get_retriever()
        raise AssertionError("无 ARK_API_KEY 应抛 EvolveMemError")
    except I.EvolveMemError:
        pass
```

- [ ] **Step 2: 确认失败**

Run: `uv run pytest tests/evolve_mem/test_index.py -v` → Expected: FAIL（无 fa.evolve_mem.index）

- [ ] **Step 3: 实现**

```python
# src/fa/evolve_mem/index.py
"""hash 键控索引、Retriever 协议与对账（设计档 §4/§7；A1/A3/A4）。

「活索引」不是常驻存储：索引 = JSONL 的 hash 键控缓存目录
（``data/mem_cache/<hash>/<league>/``，gitignore），随时可删可重建。
memory 文本 = 规范 JSON 行本身——导出/检索即原文，零 metadata 版本耦合，
对账 = 字节比较（设计档 §8 对账定义）。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Protocol

from fa import config
from fa.evolve_mem import EvolveMemError
from fa.evolve_mem.snapshot import (derive_live_jsonl, jsonl_bytes,
                                    jsonl_map_hash, parse_jsonl_bytes)

TOP_K_DEFAULT = 8                      # 设计档 §15：config 化，初值 8

_ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"
_ARK_MODEL = "doubao-embedding"        # Gate 0 报告实证后如换名在此改
_DEAD_LLM = "http://127.0.0.1:1/"      # A3 结构性保证：任何 LLM 调用即时失败


class Retriever(Protocol):
    def index_entries(self, entries: list[dict], store_dir: Path) -> None: ...
    def search(self, store_dir: Path, query: str, top_k: int) -> list[dict]: ...
    def export_entries(self, store_dir: Path) -> list[dict]: ...


class FakeRetriever:
    """确定性测试实现：无语义。query 词命中者排前、其余按锚点序；top_k 截断。"""

    def index_entries(self, entries, store_dir):
        store_dir.mkdir(parents=True, exist_ok=True)
        (store_dir / "entries.jsonl").write_bytes(jsonl_bytes(entries))

    def search(self, store_dir, query, top_k):
        entries = parse_jsonl_bytes((store_dir / "entries.jsonl").read_bytes())
        toks = {t for t in query.lower().split() if t}
        def key(e):
            hay = " ".join(str(v) for v in e.values()).lower()
            return (0 if any(t in hay for t in toks) else 1, e["anchor"])
        return sorted(entries, key=key)[:top_k]

    def export_entries(self, store_dir):
        return parse_jsonl_bytes((store_dir / "entries.jsonl").read_bytes())


class Mem0Retriever:
    """生产实现：mem0 OSS（chroma 本地目录 + ark OpenAI 兼容 embedder，
    ``add(infer=False)``）。mem0 惰性 import；配置缺失抛 EvolveMemError。"""

    def __init__(self) -> None:
        try:
            from mem0 import Memory
        except ImportError as exc:
            raise EvolveMemError(f"mem0 未安装：{exc}") from exc
        if not os.environ.get("ARK_API_KEY"):
            raise EvolveMemError("ARK_API_KEY 未配置（mem embedder）")
        self._Memory = Memory
        self._base = os.environ.get("FA_MEM_EMBED_BASE_URL", _ARK_BASE)
        self._model = os.environ.get("FA_MEM_EMBED_MODEL", _ARK_MODEL)

    def _memory(self, store_dir: Path):
        return self._Memory.from_config({
            "vector_store": {"provider": "chroma",
                             "config": {"path": str(store_dir),
                                        "collection_name": "kbmem"}},
            "embedder": {"provider": "openai",
                         "config": {"openai_base_url": self._base,
                                    "api_key": os.environ["ARK_API_KEY"],
                                    "model": self._model}},
            "llm": {"provider": "openai",
                    "config": {"openai_base_url": _DEAD_LLM,
                               "api_key": "unused"}},
        })

    def index_entries(self, entries, store_dir):
        m = self._memory(store_dir)
        for e in entries:
            m.add(_line(e), infer=False)

    def search(self, store_dir, query, top_k):
        res = self._memory(store_dir).search(query=query, limit=top_k)
        return _parse_results(res)[:top_k]

    def export_entries(self, store_dir):
        return _parse_results(self._memory(store_dir).get_all())


def _line(e: dict) -> str:
    import json
    return json.dumps(e, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_results(res) -> list[dict]:
    import json
    items = res.get("results", []) if isinstance(res, dict) else list(res)
    out = []
    for r in items:
        text = r.get("memory") if isinstance(r, dict) else str(r)
        out.append(json.loads(text))
    return out


def get_retriever() -> Retriever:
    """唯一切换缝：env ``FA_MEM_RETRIEVER=fake|mem0``（默认 mem0）。
    测试与生产同一调用路径，仅实现不同（项目 mock 哲学，C1 同源）。"""
    if os.environ.get("FA_MEM_RETRIEVER", "").strip().lower() == "fake":
        return FakeRetriever()
    return Mem0Retriever()


def index_store_dir(hash_: str, league: str) -> Path:
    return config.project_root() / "data" / "mem_cache" / hash_ / league


def _build_league(r: Retriever, hash_: str, league: str, b: bytes) -> None:
    store = index_store_dir(hash_, league)
    if (store / "READY").is_file():
        return
    tmp = store.parent / f"{store.name}.tmp-{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        r.index_entries(parse_jsonl_bytes(b), tmp)
        (tmp / "READY").write_text(hash_)
        if store.exists():
            shutil.rmtree(store, ignore_errors=True)
        os.replace(tmp, store)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ensure_index(jsonl_map: dict[str, bytes]) -> str:
    """为 {league: bytes} 确保缓存索引就绪，返回 hash。幂等（READY 即复用）。"""
    r = get_retriever()
    h = jsonl_map_hash(jsonl_map)
    for league, b in jsonl_map.items():
        _build_league(r, h, league, b)
    return h


def reconcile(derived: bytes, exported: list[dict]) -> bool:
    """对账：索引导出条目集与权威派生条目集，规范序列化后逐字节相等。"""
    return jsonl_bytes(sorted(exported, key=lambda e: e["anchor"])) == derived


def sync_all(retriever: Retriever | None = None) -> dict:
    """sync-mem 主逻辑：活树派生 → 逐联赛重建缓存索引 → 导出对账。

    逐联赛容错（单联赛不炸整体，C 线 prune_live_files 先例）；retriever
    初始化失败（缺 key/依赖）整体报 error 不抛——调用方记事件（设计档 §8
    写入侧静默停摆）。"""
    live = derive_live_jsonl()            # EvolutionError 上抛（语法破损=权威源问题）
    try:
        r = retriever if retriever is not None else get_retriever()
    except EvolveMemError as exc:
        return {"ok": False, "error": str(exc),
                "leagues": {lg: {"entries": 0, "ok": False, "error": str(exc)}
                            for lg in live}}
    h = jsonl_map_hash(live)
    leagues = {}
    for league, b in live.items():
        entry = {"entries": len(parse_jsonl_bytes(b)), "ok": False, "error": None}
        try:
            _build_league(r, h, league, b)
            entry["ok"] = reconcile(b, r.export_entries(index_store_dir(h, league)))
            if not entry["ok"]:
                entry["error"] = "drift：索引导出与权威派生不一致"
        except EvolveMemError as exc:
            entry["error"] = str(exc)
        leagues[league] = entry
    return {"ok": all(v["ok"] for v in leagues.values()), "error": None,
            "leagues": leagues}
```

注意：`sync_all` 里 `get_retriever` 失败用 `monkeypatch.setattr(I, "get_retriever", Broken)` 可测（函数内属性访问 `get_retriever()`，非 from-import 绑定——Global Constraints 第 2 条的同族纪律：**模块内自引用也走属性语义**。实现时 `get_retriever()` 调用写作直接函数名即可被 `setattr(I, ...)` 替换，因为测试 patch 的是 `I.get_retriever` 而实现通过模块全局名解析——已按此写）。

- [ ] **Step 4: 确认通过**

Run: `uv run pytest tests/evolve_mem/ -v` → Expected: 全部 passed（含 Task 2 的 7 个）

- [ ] **Step 5: Commit**

```bash
git add src/fa/evolve_mem/index.py tests/evolve_mem/test_index.py
git commit -m "feat(evolve_mem): Retriever 协议+hash 缓存索引+对账+sync_all（M7a Task3）"
```

---

### Task 4: evolve_mem.retrieve——按场检索与内联降级文本

**Files:**
- Create: `src/fa/evolve_mem/retrieve.py`
- Test: `tests/evolve_mem/test_retrieve.py`

**Interfaces:**
- Consumes: Task 2/3。
- Produces:
  - `build_query(input_obj: dict) -> str`（input_obj = persona/contract.build_input 的输出）
  - `render_line(e: dict) -> str`（`[锚点|日期] 文本`）
  - `window_mem_topk(idx: int, league: str, query: str, top_k: int = TOP_K_DEFAULT) -> list[str]`
  - `window_mem_inline(idx: int, league: str) -> str | None`

- [ ] **Step 1: 写失败测试**

```python
# tests/evolve_mem/test_retrieve.py
from fa.evolve_mem import retrieve as R
from fa.evolve_mem.snapshot import ensure_window_snapshot_mem
from tests.evolve_mem.conftest import write_kb

INPUT = {"league": "E0",
         "match": {"kickoff_utc": "2026-09-20T15:00:00Z", "home": "Arsenal",
                   "away": "Everton"},
         "candidates": [{"market": "H"}, {"market": "O2.5"}],
         "model_summary": {"p_home": .5, "p_draw": .3, "p_away": .2}}

def test_build_query():
    assert R.build_query(INPUT) == "E0 Arsenal Everton H O2.5"

def test_render_line_with_and_without_date():
    assert R.render_line({"anchor": "E0-T01", "date": "2026-09-01",
                          "text": "x"}) == "[E0-T01|2026-09-01] x"
    assert R.render_line({"anchor": "E0-S01", "date": None,
                          "text": "y"}) == "[E0-S01] y"

def test_topk_and_inline_and_empty(mem_root):
    write_kb(mem_root)
    idx = 3
    ensure_window_snapshot_mem(idx)
    lines = R.window_mem_topk(idx, "E0", "E0 阿森纳 主场", top_k=2)
    assert len(lines) == 2 and lines[0].startswith("[E0-S01]")
    inline = R.window_mem_inline(idx, "E0")
    assert inline is not None and inline.count("\n") == 3      # 4 条目
    assert R.window_mem_inline(idx, "SP1") is None             # 无该联赛=空知识库
    assert R.window_mem_topk(idx, "SP1", "q") == []

def test_topk_raises_when_retriever_broken(mem_root, monkeypatch):
    write_kb(mem_root)
    ensure_window_snapshot_mem(3)
    class Broken:
        def __init__(self): raise Exception("boom")
    monkeypatch.setenv("FA_MEM_RETRIEVER", "mem0")            # 走 Mem0Retriever
    monkeypatch.delenv("ARK_API_KEY", raising=False)          # 缺 key → 抛错
    try:
        R.window_mem_topk(3, "E0", "q")
        raise AssertionError("应上抛供调用方降级")
    except Exception:
        pass
```

- [ ] **Step 2: 确认失败** → Run: `uv run pytest tests/evolve_mem/test_retrieve.py -v` → FAIL

- [ ] **Step 3: 实现**

```python
# src/fa/evolve_mem/retrieve.py
"""按场检索与内联降级文本（设计档 §4 读取侧/§8 降级一级）。"""
from __future__ import annotations

from fa.evolve_mem.index import (TOP_K_DEFAULT, ensure_index,
                                 get_retriever, index_store_dir)
from fa.evolve_mem.snapshot import (parse_jsonl_bytes, window_mem_bytes)


def build_query(input_obj: dict) -> str:
    m = input_obj["match"]
    markets = " ".join(sorted({c["market"] for c in input_obj["candidates"]}))
    return f'{input_obj["league"]} {m["home"]} {m["away"]} {markets}'


def render_line(e: dict) -> str:
    date = f"|{e['date']}" if e.get("date") else ""
    return f"[{e['anchor']}{date}] {e['text']}"


def window_mem_topk(idx: int, league: str, query: str,
                    top_k: int = TOP_K_DEFAULT) -> list[str]:
    """冻结 JSONL → hash 缓存索引 → top-k 渲染行。空知识库 = []。
    检索链路任何失败上抛（EvolveMemError 等）——调用方落内联降级（§8）。"""
    b = window_mem_bytes(idx, league)
    if b is None:
        return []
    h = ensure_index({league: b})
    res = get_retriever().search(index_store_dir(h, league), query, top_k)
    return [render_line(e) for e in res]


def window_mem_inline(idx: int, league: str) -> str | None:
    """整文件内联形态（kb 轨同款语义；降级用）。空知识库 = None。"""
    b = window_mem_bytes(idx, league)
    if b is None:
        return None
    lines = [render_line(e) for e in parse_jsonl_bytes(b)]
    return "\n".join(lines) if lines else None
```

- [ ] **Step 4: 确认通过** → `uv run pytest tests/evolve_mem/ -v` → 全 passed

- [ ] **Step 5: Commit**

```bash
git add src/fa/evolve_mem/retrieve.py tests/evolve_mem/test_retrieve.py
git commit -m "feat(evolve_mem): 按场检索 top-k+内联降级文本（M7a Task4）"
```

---

### Task 5: db v12——第五轨枚举 + personas_mem_hash 列

**Files:**
- Modify: `src/fa/db.py`（`SCHEMA_VERSION`、`_BLINE_TABLE` 两处、`_migrate_up` docstring + 新 `if from_v < 12:` 块；若 init_db 有 `.bak` 备份命名随版本走则同步）
- Test: `tests/test_db.py`（追加）

**Interfaces:**
- Produces: `recommendations.strategy` CHECK 含 `model_persona_kbmem`；新列 `personas_mem_hash TEXT`（INSERT-only 语义同 personas_hash）；`SCHEMA_VERSION == 12`。

- [ ] **Step 1: 写失败测试（追加到 tests/test_db.py，沿用既有迁移测试风格）**

```python
_V11_RECS = """CREATE TABLE recommendations (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, fixture_id INTEGER NOT NULL,
    strategy TEXT NOT NULL CHECK (strategy IN ('model_only', 'model_persona',
        'model_persona_nokb', 'model_persona_kb_self')),
    market TEXT NOT NULL, phase TEXT NOT NULL, model_p REAL NOT NULL,
    market_p REAL NOT NULL, best_odds REAL NOT NULL, bookmaker TEXT NOT NULL,
    edge REAL NOT NULL, ev REAL NOT NULL, kelly_stake_frac REAL NOT NULL,
    verdict TEXT, confidence_delta REAL, final_stake_frac REAL,
    key_factors TEXT, report_md TEXT,
    personas_hash TEXT, personas_self_hash TEXT, created_at TEXT NOT NULL,
    UNIQUE (fixture_id, market, strategy, phase))"""

def test_v11_migrates_to_v12(tmp_path):
    db = tmp_path / "fa.db"
    conn = sqlite3.connect(db)
    conn.executescript(_V11_RECS)
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version VALUES (11)")
    conn.commit()
    legacy = ("INSERT INTO recommendations (id, run_id, fixture_id, strategy,"
              " market, phase, model_p, market_p, best_odds, bookmaker, edge,"
              " ev, kelly_stake_frac, created_at) VALUES"
              " (1, 1, 1, 'model_persona_kb_self', 'H', 'am', .5, .4, 2.1,"
              " 'b', .1, .1, .05, '2026-09-13')")
    conn.execute(legacy)
    conn.commit()
    conn.close()
    fa.db.init_db(str(db))
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name='recommendations'"
                       ).fetchone()["sql"]
    assert "model_persona_kbmem" in ddl and "personas_mem_hash" in ddl
    row = conn.execute("SELECT personas_self_hash, personas_mem_hash, strategy"
                       " FROM recommendations WHERE id=1").fetchone()
    assert row["strategy"] == "model_persona_kb_self"      # 存量行原样保留
    assert row["personas_mem_hash"] is None                # 「M7a 纪元前」
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 12
    assert not conn.execute("SELECT name FROM sqlite_master"
                            " WHERE name='recommendations_v11'").fetchone()
    # 新枚举可写 + 幂等重跑
    conn.execute("INSERT INTO recommendations (id, run_id, fixture_id, strategy,"
                 " market, phase, model_p, market_p, best_odds, bookmaker, edge,"
                 " ev, kelly_stake_frac, created_at) VALUES"
                 " (2, 1, 2, 'model_persona_kbmem', 'H', 'am', .5, .4, 2.1,"
                 " 'b', .1, .1, .05, '2026-09-13')")
    conn.commit(); conn.close()
    fa.db.init_db(str(db))                                  # 幂等：不炸不重放
```

（置于 `tests/test_db.py` 尾部；`sqlite3` / `fa.db` 已有 import。若既有 `test_migrate_and_fresh_schemas_match` 参数化枚举 from_v 列表含 11，追加 12 的参数化用例照抄其形。）

- [ ] **Step 2: 确认失败** → `uv run pytest tests/test_db.py -k v12 -v` → FAIL（SCHEMA_VERSION 仍 11）

- [ ] **Step 3: 实现（三处编辑 + 一处追加）**

1. `SCHEMA_VERSION = 11` → `SCHEMA_VERSION = 12`
2. `_BLINE_TABLE` 内 CHECK：

```python
        CHECK (strategy IN ('model_only', 'model_persona',
                            'model_persona_nokb',
                            'model_persona_kb_self',
                            'model_persona_kbmem')),  -- §6.6 双轨 + C线nokb + C'线自反思 + M7a kbmem 检索轨
```

3. `_BLINE_TABLE` 列（`personas_self_hash` 行之后）：

```python
    personas_mem_hash  TEXT,           -- M7a：kbmem 轨版本戳（mem JSONL 快照，§12.7）
```

4. `_migrate_up` docstring 追加一行：`v11->v12 重建 recommendations——strategy 扩五轨枚举（加 model_persona_kbmem）、加 personas_mem_hash 列（M7a kbmem 版本戳）；影子表名 _v11；`，并在 v11 块之后、`UPDATE schema_version` 之前追加（整体照抄 v10→v11 块，替换三处名字与列）：

```python
    if from_v < 12:
        # v12：M7a kbmem 检索轨（spec §12.7 增补）。
        # 重建 recommendations——strategy 扩五轨（加 model_persona_kbmem）、
        # 加 personas_mem_hash 列。影子表 _v11（v8/_v7、v9/_v8、v11/_v10 同惯例）。
        cur = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table'"
            " AND name='recommendations'")
        ddl = cur.fetchone()
        stale = ddl is not None and "model_persona_kbmem" not in ddl["sql"]
        if stale:
            shadow = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name='recommendations_v11'").fetchone()
            conn.execute("DROP INDEX IF EXISTS idx_recs_run")
            if conn.in_transaction:
                conn.commit()
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("PRAGMA legacy_alter_table=ON")
            try:
                if shadow is not None:
                    conn.execute("DROP TABLE IF EXISTS recommendations;")
                    conn.execute(
                        "ALTER TABLE recommendations_v11"
                        " RENAME TO recommendations;")
                conn.execute(
                    "ALTER TABLE recommendations RENAME TO recommendations_v11;")
            finally:
                conn.execute("PRAGMA legacy_alter_table=OFF")
                conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(_BLINE_TABLE)     # 新形状：五轨枚举 + personas_mem_hash
            conn.execute(
                "INSERT INTO recommendations (id, run_id, fixture_id, strategy,"
                " market, phase, model_p, market_p, best_odds, bookmaker, edge,"
                " ev, kelly_stake_frac, verdict, confidence_delta, final_stake_frac,"
                " key_factors, report_md, personas_hash, personas_self_hash,"
                " personas_mem_hash, created_at)"
                " SELECT id, run_id, fixture_id, strategy, market, phase,"
                " model_p, market_p, best_odds, bookmaker, edge, ev,"
                " kelly_stake_frac, verdict, confidence_delta, final_stake_frac,"
                " key_factors, report_md, personas_hash, personas_self_hash,"
                " NULL, created_at"
                " FROM recommendations_v11;")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_recs_run"
                " ON recommendations (run_id);")
            conn.execute("DROP TABLE recommendations_v11;")
```

（执行时先 `grep -n "bak" src/fa/db.py`：若 init_db 的自动备份文件名随 `SCHEMA_VERSION` 参数化则自动跟随 v12；若硬编码 v11 字样则同批改为引用常量——单一事实源。）

- [ ] **Step 4: 确认通过** → `uv run pytest tests/test_db.py -v` → 全 passed（既有迁移用例不回归）

- [ ] **Step 5: Commit**

```bash
git add src/fa/db.py tests/test_db.py
git commit -m "feat(db): v12——strategy 五轨枚举+personas_mem_hash 版本戳列（M7a Task5）"
```

---

### Task 6: value.py——STRATEGIES 第五轨 + mem 戳落行

**Files:**
- Modify: `src/fa/pipeline/value.py`（`STRATEGIES` 元组、`generate_recommendations` 签名、`_upsert_recommendation`）
- Modify: `src/fa/pipeline/matchday.py`（`generate_recommendations(...)` 调用点传 `personas_mem_hash`——本任务只改传参占位 `personas_mem_hash=None` 由 Task 7 接真值；**实施顺序上本任务与 Task 7 合并提交亦可，但 STRATEGIES 与戳落行属同一变更原子**）
- Test: 既有 `tests/value/`、`tests/pipeline/` 内硬编码轨数的断言更新（4→5，commit 列明文件）

**Interfaces:**
- Produces: `STRATEGIES` 含 `"model_persona_kbmem"`；`generate_recommendations(..., personas_mem_hash: str | None = None)`；`_upsert_recommendation(..., personas_mem_hash: str | None)`（INSERT-only，DO UPDATE 不含）。

- [ ] **Step 1: 跑既有测试摸清将破的断言**

Run: `uv run pytest tests/value tests/pipeline -q 2>&1 | tail -5`（此时应仍绿）→ grep 轨数断言：`grep -rn "kb_self\|== 4\|len(recs)\|STRATEGIES" tests/value tests/pipeline | head -20`，逐一记下改 4→5 的位置。

- [ ] **Step 2: 改 STRATEGIES 与签名（失败先行的断言更新即测试步）**

```python
STRATEGIES = ("model_only", "model_persona", "model_persona_nokb",
               "model_persona_kb_self", "model_persona_kbmem")
# §6.6 A/B 双轨 + M6 C 线对照轨 + C' 线自反思轨 + M7a kbmem 检索轨
# （mem0 派生索引 top-k 注入，spec §12.7 增补）——paper/render/weekly
# 全部经此单源引用，五轨自动生效。
```

`generate_recommendations` 增参 `personas_mem_hash: str | None = None`，循环内 `_upsert_recommendation(..., personas_hash=personas_hash, personas_mem_hash=personas_mem_hash)`；`_upsert_recommendation` INSERT 列表加 `personas_mem_hash`、VALUES 加一 `?`、参数元组加 `personas_mem_hash`，**DO UPDATE 子句不动**（INSERT-only 语义，docstring 补一句「personas_mem_hash 同 personas_hash：只在 INSERT 生效」）。matchday 调用点暂传 `personas_mem_hash=None`（Task 7 接真值）。

- [ ] **Step 3: 更新硬编码断言并确认全绿**

Run: `uv run pytest tests/value tests/pipeline -v` → 全 passed（断言更新在 commit message 列文件清单）

- [ ] **Step 4: Commit**

```bash
git add src/fa/pipeline/value.py src/fa/pipeline/matchday.py tests/
git commit -m "feat(value): STRATEGIES 扩第五轨 kbmem+personas_mem_hash 落行（M7a Task6）；轨数断言 4→5：<文件清单>"
```

---

### Task 7: matchday 接线——mem 快照 + 戳入 runs.summary

**Files:**
- Modify: `src/fa/pipeline/matchday.py`（`_run` 开头快照块、`generate_recommendations` 调用、`summary` dict）
- Test: `tests/pipeline/test_matchday_mem.py`（新建；沿用 tests/pipeline 既有 fixture 风格）

**Interfaces:**
- Consumes: Task 2 `ensure_current_snapshot_mem` / `personas_mem_consumed_hash`；Task 6 `personas_mem_hash` 参数。
- Produces: `runs.summary` 新键 `personas_mem_hash`（str | None）、`kb_mem_window`（int | None）；`generate_recommendations` 收到真值；快照失败 → 双 None + 该轨降级（apply.py Task 8 消费）。

- [ ] **Step 1: 写失败测试**

```python
# tests/pipeline/test_matchday_mem.py
"""M7a：matchday 接线——mem 快照、personas_mem_hash 入账、失败降级双 None。
（跑 _run 全链依赖 odds/persona mock 较重；此处直测接线函数的既有轻径：
  与 test_matchday 既有用例同构——复用其 fixture；若无轻径则新建
  _wire_mem_snapshot() 纯函数供 _run 与测试共用。）"""
from fa.pipeline import matchday as M

def test_wire_mem_snapshot_ok(mem_root, write_kb):
    write_kb(mem_root)
    idx, h = M._wire_mem_snapshot()
    assert isinstance(idx, int) and h and len(h) == 64

def test_wire_mem_snapshot_broken_kb_degrades(mem_root, write_kb):
    write_kb(mem_root, text="## 结构性认知\n- [bad] x\n")
    idx, h = M._wire_mem_snapshot()
    assert idx is None and h is None
```

（fixture `mem_root/write_kb` 从 tests/evolve_mem/conftest.py 提升 import；`_wire_mem_snapshot` 见 Step 3。）

- [ ] **Step 2: 确认失败** → `uv run pytest tests/pipeline/test_matchday_mem.py -v` → FAIL

- [ ] **Step 3: 实现**

matchday.py：import 块加

```python
from fa.evolve_mem.snapshot import (ensure_current_snapshot_mem,
                                    personas_mem_consumed_hash)
from fa.evolve import EvolutionError
```

`_run` 内紧接 `personas_hash = personas_consumed_hash(kb_window)` 之后：

```python
    # M7a（§12.7 增补）：mem 快照同款纪律——先快照后取 hash；失败双 None，
    # kbmem 轨按快照失败降级（apply 层消费），其余轨零影响（设计档 §8）。
    def _wire_mem_snapshot() -> tuple[int | None, str | None]:
        try:
            idx = ensure_current_snapshot_mem()
            return idx, personas_mem_consumed_hash(idx)
        except (OSError, ValueError, EvolutionError):
            return None, None
    kb_mem_window, personas_mem_hash = _wire_mem_snapshot()
```

`generate_recommendations(conn, leagues, phase, run_id, personas_hash=personas_hash)` → 追加 `personas_mem_hash=personas_mem_hash`；`summary` dict 在 `"personas_hash"` 键后加：

```python
        "personas_mem_hash": personas_mem_hash,   # M7a：mem 工件内容 hash（同上语义）
        "kb_mem_window": kb_mem_window,           # mem 快照窗口序号（None=该轨降级）
```

- [ ] **Step 4: 确认通过** → `uv run pytest tests/pipeline/ -v` → 全 passed

- [ ] **Step 5: Commit**

```bash
git add src/fa/pipeline/matchday.py tests/pipeline/test_matchday_mem.py
git commit -m "feat(matchday): mem 快照接线+personas_mem_hash 入 runs.summary（M7a Task7）"
```

---

### Task 8: persona/apply.py——第四消费轨 + 内联降级记账

**Files:**
- Modify: `src/fa/persona/apply.py`
- Test: `tests/persona/test_apply.py`（追加三用例）

**Interfaces:**
- Consumes: Task 4 `build_query/window_mem_topk/window_mem_inline`；Task 2 `ensure_current_snapshot_mem`。
- Produces: `run_persona_phase` 返回 dict 新键 `mem_called/mem_ok/mem_veto/mem_degraded/mem_inline`；`mem_inline > 0` 时附 `"kbmem_degraded": "inline"`（进 runs.summary.persona，设计档 §8 降级记账）。

- [ ] **Step 1: 追加失败测试（沿用 test_apply.py 既有 conn_seeded / persona_files / hermes_ok fixture）**

```python
def test_phase_mem_track_ok_and_counts(conn_seeded, monkeypatch, fix,
                                       persona_files, mem_kb):
    """mem 轨正常：called+1、kbmem 段进 prompt（hermes 脚本回显判断）、
    summary 含 mem_* 键、无 kbmem_degraded。"""
    monkeypatch.setenv("HERMES_BIN", str(fix / "hermes_ok"))
    out = apply_mod.run_persona_phase(conn_seeded, run_id, ["E0"])
    assert out["mem_called"] >= 1 and out["mem_ok"] == out["mem_called"]
    assert "kbmem_degraded" not in out

def test_phase_mem_inline_fallback(conn_seeded, monkeypatch, fix, persona_files,
                                   mem_kb, monkeypatch_retriever_broken):
    """检索链路坏 → 内联降级：mem_inline 计数、kbmem_degraded=="inline"、
    其余轨 called 不受影响。"""
    monkeypatch.setenv("HERMES_BIN", str(fix / "hermes_ok"))
    out = apply_mod.run_persona_phase(conn_seeded, run_id, ["E0"])
    assert out["mem_inline"] >= 1 and out["kbmem_degraded"] == "inline"
    assert out["ok"] >= 1 and out["nokb_ok"] >= 1     # kb/nokb 轨不连坐

def test_phase_mem_snapshot_failure_degrades_track(conn_seeded, monkeypatch, fix,
                                                   persona_files, mem_kb_broken):
    """快照建不起（语法破损）→ mem 轨整轨降级 reason=mem_snapshot、零调用。"""
    monkeypatch.setenv("HERMES_BIN", str(fix / "hermes_ok"))
    out = apply_mod.run_persona_phase(conn_seeded, run_id, ["E0"])
    assert out["mem_called"] == 0 and out["mem_degraded"] and \
        out["mem_degraded"][0]["reason"] == "mem_snapshot"
```

（`mem_kb` / `mem_kb_broken` / `monkeypatch_retriever_broken` 为本任务在 test_apply.py 内新增的 fixture：写 E0 知识文件 + `FA_MEM_RETRIEVER=fake`；broken 检索器用 `monkeypatch.setattr(apply_mod, "window_mem_topk", raiser)`——**apply 侧依赖注入点**，见 Step 3 实现为模块级名引用即被 patch 生效。）

- [ ] **Step 2: 确认失败** → `uv run pytest tests/persona/test_apply.py -k mem -v` → FAIL

- [ ] **Step 3: 实现（apply.py 全部编辑点）**

```python
from fa.evolve import EvolutionError
from fa.evolve_mem.retrieve import build_query, window_mem_inline, window_mem_topk
from fa.evolve_mem.snapshot import ensure_current_snapshot_mem

MEM_STRATEGY = "model_persona_kbmem"
_TRACKS = (STRATEGY, NOKB_STRATEGY, SELF_STRATEGY, MEM_STRATEGY)
```

counts 初始化改为 `{"called": 0, "ok": 0, "veto": 0, "degraded": [], "inline": 0}`（四轨统一，仅 mem 轨的 inline 被消费）。SELECT 的 `IN (?, ?, ?)` 扩为四占位、参数加 `MEM_STRATEGY`。快照块加：

```python
    try:
        mem_idx = ensure_current_snapshot_mem()
    except (OSError, ValueError, EvolutionError):
        mem_idx = None
```

`if kb_idx is None: snapshot_failed_tracks.add(STRATEGY)` 同族追加 `if mem_idx is None: snapshot_failed_tracks.add(MEM_STRATEGY)`（degraded reason 用 `"mem_snapshot"`）。`build_input` 成功后、逐轨调用前：

```python
        # mem 轨知识：检索优先，坏则内联降级（§8 一级）——降级只换注入形态，
        # 不降判决（hermes 照调），inline 计数供 runs.summary 记账
        mem_lines: list[str] | None = None
        if mem_idx is not None and fid not in {d["fixture_id"] for d in counts[MEM_STRATEGY]["degraded"]}:
            try:
                mem_lines = window_mem_topk(mem_idx, league,
                                            build_query(input_obj)) or None
            except Exception:
                inline = window_mem_inline(mem_idx, league)
                if inline:
                    mem_lines = inline.splitlines()
                    counts[MEM_STRATEGY]["inline"] += 1
```

（`except Exception` 收 mem0/chroma/embedder 全家族——检索链路任何失败都只降级不炸场；此处刻意宽，与 caller timeout 的场级降级同哲学。）逐轨分支加：

```python
                elif track == MEM_STRATEGY:
                    track_kb = "\n".join(mem_lines) if mem_lines else None
                    track_label = f"（C线检索快照 w{mem_idx}）"
```

返回 dict 追加：

```python
        "mem_called": mem["called"], "mem_ok": mem["ok"],
        "mem_veto": mem["veto"], "mem_degraded": mem["degraded"],
        **({"kbmem_degraded": "inline"} if mem["inline"] else {}),
```

（`mem = counts[MEM_STRATEGY]`；docstring「三轨三次调用」更新为「四轨四次调用，顺序 kb→nokb→self→kbmem」。）

- [ ] **Step 4: 确认通过 + 既有 persona 用例计数更新**

Run: `uv run pytest tests/persona/ -v` → 全 passed（既有 called/断言 3→4 处在 commit 列明）

- [ ] **Step 5: Commit**

```bash
git add src/fa/persona/apply.py tests/persona/test_apply.py
git commit -m "feat(persona): kbmem 第四消费轨+检索注入+内联降级记账（M7a Task8）；调用计数 3→4：<文件>"
```

---

### Task 9: CLI sync-mem/mem-verify + evolve tick 尾步

**Files:**
- Modify: `src/fa/cli.py`（evolve_app 两命令）
- Modify: `src/fa/evolve/runner.py`（run_tick 收尾步）
- Test: `tests/evolve/test_cli_evolve.py`、`tests/evolve/test_runner.py`（各追加）

**Interfaces:**
- Consumes: Task 3 `sync_all`；`fa.pipeline.ops.send_alert`。
- Produces: `fa evolve sync-mem [--dry-run]`（默认全链，drift/失败退码 1；dry-run 只派生+hash 报告）；`fa evolve mem-verify`（只读对账现有缓存，drift 退码 1）；tick 尾步（sync 失败→lines 记 + `send_alert` 尽力而为，**不炸 tick**）。

- [ ] **Step 1: 追加失败测试**

test_cli_evolve.py（沿用其 conn fixture 与 CliRunner 风格）：

```python
def test_sync_mem_ok_exit0(conn, monkeypatch, tmp_path):
    ...  # 隔离 root + fake retriever + 写 E0 kb → runner exit 0，stdout 含 "E0" "ok"
def test_sync_mem_drift_exit1(conn, monkeypatch, tmp_path):
    ...  # monkeypatch.setattr(index_mod, "get_retriever", tamper) → exit 1，stdout 含 DRIFT
def test_sync_mem_dry_run_no_cache(conn, monkeypatch, tmp_path):
    ...  # --dry-run：stdout 报 hash；data/mem_cache 不存在
```

test_runner.py：

```python
def test_tick_tail_sync_mem_note(seeded, monkeypatch, hermes_ok, tmp_path):
    ...  # fake retriever + kb；tick 文本含 "sync-mem"；retriever 坏 → tick 仍 exit 0、
    ...  # 文本含 "未成功"、send_alert 被调一次（monkeypatch 计数）
```

- [ ] **Step 2: 确认失败** → `uv run pytest tests/evolve -k "sync_mem or tick_tail" -v` → FAIL

- [ ] **Step 3: 实现**

cli.py（evolve_app 内，`merge/reject` 之后照既有命令风格）：

```python
@evolve_app.command("sync-mem")
def evolve_sync_mem_cmd(
        dry_run: bool = typer.Option(False, "--dry-run",
                                      help="只派生+hash 报告，不建索引不对账")) -> None:
    """M7a：活树派生 → mem0 索引重建 → 导出对账（drift/失败退码 1）。"""
    from fa.evolve_mem.index import sync_all
    from fa.evolve_mem.snapshot import derive_live_jsonl, jsonl_map_hash
    if dry_run:
        live = derive_live_jsonl()
        typer.echo(f"live leagues={sorted(live)} hash={jsonl_map_hash(live)}")
        return
    out = sync_all()
    for lg, r in sorted(out["leagues"].items()):
        typer.echo(f"{lg}: entries={r['entries']} "
                   f"{'ok' if r['ok'] else f\"DRIFT {r['error']}\"}")
    if not out["ok"]:
        typer.echo(f"sync-mem 未成功：{out['error'] or '见上'}")
        raise typer.Exit(1)


@evolve_app.command("mem-verify")
def evolve_mem_verify_cmd() -> None:
    """M7a：只读对账——现有缓存索引 vs 活树派生（drift/缺缓存退码 1）。"""
    from fa.evolve_mem.index import get_retriever, index_store_dir, reconcile
    from fa.evolve_mem.snapshot import derive_live_jsonl, jsonl_map_hash
    live = derive_live_jsonl()
    h = jsonl_map_hash(live)
    bad = []
    try:
        r = get_retriever()
    except Exception as exc:
        typer.echo(f"retriever 未就绪：{exc}")
        raise typer.Exit(1)
    for lg, b in sorted(live.items()):
        store = index_store_dir(h, lg)
        if not (store / "READY").is_file():
            bad.append(lg)
            typer.echo(f"{lg}: 无缓存（先跑 fa evolve sync-mem）")
            continue
        ok = reconcile(b, r.export_entries(store))
        typer.echo(f"{lg}: {'ok' if ok else 'DRIFT'}")
        if not ok:
            bad.append(lg)
    if bad:
        raise typer.Exit(1)
```

runner.py `run_tick` 收尾（`lines.insert(0, …)` 之前、`return` 之前）：

```python
    # M7a 尾步（设计档 A6）：sync-mem 挂 tick 收尾——失败静默停摆不炸 tick，
    # 记事件 + TG 尽力告警（§8 写入侧）；B 线下窗快照仍按 markdown 派生。
    try:
        from fa.evolve_mem.index import sync_all
        mem = sync_all()
        if not mem["ok"]:
            lines.append("sync-mem 未成功：" + str(mem["error"] or
                        "；".join(f"{lg}={v['error']}" for lg, v in
                                  mem["leagues"].items() if not v["ok"])))
            try:
                from fa.pipeline.ops import send_alert
                send_alert("fa evolve tick：sync-mem 未成功（mem 索引停摆，"
                           "不影响 B 线——详见 cron 日志）")
            except Exception:
                pass                      # 告警也失败：只剩 cron 日志，可接受
    except Exception as exc:
        lines.append(f"sync-mem 未执行：{exc}")
```

- [ ] **Step 4: 确认通过** → `uv run pytest tests/evolve/ -v` → 全 passed

- [ ] **Step 5: Commit**

```bash
git add src/fa/cli.py src/fa/evolve/runner.py tests/evolve/
git commit -m "feat(cli/evolve): sync-mem/mem-verify 命令+tick 收尾步含告警（M7a Task9）"
```

---

### Task 10: E2E 离线冒烟 + 文档收尾

**Files:**
- Create: `tests/evolve_mem/test_e2e.py`
- Modify: `spec.md`（§12.7 增补段 + §10 里程碑表 M7a/M7b 行——文本照设计档 §13 草案）、`CLAUDE.md`（当前状态加一行 M7a）、`.gitignore`（`data/mem_cache/`；确认 `evolution/snapshots_mem/` **进 git**）
- Modify: `docs/m7a-gate0-report.md` 若 Task 1 有补测

**Interfaces:** 无新接口——全链贯通验收。

- [ ] **Step 1: E2E 失败测试**

```python
# tests/evolve_mem/test_e2e.py
"""M7a 离线全链（fake 检索器）：活树派生 → 窗口冻结 → 索引 → 检索注入
→ hash 入账 → 降级路径。hermes 用回显脚本（mock=实跑同路径）。
对齐项目验收惯例：本测试是机制贯通的诚实证据，检索语义质量归 Gate 0
真跑与首窗 E2E（设计档 §9），不得据本测试宣称召回质量。"""
def test_full_chain_offline(mem_root, write_kb, conn_seeded, hermes_echo):
    # 1) 派生+冻结 w7
    idx = 7
    # 2) matchday 接线（_wire_mem_snapshot）
    # 3) run_persona_phase 四轨：kbmem prompt 段 = 检索行（hermes_echo 回显
    #    prompt 片段断言 "[E0-S01]" 在、全文件头部注释不在）
    # 4) runs.summary 断言 personas_mem_hash/kb_mem_window/mem_* 键
    # 5) 篡改活 markdown → w8 快照内容变 → hash 变（窗口隔离）
def test_degraded_chain_still_runs(mem_root, write_kb, conn_seeded, hermes_echo):
    # 检索器坏 → kbmem_degraded=inline、四轨 called 全 ≥1、run 不炸
```

（fixture 组装沿用 tests/persona 与 tests/pipeline 既有件；`hermes_echo` = 回显 prompt 的脚本，tests/persona/fixtures 若无则新建。）

- [ ] **Step 2: 确认失败 → 实现/修接线直到通过**（本任务是贯通验收，允许暴露前九任务的接缝 bug——修在对应模块并如实记入 commit）

- [ ] **Step 3: 文档收尾**

spec.md：§12.7 节末追加设计档 §13 的增补段原文；§10 里程碑表加 M7a/M7b 两行（验收判据照设计档 §13 表）。CLAUDE.md「当前状态」加：

```markdown
- **M7a（mem0 记忆基建·kbmem 检索轨）落地**（2026-09-14）：第五轨 model_persona_kbmem（mem0 派生索引 top-k 注入，markdown 权威链路不动）、schema v12、`fa evolve sync-mem/mem-verify`、tick 尾步；Gate 0 报告 `docs/m7a-gate0-report.md`；M7b（A_mem 臂）占位待立项
```

`.gitignore` 加 `data/mem_cache/`。

- [ ] **Step 4: 全量回归** → `uv run pytest -q` → 全绿（有任何红即回修，不得跳过）

- [ ] **Step 5: Commit**

```bash
git add tests/evolve_mem/test_e2e.py spec.md CLAUDE.md .gitignore
git commit -m "test/docs(m7a): 离线全链 E2E+spec §12.7 增补+CLAUDE.md 状态（M7a Task10）"
```

---

### Task 11: 验证收口

- [ ] **Step 1: 全量测试** `uv run pytest -q`（记录数字）
- [ ] **Step 2: 真跑冒烟（不依赖 ark 时也应全通）**：
  - `uv run fa evolve sync-mem --dry-run`（有 kb 则报 leagues+hash）
  - `FA_MEM_RETRIEVER=fake uv run fa evolve sync-mem`（exit 0、逐联赛 ok）
  - `FA_MEM_RETRIEVER=fake uv run fa evolve mem-verify`（exit 0）
  - `FA_MEM_RETRIEVER=fake uv run fa evolve tick`（不炸；文本含 sync-mem 行或无到期窗）
- [ ] **Step 3: ark 真跑（key 在场则）**：`ARK_API_KEY=... uv run fa evolve sync-mem`（真 embedder 建+对账）；不在场则如实记 BLOCKED
- [ ] **Step 4: 验证报告**——本文件逐任务勾选 + 数字 + BLOCKED 项，呈负责人；提交收口 commit（如有残留改动）

---

## Self-Review 记录

- **Spec 覆盖**：A1（Task 3 派生索引+对账）、A2（Task 5/6 五轨）、A3（Task 3 `_DEAD_LLM` 结构保证 + Gate 0 第 2 项实证）、A4（Task 2 JSONL 冻结 + Task 3 hash 缓存）、A5（快照机制天然下一窗生效——Task 7/10 测试覆盖窗口隔离）、A6（Task 9 tick 尾步）、A7（Task 8 内联/Task 9 停摆/Task 3 对账）、A8（Task 1 spike + env 覆盖默认）；§7 Gate 0=Task 1；§9 测试=各任务+Task 10 E2E；§13 增补=Task 10。无遗漏。
- **占位符扫描**：测试代码均为可运行实体；Task 6/8 的「断言更新清单」依赖执行时 grep 定位（轨数断言散布不可预知），已给定位命令与记录要求，非占位。
- **类型一致性**：`entry_dicts` 键集/`jsonl_bytes`/`ensure_index` 返回 hash str/`sync_all` 返回形在各任务 Interfaces 逐一核对；`_wire_mem_snapshot` 定义（Task 7）与测试（Task 7 Step 1）同名同签名。
- **清单自检**：#1 无日期依赖（窗口测试全用字面 idx）；#4 无模块级 from-import 绑定 patch（env 切换 + `setattr` 模块属性两处均验证过语义）；#6 DDL 单源 `_BLINE_TABLE`（测试 fixture 例外已注明）；#8 无先错后改双版本；#9 E2E docstring 预写「不得据本测试宣称召回质量」。
