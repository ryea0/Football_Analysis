# 看板 v2 · B 线三轨化与多维统计增强

| | |
|---|---|
| 日期 | 2026-09-07 |
| 状态 | 计划草案，待负责人批准 |
| 关联 | spec v0.6（§7.4 本地只读看板）、v0.11（§12.7 C 线 + nokb 第三轨） |
| 前置 | 后端 `STRATEGIES` 已为三轨（`src/fa/pipeline/value.py:40`）、`settle_paper_bets` 已分三轨结算、`bets`/`recommendations` 已含 nokb 数据 |

## 1. 背景与目标

### 1.1 问题

当前 dashboard（v1，2026-09-04 设计，7 页）存在以下缺口：

1. **第三轨 `model_persona_nokb` 未接入**：后端（value.py `STRATEGIES`、paper.py `settle_paper_bets`）已是三轨分账，`bets` 表里 nokb 有 54 注，但看板页3硬编码仅两轨、页1仅合计数字——C 线消融对照轨完全不可观测。
2. **缺少多维度盈亏分解**：运营和研究想知道「哪类 bet 赚钱」（分市场/分联赛/分赔率段），目前只有明细行和累计曲线，没有聚合表。
3. **在途注不可观测**：页1 只有 pending 计数，没有具体场次列表，运营无法跟踪当日结算预期。

### 1.2 目标

- **P0**：三轨全链路可见（nokb 接入页1、页2、页3），C 线进度不再黑箱
- **P1**：补齐运营刚需的多维统计表格（分市场、分联赛、按日盈亏、在途注列表）
- **不改后端、不写库、口径复用**：纯查询层 + UI 层增量，不动核心管线代码

### 1.3 非目标（不做）

- 不做 A 线部分改动（A 线回测与多轨无关）
- 不做移动端适配、不做 UI 主题
- 不加任何写操作按钮（保持只读看板定位）
- 不做新的前端框架迁移（继续 Streamlit + Plotly）
- 不做 CLV 历史回填（那是数据层任务，不归看板）

## 2. 页面变更清单

共涉及 B 线 4 页中的 3 页（页1/页2/页3），页4运维健康不涉及 strategy 维度、不动。

### 2.1 页3 「A/B 双轨」→ 「三轨对比」（P0）

**定位**：B 线核心 KPI 页，§12.3 双轨判据 + C 线 nokb 对照。

**变更**：

| 项目 | 现状 | 变更后 |
|------|------|--------|
| 列布局 | 2 列（model_only / model_persona） | 3 列（+ model_persona_nokb） |
| 进度条 | 2 条（各 300 注目标） | 3 条，nokb 同样 300 注目标（但标注为 C 线对照、不计入 §12.3 判决） |
| 累计 P&L 曲线 | 2 条线 | 3 条线叠加 |
| 标题 | "A/B 双轨进度" | "三轨进度（§12.3 + C 线对照）" |
| 底部说明 | §12.3 双轨判据原文 | 保留判据原文 + 增补一行：nokb 为 C 线消融对照（人格无知识库），用于量化 KB 增益，不参与 §12.3 胜出判决 |

**数据层改动**：
- `queries.py::b_ab_tracks`：去掉硬编码 `for strat in ("model_only", "model_persona")`，改为从 SQL 结果 `df["strategy"].unique()` 动态遍历（排序固定：model_only → model_persona → model_persona_nokb）
- 返回 dict 键集随数据自动扩展，页面层不硬编码策略名

**命名**：函数名 `b_ab_tracks` 保留（避免调用面扩散改名），但内部实现动态化；页标题改但 URL 路径（文件名）不变。

### 2.2 页1 「B 线总览」→ 分轨汇总（P0）

**定位**：`fa status` 的图形化常驻版，一眼看到三轨各自状态。

**变更**：

现有 4 个 metric tile（bankroll / 累计 P&L / pending / 额度）保留（合计口径）。

在其下方新增**三轨汇总条**（一行三列，每轨一张小卡片）：

| 指标 | 说明 |
|------|------|
| Bankroll | `meta.paper_bankroll:{strategy}`，各轨独立余额 |
| 已结注数 | won + lost（不含 void/pending） |
| 胜率 | won / settled |
| ROI（已结） | pnl / staked |
| CLV 中位 | 已结算注的 CLV 中位数 |
| Pending | 在途注数 |

