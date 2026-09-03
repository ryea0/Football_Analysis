"""近 6 场状态协变量：各队最近 ≤6 场的场均净胜球（spec §4.5 消融实验）。

严格防泄漏：某行的特征只由**该行日期之前**的比赛构成——行自身与其后的任何行
都不进入该行的特征。队史不足 6 场取现有均值，无历史取 0.0（= 模型中性项）。
"""

from collections import deque

_N_GAMES = 6


def _walk(rows: list[dict]) -> tuple[list[tuple[float, float]], dict[str, float]]:
    """按 (date, home, away) 升序走一遍，返回 (按输入顺序的特征, 走完后的队状态)。"""
    feats: list[tuple[float, float]] = [(0.0, 0.0)] * len(rows)
    hist: dict[str, deque] = {}
    order = sorted(range(len(rows)),
                   key=lambda i: (rows[i]["date"], rows[i]["home"], rows[i]["away"], i))
    for i in order:
        r = rows[i]
        dh = hist.setdefault(r["home"], deque(maxlen=_N_GAMES))
        da = hist.setdefault(r["away"], deque(maxlen=_N_GAMES))
        # 各队的场均净胜球均为该队自身视角（主队 gd = fthg − ftag，客队取反）
        fh = sum(dh) / len(dh) if dh else 0.0
        fa = sum(da) / len(da) if da else 0.0
        feats[i] = (fh, fa)
        gd = r["fthg"] - r["ftag"]
        dh.append(gd)
        da.append(-gd)
    current = {t: (sum(d) / len(d) if d else 0.0) for t, d in hist.items()}
    return feats, current


def form_features(rows: list[dict]) -> list[tuple[float, float]]:
    """每行的 (f_home, f_away)：该队在该行日期之前最近 ≤6 场的场均净胜球。

    rows: [{"date", "home", "away", "fthg", "ftag"}, ...]（调用方保证同联赛）。
    输入可为任意顺序——内部按 (date, home, away) 排序副本处理，返回与输入同序。
    """
    return _walk(rows)[0]


def current_form(rows: list[dict]) -> dict[str, float]:
    """全部行处理完后的各队近 6 场场均净胜球（预测未来场次时用）。"""
    return _walk(rows)[1]
