# CLAUDE.md — fa（Football Analysis）

## 项目是什么

足球赛事量化分析与投注推荐系统：Python 确定性管线主控，Hermes 作为可替换组件（persona 定性分析 + Telegram 推送）。**唯一权威设计文档是 [spec.md](./spec.md)**——设计问题先查 spec，改设计先改 spec 再改代码。

## 当前状态

- spec **v0.4 确认稿**（2026-09-03）：架构与关键决策已拍板
- **M1（数据层 + 回测基建）完成**（2026-09-03）：五大联赛 34 赛季 59,176 场全量入库、幂等、抽样对账通过——`docs/m1-report.md`
- **M2（模型 + 回测评估）完成，判决 NO-GO**（2026-09-03）：分层 Dixon-Coles 的 walk-forward log-loss 劣化 **+3.02%**（判据 ≤1%），11,605 场 / 2019-2025 七赛季；**四重稳健性证据全部 NO-GO**——half-life 与 σ 双轴扫描、大小球通道对照（+2.25%）、§4.5 形式协变量消融（+3.22%，反而更差）——机理详见判决书补充节——`docs/m2-verdict.md`（含复现命令、校准表、§8.3 模拟盘分解附录；判决经独立逐位复现审查确认）
- **项目按 spec §10 M2 关卡止步：M3+（价值层实盘、TG 报告、persona、实时盘、下单）不启动**。本仓库为完整可复现的研究结论存档——重启用前先读 `docs/m2-verdict.md` 的机理诊断与 spec §11 触发条件

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

- Hermes 已配好：`hermes send --to telegram` 直接可用；`hermes -z` headless 一次性运行；默认模型 ark-code-latest（火山方舟，跑现有额度）
- `ODDS_API_KEY` 走环境变量（`.env`，gitignore）
