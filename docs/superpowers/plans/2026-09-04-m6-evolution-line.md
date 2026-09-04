# M6 C 线（进化线）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 C 线进化栈——知识库版本化外置 + hermes -z 反思纯函数 + 生产第三轨对照 + 窗口快照冻结 + 人审关卡 + 版本戳入账（spec §12.7 新增）。

**Architecture:** 新模块 `src/fa/evolve/`（对 B 线表只读，自有 evolution 三表）；B 线 recommendations 扩第三轨 `model_persona_nokb` 并带 `personas_hash` 内容戳；冻结机械化为窗口快照（B 线 prompt 只读 `evolution/snapshots/w{idx}/`）；反思提案落暂存区，merge/reject/shelve 全量记 Ruling；周检 cron tick 触发。

**Tech Stack:** Python 3.11+ / uv / typer / pytest / sqlite3（WAL）/ stdlib（hashlib、difflib、shutil、zoneinfo）。零新依赖。

**Spec:** `docs/superpowers/specs/2026-09-04-m6-evolution-implementation-design.md`（已批准，本计划的设计依据）；上游宪章 `docs/superpowers/specs/2026-09-04-m6-evolution-line-design.md`。

## Global Constraints

- 设计档七项裁定（其 §0 R1–R7）是本计划一切的先验约束；与之冲突即错。
- 真实下注禁止（spec §7.2/§9.8）；C 线无 B 线表写路径、无 Odds API 调用、无落注通道。
- 进化只沉淀定性知识，不做统计调参；统计数字不进知识文件正文（数字证据放 `evidence.stat` 字段）。
- 验收含诚实条款（writing-plans 清单第 9 条）：测试断言不得依赖当前日期漂移（时间一律走可注入缝）；三轨口径分开表述；校准轮结论不进有效性断言。
- 单一事实源：`STRATEGIES` 只在 `fa/pipeline/value.py`；知识文件语法只在 `fa/evolve/knowledge.py`；cron 时刻事实单只在 `scripts/cron_jobs.txt`。
- mock 与实跑同代码路径：hermes 调用一律 `HERMES_BIN` env 指向 fixture 脚本（M4 C1 同构）；evolve 内对 `project_root` 一律 `from fa import config` + `config.project_root()` 属性访问（from-import 绑定会让 monkeypatch 失效——清单第 4 条）。
- 提交信息 conventional commits、中文主题；面向用户的 CLI 输出中文。
- 每个 Task 的测试先失败后通过（TDD）；命令一律 `uv run pytest ...`。

---

### Task 1: evolve 包骨架 + 窗口计算（config 常量 + windows.py）

**Files:**
- Create: `src/fa/evolve/__init__.py`
- Create: `src/fa/evolve/windows.py`
- Modify: `src/fa/config.py`（persona 段之后新增 evolve 常量段）
- Test: `tests/evolve/__init__.py`（空文件）、`tests/evolve/test_windows.py`

**Interfaces:**
- Consumes: `fa.config.project_root`（本任务不用，供下游）
- Produces:
  - `fa.config.EVOLUTION_EPOCH: date`（= date(2026, 9, 4)）、`EVOLUTION_WINDOW_DAYS: int`（=42）、`KB_MAX_CHARS: int`（=2400）
  - `fa.evolve.EvolutionError(Exception)`
  - `fa.evolve.windows.Window`（frozen dataclass：`idx: int, opened: date, closes: date`）
  - `fa.evolve.windows.beijing_today() -> date`、`window_bounds(idx) -> Window`、`window_of(d) -> Window`、`due_windows(today) -> list[Window]`、`current_window_idx() -> int`

- [ ] **Step 1: 写失败测试**

`tests/evolve/test_windows.py`：

```python
"""窗口计算：锚点/42 天切分/收口判定/前锚 clamp（设计档 §1）。"""
from datetime import date

from fa.evolve import windows
from fa.config import EVOLUTION_EPOCH


def test_anchor_is_cron_activation_day():
    assert EVOLUTION_EPOCH == date(2026, 9, 4)


def test_window1_bounds():
    w = windows.window_bounds(1)
    assert (w.idx, w.opened, w.closes) == (1, date(2026, 9, 4), date(2026, 10, 16))


def test_window2_bounds():
    w = windows.window_bounds(2)
    assert (w.opened, w.closes) == (date(2026, 10, 16), date(2026, 11, 27))


def test_window_of_membership():
    # 开窗当日属本窗；闭窗当日属下一窗（[opened, closes) 半开区间）
    assert windows.window_of(date(2026, 9, 4)).idx == 1
    assert windows.window_of(date(2026, 10, 15)).idx == 1
    assert windows.window_of(date(2026, 10, 16)).idx == 2


def test_window_of_before_epoch_clamps_to_1():
    assert windows.window_of(date(2026, 8, 30)).idx == 1


def test_window_bounds_rejects_zero():
    import pytest
    from fa.evolve import EvolutionError
    with pytest.raises(EvolutionError):
        windows.window_bounds(0)


def test_due_windows_empty_in_w1():
    assert windows.due_windows(date(2026, 9, 10)) == []


def test_due_windows_after_w1_close():
    due = windows.due_windows(date(2026, 10, 20))
    assert [w.idx for w in due] == [1]


def test_due_windows_multiple():
    due = windows.due_windows(date(2026, 12, 1))
    assert [w.idx for w in due] == [1, 2, 3]
    # 2026-12-01 落在第 4 窗（11-27 开），1..3 已收口
```

`tests/evolve/__init__.py` 空文件。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/evolve/test_windows.py -v`
Expected: FAIL（`ModuleNotFoundError: fa.evolve`）

- [ ] **Step 3: 最小实现**

`src/fa/config.py`——import 区补 `from datetime import date`（若已有 datetime import 则合并），persona 段之后加：

```python
# ---------------------------------------------------------------- evolve（M6，spec §12.7）

EVOLUTION_EPOCH = date(2026, 9, 4)      # §12.3 cron 激活日 = B 线前向窗口锚点
EVOLUTION_WINDOW_DAYS = 42              # 6 周/窗（设计档 §1）
KB_MAX_CHARS = 2400                     # 知识文件长度上限（设计档 §4）
```

`src/fa/evolve/__init__.py`：

```python
"""M6 C 线（进化线，spec §12.7）：知识库版本化外置 + hermes -z 反思纯函数
+ 窗口冻结合并 + 版本戳入账。

物理边界：对 B 线表（recommendations/bets/runs…）只读；只写自有
evolution_* 表与 personas/knowledge/ 版本化工件；无 Odds API、无落注通道。
"""


class EvolutionError(Exception):
    """进化线错误族（降级语义：记 evolution_runs，不动任何 B 线表）。"""
```

`src/fa/evolve/windows.py`：

```python
"""窗口计算（设计档 §1）：锚点 EVOLUTION_EPOCH、42 天/窗连续切分。

口径统一为北京时间日期（cron 调度时区 Asia/Shanghai）。窗口 W_idx 覆盖
[opened, closes)——closes 当日 00:00 起属下一窗；早于锚点的日期 clamp 到
1 号窗（M6 上线前不存在更早窗口，收口判定不得因负序号炸掉）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fa.config import EVOLUTION_EPOCH, EVOLUTION_WINDOW_DAYS
from fa.evolve import EvolutionError


@dataclass(frozen=True)
class Window:
    idx: int
    opened: date
    closes: date


def beijing_today() -> date:
    """北京时间今天（唯一时间缝，测试 monkeypatch 本函数）。"""
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def window_bounds(idx: int) -> Window:
    if idx < 1:
        raise EvolutionError(f"窗口序号从 1 起，收到 {idx!r}")
    opened = EVOLUTION_EPOCH + timedelta(days=EVOLUTION_WINDOW_DAYS * (idx - 1))
    return Window(idx, opened, opened + timedelta(days=EVOLUTION_WINDOW_DAYS))


def window_of(d: date) -> Window:
    """d 所在窗口；d 早于锚点 → 1 号窗（clamp，见模块 docstring）。"""
    idx = max(1, (d - EVOLUTION_EPOCH).days // EVOLUTION_WINDOW_DAYS + 1)
    return window_bounds(idx)


def due_windows(today: date) -> list[Window]:
    """已收口（today >= closes）的窗口，升序——tick 的反思候选。"""
    m = window_of(today).idx
    return [window_bounds(i) for i in range(1, m)]


def current_window_idx() -> int:
    """当前窗序号（快照/判决语境用）。"""
    return window_of(beijing_today()).idx
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/evolve/test_windows.py -v`
Expected: PASS（9 条）

- [ ] **Step 5: 提交**

```bash
git add src/fa/evolve/ src/fa/config.py tests/evolve/
git commit -m "feat(evolve): 窗口计算——锚点/42天切分/收口判定（M6 T1）"
```

---

### Task 2: knowledge.py——知识文件语法/树 hash/快照/契约应用

**Files:**
- Create: `src/fa/evolve/knowledge.py`
- Test: `tests/evolve/test_knowledge.py`

**Interfaces:**
- Consumes: `fa.config.KB_MAX_CHARS`、`fa.config.PERSONA_FILES`、`fa.config.project_root`（经 `from fa import config` 属性访问）、Task 1 的 `windows`
- Produces:
  - `Entry`（frozen dataclass：`anchor: str, section: str, date: str | None, ttl_days: int | None, text: str`）
  - `KnowledgeFile`（dataclass：`league: str, entries: list[Entry]`）
  - `SECTIONS = ("结构性认知", "时效", "教训")`
  - `parse_kb(text: str, league: str) -> KnowledgeFile`（语法破损/锚点前缀不符 → `EvolutionError`）
  - `render_kb(kb: KnowledgeFile, *, generated: str, digest: str) -> str`
  - `next_anchor(kb: KnowledgeFile, section: str) -> str`（如 `E0-S08`）
  - `prune_expired(kb: KnowledgeFile, today: date) -> tuple[KnowledgeFile, list[str]]`（返回修剪后文件与被修剪锚点表）
  - `apply_contract(kb: KnowledgeFile, contract: dict) -> KnowledgeFile`（deprecate→amend→append；target 缺失抛 `EvolutionError`）
  - `kb_over_cap(text: str) -> bool`
  - `personas_tree_hash(root: Path) -> str`（sha256 hex）
  - `kb_path(league: str) -> Path`（`personas/knowledge/{PERSONA_FILES[league]}`）
  - `snapshot_dir(idx: int) -> Path`、`ensure_window_snapshot(idx: int) -> Path`（幂等）、`ensure_current_snapshot() -> int`、`window_kb_text(idx: int, league: str) -> str | None`
  - `prune_live_files(today: date) -> list[tuple[str, list[str]]]`（各联赛修剪结果）
  - `git_aux() -> dict`（`{"git_rev": str|None, "git_dirty": bool|None}`）

- [ ] **Step 1: 写失败测试**

`tests/evolve/test_knowledge.py`：

```python
"""知识文件语法/树 hash/快照/契约应用（设计档 §4/§3）。"""
import hashlib
from datetime import date
from pathlib import Path

import pytest

from fa.evolve import EvolutionError
from fa.evolve import knowledge as K


KB = """<!-- kb: league=E0 generated=2026-10-16 hash=ab12 -->
# E0 知识库

## 结构性认知
- [E0-S01] 升班马主场季初高跑动，盘口惯性低估前 6 轮

## 时效
- [E0-T03|2026-09-04|90d] 某队主力门将伤缺，预计 11 月复出

## 教训
- [E0-L07|2026-10-15] 密集期 downweight 过狠（证据见台账）
"""


def test_parse_roundtrip_sections_and_fields():
    kb = K.parse_kb(KB, "E0")
    assert [e.anchor for e in kb.entries] == ["E0-S01", "E0-T03", "E0-L07"]
    s01, t03, l07 = kb.entries
    assert (s01.section, s01.date, s03_ttl(s01)) == ("结构性认知", None, None)
    assert (t03.section, t03.date, t03.ttl_days) == ("时效", "2026-09-04", 90)
    assert (l07.section, l07.date, l07.ttl_days) == ("教训", "2026-10-15", None)


def s03_ttl(e):
    return e.ttl_days


def test_parse_empty_text_is_empty_kb():
    kb = K.parse_kb("", "E0")
    assert kb.entries == [] and kb.league == "E0"


def test_parse_bad_anchor_raises():
    with pytest.raises(EvolutionError):
        K.parse_kb("## 时效\n- [E0-T3|2026-09-04|90d] x", "E0")   # 序号须两位


def test_parse_league_mismatch_raises():
    with pytest.raises(EvolutionError):
        K.parse_kb("## 时效\n- [SP1-T03|2026-09-04|90d] x", "E0")


def test_parse_t_missing_ttl_raises():
    with pytest.raises(EvolutionError):
        K.parse_kb("## 时效\n- [E0-T03|2026-09-04] x", "E0")


def test_parse_s_with_date_raises():
    with pytest.raises(EvolutionError):
        K.parse_kb("## 结构性认知\n- [E0-S01|2026-09-04] x", "E0")


def test_render_kb_idempotent():
    kb = K.parse_kb(KB, "E0")
    text = K.render_kb(kb, generated="2026-10-16", digest="ab12")
    assert K.parse_kb(text, "E0").entries == kb.entries   # 渲染可再解析（锚点/字段无损）


def test_next_anchor_sequential_no_reuse():
    kb = K.parse_kb(KB, "E0")
    assert K.next_anchor(kb, "结构性认知") == "E0-S02"
    assert K.next_anchor(kb, "时效") == "E0-T04"
    assert K.next_anchor(kb, "教训") == "E0-L08"
    empty = K.parse_kb("", "E0")
    assert K.next_anchor(empty, "教训") == "E0-L01"


def test_prune_expired_removes_only_expired_t():
    kb = K.parse_kb(KB, "E0")
    today = date(2026, 12, 1)          # 2026-09-04 + 90d = 2026-12-03 未过期
    kept, pruned = K.prune_expired(kb, today)
    assert pruned == [] and len(kept.entries) == 3
    kept, pruned = K.prune_expired(kb, date(2026, 12, 4))   # 过期日
    assert pruned == ["E0-T03"]
    assert [e.anchor for e in kept.entries] == ["E0-S01", "E0-L07"]


def test_apply_contract_append_amend_deprecate():
    kb = K.parse_kb(KB, "E0")
    contract = {
        "league": "E0",
        "appends": [{"section": "教训", "text": "新教训", "date": "2026-10-16",
                     "ttl_days": None,
                     "evidence": {"fixtures": [1], "stat": "x"}}],
        "amendments": [{"target": "E0-S01", "text": "改写后的结构认知",
                        "date": None, "ttl_days": None, "reason": "r"}],
        "deprecations": [{"target": "E0-T03", "reason": "已复出"}],
        "no_change_reason": None,
    }
    new_kb = K.apply_contract(kb, contract)
    anchors = [e.anchor for e in new_kb.entries]
    assert "E0-T03" not in anchors and "E0-L08" in anchors
    s01 = next(e for e in new_kb.entries if e.anchor == "E0-S01")
    assert s01.text == "改写后的结构认知"
    l08 = next(e for e in new_kb.entries if e.anchor == "E0-L08")
    assert l08.date == "2026-10-16"


def test_apply_contract_unknown_target_raises():
    kb = K.parse_kb(KB, "E0")
    with pytest.raises(EvolutionError):
        K.apply_contract(kb, {"league": "E0", "appends": [],
                              "amendments": [{"target": "E0-S99", "text": "x",
                                              "date": None, "ttl_days": None,
                                              "reason": "r"}],
                              "deprecations": [], "no_change_reason": None})


def test_kb_over_cap():
    assert K.kb_over_cap("x" * (K.KB_MAX_CHARS + 1))
    assert not K.kb_over_cap("x" * K.KB_MAX_CHARS)


def test_personas_tree_hash_content_and_order_sensitive(tmp_path):
    (tmp_path / "epl.md").write_text("persona")
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "epl.md").write_text("kb-v1")
    h1 = K.personas_tree_hash(tmp_path)
    (tmp_path / "knowledge" / "epl.md").write_text("kb-v2")
    assert K.personas_tree_hash(tmp_path) != h1
    # 路径排序稳定性：同名不同序不成立（文件系统枚举序无关，hash 只看排序后路径）
    other = tmp_path / "other"
    other.mkdir()
    (other / "epl.md").write_text("persona")
    (other / "knowledge").mkdir()
    (other / "knowledge" / "epl.md").write_text("kb-v2")
    assert K.personas_tree_hash(other) == K.personas_tree_hash(tmp_path)


def test_window_snapshot_freeze_semantics(tmp_path, monkeypatch):
    from fa import config
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    live = tmp_path / "personas" / "knowledge"
    live.mkdir(parents=True)
    (live / "epl.md").write_text("v1")
    snap1 = K.ensure_window_snapshot(1)
    assert (snap1 / "epl.md").read_text() == "v1"
    (live / "epl.md").write_text("v2")          # 快照后活文件变更
    assert K.ensure_window_snapshot(1) == snap1  # 幂等：不覆盖既有快照
    assert (snap1 / "epl.md").read_text() == "v1"
    assert K.window_kb_text(1, "E0") == "v1"     # B 线读快照，不读活文件
    K.ensure_window_snapshot(2)                  # 下一窗快照才吃到 v2
    assert K.window_kb_text(2, "E0") == "v2"
    assert K.window_kb_text(3, "E0") is None     # 无快照 = 空知识库


def test_prune_live_files_writes_back(tmp_path, monkeypatch):
    from fa import config
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    live = tmp_path / "personas" / "knowledge"
    live.mkdir(parents=True)
    (live / "epl.md").write_text(KB)
    result = K.prune_live_files(date(2026, 12, 4))
    assert result == [("E0", ["E0-T03"])]
    assert "E0-T03" not in (live / "epl.md").read_text()
    assert K.prune_live_files(date(2026, 12, 4)) == []   # 幂等


def test_git_aux_never_raises():
    out = K.git_aux()
    assert set(out) == {"git_rev", "git_dirty"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/evolve/test_knowledge.py -v`
Expected: FAIL（`fa.evolve.knowledge` 不存在）

- [ ] **Step 3: 实现 `src/fa/evolve/knowledge.py`**

```python
"""知识文件语法、personas 树 hash、窗口快照（设计档 §3/§4）。

语法（§4 定稿）：

    ## 结构性认知
    - [E0-S01] 文本
    ## 时效
    - [E0-T03|2026-09-04|90d] 文本
    ## 教训
    - [E0-L07|2026-10-15] 文本

锚点 = 条目 ID（{联赛码}-{S|T|L}{两位序号}），单调递增不复用（删除后空号
不补）。TTL 修剪只在进化事件时执行（run 时不过滤——窗口内 prompt 逐字稳定，
设计档 R6/R7）。快照 = B 线唯一读取口（冻结机械化）。

project_root 一律 ``config.project_root()`` 属性访问（from-import 绑定会让
monkeypatch 失效——计划 Global Constraints）。
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path

from fa import config
from fa.config import KB_MAX_CHARS
from fa.evolve import EvolutionError
from fa.evolve import windows as _windows

SECTIONS = ("结构性认知", "时效", "教训")
_SECTION_LETTER = {"结构性认知": "S", "时效": "T", "教训": "L"}
_LETTER_SECTION = {v: k for k, v in _SECTION_LETTER.items()}

