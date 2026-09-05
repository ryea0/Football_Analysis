"""三线对比：复用 evaluate/simulate，行 schema 适配正确性是核心。"""
import json

import pytest
from typer.testing import CliRunner

from fa.agentline.compare import compare_lines, debate_gain, merge_rows, render_report
from fa.db import connect, init_db

_runner = CliRunner()


def _bp(mid, ph=0.4):
    return {"match_id": mid, "league": "E0", "season": 2023,
            "date": "2024-02-01", "p_home": ph, "p_draw": 0.3,
            "p_away": 1 - ph - 0.3, "p_over25": 0.5, "p_under25": 0.5,
            "mkt_home": 0.45, "mkt_draw": 0.28, "mkt_away": 0.27,
            "odds_home": 2.2, "odds_draw": 3.5, "odds_away": 3.6,
            "outcome": "H", "total_goals": 3}


@pytest.fixture()
def db_three(tmp_path):
    """1 场 bp 行 + A_base/A_enh 各一 ok 行（p_* 与线 P 略有差异）。"""
    init_db(tmp_path / "t.db")
    conn = connect(tmp_path / "t.db")
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        "INSERT INTO backtest_predictions (league, season, week_index,"
        " match_id, date, p_home, p_draw, p_away, p_over25, p_under25,"
        " mkt_home, mkt_draw, mkt_away, mkt_over25, odds_home, odds_draw,"
        " odds_away, outcome, total_goals)"
        " VALUES ('E0', 2023, 1, 1, '2024-02-01', 0.4, 0.3, 0.3, 0.5, 0.5,"
        " 0.45, 0.28, 0.27, 0.55, 2.2, 3.5, 3.6, 'H', 3)")
    for line, ph in (("A_base", 0.6), ("A_enh", 0.5)):
        conn.execute(
            "INSERT INTO agentline_predictions (match_id, line, p_home,"
            " p_draw, p_away, p_over25, confidence, reasoning_digest,"
            " sources_json, raw_output, status, repaired, created_at)"
            " VALUES (1, ?, ?, 0.2, ?, 0.6, 0.5, 'r', '[]', 'raw', 'ok',"
            " 0, '2026-09-04')", (line, ph, 1 - ph - 0.2))
    conn.commit()
    yield conn
    conn.close()


def _fake_cmp(enh_rows=None, enh_sources=0):
    """compare_lines 返回形态的最小替身：A_enh 行与 audit.enh_sources 可调，
    用于钉住报告的「子集市场 ll」列与「sources 全空」警告的两个分支。"""
    from fa.backtest.metrics import evaluate
    rows = [_bp(1), _bp(2)]
    e = evaluate(rows)
    enh = evaluate(enh_rows) if enh_rows else {"n": 0}
    return {"n": 2, "P": e, "A_base": e, "A_enh": enh,
            "market": {"ll": e["market_ll"]},
            "roi": {"P": {"n": 0, "roi": 0.0}, "A_base": {"n": 0, "roi": 0.0},
                    "A_enh": {"n": 0, "roi": 0.0}},
            "audit": {"enh_sources": enh_sources}}


def test_merge_overwrites_probs_keeps_market():
    ag = [{"match_id": 1, "line": "A_base", "p_home": 0.6, "p_draw": 0.2,
           "p_away": 0.2, "p_over25": 0.7, "outcome": "H", "total_goals": 3}]
    rows = merge_rows([_bp(1)], ag)
    assert rows[0]["p_home"] == 0.6 and rows[0]["mkt_home"] == 0.45
    assert rows[0]["odds_home"] == 2.2 and rows[0]["outcome"] == "H"


def test_compare_lines_three_tracks(db_three):        # fixture 见 Step 3 注
    cmp = compare_lines(db_three)
    assert set(cmp) >= {"P", "A_base", "A_enh", "market", "roi", "n"}
    assert cmp["n"] >= 1
    assert cmp["P"]["model_ll"] > 0 and cmp["A_base"]["model_ll"] > 0
    assert cmp["A_base"]["model_ll"] != cmp["P"]["model_ll"]   # 钉住：A 概率真在覆盖（dict(Row) 重复列名取 ap 侧）
    assert cmp["A_enh"]["model_ll"] != cmp["P"]["model_ll"]


