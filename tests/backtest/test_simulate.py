import fa.backtest.simulate as sim
import pytest
from fa.backtest.simulate import (EDGE_MIN, EV_MIN, KELLY_FRAC, ODDS_MAX,
                                  ODDS_MIN, STAKE_CAP, candidates,
                                  simulate_flat, simulate_kelly)


def _bare(**kw):
    """最小行：未测市场全 0（edge=0 且赔率 0 出带）永不入选，用于隔离单一门槛。"""
    base = {"match_id": 9, "league": "E0", "date": "2023-08-12", "outcome": "H",
            "total_goals": 2, "mkt_over25": None,
            "p_home": 0.0, "p_draw": 0.0, "p_away": 0.0,
            "mkt_home": 0.0, "mkt_draw": 0.0, "mkt_away": 0.0,
            "odds_home": 0.0, "odds_draw": 0.0, "odds_away": 0.0}
    base.update(kw)
    return base


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


# ---- 修订 Round 1：钉死 §7 变异自验中幸存的门槛/仓位变异（spec 常量守卫）----

def test_spec_constants_verbatim():
    # spec §5.2/§5.3 逐字：EV≥3%、edge≥2%、赔率带 [1.4, 6.0]、¼ Kelly、单注上限 2%。
    assert (EV_MIN, EDGE_MIN) == (0.03, 0.02)
    assert (ODDS_MIN, ODDS_MAX) == (1.4, 6.0)
    assert (KELLY_FRAC, STAKE_CAP) == (0.25, 0.02)


def test_gates_inclusive_boundaries():
    # 赔率带端点：1.4 / 6.0 与常量同一字面量（位级相等），是可造出的真端点夹具；
    # 1.39 / 6.01 的 EV/edge 均过线、仅被赔率带剔除（阳性对照，防「全被 EV 挡住」假绿）。
    lo = candidates([_bare(p_home=0.75, mkt_home=0.70, odds_home=1.4)])
    assert [c["market"] for c in lo] == ["H"]
    hi = candidates([_bare(p_home=0.25, mkt_home=0.20, odds_home=6.0)])
    assert [c["market"] for c in hi] == ["H"]
    assert candidates([_bare(p_home=0.75, mkt_home=0.70, odds_home=1.39)]) == []
    assert candidates([_bare(p_home=0.25, mkt_home=0.20, odds_home=6.01)]) == []
    # EV / edge 的最近可达值夹住常量位置：入选侧 ev=0.030000000000000027、
    # edge=0.020000000000000018；排除侧 ev=0.0298、edge=0.0199。
    ev_in = candidates([_bare(p_home=0.515, mkt_home=0.48, odds_home=2.0)])
    assert [c["market"] for c in ev_in] == ["H"]
    assert candidates([_bare(p_home=0.5149, mkt_home=0.48, odds_home=2.0)]) == []
    eg_in = candidates([_bare(p_home=0.515, mkt_home=0.495, odds_home=2.0)])
    assert [c["market"] for c in eg_in] == ["H"]
    assert candidates([_bare(p_home=0.515, mkt_home=0.4951, odds_home=2.0)]) == []


def test_threshold_comparison_is_inclusive(monkeypatch):
    # spec §5.2 写「EV ≥ 3%、edge ≥ 2%」——含端点本身是判据，须钉死 >= 不被改成 >。
    # 造不出 ev 位级等于 0.03 的夹具：带内赔率下 ev = p*(o−1) − (1−p) 的两操作数
    # 比值恒 < 2，Sterbenz 使末次减法精确，ev 只能落在 ~2^-52 网格，而
    # float(0.03) = 0x1.eb851eb851eb8p-6 在 2^-57 网格 → 位级相等不可达
    # （edge 同理：p ≈ mkt + 0.02 时比值 < 2，减法精确，float(0.02) 不在可达网格）。
    # 故把阈值 monkeypatch 到夹具的精确计算值，使 >= 与 > 可区分。
    p, mkt, odds = 0.515, 0.48, 2.0
    ev = p * (odds - 1) - (1 - p)                     # 0.030000000000000027
    row = _bare(p_home=p, mkt_home=mkt, odds_home=odds)
    monkeypatch.setattr(sim, "EV_MIN", ev)
    assert [c["market"] for c in candidates([row])] == ["H"]   # 恰在阈值上仍入选
    monkeypatch.setattr(sim, "EV_MIN", EV_MIN)                 # 还原，单独钉 edge
    monkeypatch.setattr(sim, "EDGE_MIN", p - mkt)              # 0.03500000000000003
    assert [c["market"] for c in candidates([row])] == ["H"]


def test_kelly_uses_quarter_kelly_and_cap(monkeypatch):
    # 上限：f_raw = 0.25*(0.9*5−1)/4 = 0.21875 → 截到 STAKE_CAP=0.02
    #     final = 1000*(1 + 0.02*4) = 1080.0（精确）；无上限则 1875.0。
    cap = candidates([_bare(p_home=0.9, mkt_home=0.5, odds_home=5.0)])
    assert simulate_kelly(cap, bankroll=1000.0)["final_bankroll"] == 1080.0
    monkeypatch.setattr(sim, "STAKE_CAP", 0.5)
    assert simulate_kelly(cap, bankroll=1000.0)["final_bankroll"] == 1875.0
    monkeypatch.setattr(sim, "STAKE_CAP", STAKE_CAP)   # 还原，¼ 系数段需上限在位
    # ¼ 系数：f = 0.25*(0.53*2−1)/1 = 0.015（未触上限）→ final = 1000*1.015 = 1015.0；
    # KELLY_FRAC=1.0 时 f=0.06 被上限截到 0.02 → 1020.0（证明 ¼ 系数承重）。
    q = candidates([_bare(p_home=0.53, mkt_home=0.50, odds_home=2.0)])
    assert simulate_kelly(q, bankroll=1000.0)["final_bankroll"] == 1015.0
    monkeypatch.setattr(sim, "KELLY_FRAC", 1.0)
    assert simulate_kelly(q, bankroll=1000.0)["final_bankroll"] == 1020.0


def test_kelly_processes_in_date_order():
    # 固定比例复利可交换 → final_bankroll 对注序不变；顺序的判别量是回撤路径。
    # 日期序 [亏1.5%(08-12), 赢+10%(08-19), 亏1.5%(08-26)]：dd = 1.5%；
    # 若按输入序（赢、亏、亏）处理：dd = 2.9775% → sorted() 被删时下断言变红。
    w = _row(p_home=0.9, mkt_home=0.50, odds_home=6.0, date="2023-08-19")
    l1 = _row(p_home=0.53, mkt_home=0.50, odds_home=2.0, date="2023-08-12",
              outcome="A")
    l2 = _row(p_home=0.53, mkt_home=0.50, odds_home=2.0, date="2023-08-26",
              outcome="A", match_id=5)
    cands = candidates([w, l1, l2])                   # 输入故意非日期序
    # candidates 保持输入序（排序发生在 simulate_kelly 内部）——先钉住这一点，
    # 使「输入乱序」对 simulate_kelly 成立。
    assert [(c["date"], c["odds"], c["outcome_hit"]) for c in cands] == [
        ("2023-08-19", 6.0, True), ("2023-08-12", 2.0, False),
        ("2023-08-26", 2.0, False)]
    kel = simulate_kelly(cands, bankroll=1000.0)
    assert kel["final_bankroll"] == pytest.approx(1067.2475, abs=1e-9)
    assert kel["max_drawdown_pct"] == pytest.approx(0.015, abs=1e-12)
