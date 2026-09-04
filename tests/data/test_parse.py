import pandas as pd
import pytest

from fa.data.parse import _trim_ragged_lines, parse_csv

# 90 年代格式：无射门/角球/赔率列，日期 d/m/yyyy
CSV_90S = """Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR
E0,19/08/1995,Arsenal,West Ham,1,1,D
E0,22/08/1995,Liverpool,Everton,2,0,H
"""

# 现代格式：射门/角球 + Pinnacle 快照/收盘 + 大小球 2.5
CSV_MODERN = """Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HS,AS,HST,AST,HC,AC,PSH,PSD,PSA,PSCH,PSCD,PSCA,P>2.5,P<2.5,PC>2.5,PC<2.5
D1,13/09/2024,Bayern Munich,Hoffenheim,4,0,H,18,7,9,2,8,2,1.25,6.5,15.0,1.22,7.0,17.0,1.20,4.80,1.18,5.10
"""

CSV_WITH_JUNK = """Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR
E0,19/08/1995,Arsenal,West Ham,1,1,D
E0,xx/xx/xxxx,Chelsea,Spurs,2,2,D
E0,,Fulham,Leeds,,
"""


def test_parse_90s_basic():
    rows = parse_csv(CSV_90S, "E0", 1995)
    assert len(rows) == 2
    r = rows[0]
    assert (r.home, r.away, r.fthg, r.ftag) == ("Arsenal", "West Ham", 1, 1)
    assert r.date == "1995-08-19"
    assert r.ps_home is None and r.corners_home is None   # 老赛季列缺失 -> None


def test_parse_modern_all_fields():
    rows = parse_csv(CSV_MODERN, "D1", 2024)
    r = rows[0]
    assert r.shots_home == 18 and r.shots_away == 7
    assert r.corners_home == 8 and r.corners_away == 2
    assert r.ps_home == 1.25 and r.psc_away == 17.0
    assert r.over25_ps == 1.20 and r.under25_psc == 5.10
    assert r.raw["FTR"] == "H"                            # 原始行留档


def test_junk_rows_dropped_or_kept_correctly():
    rows = parse_csv(CSV_WITH_JUNK, "E0", 1995)
    assert len(rows) == 2          # 未赛行（无比分）丢弃；坏日期行保留但 date=None
    assert rows[1].date is None


# 真实数据（E0/I1/SP1/D1/F1 的 2002-03~2004-05 部分）数据行行尾会多出若干
# 逗号，字段数比表头还长且多出的全为空，pandas 默认 on_bad_lines='error'
# 直接抛 ParserError，导致整个赛季文件入库失败。
CSV_RAGGED = """Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HS,AS,HST,AST,HC,AC
E0,14/08/2004,Chelsea,Man United,1,0,H,15,8,9,3,7,4
E0,15/08/2004,Arsenal,Leeds,2,1,H,12,9,6,4,5,3,,,,,,,,,,,,,,
"""


def test_parse_rows_with_trailing_extra_empty_fields():
    rows = parse_csv(CSV_RAGGED, "E0", 2004)
    assert len(rows) == 2
    r = rows[1]
    assert (r.home, r.away, r.date) == ("Arsenal", "Leeds", "2004-08-15")
    assert (r.fthg, r.ftag) == (2, 1)
    assert r.shots_home == 12 and r.corners_away == 3


def test_trim_leaves_well_formed_content_alone():
    # 列数与表头一致的文件必须逐字节原样返回（裁剪只动违规行）
    assert _trim_ragged_lines(CSV_90S) == CSV_90S
    assert _trim_ragged_lines(CSV_MODERN) == CSV_MODERN


def test_trim_keeps_strictness_for_nonempty_extra_fields():
    # 多出的字段带非空值：不静默吞数据，维持 pandas 报错上抛（由 sync 记账）
    bad = CSV_RAGGED.replace("5,3,,,,,,,,,,,,,,", "5,3,99,,,,,,,,,,,,,")
    with pytest.raises(pd.errors.ParserError):
        parse_csv(bad, "E0", 2004)


# 2026-27 起 football-data.co.uk 移除 Pinnacle 列族，Betfair 交易所收盘成为
# B 线 CLV 的 fallback 基准（spec §7.3，2026-09-04 裁定）——BFE 列解析钉测
CSV_BFE = """Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,BFECH,BFECD,BFECA,BFEC>2.5
SP1,03/09/2026,Osasuna,Getafe,1,0,H,2.1,3.4,3.2,2.05
"""


def test_parse_betfair_exchange_closing_columns():
    rows = parse_csv(CSV_BFE, "SP1", 2026)
    assert len(rows) == 1
    r = rows[0]
    assert (r.bfe_home, r.bfe_draw, r.bfe_away) == (2.1, 3.4, 3.2)
    assert r.over25_bfe == 2.05


def test_parse_bfe_missing_columns_yield_none():
    rows = parse_csv(CSV_90S, "E0", 1995)
    assert all(r.bfe_home is None and r.over25_bfe is None for r in rows)
