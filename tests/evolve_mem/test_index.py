from fa.evolve_mem import index as I
from fa.evolve_mem.snapshot import entry_dicts, jsonl_bytes
from tests.evolve_mem.conftest import KB_E0, write_kb


def E(a, t, **kw):
    return {"anchor": a, "league": "E0", "section": "结构性认知",
            "date": None, "ttl_days": None, "text": t, **kw}


def test_fake_retriever_roundtrip_and_topk(tmp_path):
    r = I.FakeRetriever()
    entries = [E("E0-S01", "阿森纳主场压制"), E("E0-S02", "升班马保守"),
               E("E0-S03", "裁判尺度")]
    r.index_entries(entries, tmp_path)
    assert r.export_entries(tmp_path) == entries
    got = r.search(tmp_path, "E0 阿森纳 home", 2)
    assert len(got) == 2 and got[0]["anchor"] == "E0-S01"   # 词法命中排前
    assert [e["anchor"] for e in r.search(tmp_path, "zzz", 8)] == \
        ["E0-S01", "E0-S02", "E0-S03"]                      # 无命中→锚点稳定序


def test_ensure_index_cache_hit_and_rebuild(mem_root):
    write_kb(mem_root)
    live = {"E0": jsonl_bytes(entry_dicts("E0", KB_E0))}
    h1 = I.ensure_index(live)
    store = I.index_store_dir(h1, "E0")
    assert (store / "READY").is_file()
    (store / "entries.jsonl").write_bytes(b"tampered")     # 缓存内容被篡改
    assert I.ensure_index(live) == h1                       # READY 即复用（不重建）
    live2 = {**live, "E0": jsonl_bytes(entry_dicts(
        "E0", KB_E0 + "- [E0-S09] 新条目\n"))}
    h2 = I.ensure_index(live2)
    assert h2 != h1 and (I.index_store_dir(h2, "E0") / "READY").is_file()


def test_reconcile_detects_drift():
    derived = jsonl_bytes([E("E0-S01", "a"), E("E0-S02", "b")])
    assert I.reconcile(derived, [E("E0-S02", "b"), E("E0-S01", "a")])  # 乱序=等价
    assert not I.reconcile(derived, [E("E0-S01", "a 被改")])       # 文本漂移
    assert not I.reconcile(derived, [E("E0-S01", "a")])            # 条目缺失


def test_sync_all_ok_and_drift(mem_root):
    write_kb(mem_root)

    class Tamper:
        def __init__(self, inner):
            self._inner = inner

        def index_entries(self, entries, store_dir):
            self._inner.index_entries([{**entries[0], "text": "被污染"}], store_dir)

        def search(self, *a):
            return self._inner.search(*a)

        def export_entries(self, store_dir):
            return self._inner.export_entries(store_dir)

    good = I.sync_all(retriever=I.FakeRetriever())
    assert good["ok"] and good["leagues"]["E0"]["ok"]
    assert good["leagues"]["E0"]["entries"] == 4
    import shutil
    shutil.rmtree(mem_root / "data" / "mem_cache")       # 清缓存=强制重建（否则 READY 复用，drift 注入不达）
    bad = I.sync_all(retriever=Tamper(I.FakeRetriever()))
    assert not bad["ok"] and not bad["leagues"]["E0"]["ok"]


def test_sync_all_retriever_init_failure_reported(mem_root):
    write_kb(mem_root)

    class Broken:
        def __init__(self):
            raise RuntimeError("ARK_API_KEY 未配置")

    out = I.sync_all(retriever=Broken())
    assert out["ok"] is False and "ARK_API_KEY" in out["error"]


def test_get_retriever_env_switch(monkeypatch):
    monkeypatch.setenv("FA_MEM_RETRIEVER", "fake")
    assert isinstance(I.get_retriever(), I.FakeRetriever)
    monkeypatch.setenv("FA_MEM_RETRIEVER", "MEM0")
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    try:
        I.get_retriever()
        raise AssertionError("无 ARK_API_KEY 应抛 EvolveMemError")
    except I.EvolveMemError:
        pass
