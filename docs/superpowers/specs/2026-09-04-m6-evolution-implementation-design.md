# M6 C 线（进化线）实施设计

- 日期：2026-09-04
- 状态：设计评审通过（四节逐节确认），待负责人审阅本文档后转 writing-plans
- 上游：宪章 `2026-09-04-m6-evolution-line-design.md`（D1–D6 裁定已锁，本文不重复论证，只做实施定稿）
- 关联：spec §12（双线协议）、§12.3（B 线前向窗口）、§6（persona 层）、§12.7（本线 spec 条目，编号见 §12.9 增补草案）、M4 设计（caller 同构）、retro 设计（自有表先例）

## 0. 本次设计评审裁定记录（2026-09-04，负责人参与）

| # | 裁定 | 内容 |
|---|---|---|
| R1 | **KB 对照采用生产第三轨** | B 线新增 `model_persona_nokb`（人格但不读知识库），同期同场对照；paper 第三本 bankroll（初始 1000）；§12.3 判据口径不动（仍 model_persona vs model_only）；nokb 不进 TG 推送正文 |
| R2 | **验收 = 脚手架闭环 + 校准实跑** | 完整闭环在测试脚手架（临时 repo+库+可编程 hermes fixture）验收；对真实台账跑一次校准反思（`--calibrate`，预期 no_change，不入正式台账）；真实 W1 收口（~2026-10-15）首合并是监控点，不阻塞 M6 验收 |
| R3 | **关卡机制 = 暂存区（A1）** | 提案落 `evolution/proposals/`，真实知识文件只收已裁定版本；merge = 写文件 + Ruling 原子一步 |
| R4 | **调度 = 周检 tick（B1）** | `fa_cron.sh` 第 4 个 job，窗口收口才触发反思、否则空转记日志 |
| R5 | **版本戳 = 内容 hash** | `personas_hash` = personas 树（含 knowledge/）sha256；git short rev 仅作 runs.summary 辅助信息 |
| R6 | **冻结机械化 = 窗口快照** | B 线 prompt 只读 `evolution/snapshots/w{idx}/`；merge 最早下一窗口生效（宪章 §11「逾期顺延」从纪律变为机制） |
| R7 | 控制器代拍板（事后可翻案） | 废弃条目=整条删除（不设废弃段）；TTL 修剪只在进化事件时做（run 时不过滤，保窗口内 prompt 逐字稳定）；tick 失败沿用 cron wrapper 的 `fa ops alert`（「静默停摆」解读为不触碰 A/B 线运行与表，给人发 TG 不算触碰）；窗口内该联赛 0 判决样本 → 不调 hermes 直接记 no_change |

## 1. 模块布局

新模块 `src/fa/evolve/`（镜像 retro 子线模式；对 B 线表**只读**，无任何写路径）：

| 文件 | 职责 |
|---|---|
| `windows.py` | 窗口计算：锚点 `EVOLUTION_EPOCH = date(2026,9,4)`（§12.3 cron 激活日）、`EVOLUTION_WINDOW_DAYS = 42`，连续切分；当前窗/已收口窗查询（北京时间日期口径） |
| `evidence.py` | 只读 SQL 聚合：按窗口×联赛产出证据 JSON（§7 规格）——反思唯一证据源 |
| `reflect.py` | 反思 prompt 组装 + hermes 调用 + 契约校验（与 persona/caller 同构：`HERMES_BIN` 可 mock、超时/exit 真实） |
| `knowledge.py` | 知识文件解析/渲染/锚点寻址/TTL 修剪/长度上限/`personas_tree_hash`/窗口快照 |
| `apply.py` | 提案 → 暂存渲染（`.proposed.md` + unified diff）；merge/reject/shelve 的原子落账 |
| `__init__.py` | `EvolutionError` 族 |

CLI 新子 app `evolve_app`（`src/fa/cli.py`，命令规格见 §8）。

## 2. 数据模型（schema v7 → v8）

一次迁移完成三件事；分层迁移协议（新建 DDL 全列、存量库走迁移路径、幂等护栏、防重入，`schema_version` 单点推进；生产库合并后经 `fa init` 自迁移）。

### 2.1 `recommendations` 表重建

SQLite 不能后补 CHECK，扩枚举只能重建：

