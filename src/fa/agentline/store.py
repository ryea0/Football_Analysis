"""线 A 落库（设计 §6）：UNIQUE(match_id, line) 幂等 upsert。"""
import json
import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_prediction(conn: sqlite3.Connection, match_id: int, line: str,
                    parsed: dict, raw_output: str, harness: str,
                    model: str, duration_s: float,
                    attributor: int = 1) -> int:
    # attributor：单跑线（A_base/A_enh）恒 1（缺省即一期语义）；A_multi 成员
    # 1..3、聚合 0 分道落库（v7 UNIQUE 三元组的前提，A_multi 计划 Task 1 接口）
    conn.execute(
        "INSERT INTO agentline_predictions (match_id, line, attributor,"
        " p_home, p_draw, p_away, p_over25, confidence, reasoning_digest,"
        " sources_json, raw_output, status, repaired, harness, model,"
        " duration_s, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(match_id, line, attributor) DO UPDATE SET"
        " p_home=excluded.p_home, p_draw=excluded.p_draw,"
        " p_away=excluded.p_away, p_over25=excluded.p_over25,"
        " confidence=excluded.confidence,"
        " reasoning_digest=excluded.reasoning_digest,"
        " sources_json=excluded.sources_json, raw_output=excluded.raw_output,"
        " status=excluded.status, repaired=excluded.repaired,"
        " harness=excluded.harness, model=excluded.model,"
        " duration_s=excluded.duration_s, created_at=excluded.created_at",
        (match_id, line, attributor, parsed["p_home"], parsed["p_draw"],
         parsed["p_away"], parsed["p_over25"], parsed["confidence"],
         parsed["reasoning_digest"], parsed["sources_json"], raw_output,
         parsed["status"], int(parsed["repaired"]), harness, model,
         duration_s, _now()))
    conn.commit()
    row = conn.execute(
        "SELECT id FROM agentline_predictions"
        " WHERE match_id=? AND line=? AND attributor=?",
        (match_id, line, attributor)).fetchone()
    return row["id"]


def save_run(conn, line: str, profile: str, model: str | None,
             counts: dict, summary: dict, started_at: str | None = None) -> int:
    # started_at 由调用方传批次起点（run_line 开头取的 _now()）；缺省回落当下
    # （兼容旧调用）。不传时起点=落库时刻，长批会把批尾记成起点（2026-09-04
    # 实测 04:25 启动的批次险些记成 ~05:07）。
    cur = conn.execute(
        "INSERT INTO agentline_runs (line, profile, model, n_ok,"
        " n_parse_fail, n_timeout, n_error, started_at, finished_at, summary)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (line, profile, model, counts.get("ok", 0), counts.get("parse_fail", 0),
         counts.get("timeout", 0), counts.get("error", 0),
         started_at or _now(), _now(),
         json.dumps(summary, ensure_ascii=False)))
    conn.commit()
    return cur.lastrowid
