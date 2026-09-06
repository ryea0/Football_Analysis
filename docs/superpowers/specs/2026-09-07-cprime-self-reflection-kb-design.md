# C' 线（自反思知识库）设计文档

| | |
|---|---|
| 日期 | 2026-09-07 |
| 状态 | 设计草案，待负责人评审 |
| 关联 | spec §12.7（C 线进化线）、M6 进化线设计（`2026-09-04-m6-evolution-line-design.md`）、看板 v2 B 线三轨化（`2026-09-07-dashboard-v2-b-track-enhancement.md`） |
| 前置 | C 线 v1 实施完成（脚手架闭环 + 校准实跑）、B 线三轨结算已就位 |

## 1. 背景与目标

### 1.1 问题

C 线（进化线，§12.7）采用「外置知识库文件 + hermes -z 反思 + 人审关卡」的形态，验证"知识库进化能不能提升 B 线判决质量"。但这留下一个未回答的问题：

> **如果换一种反思风格/知识组织方式，进化效果会不会不同？**

具体来说：
- C 线的反思是"结构化、学术化、证据引用强制"的风格（受契约 JSON 和人审关卡约束）
- 如果反思更自由、更像"资深球探记笔记"，知识库会不会更有用？
- 更松散的知识组织（时间线式、故事化）vs 结构化知识（三段式、分节、锚点），哪种对判决质量增益更大？

这个问题无法用 C 线回答——因为 C 线只有一种反思风格、一种知识结构。

### 1.2 目标

新增 **C' 线（自反思知识库对照线）**，与 C 线平行运行，回答以下问题：

1. **风格差异**：不同反思策略（结构化 vs 自由式）产出的知识库，对判决质量的影响有没有可测量的差异？
2. **组织差异**：不同知识结构（三段式分类 vs 时间线笔记），哪种增益更大？
3. **人审作用**：无人审的自反思（C' 线 v1）vs 有人审把关（C 线），差异主要在质量还是在速度？

**P0 目标**：第四条对照轨跑起来，与 C 线同窗口、同证据、同度量，知识库风格/结构不同，产出可比较的结果。

**诚实预注册**：结论可以是"无差异"、"C 线更好"、"C' 线更好"中的任意一种——三种结果都有信息量。

### 1.3 非目标（明确不做）

- 不做 hermes 常驻 / gateway / 内置 memory（v1 用外置文件，与 C 线同样的 `hermes -z` 纯函数调用）
- 不做 skill 进化 / 方法进化（只进化知识内容，不进化分析框架）
- 不做实时进化（严格窗口冻结制，与 C 线同节奏）
- 不替换 C 线（即使 C' 线效果更好，C 线也继续跑——C 线有人审把关，是生产级；C' 线是实验级）
- 不改变 B 线 §12.3 判据口径（model_persona vs model_only 仍是主判决）
- 不给 C' 线任何 DB 写权、落注通道、真实下单权限
- 不做五联赛知识迁移（五个联赛独立进化，互不干扰）

## 2. 定位与结论分账

- **性质**：C 线的对照实验线，同属"元循环"家族
- **层级**：与 C 线平行，作用于 B 线消费的版本化工件
- **隔离**：C' 线知识库与 C 线知识库物理隔离（不同目录、不同表、不同 B 线轨道）
- **结论分账**：C' 线有效性单独记账，不混入 C 线结论，不影响 B 线 §12.3 判据
- **永不直接进生产推荐流**：C' 线的知识只供给 B 线第四轨（实验轨），不进入 model_persona 生产轨

## 3. 与 C 线的核心差异

| 维度 | C 线（外置知识库 + 人审） | C' 线（自反思知识库） |
|---|---|---|
| 知识结构 | 三段式（结构性认知/时效段/教训段） | 时间线笔记（按窗口累积，故事化） |
| 反思风格 | 结构化、证据强制引用、JSON 契约 | 自由式、球探笔记风格、markdown 直接输出 |
| 人审关卡 | 有（merge/reject/shelve 全量 Ruling） | v1 无（自动落库，事后可人工回滚） |
| 输出契约 | JSON（appends/amendments/deprecations） | Markdown 全文（整份知识库重写或追加） |
| 旧条目处理 | 强制 amend/deprecate，防回音室 | 建议但不强制，靠内容质量自证 |
| 版本戳 | personas_hash（内容 hash） | 同机制，独立 hash |
| B 线轨道 | model_persona（生产轨） | model_persona_kb_self（实验第四轨） |

