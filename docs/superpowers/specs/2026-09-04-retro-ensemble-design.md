# retro ensemble（N-归因者投票）设计文档

- 日期：2026-09-04
- 状态：待项目负责人审阅（A/B 两批设计已口头照准，本文档为成文稿）
- 决策记录：总纲 2026-09-04-multi-agent-charter.md D1（Python 指挥官）；本线为其第一个实例
- 关联：2026-09-04-multi-agent-charter.md（总纲）、2026-09-04-retro-attribution-design.md（宿主 spec，§7 落库/§8 关卡继承）、docs/m3-report.md

## 1. 背景与定位

retro S0+S1 已合入 main（关卡 2「重跑一致性」要求同场双跑但尚未实现为产品能力）。本设计把关卡 2 升级为正式形态：**N 个独立归因者 + 确定性投票聚合**，同时产出一致性数据与聚合标签。它是多 agent 总纲的第一个实例：编排者是 Python（聚合规则预注册），归因者是有界专家。

## 2. 形态

- N=3（起步；`fa retro run --attributors N`，**默认 1 时行为与现行逐字节一致**，不产生聚合行）
- 每成员 = **同一 ROLE_PROMPT、同一信息集**的独立无状态 hermes 会话（三次独立调用）
- v1 **刻意不做**异构 prompt / 异构模型成员——不引入视角变量；异构归因者是 gated-2 后扩展位（总纲 §9）
- 已知统计限制（如实入报告）：同一 LLM 的 N 次采样非独立样本，一致性数值的解释受此限

## 3. 落库（迁移 v5，纯加法）

```sql
ALTER TABLE retro_attributions ADD COLUMN attributor INTEGER NOT NULL DEFAULT 1;
```

- 无 CHECK 故可安全 ADD COLUMN（「词表一次到位」教训只约束带 CHECK 的列）；存量行自动 attributor=1
- **成员行**：attributor=1..N，契约+审计字段照常
- **聚合行**：attributor=0，仅当 N≥2 时产生

`SCHEMA_VERSION` 4→5；`_migrate_up` 追加 `ALTER TABLE`（新建路径 DDL 同步加列，维持单源——注意：新建 DDL 与 ALTER 语句不同构，单源化方式为「新建 DDL 含列 + 迁移用 ALTER」，两条路径以同一列定义常量注释锚定）。

## 4. 聚合规则（全部确定性，预注册）

输入 = N 个成员行中 status='ok' 的契约输出。可用成员数 k。**聚合行由 Python 构造，不经 `validate_output`**（契约校验只作用于成员的 LLM 输出；聚合行的质量由成员行与规则共同保证）：

| 字段 | 规则 |
|---|---|
| k=0 | 聚合行 status = `'error'`（统一值，杜绝失败态并列歧义；成员失败分布见成员行）；契约字段全 NULL——**唯 digest 例外**：携带诊断文案「全员失败（k=0），见成员行」（勘误 2026-09-04 E-T3 审查：错误行的诊断信息有检索价值，接口 digest 本为 str） |
| primary_tag | 成员 primary_tag 多数票；**无多数（三票各异）→ NULL**，digest 注明「成员无多数」 |
| miss_tags | **primary 一致成员（无多数时取全部可用成员）中**出现次数 ≥2 的标签集合（可为空集 → NULL）——计数域钉死为多数联盟，避免「联盟外成员的标签」混入聚合（勘误 2026-09-04 实施：原「出现≥2 次」未钉计数域，实施期裁定并回填本表） |
| model_vs_market | **与 primary 同进退**：primary 有多数时取成员多数票——**计票域=全部可用成员**（表达全场共识方向，与 miss_tags 的联盟域不同；平票取 NULL），primary 无多数 → NULL——聚合表达的是多数联盟的判断，无联盟即无 mvm（勘误同上；计票域钉定 2026-09-04 E-T2 审查） |
| tags_confidence | 成员值中位数 |
| evidence | primary_tag 与聚合 primary 一致的成员（无多数时取全部成员）证据按 URL 去重并集 |
| digest | primary_tag 与聚合一致且 attributor 序最小的成员的 digest；无多数 → 固定文案「成员无多数（<各成员 primary>），见成员行」 |
| status | k≥1 时 'ok'（无多数也是 ok——无多数由 NULL 字段表达，**不新增 status 值**：status CHECK 词表已进生产不可后补） |

## 5. 一致性测量（关卡 2 正式形态）

新命令 `fa retro consistency [--batch-id]`：

- 三档计数：全同（N/N 同 primary）/ 多数（≥2 同）/ 无多数
- primary_tag 一致率（全同率 + 多数率分开报，不合并成单数字）
- 输出与 `retro_runs` 台账联动（批内一致性）

**预写解读规则**（承袭 retro spec §8-2 与总纲 §2）：

- 全同率 <50% → 归因线维持「假设生成器」降格，聚合标签不得作分层依据
- 多数率与全同率之差 = 「弱共识带」——该带场次是分歧-错误相关性分析的主要样本
- 分歧率与成员错误率（vs 市场）的相关性 → 总纲 §3 混合门控的准入数据

## 6. audit 扩展

成员行照常审计；聚合行 evidence 为并集，逐条按日期规则判定（与现行 `audit_batch` 同规则，无需新逻辑——聚合行天然被现有 SQL 覆盖，miss_tags_json 非空且含赛前成因标签时受检）。

## 7. CLI 与成本

- `fa retro run --attributors 1|3`（默认 1）；`fa retro consistency`
- `retro_runs.params_json` 记 attributors 值
- 成本 = 现行 ×N；E2E 实测基线 11.8s/场 → N=3 约 35s/场（顺序调用；并行调用不进 v1——subprocess 并发是优化不是能力，YAGNI）

