# fa — 足球赛事量化分析与投注推荐系统 · 设计规格

| | |
|---|---|
| 项目名 | fa（Football Analysis） |
| 版本 | v0.5（确认稿） |
| 日期 | 2026-09-03 |
| 状态 | M1 完成；M2 判决 NO-GO（+3.02%，docs/m2-verdict.md）；**项目负责人批准 §12 双线并存协议——B 线（M3-M5 paper 模式）在推翻顺序关卡的前提下启动**（2026-09-03） |

> v0.1 → v0.2 变更：确认 M2 go/no-go 判据（log-loss 劣化 ≤1% 即 go）、persona 判决映射维持乘法调整（§6.4）、比赛日 17:00 定为更新版报告（§9.6）。
> v0.2 → v0.3 变更：明确球员级建模与进球时间的一期边界——伤停盲区由 persona 补偿并经 A/B 量化（§4.5）；进球时间为滚球二期核心输入、对赛前市场一阶冗余（§9.8）；M2 增加可选「近 6 场状态协变量」消融实验。
> v0.3 → v0.4 变更：Provider 体系成型——OddsProvider 家族（历史 CSV / Odds API / 二期滚球与交易所）+ 新增 ExecutionProvider（Paper 模拟盘为默认实现，真实平台二期以官方 API + 地区合规为前提）；`bets` 增 `mode` 字段，模拟盘自 M3 起每日自动落注结算，并成为 M5 实盘入场前提。
> v0.4 → v0.5 变更：新增 **§12 双线并存协议**——A 线（研究评测，已建成）与 B 线（运营模拟 = M3-M5 paper 模式）在同一程序并存、结论分账；项目负责人显式推翻「M2 NO-GO → M3+ 不启动」的顺序约束（决策记录见 §12.4）；A 线判据不变。

---

## 1. 背景与目标

### 1.1 一句话定位

Python 确定性管线做足球赛事量化分析与投注推荐，Hermes 作为**可替换组件**承担两件事：联赛 persona 的定性分析（headless 纯函数式调用）与 Telegram 推送。**agent 永远不当大脑。**

### 1.2 目标

- 五大联赛（E0 英超 / SP1 西甲 / D1 德甲 / I1 意甲 / F1 法甲）胜平负与大小球 2.5 的概率建模与价值投注推荐
- 每个推荐有明确的模型概率、市场概率、EV、仓位建议，全程可追溯、可回测
- persona（LLM 定性层）的价值用实盘 A/B 数据诚实检验，而不是靠信仰
- 比赛日自动推送 Telegram 报告，投注全生命周期（登记→结算→CLV）落库

### 1.3 非目标（一期明确不做）

- 滚球 / in-play（Provider 抽象与事件触发入口留桩，见 §9.8）
- 五大联赛以外 的赛事
- 球员级 / 阵容级建模（伤停盲区由 persona 层补偿并经 A/B 量化，见 §4.5；二期触发条件见 §11 开放问题）
- 比赛内进球时间建模（对赛前市场一阶信息为零；是滚球二期的核心输入，见 §9.8）
- 自动真实下单（人工照报告下注；模拟盘虚拟落注自动进行，见 §7.2 / §9.8）
- 套利、多账户
- 盈利承诺：本项目定位是**研究验证 + 小额实盘检验**，M2 是 go/no-go 关卡，跑不赢市场就止步

### 1.4 核心设计原则

1. **确定性主控**：Python 管线决定一切流程与数字；`hermes -z` 被当作纯函数调用——输入 JSON 文件、输出 JSON 文件
2. **有界 agent**：persona 只消费结构化输入、产出结构化输出，只能否决 / 降权 / 小幅调信心（±0.15），**永远不能发明数字、不直接碰数据库**
3. **一切落库**：每次 run、每条推荐（含被 veto 的）、每注投注都有记录
4. **市场是基准**：收盘价隐含概率是所有评估的对照线；CLV 是「是否真有 edge」的金标准
5. **诚实降级**：任何外部依赖（Odds API / hermes / 数据源）失败都有明确的降级路径并在报告中标注，绝不静默吞错

---

## 2. 总体架构

### 2.1 架构图

