"""retro CLI 测试——CliRunner + FA_DB 临时库；网络打桩在 runner 层之上
（CLI 不传 call，run_retro_batch 的 call=None 缝在**调用时**解析模块属性，
故 monkeypatch fa.retro.pipeline.run_headless 生效；patch
fa.retro.runner.run_headless 动不到 pipeline 命名空间里已 import 的绑定）。"""
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fa.cli import app
from fa.db import connect, init_db
from fa.retro.analyze import _mwu, stratified_analysis

runner = CliRunner()


@pytest.fixture
def db(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("FA_DB", str(db))
    init_db(db)
    conn = connect(db)
    from tests.retro.test_select import _seed
    _seed(conn)
    conn.close()
    return db


def test_report_renders_divergence(db):
    """S0 验收：纯 SQL 报告，零 LLM 调用（不触网即可跑）。"""
    result = runner.invoke(app, ["retro", "report", "--top", "2"])
    assert result.exit_code == 0
    assert "Arsenal" in result.output
    assert "div" in result.output or "分歧" in result.output


def test_run_manual_batch(db, tmp_path, monkeypatch):
    from fa.retro import pipeline

    def fake_headless(prompt):
        assert "Arsenal" in prompt            # prompt 确含信息集
        return {"ok": True, "output": json.dumps({
            "miss_tags": ["injury"], "primary_tag": "injury",
            "tags_confidence": 0.8, "model_vs_market": "model_wrong",
            "evidence": [{"title": "t", "date": "2024-04-01",
                          "url": "https://e.com"}],
            "digest": "伤停致模型高估主胜。"}, ensure_ascii=False),
            "error": None, "duration_s": 0.5}

    monkeypatch.setattr(pipeline, "run_headless", fake_headless)
    result = runner.invoke(
        app, ["retro", "run", "--selector", "manual",
              "--matches", "1", "--out-root", str(tmp_path / "packs")])
    assert result.exit_code == 0, result.output
    assert "ok=1" in result.output or "ok 1" in result.output
    conn = connect(db)
    assert conn.execute(
        "SELECT COUNT(*) c FROM retro_attributions").fetchone()["c"] == 1
    conn.close()


def test_run_manual_requires_a_filter(db, tmp_path, monkeypatch):
    """零过滤护栏（额度纪律）：manual 三参全空 = 全库逐场真调 LLM，
    必须在选场前拒绝，exit 1 + 明示原因（实测 11.8s/场 × 59k 场）。"""
    from fa.retro import pipeline
    calls = {"n": 0}

    def must_not_call(prompt):
        calls["n"] += 1
        raise AssertionError("护栏应先退出，不得触达 run_headless")

    monkeypatch.setattr(pipeline, "run_headless", must_not_call)
    result = runner.invoke(
        app, ["retro", "run", "--selector", "manual",
              "--out-root", str(tmp_path / "packs")])
    assert result.exit_code == 1
    assert "--matches" in result.output and "--season" in result.output
    assert calls["n"] == 0


def test_run_divergence_batch_records_league_and_control(db, tmp_path,
                                                         monkeypatch):
    """divergence 台账 params 记 league（与 manual 对齐）；批内病例/对照落库
    且 is_control 可逐行区分（0=病例 / 1=对照）。"""
    from fa.retro import pipeline

    def fake_headless(prompt):
        return {"ok": True, "output": json.dumps({
            "miss_tags": ["variance"], "primary_tag": "variance",
            "tags_confidence": 0.5, "model_vs_market": "variance",
            "evidence": [], "digest": "波动。"}, ensure_ascii=False),
            "error": None, "timeout": False, "duration_s": 0.2}

    monkeypatch.setattr(pipeline, "run_headless", fake_headless)
    r = runner.invoke(
        app, ["retro", "run", "--selector", "divergence", "--top", "1",
              "--control", "1", "--league", "E0",
              "--out-root", str(tmp_path / "p")])
    assert r.exit_code == 0, r.output
    conn = connect(db)
    try:
        run = conn.execute("SELECT params_json FROM retro_runs").fetchone()
        assert json.loads(run["params_json"])["league"] == "E0"
        rows = conn.execute(
            "SELECT match_id, is_control FROM retro_attributions"
            " ORDER BY match_id").fetchall()
        assert len(rows) == 2                       # 1 病例 + 1 对照
        assert [x["is_control"] for x in rows] == [0, 1]
    finally:
        conn.close()


def test_audit_flags_late_evidence(db, tmp_path, monkeypatch):
    """证据日期晚于比赛日（2024-04-20）→ 违规；早于 → 通过。"""
    from fa.retro import pipeline

    late = json.dumps({
        "miss_tags": ["injury"], "primary_tag": "injury",
        "tags_confidence": 0.9, "model_vs_market": "model_wrong",
        "evidence": [{"title": "赛后复盘文", "date": "2024-04-25",
                      "url": "https://e.com/late"}],
        "digest": "x" * 10}, ensure_ascii=False)

    def fake_headless(prompt):
        return {"ok": True, "output": late, "error": None, "duration_s": 0.1}

    monkeypatch.setattr(pipeline, "run_headless", fake_headless)
    r = runner.invoke(
        app, ["retro", "run", "--selector", "manual", "--matches", "1,2",
              "--out-root", str(tmp_path / "p")])
    assert r.exit_code == 0
    a = runner.invoke(app, ["retro", "audit"])
    assert a.exit_code == 0
    assert "违规" in a.output and "2024-04-25" in a.output


def test_audit_passes_early_evidence(db, tmp_path, monkeypatch):
    early = json.dumps({
        "miss_tags": ["injury"], "primary_tag": "injury",
        "tags_confidence": 0.9, "model_vs_market": "model_wrong",
        "evidence": [{"title": "赛前伤停名单", "date": "2024-04-19",
                      "url": "https://e.com/ok"}],
        "digest": "y" * 10}, ensure_ascii=False)
    from fa.retro import pipeline
    monkeypatch.setattr(pipeline, "run_headless",
                        lambda p: {"ok": True, "output": early,
                                   "error": None, "duration_s": 0.1})
    runner.invoke(app, ["retro", "run", "--selector", "manual",
                        "--matches", "1", "--out-root", str(tmp_path / "p")])
    a = runner.invoke(app, ["retro", "audit"])
    assert a.exit_code == 0
    assert "违规 0" in a.output


def _attr(conn, batch_id, match_id, date, evidence, tags=("injury",)):
    """直插一条 status=ok 的归因行（审计用例不依赖 run→audit 全链路）。"""
    conn.execute(
        "INSERT INTO retro_attributions (batch_id, match_id, league, season,"
        " date, selector, miss_tags_json, primary_tag, tags_confidence,"
        " model_vs_market, evidence_json, digest, status, repaired, harness,"
        " model, duration_s, input_pack_path, tag_set_version, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (batch_id, match_id, "E0", 2023, date, "manual",
         json.dumps(list(tags)), tags[0], 0.5, "model_wrong",
         json.dumps(evidence, ensure_ascii=False), "d", "ok", 0, "hermes",
         None, 0.1, "x", "v1", "2026-09-04T00:00:00Z"))


