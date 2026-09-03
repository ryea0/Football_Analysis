# 额度节流报告 —— quota<200 自动合并 region（spec §3.4「合并 region」落地）

日期：2026-09-04 · 分支 `worktree-m3-quota-throttle`（基于 main@cccc006）· 全程离线，未跑 matchday（不耗真实额度）

## 状态：完成 · 417 tests green（409 既有 + 8 新增）

## 改动

### 1. `src/fa/pipeline/fixtures.py`
- `sync_fixtures(conn, leagues, regions=DEFAULT_REGIONS)`：新增可选参 ``regions``，
  **原样透传**给每一次 ``fetch_odds(league, regions=regions)``。默认 `("eu", "uk")`
  未变 → 既有调用方 / 测试零改动（全库仅 matchday 一处调用）。
- 新增模块常量 `DEFAULT_REGIONS = ("eu", "uk")`（与 `odds_api.fetch_odds` 默认一致，
  避免两处字面量漂移；未动 odds_api，守住「最小改动」边界）。
- docstring 写明**节流契约**：本函数只透传、不读水位，降频决策归编排层。

### 2. `src/fa/pipeline/matchday.py`
- 新增常量：
  - `QUOTA_MERGE_FLOOR = 200`，注释钉死梯子口径：500/月档 · 双区全扫≈20/次 →
    **≥200 双区；<200 单 eu（Pinnacle 在 eu，最优价损失极小）；<100 pm 跳拉盘（既有）**
    ——设计原文写「≤200 单 eu」，与「quota < FLOOR」判据差 1，实现与注释统一取
    **严格小于**（quota 恰为 200 → 双区，有测试钉住该边界）。
  - `MERGE_REGIONS = ("eu",)`。
- `_run` 拉盘路径（am 与非降级 pm 都生效）：
  - `merge = quota_before is not None and quota_before < QUOTA_MERGE_FLOOR and not (pm and low)`
  - 命中 → `sync_fixtures(conn, leagues, regions=MERGE_REGIONS)`；否则传默认双区。
  - 标注两路并存：`degraded_reasons` 追加「额度 N < 200：额度降频，合并 region（eu）」
    （报告可见，§3.4「在报告标注」），`summary["region_merged"] = True`（机器可读）。

## 梯子不越档（本次设计的关键裁定）

pm 跳拉盘的判据是 `low = quota_before < QUOTA_FLOOR`，与 `reasons` 列表**无关**——
把合并理由追加进 `degraded_reasons` **不会**武装 pm 跳拉盘（`if not (phase == "pm" and low)`
只看 `low`）。因此：

| quota 水位 | am | pm |
|---|---|---|
| ≥200 | 双区拉盘，ok | 双区拉盘，ok |
| 100 ≤ q < 200 | 单 eu 拉盘，degraded_ok（region_merged） | **单 eu 照常拉盘**，degraded_ok（region_merged） |
| <100 | 单 eu 拉盘，degraded_ok（两条理由并存） | 跳拉盘复用快照，degraded_ok（无 region_merged） |

另取 `region_merged` 独立 summary 键而非只靠理由串：报告层 / E2E 区分「缩范围拉盘」
与「跳拉盘复用快照」两种降频时不必解析中文文案。

## 测试（8 个，全离线）

`tests/pipeline/test_matchday.py`（env fixture 的 `fake_fetch_odds` 增加
`box.regions` 记录实际发到线上的 region 集；既有断言不受影响）：

1. `test_am_quota_above_merge_floor_keeps_both_regions` — quota=250 → 双区，无标注，ok
2. `test_am_quota_at_merge_floor_is_exclusive_threshold` — quota=200 → 双区（严格小于）
3. `test_am_quota_below_merge_floor_scans_eu_only` — quota=150 → `("eu",)`、
   region_merged=True、理由串在、degraded_ok
4. `test_pm_quota_between_floors_pulls_single_region_instead_of_skipping` — pm@150
   **照常拉盘**且单 eu，理由串不含「跳过拉盘」（梯子不越档的核心回归）
5. `test_below_quota_floor_pm_skips_entirely_and_never_merges` — pm@99 → 零拉盘、
   无 region_merged（最严档不被中间档污染）
6. `test_am_below_quota_floor_stacks_both_lever_rungs` — am@99 → 拉盘照发 + 单 eu +
   两档理由并存
7. `test_no_quota_watermark_defaults_to_dual_region` — quota=None（meta 无水位）→
   默认双区，ok

`tests/pipeline/test_fixtures.py`：

8. `test_regions_thread_verbatim_to_fetch_odds` — 多联赛逐次透传；显式 `regions=("eu",)`
   原样到达 fetch_odds

既有 409 项全绿（含 `test_am_quota_below_floor_is_degraded_but_still_syncs`、
`test_return_keys_are_exactly_the_brief_contract`、部分失败额度头落库等）。

## 额度效果（机制层已验证，月耗为推算）

单次全扫 eu+uk × 2 market ≈ 20 credits；<200 后收窄单 eu 直接砍半计费 region 数。
按 E2E 口径 40/day：水位自 500 起约第 8 天跌破 200，此后日耗减半 → 全月不再在第 13 天
打穿，月耗从 ~1200 档压回 ~300 档。**该月耗数字是推算值，未经本轮实测**——下一轮
matchday（真额度）应复核 `runs.summary.region_merged` 触发时机与 `credits_before/after`
水位差，再回填 CLAUDE.md 的 E2E 实测栏。

## 边界 / 未做

- 未动 `odds_api.py`（`fetch_odds` 默认双区保持原样）；未动 reporting / CLI /
  daily（daily 本就不调 sync_fixtures，无拉盘路径可节流）。
- 未把 `region_merged` 渲染进报告正文——它已随 `summary` / `degraded_reasons`
  传给 renderer；是否单独成栏留给 T8/报告层裁决，避免本次越界改版式。
- `region_merged` 只进 `runs.summary`，不进 `run_matchday` 返回 dict（返回形状契约不变）。

## 顾虑

1. **降档判据读的是拉盘前的水位**（`quota_before`）。若 /events 探测带回更低的
   额度头，本次拉盘仍按旧水位选双区——下一 run 才生效。属设计内（判据统一走
   meta），非缺陷，但意味着降档晚一拍。
2. 合并 region 会让 `recommendations` 的最优价在 uk 侧候选缺失。Pinnacle 在 eu、
   T6 口径损失极小，但**paper 注的最优价可比性跨档不可比**（某场 am 双区、pm 单 eu）。
   若后续做最优价滑点分析，需按 `odds_snapshots.region` 过滤成同口径。
3. `DEFAULT_REGIONS` 与 `odds_api.fetch_odds` 的默认值仍是两份字面量（本次未动
   odds_api）；若日后改档需两处同步。
