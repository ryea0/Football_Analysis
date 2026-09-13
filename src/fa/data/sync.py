import sqlite3
from dataclasses import dataclass, field
from datetime import date

from fa.config import LEAGUES, SEASONS_FROM
from fa.data.download import download_csv
from fa.data.ingest import FALLBACK_MARKER_SQL, backfill_bfe_rows, ingest_rows
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
    # v0.13 恢复闭环：官方行覆盖 fallback 行后的比分 diff（一致静默、不一致
    # 记账供 daily/CLI 层告警不改账）；fail_streak = 主源当前赛季失败连续天数
    fallback_diffs: list[tuple[str, int, str, str, str]] = field(
        default_factory=list)
    fail_streak: int = 0


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
                # v0.13 恢复闭环：当前赛季重建前快照 fallback 行、重建后 diff
                # （官方行缺席则跳过，下轮再比）——数据层只记账，告警归调用层
                snap = (_fallback_snapshot(conn, league, year)
                        if year == to_year else None)
                n = ingest_rows(conn, league, year, dated)
                if snap:
                    rep.fallback_diffs.extend(
                        _diff_official(conn, league, year, snap))
            except Exception as e:   # 只记账：BaseException（如键盘中断）仍外溢
                rep.file_errors.append((league, year, f"{type(e).__name__}: {e}"))
                continue
            rep.files_ok += 1
            if n:
                rep.inserted += n
            else:
                rep.skipped_seasons += 1
    rep.fail_streak = _update_fail_streak(conn, rep.file_errors)
    return rep


# ---------------------------------------------------------------- v0.13 恢复闭环

FAIL_STREAK_META_KEY = "current_season_fail_streak"


def _fallback_snapshot(conn: sqlite3.Connection, league: str,
                       season: int) -> dict[str, tuple[int, int]]:
    """该分区 fallback 行快照 ``{date|home_id|away_id: (fthg, ftag)}``（重建前抓）。"""
    out: dict[str, tuple[int, int]] = {}
    for r in conn.execute(
            "SELECT date, home_team_id, away_team_id, fthg, ftag FROM matches"
            f" WHERE league=? AND season=? AND {FALLBACK_MARKER_SQL}",
            (league, season)):
        out[f"{r['date']}|{r['home_team_id']}|{r['away_team_id']}"] = (
            r["fthg"], r["ftag"])
    return out


def _diff_official(conn: sqlite3.Connection, league: str, season: int,
                   snapshot: dict[str, tuple[int, int]]
                   ) -> list[tuple[str, int, str, str, str]]:
    """同 key 官方行比分 diff：一致 → ``[]``（幂等覆盖静默）；
    不一致 → ``(league, season, key, fallback 比分, 官方比分)``（只记账不改账，
    裁定②）。官方内容尚无该场（行缺席或仍是 fallback 行）→ 跳过，下轮再比。"""
    diffs: list[tuple[str, int, str, str, str]] = []
    for key, (fh, fa) in snapshot.items():
        d, h, a = key.split("|")
        row = conn.execute(
            "SELECT fthg, ftag, raw_line FROM matches WHERE league=? AND season=?"
            " AND date=? AND home_team_id=? AND away_team_id=?",
            (league, season, d, int(h), int(a))).fetchone()
        if row is None:
            continue                     # 官方还没覆盖该场：下轮 sync 再比
        raw = row["raw_line"] or ""
        if "fallback" in raw or "stopgap" in raw:
            continue                     # 仍是 fallback 行（重建未过/收缩拒绝）
        if (row["fthg"], row["ftag"]) != (fh, fa):
            diffs.append((league, season, f"{d} #{h}v#{a}",
                          f"{fh}-{fa}", f"{row['fthg']}-{row['ftag']}"))
    return diffs


def _update_fail_streak(conn: sqlite3.Connection,
                        file_errors: list[tuple[str, int, str]]) -> int:
    """主源当前赛季失败连续天数（N=2 告警判据，裁定③；成功日归零）。

    只看「当前赛季条目」的失败——历史赛季缓存失败不构成赛果断供。meta 计数
    随手 commit（独立于分区入库的事务纪律）。返回更新后的 streak。
    """
    from fa.db import get_meta, set_meta
    year_now = _current_season_start()
    cur_season_failed = any(y == year_now for (_lg, y, _msg) in file_errors)
    prev = int(get_meta(conn, FAIL_STREAK_META_KEY) or 0)
    streak = prev + 1 if cur_season_failed else 0
    set_meta(conn, FAIL_STREAK_META_KEY, str(streak))
    conn.commit()
    return streak


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