## 8. 测试策略

- 聚合规则单测：多数/无多数/中位数/URL 去重/序最小 digest/k=0 失败态聚合
- 迁移 v5 单测：v4 库 ALTER 后存量行 attributor=1、新库 DDL 与迁移同构
- N=1 向后兼容：不产生聚合行、现有全量测试零改动通过
- consistency 三档计数单测（构造已知成员行）
- runner 复用无改动（编排发生在 pipeline）

## 9. 非目标

- 不做异构 prompt/模型成员、自动加权（gated-2 扩展位）
- 不做并行调用优化（v1 顺序）
- 不改聚合规则之外的任何 retro 语义（选择器/信息集/audit 不动）
- 聚合标签进分层分析（`fa retro analyze`）仍属 S2 计划，本设计只负责产数据

## 10. 风险与开放问题

| 风险/开放 | 处置 |
|---|---|
| 成本 ×3（周批 20-40 场 → 60-120 次调用/周） | ark 额度可忽略（E2E 基线外推）；台账 runs 记累计 |
| 同模型采样相关性 → 一致性虚高或虚低 | 报告标注统计限制；异构成员是后续扩展 |
| 无多数比例可能很高（承袭 LLM 归因随机性风险） | 这本身就是关卡 2 要测的数据；预写降格规则兜底 |
| 开放：N 是否固定 3 | v1 固定 3（CLI 参数留给实验但文档口径以 3 为准）；N>3 的聚合规则（出现≥2 次）对更大 N 语义不变 |

## 11. E2E 实测（2026-09-04，N=3 单场真机）

**口径先行（必须与数字一起读）**：

1. **k=1 批混入全同率**（E-T4 审查遗留提醒）：`fa retro run` 默认 `--attributors 1`（含现行单跑）产生的批，每场只有 1 个成员行，三档判定恒为「全同」——会把全同率向 100% 拉偏。当前 consistency 输出**不区分** k=1 批与真 ensemble 批，读全同率前必须先确认批的 `params_json.attributors` 与成员行数。
2. **本批实际 k=2，不是 3**：本批成员 1 `parse_fail`，三档里这场是 **2/2 全同**，不是 3/3。consistency 只统计 `status='ok'` 的成员行（`analyze.consistency_report`），成员失败从分母里掉出去后，全同判定口径随之变窄——N=3 下一票失败的「全同」证据强度低于 3/3。
3. **批号非全局**：本实测跑在 worktree 快照库（主库 v3 快照 → `fa init` 升 v5），`batch_id=1` 与主仓活库的 batch #1（3 场 S0+S1 首批，契约成功率 2/3、单场 ≈11.8s）**不是同一批数据**。跨库比较必须按「库 + batch_id」，不能只看 batch_id。
4. **台账 `n_parse_fail=0` 是聚合口径**：N≥2 时台账按聚合行计（`n_ok`=聚合可用场数、`n_error`=全员失败场数），成员级失败只体现在成员行 status 与 consistency 的「成员失败行」计数——只看台账会得出「零失败」的乐观误读。

**实测（batch #1，worktree 快照库，match_id 34180 = 2022-01-07 D1 Bayern Munich vs M'gladbach，div=+0.7598 全库 top1）**：

| 项 | 实测 | 备注 |
|---|---|---|
| 一次跑成，未重跑挑结果 | 是 | 诚实条款 |
| 三档分布 | 全同 1 / 多数 0 / 无多数 0（n=1 场） | 实际可用 k=2（见口径 2） |
| 成员失败行 | 1（attributor=1，`parse_fail`） | 失败原因未落库（无 reason 列），无法区分空输出/JSON 破损 |
| 一致率 | 100%（全同+多数，n=1） | 单场样本，无统计意义，只作管线可用性证据 |
| 聚合行（attributor=0） | primary=`variance`、miss_tags=`{model_limitation, variance}`、confidence=0.65（0.6/0.7 中位数）、mvm=`model_wrong`、digest=成员 2 的 digest（primary 一致中序最小）、evidence=空并集 `[]`、status=`ok`、repaired=0 | 与 §4 规则逐字段核对一致 |
| 单场耗时 | 总 31.2s（成员 13.9 / 9.1 / 8.2s，顺序）；聚合行 duration_s=31.2 | 与 §7 预估 ≈35s 同带 |
| 契约成功率 | 成员级 2/3=67%（与上轮基线 2/3 同）；聚合级台账 1/1 ok | 两口径并存，见口径 4 |
| audit | 检查 0 行，违规 0 | 本批 miss_tags 仅 `variance`/`model_limitation`，均非赛前成因标签（PREMATCH_CAUSE_TAGS），且证据本为空 → 不受检，非违规率 0 的证据 |
| 落库 | 4 行 = 成员 1/2/3 + 聚合 0；成员 3 `repaired=1`（JSON 截取修复后过契约） | attributor 列迁移后 DEFAULT 1 未影响本批 |
| 输入留档 | `data/retro/inputs/batch-…/34180.json`（data/ gitignore，不进 git） | |

**结论（如实，不外推）**：管线端到端可用（跑通、落库、聚合规则实测正确、全量 523 passed）。**一致性数字本批不可用作关卡 2 结论**——n=1 且 k=2；「三票各异」的极端信号本批未出现（两票一致 + 一票契约失败），不能据此判断随机性高低。关卡 2 需要的数据 = 多场、多批、成员失败率已知的 ensemble 批，且解读前先做口径 1/2 的检查。
