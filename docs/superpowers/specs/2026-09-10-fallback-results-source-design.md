# 赛果备用源 fallback 机制设计（football-data.co.uk 主 / api.football-data.org 备）

- 日期：2026-09-10
- 状态：已实施（2026-09-13，plan: docs/superpowers/plans/2026-09-13-fdorg-results-fallback.md；原批准设计 2026-09-10，brainstorm 三问三裁 + 两轮分节设计均获负责人确认）
- 决策记录：负责人 2026-09-10 三项裁定——① 触发判据选 **A 结果驱动**（弃源驱动全量镜像与纯手动）；② 恢复后比分不一致 **告警不改账**（弃自动重结算，裁定权在人）；③ 主源故障告警阈值 **N=2 连续天**（弃 N=1 噪音与不告警）。配对方式裁定走 **fdorg 别名表**（弃模糊匹配）
- 修正裁定（2026-09-13，负责人）：触发判据去「主源当前赛季失败」合取项——**纯结果驱动**。背景：主源自 503 恢复后当季 CSV 内容常态滞后 1-3 天（09-11/09-12 轮至 09-13 仍未上文件），原判据只覆盖整站故障形态，无法满足「每日看到前一日赛果」；§3 触发条件相应修改。实施同时发现并修正收缩保护与 fallback 行的计数交互（基线排除 fallback/stopgap 行，spec v0.13）
- 关联：spec §3.1/§3.4（本次修订为 v0.13 提案）、§3.3（队名对齐/隔离纪律）、§7.3（CLV 基准链——先例：Pinnacle→Betfair fallback 的记账做法）、`docs/m3-report.md`（别名对齐流程先例：oddsapi 侧 44 条确认）

## 1. 背景与动机

**事故回放（2026-09-06 ~ 09-10）**：football-data.co.uk 整站 503（维护页）持续 4 天+。T5 `sync_history` 按设计单文件容错（`file_errors` 只记账不外溢），当前赛季 5 个文件天天失败；但 `sync_degraded` 判据是 `files_ok==0 且 file_errors>0`，而历史赛季缓存命中使 `files_ok=165`——**当前赛季失败被掩盖，run 状态天天 `ok`，零告警**。27 注 paper（09-06/09-07 开球）挂 pending 四天，09-09 人工排查才发现。

**已完成的应急（本设计的实证基础）**：09-10 用 api.football-data.org（免费层）人工 stopgap——6 行赛果按「kickoff 时间戳精确相等 + fixture 已对齐 team_id」配对写入 `matches`（`raw_line` 标记来源），`fa run daily` 结算 27 注（6 中 21 负，净 -115.25）。事后独立源逐场核对 6/6 比分正确；写入时另有 9 场与官方 CSV 已入库行交叉验证 9/9 一致。备份 `data/fa.db.bak-pre-stopgap-20260910-0324`。

**动机**：把这次人工补结算沉淀为正式机制——主源故障期间结算不中断、故障可见（告警）、恢复后自动闭环（官方行覆盖 + CLV 回填 + 比分核对）。

## 2. 目标与非目标

**目标**：
1. 主源当前赛季失败时，逾期 paper 注自动经备用源结算（结果驱动、精准拉取）
2. 主源故障 2 天即 TG 告警（这次 4 天无人察觉的对症）
3. 主源恢复后全自动闭环：官方行幂等覆盖 fallback 行、比分 diff 核对、CLV 回填，无需人工
4. 比分不一致时告警不改账——已入账的钱只有人能裁定

**非目标**：
- 不做备用源全量镜像当前赛季（免费层额度、队名全量映射成本、与 T5 重建交互重——源驱动方案已裁定弃）
- 不做备用源收盘价（免费层无赔率数据，CLV 基准链仍走 §7.3 Pinnacle→Betfair）
- 不改 `ingest_rows` 等 A 线共享函数核心（B 线纪律：daily 是 B 线触达 `matches` 的唯一入口）
- 不做自动重结算/自动改账（裁定 ②）
- 不做真实外网 E2E 进测试套件（CI 无 key、不稳）

## 3. 触发判据（结果驱动）

挂载点：`run_daily`（`src/fa/pipeline/daily.py`）的 `sync_history` 与 `settle_paper_bets` 之间，新步 `results_fallback`：

```
逾期注集 O = { pending paper 注
             | kickoff_utc + MAX_MATCH_DAY_GAP(=2) 天 < 今天
             , 且 _paired_match(...) 配不到完赛行 }        # 按 fixture 去重
触发条件 = O 非空 且 当日 SyncReport.file_errors 含当前赛季(year==当前赛季起始年)条目
触发后  = O 按 (联赛, 日期窗) 分组 → 每联赛 1 次 API 调用（dateFrom/dateTo 夹紧窗口）
```

