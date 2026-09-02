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


def parse_csv(content: str, league: str, season: int) -> list[MatchRow]:
    df = pd.read_csv(io.StringIO(content))
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
