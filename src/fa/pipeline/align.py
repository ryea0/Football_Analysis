"""实时侧队名对齐（spec §3.3）：Odds API 队名 → football-data canonical 名。

两个数据源的拼写不同（``Bayern München`` vs ``Bayern Munich``）是 B 线对齐的
核心坑点。对齐三段式，逐级收紧、**绝不猜、绝不静默丢弃**：

1. **精确**：canonical 名或既有别名原文命中（M1 `resolve_team`）；
2. **归一化**：`normalize_name` 后完全一致（NFKD 去变音符 + 小写 + 去非字母数字）
   ——``Atlético Madrid`` 与 ``Atletico Madrid`` 由此收敛；同样自动写别名；
3. **模糊**：difflib.SequenceMatcher 对该联赛 canonical 名取最高分，
   ``ratio ≥ 0.87``（`AUTO_ACCEPT_RATIO`）且**无并列**才自动写别名
   （source=``oddsapi``）；否则进隔离表 `unknown_names` 并返回 ``None``，
   由 `fa data aliases --confirm` 人工确认（spec §3.3「人工确认清单」）。

归一化/模糊命中都可能**歧义**（真实语料：D1 的 ``M'Gladbach`` 与 ``M'gladbach``
归一化同形 ``mgladbach`` 但 team_id 不同）——歧义一律隔离交人工，不猜。

写入侧不 commit（与 `fa.data.teams` 同约定），由调用方（CLI / T5 同步）提交。
"""

from __future__ import annotations

import difflib
import re
import sqlite3
import unicodedata
from dataclasses import dataclass

from fa.data.teams import record_unknown, resolve_team

DEFAULT_SOURCE = "oddsapi"
# 模糊自动接受的阈值（brief 钉死）。手工验算参照：``bayernmunchen``(13) vs
# ``bayernmunich``(12) 匹配 11 字符 → 2*11/25 = 0.88 ≥ 0.87 命中；
# ``realsociedad``(12) vs ``realmadrid``(10) 匹配 6 字符 → 2*6/22 ≈ 0.545 < 0.87 隔离。
AUTO_ACCEPT_RATIO = 0.87
_TOP_SUGGESTIONS = 3                        # CLI 建议清单条数

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Candidate:
    """一条候选（canonical 名或既有别名）与 `name` 的相似度。"""

    team_id: int
    league: str
    name: str                               # 原文（canonical 或 alias）
    ratio: float                            # difflib 归一化相似度（0~1，未取整）


def normalize_name(s: str) -> str:
    """小写、去变音符（unicodedata NFKD 后丢弃 combining 字符）、去非字母数字。

    ``Bayern München`` → ``bayernmunchen``；``Schalke 04`` → ``schalke04``；
    空/纯符号串归一化为 ``""``（调用方须把空串视为不可对齐）。
    """
    decomposed = unicodedata.normalize("NFKD", s)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _NON_ALNUM.sub("", stripped.lower())


def suggest_alias(conn: sqlite3.Connection, league: str, name: str,
                  source: str = DEFAULT_SOURCE) -> int | None:
    """把实时侧队名对到队 id；对不上则入隔离表并返回 ``None``（不抛异常）。

    命中路径（精确 / 归一化 / 模糊高分）都会把 `name` 写进 `team_aliases`
    （source 默认 ``oddsapi``），下次 `resolve_team` 直取原文——别名表因此
    成为自动对齐的**留痕**（spec §3.3「别名表是持续维护成本」）。
    归一化命中限该联赛的 canonical 名 + 既有别名；模糊命中限该联赛 canonical 名。
    """
    tid = resolve_team(conn, league, name, source)
    if tid is not None:
        return tid                          # canonical / 既有别名原文命中

    target = normalize_name(name)
    if not target:
        record_unknown(conn, source, name)  # 空串与候选的 ratio 会虚高，不可作依据
        return None

    by_norm: dict[str, set[int]] = {}
    for team_id, _lg, cand in _candidate_rows(conn, source, league):
        by_norm.setdefault(normalize_name(cand), set()).add(team_id)
    hits = by_norm.get(target)
    if hits is not None and len(hits) == 1:
        return _bind_alias(conn, next(iter(hits)), source, name)
    # hits 为空或歧义 → 落到模糊；歧义时模糊同样并列，仍归隔离

    best = _rank(_canonical_rows(conn, league), target)
    if best and best[0].ratio >= AUTO_ACCEPT_RATIO and _unambiguous(best):
        return _bind_alias(conn, best[0].team_id, source, name)

    record_unknown(conn, source, name)      # 绝不静默丢弃（spec §3.3）
    return None