_ENTRY_RE = re.compile(
    r"^- \[(?P<anchor>[A-Z0-9]{2,3}-(?P<letter>[STL])\d{2})"
    r"(?:\|(?P<date>\d{4}-\d{2}-\d{2}))?(?:\|(?P<ttl>\d+)d)?\] ?(?P<text>.*)$")
_HEADER_RE = re.compile(r"^## (.+)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Entry:
    anchor: str
    section: str
    date: str | None = None
    ttl_days: int | None = None
    text: str = ""


@dataclass
class KnowledgeFile:
    league: str
    entries: list[Entry]


# ---------------------------------------------------------------- 解析/渲染


def parse_kb(text: str, league: str) -> KnowledgeFile:
    """解析知识文件文本；语法破损抛 EvolutionError（含行号）。

    容忍：空文本（= 空知识库）、未知小节标题与散文行（跳过——头部注释、
    标题都属此类）。不容忍：锚点格式错、联赛前缀不符、S/T/L 段的字段错配。
    """
    entries: list[Entry] = []
    section: str | None = None
    for lineno, line in enumerate(text.splitlines(), start=1):
        header = _HEADER_RE.match(line)
        if header:
            name = header.group(1).strip()
            section = name if name in SECTIONS else None
            continue
        if not line.startswith("- ["):
            continue
        m = _ENTRY_RE.match(line)
        if m is None:
            raise EvolutionError(f"{league} 知识文件第 {lineno} 行锚点语法破损：{line!r}")
        anchor, letter = m.group("anchor"), m.group("letter")
        if not anchor.startswith(f"{league}-"):
            raise EvolutionError(f"第 {lineno} 行锚点联赛前缀不符：{anchor!r} != {league}")
        if section is None:
            raise EvolutionError(f"第 {lineno} 行条目不在三段式小节内：{line!r}")
        entry_date = m.group("date")
        ttl = m.group("ttl")
        if letter == "S":
            if entry_date or ttl:
                raise EvolutionError(f"第 {lineno} 行结构性认知条目不得带日期/TTL：{line!r}")
        elif letter == "T":
            if not (entry_date and ttl):
                raise EvolutionError(f"第 {lineno} 行时效条目缺日期或 TTL：{line!r}")
        else:  # L
            if not entry_date or ttl:
                raise EvolutionError(f"第 {lineno} 行教训条目须有日期且无 TTL：{line!r}")
        entries.append(Entry(anchor=anchor, section=section,
                             date=entry_date, ttl_days=(int(ttl) if ttl else None),
                             text=m.group("text")))
    return KnowledgeFile(league=league, entries=entries)


def _entry_line(e: Entry) -> str:
    body = f"[{e.anchor}"
    if e.date:
        body += f"|{e.date}"
    if e.ttl_days is not None:
        body += f"|{e.ttl_days}d"
    return f"- {body}] {e.text}"


def render_kb(kb: KnowledgeFile, *, generated: str, digest: str) -> str:
    """渲染全文（含头部注释——解析器跳过）。空段保留标题（格式稳定）。"""
    lines = [f"<!-- kb: league={kb.league} generated={generated} hash={digest} -->",
             f"# {kb.league} 知识库", ""]
    for i, section in enumerate(SECTIONS):
        if i:
            lines.append("")
        lines.append(f"## {section}")
        lines.extend(_entry_line(e) for e in kb.entries if e.section == section)
    return "\n".join(lines) + "\n"


def next_anchor(kb: KnowledgeFile, section: str) -> str:
    letter = _SECTION_LETTER[section]
    used = {int(e.anchor.rsplit("-", 1)[1]) for e in kb.entries
            if e.section == section}
    n = max(used, default=0) + 1
    return f"{kb.league}-{letter}{n:02d}"


def prune_expired(kb: KnowledgeFile, today: date) -> tuple[KnowledgeFile, list[str]]:
    """移除过期时效条目（date + ttl < today），返回新文件与被移除锚点表。"""
    pruned: list[str] = []
    kept: list[Entry] = []
    for e in kb.entries:
        if e.ttl_days is not None and e.date:
            d = date.fromisoformat(e.date)
            if d + timedelta(days=e.ttl_days) < today:
                pruned.append(e.anchor)
                continue
        kept.append(e)
    return KnowledgeFile(kb.league, kept), pruned


def apply_contract(kb: KnowledgeFile, contract: dict) -> KnowledgeFile:
    """按契约变更知识文件：deprecate → amend → append（序号由本函数分配）。

    契约合法性由 reflect.validate_contract 前置把关；此处只做防御性检查
    （target 缺失抛错）。amendment 替换正文与日期/TTL（T 目标必须补齐二者）。
    """
    entries = {e.anchor: e for e in kb.entries}
    for dep in contract.get("deprecations") or []:
        target = dep["target"]
        if target not in entries:
            raise EvolutionError(f"deprecation 目标不存在：{target}")
        del entries[target]
    for am in contract.get("amendments") or []:
        target = am["target"]
        if target not in entries:
            raise EvolutionError(f"amendment 目标不存在：{target}")
        old = entries[target]
        entries[target] = replace(old, text=am["text"],
                                  date=am.get("date"), ttl_days=am.get("ttl_days"))
    for ap in contract.get("appends") or []:
        anchor = next_anchor(KnowledgeFile(kb.league, list(entries.values())),
                             ap["section"])
        entries[anchor] = Entry(anchor=anchor, section=ap["section"],
                                date=ap.get("date"), ttl_days=ap.get("ttl_days"),
                                text=ap["text"])
    return KnowledgeFile(kb.league, list(entries.values()))


def kb_over_cap(text: str) -> bool:
    """全文长度上限（设计档 §4：2400 字符）。"""
    return len(text) > KB_MAX_CHARS


# ---------------------------------------------------------------- 树 hash / 路径


def personas_tree_hash(root: Path) -> str:
    """personas 树内容 hash（R5）：排序后 相对路径+全文 串联 sha256。

    自动覆盖 knowledge/ 子目录；人手改 persona 文件同样改变 hash——任何
    treatment 变化留在戳里。
    """
    files = sorted(p for p in root.glob("**/*.md") if p.is_file())
    h = hashlib.sha256()
    for p in files:
        h.update(p.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\x1e")
    return h.hexdigest()


def kb_path(league: str) -> Path:
    return (config.project_root() / "personas" / "knowledge"
            / config.PERSONA_FILES[league])


def snapshot_dir(idx: int) -> Path:
    return config.project_root() / "evolution" / "snapshots" / f"w{idx}"


def ensure_window_snapshot(idx: int) -> Path:
    """本窗首个调用者把活知识文件拷入快照（幂等；已存在即复用）。

    cron 串行保证无并发竞态；无知识文件 → 目录空 = 空知识库（合法态）。
    """
    dest = snapshot_dir(idx)
    if dest.exists():
        return dest
    dest.mkdir(parents=True)
    src = config.project_root() / "personas" / "knowledge"
    if src.is_dir():
        for p in sorted(src.glob("*.md")):
            shutil.copy2(p, dest / p.name)
    return dest


def ensure_current_snapshot() -> int:
    """确保当前窗快照存在，返回窗口序号（persona 阶段自足入口）。"""
    idx = _windows.current_window_idx()
    ensure_window_snapshot(idx)
    return idx


def window_kb_text(idx: int, league: str) -> str | None:
    """B 线唯一读取口：本窗快照里的联赛知识文本（无 = None = 空知识库）。"""
    p = snapshot_dir(idx) / config.PERSONA_FILES[league]
    return p.read_text(encoding="utf-8") if p.is_file() else None


def prune_live_files(today: date) -> list[tuple[str, list[str]]]:
    """进化事件时对全部联赛活文件做 TTL 修剪（写回；报告列明）。

    无知识文件的联赛跳过；快照钉版保证本窗 B 线不受影响。
    """
    out: list[tuple[str, list[str]]] = []
    for league in config.PERSONA_FILES:
        p = kb_path(league)
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8")
        kb = parse_kb(text, league)
        kept, pruned = prune_expired(kb, today)
        if pruned:
            p.write_text(render_kb(kept, generated=today.isoformat(),
                                   digest=_text_digest(text)), encoding="utf-8")
            out.append((league, pruned))
    return out


def _text_digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def git_aux() -> dict:
    """git 辅助信息（尽力而为）：short rev 与树是否脏。取不到记 None。"""
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5,
                             cwd=config.project_root())
        dirty = subprocess.run(["git", "status", "--porcelain"],
                               capture_output=True, text=True, timeout=5,
                               cwd=config.project_root())
        return {"git_rev": rev.stdout.strip() or None,
                "git_dirty": bool(dirty.stdout.strip())}
    except (OSError, subprocess.SubprocessError):
        return {"git_rev": None, "git_dirty": None}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/evolve/test_knowledge.py -v`
Expected: PASS（15 条）

- [ ] **Step 5: 提交**

```bash
git add src/fa/evolve/knowledge.py tests/evolve/test_knowledge.py
git commit -m "feat(evolve): 知识文件语法/树hash/窗口快照/契约应用（M6 T2）"
```

---

### Task 3: schema v8——recommendations 重建扩枚举 + personas_hash + evolution 三表

**Files:**
- Modify: `src/fa/db.py`（`SCHEMA_VERSION`、`_BLINE_TABLE` 的 recommendations DDL、新增 `_EVOLUTION_TABLE`、`_SCHEMA` 拼接、`init_db` 备份护栏、`_migrate_up` v8 段与 docstring）
- Test: `tests/test_db.py`（追加）

**Interfaces:**
- Consumes: 无（纯 schema 层）
- Produces:
  - `SCHEMA_VERSION = 8`
  - `recommendations.strategy CHECK IN ('model_only','model_persona','model_persona_nokb')`、新列 `personas_hash TEXT`（存量 NULL）
  - 表 `evolution_windows / evolution_runs / evolution_rulings`（DDL 见下）
  - 迁移护栏：`data/fa.db.bak-v8` 自动备份（v7 及更旧库升级时，缺失才建）

**说明（设计档 §2.3 的落地口径）**：nokb bankroll **不在迁移播种**——paper.py 的惰性初始化（首次落注写 `paper_bankroll:{strategy}`=1000）即「镜像 M4」的实际机制，迁移播种与之重复。

- [ ] **Step 1: 写失败测试**

`tests/test_db.py` 追加：

```python
# ---------------------------------------------------------------- M6 v8


def _make_v7_db(path):
    """手搭 v7 形状的库：recommendations 无 nokb 枚举、无 personas_hash。"""
    import sqlite3
    from fa import db as dbmod
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE schema_version (version INTEGER NOT NULL);
    INSERT INTO schema_version (version) VALUES (7);
    CREATE TABLE runs (id INTEGER PRIMARY KEY, type TEXT NOT NULL, phase TEXT,
      started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
      credits_before INTEGER, credits_after INTEGER, summary TEXT);
    CREATE TABLE fixtures (id INTEGER PRIMARY KEY);
    CREATE TABLE recommendations (
      id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id),
      fixture_id INTEGER NOT NULL REFERENCES fixtures(id),
      strategy TEXT NOT NULL CHECK (strategy IN ('model_only','model_persona')),
      market TEXT NOT NULL, phase TEXT NOT NULL, model_p REAL NOT NULL,
      market_p REAL NOT NULL, best_odds REAL NOT NULL, bookmaker TEXT NOT NULL,
      edge REAL NOT NULL, ev REAL NOT NULL, kelly_stake_frac REAL NOT NULL,
      verdict TEXT, confidence_delta REAL, final_stake_frac REAL,
      key_factors TEXT, report_md TEXT, created_at TEXT NOT NULL,
      UNIQUE (fixture_id, market, strategy, phase));
    CREATE INDEX idx_recs_run ON recommendations (run_id);
    """)
    conn.execute(
        "INSERT INTO recommendations (run_id, fixture_id, strategy, market, phase,"
        " model_p, market_p, best_odds, bookmaker, edge, ev, kelly_stake_frac,"
        " final_stake_frac, verdict, confidence_delta, key_factors, report_md,"
        " created_at) VALUES (1, 1, 'model_persona', 'H', 'am', 0.5, 0.4, 2.2,"
        " 'b', 0.1, 0.1, 0.05, 0.05, 'agree', 0.0, '[]', 'r', '2026-09-05T03:00:00Z')")
    conn.commit()
    conn.close()
    assert dbmod.SCHEMA_VERSION == 8


def test_migrate_v7_to_v8(tmp_path):
    from fa import db as dbmod
    p = tmp_path / "fa.db"
    _make_v7_db(p)
    dbmod.init_db(p)
    conn = dbmod.connect(p)
    # 存量行保全 + personas_hash 为 NULL（知识库纪元前）
    row = conn.execute(
        "SELECT strategy, verdict, personas_hash FROM recommendations").fetchone()
    assert row["strategy"] == "model_persona" and row["verdict"] == "agree"
    assert row["personas_hash"] is None
    # 新枚举可插入
    conn.execute(
        "INSERT INTO recommendations (run_id, fixture_id, strategy, market, phase,"
        " model_p, market_p, best_odds, bookmaker, edge, ev, kelly_stake_frac,"
        " final_stake_frac, created_at, personas_hash)"
        " VALUES (1, 2, 'model_persona_nokb', 'H', 'am', 0.5, 0.4, 2.2, 'b', 0.1,"
        " 0.1, 0.05, 0.05, '2026-09-05T03:00:00Z', 'deadbeef')")
    # evolution 三表就位
    for t in ("evolution_windows", "evolution_runs", "evolution_rulings"):
        conn.execute(f"SELECT COUNT(*) FROM {t}")
    # 索引重建
    idx = conn.execute("SELECT name FROM sqlite_master WHERE type='index'"
                       " AND name='idx_recs_run'").fetchone()
    assert idx is not None
    assert conn.execute("SELECT version FROM schema_version").fetchone()["version"] == 8
    conn.close()


def test_migrate_v8_backup_created(tmp_path):
    from fa import db as dbmod
    p = tmp_path / "fa.db"
    _make_v7_db(p)
    dbmod.init_db(p)
    assert (tmp_path / "fa.db.bak-v8").exists()
    dbmod.init_db(p)   # 幂等：二次 init 不重复备份、不重复迁移
    conn = dbmod.connect(p)
    assert conn.execute("SELECT version FROM schema_version").fetchone()["version"] == 8
    conn.close()


def test_migrate_shadow_recovery_v8(tmp_path):
    """半途失败困在影子表：重试弃半建活动表、从影子重放（v7 先例同款）。"""
    import sqlite3
    from fa import db as dbmod
    p = tmp_path / "fa.db"
    _make_v7_db(p)
    conn = sqlite3.connect(p)
    conn.executescript("DROP INDEX idx_recs_run;"
                       "ALTER TABLE recommendations RENAME TO recommendations_v7;")
    conn.commit()
    conn.close()
    dbmod.init_db(p)
    conn = dbmod.connect(p)
    assert conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0] == 1
    shadow = conn.execute("SELECT 1 FROM sqlite_master WHERE name="
                          "'recommendations_v7'").fetchone()
    assert shadow is None
    conn.close()


def test_fresh_db_has_evolution_tables(tmp_path):
    from fa import db as dbmod
    p = tmp_path / "fresh.db"
    dbmod.init_db(p)
    conn = dbmod.connect(p)
    for t in ("evolution_windows", "evolution_runs", "evolution_rulings"):
        conn.execute(f"SELECT COUNT(*) FROM {t}")
    ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name="
                       "'recommendations'").fetchone()["sql"]
    assert "model_persona_nokb" in ddl and "personas_hash" in ddl
    conn.close()
```

既有形状一致性测试 `test_migrate_and_fresh_schemas_match`（若存在则自动覆盖新列；不存在则补一条：迁移库与 fresh 库的 recommendations PRAGMA table_info 列序一致）。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_db.py -v -k "v8 or evolution or shadow"`
Expected: FAIL（SCHEMA_VERSION 仍 7 / evolution 表不存在）

- [ ] **Step 3: 实现 db.py 变更**

1. `SCHEMA_VERSION = 7` → `SCHEma_VERSION` 改为 `SCHEMA_VERSION = 8`（注意大小写原样）。
2. `_BLINE_TABLE` 中 recommendations 的 DDL 替换（仅这一张表的定义，其余不动）：

```sql
CREATE TABLE IF NOT EXISTS recommendations (
    id               INTEGER PRIMARY KEY,
    run_id           INTEGER NOT NULL REFERENCES runs(id),
    fixture_id       INTEGER NOT NULL REFERENCES fixtures(id),
    strategy         TEXT NOT NULL
        CHECK (strategy IN ('model_only', 'model_persona',
                            'model_persona_nokb')),       -- §6.6 双轨 + M6 C 线对照轨
    market           TEXT NOT NULL
        CHECK (market IN ('H', 'D', 'A', 'O2.5')),
    phase            TEXT NOT NULL
        CHECK (phase IN ('am', 'pm')),
    model_p          REAL NOT NULL,
    market_p         REAL NOT NULL,
    best_odds        REAL NOT NULL,
    bookmaker        TEXT NOT NULL,
    edge             REAL NOT NULL,
    ev               REAL NOT NULL,
    kelly_stake_frac REAL NOT NULL,
    verdict          TEXT,
    confidence_delta REAL,
    final_stake_frac REAL,
    key_factors      TEXT,
    report_md        TEXT,
    personas_hash    TEXT,             -- M6：本 run 读取的 personas 树内容 hash（§12.7）
    created_at       TEXT NOT NULL,
    UNIQUE (fixture_id, market, strategy, phase)
);
```

3. `_RETRO_TABLE` 之后新增 `_EVOLUTION_TABLE`：

```python
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
```

4. `_SCHEMA` 拼接：`... + _RETRO_TABLE + _AL_TABLE` → `... + _RETRO_TABLE + _AL_TABLE + _EVOLUTION_TABLE`。
5. `init_db` 备份护栏——`elif row["version"] < SCHEMA_VERSION:` 分支内、调 `_migrate_up` 之前：

```python
    elif row["version"] < SCHEMA_VERSION:
        if row["version"] < 8:
            bak = p.with_name(p.name + ".bak-v8")
            if not bak.exists():
                shutil.copy2(p, bak)   # 表重建类迁移的保守护栏（设计档 §14）
        _migrate_up(conn, row["version"])
```

文件头补 `import shutil`。
6. `_migrate_up` 追加 v8 段（v7 段之后、`conn.execute("UPDATE schema_version ...")` 之前），并把 docstring 补一句 `v7->v8 重建 recommendations——strategy 扩三轨枚举、加 personas_hash 列（M6）；evolution 三表就位；init_db 在 v8 升级前自动备份 fa.db.bak-v8`：

```python
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
            if has_shadow:
                conn.execute("DROP TABLE IF EXISTS recommendations")
            else:
                conn.executescript(
                    "ALTER TABLE recommendations RENAME TO recommendations_v7;")
            conn.executescript(_BLINE_TABLE)   # 新形状（三轨枚举 + personas_hash）
            conn.execute(
                "INSERT INTO recommendations (run_id, fixture_id, strategy, market,"
                " phase, model_p, market_p, best_odds, bookmaker, edge, ev,"
                " kelly_stake_frac, final_stake_frac, verdict, confidence_delta,"
                " key_factors, report_md, created_at)"
                " SELECT run_id, fixture_id, strategy, market, phase, model_p,"
                " market_p, best_odds, bookmaker, edge, ev, kelly_stake_frac,"
                " final_stake_frac, verdict, confidence_delta, key_factors,"
                " report_md, created_at FROM recommendations_v7;")
            conn.execute("DROP TABLE recommendations_v7;")
        conn.executescript(_EVOLUTION_TABLE)
```

- [ ] **Step 4: 跑测试确认通过（含全量回归）**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS（既有迁移测试 + 新增 5 条）

- [ ] **Step 5: 提交**

```bash
git add src/fa/db.py tests/test_db.py
git commit -m "feat(db): schema v8——三轨枚举重建+personas_hash+evolution三表（M6 T3）"
```

---

### Task 4: value 三轨落行 + personas_hash 入账 + matchday 接线

**Files:**
- Modify: `src/fa/pipeline/value.py`（`STRATEGIES`、`generate_recommendations`、`_upsert_recommendation`）
- Modify: `src/fa/pipeline/matchday.py`（`_run`：hash 计算、传参、summary 记账）
- Test: `tests/pipeline/test_value.py`（追加）、`tests/pipeline/test_matchday.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `knowledge.personas_tree_hash / git_aux`；Task 3 的 `personas_hash` 列
- Produces:
  - `value.STRATEGIES = ("model_only", "model_persona", "model_persona_nokb")`（paper/render/weekly 全部经此单源自动三轨）
  - `generate_recommendations(conn, leagues, phase, run_id, cfg=FitConfig(), personas_hash: str | None = None) -> list[int]`
  - matchday summary 新键：`personas_hash: str`、`kb_window: int`、`git: {"git_rev", "git_dirty"}`

- [ ] **Step 1: 写失败测试**

`tests/pipeline/test_value.py` 追加（沿用该文件既有的库/盘口种子夹具；若夹具名不同，以文件内既有夹具为准接入——断言不变）：

```python
# ---------------------------------------------------------------- M6 三轨 + hash


def test_three_tracks_same_numbers_and_nokb_kelly_init(...):
    """同刻三落数字全同；nokb final 中性初始 = kelly（同 model_persona 语义）。"""
    # ...用既有夹具生成推荐后：
    rows = conn.execute(
        "SELECT strategy, model_p, best_odds, edge, ev, kelly_stake_frac,"
        " final_stake_frac FROM recommendations WHERE fixture_id=? AND market='H'",
        (fid,)).fetchall()
    by = {r["strategy"]: r for r in rows}
    assert set(by) == {"model_only", "model_persona", "model_persona_nokb"}
    nums = ("model_p", "best_odds", "edge", "ev", "kelly_stake_frac")
    assert all(by["model_persona"][k] == by["model_persona_nokb"][k] == by["model_only"][k]
               for k in nums)
    assert by["model_persona"]["final_stake_frac"] == by["model_persona"]["kelly_stake_frac"]
    assert by["model_persona_nokb"]["final_stake_frac"] == by["model_persona_nokb"]["kelly_stake_frac"]
    assert by["model_only"]["final_stake_frac"] is None


def test_personas_hash_stamped_on_all_rows(...):
    ids = generate_recommendations(conn, leagues, "am", run_id, personas_hash="a" * 64)
    rows = conn.execute("SELECT DISTINCT personas_hash, strategy FROM recommendations"
                        " WHERE run_id=?", (run_id,)).fetchall()
    assert {r["personas_hash"] for r in rows} == {"a" * 64}
    assert len(rows) == 3   # 三轨都带戳


def test_personas_hash_not_stamped_by_default(...):
    generate_recommendations(conn, leagues, "am", run_id)   # 无 hash（默认）
    row = conn.execute("SELECT DISTINCT personas_hash FROM recommendations"
                       " WHERE run_id=?", (run_id,)).fetchone()
    assert row["personas_hash"] is None


def test_upsert_refresh_keeps_personas_hash(...):
    """DO UPDATE 刷新价格字段时不动 personas_hash——判决与判决语境同源
    （run A 判的决，hash 留 A 的；新 run 刷价不冒充新语境，设计档 §3.1）。"""
    generate_recommendations(conn, leagues, "am", run_id, personas_hash="a" * 64)
    # 同 fixture/market/phase 再跑一个 run（新价格、不同 hash）
    generate_recommendations(conn, leagues, "am", run2_id, personas_hash="b" * 64)
    rows = conn.execute(
        "SELECT personas_hash, run_id FROM recommendations WHERE fixture_id=?"
        " AND market='H' AND strategy='model_persona'", (fid,)).fetchall()
    assert len(rows) == 1 and rows[0]["personas_hash"] == "a" * 64
    assert rows[0]["run_id"] == run2_id
```

`tests/pipeline/test_matchday.py` 追加（沿用既有 matchday 测试的 env/夹具骨架，monkeypatch 拉盘与推送为空操作——以文件内既有模式为准）：

```python
def test_run_summary_carries_personas_hash_and_window(...):
    out = run_matchday(conn, "am", ["E0"])
    summary = json.loads(conn.execute(
        "SELECT summary FROM runs WHERE id=?", (out["run_id"],)).fetchone()["summary"])
    assert isinstance(summary["personas_hash"], str) and len(summary["personas_hash"]) == 64
    assert isinstance(summary["kb_window"], int) and summary["kb_window"] >= 1
    assert set(summary["git"]) == {"git_rev", "git_dirty"}
    assert (conn.execute("SELECT DISTINCT personas_hash FROM recommendations"
                         " WHERE run_id=?", (out["run_id"],)).fetchone()
            is not None)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/pipeline/test_value.py tests/pipeline/test_matchday.py -v -k "track or hash"`
Expected: FAIL（STRATEGIES 两值 / 无 personas_hash 参数）

- [ ] **Step 3: 实现**

`src/fa/pipeline/value.py`：

1. 常量与 docstring：

```python
STRATEGIES = ("model_only", "model_persona", "model_persona_nokb")
# §6.6 A/B 双轨 + M6 C 线对照轨（nokb：人格无知识库，spec §12.7）——
# paper/render/weekly 全部经此单源引用，三轨自动生效。
```

模块 docstring 的「同刻双落（A1，§6.6）」段改为「同刻三落（A1 §6.6 + §12.7 对照轨）：每个过门槛候选写三行（数字全同）；model_persona 与 model_persona_nokb 的 ``final_stake_frac`` 以 kelly **中性初始**（判决前照 kelly 落注语义），model_only 落 NULL（M3「退回 kelly」）」。

2. `generate_recommendations` 签名加 `personas_hash: str | None = None`，`_upsert_recommendation(...)` 加同名列并传入：

```python
                ids.append(_upsert_recommendation(
                    conn, run_id=run_id, fixture_id=fixture["id"], market=market,
                    phase=phase, strategy=strategy, model_p=p, market_p=market_p,
                    best_odds=odds, bookmaker=bookmaker, edge=edge, ev=ev,
                    kelly=kelly, created_at=_iso(now),
                    final=kelly if strategy != "model_only" else None,
                    personas_hash=personas_hash))
```

3. `_upsert_recommendation`：参数表加 `personas_hash: str | None`；INSERT 列与 VALUES 加它；**DO UPDATE 不含 personas_hash**（docstring 补一句：「hash 只在 INSERT 生效——判决与判决语境同源，价格刷新不冒充新语境」）。

`src/fa/pipeline/matchday.py` `_run`：import 区加

```python
from fa import config as _config
from fa.evolve.knowledge import ensure_current_snapshot, git_aux, personas_tree_hash
```

`_run` 开头（`now = _now()` 之后）：

```python
    # M6（§12.7）：run 开始快照 personas 树内容 hash（版本戳，可归因命根）；
    # 确保本窗知识快照存在（幂等；persona 阶段的唯一 KB 读取口）
    personas_hash = personas_tree_hash(_config.project_root() / "personas")
    kb_window = ensure_current_snapshot()
```

`generate_recommendations(conn, leagues, phase, run_id)` → `generate_recommendations(conn, leagues, phase, run_id, personas_hash=personas_hash)`；summary dict 加三键（`"persona": persona_summary,` 之后）：

```python
        "personas_hash": personas_hash,       # M6 版本戳（recommendations 同值落行）
        "kb_window": kb_window,               # 本窗快照序号（冻结钉版语境）
        "git": git_aux(),                     # 辅助信息（尽力而为）
```

- [ ] **Step 4: 跑测试确认通过（含全量回归）**

Run: `uv run pytest tests/pipeline/test_value.py tests/pipeline/test_matchday.py tests/pipeline/test_paper.py tests/pipeline/test_weekly.py tests/report/ -v`
Expected: PASS——paper/weekly 经 STRATEGIES 单源自动三轨；若 render 测试因第三轨行数断言失败，属 Task 6 范围的预期失败，先在 Task 6 修复（记录失败清单，不硬改断言凑绿）。

- [ ] **Step 5: 提交**

```bash
git add src/fa/pipeline/value.py src/fa/pipeline/matchday.py tests/pipeline/
git commit -m "feat(bline): 三轨落行+personas_hash版本戳入账（M6 T4）"
```

---

### Task 5: build_prompt 知识注入 + run_persona_phase 双轨

**Files:**
- Modify: `src/fa/persona/contract.py`（`build_prompt` 加 `kb_md`/`kb_label`）
- Modify: `src/fa/persona/apply.py`（双轨调用、按轨落判决/传播、summary nokb 镜像键）
- Test: `tests/persona/test_contract_output.py`（追加 build_prompt 用例）、`tests/persona/test_apply.py`（追加双轨用例）

**Interfaces:**
- Consumes: Task 2 的 `knowledge.window_kb_text / ensure_current_snapshot`；Task 4 的第三轨枚举
- Produces:
  - `build_prompt(persona_md: str, input_obj: dict, kb_md: str | None = None, kb_label: str = "") -> str`
  - `apply.STRATEGY = "model_persona"`、`apply.NOKB_STRATEGY = "model_persona_nokb"`
  - `apply_verdict(conn, fixture_id, output, strategy: str = "model_persona") -> int`
  - `_propagate_verdict(conn, fixture_id, strategy: str = "model_persona") -> int`（测试可见）
  - `run_persona_phase` 返回 dict 在原键（called/ok/veto/degraded/attempted——**kb 轨语义不变**）之上增 `nokb_called / nokb_ok / nokb_veto / nokb_degraded`

- [ ] **Step 1: 写失败测试**

`tests/persona/test_contract_output.py` 追加：

```python
def test_build_prompt_with_kb_section():
    p = build_prompt("# 人格", {"x": 1}, kb_md="- [E0-S01] 知识条目",
                     kb_label="（快照 w2）")
    assert "## 联赛知识库（快照 w2）" in p
    assert "- [E0-S01] 知识条目" in p
    assert p.index("## 联赛知识库") < p.index("## 本场输入")   # 段序：人格→知识→输入


def test_build_prompt_without_kb_unchanged_shape():
    p = build_prompt("# 人格", {"x": 1})
    assert "联赛知识库" not in p
    assert "## 本场输入" in p
```

`tests/persona/test_apply.py` 追加（沿用该文件既有 HERMES_BIN fixture 与库种子模式；fixture 脚本见 `tests/persona/fixtures/hermes_ok`——输出合法 persona JSON）：

```python
def _dual_track_hermes(tmp_path):
    """可编程 HERMES_BIN：按**调用序**分档（kb→nokb 顺序固定），prompt 落盘、
    回固定合法输出。用序不用内容分档——无知识库时期两条 prompt 都无 KB 段。"""
    script = tmp_path / "hermes_dual.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'n=$(cat "$SEQ_FILE" 2>/dev/null || echo 0); n=$((n+1));'
        ' echo $n > "$SEQ_FILE"\n'
        'if [ "$n" = 1 ]; then cat > "$KB_FILE"; else cat > "$NOKB_FILE"; fi\n'
        'cat "' + str(_FIXTURE_OK) + '"\n')
    script.chmod(0o755)
    return script


def test_run_persona_phase_dual_track_kb_only_in_kb_prompt(...):
    """kb 轨 prompt 含知识库段、nokb 轨不含；两轨各得判决行；
    summary 原键 = kb 轨计数 + nokb_* 镜像键。"""
    # ...种子 run + 三轨 recommendations 行；预置 w1 知识快照（含一条 E0 条目）：
    #   (tmp_root/"evolution/snapshots/w1/epl.md").write_text("## 教训\n- [E0-L01|x]\n")
    #   并 monkeypatch fa.evolve.windows.beijing_today → date(2026, 9, 10)
    monkeypatch.setenv("HERMES_BIN", str(_dual_track_hermes(tmp_path)))
    monkeypatch.setenv("SEQ_FILE", str(tmp_path / "seq"))
    monkeypatch.setenv("KB_FILE", str(tmp_path / "kb.prompt"))
    monkeypatch.setenv("NOKB_FILE", str(tmp_path / "nokb.prompt"))
    out = run_persona_phase(conn, run_id, ["E0"])
    kb_prompt = (tmp_path / "kb.prompt").read_text()
    nokb_prompt = (tmp_path / "nokb.prompt").read_text()
    assert "联赛知识库" in kb_prompt and "E0-L01" in kb_prompt
    assert "联赛知识库" not in nokb_prompt
    assert out["called"] == 1 and out["nokb_called"] == 1
    # 两轨 verdict 均落库（各自 strategy 行）
    for strategy in ("model_persona", "model_persona_nokb"):
        row = conn.execute(
            "SELECT verdict FROM recommendations WHERE fixture_id=? AND strategy=?",
            (fid, strategy)).fetchone()
        assert row["verdict"] == "agree"


def test_run_persona_phase_degraded_per_track(...):
    """nokb 轨超时只降 nokb（degraded 按 track 分账），kb 轨照常。"""
    # ...HERMES_BIN 按调用序回两次不同输出：第一次（kb）合法、第二次（nokb）sleep 超时
    # 断言 out["ok"]==1、out["nokb_ok"]==0、out["nokb_degraded"][0]["reason"]=="timeout"


def test_propagate_verdict_per_strategy(...):
    """pm 沿用 am 按轨传播：kb 判决传播 kb 行、nokb 传播 nokb 行，互不串轨。"""
```

（实现细节以 `tests/persona/test_apply.py` 既有夹具命名接线；断言逐字保留。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/persona/ -v -k "kb or dual or track or propagate"`
Expected: FAIL（`build_prompt` 无 kb_md 参数 / run_persona_phase 单轨）

- [ ] **Step 3: 实现**

`src/fa/persona/contract.py` `build_prompt` 替换为：

```python
def build_prompt(persona_md: str, input_obj: dict, kb_md: str | None = None,
                 kb_label: str = "") -> str:
    """prompt = persona 全文 +（可选）知识库段 + 该场输入 JSON + 输出契约说明。

    知识段（M6 §12.7）：``kb_md`` 只由 model_persona 轨传入（窗口快照文本），
    nokb 轨与无知识库时期传 None——段整体缺省，prompt 形状不空挂标题。
    """
    kb = (f"\n\n## 联赛知识库{kb_label}\n\n" + kb_md.rstrip()
          if kb_md else "")
    return (persona_md.rstrip() + kb + "\n\n## 本场输入\n```json\n"
            + json.dumps(input_obj, ensure_ascii=False, indent=2)
            + "\n```" + _contract_clause())
```

`src/fa/persona/apply.py`：

1. 常量与 import：

```python
STRATEGY = "model_persona"
NOKB_STRATEGY = "model_persona_nokb"
_TRACKS = (STRATEGY, NOKB_STRATEGY)
```

```python
from fa.evolve.knowledge import ensure_current_snapshot, window_kb_text
```

2. `apply_verdict` 与 `_propagate_verdict` 签名加 `strategy: str = STRATEGY`，函数内两处 `strategy=?` SQL 参数改传该形参（逻辑不变）。
3. `run_persona_phase` 整体替换：

```python
def run_persona_phase(conn: sqlite3.Connection, run_id: int,
                      leagues: list[str],
                      attempted: set[int] | None = None) -> dict:
    """一窗（am 或 pm）的阶段编排：选该 run 的候选 fixture 去重逐场处理——

    M6 起每场**两轨两次调用**（§12.7 对照轨）：kb 轨（persona + 本窗知识快照）
    写 model_persona 行；nokb 轨（纯 persona）写 model_persona_nokb 行，顺序固定
    kb→nokb。降级按轨分别记账（degraded = kb 轨、nokb_degraded = nokb 轨），
    persona 文件缺失两轨同降（未触达调用不计入 called）。
    ``attempted`` 里的场跳过且零调用，改做判决传播（pm 沿用 am，§6.2）——按轨
    各自传播。恰一次 ``conn.commit()``。

    返回 ``{"called","ok","veto","degraded":[{fixture_id,reason}],"attempted"}
    （kb 轨语义不变，保 ops/watchdog 兼容）+ "nokb_called","nokb_ok",
    "nokb_veto","nokb_degraded" 镜像键``。
    """
    seen = set(attempted or ())
    counts = {t: {"called": 0, "ok": 0, "veto": 0, "degraded": []}
              for t in _TRACKS}
    kb_idx = ensure_current_snapshot()      # 快照自足（幂等；B 线唯一读取口）
    rows = conn.execute(
        "SELECT DISTINCT r.fixture_id, f.league FROM recommendations r"
        " JOIN fixtures f ON f.id = r.fixture_id"
        " WHERE r.run_id=? AND r.strategy IN (?, ?)"
        " ORDER BY r.fixture_id",
        (run_id, STRATEGY, NOKB_STRATEGY)).fetchall()
    league_set = set(leagues)
    for row in rows:
        fid, league = row["fixture_id"], row["league"]
        if fid in seen:                      # am 已判/已试：pm 沿用，零调用
            _propagate_verdict(conn, fid, STRATEGY)
            _propagate_verdict(conn, fid, NOKB_STRATEGY)
            continue
        seen.add(fid)
        if league not in league_set:
            continue
        try:
            persona_md = persona_path(league).read_text(encoding="utf-8")
        except (FileNotFoundError, ValueError):
            for t in _TRACKS:                # 人格这一环没就位，两轨同降
                counts[t]["degraded"].append({"fixture_id": fid,
                                               "reason": "persona_file"})
            continue
        kb_md = window_kb_text(kb_idx, league)
        input_obj = build_input(conn, fid, run_id)
        for track in _TRACKS:
            c = counts[track]
            try:
                prompt = build_prompt(persona_md, input_obj,
                                      kb_md=kb_md if track == STRATEGY else None,
                                      kb_label=f"（快照 w{kb_idx}）")
                c["called"] += 1             # hermes 实际调用数（额度口径）
                output = extract_json(call_hermes(prompt))
                validate_output(output)
                apply_verdict(conn, fid, output, strategy=track)
            except PersonaError as exc:
                reason = getattr(exc, "reason", "unknown")
                c["degraded"].append({"fixture_id": fid,
                                      "reason": _REASON.get(reason, reason)})
                continue
            c["ok"] += 1
            c["veto"] += 1 if output["verdict"] == "veto" else 0
    conn.commit()
    kb, nokb = counts[STRATEGY], counts[NOKB_STRATEGY]
    return {"called": kb["called"], "ok": kb["ok"], "veto": kb["veto"],
            "degraded": kb["degraded"], "attempted": sorted(seen),
            "nokb_called": nokb["called"], "nokb_ok": nokb["ok"],
            "nokb_veto": nokb["veto"], "nokb_degraded": nokb["degraded"]}
```

（`build_input` 移到 per-fixture 一次、两轨共用——同 run 同 fixture 的候选行数字三轨全同；`ValueError`（league 不在映射）已由前置 `persona_path` try 捕获归 persona_file。）

- [ ] **Step 4: 跑测试确认通过（含 persona 全量）**

Run: `uv run pytest tests/persona/ tests/pipeline/test_matchday.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/fa/persona/ tests/persona/
git commit -m "feat(persona): 双轨调用+知识库快照注入prompt（M6 T5）"
```

---

### Task 6: render TG 双轨口径过滤（nokb 不进推送正文）

**Files:**
- Modify: `src/fa/report/render.py`（`_REC_SQL` 过滤、`_bankroll_lines` 过滤、`_TG_STRATEGIES` 常量）
- Test: `tests/report/test_render.py`（追加）

**Interfaces:**
- Consumes: Task 4 的 `STRATEGIES`（三轨）
- Produces: `render._TG_STRATEGIES = ("model_only", "model_persona")`（TG 正文口径常量）；`fa status`/`fa ops weekly`/dashboard 三轨为自动行为（paper_summary/weekly_summary 遍历 STRATEGIES 单源），本任务只加验证。

- [ ] **Step 1: 写失败测试**

`tests/report/test_render.py` 追加（沿用该文件既有的库种子夹具）：

```python
def test_tg_report_excludes_nokb_track(...):
    """TG 正文保持双轨口径：候选表、bankroll 块均不含 nokb（设计档 §5/R1）。"""
    # ...种子一个 run 的三轨 recommendations 行 + 一条 nokb paper 注
    text = render_matchday_report(conn, run_id, "am", {"train_n": 100}, 300, False)
    assert "model_persona_nokb" not in text
    assert "纯模型" in text and "模型+persona" in text


def test_pm_update_excludes_nokb_track(...):
    am_id, pm_id = ...   # 两个 run 各三轨行
    text = render_pm_update(conn, am_id, pm_id, 300, False)
    assert "model_persona_nokb" not in text


def test_status_shows_three_tracks(...):
    """fa status 的 B 线栏三轨（paper_summary 单源自动）——验证不被 render
    过滤误伤（status 走 cli._b_line_summary，不经 render）。"""
    from fa.pipeline.paper import paper_summary
    assert set(paper_summary(conn)) == {"model_only", "model_persona",
                                        "model_persona_nokb"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/report/test_render.py -v -k "nokb or three_tracks"`
Expected: FAIL（TG 文本含 model_persona_nokb）

- [ ] **Step 3: 实现 `src/fa/report/render.py`**

1. 常量（`_PERSONA_STRATEGY` 定义之后）：

```python
# M6（§12.7）：nokb 是 C 线测量对照轨，不进 TG 推送正文（R1）——TG 保持
# §6.6 双轨口径；`fa status` / weekly / dashboard 走 STRATEGIES 单源自动三轨。
_TG_STRATEGIES = ("model_only", "model_persona")
```

2. `_REC_SQL` 的 WHERE 尾部加过滤：

```sql
WHERE r.run_id = ? AND r.strategy IN ('model_only', 'model_persona')
```

（pm diff 的配对/新增/消失全部由 `_recs` 供给——单点过滤三处全中。）
3. `_bankroll_lines` 循环 `for strategy in STRATEGIES:` → `for strategy in _TG_STRATEGIES:`，docstring 补「TG 双轨口径（nokb 不进正文，§12.7）」。

- [ ] **Step 4: 跑测试确认通过（report 全量 + Task 4 遗留失败项复绿）**

Run: `uv run pytest tests/report/ tests/pipeline/ -v`
Expected: PASS（含 Task 4 Step 4 记录的 render 失败项）

- [ ] **Step 5: 提交**

```bash
git add src/fa/report/render.py tests/report/test_render.py
git commit -m "feat(report): TG正文双轨口径——nokb对照轨不进推送（M6 T6）"
```

---

### Task 7: evidence.py——窗口×联赛证据聚合（只读）

**Files:**
- Create: `src/fa/evolve/evidence.py`
- Test: `tests/evolve/test_evidence.py`

**Interfaces:**
- Consumes: Task 1 的 `windows.Window`
- Produces: `window_evidence(conn: sqlite3.Connection, w: Window, league: str) -> dict`（JSON 兼容 dict，规格见测试与设计档 §7；未结算注不进 ROI，单列 `n_pending_settlement`）

**口径钉死（设计档 §7 的实现语义）**：
- 窗口归属按 `date(recommendations.created_at, '+8 hours') ∈ [opened, closes)`（北京时间日期；created_at 是 UTC ISO 串，SQLite `date()` 原生支持 `Z` 后缀）
- verdicts 按**场次**计（每 fixture 取首条非 NULL 判决行）；`n_judged` = 有判决的场次数
- `roi` = (Σreturn − Σstake) / Σstake，只计 `status IN ('won','lost')`（void 不计）；`clv_median/mean` 只计非 NULL clv
- `kills`：kb 轨 verdict ∈ {veto, downweight} 的 (fixture, market) 行，对照收益取 model_only 轨同 (fixture, market) 已结算注的 (return−stake)/stake；无对照注记 None
- `divergences`：kb/nokb 首判决 verdict 不同，或 |confidence_delta 差| > 1e-9 的场次
- **对设计档 §7 字段的一处精化**：`n_called`/`n_degraded`（hermes 调用数/降级数）不在 DB 直推导（它们活在 runs.summary JSON），改以 `n_judged`（有判决场次数）承载「本窗实际有效判决」语义——反思消费的信息不变，字段全部库内可推导

- [ ] **Step 1: 写失败测试**

`tests/evolve/test_evidence.py`：

```python
"""证据聚合：窗口归属/三轨统计/误杀对照/轨间分歧/未结算单列（设计档 §7）。"""
import json
from datetime import date

from fa import db as dbmod
from fa.evolve import windows
from fa.evolve.evidence import window_evidence


def _seed(conn):
    """一场三轨：kb 轨 veto、nokb 轨 agree、model_only 有已结算盈利注。"""
    conn.execute("INSERT INTO runs (id, type, phase, started_at, status)"
                 " VALUES (1, 'matchday', 'am', '2026-09-05T03:00:00Z', 'ok')")
    # fixtures/teams 种子 SQL 沿用 tests/pipeline/test_paper.py 的既有夹具形态
    # （字段以 _BLINE_TABLE 的 fixtures DDL 为准），按下列语义落行：
    #   run#1（2026-09-05，窗 1 内）fixture 1 三轨 H 行 + model_only won 注
    #   （stake 10 / return 22 / clv 0.05）+ kb 轨另一 fixture 3 的 agree 行
    #   挂 pending 注；run#2（2027-01-01，窗 3）fixture 2 三轨行
    ...
```

**实施注意**：fixtures 建表 DDL 不在本计划里复述——`tests/evolve/conftest.py` 直接从 `tests/pipeline/test_paper.py` 复制其种子夹具（`_seed_run_with_recs` 一类），或把它提取到共享夹具。**测试种子按下面语义构造**（字段名以 DDL 为准）：

```python
# run#1（2026-09-05，窗 1 内）fixture 1：
#   model_only   H 行 + 已结算 won 注（stake 10, return 22, clv +0.05）
#   model_persona    H 行 verdict='veto'
#   model_persona_nokb H 行 verdict='agree'（delta 0.0）
# run#2（2027-01-01，窗 3 内）fixture 2：三轨行——验证窗口过滤不吃串窗数据
# 另一条 kb pending 注（status='pending'）——验证不进 ROI、进 n_pending_settlement


def test_window_evidence_shape_and_semantics(conn):
    ev = window_evidence(conn, windows.window_bounds(1), "E0")
    assert ev["league"] == "E0"
    assert ev["window"] == {"idx": 1, "from": "2026-09-04", "to": "2026-10-16"}
    assert ev["kb_track"]["n_judged"] == 1
    assert ev["kb_track"]["verdicts"] == {"agree": 0, "downweight": 0, "veto": 1}
    assert ev["nokb_track"]["verdicts"] == {"agree": 1, "downweight": 0, "veto": 0}
    assert ev["model_only_ref"]["n_settled"] == 1
    assert ev["model_only_ref"]["roi"] == 1.2          # (22-10)/10
    assert ev["kb_track"]["n_bets"] == 1               # veto 行不落注；pending 注属 kb 轨？——
    # ↑ 语义：pending 注挂在 kb 轨另一 fixture 的 agree 行上（种子另加 fixture 3）
    assert ev["n_pending_settlement"] == 1
    kill = ev["kills"][0]
    assert kill["fixture_id"] == 1 and kill["kb_verdict"] == "veto"
    assert kill["nokb_verdict"] == "agree"
    assert kill["mo_return_on_stake"] == 1.2
    div = ev["divergences"][0]
    assert div["fixture_id"] == 1
    # json 兼容（evidence.json 落盘走 json.dumps）
    json.dumps(ev, ensure_ascii=False)


def test_window_evidence_empty_league(conn):
    ev = window_evidence(conn, windows.window_bounds(1), "SP1")
    assert ev["kb_track"]["n_judged"] == 0 and ev["kills"] == []
    assert ev["kb_track"]["roi"] is None and ev["kb_track"]["clv_median"] is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/evolve/test_evidence.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `src/fa/evolve/evidence.py`**

```python
"""窗口×联赛证据聚合（设计档 §7）——反思的唯一证据源，只读。