```
hermes cron（调度）
   └─> Python 管线 fa（主控）
        ├─ 1 数据层：历史 CSV + 实时盘 → SQLite
        ├─ 2 建模层：分层 Dixon-Coles → 胜平负/大小球概率
        ├─ 3 价值层：去水对比 → EV 筛选 → Kelly 仓位
        ├─ 4 Persona层：hermes -z × 5 联赛 → 有界定性覆盖
        ├─ 5 报告层：合并结果 → hermes send → Telegram
        └─ 6 回测层（独立命令）：walk-forward + 校准评估
```

### 2.2 职责边界

| | Python（fa） | Hermes |
|---|---|---|
| 流程控制 / 调度触发 | ✅ 全部 | 仅 cron 定时触发 |
| 数据获取与存储 | ✅ 全部（agent 无 DB 访问权） | ❌ |
| 模型 / 概率 / EV / 仓位 | ✅ 全部核心数字 | ❌ |
| 定性分析（伤停/新闻/动机） | ❌ | ✅ web_search + persona 判断 |
| Telegram 推送 | 组装内容 | ✅ `hermes send` 送达 |

### 2.3 Hermes 集成前提（环境事实，2026-09 确认）

- `hermes send --to telegram` 直接可用（bot-token 平台无需网关常驻），TG 推送**零额外代码**
- 默认模型 ark-code-latest（火山方舟自定义端点），persona 调用直接跑在现有额度上，成本可忽略
- `hermes -z "提示词"` 支持 headless 一次性运行；`hermes cron` 管调度

---

## 3. 数据层

### 3.1 数据源

| 源 | 用途 | 细节 |
|---|---|---|
| football-data.co.uk | 历史赛果 + 历史赔率 | 免费 CSV，五大联赛 1993 至今；含赛果、射门/角球、Pinnacle 开盘/收盘赔率。**回测的市场基准用收盘价**（行业标准做法） |
| The Odds API | 实时盘 | 免费档 500 credits/月。比赛日拉 h2h + totals × 欧英两区 × 5 联赛 ≈ 每次 10 credits，一天两次。响应头剩余额度记账，额度告急自动降频 |

队名对齐坑点：两个数据源的队名拼写不同（如 `Bayern Munich` vs `Bayern München`），是两大数据源对齐的核心难点，见 3.3。

### 3.2 数据库（SQLite 单库，WAL 模式）

| 表 | 关键字段 | 说明 |
|---|---|---|
| `teams` | id, league, name | 规范队名（以 football-data.co.uk 拼写为准） |
| `team_aliases` | team_id, source, alias | 跨源别名映射，UNIQUE(source, alias) |
| `matches` | id, league, season, date, home_team_id, away_team_id, fthg, ftag, 射门/射正/角球, Pinnacle 开盘/收盘赔率列, raw_line(JSON) | 已完赛场次；原始 CSV 行整行留档便于重放 |
| `odds_snapshots` | id, fetched_at, source, event_key, match_id(可空), market(h2h/totals), region, bookmaker, outcomes(JSON), raw(JSON) | 实时盘快照；对齐完成前 match_id 允许为空 |
| `recommendations` | id, run_id, match_id, strategy(model_only / model_persona), market, model_p, market_p, best_odds, bookmaker, edge, ev, kelly_stake_frac, verdict, confidence_delta, final_stake_frac, created_at | A/B 双轨：两套 strategy 各存一份（见 6.6） |
| `bets` | id, recommendation_id, mode(paper/live), placed_at, bookmaker, odds_taken, stake, status(pending/won/lost/void), settled_at, return_amt, closing_odds, clv | 投注台账，模拟 / 实盘分模式统计 |
| `runs` | id, type(daily/matchday/backtest/manual), phase(am/pm, 可空), started_at, finished_at, status, credits_before/after, summary(JSON) | 每次 run 的审计记录（v0.5：am/pm 移入 phase 列，type 词表四值加 CHECK 约束） |
| `meta` | key, value | bankroll、Odds API 额度水位等 KV |

### 3.3 队名对齐

- 首次入库时自动生成候选别名（编辑距离 + 人工确认清单），确认后写入 `team_aliases`
- 实时盘 event → 本地 match 匹配：队名走别名表，**匹配不上的进隔离表并 TG 告警，绝不静默丢弃**
- 别名表是持续维护成本，新增_unknown_条目每日报告可见

### 3.4 更新节奏与额度管理

