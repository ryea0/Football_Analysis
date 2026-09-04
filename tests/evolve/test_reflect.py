"""反思：命令形态/prompt 结构/契约校验（§4 分段规则）/reflect_league 降级路径。

夹具分工：``conn`` 用共享 conftest（tmp 库）；窗口种子走
``conftest.seed_window_rows``（Ruling 4 唯一事实源——零样本用例不种、
降级/ok 用例种 w1 一场三轨判决）；``root``/``kbfile`` 本文件自带
（``config.project_root`` → tmp，不摸真仓库）；``HERMES_BIN`` 指向 tmp 下
0o755 bash 脚本（C1：mock 与实跑同一 subprocess 代码路径）。

分段 date/ttl 规则用例对齐 Ruling 2：S 双禁 / T 双必 / L 仅 date，
amendment 按目标条目所在段套同一规则。
"""
import json

import pytest

from fa import config
from fa.config import KB_MAX_CHARS
from fa.evolve import knowledge as K
from fa.evolve import reflect as R
from fa.evolve.windows import window_bounds
from tests.evolve.conftest import seed_window_rows

KB = ("## 结构性认知\n- [E0-S01] 旧认知\n"
      "## 时效\n- [E0-T03|2026-09-04|90d] 旧时效\n"
      "## 教训\n- [E0-L07|2026-10-15] 旧教训\n")

_VALID = {"league": "E0",
          "appends": [{"section": "教训", "text": "t", "date": "2026-10-16",
                       "ttl_days": None,
                       "evidence": {"fixtures": [1], "stat": "s"}}],
          "amendments": [], "deprecations": [], "no_change_reason": None}

_NO_CALL = "#!/usr/bin/env bash\nexit 99\n"     # 哨兵脚本：被调即败 → status='exit'


@pytest.fixture
def root(tmp_path, monkeypatch):
    """``fa.config.project_root`` → tmp（kb_path/树 hash/暂存区全隔离）。"""
    (tmp_path / "personas" / "knowledge").mkdir(parents=True)
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def kbfile(root):
    """预置活知识文件 personas/knowledge/epl.md（E0 = epl.md，config §9.2）。"""
    p = root / "personas" / "knowledge" / "epl.md"
    p.write_text(KB, encoding="utf-8")
    return p


@pytest.fixture
def seeded(conn, root, kbfile):
    """w1 内 E0 三轨判决行（kb 轨 veto = 有判决样本）+ 活知识文件。"""
    seed_window_rows(conn)
    return root


def _hermes(script_text, tmp_path):
    script = tmp_path / "hermes.sh"
    script.write_text(script_text)
    script.chmod(0o755)
    return str(script)


def _echo_script(payload: dict, tmp_path) -> str:
    """HERMES_BIN 桩：恒回 payload 的 JSON（单引号包住，bash 不拆双引号）。"""
    return _hermes("#!/usr/bin/env bash\necho '" + json.dumps(payload) + "'\n",
                   tmp_path)


def test_reflect_toolset_is_probe_conclusion():
    """T0 探针结论钉死：空工具集形态不存在 → stopgap "search"（probe 文档）。"""
    assert R.REFLECT_TOOLSET == "search"


def test_build_reflect_command_shape(monkeypatch):
    monkeypatch.setattr(R, "REFLECT_TOOLSET", None)
    assert R.build_reflect_command("p") == ["hermes", "-z", "p"]
    monkeypatch.setattr(R, "REFLECT_TOOLSET", "search")
    assert R.build_reflect_command("p") == ["hermes", "-z", "p", "-t", "search"]


def test_build_reflect_prompt_structure():
    ev = {"league": "E0", "window": {"idx": 1}}
    p = R.build_reflect_prompt("E0", KB, ev)
    assert "E0" in p and "知识库的管理员" in p
    assert json.dumps(ev, ensure_ascii=False, indent=2) in p     # 证据逐字入 prompt
    assert f"{len(KB)}/{KB_MAX_CHARS}" in p                      # 明示现长与上限
    assert "no_change" in p                     # no_change 优先于编造（反偏差）
    assert "amend" in p and "deprecate" in p    # 显式处理旧条目（防回音室）
    assert "不得" in p and "工具" in p           # 禁工具条款（T0 stopgap 兜底层）
    assert "唯一证据源" in p                     # 证据只许来自台账 JSON
    empty = R.build_reflect_prompt("E0", None, ev)
    assert "空" in empty                        # 首版冷启动明示


