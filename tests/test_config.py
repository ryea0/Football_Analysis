import os
from pathlib import Path

import pytest

import fa.config as config
from fa.config import LEAGUES, ODDS_SPORT_KEYS, load_env, odds_api_key, project_root

# 本机 shell 常已导出 ODDS_API_KEY，探针键须先清干净，setdefault 语义才可断言
PROBE_KEYS = ("FA_ENV_PROBE", "FA_ENV_OTHER", "FA_ENV_ODDS")


@pytest.fixture
def clean_env(monkeypatch):
    for key in (*PROBE_KEYS, "ODDS_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def _write_env(tmp_path: Path, text: str) -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(text, encoding="utf-8")
    return env_file


def test_load_env_sets_os_environ(clean_env, tmp_path):
    env_file = _write_env(tmp_path, "FA_ENV_PROBE=abc123\n")
    assert load_env(env_file) == {"FA_ENV_PROBE": "abc123"}
    assert os.environ["FA_ENV_PROBE"] == "abc123"


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
