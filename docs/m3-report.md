# M3 验收报告（B 线 E2E 实测）

- 日期：2026-09-03
- 代码：branch `worktree-m3-dual-track`，基线 `e1488f7`（三流合流收尾）+ 本次 fix `4313190`
- 库文件：worktree 本地 `data/fa.db`（v3：A 线 canonical 59,176 场 + `backtest_predictions` 11,605 行；B 线五表在本次验收前**全为空**）
- 密钥：`ODDS_API_KEY` 已配置（worktree 根 `.env`，gitignore）
- 本报告所有数字均为实跑测得，无推算值

## 1. 验收表（spec §10 M3 / §12.1 边界）

| # | 验收项 | 结果 | 实测值 |
| --- | --- | --- | --- |
| 1 | 无 key 冒烟（优雅退出） | ✔ | exit 0；`判决：no_key（无密钥）`；runs 行 `status='no_key'`，0 拉盘 / 0 推荐 / 0 落注 / 0 推送 |
| 2 | 全量测试 | ✔ | **398 passed**（验收起点，2.96s）；本次 fix 后 **400 passed**（+2 新例） |
| 3 | §12.1 表边界 grep 审计 | ✔ | `src/fa/pipeline/` + `src/fa/report/` 内 `INSERT/UPDATE/DELETE … backtest_predictions` **0 命中**（`matches` 同样 0 命中）；daily 的 `sync_history` 复用是唯一的 matches 触碰点，且只写 A 线自有路径 |
| 4 | §12.1 运行时边界 | ✔ | 真跑 am+pm 后 `matches` **59,176**（不变）、`backtest_predictions` **11,605**（不变）；B 线只写了 `fixtures / odds_snapshots / recommendations / bets / runs / meta / team_aliases / unknown_names` |
| 5 | `fa status` 双线分账 | ✔ | A 线 `n=11605 模型 log-loss=0.9979 市场=0.9687 劣化=+3.02% 判决=NO-GO`；B 线 `注数=0 … ROI=— bankroll=未初始化 CLV 中位数=—`（B 线不引用回测数字，两线不互相冒充） |
| 6 | 比赛日端到端真跑 | ✔（TG 除外） | am 与 pm 各跑一次：拉盘→对齐→推荐→落注→渲染→推送，run 判决均 `ok`，耗时 27s / 20s |
| 7 | paper 自动落注 | ✔ | am 落 **14 注**（`status=pending`，注金合计 217.08 / bankroll 1000.00）；pm **0 新注**（去重生效，台账仍 14 注） |
| 8 | CLV 可计算 | ◐ | 机制就绪：14 注都带 `odds_taken`（均值 3.779），`closing_odds` 待收盘回填；**今日无法实测**（比赛未踢，见 §7） |
| 9 | TG 报告送达 | ✘ | **未送达**：hermes 本机未配置任何 messaging platform（环境缺口，非代码缺陷，见 §6） |
| 10 | 额度记账 | ✔ | `/events` 探测**实测免费**（见 §5）；两相各耗 20 credits，剩余 460/500 |

## 2. 真跑数字（漏斗）

am（run #3，2026-09-03 14:58:06Z，27s）与 pm（run #4，15:03:09Z，20s）：

| 阶段 | 数值 | 说明 |
| --- | --- | --- |
| 拉盘 upsert fixtures | 102 场 | E0 20 / SP1 24 / D1 18 / I1 22 / F1 18 |
| odds_snapshots | +4,944 / +4,941 | h2h：eu 26 家 / uk 21 家；totals：eu 17 家 / uk 5 家（append-only） |
| 双侧队名对齐 | 48/102 = **47.1%** | 首轮对齐，大头进隔离表——**设计内**（T4 预警命中） |
| 52h 窗口内场次 | 30 场 | kickoff 2026-09-03 18:45Z → 09-05 19:00Z |
| 窗口内双侧对齐 | 10/30 = 33% | 候选池的真实瓶颈是队名对齐，不是门槛 |
| 推荐条数 | am 14 / pm 14 | H 6 / A 3 / D 3 / O2.5 2；分布 E0 3、D1 2、F1 5、I1 4、SP1 0 |
| 候选场次 | 9 场 | 单场可出多条市场推荐 |
| paper 落注 | am 14 / **pm 0** | 去重键 `(fixture, market, strategy, mode)`——pm 跨相不重下；单注注金 6.50~20.00，其中 **5 笔触到 2% 单注上限（20.00）**，Kelly 上限在真实数据上生效 |
| 新增/消失候选（pm diff） | 0 / 0 | 盘口移动 2 条（见 §4），未变 12 条只计数不重发 |

推荐的 EV 偏高（均值 +29.8%~+41.9%，最高 +85.1%）——这正是 M2 判决 NO-GO（劣化 +3.02%）所指的模型失准：价值层在**负选择池**上照常工作，B 线因此必须是 paper 模式（§12.4）。

