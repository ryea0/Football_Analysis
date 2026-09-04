# 线 A 形态二/三提前实施设计（A_debate / A_division）+ 形态一放量协议

- 日期：2026-09-05
- 状态：分节设计已获项目负责人逐节认可（§0-§5）；待负责人复审本文档后走 writing-plans 出两份实施计划
- 决策记录（全部 2026-09-05 负责人裁定）：
  - **D-a 覆盖 gated-2**：multi-brain 设计档原门控「形态二/三须形态一数据齐方可实施」被显式推翻——二/三直接排实施，实施时点改为「M6（schema v8）合并后」。gated-2 重义为**评测判读门槛**（结论何时可信），不再是实施门槛
  - **D-b 先三后二**：形态三自包含（生成者+批评者，无外部依赖）先行；形态二有两处设计定稿因素（复盘官降范围、角色改写），复用形态三磨顺的跳间产物落库基建
  - **D-c 会话分工调整**：二期设计/计划由本会话产出（D4 原定「二期设计归复盘会话」，负责人 2026-09-05 直接指定本会话，记录在案；一期实施仍归 agentline 原会话不动）
- 关联：2026-09-04-agentline-multi-brain-design.md（宿主设计，本档为其 §4/§5 扩展位的全量化）、2026-09-04-multi-agent-charter.md（测量纪律与 gated-3）、2026-09-04-agentline-dual-track-design.md（spec §12.5 宿主）、2026-09-04-retro-attribution-design.md（关卡 3 分层检验口径的复用）
- 实施载体：`src/fa/agentline/` 新纯函数模块 `debate.py` / `division.py` + `orchestrate.py` 扩展 + schema v9/v10 迁移；dsh 触点复用 `runner.run_headless`

## 1. 决策记录与范围

### 1.1 gated-2 覆盖的科学代价（如实记档）

- 形态二/三开工时，**形态一的成本-收益基线尚未由滚动数据建立**（E2E 首批 30 场仅管线可用性证据，multi-brain §9 预写解读规则明言 n=30 无统计意义）
- 应对：**测量纪律一条不降**——成员级计分、三对照（聚合体 vs 最优单体 vs 基线/市场）、分歧即数据、全程预算审计，四个纪律在二三上全部强制
- gated-2 重义后的用法：见 §4.2「数据齐」判定协议——它决定**结论何时可判读**，不决定**何时开工**

### 1.2 产物清单

| 产物 | 路径 |
|---|---|
| 本设计档 | `docs/superpowers/specs/2026-09-05-agentline-debate-division-design.md` |
| 形态三实施计划 | `docs/superpowers/plans/2026-09-05-agentline-debate.md` |
| 形态二实施计划 | `docs/superpowers/plans/2026-09-05-agentline-division.md` |
| multi-brain 状态行更新 | 其头部状态行加指针（本档落地时一并提交） |

**不改 spec.md**：二三属 spec §12.5 线 A 的子形态，设计权威在 multi-brain 设计档与本档（与 multi-brain 自身做法一致，未动 §12.5 正文）。

## 2. 形态三 A_debate（辩论修订制，先实施）

### 2.1 结构

```
信息集 JSON（同一份，复用 fa agentline export）
   ├─ 生成者（A_base 同 prompt 同 profile）→ 全契约预测 v0
   ├─ 批评者（信息集 + v0 全文）→ 攻击 JSON（定性，禁概率数字）
   ├─ 生成者（信息集 + v0 + 攻击）→ 修订 v1
   ├─ 批评者（信息集 + v1 + 前轮攻击）→ 攻击 JSON
   └─ 生成者（…）→ 修订 v2
轮 ≤2；Python 指挥官计数调用、轮间落库、按预注册规则终止
```

### 2.2 轮次协议（预注册，实施不得偏离）