**相同点**：窗口节奏相同（42 天）、输入证据相同（同一份窗口聚合数据）、度量指标相同（ROI/CLV/veto 率等）、都用 `hermes -z` 纯函数调用、都有版本戳入账、都遵循窗口冻结制。

## 4. 知识文件结构

### 4.1 目录布局

```
personas/
├── epl.md / bundesliga.md / ...      # 人格文件（共用，不变）
├── knowledge/                        # C 线知识库（三段式，人审）
│   ├── epl.md / laliga.md / ...
│   └── ...
└── knowledge_self/                   # C' 线知识库（时间线笔记，自反思）
    ├── epl.md / laliga.md / ...
    └── ...
```

### 4.2 知识文件格式（时间线笔记式）

每个联赛一个 markdown 文件，按窗口时间线组织：

```markdown
# 英超知识库（自反思·时间线）

## 总原则
（窗口 0 初始化时写入：球探身份、关注维度、记录风格）

---

## W1（2026-09-04 ~ 2026-10-15）

### 本轮观察
- 升班马伊普斯维奇客场逼平切尔西，不是运气——他们的高位逼抢
  让切尔西后场出球持续失误。对阵其他控球型球队可能也有奇效。
- 曼城客场对狼队只赢 1 球，哈兰德缺阵影响比预期大。
  接下来 2 轮哈兰德出战存疑的比赛，曼城盘口可能偏深。

### 纠正旧认知
- （如果有对之前条目的修正，写在这里）

### 待验证假设
- 阿森纳周中欧冠后联赛客场表现下滑——下轮 vs 伯恩茅斯验证
```

**设计意图**：
- 更像人记笔记的方式，不强制结构化
- 每个窗口有明确的时间边界，方便回溯
- "纠正旧认知" section 鼓励回音室自检，但不强制
- "待验证假设" section 鼓励前瞻性思考

### 4.3 初始化

v1 启动时（W1 开始前），C' 线知识库为空文件，总原则 section 由 Python 侧写入固定模板（球探身份 + 记录规范）。**不从 C 线知识库迁移任何内容**——保证 C' 线是从零开始自进化的纯实验，不受 C 线知识污染。

## 5. 进化闭环

```
┌─ 窗口内（treatment 冻结）────────────────────────────┐
│                                                      │
│  B 线四轨并行跑批：                                     │
│    model_only / model_persona / model_persona_nokb    │
│    model_persona_kb_self（C' 线，读 knowledge_self/） │
│  每条判决记 strategy + personas_self_hash             │
│                                                      │
└───────────────────────┬──────────────────────────────┘
                        │ 窗口收口
                        ▼
┌─ C' 线进化事件（离线、串行）─────────────────────────┐
│                                                      │
│  ① 证据导出：同 C 线完全一样的窗口聚合 JSON             │
│     （判决 vs 结果、双轨差、CLV、按 bet 类型分拆）        │
│                                                      │
│  ② hermes -z 反思（每个联赛独立 1 次调用）：             │
│     输入 = 当前 knowledge_self/{league}.md            │
│          + 窗口证据 JSON                               │
│          + 反思 prompt（球探笔记风格）                  │
│     输出 = 更新后的完整 markdown 文件                   │
│                                                      │
│  ③ Python 侧：                                        │
│     - contract 校验（必须是合法 markdown、必须包含       │
│       "本轮观察" section、总字符数在合理范围内）          │
│     - 与上一版本做 unified diff                       │
│     - 写入 knowledge_self/{league}.md                 │
│     - 计算新 hash，落 evolution_self_runs 表           │
│                                                      │
│  ④ 人审（v1 可选，默认关闭）：                           │
│     - 生成 diff 报告，通知人看                          │
│     - 人可以回滚（git revert），但不做逐行审查           │
│     - 回滚操作记 evolution_self_rulings 表             │
│                                                      │
│  ⑤ 生成快照：evolution/snapshots_self/w{idx+1}/       │
│                                                      │
└───────────────────────┬──────────────────────────────┘
                        │ 下一窗口开始
                        ▼
           B 线第四轨读新快照，版本戳更新
```

