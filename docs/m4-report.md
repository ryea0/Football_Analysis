# M4 验收报告（persona 接入）

- 日期：2026-09-04 起
- 代码：branch `worktree-m4-persona-design`
- 本报告所有数字均为实跑测得，无推算值

## 0. 探针：hermes -z 工具集控制（§6.1 白名单落地）

**结论行（Task 5 `build_command` 据此落定）**：

```
build_command = ["hermes", "-z", <prompt>, "-t", "search"]     # 不带 --yolo
```

`-t` 可用且确实收窄工具集，但**合法 toolset 名是 `search`（= 仅 `web_search` 一个工具），不是 `web_search`**——brief 原文命令 `-t web_search` 会 exit 2（该名字不在 hermes 合法集合内，且 `-z` 后紧跟 `-t` 还会先在 argparse 处失败）。spec §6.1 写的是工具名（`web_search`），CLI 落地在 toolset 粒度，两者恰好一一对应：`TOOLSETS["search"] = {"tools": ["web_search"], "includes": []}`（`toolsets.py:104`）。

### 0.1 实测记录（hermes v0.19.0，6 次调用 / 其中 3 次真实推理）

| # | 命令 | exit | 输出（尾部） |
| --- | --- | --- | --- |
| 1 | `hermes -z -t web_search '<prompt>'`（brief 原样） | 2 | `hermes: error: argument -z/--oneshot: expected one argument`（argparse 失败，未到工具集校验，**无 API 消耗**） |
| 2 | `hermes -z '<prompt>' -t web_search` | 2 | `hermes -z: ignoring unknown --toolsets entries: web_search` + `hermes -z: --toolsets did not contain any valid toolsets.`（**无 API 消耗**） |
| 3 | `hermes -z '<prompt>' -t search` | 0 | `{"probe": "search-only"}`（stdout 恰为最终文本一行） |
| 4 | `hermes -z '<prompt>'`（默认，无 `-t`） | 0 | `{"probe": "default-tools"}`；`--usage-file`：`input_tokens=17571, output_tokens=7, api_calls=1, model=ark-code-latest` |
| 5 | `hermes -z '<问模型你有哪些工具>' -t search` | 0 | `["bash", "view_files", "skill_view", "write_to_file", "search_by_regex", "cronjob", "hermes_admin"]` ——**真假混报**（见 0.4） |
| 6 | `hermes -t web_search -z '<prompt>'`（`-t` 前置） | 2 | 同 #2 → 证明 `-t` 前置的**参数顺序可解析**，#1 失败纯属工具集名非法 |

### 0.2 免费实测与源码判据（判定依据，非猜）

- `hermes tools list`：cli 平台当前 `web / browser / terminal / file / code_execution / vision / image_gen / tts / skills / todo / memory / session_search / clarify / delegation / cronjob` 均 enabled（exit 0）。
- `hermes prompt-size --platform cli --json`（离线，无 API）：默认档 **tools count = 27，tool-schema JSON = 49,010 bytes**（口径统一：离线枚举不传 toolsets 时为 **30**，其中含 `tool_call / tool_describe / tool_search` 三个 meta 工具，feishu 系等内置集成工具经 `tool_search` 延迟暴露、受 env 门控）。
- 离线工具枚举（hermes 自带 venv 调 `get_tool_definitions(enabled_toolsets=...)`，与 CLI 同一注册表）：`['search']` → **0 个工具**；`['web']` → 0 个；不传 → 30 个（含 `terminal / write_file / patch / execute_code / delegate_task / process` 等）。为 0 的原因：`web_search`/`web_extract` 的 `check_fn=check_web_api_key` 当前为 False。
- 合法 `-t` 取值 = `toolsets.py` `TOOLSETS` 的 57 个键（`web`、`search`、`file`、`terminal`、`coding`、`safe`、`hermes-cli`…）+ `all`/`*` + 已配置的 MCP server 名。本机 `config.yaml` 无 `mcp_servers`、无 `~/.hermes/plugins` 目录（无插件/MCP 扩名），但内置集成工具随 env 启用（`TELEGRAM_BOT_TOKEN`、`FEISHU_APP_ID/SECRET` 已配置）。
- `hermes_cli/oneshot.py`（`-z` 路径）三处硬事实：① 显式 `--toolsets` 未知项直接丢弃并告警，全部非法则 exit 2；② `-z` **无条件** `os.environ["HERMES_YOLO_MODE"] = "1"` 且 `HERMES_ACCEPT_HOOKS = "1"`——审批全绕过，与是否传 `--yolo` 无关；③ stdout 只回显最终文本，运行期 stderr 被重定向丢弃（仅参数校验告警可见）。
- 搜索后端：`hermes status` 中所有搜索类后端 ✗（Tavily / Firecrawl / OpenRouter / xAI / OpenAI 等均未设）。`~/.hermes/.env` **并非全注释**——实测 18 行非注释 KEY=VALUE（`TELEGRAM_BOT_TOKEN`、`FEISHU_APP_ID`/`SECRET`、`TERMINAL_*`、`BROWSER*` 等，值非空），但其中**无任何搜索后端 key**；firecrawl 仅以注释模板 `# FIRECRAWL_API_KEY=` 存在（grep 实证）。

