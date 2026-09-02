import sqlite3
from dataclasses import dataclass, field
from datetime import date

from fa.config import LEAGUES, SEASONS_FROM
from fa.data.download import download_csv
from fa.data.ingest import ingest_rows
from fa.data.parse import parse_csv
from fa.data.reader import read_csv_text


@dataclass
class SyncReport:
    files_ok: int = 0
    files_missing: int = 0
    inserted: int = 0
    skipped_seasons: int = 0
    missing: list[tuple[str, int]] = field(default_factory=list)
    file_errors: list[tuple[str, int, str]] = field(default_factory=list)
    rows_no_date: int = 0


def _current_season_start() -> int:
    t = date.today()
    return t.year if t.month >= 8 else t.year - 1


def sync_history(conn: sqlite3.Connection, seasons_from: int = SEASONS_FROM,
                 refresh: bool = False) -> SyncReport:
    rep = SyncReport()
    to_year = _current_season_start()
    for league in LEAGUES:
        for year in range(seasons_from, to_year + 1):
            # 单文件容错：一个赛季失败（下载/解析/入库）只记账，不中断整个 sync。
            # ingest_rows 失败时自回滚，这里无需再回滚，只保证异常不外溢。
            try:
                path = download_csv(league, year, refresh=refresh)
                if path is None:
                    rep.files_missing += 1
                    rep.missing.append((league, year))
                    continue
                content = read_csv_text(path)
                if not content.strip():
                    # Task 3 可能缓存 0 字节的 200 响应；parse_csv 会抛
                    # EmptyDataError，这里按文件级错误处理而不是让它炸掉
                    rep.file_errors.append((league, year, "空文件（0 字节或全空白）"))
                    continue
                rows = parse_csv(content, league, year)
                # matches.date NOT NULL：无日期行入库即失败，先过滤并计数
                dated = [r for r in rows if r.date]
                rep.rows_no_date += len(rows) - len(dated)
                n = ingest_rows(conn, league, year, dated)
            except Exception as e:   # 只记账：BaseException（如键盘中断）仍外溢
                rep.file_errors.append((league, year, f"{type(e).__name__}: {e}"))
                continue
            rep.files_ok += 1
            if n:
                rep.inserted += n
            else:
                rep.skipped_seasons += 1
    return rep
