"""persona 判决应用与阶段编排（spec §6.4 / §6.5 / §6.2；M6 §12.7 双轨）。

降级只回退该场（§6.5 场次级）：该场三列保持中性、点评两列不动、记入返回值
degraded——调用方（matchday）把它写进 runs.summary.persona；当日不重试
（attempted 语义：am 的名单落 runs.summary 供 pm 读）。

判决传播（§6.2「17:00 更新版不重跑 persona，沿用 11:00 判决」的落库语义）：
am 广播判决时 pm 的行尚不存在，pm value 落的新行 ``verdict IS NULL``——
run_persona_phase 对 attempted 跳过的 fixture 把库内已有判决（首个非 NULL
判决行）复制到本 fixture 的 NULL 行。只写 NULL 行，绝不覆盖已判行；final 按
各行 kelly 重算，veto 源则 delta/final 双零（点评随判决走）。传播**按轨**：
model_persona 判决只落 model_persona 行、nokb 判决只落 nokb 行，互不串轨
（§12.7 对照轨的分账口径）。
"""
from __future__ import annotations

import json
import sqlite3

from fa.config import persona_path
from fa.evolve.knowledge import ensure_current_snapshot, window_kb_text
from fa.persona import PersonaError
from fa.persona.caller import call_hermes
from fa.persona.contract import (
    build_input,
    build_prompt,
    extract_json,
    validate_output,
)

STRATEGY = "model_persona"
NOKB_STRATEGY = "model_persona_nokb"
_TRACKS = (STRATEGY, NOKB_STRATEGY)

# 降级词表（写进 runs.summary.persona 的取值域）：timeout/exit 来自 caller，
# extract/contract 来自契约层；无 reason 的 PersonaError 归 unknown。
_REASON = {"timeout": "timeout", "exit": "exit",
           "extract": "extract", "contract": "contract"}


def _write_verdict_row(conn: sqlite3.Connection, row_id: int, kelly: float,
                       verdict: str, delta: float, factors: str,
                       report_md: str) -> None:
    """单行落判决：agree/downweight 的 final 按该行 kelly 重算（§6.4 乘法映射），
    veto 双零（§6.4「全部候选丢弃，记录保留」）。不 commit——commit 归编排层。"""
    final = 0.0 if verdict == "veto" else kelly * (1 + delta)
    conn.execute(
        "UPDATE recommendations SET verdict=?, confidence_delta=?,"
        " final_stake_frac=?, key_factors=?, report_md=? WHERE id=?",
        (verdict, delta, round(final, 6), factors, report_md, row_id))


def apply_verdict(conn: sqlite3.Connection, fixture_id: int, output: dict,
                  strategy: str = STRATEGY) -> int:
    """§6.4 映射广播到该 fixture 的 ``strategy`` 轨**全部行**（不分 phase：am
    判决 pm 行共用，§6.2；M6 §12.7 起按轨落——nokb 轨的 veto 只压 nokb 行）；
    agree/downweight 逐行按各自 kelly 重算 final，veto 置零。同写
    verdict/confidence_delta/key_factors(JSON 串)/report_md。
    返回更新行数（该场无此轨行则为 0）。"""
    verdict = output["verdict"]
    delta = 0.0 if verdict == "veto" else float(output["confidence_delta"])
    factors = json.dumps(output["key_factors"], ensure_ascii=False)
    rows = conn.execute(
        "SELECT id, kelly_stake_frac FROM recommendations"
        " WHERE fixture_id=? AND strategy=?",
        (fixture_id, strategy)).fetchall()
    for row in rows:
        _write_verdict_row(conn, row["id"], row["kelly_stake_frac"], verdict,
                           delta, factors, output["report_md"])
    return len(rows)


def _propagate_verdict(conn: sqlite3.Connection, fixture_id: int,
                       strategy: str = STRATEGY) -> int:
    """把该 fixture ``strategy`` 轨的已有判决复制到该轨 ``verdict IS NULL`` 的行
    （pm 沿用 am，§6.2；按轨各传各的，§12.7）。

    源行取该 fixture 该轨**首个**非 NULL 判决行（ORDER BY id LIMIT 1）；目标行由
    ``verdict IS NULL`` 选出（只写 NULL 行，绝不覆盖已判行），final 按各行
    kelly 重算、veto 源双零。无已判行 / 无 NULL 行 → 0，不报错。
    """
    src = conn.execute(
        "SELECT verdict, confidence_delta, key_factors, report_md"
        " FROM recommendations WHERE fixture_id=? AND strategy=?"
        "  AND verdict IS NOT NULL ORDER BY id LIMIT 1",
        (fixture_id, strategy)).fetchone()
    if src is None:
        return 0
    verdict = src["verdict"]
    delta = (0.0 if verdict == "veto"
             else float(src["confidence_delta"] or 0.0))
    rows = conn.execute(
        "SELECT id, kelly_stake_frac FROM recommendations"
        " WHERE fixture_id=? AND strategy=? AND verdict IS NULL",
        (fixture_id, strategy)).fetchall()
    for row in rows:
        _write_verdict_row(conn, row["id"], row["kelly_stake_frac"], verdict,
                           delta, src["key_factors"], src["report_md"])
    return len(rows)