### 0.3 判定与 caller 形态

1. **`-t` 能收窄工具集**（判定标准前半成立）→ `build_command` 带 `-t search`，不带 `--yolo`（`-z` 本就 yolo，传了也无增量语义）。
2. 参数顺序约束：`-z` 的下一个 token 就是 prompt，因此**不能写 `-z -t xxx`**；安全写法 `hermes -z <prompt> -t search`（实测 #3）或 `hermes -t search -z <prompt>`（顺序可解析，#6）。
3. **必须固定带 `-t`**：不带时（#4）从代码目录跑 `-z` 暴露 ≥27 个工具（prompt-size 实测 27；离线枚举含 meta 工具 30：`terminal / write_file / patch / execute_code / delegate_task`…）且 yolo 全放行——web 内容注入可直接触达本机执行，这是 §6.1「禁其余一切工具」要挡掉的面。
4. **禁止 `-t all` / `-t *`**：源码显式语义为「enables every toolset」（其余条目被忽略并告警）。
5. 显式 `-t` 优先于 coding posture / 平台默认（`coding_context.py`：posture 仅在「no explicit toolset given」时折叠 toolset）。

### 0.4 已知限制（如实记）

- **web_search 当前不可用**：无任何搜索后端 key → `-t search` 的实际工具集是**空集**（比 §6.1 更严：连 web_search 也没有）。人格将纯靠 contract 输入产出，§6.1 的「伤停/新闻查证」用例暂不可用；若要启用，需配置后端（如 Tavily / Firecrawl key），配好后 `-t search` 恰好等于「仅 web_search、禁其余一切」。
  **（勘误：本条「空集」表述被 T9 冒烟实证推翻，见 §0.6）**
- **模型自报工具列表真假混报**（#5）：7 个自报名字中 **2 个真实**（`skill_view`——在默认 30 工具枚举中；`cronjob`——注册于 `tools/cronjob_tools.py:935`），**5 个编造**（`bash / view_files / write_to_file / search_by_regex / hermes_admin` 不在任何内置 toolset/注册表中，本机也无 MCP server / plugins 目录可提供）。真假并列比纯幻觉更危险——**验收不得用「问模型有哪些工具」**，只能以 CLI 校验 + 源码/离线枚举为准。
- 运行期 stderr 被 `-z` 丢弃：persona 调用的诊断信息（如后端不可用告警）不会回传 caller，caller 只能靠 exit code + stdout 判定。

### 0.5 成本

6 次 hermes 调用，其中 3 次真实推理（#3/#4/#5）。实测数字仅 #4（带 `--usage-file`）：`input_tokens=17571 / output_tokens=7 / api_calls=1`；#3/#5 未带 `--usage-file`，其 token 数**未单独测得**（Task 15 实跑补测）。额度消耗量级可忽略——此为与 CLAUDE.md 前提一致的定性判断，非实测。

### 0.6 勘误（2026-09-04 T9 冒烟实证）：`-t search` 不是空集，模型会真实发起 web_search 调用

§0.2 的「`['search']` → **0 个工具**」与 §0.4 第一条「`-t search` 的实际工具集是**空集**」的推断**不成立**：0 个工具只说明**离线 schema 枚举**（`get_tool_definitions` 经 `check_fn=check_web_api_key` 门控）为空，推不出「模型看到的工具集是空集」。T9 真跑（`hermes -z '<人格 prompt>' -t search`，hermes v0.19.0，本机无搜索后端 key）实测：

