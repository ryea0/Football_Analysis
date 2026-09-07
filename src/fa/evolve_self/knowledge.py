"""C' 线知识库（时间线笔记式）：解析、hash、快照。

与 C 线 knowledge.py 的区别：
- 知识格式：时间线笔记式 markdown（自由文本 + 窗口 section） vs 三段式结构化锚点
- 解析：宽松（只要是合法 markdown 就行） vs 严格（锚点语法、三段式校验）
- TTL 修剪：v1 不做（时间线式靠「纠正旧认知」section 自净）
- 快照机制：与 C 线同构（snapshots_self/w{idx}/），只是源目录不同
"""
from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from fa import config
from fa.config import KB_SELF_MAX_CHARS
from fa.evolve_self import EvolutionSelfError
from fa.evolve_self import windows as _windows


@dataclass
class SelfKnowledgeFile:
    league: str
    text: str
    char_count: int


# ---------------------------------------------------------------- 解析/校验


def parse_kb_self(text: str, league: str) -> SelfKnowledgeFile:
    """宽松解析：只要是合法 utf-8 文本就接受；空文本 = 空知识库。

    v1 不做强结构校验（区别于 C 线的严格三段式锚点语法）——
    时间线笔记式的要点就是自由。只做两条合理性检查：
    1. 必须包含「# {league}」标题（或空文本 = 未初始化）
    2. 字符数不超上限
    """
    if not text.strip():
        return SelfKnowledgeFile(league=league, text="", char_count=0)
    stripped = text.strip()
    # 宽松校验：标题行存在（允许 # 后有空格，允许中文联赛名）
    lines = stripped.splitlines()
    title_ok = any(l.strip().startswith("# ") for l in lines[:5])
    if not title_ok:
        raise EvolutionSelfError(
            f"{league} 自反思知识库无一级标题（首 5 行须有 # 开头的标题）")
    if len(text) > KB_SELF_MAX_CHARS:
        raise EvolutionSelfError(
            f"{league} 自反思知识库超字符上限：{len(text)} > {KB_SELF_MAX_CHARS}")
    return SelfKnowledgeFile(league=league, text=text, char_count=len(text))


def kb_self_over_cap(text: str) -> bool:
    return len(text) > KB_SELF_MAX_CHARS


# ---------------------------------------------------------------- 树 hash / 路径


def kb_path_self(league: str) -> Path:
    return (config.project_root() / "personas" / "knowledge_self"
            / config.PERSONA_FILES[league])


def personas_self_tree_hash(root: Path) -> str:
    """knowledge_self/ 树内容 hash（与 personas_tree_hash 同算法：
    排序后 相对路径+全文 串联 sha256）。"""
    files = sorted(p for p in root.glob("**/*.md") if p.is_file())
    h = hashlib.sha256()
    for p in files:
        h.update(p.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\x1e")
    return h.hexdigest()


def personas_self_consumed_hash(kb_idx: int) -> str:
    """run 实际消费的 C' 线知识库 hash（与 personas_consumed_hash 同算法）。

    键集 = 活人格文件（共用）+ knowledge_self@w{kb_idx} 快照。
    """
    root = config.project_root()
    virtual: list[tuple[str, Path]] = []
    personas_dir = root / "personas"
    if personas_dir.is_dir():
        virtual.extend((f"personas/{p.name}", p)
                       for p in sorted(personas_dir.glob("*.md")) if p.is_file())
    snap = snapshot_dir_self(kb_idx)
    if snap.is_dir():
        virtual.extend((f"knowledge_self@w{kb_idx}/{p.name}", p)
                       for p in sorted(snap.glob("*.md")) if p.is_file())
    h = hashlib.sha256()
    for key, p in sorted(virtual):
        h.update(key.encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\x1e")
    return h.hexdigest()


# ---------------------------------------------------------------- 快照


def snapshot_dir_self(idx: int) -> Path:
    return config.project_root() / "evolution" / "snapshots_self" / f"w{idx}"


def ensure_window_snapshot_self(idx: int) -> Path:
    """C' 线快照：把 knowledge_self/ 拷进 snapshots_self/w{idx}/。

    与 C 线 ensure_window_snapshot 同构（原子化 tmp + rename），
    只是源目录和目标目录不同。
    """
    dest = snapshot_dir_self(idx)
    if dest.exists():
        return dest
    for stale in dest.parent.glob(f"w{idx}.tmp-*"):
        shutil.rmtree(stale, ignore_errors=True)
    tmp = dest.parent / f"w{idx}.tmp-{os.getpid()}"
    tmp.mkdir(parents=True)
    src = config.project_root() / "personas" / "knowledge_self"
    try:
        if src.is_dir():
            for p in sorted(src.glob("*.md")):
                shutil.copy2(p, tmp / p.name)
        if dest.exists():
            return dest
        os.replace(tmp, dest)
        return dest
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ensure_current_snapshot_self() -> int:
    idx = _windows.current_window_idx()
    ensure_window_snapshot_self(idx)
    return idx


def window_kb_text_self(idx: int, league: str) -> str | None:
    """B 线第四轨唯一读取口：本窗快照里的 C' 线知识文本。"""
    p = snapshot_dir_self(idx) / config.PERSONA_FILES[league]
    return p.read_text(encoding="utf-8") if p.is_file() else None


# ---------------------------------------------------------------- 杂项


def _text_digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def init_kb_self(league: str) -> Path:
    """初始化空知识库模板（如果文件不存在）。幂等。"""
    p = kb_path_self(league)
    if p.is_file():
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    template = f"""# {league} 知识库（自反思·时间线）

## 总原则

你是一位有 20 年经验的足球球探，专注于本联赛。每个窗口结束后，
把你的观察、教训、待验证假设记在这份笔记里。
- 「本轮观察」：本窗口值得记录的现象
- 「纠正旧认知」：之前的观察被证伪了，写在这里
- 「待验证假设」：还需要更多样本验证的想法
"""
    p.write_text(template, encoding="utf-8")
    return p
