"""
🧪 历史回测页面 - 连通回测引擎，匿名化显示
"""

import json
import streamlit as st
import time as time_module
import random
import os

from backtest_engine import (
    get_month_signals, generate_anonymous_map,
    load_stock_list, load_events,
)

SIGNAL_LABELS = {
    "buy_heavy": "🟢🟢🟢 可以重仓",
    "buy_medium": "🟢🟢 可以中仓",
    "buy_light": "🟢 可以轻仓",
    "buy_watch": "👀 重点关注",
    "hold": "⚪ 继续观望",
    "hold_keep": "🔵 持续持有",
    "sell_watch": "🟡 关注卖出",
    "sell_light": "🟠 适当卖出",
    "sell_medium": "🔴 中仓卖出",
    "sell_heavy": "🔴🔴 大量卖出",
    "delisted": "⛔ 已停止交易",
}


def init_state():
    if "bt_init" not in st.session_state:
        st.session_state["bt_init"] = True
        reset_game()


def reset_game():
    """重置一局（保留历史记录用于统计）"""
    # 保存上一局记录
    if "bt_game_history" not in st.session_state:
        st.session_state["bt_game_history"] = []
    if st.session_state.get("bt_holdings") and st.session_state.get("bt_trade_log"):
        st.session_state["bt_game_history"].append({
            "final_value": st.session_state.get("bt_cash", 0),
            "trades": len(st.session_state.get("bt_trade_log", [])),
        })

    # 重置虚拟账户
    capital = st.session_state.get("bt_capital_setting", 100000)
    st.session_state["bt_cash"] = capital
    st.session_state["bt_initial_capital"] = capital
    st.session_state["bt_holdings"] = []
    st.session_state["bt_watchlist_bt"] = []
    st.session_state["bt_year"] = 2015
    st.session_state["bt_month"] = 1
    st.session_state["bt_playing"] = False
    st.session_state["bt_speed"] = 1
    st.session_state["bt_trade_log"] = []
    st.session_state["bt_skip_alerts"] = {}

    # 重新生成匿名编号（每局不同）
    stocks = load_stock_list()
    seed = random.randint(1, 999999)
    st.session_state["bt_anon_map"] = generate_anonymous_map(list(stocks.keys()), seed=seed)
    st.session_state["bt_anon_seed"] = seed

    # 行业映射（内部用，不暴露给前端）
    st.session_state["bt_industry_map"] = {}


def virtual_buy(sid, anon_id, price, shares):
    cost = price * shares
    if cost > st.session_state["bt_cash"]:
        st.error(f"资金不足！需¥{cost:,.0f}，可用¥{st.session_state['bt_cash']:,.0f}")
        return False
    if shares < 100 or shares % 100 != 0:
        st.error("买入最少100股，100股为单位")
        return False
    st.session_state["bt_cash"] -= cost
    for h in st.session_state["bt_holdings"]:
        if h["sid"] == sid:
            old = h["shares"] * h["cost"]
            h["shares"] += shares
            h["cost"] = (old + cost) / h["shares"]
            st.session_state["bt_trade_log"].append({"type": "buy", "anon": anon_id, "shares": shares, "price": price, "date": f"{st.session_state['bt_year']}-{st.session_state['bt_month']:02d}"})
            return True
    st.session_state["bt_holdings"].append({"sid": sid, "anon": anon_id, "shares": shares, "cost": price})
    st.session_state["bt_trade_log"].append({"type": "buy", "anon": anon_id, "shares": shares, "price": price, "date": f"{st.session_state['bt_year']}-{st.session_state['bt_month']:02d}"})
    return True


def virtual_sell(sid, shares, current_price):
    for h in st.session_state["bt_holdings"]:
        if h["sid"] == sid:
            if shares > h["shares"]:
                st.error(f"持有{h['shares']}股，不能卖{shares}股")
                return False
            st.session_state["bt_cash"] += current_price * shares
            h["shares"] -= shares
            st.session_state["bt_trade_log"].append({"type": "sell", "anon": h["anon"], "shares": shares, "price": current_price, "date": f"{st.session_state['bt_year']}-{st.session_state['bt_month']:02d}"})
            if h["shares"] <= 0:
                st.session_state["bt_holdings"].remove(h)
            return True
    return False


