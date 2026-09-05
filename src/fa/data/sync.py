import sqlite3
from dataclasses import dataclass, field
from datetime import date

from fa.config import LEAGUES, SEASONS_FROM
from fa.data.download import download_csv
from fa.data.ingest import backfill_bfe_rows, ingest_rows
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
    """下载并入库历史+当前赛季 CSV（幂等：分区内容哈希跳过）。

    当前赛季分区（``year == 当前赛季起始年``）无条件 ``refresh=True`` 重下
    （spec v0.12 §9.5）——否则 daily 永远读陈旧缓存、赛果零入库（2026-09-05
    实况：缓存停在 08-31，九月 158 注 paper 全 pending）；历史赛季维持
    缓存 + 哈希跳过，``refresh=True`` 时全量重下。
    """
    rep = SyncReport()
    to_year = _current_season_start()
    for league in LEAGUES:
        for year in range(seasons_from, to_year + 1):
            # 单文件容错：一个赛季失败（下载/解析/入库）只记账，不中断整个 sync。
            # ingest_rows 失败时自回滚，这里无需再回滚，只保证异常不外溢。
            try:
                # 当前赛季是 live 数据：无条件强制重下；refresh 形参只额外
                # 作用到历史赛季
                path = download_csv(league, year, refresh=refresh or year == to_year)
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


@dataclass
class BackfillReport:
    filled: int = 0                    # 实际回填的 matches 行数
    files_ok: int = 0
    missing: list[tuple[str, int]] = field(default_factory=list)
    file_errors: list[tuple[str, int, str]] = field(default_factory=list)


def backfill_bfe(conn: sqlite3.Connection, seasons_from: int = 2024,
                 refresh: bool = False) -> BackfillReport:
    """把 BFE 收盘列回填进已入库分区（Pinnacle 断供应对，spec §7.3）。

    只扫 ``seasons_from`` 起的赛季——BFE 列 2024-25 才出现在 CSV 里，更老的
    分区回填必然全空。管线与 :func:`sync_history` 同构（download/parse 复用，
    单文件失败只记账）；入库语义换成 :func:`backfill_bfe_rows` 的**纯 UPDATE**
    （不重建分区——matches.id 有外键引用）。幂等：只填 NULL，重跑零变化。
    """
    rep = BackfillReport()
    to_year = _current_season_start()
    for league in LEAGUES:
        for year in range(seasons_from, to_year + 1):
            try:
                path = download_csv(league, year, refresh=refresh)
                if path is None:
                    rep.missing.append((league, year))
                    continue
                content = read_csv_text(path)
                if not content.strip():
                    rep.file_errors.append((league, year, "空文件"))
                    continue
                rows = parse_csv(content, league, year)
                dated = [r for r in rows if r.date]
                rep.filled += backfill_bfe_rows(conn, league, year, dated)
            except Exception as e:   # 只记账：单赛季失败不中断（同 sync 纪律）
                rep.file_errors.append((league, year, f"{type(e).__name__}: {e}"))
                continue
            rep.files_ok += 1
    return rep
