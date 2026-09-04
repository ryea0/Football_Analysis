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
    used = {int(e.anchor[-2:]) for e in kb.entries   # 锚点尾部恒为两位序号
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