三轨各查各账：kb/model_persona、nokb/model_persona_nokb、对照 ref/model_only。
未结算注不进 ROI、单列计数；误杀对照取 model_only 同 (fixture, market) 已结算
注的实际收益率——双轨天然提供反事实，确定性、无模拟。
"""
from __future__ import annotations

import sqlite3
import statistics

from fa.evolve.windows import Window

_SETTLED = ("won", "lost")
_EPS = 1e-9

_WINDOW = ("date(r.created_at, '+8 hours') >= ?"
           " AND date(r.created_at, '+8 hours') < ?")


def _judged(conn, opened: str, closes: str, league: str, strategy: str) -> list[dict]:
    """每场首条判决行（verdict 广播同场同轨必然一致，取 MIN(id) 稳定）。"""
    return [dict(r) for r in conn.execute(
        "SELECT r.fixture_id, r.verdict, r.confidence_delta"
        " FROM recommendations r JOIN fixtures f ON f.id = r.fixture_id"
        f" WHERE f.league=? AND r.strategy=? AND r.verdict IS NOT NULL AND {_WINDOW}"
        " GROUP BY r.fixture_id"
        " HAVING r.id = MIN(r.id)"
        " ORDER BY r.fixture_id", (league, strategy, opened, closes))]


def _bet_stats(conn, opened: str, closes: str, league: str, strategy: str) -> dict:
    rows = conn.execute(
        "SELECT b.status, b.stake, b.return_amt, b.clv"
        " FROM bets b JOIN recommendations r ON r.id = b.recommendation_id"
        " JOIN fixtures f ON f.id = r.fixture_id"
        f" WHERE b.mode='paper' AND f.league=? AND r.strategy=? AND {_WINDOW}",
        (league, strategy, opened, closes)).fetchall()
    settled = [r for r in rows if r["status"] in _SETTLED]
    staked = sum(r["stake"] for r in settled)
    returned = sum(r["return_amt"] or 0.0 for r in settled)
    clvs = [r["clv"] for r in settled if r["clv"] is not None]
    return {"n_bets": len(rows), "n_settled": len(settled),
            "roi": ((returned - staked) / staked) if staked else None,
            "clv_median": (statistics.median(clvs) if clvs else None),
            "clv_mean": (statistics.fmean(clvs) if clvs else None)}


def _track(conn, opened: str, closes: str, league: str, strategy: str) -> dict:
    judged = _judged(conn, opened, closes, league, strategy)
    n_fix = conn.execute(
        "SELECT COUNT(DISTINCT r.fixture_id) AS n FROM recommendations r"
        " JOIN fixtures f ON f.id = r.fixture_id"
        f" WHERE f.league=? AND r.strategy=? AND {_WINDOW}",
        (league, strategy, opened, closes)).fetchone()["n"]
    verdicts = {"agree": 0, "downweight": 0, "veto": 0}
    for j in judged:
        if j["verdict"] in verdicts:
            verdicts[j["verdict"]] += 1
    out = {"n_fixtures": n_fix, "n_judged": len(judged), "verdicts": verdicts}
    out.update(_bet_stats(conn, opened, closes, league, strategy))
    return out


def _mo_ref(conn, opened: str, closes: str, league: str) -> dict:
    stats = _bet_stats(conn, opened, closes, league, "model_only")
    return {"n_bets": stats["n_bets"], "n_settled": stats["n_settled"],
            "roi": stats["roi"]}


def _label(conn, fixture_id: int) -> dict:
    row = conn.execute(
        "SELECT f.kickoff_utc, th.name AS home, ta.name AS away"
        " FROM fixtures f LEFT JOIN teams th ON th.id=f.home_team_id"
        " LEFT JOIN teams ta ON ta.id=f.away_team_id WHERE f.id=?",
        (fixture_id,)).fetchone()
    if row is None:
        return {"date": None, "home": None, "away": None}
    return {"date": (row["kickoff_utc"] or "")[:10], "home": row["home"],
            "away": row["away"]}


def _kills(conn, opened: str, closes: str, league: str) -> list[dict]:
    rows = conn.execute(
        "SELECT r.fixture_id, r.market, r.verdict, r.final_stake_frac"
        " FROM recommendations r JOIN fixtures f ON f.id = r.fixture_id"
        f" WHERE f.league=? AND r.strategy='model_persona'"
        " AND r.verdict IN ('veto','downweight') AND {_WINDOW}"
        " ORDER BY r.fixture_id, r.market", (league, opened, closes)).fetchall()
    out = []
    for r in rows:
        mo = conn.execute(
            "SELECT b.return_amt, b.stake FROM bets b"
            " JOIN recommendations r2 ON r2.id = b.recommendation_id"
            " WHERE r2.fixture_id=? AND r2.market=? AND r2.strategy='model_only'"
            " AND b.mode='paper' AND b.status IN ('won','lost')",
            (r["fixture_id"], r["market"])).fetchone()
        item = {"fixture_id": r["fixture_id"], "market": r["market"],
                "kb_verdict": r["verdict"],
                "kb_final_frac": r["final_stake_frac"],
                "mo_return_on_stake": None}
        if mo is not None and mo["stake"]:
            item["mo_return_on_stake"] = ((mo["return_amt"] or 0.0)
                                          - mo["stake"]) / mo["stake"]
        item.update(_label(conn, r["fixture_id"]))
        out.append(item)
    return out


def _divergences(conn, opened: str, closes: str, league: str) -> list[dict]:
    kb = {j["fixture_id"]: j for j in
          _judged(conn, opened, closes, league, "model_persona")}
    nokb = {j["fixture_id"]: j for j in
            _judged(conn, opened, closes, league, "model_persona_nokb")}
    out = []
    for fid in sorted(set(kb) & set(nokb)):
        a, b = kb[fid], nokb[fid]
        da = a["confidence_delta"] or 0.0
        db = b["confidence_delta"] or 0.0
        if a["verdict"] != b["verdict"] or abs(da - db) > _EPS:
            out.append({"fixture_id": fid, "kb_verdict": a["verdict"],
                        "nokb_verdict": b["verdict"],
                        "kb_conf_delta": da, "nokb_conf_delta": db})
    return out


def window_evidence(conn: sqlite3.Connection, w: Window, league: str) -> dict:
    opened, closes = w.opened.isoformat(), w.closes.isoformat()
    kb = _track(conn, opened, closes, league, "model_persona")
    nokb = _track(conn, opened, closes, league, "model_persona_nokb")
    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM bets b"
        " JOIN recommendations r ON r.id = b.recommendation_id"
        " JOIN fixtures f ON f.id = r.fixture_id"
        f" WHERE b.mode='paper' AND b.status='pending' AND f.league=?"
        " AND r.strategy IN ('model_persona','model_persona_nokb') AND {_WINDOW}",
        (league, opened, closes)).fetchone()["n"]
    return {"league": league,
            "window": {"idx": w.idx, "from": opened, "to": closes},
            "kb_track": kb, "nokb_track": nokb,
            "model_only_ref": _mo_ref(conn, opened, closes, league),
            "kills": _kills(conn, opened, closes, league),
            "divergences": _divergences(conn, opened, closes, league),
            "n_pending_settlement": pending}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/evolve/test_evidence.py -v`
Expected: PASS（2 条主用例 + 种子夹具自检）

- [ ] **Step 5: 提交**

```bash
git add src/fa/evolve/evidence.py tests/evolve/
git commit -m "feat(evolve): 窗口证据聚合——三轨/误杀对照/轨间分歧（M6 T7）"
```

---

### Task 8: reflect.py——T0 探针 + 反思调用 + prompt + 契约校验

**Files:**
- Create: `src/fa/evolve/reflect.py`
- Create: `docs/evolution/t0-toolset-probe.md`（探针记录）
- Test: `tests/evolve/test_reflect.py`

**Interfaces:**
- Consumes: Task 2 的 `knowledge.*`、Task 7 的 `window_evidence`、`fa.persona.contract.extract_json`（复用 JSON 提取）、`fa.config.hermes_bin / persona_timeout`
- Produces:
  - `REFLECT_TOOLSET: str | None`（T0 探针定：None = 不带 `-t`；`"search"` = stopgap + 禁工具条款）
  - `build_reflect_command(prompt: str) -> list[str]`
  - `call_reflect(prompt: str, timeout: float | None = None) -> str`（失败抛 `ReflectCallError(EvolutionError)`，`.reason ∈ {"timeout","exit"}`）
  - `build_reflect_prompt(league: str, kb_text: str | None, evidence: dict) -> str`
  - `validate_contract(contract: dict, kb: KnowledgeFile, league: str) -> list[str]`（违规清单，空 = 过）
  - `reflect_league(conn, w: Window, league: str) -> dict`（`{"league","status","no_change_reason","proposal_path","duration_s","kb_hash_before"}`；status 词表同 evolution_runs）

- [ ] **Step 1: T0 探针（先跑，结果写进常量与记录）**

Run（真机、无副作用——只探命令形态，prompt 为无害一句话）：

```bash
hermes -z "只回复两个字：可用" ; echo "exit=$?"            # 候选A：不带 -t
hermes -z "只回复两个字：可用" -t search ; echo "exit=$?"    # 候选B（已知可用，M4 T2）
```

判定：候选 A exit 0 且 stdout 有回复 → `REFLECT_TOOLSET = None`（空工具集形态成立）；A 失败（exit 2 或挂死）→ `REFLECT_TOOLSET = "search"`（stopgap：禁工具条款兜底，M4 同款）。结果与原始输出摘录写 `docs/evolution/t0-toolset-probe.md`。

- [ ] **Step 2: 写失败测试**

`tests/evolve/test_reflect.py`：

```python
"""反思：命令形态/prompt 结构/契约校验六条/reflect_league 降级路径。"""
import json
from datetime import date
from pathlib import Path

import pytest

from fa.evolve import EvolutionError
from fa.evolve import knowledge as K
from fa.evolve import reflect as R
from fa.evolve.windows import window_bounds


KB = ("## 结构性认知\n- [E0-S01] 旧认知\n"
      "## 时效\n- [E0-T03|2026-09-04|90d] 旧时效\n"
      "## 教训\n- [E0-L07|2026-10-15] 旧教训\n")


def test_build_reflect_command_shape(monkeypatch):
    monkeypatch.setattr(R, "REFLECT_TOOLSET", None)
    assert R.build_reflect_command("p") == ["hermes", "-z", "p"]
    monkeypatch.setattr(R, "REFLECT_TOOLSET", "search")
    assert R.build_reflect_command("p") == ["hermes", "-z", "p", "-t", "search"]


def test_build_reflect_prompt_structure(tmp_path, monkeypatch):
    ev = {"league": "E0", "window": {"idx": 1}}
    p = R.build_reflect_prompt("E0", KB, ev)
    assert "E0" in p and "知识库管理员" in p
    assert json.dumps(ev, ensure_ascii=False) in p
    assert "no_change" in p                     # no_change 优先于编造（反偏差）
    assert "amend" in p and "deprecate" in p    # 显式处理旧条目（防回音室）
    assert "不得" in p and "工具" in p           # 禁工具条款
    empty = R.build_reflect_prompt("E0", None, ev)
    assert "空" in empty                        # 首版冷启动明示


_VALID = {"league": "E0",
          "appends": [{"section": "教训", "text": "t", "date": "2026-10-16",
                       "ttl_days": None,
                       "evidence": {"fixtures": [1], "stat": "s"}}],
          "amendments": [], "deprecations": [], "no_change_reason": None}


def test_validate_contract_ok():
    kb = K.parse_kb(KB, "E0")
    assert R.validate_contract(_VALID, kb, "E0") == []


def test_validate_contract_six_rules():
    kb = K.parse_kb(KB, "E0")
    bad_league = dict(_VALID, league="SP1")
    assert any("league" in e for e in R.validate_contract(bad_league, kb, "E0"))
    no_ev = dict(_VALID, appends=[dict(_VALID["appends"][0],
                                       evidence={"fixtures": [], "stat": ""})])
    assert any("fixtures" in e for e in R.validate_contract(no_ev, kb, "E0"))
    s_dated = dict(_VALID, appends=[dict(_VALID["appends"][0], section="结构性认知",
                                         date="2026-10-16")])
    assert R.validate_contract(s_dated, kb, "E0")        # S 段不得带日期 → 违规
    ghost = dict(_VALID, amendments=[{"target": "E0-S99", "text": "x",
                                      "date": None, "ttl_days": None, "reason": "r"}])
    assert any("E0-S99" in e for e in R.validate_contract(ghost, kb, "E0"))
    both = dict(_VALID, no_change_reason="证据不足")
    assert any("no_change" in e for e in R.validate_contract(both, kb, "E0"))
    t_amend_bad = dict(_VALID, amendments=[{"target": "E0-T03", "text": "x",
                                            "date": None, "ttl_days": None,
                                            "reason": "r"}])
    assert any("T03" in e for e in R.validate_contract(t_amend_bad, kb, "E0"))
    assert any("date" in e for e in
               R.validate_contract({"league": "E0", "appends": [
                   {"section": "时效", "text": "t", "date": "2026/10/16",
                    "ttl_days": 90,
                    "evidence": {"fixtures": [1], "stat": ""}}],
                   "amendments": [], "deprecations": [],
                   "no_change_reason": None}, kb, "E0"))   # 日期格式


def test_validate_contract_cap_exceeded():
    kb = K.parse_kb(KB, "E0")
    big = dict(_VALID, appends=[dict(_VALID["appends"][0], text="长" * 3000)])
    errs = R.validate_contract(big, kb, "E0")
    assert any("KB_MAX_CHARS" in e or "上限" in e for e in errs)


def _hermes(script_text, tmp_path):
    script = tmp_path / "hermes.sh"
    script.write_text(script_text)
    script.chmod(0o755)
    return str(script)


def test_reflect_league_no_sample_skips_call(conn, tmp_path, monkeypatch):
    """窗口内该联赛 0 判决样本 → 不调 hermes，直接 no_change（R7）。"""
    monkeypatch.setenv("HERMES_BIN", _NO_CALL)     # 一调就炸的哨兵脚本
    out = R.reflect_league(conn, window_bounds(1), "E0")
    assert out["status"] == "no_change"
    assert out["no_change_reason"] == "窗口内该联赛无判决样本"


def test_reflect_league_contract_fail(conn, tmp_path, monkeypatch, kbfile):
    monkeypatch.setenv("HERMES_BIN",
                       _hermes('#!/usr/bin/env bash\necho \'{"league":"E0",'
                               '"appends":[{"section":"教训","text":"x",'
                               '"date":"2026-10-16","evidence":{"fixtures":[]}}],'
                               '"amendments":[],"deprecations":[],'
                               '"no_change_reason":null}\';', tmp_path))
    out = R.reflect_league(conn, window_bounds(1), "E0")
    assert out["status"] == "contract" and out["proposal_path"] is None


def test_reflect_league_ok_stages_proposal(conn, tmp_path, monkeypatch, kbfile):
    monkeypatch.setenv("HERMES_BIN", _hermes(
        '#!/usr/bin/env bash\necho ' + json.dumps(_VALID) + ';', tmp_path))
    out = R.reflect_league(conn, window_bounds(1), "E0")
    assert out["status"] == "ok" and out["proposal_path"] is not None
```

（`conn`/`kbfile`/`_NO_CALL` 为夹具：`conn` = 窗 1 内种好 kb/nokb 判决行的临时库（复用 Task 7 种子）；`kbfile` = monkeypatch `fa.config.project_root` 到 tmp 并写活知识文件；`_NO_CALL` = `#!/usr/bin/env bash\nexit 99` 脚本——被调即失败，断言零调用。）

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run pytest tests/evolve/test_reflect.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 4: 实现 `src/fa/evolve/reflect.py`**

```python
"""反思纯函数：hermes -z 调用 + prompt 组装 + 契约校验（设计档 §6）。