- 每日：更新已完赛场次（含收盘赔率，供 CLV 结算）
- 比赛日：按窗口拉实时盘存快照（11:00 / 17:00 两次，见 9.6）
- Odds API 剩余额度从响应头读取并写入 `runs`/`meta`；低于阈值（如 100）时自动降频（丢弃非比赛日拉取、合并 region）并在报告标注

---

## 4. 建模层

### 4.1 模型形式：分层 Dixon-Coles

- 每队两个参数：攻击评分 attack、防守评分 defence
- 每联赛两个参数：主场优势 home_adv、进球率 baseline（联赛间进球环境不同）
- **分层 / partial pooling**：联赛级参数向全局均值收缩（MAP 正则化实现），避免小样本联赛参数飞掉
- 低比分相关性修正（Dixon-Coles 的 ρ 项）：0-0/1-0/0-1/1-1 比分的经验相关性

### 4.2 拟合与时间衰减

- 历史比赛按时间衰减加权，**半衰期约 100 天量级**（具体值由回测调参确定）
- 优化器 L-BFGS（scipy），每联赛几十个参数，秒级收敛
- walk-forward：每比赛周重拟合一次，只用该时点之前的数据（严格防泄漏）

### 4.3 推断输出

- 期望进球 λ_home、λ_away → 泊松（含 DC 修正）全比分矩阵
- 衍生：胜平负概率、大小球 2.5、BTTS、任意比分概率——全部由同一个比分矩阵导出，保证内部一致

### 4.4 冷启动与赛季切换

- 新赛季：上季评分带衰减继承
- 升班马：向联赛均值强收缩（无该联赛历史数据）

### 4.5 已知盲区：球员缺阵与球队状态

模型是队级的，看不到球员缺阵与阵容新闻——这是**已知且接受的盲区**，理由：

- 伤停是市场定价最快的信息，免费球员数据 + 滞后模型跑不赢 Pinnacle 的定价速度；且首发通常赛前 1 小时才确认，lineup-adjusted 模型在下注时点（11:00 / 17:00）拿不到输入
- 该盲区的**防守**已内建于架构：persona 层（web_search 查伤停）正是为此存在；A/B 双轨（§6.6）持续量化「盲区实际造成多大损失、persona 补了多少」——不需要球员评分即可检测并测量问题本身
- 球队整体状态：近况信息部分已被时间衰减加权捕获；M2 设可选消融实验——近 6 场表现协变量（积分 / 净胜球）on/off 对比 log-loss，队级数据免费可回测，用数据决定去留

二期触发条件（见 §11）：若 M5 的 A/B 归因显示模型在「大新闻场次」系统性跑输市场且 persona 补偿不足，再评估 lineup-adjusted 建模。

---

## 5. 价值层

### 5.1 去水

- 一期用**比例法**（proportional normalization）：隐含概率按比例归一
- 后续可换 Shin 法（考虑内幕交易 favors 长尾），接口留 `devig(method=...)` 参数

### 5.2 候选筛选门槛

- `edge = model_p − market_p（去水后）`，`EV = p·(o−1) − (1−p)`
- 入选需同时满足：
  - EV ≥ 3%
  - edge ≥ 2%
  - 赔率区间过滤：排除模型噪声主导的极端冷门（具体上下限 M3 调参后写入配置，暂定 [1.4, 6.0] 量级）

### 5.3 仓位

- **¼ Kelly**，单注上限 **2% bankroll**
- bankroll 记录在 `meta`，随结算更新

---

## 6. Persona 层（Hermes，方案 B 的核心增量）

### 6.1 定位与边界

5 个联赛人格（项目内 `personas/*.md`：懂德甲的、懂意甲的……），职责是模型看不到的东西：**伤停、赛程密度、欧战轮换、更衣室新闻、动机（保级/争冠/无欲无求）**。工具白名单仅 `web_search`（这是选 B 的意义所在），禁其余一切工具。

### 6.2 调用方式

- Python 侧用 `hermes -z` headless 调用：prompt = persona 文件全文 + 该场输入 JSON + 输出契约说明
- **每个候选场次一次调用**（同场多个候选共享一次判决，见 6.4）
- 比赛日候选约 3–8 场 → 3–8 次调用，ark-code-latest 现有额度下成本可忽略
- 17:00 更新版不重跑 persona（沿用 11:00 判决），仅对新增候选场次补跑
- 超时默认 120s（可配）

### 6.3 I/O 契约

**输入**（Python 写出的 JSON 文件，字段全部来自库内已有数据）：