- 新 DDL：`strategy CHECK IN ('model_only','model_persona','model_persona_nokb')`；同次带上新列 `personas_hash TEXT`（R5 版本戳）
- 迁移：建 `recommendations_new` → `INSERT INTO ... SELECT`（`personas_hash` 补 NULL）→ drop 旧表 → rename → 重建 `idx_recs_run`
- 存量行 `personas_hash = NULL`，语义 = 「知识库纪元前」，不回填
- 迁移前自动备份 `data/fa.db` → `data/fa.db.bak-v8`（表重建类迁移的保守护栏）

### 2.2 C 线自有三表

```sql
CREATE TABLE IF NOT EXISTS evolution_windows (
    id           INTEGER PRIMARY KEY,
    idx          INTEGER NOT NULL UNIQUE,   -- 窗口序号，1 起
    opened_at    TEXT NOT NULL,             -- = epoch + (idx-1)*42d（北京时间日期）
    closes_at    TEXT NOT NULL,
    reflected_at TEXT,                      -- 反思已跑时刻
    closed_at    TEXT                       -- 关卡关闭（全联赛终态）时刻
);

CREATE TABLE IF NOT EXISTS evolution_runs (          -- runs 风格事件台账
    id               INTEGER PRIMARY KEY,
    window_id        INTEGER NOT NULL REFERENCES evolution_windows(id),
    league           TEXT NOT NULL,                  -- E0/SP1/D1/I1/F1
    kb_hash_before   TEXT NOT NULL,                  -- 反思时 personas 树 hash（v_n）
    status           TEXT NOT NULL
        CHECK (status IN ('ok','no_change','timeout','exit',
                          'extract','contract','error')),
    no_change_reason TEXT,
    proposal_path    TEXT,                           -- status='ok' 时的暂存相对路径
    duration_s       REAL NOT NULL,
    created_at       TEXT NOT NULL,
    UNIQUE (window_id, league)                       -- 防重跑
);

CREATE TABLE IF NOT EXISTS evolution_rulings (       -- 人审关卡全量 Ruling
    id            INTEGER PRIMARY KEY,
    run_id        INTEGER NOT NULL REFERENCES evolution_runs(id),
    ruling        TEXT NOT NULL CHECK (ruling IN ('merged','rejected','shelved')),
    kb_hash_after TEXT,                              -- merged 时新树 hash；其余 NULL
    note          TEXT NOT NULL,                     -- 裁定理由强制非空
    decided_at    TEXT NOT NULL
);
```

### 2.3 bankroll 播种

迁移时 `bankroll_key("model_persona_nokb")` 无值则播种 1000（镜像 M4 播种 model_persona）。

## 3. 版本戳与窗口快照（R5/R6）

### 3.1 `personas_tree_hash`

```python
def personas_tree_hash(root: Path) -> str:
    files = sorted(p for p in root.glob("**/*.md") if p.is_file())
    h = hashlib.sha256()
    for p in files:
        h.update(p.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\x1e")
    return h.hexdigest()
```

- root = `personas/`（自动覆盖 `knowledge/` 子目录；人手改 persona 文件同样改变 hash——任何 treatment 变化留在戳里）
- run 开始时计算一次，随本 run 全部 recommendation 行落账（三种 strategy 都记——单 run 单值，实现上经 value 落行参数传入）；**bets 不另加列**，经 `recommendation_id` 关联即可回答「当时知识库是什么」（D6 的「注」口径）
- `runs.summary` 辅助记 `{personas_hash, git_rev, git_dirty}`（git 信息尽力而为，取不到记 null）

### 3.2 窗口快照

- 本窗口**首个 run** 启动时（persona 阶段前）调 `ensure_window_snapshot(idx)`：无快照则把 `personas/knowledge/*.md` 拷入 `evolution/snapshots/w{idx}/`（幂等：已存在即复用；无知识文件则目录空 = 空知识库）
- **B 线 prompt 组装只读快照路径**，不读活文件——merge 随时可做，新版本最早于下一窗口开窗快照生效
- 快照目录随 git 提交（6 周 5 个小文件，审计价值高）
- cron 串行保证无并发竞态；`ensure_window_snapshot` 目录存在性检查幂等

## 4. 知识文件格式（宪章 §11 开放项定稿）

路径 `personas/knowledge/{epl,laliga,bundesliga,seriea,ligue1}.md`（文件名沿用 `PERSONA_FILES` 映射值）。全文语法：

