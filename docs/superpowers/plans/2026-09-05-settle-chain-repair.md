# 结算链修复（数据链养护加固）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复「paper 注单九月起零结算」的结算链断裂——daily 的 CSV 同步永远不刷新缓存，且分区哈希回退会删除已入库赛果。

**Architecture:** 三处代码加固 + 一处 spec 增补：① `ingest_rows` 加分区收缩保护（新内容行数 < 库内现存 → 拒绝重建）；② `sync_history` 对当前赛季分区无条件强制重下（历史赛季维持缓存+哈希跳过）；③ `fa ops watchdog` 扩两项纯查询巡检（赛果滞后 / 滞留 pending 注）。收尾在生产库手工 `fa data sync --refresh` 恢复被 run#9 回退删除的 09-03 赛果行。

**Tech Stack:** Python 3.11+ / uv / pytest / SQLite（WAL, FK ON）。

**Spec:** spec.md（本计划自带 v0.11 → v0.12 增补，Task 1 先改 spec 再改代码——CLAUDE.md 纪律）。

## 事故背景（executor 需知道的实况）

- 结算机制：`settle_paper_bets`（src/fa/pipeline/paper.py:224）靠 `matches` 表的 football-data CSV 赛果行配对（联赛+主客+开球日+[0,+2]天窗）判输赢；配不上 → 永远 pending。
- 根因 A：`daily.py:64` 调 `sync_history(conn)` 不带 `refresh=True` → 只读本地缓存；缓存五文件自 09-03 04:23 下载后从未更新（内容只到 08-30/31）→ 九月赛果零入库 → 九月所有 paper 注（model_only 55 + model_persona 55 + model_persona_nokb 48）全部 pending。
- 根因 B：run#6（09-04 06:04 手动 daily）曾以带 09-03 赛果的新鲜 CSV 入库 50 行（F1 19 + SP1 31）并结算 4 注；run#9（09-05 06:30 cron daily）因库内分区哈希（新内容）≠ 陈旧缓存哈希，整分区 DELETE+INSERT 回退成 48 行——**已入库的 09-03 赛果被静默删除**。收缩保护防的正是这个方向。
- 重要语义：配对窗口按赛果「行日期」而非入库日期——赛果晚到的注仍可自愈结算；watchdog 的滞留 pending 告警因此是早期预警而非终判。

## Global Constraints

- 与用户沟通用中文；提交信息中文 conventional commits（如 `fix(data): …`）。
- agent（persona）不碰数据库与核心数字（spec §1.4/§2.2/§6）——本修复纯 Python 管线侧，不触 persona。
- 测试全离线：网络缝（download_csv）/ 推送缝（ops.send）/ 时钟缝（_today / _current_season_start）全替身。
- 阈值钉法沿用项目惯例：严格大于才告警（同 DAILY_GAP_ALERT_HOURS=25h 的边界钉法）。
- 不改 `bets`/`matches` 表结构（schema 版本不动）。

---

### Task 1: spec v0.12 增补（先改 spec 再改代码）

**Files:**
- Modify: `spec.md`（头部版本表 / 变更记录 / §3.4 / §9.5 / §9.6）

**Interfaces:**
- Produces: spec v0.12 文本（后续任务的注释引用「spec v0.12 §9.5」以此为准）。

- [ ] **Step 1: 头部版本表 v0.11 → v0.12**

`| 版本 | v0.11（确认稿） |` → `| 版本 | v0.12（确认稿） |`（日期行保持 2026-09-05）。

- [ ] **Step 2: 变更记录追加一行**（跟在 v0.10 → v0.11 行之后）

```markdown
> v0.11 → v0.12 变更：数据链养护加固（§3.4/§9.5/§9.6）——daily 的 CSV 同步对**当前赛季分区**无条件强制重下（历史赛季维持缓存+内容哈希跳过）；matches 分区**收缩保护**（新内容行数少于库内现存即拒绝重建、记 file_errors）；watchdog 扩两项数据链巡检（赛果滞后 / 滞留 pending 注，宽限 3 天）。修复实况：daily 从不刷新缓存致 09-01 起赛果零入库（九月 158 注 paper 全 pending），run#9 分区哈希回退曾删除已入库的 09-03 赛果（F1/SP1 各 1 行）。
```

