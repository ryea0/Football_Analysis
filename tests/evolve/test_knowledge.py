"""知识文件语法/树 hash/快照/契约应用（设计档 §4/§3）。"""
from datetime import date

import pytest

from fa.evolve import EvolutionError
from fa.evolve import knowledge as K


KB = """<!-- kb: league=E0 generated=2026-10-16 hash=ab12 -->
# E0 知识库

## 结构性认知
- [E0-S01] 升班马主场季初高跑动，盘口惯性低估前 6 轮

## 时效
- [E0-T03|2026-09-04|90d] 某队主力门将伤缺，预计 11 月复出

## 教训
- [E0-L07|2026-10-15] 密集期 downweight 过狠（证据见台账）
"""


def test_parse_roundtrip_sections_and_fields():
    kb = K.parse_kb(KB, "E0")
    assert [e.anchor for e in kb.entries] == ["E0-S01", "E0-T03", "E0-L07"]
    s01, t03, l07 = kb.entries
    assert (s01.section, s01.date, s03_ttl(s01)) == ("结构性认知", None, None)
    assert (t03.section, t03.date, t03.ttl_days) == ("时效", "2026-09-04", 90)
    assert (l07.section, l07.date, l07.ttl_days) == ("教训", "2026-10-15", None)


def s03_ttl(e):
    return e.ttl_days


def test_parse_empty_text_is_empty_kb():
    kb = K.parse_kb("", "E0")
    assert kb.entries == [] and kb.league == "E0"


def test_parse_bad_anchor_raises():
    with pytest.raises(EvolutionError):
        K.parse_kb("## 时效\n- [E0-T3|2026-09-04|90d] x", "E0")   # 序号须两位


def test_parse_league_mismatch_raises():
    with pytest.raises(EvolutionError):
        K.parse_kb("## 时效\n- [SP1-T03|2026-09-04|90d] x", "E0")


def test_parse_t_missing_ttl_raises():
    with pytest.raises(EvolutionError):
        K.parse_kb("## 时效\n- [E0-T03|2026-09-04] x", "E0")


def test_parse_s_with_date_raises():
    with pytest.raises(EvolutionError):
        K.parse_kb("## 结构性认知\n- [E0-S01|2026-09-04] x", "E0")


def test_render_kb_idempotent():
    kb = K.parse_kb(KB, "E0")
    text = K.render_kb(kb, generated="2026-10-16", digest="ab12")
    assert K.parse_kb(text, "E0").entries == kb.entries   # 渲染可再解析（锚点/字段无损）


def test_next_anchor_sequential_no_reuse():
    kb = K.parse_kb(KB, "E0")
    assert K.next_anchor(kb, "结构性认知") == "E0-S02"
    assert K.next_anchor(kb, "时效") == "E0-T04"
    assert K.next_anchor(kb, "教训") == "E0-L08"
    empty = K.parse_kb("", "E0")
    assert K.next_anchor(empty, "教训") == "E0-L01"


def test_prune_expired_removes_only_expired_t():
    kb = K.parse_kb(KB, "E0")
    today = date(2026, 12, 1)          # 2026-09-04 + 90d = 2026-12-03 未过期
    kept, pruned = K.prune_expired(kb, today)
    assert pruned == [] and len(kept.entries) == 3
    kept, pruned = K.prune_expired(kb, date(2026, 12, 4))   # 过期日
    assert pruned == ["E0-T03"]
    assert [e.anchor for e in kept.entries] == ["E0-S01", "E0-L07"]


def test_apply_contract_append_amend_deprecate():
    kb = K.parse_kb(KB, "E0")
    contract = {
        "league": "E0",
        "appends": [{"section": "教训", "text": "新教训", "date": "2026-10-16",
                     "ttl_days": None,
                     "evidence": {"fixtures": [1], "stat": "x"}}],
        "amendments": [{"target": "E0-S01", "text": "改写后的结构认知",
                        "date": None, "ttl_days": None, "reason": "r"}],
        "deprecations": [{"target": "E0-T03", "reason": "已复出"}],
        "no_change_reason": None,
    }
    new_kb = K.apply_contract(kb, contract)
    anchors = [e.anchor for e in new_kb.entries]
    assert "E0-T03" not in anchors and "E0-L08" in anchors
    s01 = next(e for e in new_kb.entries if e.anchor == "E0-S01")
    assert s01.text == "改写后的结构认知"
    l08 = next(e for e in new_kb.entries if e.anchor == "E0-L08")
    assert l08.date == "2026-10-16"


