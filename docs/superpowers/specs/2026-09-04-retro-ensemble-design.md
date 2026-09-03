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
| k=0 | 聚合行 status = `'error'`（统一值，杜绝失败态并列歧义；成员失败分布见成员行）；契约字段全 NULL |
| primary_tag | 成员 primary_tag 多数票；**无多数（三票各异）→ NULL**，digest 注明「成员无多数」 |
| miss_tags | **primary 一致成员（无多数时取全部可用成员）中**出现次数 ≥2 的标签集合（可为空集 → NULL）——计数域钉死为多数联盟，避免「联盟外成员的标签」混入聚合（勘误 2026-09-04 实施：原「出现≥2 次」未钉计数域，实施期裁定并回填本表） |
| model_vs_market | **与 primary 同进退**：primary 有多数时取成员多数票（平票取 NULL），primary 无多数 → NULL——聚合表达的是多数联盟的判断，无联盟即无 mvm（勘误同上） |
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