def test_audit_unparseable_date_is_violation(db):
    """spec §8-1：证据须可证早于开球——不可解析一律违规，不靠字典序误打误撞。

    0000-00-00 字典序早于比赛日（旧实现静默放行）；2024/04/19 因 `/`>`-`
    被误判「不早于」（旧实现理由错）。另含富格式 ISO 时间戳应放行的容忍臂。
    """
    conn = connect(db)
    conn.execute(
        "INSERT INTO retro_runs (id, selector, params_json, n_selected, n_ok,"
        " n_parse_fail, n_timeout, n_error, duration_s, created_at)"
        " VALUES (1, 'manual', '{}', 3, 3, 0, 0, 0, 0.3, 'now')")
    _attr(conn, 1, 1, "2024-04-20",
          [{"title": "幻觉日期", "date": "0000-00-00", "url": "https://e/1"}])
    _attr(conn, 1, 2, "2024-04-21",
          [{"title": "斜杠日期", "date": "2024/04/19", "url": "https://e/2"}])
    # 富格式（ISO 时间戳）取前 10 字符可解析且早于比赛日 → 放行
    _attr(conn, 1, 3, "2024-04-22",
          [{"title": "富格式", "date": "2024-04-21T10:00:00Z",
            "url": "https://e/3"}])
    conn.commit()
    conn.close()
    a = runner.invoke(app, ["retro", "audit"])
    assert a.exit_code == 0
    assert "检查 3 行" in a.output and "违规 2" in a.output
    assert "不可解析" in a.output
    assert "0000-00-00" in a.output and "2024/04/19" in a.output


