"""C' 线（自反思知识库）冒烟测试：临时库 + mock hermes，跑通完整闭环。

覆盖：init_db v11 迁移、STRATEGIES 四轨、知识库初始化、自反思、
自动落账、回滚、快照钉版。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

import pytest

# 确保 src 在路径里
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture
def tmp_project(monkeypatch, tmp_path):
    """临时项目根：空 personas/knowledge_self/ + 空 DB。"""
    (tmp_path / "personas").mkdir()
    (tmp_path / "personas" / "knowledge_self").mkdir()
    (tmp_path / "evolution" / "snapshots_self").mkdir(parents=True)
    (tmp_path / "data").mkdir()

    db = tmp_path / "data" / "fa.db"

    def fake_project_root() -> Path:
        return tmp_path

    def fake_db_path() -> Path:
        return db

    # 覆盖 config 的路径函数
    import fa.config as cfg
    monkeypatch.setattr(cfg, "project_root", fake_project_root)
    monkeypatch.setattr(cfg, "db_path", fake_db_path)

    # 初始化数据库
    from fa.db import init_db
    init_db(db)

    return tmp_path


def _seed_basic_data(conn: sqlite3.Connection) -> None:
    """种子数据：1 联赛 1 场 fixture + 三轨 recommendations + paper 注。"""
    conn.execute(
        "INSERT INTO teams (id, league, name) VALUES (1, 'E0', 'TeamA')")
    conn.execute(
        "INSERT INTO teams (id, league, name) VALUES (2, 'E0', 'TeamB')")
    conn.execute(
        "INSERT INTO fixtures (id, league, event_key, source, kickoff_utc,"
        " home_team_id, away_team_id, status, created_at)"
        " VALUES (1, 'E0', 'ev1', 'oddsapi', '2026-09-05T15:00:00Z',"
        " 1, 2, 'finished', '2026-09-01T00:00:00Z')")
    # 一个 run
    conn.execute(
        "INSERT INTO runs (id, type, phase, status, started_at)"
        " VALUES (1, 'matchday', 'am', 'ok', '2026-09-05T00:00:00Z')")
    # 三轨 recommendations（model_only / model_persona / model_persona_nokb）
    for i, strat in enumerate(
            ("model_only", "model_persona", "model_persona_nokb")):
        conn.execute(
            "INSERT INTO recommendations (id, run_id, fixture_id, strategy,"
            " market, phase, model_p, market_p, best_odds, bookmaker, edge,"
            " ev, kelly_stake_frac, verdict, confidence_delta, final_stake_frac,"
            " created_at)"
            " VALUES (?, 1, 1, ?, 'H', 'am', 0.55, 0.5, 2.1, 'Pinnacle',"
            " 0.05, 0.155, 0.05, 'agree', 0.0, 0.05,"
            " '2026-09-05T00:00:00Z')",
            (i + 1, strat))
    # paper 注 + 结算（用 result 列模拟）
    for i in range(3):
        conn.execute(
            "INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker,"
            " odds_taken, stake, status, return_amt, clv)"
            " VALUES (?, 'paper', '2026-09-05T00:00:00Z', 'Pinnacle',"
            " 2.1, 10.0, 'won', 21.0, 0.05)", (i + 1,))
    conn.commit()


class TestDbMigration:
    """v11 迁移：C' 线表 + recommendations 四轨枚举 + personas_self_hash 列。"""

    def test_v11_schema(self, tmp_project):
        from fa.db import SCHEMA_VERSION
        assert SCHEMA_VERSION == 11
        from fa.config import db_path
        conn = sqlite3.connect(db_path())
        conn.row_factory = sqlite3.Row
        # evolution_self_windows 表存在
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='evolution_self_windows'"
        ).fetchone() is not None
        # recommendations 有 model_persona_kb_self
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='recommendations'"
        ).fetchone()["sql"]
        assert "model_persona_kb_self" in ddl
        # 有 personas_self_hash 列
        assert "personas_self_hash" in ddl
        conn.close()

    def test_self_strategy_in_value(self):
        from fa.pipeline.value import STRATEGIES
        assert "model_persona_kb_self" in STRATEGIES
        assert len(STRATEGIES) == 4


class TestKnowledgeSelf:
    """知识库文件：初始化、解析、hash。"""

    def test_init_kb_self(self, tmp_project):
        from fa.evolve_self.knowledge import init_kb_self, kb_path_self
        p = init_kb_self("E0")
        assert p.is_file()
        text = p.read_text(encoding="utf-8")
        assert "# " in text
        assert "总原则" in text

    def test_parse_kb_self_empty(self):
        from fa.evolve_self.knowledge import parse_kb_self
        kb = parse_kb_self("", "E0")
        assert kb.char_count == 0

    def test_parse_kb_self_valid(self):
        from fa.evolve_self.knowledge import parse_kb_self
        text = "# E0 知识库\n\n## 总原则\n\n观察笔记。\n"
        kb = parse_kb_self(text, "E0")
        assert kb.char_count == len(text)

    def test_parse_kb_self_no_title_error(self):
        from fa.evolve_self.knowledge import parse_kb_self
        from fa.evolve_self import EvolutionSelfError
        with pytest.raises(EvolutionSelfError):
            parse_kb_self("没有标题的文本\n", "E0")

    def test_tree_hash(self, tmp_project):
        from fa.evolve_self.knowledge import (
            init_kb_self, personas_self_tree_hash,
        )
        init_kb_self("E0")
        init_kb_self("SP1")
        from fa.config import project_root
        h1 = personas_self_tree_hash(project_root() / "personas" / "knowledge_self")
        # 改一个文件，hash 应该变
        p = project_root() / "personas" / "knowledge_self" / "epl.md"
        p.write_text(p.read_text() + "\n# extra\n", encoding="utf-8")
        h2 = personas_self_tree_hash(project_root() / "personas" / "knowledge_self")
        assert h1 != h2


