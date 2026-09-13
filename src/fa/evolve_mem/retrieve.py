"""按场检索与内联降级文本（设计档 §4 读取侧 / §8 降级一级）。"""
from __future__ import annotations

from fa.evolve_mem.index import (TOP_K_DEFAULT, ensure_index,
                                 get_retriever, index_store_dir)
from fa.evolve_mem.snapshot import parse_jsonl_bytes, window_mem_bytes


def build_query(input_obj: dict) -> str:
    """检索 query：联赛 + 主客队（规范名，跨语言召回交 embedding——Gate 0
    live 项）+ 候选市场。中英混合原文，v1 不做别名扩展（设计档 §15）。"""
    m = input_obj["match"]
    markets = " ".join(sorted({c["market"] for c in input_obj["candidates"]}))
    return f'{input_obj["league"]} {m["home"]} {m["away"]} {markets}'


def render_line(e: dict) -> str:
    date = f"|{e['date']}" if e.get("date") else ""
    return f"[{e['anchor']}{date}] {e['text']}"


def window_mem_topk(idx: int, league: str, query: str,
                    top_k: int = TOP_K_DEFAULT) -> list[str]:
    """冻结 JSONL → hash 缓存索引 → top-k 渲染行。空知识库 = []（无该联赛
    文件或条目为空）。检索链路任何失败上抛——调用方落内联降级（设计档 §8）。"""
    b = window_mem_bytes(idx, league)
    if b is None:
        return []
    h = ensure_index({league: b})
    res = get_retriever().search(index_store_dir(h, league), query, top_k)
    return [render_line(e) for e in res]


def window_mem_inline(idx: int, league: str) -> str | None:
    """整文件内联形态（kb 轨同款语义；检索失败降级用）。空知识库 = None。"""
    b = window_mem_bytes(idx, league)
    if b is None:
        return None
    lines = [render_line(e) for e in parse_jsonl_bytes(b)]
    return "\n".join(lines) if lines else None
