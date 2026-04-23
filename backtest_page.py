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
    check_moat, get_cash_flow_warnings, _roe_historical_avg,
    get_annual_reports_before,
)
from trade_fees import calc_fees

SIGNAL_LABELS = {
    "buy_heavy": "🟢🟢🟢 可以重仓买入",
    "buy_medium": "🟢🟢 可以中仓买入",
    "buy_light": "🟢 可以轻仓买入",
    "buy_watch": "👀 重点关注买入",
    "hold": "⚪ 继续观望",
    "hold_keep": "🔵 建议持续持有",
    "sell_watch": "🟡 关注卖出（偏高）",
    "sell_light": "🟠 可以适当卖出（偏高）",
    "sell_medium": "🔴 可以中仓卖出（明显偏高）",
    "sell_heavy": "🔴🔴 可以大量卖出（远超上限）",
    "delisted": "⛔ 已停止交易",
}


def buyback_tag(score):
    """回购加分对应的徽章"""
    if score >= 15:
        return "🏅 高回购"
    if score >= 8:
        return "⭐ 中回购"
    if score >= 3:
        return "· 少量回购"
    return ""


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
    # 松动标签注册表：{sid: {broken_at, roe_at_break, anon, problems}}
    # 一旦护城河确认恶化，永久记录，10年连续ROE≥15%才解除
    st.session_state["bt_moat_broken"] = {}
    # 清掉 number_input widget 的缓存 key，否则 rerun 后 widget 旧值会覆盖
    for wkey in ("bty", "btm"):
        if wkey in st.session_state:
            del st.session_state[wkey]

    # 重新生成匿名编号（每局不同）
    stocks = load_stock_list()
    seed = random.randint(1, 999999)
    st.session_state["bt_anon_map"] = generate_anonymous_map(list(stocks.keys()), seed=seed)
    st.session_state["bt_anon_seed"] = seed

    # 行业映射（内部用，不暴露给前端）
    st.session_state["bt_industry_map"] = {}


def _get_code_by_sid(sid):
    """sid（S01/S02...） → 真实股票代码，用于判定沪/深交所计算手续费"""
    cache = st.session_state.get("_bt_sid_to_code")
    if not cache:
        stocks = load_stock_list()
        cache = {k: v.get("code", "") for k, v in stocks.items()}
        st.session_state["_bt_sid_to_code"] = cache
    return cache.get(sid, "")


def _get_current_roe_for_sid(sid):
    """从当月 signals 拿某只股票的当前 ROE"""
    yr = st.session_state.get("bt_year", 2015)
    mo = st.session_state.get("bt_month", 1)
    anon_map = st.session_state.get("bt_anon_map", {})
    signals = get_month_signals(yr, mo, anon_map=anon_map, industry_map={})
    for _, s in signals.items():
        if s.get("sid") == sid:
            return s.get("roe")
    return None


def _build_trade_context(sid):
    """构建交易时的环境快照：市场温度、模型信号、个股指标"""
    from backtest_engine import get_hs300_temperature
    yr = st.session_state.get("bt_year", 2015)
    mo = st.session_state.get("bt_month", 1)

    # 市场温度
    temp_labels = {
        2: "牛市顶部·极度高估", 1: "偏热市·谨慎",
        0: "正常市", -1: "偏冷市·机会显现", -2: "熊市底部·大机会",
    }
    temp_lv = get_hs300_temperature(yr, mo)
    ctx = {"市场温度": temp_labels.get(temp_lv, f"等级{temp_lv}")}

    # 个股信号和指标
    anon_map = st.session_state.get("bt_anon_map", {})
    signals = get_month_signals(yr, mo, anon_map=anon_map, industry_map={})
    for _, sdata in signals.items():
        if sdata.get("sid") == sid:
            sig = sdata.get("signal", "")
            sig_text = sdata.get("signal_text", "")
            ctx["模型信号"] = SIGNAL_LABELS.get(sig, sig)
            ctx["信号说明"] = sig_text
            pe = sdata.get("pe_ttm")
            if pe is not None:
                ctx["市盈率"] = round(pe, 1)
            roe = sdata.get("roe")
            if roe is not None:
                ctx["净资产收益率"] = round(roe, 1)
            gm = sdata.get("gross_margin")
            if gm is not None:
                ctx["毛利率"] = round(gm, 1)
            debt = sdata.get("debt_ratio")
            if debt is not None:
                ctx["资产负债率"] = round(debt, 1)
            score = sdata.get("score", 0)
            ctx["模型评分"] = score
            events = sdata.get("events", [])
            if events:
                ctx["当月事件"] = [e.get("text", "") for e in events[:3]]
            break

    # 护城河状态
    try:
        is_intact, probs = check_moat(sid, yr, mo)
        ctx["护城河"] = "完好" if is_intact else f"松动（{'; '.join(probs[:2])}）"
    except Exception:
        ctx["护城河"] = "数据不足"

    return ctx


