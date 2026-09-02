"""模拟盘：spec §5.2/§5.3 门槛与仓位。回测以收盘价成交（保守）。"""
EV_MIN = 0.03
EDGE_MIN = 0.02
ODDS_MIN = 1.4
ODDS_MAX = 6.0
KELLY_FRAC = 0.25
STAKE_CAP = 0.02


def _hit(row, market: str) -> bool:
    if market == "H":
        return row["outcome"] == "H"
    if market == "D":
        return row["outcome"] == "D"
    if market == "A":
        return row["outcome"] == "A"
    if market == "O2.5":
        return row["total_goals"] >= 3
    raise ValueError(market)


def candidates(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        mkts = [("H", r["p_home"], r["mkt_home"], r["odds_home"]),
                ("D", r["p_draw"], r["mkt_draw"], r["odds_draw"]),
                ("A", r["p_away"], r["mkt_away"], r["odds_away"])]
        if r.get("mkt_over25"):
            mkts.append(("O2.5", r["p_over25"], r["mkt_over25"],
                         None))                        # O2.5 赔率不在表内，跳过 EV 判定见下
        for market, p, mkt_p, odds in mkts:
            if p is None or mkt_p is None:
                continue
            edge = p - mkt_p
            if market == "O2.5":
                continue                              # 缺收盘赔率，无法算 EV，M3 接实时盘后启用
            if odds is None or not (ODDS_MIN <= odds <= ODDS_MAX):
                continue
            ev = p * (odds - 1) - (1 - p)
            if ev >= EV_MIN and edge >= EDGE_MIN:
                out.append({"match_id": r["match_id"], "league": r["league"],
                            "date": r["date"], "market": market, "p": p,
                            "mkt_p": mkt_p, "odds": odds, "ev": ev,
                            "edge": edge, "outcome_hit": _hit(r, market)})
    return out


def simulate_flat(cands: list[dict]) -> dict:
    staked = len(cands)
    returned = sum(c["odds"] for c in cands if c["outcome_hit"])
    return {"n": staked, "staked": staked, "returned": returned,
            "pnl": returned - staked,
            "roi": (returned - staked) / staked if staked else 0.0}


def simulate_kelly(cands: list[dict], bankroll: float = 1000.0) -> dict:
    start = bankroll                                   # 初始资金即参数默认 1000.0
    peak, max_dd = bankroll, 0.0
    for c in sorted(cands, key=lambda x: x["date"]):
        f = KELLY_FRAC * (c["p"] * c["odds"] - 1) / (c["odds"] - 1)
        f = min(max(f, 0.0), STAKE_CAP)
        stake = f * bankroll
        bankroll += stake * (c["odds"] - 1) if c["outcome_hit"] else -stake
        peak = max(peak, bankroll)
        max_dd = max(max_dd, (peak - bankroll) / peak)
    return {"n": len(cands), "final_bankroll": bankroll,
            "roi": (bankroll - start) / start,
            "max_drawdown_pct": max_dd}
