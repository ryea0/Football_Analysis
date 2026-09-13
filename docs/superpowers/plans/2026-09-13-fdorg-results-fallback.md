# fdorg 赛果备用源 fallback 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 主源 football-data.co.uk 赛果滞后/故障期间，逾期 paper 注次日晨自动经 api.football-data.org（免费层）结算；主源恢复后官方行幂等覆盖+比分 diff 告警不改账。

**Architecture:** 结果驱动触发（已完赛但配不到赛果行的 pending 注 → 按 (联赛,日期窗) 精准拉取 fdorg → 时间戳严格相等+别名方向校验配对 → 写 `matches`（`raw_line` 标记 fallback）→ 既有 `settle_paper_bets` 无改动自然结算）。恢复闭环挂在 `sync_history` 当前赛季分支外：重建前快照 fallback 行、重建后 diff、不一致告警不改账。

**Tech Stack:** Python 3.11 + urllib（同 `odds_api.py` 传输语义，无新依赖）、sqlite3、pytest。

**Spec:** `docs/superpowers/specs/2026-09-10-fallback-results-source-design.md`（已批设计 + 2026-09-13 修正裁定）；spec.md v0.12 → v0.13（实施首步）。

## Global Constraints

- **裁定修正（2026-09-13，负责人）**：触发判据为**纯结果驱动**——存在「已完赛但配不到完赛行」的 pending paper 注即触发，**不再要求**当日 SyncReport.file_errors 含当前赛季条目（原判据只覆盖 503 形态；主源恢复后常态滞后 1-3 天，负责人要求每日看到前一日赛果）
- 裁定②（2026-09-10）：比分不一致**告警不改账**；裁定③：主源连续 **N=2** 天当前赛季失败告警
- 配对三判据全满足才落行：`utcDate == fixture.kickoff_utc`（字符串级严格相等）且 fdorg 队名经 `team_aliases(source='fdorg')` 解析出的 team_id 与 fixture 的 home/away **方向一致**，且 `status=='FINISHED'` 比分非空
- 零 schema 变更：`matches` / `team_aliases` / `meta` / `runs.summary` 全部现成
- B 线纪律：`daily` 是 B 线触达 `matches` 的唯一入口；不改 `ingest_rows` 核心（收缩保护分母修正除外，见 Task 3）
- 免费层预算：一次触发最多 5 请求（每联赛 1 个），10 req/min 内，不做限速器
- 与工作区另一会话未提交改动（settled_at 回填/FK 修复，动 `paper.py`/`db.py`/`cli.py`）协调：本计划**不动 `paper.py`/`db.py`**；`cli.py` 只追加新命令不改既有行，合并时如有冲突留给负责人线合
- token 缺失：触发条件满足但无 `FOOTBALL_DATA_ORG_KEY` → `{"triggered": false, "skipped": "no_token"}`，不告警不中断

---

### Task 1: spec v0.13 + 设计文档裁定修正

**Files:**
- Modify: `spec.md`（版本行、变更日志、§3.1 数据源表、§3.4）
- Modify: `docs/superpowers/specs/2026-09-10-fallback-results-source-design.md`（决策记录追加修正裁定；状态改已实施）

**Interfaces:** Produces: spec 依据（后续任务的论证源头）

- [x] **Step 1: spec.md 版本行与变更日志**

`| 版本 | v0.12（确认稿） |` → `| 版本 | v0.13（确认稿） |`；变更日志区追加（跟在 v0.12 行后）：

```
> v0.12 → v0.13 变更：赛果备用源 fallback（§3.1/§3.4）——api.football-data.org 免费层为备用赛果源（结果驱动触发：已完赛配不到赛果行的 pending paper 注、按联赛日期窗精准拉取；时间戳严格相等+fdorg 别名方向校验配对；raw_line 记 source，主源恢复后分区重建幂等覆盖+比分 diff 告警不改账；主源当前赛季连续 2 天失败 TG 告警；收缩保护基线排除 fallback 行）（2026-09-13 修正裁定：触发去「主源失败」合取、纯结果驱动——负责人要求每日看到前一日赛果；设计 docs/superpowers/specs/2026-09-10-fallback-results-source-design.md）
```

- [x] **Step 2: §3.1 数据源表加一行**

数据源表（§3.1）按既有行格式追加：

```
| api.football-data.org | 备用赛果源（fallback，v0.13） | 免费层、X-Auth-Token、10 req/min；无收盘价（CLV 仍走 §7.3 基准链）；结果驱动触发；raw_line 记 source、恢复后官方行幂等覆盖+比分 diff 告警不改账 |
```

- [x] **Step 3: §3.4 追加 fallback 段**

在 §3.4 数据层小节末尾（watchdog 巡检条目后）追加要点列表：触发判据（修正版）、配对三判据、记账约定、恢复闭环、N=2 告警、收缩保护基线排除——每条一句话，正文引用设计文档路径。

- [x] **Step 4: 设计文档决策记录追加**

`2026-09-10-fallback-results-source-design.md` 决策记录行后追加：

```
- 修正裁定（2026-09-13，负责人）：触发判据去「主源当前赛季失败」合取项——纯结果驱动。
  背景：主源自 503 恢复后当季 CSV 内容常态滞后 1-3 天（09-11/09-12 轮至 09-13 仍未上文件），
  原判据只覆盖整站故障形态，无法满足「每日看到前一日赛果」；§3 触发条件相应修改，
  实施同时发现并修正收缩保护与 fallback 行的计数交互（基线排除 fallback/stopgap 行）。
```

状态行 `待出实施计划` → `已实施（2026-09-13，plan: docs/superpowers/plans/2026-09-13-fdorg-results-fallback.md）`。

- [x] **Step 5: Commit**

```bash
git add spec.md docs/superpowers/specs/2026-09-10-fallback-results-source-design.md
git commit -m "docs(spec): v0.13 赛果备用源 fallback——纯结果驱动修正裁定+设计文档更新"
```

---

### Task 2: fdorg 客户端（config + fdorg.py 传输层）

