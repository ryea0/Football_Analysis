"""缓存层：st.cache_data(ttl=300) 包住 queries（设计 §5）。

queries.py 无 streamlit 依赖（可独立 pytest），缓存与连接生命周期归这里；
FileNotFoundError 不吞——页面层捕获后提示 `fa init`。
"""
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st

from queries import (a_calibration, a_overview, a_paper_sim, b_ab_tracks,
                     b_bets, b_recommendations, b_runs, b_summary,
                     b_unknown_names, connect_ro)


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
def runs():
    return _run(b_runs)


@st.cache_data(ttl=300)
def unknown():
    return _run(b_unknown_names)


@st.cache_data(ttl=300)
def overview(leagues=None, seasons=None) -> dict:
    return _run(a_overview, leagues, seasons)


@st.cache_data(ttl=300)
def calibration(leagues=None, seasons=None) -> dict:
    return _run(a_calibration, leagues, seasons)


@st.cache_data(ttl=300)
def paper_sim(leagues=None, seasons=None) -> dict:
    return _run(a_paper_sim, leagues, seasons)
