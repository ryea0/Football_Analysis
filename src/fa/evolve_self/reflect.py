"""C' 线反思：hermes -z 自由式反思 + markdown 校验。

与 C 线 reflect.py 的差异：
- 输出：markdown 全文（直接写知识库） vs JSON 契约
- 校验：宽松（合法 md + 标题 + 字符上限 + 保留旧内容） vs 严格（6 条契约校验）
- 工具集：同 C 线 stopgap "search" + prompt 级禁工具条款
"""
from __future__ import annotations

import difflib
import json
import math
import re
import sqlite3
import subprocess
import time

from fa import config
from fa.config import KB_SELF_MAX_CHARS
from fa.evolve_self import EvolutionSelfError
from fa.evolve_self.evidence import window_evidence
from fa.evolve_self.knowledge import (
    init_kb_self, kb_path_self, kb_self_over_cap, parse_kb_self,
    personas_self_tree_hash, _text_digest,
)
from fa.evolve_self.windows import Window

# 与 C 线同款工具集 stopgap（T0 探针结论一致）
REFLECT_TOOLSET: str | None = "search"


class ReflectSelfError(EvolutionSelfError):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


def build_reflect_self_command(prompt: str) -> list[str]:
    cmd = [config.hermes_bin(), "-z", prompt]
    if REFLECT_TOOLSET:
        cmd += ["-t", REFLECT_TOOLSET]
    return cmd


def _timeout(explicit: float | None) -> float:
    value = config.persona_timeout() if explicit is None else explicit
    if not math.isfinite(value) or value <= 0:
        return 120.0
    return value


def call_reflect_self(prompt: str, timeout: float | None = None) -> str:
    try:
        proc = subprocess.run(build_reflect_self_command(prompt), capture_output=True,
                              text=True, timeout=_timeout(timeout))
    except subprocess.TimeoutExpired as exc:
        raise ReflectSelfError("timeout", f"{exc.timeout}s") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:]
        raise ReflectSelfError("exit", f"code {proc.returncode} {tail}")
    return proc.stdout


def build_reflect_self_prompt(league: str, kb_text: str, evidence: dict) -> str:
    ev = json.dumps(evidence, ensure_ascii=False, indent=2)
    cur = len(kb_text)
    return f"""你是一位有 20 年经验的足球球探，专注于 {league} 联赛。
你的工作是维护一份比赛观察笔记——每个窗口结束后，根据**下方窗口证据 JSON**（唯一证据源），更新你的笔记本。

## 当前笔记本（字符数 {cur}/{KB_SELF_MAX_CHARS}）

{kb_text}

## 本窗口证据（唯一证据源；未结算注不进 ROI，样本量字段如实读）

```json
{ev}
```

## 输出要求（直接输出完整的更新后 markdown 笔记本，别的什么都不要）

1. **保留所有旧内容**（之前窗口的笔记不要删——它们是历史记录）
2. 在末尾追加本窗口的 section，格式：

   ## W{{窗口号}}（{{起始日期}} ~ {{结束日期}}）

   ### 本轮观察
   - 每条观察尽量具体，引用具体比赛
   - 证据不足的内容不要写——硬写是最严重的错误

   ### 纠正旧认知
   - 如果之前的观察被本轮证据证伪了，写在这里
   - 必须引用具体比赛作为证据

   ### 待验证假设
   - 还需要更多样本才能确认的想法

3. 不得使用任何工具或网络检索；不得引用证据 JSON 之外的信息
4. 输出全文不得超过 {KB_SELF_MAX_CHARS} 字符
5. 保留最顶部的标题行和「## 总原则」section
"""