## 3. 额度账（含 `/events` 免费的实证）

| 时点 | 事件 | 额度（响应头） |
| --- | --- | --- |
| run 前 | `meta.odds_quota_remaining` 不存在 | **NULL**（从未拉过 → `credits_before=NULL`） |
| am 内 | 5 个联赛逐个调 `/events` 探测（不带盘口） | **500**（`summary.probe_quota`） |
| am 后 | 5 联赛 × {eu,uk} × {h2h,totals} 计费拉盘 | **480**（Δ = **20**） |
| pm 前 | `credits_before` | 480 |
| pm 内 | **无探测**（库内已有窗口 fixture → 探测整个不发） | — |
| pm 后 | 同上计费拉盘 | **460**（Δ = **20**） |

**`/events` 免费这一文档口径，由两次 run 的差分独立实证**：pm 无探测也恰好只耗 20 credits，故 am 的 20 credits 差值必然全部来自计费拉盘，5 次 `/events` 贡献为 0。计费算术自洽：每次请求 cost = regions(1) × markets(2) = 2 credits，每联赛 2 次请求 = 4，5 联赛 = **20**。

响应头 `x-requests-remaining` 首拉即 **500**（`probe_quota`），与免费档 500/月 的满额吻合——故按总量 500 记账（这是从「首次拉取剩余=500」做的推断，响应头本身只给剩余量，不给总量）。本次 E2E 消耗 40，**剩余 460**。

> **额度纪律警报（必须进 M5 排期）**：am+pm 两相全量跑 = 40 credits/日。500 credits/月只够 **12.5 天**，spec §3.4 的「低于阈值自动降频」只能延缓不能避免。可行的降频抓手（实现已在）：`--leagues` 限联赛、region 拆分后单传一个（省一半）、pm 只在比赛日跑。建议 M5 直接把「每日两相全量」改为「比赛日才拉 + 单 region」。

## 4. TG 报告（正文已还原，未送达）

推送失败，两次同因。`runs.summary.telegram`：

```
exit 1: hermes send: Platform 'telegram' is not configured. Set up credentials
        in ~/.hermes/config.yaml or environment variables.
```

代码路径**完全按 §9.5「降级不中断」工作**：`send` 返回 False → `LAST_TELEGRAM_ERROR` 写进 runs.summary → 推荐/落注照常落库 → run 判决仍是 `ok`，CLI 逐字打印失败原因。诊断结论是**环境缺口而非 fa 缺陷**：

- `hermes send --list` → `No messaging platforms configured or no channels discovered yet.`
- `~/.hermes/config.yaml` 的 `platforms:` 段只有 `feishu: enabled: true`；`~/.hermes/.env` 是全注释的模板（无任何 token）
- 即 CLAUDE.md「Hermes 已配好：`hermes send --to telegram` 直接可用」在**本机当前状态不成立**（T8 当时实测核对的是 hermes 的参数形态，不是投递）

离线复渲染（只读库内缓存，零额度）得到当时应送达的正文，骨架符合 §7.1。pm 更新版证明 §7.1-5 的 diff 语义成立：

```
## 盘口移动（2）
- F1 Lyon vs Auxerre · 平局 · 纯模型：4.70 → 4.60 （CLV 预览 +2.2%）
- E0 Fulham vs Crystal Palace · 主胜 · 纯模型：2.39 → 2.40 （CLV 预览 -0.4%）
## 新增候选（0）  ## 已消失（0）
## 未变  - 未变候选 12 条，不重发（去重）
```

复现脚本：`.superpowers/sdd/2026-09-03-m3-b-line/t12-render-check.py`（gitignore，不入库）。

## 5. 无 key 冒烟 + 本次修的缺陷（`4313190`）

冒烟本身通过：`fa run matchday --phase am` → exit 0、`no_key（无密钥）`、runs 记 `no_key`，零触网。**但它暴露了一个真缺陷**：worktree 根明明放着配置好的 `.env`，程序却报无 key。全仓 grep 证实 `load_env()` 只有定义与 6 个测试，**生产代码零调用点**——spec §9.4「key 走 .env」是死配置；hermes cron（§9.6）的裸环境永远拿不到 key，每日 run 会全部 `no_key`，M3「端到端跑通一周」的验收前提不成立。

修复（TDD，先红后绿，commit `4313190`）：`fa` 的 typer 回调（每个子命令必经）调用 `load_env()`，setdefault 语义保证 shell 已导出键优先、重复加载幂等。新增 2 例（CLI 启动装载 `.env`；shell 导出优先），并为 `test_cli_matchday_no_key_exits_zero` 补 `project_root` 隔离——不隔离则真机 `.env` 会把 key 装回环境，无 key 分支永远测不到。**修复后带真实 `.env` 全量 400 passed**。修复后复跑冒烟（`.env` 移开）仍是优雅 `no_key`，exit 0。

