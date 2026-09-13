# M7a（mem0 记忆基建·阶段一：kbmem 检索轨）设计文档

- 日期：2026-09-13
- 状态：设计已批准（2026-09-13 负责人会话拍板），待 spec 合入与实施计划
- 决策记录（本次会话逐项裁定）：① 方向 = 先 mem0 当 C 线 kb 后端、后 A_mem 臂（两阶段，M7a/M7b）；② agent 无状态**维持不动**（M6 D1 不碰，mem0 按「外置检索层」接入）；③ 方案 A 派生索引（被否：B 全托管、C 自建不接 mem0，理由见决策表）；④ 轨道策略 = 新增第五轨，不换 kb 轨引擎；⑤ 上线时点 = 下一窗口起点；⑥ hermes 调用量 +25%（4→5 轨）按 kb_self 先例接受，全量加轨
- 关联：spec §12.7（C 线进化）、M6 设计（`2026-09-04-m6-evolution-line-design.md`，D1/D2/D4/D5/D6）、M6 实施设计（同日 implementation-design）、C' 线设计（`2026-09-07-cprime-self-reflection-kb-design.md`）、§12.5 范式对比线（M7b 关联）

## 1. 背景与动机

**mem0 是什么**：开源记忆层（Apache 2.0，pip `mem0ai`），提供 `add`（LLM 抽取式记忆生产）/ `search`（semantic + BM25 + 实体混合检索）/ 窗口化状态管理。2026-04 新算法为单遍 ADD-only 抽取（无 UPDATE/DELETE）。

**为什么接**：两个驱动。

1. **C 线读取侧的真实短板**——当前 kb 轨把整份联赛知识文件（≤2400 字符）全文内联进判决 prompt：注入内容与比赛无关（本场没涉及的球队/裁判条目也占预算）；2400 字符上限本质是 prompt 预算约束而非知识容量约束。检索式注入（按场 top-k）解除该约束并提升针对性。这构成 M6 D5「最简形态不够用」的正式触发证据之一（另一触发理由见 2）。
2. **M7b（A_mem 臂）铺基建**——线 A 加「有记忆 agent」臂需要 mem0 完整记忆管线的本地实跑经验（ark 兼容性、部署形态、检索质量），M7a 是低风险的前置台阶。

**与 M6 既有裁定的关系（为什么这不违反 D1）**：D1 禁的是 agent **内部**记忆（hermes/dsh 会话状态）。本设计里 agent 仍是纯函数——persona 输入 = 信息集 + 检索到的条目文本，输出 = JSON 契约不变。mem0 是**外置派生索引**，角色等同「kb 文件的检索视图」，与「状态外置为 git 版本化文件」同族。真正动的是 D5（无检索无 embedding——其原文即为「最简形态被证明不够用之前，复杂机制即浪费；升级条件写进滚动报告」，本设计即该升级条件的正式触发）；D2 窗口冻结与 D6 版本戳通过快照机制适配（见 §4）。

## 2. 定位与结论分账

- **M7 = mem0 记忆基建**，两阶段：
  - **M7a（本 spec）**：kbmem 检索轨——mem0 作 C 线知识库的派生检索索引，B 线新增第五消费轨
  - **M7b（仅占位，§12）**：A_mem 臂——线 A 范式对比加「有记忆 dsh」臂，mem0 完整记忆管线（infer=True），届时独立 brainstorm→spec
- 性质：C 线的**读取侧升级**。权威知识生产链路（hermes 反思 → contract 暂存 → 人审 Ruling → 合并 markdown）**一行不动**
- 结论分账沿用 §12 协议：kbmem 轨与 kb 轨构成**同源消融对**——同一权威知识源、两种消费形态（检索 top-k vs 全文内联），轨道级对比进看板多轨页（自动发现），不与既有 §12.3 判据混排
- C' 线（self 轨）不动：继续作「无人审自动落账」对照

## 3. 核心决策