### 5.1 反思 prompt 设计

核心指令（球探笔记风格）：

> 你是一位有 20 年经验的足球球探，专注于[联赛名]。
> 给你看这个窗口的比赛结果和我们系统的判决表现。
> 请把你的观察、教训、待验证的假设，记在你的笔记本里。
> 直接输出更新后的完整 markdown 笔记本。
> 要求：
> 1. 保留之前的所有内容（旧窗口的笔记不要删）
> 2. 新增本窗口的 section（本轮观察 / 纠正旧认知 / 待验证假设）
> 3. 每条观察尽量具体，引用具体比赛
> 4. 如果你发现之前的观察被证伪了，写在"纠正旧认知"里
> 5. 不要编造没有证据的内容

### 5.2 降级路径

| 失败类型 | 降级方式 |
|---|---|
| hermes 调用超时 / exit 非 0 | 沿用旧知识库，记 `status='timeout'/'exit'` |
| 输出格式不对（不是合法 markdown / 缺 section） | 沿用旧知识库，记 `status='contract'` |
| 内容异常（比如字符数暴涨 10 倍 / 空内容） | 沿用旧知识库，记 `status='sanity'` |
| 整个进化事件失败 | 沿用旧快照，下一窗口再说，不影响 B 线运行 |

**降级原则**：任何异常都不中断 B 线，C' 线静默停摆（沿用旧知识），事件台账记录原因。

## 6. 数据模型

沿用分层迁移协议（新建 DDL 全列、存量库走迁移路径、幂等护栏、防重入）。

C' 线自有表（与 C 线表结构同构，但独立命名空间）：

```sql
CREATE TABLE IF NOT EXISTS evolution_self_windows (
    id           INTEGER PRIMARY KEY,
    idx          INTEGER NOT NULL UNIQUE,
    opened_at    TEXT NOT NULL,
    closes_at    TEXT NOT NULL,
    reflected_at TEXT,
    closed_at    TEXT
);

CREATE TABLE IF NOT EXISTS evolution_self_runs (
    id               INTEGER PRIMARY KEY,
    window_id        INTEGER NOT NULL REFERENCES evolution_self_windows(id),
    league           TEXT NOT NULL,
    kb_hash_before   TEXT NOT NULL,
    kb_hash_after    TEXT,
    status           TEXT NOT NULL
        CHECK (status IN ('ok','no_change','timeout','exit',
                          'contract','sanity','error')),
    no_change_reason TEXT,
    added_chars      INTEGER,       -- 本窗口新增字符数
    changed_lines    INTEGER,       -- diff 行数
    duration_s       REAL NOT NULL,
    created_at       TEXT NOT NULL,
    UNIQUE (window_id, league)
);

CREATE TABLE IF NOT EXISTS evolution_self_rulings (
    id            INTEGER PRIMARY KEY,
    run_id        INTEGER NOT NULL REFERENCES evolution_self_runs(id),
    ruling        TEXT NOT NULL CHECK (ruling IN ('kept','rolled_back')),
    note          TEXT NOT NULL,
    kb_hash_final TEXT,
    created_at    TEXT NOT NULL
);
```

`recommendations` / `bets` 表新增 strategy 枚举值 `model_persona_kb_self`（schema v8 已为重建表，可在 v9 中扩展，或 v8 迁移时直接加入）。

### 6.1 版本戳

每条 C' 线轨道的判决/下注记录 `personas_self_hash` 列（`recommendations` 和 `bets` 表各加一列），指向当时 `personas/knowledge_self/` 目录的内容 hash。可归因要求与 C 线一致：任意一行能回答「当时 C' 线知识库是什么」。

## 7. B 线第四轨接入

### 7.1 value.py STRATEGIES 扩展

