import os
from pathlib import Path

import pytest

import fa.config as config
from fa.config import (LEAGUES, ODDS_SPORT_KEYS, PERSONA_FILES, PERSONA_TIMEOUT_S,
                       hermes_bin, load_env, odds_api_key, persona_path,
                       persona_timeout, project_root)

# 本机 shell 常已导出 ODDS_API_KEY，测试须与真实环境隔离，断言才可复现
PROBE_KEYS = ("FA_ENV_PROBE", "FA_ENV_OTHER", "FA_ENV_ODDS")

# 夹具 setup 时的进程环境基线：真机本来就导出的键不算「泄漏」
_ENV_BASELINE: set[str] = set()


@pytest.fixture
def clean_env(monkeypatch):
    """快照式恢复整个 os.environ。

    load_env 直写 os.environ，不在 monkeypatch 追踪内（undo() 撤不掉，
    见 test_load_env_write_escapes_monkeypatch_undo），故 setup 存快照、
    teardown 整体还原。快照只管「还原」，真机已导出的键还须主动清掉，
    缺失分支才可断言；monkeypatch 仍负责 setenv/setattr。
    """
    saved = dict(os.environ)
    _ENV_BASELINE.clear()
    _ENV_BASELINE.update(saved)
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    for key in PROBE_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield monkeypatch
    os.environ.clear()
    os.environ.update(saved)


def _write_env(tmp_path: Path, text: str) -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(text, encoding="utf-8")
    return env_file


def test_load_env_sets_os_environ(clean_env, tmp_path):
    env_file = _write_env(tmp_path, "FA_ENV_PROBE=abc123\n")
    assert load_env(env_file) == {"FA_ENV_PROBE": "abc123"}
    assert os.environ["FA_ENV_PROBE"] == "abc123"


def test_env_restored_after_direct_write():
    """上一例 load_env 直写 os.environ，只能靠夹具快照还原。

    紧跟其上一例（其间无其它 clean_env 的 setup 会 scrub 探针键），
    故这是快照恢复的 load-bearing 断言；真机导出的键不算泄漏。
    """
    assert "FA_ENV_PROBE" not in (set(os.environ) - _ENV_BASELINE)


def test_load_env_write_escapes_monkeypatch_undo(tmp_path, monkeypatch):
    """实证 load_env 的直写不在 monkeypatch 追踪内——undo() 撤不掉。

    这正是 clean_env 必须快照式恢复的原因：只靠 undo() 会把键泄漏给后续用例。
    """
    env_file = _write_env(tmp_path, "FA_ENV_UNDO=1\n")
    load_env(env_file)
    monkeypatch.undo()
    assert os.environ.get("FA_ENV_UNDO") == "1"  # undo 之后仍在 → 逃逸实证
    del os.environ["FA_ENV_UNDO"]  # undo 撤不掉，手工清理


def test_load_env_ignores_comments_and_blank_lines(clean_env, tmp_path):
    env_file = _write_env(
        tmp_path,
        "# 注释行\n\n   \nFA_ENV_PROBE = v1 \n  # 缩进注释\nFA_ENV_OTHER=v2\n",
    )
    assert load_env(env_file) == {"FA_ENV_PROBE": "v1", "FA_ENV_OTHER": "v2"}


def test_load_env_existing_env_wins(clean_env, tmp_path):
    clean_env.setenv("FA_ENV_PROBE", "from-shell")
    env_file = _write_env(tmp_path, "FA_ENV_PROBE=from-file\nFA_ENV_OTHER=x\n")
    # 已存在的键既不覆盖，也不计入实际加载结果
    assert load_env(env_file) == {"FA_ENV_OTHER": "x"}
    assert os.environ["FA_ENV_PROBE"] == "from-shell"


def test_load_env_missing_file_is_empty(clean_env, tmp_path):
    assert load_env(tmp_path / "nope.env") == {}