| # | 决策 | 理由（含被否方案） |
|---|---|---|
| A1 | **派生索引**：markdown 为唯一权威源，mem0 为只读检索投影（条目 `add(infer=False)` 原文入库） | 关卡/git 回滚/版本戳零变动；索引可从权威源幂等重建（纯函数性质、可测试性保住）；索引坏了不伤权威数据。被否 **B 全托管**（mem0 为存储权威）：关卡载体从 git diff 变 staging diff、快照/回滚/归因链重做、与「git 为回滚机制」既有裁定（7f7a981）冲突。被否 **C 自建检索**（sqlite-vec/词法）：语义召回避不开 embedding，且 M7b 仍需引入 mem0，绕路 |
| A2 | **新增第五轨 `model_persona_kbmem`**，不换 kb 轨引擎 | §12.3 前向窗口 treatment 冻结——换引擎=污染既有对照；加轨先例：nokb 轨、kb_self 轨。kbmem vs kb = 干净消融对（§2） |
| A3 | **`infer=False` 全程零生成式 LLM** | 确定性主控边界最小化：phase 1 进程内只需要 embedder，无生成式调用。mem0 的记忆生产能力（infer=True 抽取/实体链接）属 M7b 范畴 |
| A4 | **窗口冻结载体 = JSONL 快照进 git**（`evolution/snapshots_mem/w{idx}/mem-{league}.jsonl`），运行时索引为纯派生物（缓存键 = JSONL 内容 hash） | 「git 为回滚机制」裁定延续：回滚 = git revert JSONL；快照可 diff 可审计；索引目录（`data/` 下，gitignore）随时可删可重建 |
| A5 | **上线时点 = 下一窗口起点** | 窗口快照机制天然支持；不插当前窗口（treatment 冻结） |
| A6 | sync 步挂现有 **evolve cron job（周日 03:17）尾部**，不新增 job | 六 job 现状维持；与 C 线离线串行哲学一致 |
| A7 | **降级三级**：读取侧故障→该轨退整文件内联（summary 记账）；写入侧故障→C 线式静默停摆；一致性漂移→hash 对账拦截 | 与 TG 推送降级（run 永不断流）、C 线失败停摆、对账拦截三个既有哲学同构 |
| A8 | **embedder = ark OpenAI 兼容端点 + doubao-embedding，锁版本** | 国内直连免代理（对照：OpenAI/云平台需过 clash）；成本 1 元/月级（§10）；锁版本防上游漂移（2026-04 mem0 刚换新算法） |

## 4. 架构与数据流

```
写入侧（每窗口一次，离线，跟在 Ruling(merged) 合并之后）：
  hermes 反思 → contract → 人审 Ruling → 合并 markdown（现有链路，不动）
      └→ 新增尾步 sync-mem：
           解析 approved personas/knowledge/{league}.md（复用 parse_kb 的 Entry）
           → 全量重建活索引：逐条 mem0 add(infer=False, metadata={league,section,anchor,expiry})
           → hash 对账：「从活索引导出的条目集」与「从权威 markdown 直接
             推导的条目集」各自规范序列化（排序 + 规范 JSON）后 hash 必须相等
             ——拦住 mem0 往返造成的任何文本漂移
           → 不符：索引作废重建 + 告警；B 线继续用上一份有效快照

读取侧（每比赛日 run）：
  matchday run 开始
    → ensure_mem_window_snapshot()：本窗首调用者把活 JSONL 原子拷入
        evolution/snapshots_mem/w{idx}/（与 md 快照同款 os.replace 机制）
    → personas_mem_consumed_hash() 入 runs（虚拟键 = personas 活人格文件
        + 本窗 mem JSONL 快照 hash；先快照后取 hash，归因命根顺序不变）
    → 每场组装 kbmem 轨 prompt：
        从冻结 JSONL 重建只读索引（缓存命中 = 同 hash 复用，否则重建）
        → mem0 search(query, filters={league}, top_k)
        → 命中条目按「[锚点|日期] 文本」渲染注入（格式仿 kb 轨渲染）

窗口语义：git 里只有 JSONL（可审/可 diff/可回滚）；运行时索引纯派生；
  窗口中途的 sync 对本窗口不可见（快照已冻结）——与 D2 冻结制严格同构
```

检索 query 构造：`联赛 + 主队 + 客队 + 候选市场`（中英混合原文，含既有别名口径的规范队名）；跨语言召回（阿森纳/Arsenal/枪手）由 embedding 承担，Gate 0 实测（§7）。

