"""测试进程级环境隔离（须在 fa.cli / typer 被 import 前生效，故用模块级语句）。"""
import os

# FORCE_COLOR 在部分环境非空时，typer 于 import 时（rich_utils.FORCE_TERMINAL）把
# rich console 设为 force_terminal，--help 的选项名被高亮 span 拆断
# （--half-life → `-` + `-half` + `-life`），纯文本断言因此匹配不到。
# setdefault：显式覆盖仍可生效。
os.environ.setdefault("_TYPER_FORCE_DISABLE_TERMINAL", "1")

import pytest

# 全局环境基线：pytest 启动时（或上一用例还原后）的进程环境。
# 真机本来就导出的键不算「泄漏」，还原只讲「回到用例前」，不做 scrub。
_ENV_BASELINE: dict[str, str] = {}


@pytest.fixture(autouse=True)
def seal_environ():
    """每个用例前后快照式密封 ``os.environ``（终审 Important #1）。

    为什么必须全局做：``fa.cli`` 的 typer 回调在每个 CliRunner 用例里都会跑
    ``load_env()``（读 ``project_root/.env``），而 :func:`fa.config.load_env`
    **直写 os.environ**——不在 monkeypatch 追踪内，``undo()`` 撤不掉（实证见
    ``tests/test_config.py::test_load_env_write_escapes_monkeypatch_undo``）。
    真机 .env 里的真 key 因此写进进程环境并跨用例泄漏：后续「无 key」分支被
    测成「有 key」，隔离性从 hermetic 退化为依赖宿主机状态。

    语义与 ``tests/test_config.py`` 的 ``clean_env`` 同一套（clear+update 快照还原）：
    快照只管「还原」，不主动清键；monkeypatch 仍负责 setenv/delenv，且其 undo
    发生在本夹具 teardown 之前，两次还原幂等——per-file 夹具无须改动即可叠加。
    """
    saved = dict(os.environ)
    _ENV_BASELINE.clear()
    _ENV_BASELINE.update(saved)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def env_baseline() -> dict[str, str]:
    """当前用例 setup 时的进程环境快照（只读副本）。

    供「顺序配对」的密封性 canary 断言使用：真机导出的键属于基线，不算泄漏；
    基线之外的键（如 CliRunner 用例从 .env 带出的）才算。
    """
    return dict(_ENV_BASELINE)
