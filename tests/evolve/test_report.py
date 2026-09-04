"""滚动报告：健康度计数/基线期标注/版本戳串联/固定声明（设计档 §10）。

夹具（brief 授权本文件自建，模式沿 tests/evolve/test_apply.py）：

- ``tmp_root``：``fa.config.project_root`` → tmp——报告写
  ``docs/evolution/report-*.md``、暂存三件套落 ``evolution/proposals/``，
  报告与暂存全走 ``config.project_root()`` 属性访问，打一点即全隔离。
- ``kbfile``：预置活知识文件 personas/knowledge/epl.md（E0 = epl.md，§9.2）；
  ``A.stage_proposal`` 渲染 proposed.md 要在活条目上做 amend/deprecate，
  契约 target 必须真实存在。
- ``conn``：复用 tests/evolve/conftest.py 同名夹具（tmp 库、schema v8、
  sqlite3.Row），种子原语（``seed_window_rows``/``_mk_run``/``_mk_fixture``/
  ``_mk_rec``）也从它 import（Ruling 4：窗口种子唯一事实源）。
- ``conn_with_event`` / ``conn_no_merge``：w1 五联赛反思两场景——E0 真暂存
  三件套 + Ruling 直插（merged / rejected），其余四联赛 no_change；前者另种
  窗内两批不同 personas_hash 的 recommendations（版本戳串联素材）。
"""
import json
from datetime import date

import pytest

from fa import config
from fa.evolve import apply as A
from fa.evolve import report as REP
from fa.evolve.evidence import window_evidence
from fa.evolve.windows import window_bounds
from tests.evolve.conftest import _mk_fixture, _mk_rec, _mk_run, seed_window_rows

# 契约形状沿 reflect.validate_contract 六校验（1 增 / 1 改 / 1 废——报告的
# 变更计数与健康度「被推翻或削弱 = 改+废」三路都有非零值可断）。
CONTRACT = {"league": "E0",
            "appends": [{"section": "教训", "text": "密集期晚场让步过浅，downweight 收敛",
                         "date": "2026-10-16", "ttl_days": None,
                         "evidence": {"fixtures": [1],
                                      "stat": "误杀对照 ROI +1.2（1 注）"}}],
            "amendments": [{"target": "E0-L07",
                            "text": "密集期 downweight 过狠，幅度收敛为 −0.10",
                            "date": "2026-10-16"}],
            "deprecations": [{"target": "E0-T03", "reason": "门将已复出，时效条目失效"}],
            "no_change_reason": None}

KB = """<!-- kb: league=E0 generated=2026-09-04 hash=ab12 -->
# E0 知识库

## 结构性认知
- [E0-S01] 升班马主场季初高跑动，盘口惯性低估前 6 轮

## 时效
- [E0-T03|2026-09-04|90d] 某队主力门将伤缺，预计 11 月复出

## 教训
- [E0-L07|2026-10-15] 密集期 downweight 过狠（证据见台账）
"""


@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    """fa.config.project_root → tmp（报告与暂存产物全落 tmp，不碰真仓库）。"""
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def kbfile(tmp_root):
    """预置活知识文件（E0 = epl.md）；CONTRACT 的 amend/deprecate target 在其中。"""
    p = tmp_root / "personas" / "knowledge"
    p.mkdir(parents=True)
    (p / "epl.md").write_text(KB, encoding="utf-8")
    return p / "epl.md"


def _wid(conn, idx):
    """窗口序号 → evolution_windows.id（brief 测试签名用它取 window_id）。"""
    return conn.execute("SELECT id FROM evolution_windows WHERE idx=?",
                        (idx,)).fetchone()["id"]


def _mk_window(conn, idx):
    """evolution_windows 行（opened/closes = window_bounds(1)，报告文件名取 closes）。"""
    cur = conn.execute("INSERT INTO evolution_windows (idx, opened_at, closes_at)"
                       " VALUES (?, '2026-09-04', '2026-10-16')", (idx,))
    conn.commit()
    return int(cur.lastrowid)


def _mk_evo_run(conn, wid, league, status="ok", reason=None, path=None):
    """evolution_runs 行（列清单与 test_apply._mk_run 同——事实源是 db.py 的 DDL）。"""
    cur = conn.execute(
        "INSERT INTO evolution_runs (window_id, league, kb_hash_before, status,"
        " no_change_reason, proposal_path, duration_s, created_at)"
        " VALUES (?, ?, 'h', ?, ?, ?, 0.1, '2026-10-16T00:00:00Z')",
        (wid, league, status, reason, path))
    conn.commit()
    return int(cur.lastrowid)