要点：
- 「配不到完赛行」复用 `paper._paired_match` 同一套窗语义（`[kickoff UTC 日, +2 天]`）——触发判据与结算判据同锚，不会出现「触发了但结算仍配不上」的口径分裂
- 一次 daily 最多 5 个请求（每联赛 1 个），免费层 10 req/min 天然够，**不做限速器**（记录假设：逾期 fixture 的日期窗跨度通常 ≤1 周；若未来单窗超 10 联赛再议）
- `MAX_MATCH_DAY_GAP` 改名复用 `paper` 的常量（单一事实源，不复制魔法数）
- 有意设计：主源**健康**但个别注逾期（如 CSV 数据晚到/队名错位）**不自动触发**——fallback 不介入健康源的数据质量个案。该场景**无专门告警**（§7 两条均不覆盖：#1 要求主源连续失败，#2 只在覆盖 diff 时触发），可见性靠 dashboard pending 与 weekly 小结，人工可跑 `fa data sync-fallback --dry-run` 排查（§8）。取舍记录在案

## 4. 备用源客户端与配对（`src/fa/data/fdorg.py`，新模块）

**客户端**：
- 端点 `GET /v4/competitions/{code}/matches?dateFrom=&dateTo=`，头 `X-Auth-Token: $FOOTBALL_DATA_ORG_KEY`
- 联赛映射：E0→PL、SP1→PD、D1→BL1、I1→SA、F1→FL1
- `FDORG_BASE_URL` 可 env 覆盖（测试指向本地 fixture JSON；沿用 `HERMES_BIN` 的 mock 模式）
- 4xx/5xx/网络异常 → 单联赛失败只记账进 `fallback.error`，不中断其他联赛（单文件容错同构 T5 纪律）

**配对三判据（确定性，全部满足才落行）**：
1. `match.utcDate == fixture.kickoff_utc`（时间戳严格相等——stopgap 实证 6/6；同联赛多场同刻开球不唯一，故必须叠加 2/3）
2. 队名经 `team_aliases(source='fdorg')` 解析到 team_id，与 fixture 的 `home_team_id`/`away_team_id` 相等（方向校验）
3. `status == 'FINISHED'` 且 fullTime 比分非空

**别名表**：五大联赛约 98 队一次性确认——复用 M3 别名对齐流程（编辑距离候选 + 人工确认清单），`team_aliases` 现成 `UNIQUE(source, alias)` 约束，只是新 source='fdorg'。fdorg 队名稳定（如 `RCD Espanyol de Barcelona`、`SS Lazio`），确认后预期零维护。

**配不上** → 不落该行 + `fallback.unmatched[]` 记 fixture 与原因 + TG 告警（§3.3 隔离纪律：绝不静默、绝不硬猜）。

## 5. 记账约定

| 落点 | 约定 |
|---|---|
| `matches` 行 | 与 T5 同约定：对齐 team_id、`date`=kickoff UTC 日历日、season=起始年、fthg/ftag；同 unique key `(league, season, date, home, away)` → 主源恢复后 T5 分区重建幂等覆盖，无残留 |
| `matches.raw_line` | `{"source":"api.football-data.org","fallback":true,"fetched_at":...,"fixture_id":...,"match":"<队名 比分>"}`。**识别 fallback 行统一认 `source` 字段**；09-10 已入库 6 行的 `"stopgap":true` 保留为历史痕迹，不迁移、比对逻辑两个键都认 |
| `runs.summary["fallback"]` | `{triggered, fixtures, filled, unmatched[], error}`；未触发时 `{"triggered": false}` |
| `meta["current_season_fail_streak"]` | 当前赛季失败连续天数计数器（N=2 告警判据；成功日归零） |
| token 缺失 | 触发条件满足但无 `FOOTBALL_DATA_ORG_KEY` → fallback 静默禁用，summary 记 `skipped:"no_token"`；不另发告警（主源 N=2 告警已把人叫来，dashboard 可见 pending，避免告警噪音） |

## 6. 恢复闭环（全自动）

1. **官方行覆盖 + 比分 diff**：`sync.py` 当前赛季分支外包一层（不动 `ingest_rows` 核心）——分区重建**前**抓 fallback 行快照（unique key + fthg/ftag），重建**后**对同 key 官方行 diff：
   - 一致 → 静默覆盖（幂等），`current_season_fail_streak` 归零
   - **不一致 → TG 告警**（场次、两版比分、受影响注数），**已结算注一律不动**（裁定 ②：改账需人裁定）
2. **CLV 自动回填**：daily 尾部追加幂等 `backfill_clv()`（已有函数，纯 UPDATE 只填 NULL）——恢复当天官方收盘价入库后 CLV 自动补齐；现挂着的第二阶段人工探测任务届时删除
3. 恢复当天不发「恢复简报」（dashboard 可见即可，控噪）；告警仅比分不一致一条

