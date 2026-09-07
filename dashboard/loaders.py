"""缓存层：st.cache_data(ttl=300) 包住 queries（设计 §5）。

queries.py 无 streamlit 依赖（可独立 pytest），缓存与连接生命周期归这里；
FileNotFoundError 不吞——页面层捕获后提示 `fa init`。
"""
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st

from queries import (a_calibration, a_overview, a_paper_sim, agentline_compare,
                     agentline_member_divergence, agentline_runs,
                     agentline_sample_matches, b_ab_tracks, b_bets, b_breakdown,
                     b_multi_tracks, b_pending, b_recommendations, b_runs,
                     b_summary, b_unknown_names, connect_ro)


def _run(fn, *args):
    with closing(connect_ro()) as conn:
        return fn(conn, *args)


@st.cache_data(ttl=300)
def summary() -> dict:
    return _run(b_summary)


@st.cache_data(ttl=300)
def recommendations():
    return _run(b_recommendations)


@st.cache_data(ttl=300)
def paper_bets():
    return _run(b_bets)


@st.cache_data(ttl=300)
def ab_tracks() -> dict:
    return _run(b_ab_tracks)


@st.cache_data(ttl=300)
def multi_tracks() -> dict:
    """页3多轨全景：自动发现所有 strategy（含 C' 线 kb_self 等新增轨）。"""
    return _run(b_multi_tracks)


@st.cache_data(ttl=300)
def runs():
    return _run(b_runs)


@st.cache_data(ttl=300)
def breakdown(dim: str):
    return _run(b_breakdown, dim)


@st.cache_data(ttl=300)
def pending_bets():
    return _run(b_pending)


@st.cache_data(ttl=300)
def unknown():
    return _run(b_unknown_names)


@st.cache_data(ttl=300)
def overview(leagues=None, seasons=None, date_from=None, date_to=None) -> dict:
    return _run(a_overview, leagues, seasons, date_from, date_to)


@st.cache_data(ttl=300)
def calibration(leagues=None, seasons=None, date_from=None, date_to=None) -> dict:
    return _run(a_calibration, leagues, seasons, date_from, date_to)


@st.cache_data(ttl=300)
def paper_sim(leagues=None, seasons=None, date_from=None, date_to=None) -> dict:
    return _run(a_paper_sim, leagues, seasons, date_from, date_to)


# ---- 范式对比线（A' 线 / agentline）----

@st.cache_data(ttl=300)
def al_compare(leagues=None, seasons=None, date_from=None, date_to=None) -> dict:
    return _run(agentline_compare, leagues, seasons, date_from, date_to)


@st.cache_data(ttl=300)
def al_runs() -> list:
    return _run(agentline_runs)


@st.cache_data(ttl=300)
def al_divergence(leagues=None, seasons=None, date_from=None, date_to=None) -> dict:
    return _run(agentline_member_divergence, leagues, seasons, date_from, date_to)


@st.cache_data(ttl=300)
def al_samples(leagues=None, seasons=None, date_from=None, date_to=None, limit=5) -> list:
    return _run(agentline_sample_matches, leagues, seasons, date_from, date_to, limit)
