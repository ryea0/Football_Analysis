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

---

# 附录 A：dsh headless 实测契约

> 本附录为 Task 2 spike 的终稿实测记录（2026-09-04 本机逐字实录），是 runner.py 参数形态的唯一权威；模型源裁定：火山方舟 ARK Coding Plan（plan/v3，模型 `ark-code-latest`），复用 Hermes 既有凭证，不用 DEEPSEEK_API_KEY。A.9 为 Task 9 首批 E2E 的回填。

## A.1 安装

```
$ node --version
v22.22.1                      # ≥20 满足
$ npm install -g @deepseek-ai/dsh
added 458 packages in 1m
$ dsh --version
0.1.1-rc.2
$ npm install -g pnpm         # 隐性前置依赖，dsh 不自带
$ pnpm --version
11.25.0
```

- 包：`@deepseek-ai/dsh@0.1.1-rc.2`（`latest`，MIT；`next: 0.1.2-rc.1`，2026-09-03 刚发版——生态迭代极快，**版本必须钉死**）
- npm prefix `~/.npm-global`（用户可写，无需 sudo）；bin：`~/.npm-global/bin/dsh`
- `@deepseek-ai/dsh-headless` 官方描述："The dsh one-shot bundle: a direct core Agent/Session runner over dsh-base with no Host, HTTP, or browser layer"

## A.2 headless 参数面（已实测确认 positional-arg）

`dsh --profile headless --help` 逐字实录：

```
Usage: dsh --profile headless [options] [task...]

Answer one task, print the final assistant message, and exit.

Arguments:
  task        the task text; multiple words are joined by spaces

Options:
  -h, --help  show this help
```

| brief 关切 | 实测结论 |
|---|---|
| prompt 怎么给 | **位置参数**（假设二 ✔）；多个 argv 词被 join，**必须作单个 argv 元素传入** |
| stdin（假设一 ✘） | **不读取**。`echo '…' \| dsh --profile headless` → exit 1，`error: a task is required, for example: …` |
| JSON/输出模式 flag | **不存在**。stdout 即 final assistant message（实测为裸 JSON 或混入 prose，见 A.5） |

其余可用面：`--dump-config` / `--dump-default-config`（**无需 key**，离线验证 profile 的唯一手段）、`--patch <path>`（可重复 overlay）、`$DSH_HOME`（默认 `~/.dsh`）、`DSH_TOOLS_MODE`（语义未验证）。

### 退出码表（真测，无管道污染）

| 场景 | exit | stdout | stderr |
|---|---|---|---|
| `--help` / `--version` | 0 | 帮助/版本 | — |
| 裸 `dsh`（缺 `--profile`） | 1 | 空 | `error: --profile <name> is required` |
| profile 不存在 | 1 | 空 | **Node 原始堆栈**（非干净消息） |
| 缺 task（stdin 被忽略） | 1 | 空 | 一行干净报错 |
| 缺 key | 1 | 空 | `dsh: MISSING_CREDENTIAL: …`（一行） |
| **成功**（实测 6 次） | **0** | 答案（纯净或混 prose） | **空** |
| plugin add 失败（404） | 1 | pnpm 详情**在此** | `dsh: pnpm failed in profile directory …` |
| plugin add 成功 | 0 | pnpm 输出 | — |

**runner 铁律**：判败以 returncode 为准；所有失败路径 stdout 为空（exit 0 但 stdout 空也要防御）；plugin 子命令的流方向相反。

## A.3 ARK provider 配置实录（模型源裁定落地）

**不需要 DEEPSEEK_API_KEY。** ARK plan/v3 两种协议都通（curl 实测 `/chat/completions` 与 `/responses` 均 HTTP 200，1.3–1.9s）；dsh 走 **`openai-completions`**。配置分两处，均**不含密钥**（`apiKeyEnv` 是引用，按请求从环境解析——这是 dsh 的设计约束，密钥不落 dsh 配置文件）。

**① `~/.dsh/settings.yaml`（新建，全局 settings 文档；`dsh-settings-file` 默认路径，外编辑热加载）**：

