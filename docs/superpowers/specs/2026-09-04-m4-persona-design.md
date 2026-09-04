# M4 Persona 层实现设计

- 日期：2026-09-04
- 性质：**实现设计**。契约权威在 [spec.md](../../../spec.md) §6（v0.5），本文不复制、不改写契约，只记录实现裁定与工程选型；与 spec 冲突处以 spec 为准（两处例外见 §9 修订清单，按「改设计先改 spec」随 M4 计划一并落）。
- 前置：M3 完成（`docs/m3-report.md`）；`hermes -z` headless 推理 2026-09-04 实测可用（`{"ok": true}`、exit 0）；表结构就绪——`recommendations` 有 `strategy CHECK('model_only','model_persona')` + persona 三列 + `UNIQUE(fixture_id, market, strategy, phase)`，`bets` 经 `recommendation_id` 关联。

## 1. 范围

M4 = spec §10：「Hermes persona 接入——5 personas、契约校验、降级、A/B 双轨；验收 mock + 实跑测试通过、A/B 数据落库」。

不做：真实下注（§12.2 不变）；pm 重跑已判场次；persona 新增候选的能力（§6.4 只有 agree/downweight/veto）；市场级判决细分（spec 预留不实现）。

## 2. 已裁定的决策（负责人 2026-09-04 确认）

| # | 决策 | 理由 |
|---|---|---|
| D1 | 降级粒度**场次级**（spec §6.5 原文「该联赛」修订，见 §9） | 调用本按场次独立发起（§6.2）；一场超时不构成撤销同联赛已成功判决的理由；降级场次以「persona 未生效」如实入 A/B 账 |
| D2 | A/B **分轨 bankroll**：`paper_bankroll:model_only` / `:model_persona` 各惰性初始化 1000 | 对照实验隔离——一轨盈亏不影响另一轨仓位规模；与 §12.3 分轨判据（「model_persona 轨 ROI 显著优于 model_only 轨」）语义对齐 |
| D3 | personas 产出流程：**德甲打样**（`personas/bundesliga.md`）→ 风格确认 → 铺开其余 4 个（E0/SP1/I1/F1，spec §9.2 已定文件名） | 避免五个写完才发现风格不合 |
| D4 | 实跑验收**分层**：①persona 层对库内真实候选（M3 遗留 9 场）逐场真调 `hermes -z`（零额度）；②择比赛日一次完整 `fa run matchday --phase am`（-20 credits） | ①可控可重复验契约/降级/落库；②补运营真实形态证据，M3 同款验收风格 |

工程选型（A1/B1/C1，2026-09-04 确认）：

| # | 选型 | 备选与否决理由 |
|---|---|---|
| A1 | **value 同刻双落，persona 事后回填三列**：model_persona 轨初始 `verdict=NULL`、`final_stake_frac=kelly_stake_frac`（中性），persona 后 UPDATE 三列 | A2（persona 后落轨）两轨落库时刻不同、pm 价格漂移使 A/B 基线不一致；且 A1 正是 M3 接缝预留语义（`value.py` upsert「persona 三列不清」） |
| B1 | **串行逐场 subprocess**：3–8 场 × ≤120s 上界 ~16 分钟，am 报告最晚 11:20 出，无时延要求；一场失败天然隔离；日志按场干净 | B2 并发在 3–8 次量级上是 premature optimization，hermes 并发行为未实证 |
| C1 | **`HERMES_BIN` 可配 + fixture 脚本回放**：mock 与实跑同一代码路径（真实 subprocess 全保真），测试把 `HERMES_BIN` 指向 `tests/persona/fixtures/hermes_{ok,bad_json,timeout,nonzero}` | C2 monkeypatch `subprocess.run` 测不到真实 hermes 输出噪声（围栏、思考文本）——M3 实跑教训正是噪声存在 |

## 3. 模块结构（spec §9.2 既有规划，无新发明）

```
src/fa/persona/
├── caller.py     # hermes -z 子进程：HERMES_BIN 可配（默认 "hermes"）、超时 120s 可配、串行
├── contract.py   # 输入 JSON 组装（库内查 form/h2h）+ 输出提取（剥围栏/噪声，取首个合法 JSON）与 §6.3 校验
└── apply.py      # §6.4 映射落库、降级记账（runs.summary.persona）
personas/{epl,laliga,bundesliga,seriea,ligue1}.md   # 人格 + 领域知识 + 判断纪律；输出契约说明由 Python 侧统一拼接（§6.2），文件内不内嵌
```

prompt 组装（§6.2 字面）：persona 文件全文 + 该场输入 JSON + 输出契约说明。

输入 JSON 字段全来自库内：candidates 取该场 model_persona 轨全部 market 行；`form` = matches 表该队按日期降序近 5 场（跨赛季照取）；`h2h_recent` = 两队近 3 次交手（无则空数组）。候选 fixture 必然双侧已对齐（未对齐场次进不了 value 层），故 form/h2h 可查。

## 4. 数据流

**am**（管线顺序 value → **persona** → bet）：

1. value 层同刻双落：每个过门槛候选写两行（`model_only` + `model_persona` 同数字、后者中性初始）。门槛过滤按现有标准判，两轨候选同源。
2. persona 阶段对候选 fixture 去重后逐场：contract 组装 → caller 调用 → 提取校验 → apply 把判决 UPDATE 到该场 model_persona 轨**全部 market 行**（场次级判决广播）。映射：agree/downweight → `final_stake_frac = kelly_stake_frac × (1 + confidence_delta)`；veto → 全场 `final_stake_frac=0` 且 `confidence_delta` 强制置 0（§6.4/§6.3 语义，见 §5）。失败 → D1 场次级降级。
3. paper 落注改造：按 strategy 查各自轨 bankroll，`stake = final_stake_frac × bankroll(该轨)`；model_persona 轨 veto 行跳过（recommendation 保留、无 bets 行）。

