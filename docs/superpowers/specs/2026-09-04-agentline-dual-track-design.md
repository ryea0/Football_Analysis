# 双路对比线（线 P vs 线 A）设计文档

- 日期：2026-09-04
- 状态：待项目负责人批准
- 决策记录：项目负责人 2026-09-04 口头拍板——「agent 不当大脑（线 P）与 agent 当大脑（线 A）双路运行，比较学习；不设样本上限，长期并行」
- 关联：spec §1.4（agent 不当大脑）、§12（双线并存协议）、docs/m2-verdict.md（A 线回测基建现状）

## 1. 背景与动机

spec §1.4「agent 永远不当大脑」自 v0.1 起是**设计公设**，从未被实证检验。M2 判决 NO-GO 后，该公设进一步固化为架构纪律，但「agentic 范式在这类任务上到底差多少、差在哪一环」仍然没有数据。

本设计新增一条**范式对比线**：同一批比赛、同一信息基线、同一评测判据下，让确定性 Python 管线（线 P，agent 不当大脑）与全自主 dsh agent（线 A，agent 当大脑）长期并行预测，滚动对比。

预期收益无论方向：

- 线 A 大幅落后 → 铁律获得实证；M4 persona 的有界设计（只否决/降权/±0.15）被证明是正确收敛点
- 线 A 在某些环节（定性信息整合、盘口异动解读）接近或反超 → 该环节就是 M4 persona 应放权重的地方，必要时重划 §2.2 职责边界
- 增强层（线 A + web 检索）与基线层的差值 → 「定性信息值多少」的直接测量，是 M4 A/B 双轨的先导数据

## 2. 定位与结论分账

- 性质：**A 线（研究评测）的增量子线**，spec §12 分账协议的延伸——对比维度从「目标」（研究 vs 运营）扩展到「处理范式」
- 线 A 产出**永不进入** B 线推荐流与 paper 落注流（物理隔离，见 §6 落库设计）
- 线 A 无 DB 写权：只消费导出的信息集 JSON，预测结果由 Python 侧落库（§1.4「不直接碰数据库」对线 A 同样适用——它是被评测对象，不是管线组件）
- 真实下注依然禁止（§7.2），本设计不涉及任何下单通道

## 3. 总体形态

```
        比赛池（backtest_predictions 已覆盖场次 + 滚动新增）
                        │
        ┌───────────────┴────────────────┐
   线 P（现有，agent 不当大脑）        线 A（新，agent 当大脑）
   M2 walk-forward 预测已落库          dsh headless 每场独立会话
   backtest_predictions 表             拿信息集 JSON → 自主分析
                                      → 同契约预测 JSON
                        │                      │
                        └──────────┬───────────┘
                        统一评测（复用 backtest/metrics.py）
              log-loss / Brier / 校准 / 平注与 Kelly 模拟 ROI
                  市场基准 = Pinnacle 收盘价隐含概率（已存 mkt_* 列）
                                   │
                 agentline_predictions 落库（line 分账标记）
                  滚动对比报告 docs/agentline/compare-*.md
```

## 4. 线 A 边界：约束输出，不约束过程

「agent 当大脑」的实验纯度依赖两条规则：

1. **不约束过程**：不给固定分析步骤、不给管线中间产物（尤其不给 Dixon-Coles 概率——否则测的是「agent 修正模型」而非「agent 当大脑」）。agent 自主决定如何使用信息集、是否多轮推理。给它的只有：角色设定（足球量化分析师）+ 信息集 JSON + 输出契约说明
2. **约束输出**：必须落预测契约 JSON——这不是对 agent 的限制，是**评测可比性**的保障：

```json
{
  "p_home": 0.45, "p_draw": 0.28, "p_away": 0.27,
  "p_over25": 0.55,
  "confidence": 0.6,
  "reasoning_digest": "≤200 字摘要",
  "sources": [{"title": "...", "date": "...", "url": "..."}]
}
```

