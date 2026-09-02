from fa.backtest.simulate import (candidates, simulate_flat,
                                  simulate_kelly)


def _row(**kw):
    base = {"match_id": 1, "league": "E0", "date": "2023-08-12",
            "p_home": 0.55, "p_draw": 0.25, "p_away": 0.20,
            "mkt_home": 0.50, "mkt_draw": 0.28, "mkt_away": 0.22,
            "odds_home": 2.00, "odds_draw": 3.57, "odds_away": 4.55,
            "mkt_over25": None, "p_over25": 0.6, "total_goals": 2,
            "outcome": "H"}
    base.update(kw)
    return base


def test_candidates_gates():
    # home：edge=0.05, ev=0.55*1−0.45=0.10 → 入选
    c = candidates([_row()])
    mkts = {c_["market"] for c_ in c}
    assert "H" in mkts
    # odds 6.5 超带 → 无候选
    assert candidates([_row(odds_home=6.5)]) == []
    # edge 不足 → 无候选
    assert candidates([_row(p_home=0.51, mkt_home=0.50)]) == []
    # EV 不足（高赔低概率）→ A 被 EV 关卡剔除。brief 原稿 p=0.25/mkt=0.22/odds=4.55
    # 实算 EV=0.1375、edge=0.03，两项门槛均通过（本应入选）；且 base 行 home 恒入选
    # （见上），`== []` 对任何该行变体都不可满足——计划缺陷第 6 例，测试侧修正：
    # 改用 p=0.10/mkt=0.05（edge=0.05 过 edge 关，EV=0.10*3.55−0.90=−0.545 被剔），
    # 并断言仅 home 入选以证明 A 是被 EV 关卡单独剔除。
    c4 = candidates([_row(p_away=0.10, mkt_away=0.05, odds_away=4.55)])
    assert {c_["market"] for c_ in c4} == {"H"}


def test_flat_and_kelly():
    cands = candidates([_row(), _row(match_id=2, date="2023-08-19")])
    flat = simulate_flat(cands)
    assert flat["n"] >= 1 and flat["staked"] == flat["n"]
    # outcome=H 命中 → 收益为正
    assert flat["returned"] == flat["n"] * 2.0
    kel = simulate_kelly(cands, bankroll=1000.0)
    assert kel["final_bankroll"] > 1000.0             # 全命中
    assert kel["n"] == flat["n"]
