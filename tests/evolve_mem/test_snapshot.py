from fa.evolve import EvolutionError
from fa.evolve_mem import snapshot as S
from tests.evolve_mem.conftest import write_kb


def test_entry_dicts_and_jsonl_roundtrip(mem_root):
    write_kb(mem_root)
    entries = S.entry_dicts("E0", (mem_root / "personas/knowledge/epl.md").read_text())
    assert [e["anchor"] for e in entries] == ["E0-L01", "E0-S01", "E0-S02", "E0-T01"]
    b = S.jsonl_bytes(entries)
    assert S.parse_jsonl_bytes(b) == entries            # 字节往返无损
    assert b.count(b"\n") == 4


def test_jsonl_bytes_empty():
    assert S.jsonl_bytes([]) == b""


def test_derive_live_jsonl_only_existing(mem_root):
    write_kb(mem_root)
    live = S.derive_live_jsonl()
    assert set(live) == {"E0"}                          # 无文件的联赛不出现
    assert b"E0-S01" in live["E0"]


def test_derive_raises_on_broken_syntax(mem_root):
    write_kb(mem_root, text="## 结构性认知\n- [bad anchor] x\n")
    try:
        S.derive_live_jsonl()
        raise AssertionError("应抛 EvolutionError")
    except EvolutionError:
        pass


def test_ensure_window_snapshot_freezes_and_idempotent(mem_root):
    write_kb(mem_root)
    dest = S.ensure_window_snapshot_mem(3)
    f = dest / "mem-E0.jsonl"
    assert f.is_file() and b"E0-S01" in f.read_bytes()
    f.write_bytes(f.read_bytes() + b'{"anchor":"E0-S99"}\n')   # 篡改冻结件
    assert S.ensure_window_snapshot_mem(3) == dest              # 已存在即复用，不覆盖
    assert b"E0-S99" in f.read_bytes()
    # 新窗口重新派生（不受上窗篡改影响——源是活 markdown）
    dest4 = S.ensure_window_snapshot_mem(4)
    assert b"E0-S99" not in (dest4 / "mem-E0.jsonl").read_bytes()


def test_snapshot_no_kb_is_empty_dir(mem_root):
    dest = S.ensure_window_snapshot_mem(1)
    assert dest.is_dir() and list(dest.iterdir()) == []
    assert S.window_mem_bytes(1, "E0") is None


def test_personas_mem_consumed_hash_covers_personas_and_snapshot(mem_root):
    write_kb(mem_root)
    (mem_root / "personas" / "analyst.md").write_text("p", encoding="utf-8")
    S.ensure_window_snapshot_mem(2)
    h1 = S.personas_mem_consumed_hash(2)
    (mem_root / "personas" / "analyst.md").write_text("p2", encoding="utf-8")
    assert S.personas_mem_consumed_hash(2) != h1        # 人格文件变化→戳变
    S.ensure_window_snapshot_mem(5)                     # 新快照内容同→按内容定
    (mem_root / "evolution/snapshots_mem/w5/mem-E0.jsonl").write_bytes(b"x")
    assert S.personas_mem_consumed_hash(5) != S.personas_mem_consumed_hash(2)