def test_runs_lists_ledger(db, tmp_path, monkeypatch):
    from fa.retro import pipeline
    monkeypatch.setattr(
        pipeline, "run_headless",
        lambda p: {"ok": False, "output": "", "error": "hermes -z 超时（300s）",
                   "timeout": True, "duration_s": 300.0})
    runner.invoke(app, ["retro", "run", "--selector", "manual",
                        "--matches", "1", "--out-root", str(tmp_path / "p")])
    r = runner.invoke(app, ["retro", "runs"])
    assert r.exit_code == 0
    assert "manual" in r.output and "timeout=1" in r.output


# ---- ensemble CLI（--attributors / fa retro consistency，计划 Task 4）----

_E1 = json.dumps({
    "miss_tags": ["injury", "motivation"], "primary_tag": "injury",
    "tags_confidence": 0.8, "model_vs_market": "model_wrong",
    "evidence": [{"title": "t", "date": "2024-04-01",
                  "url": "https://e.com/1"}],
    "digest": "伤停为主因。"}, ensure_ascii=False)
_E2 = json.dumps({
    "miss_tags": ["motivation"], "primary_tag": "motivation",
    "tags_confidence": 0.6, "model_vs_market": "variance",
    "evidence": [{"title": "u", "date": "2024-04-02",
                  "url": "https://e.com/2"}],
    "digest": "动机为主因。"}, ensure_ascii=False)
_E3 = json.dumps({
    "miss_tags": ["news"], "primary_tag": "news",
    "tags_confidence": 0.4, "model_vs_market": "market_wrong",
    "evidence": [{"title": "v", "date": "2024-04-03",
                  "url": "https://e.com/3"}],
    "digest": "消息面为主因。"}, ensure_ascii=False)


def test_run_with_attributors_records_params(db, tmp_path, monkeypatch):
    from fa.retro import pipeline
    outs = iter([_E1, _E1, _E2])                  # 2 票 injury + 1 票 motivation
    monkeypatch.setattr(pipeline, "run_headless",
                        lambda p: {"ok": True, "output": next(outs),
                                   "error": None, "duration_s": 0.1,
                                   "timeout": False})
    r = runner.invoke(app, ["retro", "run", "--selector", "manual",
                            "--matches", "1", "--attributors", "3",
                            "--out-root", str(tmp_path / "p")])
    assert r.exit_code == 0, r.output
    conn = connect(db)
    params = json.loads(conn.execute(
        "SELECT params_json FROM retro_runs").fetchone()["params_json"])
    conn.close()
    assert params["attributors"] == 3


def test_consistency_three_tiers(db, tmp_path, monkeypatch):
    """3 场各 3 成员：一场全同、一场 2:1、一场三票各异 + 1 场全失败。"""
    from fa.retro import pipeline
    seq = iter([_E1, _E1, _E1,        # match1 全同
                _E1, _E1, _E2,        # match2 多数
                _E1, _E2, _E3,        # match3 无多数
               ])                      # match4 全 timeout 在下一个替身
    monkeypatch.setattr(pipeline, "run_headless",
                        lambda p: {"ok": True, "output": next(seq),
                                   "error": None, "duration_s": 0.1,
                                   "timeout": False})
    r = runner.invoke(app, ["retro", "run", "--selector", "manual",
                            "--matches", "1,2,3", "--attributors", "3",
                            "--out-root", str(tmp_path / "p")])
    assert r.exit_code == 0, r.output
    monkeypatch.setattr(pipeline, "run_headless",
                        lambda p: {"ok": False, "output": "",
                                   "error": "hermes -z 超时（300s）",
                                   "duration_s": 1.0, "timeout": True})
    r = runner.invoke(app, ["retro", "run", "--selector", "manual",
                            "--matches", "1", "--attributors", "3",
                            "--out-root", str(tmp_path / "p2")])
    assert r.exit_code == 0, r.output
    c = runner.invoke(app, ["retro", "consistency"])
    assert c.exit_code == 0, c.output
    assert "全同" in c.output and "多数" in c.output and "无多数" in c.output
    assert "成员失败" in c.output
    # 3 个 timeout 场的成员行（三档场成员全 ok）——若有成员契约非法会变 4+，
    # 此行即把「三票各异」是否真发生钉进 CLI 读数。
    assert "成员失败行：3" in c.output


