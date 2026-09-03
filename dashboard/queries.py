"""dashboard 查询层：只读 SQL + fa 纯函数复用。

设计：docs/superpowers/specs/2026-09-04-web-dashboard-design.md（§2/§5）。
本模块不 import streamlit（缓存包在 loaders.py）；不碰任何有副作用的
fa 模块（写库 / hermes / 网络）；指标公式一律复用 fa.backtest.*。
"""
import sqlite3
from pathlib import Path

from fa.config import db_path


def connect_ro(path: Path | None = None) -> sqlite3.Connection:
    """只读连接（mode=ro）：dashboard 的任何 bug 都写不了库。

    WAL 下只读读者与管线写入互不阻塞；库不存在时给出可操作的报错
    （页面层捕获后提示先 `fa init`）。
    """
    p = Path(path) if path else db_path()
    if not p.exists():
        raise FileNotFoundError(f"数据库不存在：{p}（先运行 uv run fa init）")
    conn = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
