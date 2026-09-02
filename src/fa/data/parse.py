import csv
import io
from dataclasses import dataclass, field

import pandas as pd

INT_COLS = {
    "FTHG": "fthg", "FTAG": "ftag",
    "HS": "shots_home", "AS": "shots_away",
    "HST": "shots_target_home", "AST": "shots_target_away",
    "HC": "corners_home", "AC": "corners_away",
}
FLOAT_COLS = {
    "PSH": "ps_home", "PSD": "ps_draw", "PSA": "ps_away",
    "PSCH": "psc_home", "PSCD": "psc_draw", "PSCA": "psc_away",
    "P>2.5": "over25_ps", "P<2.5": "under25_ps",
    "PC>2.5": "over25_psc", "PC<2.5": "under25_psc",
}


@dataclass
class MatchRow:
    league: str
    season: int
    date: str | None
    home: str
    away: str
    fthg: int | None = None
    ftag: int | None = None
    shots_home: int | None = None
    shots_away: int | None = None
    shots_target_home: int | None = None
    shots_target_away: int | None = None
    corners_home: int | None = None
    corners_away: int | None = None
    ps_home: float | None = None
    ps_draw: float | None = None
    ps_away: float | None = None
    psc_home: float | None = None
    psc_draw: float | None = None
    psc_away: float | None = None
    over25_ps: float | None = None
    under25_ps: float | None = None
    over25_psc: float | None = None
    under25_psc: float | None = None
    raw: dict = field(default_factory=dict)


def _clean(v) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s or None


def _to_int(v) -> int | None:
    s = _clean(v)
    if s is None:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _to_float(v) -> float | None:
    s = _clean(v)
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _raw_dict(rec: dict) -> dict:
    """numpy 标量转 Python 原生类型，NaN 转 None，保证可 json.dumps。"""
    out = {}
    for k, v in rec.items():
        if v is None or v is pd.NA:
            out[k] = None
        elif isinstance(v, float) and pd.isna(v):
            out[k] = None
        elif hasattr(v, "item"):
            out[k] = v.item()
        else:
            out[k] = v
    return out


def _trim_ragged_lines(content: str) -> str:
    """截掉数据行尾部比表头多出来的空字段。

    football-data.co.uk 的 2002-03~2004-05 部分赛季（E0/I1/SP1/D1/F1）数据行
    会在行尾多出若干逗号，字段数比表头还长且多出的全为空。pandas 默认
    on_bad_lines='error' 对这种行直接抛 ParserError，一个赛季文件就整份入库
    失败。实测 170 个缓存文件里 868 行属于此类，没有一例多出非空字段，因此
    只在「多出的部分全为空」时截断；带非空值的多余字段不静默吞掉，维持
    pandas 报错上抛（由 sync 记账）。

    引号数不成对的行可能是跨行的带引号字段，逐行切分会误判，原样保留。
    缓存文件均为 CRLF：被截断的行经 csv.reader 重建后行尾 \r 随之消失，实测
    对 170 个文件全部可正确解析（字段数恰等于表头数，值逐列对齐），无需补回。
    """
    lines = content.split("\n")
    if len(lines) < 2:
        return content
    header_fields = len(next(csv.reader([lines[0]]), []))
    if header_fields == 0:
        return content          # 首行不是合法表头，交回 pandas 处理
    out = list(lines)
    for i, line in enumerate(lines[1:], start=1):
        if not line.strip() or line.count('"') % 2:
            continue
        fields = next(csv.reader([line]), None)
        if fields is None or len(fields) <= header_fields:
            continue
        if any(f.strip() for f in fields[header_fields:]):
            continue
        out[i] = ",".join(fields[:header_fields])
    return "\n".join(out)


def parse_csv(content: str, league: str, season: int) -> list[MatchRow]:
    df = pd.read_csv(io.StringIO(_trim_ragged_lines(content)))
    df.columns = [str(c).strip() for c in df.columns]
    rows: list[MatchRow] = []
    for rec in df.to_dict("records"):
        home = _clean(rec.get("HomeTeam"))
        away = _clean(rec.get("AwayTeam"))
        fthg = _to_int(rec.get("FTHG"))
        ftag = _to_int(rec.get("FTAG"))
        if not home or not away or fthg is None or ftag is None:
            continue  # 空行或未赛 fixture
        date_s = _clean(rec.get("Date"))
        date = None
        if date_s:
            ts = pd.to_datetime(date_s, dayfirst=True, format="mixed",
                                errors="coerce")
            date = None if pd.isna(ts) else ts.strftime("%Y-%m-%d")
        row = MatchRow(league=league, season=season, date=date,
                       home=home, away=away, fthg=fthg, ftag=ftag,
                       raw=_raw_dict(rec))
        for col, attr in INT_COLS.items():
            setattr(row, attr, _to_int(rec.get(col)))
        for col, attr in FLOAT_COLS.items():
            setattr(row, attr, _to_float(rec.get(col)))
        rows.append(row)
    return rows
