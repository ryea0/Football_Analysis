# 复盘归因子线（retro）设计文档

- 日期：2026-09-04
- 状态：待项目负责人审阅（方案与两批设计已口头照准，本文档为成文稿）
- 决策记录：项目负责人 2026-09-04 本会话拍板——复盘归因立项；消费者四端全选（A 线研究回流 / M4 persona 输入 / 运营报告段落 / 线 A 案例库）；节奏四模式全选（分歧周批 / 线 A 对齐 / paper T+1 / 手动）；方案 1（独立复盘线 + hermes 起步 + 分层交付）照准；结构层与纪律层两批设计照准
- 关联：spec §4.5（伤停盲区与 persona 补偿）、§8（评估）、§12（双线并存协议）、§12.5 草案（范式对比线，docs/superpowers/specs/2026-09-04-agentline-dual-track-design.md）、docs/m2-verdict.md

## 1. 背景与动机

现有评测回答「差多少」（M2：劣化 +3.02%），双路线将回答「agent 范式差多少」；但「**为什么错**」没人回答。M2 报告的四重稳健性检查是一次性的定量检验；定性维度（伤停/动机/轮换/新闻——spec §4.5 定义的模型盲区）缺一条常态化的归因产线。

本设计新增 **A 线复盘归因子线（retro）**：赛后对选定场次做定性归因，产出**结构化标签假设**回流 A 线分层验证。它与双路线（线 A）互为补充：双路线测「agent 当大脑的预测力」，retro 测「agent 当分析师的归因力」——两者都受同一纪律约束：结论可以是「无用」。

## 2. 定位与结论分账

- 性质：**A 线（研究评测）的增量子线**，spec §12 分账协议的延伸
- retro 产出**永不进入** B 线推荐流与 paper 落注流（结构性隔离：无任何 retro→recommendations 代码通路；线 A 案例库供给是 Stage 3 的显式版本化通道，另行走门）
- 复盘 agent 无 DB 写权：只消费导出的归因信息集 JSON，落库由 Python 侧执行（§1.4 对本线同样适用）
- 真实下注依然禁止（§7.2），本设计不涉及任何下单通道

## 3. 总体形态

```
场次选择器（四模式，§4）
        │
   归因信息集导出（JSON 留档 = 重放凭据）
        │
   runner：hermes -z headless，角色=赛后复盘分析师
   （无状态单场会话，可重放可审计）
        │
   契约校验（标签枚举 / 证据日期 / parse_fail 判定）
        │
   retro_attributions 落库（Python 侧）
        │
 ┌──────┼──────────┬─────────────┐
 A 线分层重析    日报摘要段落   M4 persona / 线 A 案例库（后挂）
(fa retro analyze) (report 渲染)  (Stage 3，版本标记)
```

核心原则：**agent 解释，不计算**。分歧数值（log-loss 差、概率差）由 Python 算好放进信息集；agent 产出的只有定性标签、定性判断（谁错）与证据。

## 4. 场次选择器（四模式）

| 模式 | 选法 | 节奏 | 上线 |
|---|---|---|---|
| `divergence_weekly` | 每周模型 vs 市场分歧 top-K 场 + 分层抽样命中对照场（病例-对照设计，否则分不清「所有场都有的因素」与「真正致错的因素」） | 周批 | Stage 1 |
| `paper_t1` | 前一比赛日 recommendations 所涉场次（赛果取 matches 表） | 比赛日次日 | Stage 2 |
| `agentline_aligned` | 与双路线回放批同批（直接回答「线 P 错在哪 vs 线 A 错在哪」） | 随双路线批 | Stage 3 |
| `manual` | 手动圈选（--league/--season/--matches/--date-range） | 按需 | Stage 1 |

对照抽样的 K 值与对照比例（建议起点 top-20 + 对照 10）在 Stage 0 用真实数据定并写入本档附录；抽样必须 seed 可复现。

## 5. 归因信息集

每场一个 JSON，内容：

