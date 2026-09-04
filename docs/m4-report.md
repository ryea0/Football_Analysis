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

spec §10 M4 行：**「5 personas、契约校验、降级、A/B 双轨｜mock + 实跑测试通过；A/B 数据落库」**。逐项实测：

| spec §10 验收项 | 实测结果 | 证据 |
| --- | --- | --- |
| 5 personas | ✅ 5 文件入库（D1 老凯打样 `5bc634e` → E0 薇拉 / SP1 马诺罗 / I1 焦尔乔 / F1 克莱尔 `ed095e0`），纪律八条与知识域标题 `diff` 实证逐字同源 | `git ls-files personas/` 5 行；T9 冒烟 1/1、T10 四联冒烟 4/4（task-9/10-report） |
| 契约校验 | ✅ 严格校验不修号（唯一例外 veto 置 0）；实跑① 36 次调用 0 例「理解契约但填错」，实跑② 24 场 0 例违规——生产路径 key_factors ≤5 条（最长 43 字 ≤50）、report_md ≤500 字（最长 335） | §2.1/§2.2/§3.6；`tests/persona/test_contract_output.py` 拒收矩阵 |
| 降级 | ✅ 四类失败（timeout/exit/extract/contract）场次级降级、三列中性、`runs.summary.persona.degraded` 记因、报告 ⚪ 行；实跑② degraded=0/24（降级路径由单测覆盖：`tests/persona/test_apply.py` 16 项 + `tests/pipeline/test_matchday.py` 39 项，实跑未触发属正常） | task-8/task-13-report；`uv run pytest tests/persona/test_apply.py tests/pipeline/test_matchday.py` = 55 passed |
| A/B 双轨 | ✅ value 同刻双落 32+32；paper 落注 B 轨 31 注（veto 场跳过）+ A 轨 **1 注**（其余 31 条 A 轨候选已有在途注被幂等去重，§3.3）；bankroll 双键各就位；`fa status` 双行 | §3.3 / §3.4；`tests/pipeline/test_value.py`、`test_paper.py` |
| mock + 实跑测试通过 | ✅ 全量 `uv run pytest -q` = **502 passed**（0 failed / 0 error；501 + T16 审查新增 1 条 `_pct` 回归）；实跑① 27+9 次真调、实跑② 24 场真调 | §2、§3；T15 预注册跑法 |
| A/B 数据落库 | ✅ run #7：双轨 64 行、**新落 paper 注 32**（A 轨 1 + B 轨 31）；台账口径 run#7 名下挂 63 注（含 31 条遗留注改挂，§3.3）；分轨 bankroll 双键、结算通道分轨就绪（M5 daily 起按轨回写） | §3.3 / §3.4 |

设计文档 §8 验收清单（五项）：

| # | 清单项 | 状态 |
| --- | --- | --- |
| 1 | 全量测试绿 | ✅ 502 passed（含 T16 审查新增 `_pct` 回归 1 条） |
| 2 | 实跑①（D4）：库内 9 场候选真调 hermes -z，产出真实不合规率/降级率 | ✅ §2.1（51.9%）/ §2.2（100%，9/9） |
| 3 | 实跑②（D4）：比赛日完整 `fa run matchday --phase am`，双轨推荐/注落库、报告含 persona 段、分轨 bankroll、`fa status` 两行 | ✅ §3（run #7） |
| 4 | spec 修订落库（§9）+ CLAUDE.md Hermes 声明修正 | ✅/⚠️ spec §6.5 场次级（`053a93f`）+ §7.3 分轨口径（同 commit）已落库；**CLAUDE.md 的 Hermes 行（TG 未配置）未在本任务改写**——主 checkout 该文件有并行会话修改，为避免合并冲突本任务只在「当前状态」节加 M4 行（行内带上最新事实），旧行修正留待合并时处理（详见 §5.2） |
| 5 | personas 5 文件入库（德甲打样在前） | ✅ 顺序：bundesliga（`5bc634e`）→ 四联赛（`ed095e0`） |

## 2. 真跑数字

### 2.1 实跑①基线：库内候选契约实测（Task 15，2026-09-04，修复前三遍矩阵，代码 `4e0972f`）

**判决**：契约链路（build_input → build_prompt → call_hermes → extract_json → validate_output）机械面零缺陷——27 次调用 0 timeout / 0 exit / 0 输入侧失败 / 0 真契约违规（无数值超界、无词表乱填）；**但合规率仅 51.9%（14/27），13 例失败根因 100% 是 web_search derail**（§0.6 预言的「每场都在走的钢丝」在实跑中断裂率 13/27）。M5 实跑前必须处置（建议见本节末，未改动任何文件）。

#### 方法与库快照