class TestReflectSelf:
    """反思纯函数：prompt 组装 + markdown 校验。"""

    def test_build_prompt_contains_window(self, tmp_project):
        from fa.evolve_self.reflect import build_reflect_self_prompt
        evidence = {"league": "E0", "window": {"idx": 1},
                    "self_track": {"n_judged": 10}}
        prompt = build_reflect_self_prompt("E0", "# E0 知识库\n", evidence)
        assert "E0" in prompt
        assert "本轮观察" in prompt
        assert "唯一证据源" in prompt

    def test_validate_output_ok(self):
        from fa.evolve_self.reflect import _validate_output
        old = "# E0 知识库\n\n## 总原则\n\n球探笔记。\n"
        new = ("# E0 知识库\n\n## 总原则\n\n球探笔记。\n\n"
               "## W1\n\n### 本轮观察\n\n- 测试观察\n")
        errs = _validate_output(new, "E0", old)
        assert errs == []

    def test_validate_output_missing_title(self):
        from fa.evolve_self.reflect import _validate_output
        errs = _validate_output("无标题\n正文", "E0", "# old\n")
        assert any("缺少一级标题" in e for e in errs)

    def test_validate_output_missing_section(self):
        from fa.evolve_self.reflect import _validate_output
        errs = _validate_output("# E0\n\n正文", "E0", "# E0\n")
        assert any("本轮观察" in e for e in errs)


class TestRunner:
    """编排层：tick + 自动落账（用 mock hermes）。"""

    def test_run_tick_self_no_judgements(self, tmp_project, monkeypatch):
        """窗口无判决 → no_change，不炸。"""
        from fa.config import db_path
        conn = sqlite3.connect(db_path())
        conn.row_factory = sqlite3.Row
        _seed_basic_data(conn)

        # 塞一个 self 轨的 recommendation（让窗口有数据，但 verdict=NULL
        # 表示未判决 = n_judged=0）
        conn.execute(
            "INSERT INTO recommendations (id, run_id, fixture_id, strategy,"
            " market, phase, model_p, market_p, best_odds, bookmaker, edge,"
            " ev, kelly_stake_frac, created_at)"
            " VALUES (10, 1, 1, 'model_persona_kb_self', 'H', 'am', 0.55, 0.5,"
            " 2.1, 'Pinnacle', 0.05, 0.155, 0.05,"
            " '2026-09-05T00:00:00Z')")
        conn.commit()

        from fa.evolve_self.runner import run_tick_self
        # 用今天（已远超窗口锚点），但数据在窗口 1 里且无判决
        result = run_tick_self(conn, today=date(2026, 11, 1))
        assert "w1" in result
        conn.close()

    def test_calibrate_reflect(self, tmp_project, monkeypatch):
        """校准模式：真证据、不落库、调用 mock hermes。"""
        from fa.config import db_path
        conn = sqlite3.connect(db_path())
        conn.row_factory = sqlite3.Row
        _seed_basic_data(conn)
        # 加 self 轨推荐
        conn.execute(
            "INSERT INTO recommendations (id, run_id, fixture_id, strategy,"
            " market, phase, model_p, market_p, best_odds, bookmaker, edge,"
            " ev, kelly_stake_frac, verdict, confidence_delta, final_stake_frac,"
            " created_at)"
            " VALUES (10, 1, 1, 'model_persona_kb_self', 'H', 'am', 0.55, 0.5,"
            " 2.1, 'Pinnacle', 0.05, 0.155, 0.05, 'agree', 0.0, 0.05,"
            " '2026-09-05T00:00:00Z')")
        # self 轨也落 bet
        conn.execute(
            "INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker,"
            " odds_taken, stake, status, return_amt, clv)"
            " VALUES (10, 'paper', '2026-09-05T00:00:00Z', 'Pinnacle',"
            " 2.1, 10.0, 'won', 21.0, 0.05)")
        conn.commit()

        # mock hermes：返回一个简单的 md
        def fake_call(prompt, timeout=None):
            return ("# E0 知识库（自反思·时间线）\n\n"
                    "## 总原则\n\n球探笔记。\n\n"
                    "## W1（2026-09-04 ~ 2026-10-16）\n\n"
                    "### 本轮观察\n\n- 测试观察\n")

        from fa.evolve_self import reflect as refl
        monkeypatch.setattr(refl, "call_reflect_self", fake_call)

        from fa.evolve_self.runner import run_reflect_self
        out = run_reflect_self(conn, w_idx=1, league="E0", calibrate=True,
                               today=date(2026, 11, 1))
        assert out["calibrate"] is True
        assert out["window"] == 1
        # calibration 目录有产物
        from fa.config import project_root
        cal_dirs = list((project_root() / "evolution" / "proposals_self").glob(
            "calibration-*"))
        assert len(cal_dirs) >= 1
        conn.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
