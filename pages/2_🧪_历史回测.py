"""
🧪 历史回测页面
用15年历史数据验证选股模型
虚拟账户买卖，不影响正式版
"""

import json
import streamlit as st
import time as time_module
from datetime import datetime, timedelta
import os

# ============================================
# 初始化回测状态
# ============================================

def init_backtest_state():
    """初始化回测状态（虚拟账户+时间+持仓）"""
    if "bt_initialized" not in st.session_state:
        st.session_state["bt_initialized"] = True
        st.session_state["bt_initial_capital"] = 100000
        st.session_state["bt_cash"] = 100000
        st.session_state["bt_holdings"] = []  # 虚拟持仓
        st.session_state["bt_watchlist"] = []  # 虚拟关注表
        st.session_state["bt_year"] = 2015
        st.session_state["bt_month"] = 1
        st.session_state["bt_playing"] = False
        st.session_state["bt_speed"] = 1
        st.session_state["bt_history"] = []  # 每月资产快照
        st.session_state["bt_skip_alerts"] = {}  # 跳过同级提醒
        st.session_state["bt_paused_stocks"] = {}  # 单只暂停
        st.session_state["bt_event_pause"] = False  # 事件暂停


def reset_backtest():
    """重置回测"""
    capital = st.session_state.get("bt_initial_capital", 100000)
    st.session_state["bt_cash"] = capital
    st.session_state["bt_holdings"] = []
    st.session_state["bt_watchlist"] = []
    st.session_state["bt_history"] = []
    st.session_state["bt_skip_alerts"] = {}
    st.session_state["bt_paused_stocks"] = {}
    st.session_state["bt_event_pause"] = False
    st.session_state["bt_playing"] = False


def get_current_date():
    y = st.session_state.get("bt_year", 2015)
    m = st.session_state.get("bt_month", 1)
    return f"{y}-{m:02d}-15"


def advance_month():
    m = st.session_state["bt_month"]
    y = st.session_state["bt_year"]
    if m >= 12:
        st.session_state["bt_month"] = 1
        st.session_state["bt_year"] = y + 1
    else:
        st.session_state["bt_month"] = m + 1


def retreat_month():
    m = st.session_state["bt_month"]
    y = st.session_state["bt_year"]
    if m <= 1:
        st.session_state["bt_month"] = 12
        st.session_state["bt_year"] = y - 1
    else:
        st.session_state["bt_month"] = m - 1


def load_month_data(date_str):
    """加载某月的历史数据"""
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backtest_data", "monthly", f"{date_str[:7]}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def calc_portfolio_value(data):
    """计算当前持仓市值"""
    if not data or not st.session_state.get("bt_holdings"):
        return 0
    stocks = data.get("stocks", {})
    total = 0
    for h in st.session_state["bt_holdings"]:
        stock_data = stocks.get(h["code"], {})
        price = stock_data.get("price", h.get("cost", 0))
        total += h["shares"] * price
    return total


def virtual_buy(code, name, price, shares):
    """虚拟买入"""
    cost = price * shares
    if cost > st.session_state["bt_cash"]:
        st.error(f"资金不足！需要¥{cost:,.0f}，可用¥{st.session_state['bt_cash']:,.0f}")
        return False
    if shares < 100 or shares % 100 != 0:
        st.error("买入最少100股，以100股为单位")
        return False

    st.session_state["bt_cash"] -= cost
    # 检查是否已持有
    for h in st.session_state["bt_holdings"]:
        if h["code"] == code:
            # 加仓，更新均价
            old_cost = h["shares"] * h["cost"]
            h["shares"] += shares
            h["cost"] = (old_cost + cost) / h["shares"]
            return True
    # 新建持仓
    st.session_state["bt_holdings"].append({
        "code": code, "name": name, "shares": shares,
        "cost": price, "buy_date": get_current_date(),
    })
    return True