- 库：主 checkout `data/fa.db` 在线备份（`sqlite3 .backup`，源库不停写）到本 worktree；快照内 `model_only` 共 **50 行**：run #4（**pm**，14 行/9 场）+ run #5（am，36 行/25 场，M4 期间另跑，不在本探针范围）。**brief 预期「M3 的 14 条在 run #3/#4 且 phase=am」与实库不符**：M3 时推荐落库于 run #4/pm（run #3 仅 runs.summary 记 recs=14，recommendations 表无其行），探针以「14 行」口径动态锁定 run #4，不硬编码 id。
- 库内**无任何 model_persona 行**（run #4 是 M3 单轨产物），而 `build_input` 只读该轨作 candidates——事务内按 T11 双落语义镜像补 14 行（数字全同、`final_stake_frac=kelly` 中性初始），跑完 ROLLBACK：被测的是 build_input 真实代码路径，库终态不变（实测回滚后 model_persona 计数 0、总行数 50、`integrity_check=ok`）。
- schema v3 → v4（`fa init` 加法迁移）；备份快照为 2026-09-04 06:43（北京时间 UTC+8，= 2026-09-03T22:43Z）状态。
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
- 抽查实例（fixture 4 / 99）：key_factors 恰 5 条、均 ≤50 字；report_md 结构完整（结论行 + 论据）、≤500 字；fixture 99 的 delta=+0.02 为 agree 方向合法微调。

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

1. **最小且对症**：把「禁止工具调用」从人格纪律条款（第 8 条，§2 正文）上升为 `_CONTRACT_CLAUSE` 输出契约的一部分（「输出必须是且仅是一个 JSON 对象，不得包含任何工具调用标记或工具调用 JSON」）——契约说明是模型最紧跟的段落，r1–r3 的 0 例真契约违规侧面证明其约束力强于人格正文。**（已落地：commit `5d24f13`，按 `FA_PERSONA_SEARCH` 条件拼接，效果见 §2.2）**
2. **hermes 层**：确认是否存在「空工具集」的合法 `-t` 值（`-t search` 含 web_search，是诱惑源）；若有空集 toolset，`build_command` 在无搜索 key 环境下应切换（需 T5 同款探针验证，不臆断）。
3. **caller 层**：derail 类失败自动重试 1 次（r2→r3 同场翻转数据支持重试有救回率；代价是时延与 token 翻倍，且 fixture 88 型可能三连灭）。
4. **根治**：M5 实跑前配置搜索后端 key（§0.6 待办）——web_search 真正可执行后，`-z` 下调用会得到结果回传，derail 面自然消失，同时恢复人格的伤停查证用例。
5. 若 1–4 均不采纳，M5 每场候选的期望降级率 ~48%（本节实测），paper 轨的 persona 判决覆盖率将系统性偏低，需在 m4/m5 报告里显式记账。

### 2.2 实跑①修复后单遍：禁工具过渡条款（Task 15 fix round 1，代码 `5d24f13`）

**判决**：机械合规率 **9/9 = 100%**——此为**环境性 derail 缓解后的机械合规率**（prompt 层禁工具条款生效的实测），**不是 persona 判断质量的表述**。两阶段并列（0 真契约违规 / 0 超时 / 0 exit 两阶段均成立）：

| 阶段 | 代码 commit | 报告 commit | 调用 | 合规 | timeout | exit | 真契约违规 | 失败根因=web_search derail | 耗时 min/median/max |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 修复前基线（§2.1） | `4e0972f` | `f1ef878` | 27 | 14/27 = 51.9% | 0 | 0 | 0 | 13 | 5.5 / 10.4 / 32.0 s |
| 修复后单遍（本节） | `5d24f13` | （本节随 commit B 入库） | 9 | **9/9 = 100%** | 0 | 0 | 0 | **0** | 7.7 / 11.2 / 12.9 s |

- **预注册跑法**（task-15-report.md §8，先于执行写死并声明「数字无论好坏一律入报告、不再重跑」）：同 9 场候选（run #4 口径）、同探针脚本（`probe_real.py` 一字未改）、单遍 1 次、`FA_PERSONA_SEARCH` 默认 off（条款默认生效路径）。
- **verdict 分布**：8 agree + **1 downweight**（fixture 49，delta=-0.10，满足「downweight 须 <0」；判据=帕德博恩近 5 轮 1 平 4 负 / 弗赖堡联赛第 2 / H2H 三战全胜）——两阶段 36 次调用中**首个判据驱动的下调判决**（修复前 14 例 agree 全部是「无增量信息 → agree」）；fixture 13 delta=+0.05 为 agree 方向合法微调。修复前三连灭的 fixture 88 本遍 agree 通过（10.1s）。
- **stopgap 声明**：禁工具条款是**过渡方案**——根治 = 配置搜索后端 key；届时运维置 `FA_PERSONA_SEARCH=1`，条款自动退出 prompt、检索重新合法，**本节 100% 口径随之失效**，需在真检索环境重测（derail 面消失但新增检索超时/结果质量变量）。
- **样本量警示**：单遍 9 次调用，100% 是小样本点估计，**不与修复前 51.9% 做显著性比较**——修复前三遍本身就在 33.3%–66.7% 间波动，采样噪声与条款效应在本样本量下不可分。机械面结论（0 超时/0 exit/0 真契约违规两阶段一致）比合规率数字更稳。
- 成本：ark 推理 9 次；Odds API 0 次。

