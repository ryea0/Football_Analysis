"""C' 线（自反思知识库对照线，spec §12.7 对照实验）：
时间线笔记式知识库 + hermes -z 自由式反思 + 无人审自动落账。

与 C 线（evolve 模块）的核心差异：
- 知识结构：时间线笔记式（按窗口累积） vs 三段式结构化
- 反思风格：球探笔记自由式（markdown 全文输出） vs 结构化 JSON 契约
- 人审关卡：v1 无，自动落账，事后可回滚 vs 强制人审 merge/reject/shelve
- 输出契约：markdown 全文 vs JSON appends/amendments/deprecations

物理边界：对 B 线表只读；只写自有 evolution_self_* 表与
personas/knowledge_self/ 版本化工件；无 Odds API、无落注通道。
"""


class EvolutionSelfError(Exception):
    """C' 线错误族（降级语义：记 evolution_self_runs，不动任何 B 线表）。"""