- [ ] **Step 3: §3.4 第一条扩写**

`- 每日：更新已完赛场次（含收盘赔率，供 CLV 结算）` →
`- 每日：更新已完赛场次（含收盘赔率，供 CLV 结算；当前赛季 CSV 强制重下、历史赛季缓存+哈希跳过、分区收缩保护——见 §9.5）`

- [ ] **Step 4: §9.5 追加三条**（列表末尾）

```markdown
- CSV 同步分层刷新（v0.12）：当前赛季分区每次强制重下（live 数据），历史赛季走本地缓存 + 内容哈希跳过；当前赛季下载失败沿用「最近缓存 + 告警」
- matches 分区收缩保护（v0.12）：新内容行数 < 库内现存行数 → 拒绝重建（分区保持原样）并记 file_errors——防上游/缓存回退删赛果（2026-09-05 实况：run#9 曾把已入库的 09-03 赛果回退删除）
- watchdog 巡检扩展（v0.12）：daily 间隔（原有）之外，加赛果滞后（已开球 fixture 最新日 − matches 最新赛果日 > 3 天）与滞留 pending 注（开球日 + 3 天已过仍 pending）两项（§9.6）
```

- [ ] **Step 5: §9.6 watchdog 语句扩写**

`daily 成功后 \`fa ops watchdog\` 查漏跑（最近两次成功 daily 间隔 >25h 即告警，风险 #6 落地）` →
`daily 成功后 \`fa ops watchdog\` 查漏跑（最近两次成功 daily 间隔 >25h 即告警，风险 #6 落地；并巡检数据链——赛果滞后 / 滞留 pending 注超 3 天宽限即告警，v0.12）`

- [ ] **Step 6: Commit**

```bash
git add spec.md && git commit -m "docs(spec): v0.12 数据链养护加固——当前赛季强制重下/收缩保护/watchdog 数据链巡检"
```

---

### Task 2: ingest 分区收缩保护

**Files:**
- Modify: `src/fa/data/ingest.py:17-29`（ingest_rows，哈希检查后、DELETE 前）
- Test: `tests/data/test_ingest.py`、`tests/data/test_sync.py`

**Interfaces:**
- Consumes: `ingest_rows(conn, league, season, rows)` 既有签名不变。
- Produces: 收缩时抛 `ValueError("收缩保护：…")`——sync 层既有 per-file try/except 会把它记入 `SyncReport.file_errors`（不外溢、不中断其它分区）。

- [ ] **Step 1: 写失败测试**（追加到 tests/data/test_ingest.py）

```python
def test_shrunk_rebuild_refused(conn):
    """收缩保护（spec v0.12 §9.5）：新内容行数 < 库内现存 → 拒绝重建、分区原样。"""
    ingest_rows(conn, "E0", 1995, parse_csv(CSV_A, "E0", 1995))   # 2 行
    shrunk = parse_csv(CSV_A.splitlines()[0] + "\n" + CSV_A.splitlines()[1] + "\n",
                       "E0", 1995)                                  # 1 行
    with pytest.raises(ValueError, match="收缩保护"):
        ingest_rows(conn, "E0", 1995, shrunk)
    assert count(conn) == 2                              # 分区保持原样
    row = conn.execute("SELECT COUNT(*) c FROM matches WHERE date='1995-08-22'"
                       ).fetchone()["c"]
    assert row == 1                                      # 被砍的那行还在


def test_equal_count_rebuild_still_allowed(conn):
    """等行数的内容修正（如比分改判）不受收缩保护拦截。"""
    ingest_rows(conn, "E0", 1995, parse_csv(CSV_A, "E0", 1995))
    n = ingest_rows(conn, "E0", 1995, parse_csv(CSV_B_CORRECTED, "E0", 1995))
    assert n == 2 and count(conn) == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/test_ingest.py::test_shrunk_rebuild_refused -v`
Expected: FAIL（不抛异常，count 变 1）