def align_fixture_teams(conn: sqlite3.Connection, league: str, home_name: str,
                        away_name: str,
                        source: str = DEFAULT_SOURCE) -> tuple[int | None, int | None]:
    """对齐一场比赛的主客队名；任一侧对不上则该侧为 ``None``（不抛异常）。"""
    return (suggest_alias(conn, league, home_name, source),
            suggest_alias(conn, league, away_name, source))


def rank_candidates(conn: sqlite3.Connection, name: str, league: str | None = None,
                    source: str = DEFAULT_SOURCE,
                    top: int = _TOP_SUGGESTIONS) -> list[Candidate]:
    """给 `name` 排出相似度最高的 `top` 条候选（供 `fa data aliases` 建议清单）。

    `league=None` 表示跨全部联赛找（`unknown_names` 不带联赛列，人工确认时由
    操作方自行判断）；候选含 canonical 名与该 source 的既有别名。
    排序键 ``(-ratio, name, team_id)``，结果确定。
    """
    target = normalize_name(name)
    if not target:
        return []
    return _rank(_candidate_rows(conn, source, league), target)[:top]


# ---------------------------------------------------------------- 内部实现


_CANON_SQL = (
    "SELECT t.id, t.league, t.name FROM teams t WHERE t.league=?")
_ALIAS_SQL = (
    "SELECT a.team_id, t.league, a.alias FROM team_aliases a "
    "JOIN teams t ON t.id = a.team_id WHERE t.league=? AND a.source=?")
_ALIAS_SQL_UNSCOPED = (
    "SELECT a.team_id, t.league, a.alias FROM team_aliases a "
    "JOIN teams t ON t.id = a.team_id WHERE a.source=?")


def _canonical_rows(conn: sqlite3.Connection, league: str) -> list[tuple[int, str, str]]:
    """该联赛 canonical 名 ``(team_id, league, name)``——模糊路径的候选集（brief 口径）。"""
    return [tuple(r) for r in conn.execute(_CANON_SQL, (league,))]


def _candidate_rows(conn: sqlite3.Connection, source: str,
                    league: str | None) -> list[tuple[int, str, str]]:
    """候选集 ``(team_id, league, name)``：canonical 名 + 该 source 既有别名。

    别名经 team_id 回连 teams 取联赛，保证与 canonical 同一联赛口径；
    `league=None` 表示跨全部联赛（`unknown_names` 不带联赛列）。
    """
    if league is None:
        rows = conn.execute("SELECT t.id, t.league, t.name FROM teams t")
        return [tuple(r) for r in rows] + [
            tuple(r) for r in conn.execute(_ALIAS_SQL_UNSCOPED, (source,))]
    return [tuple(r) for r in conn.execute(_CANON_SQL, (league,))] + [
        tuple(r) for r in conn.execute(_ALIAS_SQL, (league, source))]


def _rank(rows: list[tuple[int, str, str]], target: str) -> list[Candidate]:
    """按归一化相似度降序排候选，排序键 ``(-ratio, name, team_id)``（结果确定）。

    `target` 为空串时返回空（空串与任何候选的 ratio 都可能虚高，不可作依据）。
    """
    if not target:
        return []
    out: list[Candidate] = []
    for team_id, lg, cand in rows:
        cand_norm = normalize_name(cand)
        if not cand_norm:
            continue                        # 归一化为空的候选（纯符号/非拉丁名）不可比
        out.append(Candidate(
            team_id, lg, cand,
            difflib.SequenceMatcher(None, target, cand_norm).ratio()))
    out.sort(key=lambda c: (-c.ratio, c.name, c.team_id))
    return out


def _unambiguous(ranked: list[Candidate]) -> bool:
    """最高分是否唯一：并列最高分的候选若指向不同 team_id 即为歧义。"""
    top = ranked[0].ratio
    return len({c.team_id for c in ranked if c.ratio == top}) == 1


def _bind_alias(conn: sqlite3.Connection, team_id: int, source: str, alias: str) -> int:
    """写别名（幂等：UNIQUE(source, alias) 冲突让位于库内现值）并返回生效 team_id。

    返回值取**库内**绑定——同一 (source, alias) 若已被占用（并发或历史数据），
    以库为准而非本次候选，保证返回的 id 与表内一致。
    """
    conn.execute(
        "INSERT INTO team_aliases (team_id, source, alias) VALUES (?, ?, ?) "
        "ON CONFLICT(source, alias) DO NOTHING", (team_id, source, alias))
    row = conn.execute(
        "SELECT team_id FROM team_aliases WHERE source=? AND alias=?",
        (source, alias)).fetchone()
    return row["team_id"]