def run_persona_phase(conn: sqlite3.Connection, run_id: int,
                      leagues: list[str],
                      attempted: set[int] | None = None) -> dict:
    """一窗（am 或 pm）的阶段编排：选该 run 的候选 fixture 去重逐场处理——

    M6 起每场**两轨两次调用**（§12.7 对照轨）：kb 轨（persona + 本窗知识快照）
    写 model_persona 行；nokb 轨（纯 persona）写 model_persona_nokb 行，顺序固定
    kb→nokb。降级按轨分别记账（degraded = kb 轨、nokb_degraded = nokb 轨），
    persona 文件缺失 / 本场输入组装失败（§6.3 契约缺口）两轨同降（未触达调用
    不计入 called）。
    ``attempted`` 里的场跳过且零调用，改做判决传播（pm 沿用 am，§6.2）——按轨
    各自传播。恰一次 ``conn.commit()``。

    返回 ``{"called","ok","veto","degraded":[{fixture_id,reason}],"attempted"}
    （kb 轨语义不变，保 ops/watchdog 兼容）+ "nokb_called","nokb_ok",
    "nokb_veto","nokb_degraded" 镜像键``。
    """
    seen = set(attempted or ())
    counts = {t: {"called": 0, "ok": 0, "veto": 0, "degraded": []}
              for t in _TRACKS}
    kb_idx = ensure_current_snapshot()      # 快照自足（幂等；B 线唯一读取口）
    rows = conn.execute(
        "SELECT DISTINCT r.fixture_id, f.league FROM recommendations r"
        " JOIN fixtures f ON f.id = r.fixture_id"
        " WHERE r.run_id=? AND r.strategy IN (?, ?)"
        " ORDER BY r.fixture_id",
        (run_id, STRATEGY, NOKB_STRATEGY)).fetchall()
    league_set = set(leagues)
    for row in rows:
        fid, league = row["fixture_id"], row["league"]
        if fid in seen:                      # am 已判/已试：pm 沿用，零调用
            _propagate_verdict(conn, fid, STRATEGY)
            _propagate_verdict(conn, fid, NOKB_STRATEGY)
            continue
        seen.add(fid)                        # 见过即记（无论调没调、成没成）
        if league not in league_set:
            continue
        try:
            persona_md = persona_path(league).read_text(encoding="utf-8")
        except (FileNotFoundError, ValueError):
            # 文件缺失，或 fixtures 表 league 不在 persona 映射（config 抛
            # ValueError）——都归 persona_file：人格这一环没就位，非模型之过。
            for t in _TRACKS:                # 人格这一环没就位，两轨同降
                counts[t]["degraded"].append({"fixture_id": fid,
                                              "reason": "persona_file"})
            continue
        kb_md = window_kb_text(kb_idx, league)
        try:
            input_obj = build_input(conn, fid, run_id)
        except PersonaError as exc:
            # §6.3 契约缺口（fixture 缺 / 主客未对齐 / kickoff 不可解析）＝输入这一环
            # 没就位，两轨同降、继续下一场（§6.5 场次级；pre-M6 语义：无 reason 属性
            # 的 PersonaError 归 unknown）——不截断本窗余下场、不吞 commit。
            reason = getattr(exc, "reason", "unknown")
            for t in _TRACKS:
                counts[t]["degraded"].append({"fixture_id": fid,
                                              "reason": _REASON.get(reason, reason)})
            continue
        for track in _TRACKS:
            c = counts[track]
            try:
                prompt = build_prompt(persona_md, input_obj,
                                      kb_md=kb_md if track == STRATEGY else None,
                                      kb_label=f"（快照 w{kb_idx}）")
                c["called"] += 1             # hermes 实际调用数（额度口径）：
                                             # persona_file 降级未触达调用不计
                output = extract_json(call_hermes(prompt))
                validate_output(output)
                apply_verdict(conn, fid, output, strategy=track)
            except PersonaError as exc:
                reason = getattr(exc, "reason", "unknown")
                c["degraded"].append({"fixture_id": fid,
                                      "reason": _REASON.get(reason, reason)})
            else:                        # 无异常才计数（原 continue 的等价改写）
                c["ok"] += 1
                c["veto"] += 1 if output["verdict"] == "veto" else 0
    conn.commit()
    kb, nokb = counts[STRATEGY], counts[NOKB_STRATEGY]
    return {"called": kb["called"], "ok": kb["ok"], "veto": kb["veto"],
            "degraded": kb["degraded"], "attempted": sorted(seen),
            "nokb_called": nokb["called"], "nokb_ok": nokb["ok"],
            "nokb_veto": nokb["veto"], "nokb_degraded": nokb["degraded"]}