```json
{
  "league": "D1",
  "match": {
    "kickoff_utc": "2026-09-12T13:30:00Z",
    "home": "Bayern Munich",
    "away": "Borussia Dortmund"
  },
  "candidates": [
    {
      "market": "OU_over_2.5",
      "model_p": 0.58,
      "market_p": 0.545,
      "best_odds": 1.95,
      "edge": 0.035,
      "ev": 0.131,
      "kelly_stake_frac": 0.008
    }
  ],
  "model_summary": {
    "p_home": 0.52, "p_draw": 0.22, "p_away": 0.26,
    "exp_goals_home": 2.1, "exp_goals_away": 1.4
  },
  "form": {
    "home_last5": ["W", "W", "D", "L", "W"],
    "away_last5": ["D", "L", "W", "W", "L"],
    "home_pos": 2, "away_pos": 5
  },
  "h2h_recent": [{ "date": "2026-04-12", "score": "4-2", "home": "Bayern Munich" }]
}
```

**输出**（必须是且仅是一个可被 schema 校验的 JSON 代码块）：

```json
{
  "verdict": "agree",
  "confidence_delta": -0.05,
  "key_factors": ["多特主力中卫停赛", "拜仁周中多打一场欧冠"],
  "report_md": "### 德甲人点评\n拜仁近 3 轮……"
}
```

校验规则：

- `verdict ∈ {agree, downweight, veto}`
- `confidence_delta ∈ [−0.15, +0.15]`；`downweight` 时必须 < 0，`veto` 时置 0
- `key_factors` 1–5 条，每条 ≤ 50 字
- `report_md` ≤ 500 字

### 6.4 判决应用规则（确定性映射）

- `veto` → 该场全部候选丢弃（记录保留）
- `agree` / `downweight` → `final_stake_frac = kelly_stake_frac × (1 + confidence_delta)`
- 判决粒度为**场次级**；未来需要市场级细分时扩展 `markets{}` 子对象（预留，不实现）

### 6.5 降级策略

超时 / 解析失败 / JSON 不合规 / 超出值域 → **该场次**自动回退纯模型推荐，报告中标注「persona 未生效 + 原因」。降级事件记入 `runs.summary`。（粒度裁定 2026-09-04：调用本按场次发起，一场失败不撤销同联赛已成功判决；当日降级不重试。）

### 6.6 A/B 双轨追踪

- `recommendations` 同时落两套：`strategy = model_only` 与 `strategy = model_persona`
- 实盘按 model_persona 下注，但两套都跟踪命中 / 收盘对比
- **用实盘数据回答「persona 到底加没加分」**——这是对方案 B 的诚实检验；样本量在 M5 结束时评估

---

## 7. 报告与投注追踪

### 7.1 Telegram 报告（`hermes send`）

比赛日推送，内容：

1. 各联赛候选场次：市场、赔率、模型概率 vs 市场概率、EV、建议仓位
2. persona 点评（`report_md` 精简版）+ 判决标识（✅ agree / ⚠️ downweight / ⛔ veto）
3. 风险提示（样本量、半衰期窗口、降级标注、额度水位）
4. 未结投注与 bankroll 快照
5. **17:00 更新版**（`--phase pm`）：与 11:00 的差异——已推候选的盘口移动（CLV 预览）、新增/消失候选；不做全量重复推送

### 7.2 投注记录与结算（含模拟盘）

- **双模式**：`paper`（模拟盘，默认）/ `live`（实盘）；指标按模式分开统计
- **模拟盘**：matchday run 把当日推荐（model_only 与 model_persona **两套都落**，同时给 A/B 补上结算流）经 PaperExecutionProvider 自动落成虚拟注，按推荐时盘口成交；每日任务照常结算、算 CLV / ROI——**M3 起每天自动积累前向记录**
- 实盘：CLI 人工登记 `fa bet add / settle`（一期不自动真实下单）
- 结算时从已入库收盘价回填 `closing_odds`

### 7.3 核心指标

| 指标 | 定义 | 意义 |
|---|---|---|
| ROI | 净利 / 总投注额 | 最终结果，但小样本噪声大 |
| 累计 P&L | 累计净额 | 绝对量 |
| **CLV** | odds_taken / Pinnacle 收盘价 − 1，按注中位数 | **金标准**：持续买在收盘前且价格更好 = 长期正期望的信号，比短期盈亏更早暴露真相 |

