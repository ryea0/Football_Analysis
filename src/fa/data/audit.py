import random
import sqlite3
from dataclasses import dataclass

from fa.data import download
from fa.data.parse import parse_csv
from fa.data.sync import _read_text

COMPARE_FIELDS = ["fthg", "ftag", "ps_home", "ps_draw", "ps_away",
                  "psc_home", "psc_draw", "psc_away"]


@dataclass
class AuditMismatch:
    match_id: int
    field: str
    db_value: object
    csv_value: object


def audit_sample(conn: sqlite3.Connection, sample: int = 50,
                 seed: int = 42) -> list[AuditMismatch]:
    ids = [r["id"] for r in conn.execute("SELECT id FROM matches ORDER BY id")]
    if not ids:
        return []
    picked = random.Random(seed).sample(ids, min(sample, len(ids)))
    csv_cache: dict[tuple[str, int], dict[tuple[str, str, str], object]] = {}
    out: list[AuditMismatch] = []
    for mid in picked:
        m = conn.execute(
            "SELECT m.*, h.name home, a.name away FROM matches m "
            "JOIN teams h ON h.id=m.home_team_id "
            "JOIN teams a ON a.id=m.away_team_id WHERE m.id=?", (mid,)).fetchone()
        key = (m["league"], m["season"])
        if key not in csv_cache:
            # 路径统一由 download.csv_cache_path 生成（属性式访问，测试可对其打桩）
            path = download.csv_cache_path(m["league"], m["season"])
            if not path.exists():
                out.append(AuditMismatch(mid, "cache_missing", None, str(path)))
                continue
            # 读侧统一走 sync._read_text（BOM 嗅探）：个别缓存文件带 UTF-8 BOM，
            # 一律 latin-1 会把 BOM 粘在首个表头上污染该列
            rows = parse_csv(_read_text(path), m["league"], m["season"])
            csv_cache[key] = {(r.date, r.home, r.away): r for r in rows}
        row = csv_cache[key].get((m["date"], m["home"], m["away"]))
        if row is None:
            out.append(AuditMismatch(mid, "row_missing", None, None))
            continue
        for f in COMPARE_FIELDS:
            if m[f] != getattr(row, f):
                out.append(AuditMismatch(mid, f, m[f], getattr(row, f)))
    return out
