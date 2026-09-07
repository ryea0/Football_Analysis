"""fa 本地只读看板 · 入口（设计：docs/superpowers/specs/2026-09-04-web-dashboard-design.md）。

运行：uv run --group dashboard streamlit run dashboard/app.py
"""
import streamlit as st

st.set_page_config(page_title="fa 看板", page_icon="⚽", layout="wide")

# ─────────────────────────────────────────────────────────────
# 全局样式注入（字体统一 / 表格密度 / 标题自适应 / 卡片质感）
# ─────────────────────────────────────────────────────────────
st.markdown(
    """
<style>
/* ===== 字体与行高 ===== */
html, body, [class*="css"]  {
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC",
                 "Hiragino Sans GB", "Microsoft YaHei", "Segoe UI",
                 Roboto, Helvetica, Arial, sans-serif;
    font-size: 14px;
    line-height: 1.55;
}

/* ===== 标题自适应（避免换行错乱）===== */
h1 { font-size: clamp(1.4rem, 2.2vw, 2rem) !important;
     font-weight: 700; letter-spacing: -0.01em;
     margin-top: 0.2rem !important; }
h2 { font-size: clamp(1.15rem, 1.7vw, 1.5rem) !important;
     font-weight: 650; letter-spacing: -0.005em;
     padding-top: 0.6rem; margin-top: 0.4rem !important; }
h3 { font-size: clamp(1rem, 1.4vw, 1.2rem) !important; font-weight: 600; }

/* ===== 副标题 / caption 文字 ===== */
.stCaption, small, [data-testid="stCaptionContainer"] {
    font-size: 0.78rem !important;
    line-height: 1.5;
    color: var(--text-color-faded, #888);
}

/* ===== Metric 卡片 ===== */
[data-testid="stMetric"] {
    background: var(--background-secondary, #f7f8fa);
    border: 1px solid var(--border-color-light, #e5e7eb);
    border-radius: 10px;
    padding: 0.75rem 1rem;
    transition: border-color 0.15s ease;
}
[data-testid="stMetric"]:hover {
    border-color: var(--primary-color-faded, #93c5fd);
}
[data-testid="stMetricLabel"] p {
    font-size: 0.78rem !important;
    font-weight: 500;
    opacity: 0.75;
    margin-bottom: 0.15rem !important;
}
[data-testid="stMetricValue"] {
    font-size: 1.55rem !important;
    font-weight: 700;
    letter-spacing: -0.01em;
}
[data-testid="stMetricDelta"] {
    font-size: 0.78rem !important;
    margin-top: 0.1rem;
}

/* ===== 表格：铺满 + 合适密度 ===== */
[data-testid="stDataFrame"] {
    width: 100% !important;
}
.stDataFrameResizer {
    display: none !important;
}
/* 表头 */
[role="columnheader"] {
    font-weight: 600 !important;
    font-size: 0.78rem !important;
    background: var(--background-secondary, #f3f4f6) !important;
    padding: 0.4rem 0.6rem !important;
    white-space: nowrap;
}
/* 单元格 */
[role="gridcell"] {
    font-size: 0.8rem !important;
    padding: 0.35rem 0.6rem !important;
    line-height: 1.4;
}
/* 斑马纹 */
[role="row"][aria-rowindex]:nth-child(even) [role="gridcell"] {
    background: var(--background-secondary, #fafbfc);
}

/* ===== Tabs ===== */
.stTabs [data-baseweb="tab-list"] {
    gap: 0.2rem;
    margin-bottom: 0.5rem;
}
.stTabs [data-baseweb="tab"] {
    font-size: 0.85rem;
    font-weight: 500;
    padding: 0.4rem 0.9rem;
    border-radius: 8px 8px 0 0;
}
.stTabs [data-baseweb="tab"]:hover {
    background: var(--background-secondary, #f3f4f6);
}

/* ===== Expander ===== */
.streamlit-expanderHeader {
    font-weight: 600;
    font-size: 0.92rem;
    padding: 0.5rem 0.75rem;
}
.streamlit-expanderContent {
    padding: 0.25rem 0.75rem 0.75rem;
}

/* ===== 侧边栏 ===== */
section[data-testid="stSidebar"] {
    width: 260px !important;
}
section[data-testid="stSidebar"] .stButton button {
    font-weight: 500;
}

/* ===== 分割线 ===== */
hr {
    margin: 1rem 0 !important;
    opacity: 0.6;
}

/* ===== 按钮 ===== */
.stButton button {
    border-radius: 8px;
    font-weight: 500;
    transition: all 0.15s ease;
}
.stButton button:hover {
    transform: translateY(-0.5px);
    box-shadow: 0 2px 6px rgba(0,0,0,0.06);
}

/* ===== 页面顶部留白压缩 ===== */
.block-container {
    padding-top: 1.2rem !important;
    padding-bottom: 2rem;
    max-width: 1400px;
}

/* ===== 警告/信息框 ===== */
.stAlert {
    border-radius: 10px;
    padding: 0.6rem 0.9rem;
}
.stAlert [data-testid="stMarkdownContainer"] p {
    margin: 0.1rem 0 !important;
    font-size: 0.85rem;
}

/* ===== Selectbox / Multiselect 标签 ===== */
label p {
    font-size: 0.8rem !important;
    font-weight: 500;
    margin-bottom: 0.1rem !important;
}
</style>
""",
    unsafe_allow_html=True,
)

st.title("fa · 本地只读看板")
st.caption("data/fa.db 只读 · B 线=前向运营（paper）· A 线=walk-forward 回测（spec §12 分账）"
           " · C 线=persona 知识进化栈")

with st.sidebar:
    st.markdown("### ⚙️ 操作")
    if st.button("🔄 刷新数据", use_container_width=True):
        st.cache_data.clear()
        st.success("缓存已清空，数据将重新加载")
    st.caption("查询缓存 5 分钟（ttl=300），run 落库后点刷新")

    st.divider()
    st.markdown("### 📑 快速导航")
    st.caption("**B 线（运营）**")
    st.caption("1 · 总览 · 2 · 推荐台账")
    st.caption("3 · 多轨进度 · 4 · 运维健康")
    st.caption("**A 线（回测）**")
    st.caption("5 · 回测总览 · 6 · 校准曲线")
    st.caption("7 · 模拟盘回测")
    st.caption("**A' 线（范式对比）**")
    st.caption("8 · 范式对比（DSH agent）")