与 M4 persona caller 同构（HERMES_BIN mock 同路径、超时/exit 真实）；差异：
反思证据只许来自台账 JSON——禁工具条款常驻 prompt，工具集形态由 T0 探针定
（docs/evolution/t0-toolset-probe.md；None=空集成立，"search"=stopgap）。
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import subprocess
import time

from fa import config
from fa.config import KB_MAX_CHARS
from fa.evolve import EvolutionError
from fa.evolve import knowledge as K
from fa.evolve.evidence import window_evidence
from fa.evolve.windows import Window
from fa.persona.contract import extract_json

# T0 探针结论（探针记录见 docs/evolution/t0-toolset-probe.md）：
# None = 「hermes -z <prompt>」不带 -t（空工具集）；"search" = stopgap（禁工具条款兜底）
REFLECT_TOOLSET: str | None = None      # ← 按 T0 探针结果改这里


class ReflectCallError(EvolutionError):
    """子进程失败：reason 为 "timeout"（超时）或 "exit"（非 0 退出）。"""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


def build_reflect_command(prompt: str) -> list[str]:
    cmd = [config.hermes_bin(), "-z", prompt]
    if REFLECT_TOOLSET:
        cmd += ["-t", REFLECT_TOOLSET]
    return cmd


def _timeout(explicit: float | None) -> float:
    value = config.persona_timeout() if explicit is None else explicit
    if not math.isfinite(value) or value <= 0:
        return 120.0
    return value