## 3. 实跑②：比赛日完整 E2E（Task 16，run #7）

**判决：ok。** 全链真实跑通：拉盘 → 对齐 → 双轨推荐 → persona 逐场真调 → 分轨落注 → 渲染 → TG 推送（失败按设计降级）。无重跑、无凑数；0 推荐的备选叙述未用上（窗内有真实候选）。

### 3.1 运行条件与 CLI 全输出

- 库：worktree 本地库（T15 自主库在线备份的快照 + `fa init` v4 迁移），**非主库**；跑前状态：额度 440、`paper_bankroll` 单键 940.95 待迁移、`model_only` 50 行、schema v4。
- 环境：`.env` 有 `ODDS_API_KEY`；`FA_PERSONA_SEARCH` 未设（禁工具过渡条款默认生效路径，prompt 内实证含「本环境无网络搜索可用：不要调用任何工具」）；TG 推送带 Clash 代理（`https_proxy=http://127.0.0.1:7890`，进程内实测代理监听在、经代理 `curl api.telegram.org` = 302、直连超时）。
- 比赛日有效性：run 启动于 **2026-09-03T23:22:45Z（北京时间 09-04 07:22，周五）**，52h 窗内 28 场五大联赛（周六 09-05 为主力），`window_hours=52`。

CLI 原文（唯一一次运行，未重跑）：

```
比赛日 run（am）判决：ok（正常）
  行数：fixture 同步 100 场（双侧对齐 100，未对齐 0），推荐 64 条，落注 32 注
  额度：剩余 420 credits（Odds API）
  推送：失败（exit 1: hermes send: Telegram send failed: httpx.ConnectError:）——推荐与落注已落库
```

总耗时 283 s（23:22:45 → 23:27:28 UTC，`runs.started_at/finished_at`）。按轮询观测：同步 + 拟合 + 双轨推荐完成于启动后约 2 分钟，persona 24 场串行完成于约 4.5 分钟（0 超时，120 s 档无压力；库内不记单场耗时，单场量级参考实跑①均值 10.8 s）。

### 3.2 拉盘与对齐

- fixture 同步 100 场、双侧对齐 **100/100**、未对齐 0（隔离表空）——M3 遗留的 30 项 `≥0.60` 待确认别名未阻塞本次对齐。
- 额度 440 → **420**，差分 **−20**，与 D4 批准的「am 全量约 −20」一致；`runs.summary.quota_before/credits_after` 双记。

### 3.3 双轨推荐与落注（A/B 落库）

三条验收 SQL 的实测结果（brief Step 2 原样）：

```sql
-- SQL1：两轨行数 / 已判
model_only|32|0          -- A 轨 persona 盲视，verdict 恒 NULL ✔
model_persona|32|32      -- B 轨 32 行全部已判 ✔ 两轨行数相等 ✔
-- SQL2：分轨落注（全库 paper，台账口径）
model_only|37|589.07     -- 37 = 14（14:58 期）+ 22（18:20 期）+ 1（run#7 新落）
model_persona|31|485.53  -- 31 = 32 − 1 veto ✔ veto 行无 bets ✔（全部 run#7 新落）
-- SQL3：分轨 bankroll
paper_bankroll:model_only|940.95      -- 旧单键 940.95 已迁移到此 ✔ 旧键已删除 ✔
paper_bankroll:model_persona|1000.0   -- 惰性初始化 ✔
```

**落注口径（关键勘误，T16 审查修正）**：run#7 **实际新落 32 注** = A 轨 1 注（rec 68，fixture 61 Hoffenheim–Dortmund 客胜，stake 8.08，唯一一条此前无注的 A 轨候选）+ B 轨 31 注（485.53，全部新插）——CLI「落注 32 注」即此数。A 轨 32 条 run#7 候选里 31 条**已有在途注**（`bets UNIQUE(recommendation_id, mode)` 幂等去重，不重复落注），故 A 轨本 run 仅落 1 注。

**台账口径（另一套数字，勿与落注口径混用）**：按「rec 的 run_id=7」统计得 A 轨 32 注 / 519.29——但其中 **31 条是遗留注被改挂**，不是 run#7 落的。全库 68 条 paper 注按 `placed_at` 分三期：