- 工具**存在**——`toolsets.py` 定义 `search = {web_search}`，模型侧真实看到并**发起了 `web_search` 调用**（stdout 含 `<seed:tool_call><function name="web_search">` 标记）。
- 在 `hermes -z` 一次性运行下，该调用**既不执行、错误也不回传模型**，进程 exit 0，stdout 只剩开场白 + 工具调用标记——**输出 derail，永远到不了 JSON**。brief 原版人格（无工具纪律条款）0/2 全灭、同一失败模式（task-9-report.md §3.1/§3.2），是本机无搜索 key 环境下的**系统性**失败，不是偶发。
- §0.4 的推论「人格将纯靠 contract 输入产出」因此同样不成立：不是「纯靠输入」，而是「整场输出报废」。

**处置（已落地 + 待办）**：

1. **人格纪律第 8 条**（四个人格 + bundesliga 均已写入：web_search 不可用/失败/查无 → 不调用工具、不空等检索、不复述查证计划，直接给判断）——T9 修改后 1/1 通过，T10 四联赛冒烟 4/4 通过（`extract_json` + `validate_output` 无异常），防 derail 有效。
2. **M5 实跑前建议配置搜索后端 key**（如 Tavily / Firecrawl）：既恢复 §6.1 的「伤停/新闻查证」用例（检索补偿，当前人格只能 agree/凭输入判断），也消除残留的 derail 面——T10 E0 的原始输出显示模型仍会先起「让我先尝试搜索」的念头、靠第 8 条自我拉回，条款有效但属每场都在走的钢丝。（冒烟混淆声明：四场冒烟输入均为德比对阵——测试选择而非模型选择，2/2 downweight 不能区分『德比触发』与『输入选择』，归因待 T15 实跑①的非德比样本——**已归因，见 §2.1**：非德比真实样本 14/14 agree、0 downweight，冒烟的 downweight 系德比触发，非输入选择副作用）

## 1. 验收表（spec §10 M4）

（待后续任务填写）

## 2. 真跑数字

### 2.1 实跑①：库内候选契约实测（Task 15，2026-09-04，三遍矩阵）

**判决**：契约链路（build_input → build_prompt → call_hermes → extract_json → validate_output）机械面零缺陷——27 次调用 0 timeout / 0 exit / 0 输入侧失败 / 0 真契约违规（无数值超界、无词表乱填）；**但合规率仅 51.9%（14/27），13 例失败根因 100% 是 web_search derail**（§0.6 预言的「每场都在走的钢丝」在实跑中断裂率 13/27）。M5 实跑前必须处置（建议见本节末，未改动任何文件）。

#### 方法与库快照

- 库：主 checkout `data/fa.db` 在线备份（`sqlite3 .backup`，源库不停写）到本 worktree；快照内 `model_only` 共 **50 行**：run #4（**pm**，14 行/9 场）+ run #5（am，36 行/25 场，M4 期间另跑，不在本探针范围）。**brief 预期「M3 的 14 条在 run #3/#4 且 phase=am」与实库不符**：M3 时推荐落库于 run #4/pm（run #3 仅 runs.summary 记 recs=14，recommendations 表无其行），探针以「14 行」口径动态锁定 run #4，不硬编码 id。
- 库内**无任何 model_persona 行**（run #4 是 M3 单轨产物），而 `build_input` 只读该轨作 candidates——事务内按 T11 双落语义镜像补 14 行（数字全同、`final_stake_frac=kelly` 中性初始），跑完 ROLLBACK：被测的是 build_input 真实代码路径，库终态不变（实测回滚后 model_persona 计数 0、总行数 50、`integrity_check=ok`）。
- schema v3 → v4（`fa init` 加法迁移）；备份快照为 2026-09-04 06:43 UTC 状态。
- **零 Odds API 额度**：全程未触 /events、/odds；ark 推理额度项目确认可忽略（27 次调用）。
- 探针脚本：`.superpowers/sdd/2026-09-04-m4-persona/probe_real.py`（gitignore）；逐场原文落 `probe_real_{r1,r2,r3}.jsonl`。

#### 9 场 × 3 遍 verdict 矩阵（run #4，league / kickoff UTC）