## 7. 告警（两条，刻意克制）

| # | 条件 | 动作 |
|---|---|---|
| 1 | `current_season_fail_streak >= 2` | `fa ops alert` TG：主源当前赛季连续 N 天失败 |
| 2 | 恢复覆盖 diff 不一致 | TG：比分存疑，受影响注待人工裁定 |

**有意不加**：第三条「逾期注存在」告警——主源故障场景下根因已被 #1 覆盖（逾期持续 ⇒ 主源持续失败 ⇒ #1 已响），加第三条只会同因重复响。**已知未覆盖**：主源健康但个别注逾期（CSV 行缺失/晚到类数据质量问题）无推送告警，靠 dashboard pending 可见（§3 取舍）。此取舍记录在案。

## 8. CLI

- `fa data sync-fallback [--dry-run]`：手动跑同一 fallback 函数（与 daily 自动步同一代码路径）；`--dry-run` 打印将拉的 fixture、API 响应与配对结果，不落库——审计与应急双用途（如 §3 所述主源健康但个别逾期的场景）
- fdorg 别名确认复用既有 `fa data aliases --confirm` 流程（候选清单 + 人工确认写 `team_aliases`），扩展 source 通道，不新造交互

## 9. 降级与容错汇总

| 故障 | 行为 |
|---|---|
| fdorg API 单联赛失败 | 记 `fallback.error`，其他联赛照常，run 不中断 |
| 配对失败（别名缺/kickoff 不等/未完赛） | 不落行，`unmatched[]` + 告警 |
| token 缺失 | fallback 禁用，`skipped:"no_token"` |
| 主源恢复但 fallback 行仍在（覆盖窗口外） | T5 重建覆盖之；比分 diff 同 §6 |
| 逾期注永远配不上（比赛腰斩/取消） | 留 pending + unmatched 告警，人裁定（void 手动） |

## 10. 测试策略

- **单元**：逾期集合计算（窗边界 ±2d、pending 过滤、fixture 去重）；配对三判据（含方向反、同刻多场、别名缺失、未完赛）；`unmatched` 记账；覆盖 diff（一致/不一致/快照空）；`current_season_fail_streak` 计数与归零；token 缺失降级；`degraded_current_season` 判据（历史命中掩盖场景的回归测试——用这次事故形态做金案例）
- **客户端 mock**：`FDORG_BASE_URL` 指向本地 fixture JSON；**金案例 = 2026-09-10 stopgap 的 6 场真实响应**（覆盖 1-1 平局、主客双方向、多场同刻）存入测试 fixture
- **回归**：`sync_degraded` 修正后旧断言更新（`files_ok>0` 但当前赛季全错的形态必须 degraded）

## 11. spec 修订（v0.13 提案，实施首步）

- **§3.1** 数据源表 +1 行：`api.football-data.org | 备用赛果源（fallback） | 免费层、X-Auth-Token、10 req/min；无收盘价（CLV 仍走 §7.3 基准链）；结果驱动触发（逾期注+主源当前赛季失败）；raw_line 记 source、恢复后官方行幂等覆盖+比分 diff 告警不改账`
- **§3.4**：补 fallback 触发判据、`degraded_current_season` 单列（修正「历史缓存命中掩盖当前赛季失败」的判据缺陷）、连续 2 天失败 TG 告警、恢复闭环（覆盖+diff+CLV 回填）
- **零 schema 变更**：`team_aliases`/`matches`/`meta`/`runs.summary` 全部现成，不 bump schema 版本；spec 版本 v0.12 → v0.13

## 12. 实施面与前提

| 文件 | 变更 |
|---|---|
| `src/fa/data/fdorg.py` | 新：客户端 + 联赛映射 + 配对 |
| `src/fa/data/sync.py` | `degraded_current_season` 判据 + fallback 行快照/diff 包装 |
| `src/fa/pipeline/daily.py` | `results_fallback` 步 + 尾部 `backfill_clv()` |
| `src/fa/pipeline/paper.py` | `MAX_MATCH_DAY_GAP` 导出复用（如需） |
| `src/fa/data/`（别名对齐流程） | source='fdorg' 支持 + 确认清单 |
| `src/fa/cli.py` | `fa data sync-fallback [--dry-run]` |
| `src/fa/config.py` | `FDORG_BASE_URL`、token 读取 |
| `tests/` | 单元 + mock 客户端 + 金案例 fixture |

**实施前提**：工作区现有另一会话未提交改动（settled_at 回填、FK 修复相关，涉及 `paper.py`/`db.py`/`cli.py`）——开工前先确认该线状态（合入或协调），`paper.py` 双线都会动，避免踩脚。