| 落注期 | track | 注数 | 注金 | 备注 |
| --- | --- | --- | --- | --- |
| 09-03 14:58（M3 首赛日，run#3 期） | model_only | 14 | 217.08 | bets 1–14 |
| 09-03 18:20（M4 期间，run#5 期） | model_only | 22 | 363.91 | bets 15–36 |
| 09-03 23:27（run#7，本次） | model_only / model_persona | 1 / 31 | 8.08 / 485.53 | bets 37–68 |

36 条遗留注中 **31 条现挂 run#7 名下**（12 条 14:58 期 + 19 条 18:20 期），其余 5 条仍挂旧 rec（对应候选本 run 未再生成：fixture 96 H/D、14 O2.5、30 A/O2.5）。

**改挂机制（M5 必须知道的归因语义）**：`value.py` 的候选 upsert 是 `ON CONFLICT(fixture_id, market, strategy, phase) DO UPDATE SET run_id=excluded.run_id, model_p=…, kelly_stake_frac=…, created_at=…`——**保留 rec 主键 id**，只刷新 run_id 与价格/仓位字段；骑在该 rec id 上的旧注因此被整体改挂到最新 run 名下（且旧注的 `stake/odds_taken` 仍是落注当时的旧值，rec 行上的数字却已是新值——同一条 rec 两套时刻的数字并存）。这是幂等刷新的既有设计（「刷新不清判决/注」），但意味着 **「run_id」不是可靠的落注归因键，`placed_at` 才是**。

**A/B 同刻对照本 run 不存在（诚实记）**：初稿曾写「persona 净效应 −33.76（−6.5%）」，那是把 31 条遗留注当 A 轨新注的假对比（含价格刷新效应），**已撤回**。本 run 的 A 轨新注仅 1 注 8.08，无同刻 A/B 可言；A/B 对比只能取（a）A 轨无遗留注的干净库首跑，或（b）结算期分轨 pnl（§12.3 预注册判据本就是结算期口径）——已列入 §7 M5 建议。

`runs.summary.persona`：`called=24, ok=24, veto=1, degraded=0`（attempted 24 场）。**24 场 × 候选市场 = 32 行**（17 场 1 市场 / 6 场 2 市场 / 1 场 3 市场）——判决按场广播到该场全部 market 行，故「已判行数 = called − degraded」在市场数 >1 的场次按行数放大，场级恒等式 24 = 24 − 0 成立。

### 3.4 persona 判决明细（24 场逐条，样本全量记录）

| fixture | 联赛（人格） | 场次 | 市场 | verdict | delta | 关键判据（key_factors 摘） |
| --- | --- | --- | --- | --- | --- | --- |
| 3 | E0（薇拉） | Hull – Aston Villa | H | ⚠️ downweight | −0.06 | 主队近 5 场 3 负；维拉近 5 场 2 胜回升；模型 0.537 vs 市场 0.232 差距过大 |
| 4 | E0（薇拉） | Brentford – Sunderland | D,A | ✅ agree | 0 | 平局属性 + 首回合 3-0 但样本久 |
| 5 | E0（薇拉） | Brighton – Fulham | H,O2.5 | ✅ agree | 0 | 无可靠伤停来源，不凭空猜测 |
| 8 | E0（薇拉） | Ipswich – Liverpool | D | ✅ agree | 0 | 利物浦近 5 场 4 平 1 负，平局方向一致 |
| 10 | E0（薇拉） | Newcastle – Bournemouth | A | ✅ agree | 0 | 交手记录不愵（纽卡主场曾 1-4） |
| 13 | E0（薇拉） | Fulham – Crystal Palace | H | ✅ agree | 0 | 双双低迷的保级六分战 |
| 14 | E0（薇拉） | Nott'm Forest – Tottenham | H | ✅ agree | 0 | 热刺垫底但样本不足，不过度下修 |
| 24 | SP1（马诺罗） | Betis – Real Madrid | D | ✅ agree | 0 | 信息边界声明 + 贝蒂斯主场对皇马 1 胜 1 平 |
| 28 | SP1（马诺罗） | Ath Bilbao – Ath Madrid | H,O2.5 | ⚠️ downweight | −0.05 | 毕尔巴鄂近 5 场 1 胜 1 平 3 负；马竞第 3；H2H 下风 |
| 39 | SP1（马诺罗） | Villarreal – La Coruna | O2.5 | ✅ agree | 0 | H2H 全在 2017-18，无参考价值 |
| 43 | SP1（马诺罗） | Vallecano – Almeria | H | ✅ agree | 0 | 两连胜反弹但「赢下谁不知」诚实表述 |
| 48 | D1（老凯） | Werder Bremen – RB Leipzig | H | ✅ agree | 0 | 不来梅 1 平 4负，但模型 28% vs 市场 22% 已定价低估 |
| 49 | D1（老凯） | Paderborn – Freiburg | H | ⚠️ downweight | **−0.10** | 帕德博恩 1 平 4 负崩盘；弗赖堡第 2；H2H 三战全胜（**与实跑①同一判据复现**） |
| 51 | D1（老凯） | M'gladbach – Elversberg | A | ✅ agree | 0 | 升班马样本仅 1 场不足判断 |
| 61 | D1（老凯） | Hoffenheim – Dortmund | A | ✅ agree | 0 | 多特 4 胜 1 负状态占优 |
| 63 | I1（焦尔乔） | Inter – Napoli | O2.5 | ⚠️ downweight | −0.08 | 强强对话先求不失球；近 3 次交锋 2 平、大球率 1/3 |
| 65 | I1（焦尔乔） | Roma – Atalanta | A | ✅ agree | 0 | 罗马五连胜但亚特兰大硬实力在 |
| 75 | I1（焦尔乔） | Genoa – Como | H,A | ✅ agree | 0 | 热那亚 1 平 4 负 vs 科莫 4 胜 1 平 |
| 82 | I1（焦尔乔） | Fiorentina – Torino | A,O2.5 | ✅ agree | 0 | 近 3 次交手全平 |
| 88 | F1（克莱尔） | Le Havre – Brest | H,O2.5 | ✅ agree | 0 | 实跑①三连灭场，本轮一次通过（禁工具条款下） |
| 92 | F1（克莱尔） | Caen – Lorient | D | ✅ agree | 0 | H2H 距今近十年无参考价值 |
| 99 | F1（克莱尔） | Lyon – Auxerre | D | ✅ agree | 0 | 青年军波动属常态；无抽调信息 |
| 100 | F1（克莱尔） | Treviso – Monaco | D | ⛔ **veto** | 0（强制） | **主队 Treviso 非法甲队，判「输入数据错误」整场否决**（见 §3.5） |
| 101 | F1（克莱尔） | Nice – Le Mans | D,A,O2.5 | ✅ agree | 0 | H2H 全在 2009-10，跨度 15 年 |