| 规则 | 取值 |
|---|---|
| 角色 | 1 生成者 + 1 批评者（同 profile `fa-agent-base`，与 A_base/A_multi 成员同源） |
| 轮数上限 | 2（一轮 = 批评+修订） |
| 提前终止 | 相邻版本概率向量 (p_home, p_draw, p_away) 的 **max-abs 变化 < 0.02** → 停轮（确定性，Python 判定；比较发生在修订版产出后、下一轮批评者调用前） |
| 预算硬上限 | 每场 ≤5 次调用（1 初版 + 2×2 轮）；超限/中断取末版落库，终版行 `budget_exhausted=1`（v9 重建时加列，见 §2.4） |
| 修订权 | 只有生成者可改数字（它是本形态的大脑）；批评者永远只产定性标签 |
| 批评者失败 | 该轮批评者调用失败（timeout/error/parse_fail）→ **不发起该轮修订**，辩论终止于当前末版；失败如实记该轮 rounds 行 status |
| 终版取值 | **最晚一个 status='ok' 的生成者版本**；v0 即失败 → 终版为该失败态（无数字，predictions 行如实落） |

### 2.3 契约

- **生成者**：复用 `contract.parse_prediction` 全契约（p_home/p_draw/p_away/p_over25/confidence/reasoning_digest/sources），三项和容差 ±0.05 归一口径照旧
- **批评者（新预注册）**：攻击 JSON，字段：
  ```json
  {"attacks": [{"label": "overconfidence|missing_context|alt_explanation|internal_inconsistency|evidence_weak",
                "reason": "≤200 字", "severity": 0.0-1.0}]}
  ```
  - label **封闭五词枚举**（与 §3.3 质询官同构，一套标签两处复用）
  - 禁概率数字：出现 p_home 等预测字段即 parse_fail（守卫与 parse_prediction 同风格）
  - attacks 可为空数组（批评者认为无从攻击 = 合法输出）
- **修订版生成者 prompt** = 信息集 + 上一版全文 + 攻击 JSON 原文；输出仍是全契约

### 2.4 落库（schema v9）

- 新表 `agentline_debate_rounds`（逐轮全审计——轮数本身是难归因变量）：
  ```sql
  CREATE TABLE agentline_debate_rounds (
      id           INTEGER PRIMARY KEY,
      match_id     INTEGER NOT NULL REFERENCES matches(id),
      round        INTEGER NOT NULL,            -- 0=初版；1..2=修订版
      role         TEXT NOT NULL CHECK (role IN ('generator','critic')),
      payload_json TEXT NOT NULL,               -- 契约字段或攻击字段的规范化 JSON
      raw_output   TEXT NOT NULL,               -- agent 原始返回全文（审计/重放）
      status       TEXT NOT NULL CHECK (status IN ('ok','parse_fail','timeout','error')),
      duration_s   REAL, harness TEXT, model TEXT, created_at TEXT NOT NULL,
      UNIQUE (match_id, round, role)
  );
  ```
- 终版入 `agentline_predictions`：`line='A_debate'`、`attributor=1`（链整体为一个归因单元）；run 摘要同时记 budget_exhausted 场次计数
- `agentline_predictions` 重建（v9 本就重建，加列零成本）：line CHECK 词表扩为 `('A_base','A_enh','A_multi','A_debate','A_division')`——五词一次到位，形态二免再重建；顺带加 `budget_exhausted INTEGER`（0/1；非 A_debate 行恒 0，A_base/A_enh/A_multi 历史行回填 0）——per 场预算审计落在行级（v7 同款影子表重放迁移）

### 2.5 评测

- **六对照**：线 P / A_base / A_enh / A_multi / A_debate / 市场收盘，同批同判据（`compare_lines` 加 A_debate 列，attributor=1）
- **成员级计分**：
  - 修订增益：v0 与终版各自对市场算 log-loss/Brier，差值 = 辩论净效应
  - 分歧即数据：攻击 severity（场均/最大值）与修订幅度（向量 L1）的相关性——高攻击低修订 = 固执信号，低攻击高修订 = 无主见信号，均入滚动报告披露行
- compare 报告新增「修订增益」段与上述披露行（口径预注册，不做事后挑选）

## 3. 形态二 A_division（角色分工制，后实施）

### 3.1 角色改写（原 sketch → 预测原生）

原 sketch「联赛专家→风控官→复盘官」是投注管线角色移植，与线 A 任务（产出概率预测）错位——「仓位审查」在无注可下的评测线里不存在。改写为：