def call_reflect(prompt: str, timeout: float | None = None) -> str:
    try:
        proc = subprocess.run(build_reflect_command(prompt), capture_output=True,
                              text=True, timeout=_timeout(timeout))
    except subprocess.TimeoutExpired as exc:
        raise ReflectCallError("timeout", f"{exc.timeout}s") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:]
        raise ReflectCallError("exit", f"code {proc.returncode} {tail}")
    return proc.stdout


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def build_reflect_prompt(league: str, kb_text: str | None, evidence: dict) -> str:
    kb_block = kb_text if kb_text else "（当前为空——这是首个版本前的初始态）"
    ev = json.dumps(evidence, ensure_ascii=False, indent=2)
    cur = len(kb_text or "")
    return f"""你是 {league} 联赛知识库的管理员（不是比赛人格，不预测比赛）。
你的任务：基于**下方窗口证据 JSON**（唯一证据源），对知识文件提出修改提案。

## 当前知识文件（长度 {cur}/{KB_MAX_CHARS}）

{kb_block}

## 本窗口证据（唯一证据源；未结算注不进 ROI，样本量字段如实读）

```json
{ev}
```

## 输出契约（只输出一个 JSON 代码块，别的什么都不要）

```json
{{
  "league": "{league}",
  "appends": [{{"section": "结构性认知|时效|教训", "text": "...",
               "date": "YYYY-MM-DD 或 null", "ttl_days": "仅时效段：正整数或 null",
               "evidence": {{"fixtures": [id...], "stat": "一句话数字摘要"}}}}],
  "amendments": [{{"target": "旧条目锚点如 E0-S01", "text": "替换后的完整正文",
                   "date": "同 appends 规则", "ttl_days": "同 appends 规则",
                   "reason": "修订理由"}}],
  "deprecations": [{{"target": "锚点", "reason": "废弃理由"}}],
  "no_change_reason": "无变更时填理由，有变更时必须为 null"
}}
```

## 纪律（必须遵守）

1. **证据不足时 no_change 优于编造**——弱证据硬写条目是本岗位最严重的错误。
2. appends 必须引 evidence.fixtures（台账里真实存在的场次 id）；结构性认知段
   不带日期，时效段必带 date+ttl_days，教训段带 date。统计数字只放
   evidence.stat 摘要，不进知识正文。
3. 必须显式处理旧条目：本期哪些该 amend（写弱了/写反了）、哪些该 deprecate
   （被证据推翻）；全都不动就说明理由（写进 no_change_reason 或省略字段）。
4. 不得使用任何工具或网络检索；不得引用证据 JSON 之外的信息（你的记忆不算
   证据）。渲染后全文不得超过 {KB_MAX_CHARS} 字符。
"""


def validate_contract(contract: dict, kb: K.KnowledgeFile, league: str) -> list[str]:
    """六条校验（设计档 §6.3）；返回违规清单（空 = 通过）。"""
    errs: list[str] = []
    if contract.get("league") != league:
        return [f"league 不匹配：{contract.get('league')!r} != {league!r}"]
    appends = contract.get("appends") or []
    amendments = contract.get("amendments") or []
    deprecations = contract.get("deprecations") or []
    anchors = {e.anchor: e for e in kb.entries}
    changes = bool(appends or amendments or deprecations)
    ncr = contract.get("no_change_reason")
    if changes == bool(ncr):
        errs.append("变更与 no_change_reason 必须二选一（同时给或同时缺=违规）")
    for i, ap in enumerate(appends):
        section = ap.get("section")
        if section not in K.SECTIONS:
            errs.append(f"appends[{i}].section 非法：{section!r}")
            continue
        d, ttl = ap.get("date"), ap.get("ttl_days")
        if section == "时效" and not (d and isinstance(ttl, int) and ttl > 0):
            errs.append(f"appends[{i}] 时效段须带 date 与正 ttl_days")
        if section != "时效" and (d or ttl is not None):
            errs.append(f"appends[{i}] {section}段不得带 date/ttl_days")
        if d is not None and not _DATE_RE.match(str(d)):
            errs.append(f"appends[{i}].date 格式非法：{d!r}")
        if not (ap.get("text") or "").strip():
            errs.append(f"appends[{i}].text 为空")
        ev = ap.get("evidence") or {}
        if not ev.get("fixtures"):
            errs.append(f"appends[{i}].evidence.fixtures 为空（防事后诸葛强制项）")
    for i, am in enumerate(amendments):
        target = am.get("target")
        if target not in anchors:
            errs.append(f"amendments[{i}].target 不存在：{target!r}")
            continue
        d, ttl = am.get("date"), am.get("ttl_days")
        if anchors[target].ttl_days is not None:      # T 目标：日期/TTL 必须补齐
            if not (d and isinstance(ttl, int) and ttl > 0):
                errs.append(f"amendments[{i}] 时效目标须补齐 date 与 ttl_days")
        else:                                          # S/L 目标：不得带
            if d or ttl is not None:
                errs.append(f"amendments[{i}] 非时效目标不得带 date/ttl_days")
        if not (am.get("text") or "").strip():
            errs.append(f"amendments[{i}].text 为空")
        if d is not None and not _DATE_RE.match(str(d)):
            errs.append(f"amendments[{i}].date 格式非法：{d!r}")
    for i, dep in enumerate(deprecations):
        if dep.get("target") not in anchors:
            errs.append(f"deprecations[{i}].target 不存在：{dep.get('target')!r}")
        if not (dep.get("reason") or "").strip():
            errs.append(f"deprecations[{i}].reason 为空")
    if not errs and changes:
        try:
            new_kb = K.apply_contract(kb, contract)
            text = K.render_kb(new_kb, generated="probe", digest="probe")
            if K.kb_over_cap(text):
                errs.append(f"渲染后全文超 KB_MAX_CHARS={KB_MAX_CHARS} 上限")
        except EvolutionError as exc:
            errs.append(f"契约应用到当前文件失败：{exc}")
    return errs


def reflect_league(conn: sqlite3.Connection, w: Window, league: str) -> dict:
    """一次反思（不落库——持久化归 runner；返回值即 evolution_runs 字段源）。"""
    started = time.monotonic()
    kb_file = K.kb_path(league)
    kb_text = (kb_file.read_text(encoding="utf-8")
               if kb_file.is_file() else None)
    kb_hash_before = K.personas_tree_hash(config.project_root() / "personas")
    ev = window_evidence(conn, w, league)
    base = {"league": league, "proposal_path": None,
            "kb_hash_before": kb_hash_before}
    if ev["kb_track"]["n_judged"] == 0 and ev["nokb_track"]["n_judged"] == 0:
        return {**base, "status": "no_change",
                "no_change_reason": "窗口内该联赛无判决样本",
                "duration_s": round(time.monotonic() - started, 3)}
    prompt = build_reflect_prompt(league, kb_text, ev)
    try:
        out = call_reflect(prompt)
    except ReflectCallError as exc:
        return {**base, "status": exc.reason, "no_change_reason": None,
                "duration_s": round(time.monotonic() - started, 3)}
    try:
        contract = extract_json(out)
    except Exception:
        return {**base, "status": "extract", "no_change_reason": None,
                "duration_s": round(time.monotonic() - started, 3)}
    kb = K.parse_kb(kb_text or "", league)
    errs = validate_contract(contract, kb, league)
    if errs:
        return {**base, "status": "contract",
                "no_change_reason": "；".join(errs)[:500],
                "duration_s": round(time.monotonic() - started, 3)}
    if contract.get("no_change_reason"):
        return {**base, "status": "no_change",
                "no_change_reason": contract["no_change_reason"],
                "duration_s": round(time.monotonic() - started, 3)}
    from fa.evolve.apply import stage_proposal          # 延迟导入避环
    path = stage_proposal(w.idx, league, contract, ev)
    return {**base, "status": "ok", "no_change_reason": None,
            "proposal_path": str(path.relative_to(config.project_root())),
            "duration_s": round(time.monotonic() - started, 3)}
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/evolve/test_reflect.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/fa/evolve/reflect.py docs/evolution/t0-toolset-probe.md tests/evolve/test_reflect.py
git commit -m "feat(evolve): 反思纯函数——T0工具集/prompt/契约六校验（M6 T8）"
```

---

### Task 9: evolve/apply.py——提案暂存 + 关卡原子操作

**Files:**
- Create: `src/fa/evolve/apply.py`
- Test: `tests/evolve/test_apply.py`

**Interfaces:**
- Consumes: Task 2 的 `knowledge.*`、Task 8 的契约 dict
- Produces:
  - `proposals_dir(w_idx: int) -> Path`（`evolution/proposals/w{idx}`）
  - `stage_proposal(w_idx: int, league: str, contract: dict, evidence: dict) -> Path`（返回 `{league}.json` 的**绝对** Path；reflect_league 落库时 `relative_to(project_root())` 转相对路径存 `proposal_path`）
  - `merge_proposal(conn, window_id: int, league: str, note: str) -> str`（写活文件 + Ruling + 关窗刷新；返回建议 commit message）
  - `record_ruling(conn, run_id: int, ruling: str, note: str) -> None`（reject/shelve）
  - `gate_closed(conn, w_idx: int) -> bool`（全联赛终态 = 每联赛有 Ruling 或 status != 'ok'）
  - `close_window_if_terminal(conn, window_id: int) -> None`

- [ ] **Step 1: 写失败测试**

`tests/evolve/test_apply.py`：

```python
"""关卡原子性：暂存渲染/merge 原子一步/重入防护/关窗判定（设计档 §8）。"""
import json
from datetime import date, datetime

import pytest

from fa import config
from fa.evolve import EvolutionError, apply as A, knowledge as K
from fa.evolve.windows import window_bounds


CONTRACT = {"league": "E0",
            "appends": [{"section": "教训", "text": "新教训", "date": "2026-10-16",
                         "ttl_days": None,
                         "evidence": {"fixtures": [1], "stat": "s"}}],
            "amendments": [], "deprecations": [], "no_change_reason": None}


def test_stage_proposal_writes_three_artifacts(tmp_root, kbfile):
    ev = {"league": "E0"}
    path = A.stage_proposal(1, "E0", CONTRACT, ev)
    d = tmp_root / "evolution" / "proposals" / "w1"
    assert path == d / "E0.json"
    assert json.loads((d / "E0.json").read_text()) == CONTRACT
    assert "新教训" in (d / "E0.proposed.md").read_text()
    assert (d / "E0.diff").exists() and (d / "E0.evidence.json").exists()
    # 活文件未被触碰（暂存区语义）
    assert "新教训" not in K.kb_path("E0").read_text()


def test_merge_proposal_atomic_and_reentrant_guard(conn, tmp_root, kbfile):
    wid = _mk_window(conn, 1)
    rid = _mk_run(conn, wid, "E0", path="evolution/proposals/w1/E0.json")
    A.stage_proposal(1, "E0", CONTRACT, {"league": "E0"})
    msg = A.merge_proposal(conn, wid, "E0", note="裁定通过")
    assert "新教训" in K.kb_path("E0").read_text()
    ruling = conn.execute("SELECT * FROM evolution_rulings").fetchone()
    assert ruling["ruling"] == "merged" and ruling["note"] == "裁定通过"
    assert ruling["kb_hash_after"] == K.personas_tree_hash(
        tmp_root / "personas")
    assert "w1" in msg and "E0" in msg        # 建议 commit message
    with pytest.raises(EvolutionError):
        A.merge_proposal(conn, wid, "E0", note="二次裁定")   # 重入防护


def test_reject_leaves_file_untouched(conn, tmp_root, kbfile):
    wid = _mk_window(conn, 1)
    rid = _mk_run(conn, wid, "E0")
    A.record_ruling(conn, rid, "rejected", note="证据链不实")
    assert "新教训" not in K.kb_path("E0").read_text()
    assert conn.execute("SELECT ruling FROM evolution_rulings"
                        ).fetchone()["ruling"] == "rejected"


def test_gate_closed_requires_all_leagues_terminal(conn):
    wid = _mk_window(conn, 1)
    for lg in ("E0", "SP1", "D1", "I1", "F1"):
        _mk_run(conn, wid, lg)
    assert not A.gate_closed(conn, 1)
    # E0 提案被 reject（终态），其余四联赛反思结果置 no_change（终态）
    conn.execute("UPDATE evolution_runs SET status='no_change'"
                 " WHERE window_id=? AND league!='E0'", (wid,))
    A.record_ruling(conn, _run_id(conn, wid, "E0"), "rejected", note="证据不足")
    assert A.gate_closed(conn, 1)
