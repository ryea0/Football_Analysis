"""M7a Gate 0 spike（设计档 §7）——ark × mem0 兼容性实证（throwaway，不进主链路）。

跑法：
  live:   ARK_API_KEY=... uv run python scripts/spike/m7a_gate0.py
  offline: uv run python scripts/spike/m7a_gate0.py
           （无 key 时探针模式：mem0 的 chroma 本地 embedder——只验机制，
            跨语言召回不作数；设计档允许的 BLOCKED 降级路径）

检查项（设计档 §7）：
  C1 依赖导入与版本（安装时已验，此处记录）
  C2 add(infer=False) / search 实跑（live 模式含中英/别名召回 sanity）
  C3 零生成式 LLM 调用（llm 指向 http://127.0.0.1:1/ 仍全链成功=结构性证明）
  C4 export 往返：get_all 文本集 == 派生规范 JSONL 字节
  C5 chroma 目录跨实例重开持久
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"
ARK_MODEL = os.environ.get("FA_MEM_EMBED_MODEL", "doubao-embedding")
DEAD_LLM = "http://127.0.0.1:1/"

ENTRIES = [
    {"anchor": "E0-S01", "date": None, "league": "E0", "section": "结构性认知",
     "text": "阿森纳（枪手，Arsenal）主场控球压制，对中下游队让球盘偏软",
     "ttl_days": None},
    {"anchor": "E0-S02", "date": None, "league": "E0", "section": "结构性认知",
     "text": "英超升班马客场普遍保守，大球率低", "ttl_days": None},
    {"anchor": "D1-S01", "date": None, "league": "D1", "section": "结构性认知",
     "text": "拜仁（Bayern）主场对保级队常出大比分", "ttl_days": None},
]


def line(e: dict) -> str:
    return json.dumps(e, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_config(store: Path, embedder: dict) -> dict:
    return {
        "vector_store": {"provider": "chroma",
                         "config": {"path": str(store), "collection_name": "spike"}},
        "embedder": embedder,
        # A3 结构性证明：任何 LLM 调用会即时 Connection refused
        "llm": {"provider": "openai",
                "config": {"openai_base_url": DEAD_LLM, "api_key": "unused"}},
    }


def _items(res):
    if isinstance(res, dict):
        return res.get("results", [])
    return list(res)


def main() -> int:
    mode = "live" if os.environ.get("ARK_API_KEY") else "offline"
    try:
        from mem0 import Memory
    except ImportError as exc:
        print(json.dumps({"C1": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    import chromadb

    if mode == "live":
        embedder = {"provider": "openai",
                    "config": {"openai_base_url": ARK_BASE,
                               "api_key": os.environ["ARK_API_KEY"],
                               "model": ARK_MODEL}}
    else:
        # 探针：openai provider 离线构造（dummy key，init 不触网），事后把
        # embedding_model 换成 mem0 自带 MockEmbeddings（常量向量，零网络——
        # 只验机制，排名不作数；from_config 白名单不收 mock 的绕法）
        embedder = {"provider": "openai",
                    "config": {"openai_base_url": "http://127.0.0.1:1/",
                               "api_key": "unused"}}

    store = Path(tempfile.mkdtemp(prefix="m7a-gate0-"))
    out: dict = {"mode": mode, "C1": True,
                 "versions": {"mem0ai": "2.0.20", "chromadb": chromadb.__version__}}
    try:
        cfg = build_config(store, embedder)
        m = Memory.from_config(cfg)                     # C3 之一：init 不炸
        if mode == "offline":
            from mem0.embeddings.mock import MockEmbeddings
            m.embedding_model = MockEmbeddings()        # 离线换心：此后零网络
        for e in ENTRIES:
            m.add(line(e), user_id="kbmem-spike", infer=False)                 # C2/C3：零 LLM 实证
        queries = (["Arsenal 主场风格", "枪手 让球", "拜仁 大比分", "Bayern home"]
                   if mode == "live" else ["E0 阿森纳", "升班马 保守"])
        results = {q: [r.get("memory") for r in _items(m.search(
                       query=q, filters={"user_id": "kbmem-spike"}, limit=2))]
                   for q in queries}
        derived = sorted(line(e) for e in ENTRIES)
        exported = sorted(str(r.get("memory")) for r in _items(m.get_all(filters={"user_id": "kbmem-spike"})))
        m2 = Memory.from_config(cfg)                    # C5：重开同目录
        reopened = sorted(str(r.get("memory")) for r in _items(m2.get_all(filters={"user_id": "kbmem-spike"})))
        out["recall"] = results
        out["C2_index_search"] = all(results.values())
        if mode == "live":                              # 跨语言召回 sanity
            out["C2_recall"] = all(
                any("E0-S01" in x for x in results[q])
                for q in ("Arsenal 主场风格", "枪手 让球")) and any(
                any("D1-S01" in x for x in results[q])
                for q in ("拜仁 大比分", "Bayern home"))
        else:
            out["C2_recall"] = None                     # offline 不作数
        out["C3_zero_llm"] = True                       # 走到这里 = 全链零 LLM
        out["C4_roundtrip"] = exported == derived
        out["C5_reopen"] = reopened == derived
        out["exported_sample"] = exported[:1]
    except Exception as exc:                            # noqa: BLE001——spike 全捕如实报
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["C2_index_search"] = out.get("C2_index_search", False)
        out["C3_zero_llm"] = False
        out["C4_roundtrip"] = out.get("C4_roundtrip", False)
        out["C5_reopen"] = out.get("C5_reopen", False)
    finally:
        shutil.rmtree(store, ignore_errors=True)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    live_gate = (out.get("C2_index_search") and out.get("C3_zero_llm")
                 and out.get("C4_roundtrip") and out.get("C5_reopen")
                 and (out.get("C2_recall") is not False))
    return 0 if live_gate else 1


if __name__ == "__main__":
    sys.exit(main())