def test_validate_contract_ok():
    """教训段 append 带 date = 合法（§4 语法；Ruling 2 后不被误拒）。"""
    kb = K.parse_kb(KB, "E0")
    assert R.validate_contract(_VALID, kb, "E0") == []


def test_validate_contract_six_rules():
    kb = K.parse_kb(KB, "E0")
    bad_league = dict(_VALID, league="SP1")
    assert any("league" in e for e in R.validate_contract(bad_league, kb, "E0"))
    no_ev = dict(_VALID, appends=[dict(_VALID["appends"][0],
                                       evidence={"fixtures": [], "stat": ""})])
    assert any("fixtures" in e for e in R.validate_contract(no_ev, kb, "E0"))
    s_dated = dict(_VALID, appends=[dict(_VALID["appends"][0], section="结构性认知",
                                         date="2026-10-16")])
    assert R.validate_contract(s_dated, kb, "E0")        # S 段不得带日期 → 违规
    ghost = dict(_VALID, amendments=[{"target": "E0-S99", "text": "x",
                                      "date": None, "ttl_days": None, "reason": "r"}])
    assert any("E0-S99" in e for e in R.validate_contract(ghost, kb, "E0"))
    both = dict(_VALID, no_change_reason="证据不足")
    assert any("no_change" in e for e in R.validate_contract(both, kb, "E0"))
    t_amend_bad = dict(_VALID, amendments=[{"target": "E0-T03", "text": "x",
                                            "date": None, "ttl_days": None,
                                            "reason": "r"}])
    assert any("T03" in e for e in R.validate_contract(t_amend_bad, kb, "E0"))
    assert any("date" in e for e in
               R.validate_contract({"league": "E0", "appends": [
                   {"section": "时效", "text": "t", "date": "2026/10/16",
                    "ttl_days": 90,
                    "evidence": {"fixtures": [1], "stat": ""}}],
                   "amendments": [], "deprecations": [],
                   "no_change_reason": None}, kb, "E0"))   # 日期格式


def test_validate_contract_section_rules():
    """Ruling 2 三分规则（append 侧）：L 必带 date；S 双禁；T 双必。"""
    kb = K.parse_kb(KB, "E0")
    no_date = dict(_VALID, appends=[dict(_VALID["appends"][0], date=None)])
    assert any("教训" in e and "date" in e
               for e in R.validate_contract(no_date, kb, "E0"))
    s_ttl = dict(_VALID, appends=[dict(_VALID["appends"][0],
                                       section="结构性认知", date=None,
                                       ttl_days=30)])
    assert any("结构性认知" in e for e in R.validate_contract(s_ttl, kb, "E0"))
    t_no_ttl = dict(_VALID, appends=[dict(_VALID["appends"][0], section="时效",
                                          date="2026-10-16", ttl_days=None)])
    assert any("ttl_days" in e for e in R.validate_contract(t_no_ttl, kb, "E0"))
    assert any("教训" in e and "ttl_days" in e for e in R.validate_contract(
        dict(_VALID, appends=[dict(_VALID["appends"][0], ttl_days=30)]), kb, "E0"))


def test_validate_contract_amendment_follows_target_section():
    """Ruling 2 三分规则（amendment 侧）：date/ttl 按目标条目所在段。"""
    kb = K.parse_kb(KB, "E0")
    bad = dict(_VALID, appends=[], amendments=[
        {"target": "E0-T03", "text": "x", "date": "2026-10-16",
         "ttl_days": None, "reason": "r"},       # T 目标缺 ttl
        {"target": "E0-L07", "text": "x", "date": "2026-10-16",
         "ttl_days": 30, "reason": "r"},         # L 目标不得带 ttl
        {"target": "E0-S01", "text": "x", "date": "2026-10-16",
         "ttl_days": None, "reason": "r"},       # S 目标不得带 date
    ])
    errs = R.validate_contract(bad, kb, "E0")
    assert any("T03" in e and "ttl" in e for e in errs)
    assert any("L07" in e and "ttl" in e for e in errs)
    assert any("S01" in e and "date" in e for e in errs)
    good_l = dict(_VALID, appends=[], amendments=[
        {"target": "E0-L07", "text": "改写后的教训正文", "date": "2026-10-16",
         "ttl_days": None, "reason": "r"}])
    assert R.validate_contract(good_l, kb, "E0") == []
    good_t = dict(_VALID, appends=[], amendments=[
        {"target": "E0-T03", "text": "续期后的时效正文", "date": "2026-11-01",
         "ttl_days": 60, "reason": "r"}])
    assert R.validate_contract(good_t, kb, "E0") == []