**数据层改动**：
- `queries.py::b_summary` 增补 `by_strategy` 字段：按 strategy 分组的 bankroll / n_settled / win_rate / roi / clv_median / pending
- 复用 `bets` + `meta` 数据，不新增 SQL 查询模式

**空态**：某轨无数据时卡片显示「—」，不消失（保持三列布局稳定）。

### 2.3 页2 「推荐与台账」→ 多维分解（P1）

**定位**：运营主工作台——从汇总下钻到明细。

**变更**：在推荐表和台账表之间，新增一个**「盈亏分解」折叠面板**（默认展开），内含 3 个 tab：

#### Tab 1：按市场 Market
表格：3 轨 × 4 市场（H/D/A/O2.5）的交叉矩阵

| 列 | 说明 |
|----|------|
| strategy | 策略轨 |
| market | 市场 |
| n / n_settled | 总注数 / 已结算数 |
| win_rate | 胜率 |
| roi | 已结算 ROI |
| clv_median | CLV 中位数 |
| avg_odds | 平均赔率 |

按 strategy 分组着色，正 ROI 绿色、负 ROI 红色（同现有 CLV 着色模式）。

#### Tab 2：按联赛 League
同结构，维度换为 league（E0/D1/SP1/I1/F1）。列同上。

#### Tab 3：按日 Daily
按结算日（北京日口径，与页4 run 日期同一口径）的每日盈亏：

| 列 | 说明 |
|----|------|
| 日期 | 结算日（北京日） |
| 注数 | 当日结算注数 |
| 当日 P&L | 当日盈亏（三轨合计？或分轨？→ **分轨三列**：每轨一列当日 P&L） |
| 累计 P&L | 累计到当日的盈亏（三轨各一列） |

用柱图 + 表格双视图（柱图看趋势，表格看数字）。

#### 推荐表过滤器：已动态支持
推荐表的 strategy 过滤器是 `sorted(recs["strategy"].unique())`，已自动含 nokb，无需改。

### 2.4 页1 / 页2 共享：在途注列表（P1）

放在页1「pending 计数」下方或页2 独立 tab——放**页1**更好（运营一眼看到在途）。

表格列：开赛时间（北京时）、联赛、对阵、市场、策略轨、拿价赔率、下注金额。
按开赛时间升序（先开赛的在上）。

空态：pending = 0 时折叠面板关闭 + 「当前无在途注」提示。

## 3. 数据层设计

### 3.1 新增/修改的查询函数

| 函数 | 状态 | 说明 |
|------|------|------|
| `b_ab_tracks` | 修改 | 硬编码两轨 → 动态三轨 |
| `b_summary` | 修改 | 增补 `by_strategy` 字段 |
| `b_breakdown` | 新增 | 通用聚合：按 strategy + dim 维度（market/league/settled_date）返回分解表 |
| `b_pending` | 新增 | 在途注列表（fixture + bet + strategy） |

### 3.2 `b_breakdown` 设计

```python
def b_breakdown(conn, dim: str) -> pd.DataFrame:
    """按 dim 维度聚合 paper 注的分轨统计。

    dim ∈ {"market", "league", "settled_date"}
    返回列：strategy, dim, n, n_settled, win_rate, roi, clv_median, avg_odds
    """
```

- **`dim=market`**：GROUP BY strategy, r.market
- **`dim=league`**：GROUP BY strategy, f.league
- **`dim=settled_date`**：GROUP BY strategy, DATE(b.settled_at)（北京日口径，复用现有 `_bj_days` 辅助函数）

单一函数三个维度，避免三个重复查询。页面层按 tab 切换传参。

### 3.3 口径一致性

所有指标口径与 `settle_paper_bets()` / `b_ab_tracks` 保持一致：

- **settled** = status IN ('won', 'lost')（void 不计入胜率和 ROI 分母）
- **pnl** = won: (return_amt - stake) / lost: (-stake) / void: 0 / pending: NaN
- **ROI** = pnl_sum / staked_sum（staked = 已结算注的 stake 合计）
- **CLV 中位** = 所有已结算注（有 closing_odds 的）的 CLV 中位数
- **北京日口径**：settled_at 是 UTC ISO-Z，转北京日时 +8h，与 `loaders.py` 现有 `_bj_days` 同函数

