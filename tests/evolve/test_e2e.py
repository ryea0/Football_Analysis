"""M6 脚手架 E2E：完整闭环 + 三降级 + 快照钉版（设计档 §11.2，验收判据）。

闭环（test_full_loop_*，一个函数走完）：tmp 项目根（真 personas/epl.md + git
init）→ 种子 w1 三轨判决 → ``run_tick``（HERMES_BIN 契约 fixture）→ 反思 +
提案 + 报告 → ``merge_proposal`` 落活文件 + Ruling → 快照钉版（w2 快照建在
merge 前仍是旧/空内容，w3 快照建在 merge 后含新条目）→ 新 run 的 persona
双轨（kb prompt 带新知识条目、nokb 不带）→ 全库 personas_hash 新旧并存。

三降级各一函数（§6.5）：反思超时（status=timeout、知识文件与暂存区零变化）、
契约破损（evidence.fixtures 空 → status=contract）、关卡拒绝（活文件不动、
Ruling 在案、gate_closed 终态语义）。

时间缝：``windows.beijing_today`` 是唯一时钟（evolve 全链经它走），测试只
monkeypatch 本函数——断言不依赖真实日期。窗口几何（锚点 2026-09-04、42 天）：
w1=[09-04,10-16) w2=[10-16,11-27) w3=[11-27,2027-01-08)。
"""
import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from fa import config
from fa.evolve import apply as A, knowledge as K, runner, windows
from fa.persona.apply import run_persona_phase
from tests.evolve.conftest import seed_window_rows

# 合法反思契约：教训段 append（§4 分段规则 L：date 必带、ttl 禁带）+
# evidence.fixtures 引台账真实场次 id（防事后诸葛强制项，§6.3 校验六条）
CONTRACT = {"league": "E0",
            "appends": [{"section": "教训", "text": "密集期 downweight 需更谨慎",
                         "date": "2026-10-16", "ttl_days": None,
                         "evidence": {"fixtures": [1], "stat": "2 注误杀"}}],
            "amendments": [], "deprecations": [], "no_change_reason": None}


@pytest.fixture
def root(tmp_path, monkeypatch):
    """真 personas/epl.md 拷贝 + git init（``git_aux`` 有值）+ project_root 重定向。

    只拷 epl.md：反思对无判决样本的联赛零样本短路，其余四联赛不需要人格文件。
    """
    repo = Path(__file__).resolve().parents[2]
    personas = tmp_path / "personas"
    personas.mkdir()
    shutil.copy2(repo / "personas" / "epl.md", personas / "epl.md")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True,
                   capture_output=True)
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def db(root, tmp_path, monkeypatch):
    """FA_DB 指向 tmp 库；w1 内种三轨判决行（fixture 1：kb 轨 veto / nokb 轨
    agree / model_only 已结算注——conftest ``seed_window_rows`` 缺省场景，
    Ruling 4 的窗口种子唯一事实源）。"""
    p = tmp_path / "e2e.db"
    monkeypatch.setenv("FA_DB", str(p))
    from fa import db as dbmod
    dbmod.init_db(p)
    conn = dbmod.connect(p)
    seed_window_rows(conn)          # w1（2026-09-05）fixture 1 三轨 + 结算注
    return conn


def _hermes_contract(root, name="hermes_contract.sh", contract=CONTRACT):
    """恒回合法反思契约的 fixture 脚本（单引号包 JSON——bash 不拆引号）。"""
    s = root / name
    s.write_text("#!/usr/bin/env bash\necho '" + json.dumps(contract) + "'\n",
                 encoding="utf-8")
    s.chmod(0o755)
    return str(s)


