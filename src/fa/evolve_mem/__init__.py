"""M7a kbmem：mem0 派生检索索引（docs/superpowers/specs/2026-09-13-m7a-mem0-kbmem-design.md）。

markdown 权威源不动；本包只做三件事：JSONL 规范派生与窗口冻结（snapshot）、
hash 键控索引与对账（index）、按场检索与内联降级（retrieve）。agent 无状态
（M6 D1）——mem0 是外置检索视图，不是 agent 记忆。
"""


class EvolveMemError(Exception):
    """kbmem 检索链路错误（调用方降级，绝不炸 run——设计档 §8）。"""
