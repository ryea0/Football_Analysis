# M7a 实施验证报告（2026-09-14）

计划：`docs/superpowers/plans/2026-09-14-m7a-kbmem-retrieval-track.md`（11 任务）
分支：`worktree-m7a-mem0-kbmem-spec`（基线 dd821d4，共 10 commits）

## 验证数字

| 项 | 结果 |
|---|---|
| 全量 pytest | **1114 passed, 1 skipped**（基线外新增 ~40 用例：evolve_mem 20 + db 迁移 2 + persona kbmem 3 + matchday_mem 2 + cli/runner 6 + E2E 3） |
| Gate 0 offline（docs/m7a-gate0-report.md） | C1 依赖 ✅ / C2 机制 ✅ / C3 零 LLM ✅ / C4 字节往返 ✅ / C5 重开持久 ✅（exit=0） |
| CLI 冒烟 | `sync-mem --dry-run` exit 0；`FA_MEM_RETRIEVER=fake sync-mem` exit 0；`mem-verify` exit 0；`tick`（fake）exit 0 且不被尾步炸 |
| **ark live** | **BLOCKED**——环境无 `ARK_API_KEY`（arkcli 已登录但 key 值受保护不外显；按规未挖凭证存储）。补跑：key 入项目 `.env` 后 `fa evolve sync-mem`，或 `ARK_API_KEY=... uv run python scripts/spike/m7a_gate0.py`（补验跨语言召回三查） |

## 实施偏离记录（对计划）

1. **T6+T7+T8 合一提交**（fe3e76a）：计划切三刀，实测原子——mem 轨行存在而 persona 未判时 paper 会按中性 kelly 落注（`test_am_veto_drops_persona_track_bet` 实证 2≠1），单独提交无法绿。
2. **`sync_all` 空知识库早退**（99a3f77）：计划外小改进——空 live 时 ok 且不构造 retriever，否则 tick 在「无知识文件」期会因缺 ARK key 周周告警（语义错误：无事可同步）。
3. **tests/evolve autouse 告警桩**：tick 尾步会调 `fa ops alert` → hermes → 真 TG（本机 clash 在线即真送达）——测试面必须打桩，属计划遗漏的测试卫生。
4. **C 线 E2E dumper 重构**：原「第 ≥2 次调用全覆写 nokb.prompt」在四轨下把 mem 轨 prompt 冒充 nokb（三轨时第 3 调 self 恰好无知识段而蒙混）——改为按调用序分文件，并顺势把 kbmem 内联降级断言补进 C 线 E2E。
5. **`Mem0Retriever` 内化 Gate 0 API 事实**：namespace 必填（user_id=kbmem-{league}）、search/get_all 走 `filters=`、telemetry import 前默认关（`MEM0_TELEMETRY=False`）、history_db 收进 store 目录、llm 指死端口（A3 结构性保证——任何生成式调用即时失败而非静默烧钱）。

## 呈报：实施中发现的既有缺口（非本线引入、未修复）

- **`personas_self_hash` 列无任何写入者**：C' 线（kb_self 轨）的版本戳列自 v11 建列以来从未被生产代码写入（`personas_self_consumed_hash` 存在但零调用方）——self 轨的 D6 归因目前只有快照目录可考、无行级戳。建议：独立小修（照 `personas_mem_hash` 的 INSERT-only 模式补 value.py 传递即可），或接受现状并记录。**待负责人裁定。**

## 负责人动作清单

1. **审阅分支** `worktree-m7a-mem0-kbmem-spec`（spec v0.15 + schema v12 + 第五轨全链）。
2. **补 ark key**：`ARK_API_KEY=<key>` 写入项目根 `.env`（`config.load_env` 自动读；key 全程不经代码/提交）→ 跑 `uv run fa evolve sync-mem` 真验（Gate 0 live 三项 + 首次真索引）。**key 到位前生产 kbmem 轨稳定运行于内联降级形态（run 不断流，summary 记账可辨）。**
3. 合并后生产库自迁移 v12（既有惯例）；`data/mem_cache/` 已 gitignore，无需迁移。
4. kbmem 轨随**下一窗口**生效（快照机制天然支持）；看板多轨页自动出现第五轨。
5. M7b（A_mem 臂）按 spec §10 占位，届时独立 brainstorm。