## 5. 组件与接口

| 组件 | 职责 |
|---|---|
| `src/fa/evolve_mem/`（与 evolve/、evolve_self/ 并列） | `index.py`：mem0 客户端构造（config 指向 ark embedder）、全量重建、hash 对账；`retrieve.py`：按场检索（query 构造 + filters + top_k + 条目渲染）；`snapshot.py`：窗口冻结 + `personas_mem_consumed_hash` |
| mem0 配置 | OSS library **in-process**（无常驻服务、不自托管 server、不用云平台）；embedder 走 ark OpenAI 兼容端点（`ARK_API_KEY` 环境变量，与 `ODDS_API_KEY` 同惯例）；`infer=False` |
| CLI | `fa evolve sync-mem [--dry-run]`（重建 + 对账，手动入口；cron evolve job 尾部自动调）；`fa evolve mem-verify`（对账巡检，可手动/可挂 watchdog，默认不挂） |
| persona 接入 | `persona/apply.py` 三轨扩四轨（kb→nokb→self→**kbmem**），复用既有分轨降级机制（snapshot_failed_tracks / read_failed_tracks）；kbmem 轨注入文本来自 `retrieve.py`，其余契约（输出 JSON、禁工具条款）不变 |
| 看板 | 多轨页自动发现第五轨，零配置 |

## 6. schema v12（按撞号协议：占下一号、分层迁移、防重入）

- `recommendations.strategy` CHECK 枚举 + `model_persona_kbmem`
- 新列 `personas_mem_hash`（与 `personas_hash` / `personas_self_hash` 并列，D6 版本戳第三列）
- 生产库留给合并后自迁移（既有惯例）

## 7. Gate 0：ark 兼容性 spike（先行拦截，任一失败即回方案对比重议）

1. mem0 embedder 配置指向 ark 端点 + doubao-embedding：`add(infer=False)` / `search` 跑通
2. 实证 `infer=False` 零生成式 LLM 调用（若 mem0 强制 LLM config，挂 ark chat 模型但计数调用=0）
3. 召回 sanity：中文/别名/德语队名交叉检索（Arsenal / 阿森纳 / 枪手；Bayern / 拜仁）top_k 命中正确条目
4. 依赖树体检：`mem0ai` 传递依赖（openai SDK 等）规模、锁定与归属（主依赖组 vs 独立 `mem0` 组——实施定）

spike 产物为结论文档 + throwaway 脚本，不进主线。

## 8. 错误处理与降级

| 故障面 | 行为 |
|---|---|
| 读取侧（matchday run 中 embedder/mem0 不可用） | kbmem 轨注入退化为**整文件内联**（读同一份冻结 JSONL 全文渲染，与 kb 轨同形态）；`runs.summary` 记 `kbmem_degraded=inline`——降级场次可单独剔除，归因诚实；run 永不因 mem0 断流 |
| 写入侧（sync-mem 重建/对账失败） | C 线哲学：静默停摆——记事件台账 + `fa ops alert` TG 告警；B 线继续用上一份有效快照 |
| 一致性（活索引与权威源漂移） | hash 对账拦截（sync 尾步 + mem-verify）；漂移 = 索引作废重建；权威数据永不被索引反向污染 |
| 快照/缓存错配 | 缓存键 = JSONL hash，不匹配即重建（幂等） |
| 首窗无 JSONL 快照（sync 未跑过） | 走既有「快照建不起→该轨降级」路径（snapshot_failed_tracks）：kbmem 轨不注入知识段照常出判决，记降级 |

## 9. 测试策略（mock 与实跑同代码路径）

- **fake embedder**（确定性向量，词哈希投影）从 config 层注入——单测零网络零 ark，代码路径与生产仅差 config
- 冻结 JSONL → 重建索引 → 检索断言（top_k 截断、league 过滤、锚点/日期渲染）
- 对账测试：篡改权威 markdown 后 sync 必须检出 hash 不匹配并作废索引
- 降级测试：embedder 抛错 → kbmem 轨落回整文件内联 + summary 记账 + 其余轨不受累
- E2E：上线首窗拿真实 matchday run 验一遍（沿「校准实跑」惯例）

## 10. 成本（descriptive，Gate 0 后修正）