- [ ] **Step 3: 最小实现**（ingest_rows 内，`if get_meta(conn, key) == h: return 0` 之后、`conn.execute("DELETE …")` 之前插入）

```python
    existing = conn.execute(
        "SELECT COUNT(*) c FROM matches WHERE league=? AND season=?",
        (league, season)).fetchone()["c"]
    if len(rows) < existing:         # 收缩保护（spec v0.12 §9.5）：
        raise ValueError(            # 上游/缓存回退时宁可不动分区也不删赛果
            f"收缩保护：{league}/{season} 分区库内 {existing} 行 > 新内容 "
            f"{len(rows)} 行，拒绝重建（疑似上游/缓存回退），分区保持原样")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/data/test_ingest.py -v`
Expected: 全 PASS（含既有用例）

- [ ] **Step 5: sync 层透传测试**（追加到 tests/data/test_sync.py）

```python
def test_shrink_guard_lands_in_file_errors_not_crash(conn, monkeypatch, tmp_path):
    """收缩保护经 sync 记 file_errors（run#9 实况防线）：不抛、分区原样。"""
    big = _cached(tmp_path, "E0", 1995, CSV_A.encode("utf-8"))        # 2 行
    _serve(monkeypatch, {("E0", 1995): big})
    sync_history(conn, seasons_from=1995)                             # 入库 2 行
    small = _cached(tmp_path, "E0", 1995,
                    (CSV_A.splitlines()[0] + "\n"
                     + CSV_A.splitlines()[1] + "\n").encode("utf-8"))  # 1 行
    _serve(monkeypatch, {("E0", 1995): small})
    rep = sync_history(conn, seasons_from=1995)                       # 哈希变了→重建→收缩
    assert len(rep.file_errors) == 1
    lg, y, msg = rep.file_errors[0]
    assert (lg, y) == ("E0", 1995) and "收缩保护" in msg
    assert _count(conn) == 2 and rep.inserted == 0
```

- [ ] **Step 6: 跑测试确认通过**

Run: `uv run pytest tests/data/test_sync.py -v`
Expected: 全 PASS

- [ ] **Step 7: Commit**

```bash
git add src/fa/data/ingest.py tests/data/test_ingest.py tests/data/test_sync.py
git commit -m "fix(data): matches 分区收缩保护——新内容行数少于库内即拒绝重建（spec v0.12 §9.5）"
```

---

### Task 3: sync 当前赛季强制重下

**Files:**
- Modify: `src/fa/data/sync.py:34-73`（sync_history 的 download_csv 调用与 docstring）
- Test: `tests/data/test_sync.py`

**Interfaces:**
- Consumes: `download_csv(league, start_year, refresh)` 既有签名。
- Produces: `sync_history(conn, seasons_from, refresh)` 签名不变；行为变化 = 当前赛季分区（`year == _current_season_start()`）无论 refresh 形参一律 `refresh=True`。

- [ ] **Step 1: 写失败测试**（追加到 tests/data/test_sync.py）

```python
def test_current_season_always_refreshed(conn, monkeypatch, tmp_path):
    """当前赛季分区无条件强制重下（spec v0.12 §9.5）——daily 不传 refresh 也能拿到新赛果。"""
    flags: dict[tuple[str, int], bool] = {}
    monkeypatch.setattr("fa.data.sync._current_season_start", lambda: 1996)

    def fake_download(league, start_year, refresh=False):
        flags[(league, start_year)] = refresh
        if (league, start_year) != ("E0", 1995) and (league, start_year) != ("E0", 1996):
            return None
        p = tmp_path / f"{league}_{start_year}.csv"
        p.write_text(CSV_A.replace("1995", str(start_year)))
        return p

    monkeypatch.setattr("fa.data.sync.download_csv", fake_download)
    sync_history(conn, seasons_from=1995)             # 不传 refresh
    assert flags[("E0", 1996)] is True                # 当前赛季：强制重下
    assert flags[("E0", 1995)] is False               # 历史赛季：走缓存
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/test_sync.py::test_current_season_always_refreshed -v`
Expected: FAIL（flags[("E0",1996)] is False）

- [ ] **Step 3: 最小实现**

