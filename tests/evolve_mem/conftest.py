"""evolve_mem 测试基线：隔离 root + fake 检索器（env 切换，同路径不同实现）。"""
import pytest

from fa import config


@pytest.fixture
def mem_root(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    monkeypatch.setenv("FA_MEM_RETRIEVER", "fake")
    (tmp_path / "personas").mkdir()
    return tmp_path


KB_E0 = """<!-- kb: league=E0 -->
# E0 知识库

## 结构性认知
- [E0-S01] 阿森纳主场控球压制
- [E0-S02] 升班马客场保守

## 时效
- [E0-T01|2026-09-01|90d] 某队伤停潮

## 教训
- [E0-L01|2026-09-10] 某教训条目
"""


def write_kb(root, league="E0", text=KB_E0):
    from fa.config import PERSONA_FILES
    d = root / "personas" / "knowledge"
    d.mkdir(parents=True, exist_ok=True)
    (d / PERSONA_FILES[league]).write_text(text, encoding="utf-8")
