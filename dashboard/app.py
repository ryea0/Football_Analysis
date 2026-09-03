"""fa 本地只读看板 · 入口（设计：docs/superpowers/specs/2026-09-04-web-dashboard-design.md）。

运行：uv run --group dashboard streamlit run dashboard/app.py
"""
import streamlit as st

st.set_page_config(page_title="fa 看板", page_icon="⚽", layout="wide")
st.title("fa · 本地只读看板")
st.caption("data/fa.db 只读 · B 线=前向运营（paper）· A 线=walk-forward 回测（spec §12 分账）")

with st.sidebar:
    if st.button("🔄 刷新数据", use_container_width=True):
        st.cache_data.clear()
    st.caption("查询缓存 5 分钟（ttl=300），run 落库后点刷新")