| 跳 | 角色 | 输入 | 产出 | 禁区 |
|---|---|---|---|---|
| 1 | **历史考古官**（原复盘官） | 信息集内置历史段 | 历史要点 JSON（§3.3） | 禁概率、禁场外检索 |
| 2 | **预测者**（原联赛专家） | 信息集 + 跳1要点 | 全契约预测 JSON | —（数字归此跳） |
| 3 | **质询官**（原风控官） | 信息集 + 跳2预测 | 质询 JSON（§2.3 同构五标签） | 禁数字、禁修改权 |

- **跳1 降范围（v1）**：只消化信息集 JSON 内置历史段（H2H/近况），**不接 retro S3 案例库**；S3 建成后的外部案例供给列为**后续注册项**（届时先改本节）
- **测的命题**：结构化分工供给（历史要点 + 质询）vs 单体（A_base）的预测增量

### 3.2 跳链协议（预注册）

- 三跳串行，Python 指挥官路由、跳间产物全落库、每跳独立预算计数（每场恒 3 次调用）
- **降级规则**（任何一跳失败 → 该场不报废）：
  - 跳1 失败 → 预测者退化为原始信息集输入（无要点），该场记 `jump1_fail` flag
  - 跳2 失败 → 该场终版 status=跳2 的失败态（parse_fail/timeout/error），无终版数字
  - 跳3 失败 → 跳2 预测原样入终版，该场记 `jump3_fail` flag
- flag 落 `agentline_predictions.raw_output` 邻接的 jumps 表（§3.4），评测可按 flag 分层剔除或披露

### 3.3 跳1 契约（历史要点 JSON，新预注册）

```json
{"h2h_points": [{"point": "≤100 字", "relevance": "high|medium|low"}],
 "recent_form_points": [{"point": "≤100 字", "relevance": "high|medium|low"}]}
```

- relevance **封闭三词枚举**；两数组均可为空（历史段无信息 = 合法输出）
- 出现任何概率字段即 parse_fail

### 3.4 判决应用与落库（schema v10）

- **判决应用（沿用 M4 persona 模式，Python 确定性映射）**：v1 **只落 flag 不改数**——质询标签命中记入 `division_flags_json`（如 `{"internal_inconsistency": 1}`），数字增量是否可信留给分层检验判读（复用 retro 关卡 3 口径：MWU 双侧、audit 违规行默认剔除、允许结论为「质询无信息量」）
- 新表 `agentline_division_jumps`：
  ```sql
  CREATE TABLE agentline_division_jumps (
      id           INTEGER PRIMARY KEY,
      match_id     INTEGER NOT NULL REFERENCES matches(id),
      jump         INTEGER NOT NULL CHECK (jump IN (1,2,3)),
      role         TEXT NOT NULL CHECK (role IN ('archivist','predictor','challenger')),
      payload_json TEXT NOT NULL,
      raw_output   TEXT NOT NULL,
      status       TEXT NOT NULL CHECK (status IN ('ok','parse_fail','timeout','error')),
      duration_s   REAL, harness TEXT, model TEXT, created_at TEXT NOT NULL,
      UNIQUE (match_id, jump)
  );
  ```
- 终版 = 跳2 预测原样：`line='A_division'`、`attributor=1`；`division_flags_json` 并入 predictions 行的 `sources_json` 邻接新列**不加列**——flags 存 jumps 表跳3 行 payload，评测侧 JOIN 取（避免 predictions 再重建）
- 评测：七对照（六对照 + A_division）+ 质询标签分层检验段

## 4. 形态一放量协议与「数据齐」判定

### 4.1 放量梯子

| 级 | 批量 | 升级条件 |
|---|---|---|
| L1（当前） | 30-50 场/批（周批） | 每级出 compare 报告、复盘成本与 dsh 调用数后再升 |
| L2 | 100 场/批 | L1 累计 ≥3 批且管线零事故（无未披露的 status 异常） |
| L3 | 放量至赛季节奏 | L2 复盘通过，负责人裁定 |

