"""persona I/O 契约（spec §6.3）：输入组装（全来自库内已有数据）与输出提取校验。

输入裁剪（对 §6.3 示例的有意裁定）：``model_summary`` 只含 H/D/A 行的 ``model_p``
（λ 不在库内，不发明数字）；``home_pos``/``away_pos`` 赛季无数据则缺省（缺键，
不写 NULL）。时间口径：form / h2h / 排名一律取 kickoff **日历日之前**的完赛行
（当日同场不是历史；未完赛行 ``fthg IS NULL`` 一律剔除）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from fa.config import persona_search_enabled
from fa.persona import PersonaError

_MARKET_TO_KEY = {"H": "p_home", "D": "p_draw", "A": "p_away"}

_FORM_LEN = 5
_H2H_LEN = 3

_CONTRACT_CLAUSE_BASE = """

## 输出契约（必须严格遵守）
输出必须是且仅是一个 JSON 对象（不要代码围栏、不要任何多余文字），字段：
- verdict：三选一 "agree" / "downweight" / "veto"
- confidence_delta：[-0.15, +0.15] 内的数；downweight 时必须 < 0；veto 时填 0
- key_factors：1–5 条字符串数组，每条 ≤ 50 字
- report_md：≤ 500 字中文点评（markdown）
不合规的输出会被程序整场丢弃（该场按纯模型处理），宁保守勿越界。
"""

# 过渡条款（stopgap，T15 fix round 1）：web_search 不可用时防 derail——m4-report
# §2.1 实测 27 调用 13 例失败全是模型先调 web_search（标记式/JSON 式/叙述态）。
# 根治 = 配搜索后端 key（FA_PERSONA_SEARCH=1，本句自动退出 prompt）。
_NO_SEARCH_CLAUSE = ("本环境无网络搜索可用：不要调用任何工具（包括 web_search），"
                     "直接基于本场输入 JSON 给出判断。\n")


def _contract_clause() -> str:
    """输出契约两形态：``persona_search_enabled()`` 为真保持原样（检索合法）；
    为假（默认）追加禁工具句。"""
    if persona_search_enabled():
        return _CONTRACT_CLAUSE_BASE
    return _CONTRACT_CLAUSE_BASE + "\n" + _NO_SEARCH_CLAUSE


def build_prompt(persona_md: str, input_obj: dict, kb_md: str | None = None,
                 kb_label: str = "") -> str:
    """prompt = persona 全文 +（可选）知识库段 + 该场输入 JSON + 输出契约说明。

    知识段（M6 §12.7）：``kb_md`` 只由 model_persona 轨传入（窗口快照文本），
    nokb 轨与无知识库时期传 None——段整体缺省，prompt 形状不空挂标题。
    """
    kb = (f"\n\n## 联赛知识库{kb_label}\n\n" + kb_md.rstrip()
          if kb_md else "")
    return (persona_md.rstrip() + kb + "\n\n## 本场输入\n```json\n"
            + json.dumps(input_obj, ensure_ascii=False, indent=2)
            + "\n```" + _contract_clause())


# ---------------------------------------------------------------- 输入组装


def build_input(conn: sqlite3.Connection, fixture_id: int, run_id: int) -> dict:
    """§6.3 输入 JSON 的 dict 形态——字段全部来自库内（§1.4：不发明数字）。

    ``run_id`` = 本次点评所属的 run（run 承载 phase，§3.2）：candidates /
    model_summary 只取 ``(fixture_id, run_id)`` 的行——am/pm 两窗同 market 各有
    一行，不过滤会把双窗行一起塞进 candidates（T6 review 裁定修正）。

    fixture 不存在 / 主客任一侧未对齐（§3.3 NULL）/ kickoff 不可解析时抛
    :class:`PersonaError`：persona 失败可降级（§6.6，该场按纯模型处理）。
    """
    head = conn.execute(
        "SELECT f.league, f.kickoff_utc, f.home_team_id, f.away_team_id,"
        " h.name AS home, a.name AS away"
        " FROM fixtures f"
        " LEFT JOIN teams h ON h.id = f.home_team_id"
        " LEFT JOIN teams a ON a.id = f.away_team_id"
        " WHERE f.id = ?", (fixture_id,)).fetchone()
    if head is None:
        raise PersonaError(f"fixture {fixture_id} 不存在")
    if head["home"] is None or head["away"] is None:
        raise PersonaError(f"fixture {fixture_id} 主客未对齐，persona 无从点评")
    kickoff_date = _kickoff_date(head["kickoff_utc"])

    # 本 run（=本窗）的 model_persona 行（按落库序）；summary 从 H/D/A 行拼 model_p。
    # persona 一场一窗一次调用（§6.2），pm 不重跑只沿用 am 判决（apply 层传播）。
    rows = conn.execute(
        "SELECT market, model_p, market_p, best_odds, edge, ev, kelly_stake_frac"
        " FROM recommendations"
        " WHERE fixture_id = ? AND run_id = ? AND strategy = 'model_persona'"
        " ORDER BY id", (fixture_id, run_id)).fetchall()

    obj: dict = {
        "league": head["league"],
        "match": {"kickoff_utc": head["kickoff_utc"],
                  "home": head["home"], "away": head["away"]},
        "candidates": [dict(r) for r in rows],
        "model_summary": {_MARKET_TO_KEY[r["market"]]: r["model_p"]
                          for r in rows if r["market"] in _MARKET_TO_KEY},
        "form": {"home_last5": _last5(conn, head["league"], head["home_team_id"],
                                      kickoff_date),
                 "away_last5": _last5(conn, head["league"], head["away_team_id"],
                                      kickoff_date)},
        "h2h_recent": _h2h(conn, head["league"], head["home_team_id"],
                           head["away_team_id"], kickoff_date),
    }
    pos = _positions(conn, head["league"], _season(kickoff_date), kickoff_date)
    for key, team in (("home_pos", head["home_team_id"]),
                      ("away_pos", head["away_team_id"])):
        if team in pos:                      # 无本赛季行 → 键缺省（不写 NULL）
            obj[key] = pos[team]
    return obj


def _kickoff_date(kickoff_utc: str) -> str:
    """``...Z`` / 带偏移 ISO 串 → UTC 日历日（``YYYY-MM-DD``，与 ``matches.date``
    同口径可比）；不可解析 → :class:`PersonaError`（不猜时区，同 paper 层）。"""
    try:
        parsed = datetime.fromisoformat(str(kickoff_utc).replace("Z", "+00:00"))
    except ValueError as exc:
        raise PersonaError(f"kickoff_utc 不可解析: {kickoff_utc!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)     # 裸时间按 UTC 读
    return parsed.astimezone(timezone.utc).date().isoformat()


def _season(kickoff_date: str) -> int:
    """赛季推断（M1 口径，``matches.season`` = 起始年）：7 月起算新季。"""
    year, month = int(kickoff_date[:4]), int(kickoff_date[5:7])
    return year if month >= 7 else year - 1


def _last5(conn: sqlite3.Connection, league: str, team_id: int,
           before_date: str) -> list[str]:
    """该队 ``before_date`` 之前的近 5 场 W/D/L 串（date DESC，跨赛季照取）。"""
    rows = conn.execute(
        "SELECT date, fthg, ftag, home_team_id FROM matches"
        " WHERE league=? AND (home_team_id=? OR away_team_id=?)"
        "  AND date<? AND fthg IS NOT NULL"
        " ORDER BY date DESC, id DESC LIMIT ?",
        (league, team_id, team_id, before_date, _FORM_LEN)).fetchall()
    out = []
    for r in rows:
        gf, ga = ((r["fthg"], r["ftag"]) if r["home_team_id"] == team_id
                  else (r["ftag"], r["fthg"]))       # 队视角：客场时主客互换
        out.append("W" if gf > ga else "D" if gf == ga else "L")
    return out


def _h2h(conn: sqlite3.Connection, league: str, home_id: int, away_id: int,
         before_date: str) -> list[dict]:
    """近 3 次交手，近的在前；``score``/``home`` 按当场的真实主客方位写。
    交手限定同联赛（M1 库只含五大联赛联赛场）。"""
    rows = conn.execute(
        "SELECT m.date, m.fthg, m.ftag, t.name AS home FROM matches m"
        " JOIN teams t ON t.id = m.home_team_id"
        " WHERE m.league=? AND m.date<? AND m.fthg IS NOT NULL"
        "  AND ((m.home_team_id=? AND m.away_team_id=?)"
        "    OR (m.home_team_id=? AND m.away_team_id=?))"
        " ORDER BY m.date DESC, m.id DESC LIMIT ?",
        (league, before_date, home_id, away_id, away_id, home_id,
         _H2H_LEN)).fetchall()
    return [{"date": r["date"], "score": f"{r['fthg']}-{r['ftag']}",
             "home": r["home"]} for r in rows]


def _positions(conn: sqlite3.Connection, league: str, season: int,
               before_date: str) -> dict[int, int]:
    """本赛季 ``before_date`` 之前的积分榜 → ``{team_id: 名次}``（胜 3 平 1，
    净胜球 → 进球 → 队 id 定序）。无行 → 空 dict，调用方据此省略两个 pos 键。"""
    rows = conn.execute(
        "SELECT home_team_id, away_team_id, fthg, ftag FROM matches"
        " WHERE league=? AND season=? AND date<? AND fthg IS NOT NULL",
        (league, season, before_date)).fetchall()
    stats: dict[int, list[int]] = {}     # team_id -> [积分, 净胜球, 进球]
    for r in rows:
        for team, gf, ga in ((r["home_team_id"], r["fthg"], r["ftag"]),
                             (r["away_team_id"], r["ftag"], r["fthg"])):
            s = stats.setdefault(team, [0, 0, 0])
            s[0] += 3 if gf > ga else (1 if gf == ga else 0)
            s[1] += gf - ga
            s[2] += gf
    order = sorted(stats,
                   key=lambda t: (-stats[t][0], -stats[t][1], -stats[t][2], t))
    return {team: i + 1 for i, team in enumerate(order)}


# ---------------------------------------------------------------- 输出提取与校验


class PersonaContractError(PersonaError):
    """输出提取/校验失败（§6.5 该场可降级）：``reason`` ∈ {"extract", "contract"}。"""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


def extract_json(text: str) -> dict:
    """stdout → JSON 对象：整体 → 围栏 → 平衡花括号扫描（字符串内 } 不误判）。"""
    stripped = (text or "").strip()
    for candidate in (stripped, _fenced(stripped)):
        if candidate is not None:
            try:
                obj = json.loads(candidate)
            except ValueError:
                continue
            if isinstance(obj, dict):
                return obj
    obj = _balanced_scan(stripped)
    if obj is not None:
        return obj
    raise PersonaContractError(
        "extract", f"stdout 无合法 JSON（前 80 字：{stripped[:80]!r}）")


def _fenced(text: str) -> str | None:
    """取第一段完整 ```json 围栏内容；无围栏或无闭合则 None（保守不硬拆）。"""
    if "```" not in text:
        return None
    parts = text.split("```")
    for i in range(1, len(parts) - 1, 2):        # 围栏内容在奇数段
        body = parts[i]
        body = body[4:] if body.lstrip().startswith("json") else body
        if "{" in body:
            return body.strip()
    return None