| fixture | 场次 | r1 | r2 | r3 | 合格 | 单场耗时 (s) |
| --- | --- | --- | --- | --- | --- | --- |
| 4 | E0 Brentford–Sunderland 09-05 14:00 | agree | agree | agree | 3/3 | 11.0 / 11.7 / 12.6 |
| 13 | E0 Fulham–Crystal Palace 09-05 14:00 | extract | contract | agree | 1/3 | 15.6 / 6.5 / 11.6 |
| 48 | D1 Bremen–Leipzig 09-05 13:30 | agree | derail | agree | 2/3 | 32.0 / 5.5 / 10.4 |
| 49 | D1 Paderborn–Freiburg 09-05 13:30 | contract | derail | agree | 1/3 | 6.2 / 6.8 / 8.9 |
| 75 | I1 Genoa–Como 09-04 18:45 | agree | derail | derail | 1/3 | 9.6 / 7.9 / 7.0 |
| 82 | I1 Fiorentina–Torino 09-05 13:00 | agree | derail | agree | 2/3 | 15.6 / 11.4 / 13.4 |
| 88 | F1 Le Havre–Brest 09-05 18:45 | derail | derail | derail | **0/3** | 6.1 / 5.5 / 7.7 |
| 96 | F1 Toulouse–Lille 09-03 18:45 | derail | agree | agree | 2/3 | 7.0 / 12.1 / 12.8 |
| 99 | F1 Lyon–Auxerre 09-04 17:00 | agree | agree | contract | 2/3 | 11.8 / 18.5 / 5.6 |

（表中 `extract`/`contract`/`derail` 为四类降级口径；derail = extract 失败且原文含 `<seed:tool_call>` 标记，详见下）

#### 失败计数：四类表面口径与根因分层

| 口径 | r1 | r2 | r3 | 合计 |
| --- | --- | --- | --- | --- |
| ok | 5 | 3 | 6 | **14** |
| timeout | 0 | 0 | 0 | **0** |
| exit | 0 | 0 | 0 | **0** |
| extract | 3 | 5 | 2 | **10** |
| contract | 1 | 1 | 1 | **3** |
| — 其中 derail（窄口径：extract+`<seed:tool_call>` 标记） | 2 | 5 | 2 | **9** |
| — 根因=结构化 web_search derail（宽口径：含 contract 类里 `{"tool":...}` / `{"role":"tool_use",...}` 形态） | 3 | 6 | 3 | **12** |
| — 根因=叙述态搜索意图（长篇自我辩论后决定搜索，被输出截断） | 1 | 0 | 0 | **1** |

- **13 例失败根因全部指向同一件事：模型要调 web_search**。两种形态：①标记式——stdout 以 `<seed:tool_call><function name="web_search">…` 收尾（`-z` 一次性运行下该调用不执行、错误不回传，永远到不了 JSON，§0.6 机制）；②JSON 式——模型把工具调用写成一个 JSON 对象（`{"tool": "web_search", ...}` / `{"role": "tool_use", ...}`），extract_json 能解析成 dict 但 `verdict` 缺失 → 记 contract。窄口径（9）**低估** derail 面：3 例 JSON 式被计入 contract。
- **0 例真契约违规**：没有一例是模型理解了契约但填错（超值域 delta、key_factors 超 50 字等）——validate_output 的全部触发都是「verdict 键不存在」这一种。契约说明本身可读性不是瓶颈。
- **成功 14 例 verdict 全部为 agree**（delta 13 例 =0，1 例 +0.02），0 downweight / 0 veto。与 T10 冒烟（2/2 downweight，德比样本）合看：非德比真实样本无一触发下调，符合人格「无可靠增量 → agree」的保守纪律；样本量小，不外推。
- 成功产物形态抽查（fixture 4 / 99）：key_factors 恰 5 条、均 ≤50 字；report_md 结构完整（结论行 + 论据）、≤500 字；fixture 99 的 delta=+0.02 为 agree 方向合法微调。

#### 合规率（三遍口径）

| 遍 | 合规/9 | 合规率 |
| --- | --- | --- |
| r1 | 5 | 55.6% |
| r2 | 3 | 33.3% |
| r3 | 6 | 66.7% |
| **合计** | **14/27** | **51.9%** |

不合规率 48.1% 远超 20% 阈值——**如实记录，未调人格文件、未调阈值、未重跑凑数**。逐场看失败非均匀：fixture 88（勒阿弗尔–布雷斯特）三遍全灭，fixture 4 三遍全过；同一场跨遍翻转（13/49/75/82/96/99）说明是**采样波动下的系统性诱因**（工具诱惑常驻），不是偶发。