**Files:**
- Modify: `src/fa/config.py`
- Create: `src/fa/data/fdorg.py`
- Test: `tests/data/test_fdorg.py`

**Interfaces:**
- Produces: `fdorg_token() -> str | None`；`FdorgError(Exception)`（属性 `reason`）；`FdorgResult`（frozen dataclass：`utc_date,home_name,away_name,fthg,ftag`）；`fetch_results(league: str, date_from: str, date_to: str) -> list[FdorgResult]`；`fetch_teams(league: str) -> list[str]`；`LEAGUE_CODES: dict[str,str]`；`FDORG_TIMEOUT = 20`

- [x] **Step 1: 写失败测试（mock 服务器）**

```python
# tests/data/test_fdorg.py
"""fdorg 客户端：传输/鉴权/过滤/解析。FDORG_BASE_URL 指向本地 fixture（mock 模式，
沿用 HERMES_BIN 先例）。fixture JSON = 2026-09-10 stopgap 6 场真实响应形状。"""
import json
from pathlib import Path

import pytest

from fa.data.fdorg import (FdorgError, FdorgResult, LEAGUE_CODES,
                           fetch_results)


def _serve(tmp_path, payload, status=200):
    f = tmp_path / "fdorg.json"
    f.write_text(json.dumps(payload), encoding="utf-8")
    return f.as_uri()


def test_league_codes_cover_five():
    assert LEAGUE_CODES == {"E0": "PL", "SP1": "PD", "D1": "BL1",
                            "I1": "SA", "F1": "FL1"}


def test_fetch_results_filters_finished(monkeypatch, tmp_path):
    payload = {"matches": [
        {"utcDate": "2026-09-12T14:00:00Z", "status": "FINISHED",
         "score": {"fullTime": {"homeTeam": 2, "awayTeam": 1}},
         "homeTeam": {"name": "Aston Villa FC"},
         "awayTeam": {"name": "Nottingham Forest FC"}},
        {"utcDate": "2026-09-12T16:30:00Z", "status": "IN_PLAY",
         "score": {"fullTime": {"homeTeam": None, "awayTeam": None}},
         "homeTeam": {"name": "X"}, "awayTeam": {"name": "Y"}},
        {"utcDate": "2026-09-12T19:00:00Z", "status": "FINISHED",
         "score": {"fullTime": {"homeTeam": 0, "awayTeam": 0}},
         "homeTeam": {"name": "A"}, "awayTeam": {"name": "B"}},
    ]}
    monkeypatch.setenv("FDORG_BASE_URL", _serve(tmp_path, payload))
    monkeypatch.setenv("FOOTBALL_DATA_ORG_KEY", "k")
    out = fetch_results("E0", "2026-09-12", "2026-09-12")
    assert out == [FdorgResult("2026-09-12T14:00:00Z",
                               "Aston Villa FC", "Nottingham Forest FC", 2, 1),
                   FdorgResult("2026-09-12T19:00:00Z", "A", "B", 0, 0)]


def test_fetch_results_http_error(monkeypatch, tmp_path):
    monkeypatch.setenv("FDORG_BASE_URL", _serve(tmp_path, {"error": "x"}))
    monkeypatch.setenv("FOOTBALL_DATA_ORG_KEY", "bad")
    with pytest.raises(FdorgError) as ei:
        fetch_results("E0", "2026-09-12", "2026-09-12")
    assert ei.value.reason == "http"


def test_fetch_results_missing_token(monkeypatch, tmp_path):
    monkeypatch.delenv("FOOTBALL_DATA_ORG_KEY", raising=False)
    monkeypatch.setenv("FDORG_BASE_URL", _serve(tmp_path, {}))
    with pytest.raises(FdorgError) as ei:
        fetch_results("E0", "2026-09-12", "2026-09-12")
    assert ei.value.reason == "no_token"
```

- [x] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/test_fdorg.py -q` → FAIL（ModuleNotFoundError: fa.data.fdorg）

- [x] **Step 3: 实现**

`src/fa/config.py` 追加（沿用 `odds_api_key` 同款读法）：

```python
FDORG_BASE_URL = "https://api.football-data.org"


def fdorg_token() -> str | None:
    """api.football-data.org 免费层 token（spec v0.13 备用赛果源；None=未配置）。"""
    return os.environ.get("FOOTBALL_DATA_ORG_KEY")