单一事实源原则（checklist #6）：不在 queries.py 里重新定义口径，而是……
- 策略名排序与后端 `STRATEGIES` 常量保持同序（`model_only` → `model_persona` → `model_persona_nokb`）
- 从 `fa.pipeline.value` import `STRATEGIES` 作为排序参照（但不强依赖，缺数据的轨补零值）

## 4. 测试策略

沿用 `tests/dashboard/test_queries.py` 模式（内存 SQLite 夹具 + 断言形状与关键数字）。

### 4.1 新增测试

1. **`b_ab_tracks` 三轨测试**：夹具造三轨各若干注，断言返回 3 个键、各键形状正确、排序正确
2. **`b_summary` 分轨测试**：断言 `by_strategy` 含三轨、bankroll 各轨独立
3. **`b_breakdown` 三维度测试**：每个维度一个夹具，断言列名正确、聚合数字正确
4. **`b_pending` 测试**：夹具造 pending 注，断言返回列正确、按开赛时间排序
5. **空库契约**：每个新增查询在空库下返回空 DataFrame 不抛异常（checklist 空态要求）

### 4.2 时间炸弹防御（checklist #1）

- 测试夹具的日期用固定日期（如 2026-09-04 / 2026-09-05），不依赖 `today()`
- 北京日转换测试用固定 UTC 时间戳断言正确的北京日

### 4.3 UI 不测试

Streamlit 页面不做自动化测试，验收靠手动冒烟。

## 5. 实施顺序（分两个 PR）

### PR 1：P0 — 三轨接入
1. 改 `queries.py::b_ab_tracks`：动态策略轨 + 固定排序
2. 改 `queries.py::b_summary`：增补 `by_strategy`
3. 改 `loaders.py`：`summary()` / `ab_tracks()` 缓存函数签名不变
4. 改页3 UI：2 列 → 3 列 + 标题 + 底部说明
5. 改页1 UI：新增三轨汇总条
6. 补测试
7. 验收：`streamlit run` 手动冒烟 + pytest 通过

### PR 2：P1 — 多维分解 + 在途注
1. 新增 `queries.py::b_breakdown`（三维度）
2. 新增 `queries.py::b_pending`
3. 改 `loaders.py`：新增缓存函数
4. 改页2 UI：盈亏分解面板（3 tab）
5. 改页1 UI：在途注列表
6. 补测试
7. 验收：手动冒烟 + pytest 通过

## 6. 验收标准

- `uv run --group dashboard streamlit run dashboard/app.py` 启动无报错，7 页可切换
- 页3 显示三轨卡片 + 三线累计曲线，nokb 有正确的注数/ROI/CLV
- 页1 显示三轨汇总条，各轨 bankroll 与 CLI `fa bet list --strategy` 对账一致
- 页2 分解 tab 能切换、表格数据正确（随机抽 2-3 行与 SQL 手工对账）
- 不装 dashboard 组时 `uv run pytest` 全绿（依赖隔离）
- `tests/dashboard/` 新增测试全绿，含空库契约用例
- spec.md §7.4 增补一句：「v2 增强：三轨化 + 多维盈亏分解 + 在途注列表」（changelog 式）

## 7. 风险与降级

| 风险 | 概率 | 影响 | 对策 |
|------|------|------|------|
| nokb 轨样本太少，表格全是 0/空 | 中 | 低 | 空态提示 + 标注「样本不足」；布局不塌陷 |
| CLV 大面积缺失（football-data 挂了） | 高 | 中 | CLV 列显示「—」而非 0，与现有页3 一致 |
| 三列布局在窄屏溢出 | 低 | 低 | Streamlit 自动换行会处理；自用看板不做移动端 |
| 新增查询性能问题 | 低 | 低 | B 线数据量 <500 注，全表扫无压力；5 分钟 TTL 缓存已足够 |

## 8. 诚实条款（checklist #9）

- 所有指标为描述性统计，**不做任何显著性检验**（页面不呈现 p 值、不标注"显著"）
- 样本不足 300 注时，所有 ROI/CLV 数字旁标注「样本不足，仅供观察」（与页3 现有说明一致）
- nokb 轨明确标注「C 线消融对照，不参与 §12.3 判决」——不得与 model_persona 作"胜出"结论
- 分解表（分市场/分联赛）的数字不引申因果（"某联赛赚钱"不等于"模型在该联赛有效"）