def test_compare_lines_each_line_carries_own_market_ll(db_three):
    # 报告按行展示「子集市场 ll」：每行的 dict 必须自带 market_ll（P 行即全量值）
    cmp = compare_lines(db_three)
    for k in ("P", "A_base", "A_enh"):
        assert cmp[k].get("market_ll", 0) > 0, k


def test_render_report_subset_market_ll_and_footnote(tmp_path):
    from fa.backtest.metrics import evaluate
    enh_rows = [_bp(3, ph=0.5), _bp(4, ph=0.35)]
    enh = evaluate(enh_rows)
    out = tmp_path / "r.md"
    render_report(_fake_cmp(enh_rows=enh_rows, enh_sources=3), out)
    text = out.read_text(encoding="utf-8")
    assert ("| 线 | n | log-loss | Brier | 子集市场 ll | vs 子集市场 |" in text)
    # A_enh 行的比值与市场 ll 都来自它自己的 2 场子集（≠ 全量值）——分母就地标注
    assert (f"| A_enh | 2 | {enh['model_ll']:.4f} | {enh['model_brier']:.4f}"
            f" | {enh['market_ll']:.4f} | {enh['ratio']:.3f}× |") in text
    # 固定脚注：跨线直比无效 + 小样本免责，必须逐字在报告里
    assert "各行比值在其自身 n 场子集内计算，跨线直比无效" in text
    assert "n<100 的行为链路验证样本，数字无统计意义" in text
    # sources 非空（enh_sources>0）→ 不出「sources 全空」警告
    assert "sources 全空" not in text
    # 泄漏提示保留
    assert "泄漏" in text and "A_base" in text and "A_enh" in text


def test_render_report_warns_when_enh_sources_all_empty(tmp_path):
    # sources 全空 ≠ 检索未发生（附录 A.6：首批 E2E 检索实际发生了，是引擎
    # 返回垃圾致无可引用项）——警告须按「引用口径」措辞并指向两种可能；
    # 此时 A_enh vs A_base 的差异是采样噪声，报告必须自己说破。
    out = tmp_path / "r.md"
    render_report(_fake_cmp(enh_rows=[_bp(3, ph=0.5), _bp(4, ph=0.35)],
                            enh_sources=0), out)
    text = out.read_text(encoding="utf-8")
    assert "增强层 sources 全空" in text
    assert "采样噪声" in text
    assert "附录 A.6" in text                  # 指向判定方法


def test_render_report_no_warning_when_enh_empty(tmp_path):
    out = tmp_path / "r.md"
    render_report(_fake_cmp(enh_sources=0), out)
    text = out.read_text(encoding="utf-8")
    assert "增强层 sources 全空" not in text      # A_enh n=0：无检索可言
    assert "| A_enh | 0 | — | — | — | — |" in text


def test_cli_compare_empty_db_friendly_exit(tmp_path, monkeypatch):
    """空表/未迁移都不得甩 traceback（先例：status 的 A 线空表措辞）。"""
    from fa.cli import app
    db = tmp_path / "empty.db"
    monkeypatch.setenv("FA_DB", str(db))
    init_db(db)                                   # 迁移齐全，但 backtest_predictions 空
    res = _runner.invoke(app, ["agentline", "compare"])
    assert res.exit_code == 1, res.output
    assert "无预测行" in res.output and "fa init" in res.output


def test_cli_compare_unmigrated_db_friendly_exit(tmp_path, monkeypatch):
    from fa.cli import app
    monkeypatch.setenv("FA_DB", str(tmp_path / "nope.db"))   # 无表 → OperationalError
    res = _runner.invoke(app, ["agentline", "compare"])
    assert res.exit_code == 1, res.output
    assert "未迁移" in res.output


# ---- A_multi（multi-brain）：五对照 + 成员级审计 -----------------------------
# 夹具沿用本文件既有 db_three 的造数方式（foreign_keys=OFF + 同款 bp INSERT）。