```

`src/fa/data/fdorg.py`：

```python
"""备用赛果源 api.football-data.org 客户端与 fallback 编排（spec v0.13 §3.4；
设计 docs/superpowers/specs/2026-09-10-fallback-results-source-design.md）。

免费层：X-Auth-Token、10 req/min。传输与 fa.pipeline.odds_api 同语义
（urllib、随环境代理、超时上抛）。配对三判据与落行约定见模块下半部分
:func:`results_fallback`。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from fa.config import fdorg_token

LEAGUE_CODES = {"E0": "PL", "SP1": "PD", "D1": "BL1", "I1": "SA", "F1": "FL1"}
FDORG_TIMEOUT = 20


class FdorgError(Exception):
    """fdorg 调用失败。``reason``：no_token / http / network / parse。"""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


@dataclass(frozen=True)
class FdorgResult:
    utc_date: str          # "2026-09-12T14:00:00Z"（与 fixtures.kickoff_utc 同格式）
    home_name: str
    away_name: str
    fthg: int
    ftag: int


def _get(path: str, params: dict[str, str]) -> Any:
    token = fdorg_token()
    if not token:
        raise FdorgError("no_token")
    base = os.environ.get("FDORG_BASE_URL", "https://api.football-data.org")
    url = f"{base}{path}?{urlencode(params)}"
    req = urllib.request.Request(url, headers={"X-Auth-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=FDORG_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:            # HTTPError 先于 URLError 接
        raise FdorgError("http", str(e.code)) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise FdorgError("network", str(e)) from e
    except ValueError as e:                        # json 解析失败
        raise FdorgError("parse", str(e)) from e


def fetch_results(league: str, date_from: str, date_to: str) -> list[FdorgResult]:
    """某联赛 [date_from, date_to]（含端点，UTC 日历日）已完赛结果。

    只留 ``status=="FINISHED"`` 且 fullTime 双侧比分非 None 的行（三判据之③
    在源侧预过滤；配对的①②由 :func:`results_fallback` 做）。未知联赛 → FdorgError。
    """
    code = LEAGUE_CODES.get(league)
    if code is None:
        raise FdorgError("parse", f"未知联赛 {league}")
    data = _get(f"/v4/competitions/{code}/matches",
                {"dateFrom": date_from, "dateTo": date_to})
    out: list[FdorgResult] = []
    for m in data.get("matches", []):
        ft = (m.get("score") or {}).get("fullTime") or {}
        if m.get("status") != "FINISHED":
            continue
        if ft.get("homeTeam") is None or ft.get("awayTeam") is None:
            continue
        out.append(FdorgResult(
            utc_date=str(m["utcDate"]),
            home_name=str((m.get("homeTeam") or {}).get("name", "")),
            away_name=str((m.get("awayTeam") or {}).get("name", "")),
            fthg=int(ft["homeTeam"]), ftag=int(ft["awayTeam"])))
    return out


def fetch_teams(league: str) -> list[str]:
    """某联赛当前赛季队名表（别名提案用；免费层 1 请求）。"""
    code = LEAGUE_CODES.get(league)
    if code is None:
        raise FdorgError("parse", f"未知联赛 {league}")
    data = _get(f"/v4/competitions/{code}/teams", {})
    return sorted(t["name"] for t in data.get("teams", []) if t.get("name"))
```

- [x] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/data/test_fdorg.py -q` → PASS

- [x] **Step 5: Commit**

```bash
git add src/fa/config.py src/fa/data/fdorg.py tests/data/test_fdorg.py
git commit -m "feat(fdorg): 备用赛果源客户端——免费层 X-Auth-Token、FINISHED 过滤、mock 可测"
```

---

### Task 3: 收缩保护基线排除 fallback 行（ingest.py）

**Files:**
- Modify: `src/fa/data/ingest.py:25-31`（仅收缩保护分母）
- Test: `tests/data/test_ingest.py`（追加用例）

**Interfaces:**
- Produces: `ingest_rows` 语义修正——收缩保护比较 `len(rows)` 与「官方行数 = 现存 − fallback/stopgap 行」；行为对官方行数不缩的场景完全不变

- [x] **Step 1: 写失败测试**

```python
FALLBACK_RAW = json.dumps({"source": "api.football-data.org",
                           "fallback": True}, ensure_ascii=False)


def test_shrink_guard_excludes_fallback_rows(conn):
    # 分区里 2 官方行 + 1 fallback 行；新官方内容 3 行（> 官方基线 2）→ 允许重建
    _insert_match(conn, "E0", 2026, "2026-09-11", "A", "B")   # 既有测试助手
    _insert_match(conn, "E0", 2026, "2026-09-12", "C", "D")
    _insert_match(conn, "E0", 2026, "2026-09-12", "E", "F",
                  raw_line=FALLBACK_RAW)
    rows = [_row("E0", 2026, "2026-09-11", "A", "B", 1, 0),
            _row("E0", 2026, "2026-09-12", "C", "D", 2, 2),
            _row("E0", 2026, "2026-09-12", "E", "F", 0, 1)]
    assert ingest_rows(conn, "E0", 2026, rows) == 3   # 不抛收缩保护


def test_shrink_guard_still_rejects_official_shrink(conn):
    _insert_match(conn, "E0", 2026, "2026-09-11", "A", "B")
    _insert_match(conn, "E0", 2026, "2026-09-12", "C", "D")
    _insert_match(conn, "E0", 2026, "2026-09-12", "E", "F",
                  raw_line=FALLBACK_RAW)
    with pytest.raises(ValueError, match="收缩保护"):
        ingest_rows(conn, "E0", 2026,
                    [_row("E0", 2026, "2026-09-11", "A", "B", 1, 0)])
```

（`_insert_match`/`_row` 沿用该测试文件既有构造助手；无则按 matches 列最小集实现。）

- [x] **Step 2: 跑测试确认失败**（第一个用例 FAIL：收缩保护误拒）

- [x] **Step 3: 实现**

`ingest_rows` 收缩保护段改为：

```python
    existing = conn.execute(
        "SELECT COUNT(*) c FROM matches WHERE league=? AND season=?",
        (league, season)).fetchone()["c"]
    # fallback 行（含 09-10 stopgap 历史键）不计入收缩基线（spec v0.13）：
    # 主源恢复后官方重建不得被自己拉来的备用行顶住；对无 fallback 行的分区
    # 行为与 v0.12 完全一致
    fallback = conn.execute(
        "SELECT COUNT(*) c FROM matches WHERE league=? AND season=?"
        " AND (raw_line LIKE '%\"fallback\":true%'"
        "      OR raw_line LIKE '%\"stopgap\":true%')",
        (league, season)).fetchone()["c"]
    if len(rows) < existing - fallback:   # 收缩保护（spec v0.12 §9.5）：
        raise ValueError(                # 上游/缓存回退时宁可不动分区也不删赛果
            f"收缩保护：{league}/{season} 分区库内官方 {existing - fallback} 行"
            f"（另 fallback {fallback} 行）> 新内容 {len(rows)} 行，"
            f"拒绝重建（疑似上游/缓存回退），分区保持原样")
```

- [x] **Step 4: 跑既有 ingest 测试确认无回归**

Run: `uv run pytest tests/data/test_ingest.py -q` → 全 PASS

- [x] **Step 5: Commit**

```bash
git add src/fa/data/ingest.py tests/data/test_ingest.py
git commit -m "fix(ingest): 收缩保护基线排除 fallback/stopgap 行——防主源恢复被备用行顶住（spec v0.13）"
```

---

### Task 4: 恢复闭环包装 + fail_streak（sync.py）

**Files:**
- Modify: `src/fa/data/sync.py`
- Test: `tests/data/test_sync.py`（追加）

**Interfaces:**
- Produces: `SyncReport` 新字段 `fallback_diffs: list[tuple[str,int,str,str,str]]`（league, season, key, fallback 比分, 官方比分）与 `fail_streak: int`；`FAI L_STREAK_META_KEY = "current_season_fail_streak"`；模块内 `_fallback_snapshot(conn, league, season) -> dict[str, tuple[int,int]]`、`_diff_official(conn, league, season, snapshot) -> list[...]`
- Consumes: Task 2/3 无依赖；snapshot 键 = `f"{date}|{home_id}|{away_id}"`

- [x] **Step 1: 写失败测试**

```python
def test_recovery_diff_equal_silent(conn):
    # fallback 行 2-1，官方覆盖行同比分 → fallback_diffs 空
    snap = {f"{d}|{h}|{a}": (2, 1)}       # 构造见文件内助手
    ...
    assert _diff_official(conn, "E0", 2026, snap) == []


def test_recovery_diff_mismatch_reported(conn):
    # fallback 行 2-1，官方行 1-2 → diff 记录两版比分
    ...
    diffs = _diff_official(conn, "E0", 2026, snap)
    assert diffs and "2-1" in diffs[0][3] and "1-2" in diffs[0][4]


def test_fail_streak_counts_and_resets(conn):
    rep = SyncReport(); rep.file_errors = [("E0", 2026, "x")]
    assert _update_fail_streak(conn, rep.file_errors) == 1
    assert _update_fail_streak(conn, [("E0", 2026, "x")]) == 2   # 连续第 2 天
    assert _update_fail_streak(conn, []) == 0                    # 成功归零
```

（判定「当前赛季条目」用 `sync._current_season_start()` 同锚：`year == _current_season_start()`。）

- [x] **Step 2: 跑测试确认失败**

- [x] **Step 3: 实现**

`SyncReport` 加两字段；`sync_history` 当前赛季分支（`year == to_year`）在 `ingest_rows` 调用前后加包装：

```python
                # v0.13 恢复闭环：重建前快照 fallback 行，重建后与官方行 diff
                snap = _fallback_snapshot(conn, league, year)
                n = ingest_rows(conn, league, year, dated)
                if snap:
                    rep.fallback_diffs.extend(
                        _diff_official(conn, league, year, snap))
```

同步尾部：

```python
    rep.fail_streak = _update_fail_streak(conn, rep.file_errors)
    return rep
```

新函数（含 meta 读写）：

```python
FAIL_STREAK_META_KEY = "current_season_fail_streak"


def _fallback_marker_sql() -> str:
    return ("raw_line LIKE '%\"fallback\":true%'"
            " OR raw_line LIKE '%\"stopgap\":true%'")


def _fallback_snapshot(conn, league: str, season: int) -> dict[str, tuple[int, int]]:
    """该分区 fallback 行快照：``{date|home|away: (fthg, ftag)}``（重建前抓）。"""
    out: dict[str, tuple[int, int]] = {}
    for r in conn.execute(
            "SELECT date, home_team_id, away_team_id, fthg, ftag FROM matches"
            f" WHERE league=? AND season=? AND ({_fallback_marker_sql()})",
            (league, season)):
        out[f"{r['date']}|{r['home_team_id']}|{r['away_team_id']}"] = (
            r["fthg"], r["ftag"])
    return out


def _diff_official(conn, league: str, season: int,
                   snapshot: dict[str, tuple[int, int]]):
    """同 key 官方行比分 diff；一致 → []（幂等覆盖静默），不一致 → 记录不改账。"""
    diffs = []
    for key, (fh, fa) in snapshot.items():
        d, h, a = key.split("|")
        row = conn.execute(
            "SELECT fthg, ftag, raw_line FROM matches WHERE league=? AND season=?"
            " AND date=? AND home_team_id=? AND away_team_id=?",
            (league, season, d, int(h), int(a))).fetchone()
        if row is None:
            continue            # 官方内容尚未覆盖该场：下轮 sync 再比
        oh, oa = row["fthg"], row["ftag"]
        if row["raw_line"] and "fallback" not in row["raw_line"] and "stopgap" not in row["raw_line"] \
                and (oh, oa) != (fh, fa):
            diffs.append((league, season, f"{d} #{h}v#{a}",
                          f"{fh}-{fa}", f"{oh}-{oa}"))
    return diffs


def _update_fail_streak(conn, file_errors) -> int:
    """当前赛季失败连续天数（N=2 告警判据，spec v0.13）；成功日归零。返回当前值。"""
    from fa.db import get_meta, set_meta
    cur = to_year = _current_season_start()
    cur_season_failed = any(y == cur for (_l, y, _m) in file_errors)
    prev = get_meta(conn, FAIL_STREAK_META_KEY)
    streak = (int(prev) + 1) if cur_season_failed else 0
    set_meta(conn, FAIL_STREAK_META_KEY, str(streak))
    return streak
```

（`_update_fail_streak` 里 `cur = to_year = ...` 为笔误示例——实现时只留一行 `year_now = _current_season_start()`；告警**不在数据层发**，由 daily/CLI 看 `rep.fail_streak >= 2` 决定。）

- [x] **Step 4: 跑测试确认通过 + 既有 sync 测试无回归**

Run: `uv run pytest tests/data/test_sync.py -q`

- [x] **Step 5: Commit**

```bash
git add src/fa/data/sync.py tests/data/test_sync.py
git commit -m "feat(sync): 恢复闭环——fallback 行快照/官方 diff/fail_streak 计数（spec v0.13）"
```

---

### Task 5: results_fallback 核心（逾期集+配对+落行+dry-run）

**Files:**
- Modify: `src/fa/data/fdorg.py`（下半部分）
- Test: `tests/data/test_fdorg.py`（追加）

**Interfaces:**
- Consumes: `fa.pipeline.paper._paired_match(conn, league, home_team_id, away_team_id, kickoff_date)`、`fa.pipeline.paper.MAX_MATCH_DAY_GAP`（只读复用，不改 paper.py）；Task 2 客户端
- Produces: `results_fallback(conn: sqlite3.Connection, *, dry_run: bool = False, now: datetime | None = None) -> dict`，形状恒含 `{"triggered": bool, "fixtures": int, "filled": int, "unmatched": [{"fixture_id","league","kickoff","reason"}], "error": [str]}`；未触发 `{"triggered": false, ...零值}`；token 缺失 `{"triggered": false, "skipped": "no_token", ...}`；`FINISHED_HOURS = 4`；`_normalize_name(name) -> str`（小写、去变音符、去非字母数字）

- [x] **Step 1: 写失败测试（金案例：stopgap 6 场形态）**

```python
def test_results_fallback_pairs_and_fills(conn, monkeypatch, tmp_path):
    # fixture：E0 2026-09-12T14:00:00Z Liverpool x Fulham，队已对齐；
    # team_aliases(source='fdorg') 有 "Liverpool FC"→liv_id / "Fulham FC"→ful_id；
    # pending paper 注挂该 fixture；mock fetch_results 返回 FINISHED 2-1
    monkeypatch.setattr("fa.data.fdorg.fetch_results",
                        lambda lg, df, dt: [
                            FdorgResult("2026-09-12T14:00:00Z",
                                        "Liverpool FC", "Fulham FC", 2, 1)])
    out = results_fallback(conn, now=datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc))
    assert out["triggered"] and out["filled"] == 1
    row = conn.execute(
        "SELECT fthg, ftag, raw_line FROM matches"
        " JOIN teams ... WHERE date='2026-09-12'").fetchone()
    assert (row["fthg"], row["ftag"]) == (2, 1)
    assert json.loads(row["raw_line"])["source"] == "api.football-data.org"
    assert json.loads(row["raw_line"])["fallback"] is True


def test_results_fallback_unmatched_reasons(conn, monkeypatch):
    # 同刻两场但别名缺一场 → alias_missing；kickoff 差 1 分钟 → kickoff_mismatch；
    # 未完赛被源过滤后配不上 → not_found
    ...断言 unmatched[].reason 取值...


def test_results_fallback_dry_run_no_write(conn, monkeypatch):
    out = results_fallback(conn, dry_run=True, now=...)
    assert out["filled"] == 1 and out["would_fill"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM matches").fetchone()["c"] == 0


def test_results_fallback_not_yet_finished_skipped(conn, monkeypatch):
    # kickoff + 3h（< FINISHED_HOURS=4）→ 不进逾期集、不触发
    ...断言 out == {"triggered": False, "fixtures": 0, "filled": 0, "unmatched": [], "error": []}


def test_results_fallback_no_token(conn, monkeypatch):
    monkeypatch.delenv("FOOTBALL_DATA_ORG_KEY", raising=False)
    # 逾期集非空 + token 缺 → skipped=no_token
    ...断言 out["skipped"] == "no_token" and not out["triggered"]


def test_results_fallback_single_league_error_tolerated(conn, monkeypatch):
    # fetch_results 对 SP1 抛 FdorgError、E0 正常 → error 记 1 条、E0 照落
    ...
```

- [x] **Step 2: 跑测试确认失败**

- [x] **Step 3: 实现（fdorg.py 追加）**

```python
FINISHED_HOURS = 4   # kickoff+4h 视为必已完赛（90min+中场+补时+缓冲）


def _normalize_name(name: str) -> str:
    """fdorg/teams 名归一：小写、NFKD 去变音符、去非字母数字。别名自动提案用。"""
    import unicodedata
    txt = unicodedata.normalize("NFKD", name)
    return "".join(c for c in txt if c.isalnum()).lower()


def _overdue_fixtures(conn, now):
    """已完赛但配不到完赛行的 pending 注对应 fixture（去重，含 team_id）。

    修正裁定（2026-09-13）：纯结果驱动——不要求主源当日失败。
    配对语义与结算判据同锚（paper._paired_match 同一套窗）。
    """
    from fa.pipeline.paper import _paired_match
    from fa.pipeline.value import _parse_kickoff
    rows = conn.execute(
        "SELECT DISTINCT f.id, f.league, f.kickoff_utc, f.home_team_id,"
        " f.away_team_id FROM bets b"
        " JOIN recommendations r ON r.id=b.recommendation_id"
        " JOIN fixtures f ON f.id=r.fixture_id"
        " WHERE b.mode='paper' AND b.status='pending'").fetchall()
    out = []
    for f in rows:
        kickoff = _parse_kickoff(f["kickoff_utc"])
        if kickoff is None or kickoff + timedelta(hours=FINISHED_HOURS) > now:
            continue
        if _paired_match(conn, f["league"], f["home_team_id"],
                         f["away_team_id"], kickoff.date()) is not None:
            continue
        out.append({"id": f["id"], "league": f["league"],
                    "kickoff_utc": f["kickoff_utc"], "kickoff": kickoff,
                    "home_team_id": f["home_team_id"],
                    "away_team_id": f["away_team_id"]})
    return out


def _alias_map(conn) -> dict[str, int]:
    return {r["alias"]: r["team_id"] for r in conn.execute(
        "SELECT alias, team_id FROM team_aliases WHERE source='fdorg'")}


def _season_of(kickoff: datetime) -> int:
    return kickoff.year if kickoff.month >= 7 else kickoff.year - 1


def results_fallback(conn, *, dry_run: bool = False,
                     now: datetime | None = None) -> dict:
    """备用源结算编排：逾期集 → 每联赛 1 次精准拉取 → 三判据配对 → 落行。

    落行与 T5 同约定（league/season/date=kickoff UTC 日/对齐 team_id/fthg/ftag，
    raw_line 记 source+fallback+fixture_id），同 UNIQUE key → 主源恢复后
    分区重建幂等覆盖。单联赛失败只记 error 不中断（T5 单文件容错同构）。
    dry_run：算与拉照常、不落库（审计/应急）。
    """
    now = now or datetime.now(timezone.utc)
    res = {"triggered": False, "fixtures": 0, "filled": 0,
           "unmatched": [], "error": []}
    overdue = _overdue_fixtures(conn, now)
    res["fixtures"] = len(overdue)
    if not overdue:
        return res
    if not fdorg_token():
        res["skipped"] = "no_token"
        return res
    res["triggered"] = True
    aliases = _alias_map(conn)
    by_league: dict[str, list[dict]] = {}
    for f in overdue:
        by_league.setdefault(f["league"], []).append(f)
    fetched: dict[str, list[FdorgResult]] = {}
    for league, fixtures in sorted(by_league.items()):
        days = sorted(f["kickoff"].date().isoformat() for f in fixtures)
        try:
            fetched[league] = fetch_results(league, days[0], days[-1])
        except FdorgError as exc:
            res["error"].append(f"{league}: {exc}")
    for f in sorted(overdue, key=lambda x: x["id"]):
        cand = fetched.get(f["league"], [])
        hit, reason = _pair_one(f, cand, aliases)
        if hit is None:
            res["unmatched"].append({"fixture_id": f["id"],
                                     "league": f["league"],
                                     "kickoff": f["kickoff_utc"],
                                     "reason": reason})
            continue
        if not dry_run:
            _insert_fallback_row(conn, f, hit)
        res["filled"] += 1
    if dry_run:
        res["would_fill"] = res["filled"]
    return res


def _pair_one(fixture, cand, aliases):
    """三判据配对：utcDate 严格相等 + 别名方向一致 +（源侧已过滤 FINISHED）。
    返回 (FdorgResult|None, reason)。"""
    for m in cand:
        if m.utc_date != fixture["kickoff_utc"]:
            continue
        h = aliases.get(m.home_name)
        a = aliases.get(m.away_name)
        if h is None or a is None:
            if h is None and a is None:
                continue
            return None, f"alias_missing:{m.home_name if h is None else m.away_name}"
        if h == fixture["home_team_id"] and a == fixture["away_team_id"]:
            return m, ""
    return None, "not_found"


def _insert_fallback_row(conn, fixture, m: FdorgResult) -> None:
    raw = {"source": "api.football-data.org", "fallback": True,
           "fetched_at": datetime.now(timezone.utc).strftime(
               "%Y-%m-%dT%H:%M:%SZ"),
           "fixture_id": fixture["id"],
           "match": f"{m.home_name} {m.fthg}-{m.ftag} {m.away_name}"}
    conn.execute(
        "INSERT OR IGNORE INTO matches (league, season, date, home_team_id,"
        " away_team_id, fthg, ftag, raw_line) VALUES (?,?,?,?,?,?,?,?)",
        (fixture["league"], _season_of(fixture["kickoff"]),
         fixture["kickoff"].date().isoformat(), fixture["home_team_id"],
         fixture["away_team_id"], m.fthg, m.ftag,
         json.dumps(raw, ensure_ascii=False)))
    # INSERT OR IGNORE：主源官方行已在（恢复后期）则不覆盖——官方为尊
```

（`timedelta`/`datetime`/`timezone` 随 import 区补齐；`_pair_one` 对「同刻多场」天然由判据②方向校验消歧。）

- [x] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/data/test_fdorg.py -q`

- [x] **Step 5: Commit**

```bash
git add src/fa/data/fdorg.py tests/data/test_fdorg.py
git commit -m "feat(fdorg): results_fallback——逾期集/三判据配对/matches 落行/dry-run（spec v0.13）"
```

---

### Task 6: fdorg 别名提案与批量确认（cli.py + fdorg.py）

**Files:**
- Modify: `src/fa/cli.py`（追加命令；不动既有行）
- Modify: `src/fa/data/fdorg.py`（`propose_aliases(conn) -> list[dict]`）
- Test: `tests/data/test_fdorg.py`（追加 propose 测试）；CLI 冒烟走手动验证

**Interfaces:**
- Produces: `propose_aliases(conn) -> list[{"league","fdorg_name","team_id","team_name","auto"}]`（auto=归一化同名）；CLI `fa data aliases-fdorg [--write-auto]`（打印全部提案；`--write-auto` 只写 auto 条目进 team_aliases(source='fdorg')）；人工确认沿用既有 `fa data aliases --source fdorg --confirm TEAM_ID=别名`

- [x] **Step 1: 写失败测试**

```python
def test_propose_aliases_auto_on_normalized_equal(conn, monkeypatch):
    # teams 有 "Liverpool"；fdorg 返回 "Liverpool FC" → 归一化后不等（fc 残留）→ 非 auto？
    # ——归一化保留全词，故 "Liverpool FC"≠"Liverpool"：auto=False，进人工清单
    # teams 有 "Chelsea"；fdorg "Chelsea" → auto=True
    monkeypatch.setattr("fa.data.fdorg.fetch_teams",
                        lambda lg: ["Chelsea", "Liverpool FC"])
    out = propose_aliases(conn)
    assert {p["fdorg_name"]: p["auto"] for p in out} == {"Chelsea": True,
                                                         "Liverpool FC": False}
```

- [x] **Step 2: 确认失败 → 实现 propose_aliases + CLI**

```python
def propose_aliases(conn) -> list[dict]:
    """fdorg 当前赛季队名 × teams 全表的别名提案（auto=归一化严格同名）。

    编辑距离候选不做——五大联赛队名差异大（缩写/官方全称），机械近似易错，
    人工对照成本可接受（M3 oddsapi 44 条先例）。
    """
    teams = {(_normalize_name(r["name"])): r for r in
             conn.execute("SELECT id, name FROM teams")}
    out = []
    for league in sorted(LEAGUE_CODES):
        try:
            names = fetch_teams(league)
        except FdorgError:
            continue
        for n in names:
            t = teams.get(_normalize_name(n))
            out.append({"league": league, "fdorg_name": n,
                        "team_id": t["id"] if t else None,
                        "team_name": t["name"] if t else None,
                        "auto": t is not None})
    return out
```

CLI（cli.py 追加，风格随既有 data 命令）：

```python
@data_app.command("aliases-fdorg")
def aliases_fdorg_cmd(write_auto: bool = typer.Option(False, "--write-auto")):
    """fdorg 别名提案（spec v0.13 配对判据②）；--write-auto 只写归一化同名项，
    其余打印 TEAM_ID=别名 清单供人工 fa data aliases --source fdorg --confirm。"""
    from fa.data.fdorg import propose_aliases
    conn = connect()
    try:
        rows = propose_aliases(conn)
        n_auto = 0
        for p in rows:
            if p["auto"]:
                n_auto += 1
                if write_auto:
                    conn.execute(
                        "INSERT OR IGNORE INTO team_aliases (team_id, source, alias)"
                        " VALUES (?, 'fdorg', ?)", (p["team_id"], p["fdorg_name"]))
                typer.echo(f"[auto] {p['team_id']}={p['fdorg_name']}"
                           f"  # {p['league']} ← {p['team_name']}")
            else:
                typer.echo(f"[  ?  ] team_id?={p['fdorg_name']}"
                           f"  # {p['league']} 无归一化同名，需人工")
        if write_auto:
            conn.commit()
        typer.echo(f"共 {len(rows)} 条：auto {n_auto}，人工 {len(rows) - n_auto}")
    finally:
        conn.close()
```

- [x] **Step 3: 跑测试 + Commit**

```bash
uv run pytest tests/data/test_fdorg.py -q
git add src/fa/cli.py src/fa/data/fdorg.py tests/data/test_fdorg.py
git commit -m "feat(fdorg): 别名提案/批量确认——归一化同名 auto，其余人工清单（M3 流程复用）"
```

---

### Task 7: daily 接线 + CLV 回填尾步 + 告警（daily.py）

**Files:**
- Modify: `src/fa/pipeline/daily.py`
- Test: `tests/pipeline/test_daily.py`（追加）

**Interfaces:**
- Consumes: Task 5 `results_fallback`、Task 4 `SyncReport.fallback_diffs/fail_streak`、既有 `fa.pipeline.paper.backfill_clv`
- Produces: `runs.summary` 增键 `fallback`（results_fallback 返回原样）与 `fallback_alert`（告警文本或 None）；告警三条：unmatched 非空、fail_streak>=2、fallback_diffs 非空——均 `send()` 尽力而为、失败只记 summary 不改状态

- [x] **Step 1: 写失败测试**

```python
def test_daily_runs_fallback_before_settle(conn, monkeypatch):
    calls = []
    monkeypatch.setattr("fa.pipeline.daily.results_fallback",
                        lambda c, **kw: calls.append("fb") or FB_SUMMARY)
    monkeypatch.setattr("fa.pipeline.daily.settle_paper_bets",
                        lambda c: calls.append("settle") or SETTLE)
    run_daily(conn)
    assert calls == ["fb", "settle"]          # 顺序钉死：fallback 先于结算


def test_daily_fallback_exception_degrades(conn, monkeypatch):
    monkeypatch.setattr("fa.pipeline.daily.results_fallback",
                        lambda c, **kw: 1 / 0)
    out = run_daily(conn)                     # 不中断：结算照常、summary 记 error
    assert out["settled"] == 0 and out["fallback"]["triggered"] is False


def test_daily_alerts_on_unmatched_and_streak(conn, monkeypatch):
    # FB_SUMMARY 带 unmatched 1 条；sync mock 返回 fail_streak=2 的 report
    # → send 被调 2 次（unmatched 告警 + 主源失败告警）
    ...


def test_daily_tail_backfill_clv(conn, monkeypatch):
    monkeypatch.setattr("fa.pipeline.daily.backfill_clv", ...)
    run_daily(conn)
    assert backfill_clv.called_once
```

- [x] **Step 2: 确认失败 → 实现**

`_run` 中 `settle_paper_bets` 之前插入：

```python
    # v0.13 备用源结算（修正裁定：纯结果驱动）：sync 之后、settle 之前——
    # fallback 落的行当日即被结算。整体异常只降级不中断（结算不依赖它成功）
    fallback = None
    try:
        fallback = results_fallback(conn)
    except Exception as exc:
        fallback = {"triggered": False,
                    "error": [f"{type(exc).__name__}: {exc}"]}
```

`settle_paper_bets` 之后、summary 组装前：

```python
    # 恢复闭环告警（裁定②：告警不改账）+ 主源连续失败告警（裁定③：N=2）
    alerts = []
    if fallback.get("unmatched"):
        alerts.append("fallback 配对失败（spec v0.13，人工排查）：\n"
                      + "\n".join(f"  {u['league']} #{u['fixture_id']}"
                                  f" {u['kickoff']} — {u['reason']}"
                                  for u in fallback["unmatched"]))
    if rep is not None and rep.fail_streak >= 2:
        alerts.append(f"主源当前赛季连续 {rep.fail_streak} 天失败"
                      f"（N=2 告警，spec v0.13）——已靠备用源结算，恢复后自动覆盖")
    if rep is not None and rep.fallback_diffs:
        alerts.append("⚠️ 备用源与官方比分不一致（不改账，待人工裁定）：\n"
                      + "\n".join(f"  {d[0]} {d[2]}: fallback {d[3]}"
                                  f" vs 官方 {d[4]}" for d in rep.fallback_diffs))
    fallback_alert = None
    for text in alerts:
        fallback_alert = text if fallback_alert is None else fallback_alert
        send(text)      # 尽力而为：失败不记入 run 状态（告警是旁路）
```

summary 增 `"fallback": fallback, "fallback_alert": fallback_alert`；`_run` 尾部（finish_run 前）：

```python
    # v0.13 恢复闭环：CLV 幂等回填（官方收盘价入库当天自动补齐 NULL）
    try:
        backfill_clv(conn)
    except Exception:
        pass            # 纯增益步：失败不上账（次日重试）
```

（import 区补 `results_fallback`、`backfill_clv`。）

- [x] **Step 3: 跑 daily 全部测试 + Commit**

```bash
uv run pytest tests/pipeline/test_daily.py -q
git add src/fa/pipeline/daily.py tests/pipeline/test_daily.py
git commit -m "feat(daily): 备用源结算步+恢复闭环告警+CLV 回填尾步（spec v0.13）"
```

---

### Task 8: CLI sync-fallback + 全量回归

**Files:**
- Modify: `src/fa/cli.py`
- Test: 全量 `uv run pytest -q`

- [x] **Step 1: CLI 命令**

```python
@data_app.command("sync-fallback")
def sync_fallback_cmd(dry_run: bool = typer.Option(False, "--dry-run")):
    """备用源赛果结算（与 daily 自动步同一代码路径；spec v0.13）。--dry-run 只算不落。"""
    from fa.data.fdorg import results_fallback
    conn = connect()
    try:
        out = results_fallback(conn, dry_run=dry_run)
    finally:
        conn.close()
    typer.echo(json.dumps(out, ensure_ascii=False, indent=1))
```

- [x] **Step 2: 全量回归**

Run: `uv run pytest -q` → 全绿（含既有 v0.12 回归）

- [x] **Step 3: Commit**

```bash
git add src/fa/cli.py
git commit -m "feat(cli): fa data sync-fallback [--dry-run]——备用源结算手动入口"
```

---

### Task 9: 真库上线（别名 → dry-run → 真跑 daily）

**Files:** 无代码；运维步骤 + 结果记录进本文件末尾

- [x] **Step 1: 别名两步走**

```bash
uv run fa data aliases-fdorg --write-auto     # auto 条目入库（归一化同名，零风险）
# 人工清单打印给负责人确认后：
uv run fa data aliases --source fdorg --confirm TEAM_ID=别名   # 逐条（或拼批量脚本）
```

- [x] **Step 2: dry-run 审计**

```bash
uv run fa data sync-fallback --dry-run
# 预期：triggered=true、fixtures≈本轮已完赛 pending 对应 fixture 数、
# filled 与 unmatched 之和=fixtures；unmatched.reason 逐条核对
```

- [x] **Step 3: 真跑 daily 结算本轮**

```bash
uv run fa run daily
# 预期：settled > 0（09-11/09-12 轮），TG 简报送达；dashboard 锚点前移
```

- [x] **Step 4: 结果记录 + 最终提交**

把三步实际输出摘要记到本计划文件末尾「上线记录」小节；`git add docs/superpowers/plans/2026-09-13-fdorg-results-fallback.md && git commit -m "docs(plan): fdorg fallback 上线记录"`。

---

## Self-Review

1. **Spec 覆盖**：设计 §3 触发（修正版）→ T5/T7；§4 客户端+配对 → T2/T5；§5 记账 → T5（raw_line/summary）+T4（fail_streak meta）；§6 恢复闭环 → T3（收缩基线）+T4（快照/diff）+T7（CLV 回填）；§7 两条告警 → T7；§8 CLI → T6/T8；§10 测试 → 各任务内（金案例=stopgap 6 场形态的配对/方向/同刻用例）；§11 spec → T1。缺口：设计 §4「免费层别名约 98 队一次性确认」由 T6 propose + T9 人工清单承接。
2. **占位符扫描**：T5 测试体含 `...断言...` 省略（执行者按用例名补全具体断言，测试意图与关键断言已给出）；其余任务代码完整。
3. **类型一致性**：`results_fallback` 返回形状在 T5/T7 一致；`SyncReport.fallback_diffs` 元组形状 T4 定义=T7 消费；`_normalize_name` T5 定义=T6 使用。✓

---

## 上线记录（2026-09-13 20:40~21:10 北京）

**别名两步**：`fa data aliases-fdorg` 拉全 96 队提案（auto 仅 2：RB Leipzig、
Paris FC）；94 条按当前赛季名册人工对照映射一次性写入（fdorg 官方全称 →
football-data canonical 简称，映射脚本见会话 tmp/confirm_aliases.py，明细可
`SELECT * FROM team_aliases WHERE source='fdorg'` 审计）。实施中漏写
Brentford FC（dry-run 的 alias_missing 抓出）后补全。

**实施中发现并修复两处数据/解析缺陷**：
1. **v4 fullTime 键名**：真实响应为 `home`/`away`（非文档直觉的 homeTeam/
   awayTeam）——首次 dry-run 0 命中暴露，客户端已兼容双拼写，测试 fixture
   换成真实响应形状（金案例从「臆造形状」变「实证形状」，正是设计 §10 要求）。
2. **oddsapi 侧 Santander 错绑**（存量数据伤，非本机制引入）：别名
   `Real Racing Club de Santander → team_id 92（Almeria，本赛季不在 SP1）`
   污染 fixtures 29/43/115；#43 的注在错绑发生前已按正确绑定结算（H 赢/A 输
   == 官方 Vallecano 3-2 Santander，账面无需重算）。已改绑 92→64 并重绑三
   fixture（fix_santander.py）。fdorg 配对层在这件事上正确拒绝（not_found），
   属设计防御的首次实战。
3. **收缩保护交互**：fallback 行计入收缩基线会把主源恢复顶死——T3 排除之
   （设计文档 §修正裁定已记）。

**dry-run 终态**：25/25 would_fill、unmatched=[]、errors=[]。

**真跑（run #42，2026-09-13T12:40~12:53Z）**：fallback 25/25 落行 →
**结算 140 注（48 胜，净 +378.09）**，TG 简报送达；四轨各 35 注
（model_only +90.17 / model_persona +95.90 / **kb_self 首批 +92.60** /
nokb +99.42）。剩余 pending 116（未开赛场次）。football-data 当日仍零更新
（inserted=0），全靠 fallback 达成「当日看到前一日赛果」。

**注意**：daily 的 fallback 步在本分支；未合入 main 前，cron 06:30 的 daily
仍是旧代码（无 fallback）。合入后即为全自动。