def virtual_sell(code, shares):
    """虚拟卖出"""
    for h in st.session_state["bt_holdings"]:
        if h["code"] == code:
            if shares > h["shares"]:
                st.error(f"持有{h['shares']}股，不能卖{shares}股")
                return False
            # 需要当前价格
            date_str = get_current_date()
            data = load_month_data(date_str)
            price = h["cost"]  # 默认用成本价
            if data:
                stock_data = data.get("stocks", {}).get(code, {})
                price = stock_data.get("price", h["cost"])

            st.session_state["bt_cash"] += price * shares
            h["shares"] -= shares
            if h["shares"] <= 0:
                st.session_state["bt_holdings"].remove(h)
            return True
    st.error("未持有该股票")
    return False


# ============================================
# 页面
# ============================================

init_backtest_state()

# 警告条
st.markdown("""
<div style="background-color: #fff3cd; padding: 10px; border-radius: 5px; margin-bottom: 15px; border-left: 4px solid #ffc107;">
⚠️ <b>回测模式</b> — 使用历史数据，非真实推荐。虚拟交易不影响正式版。
</div>
""", unsafe_allow_html=True)

st.title("🧪 历史回测")

# ============================================
# 侧边栏：账户+时间控制
# ============================================

with st.sidebar:
    st.markdown("---")
    st.subheader("🧪 回测控制")

    # 虚拟账户
    st.markdown("**💰 虚拟账户**")
    new_capital = st.number_input("起始资金", min_value=10000, value=st.session_state["bt_initial_capital"], step=10000, key="bt_cap_input")
    if new_capital != st.session_state["bt_initial_capital"]:
        st.session_state["bt_initial_capital"] = new_capital

    if st.button("🔄 重置回测", use_container_width=True):
        reset_backtest()
        st.rerun()

    st.markdown("---")

    # 时间选择
    st.markdown("**📅 时间设置**")
    col_y, col_m = st.columns(2)
    with col_y:
        st.session_state["bt_year"] = st.number_input("年", min_value=2010, max_value=2025, value=st.session_state["bt_year"], key="bt_y")
    with col_m:
        st.session_state["bt_month"] = st.number_input("月", min_value=1, max_value=12, value=st.session_state["bt_month"], key="bt_m")

    # 倍速
    speed = st.select_slider("⏩ 播放倍速", options=[1, 2, 5], value=st.session_state.get("bt_speed", 1))
    st.session_state["bt_speed"] = speed

    # 进度
    total_months = (2025 - 2010) * 12
    current_months = (st.session_state["bt_year"] - 2010) * 12 + st.session_state["bt_month"]
    progress = min(current_months / total_months, 1.0)
    st.progress(progress, text=f"{st.session_state['bt_year']}-{st.session_state['bt_month']:02d}")


# ============================================
# 播放控制条
# ============================================

current_date = get_current_date()
st.markdown(f"### 📅 当前时间：{current_date}")

col_back, col_play, col_fwd, col_info = st.columns([1, 1, 1, 3])
with col_back:
    if st.button("⏪ 后退", use_container_width=True):
        retreat_month()
        st.rerun()
with col_play:
    if st.session_state.get("bt_playing"):
        if st.button("⏸️ 暂停", use_container_width=True):
            st.session_state["bt_playing"] = False
            st.rerun()
    else:
        if st.button("▶️ 播放", use_container_width=True):
            st.session_state["bt_playing"] = True
            st.rerun()
with col_fwd:
    if st.button("⏩ 前进", use_container_width=True):
        advance_month()
        st.rerun()

# ============================================
# 虚拟账户概览
# ============================================

month_data = load_month_data(current_date)
portfolio_value = calc_portfolio_value(month_data)
cash = st.session_state["bt_cash"]
total_value = cash + portfolio_value
initial = st.session_state["bt_initial_capital"]
pnl = total_value - initial
pnl_pct = (pnl / initial * 100) if initial > 0 else 0
pnl_color = "green" if pnl >= 0 else "red"

col_a, col_b, col_c, col_d = st.columns(4)
with col_a:
    st.metric("💰 可用资金", f"¥{cash:,.0f}")
with col_b:
    st.metric("📈 持仓市值", f"¥{portfolio_value:,.0f}")
with col_c:
    st.metric("💎 总资产", f"¥{total_value:,.0f}")
with col_d:
    st.metric("📊 收益", f"¥{pnl:+,.0f} ({pnl_pct:+.1f}%)")