现有三轨：`model_only` / `model_persona` / `model_persona_nokb`，新增第四轨 `model_persona_kb_self`。

第四轨的判决逻辑：
- 模型概率 = 同 model_only（共享模型层）
- persona 判决 = 人格文件 + C' 线知识库（`knowledge_self/`）
- 与 model_persona 的区别：知识库来源不同（C 线三段式 vs C' 线时间线式）
- 与 model_persona_nokb 的区别：有知识库（只是来源不同）

### 7.2 结算

沿用现有 `settle_paper_bets` 机制，按 strategy 分账。第四轨独立 bankroll（初始 1000，与其他轨道同起点）。

### 7.3 TG 推送

C' 线第四轨**不进 TG 推送正文**（与 nokb 第三轨同待遇）——推送保持双轨口径（model_only / model_persona），避免信息过载。C' 线数据只在看板和 `fa status` 中可见。

## 8. 看板展示

看板 v2（`dashboard-v2-b-track-enhancement.md`）已做三轨动态化改造，C' 线第四轨接入成本低：

### 8.1 页3 「三轨对比」→「多轨对比」

- 动态扩展为 4 列（或 N 列，随 strategy 枚举自动增长）
- 累计 P&L 曲线：4 条线同图
- CLV 对比柱：4 根柱子
- 底部说明增补：第四轨为 C' 线实验对照（自反思知识库，无人审），不参与 §12.3 判决

### 8.2 新增「C 线家族对比」小板块

| 对比维度 | C 线（外置+人审） | C' 线（自反思） |
|---|---|---|
| ROI | x% | y% |
| CLV 中位数 | x% | y% |
| 注数 | N | M |
| veto 率 | x% | y% |
| 知识库条目数 / 字符数 | N 条 / M 字 | X 字 |
| 推翻旧条目比例 | x% | y%（回音室指标） |
| 进化事件成功率 | x% | y% |

### 8.3 版本时间轴

C 线版本时间轴旁，叠加 C' 线版本切换标记（用不同颜色/线型区分）。

## 9. 判据预注册（诚实条款）

### 9.1 v1 不设统计门槛

与 C 线同款诚实条款：每版本每联赛几十个判决，统计分辨不可达。**效果判断以描述性为主，不做显著性检验。**

### 9.2 监控指标（描述性）

| 指标 | 用途 | 注意 |
|---|---|---|
| ROI 差（C' vs C） | 顶层效果对比 | 样本量小，波动大，不当结论 |
| CLV 中位数差 | 金标准对比 | 比 ROI 稳定，但仍受样本限制 |
| veto 率差 | 行为差异 | KB 不同会不会改变干预强度 |
| 知识库字符数 / 增长率 | 膨胀监控 | 连续膨胀不收敛 = 坏信号 |
| 「纠正旧认知」条目占比 | 回音室指标 | 占比太低 = 只增不改，回音室风险 |
| 进化事件成功率 | 稳定性 | contract 失败率高 = 反思 prompt 需调 |
| 人审回滚率（如果开了人审） | 质量底线 | 回滚率高 = 自反思质量差 |

### 9.3 终止条件（预注册）

如果连续 **2 个窗口**出现以下任一情况，C' 线停跑归档：

1. **知识库膨胀失控**：单窗口字符增长 > 200%，且内容质量检查（人工抽查 3 条）发现大量重复/无关内容
2. **ROI 持续劣于 nokb**：两个窗口累计 ROI 都低于 model_persona_nokb（无知识库轨）
3. **契约失败率 > 30%**：反思输出经常格式不对，维护成本过高
4. **与 C 线无差异**：两个窗口后，ROI / CLV / veto 率与 C 线基本重合（差异 < 0.5%），继续跑信息量太低

**终止的结论**：如实记录，不删数据，归档留作以后参考。

### 9.4 统计判据 = 后续注册项

≥2 个窗口数据积累后，评估是否需要注册正式的统计判据。**届时先改本节再启用，不得回溯套用。**

## 10. 实施分期

### Stage 0：脚手架（T 估：3-5 天）