def virtual_buy(sid, anon_id, price, shares):
    date_str = f"{st.session_state['bt_year']}-{st.session_state['bt_month']:02d}"
    # 自动按上/深交所规则算买入手续费
    real_code = _get_code_by_sid(sid)
    fee_info = calc_fees(real_code, price, shares, "buy")
    fee = fee_info["total"]
    cost = price * shares + fee  # 买入总花费 = 成交金额 + 手续费
    if cost > st.session_state["bt_cash"]:
        st.error(f"资金不足！需¥{cost:,.0f}（含手续费¥{fee:,.2f}），"
                 f"可用¥{st.session_state['bt_cash']:,.0f}")
        return False
    if shares < 100 or shares % 100 != 0:
        st.error("买入最少100股，100股为单位")
        return False

    # ---- 松动标签检查：有标签的股票禁止买入 ----
    moat_broken = st.session_state.get("bt_moat_broken", {})
    if sid in moat_broken:
        info = moat_broken[sid]
        st.error(
            f"🏷️ **{anon_id} 有护城河松动标签（{info['broken_at']}），禁止买入！**\n\n"
            f"巴菲特标准：需连续10年ROE≥15%才能解除标签\n\n"
            + "\n".join(f"- {p}" for p in info.get("problems", [])[:3])
        )
        return False

    # ---- 护城河实时预检：即使没有标签，当前松动也不能买 ----
    yr = st.session_state.get("bt_year", 2015)
    mo = st.session_state.get("bt_month", 1)
    try:
        is_intact, moat_probs = check_moat(sid, yr, mo)
        if not is_intact:
            st.error(
                f"🚫 **{anon_id} 护城河当前松动，禁止买入！**\n\n"
                + "\n".join(f"- {p}" for p in moat_probs[:3])
            )
            return False
    except Exception:
        pass  # 数据不足时不阻止

    # 构建环境快照
    ctx = _build_trade_context(sid)

    st.session_state["bt_cash"] -= cost
    current_roe = _get_current_roe_for_sid(sid)

    trade_entry = {
        "type": "buy", "anon": anon_id, "shares": shares,
        "price": price, "date": date_str, "context": ctx,
        "fee": fee, "fee_breakdown": fee_info["breakdown"],
        "exchange": fee_info["exchange"], "gross": round(price * shares, 2),
        "net": fee_info["net"],  # 买入总花费
    }

    for h in st.session_state["bt_holdings"]:
        if h["sid"] == sid:
            old_shares = h["shares"]
            old = old_shares * h["cost"]
            h["shares"] += shares
            h["cost"] = (old + cost) / h["shares"]
            # 追加时间记录
            if "add_dates" not in h:
                h["add_dates"] = []
            h["add_dates"].append(date_str)
            # 加仓时更新 ROE 基准（加权平均）
            old_roe = h.get("roe_baseline") or h.get("roe_at_buy")
            if old_roe and current_roe:
                h["roe_baseline"] = (old_roe * old_shares + current_roe * shares) / h["shares"]
            st.session_state["bt_trade_log"].append(trade_entry)
            return True
    st.session_state["bt_holdings"].append({
        "sid": sid, "anon": anon_id, "shares": shares, "cost": cost / shares,
        "buy_date": date_str,
        "add_dates": [],
        "roe_at_buy": current_roe,      # 首次建仓时的 ROE
        "roe_baseline": current_roe,    # 加仓后加权基准
    })
    st.session_state["bt_trade_log"].append(trade_entry)
    _exch = "沪" if fee_info["exchange"] == "SH" else "深"
    st.toast(f"✅ 买入 {anon_id} {shares}股 @¥{price:.2f}（{_exch}市·手续费¥{fee:.2f}）",
             icon="💰")
    return True


