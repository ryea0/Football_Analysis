import urllib.error
import urllib.request
from pathlib import Path

from fa.config import csv_url, csv_cache_dir, season_code


def csv_cache_path(league: str, start_year: int) -> Path:
    return csv_cache_dir() / f"{league}_{season_code(start_year)}.csv"


def download_csv(league: str, start_year: int, refresh: bool = False) -> Path | None:
    """下载 football-data.co.uk 赛季 CSV 到本地缓存。404 -> None（该赛季无数据）。"""
    path = csv_cache_path(league, start_year)
    if path.exists() and not refresh:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(csv_url(league, start_year), timeout=30) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    path.write_bytes(data)
    return path