def test_apply_contract_unknown_target_raises():
    kb = K.parse_kb(KB, "E0")
    with pytest.raises(EvolutionError):
        K.apply_contract(kb, {"league": "E0", "appends": [],
                              "amendments": [{"target": "E0-S99", "text": "x",
                                              "date": None, "ttl_days": None,
                                              "reason": "r"}],
                              "deprecations": [], "no_change_reason": None})


def test_kb_over_cap():
    assert K.kb_over_cap("x" * (K.KB_MAX_CHARS + 1))
    assert not K.kb_over_cap("x" * K.KB_MAX_CHARS)


def test_personas_tree_hash_content_and_order_sensitive(tmp_path):
    (tmp_path / "epl.md").write_text("persona")
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "epl.md").write_text("kb-v1")
    h1 = K.personas_tree_hash(tmp_path)
    (tmp_path / "knowledge" / "epl.md").write_text("kb-v2")
    assert K.personas_tree_hash(tmp_path) != h1
    h2 = K.personas_tree_hash(tmp_path)   # 先取基线（other 建在 tmp_path 内会污染树）
    # 路径排序稳定性：同名不同序不成立（文件系统枚举序无关，hash 只看排序后路径）
    other = tmp_path / "other"
    other.mkdir()
    (other / "epl.md").write_text("persona")
    (other / "knowledge").mkdir()
    (other / "knowledge" / "epl.md").write_text("kb-v2")
    assert K.personas_tree_hash(other) == h2


def test_window_snapshot_freeze_semantics(tmp_path, monkeypatch):
    from fa import config
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    live = tmp_path / "personas" / "knowledge"
    live.mkdir(parents=True)
    (live / "epl.md").write_text("v1")
    snap1 = K.ensure_window_snapshot(1)
    assert (snap1 / "epl.md").read_text() == "v1"
    (live / "epl.md").write_text("v2")          # 快照后活文件变更
    assert K.ensure_window_snapshot(1) == snap1  # 幂等：不覆盖既有快照
    assert (snap1 / "epl.md").read_text() == "v1"
    assert K.window_kb_text(1, "E0") == "v1"     # B 线读快照，不读活文件
    K.ensure_window_snapshot(2)                  # 下一窗快照才吃到 v2
    assert K.window_kb_text(2, "E0") == "v2"
    assert K.window_kb_text(3, "E0") is None     # 无快照 = 空知识库


def test_ensure_window_snapshot_atomic_torn_tmp_cleaned(tmp_path, monkeypatch):
    """M6 终审 F4：快照落盘原子化——先写 ``w1.tmp-<pid>`` 再改名，中断残留的
    tmp 目录一律先清；最终目录要么不存在、要么完整（半成品绝不被 B 线钉版）。"""
    from fa import config
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    live = tmp_path / "personas" / "knowledge"
    live.mkdir(parents=True)
    (live / "epl.md").write_text("v1")
    (live / "laliga.md").write_text("v1")
    snap_root = tmp_path / "evolution" / "snapshots"
    torn = snap_root / "w1.tmp-99999"           # 上次中断的残留
    torn.mkdir(parents=True)
    (torn / "seriea.md").write_text("stale")

    dest = K.ensure_window_snapshot(1)

    assert dest == snap_root / "w1"
    assert sorted(p.name for p in dest.glob("*.md")) == ["epl.md", "laliga.md"]
    assert not torn.exists() and not list(snap_root.glob("w1.tmp-*"))
    assert K.ensure_window_snapshot(1) == dest   # 幂等：终态在 → 原样复用
    assert sorted(p.name for p in dest.glob("*.md")) == ["epl.md", "laliga.md"]


