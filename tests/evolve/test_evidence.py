"""证据聚合：窗口归属/三轨统计/误杀对照/轨间分歧/未结算单列（设计档 §7）。

种子走 tests/evolve/conftest.py 的 ``seed_window_rows``（Ruling 4：唯一事实源，
T13 E2E 复用同款）；fixture 3 / 窗 3 的编排是本文件的场景层。
"""
import json

from fa.evolve import windows
from fa.evolve.evidence import window_evidence

DAY1 = "2026-09-05"          # 窗 1 内（[2026-09-04, 2026-10-16)）


def _seed_full(conn):
    """窗 1 主场景 + 窗 3 对照 run——验证窗口互不吃串。

    run#1（窗 1，2026-09-05）：
      - fixture 1 三轨（kb veto / nokb agree）+ model_only 已结算 won 注
        （stake 10 / return 22 / clv 0.05 → ROI = (22-10)/10 = 1.2）
        + nokb 轨 void 注（计 n_bets、不进 ROI）
      - fixture 3 kb 轨**未判决**行（verdict NULL）挂 pending 注
        （不进 ROI、进 n_pending_settlement；也不计 n_judged——计 n_fixtures）
    run#2（2027-01-01，窗 3 [2026-11-27, 2027-01-08)）：fixture 2 三轨。
    """
    from tests.evolve.conftest import (_mk_bet, _mk_fixture, _mk_rec,
                                       seed_window_rows)
    s = seed_window_rows(conn)                       # run#1 / fixture 1
    _mk_bet(conn, s["nokb"], status="void", stake=10.0, day=DAY1)
    rec3 = _mk_rec(conn, s["run_id"], _mk_fixture(conn, 3, day=DAY1),
                   "model_persona", day=DAY1)        # verdict 留 NULL
    _mk_bet(conn, rec3, status="pending", stake=10.0, day=DAY1)
    seed_window_rows(conn, day="2027-01-01", fixture_id=2)   # 窗 3
    return s


def test_window_evidence_shape_and_semantics(conn):
    _seed_full(conn)
    ev = window_evidence(conn, windows.window_bounds(1), "E0")
    assert ev["league"] == "E0"
    assert ev["window"] == {"idx": 1, "from": "2026-09-04", "to": "2026-10-16"}
    # kb 轨：两场入库（fixture 1 + 3）、一场有判决（未判决行不计 judged）
    assert ev["kb_track"]["n_fixtures"] == 2
    assert ev["kb_track"]["n_judged"] == 1
    assert ev["kb_track"]["verdicts"] == {"agree": 0, "downweight": 0, "veto": 1}
    assert ev["nokb_track"]["verdicts"] == {"agree": 1, "downweight": 0, "veto": 0}
    assert ev["model_only_ref"]["n_settled"] == 1
    assert ev["model_only_ref"]["roi"] == 1.2          # (22-10)/10
    # pending 注挂在 kb 轨 fixture 3 的未判决行上：计 n_bets、不进 ROI
    assert ev["kb_track"]["n_bets"] == 1
    assert ev["kb_track"]["n_settled"] == 0
    assert ev["kb_track"]["roi"] is None
    assert ev["kb_track"]["clv_median"] is None
    assert ev["kb_track"]["clv_mean"] is None
    assert ev["n_pending_settlement"] == 1
    # void 注：settled = won/lost，void 不进 ROI、也不进 pending 计数
    assert ev["nokb_track"]["n_bets"] == 1
    assert ev["nokb_track"]["n_settled"] == 0
    assert ev["nokb_track"]["roi"] is None
    # 误杀明细：kb veto 场对照 model_only 同 (fixture, market) 已结算注
    assert len(ev["kills"]) == 1
    kill = ev["kills"][0]
    assert kill["fixture_id"] == 1 and kill["kb_verdict"] == "veto"
    assert kill["nokb_verdict"] == "agree"
    assert kill["kb_final_frac"] == 0.0
    assert kill["mo_return_on_stake"] == 1.2           # (22-10)/10
    assert kill["market"] == "H"
    assert kill["date"] == "2026-09-05"
    assert kill["home"] == "Home1" and kill["away"] == "Away1"
    div = ev["divergences"][0]
    assert div["fixture_id"] == 1
    assert div["kb_verdict"] == "veto" and div["nokb_verdict"] == "agree"
    # 窗口互不吃串：窗 3 只见 fixture 2（kb veto → 同样有 kill/分歧）
    ev3 = window_evidence(conn, windows.window_bounds(3), "E0")
    assert ev3["kb_track"]["n_fixtures"] == 1
    assert ev3["kb_track"]["n_judged"] == 1
    assert [k["fixture_id"] for k in ev3["kills"]] == [2]
    assert [d["fixture_id"] for d in ev3["divergences"]] == [2]
    assert ev3["n_pending_settlement"] == 0
    # json 兼容（evidence.json 落盘走 json.dumps）
    json.dumps(ev, ensure_ascii=False)


