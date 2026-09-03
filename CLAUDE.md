# CLAUDE.md — fa（Football Analysis）

## 项目是什么

足球赛事量化分析与投注推荐系统：Python 确定性管线主控，Hermes 作为可替换组件（persona 定性分析 + Telegram 推送）。**唯一权威设计文档是 [spec.md](./spec.md)**——设计问题先查 spec，改设计先改 spec 再改代码。

## 当前状态

- spec **v0.4 确认稿**（2026-09-03）：架构与关键决策已拍板
- **M1（数据层 + 回测基建）完成**（2026-09-03）：五大联赛 34 赛季 59,176 场全量入库、幂等、抽样对账通过——`docs/m1-report.md`
- **M2（模型 + 回测评估）完成，判决 NO-GO**（2026-09-03）：劣化 +3.02%（判据 ≤1%），四重稳健性证据全部 NO-GO——`docs/m2-verdict.md`
- **spec v0.5 双线并存协议（2026-09-03 启用）**：A 线（研究评测，已建成）与 B 线（M3-M5 paper 运营）同程序并存、结论分账（§12）；项目负责人显式推翻「M2 止步」的顺序约束（决策记录 §12.4），**真实下注依然禁止**；B 线预注册判据见 §12.3
- **M3（B 线运营栈）完成**（2026-09-04，docs/m3-report.md）：真跑实测 102 fixtures→14 推荐→14 paper 注、/events 探测实证免费、额度 40/日（500/月档）；对齐为候选池瓶颈（30 项 ≥0.60 待 `fa data aliases --confirm`，2 真歧义）；**M5 首务=额度节流**（单 region/pm 限比赛日），否则第 13 天耗尽免费额度
- **M4（persona 接入）完成**（2026-09-04，docs/m4-report.md）：实跑①契约合规 51.9%→禁工具条款后 100%（9/9），实跑②比赛日 E2E 判决 ok（persona 24 场 called/24 ok/1 veto/0 降级，双轨 64 行、paper 32+31 注，额度 440→420）；**veto 首例抓到候选池跨联赛队名错位**（fixture 100，F1 挂 I1 的 Treviso）；TG 推送经代理实测可达（`hermes status` Telegram ✓ configured，run 内 1 次瞬态失败按设计降级）；遗留与 M5 建议见 m4-report §7

## 关键约束（详见 spec 对应章节）

- agent（persona）只消费/产出结构化 JSON，不碰数据库与核心数字（§1.4 / §2.2 / §6）
- 回测基准用 Pinnacle 收盘价；市场是所有评估的对照线（§3.1 / §8.2）
- 模拟盘（paper）自 M3 起每日自动落注；真实下单一期不做（§7.2 / §9.8）
- 球员级建模与进球时间不进一期模型，边界与触发条件见 §4.5 / §9.8 / §11

## 技术栈与约定

- Python 3.11+、uv、pandas + scipy、typer、pytest、SQLite（WAL）
- 目录规划：`src/fa/{data,model,value,persona,report,backtest,cli.py}` + `personas/*.md`（spec §9.2）
- 与用户沟通用中文

## 环境

- Hermes **TG 平台未配置**（2026-09-03 E2E 实测：`~/.hermes/.env` 全注释、无任何 token，`hermes send --to telegram` 报 `Platform 'telegram' is not configured`）；配好后 `fa` 的推送自动恢复，代码侧降级路径已验证（`runs.summary` 记推送失败、不中断 run）；`hermes -z` headless 一次性运行；默认模型 ark-code-latest（火山方舟，跑现有额度）
- `ODDS_API_KEY` 走环境变量（`.env`，gitignore）
