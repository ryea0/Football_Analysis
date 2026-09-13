"""M7a 离线全链（fake 检索器）：派生 → 冻结 → 索引 → 检索 → 戳 → 窗口隔离。

对齐项目验收惯例：本文件是**机制贯通**的诚实证据——检索语义质量归 Gate 0
真跑（docs/m7a-gate0-report.md）与首窗 E2E（设计档 §9），不得据本文件宣称
召回质量。B 线注入面（prompt 组装、内联降级、四轨记账）已由
tests/persona/test_apply.py kbmem 三用例与 tests/evolve/test_e2e.py ④ 段
覆盖，此处不重复。
"""
from fa.evolve_mem.index import ensure_index, get_retriever, index_store_dir
from fa.evolve_mem.retrieve import window_mem_inline, window_mem_topk
from fa.evolve_mem.snapshot import (ensure_window_snapshot_mem,
                                    jsonl_map_hash,
                                    personas_mem_consumed_hash,
                                    window_mem_bytes)
from tests.evolve_mem.conftest import write_kb


def test_full_chain_offline(mem_root):
    write_kb(mem_root)
    # ① 冻结 w7（源 = 活 markdown 派生）
    ensure_window_snapshot_mem(7)
    b = window_mem_bytes(7, "E0")
    assert b and b"E0-S01" in b
    # ② 索引就绪（hash 缓存）+ 检索
    h = ensure_index({"E0": b})
    assert (index_store_dir(h, "E0") / "READY").is_file()
    lines = window_mem_topk(7, "E0", "阿森纳 主场")
    assert lines and lines[0].startswith("[E0-S01]")
    # ③ 内联形态与检索形态同源同 renderer
    assert lines[0] in window_mem_inline(7, "E0")
    # ④ 版本戳可计算（D6：先快照后取 hash 的读取面）
    assert len(personas_mem_consumed_hash(7)) == 64


def test_window_isolation_and_hash_sensitivity(mem_root):
    write_kb(mem_root)
    ensure_window_snapshot_mem(7)
    h7 = jsonl_map_hash({"E0": window_mem_bytes(7, "E0")})
    # 活树变更 → 新窗口冻结内容变、hash 变；旧窗口冻结不动（快照钉版）
    write_kb(mem_root, text=write_kb.__defaults__[0] + "- [E0-S08] 新条目\n")
    ensure_window_snapshot_mem(8)
    h8 = jsonl_map_hash({"E0": window_mem_bytes(8, "E0")})
    assert h8 != h7
    assert b"E0-S08" not in window_mem_bytes(7, "E0")
    assert personas_mem_consumed_hash(8) != personas_mem_consumed_hash(7)
    # 旧窗检索不受活树变更影响（只读冻结 JSONL）
    assert "E0-S08" not in "\n".join(window_mem_topk(7, "E0", "", top_k=99))


def test_retriever_round_is_production_seam(mem_root):
    """生产缝自证：fake 实现满足协议三方法（index/search/export），与
    Mem0Retriever 同签名——config 切换即换实现，无路径分叉。"""
    write_kb(mem_root)
    ensure_window_snapshot_mem(9)
    r = get_retriever()
    assert callable(r.index_entries) and callable(r.search) and callable(r.export_entries)
    b = window_mem_bytes(9, "E0")
    h = ensure_index({"E0": b})
    assert r.export_entries(index_store_dir(h, "E0")) and r.search(
        index_store_dir(h, "E0"), "阿森纳", 2)