由此冒烟实际跑了两次（runs #1、#2 都是 `no_key`），是修复前后的各一次，非重复污染。

## 6. 对齐命中率与 unknown 清单（34 条，全名 vs 缩写）

首轮隔离 34 个 Odds API 侧全名，逐条给了建议（`fa data aliases`）：**30 条 top1 ≥ 0.60**，人工一次 `--confirm` 即可收敛；**2 条真歧义**（设计规定不猜，隔离正确）：`Borussia Monchengladbach`→`M'Gladbach`(113)/`M'gladbach`(119) 并列 0.5625、`Inter Milan`→`Inter`(152)/`Milan`(159) 并列 0.6667。命中规律与 T4 预警逐字一致：football-data canonical 缩写 vs Odds API 全称。

<details><summary>34 条隔离名 + 建议（ratio）</summary>

`AC Milan`→Milan .83｜`AS Monaco`→Monaco .86｜`AS Roma`→Roma .80｜`Athletic Bilbao`→Ath Bilbao .78｜`Atlético Madrid`→Ath Madrid .70｜`Bayer Leverkusen`→Leverkusen .80｜`Borussia Dortmund`→Dortmund .67｜`Borussia Monchengladbach`→*歧义*｜`Brighton and Hove Albion`→Brighton .55｜`Celta Vigo`→Celta .71｜`Coventry City`→Coventry .80｜`Deportivo La Coruña`→La Coruna .64｜`Eintracht Frankfurt`→Ein Frankfurt .80｜`Elche CF`→Elche .83｜`FSV Mainz 05`→Mainz .67｜`Hamburger SV`→Hamburg｜`Hull City`→Hull .67｜`Inter Milan`→*歧义*｜`Ipswich Town`→Ipswich .78｜`Le Mans FC`→Le Mans .86｜`Leeds United`→Leeds .63｜`Manchester City`→Man City .67｜`Manchester United`→Man United .72｜`Newcastle United`→Newcastle .75｜`Nottingham Forest`→Nott'm Forest .81｜`Paris Saint Germain`→Paris SG .58｜`RC Lens`→Lens .80｜`Rayo Vallecano`→Vallecano .82｜`Real Betis`→Betis .71｜`Real Racing Club de Santander`→Santander .53｜`Real Sociedad`→Sociedad .80｜`TSG Hoffenheim`→Hoffenheim .87｜`Tottenham Hotspur`→Tottenham .72｜`VfB Stuttgart`→Stuttgart .86

</details>

**运营含义（M5 前必须处理）**：候选池瓶颈是对齐不是门槛——窗口内 30 场只有 10 场双侧对齐。把 34 条建议人工确认后，可对齐场次会显著上升，推荐与落注数随之增长。别名确认按 §3.3 属人工操作，本次未代做。

## 7. 已知限制（本次无法实测的部分）

1. **真实结算与 CLV 回填未实测**：窗口内 30 场在 2026-09-03 18:45Z 之后才开球，无已完赛场次可结算。结算/CLV/bankroll 记账逻辑由 T7 的测试覆盖（T7 完成时 281 tests；`place_paper_bets` / `settle_paper_bets` / ROI 只计终态 / CLV 中位数），但「`fa run daily` 在真实完赛数据上把注结掉」这一步只能等次日数据。另一前置是 fixture↔match 配对需要队名对齐，当前 33% 的窗口对齐率会让部分注挂 `pending` 直到别名补齐。
2. **TG 投递未实测**（§4）：需先在 hermes 配置 telegram 凭证。修复后**没有一键重发**——spec §9.3 列的 `fa report send`（手动重发最近报告）在 M3 计划里未派任务、未实现。
3. **额度基线缺一手**：本次 am 是该 key 的第一次拉盘，`credits_before=NULL`，只能靠 am/pm 两相差分反推（§5 的做法）。第二次比赛日起就有完整 `credits_before/after` 链。
4. **am/pm 各只跑一次**：按派单额度纪律（am 一次、pm 一次，调试用缓存快照），未做连续多日稳定性观察；「连续跑通一周」的验收（§10 M3）留待 M5 以 cron 形态达成。
5. **CLAUDE.md 的 Hermes 声明过时**（§4），建议随 M4/M5 修正，避免下一个会话再被误导。

## 8. 提交

| commit | 内容 |
| --- | --- |
| `e1488f7` | 验收基线（三流合流收尾，398 tests） |
| `4313190` | fix(cli): CLI 启动接线 `.env`——`load_env` 此前零生产调用点（本次 E2E 实测发现）；400 tests |