sync_history 内 `path = download_csv(league, year, refresh=refresh)` 改为：

```python
                # 当前赛季是 live 数据：无条件强制重下（spec v0.12 §9.5——
                # 修复 daily 只读陈旧缓存致赛果零入库）；refresh 形参只额外
                # 作用到历史赛季
                path = download_csv(league, year, refresh=refresh or year == to_year)
```

并在 sync_history docstring 开头补一段：

```python
    """下载并入库历史+当前赛季 CSV（幂等：内容哈希跳过）。

    当前赛季分区（``year == 当前赛季起始年``）无条件 ``refresh=True`` 重下——
    否则 daily 永远读陈旧缓存、赛果零入库（2026-09-05 实况）；历史赛季维持
    缓存 + 哈希跳过，``refresh=True`` 时全量重下。
    """
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/data/test_sync.py -v`
Expected: 全 PASS（既有用例不受影响——fake_download 均容忍 refresh 形参）

- [ ] **Step 5: Commit**

```bash
git add src/fa/data/sync.py tests/data/test_sync.py
git commit -m "fix(data): sync 当前赛季分区无条件强制重下——daily 赛果零入库根因（spec v0.12 §9.5）"
```

---

### Task 4: watchdog 数据链巡检

**Files:**
- Modify: `src/fa/pipeline/ops.py`（新增两检查 + run_watchdog 聚合 + `_today` 时钟缝）
- Modify: `src/fa/cli.py:1091-1109`（watchdog 帮助文案与静默输出）
- Test: `tests/pipeline/test_ops.py`

**Interfaces:**
- Consumes: `fa.pipeline.paper.MODE`（"paper"）/ `STATUS_PENDING`（"pending"）常量；`fa.pipeline.runs` 既有常量。
- Produces: `check_matches_lag(conn) -> str | None`、`check_stuck_bets(conn) -> str | None`、`_today() -> date`（时钟缝，测试 monkeypatch 用）；`run_watchdog` 返回形状不变 `{"alert": str | None, "sent": bool | None}`（多条告警以 `\n` 连接为一条推送）。
- 常量：`DATA_LAG_ALERT_DAYS = 3`。

- [ ] **Step 1: 写失败测试**（追加到 tests/pipeline/test_ops.py；文件头 import 补 `from datetime import date`）