#### 耗时（M4 报告要回答「120s 超时够不够」）

| 样本 | min | median | max | mean |
| --- | --- | --- | --- | --- |
| 全体 27 次 | 5.5 s | 10.4 s | 32.0 s | 10.8 s |
| 成功 14 次 | 8.9 s | 12.0 s | 32.0 s | — |
| 失败 13 次 | 5.5 s | 6.8 s | 15.6 s | — |

**120s 够，且余量大**：最大单场 32.0s（r1 fixture 48，成功例），超时余量 88s；失败例反而更快（中位 6.8s，derail 早退）。全场 9 场串行一遍 1.5–2 分钟，am 窗 14 场候选的 persona 开销量级 <1 分钟，不构成运行时瓶颈。超时档当前无调整必要。

#### 失败样本原文摘录（诊断用，全文在 probe_real_*.jsonl）

标记式（r2 / fixture 88，该场三遍皆此形态）：

> 我需要先搜索这场比赛的伤停和阵容信息，再给出判断。
> `<seed:tool_call><function name="web_search"><parameter name="query" string="true">Le Havre Brest 2026-09-05 team news injuries lineup</parameter><function name="web_search">…`（一次输出两个双语 query，无 JSON）

JSON 式（r2 / fixture 13 → 表面记 contract；r3 / fixture 99 同型）：

> 我来分析这场富勒姆对水晶宫的英超比赛。首先需要查证两队的伤停情况和其他关键信息。
> `{"role": "tool_use", "name": "web_search", "input": {"query": "Fulham vs Crystal Palace team news injuries September 5 2026 Premier League"}}`

叙述态（r1 / fixture 13，890 字无一 JSON）——第 8 条纪律「先想搜索再自我拉回」的钢丝这一次没拉回来，结尾是：

> 我应该先尝试搜索伤停信息……我来搜索富勒姆 vs 水晶宫 2026年9月的伤停信息。

成功例（r1 / fixture 4，报告开头）：`## 薇拉点评：布伦特福德 vs 桑德兰 / **结论：同意模型判断，无增量调整。** …`

#### 备注与事实修正

- **比赛时间事实**：9 场中仅 fixture 96（09-03 18:45）已完赛；75 / 99 于 09-04 傍晚开球；其余 6 场 09-05 开球——「M3 候选多数已开球」的原假设不成立。验收目的不受影响：`build_input` 不读 fixture status，输入 JSON 无赛果/比分字段；本机无搜索 key，模型也查不到赛果——persona 输入无污染成立。
- **环境事实**：无搜索后端 key，但模型侧 web_search 工具存在且可发起调用（§0.6）——人格「有 web_search 时先查证」的知识域条款在每场都在激活一次注定失败的调用。这是环境配置与人格条款的组合效应，不是单一方缺陷。

#### 改进建议（只建议，本轮未改任何文件/阈值/人格）

1. **最小且对症**：把「禁止工具调用」从人格纪律条款（第 8 条，§2 正文）上升为 `_CONTRACT_CLAUSE` 输出契约的一部分（「输出必须是且仅是一个 JSON 对象，不得包含任何工具调用标记或工具调用 JSON」）——契约说明是模型最紧跟的段落，r1–r3 的 0 例真契约违规侧面证明其约束力强于人格正文。
2. **hermes 层**：确认是否存在「空工具集」的合法 `-t` 值（`-t search` 含 web_search，是诱惑源）；若有空集 toolset，`build_command` 在无搜索 key 环境下应切换（需 T5 同款探针验证，不臆断）。
3. **caller 层**：derail 类失败自动重试 1 次（r2→r3 同场翻转数据支持重试有救回率；代价是时延与 token 翻倍，且 fixture 88 型可能三连灭）。
4. **根治**：M5 实跑前配置搜索后端 key（§0.6 待办）——web_search 真正可执行后，`-z` 下调用会得到结果回传，derail 面自然消失，同时恢复人格的伤停查证用例。
5. 若 1–4 均不采纳，M5 每场候选的期望降级率 ~48%（本节实测），paper 轨的 persona 判决覆盖率将系统性偏低，需在 m4/m5 报告里显式记账。

## 3. 人格产物质量

（待 Task 9 / 10 填写）

## 4. 环境缺口与降级

（待 Task 8 / 13 填写）
