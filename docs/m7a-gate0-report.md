# M7a Gate 0 报告——ark × mem0 兼容性实证

- 日期：2026-09-14
- 结论：**offline 机制全过（有条件 go）；ark live 项 BLOCKED（无 key）**——按设计档 §7 预授权的降级路径实施（fake/mock 机制全测，live 待 key 补跑）
- 脚本：`scripts/spike/m7a_gate0.py`（live/offline 双模，throwaway）

## 判定表

| # | 检查 | offline | live(ark) |
|---|---|---|---|
| C1 | 依赖导入与版本 | ✅ mem0ai 2.0.20 + chromadb 1.5.9（主依赖已加） | 同 |
| C2 | add(infer=False)/search 实跑 | ✅ 机制通（mock 常量向量，排名不作数） | ⏸ BLOCKED（ARK_API_KEY） |
| C2b | 中英/别名召回 sanity | N/A（mock 无语义） | ⏸ 同上 |
| C3 | 零生成式 LLM（llm 指向 http://127.0.0.1:1/ 全链成功） | ✅ init+add+search+get_all 全通=结构性证明 | 同机制（live 顺带复核） |
| C4 | export 往返字节相等（get_all 文本集 == 派生规范 JSONL） | ✅ | ✅（同机制，live 复核） |
| C5 | chroma 目录跨实例重开持久 | ✅ | 同 |

live 补跑法：`ARK_API_KEY=<key> uv run python scripts/spike/m7a_gate0.py`（退出码即判定）。key 建议放项目 `.env`（`config.load_env` 自动读入，与 `ODDS_API_KEY` 同惯例）。

## mem0 2.0.20 API 硬事实（进 Mem0Retriever 实现）

1. `Memory.from_config(dict)`：`vector_store.provider="chroma"`（`path`+`collection_name`）+ `embedder.provider="openai"`（`openai_base_url/api_key/model`）+ `llm` 可指向死端口——init 不触网。
2. **namespace 必填**：`add(..., user_id=...)` 缺 `user_id/agent_id/run_id` 抛 ValidationError。生产用 `user_id=f"kbmem-{league}"`（每联赛一 namespace）。
3. **search/get_all 不收顶层 user_id**：必须 `filters={"user_id": ...}`（新算法 API）。
4. 返回形状 `{"results": [{"memory": <原文文本>, ...}]}`；memory 文本 = 我们存入的规范 JSON 行——export 往返字节相等（C4 证明「文本即 JSON 行」设计成立）。
5. `add(text, infer=False)` 全链零 LLM 调用（C3 结构证明）；metadata 不依赖（锚点/日期都在 JSON 行内）。

## 卫生裁定（进实现）

- **telemetry 默认关**：mem0 默认 PostHog 上报（`MEM0_TELEMETRY` 默认 True）。`Mem0Retriever` 惰性 import 前 `os.environ.setdefault("MEM0_TELEMETRY", "False")`——进程内默认关，显式置 True 可覆盖。
- **history_db 收进 store 目录**：mem0 默认写 `~/.mem0/history.db`（全局副作用）。`from_config` 传 `history_db_path=<store_dir>/history.db` 隔离。
- **chroma 无 keyword_search**：混合检索（BM25）被禁，纯语义召回（stderr 实证）。检索质量兜底 = kbmem vs kb 消融轨本身（设计档 §11）。
- **离线测试缝**：`from_config` 的 embedder 白名单不收 `mock`/`chroma`；但 `Memory` 实例的 `embedding_model` 属性可事后替换（`m.embedding_model = MockEmbeddings()`，常量向量零网络）——`Mem0Retriever` 生产类可获离线机制覆盖（排名不作数）。

## BLOCKED 项与补跑路径

| 项 | 阻塞 | 补跑 |
|---|---|---|
| C2 live（ark embedder 实跑+跨语言召回） | 环境无 `ARK_API_KEY`（arkcli 已登录但 key 值不回显；按规不挖凭证存储） | key 入项目 `.env` 后重跑 spike（或 `fa evolve sync-mem` 真跑即天然复核） |

**诚实声明**：截至本报告，ark 端点的 embedding 调用从未被实证（连通性、模型名 `doubao-embedding` 是否存在/可用、跨语言召回质量均未验证）。上述「go」仅覆盖机制侧；live 三项补跑前，生产 kbmem 轨将稳定运行于「检索失败→整文件内联降级」形态（设计档 §8 一级降级，run 不断流）。
