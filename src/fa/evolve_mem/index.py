"""hash 键控索引、Retriever 协议与对账（设计档 §4/§7；A1/A3/A4）。

「活索引」不是常驻存储：索引 = JSONL 的 hash 键控缓存目录
（``data/mem_cache/<hash>/<league>/``，gitignore），随时可删可重建。
memory 文本 = 规范 JSON 行本身（Gate 0 C4 实证：export 往返字节相等）——
锚点/日期全在行内，零 metadata 版本耦合，对账 = 字节比较。

Gate 0 报告（docs/m7a-gate0-report.md）的 API 硬事实内化于此：
namespace 必填（user_id=kbmem-{league}）、search/get_all 走 filters、
telemetry 默认关（惰性 import 前 setdefault）、history_db 收进 store 目录、
llm 指死端口（A3 结构性保证——任何生成式调用即时失败而非静默烧钱）。
"""
from __future__ import annotations

import json
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
_ARK_MODEL = "doubao-embedding"        # Gate 0 live 补跑实证后如换名在此改
_DEAD_LLM = "http://127.0.0.1:1/"      # A3 结构性保证（见模块 docstring）


def _line(e: dict) -> str:
    return json.dumps(e, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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
    ``add(infer=False)`` 零生成式 LLM——Gate 0 C3 结构证明）。

    mem0 惰性 import；依赖缺席 / 缺 ARK key 抛 EvolveMemError（调用方降级）。
    namespace = ``kbmem-{store_dir.name}``（每联赛一命名空间）。
    """

    def __init__(self) -> None:
        os.environ.setdefault("MEM0_TELEMETRY", "False")   # Gate 0 卫生裁定
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
            "history_db_path": str(store_dir / "history.db"),
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

    @staticmethod
    def _ns(store_dir: Path) -> str:
        return f"kbmem-{store_dir.name}"

    def index_entries(self, entries, store_dir):
        m = self._memory(store_dir)
        ns = self._ns(store_dir)
        for e in entries:
            m.add(_line(e), user_id=ns, infer=False)

    def search(self, store_dir, query, top_k):
        res = self._memory(store_dir).search(
            query=query, filters={"user_id": self._ns(store_dir)}, limit=top_k)
        return self._parse(res)[:top_k]

    def export_entries(self, store_dir):
        return self._parse(
            self._memory(store_dir).get_all(
                filters={"user_id": self._ns(store_dir)}))

    @staticmethod
    def _parse(res) -> list[dict]:
        items = res.get("results", []) if isinstance(res, dict) else list(res)
        out = []
        for r in items:
            text = r.get("memory") if isinstance(r, dict) else str(r)
            try:
                out.append(json.loads(text))
            except (TypeError, ValueError) as exc:
                raise EvolveMemError(f"索引内存文本破损（非规范 JSON 行）：{exc}") from exc
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
    """单联赛缓存索引：READY 即复用；否则 tmp 目录建好再 ``os.replace``
    （半成品只在 tmp，绝无「读得像就绪」的半缓存——C 线快照同款纪律）。"""
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
    """对账：索引导出条目集与权威派生条目集，规范序列化后逐字节相等
    （设计档 §8——拦住 mem0 往返造成的任何文本漂移）。"""
    return jsonl_bytes(sorted(exported, key=lambda e: e["anchor"])) == derived


def sync_all(retriever: Retriever | None = None) -> dict:
    """sync-mem 主逻辑：活树派生 → 逐联赛重建缓存索引 → 导出对账。

    逐联赛容错（单联赛不炸整体，C 线 prune_live_files 先例）；retriever
    初始化失败（缺 key/依赖）整体报 error 不抛——调用方记事件（设计档 §8
    写入侧静默停摆）。权威源语法破损上抛 EvolutionError（重议级，不吞）。
    """
    live = derive_live_jsonl()
    if not live:
        # 空知识库 = 无事可同步（ok、不告警）——retriever 都不必构造
        return {"ok": True, "error": None, "leagues": {}}
    try:
        r = retriever if retriever is not None else get_retriever()
    except Exception as exc:      # 构造期=外部依赖地界（缺 key/依赖/chroma），一律上报不抛
        return {"ok": False, "error": f"{exc}",
                "leagues": {lg: {"entries": 0, "ok": False, "error": f"{exc}"}
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
