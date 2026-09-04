"""批跑编排（设计 §3 数据流）：选→导出→hermes→契约→落库→台账。

单场失败（timeout/parse_fail/error）只落该场 status，绝不中断批。
ensemble（attributors≥2）：每场落 N 条成员行（attributor 1..N）+ 1 条
聚合行（attributor=0），台账 n_ok=聚合可用场数、n_error=全员失败场数
（spec §3/§4）；N=1 保持现行按场计数。
`call=None` 是注入缝：缺省在**调用时**解析模块属性 run_headless（不得写成
默认参 `call=run_headless`——默认参在 import 时绑定，monkeypatch
`pipeline.run_headless` 将不生效，清单 #4）。
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fa.retro.aggregate import aggregate_members
from fa.retro.contract import TAG_SET_VERSION, validate_output
from fa.retro.export import build_pack, write_packs
from fa.retro.runner import HARNESS, build_prompt, run_headless


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts() -> str:
    return _now().strftime("%Y-%m-%dT%H:%M:%SZ")


def run_retro_batch(conn: sqlite3.Connection, cands: list[dict],
                    selector: str, params: dict, out_root: Path,
                    call=None, attributors: int = 1) -> dict:
    import time
    if call is None:
        call = run_headless
    if attributors < 1:
        raise ValueError(f"attributors 须 ≥1，收到 {attributors}")
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
        prompt = build_prompt(pack)
        members: list[tuple] = []          # (status, parsed, repaired)
        member_durations: list[float] = []
        for i in range(attributors):
            res = call(prompt)
            member_durations.append(res["duration_s"])
            if not res["ok"]:
                member = ("timeout" if res.get("timeout") else "error",
                          None, False)
            else:
                v = validate_output(res["output"])
                member = (("ok", v["parsed"], v["repaired"])
                          if v["status"] == "ok" else ("parse_fail", None, False))
            members.append(member)
            _insert_member(conn, batch_id, cand, selector, i + 1, member,
                           res["duration_s"], pack_dir, paths)
        if attributors == 1:
            counts[members[0][0]] += 1     # 现行按场计数，逐字节不变
        else:
            agg = aggregate_members(
                [{"status": s, "parsed": p} for s, p, _ in members])
            _insert_aggregate(conn, batch_id, cand, selector, agg,
                              sum(member_durations), pack_dir, paths)
            counts["ok" if agg["status"] == "ok" else "error"] += 1
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


def _insert_member(conn: sqlite3.Connection, batch_id: int, cand: dict,
                   selector: str, attributor: int, member: tuple,
                   duration_s: float, pack_dir: Path, paths: dict) -> None:
    status, parsed, repaired = member
    conn.execute(
        "INSERT INTO retro_attributions (batch_id, match_id, league, season,"
        " date, selector, attributor, miss_tags_json, primary_tag,"
        " tags_confidence, model_vs_market, evidence_json, digest, status,"
        " repaired, harness, model, duration_s, input_pack_path,"
        " tag_set_version, created_at, is_control)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (batch_id, cand["match_id"], cand["league"], cand["season"],
         cand["date"], selector, attributor,
         json.dumps(parsed["miss_tags"], ensure_ascii=False) if parsed else None,
         parsed["primary_tag"] if parsed else None,
         parsed["tags_confidence"] if parsed else None,
         parsed["model_vs_market"] if parsed else None,
         json.dumps(parsed["evidence"], ensure_ascii=False) if parsed else None,
         parsed["digest"] if parsed else None,
         status, 1 if repaired else 0, HARNESS, None, duration_s,
         str(pack_dir / paths[cand["match_id"]]), TAG_SET_VERSION, _ts(),
         1 if cand.get("is_control") else 0))


def _insert_aggregate(conn: sqlite3.Connection, batch_id: int, cand: dict,
                      selector: str, agg: dict, duration_s: float,
                      pack_dir: Path, paths: dict) -> None:
    # 聚合行 repaired 恒 0——聚合由 Python 构造，无「修复」语义；
    # duration_s = 本场全部成员调用耗时之和（调用方传入）。
    conn.execute(
        "INSERT INTO retro_attributions (batch_id, match_id, league, season,"
        " date, selector, attributor, miss_tags_json, primary_tag,"
        " tags_confidence, model_vs_market, evidence_json, digest, status,"
        " repaired, harness, model, duration_s, input_pack_path,"
        " tag_set_version, created_at, is_control)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (batch_id, cand["match_id"], cand["league"], cand["season"],
         cand["date"], selector, 0,
         json.dumps(agg["miss_tags"], ensure_ascii=False)
         if agg["miss_tags"] is not None else None,
         agg["primary_tag"], agg["tags_confidence"], agg["model_vs_market"],
         json.dumps(agg["evidence"], ensure_ascii=False)
         if agg["evidence"] is not None else None,
         agg["digest"], agg["status"], 0, HARNESS, None, duration_s,
         str(pack_dir / paths[cand["match_id"]]), TAG_SET_VERSION, _ts(),
         1 if cand.get("is_control") else 0))