- 概率字段校验：非负、三项和 ≈1（±0.01 容差，超差按比例归一并在审计字段记录）
- `confidence` 为 agent 自报信心，落库供后续分析（如与实际命中率的相关性），评测指标不依赖它；`sources` 在基线层恒为空数组
- JSON 破损修复：dsh 环境装 dsh-translate（deterministic JSON repair，不造数据）兜底；修复后仍不契约 → 该场记 `parse_fail` 弃用（诚实降级，绝不脑补）
- 每场独立无状态会话（可重放、可审计）

## 5. 信息集与公平性（受控变量，两层）

| 层 | 线 P 信息 | 线 A 信息 | 度量什么 |
|---|---|---|---|
| 基线层 `A_base` | 历史赛果 + 赛前盘口（Dixon-Coles 建模原始输入） | **同一份**导出 JSON，无检索工具 | 纯范式对比 |
| 增强层 `A_enh` | 同上（线 P 无检索） | 同上 **+ web 检索**（伤停/新闻/动机） | 「+定性检索」的增量（M4 先导数据） |

### 5.1 信息集打包器（防泄漏的结构性保障）

`fa agentline export` 从 SQLite 按 match date 导出**该场赛前可见**的信息（结构上无泄漏）：

- 两队近 N 场赛果与基础统计（射门/射正/角球）
- H2H 历史交锋
- 当前赛季积分榜位置与近期走势
- 赛前盘口（Pinnacle 收盘价，与线 P 的 mkt_* 同源）

所有查询以 `date < match_date` 为界；导出 JSON 留档（重放凭据）。

**已知的时间戳不对称**：线 A 拿 Pinnacle 收盘价（临场视角），而线 P 的 walk-forward 预测建模于每周窗口开始（略早）。接受此不对称——收盘价对两线都是「赛前可见」，且它恰是评测的市场基准，线 A 不会因晚知而获得未被度量的信息优势；报告中如实标注。

### 5.2 增强层的泄漏风险（已知限制，如实标注）

历史回放时 agent 检索 web 可能命中**赛后**内容（搜索引擎返回赛后报道/比分）。缓解措施按序：

1. 检索 query 模板强制带赛前日期限定（`before:<match_date>`）
2. prompt 明令拒用任何赛后信息，输出契约要求 `sources[].date` 全部早于比赛日
3. `sources` 落库，`fa agentline compare` 抽样审计来源日期

即便如此，增强层结论仍标注「可能受泄漏污染、经抽样审计缓解」——不与基线层混排结论。若发现污染率不可接受，增强层降级为只在比赛日实时跑（彼时天然无赛后信息）。

## 6. 落库设计（新表，物理隔离分账）

不动现有表。新表 `agentline_predictions`：

| 列 | 说明 |
|---|---|
| id, match_id, league, season, date | 对齐 backtest_predictions，可 join |
| line | `A_base` / `A_enh`（线 P 仍在 backtest_predictions，天然隔离） |
| p_home, p_draw, p_away, p_over25, confidence | 契约输出（归一后） |
| raw_output | agent 原始返回全文（审计/重放） |
| sources_json | 增强层引用来源 |
| status | `ok` / `parse_fail` / `timeout` / `error` |
| repaired | bool，是否经 JSON 修复 |
| harness, model, duration_s | dsh 调用审计（版本、模型、耗时） |
| created_at | 写入时间 |

每次批量跑记 `agentline_runs`（样本筛选条件、dsh 版本、模型、成功/失败计数），与 runs 台账同风格。

## 7. 长期并行机制

- **不设样本上限、无终止判据**：滚动累积，报告随样本增长更新
- 起始覆盖 backtest_predictions 已有场次中分层抽样的子集（5 联赛 × 赛季 × 结果分布均衡），后续按需扩批——扩批是常规操作不是里程碑
- **比赛日实时双跑**（阶段 2，可选项）：线 A 在比赛日拿当日信息集跑预测，天然无泄漏、检验实时定性信息价值。不烧 Odds API 额度（复用线 P 已拉的盘口），只烧 LLM 额度；是否启用、何时启用由 M5 额度节流完成情况与回放阶段结论共同决定
- 成本量级：单场 ≈ 一次 headless V4 Flash 会话（信息集 ~数千 token 输入），百场量级成本个位数人民币；扩批前 `fa agentline runs` 可查累计

