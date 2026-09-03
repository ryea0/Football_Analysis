"""实时侧队名对齐测试（spec §3.3：归一化精确命中 → 模糊高分自动别名 → 隔离表）。

阈值两侧的样例都经**手工验算**（M2 教训：阈值可达性要自己算，不能只看结果）：

- 命中侧 ``Bayern München`` → 归一化 ``bayernmunchen``(13) vs ``bayernmunich``(12)：
  匹配块 ``bayernmun``(9) + ``ch``(2) → M=11 → 2*11/(13+12) = 22/25 = **0.88 ≥ 0.87**
- 隔离侧 ``Real Sociedad`` → ``realsociedad``(12) vs 最接近的 ``realmadrid``(10)：
  匹配块 ``real``(4) + ``ad``(2) → M=6 → 2*6/(12+10) = 12/22 ≈ **0.5455 < 0.87**

真实语料的边界（M1 库实测）：D1 里 ``M'Gladbach`` 与 ``M'gladbach`` 仅大小写之差、
归一化同形 ``mgladbach`` 但 team_id 不同 → 归一化命中**歧义**，不得自动猜。
"""
import difflib

import pytest
from typer.testing import CliRunner

from fa.cli import app
from fa.data.teams import get_or_create_team, record_unknown, resolve_team
from fa.db import connect, init_db
from fa.pipeline.align import (
    AUTO_ACCEPT_RATIO,
    align_fixture_teams,
    normalize_name,
    rank_candidates,
    suggest_alias,
)

runner = CliRunner()

D1 = ("Bayern Munich", "Dortmund", "Freiburg")
SP1 = ("Atletico Madrid", "Real Madrid", "Barcelona")


@pytest.fixture
def conn(tmp_path):
    init_db(tmp_path / "t.db")
    c = connect(tmp_path / "t.db")
    for lg, names in (("D1", D1), ("SP1", SP1)):
        for n in names:
            get_or_create_team(c, lg, n)
    c.commit()
    yield c
    c.close()


def alias_rows(conn, source="oddsapi"):
    return conn.execute(
        "SELECT team_id, source, alias FROM team_aliases WHERE source=? ORDER BY alias",
        (source,)).fetchall()


def unknown_rows(conn, source="oddsapi"):
    return [r["name"] for r in conn.execute(
        "SELECT name FROM unknown_names WHERE source=? ORDER BY name", (source,))]


def bayern_id(conn):
    return conn.execute(
        "SELECT id FROM teams WHERE league='D1' AND name='Bayern Munich'").fetchone()["id"]


# ---------------------------------------------------------------- normalize_name

@pytest.mark.parametrize("raw, want", [
    ("Bayern München", "bayernmunchen"),          # NFKD 去变音符（ü→u）
    ("Atlético Madrid", "atleticomadrid"),        # é→e
    ("Paris Saint-Germain", "parissaintgermain"),  # 连字符去除
    ("M'Gladbach", "mgladbach"),                  # 撇号去除
    ("Schalke 04", "schalke04"),                  # 数字保留
    ("  Man City  ", "mancity"),                  # 空白去除
])
def test_normalize_name(raw, want):
    assert normalize_name(raw) == want


def test_normalize_name_empty_and_symbols():
    assert normalize_name("") == ""
    assert normalize_name("!?") == ""


# ---------------------------------------------------------------- suggest_alias


def test_auto_accept_threshold_is_spec_value():
    """0.87 是 brief/spec 钉死的自动接受线——被改动即视为改设计，须先改 spec。"""
    assert AUTO_ACCEPT_RATIO == 0.87


def test_suggest_exact_canonical_and_existing_alias(conn):
    tid = bayern_id(conn)
    conn.execute(
        "INSERT INTO team_aliases (team_id, source, alias) VALUES (?, 'oddsapi', ?)",
        (tid, "FC Hollywood"))
    conn.commit()
    assert suggest_alias(conn, "D1", "Bayern Munich") == tid
    assert suggest_alias(conn, "D1", "FC Hollywood") == tid
    assert len(alias_rows(conn)) == 1                      # 精确命中不新增别名行


def test_suggest_normalized_hit_writes_alias_idempotently(conn):
    tid = conn.execute(
        "SELECT id FROM teams WHERE league='SP1' AND name='Atletico Madrid'").fetchone()["id"]
    assert normalize_name("Atlético Madrid") == normalize_name("Atletico Madrid")
    assert suggest_alias(conn, "SP1", "Atlético Madrid") == tid
    rows = alias_rows(conn)
    assert [(r["team_id"], r["alias"]) for r in rows] == [(tid, "Atlético Madrid")]
    # 重复调用：幂等（alias UNIQUE 不炸、不重复入行），返回同一 team_id
    assert suggest_alias(conn, "SP1", "Atlético Madrid") == tid
    assert len(alias_rows(conn)) == 1
    assert unknown_rows(conn) == []