def _validate_output(text: str, league: str, old_text: str) -> list[str]:
    """宽松校验：合法 md + 标题 + 保留旧内容 + 字符上限。

    返回违规清单（空 = 通过）。全程不抛，异常当违规。
    """
    errs: list[str] = []
    # 字符上限
    if len(text) > KB_SELF_MAX_CHARS:
        errs.append(f"全文超 KB_SELF_MAX_CHARS={KB_SELF_MAX_CHARS} 上限：{len(text)}")
    # 必须有一级标题
    if not re.search(r"^#\s+.+", text, re.MULTILINE):
        errs.append("输出缺少一级标题（# 开头）")
    # 必须包含旧内容的核心骨架（标题行 + 总原则 section）
    old_title = ""
    for line in old_text.splitlines():
        if line.startswith("# "):
            old_title = line.strip()
            break
    if old_title and old_title not in text:
        errs.append(f"输出丢失了原标题：{old_title[:60]}")
    # 必须有「本轮观察」子 section（说明模型理解了任务）
    if "### 本轮观察" not in text:
        errs.append("输出缺少「### 本轮观察」subsection")
    # 用 parse_kb_self 再做一次完整性校验
    try:
        parse_kb_self(text, league)
    except EvolutionSelfError as exc:
        errs.append(f"parse_kb_self 失败：{exc}")
    return errs


def compute_diff(old: str, new: str, league: str) -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"personas/knowledge_self/{config.PERSONA_FILES[league]}",
        tofile=f"proposed/{league}.md"))


def reflect_league_self(conn: sqlite3.Connection, w: Window, league: str) -> dict:
    """C' 线一次反思（不落库——持久化归 runner）。"""
    started = time.monotonic()
    # 确保空模板存在（幂等）
    init_kb_self(league)
    kb_file = kb_path_self(league)
    old_text = kb_file.read_text(encoding="utf-8") if kb_file.is_file() else ""
    kb_hash_before = personas_self_tree_hash(
        config.project_root() / "personas" / "knowledge_self")
    ev = window_evidence(conn, w, league)
    base = {"league": league, "kb_hash_before": kb_hash_before}
    if (ev["self_track"]["n_judged"] == 0
            and ev["nokb_track"]["n_judged"] == 0):
        return {**base, "status": "no_change",
                "no_change_reason": "窗口内该联赛无判决样本",
                "added_chars": 0, "changed_lines": 0,
                "duration_s": round(time.monotonic() - started, 3)}
    prompt = build_reflect_self_prompt(league, old_text, ev)
    try:
        out = call_reflect_self(prompt)
    except ReflectSelfError as exc:
        return {**base, "status": exc.reason, "no_change_reason": None,
                "added_chars": 0, "changed_lines": 0,
                "duration_s": round(time.monotonic() - started, 3)}
    except OSError as exc:
        return {**base, "status": "error",
                "no_change_reason": f"os: {exc}"[:500],
                "added_chars": 0, "changed_lines": 0,
                "duration_s": round(time.monotonic() - started, 3)}
    # 提取 markdown 正文（去掉首尾的 ```markdown 围栏，如果有）
    body = out.strip()
    fence = re.match(r"^```(?:markdown)?\s*\n(.*?)\n```\s*$", body, re.DOTALL)
    if fence:
        body = fence.group(1)
    # 校验
    errs = _validate_output(body, league, old_text)
    if errs:
        return {**base, "status": "contract",
                "no_change_reason": "；".join(errs)[:500],
                "added_chars": 0, "changed_lines": 0,
                "duration_s": round(time.monotonic() - started, 3)}
    # 检查是否实质变更（diff 行数）
    diff = compute_diff(old_text, body, league)
    added_chars = len(body) - len(old_text)
    changed_lines = sum(1 for ln in diff.splitlines()
                        if ln.startswith("+") and not ln.startswith("+++"))
    if not diff.strip() or added_chars == 0:
        return {**base, "status": "no_change",
                "no_change_reason": "反思无实质变更",
                "added_chars": 0, "changed_lines": 0,
                "duration_s": round(time.monotonic() - started, 3)}
    # 写入暂存区（不直接写活文件——runner 决定是否落账）
    from fa.evolve_self.apply import stage_proposal_self  # 延迟导入避环
    path = stage_proposal_self(w.idx, league, body, ev, diff)
    return {**base, "status": "ok", "no_change_reason": None,
            "proposal_path": str(path.relative_to(config.project_root())),
            "added_chars": added_chars, "changed_lines": changed_lines,
            "duration_s": round(time.monotonic() - started, 3)}
