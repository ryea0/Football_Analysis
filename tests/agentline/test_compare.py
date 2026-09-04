"""三线对比：复用 evaluate/simulate，行 schema 适配正确性是核心。"""
import pytest
from typer.testing import CliRunner

from fa.agentline.compare import compare_lines, merge_rows, render_report
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
