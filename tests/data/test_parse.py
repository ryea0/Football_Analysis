from fa.data.parse import parse_csv

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