def _al(conn, attributor, ph, pd_, pa, po25, status):
    """A_multi 行造数：attributor 区分聚合行（0）与成员行（1..N）。"""
    conn.execute(
        "INSERT INTO agentline_predictions (match_id, line, attributor,"
        " p_home, p_draw, p_away, p_over25, confidence, reasoning_digest,"
        " sources_json, raw_output, status, repaired, created_at)"
        " VALUES (1, 'A_multi', ?, ?, ?, ?, ?, 0.5, 'r', '[]', 'raw', ?,"
        " 0, '2026-09-04')", (attributor, ph, pd_, pa, po25, status))


@pytest.fixture()
def conn_amulti(tmp_path):
    """1 场 bp 行 + A_multi 4 行：1 聚合 ok（成员中位数 0.6/0.25/0.15）+
    3 成员行（2 ok 概率故意偏移 0.7/0.5 以证明排除 + 1 parse_fail）。
    parse_fail 成员钉住「成员审计分母含非 ok 行」——否则失败被静默吞掉。"""
    init_db(tmp_path / "am.db")
    conn = connect(tmp_path / "am.db")
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        "INSERT INTO backtest_predictions (league, season, week_index,"
        " match_id, date, p_home, p_draw, p_away, p_over25, p_under25,"
        " mkt_home, mkt_draw, mkt_away, mkt_over25, odds_home, odds_draw,"
        " odds_away, outcome, total_goals)"
        " VALUES ('E0', 2023, 1, 1, '2024-02-01', 0.4, 0.3, 0.3, 0.5, 0.5,"
        " 0.45, 0.28, 0.27, 0.55, 2.2, 3.5, 3.6, 'H', 3)")
    _al(conn, 0, 0.6, 0.25, 0.15, 0.575, "ok")      # 聚合行（attributor=0）
    _al(conn, 1, 0.7, 0.2, 0.1, 0.6, "ok")          # 成员：概率故意偏移
    _al(conn, 2, 0.5, 0.3, 0.2, 0.55, "ok")
    _al(conn, 3, None, None, None, None, "parse_fail")
    conn.commit()
    yield conn
    conn.close()


def test_compare_includes_amulti_aggregate_only(conn_amulti):
    """A_multi 只计 attributor=0；成员行（1..3）不得混入该线指标。"""
    from fa.agentline.compare import compare_lines
    cmp = compare_lines(conn_amulti)
    assert cmp["A_multi"]["n"] == 1            # 3 条成员行（2 ok + 1 parse_fail）全被排除
    # 成员行存在性（纪律 1：成员级计分的数据基础）
    assert cmp["audit"]["multi_members"] == 3
    assert cmp["audit"]["multi_member_ok"] == 2


def test_amulti_metric_is_aggregate_row_not_members(conn_amulti):
    """AM-T1 审查点实证：merge_rows 按 match_id 一对一折叠，n 恒为场数——
    「n==1」区分不了成员是否混入，只有概率值能。A_multi 线指标必须逐位等于
    只取 attributor=0 的重算值，且与混入成员行的重算值不同。"""
    from fa.agentline.compare import _fetch_agent, compare_lines, merge_rows
    from fa.backtest.metrics import evaluate, fetch_predictions
    conn = conn_amulti
    bp = fetch_predictions(conn, None, None)
    agg = _fetch_agent(conn, "A_multi", None, None, attributor=0)
    mem = [r for r in _fetch_agent(conn, "A_multi", None, None)
           if r["attributor"] > 0]              # 无归因子过滤：ok 成员行在列
    assert len(agg) == 1 and len(mem) == 2
    cmp = compare_lines(conn)
    assert cmp["A_multi"] == evaluate(merge_rows(bp, agg))
    contaminated = evaluate(merge_rows(bp, agg + mem))
    assert contaminated["n"] == cmp["A_multi"]["n"]      # n 无法区分两者
    assert cmp["A_multi"]["model_ll"] != contaminated["model_ll"]


def test_report_renders_amulti_row(conn_amulti, tmp_path):
    from fa.agentline.compare import render_report
    from fa.agentline.compare import compare_lines
    cmp = compare_lines(conn_amulti)
    out = tmp_path / "r.md"
    render_report(cmp, out)
    text = out.read_text(encoding="utf-8")
    assert "| A_multi |" in text
    assert "A_multi" in text.split("## 平注 ROI")[1]  # ROI 段也含该线
    # 成员披露行（成员级计分入口）必须在脚注前出现
    assert ("A_multi 为多成员确定性聚合（分量中位数）" in text
            and "本批成员 3 行 / ok 2" in text)