```yaml
llm-pi-ai:
  providers:
    ark:
      displayName: ARK Coding Plan (volces plan/v3)
      apiKeyEnv: ARK_API_KEY          # ← key 的环境变量名（不是 DEEPSEEK_API_KEY）
      api: openai-completions
      baseURL: https://ark.cn-beijing.volces.com/api/plan/v3
      compat:                          # 防 pi-ai 按 OpenAI 原生形态发请求被拒
        supportsDeveloperRole: false   # system prompt 走 system 而非 developer 角色
        maxTokensField: max_tokens     # 上限字段用 max_tokens 而非 max_completion_tokens
      models:
        - id: ark-code-latest
          name: ARK Code Latest
```

**② 每个 profile 的 `~/.dsh/profiles/<name>/cordis.patch.yml`（用户 patch 层）**：

```yaml
- id: agent-default-model
  config:
    provider: ark
    model: ark-code-latest
```

`dsh --profile fa-agent-base --dump-config` 验证合成结果为 `provider: ark / model: ark-code-latest`；`--dump-default-config` 确认 base 层仍为 `deepseek-official`（patch 只动用户层 ✔）。

**③ key 注入方式**：`export ARK_API_KEY=ark-12005144…` 于启动 dsh 的进程环境（来源：`/home/ryea0/.hermes/config.yaml` 的 `model.api_key`，Hermes 同源同凭证；该文件另见 `base_url`/`api_mode: codex_responses`——**Hermes 走 responses，dsh 走 completions，两者互不影响**）。仅 export 不写盘；fa 侧由既有 `.env` 加载层注入。

**试错记录**：无（一次配通）。曾担心的两点均不成立——plan/v3 接受 `max_tokens`+`system` 角色；`llm-pi-ai` 的 hand-declared route 直接吃 OpenAI 兼容网关。

## A.4 profile 与插件

profile = `~/.dsh/profiles/<name>/` 下 pnpm workspace；`package.json` 的 `dsh.profile.bundles` 决定加载层；`cordis.yml` 恒为 `[]` 勿动；`cordis.patch.yml` 是用户层。

**实建结果（`dsh plugin ls` 实录，均无需 key）：**

```
fa-agent-base:  bundles [dsh-base, dsh-translate, dsh-headless]
                deps: @deepseek-ai/dsh-headless@0.1.1-rc.2, dsh-translate@0.2.3
fa-agent-enh:   bundles [dsh-base, dsh-translate, dsh-free-search, dsh-headless]
                deps: 同上 + dsh-free-search@0.4.22
```

- **创建方式** = 首次 `dsh plugin --profile <name> add <pkg>`（报错信息自带此指令）；首个 add 的包自动进 bundles
- **`dsh plugin` 是 pnpm 薄转发**（其 `--help` 打印的就是 pnpm 帮助）
- `dsh-translate@0.2.3`（第三方，publisher perrylink）：`/translate` 命令 + **tools/post-execute JSON 修复层** + `fix_json` 工具——注意它修的是**工具调用**的 JSON，**不覆盖 final assistant message**（实测证实，见 A.5）
- web 检索：brief 写的 `dsh-free-web-search` 在 npm 不存在，实际为 **`dsh-free-search`**（10 引擎 keyless）；`--dump-config` 证实 patch 生效：`web` seam `searchProvider: ddg`。备选：`dsh-tavily@0.3.1`（keyless 默认）、官方 `@deepseek-ai/dsh-web-search-deepseek`（吃 DEEPSEEK_API_KEY，与裁定不符）

## A.5 三次（实为 6 次）冒烟结果

**样本比赛**（真实数据，`sqlite3 -readonly data/fa.db` 现查现组）：

```
Arsenal vs Newcastle  2024-02-24  E0 2023-24
Pinnacle 收盘 1.36 / 5.75 / 8.2   O2.5 1.45
info_set = {match, odds, home_recent[5], away_recent[5], h2h[5], standings[20 队]}
prompt = runner.py build_prompt 原文（逐字节采用），4349 bytes
```

**输入 token 实测**（同 prompt 直连 `/chat/completions`）：**prompt_tokens 2405**（completion 181；`model` 回显字段为 `auto`——ARK 网关路由，非模型自报）。