def test_consistency_report_values(db, tmp_path, monkeypatch):
    from fa.retro import pipeline
    from fa.retro.analyze import consistency_report
    seq = iter([_E1, _E1, _E1, _E1, _E1, _E2])
    monkeypatch.setattr(pipeline, "run_headless",
                        lambda p: {"ok": True, "output": next(seq),
                                   "error": None, "duration_s": 0.1,
                                   "timeout": False})
    runner.invoke(app, ["retro", "run", "--selector", "manual",
                        "--matches", "1,2", "--attributors", "3",
                        "--out-root", str(tmp_path / "p")])
    conn = connect(db)
    rep = consistency_report(conn)
    conn.close()
    assert rep["n_matches"] == 2
    assert (rep["unanimous"], rep["majority"], rep["none"]) == (1, 1, 0)
    assert rep["member_failures"] == 0
    assert rep["n_k1"] == 0                        # 三成员场无 k=1 虚高


def test_consistency_discloses_k1_matches(db, tmp_path, monkeypatch):
    """k=1 场（恰 1 个 ok 成员）恒计「全同」，会把默认读数推高——
    consistency 须披露 n_k1 且 CLI 出 ⚠ 行（终审裁定 2026-09-04）。

    构造：ensemble 批（match1/match2 各三成员）与 k=1 批（match3 单成员）共存。"""
    from fa.retro import pipeline
    from fa.retro.analyze import consistency_report
    seq = iter([_E1, _E1, _E1,                      # match1：ensemble 批
                _E1, _E1, _E1,                      # match2：ensemble 批
                _E3])                               # match3：k=1 批
    monkeypatch.setattr(pipeline, "run_headless",
                        lambda p: {"ok": True, "output": next(seq),
                                   "error": None, "duration_s": 0.1,
                                   "timeout": False})
    r = runner.invoke(app, ["retro", "run", "--selector", "manual",
                            "--matches", "1,2", "--attributors", "3",
                            "--out-root", str(tmp_path / "p3")])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["retro", "run", "--selector", "manual",
                            "--matches", "3", "--attributors", "1",
                            "--out-root", str(tmp_path / "p1")])
    assert r.exit_code == 0, r.output
    conn = connect(db)
    try:
        rep = consistency_report(conn)
    finally:
        conn.close()
    assert rep["n_matches"] == 3                   # k=1 场仍入三档（恒全同）
    assert rep["unanimous"] == 3 and rep["n_k1"] == 1
    c = runner.invoke(app, ["retro", "consistency"])
    assert c.exit_code == 0, c.output
    assert "⚠ 含 k=1 场 1" in c.output


# ---- 关卡 3：分层检验（设计 §8-3）----

# 赛前成因标签行的合规证据（date 早于比赛日 2024-04-20）——本文件既有
# `_seed`（tests.retro.test_select）三场 div = +0.1446/−0.2049/−0.0741，
# 已由 db fixture 落库，此处只补归因行
_EV_PRE = json.dumps([{"title": "赛前伤停名单", "date": "2024-04-19",
                       "url": "https://e.com/pre"}])