1. **赛前信息摘要**：两队近 N 场赛果与基础统计、H2H、积分榜位置与走势、赛前盘口（Pinnacle 收盘）——查询一律 `date < match_date`，与双路线信息集同防泄漏模式
2. **三方预测**：模型概率（backtest_predictions 或当日 run）、市场隐含概率（mkt_* 同源）、paper 注（如有）
3. **分歧摘要**（Python 预计算）：模型 vs 市场概率差、单场 log-loss 差
4. **赛果与结算**：比分、盘口结算结果——归因是赛后视角，**看结果是合法的**（这是与线 A 预测任务的本质区别）

## 6. 归因契约（输出 JSON）

```json
{
  "miss_tags": ["injury", "motivation"],
  "primary_tag": "injury",
  "tags_confidence": 0.7,
  "model_vs_market": "model_wrong",
  "evidence": [{"title": "...", "date": "...", "url": "..."}],
  "digest": "≤200 字中文摘要"
}
```

- **标签封闭枚举 v1（8 个）**：`injury`（伤停）/ `rotation`（轮换欧战）/ `motivation` 保级争冠无欲 / `congestion`（赛程密度）/ `news`（更衣室突发）/ `market_info`（市场掌握未公开信息）/ `model_limitation`（无外部因素的系统性盲区）/ `variance`（正常波动，不归因）
- 落库带 `tag_set_version`；枚举未来变更不污染历史分层
- `model_vs_market` 四选一：`model_wrong` / `market_wrong` / `both_off` / `variance`
- 校验：miss_tags ⊆ 枚举且非空；primary_tag ∈ miss_tags；tags_confidence ∈ [0,1]；digest ≤200 字；evidence 条目含 date；不合规 → `parse_fail`
- **刻意不做** counterfactual（「若无伤停会怎样」）——那是幻觉发生器
- 角色 prompt 以 runner 模块常量起步；外置化等有迭代需求再做（不与 M4 的 `personas/*.md` 联赛人格纠缠）

## 7. 落库设计（新表，物理隔离分账）

不动现有表。新表 `retro_attributions`：

| 列 | 说明 |
|---|---|
| id, match_id, league, season, date | 对齐现有预测表，可 join |
| batch_id, selector | 批次与选择器模式 |
| miss_tags_json, primary_tag, tags_confidence, model_vs_market | 契约输出 |
| evidence_json, digest | 证据与摘要 |
| status | ok / parse_fail / timeout / error |
| repaired | bool，是否经 JSON 修复 |
| harness, model, duration_s | 调用审计 |
| input_pack_path | 信息集 JSON 留档路径（重放凭据） |
| tag_set_version | 标签集版本 |
| created_at | 写入时间 |

每次批跑记 `retro_runs` 台账（选择器、筛选参数、成功/失败计数、耗时/成本摘要），与 runs/agentline_runs 同风格。

## 8. 验收关卡（诚实检验）

1. **证据审计**（防编造）：`fa retro audit` 抽样检查 `evidence[].date`——赛前成因类标签（injury/rotation/motivation/congestion/news）引用来源必须早于开球；market_info 可引盘口数据；model_limitation/variance 允许零证据。违规率进报告
2. **重跑一致性**（防随机叙事）：同场独立双跑 → primary_tag 一致率。v1 **先测不设门槛**，一致率本身即「LLM 归因可靠性」的第一手数据；解读规则预先写死：**若 <50%，retro 降格为「假设生成器」，不得作为分层依据**
3. **预测效度**（真检验）：`fa retro analyze` 按 miss_tags 分层重算模型 vs 市场 log-loss 差——标签层差距明显大于无标签层 → 标签有信息量；各层无差 → **归因是叙事不是科学**，如实报告。v1 报点估计与每层样本量（小样本不装精确）；显著性检验方法随 analyze 实现定并写回本档。允许最终结论是「复盘归因无用」（spec「诚实检验而不是信仰」对本线同样生效）
4. **parse_fail 如实计数**：契约成功率本身是实验数据

## 9. 错误处理与降级

- hermes 超时 / 返回破损 / 枚举外标签 / 超长 → 该场 status 落库，批不中断
- 无归因记录的场次在 analyze 侧按缺失处理，报告**显式标注选择偏差**（分歧优先抽样天然有偏，命中对照场为对冲而设）
- retro 不直接推 TG：日报摘要走现有 report 渲染，推送失败沿用 M3 降级路径（记 summary、不中断）
- 复盘 agent 无 DB 写权、无落注通路（结构性，不是约定）