```python
# ---------------------------------------------------------------- 数据链巡检（v0.12）

def _seed_bet_chain(conn, kickoff_utc: str, bet_status: str = "pending"):
    """种一条 runs→fixtures→recommendations→bets 全链（FK ON，缺一不可）。"""
    conn.execute(
        "INSERT INTO runs (type, phase, started_at, status)"
        " VALUES ('matchday', 'am', '2026-09-01T03:00:00Z', 'ok')")
    run_id = conn.execute("SELECT MAX(id) id FROM runs").fetchone()["id"]
    conn.execute(
        "INSERT INTO fixtures (league, event_key, source, kickoff_utc, status,"
        " created_at) VALUES ('E0', 'k1', 'oddsapi', ?, 'scheduled',"
        " '2026-09-01T00:00:00Z')", (kickoff_utc,))
    fx = conn.execute("SELECT MAX(id) id FROM fixtures").fetchone()["id"]
    conn.execute(
        "INSERT INTO recommendations (run_id, fixture_id, strategy, market,"
        " phase, model_p, market_p, best_odds, bookmaker, edge, ev,"
        " kelly_stake_frac, created_at)"
        " VALUES (?, ?, 'model_only', 'H', 'am', 0.4, 0.3, 2.5, 'b', 0.1,"
        " 0.05, 0.01, '2026-09-01T03:00:00Z')", (run_id, fx))
    rec = conn.execute("SELECT MAX(id) id FROM recommendations").fetchone()["id"]
    conn.execute(
        "INSERT INTO bets (recommendation_id, mode, placed_at, bookmaker,"
        " odds_taken, stake, status)"
        " VALUES (?, 'paper', '2026-09-01T03:00:00Z', 'b', 2.5, 10.0, ?)",
        (rec, bet_status))
    conn.commit()


def _seed_match_row(conn, d: str):
    conn.execute(
        "INSERT INTO matches (league, season, date, fthg, ftag, raw_line)"
        " VALUES ('E0', 2026, ?, 1, 0, '{}')", (d,))
    conn.commit()


def test_check_matches_lag_alerts_when_results_frozen(env, monkeypatch):
    """实况复刻：fixture 已开球到 09-04、matches 冻在 08-31 → 滞后 4 天 > 3 告警。"""
    monkeypatch.setattr(ops, "_today", lambda: date(2026, 9, 5))
    _seed_bet_chain(env.conn, "2026-09-04T19:00:00Z")   # 只为种出 past fixture
    _seed_match_row(env.conn, "2026-08-31")
    text = ops.check_matches_lag(env.conn)
    assert text is not None and "滞后 4 天" in text and "2026-08-31" in text


def test_check_matches_lag_silent_within_grace(env, monkeypatch):
    """常规滞后（football-data 次日出赛果）不告警。"""
    monkeypatch.setattr(ops, "_today", lambda: date(2026, 9, 5))
    _seed_bet_chain(env.conn, "2026-09-04T19:00:00Z")
    _seed_match_row(env.conn, "2026-09-03")
    assert ops.check_matches_lag(env.conn) is None


def test_check_matches_lag_silent_when_no_past_fixtures(env, monkeypatch):
    """只有未来 fixture（含休赛期空表）→ 静默。"""
    monkeypatch.setattr(ops, "_today", lambda: date(2026, 9, 5))
    _seed_bet_chain(env.conn, "2026-09-06T19:00:00Z")
    assert ops.check_matches_lag(env.conn) is None


def test_check_matches_lag_alerts_when_matches_empty(env, monkeypatch):
    monkeypatch.setattr(ops, "_today", lambda: date(2026, 9, 5))
    _seed_bet_chain(env.conn, "2026-09-04T19:00:00Z")
    text = ops.check_matches_lag(env.conn)
    assert text is not None and "无任何赛果行" in text


def test_check_stuck_bets_alerts_after_grace(env, monkeypatch):
    monkeypatch.setattr(ops, "_today", lambda: date(2026, 9, 5))
    _seed_bet_chain(env.conn, "2026-09-01T19:00:00Z")   # 开球 09-01，宽限线 09-02
    text = ops.check_stuck_bets(env.conn)
    assert text is not None and "1 注" in text


def test_check_stuck_bets_silent_within_grace(env, monkeypatch):
    monkeypatch.setattr(ops, "_today", lambda: date(2026, 9, 5))
    _seed_bet_chain(env.conn, "2026-09-03T19:00:00Z")   # 09-03 ≥ 宽限线 09-02
    assert ops.check_stuck_bets(env.conn) is None


def test_check_stuck_bets_ignores_settled_and_non_paper(env, monkeypatch):
    monkeypatch.setattr(ops, "_today", lambda: date(2026, 9, 5))
    _seed_bet_chain(env.conn, "2026-09-01T19:00:00Z", bet_status="lost")
    assert ops.check_stuck_bets(env.conn) is None


def test_run_watchdog_joins_alerts_into_one_push(env, monkeypatch):
    """漏跑 + 数据链滞后同时发生 → 两条文本合一条推送。"""
    monkeypatch.setattr(ops, "_today", lambda: date(2026, 9, 5))
    _seed(env.conn, "daily", None, _iso(T0), "ok")
    _seed(env.conn, "daily", None, _iso(T0 + timedelta(hours=30)), "ok")
    _seed_bet_chain(env.conn, "2026-09-01T19:00:00Z")
    out = ops.run_watchdog(env.conn)
    assert out["sent"] is True
    assert out["alert"].count("⚠️") == 2 and "\n" in out["alert"]
    assert env.pushed == [out["alert"]]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/pipeline/test_ops.py -k "lag or stuck or joins" -v`
Expected: FAIL（check_matches_lag / check_stuck_bets 不存在）

- [ ] **Step 3: 最小实现**（src/fa/pipeline/ops.py）

文件头 import 改为：

