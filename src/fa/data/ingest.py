import hashlib
import json
import sqlite3

from fa.data.parse import MatchRow
from fa.data.teams import get_or_create_team
from fa.db import get_meta, set_meta


def _rows_hash(rows: list[MatchRow]) -> str:
    """整行指纹：任意列的上游修正（含 ps_draw / shots 等非抽样字段）都改变哈希，重跑即收敛。"""
    payload = "\n".join(
        json.dumps(r.raw, ensure_ascii=False, sort_keys=True) for r in rows)
    return hashlib.sha256(payload.encode()).hexdigest()


def ingest_rows(conn: sqlite3.Connection, league: str, season: int,
                rows: list[MatchRow]) -> int:
    if not rows:
        return 0  # 空输入：不动分区，也绝不写入空内容哈希
    key = f"csv_hash:{league}:{season}"
    h = _rows_hash(rows)
    if get_meta(conn, key) == h:
        return 0
    try:
        conn.execute("DELETE FROM matches WHERE league=? AND season=?",
                     (league, season))
        inserted = 0
        for r in rows:
            home_id = get_or_create_team(conn, league, r.home)
            away_id = get_or_create_team(conn, league, r.away)
            conn.execute(
                "INSERT INTO matches (league, season, date, home_team_id, away_team_id,"
                " fthg, ftag, shots_home, shots_away, shots_target_home, shots_target_away,"
                " corners_home, corners_away, ps_home, ps_draw, ps_away,"
                " psc_home, psc_draw, psc_away, over25_ps, under25_ps,"
                " over25_psc, under25_psc,"
                " bfe_home, bfe_draw, bfe_away, over25_bfe, raw_line)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r.league, r.season, r.date, home_id, away_id,
                 r.fthg, r.ftag, r.shots_home, r.shots_away,
                 r.shots_target_home, r.shots_target_away,
                 r.corners_home, r.corners_away,
                 r.ps_home, r.ps_draw, r.ps_away,
                 r.psc_home, r.psc_draw, r.psc_away,
                 r.over25_ps, r.under25_ps, r.over25_psc, r.under25_psc,
                 r.bfe_home, r.bfe_draw, r.bfe_away, r.over25_bfe,
                 json.dumps(r.raw, ensure_ascii=False)))
            inserted += 1
        set_meta(conn, key, h)
        # commit 一并放进 try：SQLITE_FULL / IO 错误时同样回滚，不留悬挂事务
        conn.commit()
    except BaseException:
        try:
            conn.rollback()
        except Exception:
            pass  # 回滚失败时保住原始异常
        raise
    return inserted


def backfill_bfe_rows(conn: sqlite3.Connection, league: str, season: int,
                      rows: list) -> int:
    """把已入库 matches 的 BFE 收盘列从解析行**回填**（spec §7.3 fallback 基准）。

    背景：football-data 2025-12 起断供 Pinnacle，B 线 CLV 改以 Betfair 交易所
    收盘 fallback；但 ingest_rows 的分区哈希跳过机制对「已入库且 CSV 未变的
    分区」不重建，BFE 列落库后旧分区仍是 NULL——本函数按身份列
    ``(league, season, date, home_team_id, away_team_id)`` UPDATE 补齐。

    硬约束（与 ingest 的 DELETE+INSERT 相反，这里**只 UPDATE**）：
    matches.id 被 backtest_predictions / retro_attributions 外键引用，任何
    重建都会悬挂引用。只填 NULL、绝不覆盖既有值（哪怕 CSV 带了不同的数）；
    身份对不上（队名未注册 / 无该比赛行）→ 跳过该行，**绝不 get_or_create**。
    返回实际回填的行数。
    """
    filled = 0
    for r in rows:
        if not any((r.bfe_home, r.bfe_draw, r.bfe_away, r.over25_bfe)):
            continue                       # 该行 CSV 无任何 BFE 值
        home = conn.execute(
            "SELECT id FROM teams WHERE league=? AND name=?",
            (league, r.home)).fetchone()
        away = conn.execute(
            "SELECT id FROM teams WHERE league=? AND name=?",
            (league, r.away)).fetchone()
        if home is None or away is None:
            continue                       # 回填不建队：身份不明即跳过
        cur = conn.execute(
            "UPDATE matches SET bfe_home=?, bfe_draw=?, bfe_away=?, over25_bfe=?"
            " WHERE league=? AND season=? AND date=? AND home_team_id=?"
            " AND away_team_id=?"
            " AND bfe_home IS NULL AND bfe_draw IS NULL AND bfe_away IS NULL"
            " AND over25_bfe IS NULL",
            (r.bfe_home, r.bfe_draw, r.bfe_away, r.over25_bfe,
             league, season, r.date, home["id"], away["id"]))
        filled += cur.rowcount
    conn.commit()
    return filled