## 10. 分期交付

| Stage | 内容 | 依赖 | 成本 |
|---|---|---|---|
| 0 | 选择器 + 纯 SQL 分歧报告（`fa retro report`，零 LLM） | 无（M2 库已在） | 零 |
| 1 | runner + contract + 周批（含命中对照）+ manual + audit | hermes（本机已验证） | 周批 20-40 次 headless 调用，ark 现有额度可忽略 |
| 2 | paper_t1 挂 M3 paper 流次日 + 日报段落 | M3（已完成） | +日若干次 |
| 3 | agentline_aligned + 案例库供给线 A | 双路线 spike 完成 | 案例库带信息集版本标记（如 A_enh_v2）；**A_base 永不供给**（否则范式对比被追认数据污染） |

## 11. 代码落点与 CLI

```
src/fa/retro/
├── select.py     # 四模式选择器（seed 可复现分层抽样）
├── export.py     # 归因信息集打包（防泄漏查询 + JSON 留档）
├── runner.py     # hermes -z headless 调用器（subprocess，与 telegram.py 定参同惯例）
├── contract.py   # 契约校验 + parse_fail 判定
└── analyze.py    # 分层重析（复用 backtest/metrics.py）+ 报告渲染
```

CLI（typer，挂现有 app）：

- `fa retro run --selector ... [--matches ...]` → 选场、导出、调 hermes、落库
- `fa retro report [--selector ...]` → Stage 0 起可用的分歧报告（纯 SQL）
- `fa retro audit` → 证据日期抽样审计
- `fa retro analyze` → 按标签分层重析 + 预测效度结论
- `fa retro runs` → 批台账与成本摘要

## 12. 测试策略

- contract：枚举/主标签从属/日期规则/长度/parse_fail 单测
- select：分层对照抽样 seed 可复现性单测
- export：防泄漏（`date < match_date`）单测
- analyze：分层 log-loss 对照手算 fixture 单测
- runner：mock 单测 + hermes -z 本机实测一条真链路（项目惯例，先实测定参再固化）

## 13. spec §12.6 增补草案（待批准后合入 spec.md）

> **§12.6 复盘归因子线（2026-09-04）**：A 线新增复盘归因子线：赛后对选定场次（分歧周批含命中对照 / paper T+1 / 线 A 对齐 / 手动）做定性归因（封闭标签枚举、`tag_set_version` 版本化），产出落 `retro_attributions` 表，永不进入推荐与落注流。验收三关卡：证据日期审计、重跑一致性测量（<50% 降格为假设生成器）、按标签分层的预测效度检验（允许结论为「归因无信息量」）。分期：统计分歧报告（S0）→ hermes 周批（S1）→ paper T+1（S2）→ 线 A 案例库（S3，版本标记供给，A_base 永不供给）。

## 14. 非目标（明确排除）

- 不做 counterfactual 推演
- 不给复盘 agent 任何 DB 写权 / 落注 / 下单 / 推送通道
- retro 产出不进 B 线推荐流（案例库供给线 A 是唯一对外门，且版本化）
- 不在本线内实现 M4 persona 或双路线（各自独立立项）
- Stage 0-2 不改 spec §1.4 / §2.2 的任何职责边界

## 15. 风险与开放问题

| 风险/开放 | 处置 |
|---|---|
| 重跑一致性可能很低（LLM 归因随机性） | 关卡 2 预写降格规则；低一致率本身是诚实数据，报告如实呈现 |
| 分歧优先抽样的选择偏差 | 命中对照场设计 + analyze 侧显式标注覆盖率与偏差 |
| hermes prompt 角色 drifted | prompt 模块常量 + input_pack 留档可重放；harness/model 审计字段落库 |
| 开放：K 值与对照比例（建议 top-20+对照 10） | Stage 0 用真实数据定，写回本档附录 |
| 开放：retro/export.py 与 agentline/export.py 查询模式重复 | 先独立实现同模式；两边都落地后提取共享（不阻塞，避免跨 worktree 依赖） |
| 开放：paper 注场次赛果结算口径 | Stage 2 前核对 M3 结算路径，赛果以 matches 表为准 |