def _balanced_scan(text: str) -> dict | None:
    """自首个 ``{`` 起做平衡花括号扫描；字符串内的花括号/引号不计数。"""
    start = text.find("{")
    while start != -1:
        depth, in_str, escape = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                    except ValueError:
                        break                      # 该起点不成 JSON，试下一个 {
                    if isinstance(obj, dict):
                        return obj
        start = text.find("{", start + 1)
    return None


def validate_output(obj: dict) -> None:
    """§6.3 逐条；veto 的 delta 不在此卡（apply 强制置 0，设计文档 §5 裁定）。"""
    if obj.get("verdict") not in ("agree", "downweight", "veto"):
        raise PersonaContractError("contract", f"verdict 词表外：{obj.get('verdict')!r}")
    delta = obj.get("confidence_delta")
    if not isinstance(delta, (int, float)) or isinstance(delta, bool):
        raise PersonaContractError("contract", f"confidence_delta 非数值：{delta!r}")
    if not -0.15 <= delta <= 0.15:
        raise PersonaContractError("contract", f"confidence_delta 超值域：{delta}")
    if obj["verdict"] == "downweight" and delta >= 0:
        raise PersonaContractError("contract", f"downweight 须 <0：{delta}")
    factors = obj.get("key_factors")
    if (not isinstance(factors, list) or not 1 <= len(factors) <= 5
            or not all(isinstance(f, str) for f in factors)):
        raise PersonaContractError("contract", f"key_factors 须 1–5 条字符串：{factors!r}")
    if any(len(f) > 50 for f in factors):
        raise PersonaContractError("contract", "key_factors 单条超 50 字")
    report = obj.get("report_md")
    if not isinstance(report, str) or len(report) > 500:
        raise PersonaContractError("contract", f"report_md 须 ≤500 字：{report!r}")
