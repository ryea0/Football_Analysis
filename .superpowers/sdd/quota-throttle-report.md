# 额度节流报告 —— quota<200 自动合并 region（spec §3.4「合并 region」落地）

日期：2026-09-04 · 分支 `worktree-m3-quota-throttle`（基于 main@cccc006）· 全程离线，未跑 matchday（不耗真实额度）

## 状态：完成 · 426 tests green（round 1 后；首版 417 = 409 既有 + 8 新增）

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

## 边界 / 未做（round 1 后已部分更新，见文末）

- 未动 `odds_api.py`（`fetch_odds` 默认双区保持原样）；未动 daily（本就不调
  sync_fixtures，无拉盘路径可节流）。
- `region_merged` 进 `runs.summary`；round 1 起也随返回 dict 暴露（CLI 选文案需要）。

## 顾虑

1. **降档判据读的是拉盘前的水位**（`quota_before`）。若 /events 探测带回更低的
   额度头，本次拉盘仍按旧水位选双区——下一 run 才生效。属设计内（判据统一走
   meta），非缺陷，但意味着降档晚一拍。
2. 合并 region 会让 `recommendations` 的最优价在 uk 侧候选缺失。Pinnacle 在 eu、
   T6 口径损失极小，但**paper 注的最优价可比性跨档不可比**（某场 am 双区、pm 单 eu）。
   若后续做最优价滑点分析，需按 `odds_snapshots.region` 过滤成同口径。
3. `DEFAULT_REGIONS` 与 `odds_api.fetch_odds` 的默认值仍是两份字面量（本次未动
   odds_api）；若日后改档需两处同步——round 1 已加签名断言测试钉住（见下）。

---

## Fix round 1（2026-09-04，评审通过后）：报告降级文案三态真实化 + 节流收尾三项

### 1.（Important）降级文案与真实拉取形态一致

**问题**：`render.py` 把 `degraded` 布尔直接翻成「是——本窗复用既有快照，价格类字段
可能滞后」。quota=150 的 run 拉的是**实时盘**（只是收窄到单 eu），价格并不滞后——
这句是向用户谎报价格新鲜度。同一句谎话也在 `cli.py` 的降级分支里。

**修法（未改两个 renderer 公开签名）**：matchday 把本窗事实落成两个机器可读键，
渲染层按键选句、不解析中文理由串：

- `runs.summary["snapshot_reused"] = True`（新）：本窗没拉盘（pm 低水位跳拉盘）或
  拉盘失败（OddsApiError）——价格确实滞后，保留原句。
- `runs.summary["region_merged"] = True`（既有）：本窗实时盘、uk 侧最优价缺失 →
  新句「是——本窗为实时盘，但已按额度收窄到单 eu（uk 侧最优价缺失）」。
- 两键并存时**快照复用优先**（合并档拉盘失败，真话是「没有实时盘」）。
- 都没有却 `degraded=True` → 中性句「是（原因见 runs.summary 的 degraded_reasons）」，
  不猜原因、绝不复用滞后句。

两处面已同步：`fa/report/render.py`（`_risk_block` 新增私有 `state` 参 +
`_degraded_s` 三态）与 `fa/cli.py`（降级分支按键四分支）。另两处关键裁定：

- **pm_update 的三态读 pm 自己的 summary**（`_risk_block(quota, degraded, am_summary,
  pm_summary)`）——样本量/半衰期沿用 am（pm 不重拟合），但「本窗拉取形态」以 pm 为
  准，am 的降级不得污染 pm 的风险行。
- **`region_merged` / `snapshot_reused` 同时进 `run_matchday` 返回 dict**（加键，
  不破既有契约）：CLI 在 `conn.close()` 后只剩 `out` 可读，加键比让 CLI 回读
  runs.summary 再解析 JSON 干净得多。`degraded` 布尔语义不变（「有降级」）。

### 2.（Minor）空跑降级带理由

`_finish_skipped` 原来只收 `degraded: bool`，落 summary 时没有 `degraded_reasons`
（pm 低水位 + 当日无赛事会留下「为何标降」无法解释的行）。改为收 `reasons: list[str]`，
`degraded` 由它推导（两参冗余且可能分叉），summary 一并落 `degraded_reasons`。

### 3.（Minor）默认双区漂移守卫

`test_default_regions_match_fetch_odds_signature`：断言
`inspect.signature(fetch_odds).parameters["regions"].default == DEFAULT_REGIONS`。
两个常量分叉会让 quota≥200 的全扫基线悄悄移动，行为测试测不出（各见一份）。

### 4.（Minor）透传测试改走 replay 替身 + pm 双区断言

- `test_fixtures.replay` 替身改为返回 `_Calls(list)`（带 `regions` 侧信道，仍是 list，
  既有 `replay == ["E0","D1"]` 断言零改动），`test_regions_thread_verbatim_to_fetch_odds`
  改收 `replay`：触网炸弹与「一次两 market」守卫照常生效，不再自带一份 markets 断言。
- `test_pm_full_chain_diffs_against_am_and_places_new_market` 补
  `assert env.regions == [DEFAULT_REGIONS]`（正常档 pm＝双区全扫的既有路径回归）。

### 测试净增 9（417 → 426）

render 三态 5（双区否 / 快照复用保留滞后句 / 合并不许说滞后 / 无键中性句 /
pm_update 读 pm 状态且 am 状态不泄漏）+ matchday 2（空跑降级带理由串、真空跑落空列表）
+ fixtures 1（签名守卫）+ CLI 1（quota=150 am：无「复用最近快照」、有「收窄到单 eu」，
且替身断言 regions 确为单 eu）。

### 报告勘误

首版「未把 region_merged 渲染进报告正文」的说法已过时：round 1 起风险提示块的
「降级」行按键三态渲染，quota=150 的报告明确写「本窗为实时盘，但已按额度收窄到单 eu」。
首版「region_merged 只进 summary 不入返回 dict」同样过时（见上）。

### 未改（有意）

- `tests/pipeline/test_matchday.py::test_cli_skipped_and_degraded_prints_empty_run_reason`
  的断言（「本次空跑未拉盘」+ 不得出现「复用最近快照」）在改后仍成立，无需动。
  评审提到的「pinned 旧文案的那一个测试」实际不存在——全库唯一写死旧句的地方是
  `render.py` 本体与 `cli.py`，测试只断言「降级/是」子串。
