import sqlite3

import numpy as np

from fa.data.walkforward import iter_matchweeks
from fa.model.fit import FitConfig, fit_league, training_rows
from fa.model.predict import (btts_prob, expected_goals, fit_rho,
                              over25_probs, outcome_probs, score_matrix)
from fa.value.devig import devig_ou, devig_proportional


def global_targets(conn: sqlite3.Connection, asof: str,
                   cfg: FitConfig) -> tuple[float, float]:
    """全联赛训练窗的加权全局目标（spec §4.1 partial pooling 的收缩中心）。"""
    rows = conn.execute(
        "SELECT date, fthg, ftag FROM matches "
        "WHERE date < ? AND date >= date(?, '-' || ? || ' day')"
        " AND fthg IS NOT NULL",
        (asof, asof, cfg.window_days)).fetchall()
    if not rows:
        return 0.0, 0.0
    from datetime import date as _d
    asof_d = _d.fromisoformat(asof)
    # 年龄 = asof − date（与 fit.py 的 _design_arrays 同向）：越旧权重越低。
    # （date − asof 会得到负指数，权重随年龄指数级增长——计划缺陷，已修。）
    w = np.array([0.5 ** ((asof_d - _d.fromisoformat(r["date"])).days
                          / cfg.half_life_days) for r in rows])
    gh = float(np.dot(w, [r["fthg"] for r in rows]) / w.sum())
    ga = float(np.dot(w, [r["ftag"] for r in rows]) / w.sum())
    return float(np.log((gh + ga) / 2)), float(np.log(max(gh, 1e-6) / max(ga, 1e-6)))


def _week_matches(conn, league, season, week):
    return conn.execute(
        "SELECT m.id, m.date, h.name AS home, a.name AS away,"
        " m.fthg, m.ftag, m.psc_home, m.psc_draw, m.psc_away,"
        " m.over25_psc, m.under25_psc"
        " FROM matches m JOIN teams h ON h.id=m.home_team_id"
        " JOIN teams a ON a.id=m.away_team_id"
        " WHERE m.id IN (%s)" % ",".join("?" * len(week.match_ids)),
        week.match_ids).fetchall()


def run_backtest(conn, leagues, seasons, cfg: FitConfig = FitConfig(),
                 rho: float | None = None, verbose: bool = False) -> int:
    written = 0
    for league in leagues:
        for season in seasons:
            conn.execute(
                "DELETE FROM backtest_predictions WHERE league=? AND season=?",
                (league, season))
            for week in iter_matchweeks(conn, league, season):
                rows = _week_matches(conn, league, season, week)
                rows = [r for r in rows
                        if r["psc_home"] and r["psc_draw"] and r["psc_away"]]
                if not rows:
                    continue
                train = training_rows(conn, league, week.start, cfg.window_days)
                if len(train) < 30:
                    continue                          # 数据不足（早期赛季）跳过
                mu_g, ha_g = global_targets(conn, week.start, cfg)
                try:
                    fit = fit_league(train, week.start, league,
                                     mu_g, ha_g, cfg)
                except ValueError:
                    continue
                rho_eff = rho if rho is not None else fit_rho(
                    train, week.start, cfg,
                    lambda h, a: expected_goals(fit, h, a))
                for r in rows:
                    lh, la = expected_goals(fit, r["home"], r["away"])
                    m = score_matrix(lh, la, rho_eff)
                    ph, pd, pa = outcome_probs(m)
                    po, _pu = over25_probs(m)
                    mk = devig_proportional(
                        [r["psc_home"], r["psc_draw"], r["psc_away"]])
                    mkt_o = (devig_ou(r["over25_psc"], r["under25_psc"])[0]
                             if r["over25_psc"] and r["under25_psc"] else None)
                    total = r["fthg"] + r["ftag"]
                    conn.execute(
                        "INSERT INTO backtest_predictions (league, season,"
                        " week_index, match_id, date, p_home, p_draw, p_away,"
                        " p_over25, p_under25, p_btts, mkt_home, mkt_draw,"
                        " mkt_away, mkt_over25, odds_home, odds_draw, odds_away,"
                        " outcome, total_goals)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (league, season, week.index, r["id"], r["date"],
                         ph, pd, pa, po, 1 - po, btts_prob(m),
                         mk[0], mk[1], mk[2], mkt_o,
                         r["psc_home"], r["psc_draw"], r["psc_away"],
                         "H" if r["fthg"] > r["ftag"]
                         else "D" if r["fthg"] == r["ftag"] else "A", total))
                    written += 1
            conn.commit()
            if verbose:
                print(f"{league} {season}: 累计 {written} 行")
    return written
