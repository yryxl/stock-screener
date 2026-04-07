"""
芒格选股系统 - 入口页面
侧边栏导航：正式版 / 历史回测
"""

import streamlit as st

st.set_page_config(page_title="芒格选股系统", page_icon="📊", layout="wide")

st.sidebar.title("芒格选股系统")
st.sidebar.markdown("---")
st.sidebar.markdown("📊 **正式版** — 实时数据+真实推荐")
st.sidebar.markdown("🧪 **历史回测** — 历史数据验证模型")

st.title("📊 芒格选股系统")
st.markdown("### 请从左侧选择页面")
st.markdown("")
st.markdown("- **📊 正式版** — 每日模型推荐、持仓管理、关注表")
st.markdown("- **🧪 历史回测** — 用15年历史数据验证模型准确性")
