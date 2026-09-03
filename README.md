# fa — 足球量化分析与投注推荐（研究存档）

分层 Dixon-Coles 进球模型 vs **Pinnacle 收盘价**的诚实研究验证：walk-forward 预测能否跑赢市场。设计上含 Hermes persona（5 个联赛人格做定性分析，spec §6）与 Telegram 推送的完整规划，但 **M2 关卡未通过，persona 从未启用**。唯一权威设计文档是 [spec.md](./spec.md)。

## 当前状态

| 里程碑 | 状态 | 结论 |
|---|---|---|
| M1 数据层 + 回测基建 | ✅ 完成（2026-09-03） | 五大联赛 34 赛季 **59,176 场**全量入库，幂等重跑、抽样 50 场对账通过 |
| M2 模型 + 回测评估 | ⛔ **NO-GO**（2026-09-03） | walk-forward log-loss 劣化 **+3.02%**（判据 ≤1%，超标 3.0 倍），11,605 场 / 2019–2025 七赛季全部 NO-GO |
| M3–M5 价值层 / persona / 实盘 | 🚫 不启动 | 项目按 **spec §10 M2 关卡止步**，本仓库仅为可复现的研究结论存档 |

## 关键文档

- [spec.md](./spec.md) — 权威设计（v0.4 确认稿）；§8 回测判据、§10 里程碑关卡、§11 风险与重启触发条件
- [docs/m1-report.md](./docs/m1-report.md) — M1 数据层验收报告（59,176 场、幂等、对账）
- [docs/m2-verdict.md](./docs/m2-verdict.md) — **M2 NO-GO 判决书**：证据链、分联赛/分赛季、校准、模拟盘分解（§8.3）、half-life 与 σ 双轴稳健性扫描、复现命令
- [docs/m2-report.md](./docs/m2-report.md) — 回测渲染产物（主判决口径，逐字节归档）
- [docs/superpowers/plans/](./docs/superpowers/plans/) — M1 / M2 实施计划

## 复现三步

```bash
uv sync                                               # Python 3.11+，pandas/scipy/typer/pytest
uv run fa init && uv run fa data sync-history          # 建库并拉五大联赛历史 CSV（data/ 不入库）
uv run fa backtest run --from 2019 --to 2025           # 全量 walk-forward 回测（~65 s，11,605 行）
```

完整审计顺序（含 half-life 50/200/400、σ 0.5/0.8/1.5/3.0 扫描、大小球通道与 §4.5 形式协变量消融、只读取数一行）见 [docs/m2-verdict.md](./docs/m2-verdict.md) 的「复现命令」「σ 扫描复现命令」等节。测试：`uv run pytest -q`（98 项，不触碰 `data/fa.db`）。

## 仓库结构

```
src/fa/    data/（入库·队名映射·walk-forward 取数） model/（DC 拟合与预测）
           backtest/（指标·模拟盘·报告） value/（去水） db.py config.py cli.py
tests/     90 项 pytest
docs/      m1-report.md · m2-report.md · m2-verdict.md · superpowers/plans/
data/      gitignored（fa.db + CSV 缓存）——新 clone 必须先 sync-history 重建
spec.md    唯一权威设计文档
```

## 重启前

**先读 [docs/m2-verdict.md](./docs/m2-verdict.md) 的机理诊断与 [spec.md](./spec.md) §11**：模型确有信号（优于常数基线 +7.7%）但概率离散度不足（过度收缩），在收盘价上无信息增量，模拟盘负 EV；half-life 与 σ 两根超参轴均到不了 +1% 门槛——重启需要新的建模思路与样本外确认，而不是调参。
