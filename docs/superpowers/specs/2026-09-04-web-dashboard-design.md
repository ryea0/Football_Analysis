# fa 本地只读看板（dashboard）· 设计

| | |
|---|---|
| 日期 | 2026-09-04 |
| 状态 | 头脑风暴设计已获项目负责人批准（用途/路线/七节设计逐项确认）；待 spec 增补后进入实施计划 |
| 关联 | spec v0.5（§7 报告层、§9 工程设计、§12 双线并存协议） |
| 实施载体 | `dashboard/` 新目录 + `pyproject.toml` 新 dependency-group；核心管线代码零改动 |

## 1. 背景与目标

当前项目的观察出口只有两条：Telegram 推送（聊天记录里翻数字）和 CLI `fa status`（单次快照、无趋势）。随着 B 线 paper 模式每日自动落注（M3 起累积）与 A 线回测判决的存档，需要一个**本地只读 dashboard**：

- **B 线运营监控**：bankroll/P&L 趋势、paper 台账与 CLV、A/B 双轨对预注册判据（§12.3）的进度、额度水位与运维健康
- **A 线研究可视化**：校准曲线、log-loss vs 市场基准、模拟盘回测盈亏

**决策记录**（brainstorming 过程，2026-09-04）：

1. 用途 = 两者兼顾，页面按 §12 双线分区（用户选定；对外演示/作品集明确**不是**目标）
2. 技术路线 = **Streamlit 多页应用**（vs FastAPI+HTMX：自用场景下 Streamlit 出活快、无前端工具链；颜值短板因无对外需求而不构成问题；`queries.py` 查询层独立，将来换 FastAPI 壳可整体复用）
3. 纯静态 HTML 导出方案被排除——跨日聚合与筛选交互做不了

**边界（不做）**：不写库（只读连接）、不加鉴权（仅 localhost 监听）、不含任何下注/触发 run 的操作按钮、不做移动端适配、不做 UI 自动化测试。

## 2. 架构与目录

```
dashboard/
├── app.py          # 入口：页面标题、侧边栏（导航由 streamlit 自动生成 + 手动刷新按钮 + 数据源标注）
├── queries.py      # 纯函数查询层：一段 SQL → pandas DataFrame，无 streamlit 依赖
└── pages/
    ├── 1_📋_B线_总览.py
    ├── 2_🎯_B线_推荐与台账.py
    ├── 3_⚖️_B线_AB双轨进度.py
    ├── 4_🩺_B线_运维健康.py
    ├── 5_📊_A线_回测总览.py
    ├── 6_📈_A线_校准曲线.py
    └── 7_💰_A线_模拟盘回测.py
```

- **运行**：`uv run --group dashboard streamlit run dashboard/app.py`（README 与 spec 各记一处）
- **依赖**：`pyproject.toml` 新增 `[dependency-groups] dashboard = ["streamlit>=1.40", "plotly>=5.24"]`；主 `dependencies` 不动——不装 dashboard 组时管线与测试行为完全不变
- **DB 连接**：复用 `fa.config.db_path()`（含 `FA_DB` 环境变量覆盖），以 `sqlite3.connect(f"file:{path}?mode=ro", uri=True)` 只读打开。WAL 模式下只读读者与管线写入互不阻塞；dashboard 崩溃不可能污染数据
- **导入方式**：页面脚本通过 `fa.config`（uv 可编辑安装）取路径；`queries.py` 用 stdlib sqlite3 + pandas 取数，可调用 `fa` 的**纯计算函数**（`backtest.metrics` / `backtest.simulate`）复用口径，但不碰任何有副作用的模块（写库、hermes、网络）——口径单一来源，避免 dashboard 与回测报告两处实现漂移

## 3. 页面清单（v1 = 7 页）

数据事实依据（2026-09-04 实测 `data/fa.db`）：`matches` 59,176 行、`backtest_predictions` 11,605 行、`odds_snapshots` 9,885 行、`recommendations` 28、`bets` 14、`fixtures` 102、`runs` 4、`unknown_names` 0 行（隔离表随确认清空，属正常状态）；`meta` 现有 `paper_bankroll`、`odds_quota_remaining` 两个业务键。

### B 线运营组

**1. 总览** — `fa status` 的图形化常驻版
- 指标卡：bankroll（`meta.paper_bankroll`）、累计 P&L（`bets.return_amt − stake` 汇总）、未结注数（`bets.status='pending'`）、额度水位（`meta.odds_quota_remaining` + `runs.credits_*` 序列）
- 额度消耗曲线（按 run 时间序列）+ 告警阈值线（spec §3.4 的 100 credits 降频线；40/日消耗节奏直观化，直接服务 M5 首务「额度节流」）
- 最近 5 次 run 状态条

**2. 推荐与台账**
- 推荐表：`recommendations` 全字段（strategy / phase / market / model_p vs market_p / best_odds / bookmaker / edge / ev / kelly_stake_frac / verdict / confidence_delta / final_stake_frac），关联 `fixtures` 显示队名与开球时间；strategy 与 phase 可筛选
- paper 注明细：`bets` 全字段（status / odds_taken / closing_odds / clv / return_amt），CLV 正负着色

**3. A/B 双轨进度** — B 线核心 KPI 页
- model_only vs model_persona 两轨的：注数、ROI、CLV 中位数，按结算日累计的 P&L 双曲线
- 判据进度条：距 §12.3 预注册条件「累计 ≥300 注」的进度（当前 14 注，页面如实显示 4.7%——这就是它存在的意义：诚实呈现距离）
- 注数不足时标注「样本不足，指标仅供参考」（小样本噪声，spec §7.3 的立场）

