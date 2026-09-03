"""近 6 场状态协变量（spec §4.5 消融）：特征数学、防泄漏、梯度与 λ 方向。"""
import copy

import numpy as np
import pytest

from fa.model.form import current_form, form_features

# 10 行手造序列：A 是主视角参照队（前 8 行场场有 A），B/C/E/F 用于视角取反与 0 历史分支
_ROWS = [
    {"date": "2023-01-01", "home": "A", "away": "B", "fthg": 2, "ftag": 0},
    {"date": "2023-01-02", "home": "B", "away": "C", "fthg": 1, "ftag": 1},
    {"date": "2023-01-03", "home": "C", "away": "A", "fthg": 0, "ftag": 3},
    {"date": "2023-01-04", "home": "D", "away": "A", "fthg": 1, "ftag": 2},
    {"date": "2023-01-05", "home": "A", "away": "E", "fthg": 4, "ftag": 0},
    {"date": "2023-01-06", "home": "E", "away": "A", "fthg": 0, "ftag": 2},
    {"date": "2023-01-07", "home": "A", "away": "F", "fthg": 3, "ftag": 1},
    {"date": "2023-01-08", "home": "F", "away": "A", "fthg": 2, "ftag": 5},
    {"date": "2023-01-09", "home": "A", "away": "B", "fthg": 5, "ftag": 0},
    {"date": "2023-01-10", "home": "B", "away": "A", "fthg": 0, "ftag": 1},
]


def test_form_features_math():
    """人工算出的检查点：0 历史 → 0.0、<6 场均值、6 场窗口滚动、主客视角取反。

    A 的队史净胜球序列（A 视角，行 1,3,4,5,6,7,8,9,10）：+2,+3,+1,+4,+2,+2,+3,+5,+1
    """
    feats = form_features(copy.deepcopy(_ROWS))
    assert len(feats) == len(_ROWS)

    # 第 1 行：A、B 均无历史 → 0.0
    assert feats[0] == (0.0, 0.0)
    # 第 2 行（B 主场）：B 队史 1 场 = −2（第 1 行客队 0:2）；C 无历史 → 0.0
    assert feats[1] == (-2.0, 0.0)
    # 第 3 行（C 主场 vs A）：C 队史 0（1:1 平）；A 队史 +2
    assert feats[2] == (0.0, 2.0)
    # 第 4 行（D 主场 vs A）：D 无历史；A 队史 (+2, +3) → 均值 2.5
    assert feats[3] == (0.0, pytest.approx(2.5))
    # 第 6 行（E 主场 vs A）：E 队史 −4（第 5 行客场 0:4）；A 队史 (2,3,1,4) → 2.5
    assert feats[5] == (-4.0, pytest.approx(2.5))
    # 第 8 行（F 主场 vs A）：F 队史 −2（第 7 行客场 1:3）；A 恰 6 场 (2,3,1,4,2,2) → 7/3
    assert feats[7] == (-2.0, pytest.approx(7.0 / 3.0))
    # 第 9 行（A 主场 vs B）：A 已有 7 场，截最近 6 场 (3,1,4,2,2,3) → 2.5；B 队史 (−2, 0) → −1.0
    assert feats[8] == (pytest.approx(2.5), -1.0)
    # 第 10 行（B 主场 vs A）：B 队史 3 场 (−2, 0, −5) → −7/3；
    # A 已有 8 场，窗口截到最近 6 场 (1,4,2,2,3,5) → 17/6（最老的 +2,+3 被挤出）
    assert feats[9] == (pytest.approx(-7.0 / 3.0), pytest.approx(17.0 / 6.0))


def test_current_form_is_end_of_sequence():
    """current_form = 全部行走完后的各队近 6 场均值（预测用），非最后一场的特征。"""
    cf = current_form(copy.deepcopy(_ROWS))
    # A 队史 9 场 → 最近 6 场 (4, 2, 2, 3, 5, 1) → 17/6
    assert cf["A"] == pytest.approx(17.0 / 6.0)
    # B 队史 4 场 (−2, 0, −5, −1) → −2.0
    assert cf["B"] == pytest.approx(-2.0)
    # 只出现过一次的队：单场均值（D 第 4 行主场 1:2 → −1）
    assert cf["D"] == pytest.approx(-1.0)
    assert cf["C"] == pytest.approx(-1.5)
    assert cf["E"] == pytest.approx(-3.0)
    assert cf["F"] == pytest.approx(-2.5)
    assert set(cf) == {"A", "B", "C", "D", "E", "F"}
    # 未知队不在表内 → 调用方以 cf.get(team, 0.0) 兜底（与 expected_goals 同约定）


def test_form_features_leak_free():
    """两重防泄漏：输入顺序无关；行自身与其后的任何行不影响该行特征。"""
    ref = {(r["date"], r["home"], r["away"]): f
           for r, f in zip(_ROWS, form_features(copy.deepcopy(_ROWS)))}

    shuffled = list(reversed(copy.deepcopy(_ROWS)))      # 完全倒序输入
    got = {(r["date"], r["home"], r["away"]): f
           for r, f in zip(shuffled, form_features(shuffled))}
    assert got == ref                                    # 同一场次的特征逐位一致

    # 把第 5 行及之后的比分全部改掉：前 4 行的特征必须不变（第 4 行的 A 队史
    # 只含第 1/3 行，若实现把行自身或其后行算进去，这里必然漂移）
    mutated = copy.deepcopy(_ROWS)
    for i in range(4, len(mutated)):
        mutated[i]["fthg"], mutated[i]["ftag"] = 9, 9
    got2 = {(r["date"], r["home"], r["away"]): f
            for r, f in zip(mutated, form_features(mutated))}
    for k in [(_ROWS[i]["date"], _ROWS[i]["home"], _ROWS[i]["away"])
              for i in range(4)]:
        assert got2[k] == ref[k], k
    assert got2 != ref                                   # 阳性对照：后面几行确实变了


def test_gradient_with_form_matches_numeric():
    """带 form 通道的解析梯度 vs 数值梯度（β 项符号/位置错误的直接守卫）。"""
    from fa.model.fit import FitConfig, _objective

    rows = [{"home": "A", "away": "B", "fthg": 2, "ftag": 1, "date": "2023-09-01"},
            {"home": "B", "away": "C", "fthg": 0, "ftag": 0, "date": "2023-09-08"},
            {"home": "C", "away": "A", "fthg": 1, "ftag": 3, "date": "2023-09-15"}]
    from fa.model.fit import _design_arrays
    h, a, yh, ya, w, n = _design_arrays(rows, "2023-09-20", FitConfig())
    fh = np.array([0.4, -1.2, 0.7])
    fa = np.array([-0.3, 0.5, 1.1])
    x = np.array([0.1, -0.1, 0.0, 0.05, -0.05, 0.0, 0.2, 0.3, -0.4])
    f0, g = _objective(x, h, a, yh, ya, w, 0.15, 0.25, FitConfig(), fh=fh, fa=fa)
    assert len(g) == len(x) == 2 * n + 3
    eps = 1e-6
    for i in range(len(x)):
        xp = x.copy(); xp[i] += eps
        xm = x.copy(); xm[i] -= eps
        fp, _ = _objective(xp, h, a, yh, ya, w, 0.15, 0.25, FitConfig(), fh=fh, fa=fa)
        fm, _ = _objective(xm, h, a, yh, ya, w, 0.15, 0.25, FitConfig(), fh=fh, fa=fa)
        assert abs((fp - fm) / (2 * eps) - g[i]) < 1e-4, f"param {i}"