```markdown
<!-- kb: league=E0 generated=2026-10-16 hash=ab12cd34ef56 -->（apply 时生成，解析器忽略）

# {联赛名} 知识库

## 结构性认知
- [E0-S01] 升班马主场季初高跑动风格，盘口惯性低估前 6 轮……

## 时效
- [E0-T03|2026-09-04|90d] 某队主力门将伤缺，预计 11 月复出……

## 教训
- [E0-L07|2026-10-15] 圣诞密集期 downweight 判定过狠（证据：fixtures 12345/12400，放行口径 +8.5%）……
```

- **锚点 = 条目 ID**：`{联赛码}-{S|T|L}{两位序号}`（S 结构性认知 / T 时效 / L 教训）；amendments/deprecations 以此寻址；序号单调递增不复用（删除后空号不补）
- **TTL**：时效条目必带 `[ID|YYYY-MM-DD|Nd]`；修剪**只在进化事件时执行**（窗口边界），run 时不过滤——窗口内 prompt 逐字稳定（与 R6 同理）；被修剪条目在事件报告列明 id 与原因
- **时效/教训条目带日期**；结构性认知不带
- **废弃 = 整条删除**（R7）：deprecation 的 target/reason/日期落 evolution_runs 与滚动报告，git 历史兜底；不设「已废弃」段落
- **长度上限 `KB_MAX_CHARS = 2400`**（与 persona 文件同量级）：提案渲染后超限 = 契约违规 → 该联赛本轮降级；反思 prompt 明示当前长度与上限
- **注入点**：`build_prompt(persona_md, input_obj, kb_md=None)`——persona 全文后、「本场输入」前插 `\n\n## 联赛知识库（快照 w{idx}）\n\n` + kb_md；**只进 model_persona 轨**
- v0 初始态：文件不存在 = 空知识库，prompt 省略该段；首个 merge 创建 v1 文件
- 解析器（knowledge.py）容忍空文件/缺段（视为空），不容忍语法破损条目（锚点格式错 → 解析错误 → 该联赛反思降级）

## 5. 第三轨接线（R1）

| 触点 | 改动 |
|---|---|
| `pipeline/value.py` | `STRATEGIES = ("model_only","model_persona","model_persona_nokb")`（仍是单源）；同刻三落、数字全同；nokb 的 `final_stake_frac` 中性初始 = kelly（同 model_persona 语义，等本轨判决调整）；落行统一带 `personas_hash` |
| `persona/apply.py` | `run_persona_phase` 每场**两次调用**：kb 轨（`kb_md`=本窗快照）写 model_persona 行；nokb 轨（`kb_md=None`）写 model_persona_nokb 行；顺序固定 kb→nokb；降级按轨分别记 reason；pm 沿用 am 的传播按轨各自进行；runs.summary 的 persona 段在原键基础上增 `nokb_*` 镜像键（原键语义不变，保 ops/watchdog 兼容） |
| `pipeline/paper.py` | bankroll 按 strategy 分账自然扩展（迁移已播种）；结算/CLV 链路无改动 |
| `report/render.py` | **TG 推送正文保持双轨口径**：nokb 行不进推送正文与判决标识映射（`_PERSONA_STRATEGY` 不变）；`fa ops weekly` 与 `fa status` B 线栏改三轨表 |
| `dashboard/` | strategy 维度自然呈现三轨（无结构改动） |
| §12.3 | **不变**：A/B 判据仍 model_persona vs model_only；nokb 结论只进 C 线滚动报告 |

成本披露：每场 persona 调用 ×2（kb+nokb，ark 额度）；首个版本合并前两轨 prompt 逐字相同 = 天然基线期，两轨输出差异即 persona 调用噪声底（滚动报告披露为健康度参考）。

## 6. 反思契约与 prompt

### 6.1 调用形态

- 命令同构 persona caller：`hermes -z <prompt> [-t <?>]`；`HERMES_BIN` mock 同路径
- **工具集 T0 探针**（并入 M6，即遗留清单「hermes 空工具集探针」）：优先空工具集形态（不带 `-t` 或空集名，探针定）；不支持则退 `-t search` + 禁工具 prompt 条款（M4 同款 stopgap），滚动报告披露所用形态
- 超时复用 `persona_timeout()` 语义（env `FA_PERSONA_TIMEOUT`），反思默认沿用 120s

### 6.2 prompt 结构（固定顺序）