def virtual_sell(sid, shares, current_price):
    for h in st.session_state["bt_holdings"]:
        if h["sid"] == sid:
            if shares > h["shares"]:
                st.error(f"持有{h['shares']}股，不能卖{shares}股")
                return False
            # 自动按上/深交所规则算卖出手续费（含印花税）
            real_code = _get_code_by_sid(sid)
            fee_info = calc_fees(real_code, current_price, shares, "sell_partial")
            fee = fee_info["total"]
            st.session_state["bt_cash"] += current_price * shares - fee  # 净收回

            # 构建卖出环境快照
            ctx = _build_trade_context(sid)
            # 补充持仓相关信息
            pnl_pct = (current_price / h["cost"] - 1) * 100 if h["cost"] > 0 else 0
            ctx["持仓成本"] = round(h["cost"], 2)
            ctx["持仓盈亏"] = f"{pnl_pct:+.1f}%"
            ctx["建仓时间"] = h.get("buy_date", "—")
            if h.get("roe_at_buy"):
                ctx["买入时净资产收益率"] = round(h["roe_at_buy"], 1)
            # 松动标签状态
            moat_broken = st.session_state.get("bt_moat_broken", {})
            if sid in moat_broken:
                ctx["松动标签"] = moat_broken[sid].get("broken_at", "有")

            h["shares"] -= shares
            date_str = f"{st.session_state['bt_year']}-{st.session_state['bt_month']:02d}"
            st.session_state["bt_trade_log"].append({
                "type": "sell", "anon": h["anon"], "shares": shares,
                "price": current_price, "date": date_str, "context": ctx,
                "fee": fee, "fee_breakdown": fee_info["breakdown"],
                "exchange": fee_info["exchange"],
                "gross": round(current_price * shares, 2),
                "net": fee_info["net"],  # 净收回
            })
            if h["shares"] <= 0:
                st.session_state["bt_holdings"].remove(h)
            _exch = "沪" if fee_info["exchange"] == "SH" else "深"
            st.toast(f"✅ 卖出 {h['anon']} {shares}股 @¥{current_price:.2f}"
                     f"（{_exch}市·手续费含印花税¥{fee:.2f}）",
                     icon="💰")
            return True
    return False


@st.cache_data(ttl=3600, show_spinner=False)
def _get_realtime_temperature():
    """缓存 1 小时的实时温度计"""
    try:
        from market_temperature import get_realtime_market_temperature
        return get_realtime_market_temperature()
    except Exception:
        return None


