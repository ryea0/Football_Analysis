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
| 5 | `hermes -z '<问模型你有哪些工具>' -t search` | 0 | `["bash", "view_files", "skill_view", "write_to_file", "search_by_regex", "cronjob", "hermes_admin"]` ——**模型自报不可信**（见 0.4） |
| 6 | `hermes -t web_search -z '<prompt>'`（`-t` 前置） | 2 | 同 #2 → 证明 `-t` 前置的**参数顺序可解析**，#1 失败纯属工具集名非法 |

### 0.2 免费实测与源码判据（判定依据，非猜）

- `hermes tools list`：cli 平台当前 `web / browser / terminal / file / code_execution / vision / image_gen / tts / skills / todo / memory / session_search / clarify / delegation / cronjob` 均 enabled（exit 0）。
- `hermes prompt-size --platform cli --json`（离线，无 API）：默认工具集 **tools count = 27，tool-schema JSON = 49,010 bytes**。
- 离线工具枚举（hermes 自带 venv 调 `get_tool_definitions(enabled_toolsets=...)`，与 CLI 同一注册表）：`['search']` → **0 个工具**；`['web']` → 0 个；不传 → 30 个（含 `terminal / write_file / patch / execute_code / delegate_task / process` 等）。为 0 的原因：`web_search`/`web_extract` 的 `check_fn=check_web_api_key` 当前为 False。
- 合法 `-t` 取值 = `toolsets.py` `TOOLSETS` 的 57 个键（`web`、`search`、`file`、`terminal`、`coding`、`safe`、`hermes-cli`…）+ `all`/`*` + 已配置的 MCP server 名。本机 `config.yaml` 无 `mcp_servers`、无 `~/.hermes/plugins` 目录 → 无插件/MCP 扩名。
- `hermes_cli/oneshot.py`（`-z` 路径）三处硬事实：① 显式 `--toolsets` 未知项直接丢弃并告警，全部非法则 exit 2；② `-z` **无条件** `os.environ["HERMES_YOLO_MODE"] = "1"` 且 `HERMES_ACCEPT_HOOKS = "1"`——审批全绕过，与是否传 `--yolo` 无关；③ stdout 只回显最终文本，运行期 stderr 被重定向丢弃（仅参数校验告警可见）。
- 搜索后端：`hermes status` 全部 ✗（Tavily / Firecrawl / OpenRouter / xAI / OpenAI 等均未设）；`~/.hermes/.env` 仅含注释模板 `# FIRECRAWL_API_KEY=`（grep 实证）。

### 0.3 判定与 caller 形态

1. **`-t` 能收窄工具集**（判定标准前半成立）→ `build_command` 带 `-t search`，不带 `--yolo`（`-z` 本就 yolo，传了也无增量语义）。
2. 参数顺序约束：`-z` 的下一个 token 就是 prompt，因此**不能写 `-z -t xxx`**；安全写法 `hermes -z <prompt> -t search`（实测 #3）或 `hermes -t search -z <prompt>`（顺序可解析，#6）。
3. **必须固定带 `-t`**：不带时（#4）从代码目录跑 `-z` 暴露 27 个工具（`terminal / write_file / patch / execute_code / delegate_task`…）且 yolo 全放行——web 内容注入可直接触达本机执行，这是 §6.1「禁其余一切工具」要挡掉的面。
4. **禁止 `-t all` / `-t *`**：源码显式语义为「enables every toolset」（其余条目被忽略并告警）。
5. 显式 `-t` 优先于 coding posture / 平台默认（`coding_context.py`：posture 仅在「no explicit toolset given」时折叠 toolset）。

### 0.4 已知限制（如实记）

- **web_search 当前不可用**：无任何搜索后端 key → `-t search` 的实际工具集是**空集**（比 §6.1 更严：连 web_search 也没有）。人格将纯靠 contract 输入产出，§6.1 的「伤停/新闻查证」用例暂不可用；若要启用，需配置后端（如 Tavily / Firecrawl key），配好后 `-t search` 恰好等于「仅 web_search、禁其余一切」。
- **模型自报工具列表不可信**（#5）：7 个自报名字无一个存在于 hermes 工具注册表（`bash / view_files / write_to_file / search_by_regex / hermes_admin` 均非 hermes 工具名），且本机无 plugins/MCP 可提供它们——属幻觉。**验收不得用「问模型有哪些工具」**，只能以 CLI 校验 + 源码/离线枚举为准。
- 运行期 stderr 被 `-z` 丢弃：persona 调用的诊断信息（如后端不可用告警）不会回传 caller，caller 只能靠 exit code + stdout 判定。

### 0.5 成本

6 次 hermes 调用，其中 3 次真实推理（#3/#4/#5），单次 input_tokens ≤ 17,571（#4，默认工具集）、`-t search` 次之，output_tokens ≤ 个位数——额度消耗可忽略（与 CLAUDE.md 前提一致）。

## 1. 验收表（spec §10 M4）

（待后续任务填写）

## 2. 真跑数字

（待 Task 15 / 16 填写）

## 3. 人格产物质量

（待 Task 9 / 10 填写）

## 4. 环境缺口与降级

（待 Task 8 / 13 填写）
