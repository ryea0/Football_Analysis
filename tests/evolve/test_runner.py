"""runner：tick 顺延/触发/零样本、calibrate 不落库、review/status 文本。

夹具（本文件自带，不依赖共享 conftest 的 ``conn``）：

- ``root``：``config.project_root`` → tmp——runner 的 TTL 修剪/提案/校准目录
  全经该缝定位，不重定向会摸真仓库（knowledge.py 的 Global Constraint：属性
  访问，打一点即全隔离）。
- ``conn``：tmp 库 + ``FA_DB`` 指向它 + ``root`` 重定向。**本模块自定义、
  有意盖过 tests/evolve/conftest.py 的同名夹具**（模块级夹具优先于 conftest）：
  runner 是唯一在用例体内就写 ``project_root()`` 的一层，隔离必须随 ``conn``
  默认到位，brief 的用例签名才不必逐个补 ``root``。
- ``seeded``：``conftest.seed_window_rows``（窗口种子唯一事实源，Ruling 4）
  + 活知识文件，返回项目根。
- ``hermes_ok``：HERMES_BIN → 恒回合法反思契约的脚本（真子进程，与实跑同
  代码路径 C1）。契约取**时效段 append**：T8 六校验与 §4 语法对时效段的要求
  一致（date+正 ttl），不依赖「教训段可否带日期」的口径分歧。
"""
import json
from datetime import date

import pytest

from fa import config
from fa.db import connect, init_db
from fa.evolve import runner
from tests.evolve.conftest import seed_window_rows

CONTRACT = {"league": "E0",
            "appends": [{"section": "时效",
                         "text": "某队主力门将复出在即，前场压迫强度回升",
                         "date": "2026-10-16", "ttl_days": 90,
                         "evidence": {"fixtures": [1],
                                      "stat": "误杀对照 ROI +1.2（1 注）"}}],
            "amendments": [], "deprecations": [], "no_change_reason": None}

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
def root(tmp_path, monkeypatch):
    """``fa.config.project_root`` → tmp（personas/evolution 产物全落 tmp）。"""
    (tmp_path / "personas" / "knowledge").mkdir(parents=True)
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def conn(root, tmp_path, monkeypatch):
    """tmp 库 + FA_DB + project_root 重定向（盖过 conftest 同名夹具，见模块 docstring）。"""
    p = tmp_path / "runner.db"
    monkeypatch.setenv("FA_DB", str(p))
    init_db(p)
    c = connect(p)
    yield c
    c.close()


@pytest.fixture
def seeded(conn, root):
    """w1 内三轨判决行 + 活知识文件；返回项目根（brief 断言在其上取路径）。"""
    seed_window_rows(conn)
    (root / "personas" / "knowledge" / "epl.md").write_text(KB, encoding="utf-8")
    return root


@pytest.fixture
def seeded_kb_expired(seeded):
    """同 seeded——KB 的 E0-T03（2026-09-04 + 90d = 2026-12-03）在 tick 基准日
    2026-12-04 已过期，正是 TTL 修剪的靶子。"""
    return seeded


@pytest.fixture
def hermes_ok(tmp_path, monkeypatch):
    """HERMES_BIN → 恒回合法契约的 fixture 脚本（单引号包 JSON，bash 不拆引号）。"""
    s = tmp_path / "hermes_contract.sh"
    s.write_text("#!/usr/bin/env bash\necho '" + json.dumps(CONTRACT) + "'\n",
                 encoding="utf-8")
    s.chmod(0o755)
    monkeypatch.setenv("HERMES_BIN", str(s))
    return s


def test_tick_noop_when_no_due_window(conn, monkeypatch):
    monkeypatch.setattr(runner, "_today", lambda: date(2026, 9, 10))
    assert "无到期窗口" in runner.run_tick(conn)


def test_tick_reflects_due_window(conn, seeded, monkeypatch, hermes_ok):
    monkeypatch.setattr(runner, "_today", lambda: date(2026, 10, 20))
    out = runner.run_tick(conn)
    assert "w1" in out
    rows = conn.execute("SELECT league, status FROM evolution_runs").fetchall()
    assert len(rows) == 5                       # 五联赛串行
    assert conn.execute("SELECT reflected_at FROM evolution_windows"
                        " WHERE idx=1").fetchone()["reflected_at"] is not None
    assert not (seeded / "evolution" / "snapshots").exists()   # tick 不建快照（run 才建）


def test_tick_defers_when_previous_gate_open(conn, seeded, monkeypatch, hermes_ok):
    """w1 已反思未关 + w2 已收口 → tick 记顺延、不反思 w2（串行关卡）。"""
    monkeypatch.setattr(runner, "_today", lambda: date(2026, 10, 20))
    runner.run_tick(conn)                        # w1 反思（hermes_ok 出合法契约）
    monkeypatch.setattr(runner, "_today", lambda: date(2026, 12, 1))
    out = runner.run_tick(conn)
    assert "顺延" in out
    assert conn.execute("SELECT COUNT(*) FROM evolution_runs"
                        " WHERE window_id=(SELECT id FROM evolution_windows"
                        " WHERE idx=2)").fetchone()[0] == 0


def test_tick_prunes_ttl_before_reflect(conn, seeded_kb_expired, monkeypatch, hermes_ok):
    monkeypatch.setattr(runner, "_today", lambda: date(2026, 12, 4))
    runner.run_tick(conn)
    assert "E0-T03" not in (seeded_kb_expired / "personas" / "knowledge"
                            / "epl.md").read_text()


def test_run_reflect_calibrate_writes_no_db_rows(conn, seeded, monkeypatch, hermes_ok):
    monkeypatch.setattr(runner, "_today", lambda: date(2026, 10, 20))
    out = runner.run_reflect(conn, 1, "E0", calibrate=True)
    assert out["calibrate"] is True
    assert conn.execute("SELECT COUNT(*) FROM evolution_windows").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM evolution_runs").fetchone()[0] == 0
    assert (seeded / "evolution" / "proposals").exists()