### Smoke A — JSON 纯净度（fa-agent-base）

```
$ dsh --profile fa-agent-base '只输出一个 JSON 对象：{"ok": true, "n": 1}，不要任何其他文字'
exit 0   4.7s   stdout = {"ok": true, "n": 1}\n   （21 bytes，cat -A 验证无隐藏字符）
stderr 空
```

简单指令下 **stdout 是裸 JSON，无任何包装/日志污染**。

### Smoke B — 单场信息集（fa-agent-base，5 次独立运行）

| run | exit | 耗时 | stdout 纯净 | 7 键齐全 | p_home/p_draw/p_away/O25/conf | digest 字数 |
|---|---|---|---|---|---|---|
| 1 | 0 | 31.7s | ✘ prose 混入（1689 字符 prose 包裹） | ✔(可恢复) | —/—/—/—/— | — |
| 2 | 0 | 39.0s | ✔ | ✔ | 0.71/0.17/0.12/0.62/0.72 | 272 |
| 3 | 0 | 18.4s | ✔ | ✔ | 0.70/0.18/0.12/0.74/0.72 | 266 |
| 4 | 0 | 9.7s | ✘ prose 混入 | ✔(可恢复) | —/—/—/—/— | — |
| 5 | 0 | 29.3s | ✔ | ✔ | 0.61/0.20/0.19/0.57/0.65 | 283 |

- **契约成功率：裸 JSON 3/5（60%）；加一层「提取大括号 JSON + json.loads」修复后 5/5（100%）**——两次失败均为「prose + 完整 JSON 对象」形态，正则 `\{[^{}]*"p_home"[^{}]*\}` 可完整恢复
- 耗时 **9.7–39.0s（中位 29.3s）**，全部 stderr 为空
- p_sum 全部 = 1.0；`sources` 全部 `[]`（未用信息集外信息，合规）
- **两处契约偏差**：`reasoning_digest` 272–283 字 > 要求的 ≤200 字（模型无视长度约束，靠截断兜底即可）；同比赛多次运行 p_home 在 0.61–0.71 间波动（**不可复现**；headless 无 temperature/seed flag，采样不可控）

### Smoke C — 增强层（fa-agent-enh，1 次）

```
exit 0   73.0s（base 中位 29s 的 2.5 倍）   stdout 454 bytes   PURE JSON ✔  7 键齐全
p 0.70/0.16/0.14/0.66/0.78   sources: []   stderr 空
```

profile 正常启动并产出合法 JSON。**但 `sources` 为空 ⇒ 本次运行未观察到检索触发**（控制样仅 1 次，且 ddg 出网是否被本机网络环境放行未单独验证）——**Task 6 验收前必须用强制检索的 prompt 复测一次**，若 ddg 不可达则切 `dsh-tavily`。

## A.6 已知陷阱清单（全部实测，runner 必须内建）

1. **挂起陷阱（最重）**：profile 缺 `@deepseek-ai/dsh-headless` bundle 时，任何调用（连 `--help`）**无限挂起零输出**（实测 120s 不退，手工 kill）。→ timeout 不可省（`_TIMEOUT_S=300` 合理，实测最慢 73s）
2. **argv 铁律**：prompt 必须单个 argv 元素、不经 shell；否则 word-splitting 破坏 JSON prompt
3. **判败以 returncode 为准**；所有失败 stdout 为空；plugin 子命令详情在 stdout（与主命令相反）
4. **pnpm 是隐性前置**：不装则 `dsh plugin` 全部不可用
5. **dsh-headless 必须钉版 `@0.1.1-rc.2`**：其 `latest` tag 腐化指向 0.0.1-rc.1（依赖已 404），裸装必败
6. **最终输出的 JSON 修复不能指望 dsh-translate**（只修 tool-call 层）；runner 需自带提取修复（实测 `大括号正则 + json.loads` 即可 100% 恢复）
7. **key 走环境变量名 `ARK_API_KEY`**（不是 DEEPSEEK_API_KEY）；`apiKeyEnv` 只存引用，密钥不进 dsh 配置文件
8. 采样不可控（无 temperature/seed flag）→ 同场多次运行概率有波动，paper 语义下可接受但需知晓
9. 每次调用（含失败）在 `~/.dsh/sessions/<cwd 混排>/` 留会话目录——批次长跑的磁盘残留与 prompt 是否落盘待 M4 前确认

