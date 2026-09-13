"""M7a/C' 补修：matchday 接线函数自身的契约——_wire_mem_snapshot 与
_wire_self_snapshot 的正常/降级两态。

（全链行为已由 test_matchday 同刻五落系列覆盖：各轨 recs/bets、版本戳
落 runs.summary；此处只钉接线函数。）
"""
from tests.evolve_mem.conftest import write_kb


def test_wire_mem_snapshot_ok(tmp_path, monkeypatch):
    from fa import config
    from fa.pipeline import matchday as M
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    (tmp_path / "personas").mkdir()
    write_kb(tmp_path)
    idx, h = M._wire_mem_snapshot()
    assert isinstance(idx, int) and h and len(h) == 64


def test_wire_mem_snapshot_broken_kb_degrades(tmp_path, monkeypatch):
    from fa import config
    from fa.pipeline import matchday as M
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    (tmp_path / "personas").mkdir()
    write_kb(tmp_path, text="## 结构性认知\n- [bad anchor] x\n")
    idx, h = M._wire_mem_snapshot()
    assert idx is None and h is None


def test_wire_self_snapshot_ok(tmp_path, monkeypatch):
    from fa import config
    from fa.pipeline import matchday as M
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    (tmp_path / "personas").mkdir()
    idx, h = M._wire_self_snapshot()
    assert isinstance(idx, int) and h and len(h) == 64


def test_wire_self_snapshot_oserror_degrades(tmp_path, monkeypatch):
    from fa.pipeline import matchday as M
    # from-import 绑定：patch 消费方（matchday）命名空间而非源模块
    monkeypatch.setattr(M, "ensure_current_snapshot_self",
                        lambda: (_ for _ in ()).throw(OSError("disk")))
    idx, h = M._wire_self_snapshot()
    assert idx is None and h is None
