from fa.evolve_mem import retrieve as R
from fa.evolve_mem.snapshot import ensure_window_snapshot_mem
from tests.evolve_mem.conftest import write_kb

INPUT = {"league": "E0",
         "match": {"kickoff_utc": "2026-09-20T15:00:00Z", "home": "Arsenal",
                   "away": "Everton"},
         "candidates": [{"market": "H"}, {"market": "O2.5"}],
         "model_summary": {"p_home": .5, "p_draw": .3, "p_away": .2}}


def test_build_query():
    assert R.build_query(INPUT) == "E0 Arsenal Everton H O2.5"


def test_render_line_with_and_without_date():
    assert R.render_line({"anchor": "E0-T01", "date": "2026-09-01",
                          "text": "x"}) == "[E0-T01|2026-09-01] x"
    assert R.render_line({"anchor": "E0-S01", "date": None,
                          "text": "y"}) == "[E0-S01] y"


def test_topk_and_inline_and_empty(mem_root):
    write_kb(mem_root)
    idx = 3
    ensure_window_snapshot_mem(idx)
    lines = R.window_mem_topk(idx, "E0", "阿森纳 主场", top_k=2)
    assert len(lines) == 2 and lines[0].startswith("[E0-S01]")
    inline = R.window_mem_inline(idx, "E0")
    assert inline is not None and inline.count("\n") == 3      # 4 条目
    assert R.window_mem_inline(idx, "SP1") is None             # 无该联赛=空知识库
    assert R.window_mem_topk(idx, "SP1", "q") == []


def test_topk_raises_when_retriever_broken(mem_root, monkeypatch):
    write_kb(mem_root)
    ensure_window_snapshot_mem(3)
    monkeypatch.setenv("FA_MEM_RETRIEVER", "mem0")            # 走 Mem0Retriever
    monkeypatch.delenv("ARK_API_KEY", raising=False)          # 缺 key → 构造即抛
    raised = False
    try:
        R.window_mem_topk(3, "E0", "q")
    except Exception:                                        # 任意失败上抛供调用方降级
        raised = True
    assert raised