- verdict 分布：**场级 19 ✅ / 4 ⚠️ / 1 ⛔（=24 场）；行级 26 ✅ / 5 ⚠️ / 1 ⛔（=32 行）**。agree 全部 delta=0（0 微调）。
- 4 场 downweight 的 delta 全部为负（−0.05～−0.10），`final_stake_frac = kelly × (1+delta)` 乘法公式逐行核验通过（0.02×0.94=0.0188、0.02×0.90=0.018、0.01505×0.95=0.0143、0.02×0.92=0.0184）；veto 行 final=0。
- **机械合规 24/24 = 100%**：degraded 列表为空，0 timeout / 0 exit / 0 extract / 0 contract；key_factors 1–5 条、单条最长 43 字（≤50），report_md 最长 335 字（≤500）。禁工具条款生效后累计 33/33（§2.2 的 9 + 本节 24），仍属小样本，不外推。
- verdict 非中性率 5/24 = 20.8%（4 下调 + 1 否决）——显著高于实跑①的 1/9；同场 fixture 49 的判据与实跑①复现（帕德博恩崩盘/弗赖堡第 2/H2H 三战全胜），说明判决可溯源、非采样噪声。

### 3.5 veto 首例：persona 抓到候选池数据错误（非比赛判断）

fixture 100（F1 Treviso – Monaco，kickoff 09-04 19:05 UTC，平局候选 EV +54.26%、kelly 顶格 2.00%）：克莱尔（法甲人格）以「主队 Treviso 是意大利球队、法甲无此队 → 本场输入不可信」为由 **veto 整场**，key_factors 三条全部指向数据错误而非比赛判断。

库内核实：`fixtures.league='F1'` 但 `home_team_id=187`，而 teams 表 id=187 的 Treviso 属 **I1**（意甲）——即 Odds API 事件的对阵名与联赛属性错位，属于候选池真实脏数据。

**该场 A/B 对照的如实口径（勿夸大）**：A 轨账上确有该场平局 20.00 在途注（bet 33），但它落于 **09-03 18:20（run#5 期）**，早于本 run 5 小时，是遗留注被 upsert 改挂到 run#7 名下（§3.3）——本 run 的幂等去重下，A 轨本来也**不会再落新注**。因此 veto 的边际效果是「B 轨对该脏场零敞口」（本 run 不新增），而非「阻止了一笔即将发生的 2% 顶格落注」；后者只在干净库（A 轨无遗留注）的首跑里成立。