def test_load_env_defaults_to_project_root_dotenv(clean_env, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    _write_env(tmp_path, "FA_ENV_PROBE=root-dotenv\n")
    assert load_env() == {"FA_ENV_PROBE": "root-dotenv"}


def test_cli_startup_loads_project_root_dotenv(clean_env, tmp_path, monkeypatch):
    """CLI 启动即读 project_root/.env——spec §9.4「key 走 .env」的唯一接线点。

    没有这行接线，.env 是死配置：程序从不调用 load_env，用户按文档把 key 写进
    .env 后 `fa run matchday` 依然 no_key，hermes cron（§9.6）的裸环境更拿不到。
    load_env 直写 os.environ（见上 test_load_env_write_escapes_monkeypatch_undo），
    故用 clean_env 的快照兜底还原；monkeypatch 只负责把 project_root 指到 tmp_path。
    """
    from typer.testing import CliRunner

    from fa.cli import app

    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    _write_env(tmp_path, "ODDS_API_KEY=from-dotenv-123\n")
    assert odds_api_key() is None                    # 前置：文件此时还没被读

    result = CliRunner().invoke(app, ["version"])

    assert result.exit_code == 0, result.output
    assert os.environ["ODDS_API_KEY"] == "from-dotenv-123"
    assert odds_api_key() == "from-dotenv-123"


def test_cli_env_does_not_override_exported_key(clean_env, tmp_path, monkeypatch):
    """load_env 的 setdefault 语义穿过 CLI：shell 已导出的 key 优先于 .env。"""
    from typer.testing import CliRunner

    from fa.cli import app

    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    clean_env.setenv("ODDS_API_KEY", "from-shell")
    _write_env(tmp_path, "ODDS_API_KEY=from-dotenv-123\n")

    result = CliRunner().invoke(app, ["version"])

    assert result.exit_code == 0, result.output
    assert odds_api_key() == "from-shell"


def test_cli_dotenv_write_is_visible_to_the_process(tmp_path, monkeypatch):
    """canary 前半（顺序配对，紧跟下一例）：CliRunner 经 typer 回调 load_env 直写
    os.environ——这正是真机 .env 的真 key 会跨用例泄漏的写入点。

    **故意不用 clean_env**：test_cli.py 的 CliRunner 用例也没有——泄漏正是从那类
    用例发生的；clean_env 自己的快照还原会把本 canary 掩盖成永绿。
    monkeypatch.delenv 只为在宿主机已导出真 key 时（``ODDS_API_KEY=real`` 跑全套）
    让 .env 的 canary 仍能写进进程环境，undo 后真 key 原样回来。
    """
    from typer.testing import CliRunner

    from fa.cli import app

    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    _write_env(tmp_path, "ODDS_API_KEY=canary-dotenv-7f3a\n")

    result = CliRunner().invoke(app, ["version"])

    assert result.exit_code == 0, result.output
    assert os.environ["ODDS_API_KEY"] == "canary-dotenv-7f3a"


def test_cli_dotenv_key_does_not_leak_into_later_tests(env_baseline):
    """canary 后半（顺序配对，紧跟上一例，其间不得插入别的用例）。

    上一例的 CliRunner 用例把 .env 的 key 直写进 os.environ，monkeypatch.undo()
    撤不掉（实证见上 test_load_env_write_escapes_monkeypatch_undo）；真机上那个
    就是真 key，会泄漏给后续所有用例，把「无 key」分支测成「有 key」。终审
    Important #1 后由 conftest 的 autouse ``seal_environ`` 在上一例 teardown 快照
    还原——若失守，这里读到的就是 canary 值。真机导出的键属基线（不算泄漏），
    只断言基线之外的增量。
    """
    assert os.environ.get("ODDS_API_KEY") != "canary-dotenv-7f3a"
    leaked = {k for k in set(os.environ) - set(env_baseline)
              if k.startswith("FA_") or k == "ODDS_API_KEY"}
    assert not leaked, f"环境键跨用例泄漏: {sorted(leaked)}"


def test_odds_api_key_from_env(clean_env):
    clean_env.setenv("ODDS_API_KEY", "k-123")
    assert odds_api_key() == "k-123"


def test_odds_api_key_missing_is_none(clean_env):
    assert odds_api_key() is None


def test_odds_api_key_empty_string_is_none(clean_env):
    clean_env.setenv("ODDS_API_KEY", "")
    assert odds_api_key() is None


def test_sport_keys_mapping():
    assert ODDS_SPORT_KEYS == {
        "E0": "soccer_epl",
        "SP1": "soccer_spain_la_liga",
        "D1": "soccer_germany_bundesliga",
        "I1": "soccer_italy_serie_a",
        "F1": "soccer_france_ligue_one",
    }
    assert set(ODDS_SPORT_KEYS) == set(LEAGUES)


def test_project_root_holds_src_fa():
    assert project_root() == Path(__file__).resolve().parents[1]


def test_persona_files_cover_five_leagues():
    assert set(PERSONA_FILES) == {"E0", "SP1", "D1", "I1", "F1"}


def test_persona_path_maps_and_rejects(tmp_path, monkeypatch):
    monkeypatch.setattr("fa.config.project_root", lambda: tmp_path)
    assert (persona_path("D1") ==
            tmp_path / "personas" / "bundesliga.md")
    with pytest.raises(ValueError):
        persona_path("XX")


def test_hermes_bin_env_override(monkeypatch):
    monkeypatch.setenv("HERMES_BIN", "/tmp/fake-hermes")
    assert hermes_bin() == "/tmp/fake-hermes"
    monkeypatch.delenv("HERMES_BIN")
    assert hermes_bin() == "hermes"


def test_persona_timeout_default_and_override(monkeypatch):
    monkeypatch.delenv("FA_PERSONA_TIMEOUT", raising=False)
    assert persona_timeout() == PERSONA_TIMEOUT_S == 120.0
    monkeypatch.setenv("FA_PERSONA_TIMEOUT", "5")
    assert persona_timeout() == 5.0
    monkeypatch.setenv("FA_PERSONA_TIMEOUT", "not-a-number")
    assert persona_timeout() == 120.0        # 非法值回退默认，不炸