def _consumed_root(tmp_path, monkeypatch):
    """consumed-hash 测试环境：活人格文件 + 活知识文件 + 快照（w1/w2）。"""
    from fa import config
    (tmp_path / "personas").mkdir()
    (tmp_path / "personas" / "epl.md").write_text("persona-v1", encoding="utf-8")
    live = tmp_path / "personas" / "knowledge"
    live.mkdir(parents=True)
    (live / "epl.md").write_text("kb-v1", encoding="utf-8")
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    K.ensure_window_snapshot(1)
    K.ensure_window_snapshot(2)
    (live / "epl.md").write_text("kb-v2", encoding="utf-8")   # w2 快照后活树再变
    return tmp_path


def test_personas_consumed_hash_tracks_consumed_artifacts(tmp_path, monkeypatch):
    """M6 终审 F2：consumed hash = 活顶层人格 + 本窗知识快照（消费什么戳什么）。

    - (i) 人格文件被编辑 → 戳变
    - (ii) 本窗快照滚到知识不同的新窗 → 戳变
    - (iii) 窗内**活 knowledge 文件**变化 → 戳不变（run 读的是快照——这正是
      版本戳要钉住的语义，窗内合并落盘不得污染归因）
    """
    root = _consumed_root(tmp_path, monkeypatch)
    h1 = K.personas_consumed_hash(1)
    assert len(h1) == 64
    (root / "personas" / "epl.md").write_text("persona-v2", encoding="utf-8")
    assert K.personas_consumed_hash(1) != h1                      # (i)
    h2 = K.personas_consumed_hash(1)
    (root / "personas" / "knowledge" / "epl.md").write_text(
        "kb-merged-mid-window", encoding="utf-8")
    assert K.personas_consumed_hash(1) == h2                      # (iii) 不变
    assert K.personas_consumed_hash(2) != h2                      # (ii) w2 快照不同
    # 与活树 hash 不同一计算：knowledge 部分键名是 knowledge@w{n}/（快照），非
    # personas/knowledge/——两者即便内容同源，digest 也不同
    assert h2 != K.personas_tree_hash(root / "personas")


def test_personas_consumed_hash_stable_keys_and_empty_kb(tmp_path, monkeypatch):
    """虚拟键集稳定（同名同内容重算不变）；无快照窗口 = 只有活人格部分。"""
    root = _consumed_root(tmp_path, monkeypatch)
    assert K.personas_consumed_hash(1) == K.personas_consumed_hash(1)
    assert K.personas_consumed_hash(9) != K.personas_consumed_hash(1)   # w9 无快照
    (root / "personas" / "epl.md").write_text("persona-v1", encoding="utf-8")
    assert K.personas_consumed_hash(9) == K.personas_consumed_hash(9)   # 仍稳定


def test_prune_live_files_writes_back(tmp_path, monkeypatch):
    from fa import config
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    live = tmp_path / "personas" / "knowledge"
    live.mkdir(parents=True)
    (live / "epl.md").write_text(KB)
    result = K.prune_live_files(date(2026, 12, 4))
    assert result == [("E0", ["E0-T03"])]
    assert "E0-T03" not in (live / "epl.md").read_text()
    assert K.prune_live_files(date(2026, 12, 4)) == []   # 幂等


def test_prune_live_files_broken_file_degrades_per_league(tmp_path, monkeypatch):
    """M6 终审 F1：单联赛知识文件语法破损 → 记哨兵锚点不上抛，健康联赛照常
    修剪（返回契约不变，哨兵 ``<parse-error: …>`` 原样透给报告/tick 摘要）。"""
    from fa import config
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    live = tmp_path / "personas" / "knowledge"
    live.mkdir(parents=True)
    (live / "epl.md").write_text(KB, encoding="utf-8")
    broken = "## 时效\n- [SP1-T3|2026-09-04|90d] 序号一位即破损\n"
    (live / "laliga.md").write_text(broken, encoding="utf-8")
    out = K.prune_live_files(date(2026, 12, 4))
    by = dict(out)
    assert by["E0"] == ["E0-T03"]                        # 健康联赛不受牵连
    assert by["SP1"][0].startswith("<parse-error:")
    assert "E0-T03" not in (live / "epl.md").read_text(encoding="utf-8")
    assert (live / "laliga.md").read_text(encoding="utf-8") == broken   # 坏文件未被动


def test_git_aux_never_raises():
    out = K.git_aux()
    assert set(out) == {"git_rev", "git_dirty"}