```

（`tmp_root`/`kbfile` 夹具：monkeypatch `fa.config.project_root` 到 tmp 并预置 `personas/knowledge/epl.md`；`_mk_window(conn, idx)` / `_mk_run(conn, wid, league, status='ok', path=None)` / `_run_id(conn, wid, league)` 为直插 evolution 表的辅助函数——测试文件内定义。）

```python
def _mk_window(conn, idx):
    cur = conn.execute("INSERT INTO evolution_windows (idx, opened_at, closes_at)"
                       " VALUES (?, '2026-09-04', '2026-10-16')", (idx,))
    conn.commit()
    return int(cur.lastrowid)


def _mk_run(conn, wid, league, status="ok", path=None):
    cur = conn.execute(
        "INSERT INTO evolution_runs (window_id, league, kb_hash_before, status,"
        " no_change_reason, proposal_path, duration_s, created_at)"
        " VALUES (?, ?, 'h', ?, NULL, ?, 0.1, '2026-10-16T00:00:00Z')",
        (wid, league, status, path))
    conn.commit()
    return int(cur.lastrowid)


def _run_id(conn, wid, league):
    return conn.execute("SELECT id FROM evolution_runs WHERE window_id=? AND"
                        " league=?", (wid, league)).fetchone()["id"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/evolve/test_apply.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `src/fa/evolve/apply.py`**

```python
"""提案暂存与关卡原子操作（设计档 §8，R3 暂存区）。

提案永不触碰 personas/knowledge/ 真实路径——merge 是唯一写入口，且为
原子落账：先写文件、后单事务记 Ruling + 关窗刷新。失败语义：文件已写而
事务失败 → 无 Ruling 行，快照保证本窗不受影响；`fa evolve status` 以活树
hash 与 Ruling 不一致披露。
"""
from __future__ import annotations

import difflib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fa import config
from fa.evolve import EvolutionError
from fa.evolve import knowledge as K

_LEAGUES = tuple(config.PERSONA_FILES)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def proposals_dir(w_idx: int) -> Path:
    return config.project_root() / "evolution" / "proposals" / f"w{w_idx}"


def stage_proposal(w_idx: int, league: str, contract: dict,
                   evidence: dict) -> Path:
    """契约 + 证据 → 暂存区三件套（json / proposed.md / diff）+ 证据留档。"""
    d = proposals_dir(w_idx)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{league}.json").write_text(
        json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / f"{league}.evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    live = K.kb_path(league)
    old_text = live.read_text(encoding="utf-8") if live.is_file() else ""
    kb = K.parse_kb(old_text, league)
    new_text = K.render_kb(K.apply_contract(kb, contract),
                           generated=_now_iso()[:10],
                           digest=K._text_digest(old_text))
    (d / f"{league}.proposed.md").write_text(new_text, encoding="utf-8")
    diff = "".join(difflib.unified_diff(
        old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
        fromfile=f"personas/knowledge/{config.PERSONA_FILES[league]}",
        tofile=f"evolution/proposals/w{w_idx}/{league}.proposed.md"))
    (d / f"{league}.diff").write_text(diff, encoding="utf-8")
    return d / f"{league}.json"


def _run_row(conn, window_id: int, league: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM evolution_runs WHERE window_id=? AND league=?",
        (window_id, league)).fetchone()
    if row is None:
        raise EvolutionError(f"无反思记录：window={window_id} league={league}")
    return row


def _require_unruled(conn, run_id: int) -> None:
    if conn.execute("SELECT 1 FROM evolution_rulings WHERE run_id=?",
                    (run_id,)).fetchone() is not None:
        raise EvolutionError(f"run {run_id} 已裁定——拒绝二次裁定（重入防护）")


def close_window_if_terminal(conn: sqlite3.Connection, window_id: int) -> None:
    """全联赛终态（有 Ruling 或 status != 'ok'）→ 记 closed_at。幂等。"""
    row = conn.execute("SELECT idx FROM evolution_windows WHERE id=?",
                       (window_id,)).fetchone()
    if row is None:
        return
    if gate_closed(conn, row["idx"]):
        conn.execute(
            "UPDATE evolution_windows SET closed_at=COALESCE(closed_at, ?)"
            " WHERE id=?", (_now_iso(), window_id))


def gate_closed(conn: sqlite3.Connection, w_idx: int) -> bool:
    row = conn.execute("SELECT id FROM evolution_windows WHERE idx=?",
                       (w_idx,)).fetchone()
    if row is None:
        return False
    for lg in _LEAGUES:
        r = conn.execute(
            "SELECT er.id FROM evolution_runs ru"
            " LEFT JOIN evolution_rulings er ON er.run_id = ru.id"
            " WHERE ru.window_id=? AND ru.league=?",
            (row["id"], lg)).fetchone()
        if r is None or r["id"] is None:
            ruled = conn.execute(
                "SELECT status FROM evolution_runs WHERE window_id=? AND league=?",
                (row["id"], lg)).fetchone()
            if ruled is None or ruled["status"] == "ok":
                return False        # 未反思/待裁定 = 未终态
    return True


def record_ruling(conn: sqlite3.Connection, run_id: int, ruling: str,
                  note: str) -> None:
    if ruling not in ("rejected", "shelved"):
        raise EvolutionError(f"record_ruling 只收 rejected/shelved：{ruling!r}")
    if not note.strip():
        raise EvolutionError("裁定必须留理由（note 强制非空）")
    _require_unruled(conn, run_id)
    conn.execute(
        "INSERT INTO evolution_rulings (run_id, ruling, kb_hash_after, note,"
        " decided_at) VALUES (?, ?, NULL, ?, ?)", (run_id, ruling, note, _now_iso()))
    wid = conn.execute("SELECT window_id FROM evolution_runs WHERE id=?",
                       (run_id,)).fetchone()["window_id"]
    close_window_if_terminal(conn, wid)
    conn.commit()


def merge_proposal(conn: sqlite3.Connection, window_id: int, league: str,
                   note: str) -> str:
    """原子落账：先写活文件 → 单事务 Ruling(merged) + 关窗刷新。"""
    if not note.strip():
        raise EvolutionError("裁定必须留理由（note 强制非空）")
    run = _run_row(conn, window_id, league)
    if run["status"] != "ok" or not run["proposal_path"]:
        raise EvolutionError(f"{league} 无待裁提案（status={run['status']!r}）")
    _require_unruled(conn, run["id"])
    contract = json.loads(
        (config.project_root() / run["proposal_path"]).read_text(encoding="utf-8"))
    live = K.kb_path(league)
    old_text = live.read_text(encoding="utf-8") if live.is_file() else ""
    kb = K.parse_kb(old_text, league)
    new_text = K.render_kb(K.apply_contract(kb, contract),
                           generated=_now_iso()[:10],
                           digest=K._text_digest(old_text))
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text(new_text, encoding="utf-8")       # 先写文件
    hash_after = K.personas_tree_hash(config.project_root() / "personas")
    with conn:                                        # 后原子落账（单事务）
        conn.execute(
            "INSERT INTO evolution_rulings (run_id, ruling, kb_hash_after, note,"
            " decided_at) VALUES (?, 'merged', ?, ?, ?)",
            (run["id"], hash_after, note, _now_iso()))
        close_window_if_terminal(conn, window_id)
    n_a = len(contract.get("appends") or [])
    n_m = len(contract.get("amendments") or [])
    n_d = len(contract.get("deprecations") or [])
    return (f"feat(kb): w合并 {league} 反思提案（{n_a}增/{n_m}改/{n_d}废）"
            f"——建议随后 git add personas/knowledge/ evolution/ 并提交")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/evolve/test_apply.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/fa/evolve/apply.py tests/evolve/test_apply.py
git commit -m "feat(evolve): 提案暂存+关卡原子操作+重入防护（M6 T9）"
```

---

### Task 10: runner + CLI evolve_app（status/reflect/review/merge/reject/shelve/tick）

**Files:**
- Create: `src/fa/evolve/runner.py`
- Modify: `src/fa/cli.py`（`evolve_app` 注册 + 七个命令）
- Test: `tests/evolve/test_runner.py`、`tests/evolve/test_cli_evolve.py`

**Interfaces:**
- Consumes: Task 1-9 全部
- Produces:
  - `runner.run_tick(conn, today: date | None = None) -> str`（人读摘要；TTL 修剪 → 逐窗反思 → 滚动报告）
  - `runner.run_reflect(conn, w_idx: int | None, league: str | None, calibrate: bool = False, today: date | None = None) -> dict`
  - `runner.run_review(conn, w_idx: int | None, league: str | None) -> str`
  - `runner.evolve_status_text(conn) -> str`
  - CLI：`fa evolve status|reflect|review|merge|reject|shelve|tick`

- [ ] **Step 1: 写失败测试**

`tests/evolve/test_runner.py`：

```python
"""runner：tick 顺延/触发/零样本、calibrate 不落库、review/status 文本。"""
from datetime import date

from fa.evolve import runner


def test_tick_noop_when_no_due_window(conn, monkeypatch):
    monkeypatch.setattr(runner, "_today", lambda: date(2026, 9, 10))
    assert "无到期窗口" in runner.run_tick(conn)


def test_tick_reflects_due_window(conn, seeded, monkeypatch, hermes_ok):
    monkeypatch.setattr(runner, "_today", lambda: date(2026, 10, 20))
    out = runner.run_tick(conn)
    assert "w1" in out
    rows = conn.execute("SELECT league, status FROM evolution_runs").fetchall()
    assert len(rows) == 5                       # 五联赛串行
    assert conn.execute("SELECT reflected_at FROM evolution_windows"
                        " WHERE idx=1").fetchone()["reflected_at"] is not None
    assert not (seeded / "evolution" / "snapshots").exists()   # tick 不建快照（run 才建）


def test_tick_defers_when_previous_gate_open(conn, seeded, monkeypatch, hermes_ok):
    """w1 已反思未关 + w2 已收口 → tick 记顺延、不反思 w2（串行关卡）。"""
    monkeypatch.setattr(runner, "_today", lambda: date(2026, 10, 20))
    runner.run_tick(conn)                        # w1 反思（hermes_ok 出合法契约）
    monkeypatch.setattr(runner, "_today", lambda: date(2026, 12, 1))
    out = runner.run_tick(conn)
    assert "顺延" in out
    assert conn.execute("SELECT COUNT(*) FROM evolution_runs"
                        " WHERE window_id=(SELECT id FROM evolution_windows"
                        " WHERE idx=2)").fetchone()[0] == 0


def test_tick_prunes_ttl_before_reflect(conn, seeded_kb_expired, monkeypatch, hermes_ok):
    monkeypatch.setattr(runner, "_today", lambda: date(2026, 12, 4))
    runner.run_tick(conn)
    assert "E0-T03" not in (seeded_kb_expired / "personas" / "knowledge"
                            / "epl.md").read_text()


def test_run_reflect_calibrate_writes_no_db_rows(conn, seeded, monkeypatch, hermes_ok):
    monkeypatch.setattr(runner, "_today", lambda: date(2026, 10, 20))
    out = runner.run_reflect(conn, 1, "E0", calibrate=True)
    assert out["calibrate"] is True
    assert conn.execute("SELECT COUNT(*) FROM evolution_windows").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM evolution_runs").fetchone()[0] == 0
    assert (seeded / "evolution" / "proposals").exists()
```

`tests/evolve/test_cli_evolve.py`：

```python
"""fa evolve 七命令：参数校验/输出形态/关卡操作接线。"""
from typer.testing import CliRunner

from fa.cli import app


def test_evolve_status_empty_db(smoke_db):
    result = CliRunner().invoke(app, ["evolve", "status"])
    assert result.exit_code == 0
    assert "当前窗" in result.output


def test_evolve_merge_requires_note(conn_with_proposal):
    result = CliRunner().invoke(
        app, ["evolve", "merge", "--window", "1", "--league", "E0",
              "--note", " "])
    assert result.exit_code != 0 and "理由" in result.output


def test_evolve_tick_command_wires_runner(conn, monkeypatch):
    monkeypatch.setattr("fa.evolve.runner.run_tick", lambda c: "tick 摘要")
    result = CliRunner().invoke(app, ["evolve", "tick"])
    assert result.exit_code == 0 and "tick 摘要" in result.output
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/evolve/test_runner.py tests/evolve/test_cli_evolve.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `src/fa/evolve/runner.py`**

```python
"""C 线编排：tick（修剪→反思→报告）、手动反思/校准、人审视图与状态。

``_today`` 是唯一时间缝（默认北京时间今天，测试注入）——tick 的全部窗口
判定经它走（诚实条款：断言不依赖真实时钟）。
"""
from __future__ import annotations

import sqlite3
from datetime import date

from fa import config
from fa.evolve import apply as A
from fa.evolve import knowledge as K
from fa.evolve import report as REP
from fa.evolve import windows as W
from fa.evolve.reflect import reflect_league


def _today() -> date:
    return W.beijing_today()


def _ensure_window_row(conn, w: W.Window) -> int:
    row = conn.execute("SELECT id FROM evolution_windows WHERE idx=?",
                       (w.idx,)).fetchone()
    if row is not None:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO evolution_windows (idx, opened_at, closes_at)"
        " VALUES (?, ?, ?)", (w.idx, w.opened.isoformat(), w.closes.isoformat()))
    conn.commit()
    return int(cur.lastrowid)


def _reflect_window(conn, w: W.Window, leagues: list[str],
                    calibrate: bool = False) -> list[dict]:
    outs = []
    for league in leagues:
        out = reflect_league(conn, w, league)
        if calibrate:
            out["calibrate"] = True
            continue
        wid = _ensure_window_row(conn, w)
        conn.execute(
            "INSERT INTO evolution_runs (window_id, league, kb_hash_before,"
            " status, no_change_reason, proposal_path, duration_s, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (wid, out["league"], out["kb_hash_before"], out["status"],
             out["no_change_reason"], out["proposal_path"], out["duration_s"]))
        conn.commit()
        outs.append(out)
    return outs


def run_tick(conn: sqlite3.Connection, today: date | None = None) -> str:
    """周检：TTL 修剪 → 升序反思到期未反思窗口（前窗关卡未关则顺延）。"""
    d = today or _today()
    pruned = K.prune_live_files(d)
    lines = [f"tick @ {d.isoformat()}：TTL 修剪 "
             + ("；".join(f"{lg}×{len(a)}" for lg, a in pruned) if pruned
                else "无")]
    for w in W.due_windows(d):
        row = conn.execute("SELECT id, reflected_at FROM evolution_windows"
                           " WHERE idx=?", (w.idx,)).fetchone()
        if row is not None and row["reflected_at"] is not None:
            continue
        if w.idx > 1 and not A.gate_closed(conn, w.idx - 1):
            lines.append(f"w{w.idx - 1} 关卡未关，w{w.idx} 顺延（下次 tick 再查）")
            break
        outs = _reflect_window(conn, w, list(config.PERSONA_FILES))
        wid = conn.execute("SELECT id FROM evolution_windows WHERE idx=?",
                           (w.idx,)).fetchone()["id"]
        conn.execute("UPDATE evolution_windows SET reflected_at=datetime('now')"
                     " WHERE id=?", (wid,))
        conn.commit()
        REP.write_event_report(conn, wid, pruned=pruned, today=d)
        lines.append(f"w{w.idx} 反思完成：" + "，".join(
            f"{o['league']}={o['status']}" for o in outs))
    if len(lines) == 1:
        lines.append("无到期窗口")
    return "\n".join(lines)


def run_reflect(conn: sqlite3.Connection, w_idx: int | None = None,
                league: str | None = None, calibrate: bool = False,
                today: date | None = None) -> dict:
    """手动反思：默认最新到期窗；calibrate 用部分窗口证据、零 DB 写。"""
    d = today or _today()
    due = W.due_windows(d)
    if calibrate:
        cur = W.window_of(d)                    # 校准 = 当前窗至今的部分证据
        w = W.window_bounds(w_idx) if w_idx else cur
        leagues = [league] if league else list(config.PERSONA_FILES)
        _reflect_calibrate(conn, w, leagues)
        return {"calibrate": True, "window": w.idx, "leagues": leagues}
    if not due:
        raise ValueError("无已收口窗口可反思（--window 显式指定或等窗口收口）")
    w = W.window_bounds(w_idx) if w_idx else due[-1]
    if w not in due:
        raise ValueError(f"w{w.idx} 尚未收口（反射窗口：{[x.idx for x in due]}）")
    leagues = [league] if league else list(config.PERSONA_FILES)
    outs = _reflect_window(conn, w, leagues)
    wid = conn.execute("SELECT id FROM evolution_windows WHERE idx=?",
                       (w.idx,)).fetchone()["id"]
    conn.execute("UPDATE evolution_windows SET reflected_at=datetime('now')"
                 " WHERE id=?", (wid,))
    conn.commit()
    REP.write_event_report(conn, wid, pruned=[], today=d)
    return {"calibrate": False, "window": w.idx, "results": outs}


def _reflect_calibrate(conn, w, leagues) -> None:
    """校准：真调用、真证据、产物落 calibration 目录、零 DB 行（R2）。"""
    import json
    from fa.evolve.evidence import window_evidence
    from fa.evolve.reflect import build_reflect_prompt, call_reflect
    proposals_root = (config.project_root() / "evolution" / "proposals"
                      / f"calibration-{_today().isoformat()}")
    proposals_root.mkdir(parents=True, exist_ok=True)
    for league in leagues:
        ev = window_evidence(conn, w, league)
        kb_file = K.kb_path(league)
        kb_text = kb_file.read_text(encoding="utf-8") if kb_file.is_file() else None
        try:
            out = call_reflect(build_reflect_prompt(league, kb_text, ev))
        except Exception as exc:                      # 校准连失败都要留痕
            (proposals_root / f"{league}.error.txt").write_text(str(exc))
            continue
        (proposals_root / f"{league}.raw.txt").write_text(out)
        (proposals_root / f"{league}.evidence.json").write_text(
            json.dumps(ev, ensure_ascii=False, indent=2))


def run_review(conn: sqlite3.Connection, w_idx: int | None = None,
               league: str | None = None) -> str:
    """人审视图：状态 + diff 全文 + 证据摘要。"""
    q = ("SELECT ew.idx, er.league, er.status, er.no_change_reason,"
         " er.proposal_path, er.id AS run_id FROM evolution_runs er"
         " JOIN evolution_windows ew ON ew.id = er.window_id"
         " WHERE 1=1")
    args: list = []
    if w_idx:
        q += " AND ew.idx=?"
        args.append(w_idx)
    if league:
        q += " AND er.league=?"
        args.append(league)
    rows = conn.execute(q + " ORDER BY ew.idx, er.league", args).fetchall()
    if not rows:
        return "（无反思记录）"
    lines = []
    for r in rows:
        ruled = conn.execute("SELECT ruling, note FROM evolution_rulings"
                             " WHERE run_id=?", (r["run_id"],)).fetchone()
        head = (f"## w{r['idx']} {r['league']}：{r['status']}"
                + (f"（已裁定 {ruled['ruling']}：{ruled['note']}）" if ruled else ""))
        lines.append(head)
        if r["no_change_reason"]:
            lines.append(f"  理由：{r['no_change_reason']}")
        if r["proposal_path"]:
            diff = (config.project_root() / r["proposal_path"]).parent \
                / f"{r['league']}.diff"
            if diff.is_file():
                lines.append("```diff")
                lines.append(diff.read_text(encoding="utf-8").rstrip())
                lines.append("```")
        lines.append("")
    return "\n".join(lines)