paper bankroll 与上表指标按 `strategy` 分轨统计（model_only / model_persona 各一本，初始各 1000）——§12.3 预注册判据的分轨对比口径（M4 裁定，2026-09-04：分轨隔离使一轨盈余不放大另一轨仓位，A/B 对比不失真）。

---

## 8. 回测与验证（「研究验证」的立足点）

### 8.1 walk-forward 协议

- 5+ 赛季（如 2019/20–2024/25），每比赛周重拟合，严格时点外推
- 每场比赛记录：模型概率、收盘价隐含概率（去水）、真实结果

### 8.2 评估指标与市场基准

- 校准曲线（按概率十分位分组）、Brier、log-loss
- **基准线 = 去水收盘赔率隐含概率**：模型必须接近或超过市场精度才有继续的资格
- **go/no-go 判据（已确认）**：walk-forward 全样本上，模型 log-loss 相对基准**劣化 ≤1% 即 go**；劣化 >1% → no-go，项目止步、研究结论存档。依据：实盘打的是软庄去水价（水位 ~5%）而非 Pinnacle（~2.5%）本身，总体略输市场仍可能对软庄盘口存在正 EV；但劣化超过 1% 说明模型没有可用的信息增量

### 8.3 模拟盘盈亏

- 按 5.2 门槛筛出的候选，平注（flat）与 ¼ Kelly 两种仓位模拟
- 输出回测报告：分赛季 / 分市场 / 分赔率区间的盈亏与回撤

### 8.4 persona 的评估边界

**回测不含 persona**（LLM 不可复现且贵）；persona 的价值只由 6.6 的实盘 A/B 衡量。

---

## 9. 工程设计

### 9.1 技术栈

- Python 3.11+（本机 miniconda 3.13 可用）、uv 管依赖
- pandas + scipy、typer（CLI）、pytest、SQLite（stdlib sqlite3）

### 9.2 目录结构

```
Football_Analysis/
├── spec.md
├── pyproject.toml
├── personas/            # 5 个联赛人格定义（markdown）
│   ├── epl.md           # E0
│   ├── laliga.md        # SP1
│   ├── bundesliga.md    # D1
│   ├── seriea.md        # I1
│   └── ligue1.md        # F1
├── src/fa/
│   ├── config.py
│   ├── db.py
│   ├── data/            # 历史 CSV 入库、Odds API、队名映射
│   ├── model/           # Dixon-Coles 拟合与推断
│   ├── value/           # 去水 / EV / Kelly
│   ├── persona/         # hermes 调用、契约校验、判决应用
│   ├── report/          # 渲染 + hermes send
│   ├── backtest/        # walk-forward、指标、模拟盘
│   └── cli.py
├── tests/
└── data/                # SQLite 库与原始 CSV 缓存（gitignore）
```

### 9.3 CLI 命令面（typer，入口 `fa`）

```
fa init                    # 初始化数据库与目录
fa data sync-history       # 拉取/增量更新历史 CSV 入库（幂等）
fa data sync-odds          # 拉实时盘快照（消耗 Odds API 额度）
fa model fit               # 拟合当前模型
fa run daily               # 每日任务：更新完赛 + 结算昨日 + 回填 CLV
fa run matchday [--phase am|pm]  # 比赛日任务：am=完整推荐报告；pm=盘口更新版（见 9.6）
fa backtest run --from 2019 --to 2025 [--flat|--kelly]
fa report send             # 手动重发最近报告
fa bet add|settle|list     # 投注台账
fa status                  # bankroll / 额度水位 / 最近 run / 未结注
```

### 9.4 配置与密钥

- `ODDS_API_KEY` 走环境变量（`.env`，gitignore）
- 其余参数集中在 `config.py` 默认值：半衰期、EV/edge 门槛、赔率区间、Kelly 系数与单注上限、persona 超时、时区（Asia/Shanghai）

### 9.5 错误处理与可观测性

- 数据源失败 → 用最近缓存 + TG 告警
- Odds API 额度耗尽 → 跳过拉盘、用最近快照并在报告标注「非实时盘」
- persona 失败 → 6.5 降级
- 所有 run 写 `runs` 表 + 文件日志

### 9.6 调度（hermes cron，两条 job，北京时间）