- embedding：窗口重建 ≈5 联赛 × ≤2400 字符（≈12K token/窗口）+ 每场 query ~100 token × 10–30 场/日 → **月成本 1 元以内量级**
- 生成式 LLM 新增调用：**0**（A3）
- hermes 调用：+25%（persona 4 轨→5 轨），kb_self 上线时 3→4 轨有先例；Odds API 额度零影响（节流梯子不涉 hermes）
- 零新增常驻服务

## 11. 风险

| 风险 | 处置 |
|---|---|
| ark embedder 兼容性不符预期 | Gate 0 拦截；失败回方案对比（C 方案复活重议） |
| 检索质量差（召回错条目比全文内联更糟） | kbmem vs kb 消融对本身即检测器；滚动报告披露检索命中率与降级率 |
| mem0 依赖树膨胀污染主环境 | Gate 0 第 4 项体检 + 锁版本 |
| mem0 上游 breaking change（2026-04 刚换算法） | 锁版本；JSONL 为自有格式——索引可换引擎重建，**不锁死 mem0** |
| 快照 JSONL 膨胀 | 上限继承 markdown KB_MAX_CHARS 权威约束（索引不放大存储）；M7b 记忆无上限问题届时另议 |

## 12. M7b 占位：A_mem 臂（本 spec 只立碑不展开）

- 形态：dsh 挂 mem0 完整记忆管线（独立 namespace `agentline_mem`，`infer=True`：agent 自主 add/search、跨场累积）——mem0 记忆生产的主场
- 方法论：新臂不动既有臂（A_base/A_enh/A_multi/A_debate/A_division），换臂不换轨；对 §12.5「每场独立无状态」的扩展需**显式决策记录 + 预注册判据**
- 依赖：M7a 基建落地 + Gate 0 结论
- 届时独立 brainstorm → 独立 spec，本节仅为路线图锚点

## 13. spec 增补草案（待实施会话合入 spec.md）

> **§12.7 增补（kbmem 检索层）**：C 线知识库读取侧升级——markdown 仍为唯一权威源与人审载体，mem0（OSS library、`infer=False`、ark embedder）为其派生检索索引：合并后全量重建 + hash 对账，窗口冻结载体为 JSONL 快照（git 版本化，运行时索引纯派生）。B 线新增第五消费轨 `model_persona_kbmem`（检索 top-k 注入，与 kb 轨构成同源消融对）；读取侧故障降级为整文件内联并记 `kbmem_degraded`。D5「无检索」条款由本增补正式触发升级（触发理由：检索针对性需求 + M7b 基建前置）。agent 无状态（D1）不变。

§10 里程碑表新增：

| 里程碑 | 内容 | 验收判据 | 依赖 |
|---|---|---|---|
| M7a | kbmem 检索轨：evolve_mem 模块 + sync-mem/mem-verify CLI + schema v12（第五轨 + personas_mem_hash）+ 降级三级 | Gate 0 四项全过；闭环实跑（合并→重建→对账→冻结→run 检索注入→版本戳入账）；降级路径实测（embedder 故障→内联 + 记账）；看板多轨页出现 kbmem 轨 | C 线（M6）已合入 |
| M7b | A_mem 臂（占位，另行立项） | 届时预注册 | M7a + 决策记录 |

## 14. 非目标

- 不动权威知识生产链路（反思/暂存/人审/合并）
- 不给 persona/hermes 任何记忆或会话状态（D1 维持）
- 不做 `infer=True` 记忆生产、不建实体链接知识图谱（M7b 范畴）
- 不自托管 mem0 server、不用 mem0 云平台、不建常驻服务
- 不动 C' 线（self 轨）与既有四轨
- 不因 M7a 改 M5 节流优先级
- 真实下注依然禁止（§7.2 继承）

## 15. 开放问题（实施定）

- top_k 默认值与注入预算（config 化，初值 8）
- mem0ai 依赖归属（主依赖组 vs 独立组）——Gate 0 第 4 项定
- 检索 query 的别名扩展（v1 默认只用规范队名，靠跨语言 embedding；不足再加）
- mem-verify 是否挂 watchdog（默认不挂，仅手动 + sync 尾步对账）
