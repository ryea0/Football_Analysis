"""比例法去水（spec §5.1）。一期仅 proportional；Shin 法留待后续同签名扩展。"""
from collections.abc import Sequence


def devig_proportional(odds: Sequence[float]) -> list[float]:
    """比例法去水：隐含概率 1/o 按比例归一到和为 1（spec §5.1 一期）。

    Shin 法留待后续同签名扩展。
    """
    inv = [1.0 / o for o in odds]
    s = sum(inv)
    return [v / s for v in inv]


def devig_ou(over: float, under: float) -> tuple[float, float]:
    """大小球比例法去水：返回 (p_over, p_under)，和为 1（spec §5.1 一期）。

    Shin 法留待后续同签名扩展。
    """
    io, iu = 1.0 / over, 1.0 / under
    s = io + iu
    return io / s, iu / s
