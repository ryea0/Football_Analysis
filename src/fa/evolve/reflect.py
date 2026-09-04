"""反思纯函数：hermes -z 调用 + prompt 组装 + 契约校验（设计档 §6）。

与 M4 persona caller 同构（HERMES_BIN mock 同路径、超时/exit 真实）；差异：
反思证据只许来自台账 JSON——禁工具条款常驻 prompt。工具集形态由 T0 探针钉死
（docs/evolution/t0-toolset-probe.md）：「空工具集」形态不存在（不带 -t =
全量 27 工具、无效 toolset 名静默空输出），故取 stopgap ``"search"`` +
prompt 级禁工具条款双层围栏（M4 同款）。

契约校验的 date/ttl 段规则与 §4 语法、knowledge.parse_kb 完全同口径
（S 双禁 / T 双必 / L 仅 date），amendment 按目标条目所在段套同一规则
（Ruling 2：计划原两条 if 与 §4 矛盾——会系统性拒绝一切合法教训 append）。
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

# T0 探针结论（docs/evolution/t0-toolset-probe.md，2026-09-05 控制器实机执行）：
# 不带 -t = 全量 27 工具、`-t none` = 无效名静默空输出 → 空集形态不存在，
# 取 stopgap "search"（工具面缩到 {web_search}，禁工具条款常驻兜底）
REFLECT_TOOLSET: str | None = "search"


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
                   "date": "按目标条目所在段：时效/教训必带、结构性认知不带",
                   "ttl_days": "仅目标在时效段：正整数，其余 null",
                   "reason": "修订理由"}}],
  "deprecations": [{{"target": "锚点", "reason": "废弃理由"}}],
  "no_change_reason": "无变更时填理由，有变更时必须为 null"
}}
```

## 纪律（必须遵守）

1. **证据不足时 no_change 优于编造**——弱证据硬写条目是本岗位最严重的错误。
2. appends 必须引 evidence.fixtures（台账里真实存在的场次 id）；结构性认知段
   不带日期，时效段必带 date+ttl_days，教训段带 date（amendments 按目标条目
   所在段套同一规则）。统计数字只放 evidence.stat 摘要，不进知识正文。
3. 必须显式处理旧条目：本期哪些该 amend（写弱了/写反了）、哪些该 deprecate
   （被证据推翻）；全都不动就说明理由（写进 no_change_reason 或省略字段）。
4. 不得使用任何工具或网络检索；不得引用证据 JSON 之外的信息（你的记忆不算
   证据）。渲染后全文不得超过 {KB_MAX_CHARS} 字符。
"""


def _section_field_errs(tag: str, section: str, d, ttl) -> list[str]:
    """date/ttl 的分段规则（§4 语法，与 parse_kb 同口径）——Ruling 2 三分。

    S（结构性认知）：date、ttl_days 双禁；T（时效）：date、正 ttl_days 双必；
    L（教训）：date 必带、ttl_days 禁带。``tag`` 定位违规条目（含锚点时
    关卡人审能直接定位到具体旧条目）。
    """
    if section == "时效":
        if d and isinstance(ttl, int) and ttl > 0:
            return []
        return [f"{tag} 时效段须带 date 与正 ttl_days"]
    if section == "教训":
        errs = []
        if not d:
            errs.append(f"{tag} 教训段须带 date")
        if ttl is not None:
            errs.append(f"{tag} 教训段不得带 ttl_days")
        return errs
    if d is not None or ttl is not None:            # 结构性认知
        return [f"{tag} 结构性认知段不得带 date/ttl_days"]
    return []


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
        errs.extend(_section_field_errs(f"appends[{i}]", section, d, ttl))
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
        errs.extend(_section_field_errs(f"amendments[{i}]({target})",
                                        anchors[target].section, d, ttl))
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
    except OSError as exc:
        # hermes 缺失/不可执行（FileNotFoundError/PermissionError）——同 §6.5
        # 降级语义：记 status='error' 静默停摆，不炸 tick/reflect（DDL 词表
        # 'error' 正是留给这类调用面外故障；控制者裁定，报告 §8）
        return {**base, "status": "error",
                "no_change_reason": f"os: {exc}"[:500],
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