如实记两点：① veto 触发条件是「压倒性证据」，此处触发的是**数据完整性证据**而非伤停/状态类比赛证据，属设计未列举但语义正确的用法，值得在 M5 把「输入自洽性检查」固化到确定性管线（不该由每场一次的 LLM 调用来兜底数据质量）；② 人格同时给出「建议核查别名映射表」的行动建议，方向正确。

### 3.6 报告验收（§7.1-2/3 逐项，离线复渲染）

`render_matchday_report(conn, 7, 'am', summary, 420, False)` 从 runs.summary 只读复渲染，与 run 内渲染同库同参：

| spec 条款 | 实测 |
| --- | --- |
| §7.1-1 候选场次表 | ✅ 64 行（双轨并列、市场/最优价/模型 p/市场 p/EV/仓位/博彩商全列） |
| §7.1-2 persona 点评 + 判决标识 | ✅ 24 行（按场一行）：✅19 ⚠️4（含 delta）⛔1；每场 key_factors 缩进逐条 + `report_md` 引用行 |
| §6.5 降级标注 | ✅ 0 场降级 → 0 个 ⚪ 行（渲染路径由 `tests/report/test_render.py` 覆盖） |
| 200 字截断 | ✅ 24 段引用最长恰 200 字（`_REPORT_MD_CLIP=200`），短文不截 |
| 段尾汇总行 | ✅ `persona 24 场：✅19 ⚠️4 ⛔1 · 未生效 0` |
| §7.1-3 风险提示 | ✅ 样本量 5353 / 半衰期 100 天 / 降级：否 / 额度水位 420 |
| §7.3 bankroll 快照 | ✅ 两行：`model_only：940.95（meta paper_bankroll:model_only）`、`model_persona：1000.00` + 未结注 64 |
| 判决只标 B 轨 | ✅ 候选表 A/B 同价同行并列，无图标悬空（`_persona_note` 仅 pm 新增候选行使用） |

### 3.7 `fa status` 双轨行（§12.3 判据表就位）

run 内实测（当时输出，含 P2 待修的百分比缺陷）：

```
== B 线·运营模拟（paper） ==
[model_only] 注数=37（pending 33）  已结算注金=59.05  回报=0.00  ROI=-1.00%  bankroll=940.95  CLV 中位数=—
[model_persona] 注数=31（pending 31）  已结算注金=0.00  回报=0.00  ROI=—  bankroll=1000.00  CLV 中位数=—
额度水位：余 420 次（meta odds_quota_remaining）
```

- 注数为**台账口径**：model_only 37 = 14（14:58 期）+ 22（18:20 期）+ 1（run#7 新落）；model_persona 31 全部 run#7 新落（§3.3）。
- **P2 修复（T16 审查）**：`_pct` 此前把 `paper_summary` 的分数直出为百分比——59.05 注金全损的真实 ROI 是 **−100%**，却显示成「−1.00%」（缩水 100 倍，把最刺眼的信号抹平）。修复为 `value*100` 后复跑：

```
[model_only] 注数=37（pending 33）  已结算注金=59.05  回报=0.00  ROI=-100.00%  bankroll=940.95  CLV 中位数=—
```

回归：`tests/test_cli.py::test_status_pct_scales_fraction_values`（1 注 stake 20 全输 → roi=-1.0 → 断言显示 `-100.00%` 且不出现 `-1.00%`；clv=-0.2 → `-20.00%`），全量 **502 passed**。同文件排查：`degradation_pct` 是真百分数但走 f-string 内联格式、不经 `_pct`，无双乘风险；`render_settlement_brief` 的 CLV 用 `{:+.2%}`（Python 百分号格式自带 ×100），语义本就正确。

分轨账本、额度水位、最近 runs 一屏可读——M5 daily 结算起按轨回写 pnl 后即为 §12.3 预注册判据的直接读数表。

### 3.8 TG 送达状态（失败降级 + 复盘）

1. **run 内推送失败**：`exit 1: hermes send: Telegram send failed: httpx.ConnectError:`（错误详情为空串）。按设计降级：run 状态不受影响（判决 ok）、推荐与落注已落库、原因落 `runs.summary.telegram.error`。
2. **诊断事实**：代理在监听（verge-mihomo 127.0.0.1:7890）、经代理 `curl https://api.telegram.org` = 302、直连超时无响应；代理变量经 `uv run` 确认透传到 fa 子进程链；hermes 独立发送路径的 `resolve_proxy_url`（`gateway/platforms/base.py:405`）检查顺序为 `TELEGRAM_PROXY` → `HTTPS_PROXY/HTTP_PROXY/ALL_PROXY`（含小写）→ macOS 系统代理，代理附加失败时**告警后回退直连**（`tools/send_message_tool.py:1208-1228`）。
3. **复现与补投递**：同一渲染文本（14,944 字符）、同一代理环境变量，15 分钟后手动 `hermes send --to telegram` **成功送达** home channel（chat_id 8853969879，rc=0）。
4. **结论（如实记，不下断言）**：传输链路可用；run 内失败为**单次瞬态连接错误**，根因未能定位（hermes `-z`/send 丢弃运行期 stderr，诊断信息不可回传，§0.4 已知限制）。不是 fa 代码缺陷——降级路径行为与设计完全一致。
5. **M5 建议**：reporting 层对推送失败自动重试 1 次（成本仅数秒，可吸收瞬态类失败）；或在 fa 侧显式设置 `TELEGRAM_PROXY` 走第一优先级路径。