| job | 时间 | 动作 |
|---|---|---|
| daily | 每日 06:30 | `fa run daily`（数据更新 + 结算） |
| matchday-am | 每日 11:00 | `fa run matchday --phase am`：拉盘→建模→价值→persona→**完整推荐报告**；管线内部检查当日赛程，无赛事即空跑退出，不耗额度 |
| matchday-pm | 每日 17:00 | `fa run matchday --phase pm`：**更新版报告（已确认）**——与 11:00 对比只推差异：已推候选的盘口移动（CLV 预览）、新增/消失候选，去重不重发全量；persona 不重跑（沿用 11:00 判决），仅对新增候选场次补跑一次 |

### 9.7 测试策略

| 对象 | 用例 |
|---|---|
| 模型层 | 概率归一；主客对称性（交换主客 → 预测镜像）；比分矩阵行列和一致 |
| 去水/EV | 已知盘口 → 已知隐含概率；EV 公式边界 |
| 队名映射 | 别名解析、未知队名进隔离表 |
| persona 契约 | mock hermes（fixture 脚本回放合法/非法/超时 JSON），覆盖降级全路径 |
| 管线 | 小样本 CSV 夹具端到端冒烟 |

### 9.8 Provider 抽象与平台对接预留

两个 Provider 家族隔离外部平台差异，上层管线只面向接口：

**OddsProvider（赔率数据源）**

- 统一接口 `get_odds(leagues, markets, regions) -> OddsSnapshot[]`
- 一期实现：`HistoricalCsvProvider`（football-data.co.uk，回测）+ `OddsApiProvider`（The Odds API，赛前实时）——**回测与实盘共用同一接口同一代码路径**
- 二期候选：`InPlayProvider`（滚球盘）、`ExchangeProvider`（Betfair 类交易所，真流动性可下单）

**ExecutionProvider（下单执行）**

- 统一接口 `place_bet / get_balance / settle`
- 一期实现：**PaperExecutionProvider（模拟盘，默认）**——按推荐时盘口虚拟成交，M3 起每日自动积累记录（§7.2）
- 二期候选：真实平台 adapter——前提是**平台提供官方 API 且用户所在地区合规可用**（Pinnacle / Betfair 有官方 API；多数软庄无官方下单接口）；一期不承诺任何真实执行

**滚球预留**

- 事件触发入口留桩（`fa run live` 占位）
- 进球时间 / 比分进程数据是滚球模型的核心输入（条件于当前比分与剩余时间的进球强度）；对赛前市场它一阶冗余（确定性时间剖面下总进球仍 ~ Poisson(λ)），故一期完全不采集

---

## 10. 里程碑与验收标准

| # | 里程碑 | 内容 | 验收标准 | 备注 |
|---|---|---|---|---|
| M1 | 数据层 + 回测基建 | 历史入库、队名映射、walk-forward 取数框架 | 五大联赛全量入库；抽样 50 场与 CSV 对账一致；`sync-history` 幂等重跑；未知队名进隔离表 | |
| M2 | 模型 + 回测评估 | DC 拟合、概率输出、校准 / Brier / log-loss、模拟盘、（可选）近 6 场状态协变量消融（§4.5） | walk-forward ≥5 赛季跑通并产出对比报告；判据（已定）：log-loss 劣化 ≤1% 即 go，否则止步 | ⛔ **go/no-go 决策点** |
| M3 | 价值层 + TG 数字报告 + 投注追踪 | Odds API、去水/EV/Kelly、`hermes send`、bet 台账 + **模拟盘自动落注** | 比赛日端到端连续跑通一周；CLV 可计算；paper 模式每日自动落注与结算 | 报告暂无 persona 段 |
| M4 | Hermes persona 接入 | 5 personas、契约校验、降级、A/B 双轨 | mock + 实跑测试通过；A/B 数据落库 | |
| M5 | 实盘小注 4–6 周 | 小注运行、每日结算、周度小结 | 入场前提：**模拟盘 CLV 达标**；以 **CLV 为主、ROI 为辅**决策加码 / 维持 / 停止 / 滚球与平台对接二期立项 | 模拟盘数据自 M3 起积累 |

**一句重申：M2 是诚实的关卡——如果模型 log-loss 跑不赢收盘盘，后面的钱和精力都应该省下来。**

---

## 11. 风险与开放问题

### 风险