# ---- A_debate（生成者-批评者-修订）：五对照 + 修订增益 -----------------------
# 夹具沿用本文件 db_three / conn_amulti 的造数方式（foreign_keys=OFF + 同款
# bp INSERT）；rounds 行 payload 与 test_orchestrate 的 _DEB_OK0/_DEB_ATK 同源
# （round=0 generator = parse_prediction 形态、critic = attacks+severity 形态）。

def _deb_round(conn, mid, round_no, role, payload):
    conn.execute(
        "INSERT INTO agentline_debate_rounds (match_id, round, role,"
        " payload_json, raw_output, status, duration_s, harness, model,"
        " created_at)"
        " VALUES (?, ?, ?, ?, 'raw', 'ok', 0.1, 'h', 'm', '2026-09-05')",
        (mid, round_no, role, payload))


def _deb_seed(conn, mid, ph_v0, ph_fin, sev, with_critic=True):
    """一场完整辩论链：bp 行 + A_debate 终版 ok 行 + round0 生成行（+批评行）。"""
    conn.execute(
        "INSERT INTO backtest_predictions (league, season, week_index,"
        " match_id, date, p_home, p_draw, p_away, p_over25, p_under25,"
        " mkt_home, mkt_draw, mkt_away, mkt_over25, odds_home, odds_draw,"
        " odds_away, outcome, total_goals)"
        " VALUES ('E0', 2023, 1, ?, '2024-02-01', 0.4, 0.3, 0.3, 0.5, 0.5,"
        " 0.45, 0.28, 0.27, 0.55, 2.2, 3.5, 3.6, 'H', 3)", (mid,))
    conn.execute(
        "INSERT INTO agentline_predictions (match_id, line, p_home,"
        " p_draw, p_away, p_over25, confidence, reasoning_digest,"
        " sources_json, raw_output, status, repaired, created_at)"
        " VALUES (?, 'A_debate', ?, 0.3, ?, 0.5, 0.5, 'r', '[]', 'raw',"
        " 'ok', 0, '2026-09-05')", (mid, ph_fin, 1 - ph_fin - 0.3))
    _deb_round(conn, mid, 0, "generator", json.dumps(
        {"p_home": ph_v0, "p_draw": 0.3, "p_away": 1 - ph_v0 - 0.3,
         "p_over25": 0.5, "confidence": 0.5, "reasoning_digest": "d",
         "sources": []}))
    if with_critic:
        _deb_round(conn, mid, 1, "critic", json.dumps(
            {"attacks": [{"label": "overconfidence", "reason": "r",
                          "severity": sev}]}))


def _conn_with(tmp_path, name, seed):
    init_db(tmp_path / f"{name}.db")
    conn = connect(tmp_path / f"{name}.db")
    conn.execute("PRAGMA foreign_keys=OFF")
    seed(conn)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture()
def conn_debate(tmp_path):
    """3 场完整辩论链。两处方向性钉死：终版比 v0 更贴 outcome='H'
    （修订必须真增益）；批评 severity 0.2/0.5/0.8 与修订幅度同向单调
    （批评越狠改得越多）→ 三对即可钉出 ρ=+1 的强信号。"""
    def seed(conn):
        for mid, ph_v0, ph_fin, sev in ((1, 0.34, 0.40, 0.2),
                                        (2, 0.34, 0.50, 0.5),
                                        (3, 0.34, 0.66, 0.8)):
            _deb_seed(conn, mid, ph_v0, ph_fin, sev)
    yield from _conn_with(tmp_path, "deb", seed)


@pytest.fixture()
def conn_debate_nocritic(tmp_path):
    """1 场只有 round0 生成行、无批评行 → 无可比对 → ρ=None（n_rho=0）。"""
    def seed(conn):
        _deb_seed(conn, 1, 0.34, 0.40, 0.0, with_critic=False)
    yield from _conn_with(tmp_path, "debnc", seed)