def test_validate_contract_cap_exceeded():
    kb = K.parse_kb(KB, "E0")
    big = dict(_VALID, appends=[dict(_VALID["appends"][0], text="长" * 3000)])
    errs = R.validate_contract(big, kb, "E0")
    assert any("KB_MAX_CHARS" in e or "上限" in e for e in errs)


def test_validate_contract_type_confusion_degrades():
    """F1–F4：类型错契约一律记违规清单，绝不抛（reflect_league 才能降级，
    tick 不被 AttributeError/TypeError 打断在窗中途）。"""
    kb = K.parse_kb(KB, "E0")
    ap = _VALID["appends"][0]
    # F1：三列表字段给了字符串（"none" 按字符迭代会 AttributeError）
    assert any("appends" in e and "列表" in e
               for e in R.validate_contract(dict(_VALID, appends="none"), kb, "E0"))
    assert any("amendments" in e and "列表" in e for e in R.validate_contract(
        dict(_VALID, amendments="x"), kb, "E0"))
    assert any("deprecations" in e and "列表" in e for e in R.validate_contract(
        dict(_VALID, deprecations="x"), kb, "E0"))
    # F1：条目非对象
    assert any("appends[0] 须为对象" in e for e in
               R.validate_contract(dict(_VALID, appends=["x"]), kb, "E0"))
    # F1：target 不可哈希（dict 成员测试会 TypeError）
    bad_target = dict(_VALID, appends=[], amendments=[
        {"target": ["E0-S01"], "text": "x", "date": None, "ttl_days": None,
         "reason": "r"}])
    assert any("E0-S01" in e for e in R.validate_contract(bad_target, kb, "E0"))
    # F1：text / evidence 非字符串标量
    assert any("text" in e for e in R.validate_contract(
        dict(_VALID, appends=[dict(ap, text=123)]), kb, "E0"))
    assert any("evidence" in e for e in R.validate_contract(
        dict(_VALID, appends=[dict(ap, evidence="s")]), kb, "E0"))
    # F2：ttl_days=true（bool 是 int 子类，True 会渲染成 |Trued 炸下窗修剪）
    assert any("ttl_days" in e for e in R.validate_contract(
        dict(_VALID, appends=[dict(ap, section="时效", date="2026-10-16",
                                   ttl_days=True)]), kb, "E0"))
    assert any("ttl_days" in e for e in R.validate_contract(
        dict(_VALID, appends=[dict(ap, ttl_days=True)]), kb, "E0"))
    # F3：过正则的非法日历日（prune 的 fromisoformat 会炸）
    assert any("date" in e for e in R.validate_contract(
        dict(_VALID, appends=[dict(ap, date="2026-13-45")]), kb, "E0"))
    assert any("date" in e for e in R.validate_contract(
        dict(_VALID, appends=[dict(ap, section="时效", date="2026-02-30",
                                   ttl_days=30)]), kb, "E0"))
    # F4：fixtures 须非空整数列表（探针文档口径）
    assert any("整数" in e for e in R.validate_contract(
        dict(_VALID, appends=[dict(ap, evidence={"fixtures": ["1"],
                                                 "stat": "s"})]), kb, "E0"))
    assert any("整数" in e for e in R.validate_contract(
        dict(_VALID, appends=[dict(ap, evidence={"fixtures": [1, True],
                                                 "stat": "s"})]), kb, "E0"))
    # F1 同族（控制者追加）：no_change_reason 非字符串 → runner INSERT 绑定
    # dict 会炸 sqlite3.InterfaceError，校验层拦截降级
    assert "no_change_reason 须为字符串" in R.validate_contract(
        dict(_VALID, appends=[], no_change_reason={"why": "x"}), kb, "E0")
    # 反向：真合法契约在这些守卫下零回归
    assert R.validate_contract(_VALID, kb, "E0") == []