st.markdown("---")

# ============================================
# 三个Tab
# ============================================

tab1, tab2, tab3 = st.tabs(["🔮 模型推荐 [回测]", "📦 模拟持仓 [回测]", "⭐ 模拟关注 [回测]"])

# ============================================
# Tab1: 模型推荐
# ============================================
with tab1:
    st.subheader("🔮 模型推荐 [回测]")

    if not month_data:
        st.info(f"📅 {current_date} 暂无历史数据。请先运行数据采集脚本，或选择有数据的时间段。")
    else:
        stocks = month_data.get("stocks", {})
        if not stocks:
            st.info("本月无股票数据")
        else:
            for code, sdata in stocks.items():
                name = sdata.get("name", code)
                price = sdata.get("price", 0)
                pe = sdata.get("pe_ttm", 0)
                roe = sdata.get("roe", 0)
                div_yield = sdata.get("dividend_yield", 0)
                signal = sdata.get("signal", "hold")
                signal_text = sdata.get("signal_text", "")

                # 只显示有买入信号的
                if signal and "buy" in signal:
                    col1, col2, col3, col4 = st.columns([3, 1.5, 1.5, 3])
                    with col1:
                        st.markdown(f"**{name}**（{code}）")
                        st.caption(f"PE={pe:.1f} ROE={roe:.1f}% 股息{div_yield:.1f}%")
                    with col2:
                        st.metric("股价", f"¥{price:.2f}")
                    with col3:
                        # 事件控制
                        is_paused = st.session_state["bt_paused_stocks"].get(code, False)
                        if st.button("⏸️" if not is_paused else "▶️", key=f"bt_pause_{code}"):
                            st.session_state["bt_paused_stocks"][code] = not is_paused
                            st.rerun()
                        skip_key = f"bt_skip_{code}"
                        st.checkbox("跳过同级", key=skip_key, value=st.session_state["bt_skip_alerts"].get(code, False))
                    with col4:
                        st.caption(signal_text)
                        # 虚拟买入按钮
                        bc1, bc2, bc3 = st.columns(3)
                        with bc1:
                            if st.button(f"买100股", key=f"bt_buy100_{code}"):
                                if virtual_buy(code, name, price, 100):
                                    st.success(f"虚拟买入 {name} 100股 @ ¥{price:.2f}")
                                    st.rerun()
                        with bc2:
                            if st.button(f"买500股", key=f"bt_buy500_{code}"):
                                if virtual_buy(code, name, price, 500):
                                    st.success(f"虚拟买入 {name} 500股 @ ¥{price:.2f}")
                                    st.rerun()
                        with bc3:
                            custom = st.number_input("股数", min_value=100, step=100, value=100, key=f"bt_custom_{code}")
                            if st.button("买入", key=f"bt_buyx_{code}"):
                                if virtual_buy(code, name, price, int(custom)):
                                    st.success(f"虚拟买入 {name} {int(custom)}股")
                                    st.rerun()
                    st.divider()