@pytest.fixture()
def conn_empty(tmp_path):
    """空表库：迁移齐全、零行。"""
    init_db(tmp_path / "empty.db")
    conn = connect(tmp_path / "empty.db")
    yield conn
    conn.close()


def test_compare_lines_includes_debate(conn_debate):
    cmp = compare_lines(conn_debate)
    assert "A_debate" in cmp and cmp["A_debate"]["n"] >= 1
    assert "debate" in cmp and cmp["debate"]["n"] >= 1


def test_debate_gain_v0_vs_final_and_rho(conn_debate):
    g = debate_gain(conn_debate)
    assert g["n"] == 3 and g["n_rho"] == 3
    assert g["v0_ll"] > 0 and g["final_ll"] > 0
    # 终版比 v0 更贴 outcome='H'：修订必须真增益（方向钉死，防 v0/终版对调）
    assert g["final_ll"] < g["v0_ll"]
    # severity 0.2/0.5/0.8 与修订幅度同向单调 → Spearman 恰 +1
    assert g["rho"] == pytest.approx(1.0)


def test_debate_gain_no_critic_rows_yields_rho_none(conn_debate_nocritic):
    g = debate_gain(conn_debate_nocritic)
    assert g["n"] == 1 and g["rho"] is None and g["n_rho"] == 0


@pytest.fixture()
def conn_debate_flat_revs(tmp_path):
    """3 场可比但 v0==终版（revs 全 0 零方差）、severity 各异：Spearman
    未定义 → nan。仿 retro/_mwu_p 口径必须如实记 None，不得让 nan 穿透
    到报告渲染成 ρ=nan（n_rho 照报——有可比对但向量退化，完整披露）。"""
    def seed(conn):
        for mid, sev in ((1, 0.2), (2, 0.5), (3, 0.8)):
            _deb_seed(conn, mid, 0.40, 0.40, sev)   # ph_v0 == ph_fin
    yield from _conn_with(tmp_path, "debflat", seed)


@pytest.mark.filterwarnings("ignore:An input array is constant")
def test_debate_gain_flat_revisions_rho_is_none_not_nan(conn_debate_flat_revs):
    g = debate_gain(conn_debate_flat_revs)
    assert g["n"] == 3 and g["n_rho"] == 3
    assert g["rho"] is None                          # nan 不得穿透


def test_debate_gain_empty(conn_empty):
    assert debate_gain(conn_empty) == {"n": 0}


def test_report_renders_debate_row_and_revision_gain(conn_debate, tmp_path):
    cmp = compare_lines(conn_debate)
    out = tmp_path / "r.md"
    render_report(cmp, out)
    text = out.read_text(encoding="utf-8")
    assert "| A_debate |" in text
    d = cmp["debate"]
    assert "A_debate 修订增益" in text
    assert (f"v0 ll={d['v0_ll']:.4f}" in text
            and f"终版 ll={d['final_ll']:.4f}" in text)
    assert "ρ=+1.00" in text and "固执" in text and "无主见" in text
    # 修订增益段必须在全局免责脚注之前（脚注前追加，不吞掉既有小样本免责）
    assert (text.index("A_debate 修订增益")
            < text.index("各行比值在其自身 n 场子集内计算"))


def test_report_omits_debate_gain_when_absent(tmp_path):
    # 旧形态 dict（无 debate 键）不得炸 render_report，也不得虚报修订增益
    out = tmp_path / "r.md"
    render_report(_fake_cmp(enh_sources=0), out)
    text = out.read_text(encoding="utf-8")
    assert "修订增益" not in text and "| A_debate |" not in text


# ---- A_division（三跳 + 质询判决）：七对照 + 质询标签分层检验 ---------------
# 夹具沿用本文件 _deb_seed 的造数方式（同款 bp INSERT + ok 终版行）；jumps 行
# payload 与 test_division / 引擎 _record 输出同源（json.dumps 的规范化 dict）。

_HIST_OK = json.dumps({"h2h_points": [], "recent_form_points": []})
_PRED_OK = json.dumps({"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2,
                       "p_over25": 0.5, "confidence": 0.5,
                       "reasoning_digest": "d", "sources": []})
_ATK_OC = json.dumps({"attacks": [{"label": "overconfidence", "reason": "r",
                                   "severity": 0.9}]})
