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
