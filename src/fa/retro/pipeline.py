"""批跑编排（设计 §3 数据流）：选→导出→hermes→契约→落库→台账。

单场失败（timeout/parse_fail/error）只落该场 status，绝不中断批。
`call=None` 是注入缝：缺省在**调用时**解析模块属性 run_headless（不得写成
默认参 `call=run_headless`——默认参在 import 时绑定，monkeypatch
`pipeline.run_headless` 将不生效，清单 #4）。
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fa.retro.contract import TAG_SET_VERSION, validate_output
from fa.retro.export import build_pack, write_packs
from fa.retro.runner import HARNESS, build_prompt, run_headless


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts() -> str:
    return _now().strftime("%Y-%m-%dT%H:%M:%SZ")


def run_retro_batch(conn: sqlite3.Connection, cands: list[dict],
                    selector: str, params: dict, out_root: Path,
                    call=None) -> dict:
    import time
    if call is None:
        call = run_headless
    t0 = time.monotonic()
    pack_dir = Path(out_root) / f"batch-{_ts().replace(':', '').replace('-', '')}"
    paths = write_packs(conn, cands, pack_dir)
    counts = {"ok": 0, "parse_fail": 0, "timeout": 0, "error": 0}
    ledger = conn.execute(
        "INSERT INTO retro_runs (selector, params_json, n_selected, n_ok,"
        " n_parse_fail, n_timeout, n_error, duration_s, created_at)"
        " VALUES (?, ?, ?, 0, 0, 0, 0, 0.0, ?)",
        (selector, json.dumps(params, ensure_ascii=False), len(cands),
         _ts()))
    batch_id = ledger.lastrowid
    for cand in cands:
        pack = build_pack(conn, cand)
        res = call(build_prompt(pack))
        if not res["ok"]:
            status = ("timeout" if "超时" in (res["error"] or "")
                      else "error")
            counts[status] += 1
            conn.execute(
                "INSERT INTO retro_attributions (batch_id, match_id, league,"
                " season, date, selector, status, repaired, harness, model,"
                " duration_s, input_pack_path, tag_set_version, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (batch_id, cand["match_id"], cand["league"], cand["season"],
                 cand["date"], selector, status, 0, HARNESS, None,
                 res["duration_s"], str(pack_dir / paths[cand["match_id"]]),
                 TAG_SET_VERSION, _ts()))
            continue
        v = validate_output(res["output"])
        if v["status"] != "ok":
            counts["parse_fail"] += 1
            conn.execute(
                "INSERT INTO retro_attributions (batch_id, match_id, league,"
                " season, date, selector, status, repaired, harness, model,"
                " duration_s, input_pack_path, tag_set_version, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (batch_id, cand["match_id"], cand["league"], cand["season"],
                 cand["date"], selector, "parse_fail", 0, HARNESS, None,
                 res["duration_s"], str(pack_dir / paths[cand["match_id"]]),
                 TAG_SET_VERSION, _ts()))
            continue
        counts["ok"] += 1
        p = v["parsed"]
        conn.execute(
            "INSERT INTO retro_attributions (batch_id, match_id, league,"
            " season, date, selector, miss_tags_json, primary_tag,"
            " tags_confidence, model_vs_market, evidence_json, digest,"
            " status, repaired, harness, model, duration_s, input_pack_path,"
            " tag_set_version, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (batch_id, cand["match_id"], cand["league"], cand["season"],
             cand["date"], selector,
             json.dumps(p["miss_tags"], ensure_ascii=False),
             p["primary_tag"], p["tags_confidence"], p["model_vs_market"],
             json.dumps(p["evidence"], ensure_ascii=False), p["digest"],
             "ok", 1 if v["repaired"] else 0, HARNESS, None,
             res["duration_s"], str(pack_dir / paths[cand["match_id"]]),
             TAG_SET_VERSION, _ts()))
    duration = time.monotonic() - t0
    conn.execute(
        "UPDATE retro_runs SET n_ok=?, n_parse_fail=?, n_timeout=?, n_error=?,"
        " duration_s=? WHERE id=?",
        (counts["ok"], counts["parse_fail"], counts["timeout"],
         counts["error"], duration, batch_id))
    conn.commit()
    # 摘要键与台账列同名（n_ok/n_parse_fail/n_timeout/n_error）——Task 7 CLI
    # 依赖此键名，不得改名；计划稿此处误写 **counts（键无 n_ 前缀，测试不过）。
    return {"batch_id": batch_id, "n_selected": len(cands),
            "n_ok": counts["ok"], "n_parse_fail": counts["parse_fail"],
            "n_timeout": counts["timeout"], "n_error": counts["error"],
            "duration_s": duration}
