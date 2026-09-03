"""persona 判决应用与阶段编排（spec §6.4 / §6.5 / §6.2）。

降级只回退该场（§6.5 场次级）：该场三列保持中性、点评两列不动、记入返回值
degraded——调用方（matchday）把它写进 runs.summary.persona；当日不重试
（attempted 语义：am 的名单落 runs.summary 供 pm 读）。

判决传播（§6.2「17:00 更新版不重跑 persona，沿用 11:00 判决」的落库语义）：
am 广播判决时 pm 的行尚不存在，pm value 落的新行 ``verdict IS NULL``——
run_persona_phase 对 attempted 跳过的 fixture 把库内已有判决（首个非 NULL
判决行）复制到本 fixture 的 NULL 行。只写 NULL 行，绝不覆盖已判行；final 按
各行 kelly 重算，veto 源则 delta/final 双零（点评随判决走）。
"""
from __future__ import annotations

import json
import sqlite3

from fa.config import persona_path
from fa.persona import PersonaError
from fa.persona.caller import call_hermes
from fa.persona.contract import (
    build_input,
    build_prompt,
    extract_json,
    validate_output,
)

STRATEGY = "model_persona"

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


def apply_verdict(conn: sqlite3.Connection, fixture_id: int, output: dict) -> int:
    """§6.4 映射广播到该 fixture 的 model_persona 轨**全部行**（不分 phase：am
    判决 pm 行共用，§6.2）；agree/downweight 逐行按各自 kelly 重算 final，veto
    置零。同写 verdict/confidence_delta/key_factors(JSON 串)/report_md。
    返回更新行数（该场无此轨行则为 0）。"""
    verdict = output["verdict"]
    delta = 0.0 if verdict == "veto" else float(output["confidence_delta"])
    factors = json.dumps(output["key_factors"], ensure_ascii=False)
    rows = conn.execute(
        "SELECT id, kelly_stake_frac FROM recommendations"
        " WHERE fixture_id=? AND strategy=?",
        (fixture_id, STRATEGY)).fetchall()
    for row in rows:
        _write_verdict_row(conn, row["id"], row["kelly_stake_frac"], verdict,
                           delta, factors, output["report_md"])
    return len(rows)


def _propagate_verdict(conn: sqlite3.Connection, fixture_id: int) -> int:
    """把该 fixture 已有判决复制到 ``verdict IS NULL`` 的行（pm 沿用 am，§6.2）。

    源行取该 fixture **首个**非 NULL 判决行（ORDER BY id LIMIT 1）；目标行由
    ``verdict IS NULL`` 选出（只写 NULL 行，绝不覆盖已判行），final 按各行
    kelly 重算、veto 源双零。无已判行 / 无 NULL 行 → 0，不报错。
    """
    src = conn.execute(
        "SELECT verdict, confidence_delta, key_factors, report_md"
        " FROM recommendations WHERE fixture_id=? AND strategy=?"
        "  AND verdict IS NOT NULL ORDER BY id LIMIT 1",
        (fixture_id, STRATEGY)).fetchone()
    if src is None:
        return 0
    verdict = src["verdict"]
    delta = (0.0 if verdict == "veto"
             else float(src["confidence_delta"] or 0.0))
    rows = conn.execute(
        "SELECT id, kelly_stake_frac FROM recommendations"
        " WHERE fixture_id=? AND strategy=? AND verdict IS NULL",
        (fixture_id, STRATEGY)).fetchall()
    for row in rows:
        _write_verdict_row(conn, row["id"], row["kelly_stake_frac"], verdict,
                           delta, src["key_factors"], src["report_md"])
    return len(rows)


def run_persona_phase(conn: sqlite3.Connection, run_id: int,
                      leagues: list[str],
                      attempted: set[int] | None = None) -> dict:
    """一窗（am 或 pm）的阶段编排：选该 run 的候选 fixture 去重逐场处理——

    persona 文件 → build_input（按本 run 取候选）→ build_prompt → call_hermes
    → extract → validate → apply_verdict；任一环节失败只降该场（§6.5 场次级，
    ``PersonaError``/``FileNotFoundError``/``ValueError`` → reason
    ``persona_file``/``timeout``/``exit``/``extract``/``contract``/``unknown``）。
    ``attempted`` 里的场跳过且零调用，改做判决传播（pm 沿用 am，§6.2）；不在
    ``leagues`` 清单的场不调用但记入 attempted。恰一次 ``conn.commit()``。

    返回 ``{"called", "ok", "veto", "degraded": [{"fixture_id", "reason"}],
    "attempted": 排序名单（传入 ∪ 本次涉及）}``。
    """
    seen = set(attempted or ())
    called = ok = veto = 0
    degraded: list[dict] = []
    rows = conn.execute(
        "SELECT DISTINCT r.fixture_id, f.league FROM recommendations r"
        " JOIN fixtures f ON f.id = r.fixture_id"
        " WHERE r.run_id=? AND r.strategy=?"
        " ORDER BY r.fixture_id", (run_id, STRATEGY)).fetchall()
    league_set = set(leagues)
    for row in rows:
        fid, league = row["fixture_id"], row["league"]
        if fid in seen:                      # am 已判/已试：pm 沿用，零调用
            _propagate_verdict(conn, fid)
            continue
        seen.add(fid)                        # 见过即记（无论调没调、成没成）
        if league not in league_set:
            continue
        try:
            persona_md = persona_path(league).read_text(encoding="utf-8")
            prompt = build_prompt(persona_md, build_input(conn, fid, run_id))
            called += 1                      # hermes 实际调用数（额度口径）：
                                             # persona_file 降级未触达调用不计
            output = extract_json(call_hermes(prompt))
            validate_output(output)
            apply_verdict(conn, fid, output)
        except (FileNotFoundError, ValueError):
            # 文件缺失，或 fixtures 表 league 不在 persona 映射（config 抛
            # ValueError）——都归 persona_file：人格这一环没就位，非模型之过。
            degraded.append({"fixture_id": fid, "reason": "persona_file"})
            continue
        except PersonaError as exc:
            reason = getattr(exc, "reason", "unknown")
            degraded.append({"fixture_id": fid,
                             "reason": _REASON.get(reason, reason)})
            continue
        ok += 1
        veto += 1 if output["verdict"] == "veto" else 0
    conn.commit()
    return {"called": called, "ok": ok, "veto": veto,
            "degraded": degraded, "attempted": sorted(seen)}
