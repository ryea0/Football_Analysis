"""价值层门槛与仓位的**单一事实源**（spec §5.2 / §5.3）。

六个常量逐字钉死于此；``fa/backtest/simulate.py`` 从此 import 后 re-export——
回测（A 线）与实时推荐（B 线）用同一组门槛，改一处两处生效，改值须先改 spec。

``ev_of`` / ``kelly_fraction`` 是 §5.2/§5.3 两条公式的落点。本模块只依赖纯算术，
不 import fa.backtest / fa.db（避免环，回测模块反向 import 本模块）。
"""

EV_MIN = 0.03           # EV ≥ 3%（spec §5.2）
EDGE_MIN = 0.02         # edge = model_p − market_p ≥ 2%（spec §5.2）
ODDS_MIN = 1.4          # 赔率带下限：排除模型噪声主导的极端热门（spec §5.2）
ODDS_MAX = 6.0          # 赔率带上限：排除极端冷门（spec §5.2）
KELLY_FRAC = 0.25       # ¼ Kelly（spec §5.3）
STAKE_CAP = 0.02        # 单注上限 2% bankroll（spec §5.3）

__all__ = ["EV_MIN", "EDGE_MIN", "ODDS_MIN", "ODDS_MAX", "KELLY_FRAC",
           "STAKE_CAP", "ev_of", "kelly_fraction"]


def ev_of(p: float, odds: float) -> float:
    """每 1 单位注金的期望回报：``EV = p·(o−1) − (1−p)``（spec §5.2）。"""
    return p * (odds - 1) - (1 - p)


def kelly_fraction(p: float, odds: float) -> float:
    """¼ Kelly 仓位，截断到 ``[0, STAKE_CAP]``（spec §5.3）。

    满 Kelly ``f* = (p·o − 1)/(o − 1)``，乘 ``KELLY_FRAC`` 后：无优势（f* ≤ 0）
    取 0（不倒贴），上限 ``STAKE_CAP`` 封顶（单注 ≤ 2% bankroll）。
    ``odds <= 1`` 时无盈利空间，直接返回 0.0——既是语义也是除零守卫。
    """
    if odds <= 1:
        return 0.0
    f = KELLY_FRAC * (p * odds - 1) / (odds - 1)
    return min(max(f, 0.0), STAKE_CAP)