## 8. 评测设计

- 全部复用 `src/fa/backtest/metrics.py`：`log_loss` / `brier` / `calibration` / `evaluate` / `by_group`；`simulate.py` 的 `candidates` / `simulate_flat` / `simulate_kelly`
- 对比维度：线 P vs A_base vs A_enh vs 市场（mkt_* 隐含概率——**市场是所有评估的对照线**，§8.2）
- `by_group` 沿用联赛/赛季分组；新增「dsh 模型」分组维度（若未来换模型可比）
- 产出：`docs/agentline/compare-YYYYMMDD.md` 滚动报告（样本量、各线各指标、结论段落诚实标注增强层泄漏限制）

## 9. 代码落点与 CLI

```
src/fa/agentline/
├── export.py      # 信息集打包器（防泄漏查询 + JSON 留档）
├── runner.py      # dsh headless 调用器（subprocess，与 hermes -z 调用同构）
├── contract.py    # 契约校验 + 归一 + parse_fail 判定
└── compare.py     # 对比评测与报告渲染
```

CLI（typer，挂现有 app）：

- `fa agentline export --league ... --season ...` → 信息集 JSON 批量导出
- `fa agentline run --line A_base|A_enh --matches ...` → 调 dsh 跑批 + 落库
- `fa agentline compare` → 滚动对比报告
- `fa agentline runs` → 运行台账与成本摘要

dsh 侧 profile（`~/.dsh/` 内独立 profile）：headless + web 检索插件（A_enh 用）+ dsh-translate（JSON 修复）。**参数形态以本机实测为准**（项目惯例，同 telegram.py 定参做法）。

## 10. 前置 spike（开工第一项，半天）

唯一未验证的技术前提——dsh headless 调用契约：

1. `dsh --profile headless` 本机安装与一次性运行的输入/输出形态（stdin prompt / 文件 → stdout JSON？退出码语义？）
2. 单场信息集实测：契约成功率、耗时、token 成本
3. spike 产出写入本设计文档附录；若 CLI 契约不可行（无 headless JSON 输出），降级路径：dsh 官方 Python SDK（docs 提及的 SDK 模式），仍不可行则整体搁置重议

## 11. spec §12 增补草案（待批准后合入 spec.md）

> **§12.5 范式对比线（2026-09-04）**：A 线新增「agent 当大脑」对比子线（线 A）：dsh headless agent 对同批比赛做全自主预测，与线 P（确定性管线）共用评测判据长期并行滚动对比。线 A 只消费导出信息集 JSON、无 DB 写权；其产出落 `agentline_predictions` 表，永不进入 B 线推荐与落注流。基线层（无检索）度量范式差，增强层（+web 检索）度量定性信息增量，为 M4 persona 设计提供先导数据。「agent 不当大脑」由公设转为待实证命题。

## 12. 非目标（明确排除）

- 不做 GUI 终端 / watchlist（dsh-trading 形态另案讨论）
- 不做 dsh↔hermes 串联（MCP 拓扑对 fa 无增量）
- 不替换 hermes 的 TG 推送与 cron 调度
- 不给线 A 任何 DB 访问/落注/下单通道
- 不因线 A 结果改动 M5 额度节流的优先级（两者资源面正交：Odds API vs LLM 额度）

## 13. 风险与开放问题

| 风险 | 处置 |
|---|---|
| dsh 处于 developer preview，CLI 契约可能变动 | 调用器集中在 runner.py 单点；harness/model 审计字段落库 |
| 增强层历史回放泄漏 | §5.2 三级缓解 + 结论标注；不可接受则增强层移至比赛日实时 |
| agent 契约成功率低导致样本损耗 | parse_fail 如实计数入报告；成功率本身是实验数据（agentic 范式工程性的度量） |
| 开放：信息集窗口 N（近 10 场？近 20 场？） | spike 阶段定，原则：与 Dixon-Coles 训练窗口信息量可比，不偏向任何一线 |
| 开放：dsh 模型（Flash 起步，Pro 对照是否值得） | 先 Flash 单模型跑通，模型维度列为后续可选扩展 |