def test_suggest_fuzzy_hit_auto_accepts_above_threshold(conn):
    """2*11/25 = 0.88 ≥ 0.87（次优仅 0.38，无并列）→ 自动写别名并返回 team_id。"""
    tid = bayern_id(conn)
    probe = "Bayern München"
    ratio = difflib.SequenceMatcher(
        None, normalize_name(probe), normalize_name("Bayern Munich")).ratio()
    assert ratio == pytest.approx(0.88)                    # 手工验算：22/25
    assert ratio >= AUTO_ACCEPT_RATIO
    assert suggest_alias(conn, "D1", probe) == tid
    assert [(r["team_id"], r["alias"]) for r in alias_rows(conn)] == [(tid, probe)]
    assert unknown_rows(conn) == []                        # 命中不进隔离表


def test_suggest_below_threshold_quarantines(conn):
    """2*6/22 ≈ 0.5455 < 0.87 → 不写别名、入隔离表、返回 None（绝不静默丢弃）。"""
    probe = "Real Sociedad"
    best = rank_candidates(conn, probe, league="SP1")[0]
    # 手工验算：块 real(4)+ad(2) → 2*6/22 = 12/22
    assert best.ratio == pytest.approx(0.545455, abs=1e-5)
    assert best.ratio < AUTO_ACCEPT_RATIO
    assert suggest_alias(conn, "SP1", probe) is None
    assert alias_rows(conn) == []
    assert unknown_rows(conn) == [probe]
    conn.commit()                                          # record_unknown 不提交，由调用方提交
    again = suggest_alias(conn, "SP1", probe)
    assert again is None
    assert unknown_rows(conn) == [probe]                   # 隔离表幂等


def test_suggest_ambiguous_normalized_hit_never_guesses(conn):
    """真实语料：D1 的 M'Gladbach / M'gladbach 归一化同形但 team_id 不同 → 隔离，不猜。

    M1 库里这两个名字都在 D1（football-data 历史拼写差异，仅大小写之别）。
    探测名用 ``M Gladbach``（空格而非撇号，归一化同为 ``mgladbach``、原文非
    canonical）——归一化命中两个 team_id 即歧义，宁可隔离交人工也不猜。
    """
    a = get_or_create_team(conn, "D1", "M'Gladbach")
    b = get_or_create_team(conn, "D1", "M'gladbach")
    conn.commit()
    assert a != b and normalize_name("M'Gladbach") == normalize_name("M Gladbach")
    assert suggest_alias(conn, "D1", "M Gladbach") is None
    assert alias_rows(conn) == []
    assert unknown_rows(conn) == ["M Gladbach"]
    # 歧义的两个 canonical 名本身仍走精确命中（不误伤）
    assert suggest_alias(conn, "D1", "M'Gladbach") == a
    assert suggest_alias(conn, "D1", "M'gladbach") == b


def test_suggest_normalized_ambiguity_never_falls_through_to_fuzzy(conn):
    """归一化命中 >1 个 team_id 时**立即隔离**，不得穿透到模糊路径。

    复现审查场景：canonical ``M'Gladbach``=t1 + 人工确认别名 ``Mgladbach``→t2，
    两者归一化同形 ``mgladbach``。探测名 ``M. Gladbach``（空格+缩写点）同样归一化
    为 ``mgladbach`` → 命中 {t1, t2} 歧义。修复前会穿透到模糊路径、以 ratio 1.0
    独占最高分自动写 ``M. Gladbach``→t1 ——结果两个 club 各持一条归一化同形的
    别名（t1: M. Gladbach / t2: Mgladbach），且隔离表**零痕迹**。
    """
    t1 = get_or_create_team(conn, "D1", "M'Gladbach")
    t2 = get_or_create_team(conn, "D1", "Dortmund")
    conn.execute(
        "INSERT INTO team_aliases (team_id, source, alias) VALUES (?, 'oddsapi', ?)",
        (t2, "Mgladbach"))
    conn.commit()
    assert t1 != t2
    assert normalize_name("M. Gladbach") == normalize_name("M'Gladbach") == "mgladbach"

    assert suggest_alias(conn, "D1", "M. Gladbach") is None      # 不猜，立即隔离
    assert [(r["team_id"], r["alias"]) for r in alias_rows(conn)] == \
        [(t2, "Mgladbach")]                                      # 未新写任何别名
    assert unknown_rows(conn) == ["M. Gladbach"]                 # 隔离表留痕
    # 歧义两侧既有绑定不受影响，且人工确认路径仍可消化
    assert resolve_team(conn, "D1", "M. Gladbach", "oddsapi") is None