def _stage_and_rule(conn, wid, ruling):
    """E0 真暂存三件套（stage_proposal 原路径）+ Ruling 直插；返回 run id。

    proposal_path 落库取 project-relative（reflect_league 同一约定，报告侧
    ``(root / proposal_path).parent`` 反解）；Ruling 直插而非走 merge_proposal
    ——报告只读 ruling/note 两列，merged 落账的原子性与重入防护归
    test_apply 覆盖，此处不重复。
    """
    path = A.stage_proposal(1, "E0", CONTRACT,
                            window_evidence(conn, window_bounds(1), "E0"))
    contract = json.loads(path.read_text(encoding="utf-8"))
    # 夹具自检：暂存契约确含 1 改 1 废（否则健康度断言是空转通过）
    assert len(contract["amendments"]) == 1 and len(contract["deprecations"]) == 1
    rid = _mk_evo_run(conn, wid, "E0", status="ok",
                      path=path.relative_to(config.project_root()).as_posix())
    conn.execute(
        "INSERT INTO evolution_rulings (run_id, ruling, kb_hash_after, note,"
        " decided_at) VALUES (?, ?, 'h-after', ?, '2026-10-18T08:00:00Z')",
        (rid, ruling, "裁定理由（测试夹具）"))
    conn.commit()
    return rid


def _other_leagues_no_change(conn, wid):
    """其余四联赛 no_change（终态、无提案——报告只列头部与说明行）。"""
    for lg in ("SP1", "D1", "I1", "F1"):
        _mk_evo_run(conn, wid, lg, status="no_change",
                    reason="窗口内该联赛证据不足，无条目变更")


@pytest.fixture
def conn_with_event(conn, kbfile):
    """w1：E0 ok+暂存三件套、Ruling=merged；窗内两批不同 personas_hash 的
    recommendations（先种子后暂存——evidence 才带得出判决/误杀数字）。"""
    wid = _mk_window(conn, 1)
    seeded = seed_window_rows(conn)              # E0 三轨 + 对照注（2026-09-05 ∈ 窗 1）
    run2 = _mk_run(conn, day="2026-10-01")       # 第二批（仍属窗 1 [09-04, 10-16)）
    _mk_fixture(conn, 2, day="2026-10-01")
    recs_b = [_mk_rec(conn, run2, 2, s, day="2026-10-01")
              for s in ("model_persona", "model_persona_nokb")]
    conn.executemany("UPDATE recommendations SET personas_hash=? WHERE id=?",
                     [("a" * 40, i) for i in
                      (seeded["kb"], seeded["nokb"], seeded["mo"])]
                     + [("b" * 40, i) for i in recs_b])
    conn.commit()
    _stage_and_rule(conn, wid, "merged")
    _other_leagues_no_change(conn, wid)
    return conn


@pytest.fixture
def conn_no_merge(conn, kbfile):
    """w1：E0 暂存三件套在、判决样本在，但 Ruling=rejected（本窗零 merged）
    → 基线期标注必须由 merged 计数驱动，而非「无 Ruling/无工件」。"""
    wid = _mk_window(conn, 1)
    seed_window_rows(conn)
    _stage_and_rule(conn, wid, "rejected")
    _other_leagues_no_change(conn, wid)
    return conn


def test_write_event_report_content(tmp_root, conn_with_event):
    """conn_with_event：w1 五联赛反思（E0 ok+契约三件套、其余 no_change），
    E0 已 merge；窗 1 内种两批不同 personas_hash 的 recommendations 行。"""
    path = REP.write_event_report(conn_with_event, _wid(conn_with_event, 1),
                                  pruned=[("E0", ["E0-T03"])],
                                  today=date(2026, 10, 18))
    assert path == tmp_root / "docs" / "evolution" / "report-2026-10-16.md"
    text = path.read_text(encoding="utf-8")
    assert "E0-T03" in text                       # TTL 修剪明细
    assert "推翻" in text and "削弱" in text         # 健康度（amend+deprecate）
    assert "kb" in text and "nokb" in text         # 轨道对照
    assert "版本戳" in text or "personas_hash" in text
    assert "后续注册" in text                       # 固定声明
    assert "基线期" not in text or "噪声底" not in text   # 有 merged → 非基线期


def test_write_event_report_baseline_period_note(tmp_root, conn_no_merge):
    """无任何 merged → 基线期标注（噪声底披露）。"""
    path = REP.write_event_report(conn_no_merge, _wid(conn_no_merge, 1))
    text = path.read_text(encoding="utf-8")
    assert "基线期" in text and "噪声底" in text