def evolve_status_text(conn: sqlite3.Connection) -> str:
    cur = W.window_of(_today())
    lines = [f"当前窗：w{cur.idx}（{cur.opened} ~ {cur.closes}，北京时间）",
             f"活树 personas_hash：{K.personas_tree_hash(config.project_root() / 'personas')[:12]}…",
             f"本窗快照：{'已建' if K.snapshot_dir(cur.idx).exists() else '未建（首个 run 时建）'}",
             "各联赛 活文件 vs 本窗快照（设计档 §8）："]
    for lg in config.PERSONA_FILES:
        live = K.kb_path(lg)
        live_h = (K._text_digest(live.read_text(encoding="utf-8"))
                  if live.is_file() else "（无活文件）")
        snap_text = K.window_kb_text(cur.idx, lg)
        snap_h = (K._text_digest(snap_text) if snap_text is not None
                  else "（快照无此联赛）")
        mark = "一致" if live_h == snap_h else "不一致（合并待下窗生效/活文件有未裁定变更）"
        lines.append(f"  {lg}：活 {live_h} / 快照 {snap_h} —— {mark}")
    rows = conn.execute("SELECT ew.idx, er.league, er.status, er.proposal_path,"
                        " er.id AS run_id FROM evolution_runs er"
                        " JOIN evolution_windows ew ON ew.id = er.window_id"
                        " ORDER BY ew.idx DESC, er.league LIMIT 20").fetchall()
    if rows:
        lines.append("最近反思：")
        for r in rows:
            ruled = conn.execute("SELECT ruling FROM evolution_rulings"
                                 " WHERE run_id=?", (r["run_id"],)).fetchone()
            lines.append(f"  w{r['idx']} {r['league']} {r['status']}"
                         + (f"→{ruled['ruling']}" if ruled else "→待裁定"
                            if r["status"] == "ok" else ""))
    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM evolution_runs er"
        " LEFT JOIN evolution_rulings el ON el.run_id = er.id"
        " WHERE er.status='ok' AND el.id IS NULL").fetchone()["n"]
    lines.append(f"待裁提案：{pending} 个（fa evolve review 查看）")
    return "\n".join(lines)
```

`src/fa/cli.py`——`retro_app` 注册块之后加：

```python
evolve_app = typer.Typer(help="C 线进化线（M6，spec §12.7）")
app.add_typer(evolve_app, name="evolve")


@evolve_app.command("status")
def evolve_status_cmd() -> None:
    """当前窗/知识库版本/待裁提案"""
    from fa.evolve.runner import evolve_status_text
    conn = connect()
    try:
        typer.echo(evolve_status_text(conn))
    finally:
        conn.close()


@evolve_app.command("tick")
def evolve_tick_cmd() -> None:
    """周检：窗口收口触发反思，否则空转（cron 入口）"""
    from fa.evolve.runner import run_tick
    conn = connect()
    try:
        typer.echo(run_tick(conn))
    finally:
        conn.close()


@evolve_app.command("reflect")
def evolve_reflect_cmd(
    window: int = typer.Option(None, "--window", help="窗口序号；缺省=最新到期窗"),
    league: str = typer.Option(None, "--league", help="联赛码；缺省=五联赛全跑"),
    calibrate: bool = typer.Option(False, "--calibrate",
                                    help="校准模式：不落库、产物进 calibration 目录"),
) -> None:
    """手动反思一个窗口"""
    from fa.evolve.runner import run_reflect
    conn = connect()
    try:
        out = run_reflect(conn, window, league, calibrate=calibrate)
    except ValueError as exc:
        typer.echo(f"参数错误：{exc}")
        raise typer.Exit(code=1)
    finally:
        conn.close()
    if out["calibrate"]:
        typer.echo(f"校准反思完成：w{out['window']} {out['leagues']}——产物在"
                   " evolution/proposals/calibration-*/，未落任何 DB 行")
    else:
        for r in out["results"]:
            typer.echo(f"w{out['window']} {r['league']}：{r['status']}"
                       + (f"（{r['no_change_reason']}）"
                          if r["no_change_reason"] else ""))
        typer.echo("fa evolve review 人审 → merge/reject/shelve 裁定")


@evolve_app.command("review")
def evolve_review_cmd(
    window: int = typer.Option(None, "--window"),
    league: str = typer.Option(None, "--league"),
) -> None:
    """人审视图：状态 + diff + 证据摘要"""
    from fa.evolve.runner import run_review
    conn = connect()
    try:
        typer.echo(run_review(conn, window, league))
    finally:
        conn.close()


def _evolve_ruled_action(ruling: str, window: int, league: str, note: str) -> None:
    from fa.evolve import apply as A
    conn = connect()
    try:
        wrow = conn.execute("SELECT id FROM evolution_windows WHERE idx=?",
                            (window,)).fetchone()
        if wrow is None:
            typer.echo(f"窗口 w{window} 无反思记录")
            raise typer.Exit(code=1)
        if ruling == "merged":
            msg = A.merge_proposal(conn, wrow["id"], league, note)
            typer.echo(f"已合并：{league}（快照钉版——下一窗口生效）")
            typer.echo(f"建议 commit message：{msg}")
        else:
            run_id = conn.execute(
                "SELECT id FROM evolution_runs WHERE window_id=? AND league=?",
                (wrow["id"], league)).fetchone()
            if run_id is None:
                typer.echo(f"{league} 无反思记录")
                raise typer.Exit(code=1)
            A.record_ruling(conn, run_id["id"], ruling, note)
            typer.echo(f"已裁定 {ruling}：{league}（note 已入 Ruling 台账）")
    finally:
        conn.close()


@evolve_app.command("merge")
def evolve_merge_cmd(
    window: int = typer.Option(..., "--window"),
    league: str = typer.Option(..., "--league"),
    note: str = typer.Option(..., "--note", help="裁定理由（强制）"),
) -> None:
    """合并提案：写知识文件 + Ruling 原子一步（下一窗口生效）"""
    _evolve_ruled_action("merged", window, league, note)


@evolve_app.command("reject")
def evolve_reject_cmd(
    window: int = typer.Option(..., "--window"),
    league: str = typer.Option(..., "--league"),
    note: str = typer.Option(..., "--note", help="裁定理由（强制）"),
) -> None:
    """拒绝提案（只记 Ruling）"""
    _evolve_ruled_action("rejected", window, league, note)


@evolve_app.command("shelve")
def evolve_shelve_cmd(
    window: int = typer.Option(..., "--window"),
    league: str = typer.Option(..., "--league"),
    note: str = typer.Option(..., "--note", help="裁定理由（强制）"),
) -> None:
    """搁置提案（记 Ruling，提案留暂存区供复看）"""
    _evolve_ruled_action("shelved", window, league, note)
```

- [ ] **Step 4: 跑测试确认通过（先补 Task 12 的 report 空实现桩——见该任务 Step 1 说明，本任务测试依赖 `fa.evolve.report.write_event_report` 存在）**

Run: `uv run pytest tests/evolve/ -v`
Expected: PASS

（实现顺序注：`runner` import `report`，`report` 在 Task 12 才真实现——本任务先建 `src/fa/evolve/report.py` 空桩（`def write_event_report(conn, window_id, *, pruned=(), today=None): return None`）与空测试文件，Task 12 替换为真实现。桩不留到最后——不留半成品 import 错误。）

- [ ] **Step 5: 提交**

```bash
git add src/fa/evolve/runner.py src/fa/evolve/report.py src/fa/cli.py tests/evolve/
git commit -m "feat(evolve): runner编排+fa evolve七命令+calibrate零落库（M6 T10）"
```

---

### Task 11: cron 第 5 个 job（evolve 周检）

**Files:**
- Modify: `scripts/fa_cron.sh`（合法 job 集合 + case 分支）
- Modify: `scripts/cron_jobs.txt`（事实单加一行）
- Test: `tests/pipeline/test_ops.py`（追加，grep/语法级断言）

**Interfaces:**
- Consumes: Task 10 的 `fa evolve tick`
- Produces: `fa_cron.sh evolve` 分支（`CMD=(fa evolve tick)`）；事实单行 `evolve\t17 3 * * 0\tevolve`（周日 03:17，避开 daily 06:30 / am 11:00 / pm 17:00 / weekly 周一 07:00）

- [ ] **Step 1: 写失败测试**

`tests/pipeline/test_ops.py` 追加：

```python
# ---------------------------------------------------------------- M6 evolve job


def test_cron_wrapper_accepts_evolve_job():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    script = (root / "scripts" / "fa_cron.sh").read_text()
    assert '"$JOB" != evolve' in script
    assert "evolve)  CMD=(fa evolve tick) ;;" in script


def test_cron_jobs_file_has_evolve_weekly_slot():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    jobs = (root / "scripts" / "cron_jobs.txt").read_text()
    line = next(l for l in jobs.splitlines() if l.startswith("evolve\t"))
    assert line.split("\t")[1] == "17 3 * * 0"