_ATK_NONE = json.dumps({"attacks": []})


def _div_jump(conn, mid, jump, role, payload):
    conn.execute(
        "INSERT INTO agentline_division_jumps (match_id, jump, role,"
        " payload_json, raw_output, status, duration_s, harness, model,"
        " created_at)"
        " VALUES (?, ?, ?, ?, 'raw', 'ok', 0.1, 'h', 'm', '2026-09-05')",
        (mid, jump, role, payload))


def _div_seed(conn, mid, atk, ph_fin=0.5):
    """一场完整跳链：bp 行 + A_division 终版 ok 行 + 三跳 ok 行。"""
    conn.execute(
        "INSERT INTO backtest_predictions (league, season, week_index,"
        " match_id, date, p_home, p_draw, p_away, p_over25, p_under25,"
        " mkt_home, mkt_draw, mkt_away, mkt_over25, odds_home, odds_draw,"
        " odds_away, outcome, total_goals)"
        " VALUES ('E0', 2023, 1, ?, '2024-02-01', 0.4, 0.3, 0.3, 0.5, 0.5,"
        " 0.45, 0.28, 0.27, 0.55, 2.2, 3.5, 3.6, 'H', 3)", (mid,))
    conn.execute(
        "INSERT INTO agentline_predictions (match_id, line, p_home,"
        " p_draw, p_away, p_over25, confidence, reasoning_digest,"
        " sources_json, raw_output, status, repaired, created_at)"
        " VALUES (?, 'A_division', ?, 0.3, ?, 0.5, 0.5, 'r', '[]', 'raw',"
        " 'ok', 0, '2026-09-05')", (mid, ph_fin, 1 - ph_fin - 0.3))
    _div_jump(conn, mid, 1, "archivist", _HIST_OK)
    _div_jump(conn, mid, 2, "predictor", _PRED_OK)
    _div_jump(conn, mid, 3, "challenger", atk)


@pytest.fixture()
def conn_with_div_rows(tmp_path):
    """6 场可评终版 + 1 场 parse_fail 终版：match 1 跳3 落 overconfidence
    攻击（有标签层），其余 5 场空 attacks（无标签层）——两向计数都非零；
    parse_fail 场钉住「非 ok 终版行不进分层」（audit 口径：只评可评行）。"""
    def seed(conn):
        _div_seed(conn, 1, _ATK_OC)
        for mid in range(2, 7):
            _div_seed(conn, mid, _ATK_NONE)
        # parse_fail 终版：bp 行在、终版行非 ok（概率 NULL）→ 分层不计
        conn.execute(
            "INSERT INTO backtest_predictions (league, season, week_index,"
            " match_id, date, p_home, p_draw, p_away, p_over25, p_under25,"
            " mkt_home, mkt_draw, mkt_away, mkt_over25, odds_home, odds_draw,"
            " odds_away, outcome, total_goals)"
            " VALUES ('E0', 2023, 1, 7, '2024-02-01', 0.4, 0.3, 0.3, 0.5,"
            " 0.5, 0.45, 0.28, 0.27, 0.55, 2.2, 3.5, 3.6, 'H', 3)")
        conn.execute(
            "INSERT INTO agentline_predictions (match_id, line,"
            " reasoning_digest, sources_json, raw_output, status, repaired,"
            " created_at)"
            " VALUES (7, 'A_division', 'r', '[]', 'raw', 'parse_fail',"
            " 0, '2026-09-05')")
    yield from _conn_with(tmp_path, "div", seed)


@pytest.fixture()
def conn_div_flat(tmp_path):
    """10 场全平手（retro._mwu 先例口径）：5 有标签 + 5 无标签，终版概率
    全同 → 10 场 log-loss 全同。两侧 n≥5 可触发 MWU，但全平手 → p=nan，
    须如实记 None（n 也照报——向量退化不是样本缺失，完整披露）。
    实证：scipy 1.18 的 MWU 仅在全平手时 nan——单侧常量、另一侧有差异
    给有限 p（0.656），不足以触发。"""
    def seed(conn):
        for mid in range(1, 6):
            _div_seed(conn, mid, _ATK_OC, 0.5)
        for mid in range(6, 11):
            _div_seed(conn, mid, _ATK_NONE, 0.5)
    yield from _conn_with(tmp_path, "divflat", seed)