### 4.2 「数据齐」判定协议（现预注册，零事后偏差）

- **口径**：A_multi 聚合行（attributor=0）**n≥300 且 ≥2 个评估窗口**（compare 报告跨批累计）→ 形态一结论可判读
- **双用途**：同时是形态二/三判读的基准时点——此前二三的数字一律按「管线可用性证据」披露（沿用 multi-brain §9 预写解读规则），不构成方向性结论
- gated-2 后半条件（形态二原门控「聚合增益有限但定性信息有增量」）随之转为**判读期的归因视角**而非开工条件

### 4.3 成本记账

- 每场 dsh 调用数：A_base 1 / A_enh 1 / A_multi 3 / A_debate ≤5 / A_division 3
- 全部进 `agentline_runs.summary`（counts + 调用数 + 批样本条件），预算审计沿用一期

## 5. schema 与成本协调

| 版本 | 内容 | 时点 |
|---|---|---|
| v8 | M6 进化线（他线，本档不动） | 合并后本线才开工 |
| v9 | 重建 `agentline_predictions`（五词表）+ 新表 `agentline_debate_rounds` | 形态三计划首批任务 |
| v10 | 新表 `agentline_division_jumps`（词表已备齐，无需重建） | 形态二计划首批任务 |

- 撞号协议：v9/v10 在 M6 v8 合并后依序取号；分层迁移 + 防重入护栏沿用 v6→v7 模式（影子表重放）；生产库自迁移
- 二三串行开工（D-b），两计划各自独立 worktree，互不双驱动

## 6. 工程与测试

- 新模块：`src/fa/agentline/debate.py`（轮次编排纯函数）、`division.py`（跳链编排纯函数）；`orchestrate.py` 加 `run_debate` / `run_division`；契约校验 `contract.py` 加 `parse_attack` / `parse_history_points`
- CLI：`fa agentline run --line A_debate|A_division`（现有命令面扩展，help 文案同步）；`compare` 增列
- 测试全离线：mock runner 注入各跳/各轮产物（含全失败、空 attacks、budget_exhausted、提前终止 ε 边界）；时间注入缝照旧；幂等重跑（UNIQUE 冲突目标）照 v7 口径
- E2E：主库直跑小批 10 场（管线可用性证据口径，同形态一 E2E 模式），先三后二

## 7. 与其他线关系

- **B 线 / C 线**：零耦合——A_debate/A_division 产出永不进推荐与落注流（§12 分账继承）；真实下注禁止
- **retro S3**：跳1 v1 降范围不依赖 S3；S3 建成后外部案例供给为后续注册项
- **gated-3（混合裁量）**：不受本次覆盖影响，仍按 charter §3 三条件，前提数据照攒
- **M6**：仅 schema 时序依赖（v8 先行），无代码耦合

## 8. 非目标

- 不做 agent 指挥 agent / meta-agent 路由（charter D2）
- 不做异构模型成员、自动加权（gated-3 前提数据先攒）
- 质询官/批评者无改数权、无触发重跑之外的中断权（重跑只由 Python 预注册规则决定）
- 不建编排框架——`orchestration/` 提取仍等重复真实出现（charter 反 YAGNI）
- 不给任何多 agent 通路接触 B 线推荐/落注流

## 9. 风险与开放问题

| 风险/开放 | 处置 |
|---|---|
| 同模型批评者与生成者相关性高，攻击可能空转 | 这正是要测的；severity-修订幅度相关性即「攻击是否被吸收」的直接度量，如实报告 |
| 一期滚动 + 三 + 二并行跑批，dsh 额度叠加 | 放量梯子控节奏；调用数全量入 `agentline_runs` 审计；必要时按线限额（负责人裁定） |
| 提前终止 ε=0.02 的终值 | 预注册不动；如需变更走本档修订（记决策），不实施期静默调 |
| 开放：severity 是否进分层检验 | 进——v1 作为分层维度之一（与五标签并列），口径随分层检验一并预注册 |
| 开放：A_debate/A_division 的 E2E 首批规模 | 沿用 10 场（形态一先例）；放量并入 §4.1 梯子统一管理 |