## 4. 人格产物质量

（本节综合 T9/T10 冒烟与实跑①② 的 67 次真实调用：T9=3 + T10=4 + 实跑①=36（修复前 27 + 修复后 9）+ 实跑②=24。）

- **人格文件**：5 个（D1 老凯 / E0 薇拉 / SP1 马诺罗 / I1 焦尔乔 / F1 克莱尔），每人 6 条知识域（brief 要求 5–7）+ 八条判断纪律（八条逐字同源，`diff` 实证）。德甲打样在前（T9），四联赛铺开在后（T10），与设计文档「德甲打样在前」的顺序一致。
- **冒烟**：T9 brief 原文人格 0/2（同型 web_search derail）→ 修改后 1/1；T10 四联赛首跑 4/4、零重试；四份原始 stdout 无一含 `seed:tool_call` 标记（T10 时点禁工具条款尚未上 prompt，靠纪律第 8 条自我拉回）。
- **判决保守性**（§2.2 + §3.4，禁工具条款生效后 33 场合计）：27 agree / 5 downweight / 1 veto（实跑①修复后 9 场 8✅1⚠️，实跑② 24 场 19✅4⚠️1⛔）。agree 全部声明「无可靠增量信息」——人格遵守「模型为先、没有增量不动手」的纪律，没有为「显得有用」而制造调整。
- **可溯源性**：抽读 24 场 key_factors 与 report_md，引用均落在输入 JSON 可得字段（form/h2h_recent/排名）或明确的「无法查证」声明上，未发现幻觉伤停、幻觉比分。
- **发现的瑕疵（如实记 1 例）**：fixture 24（贝蒂斯–皇马）report_md 出现「塞维利亚德比氛围」的语境误植（该对阵并非塞维利亚德比），但该 persona 明确写「无量化依据，不纳入概率」、判决未受影响——修辞层面的知识域串线，非数字污染。
- **veto 质量**：首例 veto（§3.5）否决的是**数据完整性**而非比赛判断，把「不发明数字、证据不足不动手」的纪律用在了正确的方向上；同时也暴露该检查更适合确定性代码做（见 §5.4）。

## 5. 环境缺口与降级

### 5.1 搜索后端 key 缺失（§0.4/§0.6 遗留，未解）

本机仍无任何搜索后端 key，`-t search` 的 web_search 只能发起、不能执行。处置即 §2.2 的禁工具过渡条款（`FA_PERSONA_SEARCH` 默认 off）：derail 面从 48.1% 压到 0（33/33），代价是人格只能凭输入判断、§6.1「伤停/新闻查证」用例持续不可用。**M5 实跑前建议配置 Tavily/Firecrawl key**：配好后置 `FA_PERSONA_SEARCH=1`，条款自动退出 prompt、检索重新合法——届时本报告 §2.2/§3.4 的 100% 合规口径失效，需在真检索环境重测。

### 5.2 CLAUDE.md 的 Hermes 声明已过时（本任务未改，留待合并）

CLAUDE.md 环境节仍写「Hermes **TG 平台未配置**（2026-09-03 实测：`~/.hermes/.env` 全注释、无任何 token）」。两处事实已变：① m4-report §0.2 实测 `~/.hermes/.env` 有 18 行非注释 KEY=VALUE；② **Telegram 平台现已配置**（`hermes status` 显示 `Telegram ✓ configured (home: 8853969879)`，且本次 §3.8 手动重发实送成功）。按 T16 约束本任务只往「当前状态」节加 M4 一行、不动其他行（主 checkout 同文件有并行会话修改，避免合并冲突），**旧行的改写留到合并时处理**；本节即为该项验收（设计文档 §8 第 4 条后半）的如实交账。

### 5.3 Odds API 额度与 TG 代理

额度 440 → 420（−20，实测与批准口径一致），按 500/月档与「M5 首务=额度节流」（单 region/pm 限比赛日）的既有结论不变。TG 必须带代理（直连不通），run 内 1 次瞬态失败 + 手动重发成功，见 §3.8。

### 5.4 数据质量缺口（实跑② 新发现）