def render_backtest_page():
    init_state()

    # 警告条
    st.markdown("""<div style="background:#f3e8ff;padding:10px;border-radius:5px;border-left:4px solid #9333ea;margin-bottom:15px;">
    🧪 <b>回测模式</b> — 历史数据验证，虚拟交易，不影响正式版</div>""", unsafe_allow_html=True)

    st.title("🧪 历史回测")

    # 侧边栏
    with st.sidebar:
        st.markdown("---")
        st.subheader("🧪 回测控制")
        cap = st.number_input("💰 起始资金", 10000, 1000000, st.session_state.get("bt_initial_capital", 100000), 10000, key="bt_cap_s")
        st.session_state["bt_capital_setting"] = cap

        if st.button("🔄 重置（新一局）", use_container_width=True, type="primary"):
            reset_game()
            st.rerun()

        st.markdown("---")
        c1, c2 = st.columns(2)
        with c1:
            st.session_state["bt_year"] = st.number_input("年", 2010, 2025, st.session_state["bt_year"], key="bty")
        with c2:
            st.session_state["bt_month"] = st.number_input("月", 1, 12, st.session_state["bt_month"], key="btm")
        st.session_state["bt_speed"] = st.select_slider("⏩ 倍速", [1, 2, 5], st.session_state.get("bt_speed", 1))

        # 历史战绩
        games = st.session_state.get("bt_game_history", [])
        if games:
            st.markdown("---")
            st.markdown(f"**📜 历史战绩：{len(games)}局**")
            for i, g in enumerate(games[-3:]):
                st.caption(f"第{len(games)-2+i}局：最终¥{g['final_value']:,.0f}，{g['trades']}笔交易")

    # 时间和控制
    yr, mo = st.session_state["bt_year"], st.session_state["bt_month"]
    st.markdown(f"### 📅 {yr}年{mo}月")

    c1, c2, c3, c4 = st.columns([1, 1, 1, 3])
    with c1:
        if st.button("⏪ 后退", use_container_width=True):
            if mo <= 1: st.session_state["bt_month"] = 12; st.session_state["bt_year"] -= 1
            else: st.session_state["bt_month"] -= 1
            st.rerun()
    with c2:
        if st.button("▶️ 播放" if not st.session_state.get("bt_playing") else "⏸️ 暂停", use_container_width=True):
            st.session_state["bt_playing"] = not st.session_state.get("bt_playing", False)
            st.rerun()
    with c3:
        if st.button("⏩ 前进", use_container_width=True):
            if mo >= 12: st.session_state["bt_month"] = 1; st.session_state["bt_year"] += 1
            else: st.session_state["bt_month"] += 1
            st.rerun()

    # 获取当月数据
    anon_map = st.session_state.get("bt_anon_map", {})
    industry_map = st.session_state.get("bt_industry_map", {})
    signals = get_month_signals(yr, mo, anon_map=anon_map, industry_map=industry_map)

    # 更新持仓市值
    portfolio_value = 0
    for h in st.session_state.get("bt_holdings", []):
        for anon_id, sdata in signals.items():
            if sdata.get("sid") == h["sid"]:
                portfolio_value += h["shares"] * sdata.get("price", h["cost"])
                break
        else:
            portfolio_value += h["shares"] * h["cost"]

    cash = st.session_state["bt_cash"]
    total = cash + portfolio_value
    initial = st.session_state["bt_initial_capital"]
    pnl_pct = (total / initial - 1) * 100 if initial > 0 else 0

    c1, c2, c3, c4 = st.columns(4)
    with c1: st.metric("💰 可用", f"¥{cash:,.0f}")
    with c2: st.metric("📈 持仓", f"¥{portfolio_value:,.0f}")
    with c3: st.metric("💎 总资产", f"¥{total:,.0f}")
    with c4: st.metric("📊 收益", f"{pnl_pct:+.1f}%")

    st.markdown("---")

    # 三Tab
    tab1, tab2, tab3 = st.tabs(["🔮 模型推荐 [回测]", "📦 模拟持仓 [回测]", "⭐ 模拟关注 [回测]"])

    # ========================================
    # Tab1: 模型推荐
    # ========================================
    with tab1:
        if not signals:
            st.info(f"{yr}年{mo}月暂无数据")
        else:
            buy_signals = ["buy_heavy", "buy_medium", "buy_light", "buy_watch"]
            for sig_key in buy_signals:
                group = {k: v for k, v in signals.items() if v.get("signal") == sig_key}
                if not group:
                    continue
                st.subheader(SIGNAL_LABELS.get(sig_key, sig_key))
                for anon_id, sdata in group.items():
                    sid = sdata["sid"]
                    price = sdata.get("price", 0)
                    pe = sdata.get("pe_ttm")
                    roe = sdata.get("roe")
                    div_y = sdata.get("dividend_yield", 0)
                    score = sdata.get("score", 0)
                    events = sdata.get("events", [])

                    c1, c2, c3, c4 = st.columns([2, 1.5, 1.5, 3])
                    with c1:
                        st.markdown(f"**{anon_id}**")
                        st.caption(f"PE={pe:.1f}" if pe else "PE=—")
                    with c2:
                        st.metric("股价", f"¥{price:.2f}" if price else "—")
                    with c3:
                        st.metric("评分", f"{score}/50")
                    with c4:
                        st.caption(sdata.get("signal_text", ""))
                        # 事件
                        if events:
                            for evt in events:
                                etype = evt.get("type", "neutral")
                                emoji = "🟢" if etype == "positive" else "🔴" if etype == "negative" else "⚪"
                                st.info(f"📰 {emoji} {evt.get('text', '')}")
                        # 买入按钮
                        bc1, bc2, bc3 = st.columns(3)
                        with bc1:
                            if st.button(f"虚拟买100股", key=f"bb1_{anon_id}"):
                                if virtual_buy(sid, anon_id, price, 100):
                                    st.success(f"买入{anon_id} 100股 @¥{price:.2f}")
                                    st.rerun()
                        with bc2:
                            if st.button(f"虚拟买500股", key=f"bb5_{anon_id}"):
                                if virtual_buy(sid, anon_id, price, 500):
                                    st.rerun()
                        with bc3:
                            n = st.number_input("股数", 100, 10000, 100, 100, key=f"bn_{anon_id}")
                            if st.button("买入", key=f"bbx_{anon_id}"):
                                if virtual_buy(sid, anon_id, price, int(n)):
                                    st.rerun()
                    st.divider()

            # 观望的也显示（不显示买入按钮）
            hold_count = sum(1 for v in signals.values() if v.get("signal") == "hold")
            if hold_count:
                with st.expander(f"⚪ 继续观望（{hold_count}只）"):
                    for anon_id, sdata in signals.items():
                        if sdata.get("signal") != "hold":
                            continue
                        pe = sdata.get("pe_ttm")
                        st.caption(f"{anon_id} | PE={pe:.1f if pe else '—'} | ¥{sdata.get('price',0):.2f} | {sdata.get('signal_text','')}")

    # ========================================
    # Tab2: 模拟持仓
    # ========================================
    with tab2:
        holdings = st.session_state.get("bt_holdings", [])
        if not holdings:
            st.info("暂无虚拟持仓，去模型推荐页买入")
        else:
            for h in holdings:
                sid = h["sid"]
                anon_id = h["anon"]
                sdata = {}
                for k, v in signals.items():
                    if v.get("sid") == sid:
                        sdata = v
                        break
                cur_price = sdata.get("price", h["cost"])
                pnl = (cur_price - h["cost"]) * h["shares"]
                pnl_p = (cur_price / h["cost"] - 1) * 100 if h["cost"] > 0 else 0
                sig = sdata.get("signal", "")
                sig_label = SIGNAL_LABELS.get(sig, "")

                c1, c2, c3, c4 = st.columns([2, 2, 2, 3])
                with c1:
                    st.markdown(f"**{anon_id}**")
                    st.caption(f"成本¥{h['cost']:.2f}")
                with c2:
                    st.metric("持有", f"{h['shares']}股")
                with c3:
                    color = "normal" if pnl >= 0 else "inverse"
                    st.metric("盈亏", f"¥{pnl:+,.0f}", f"{pnl_p:+.1f}%", delta_color=color)
                with c4:
                    if sig_label:
                        st.markdown(sig_label)
                    # 事件
                    events = sdata.get("events", [])
                    if events:
                        for evt in events:
                            etype = evt.get("type", "")
                            emoji = "🟢" if etype == "positive" else "🔴" if etype == "negative" else "⚪"
                            st.warning(f"📰 {emoji} {evt.get('text', '')}")
                    # 卖出
                    sc1, sc2, sc3 = st.columns(3)
                    with sc1:
                        if st.button("全部卖出", key=f"sa_{anon_id}"):
                            if virtual_sell(sid, h["shares"], cur_price):
                                st.rerun()
                    with sc2:
                        half = h["shares"] // 2
                        if half > 0 and st.button(f"卖{half}股", key=f"sh_{anon_id}"):
                            if virtual_sell(sid, half, cur_price):
                                st.rerun()
                    with sc3:
                        sn = st.number_input("卖出", 1, h["shares"], min(100, h["shares"]), key=f"sn_{anon_id}")
                        if st.button("卖出", key=f"sx_{anon_id}"):
                            if virtual_sell(sid, int(sn), cur_price):
                                st.rerun()
                st.divider()

    # ========================================
    # Tab3: 模拟关注
    # ========================================
    with tab3:
        wl = st.session_state.get("bt_watchlist_bt", [])
        if not wl:
            st.info("暂无关注。在模型推荐里看到感兴趣的，手动记下编号。")
        for item in wl:
            st.markdown(f"**{item}**")

    st.markdown("---")
    st.caption(f"🧪 回测模式 | 第{len(st.session_state.get('bt_game_history',[]))+1}局 | {len(st.session_state.get('bt_trade_log',[]))}笔交易")

    # 自动播放
    if st.session_state.get("bt_playing"):
        speed = st.session_state.get("bt_speed", 1)
        time_module.sleep(max(0.3, 1.0 / speed))
        if mo >= 12:
            st.session_state["bt_month"] = 1
            st.session_state["bt_year"] += 1
        else:
            st.session_state["bt_month"] += 1
        if st.session_state["bt_year"] > 2025:
            st.session_state["bt_playing"] = False
        st.rerun()
