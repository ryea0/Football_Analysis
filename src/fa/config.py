import os
from pathlib import Path

LEAGUES: dict[str, str] = {
    "E0": "Premier League",
    "SP1": "La Liga",
    "D1": "Bundesliga",
    "I1": "Serie A",
    "F1": "Ligue 1",
}
SEASONS_FROM = 1993
BASE_URL = "https://www.football-data.co.uk/mmz4281"

# The Odds API 的 sport key（B 线实时盘，spec §3.1）——键与 LEAGUES 一一对应
ODDS_SPORT_KEYS: dict[str, str] = {
    "E0": "soccer_epl",
    "SP1": "soccer_spain_la_liga",
    "D1": "soccer_germany_bundesliga",
    "I1": "soccer_italy_serie_a",
    "F1": "soccer_france_ligue_one",
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def db_path() -> Path:
    return Path(os.environ.get("FA_DB", project_root() / "data" / "fa.db"))


def csv_cache_dir() -> Path:
    return project_root() / "data" / "csv"


def season_code(start_year: int) -> str:
    """2025 -> '2526'；1999 -> '9900'"""
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def csv_url(league: str, start_year: int) -> str:
    return f"{BASE_URL}/{season_code(start_year)}/{league}.csv"


def load_env(path: Path | None = None) -> dict[str, str]:
    """读 .env 的 KEY=VALUE 进 os.environ（setdefault 语义：已有 env 优先）。

    返回实际加载进 os.environ 的键值；忽略空行与 `#` 注释行；
    不处理行内注释与引号（`KEY=abc  # x` 会把注释并入值）；
    文件缺失返回 {}（.env 是可选项，spec §9.4）。
    """
    env_file = path or project_root() / ".env"
    loaded: dict[str, str] = {}
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return loaded
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, _, value = text.partition("=")
        key, value = key.strip(), value.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value
        loaded[key] = value
    return loaded


def odds_api_key() -> str | None:
    """Odds API key；未配置或空串视为 None（额度纪律见 spec §3.4）。"""
    return os.environ.get("ODDS_API_KEY") or None


# ---------------------------------------------------------------- persona（M4，spec §6）

PERSONA_TIMEOUT_S = 120.0            # §6.2「超时默认 120s（可配）」
PERSONA_FILES = {                    # §9.2 目录规划（personas/ 文件名）
    "E0": "epl.md", "SP1": "laliga.md", "D1": "bundesliga.md",
    "I1": "seriea.md", "F1": "ligue1.md",
}


def hermes_bin() -> str:
    """hermes 可执行文件路径；测试以 env HERMES_BIN 指向 fixture 脚本（C1：mock
    与实跑同一 subprocess 代码路径）。"""
    return os.environ.get("HERMES_BIN") or "hermes"


def persona_timeout() -> float:
    """persona 单次调用超时秒数（env FA_PERSONA_TIMEOUT 覆盖；非法值回退默认）。"""
    raw = os.environ.get("FA_PERSONA_TIMEOUT")
    if raw is None:
        return PERSONA_TIMEOUT_S
    try:
        return float(raw)
    except ValueError:
        return PERSONA_TIMEOUT_S


def persona_search_enabled() -> bool:
    """web_search 对 persona 是否可用（env ``FA_PERSONA_SEARCH``，默认 off）。

    判定权交运维：M5 配好搜索后端 key 时置 1——届时 :func:`build_prompt` 的
    禁工具过渡条款自动消失（stopgap，根治 = 配 key；T15 fix round 1，
    m4-report §2.1 实测 derail 13/27 的对症缓解）。
    """
    return os.environ.get("FA_PERSONA_SEARCH", "").strip().lower() in (
        "1", "true", "yes", "on")


def persona_path(league: str) -> Path:
    """联赛 → personas/ 下的人格文件路径（spec §9.2）。"""
    try:
        name = PERSONA_FILES[league]
    except KeyError:
        raise ValueError(f"无 persona 文件映射：{league!r}") from None
    return project_root() / "personas" / name