`fa data aliases` 待确认清单之外新暴露一类：**跨联赛同名/错挂**（fixture 100 的 F1 事件挂到 I1 的 team_id）。本次被人格 veto 兜住，但兜底不应长期由 LLM 承担——建议 M5 在确定性管线加输入自洽性检查（fixture.league 与两端 team.league 一致性校验，不一致即隔离该场），成本一行 SQL 量级。

### 5.5 pm 相位未实跑

实跑②按 brief 只跑 am 全量。pm 的插层/沿用判决/新增候选图标路径由 39 项 `tests/pipeline/test_matchday.py` + 渲染单测覆盖（全绿），但**无真实 pm E2E 数字**——如实记为 M4 的已知边界，建议 M5 首个比赛日顺带实测 pm。

## 6. 提交记录（M4 全量，branch `worktree-m4-persona-design`）

| commit | 内容 |
| --- | --- |
| `1a0d480` | docs: M4 persona 实现设计（D1 场次级降级 / D2 分轨 bankroll / A1B1C1 工程选型） |
| `ffaedb2` | docs: M4 实施计划（16 任务 TDD） |
| `053a93f` | docs(spec): §6.5 降级粒度改场次级、§7.3 补 bankroll 分轨口径 |
| `780f022` / `4369fd9` | docs: m4-report 骨架 + 探针结论与措辞修正 |
| `05b9a2f` | feat(config): hermes/persona 配置（HERMES_BIN、超时、persona 文件映射） |
| `b3c5d0d` | feat(db): schema v4（key_factors / report_md 两列） |
| `a1dcf3d` | feat(persona): hermes -z caller |
| `4268341` | feat(persona): 输入组装（candidates/form/h2h/排名全自库内） |
| `6e25468` | feat(persona): 输出提取与 §6.3 严格校验 |
| `b920fea` | feat(persona): §6.4 判决广播（veto 置零）与阶段编排 |
| `5bc634e` | feat(personas): 德甲「老凯」打样 |
| `ed095e0` | feat(personas): E0/SP1/I1/F1 四联赛人格 |
| `f48c491` | docs: m4-report §0.6 勘误（`-t search` 非空集） |
| `a76f393` | feat(value): 同刻双落 model_only / model_persona |
| `2d03987` | feat(paper): bankroll 按 strategy 分轨（迁移/双轨落注/veto 跳过/分轨结算） |
| `e9fb457` | feat(matchday): value→persona→bet 插层，pm 沿用 am 判决 |
| `b71f1b7` / `4e0972f` | feat(report): persona 段真渲染 + status 双轨行（+ 审查修复） |
| `f1ef878` | docs: m4-report §2.1 实跑①基线（三遍矩阵，51.9%） |
| `5d24f13` | fix(persona): 禁工具过渡条款按 FA_PERSONA_SEARCH 条件拼接 |
| `7448941` | docs: m4-report §2.2 实跑①修复后单遍（9/9） |
| `3de1f95` | docs: M4 验收报告完稿（§1/§3/§4/§5/§6）+ CLAUDE.md 状态更新 |
| `5bcb353` | fix(cli): `_pct` 分数×100（T16 审查 P2，含 `_pct` 回归测试，全量 502 passed） |
| （本 commit） | docs: 注数归因勘误（P1）+ 时间戳口径（P3）——即本表所在提交 |

## 7. 遗留与 M5 建议（按优先级）

1. **额度节流**（既有结论，M5 首务）：单 region/pm 限比赛日，否则第 13 天耗尽免费额度。
2. **A/B 归因口径（T16 审查提出，评估前必须解决）**：`value.py` 的 `DO UPDATE SET run_id=…` 保留 rec id，把骑在其上的遗留注整体改挂到最新 run 名下——`recommendations.run_id` 不可作落注归因键，`bets.placed_at` 才是。后果：**同刻 A/B 对照在本库已不可得**（A 轨 36 条遗留注占住了 32/36 候选位，新 run 的 A 轨几乎必然幂等去重）。二选一：① A/B 对比只从**结算期分轨 pnl** 取数（§12.3 预注册判据本就是结算期口径，推荐此项）；② 若要做「当日落注口径」的对照，需 A 轨无遗留注的干净库首跑（如另建 snapshot 库跑 A/B 对照实验）。
3. **推送重试**：reporting 层推送失败重试 1 次（§3.8 瞬态失败的廉价吸收）。
4. **输入自洽性检查**：fixture.league 与两端 team.league 一致性校验，脏场隔离（§3.5/§5.4 的根治）。
5. **搜索后端 key**：配置后置 `FA_PERSONA_SEARCH=1`，恢复查证用例并在真检索环境重测合规率（§5.1）。
6. **CLAUDE.md Hermes 旧行改写**：随合并处理（§5.2）。
7. **pm 相位实跑**：M5 首个比赛日顺带实测（§5.5）。