**4. 运维健康**
- `runs` 历史表（type / phase / status / 起止 / credits 差），`summary` JSON 展开为列（aligned / fixtures / bets / degraded / degraded_reasons）
- 降级事件清单（degraded=true 或 degraded_reasons 非空的 run，spec §1.4「诚实降级」的巡检入口）
- `unknown_names` 隔离表：逐条列出 + 来源/出现次数统计，页尾指引「`fa data aliases` 看建议、`--confirm` 确认」——**页面保持只读**，不做写操作
- 「top-N 建议候选」列为 v2：建议逻辑现嵌在 CLI 内（运行时编辑距离打分、不落库，`team_aliases` 无 score/confirmed 列——实测确认），复用需先把它提炼成纯函数，属独立小重构，不塞进 v1

### A 线研究组

**5. 回测总览**
- 核心对比条：模型 log-loss / Brier vs 去水收盘基准（复算自 `backtest_predictions` 的 p_* vs mkt_* vs outcome），把 m2-verdict 的 +3.02% 劣化直接画出来
- 分赛季、分联赛分解表（劣化百分比热着色）；口径固定 walk-forward 全样本，与 §8.2 判据一致

**6. 校准曲线**
- 十分位分组：预测概率 vs 实际频率，分市场（h2h 三向拆成三类二元 + OU2.5）画校准线与理想对角线
- 分组样本量以气泡大小/副轴呈现，避免小样本组误导

**7. 模拟盘回测**
- flat 与 ¼ Kelly 两种仓位的累计盈亏曲线——**直接复用 `fa.backtest.simulate` 的 `candidates()/simulate_flat()/simulate_kelly()`**（模拟结果不落库，是对 `backtest_predictions` 的纯函数现算——实测确认；复用而非重写，保证与 M2 报告同口径）
- 分市场 / 分赔率区间（如 [1.4,2.0] [2.0,3.0] [3.0,6.0]）的盈亏与最大回撤表

### 明确延后（v2 候选，YAGNI）

- 历史数据浏览器（59k 场筛选）——数据在但无运营刚需
- 单场比分矩阵热力图——需 view 时跑模型推断
- DC 攻防评分散点图——**fit 参数未落库**（`meta` 无参数键，实测确认），v2 需现算+缓存或先做参数落库
- 盘口移动时间线（`odds_snapshots` 9,885 行已够画，等 B 线有真实盯盘需求再做）

## 4. 双线分账约束（§12.3 的界面落地）

- A 线页（5/6/7）只读 `backtest_predictions` + `matches`；B 线页（1/2/3/4）只读 `recommendations` / `bets` / `runs` / `odds_snapshots` / `fixtures` / `meta`
- 两线数字**不合成混合指标、不同屏混排**；若某页确需同屏（如总览），必须分栏并标注来源线
- 页面分组命名（B线_/A线_ 前缀）让证据归属在导航层即不可混淆——回测数字不能冒充前向证据，反之亦然

## 5. 数据流与缓存

- `queries.py` 每函数：`(conn, 参数...) -> DataFrame`，纯查询、无副作用，pytest 可直测
- 页面层 `@st.cache_data(ttl=300)` 包裹查询调用 + 侧边栏「刷新」按钮（`st.cache_data.clear()`）——run 每 30 分钟才可能写库，5 分钟 TTL 足够新鲜
- **空态优先**：B 线仅 14 注、多数筛选组合为空 → 渲染「暂无数据 + 一句原因」（如「paper 模式自 M3 起累积，当前 N 注」），不显示残缺图表
- 库文件缺失 → 页面顶部 `st.error` 提示「数据库不存在：{path}，先运行 `fa init`」，其余元素不渲染

## 6. 错误处理

| 情形 | 行为 |
|---|---|
| `fa.db` 不存在 | 明确报错 + `fa init` 指引，不抛栈 |
| 表存在但为空 | 空态说明（见 §5） |
| `meta` 业务键缺失（如 paper_bankroll 未初始化） | 显示「未记录」而非 0 |
| 查询异常 | `st.exception` 显示原始错误（本地自用工具，不需要更精致的兜底） |

## 7. 测试策略

- `tests/dashboard/test_queries.py`：每个查询函数两类用例——(a) 内存 SQLite 夹具造数 → 断言返回形状与关键数字；(b) **空库 → 返回空 DataFrame 不抛异常**（空态行为的契约测试）
- 夹具复用 `tests/conftest.py` 现有模式（项目已有临时库与 schema 初始化工具）
- 页面脚本不做 UI 测试；验收含一次 `streamlit run` 冒烟（启动无报错、7 页可切换）
- 指标口径单一来源：log-loss/Brier 调 `src/fa/backtest/metrics.py`、模拟盘调 `backtest/simulate.py`，`queries.py` 不另写公式；测试用已知数字夹具钉住这些函数的输出，防止未来改口径时 dashboard 静默漂移

## 8. spec 增补清单（先改 spec 再写码，v0.5 → v0.6）

1. §9.1 技术栈：加一行「dashboard（可选）：streamlit + plotly，独立 dependency-group，只读看板」
2. §9.2 目录结构：`dashboard/`（app.py / queries.py / pages/）
3. §7 报告与投注追踪：补一段「本地只读看板」——定位为 TG 报告与 `fa status` 之外的第三观察出口，双线分区规则引 §12.3
4. changelog 记一行（v0.5 → v0.6：新增本地看板设计）

## 9. 验收标准

- `uv run --group dashboard streamlit run dashboard/app.py` 一条命令起服务，7 页全部可浏览
- 不装 dashboard 组时 `uv run pytest` 全绿（依赖隔离的证明）
- 每页在当前真实库（14 注 B 线数据 + 11,605 行 A 线预测）上渲染无错，空态正常
- `tests/dashboard/` 全绿，含空库契约用例
- spec.md 完成 §8 所列增补并提交
