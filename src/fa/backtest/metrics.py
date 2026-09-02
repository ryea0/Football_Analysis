"""回测评估指标：log-loss / Brier / 校准分桶 / go-no-go 判决（spec §8.2）。

判据是 spec 的关卡，不得改动：模型 log-loss 相对去水收盘基准**劣化 ≤1% 即 GO**，
劣化 >1% → NO-GO（项目止步、研究结论存档）。
"""
import math
import sqlite3

_EPS = 1e-12
_IDX = {"H": 0, "D": 1, "A": 2}


def fetch_predictions(conn: sqlite3.Connection, leagues=None,
                      seasons=None) -> list[dict]:
    """读 backtest_predictions（Task 2/6 产出的表），可选联赛/赛季过滤。"""
    sql = "SELECT * FROM backtest_predictions WHERE 1=1"
    args: list = []
    if leagues:
        sql += f" AND league IN ({','.join('?' * len(leagues))})"
        args += list(leagues)
    if seasons:
        sql += f" AND season IN ({','.join('?' * len(seasons))})"
        args += list(seasons)
    return [dict(r) for r in conn.execute(sql, args)]


def log_loss(probs, outcomes) -> float:
    """多分类 log-loss（自然对数），概率先 clip 到 [1e-12, 1-1e-12]。"""
    total = 0.0
    for (ph, pd, pa), o in zip(probs, outcomes):
        p = (ph, pd, pa)[_IDX[o]]
        total -= math.log(min(max(p, _EPS), 1 - _EPS))
    return total / len(outcomes)


def brier(probs, outcomes) -> float:
    """三分量多项 Brier：Σ_i (p_i − y_i)² / 2（除以 2 归一到 [0, 1]）。"""
    total = 0.0
    for (ph, pd, pa), o in zip(probs, outcomes):
        y = [0.0, 0.0, 0.0]
        y[_IDX[o]] = 1.0
        total += sum((a - b) ** 2 for a, b in zip((ph, pd, pa), y)) / 2
    return total / len(outcomes)


def calibration(p: list[float], hit: list[bool], bins: int = 10) -> list[dict]:
    """等宽分桶校准曲线：[{lo, hi, n, avg_p, emp}]，空桶省略，右端点含 1.0。"""
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [i for i, v in enumerate(p)
               if lo <= v < hi or (b == bins - 1 and v == 1)]
        if not sel:
            continue
        out.append({"lo": lo, "hi": hi, "n": len(sel),
                    "avg_p": sum(p[i] for i in sel) / len(sel),
                    "emp": sum(1 for i in sel if hit[i]) / len(sel)})
    return out


def evaluate(rows: list[dict]) -> dict:
    """全样本（或任一分组）的对比摘要；verdict 为 spec §8.2 的 go/no-go 关卡。"""
    if not rows:
        raise ValueError("无预测行——先跑回测或检查过滤条件")
    probs_m = [(r["p_home"], r["p_draw"], r["p_away"]) for r in rows]
    probs_k = [(r["mkt_home"], r["mkt_draw"], r["mkt_away"]) for r in rows]
    outs = [r["outcome"] for r in rows]
    mll, kll = log_loss(probs_m, outs), log_loss(probs_k, outs)
    deg = (mll / kll - 1) * 100 if kll > 0 else 0.0
    return {"n": len(rows), "model_ll": mll, "market_ll": kll,
            "ratio": mll / kll if kll > 0 else 0.0,
            "degradation_pct": deg,
            "model_brier": brier(probs_m, outs),
            "market_brier": brier(probs_k, outs),
            "verdict": "GO" if mll <= kll * 1.01 else "NO-GO",
            "cal_home": calibration([r["p_home"] for r in rows],
                                    [r["outcome"] == "H" for r in rows])}


def by_group(rows: list[dict], key: str) -> dict:
    """按 league / season 等键分组，各组各出一份 evaluate 摘要（键序稳定）。"""
    groups: dict = {}
    for r in rows:
        groups.setdefault(r[key], []).append(r)
    return {k: evaluate(v) for k, v in sorted(groups.items())}