```python
import sqlite3
from datetime import date, datetime, timedelta, timezone

from fa.pipeline.paper import MODE as PAPER_MODE
from fa.pipeline.paper import STATUS_PENDING
from fa.pipeline.reporting import send
from fa.pipeline.runs import RUN_DAILY, STATUS_DEGRADED, STATUS_OK
```

`DAILY_GAP_ALERT_HOURS = 25` 之后追加常量与时钟缝：

```python
# 数据链巡检宽限（天，spec v0.12 §9.6）：已开球 fixture 超过该天数仍无赛果行 /
# pending 注开球超过该天数仍未结算 → 告警。football-data 常规滞后 1-2 天、个别
# 联赛可达 4-5 天（2026-09 实况 E0 停在 08-31）；3 天宽限下正常滞后不告警、
# 链路断才告警。结算窗按赛果「行日期」配对，赛果晚到的注仍可自愈——本告警是
# 早期预警不是终判。严格大于才告警（同 DAILY_GAP_ALERT_HOURS 钉法）。
DATA_LAG_ALERT_DAYS = 3


def _today() -> date:
    """今日（日历日）——独立函数供测试 monkeypatch（同 daily._yesterday 纪律）。"""
    return date.today()
```

`check_daily` 之后追加两个检查（正文见 Step 1 测试的断言措辞）：

```python
def check_matches_lag(conn: sqlite3.Connection) -> str | None:
    """纯查询：赛果链滞后——最新已开球 fixture 日 − matches 最新赛果日 >
    DATA_LAG_ALERT_DAYS 即告警；无已开球 fixture（休赛期/空库）静默。"""
    today = _today().isoformat()
    fx = conn.execute(
        "SELECT MAX(substr(kickoff_utc, 1, 10)) d FROM fixtures"
        " WHERE substr(kickoff_utc, 1, 10) < ?", (today,)).fetchone()["d"]
    if fx is None:
        return None
    m = conn.execute("SELECT MAX(date) d FROM matches").fetchone()["d"]
    if m is None:
        return (f"⚠️ fa watchdog：matches 无任何赛果行，但已有开球日 {fx} 的 "
                f"fixture——数据链未跑通")
    lag = (date.fromisoformat(fx) - date.fromisoformat(m)).days
    if lag <= DATA_LAG_ALERT_DAYS:
        return None
    return (f"⚠️ fa watchdog：赛果链滞后——最新已开球 fixture {fx}，matches 最新"
            f"赛果 {m}（滞后 {lag} 天 > {DATA_LAG_ALERT_DAYS} 天）。"
            f"检查 CSV 同步是否刷新、file_errors 里有无收缩保护记录")


def check_stuck_bets(conn: sqlite3.Connection) -> str | None:
    """纯查询：滞留 pending paper 注——开球日早于 今日−DATA_LAG_ALERT_DAYS
    仍 pending 即告警（计数不点名，直读症状）。"""
    cutoff = (_today() - timedelta(days=DATA_LAG_ALERT_DAYS)).isoformat()
    n = conn.execute(
        "SELECT COUNT(*) c FROM bets b JOIN recommendations r"
        " ON b.recommendation_id = r.id JOIN fixtures f ON f.id = r.fixture_id"
        f" WHERE b.mode='{PAPER_MODE}' AND b.status='{STATUS_PENDING}'"
        " AND substr(f.kickoff_utc, 1, 10) < ?", (cutoff,)).fetchone()["c"]
    if not n:
        return None
    return (f"⚠️ fa watchdog：{n} 注 paper 开球已超 {DATA_LAG_ALERT_DAYS} 天仍 "
            f"pending——赛果晚到或结算链断（开球日早于 {cutoff}）")
```

`run_watchdog` 改为聚合：

```python
def run_watchdog(conn: sqlite3.Connection) -> dict:
    """判据 + 推送一体：三项巡检（daily 间隔 / 赛果滞后 / 滞留 pending 注），
    异常文本以 ``\\n`` 合并成**一条**推送。返回 ``{"alert": str | None,
    "sent": bool | None}``——``sent=None`` 表示无告警可推（区别于「推了但
    失败」的 ``False``）。"""
    alerts = [t for t in (check_daily(conn), check_matches_lag(conn),
                          check_stuck_bets(conn)) if t is not None]
    if not alerts:
        return {"alert": None, "sent": None}
    alert = "\n".join(alerts)
    return {"alert": alert, "sent": send_alert(alert)}
```

