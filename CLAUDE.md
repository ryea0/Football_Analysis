# CLAUDE.md — fa（Football Analysis）

## 项目是什么

足球赛事量化分析与投注推荐系统：Python 确定性管线主控，Hermes 作为可替换组件（persona 定性分析 + Telegram 推送）。**唯一权威设计文档是 [spec.md](./spec.md)**——设计问题先查 spec，改设计先改 spec 再改代码。

## 当前状态

- spec **v0.7 确认稿**（2026-09-04）：架构与关键决策已拍板；v0.5 双线协议、v0.6 本地看板（§7.4）、v0.7 调度双轨化（§9.6）逐版叠加
- **M1（数据层 + 回测基建）完成**（2026-09-03）：五大联赛 34 赛季 59,176 场全量入库、幂等、抽样对账通过——`docs/m1-report.md`
- **M2（模型 + 回测评估）完成，判决 NO-GO**（2026-09-03）：劣化 +3.02%（判据 ≤1%），四重稳健性证据全部 NO-GO——`docs/m2-verdict.md`
- **spec v0.5 双线并存协议（2026-09-03 启用）**：A 线（研究评测，已建成）与 B 线（M3-M5 paper 运营）同程序并存、结论分账（§12）；项目负责人显式推翻「M2 止步」的顺序约束（决策记录 §12.4），**真实下注依然禁止**；B 线预注册判据见 §12.3
- **M3（B 线运营栈）完成**（2026-09-04，docs/m3-report.md）：真跑实测 102 fixtures→14 推荐→14 paper 注、/events 探测实证免费；**额度节流梯子已落地**（quota<200 单 eu / <100 pm 跳拉盘，月耗推算 ~300——`.superpowers/sdd/quota-throttle-report.md`）；**别名对齐已完成**（oddsapi 侧 44 条确认、隔离表清零）
- **M4（persona 接入）完成**（2026-09-04，docs/m4-report.md）：实跑①契约合规 51.9%→禁工具条款后 100%（9/9），实跑②比赛日 E2E 判决 ok（persona 24 场 called/24 ok/1 veto/0 降级，双轨 64 行、run#7 实落 32 注=A 轨 1+B 轨 31）——A 轨候选多被遗留注幂等去重，额度 440→420）；**veto 首例抓到候选池跨联赛队名错位**（fixture 100，F1 挂 I1 的 Treviso）；TG 推送经代理实测可达；遗留与 M5 建议见 m4-report §7；**集成注（2026-09-04）**：M4 分支已并入本集成分支，persona 两列并入 schema v6
- **M5 运营基建完成**（2026-09-04）：cron **双载体可切换**（§9.6，负责人裁定）——`scripts/fa_cron.sh` 为三 job 唯一入口（失败→`fa ops alert` TG 告警、daily 后 `fa ops watchdog` 查漏跑，风险 #6 落地），装配用 `scripts/cron_install.sh`（system crontab，**当前激活**）或 `scripts/hermes_cron_install.sh`（hermes cron，互斥切换）；「连续跑通一周」观察期进行中。**CLV 基准链修复**（spec v0.8，schema v6）：football-data 2025-12 起断供 Pinnacle → Betfair 交易所收盘 fallback（`closing_source` 记账），`fa data backfill-bfe` 回填 3,492 行、`fa ops backfill-clv` 补齐首批 4 注（CLV 中位 +2.9%）
- **范式对比线（§12.5）立项**（2026-09-04）：线 A（dsh headless agent 当大脑）与线 P 长期并行滚动对比，不设样本上限；设计 `docs/superpowers/specs/2026-09-04-agentline-dual-track-design.md`，首批 E2E 完成（10 场×双线全 ok，快照库），滚动扩批中
- **M6（C 线进化栈）立项**（2026-09-04，docs/superpowers/specs/2026-09-04-m6-evolution-line-design.md）：第三条顶层线路——知识库版本化外置 + hermes -z 反思纯函数 + 窗口冻结合并 + 版本戳入账；spec 增补草案取 §12.6（§12.5 为范式对比线占用）；实施依赖 M4 合并与 M5 节流落地（两者均已完成，已具备开工条件），未插队
- **v0.6 本地只读看板**（§7.4，`dashboard/`，Streamlit）与 **retro 复盘归因子线**（`src/fa/retro/` + `fa retro`，设计 docs/superpowers/specs/2026-09-04-retro-attribution-design.md）已进主线