- [ ] 新建 `personas/knowledge_self/` 目录与初始化文件
- [ ] `src/fa/evolve_self/` 模块（镜像 C 线 `src/fa/evolve/` 结构）
  - `windows.py`（与 C 线同窗口节奏，复用计算逻辑）
  - `evidence.py`（与 C 线同款证据导出）
  - `reflect.py`（自由式反思 + markdown 校验）
  - `knowledge.py`（knowledge_self 读写 + hash 计算 + 快照）
  - `apply.py`（diff + 写入，无人审自动落账）
- [ ] 数据库迁移（schema v8 → v9）
  - `recommendations` / `bets` 扩 strategy 枚举 + `personas_self_hash` 列
  - C' 线自有三表
- [ ] CLI 子命令：`fa evolve-self`（`tick` / `status` / `diff` / `rollback`）
- [ ] 测试脚手架：mock hermes + 临时库 + 可编程 fixture

### Stage 1：接入 B 线第四轨（T 估：2-3 天）

- [ ] `value.py` STRATEGIES 扩展第四轨
- [ ] `paper.py` 结算第四轨
- [ ] `persona/caller.py` 支持 C' 线知识库注入
- [ ] 端到端测试：一窗口完整闭环（跑批→收口→反思→新窗口→新版本戳）

### Stage 2：看板接入（T 估：1-2 天）

- [ ] 页3 多轨动态化（已在看板 v2 部分完成，扩展到第四轨）
- [ ] C 线家族对比板块
- [ ] 版本时间轴叠加

### Stage 3：W1 启动与校准

- [ ] 知识库初始化（空模板）
- [ ] 接入 cron 周检 tick（与 C 线 tick 串行，先后顺序不影响）
- [ ] 校准实跑（用已有数据跑一次反思，预期 no_change 或少量内容，不入正式台账）
- [ ] 看板验证：第四轨数据可见、曲线正确

**总 T 估**：约 1-2 周（不含等待窗口数据的时间）。依赖 C 线 v1 实施完成。

## 11. 风险与应对

| 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|
| 回音室效应（只增不改、自我强化） | 中 | 高 | 监控「纠正旧认知」占比；连续 2 窗口为 0 触发告警 |
| 知识库膨胀失控 | 中 | 中 | 字符数上限（比如单联赛 10000 字）；超限触发截断或告警 |
| 反思输出格式不稳定 | 低-中 | 低 | 多层校验（markdown 解析 + section 检查 + 长度检查）；失败就沿用旧版 |
| 与 C 线无差异（实验无信息量） | 中 | 中 | 预注册终止条件 ④，连续 2 窗口无差异就停 |
| 维护成本过高（两套进化代码） | 中 | 中 | C' 线尽量复用 C 线代码（evidence.py、窗口计算等）；差异控制在 reflect.py 和 knowledge.py |
| 混淆 C 线和 C' 线的结果 | 低 | 高 | 命名清晰（`_self` 后缀贯穿所有表/目录/轨道名）；看板和报告明确标注 |

## 12. 关系图

```
A 线（研究评测·永久）
  ├─ 线 P（确定性管线）
  ├─ 线 A（dsh 范式对比·长期滚动）
  └─ retro（复盘归因·多阶段）

B 线（Paper 运营·6 周观察期）
  ├─ model_only 轨 ← 基线
  ├─ model_persona 轨 ← 人格 + C 线知识库（生产）
  ├─ model_persona_nokb 轨 ← 人格无知识库（C 线消融对照）
  └─ model_persona_kb_self 轨 ← 人格 + C' 线知识库（实验）
         ↑                    ↑
    C 线（进化·人审）    C' 线（自反思·无人审）
    三段式结构化        时间线笔记式
```

## 13. 后续方向（v2+，不进 v1）

- **方案 B 验证**：如果 C' 线证明有价值，再试 hermes 内置 memory / 会话文件持久化方案，对比外置文件 vs 内置记忆的差异
- **skill 进化**：验证知识进化有用后，再考虑要不要进化分析方法（风险更高，需单独设计）
- **多联赛迁移学习**：五个联赛的知识能不能互相借鉴？怎么避免污染？
- **自动质量评估**：不依赖人审，用自动化指标评估知识库质量