1. 任务说明：角色 = 联赛知识管理员（**非**人格；与 personas/*.md 的 persona 无关）
2. 当前知识文件全文（若存在），明示当前长度/`KB_MAX_CHARS`
3. 窗口证据 JSON（evidence.py 产出，§7 规格）
4. 输出契约（§6.3 JSON schema + 字段说明）
5. 反偏差条款：**证据不足时 no_change 优于编造**（no_change 是合法且优先的输出）；appends 必须引 evidence.fixtures；必须显式考虑旧条目 amend/deprecate 或说明为何不动；只消费提供的证据 JSON，不得引用外部信息

### 6.3 输出契约与校验规则

```json
{
  "league": "E0",
  "appends": [{"section": "结构性认知|时效|教训", "text": "...",
               "ttl_days": 90, "evidence": {"fixtures": [123], "stat": "..."}}],
  "amendments": [{"target": "E0-S01", "text": "...", "reason": "..."}],
  "deprecations": [{"target": "E0-T03", "reason": "..."}],
  "no_change_reason": null
}
```

校验（任一不过 → `status='contract'`，知识文件与暂存区都不动）：

1. `league` 匹配本联赛
2. appends：`section` ∈ 三段枚举；时效段必带 `ttl_days`（正整数）且非时效段不得带；`evidence.fixtures` 非空整数列表；text 非空
3. amendments/deprecations：`target` 锚点在当前文件中存在；amendment 遵守目标条目所在段的格式（时效段需保持日期/TTL 形态）
4. 变更（三者任一非空）与 `no_change_reason` **二选一**：同时给或同时缺 = 违规
5. 渲染后全文 ≤ `KB_MAX_CHARS`
6. 日期/TTL 格式合法

契约不含手写条目 ID——序号由 apply 按段内递增分配（天然无冲突，非校验项）。

## 7. 证据聚合规格（evidence.py，只读）

按 窗口 × 联赛 产出（fixture 带 id/日期/队名；样本量明示；**未结算注单列计数不进 ROI**）：

```json
{
  "league": "E0",
  "window": {"idx": 1, "from": "2026-09-04", "to": "2026-10-15"},
  "kb_track":   {"n_fixtures": 0, "n_called": 0, "n_degraded": 0,
                 "verdicts": {"agree": 0, "downweight": 0, "veto": 0},
                 "n_bets": 0, "n_settled": 0, "roi": null,
                 "clv_median": null, "clv_mean": null},
  "nokb_track": {"同 kb_track 结构"},
  "model_only_ref": {"n_bets": 0, "n_settled": 0, "roi": null},
  "kills": [{"fixture_id": 0, "date": "", "home": "", "away": "",
             "kb_verdict": "veto", "nokb_verdict": "agree",
             "kb_final_frac": 0.0, "mo_return_on_stake": null}],
  "divergences": [{"fixture_id": 0, "kb_verdict": "", "nokb_verdict": "",
                   "kb_conf_delta": 0.0, "nokb_conf_delta": 0.0}],
  "n_pending_settlement": 0
}
```

- `kills`（误杀明细）：kb 轨 verdict ∈ {veto, downweight} 的场次，对照收益取 **model_only 轨同 fixture 已结算注**的实际收益率（双轨天然提供反事实，确定性、无模拟）
- `divergences`：kb/nokb 两轨 verdict 或 confidence_delta 不同的场次
- SQL 只读 `recommendations`/`bets`/`fixtures`/`matches`；窗口边界按 created_at（北京时间）落在 [opened_at, closes_at) 判定

## 8. 关卡与命令流

```
fa evolve status                                # 当前窗 idx/边界日期；各联赛 KB 活版本 vs 窗口快照版本；
                                                #   最近 Ruling；待审提案
fa evolve tick                                  # 周检（cron 入口）：升序找已收口未反思窗口；
                                                #   前一窗关卡未关 → 记日志顺延（不建行）；
                                                #   否则 5 联赛串行反思，落 evolution_runs，
                                                #   产物 evolution/proposals/w{idx}/{league}.json
                                                #     + {league}.proposed.md + {league}.diff + evidence.json
                                                #   窗口内该联赛 0 判决样本 → 不调 hermes，
                                                #   直接记 no_change（reason=无样本）
fa evolve reflect --window N [--league E0] [--calibrate]
                                                # 手动反思（tick 的单窗版）；
                                                #   --calibrate：用当前至今的部分窗口证据跑真调用，
                                                #   产物落 proposals/calibration-{date}/，不建任何 DB 行，
                                                #   不占 UNIQUE(window_id, league)——正式反思不受影响
fa evolve review [--window N] [--league E0]     # 人审视图：状态 + diff + 证据摘要
fa evolve merge  --window N --league E0 --note "…"
                                                # 原子落账：校验提案存在且未裁定 → 先写真实知识文件 →
                                                #   单事务记 Ruling(merged, kb_hash_after=新树hash) +
                                                #   若因此全联赛终态则记窗口 closed_at → 打印建议 commit message。
                                                #   失败语义：文件已写而事务失败 → 无 Ruling 行，快照保证本窗
                                                #   不受影响；`fa evolve status` 以活树 hash 与 Ruling 不一致披露，
                                                #   可人工补裁定或回退文件
fa evolve reject --window N --league E0 --note "…"   # 只记 Ruling
fa evolve shelve --window N --league E0 --note "…"   # 记 Ruling，提案留暂存区供复看
```

- **重入防护**：run 已有 Ruling → 拒绝二次裁定；窗口已反思 → 拒绝重跑（UNIQUE 兜底）
- **git commit 留给人**：merge 打印建议 commit message，不自动 commit（git 权威在人；忘 commit 不破安全——hash 与 Ruling 已入账，run 读的就是工作树内容）
- 关卡无 SLA：窗口开了关卡没关 → B 线用旧版本照跑（快照钉版），不阻塞
- 窗口 `closed_at` = 全联赛终态（有 Ruling，或 no_change/失败态）时刻
- **提案跨窗冲突防串行化**：tick 反思 w{k+1} 前置要求 w{k} 关卡已关；未关则本周记日志顺延，下周一再查

## 9. 调度（R4）

- `scripts/fa_cron.sh` 第 5 个 job（现有 daily/am/pm/weekly 之后）：周检 `fa evolve tick`，北京时间**周日 03:17**（避开 daily 06:30 / matchday 11:00/17:00 / weekly 周一 07:00），日志 `logs/cron/evolve.log`
- tick 空转（无到期窗口）→ 一行日志退出 0
- 装配：重跑 `scripts/cron_install.sh`（`# BEGIN fa-cron-m5` 标记块整体替换）；hermes 载体切换互斥语义不变
- 失败告警口径（R7）：job 失败沿用 wrapper 的 `fa ops alert`——「静默停摆」指不触碰 A/B 线运行与表，不指对运维静默

## 10. 滚动报告与判据预注册

`docs/evolution/report-YYYYMMDD.md`（每个进化事件一份，descriptive，同线 A 风格）：

- 本期 appends/amendments/deprecations 数与全文 diff 指针；**被推翻/削弱旧条目数**（知识库健康度）；TTL 修剪数
- kb vs nokb 轨道级：ROI 差、误杀率、CLV 对比；基线期（首版本合并前）两轨差异 = persona 调用噪声底，单列披露
- 版本戳串联：本窗口各行 `personas_hash` 分布
- 校准轮结论**不得**作为知识库有效性证据（诚实条款）
- 统计判据 = ≥2 窗口数据后的**后续注册项**：届时先改 spec §12.7 再启用，不回溯套用

## 11. 验收与测试计划（R2）

1. **单元/集成**：窗口切分（边界日期/当前窗）；知识文件解析/锚点寻址/TTL 修剪/上限/语法破损路径；`personas_tree_hash`（内容敏感、路径排序稳定）；evidence SQL（临时库 fixture 数据，含未结算单列）；契约校验全部失败路径（§6.3 七条逐一）；merge 原子性与重入防护；快照钉版（窗口中 merge 不影响本窗 prompt、下窗生效）；三轨落行与按轨传播/降级；`--calibrate` 不落 DB
2. **脚手架 E2E**（临时 git repo + 临时库 + 可编程 `HERMES_BIN` fixture 脚本，响应序列可注入）：tick→reflect→review→merge→**新窗口 run 判决带新 `personas_hash`、nokb 轨 prompt 无 KB 段** 的完整闭环；**降级路径实测三例**（反思超时 / 契约破损 / 关卡拒绝各一，验收判据原文）
3. **校准实跑**：真 hermes + 真台账（当前 W1 部分证据）——预期 `no_change_reason`（样本极少的合法输出）；产物留档校准目录，不合并
4. 真实 W1 收口（~2026-10-15）监控点：tick 触发首次正式反思 → 人审 → 首个真实 v2（若通过）——不阻塞 M6 验收
5. 诚实条款预写：三轨口径分开表述；禁调参凑结论；校准轮不入有效性证据；迁移实测（含备份恢复路径）

## 12. spec 增补草案（v0.10 → v0.11，实施时合入）

### §12.7 进化线（C 线，M6）

> 新增第三条顶层线路 C 线（进化线）：以「反思 → 提案 → 关卡 → 合并」闭环演进 B 线消费的版本化工件（persona 知识库 `personas/knowledge/*.md`；人格文件 v1 不在反思契约内）。状态一律外置 git 版本化文件，agent 无记忆（hermes/dsh 均纯函数调用）。进化事件离线独立调度（周检 tick，B 线 §12.3 前向窗口收口触发），只读 B 线台账、只写自有表与版本化工件，失败静默停摆（记 `evolution_runs`）不影响 A/B 线。判决入账强制记 personas 树内容 hash（`recommendations.personas_hash`）；窗口冻结机械化为快照——B 线 prompt 只读 `evolution/snapshots/w{idx}/`，合并最早于下一窗口生效，关卡逾期自然顺延。C 线对照采用生产第三轨 `model_persona_nokb`（人格无知识库、paper 独立 bankroll、不进 TG 推送正文）；§12.3 判据口径不变。合并经人审关卡：暂存区提案 + merge/reject/shelve 全量记 Ruling（note 强制）；v1 不设统计合并门槛（样本量不可达，诚实注册），统计判据为 ≥2 窗口后后续注册项。进化只沉淀定性知识，不做统计调参——数字归模型与 Python 主控。

### §10 里程碑表 M6 行（验收判据改写）

| 里程碑 | 内容 | 验收判据 | 依赖 |
|---|---|---|---|
| M6 | C 线进化栈：知识库文件 + 反思任务（hermes -z 纯函数）+ 生产第三轨对照 + 版本戳入账 + 快照冻结 + 人审关卡 + 判据预注册 | 脚手架 E2E 全闭环（反思→diff→人审→合并→新窗口判决带新版本戳、nokb 轨无 KB）；降级路径实测三例（反思失败/契约破损/关卡拒绝）；校准实跑（真 hermes 真台账，预期 no_change）；C 线判据文档预注册存档。真实 W1 首合并（~2026-10-15）为监控点不阻塞 | M4 合并✓、M5 节流✓ |

### 其他

- §6.6 双轨表述改三轨（注明 TG 推送保持双轨口径、§12.3 判据不变）
- changelog 行：v0.10 → v0.11 变更摘要
- **宪章档同步小改**：§9 编号 §12.6 → §12.7（retro 已占 §12.6，spec §12.6 末尾已注明本线「不抢号、跟随」）；补记本文 §0 七项裁定

## 13. 非目标（宪章 §10 沿用 + 本次新增）

- 沿用：不做统计调参；不做无人审自动合并；不给 agent 常驻记忆；不建检索/embedding；不动 M5 优先级；不给 DB 写 B 线表/落注通道
- 新增：**人格文件 `personas/*.md` 顶层不在 v1 反思契约内**（只动 `knowledge/`）；nokb 不进 TG 推送正文；不做自动 git commit；不做跨窗并行关卡（串行防提案基线漂移）

## 14. 风险与开放问题

| 风险/开放 | 处置 |
|---|---|
| 三轨每场双调用的 ark 额度 | runs.summary 记 kb/nokb called 数；30-50 场后随 A_multi 成本复盘一并读数 |
| 表重建迁移伤生产库 | 迁移前自动备份 `fa.db.bak-v8`；验收含迁移实测与恢复路径；合并后生产库自迁移（分层协议惯例） |
| 反思质量差/编造证据 | 契约强制 evidence.fixtures + 关卡人审抽查证据链 + no_change 合法且优先 |
| 基线期 kb/nokb 同 prompt 输出不同 | 即 persona 调用噪声底，滚动报告单列披露（是信息不是缺陷） |
| 快照/暂存目录膨胀 | 6 周量级极小，忽略；暂存区 shelve 提案按窗口目录归档 |
| 人审悬置跨多窗 | tick 串行化关卡（前窗未关后窗不反思）；shelve 可复看 |
| 开放：空工具集形态 | T0 探针定；不支持则 `-t search` + 禁工具条款 stopgap，报告披露 |
| 开放：知识文件首版冷启动 | 首个 merge 前知识库为空（合法态）；反思从零建 v1 属正常路径 |