def _hermes_seq_dumper(root, seq_file, kb_file, nokb_file):
    """按调用序把 prompt 落盘（1→kb、2→nokb），回合法 persona JSON。

    prompt 走 argv 第 2 参（``hermes -z <prompt> -t search``，caller.build_command
    唯一事实源）——与实跑同一代码路径（C1），不做任何额外 mock。
    """
    s = root / "hermes_seq.sh"
    s.write_text(
        "#!/usr/bin/env bash\n"
        f'n=$(cat "{seq_file}" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "{seq_file}"\n'
        f'if [ "$n" = 1 ]; then printf \'%s\' "$2" > "{kb_file}";'
        f' else printf \'%s\' "$2" > "{nokb_file}"; fi\n'
        'echo \'{"verdict":"agree","confidence_delta":0.0,"key_factors":["x"],'
        '"report_md":"r"}\'\n', encoding="utf-8")
    s.chmod(0o755)
    return str(s)


def test_full_loop_merge_snapshot_and_prompt(root, db, monkeypatch):
    # ① w2 开始（w1 已收口）：先建 w2 快照 = merge 前的旧/空知识，钉死本窗读数
    K.ensure_window_snapshot(2)
    monkeypatch.setattr(windows, "beijing_today", lambda: date(2026, 10, 20))
    monkeypatch.setenv("HERMES_BIN", _hermes_contract(root))
    out = runner.run_tick(db)
    assert "w1" in out and "E0=ok" in out
    # 提案三件套落暂存区，活文件未被触碰（R3）
    assert (root / "evolution" / "proposals" / "w1" / "E0.json").is_file()
    assert not K.kb_path("E0").exists()
    # ② 人审关卡：merge 原子落账（先写活文件、后单事务 Ruling + 关窗刷新）
    wid = db.execute("SELECT id FROM evolution_windows WHERE idx=1"
                     ).fetchone()["id"]
    msg = A.merge_proposal(db, wid, "E0", note="证据链核实通过")
    assert "w1" in msg and "E0" in msg
    live = K.kb_path("E0").read_text(encoding="utf-8")
    assert "- [E0-L01|2026-10-16] 密集期 downweight 需更谨慎" in live  # §4 语法
    ruling = db.execute("SELECT ruling, kb_hash_after, note FROM evolution_rulings"
                        ).fetchone()
    assert ruling["ruling"] == "merged" and ruling["note"] == "证据链核实通过"
    assert ruling["kb_hash_after"] == K.personas_tree_hash(root / "personas")
    assert db.execute("SELECT closed_at FROM evolution_windows WHERE id=?",
                      (wid,)).fetchone()["closed_at"] is not None
    # ③ 快照钉版（设计档 §4/R6）：w2 快照建在 merge 前 → 旧（空）内容；
    #    w3 快照建在 merge 后 → 含新条目
    assert K.window_kb_text(2, "E0") in (None, "")
    K.ensure_window_snapshot(3)
    assert "密集期" in K.window_kb_text(3, "E0")
    # ④ 事件报告落盘（§10：以 closes 日命名）
    assert (root / "docs" / "evolution" / "report-2026-10-16.md").is_file()
    # ⑤ 新 run 的 persona 双轨（§12.7）：kb 轨 prompt 带新知识条目、nokb 不带。
    #    today 取 w3（[2026-11-27, 2027-01-08)）——run 时建/复用当前窗快照，
    #    该快照已在 merge 后建好，故 kb 轨读到的是新条目（快照钉版的 B 线面）。
    monkeypatch.setattr(windows, "beijing_today", lambda: date(2026, 12, 5))
    run2 = seed_window_rows(db, day="2026-12-05", fixture_id=2)["run_id"]
    new_hash = K.personas_tree_hash(root / "personas")
    db.execute("UPDATE recommendations SET personas_hash=? WHERE run_id=?",
               (new_hash, run2))            # B 线落行时的版本戳（value.py 同值）
    db.commit()
    monkeypatch.setenv("HERMES_BIN", _hermes_seq_dumper(
        root, root / "seq", root / "kb.prompt", root / "nokb.prompt"))
    res = run_persona_phase(db, run2, ["E0"])
    assert res["called"] == 1 and res["ok"] == 1          # 一场 × kb 轨一次
    assert res["nokb_called"] == 1 and res["nokb_ok"] == 1
    assert res["degraded"] == [] and res["nokb_degraded"] == []
    assert res["veto"] == 0 and res["nokb_veto"] == 0
    kb_prompt = (root / "kb.prompt").read_text(encoding="utf-8")
    nokb_prompt = (root / "nokb.prompt").read_text(encoding="utf-8")
    # 鉴别标记用**完整条目正文**：真人格文件 epl.md 本身含「密集期」字样
    # （密集期的联赛排阵…），只有新条目整句才是 kb 轨独有的注入内容
    ENTRY = "密集期 downweight 需更谨慎"
    assert "联赛知识库（快照 w3）" in kb_prompt and ENTRY in kb_prompt
    assert "联赛知识库" not in nokb_prompt and ENTRY not in nokb_prompt
    # ⑥ 全库版本戳：新旧行并存（w1 行 NULL=纪元前、新 run 行 64 hex）
    assert len(new_hash) == 64
    assert db.execute("SELECT COUNT(*) AS n FROM recommendations"
                      " WHERE personas_hash IS NULL").fetchone()["n"] > 0
    assert db.execute("SELECT COUNT(*) AS n FROM recommendations WHERE"
                      " personas_hash=?", (new_hash,)).fetchone()["n"] > 0