def test_cron_wrapper_evolve_branch_bash_syntax():
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(["bash", "-n", str(root / "scripts" / "fa_cron.sh")],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/pipeline/test_ops.py -v -k evolve`
Expected: FAIL

- [ ] **Step 3: 实现**

`scripts/fa_cron.sh`：

1. 用法行：`# 用法: fa_cron.sh <daily|am|pm|weekly>` → `# 用法: fa_cron.sh <daily|am|pm|weekly|evolve>`
2. 合法集合判定行加 `&& "$JOB" != evolve`：

```bash
if [[ "$JOB" != daily && "$JOB" != am && "$JOB" != pm && "$JOB" != weekly && "$JOB" != evolve ]]; then
```

3. case 块加分支（weekly 行之后）：

```bash
  # M6（§12.7）：C 线周检——窗口收口才触发反思，否则一行日志空转退出 0。
  # 周日 03:17（事实单 scripts/cron_jobs.txt）：避开 daily/am/pm/weekly。
  # 失败告警沿用本 wrapper 的 fa ops alert（「静默停摆」=不触碰 A/B 线，
  # 不指对运维静默——设计档 R7）
  evolve) CMD=(fa evolve tick) ;;
```

`scripts/cron_jobs.txt` 末尾加一行（TAB 分隔）：

```
evolve	17 3 * * 0	evolve
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/pipeline/test_ops.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/fa_cron.sh scripts/cron_jobs.txt tests/pipeline/test_ops.py
git commit -m "feat(cron): evolve周检job——fa_cron.sh第5分支+事实单（M6 T11）"
```

---

### Task 12: report.py 真实现——进化事件滚动报告

**Files:**
- Modify: `src/fa/evolve/report.py`（替换 Task 10 的空桩）
- Test: `tests/evolve/test_report.py`

**Interfaces:**
- Consumes: Task 9/10 的表结构与暂存产物（`{league}.json` / `{league}.evidence.json`）
- Produces: `write_event_report(conn, window_id: int, *, pruned: list[tuple[str, list[str]]] = (), today: date | None = None) -> Path`（写 `docs/evolution/report-{closes 日期}.md`，返回其路径）

**报告内容（设计档 §10，descriptive 不设终止判据）**：
1. 头部：窗口区间、事件时刻、TTL 修剪明细（联赛×锚点）
2. 各联赛：status / Ruling / 变更计数（appends/amendments/deprecations——从暂存 json 统计）/**被推翻或削弱旧条目数**（= deprecations + amendments，知识库健康度）
3. kb vs nokb 轨道级对照（ROI / 误杀数 / CLV 中位——读暂存 evidence.json）
4. 基线期标注：本窗口无任何 merged Ruling 时，明示「基线期——两轨差异即 persona 调用噪声底，非知识库效应」
5. 版本戳串联：本窗 recommendations 的 `personas_hash` distinct 计数
6. 尾部固定声明：统计判据为 ≥2 窗口后的后续注册项（届时先改 spec §12.7 再启用，不回溯）；校准轮结论不得作为有效性证据

- [ ] **Step 1: 写失败测试**

`tests/evolve/test_report.py`：

```python
"""滚动报告：健康度计数/基线期标注/版本戳串联/固定声明（设计档 §10）。"""
import json
from datetime import date
from pathlib import Path

from fa import config
from fa.evolve import report as REP


def test_write_event_report_content(tmp_root, conn_with_event):
    """conn_with_event：w1 五联赛反思（E0 ok+契约三件套、其余 no_change），
    E0 已 merge；窗 1 内种两批不同 personas_hash 的 recommendations 行。"""
    path = REP.write_event_report(conn_with_event, _wid(conn_with_event, 1),
                                  pruned=[("E0", ["E0-T03"])],
                                  today=date(2026, 10, 18))
    assert path == tmp_root / "docs" / "evolution" / "report-2026-10-16.md"
    text = path.read_text(encoding="utf-8")
    assert "E0-T03" in text                       # TTL 修剪明细
    assert "推翻" in text and "削弱" in text         # 健康度（amend+deprecate）
    assert "kb" in text and "nokb" in text         # 轨道对照
    assert "版本戳" in text or "personas_hash" in text
    assert "后续注册" in text                       # 固定声明
    assert "基线期" not in text or "噪声底" not in text   # 有 merged → 非基线期


def test_write_event_report_baseline_period_note(tmp_root, conn_no_merge):
    """无任何 merged → 基线期标注（噪声底披露）。"""
    path = REP.write_event_report(conn_no_merge, _wid(conn_no_merge, 1))
    text = path.read_text(encoding="utf-8")
    assert "基线期" in text and "噪声底" in text
```

（`tmp_root` monkeypatch `fa.config.project_root`；`_wid` 查窗口 id。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/evolve/test_report.py -v`
Expected: FAIL（桩返回 None）

- [ ] **Step 3: 实现 `src/fa/evolve/report.py`（替换桩）**

```python
"""进化事件滚动报告（设计档 §10）：descriptive、不设终止判据（同线 A 风格）。

每窗一份 docs/evolution/report-{closes 日期}.md。统计判据为 ≥2 窗口后的
后续注册项——届时先改 spec §12.7 再启用，不回溯套用；校准轮结论不得作为
知识库有效性证据（固定声明常驻尾部）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from fa import config


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt(x, pct=True):
    if x is None:
        return "—"
    return f"{x:+.2%}" if pct else f"{x:.4f}"


def write_event_report(conn: sqlite3.Connection, window_id: int, *,
                       pruned: list[tuple[str, list[str]]] = (),
                       today: date | None = None) -> Path:
    win = conn.execute("SELECT * FROM evolution_windows WHERE id=?",
                       (window_id,)).fetchone()
    closes = win["closes_at"]
    root = config.project_root()
    path = root / "docs" / "evolution" / f"report-{closes}.md"
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [f"# C 线进化事件报告 — 窗口 w{win['idx']}（{win['opened_at']} ~ {closes}）",
             "", f"- 事件时刻：{_now_iso()}（UTC）",
             f"- 报告基准日：{today.isoformat() if today else _now_iso()[:10]}",
             "- TTL 修剪：" + ("；".join(f"{lg}×{len(a)}（{', '.join(a)}）"
                                          for lg, a in pruned) if pruned else "无"),
             ""]
    runs = conn.execute(
        "SELECT * FROM evolution_runs WHERE window_id=? ORDER BY league",
        (window_id,)).fetchall()
    n_merged = 0
    dep_total = am_total = 0
    for r in runs:
        ruling = conn.execute(
            "SELECT ruling, note FROM evolution_rulings WHERE run_id=?",
            (r["id"],)).fetchone()
        head = f"## {r['league']}：{r['status']}"
        if ruling:
            head += f" → {ruling['ruling']}（{ruling['note']}）"
            n_merged += 1 if ruling["ruling"] == "merged" else 0
        lines.append(head)
        if r["no_change_reason"]:
            lines.append(f"- 说明：{r['no_change_reason']}")
        if r["proposal_path"]:
            pdir = (root / r["proposal_path"]).parent
            cpath = pdir / f"{r['league']}.json"
            epath = pdir / f"{r['league']}.evidence.json"
            if cpath.is_file():
                contract = json.loads(cpath.read_text(encoding="utf-8"))
                n_a = len(contract.get("appends") or [])
                n_m = len(contract.get("amendments") or [])
                n_d = len(contract.get("deprecations") or [])
                am_total += n_m
                dep_total += n_d
                lines.append(f"- 变更：+{n_a} / 改{n_m} / 废{n_d}"
                             f"；**被推翻或削弱旧条目 {n_m + n_d}**（健康度）")
            if epath.is_file():
                ev = json.loads(epath.read_text(encoding="utf-8"))
                kb, nokb = ev["kb_track"], ev["nokb_track"]
                lines.append(
                    f"- kb 轨：判 {kb['n_judged']} 场（veto {kb['verdicts']['veto']}"
                    f" / down {kb['verdicts']['downweight']}），ROI {_fmt(kb['roi'])}，"
                    f"CLV 中位 {_fmt(kb['clv_median'])}，误杀 {len(ev['kills'])} 例")
                lines.append(
                    f"- nokb 轨：判 {nokb['n_judged']} 场，ROI {_fmt(nokb['roi'])}，"
                    f"CLV 中位 {_fmt(nokb['clv_median'])}；轨间分歧"
                    f" {len(ev['divergences'])} 场")
                lines.append(f"- 未结算注 {ev['n_pending_settlement']}（不进 ROI）")
        lines.append("")

    lines.append("## 版本戳串联")
    for row in conn.execute(
            "SELECT r.strategy, r.personas_hash, COUNT(*) AS n FROM recommendations r"
            " JOIN fixtures f ON f.id = r.fixture_id"
            " WHERE date(r.created_at, '+8 hours') >= ?"
            " AND date(r.created_at, '+8 hours') < ?"
            " GROUP BY r.strategy, r.personas_hash ORDER BY n DESC",
            (win["opened_at"], closes)):
        h = row["personas_hash"] or "（纪元前 NULL）"
        lines.append(f"- {row['strategy']} × {row['n']} 行：{h[:12]}…")
    lines.append("")
    if n_merged == 0:
        lines.append("## 基线期标注")
        lines.append("- 本窗口无 merged 版本——kb/nokb 两轨 prompt 逐字相同，"
                     "其差异为 **persona 调用噪声底**，不是知识库效应。")
        lines.append("")
    lines.append("## 判据声明（固定）")
    lines.append("- 统计判据为 ≥2 个窗口数据积累后的**后续注册项**：届时先改"
                 " spec §12.7 再启用，不得回溯套用。")
    lines.append("- 校准轮（calibration-*）结论不得作为知识库有效性证据；"
                 "三轨口径分开表述，禁调参凑结论。")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
```

- [ ] **Step 4: 跑测试确认通过（evolve 全量）**

Run: `uv run pytest tests/evolve/ -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/fa/evolve/report.py tests/evolve/test_report.py
git commit -m "feat(evolve): 事件滚动报告——健康度/基线期/版本戳串联（M6 T12）"
```

---

### Task 13: 脚手架 E2E——完整闭环 + 三降级 + 快照钉版

**Files:**
- Test: `tests/evolve/test_e2e.py`

**Interfaces:**
- Consumes: Task 1-12 全链路
- Produces: 无新生产代码（E2E 只测）——发现缺陷按 systematic-debugging 修在生产码里

**闭环剧本（一个测试函数走完）**：
1. tmp 项目根（真 personas/epl.md 拷贝 + git init 使 `git_aux` 有值）
2. FA_DB 指向 tmp 库；种子 w1 内三轨判决行（kb 轨 veto 一场 + agree 若干）
3. `runner.run_tick(today=w2 日期)`（HERMES_BIN 契约 fixture）→ w1 反思 + 提案 + 报告
4. `apply.merge_proposal`（note 裁定）→ 活文件出现新条目、Ruling 落账
5. 快照钉版断言：`window_kb_text(2, "E0")` 仍是旧内容（w2 快照在 merge 前已建——种子阶段建）；`window_kb_text(3, "E0")` 含新条目
6. `run_persona_phase`（HERMES_BIN 按**调用序**分档落 prompt）：第 1 条 prompt（kb）含新知识条目、第 2 条（nokb）不含
7. 全库断言：`personas_hash` 新旧行并存（w1 行 NULL、w2 行 64 hex）

三降级（三个独立测试函数，验收判据原文）：
- **反思超时**：HERMES_BIN = sleep 大于 FA_PERSONA_TIMEOUT（env 设 1s，脚本 sleep 5）→ `evolution_runs.status='timeout'`，知识文件与暂存区均无变化
- **契约破损**：HERMES_BIN 输出合法 JSON 但 `evidence.fixtures` 为空 → `status='contract'`
- **关卡拒绝**：正常反思后 `record_ruling(rejected)` → 活文件不变、Ruling 在案、`gate_closed` 语义正确

- [ ] **Step 1: 写失败测试**

`tests/evolve/test_e2e.py`：

```python
"""M6 脚手架 E2E：完整闭环 + 三降级 + 快照钉版（设计档 §11.2，验收判据）。"""
import json
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from fa import config
from fa.evolve import apply as A, knowledge as K, runner, windows
from fa.persona.apply import run_persona_phase


CONTRACT = {"league": "E0",
            "appends": [{"section": "教训", "text": "密集期 downweight 需更谨慎",
                         "date": "2026-10-16", "ttl_days": None,
                         "evidence": {"fixtures": [1], "stat": "2 注误杀"}}],
            "amendments": [], "deprecations": [], "no_change_reason": None}


@pytest.fixture
def root(tmp_path, monkeypatch):
    """真 personas 拷贝 + git init + project_root 重定向。"""
    repo = Path(__file__).resolve().parents[2]
    personas = tmp_path / "personas"
    personas.mkdir()
    shutil.copy2(repo / "personas" / "epl.md", personas / "epl.md")
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def db(root, tmp_path, monkeypatch):
    """FA_DB 指向 tmp 库；w1 内种三轨判决行（fixture 1：kb veto / nokb agree /
    model_only 已结算注）。种子 SQL 复用 tests/evolve/conftest.py 的
    seed_window_rows（Task 7 建立）。"""
    p = tmp_path / "e2e.db"
    monkeypatch.setenv("FA_DB", str(p))
    from fa import db as dbmod
    dbmod.init_db(p)
    conn = dbmod.connect(p)
    seed_window_rows(conn)          # w1（2026-09-05）fixture 1 三轨 + 结算注
    return conn


def _hermes_contract(root):
    s = root / "hermes_contract.sh"
    s.write_text("#!/usr/bin/env bash\necho " + json.dumps(CONTRACT) + "\n")
    s.chmod(0o755)
    return str(s)


def _hermes_seq_dumper(root, seq_file, out_file):
    """按调用序把 prompt 落盘（1→kb、2→nokb），回合法 persona JSON。"""
    s = root / "hermes_seq.sh"
    s.write_text(
        "#!/usr/bin/env bash\n"
        f'n=$(cat "{seq_file}" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "{seq_file}"\n'
        f'[ "$n" = 1 ] && cp /dev/stdin "{root}/kb.prompt" || cp /dev/stdin "{root}/nokb.prompt"\n'
        'echo \'{"verdict":"agree","confidence_delta":0.0,"key_factors":["x"],'
        '"report_md":"r"}\'\n')
    s.chmod(0o755)
    return str(s)


def test_full_loop_merge_snapshot_and_prompt(root, db, monkeypatch):
    # w2 开始：先建 w2 快照（= 旧/空知识），merge 后 w2 仍读旧、w3 读新
    K.ensure_window_snapshot(2)
    monkeypatch.setattr(windows, "beijing_today", lambda: date(2026, 10, 20))
    monkeypatch.setenv("HERMES_BIN", _hermes_contract(root))
    out = runner.run_tick(db)
    assert "w1" in out and "E0=ok" in out
    wid = db.execute("SELECT id FROM evolution_windows WHERE idx=1").fetchone()["id"]
    msg = A.merge_proposal(db, wid, "E0", note="证据链核实通过")
    assert "密集期 downweight 需更谨慎" in K.kb_path("E0").read_text()
    # 快照钉版：w2 快照在 merge 前已建 → 旧内容；w3 未建 → merge 后建 = 新内容
    assert K.window_kb_text(2, "E0") in (None, "") or \
        "密集期" not in (K.window_kb_text(2, "E0") or "")
    K.ensure_window_snapshot(3)
    assert "密集期" in K.window_kb_text(3, "E0")
    # 报告落盘
    assert any((root / "docs" / "evolution").glob("report-2026-10-16.md"))
    # 新 run 的 persona 双轨：kb prompt 带新知识、nokb 不带
    monkeypatch.setattr(windows, "beijing_today", lambda: date(2026, 11, 1))  # w3
    run2 = _mk_run(db)
    seed_window_rows(db, run_id=run2, day="2026-11-01", fixture_id=2)
    monkeypatch.setenv("HERMES_BIN", _hermes_seq_dumper(root, root / "seq",
                                                        root / "kb.prompt"))
    run_persona_phase(db, run2, ["E0"])
    kb_prompt = (root / "kb.prompt").read_text()
    nokb_prompt = (root / "nokb.prompt").read_text()
    assert "联赛知识库" in kb_prompt and "密集期" in kb_prompt
    assert "联赛知识库" not in nokb_prompt


def test_degradation_reflect_timeout(root, db, monkeypatch):
    s = root / "hermes_sleep.sh"
    s.write_text("#!/usr/bin/env bash\nsleep 5\n")
    s.chmod(0o755)
    monkeypatch.setenv("HERMES_BIN", str(s))
    monkeypatch.setenv("FA_PERSONA_TIMEOUT", "1")
    monkeypatch.setattr(windows, "beijing_today", lambda: date(2026, 10, 20))
    runner.run_tick(db)
    row = db.execute("SELECT status FROM evolution_runs WHERE league='E0'"
                     ).fetchone()
    assert row["status"] == "timeout"
    assert not K.kb_path("E0").exists()
    assert not (root / "evolution" / "proposals" / "w1" / "E0.json").exists()


def test_degradation_contract_broken(root, db, monkeypatch):
    bad = dict(CONTRACT)
    bad["appends"] = [dict(CONTRACT["appends"][0],
                           evidence={"fixtures": [], "stat": ""})]
    s = root / "hermes_bad.sh"
    s.write_text("#!/usr/bin/env bash\necho " + json.dumps(bad) + "\n")
    s.chmod(0o755)
    monkeypatch.setenv("HERMES_BIN", str(s))
    monkeypatch.setattr(windows, "beijing_today", lambda: date(2026, 10, 20))
    runner.run_tick(db)
    row = db.execute("SELECT status, no_change_reason FROM evolution_runs"
                     " WHERE league='E0'").fetchone()
    assert row["status"] == "contract" and row["no_change_reason"]
    assert not K.kb_path("E0").exists()


def test_degradation_gate_reject(root, db, monkeypatch):
    monkeypatch.setattr(windows, "beijing_today", lambda: date(2026, 10, 20))
    monkeypatch.setenv("HERMES_BIN", _hermes_contract(root))
    runner.run_tick(db)
    wid = db.execute("SELECT id FROM evolution_windows WHERE idx=1"
                     ).fetchone()["id"]
    rid = db.execute("SELECT id FROM evolution_runs WHERE window_id=? AND"
                     " league='E0'", (wid,)).fetchone()["id"]
    A.record_ruling(db, rid, "rejected", note="证据引用与台账不符")
    assert not K.kb_path("E0").exists()          # 拒绝 = 活文件不动
    assert db.execute("SELECT ruling FROM evolution_rulings WHERE run_id=?",
                      (rid,)).fetchone()["ruling"] == "rejected"
```

（`_mk_run` / `seed_window_rows` 在 `tests/evolve/conftest.py`——Task 7 建立的共享夹具；`seed_window_rows(conn, run_id=None, day="2026-09-05", fixture_id=1)` 缺省建新 run。）

- [ ] **Step 2: 跑测试确认失败→通过循环**

Run: `uv run pytest tests/evolve/test_e2e.py -v`
Expected: 初跑可能暴露接线缺陷（如 conftest 种子缺字段）——按 systematic-debugging 定位修在生产码/夹具，直至 PASS（4 条）

- [ ] **Step 3: 全量回归**

Run: `uv run pytest -q`
Expected: 全绿（任何既有用例失败先修再继续——M6 不许带伤合入）

- [ ] **Step 4: 提交**

```bash
git add tests/evolve/test_e2e.py tests/evolve/conftest.py
git commit -m "test(evolve): 脚手架E2E——闭环+三降级+快照钉版（M6 T13）"
```

---

### Task 14: spec.md v0.11 增补 + 宪章档小改

**Files:**
- Modify: `spec.md`（changelog 行、§6.6 三轨表述、§10 M6 行、新增 §12.7）
- Modify: `docs/superpowers/specs/2026-09-04-m6-evolution-line-design.md`（§9 编号 + 裁定补记）
- Test: 无新增测试文件——用 grep 断言核验（步骤内）

**Interfaces:**
- Consumes: 设计档 §12 增补草案文本
- Produces: spec v0.11（§12.7 进化线）；宪章档编号同步

- [ ] **Step 1: spec.md 四处编辑**

1. 版本头 changelog 区（v0.10 → v0.11 行）：

```markdown
> v0.10 → v0.11 变更：新增 §12.7 进化线（C 线，M6）——知识库版本化外置 + hermes -z 反思纯函数 + 窗口快照冻结 + 人审关卡；B 线扩第三轨 model_persona_nokb（C 线同期对照，TG 推送保持双轨口径，§12.3 判据口径不变）；recommendations 增 personas_hash 版本戳（schema v8）（2026-09-04 设计评审，docs/superpowers/specs/2026-09-04-m6-evolution-implementation-design.md）。
```

2. §6.6（A/B 双轨表述处）段尾补一句（原文不动）：

```markdown
M6 起同刻三落（§12.7）：第三轨 `model_persona_nokb` 为 C 线对照轨（人格无
知识库、paper 独立 bankroll）；TG 推送正文保持双轨口径（nokb 不进推送），
§12.3 预注册判据的 A/B 对比口径不变（仍 model_only vs model_persona）。
```

3. §10 里程碑表 M6 行（若无则新增；有则替换）——验收判据按 R2 口径：

```markdown
| M6 | C 线进化栈：知识库文件 + 反思任务（hermes -z 纯函数）+ 生产第三轨对照 + 版本戳入账 + 快照冻结 + 人审关卡 + 判据预注册 | 脚手架 E2E 全闭环（反思→diff→人审→合并→新窗口判决带新版本戳、nokb 轨无 KB）；降级路径实测三例（反思超时/契约破损/关卡拒绝）；校准实跑（真 hermes 真台账，预期 no_change）；C 线判据文档预注册存档。真实 W1 首合并（~2026-10-15）为监控点不阻塞验收 | M4 合并✓、M5 节流✓ |
```

4. §12.6 复盘归因子线之后新增整节（文本 = 设计档 §12 的 §12.7 条目逐字）：

```markdown
### 12.7 进化线（C 线，M6）

新增第三条顶层线路 C 线（进化线）：以「反思 → 提案 → 关卡 → 合并」闭环演进
B 线消费的版本化工件（persona 知识库 `personas/knowledge/*.md`；人格文件
v1 不在反思契约内）。状态一律外置 git 版本化文件，agent 无记忆（hermes/dsh
均纯函数调用）。进化事件离线独立调度（周检 tick，B 线 §12.3 前向窗口收口
触发），只读 B 线台账、只写自有表与版本化工件，失败静默停摆（记
`evolution_runs`）不影响 A/B 线。判决入账强制记 personas 树内容 hash
（`recommendations.personas_hash`）；窗口冻结机械化为快照——B 线 prompt 只读
`evolution/snapshots/w{idx}/`，合并最早于下一窗口生效，关卡逾期自然顺延。
C 线对照采用生产第三轨 `model_persona_nokb`（人格无知识库、paper 独立
bankroll、不进 TG 推送正文）；§12.3 判据口径不变。合并经人审关卡：暂存区
提案 + merge/reject/shelve 全量记 Ruling（note 强制）；v1 不设统计合并门槛
（样本量不可达，诚实注册），统计判据为 ≥2 窗口后后续注册项。进化只沉淀
定性知识，不做统计调参——数字归模型与 Python 主控。设计文档：
docs/superpowers/specs/2026-09-04-m6-evolution-line-design.md 与
2026-09-04-m6-evolution-implementation-design.md。
```

- [ ] **Step 2: 宪章档小改**

`docs/superpowers/specs/2026-09-04-m6-evolution-line-design.md`：

1. §9 标题与首段：「**编号说明**：§12.5 已被范式对比线占用……本线取 §12.6，若其编号变动则跟随，不抢号」改为「**编号说明**：§12.5 范式对比线、§12.6 复盘归因子线先后占用（spec §12.6 末尾已注明本线「不抢号、跟随」），本线最终取 **§12.7**（spec v0.11 落地）」。
2. 文末追加裁定补记：

```markdown
## 15. 实施设计评审补记（2026-09-04，负责人参与）

七项裁定（R1–R7）与全部实施定稿见
`2026-09-04-m6-evolution-implementation-design.md` §0；要点：KB 对照采用
生产第三轨 model_persona_nokb；验收 = 脚手架闭环 + 校准实跑（真实 W1 首合并
~2026-10-15 为监控点不阻塞）；关卡 = 暂存区提案（personas/knowledge/ 只收已
裁定版本）；调度 = fa_cron.sh 周检 tick；版本戳 = personas 树内容 hash；
冻结机械化 = 窗口快照（B 线 prompt 只读 snapshots/w{idx}/）。
```

- [ ] **Step 3: grep 核验**

Run:
```bash
grep -c "12.7 进化线" spec.md && grep -c "model_persona_nokb" spec.md \
  && grep -c "§12.7" docs/superpowers/specs/2026-09-04-m6-evolution-line-design.md
```
Expected: 三者均 ≥ 1。

- [ ] **Step 4: 提交**

```bash
git add spec.md docs/superpowers/specs/2026-09-04-m6-evolution-line-design.md
git commit -m "docs(spec): v0.11——§12.7进化线+三轨表述+M6验收口径（M6 T14）"
```

---

### Task 15: 校准实跑 + M6 验收清单核对（人工关卡步）

**Files:**
- Create: `docs/evolution/calibration-2026-09-04.md`（校准记录，人写/命令输出粘贴）
- 无生产代码

**Interfaces:**
- Consumes: 已部署的 `fa evolve reflect --calibrate`；真生产库 `data/fa.db`；真 hermes
- Produces: 校准产物目录 `evolution/proposals/calibration-{date}/`（raw/evidence/error）

**前置确认（每步都有验证）**：

- [ ] **Step 1: 确认生产库迁移已发生**

Run: `uv run fa init && sqlite3 data/fa.db "SELECT version FROM schema_version; SELECT COUNT(*) FROM evolution_windows;"`
Expected: version=8；evolution_windows 计数 0；`data/fa.db.bak-v8` 存在。

- [ ] **Step 2: 校准反思（真 hermes、真台账、零落库）**

Run: `uv run fa evolve reflect --calibrate --league E0`
Expected: exit 0，输出「校准反思完成：w1 …未落任何 DB 行」；`evolution/proposals/calibration-2026-09-04/E0.raw.txt` 与 `E0.evidence.json`（或 `.error.txt`）存在。

- [ ] **Step 3: 校准产物检查 + 记录**

人工读 raw 输出：预期是 `no_change_reason`（当前窗口样本极少——W1 刚开始）或少量带 evidence 的 appends。**无论哪种都合法**；把输出摘要与判读写进 `docs/evolution/calibration-2026-09-04.md`（诚实条款：校准轮结论不作为知识库有效性证据，文件里写明这句）。

Run: `sqlite3 data/fa.db "SELECT COUNT(*) FROM evolution_runs;"`
Expected: 0（校准零落库的机械验证）。

- [ ] **Step 4: cron 装配**

Run: `scripts/cron_install.sh && crontab -l | grep evolve`
Expected: 标记块内出现 `17 3 * * 0 .../fa_cron.sh evolve ...` 行。

- [ ] **Step 5: M6 验收清单核对（对照 spec §10 M6 行逐项打勾并记录）**

1. 脚手架 E2E 全闭环：`uv run pytest tests/evolve/test_e2e.py -v` 全绿（Task 13）
2. 降级三例实测：同上文件三个 degradation 用例绿
3. 校准实跑：Step 2-3 完成
4. C 线判据文档预注册存档：spec §12.7 + 设计档 §10 已入库（Task 14）
5. 真实 W1 首合并（~2026-10-15）：**监控点，不阻塞**——加进待办读数清单

- [ ] **Step 6: 提交**

```bash
git add docs/evolution/calibration-2026-09-04.md
git commit -m "docs(evolve): 校准实跑记录——W1早期样本+零落库验证（M6 T15）"
```

---

## 附：任务依赖图

```
T1 windows ─┬─→ T2 knowledge ─┬─→ T4 value/三轨/hash ─→ T5 persona双轨 ─→ T6 render
            │                 │        ↑
            │        T3 schema v8 ─────┘
            │                 └─→ T7 evidence ─→ T8 reflect ─→ T9 apply ─→ T10 runner/CLI
            │                                                      │
            │                                    T11 cron ←────────┤
            │                                    T12 report ←──────┤
            └────────────────────────────────────── T13 E2E ←──────┤
                                                                   → T14 docs → T15 校准/验收
```

## 附：九条自检清单的应用记录（writing-plans-quality-checklist）

1. **时间炸弹**：全部窗口判定经 `windows.beijing_today` / `runner._today` 注入缝；测试无裸 `date.today()`。
2. **方向性代入**：ROI=(return−stake)/stake、CLV=odds_taken/closing−1（沿 M3 口径）、误杀对照收益方向已在 Task 7 测试里以具体数字（22/10→1.2）代入验证。
3. **阈值可达性**：KB_MAX_CHARS=2400 的上/下界测试（恰好 2400 过、2401 违规）先验算；TTL 90d 过期日用 2026-12-03/04 边界两侧验证。
4. **mock/补丁顺序**：evolve 内一律 `from fa import config` 属性访问；HERMES_BIN fixture 先写脚本后 setenv。
5. **命名契约**：锚点 `E0-S01` 形、文件名沿用 PERSONA_FILES、表名列名与 DDL 逐一核对。
6. **单一事实源**：STRATEGIES/知识语法/cron 时刻单源三处，计划内无第二定义。
7. **环境敏感性**：conftest 已有 `_TYPER_FORCE_DISABLE_TERMINAL`（既有）；E2E 的 FA_DB/HERMES_BIN 均 monkeypatch，不漏真机环境。
8. **防呆段落禁用**：计划中无「先错后改」双版本代码块。
9. **诚实条款预写**：报告固定声明、基线期噪声底标注、校准轮不作数、三轨口径分开——均落在生产文本与测试断言里。