def test_suggest_never_raises_on_garbage(conn):
    for probe in ("", "   ", "!?", "——"):
        assert suggest_alias(conn, "D1", probe) is None
    assert unknown_rows(conn) == ["", "   ", "!?", "——"]    # 隔离而非丢弃
    assert alias_rows(conn) == []


def test_suggest_is_league_scoped(conn):
    """模糊只在给定联赛内取最高分：D1 名不落进 SP1 的 Real Madrid。"""
    assert suggest_alias(conn, "D1", "Real Betis") is None
    assert [(r["team_id"], r["alias"]) for r in alias_rows(conn)] == []


# ---------------------------------------------------------------- align_fixture_teams


def test_align_fixture_teams_both_sides(conn):
    atleti = conn.execute(
        "SELECT id FROM teams WHERE league='SP1' AND name='Atletico Madrid'").fetchone()["id"]
    real = conn.execute(
        "SELECT id FROM teams WHERE league='SP1' AND name='Real Madrid'").fetchone()["id"]
    assert align_fixture_teams(conn, "SP1", "Atlético Madrid", "Real Madrid") == (atleti, real)


def test_align_fixture_teams_partial_and_total_failure_never_raises(conn):
    assert align_fixture_teams(conn, "D1", "Bayern München", "Real Sociedad") == \
        (bayern_id(conn), None)
    assert align_fixture_teams(conn, "D1", "FC Hollywood", "X Fax") == (None, None)


# ---------------------------------------------------------------- rank_candidates


def test_rank_candidates_orders_by_ratio_and_takes_top3(conn):
    rows = rank_candidates(conn, "Bayern München", league="D1", top=3)
    assert [c.name for c in rows] == ["Bayern Munich", "Dortmund", "Freiburg"]
    assert rows[0].ratio == pytest.approx(0.88)
    assert rows[0].team_id == bayern_id(conn)
    assert rows[0].league == "D1"
    assert [c.ratio for c in rows] == sorted((c.ratio for c in rows), reverse=True)
    assert len(rank_candidates(conn, "Bayern München", league="D1", top=2)) == 2


def test_rank_candidates_across_leagues_when_unscoped(conn):
    """league=None 跨全部联赛找（unknown_names 无联赛列，建议须跨库）。

    手工验算 top-3：realmadrid 0.5455(SP1) > atleticomadrid 0.3077(SP1) >
    freiburg 0.30(D1) > barcelona 0.2857(SP1)。
    """
    rows = rank_candidates(conn, "Real Sociedad", league=None, top=3)
    assert [c.name for c in rows] == ["Real Madrid", "Atletico Madrid", "Freiburg"]
    assert {c.league for c in rows} == {"SP1", "D1"}
    assert rows[0].ratio == pytest.approx(0.545455, abs=1e-5)


def test_rank_candidates_includes_existing_alias(conn):
    tid = bayern_id(conn)
    conn.execute(
        "INSERT INTO team_aliases (team_id, source, alias) VALUES (?, 'oddsapi', ?)",
        (tid, "FC Hollywood"))
    conn.commit()
    names = [c.name for c in rank_candidates(conn, "FC Hollywod", league="D1", top=3)]
    assert "FC Hollywood" in names


def test_rank_candidates_dedups_same_team_same_normalized(conn):
    """候选池按 (team_id, 归一化名) 去重——canonical 与同形别名只留先出现者。

    复现审查场景：canonical ``M'Gladbach``=t1、``M'gladbach``=t2，另有确认别名
    ``Mgladbach``→t2；探测 ``M'gladbach`` 三者 ratio 全 1.0，不去重则 top-3 出现
    两条 (t2, mgladbach)。
    """
    t1 = get_or_create_team(conn, "D1", "M'Gladbach")
    t2 = get_or_create_team(conn, "D1", "M'gladbach")
    conn.execute(
        "INSERT INTO team_aliases (team_id, source, alias) VALUES (?, 'oddsapi', ?)",
        (t2, "Mgladbach"))
    conn.commit()
    rows = rank_candidates(conn, "M'gladbach", league="D1", top=3)
    keys = [(c.team_id, normalize_name(c.name)) for c in rows]
    assert len(keys) == len(set(keys))                    # 无重复 (team_id, 归一化名)
    assert [(c.team_id, c.name) for c in rows[:2]] == \
        [(t1, "M'Gladbach"), (t2, "M'gladbach")]          # canonical 文本优先保留
    assert rows[0].ratio == rows[1].ratio == 1.0
    assert rows[2].team_id not in (t1, t2)                # 第三席让给其余候选（非重复）


