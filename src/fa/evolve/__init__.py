"""M6 C 线（进化线，spec §12.7）：知识库版本化外置 + hermes -z 反思纯函数
+ 窗口冻结合并 + 版本戳入账。

物理边界：对 B 线表（recommendations/bets/runs…）只读；只写自有
evolution_* 表与 personas/knowledge/ 版本化工件；无 Odds API、无落注通道。
"""


class EvolutionError(Exception):
    """进化线错误族（降级语义：记 evolution_runs，不动任何 B 线表）。"""
