"""页面冒烟：AppTest 逐页跑空库——页面必须优雅空态、不抛异常（设计 §7）。"""
from pathlib import Path

import pytest

st = pytest.importorskip("streamlit")   # 裸环境（无 dashboard 组）整文件跳过
import streamlit.testing.v1   # noqa: F401  子包懒加载，import streamlit 不带出 st.testing
from fa.db import init_db

PAGES = sorted((Path(__file__).resolve().parents[2] / "dashboard" / "pages").glob("*.py"))
assert PAGES, "dashboard/pages/ 下没有页面文件"   # 空目录即失败信号，不让冒烟静默空跑


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_page_renders_on_empty_db(page, tmp_path, monkeypatch):
    monkeypatch.setenv("FA_DB", str(tmp_path / "t.db"))
    init_db(tmp_path / "t.db")
    at = st.testing.v1.AppTest.from_file(str(page), default_timeout=60)
    at.run()
    assert not at.exception