1. **M2 no-go 概率不低**（收盘价非常强）——设计已把它当廉价止损点：M2 之前不投入 M3+ 资源
2. **Odds API 额度**：比赛日 2×10 credits × 全月比赛日可能贴着 500 上限——记账 + 降频缓解；仍不够则减 region 或升档
3. **队名对齐是持续成本**：新赛季 / 杯赛交叉数据会持续产生新别名；隔离表 + 告警保证不脏数据
4. **大小球线对齐**：历史 CSV 基准为 2.5 线；实时盘主线可能漂移（2.75 等）——一期只取 2.5 线，其余丢弃并计数
5. **persona 输出不合规率未知**：降级路径必须被 mock 测试全路径覆盖
6. **hermes cron 依赖本机常开**：跑批失败要有 TG 告警（daily 失败超 1 天即告警）

### 开放问题（到对应里程碑再定，不提前锁死）

1. M3：赔率区间过滤的上下限终值
2. M4：`confidence_delta` 应用公式的微调（是否对 veto 之外的高 |delta| 加二次衰减）
3. M5：A/B 双轨的样本量是否足以下 persona 结论，还是需要延长观察期
4. 二期：是否引入 lineup-adjusted（球员级）建模——触发条件：M5 A/B 归因显示模型在「大新闻场次」系统性跑输且 persona 补偿不足（§4.5）；进球时间数据采集同理，随滚球二期一起评估（§9.8）

（原开放问题 #1「M2 容差」与 #3「两次报告关系」已于 v0.2 确认并落入正文 §8.2 / §9.6。）

---

## 12. 双线并存协议（v0.5）

### 12.1 架构

一个程序、一个库，两条线并存，**结论分账**：

| | A 线·研究评测（已建成） | B 线·运营模拟（本协议启动） |
|---|---|---|
| 命令 | `fa backtest run` | `fa run daily` / `fa run matchday [--phase am|pm]` |
| 写入表 | `backtest_predictions`（独占） | `odds_snapshots` / `recommendations` / `bets(mode=paper)` / `runs`（独占） |
| 结论产物 | `docs/m2-*.md` 判决系 | 每日 TG 报告 + `fa status` B 线栏（paper ROI/CLV） |
| 度量 | log-loss vs 去水收盘（历史，秒级/想法） | 模拟盘 ROI / CLV（前向，每比赛日累积） |
| 依赖 | 无外网（用库内历史） | Odds API + hermes（persona/TG） |

**表边界是硬约束**：A 线不写 B 线的表，反之亦然——两线的证据链互不污染。模型层（`fa/model`）共享：A 线产出更优模型时，B 线改一行配置即切换，两线结论历史各自连续。

### 12.2 B 线范围（= 原 M3 + M4 + M5 paper 模式，spec §10 原文照旧执行，仅解锁）

- M3：Odds API 接入 + 价值层（§5）+ TG 数字报告 + paper 台账自动落注结算
- M4：persona 接入（§6，A/B 双轨照旧）
- M5：paper 模式运行 4–6 周（**真实下注依然禁止**——本协议不改变 M5 的实盘前提）

### 12.3 预注册判据（先于 B 线第一次运行写定）

- **B 线胜出**：模拟盘累计 ≥300 注 且（a）model_persona 轨 CLV > 0，或（b）model_persona 轨 ROI 显著优于 model_only 轨——则 persona/运营假设成立，再议实盘
- **B 线归档**：6 周后未达胜出条件 → B 线停跑归档，结论记档（「persona 未能拯救负选择池」或「样本不足」如实记录）
- **A 线判据不变**：任何新建模想法仍须样本外劣化 ≤1% 才具转正资格
- 两线结论**不得互相冒充**：A 线的历史判决不因 B 线前向波动修改；B 线不引用回测数字充当前向证据

### 12.4 决策记录（推翻关卡的显式授权）

M2 判决 NO-GO（+3.02%，四重稳健性证据）后，spec §10 原判「M3+ 不启动」。项目负责人于 2026-09-03 **显式推翻该顺序约束**，理由：将负先验转为长期对照实验（成本：paper 模式零真金 + Odds API 免费额度 + 工程时间）。已如实告知的科学非对称性：B 线前向样本（~300 注/6 周）信息量约为回测（9,679 注）的 1/30，翻案概率极低；persona 只能过滤注、不能翻转候选池符号。本决策**不修改** M2 判决本身，A 线判据与存档不动。
