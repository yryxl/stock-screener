"""
🧪 历史回测页面（独立模块，由app.py调用）
"""

import json
import streamlit as st
import time as time_module
from datetime import datetime, timedelta
import os


def render_backtest_page():
    """渲染回测页面"""

    # 初始化状态
    if "bt_initialized" not in st.session_state:
        st.session_state["bt_initialized"] = True
        st.session_state["bt_initial_capital"] = 100000
        st.session_state["bt_cash"] = 100000
        st.session_state["bt_holdings"] = []
        st.session_state["bt_watchlist"] = []
        st.session_state["bt_year"] = 2015
        st.session_state["bt_month"] = 1
        st.session_state["bt_playing"] = False
        st.session_state["bt_speed"] = 1
        st.session_state["bt_history"] = []
        st.session_state["bt_skip_alerts"] = {}
        st.session_state["bt_paused_stocks"] = {}

    # 警告条
    st.markdown("""
    <div style="background-color: #fff3cd; padding: 10px; border-radius: 5px; margin-bottom: 15px; border-left: 4px solid #ffc107;">
    ⚠️ <b>回测模式</b> — 使用历史数据，非真实推荐。虚拟交易不影响正式版。
    </div>
    """, unsafe_allow_html=True)

    st.title("🧪 历史回测")

    # 侧边栏控制
    with st.sidebar:
        st.markdown("---")
        st.subheader("🧪 回测控制")

        st.markdown("**💰 虚拟账户**")
        new_cap = st.number_input("起始资金", min_value=10000, value=st.session_state["bt_initial_capital"], step=10000, key="bt_cap")

        if st.button("🔄 重置回测", use_container_width=True):
            st.session_state["bt_cash"] = new_cap
            st.session_state["bt_initial_capital"] = new_cap
            st.session_state["bt_holdings"] = []
            st.session_state["bt_watchlist"] = []
            st.session_state["bt_history"] = []
            st.session_state["bt_playing"] = False
            st.rerun()

        st.markdown("---")
        st.markdown("**📅 时间**")
        c1, c2 = st.columns(2)
        with c1:
            st.session_state["bt_year"] = st.number_input("年", 2010, 2025, st.session_state["bt_year"], key="bt_y")
        with c2:
            st.session_state["bt_month"] = st.number_input("月", 1, 12, st.session_state["bt_month"], key="bt_m")

        st.session_state["bt_speed"] = st.select_slider("⏩ 倍速", [1, 2, 5], st.session_state["bt_speed"])

    # 当前时间
    current_date = f"{st.session_state['bt_year']}-{st.session_state['bt_month']:02d}-15"
    st.markdown(f"### 📅 当前：{current_date}")

    # 播放控制
    c1, c2, c3, c4 = st.columns([1, 1, 1, 3])
    with c1:
        if st.button("⏪ 后退", use_container_width=True):
            if st.session_state["bt_month"] <= 1:
                st.session_state["bt_month"] = 12
                st.session_state["bt_year"] -= 1
            else:
                st.session_state["bt_month"] -= 1
            st.rerun()
    with c2:
        if st.button("▶️ 播放" if not st.session_state["bt_playing"] else "⏸️ 暂停", use_container_width=True):
            st.session_state["bt_playing"] = not st.session_state["bt_playing"]
            st.rerun()
    with c3:
        if st.button("⏩ 前进", use_container_width=True):
            if st.session_state["bt_month"] >= 12:
                st.session_state["bt_month"] = 1
                st.session_state["bt_year"] += 1
            else:
                st.session_state["bt_month"] += 1
            st.rerun()

    # 虚拟资产概览
    cash = st.session_state["bt_cash"]
    initial = st.session_state["bt_initial_capital"]
    # TODO: 用历史数据计算持仓市值
    portfolio = sum(h.get("shares", 0) * h.get("cost", 0) for h in st.session_state["bt_holdings"])
    total = cash + portfolio
    pnl = total - initial
    pnl_pct = (pnl / initial * 100) if initial > 0 else 0

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("💰 可用资金", f"¥{cash:,.0f}")
    with c2:
        st.metric("📈 持仓市值", f"¥{portfolio:,.0f}")
    with c3:
        st.metric("💎 总资产", f"¥{total:,.0f}")
    with c4:
        st.metric("📊 收益", f"{pnl_pct:+.1f}%")

    st.markdown("---")

    # 三个Tab
    tab1, tab2, tab3 = st.tabs(["🔮 模型推荐 [回测]", "📦 模拟持仓 [回测]", "⭐ 模拟关注 [回测]"])

    with tab1:
        st.subheader("🔮 模型推荐 [回测]")
        st.info(f"📅 {current_date} — 暂无历史数据。请先运行数据采集后使用回测功能。")

    with tab2:
        st.subheader("📦 模拟持仓 [回测]")
        if not st.session_state["bt_holdings"]:
            st.info("暂无虚拟持仓")
        else:
            for h in st.session_state["bt_holdings"]:
                st.markdown(f"**{h['name']}** {h['shares']}股 @ ¥{h['cost']:.2f}")

    with tab3:
        st.subheader("⭐ 模拟关注 [回测]")
        if not st.session_state["bt_watchlist"]:
            st.info("暂无虚拟关注")

    st.markdown("---")
    st.caption("🧪 回测模式 | 虚拟交易不影响正式版 | 历史数据采集后可用")
