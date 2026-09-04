"""窗口计算：锚点/42 天切分/收口判定/前锚 clamp（设计档 §1）。"""
from datetime import date

from fa.evolve import windows
from fa.config import EVOLUTION_EPOCH


def test_anchor_is_cron_activation_day():
    assert EVOLUTION_EPOCH == date(2026, 9, 4)


def test_window1_bounds():
    w = windows.window_bounds(1)
    assert (w.idx, w.opened, w.closes) == (1, date(2026, 9, 4), date(2026, 10, 16))


def test_window2_bounds():
    w = windows.window_bounds(2)
    assert (w.opened, w.closes) == (date(2026, 10, 16), date(2026, 11, 27))


def test_window_of_membership():
    # 开窗当日属本窗；闭窗当日属下一窗（[opened, closes) 半开区间）
    assert windows.window_of(date(2026, 9, 4)).idx == 1
    assert windows.window_of(date(2026, 10, 15)).idx == 1
    assert windows.window_of(date(2026, 10, 16)).idx == 2


def test_window_of_before_epoch_clamps_to_1():
    assert windows.window_of(date(2026, 8, 30)).idx == 1


def test_window_bounds_rejects_zero():
    import pytest
    from fa.evolve import EvolutionError
    with pytest.raises(EvolutionError):
        windows.window_bounds(0)


def test_due_windows_empty_in_w1():
    assert windows.due_windows(date(2026, 9, 10)) == []


def test_due_windows_after_w1_close():
    due = windows.due_windows(date(2026, 10, 20))
    assert [w.idx for w in due] == [1]


def test_due_windows_multiple():
    due = windows.due_windows(date(2026, 12, 1))
    assert [w.idx for w in due] == [1, 2]
    # 2026-12-01 落在第 3 窗（11-27 开、2027-01-08 收），1..2 已收口
    # （brief 原稿写 [1,2,3]/「第 4 窗」系窗口序号 off-by-one：11-27 开的是第 3 窗）
