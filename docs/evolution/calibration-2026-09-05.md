# M6 校准实跑记录（T15，2026-09-05）

- 命令：`uv run fa evolve reflect --calibrate --league E0`（真 hermes、真生产台账、零落库）
- 产物：`evolution/proposals/calibration-2026-09-05/`（E0.raw.txt / E0.evidence.json）
- DB 验证：`evolution_runs` / `evolution_windows` 计数均为 0（校准零落库的机械验证）

## 结果

反思输出为合法 **no_change** 契约（appends/amendments/deprecations 全空 + no_change_reason）：

> 首窗口（idx=1）8 场 11 注全部未结算，ROI/CLV 均为 null，无任何已验证的结果性证据。2 场 downweight 仅为过程记录，缺乏结算验证无法形成教训或结构性认知。按纪律，证据不足时不编造条目，待后续窗口积累结算数据后再写入。

## 判读（诚实条款适用）

- **符合 R2 预期**：「预期输出 no_change_reason——本身就是契约合法输出」得到实证。
- **三层防线初步实证**：证据只来自台账 JSON（输出逐点引用 evidence 字段）；「证据不足时 no_change 优于编造」的纪律条款被反思遵守；契约形状合法。
- **本记录不作为知识库有效性证据**（校准轮结论不入有效性断言——spec §12.7 / 设计档 §10 固定声明）。
- 真实首次反思在 W1 收口（~2026-10-15）由周检 tick 触发，届时按人审关卡走 merge/reject/shelve。

## 验收清单对照（spec §10 M6 行）

| 判据 | 状态 |
|---|---|
| 脚手架 E2E 全闭环（反思→diff→人审→合并→新窗口判决带新版本戳、nokb 轨无 KB） | ✅ tests/evolve/test_e2e.py 4/4（878 passed 全量） |
| 降级路径实测三例（反思超时/契约破损/关卡拒绝） | ✅ 同文件三 degradation 用例 |
| 校准实跑（真 hermes 真台账，预期 no_change） | ✅ 本记录 |
| C 线判据文档预注册存档 | ✅ spec v0.11 §12.7（宪章 §9/§12 同步） |
| 真实 W1 首合并（~2026-10-15） | 监控点，不阻塞验收——已列观察期读数清单 |

## 部署核验（同日）

- 生产库自迁移：schema_version=8；85 注台账 join 全通（85=85）；`data/fa.db.bak-v8` 备份就位
- cron 装配：`scripts/cron_install.sh` 重跑，crontab 标记块含 evolve 周检 job（周日 03:17）