def test_call_reflect_raises_with_reason(tmp_path, monkeypatch):
    """exit 99 → reason="exit"；0.05s 超时护栏撞上 sleep 2 → reason="timeout"。"""
    monkeypatch.setenv("HERMES_BIN", _hermes(_NO_CALL, tmp_path))
    with pytest.raises(R.ReflectCallError) as ei:
        R.call_reflect("p")
    assert ei.value.reason == "exit"
    slow_dir = tmp_path / "slow"
    slow_dir.mkdir()
    monkeypatch.setenv("HERMES_BIN",
                       _hermes("#!/usr/bin/env bash\nsleep 2\n", slow_dir))
    monkeypatch.setenv("FA_PERSONA_TIMEOUT", "0.05")
    with pytest.raises(R.ReflectCallError) as ei:
        R.call_reflect("p")
    assert ei.value.reason == "timeout"


def test_reflect_league_hermes_missing_degrades(conn, seeded, monkeypatch):
    """HERMES_BIN 指向不存在路径 → OSError 降级为 status='error'，不炸不抛。

    设计档 §6.5「失败即静默停摆」：调用面外故障记 DDL 词表 'error'，tick 与
    其余联赛不受影响（控制者裁定，报告 §8）。
    """
    monkeypatch.setenv("HERMES_BIN", "/nonexistent/hermes-missing")
    out = R.reflect_league(conn, window_bounds(1), "E0")
    assert out["status"] == "error"
    assert "os:" in (out["no_change_reason"] or "")
    assert out["proposal_path"] is None
    assert not (seeded / "evolution" / "proposals").exists()   # 暂存区零写入


def test_reflect_league_broken_kb_degrades_not_raises(conn, seeded, tmp_path,
                                                      monkeypatch):
    """M6 终审 F1：活知识文件语法破损 → 该联赛 status='error'（理由 kb parse:），
    不炸 reflect、暂存区零写入（降级语义同 OSError 分支，同一 6 键返回形状）；
    hermes 照常被调到（回合法契约）——失败点确在 parse 而非调用。"""
    (seeded / "personas" / "knowledge" / "epl.md").write_text(
        "## 时效\n- [E0-T3|2026-09-04|90d] 序号一位即语法破损\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_BIN", _echo_script(_VALID, tmp_path))
    out = R.reflect_league(conn, window_bounds(1), "E0")
    assert out["status"] == "error"
    assert (out["no_change_reason"] or "").startswith("kb parse:")
    assert out["proposal_path"] is None
    assert out["league"] == "E0" and out["duration_s"] >= 0
    assert out["kb_hash_before"] == K.personas_tree_hash(seeded / "personas")
    assert not (seeded / "evolution" / "proposals").exists()


def test_reflect_league_no_sample_skips_call(conn, root, tmp_path, monkeypatch):
    """窗口内该联赛 0 判决样本 → 不调 hermes，直接 no_change（R7）。"""
    monkeypatch.setenv("HERMES_BIN", _hermes(_NO_CALL, tmp_path))
    out = R.reflect_league(conn, window_bounds(1), "E0")
    assert out["status"] == "no_change"
    assert out["no_change_reason"] == "窗口内该联赛无判决样本"


def test_reflect_league_contract_fail(conn, seeded, tmp_path, monkeypatch):
    bad = {"league": "E0",
           "appends": [{"section": "教训", "text": "x", "date": "2026-10-16",
                        "evidence": {"fixtures": []}}],
           "amendments": [], "deprecations": [], "no_change_reason": None}
    monkeypatch.setenv("HERMES_BIN", _echo_script(bad, tmp_path))
    out = R.reflect_league(conn, window_bounds(1), "E0")
    assert out["status"] == "contract" and out["proposal_path"] is None
    assert "fixtures" in (out["no_change_reason"] or "")
    assert not (seeded / "evolution" / "proposals").exists()   # 暂存区零写入


def test_reflect_league_ok_stages_proposal(conn, seeded, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_BIN", _echo_script(_VALID, tmp_path))
    out = R.reflect_league(conn, window_bounds(1), "E0")
    assert out["status"] == "ok" and out["no_change_reason"] is None
    assert out["proposal_path"] is not None
    assert (seeded / out["proposal_path"]).is_file()           # 相对项目根
    assert out["league"] == "E0" and out["duration_s"] >= 0
    assert out["kb_hash_before"] == K.personas_tree_hash(seeded / "personas")