def _render_temperature_banner_in_backtest(year, month):
    """回测页面的温度计 banner，分两层显示：
    - 当前实时温度（顶部，让用户知道真实市场）
    - 回测当月温度（下方，让用户看到历史时点的温度）
    """
    from backtest_engine import get_hs300_temperature

    bg_colors = {2: "#ffebee", 1: "#fff3e0", 0: "#f5f5f5", -1: "#e3f2fd", -2: "#e8f5e9"}
    bd_colors = {2: "#d32f2f", 1: "#f57c00", 0: "#9e9e9e", -1: "#1976d2", -2: "#388e3c"}
    labels = {
        2: "🔴 牛市顶部·极度高估",
        1: "🔥 偏热市·谨慎",
        0: "⚪ 正常市",
        -1: "🧊 偏冷市·机会显现",
        -2: "❄️ 熊市底部·大机会",
    }

    # label 和 description 从 TEMP_LEVELS 动态查（保证总是最新文案）
    try:
        from market_temperature import TEMP_LEVELS as _RT_LEVELS
    except Exception:
        _RT_LEVELS = {}

    # 1. 实时温度（当前市场状态）
    realtime = _get_realtime_temperature()
    if realtime:
        lv = realtime.get("level", 0)
        lbl, desc = _RT_LEVELS.get(lv, (labels.get(lv, "⚪ 正常市"), ""))
        pe = realtime.get("current_pe_median")
        pct = realtime.get("percentile")
        as_of = realtime.get("as_of", "")
        bg = bg_colors.get(lv, "#f5f5f5")
        bd = bd_colors.get(lv, "#9e9e9e")
        st.markdown(
            f"""<div style="background:{bg};padding:12px 18px;border-left:5px solid {bd};
            border-radius:6px;margin-bottom:10px;">
            <div style="font-size:14px;color:#666;">📍 <b>当前真实市场</b></div>
            <div style="font-size:17px;font-weight:bold;margin:3px 0;">{lbl}</div>
            <div style="color:#333;font-size:13px;line-height:1.5;">{desc}</div>
            <div style="color:#888;font-size:11px;margin-top:4px;">沪深300中位数PE={pe} | 历史{pct}%分位（截至 {as_of}）</div>
            </div>""",
            unsafe_allow_html=True,
        )

    # 2. 回测当月温度（历史时点的温度）
    lv_bt = get_hs300_temperature(year, month)
    lbl_bt = labels.get(lv_bt, "⚪ 正常市")
    bg_bt = bg_colors.get(lv_bt, "#f5f5f5")
    bd_bt = bd_colors.get(lv_bt, "#9e9e9e")
    st.markdown(
        f"""<div style="background:{bg_bt};padding:10px 18px;border-left:5px solid {bd_bt};
        border-radius:6px;margin-bottom:15px;">
        <div style="font-size:14px;color:#666;">🕰️ <b>回测时点市场（{year}年{month}月）</b></div>
        <div style="font-size:16px;font-weight:bold;">{lbl_bt}</div>
        </div>""",
        unsafe_allow_html=True,
    )


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
        default_cap = max(10000, min(10000000, st.session_state.get("bt_initial_capital", 100000)))
        cap = st.number_input(
            "💰 起始资金",
            min_value=10000,
            max_value=10000000,
            value=default_cap,
            step=10000,
            key="bt_cap_s",
            help="修改后点击下方按钮生效，或按回车自动重置"
        )
        st.session_state["bt_capital_setting"] = cap

        # 起始资金变化 → 自动提示需要重置
        if cap != st.session_state.get("bt_initial_capital", 100000):
            st.warning(f"⚠️ 起始资金已改为 ¥{cap:,}，点击下方按钮应用")

        if st.button("🔄 重置（新一局）", use_container_width=True, type="primary"):
            reset_game()
            st.rerun()

        st.markdown("---")
        st.caption("📅 数据范围：2001年1月 ~ 2025年12月（部分股票上市晚，早期月份无数据会自动跳过）")

        # 年/月：用按钮控制，不用 number_input（避免 widget key 覆盖 session_state）
        # 之前用 st.number_input(key="bty") 绑定 bt_year，导致前进/后退/播放按钮改了
        # bt_year 后被 widget rerun 时的旧值覆盖回去 —— 所有按钮都失效。
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**年**")
            yc1, yc2, yc3 = st.columns([1, 2, 1])
            with yc1:
                if st.button("−", key="yr_dec"):
                    st.session_state["bt_year"] = max(2001, st.session_state["bt_year"] - 1)
                    st.rerun()
            with yc2:
                st.markdown(f"<div style='text-align:center;font-size:20px;padding:4px 0;'>{st.session_state['bt_year']}</div>", unsafe_allow_html=True)
            with yc3:
                if st.button("+", key="yr_inc"):
                    st.session_state["bt_year"] = min(2025, st.session_state["bt_year"] + 1)
                    st.rerun()
        with c2:
            st.markdown("**月**")
            mc1, mc2, mc3 = st.columns([1, 2, 1])
            with mc1:
                if st.button("−", key="mo_dec"):
                    st.session_state["bt_month"] = max(1, st.session_state["bt_month"] - 1)
                    st.rerun()
            with mc2:
                st.markdown(f"<div style='text-align:center;font-size:20px;padding:4px 0;'>{st.session_state['bt_month']}</div>", unsafe_allow_html=True)
            with mc3:
                if st.button("+", key="mo_inc"):
                    st.session_state["bt_month"] = min(12, st.session_state["bt_month"] + 1)
                    st.rerun()

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

    # 温度计 banner（显示当前真实市场温度 + 回测时点温度）
    _render_temperature_banner_in_backtest(yr, mo)

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
    # Tab1: 模型推荐（所有档位）
    # ========================================
    with tab1:
        if not signals:
            st.info(f"{yr}年{mo}月暂无数据")
        else:
            # 按档位分组展示
            all_signals_order = [
                "buy_heavy", "buy_medium", "buy_light", "buy_watch",
                "sell_heavy", "sell_medium", "sell_light", "sell_watch",
            ]
            for sig_key in all_signals_order:
                group = {k: v for k, v in signals.items() if v.get("signal") == sig_key}
                if not group:
                    continue
                st.subheader(SIGNAL_LABELS.get(sig_key, sig_key))

                # 按"回购加分 > 评分"排序，体现买入优先级
                items = sorted(
                    group.items(),
                    key=lambda kv: (
                        -(kv[1].get("buyback_score") or 0),
                        -(kv[1].get("score") or 0),
                    ),
                )

                for anon_id, sdata in items:
                    sid = sdata["sid"]
                    price = sdata.get("price", 0)
                    pe = sdata.get("pe_ttm")
                    roe = sdata.get("roe")
                    gm = sdata.get("gross_margin")
                    debt = sdata.get("debt_ratio")
                    div_y = sdata.get("dividend_yield", 0)
                    score = sdata.get("score", 0)
                    bb_score = sdata.get("buyback_score", 0)
                    bb_yi = sdata.get("buyback_yi", 0)
                    events = sdata.get("events", [])
                    is_buy = sig_key.startswith("buy_")

                    # 股票标题行（含回购徽章）
                    title_parts = [f"**{anon_id}**"]
                    bb_tag = buyback_tag(bb_score)
                    if bb_tag:
                        title_parts.append(bb_tag)

                    c1, c2, c3, c4 = st.columns([2.2, 1.3, 1.3, 3.2])
                    with c1:
                        st.markdown(" ".join(title_parts))
                        # 关键财务指标一行
                        fin_parts = []
                        if pe is not None:
                            fin_parts.append(f"市盈率 {pe:.1f}")
                        if roe is not None:
                            fin_parts.append(f"净收益率 {roe:.1f}%")
                        if gm is not None:
                            fin_parts.append(f"毛利 {gm:.0f}%")
                        if debt is not None:
                            fin_parts.append(f"负债 {debt:.0f}%")
                        if div_y:
                            fin_parts.append(f"股息 {div_y:.1f}%")
                        st.caption(" | ".join(fin_parts) if fin_parts else "—")
                    with c2:
                        st.metric("股价", f"¥{price:.2f}" if price else "—")
                    with c3:
                        st.metric("评分", f"{score}/50")
                        if bb_score > 0:
                            st.caption(f"近5年回购 {bb_yi:.1f}亿")
                    with c4:
                        st.caption(sdata.get("signal_text", ""))
                        # 事件
                        if events:
                            for evt in events:
                                etype = evt.get("type", "neutral")
                                emoji = "🟢" if etype == "positive" else "🔴" if etype == "negative" else "⚪"
                                st.info(f"📰 {emoji} {evt.get('text', '')}")
                        # 松动标签警告（有标签的股票即使信号为买入也不可操作）
                        _moat_reg = st.session_state.get("bt_moat_broken", {})
                        if sid in _moat_reg:
                            _bi = _moat_reg[sid]
                            st.error(
                                f"🏷️ **松动标签**（{_bi['broken_at']}）"
                                f"：需10年连续ROE≥15%才解除，禁止买入"
                            )
                        # 买入按钮 + 加入关注表（仅买入档位 且 无松动标签 才显示）
                        if is_buy and price > 0 and sid not in _moat_reg:
                            bc1, bc2, bc3, bc4 = st.columns(4)
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
                                n = st.number_input("股数", 100, 100000, 100, 100, key=f"bn_{anon_id}")
                                if st.button("买入", key=f"bbx_{anon_id}"):
                                    if virtual_buy(sid, anon_id, price, int(n)):
                                        st.rerun()
                            with bc4:
                                if st.button(f"⭐ 加入关注", key=f"bw_{anon_id}"):
                                    wl = st.session_state.get("bt_watchlist_bt", [])
                                    if anon_id not in wl:
                                        wl.append(anon_id)
                                        st.session_state["bt_watchlist_bt"] = wl
                                        st.success(f"{anon_id} 已加入关注表")
                                    else:
                                        st.info(f"{anon_id} 已在关注表中")
                    st.divider()

            # 观望的也显示（不显示买入按钮）
            hold_count = sum(1 for v in signals.values() if v.get("signal") == "hold")
            if hold_count:
                with st.expander(f"⚪ 继续观望（{hold_count}只）"):
                    for anon_id, sdata in signals.items():
                        if sdata.get("signal") != "hold":
                            continue
                        pe = sdata.get("pe_ttm")
                        pe_str = f"{pe:.1f}" if pe else "—"
                        _tag = "🏷️松动 | " if sdata.get("sid") in st.session_state.get("bt_moat_broken", {}) else ""
                        st.caption(f"{_tag}{anon_id} | 市盈率={pe_str} | ¥{sdata.get('price',0):.2f} | {sdata.get('signal_text','')}")

    # ========================================
    # Tab2: 模拟持仓（含护城河松动和消费龙头警示）
    # ========================================
    with tab2:
        holdings = st.session_state.get("bt_holdings", [])
        if not holdings:
            st.info("暂无虚拟持仓，去模型推荐页买入")
        else:
            # ---- 松动标签恢复校验（每次渲染时检查）----
            # 巴菲特铁律：10 年连续 ROE ≥ 15% 才能解除松动标签
            MOAT_RECOVERY_YEARS = 10
            _moat_reg = st.session_state.get("bt_moat_broken", {})
            _to_remove = []
            for _sid, _info in _moat_reg.items():
                _broken_yr = int(_info["broken_at"][:4])
                if yr - _broken_yr >= MOAT_RECOVERY_YEARS:
                    _reps = get_annual_reports_before(_sid, yr, mo, lookback_years=MOAT_RECOVERY_YEARS + 1)
                    _post = [r for r in _reps if int(str(r["date"])[:4]) >= _broken_yr]
                    _roes = [r.get("roe") for r in _post if r.get("roe") is not None]
                    if len(_roes) >= MOAT_RECOVERY_YEARS and all(r >= 15 for r in _roes):
                        _to_remove.append(_sid)
                        st.success(
                            f"✅ **{_info.get('anon', _sid)} 松动标签解除！**"
                            f"（松动后连续{MOAT_RECOVERY_YEARS}年ROE≥15%，恢复信任）"
                        )
            for _sid in _to_remove:
                del _moat_reg[_sid]

            # 计算总持仓市值（用于各股占比）
            total_holding_value = 0
            for h in holdings:
                sdata_tmp = {}
                for k, v in signals.items():
                    if v.get("sid") == h["sid"]:
                        sdata_tmp = v
                        break
                total_holding_value += sdata_tmp.get("price", h["cost"]) * h["shares"]
            total_value = st.session_state.get("bt_cash", 0) + total_holding_value

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
                holding_value = cur_price * h["shares"]
                hold_pct = (holding_value / total_value * 100) if total_value > 0 else 0
                sig = sdata.get("signal", "")
                sig_label = SIGNAL_LABELS.get(sig, "")

                # 建仓/追加时间
                buy_date = h.get("buy_date", "—")
                add_dates = h.get("add_dates", [])
                time_info = f"建仓 {buy_date}"
                if add_dates:
                    time_info += f" | 追加 {', '.join(add_dates[-3:])}"
                    if len(add_dates) > 3:
                        time_info += f" 等{len(add_dates)}次"

                c1, c2, c3, c4 = st.columns([2.5, 1.5, 2, 3])
                with c1:
                    st.markdown(f"**{anon_id}**")
                    st.caption(f"成本¥{h['cost']:.2f} | 占比 **{hold_pct:.1f}%** | {time_info}")
                with c2:
                    st.metric("持有", f"{h['shares']}股", f"¥{holding_value:,.0f}")
                with c3:
                    color = "normal" if pnl >= 0 else "inverse"
                    st.metric("盈亏", f"¥{pnl:+,.0f}", f"{pnl_p:+.1f}%", delta_color=color)
                with c4:
                    if sig_label:
                        st.markdown(sig_label)
                    events = sdata.get("events", [])
                    if events:
                        for evt in events:
                            etype = evt.get("type", "")
                            emoji = "🟢" if etype == "positive" else "🔴" if etype == "negative" else "⚪"
                            st.warning(f"📰 {emoji} {evt.get('text', '')}")
                    # 卖出 + 买入（加仓）按钮
                    sc1, sc2, sc3, sc4 = st.columns(4)
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
                    with sc4:
                        # 加仓按钮（和推荐页同功能）
                        bn = st.number_input("加仓", 100, 100000, 100, 100, key=f"add_{anon_id}")
                        if st.button("买入加仓", key=f"ab_{anon_id}"):
                            if cur_price > 0 and virtual_buy(sid, anon_id, cur_price, int(bn)):
                                st.success(f"加仓 {anon_id} {bn}股 @¥{cur_price:.2f}")
                                st.rerun()

                # ---- 松动标签持久显示 ----
                moat_broken = st.session_state.get("bt_moat_broken", {})
                if sid in moat_broken:
                    bi = moat_broken[sid]
                    st.error(
                        f"🏷️ **{anon_id} 护城河松动标签**"
                        f"（{bi['broken_at']} 确认恶化）\n\n"
                        + "\n".join(f"- {p}" for p in bi.get("problems", [])[:3])
                        + f"\n\n**需连续10年ROE≥15%才能解除，建议清仓**"
                    )

                # 护城河松动分级警告（黄色观察 vs 红色行动）
                try:
                    is_intact, moat_problems = check_moat(sid, yr, mo)
                    if not is_intact:
                        # 更新连续松动计数
                        prev_alert = h.get("moat_alert_months", 0)
                        h["moat_alert_months"] = prev_alert + 1
                        n_probs = len(moat_problems)

                        if h["moat_alert_months"] >= 2 or n_probs >= 2:
                            # 🚨 红色行动 → 自动打上松动标签
                            if sid not in moat_broken:
                                cur_roe_val = sdata.get("roe")
                                st.session_state.setdefault("bt_moat_broken", {})[sid] = {
                                    "broken_at": f"{yr}-{mo:02d}",
                                    "roe_at_break": cur_roe_val,
                                    "anon": anon_id,
                                    "problems": moat_problems[:3],
                                }
                            st.error(
                                f"🚨 **{anon_id} 护城河确认恶化**"
                                f"（连续{h['moat_alert_months']}月·{n_probs}条规则）\n\n"
                                + "\n".join(f"- {p}" for p in moat_problems[:3])
                                + "\n\n**已打上松动标签，建议清仓**"
                            )
                        else:
                            # ⚠ 黄色观察
                            st.warning(
                                f"⚠ **{anon_id} 护城河首次松动**（观察中）\n\n"
                                + "\n".join(f"- {p}" for p in moat_problems[:3])
                                + "\n\n暂不动，等下个月确认是否持续"
                            )
                    else:
                        # 数据恢复 → 清零观察计数（但松动标签不消失！）
                        h["moat_alert_months"] = 0
                except Exception:
                    pass

                # 买入后 ROE 监测（长春高新教训：3年从28%滑到12%无预警）
                roe_baseline = h.get("roe_baseline")
                if roe_baseline and roe_baseline > 0:
                    cur_roe = sdata.get("roe") if sdata else None
                    if cur_roe is not None:
                        roe_drop = roe_baseline - cur_roe
                        if roe_drop >= 15 or cur_roe < 15:
                            st.error(
                                f"🔴 **{anon_id} ROE严重衰退**\n\n"
                                f"买入基准 **{roe_baseline:.1f}%** → 当前 **{cur_roe:.1f}%**"
                                f"（降 {roe_drop:.1f}pp）\n\n"
                                f"**建议：立即减仓或清仓**"
                            )
                        elif roe_drop >= 10:
                            st.warning(
                                f"🟠 **{anon_id} ROE明显下滑**\n\n"
                                f"买入基准 **{roe_baseline:.1f}%** → 当前 **{cur_roe:.1f}%**"
                                f"（降 {roe_drop:.1f}pp）\n\n"
                                f"**建议：考虑减仓一半**"
                            )
                        elif roe_drop >= 5:
                            st.info(
                                f"⚠ **{anon_id} ROE轻微下滑**\n\n"
                                f"买入基准 **{roe_baseline:.1f}%** → 当前 **{cur_roe:.1f}%**"
                                f"（降 {roe_drop:.1f}pp）\n\n"
                                f"持续观察，暂不操作"
                            )

                # 消费龙头现金流警示（已豁免但需重点关注）
                try:
                    cf_warnings = get_cash_flow_warnings(sid, yr, mo)
                    if cf_warnings:
                        for w in cf_warnings:
                            st.warning(f"⚠️ **{anon_id} 重点关注**：{w}")
                except Exception:
                    pass

                st.divider()

    # ========================================
    # Tab3: 模拟关注
    # ========================================
    with tab3:
        wl = st.session_state.get("bt_watchlist_bt", [])
        if not wl:
            st.info("暂无关注。在模型推荐页点 ⭐ 加入关注。")
        else:
            for item in wl:
                sdata = signals.get(item, {})
                pe = sdata.get("pe_ttm")
                price = sdata.get("price", 0)
                score = sdata.get("score", 0)
                sig_text = sdata.get("signal_text", "")

                c1, c2, c3, c4, c5 = st.columns([1.5, 1.2, 1.2, 3, 1])
                with c1:
                    st.markdown(f"**{item}**")
                with c2:
                    st.metric("股价", f"¥{price:.2f}" if price else "—")
                with c3:
                    st.metric("PE", f"{pe:.1f}" if pe else "—")
                with c4:
                    st.caption(sig_text[:80] if sig_text else "—")
                with c5:
                    if st.button("移除", key=f"rw_{item}"):
                        wl.remove(item)
                        st.session_state["bt_watchlist_bt"] = wl
                        st.rerun()
                st.divider()

    st.markdown("---")
    st.caption(f"🧪 回测模式 | 第{len(st.session_state.get('bt_game_history',[]))+1}局 | {len(st.session_state.get('bt_trade_log',[]))}笔交易")

    # 保存本局操作数据（本地下载 + GitHub 云端双保存）
    trade_log = st.session_state.get("bt_trade_log", [])
    if trade_log:
        import json as _json
        from datetime import datetime as _dt
        save_data = {
            "saved_at": _dt.now().strftime("%Y-%m-%d %H:%M"),
            "game_number": len(st.session_state.get("bt_game_history", [])) + 1,
            "initial_capital": st.session_state.get("bt_initial_capital", 100000),
            "current_cash": round(st.session_state.get("bt_cash", 0), 2),
            "start_year_month": "2015-01",
            "current_year": st.session_state.get("bt_year"),
            "current_month": st.session_state.get("bt_month"),
            "holdings": [
                {
                    "anon": h["anon"],
                    "shares": h["shares"],
                    "cost": round(h["cost"], 2),
                }
                for h in st.session_state.get("bt_holdings", [])
            ],
            "portfolio_value": round(portfolio_value, 2),
            "total_value": round(st.session_state.get("bt_cash", 0) + portfolio_value, 2),
            "pnl_pct": round(
                ((st.session_state.get("bt_cash", 0) + portfolio_value)
                 / max(st.session_state.get("bt_initial_capital", 100000), 1) - 1) * 100, 2
            ),
            "trade_count": len(trade_log),
            "trade_log": trade_log,
            "watchlist": st.session_state.get("bt_watchlist_bt", []),
            "moat_broken": {
                sid: {
                    "anon": info.get("anon", ""),
                    "broken_at": info.get("broken_at", ""),
                    "problems": info.get("problems", []),
                }
                for sid, info in st.session_state.get("bt_moat_broken", {}).items()
            },
        }
        save_json_str = _json.dumps(save_data, ensure_ascii=False, indent=2)
        filename = f"backtest_game_{save_data['game_number']}_{_dt.now().strftime('%Y%m%d_%H%M')}.json"

        sc1, sc2 = st.columns(2)
        with sc1:
            # 本地下载
            st.download_button(
                "💾 下载到本地",
                data=save_json_str,
                file_name=filename,
                mime="application/json",
                use_container_width=True,
            )
        with sc2:
            # 保存到指定目录（本地分析用 + GitHub 云端备份）
            if st.button("💾 保存到分析目录", use_container_width=True):
                import os
                # 双路径保存：分析目录 + GitHub 云端
                save_dirs = [
                    r"G:\Claude Code\ask\手动回测",           # 用户指定的分析目录
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_games"),  # GitHub 云端
                ]
                saved = []
                for d in save_dirs:
                    try:
                        os.makedirs(d, exist_ok=True)
                        p = os.path.join(d, filename)
                        with open(p, "w", encoding="utf-8") as f:
                            f.write(save_json_str)
                        saved.append(p)
                    except Exception:
                        pass
                if saved:
                    st.success(f"✅ 已保存到：\n" + "\n".join(f"- {p}" for p in saved))

    # 自动播放（使用 st.empty 占位 + 倒计时重跑）
    # 之前放在文件末尾用 time.sleep + st.rerun，但 Streamlit Cloud 上
    # sleep 会被 session timeout 打断导致播放无效。
    # 改用 streamlit 原生的 rerun 触发：在页面渲染完后立即检查是否需要前进
    if st.session_state.get("bt_playing"):
        speed = st.session_state.get("bt_speed", 1)
        # 前进一个月
        if mo >= 12:
            st.session_state["bt_month"] = 1
            st.session_state["bt_year"] += 1
        else:
            st.session_state["bt_month"] += 1
        if st.session_state["bt_year"] > 2025:
            st.session_state["bt_playing"] = False
        else:
            # 用 sleep 控制速度，然后 rerun
            time_module.sleep(max(0.3, 1.5 / speed))
            st.rerun()