def test_window_evidence_empty_league(conn):
    _seed_full(conn)
    ev = window_evidence(conn, windows.window_bounds(1), "SP1")
    assert ev["kb_track"]["n_judged"] == 0 and ev["kills"] == []
    assert ev["kb_track"]["roi"] is None and ev["kb_track"]["clv_median"] is None
    assert ev["kb_track"]["n_fixtures"] == 0
    assert ev["kb_track"]["verdicts"] == {"agree": 0, "downweight": 0, "veto": 0}
    assert ev["model_only_ref"] == {"n_bets": 0, "n_settled": 0, "roi": None}
    assert ev["divergences"] == [] and ev["n_pending_settlement"] == 0


def test_divergence_delta_only_and_unaligned_kill(conn):
    """verdict 同而 confidence_delta 异（>1e-9）也计分歧；未对齐场 kill 的
    队名/对照走 NULL/负收益兜底。"""
    from tests.evolve.conftest import _mk_bet, _mk_fixture, _mk_rec, _mk_run
    run1 = _mk_run(conn)
    _mk_rec(conn, run1, _mk_fixture(conn, 11), "model_persona",
            verdict="agree", confidence_delta=0.10)
    _mk_rec(conn, run1, 11, "model_persona_nokb", verdict="agree",
            confidence_delta=0.30)
    _mk_rec(conn, run1, _mk_fixture(conn, 12), "model_persona",
            verdict="agree", confidence_delta=0.0)
    _mk_rec(conn, run1, 12, "model_persona_nokb", verdict="agree",
            confidence_delta=0.0)
    # 未对齐场（主客 NULL）：kb downweight 是 kill；对照注 lost
    _mk_fixture(conn, 13, aligned=False)
    _mk_rec(conn, run1, 13, "model_persona", verdict="downweight",
            confidence_delta=0.0, final_stake_frac=0.01)
    _mk_bet(conn, _mk_rec(conn, run1, 13, "model_only"), status="lost",
            stake=10.0, return_amt=0.0)
    ev = window_evidence(conn, windows.window_bounds(1), "E0")
    assert [d["fixture_id"] for d in ev["divergences"]] == [11]
    assert (ev["divergences"][0]["kb_conf_delta"],
            ev["divergences"][0]["nokb_conf_delta"]) == (0.10, 0.30)
    assert [k["fixture_id"] for k in ev["kills"]] == [13]
    assert ev["kills"][0]["kb_verdict"] == "downweight"
    assert ev["kills"][0]["nokb_verdict"] is None      # 该场无 nokb 行
    assert ev["kills"][0]["mo_return_on_stake"] == -1.0    # (0-10)/10
    assert ev["kills"][0]["home"] is None and ev["kills"][0]["away"] is None
    assert ev["kb_track"]["verdicts"] == {"agree": 2, "downweight": 1, "veto": 0}
    assert ev["model_only_ref"]["roi"] == -1.0