# ============================================
# Tab2: 模拟持仓
# ============================================
with tab2:
    st.subheader("📦 模拟持仓 [回测]")

    holdings = st.session_state.get("bt_holdings", [])
    if not holdings:
        st.info("暂无虚拟持仓，去模型推荐页虚拟买入股票")
    else:
        stocks = month_data.get("stocks", {}) if month_data else {}

        for i, h in enumerate(holdings):
            code = h["code"]
            sdata = stocks.get(code, {})
            current_price = sdata.get("price", h["cost"])
            pnl_stock = (current_price - h["cost"]) * h["shares"]
            pnl_pct_stock = (current_price / h["cost"] - 1) * 100 if h["cost"] > 0 else 0

            col1, col2, col3, col4 = st.columns([3, 2, 2, 3])
            with col1:
                st.markdown(f"**{h['name']}**（{code}）")
                st.caption(f"买入日期: {h.get('buy_date', '?')}")
            with col2:
                st.metric("持有", f"{h['shares']}股 @ ¥{h['cost']:.2f}")
            with col3:
                color = "green" if pnl_stock >= 0 else "red"
                st.metric("现价", f"¥{current_price:.2f}")
                st.caption(f"盈亏: ¥{pnl_stock:+,.0f} ({pnl_pct_stock:+.1f}%)")
            with col4:
                # 事件控制
                is_paused = st.session_state["bt_paused_stocks"].get(f"sell_{code}", False)
                col_p, col_s = st.columns(2)
                with col_p:
                    if st.button("⏸️" if not is_paused else "▶️", key=f"bt_spause_{code}"):
                        st.session_state["bt_paused_stocks"][f"sell_{code}"] = not is_paused
                        st.rerun()
                with col_s:
                    st.checkbox("跳过同级", key=f"bt_sskip_{code}")

                # 卖出按钮
                sc1, sc2, sc3 = st.columns(3)
                with sc1:
                    if st.button("全部卖出", key=f"bt_sellall_{code}"):
                        if virtual_sell(code, h["shares"]):
                            st.success(f"虚拟卖出 {h['name']} 全部")
                            st.rerun()
                with sc2:
                    half = h["shares"] // 2
                    if half > 0 and st.button(f"卖{half}股", key=f"bt_sellhalf_{code}"):
                        if virtual_sell(code, half):
                            st.success(f"虚拟卖出 {h['name']} {half}股")
                            st.rerun()
                with sc3:
                    sell_n = st.number_input("卖出股数", min_value=1, max_value=h["shares"], value=min(100, h["shares"]), key=f"bt_selln_{code}")
                    if st.button("卖出", key=f"bt_sellx_{code}"):
                        if virtual_sell(code, int(sell_n)):
                            st.success(f"虚拟卖出 {sell_n}股")
                            st.rerun()
            st.divider()

# ============================================
# Tab3: 模拟关注表
# ============================================
with tab3:
    st.subheader("⭐ 模拟关注 [回测]")
    st.caption("从模型推荐页添加到关注表，追踪信号变化")

    bt_watchlist = st.session_state.get("bt_watchlist", [])
    if not bt_watchlist:
        st.info("暂无虚拟关注股票")
    else:
        stocks = month_data.get("stocks", {}) if month_data else {}
        for item in bt_watchlist:
            code = item["code"]
            sdata = stocks.get(code, {})
            price = sdata.get("price", 0)
            pe = sdata.get("pe_ttm", 0)
            signal = sdata.get("signal", "")
            signal_text = sdata.get("signal_text", "")

            col1, col2, col3, col4 = st.columns([3, 1.5, 1.5, 3])
            with col1:
                st.markdown(f"**{item['name']}**（{code}）")
            with col2:
                st.metric("PE", f"{pe:.1f}" if pe else "—")
            with col3:
                st.metric("股价", f"¥{price:.2f}" if price else "—")
            with col4:
                st.caption(signal_text)
                if signal and "buy" in signal:
                    if st.button(f"虚拟买入100股", key=f"bt_wbuy_{code}"):
                        if virtual_buy(code, item["name"], price, 100):
                            st.rerun()
            st.divider()

    # 手动添加关注
    with st.form("bt_add_watch", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            wcode = st.text_input("股票代码", key="bt_wcode")
        with c2:
            wname = st.text_input("名称", key="bt_wname")
        if st.form_submit_button("添加到模拟关注", use_container_width=True):
            if wcode:
                bt_watchlist.append({"code": wcode.strip(), "name": wname.strip() or wcode.strip()})
                st.session_state["bt_watchlist"] = bt_watchlist
                st.rerun()

# ============================================
# 底部
# ============================================
st.markdown("---")
st.caption("🧪 历史回测模式 | 虚拟交易不影响正式版 | 数据来自历史记录")

# 自动播放逻辑
if st.session_state.get("bt_playing") and not st.session_state.get("bt_event_pause"):
    speed = st.session_state.get("bt_speed", 1)
    delay = max(0.2, 1.0 / speed)
    time_module.sleep(delay)
    advance_month()
    # 检查是否到终点
    if st.session_state["bt_year"] >= 2025 and st.session_state["bt_month"] >= 12:
        st.session_state["bt_playing"] = False
    st.rerun()
