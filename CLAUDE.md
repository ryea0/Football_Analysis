# CLAUDE.md — fa（Football Analysis）

## 项目是什么

足球赛事量化分析与投注推荐系统：Python 确定性管线主控，Hermes 作为可替换组件（persona 定性分析 + Telegram 推送）。**唯一权威设计文档是 [spec.md](./spec.md)**——设计问题先查 spec，改设计先改 spec 再改代码。

## 当前状态

- spec **v0.4 确认稿**（2026-09-03）：架构与关键决策已拍板（M2 判据、判决映射、17:00 更新版、建模盲区边界、Provider/模拟盘）
- 尚无代码；下一步是 **M1（数据层 + 回测基建）**
- **M2 是 go/no-go 关卡**：walk-forward log-loss 相对去水收盘基准劣化 ≤1% 才继续，否则项目止步于研究结论

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