def test_rank_candidates_empty_pool(conn):
    assert rank_candidates(conn, "Nowhere United", league="I1") == []


# ---------------------------------------------------------------- CLI: fa data aliases


def _use_tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "cli.db"
    monkeypatch.setenv("FA_DB", str(db))
    init_db(db)
    conn = connect(db)
    for lg, names in (("D1", D1), ("SP1", SP1)):
        for n in names:
            get_or_create_team(conn, lg, n)
    conn.commit()
    return conn


def test_cli_aliases_lists_unknown_with_suggestions(tmp_path, monkeypatch):
    conn = _use_tmp_db(tmp_path, monkeypatch)
    record_unknown(conn, "oddsapi", "Bayern München")
    record_unknown(conn, "oddsapi", "Real Sociedad")
    conn.commit()
    conn.close()
    result = runner.invoke(app, ["data", "aliases"])
    assert result.exit_code == 0, result.output
    assert "Bayern München" in result.output and "Real Sociedad" in result.output
    assert "Bayern Munich" in result.output                # 建议给出 canonical 名
    assert "0.88" in result.output                          # 建议 ratio 可见
    assert "2 条" in result.output


def test_cli_aliases_empty_quarantine(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch).close()
    result = runner.invoke(app, ["data", "aliases"])
    assert result.exit_code == 0, result.output
    assert "0 条" in result.output


def test_cli_aliases_scoped_to_source(tmp_path, monkeypatch):
    conn = _use_tmp_db(tmp_path, monkeypatch)
    record_unknown(conn, "oddsapi", "Bayern München")
    record_unknown(conn, "other", "Some Club")
    conn.commit()
    conn.close()
    result = runner.invoke(app, ["data", "aliases", "--source", "other"])
    assert result.exit_code == 0, result.output
    assert "Some Club" in result.output
    assert "Bayern München" not in result.output


def test_cli_aliases_confirm_writes_alias_and_clears_unknown(tmp_path, monkeypatch):
    conn = _use_tmp_db(tmp_path, monkeypatch)
    tid = bayern_id(conn)
    record_unknown(conn, "oddsapi", "Bayern München")
    record_unknown(conn, "other", "Bayern München")        # 其他 source 不受影响
    conn.commit()
    conn.close()
    result = runner.invoke(app, ["data", "aliases", "--confirm", f"{tid}=Bayern München"])
    assert result.exit_code == 0, result.output
    assert "Bayern Munich" in result.output                # 回显绑定到的 canonical 名
    conn = connect(tmp_path / "cli.db")
    assert resolve_team(conn, "D1", "Bayern München", "oddsapi") == tid
    assert unknown_rows(conn) == []                        # oddsapi 行移出隔离表
    assert unknown_rows(conn, source="other") == ["Bayern München"]
    # 幂等：重复 confirm 不炸，仍指向同一 team_id
    conn.close()
    again = runner.invoke(app, ["data", "aliases", "--confirm", f"{tid}=Bayern München"])
    assert again.exit_code == 0, again.output
    conn = connect(tmp_path / "cli.db")
    assert resolve_team(conn, "D1", "Bayern München", "oddsapi") == tid
    conn.close()


def test_cli_aliases_confirm_overwrites_stale_binding(tmp_path, monkeypatch):
    conn = _use_tmp_db(tmp_path, monkeypatch)
    tid = bayern_id(conn)
    dortmund = conn.execute(
        "SELECT id FROM teams WHERE league='D1' AND name='Dortmund'").fetchone()["id"]
    conn.execute(
        "INSERT INTO team_aliases (team_id, source, alias) VALUES (?, 'oddsapi', ?)",
        (dortmund, "Bayern München"))
    conn.commit()
    conn.close()
    result = runner.invoke(app, ["data", "aliases", "--confirm", f"{tid}=Bayern München"])
    assert result.exit_code == 0, result.output
    conn = connect(tmp_path / "cli.db")
    assert resolve_team(conn, "D1", "Bayern München", "oddsapi") == tid  # 人工确认覆盖旧绑定
    conn.close()


@pytest.mark.parametrize("bad", ["nonsense", "=NoId", "abc=Bayern", "999=Ghost FC"])
def test_cli_aliases_confirm_rejects_bad_input(tmp_path, monkeypatch, bad):
    conn = _use_tmp_db(tmp_path, monkeypatch)
    conn.close()
    result = runner.invoke(app, ["data", "aliases", "--confirm", bad])
    assert result.exit_code != 0, result.output
    conn = connect(tmp_path / "cli.db")
    assert alias_rows(conn) == []                          # 失败不半写
    conn.close()
