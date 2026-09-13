"""JSONL 派生、窗口冻结、版本戳（设计档 §4/A4/A5）。

权威源 = 活 ``personas/knowledge/*.md``（只读）。JSONL = 规范派生表示：
条目按 anchor 排序、每行紧凑 JSON（sort_keys、ensure_ascii=False）——
既是窗口冻结载体（git 版本化，回滚 = git revert），也是索引对账基准。
冻结机械化与 C 线同款：tmp 目录 + ``os.replace``（M6 终审 F4）。

project_root 一律 ``config.project_root()`` 属性访问（from-import 绑定会让
monkeypatch 失效——计划 Global Constraints）。
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
    """知识文件文本 → 规范条目 dict 列表（排序键 = anchor；语法破损抛
    EvolutionError，由调用方按写入侧拦截/读取侧降级处置——设计档 §8）。"""
    kb = parse_kb(text, league)
    return [{"anchor": e.anchor, "league": league, "section": e.section,
             "date": e.date, "ttl_days": e.ttl_days, "text": e.text}
            for e in sorted(kb.entries, key=lambda e: e.anchor)]


def jsonl_bytes(entries: list[dict]) -> bytes:
    """规范序列化：每行 sort_keys + 紧凑分隔 + 非 ASCII 原样（对账可比字节）。"""
    lines = [json.dumps(e, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":")) for e in entries]
    return ("\n".join(lines) + "\n").encode("utf-8") if lines else b""


def parse_jsonl_bytes(b: bytes) -> list[dict]:
    return [json.loads(ln) for ln in b.decode("utf-8").splitlines() if ln.strip()]


def derive_live_jsonl() -> dict[str, bytes]:
    """活 knowledge 树 → {league: JSONL bytes}。无文件的联赛不出现。"""
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
    """冻结 JSONL 读取口（无该联赛文件 = None = 空知识库）。"""
    p = window_jsonl_path(idx, league)
    return p.read_bytes() if p.is_file() else None


def jsonl_map_hash(jsonl_map: dict[str, bytes]) -> str:
    """{league: bytes} → sha256（键值排序、``键\\0bytes\\x1e`` 串联）。"""
    h = hashlib.sha256()
    for league, b in sorted(jsonl_map.items()):
        h.update(league.encode())
        h.update(b"\0")
        h.update(b)
        h.update(b"\x1e")
    return h.hexdigest()


def personas_mem_consumed_hash(kb_mem_idx: int) -> str:
    """run 实际消费的 mem 工件 hash（D6）：活人格文件 + 本窗 mem JSONL 快照。

    与 personas_consumed_hash 同方案（稳定键排序 ``键\\0全文\\x1e``）、同
    「先快照后取 hash」纪律——调用方保证顺序（matchday 接线处注释）。
    """
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