def test_compare_lines_includes_division(conn_with_div_rows):
    cmp = compare_lines(conn_with_div_rows)
    assert "A_division" in cmp and cmp["A_division"]["n"] >= 1
    assert "division" in cmp


def test_division_stratified_flag_vs_unflag(conn_with_div_rows):
    from fa.agentline.compare import division_stratified
    s = division_stratified(conn_with_div_rows)
    assert s["n_flagged"] == 1 and s["n_unflagged"] == 5
    # 小样本（任一层 n<5）不出 mwu_p——诚实边界；两层 ll 都要报
    assert "mwu_p" not in s
    assert 0 < s["ll_flagged"] < 1 and 0 < s["ll_unflagged"] < 1


def test_division_stratified_excludes_non_ok_finals(conn_with_div_rows):
    """parse_fail 终版场（match 7）不得混进任一层——分层只取 ok 终版行。"""
    from fa.agentline.compare import _fetch_agent, division_stratified
    mids = {r["match_id"] for r in _fetch_agent(
        conn_with_div_rows, "A_division", None, None, attributor=1)}
    assert 7 not in mids and len(mids) == 6
    s = division_stratified(conn_with_div_rows)
    assert s["n_flagged"] + s["n_unflagged"] == 6


def test_division_stratified_empty(conn_empty):
    from fa.agentline.compare import division_stratified
    s = division_stratified(conn_empty)
    assert s == {"n_flagged": 0, "n_unflagged": 0}


def test_division_stratified_all_ties_mwu_p_is_none_not_nan(conn_div_flat):
    """两组 log-loss 全平手 → MWU 未定义返回 nan：必须如实记 None，不得让
    nan 穿透到报告渲染成 p=nan（先例：retro._mwu / debate_gain.rho）；
    n 照报——向量退化不是样本缺失，是另一回事。"""
    from fa.agentline.compare import division_stratified
    s = division_stratified(conn_div_flat)
    assert s["n_flagged"] == 5 and s["n_unflagged"] == 5
    assert s["ll_flagged"] == s["ll_unflagged"]     # 全平手前提成立
    assert s["mwu_p"] is None


def test_report_renders_division_row_and_stratified(conn_with_div_rows,
                                                    tmp_path):
    from fa.agentline.compare import compare_lines
    cmp = compare_lines(conn_with_div_rows)
    out = tmp_path / "r.md"
    render_report(cmp, out)
    text = out.read_text(encoding="utf-8")
    assert "| A_division |" in text
    assert "A_division 质询分层" in text
    assert "有标签 n=1" in text and "无标签 n=5" in text
    # 小样本占位必须逐字在报告里，且允许「质询无信息量」这个结论方向
    assert "n<5/层不报（小样本诚实）" in text
    assert "质询无信息量" in text
    # 分层段必须在全局免责脚注之前（脚注前追加，不吞掉既有小样本免责）
    assert (text.index("A_division 质询分层")
            < text.index("各行比值在其自身 n 场子集内计算"))


def test_report_renders_degenerate_mwu_placeholder(conn_div_flat, tmp_path):
    """mwu_p=None（向量退化）不得渲染成 p=nan 或 p=None——专用占位与
    小样本占位措辞不同，否则「算不出」会被误读成「样本不够」。"""
    from fa.agentline.compare import compare_lines
    out = tmp_path / "r.md"
    render_report(compare_lines(conn_div_flat), out)
    text = out.read_text(encoding="utf-8")
    assert "p=nan" not in text and "p=None" not in text
    assert "MWU 未定义（两组 log-loss 全平手）" in text
    assert "有标签 n=5" in text and "无标签 n=5" in text


def test_report_omits_division_stratified_when_absent(tmp_path):
    # 旧形态 dict（无 division 键）不得炸 render_report，也不得虚报分层段
    out = tmp_path / "r.md"
    render_report(_fake_cmp(enh_sources=0), out)
    text = out.read_text(encoding="utf-8")
    assert "质询分层" not in text and "| A_division |" not in text
