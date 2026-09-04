# T0 探针记录：反思调用的工具集形态（2026-09-05，控制器执行）

目的：为 M6 反思纯函数（`fa/evolve/reflect.py` 的 `REFLECT_TOOLSET`）选定 hermes 调用形态。要求：反思证据只许来自台账 JSON，调用面不得携带可产生副作用的工具。

## 探针矩阵（真机、timeout 护栏包装）

| 形态 | 命令 | 结果 | 判定 |
|---|---|---|---|
| A：不带 `-t` | `timeout 60 hermes -z "只回复两个字：可用"` | exit 0，回复「可用」 | 命令可跑，但工具枚举探针显示**全量 27 工具**（browser_*、terminal、write_file、process、execute_code、memory、patch…）——反思不可用 |
| B：`-t none` | `timeout 45 hermes -z "只回复ok" -t none` | stderr 两行警告（`ignoring unknown --toolsets entries: none` / `did not contain any valid toolsets`），exit 0，**无模型输出**；工具枚举探针同样只有警告、零输出 | 无效名 + 静默空输出，不可用 |
| C：`-t search` | `timeout 45 hermes -z "…工具名数量" -t search` | exit 0，模型回复「0」 | M4 已验证的唯一可用围栏形态（工具集 = {web_search}） |

工具枚举探针（判别 A/B 的实际工具面）：`hermes -z "不要调用任何工具。直接回答：你当前会话里可用的工具名称列表…"`——A 形态下模型列出 27 个工具名（证明全量）；B 形态下零输出。

## 结论

- **「空工具集」形态不存在**：不带 `-t` 是全量而非空集；无效 toolset 名静默产出空输出。
- **`REFLECT_TOOLSET = "search"`**（计划预设的 stopgap 路径）：`-t search` 将工具面缩到 {web_search} 单工具，prompt 内禁工具条款常驻兜底（「不得使用任何工具或网络检索；证据只来自下方 JSON」）——M4 persona 同款双层围栏。
- 副作用面披露：`-t search` 下理论上 web_search 仍可被模型自主调用（禁工具条款是 prompt 级围栏）；反思输出经契约六校验（evidence.fixtures 强制非空整数列表——**不校验 id 是否命中台账**，命中性由关卡人审抽查证据链兜底），外部信息混入还有滚动报告的场次披露可对照——三层防线。