## 关键约束（详见 spec 对应章节）

- agent（persona）只消费/产出结构化 JSON，不碰数据库与核心数字（§1.4 / §2.2 / §6）
- 回测基准用 Pinnacle 收盘价；市场是所有评估的对照线（§3.1 / §8.2）。**B 线 CLV 收盘基准链（spec v0.8）**：Pinnacle 优先、缺失 fallback Betfair 交易所收盘（`bfe_*`），`bets.closing_source` 记账实际所用——football-data 自 2025-12 断供 Pinnacle
- 模拟盘（paper）自 M3 起每日自动落注；真实下单一期不做（§7.2 / §9.8）
- 球员级建模与进球时间不进一期模型，边界与触发条件见 §4.5 / §9.8 / §11

## 技术栈与约定

- Python 3.11+、uv、pandas + scipy、typer、pytest、SQLite（WAL）
- 目录规划：`src/fa/{data,model,value,persona,report,backtest,cli.py}` + `personas/*.md`（spec §9.2）
- 与用户沟通用中文

## 环境

- Hermes **TG 平台已配置可用**（M4 E2E 2026-09-04 实证：`~/.hermes/.env` 含 `TELEGRAM_BOT_TOKEN`，`hermes status` Telegram ✓ configured，TG 推送实送成功——docs/m4-report.md §3.8；须过代理，见下节）；`hermes -z` headless 一次性运行（推理可用）；默认模型 ark-code-latest（火山方舟，跑现有额度）；代码侧推送失败降级路径已验证（`runs.summary` 记推送失败、不中断 run）
- `ODDS_API_KEY` 走环境变量（`.env`，gitignore）

### TG 推送代理依赖（2026-09-04 实测）

- 本机直连 api.telegram.org 不通（区域封锁），必须走 Clash 代理 `127.0.0.1:7890`（clash-verge 常驻）
- 交互式跑法：`export https_proxy=http://127.0.0.1:7890 http_proxy=http://127.0.0.1:7890 && uv run fa run matchday --phase am`
- 代理离线时：推送失败按设计降级（runs.summary 记原因、run 不中断、报告静默落库）——不是 bug
- bot: @Cheung_football_analysis_bot，home channel 8853969879

### cron 定时跑批（M5，spec §9.6 双载体）

- 三条 job（北京时间）：daily 06:30 / matchday-am 11:00 / matchday-pm 17:00；入口 `scripts/fa_cron.sh`，日志 `logs/cron/`
- **当前激活载体：system crontab**（`crontab -l` 可见 `# BEGIN fa-cron-m5` 标记块）；切到 hermes 载体：`scripts/cron_install.sh --remove && scripts/hermes_cron_install.sh`（后者需 `hermes gateway` 在跑）
- 失败告警：job 失败 → `fa ops alert` 即时 TG；daily 漏跑/连续失败（最近两次成功间隔 >25h）→ `fa ops watchdog` 告警（仅 daily wrapper 收尾调一次）

### 本地只读看板（v0.6，`dashboard/`）

- 启动：`uv run --group dashboard streamlit run dashboard/app.py` → 浏览器开 http://localhost:8501（streamlit/plotly 走 `dashboard` 依赖组，首次自动安装；后台常驻可 `nohup … &`）
- 只读：`connect_ro` 连生产库 `data/fa.db`，不写任何表；A 线 3 页（回测总览/校准/模拟盘）+ B 线 4 页（总览/推荐台账/AB 双轨/运维健康）
