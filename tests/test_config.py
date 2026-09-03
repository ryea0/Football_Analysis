import os
from pathlib import Path

import pytest

import fa.config as config
from fa.config import LEAGUES, ODDS_SPORT_KEYS, load_env, odds_api_key, project_root

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