模块 docstring 的 watchdog 段落补一句：「v0.12 起加数据链两项巡检（check_matches_lag / check_stuck_bets），本模块仍只读 runs/fixtures/matches/bets、不写任何表」。

- [ ] **Step 4: CLI 文案对齐**（src/fa/cli.py）

- `@ops_app.command("watchdog")` 的 docstring：`"""daily 健诊：成功间隔超阈值（漏跑/连续失败）即告警（daily wrapper 收尾调用）"""` → `"""daily 健诊：跑批间隔 + 数据链（赛果滞后/滞留 pending 注）三项巡检，异常即告警（daily wrapper 收尾调用）"""`
- 静默分支输出：`typer.echo("watchdog：无异常（daily 成功间隔在阈值内）")` → `typer.echo("watchdog：无异常（三项巡检通过）")`

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/pipeline/test_ops.py -v`
Expected: 全 PASS（既有用例不种子 fixtures/matches/bets → 两新检查天然静默）

- [ ] **Step 6: Commit**

```bash
git add src/fa/pipeline/ops.py src/fa/cli.py tests/pipeline/test_ops.py
git commit -m "feat(ops): watchdog 扩数据链巡检——赛果滞后/滞留 pending 注（spec v0.12 §9.6）"
```

---

### Task 5: 全量回归 + 推送

- [ ] **Step 1: 全量测试**

Run: `uv run pytest -q`
Expected: 全 PASS（零 skip 异常；如有既有失败先核对是否 main 基线既有）

- [ ] **Step 2: 推送分支**（GitHub 须走 clash 代理，记忆 local-network-github-push-proxy）

```bash
https_proxy=http://127.0.0.1:7890 http_proxy=http://127.0.0.1:7890 \
  git push -u origin worktree-fix-settle-chain
```

---

### Epilogue: 生产库数据恢复（操作步骤，非代码任务）

**背景**：run#9 把 F1/SP1 2026 分区回退删除了 09-03 两行赛果（fixtures 96/30 已结算的账面对应行）。**必须在主仓执行**（下载落主仓 `data/csv` 缓存；在 worktree 执行会把新赛果下进 worktree 缓存，明早 cron 在主仓用旧缓存哈希不匹配又把新行删掉——正是要修的回退路径）。

- [ ] 主仓执行：`cd /home/ryea0/Project/Football_Analysis && uv run fa data sync --refresh`
- [ ] 验证：`sqlite3 data/fa.db "SELECT league, MAX(date), COUNT(*) FROM matches WHERE season=2026 GROUP BY league;"` → F1=2026-09-03/19 行、SP1=2026-09-03/31 行（其余联赛按上游现状）
- [ ] 说明：09-04 场次的注（model_persona 5 注等）需等 football-data 发布 09-04 赛果后由下一次 daily 结算；赛果行日期在配对窗内即可自愈，不会过期作废。
- [ ] 分支合并 main 后，cron daily 自动获得「当前赛季强制重下 + 收缩保护」行为；合并前明晚的 daily 用旧代码但缓存哈希已一致（本次 refresh 写入），不会再次回退。

## Self-Review

1. **Spec 覆盖**：四项裁定（当前赛季重下 / 收缩保护 / watchdog 两巡检 / 生产数据恢复）分别落在 Task 3 / Task 2 / Task 4 / Epilogue；spec 文本在 Task 1 先行。✓
2. **占位符扫描**：所有代码步骤含完整代码；无 TBD/「适当处理」。✓
3. **类型一致性**：`check_matches_lag` / `check_stuck_bets` / `_today` / `DATA_LAG_ALERT_DAYS` 在 Task 4 的测试与实现两侧签名一致；`PAPER_MODE`/`STATUS_PENDING` 从 paper.py 导入（已确认存在：paper.py:249 使用 `MODE, STATUS_PENDING`）。✓