class TestAnalyze:
    def _seed_attrib(self, conn, match_id, tags, evidence="[]",
                     date_="2024-04-20", is_control=0, batch_id=1):
        # batch_id 外键→retro_runs（connect 开 PRAGMA foreign_keys=ON），先补父行
        conn.execute(
            "INSERT OR IGNORE INTO retro_runs (id, selector, params_json,"
            " n_selected, n_ok, n_parse_fail, n_timeout, n_error, duration_s,"
            " created_at) VALUES (1, 'manual', '{}', 0, 0, 0, 0, 0, 0.0,"
            " 'now')")
        conn.execute(
            "INSERT INTO retro_attributions (batch_id, match_id, league,"
            " season, date, selector, attributor, miss_tags_json,"
            " primary_tag, tags_confidence, model_vs_market,"
            " evidence_json, digest, status, repaired, harness, model,"
            " duration_s, input_pack_path, tag_set_version, created_at,"
            " is_control) VALUES (?,?, 'E0',2024,?, 'manual',1,?,"
            " ?,0.7,'model_wrong',?,'d','ok',0,'hermes',NULL,1.0,'p',"
            " 'v1','2026-09-04T00:00:00Z',?)",
            (batch_id, match_id, date_, json.dumps(tags), tags[0],
             evidence, is_control))
        conn.commit()

    def test_stratified_point_estimates_handchecked(self, db):
        """手算口径：_seed 三场 div = +0.1446 / −0.2049 / −0.0741。
        归因行：match1 标 injury（div 0.1446），match2/3 无 injury 标签
        （div −0.2049/−0.0741）→ injury 层均值 0.1446，对照层均值
        (−0.2049 + −0.0741)/2 = −0.1395。"""
        conn = connect(db)
        self._seed_attrib(conn, 1, ["injury"], evidence=_EV_PRE)
        self._seed_attrib(conn, 2, ["variance"])
        self._seed_attrib(conn, 3, ["variance"])
        try:
            res = stratified_analysis(conn)
        finally:
            conn.close()
        t = res["per_tag"]["injury"]
        assert t["n"] == 1 and t["mean_div"] == pytest.approx(0.1446, abs=1e-4)
        assert t["mean_div_rest"] == pytest.approx(-0.1395, abs=1e-4)
        v = res["per_tag"]["variance"]
        assert v["n"] == 2 and v["n_rest"] == 1
        assert res["n_excluded"] == 0

    def test_violating_rows_excluded_by_default(self, db):
        conn = connect(db)
        self._seed_attrib(conn, 1, ["injury"],
                          evidence=json.dumps(
                              [{"title": "t", "date": "2024-04-21",
                                "url": "u"}]))          # 不早于比赛日 → 违规
        self._seed_attrib(conn, 2, ["variance"])
        try:
            res = stratified_analysis(conn)              # 默认剔除
            assert res["n_excluded"] == 1
            assert res["per_tag"]["injury"]["n"] == 0
            res2 = stratified_analysis(conn, include_violations=True)
        finally:
            conn.close()
        assert res2["n_excluded"] == 0
        assert res2["per_tag"]["injury"]["n"] == 1

    def test_case_control_counts_split(self, db):
        conn = connect(db)
        self._seed_attrib(conn, 1, ["injury"], is_control=0, evidence=_EV_PRE)
        self._seed_attrib(conn, 2, ["injury"], is_control=1, evidence=_EV_PRE)
        try:
            t = stratified_analysis(conn)["per_tag"]["injury"]
        finally:
            conn.close()
        assert (t["n"], t["n_case"], t["n_control"]) == (2, 1, 1)

    def test_mwu_small_layer_gives_none_or_pvalue(self, db):
        """n<2 的层无法检验——p_value=None，不抛错（小样本如实降格）。"""
        conn = connect(db)
        self._seed_attrib(conn, 1, ["injury"], evidence=_EV_PRE)
        try:
            res = stratified_analysis(conn)
        finally:
            conn.close()
        assert res["per_tag"]["injury"]["p_value"] is None

    def test_cli_analyze_outputs_tag_lines(self, db):
        conn = connect(db)
        self._seed_attrib(conn, 1, ["injury"], evidence=_EV_PRE)
        conn.close()
        result = runner.invoke(app, ["retro", "analyze"])
        assert result.exit_code == 0
        assert "injury" in result.output and "分层" in result.output

    def test_null_tags_row_skipped_not_fatal(self, db):
        """status='ok' 但 miss_tags_json 为 NULL：与 audit_batch 同一过滤
        （IS NOT NULL）——无标签集无从分层，不入 n_rows，命令不崩。"""
        conn = connect(db)
        self._seed_attrib(conn, 1, ["injury"], evidence=_EV_PRE)
        self._seed_attrib(conn, 2, ["variance"])
        conn.execute(
            "UPDATE retro_attributions SET miss_tags_json=NULL"
            " WHERE match_id=2")
        conn.commit()
        try:
            res = stratified_analysis(conn)
        finally:
            conn.close()
        assert res["n_rows"] == 1                       # NULL tags 行不入分层
        result = runner.invoke(app, ["retro", "analyze"])
        assert result.exit_code == 0
        assert "injury" in result.output and "variance" not in result.output

    def test_mwu_all_ties_returns_none_not_nan(self, db):
        """全平手（两组值全同）且两侧 n≥8（asymptotic）→ 双侧 p=nan，
        如实记 None——不让 nan 漏进渲染成 p=nan。"""
        assert _mwu([1.0] * 8, [1.0] * 8) is None