**pm**：value 重跑 DO UPDATE 刷新两轨价格、判决三列不清（M3 接缝语义）→ 补跑判定：fixture 出现在**当日 am run** 的 model_persona 行中即「已尝试」（含降级场），不重调；仅新 fixture 补跑 → 落注去重键 `(fixture, market, strategy, mode)` 防重落。降级场当日不重试，次日新 run 重新判。

## 5. 降级路径（D1 场次级）

四类失败一种处置：**超时**（caller）/ **非 0 exit 或无额度**（caller）/ **剥不出合法 JSON**（contract 提取）/ **§6.3 校验失败**（contract）。处置：该场三列保持中性，model_persona 轨行为 = model_only；事件记 `runs.summary` 的 `persona` 键（called/ok/degraded：fixture + 失败类）；报告标注「persona 未生效 + 原因」。

校验语义裁定：

- **严格校验、不做静默修复**：downweight + delta≥0 等符号矛盾直接降级，不代 LLM 修号——A/B 实验对象必须是 persona 真实判断；不合规率本身是 M4 要实测的产出数字（spec §11 风险 #5）。
- 唯一例外 **veto 时 delta 强制置 0**：非修判断，是判决语义（veto 下 delta 无意义；§6.3「veto 时置 0」读作应用规则）。

## 6. 分轨 bankroll（D2）

- meta 键 `paper_bankroll:model_only` / `:model_persona`，各惰性初始化 1000（沿用现有模式）；`BANKROLL_KEY` 单键改 `bankroll_key(strategy)`，render/cli 引用同步。
- 迁移（惰性一次性，无需 schema 版本变更——meta 是 KV）：首次落注前发现旧 `paper_bankroll` → 复制为 `:model_only` 同值 → 删旧键。现有 14 注全 model_only，归属无歧义。
- 结算/统计全分轨：pnl 按 `recommendation.strategy` 回写对应轨；ROI、CLV 中位数、bankroll 按轨分组；`fa status` B 线栏两行——即 §12.3 预注册判据直接可读的表。
- `fa bet add` 注金基数按所挂 recommendation 的 strategy 查对应轨；live 注不碰 paper 账（现状不变）。

## 7. 报告渲染

- am 完整报告 persona 段，每候选场次：判决标识（✅ agree / ⚠️ downweight（含 delta）/ ⛔ veto）+ `key_factors` 逐条 + `report_md` 截 200 字（「精简版」裁定）；段尾汇总行如「persona 8 场：✅5 ⚠️2 ⛔1 · 未生效 0」。
- 降级场次：无 persona 内容，标注「persona 未生效（原因）」。
- pm 更新版：新增候选 diff 行带判决标识；盘口移动行不重复渲染 persona 点评（判决沿用 am）。

## 8. 测试与验收

测试布局（C1）：

```
tests/persona/
├── test_contract.py   # 输入组装；输出提取矩阵：纯JSON/围栏/噪声/空；校验矩阵：词表/值域/符号矛盾/条数字数边界
├── test_caller.py     # HERMES_BIN → fixture 脚本：ok/bad_json/timeout/nonzero 四场景；超时可配
├── test_apply.py      # §6.4 映射：乘法公式、veto置0、广播到全部 market 行、降级记账
└── fixtures/hermes_{ok,bad_json,timeout,nonzero}
```

`tests/pipeline/` 追加：value 同刻双落 + DO UPDATE 保三列；paper 分轨落注/veto 跳过/键迁移/分轨结算；matchday am 插层顺序、pm 补跑判定；render persona 段。

验收清单（`docs/m4-report.md`，实测风格同 M3）：

1. 全量测试绿
2. 实跑①（D4）：库内 9 场候选真调 hermes -z——产出真实不合规率/降级率
3. 实跑②（D4）：比赛日完整 `fa run matchday --phase am`（-20 credits）——双轨推荐/注落库、报告含 persona 段、分轨 bankroll、`fa status` 两行
4. spec 修订落库（§9）+ CLAUDE.md Hermes 声明修正（推理可用、TG 未配置——M3 报告遗留）
5. personas 5 文件入库（德甲打样在前）

前置探针（计划任务 0）：实测 `hermes -z` 工具集控制（`-t TOOLSETS` 形态、工具授权行为）——§6.1「白名单仅 web_search」的落地取决于 hermes CLI 实际能力，探针产出 caller 最终调用参数。**回退语义**：若实测无法严格限制工具集，则取可达到的最小集合并不启用自动授权（不用 `--yolo`），白名单落地程度如实记入 m4-report——降级为「已知限制」而非阻塞项。

## 9. spec 修订清单（随 M4 计划首个任务落，「改设计先改 spec」）

| 位置 | 原文 | 改为 | 依据 |
|---|---|---|---|
| §6.5 | 「**该联赛**自动回退纯模型推荐」 | 「**该场次**自动回退纯模型推荐」 | D1（2026-09-04 负责人确认） |
| §7.3（bankroll 记账处） | 未规定记账粒度 | 补一句：paper bankroll 按 strategy 分轨各记一本（初始各 1000），ROI/CLV/bankroll 按轨统计 | D2（同日确认）——§12.3 分轨判据的前提 |