def test_degradation_reflect_timeout(root, db, monkeypatch):
    """反思超时：status=timeout，知识文件与暂存区零变化（§6.5 降级语义）。"""
    s = root / "hermes_sleep.sh"
    s.write_text("#!/usr/bin/env bash\nsleep 5\n")
    s.chmod(0o755)
    monkeypatch.setenv("HERMES_BIN", str(s))
    monkeypatch.setenv("FA_PERSONA_TIMEOUT", "1")
    monkeypatch.setattr(windows, "beijing_today", lambda: date(2026, 10, 20))
    runner.run_tick(db)
    row = db.execute("SELECT status FROM evolution_runs WHERE league='E0'"
                     ).fetchone()
    assert row["status"] == "timeout"
    assert not K.kb_path("E0").exists()
    assert not (root / "evolution" / "proposals" / "w1" / "E0.json").exists()
    assert db.execute("SELECT COUNT(*) FROM evolution_rulings").fetchone()[0] == 0


def test_degradation_contract_broken(root, db, monkeypatch):
    """契约破损：合法 JSON 但 evidence.fixtures 为空 → status=contract 且留理由。"""
    bad = dict(CONTRACT)
    bad["appends"] = [dict(CONTRACT["appends"][0],
                           evidence={"fixtures": [], "stat": ""})]
    monkeypatch.setenv("HERMES_BIN", _hermes_contract(root, "hermes_bad.sh",
                                                      bad))
    monkeypatch.setattr(windows, "beijing_today", lambda: date(2026, 10, 20))
    runner.run_tick(db)
    row = db.execute("SELECT status, no_change_reason FROM evolution_runs"
                     " WHERE league='E0'").fetchone()
    assert row["status"] == "contract" and row["no_change_reason"]
    assert not K.kb_path("E0").exists()
    assert not (root / "evolution" / "proposals").exists()


def test_degradation_gate_reject(root, db, monkeypatch):
    """关卡拒绝：活文件不动、Ruling 在案、全联赛终态后 gate_closed 成立。"""
    monkeypatch.setattr(windows, "beijing_today", lambda: date(2026, 10, 20))
    monkeypatch.setenv("HERMES_BIN", _hermes_contract(root))
    runner.run_tick(db)
    wid = db.execute("SELECT id FROM evolution_windows WHERE idx=1"
                     ).fetchone()["id"]
    rid = db.execute("SELECT id FROM evolution_runs WHERE window_id=? AND"
                     " league='E0'", (wid,)).fetchone()["id"]
    A.record_ruling(db, rid, "rejected", note="证据引用与台账不符")
    assert not K.kb_path("E0").exists()          # 拒绝 = 活文件不动
    assert db.execute("SELECT ruling FROM evolution_rulings WHERE run_id=?",
                      (rid,)).fetchone()["ruling"] == "rejected"
    assert A.gate_closed(db, 1)                  # 五联赛全终态 → 关卡关
    assert db.execute("SELECT closed_at FROM evolution_windows WHERE id=?",
                      (wid,)).fetchone()["closed_at"] is not None
