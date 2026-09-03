"""§4.5 近 6 场状态协变量消融的 β 探针（只读，不写任何表）。

对 5 个抽样 league-week 按驱动器同一路径重拟合并读 `beta_form`，同时给出
训练窗内各队 form（近 6 场场均净胜球）的分位数——判决书
`docs/m2-verdict.md` 的「补充：§4.5 近 6 场状态协变量消融」节的 β 表与
form 分位列即出自本脚本，二者须一致（脚本为该表的唯一事实来源）。

用法：`uv run python scripts/form_beta_probe.py`（FA_DB 默认 data/fa.db）。
"""
from fa.backtest.run import global_targets
from fa.config import LEAGUES
from fa.data.walkforward import iter_matchweeks
from fa.db import connect
from fa.model.fit import FitConfig, fit_league, training_rows
from fa.model.form import current_form, form_features

# (league, season, 第几个 matchweek，0 起)；week.index 为 1 起的周序号
PICKS = [("E0", 2019, 10), ("SP1", 2021, 20), ("D1", 2023, 15),
         ("I1", 2024, 25), ("F1", 2025, 12)]


def main() -> None:
    cfg = FitConfig()
    conn = connect()
    betas = []
    for lg, season, want in PICKS:
        weeks = list(iter_matchweeks(conn, lg, season))
        wk = weeks[min(want, len(weeks) - 1)]
        train = training_rows(conn, lg, wk.start, cfg.window_days)
        mu_g, ha_g = global_targets(conn, wk.start, cfg)
        fit = fit_league(train, wk.start, lg, mu_g, ha_g, cfg,
                         form_pairs=form_features(train))
        cf = current_form(train)
        vals = sorted(cf.values())
        # 分位配方：按序数取 p10/p50/p90（n//10, n//2, 9n//10），非插值
        p10, p50, p90 = (vals[len(vals) // 10], vals[len(vals) // 2],
                         vals[9 * len(vals) // 10])
        betas.append(fit.beta_form)
        print(f"{lg} {season} wk{wk.index} (asof {wk.start}, "
              f"n_train={len(train)}): beta={fit.beta_form:+.5f}  "
              f"form p10/p50/p90 = {p10:+.3f}/{p50:+.3f}/{p90:+.3f}")
    conn.close()
    bs = sorted(betas)
    print(f"β 抽样 5 周: min={bs[0]:+.5f} median={bs[2]:+.5f} max={bs[-1]:+.5f}")
    print(f"LEAGUES 确认: {list(LEAGUES)}")


if __name__ == "__main__":
    main()