## A.7 对 runner.py 的结论（Task 6 依据）

- **argv 假设成立**：`cmd = [_DSH, "--profile", profile, prompt]`（runner.py:49）与实测形态**完全一致，无需改**
- **常量核对**：`_DSH = "dsh"` ✔（`~/.npm-global/bin` 在 PATH）；`_TIMEOUT_S = 300` ✔（实测最慢 73s，挂起风险由 timeout 兜住）；`_PROFILE_LINE` 两个 profile 名与实建一致 ✔；`_ENH_SUFFIX` 的检索指令可正常拼入（Smoke C 用其原文跑通）
- **唯一实质缺口**：`run_headless` 返回的 stdout **不能直接当 JSON 解析**——需在解析层加「裸 JSON → 失败则大括号提取 → 仍失败才判契约违约」的三级兜底（实测可使成功率 60%→100%）
- **新增环境前提**：dsh 需 `ARK_API_KEY` 在 fa 进程环境中（subprocess 默认继承 fa 的 env，`.env` 加载层已具备）；另需 `pnpm` 在 PATH（装机一次性）
- 附录 A 的权威形态：`dsh --profile fa-agent-base "<单个 argv 的完整 prompt>"`，exit 0 + stdout，裸 JSON 率 60%/修复后 100%，耗时中位 29s（enh 73s），输入 ≈2400 tokens/场

## A.8 key 边界（本阶段遗留 → 已全部闭合）

原 no-key 阶段标注的未验证项现已全部实测：退出码 0 的成功形态 ✔、stdout 纯净度 ✔（裸或可恢复）、usage ✔（2405 tokens）、耗时 ✔、enh profile 启动 ✔。**仍开放的两点**（不阻塞 Task 6 开工，验收前闭合）：① enh 的检索是否真触发（需强制检索复测）；② `~/.dsh/sessions` 的落盘内容是否含完整 prompt（隐私面，M4 前确认）。

---

## A.9 首批 E2E 实测回填（Task 9，2026-09-04）

首批 10 场（E0 2023，`fa agentline export --n 10` 随机样本）双线真跑的实测三数字，
回填本附录的成本/成功率结论：

| 指标 | A_base（fa-agent-base） | A_enh（fa-agent-enh） |
|---|---|---|
| 契约成功率（裸 JSON，无需修复） | **10/10（100%）** | **10/10（100%）** |
| 单场耗时 | 6.1–70.3 s（均值 31.2） | 17.4–198.4 s（均值 61.4，约为 base 的 2 倍） |
| 输入规模 | 信息集 JSON ≈ 3.3 KB/场（≈ 2.4k tokens，见 A.5） | 同左 + 检索指令段 |
| `sources` 非空 | 0/10 | **0/10 —— 检索未触发** |
| `repaired`（经 JSON 修复） | 0/10 | 0/10 |

- 批量形态：`fa agentline run --line A_base|A_enh`，3 场先行验证后补满；全程 0 超时 / 0 错误 / 0 parse_fail，
  退出码语义（A.2 表）在编排链路上成立。
- **A.5「Smoke C sources 空」的观察在 10 场上确证为系统性现象**：增强层（dsh-free-search/ddg）
  在本机环境从未真正触发检索——A_enh 与 A_base 的差异目前只能归因于采样波动（A.6 陷阱 8），
  不能解读为「定性信息增量」。修复属后续任务（prompt 强化或换 dsh-tavily，本批未动）。
- 成本量级：20 场 headless 会话总耗时 ≈ 15.5 分钟（base 3 批共 ≈5.2 min + enh ≈10.2 min），
  与设计 §7「百场量级成本个位数人民币」的估算相容。
- 对比报告（首份）：`docs/agentline/compare-20260904.md`（n=10/线，市场为对照线）。
