"""dashboard 查询层测试：只读契约 + 各查询函数（设计 §5/§7）。

种子日期全部用字面量；断言数字均手算钉死。
"""
import sqlite3

import pytest

from fa.db import init_db


def test_connect_ro_missing_db_raises(tmp_path):
    from queries import connect_ro

    with pytest.raises(FileNotFoundError):
        connect_ro(tmp_path / "nope.db")


def test_connect_ro_is_readonly(tmp_path):
    from queries import connect_ro

    init_db(tmp_path / "t.db")
    ro = connect_ro(tmp_path / "t.db")
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("INSERT INTO meta (key, value) VALUES ('k', 'v')")
    finally:
        ro.close()
