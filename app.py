"""
Streamlit 管理界面
Tab1: AI推荐（每日筛选结果）
Tab2: 持仓管理
Tab3: 重点关注表（含实时数据+信号）
"""

import json
import streamlit as st
import requests
import base64

st.set_page_config(page_title="芒格选股系统", page_icon="📊", layout="wide")

# ============================================
# GitHub API
# ============================================

def get_github_config():
    return {
        "token": st.secrets["github"]["token"],
        "repo": st.secrets["github"]["repo"],
    }


def github_headers(token):
    return {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}


def load_from_github(filename):
    try:
        cfg = get_github_config()
        url = f"https://api.github.com/repos/{cfg['repo']}/contents/{filename}"
        resp = requests.get(url, headers=github_headers(cfg["token"]), timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            content = base64.b64decode(data["content"]).decode("utf-8")
            return json.loads(content), data["sha"]
        return [], None
    except Exception as e:
        st.error(f"读取失败: {e}")
        return [], None


def save_to_github(filename, data, sha):
    try:
        cfg = get_github_config()
        url = f"https://api.github.com/repos/{cfg['repo']}/contents/{filename}"
        content = json.dumps(data, ensure_ascii=False, indent=2)
        encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        payload = {"message": f"更新{filename}", "content": encoded}
        if sha:
            payload["sha"] = sha
        resp = requests.put(url, json=payload, headers=github_headers(cfg["token"]), timeout=10)
        if resp.status_code in (200, 201):
            return resp.json()["content"]["sha"]
        st.error(f"保存失败: {resp.status_code}")
        return None
    except Exception as e:
        st.error(f"保存失败: {e}")
        return None


# ============================================
# 信号标签
# ============================================

SIGNAL_LABELS = {
    "buy_heavy": "🔴 可以重仓买入",
    "buy_medium": "🟠 可以中仓买入",
    "buy_light": "🟡 可以轻仓买入",
    "buy_watch": "⚪ 重点关注买入",
    "hold_keep": "🟢 建议持续持有",
    "hold": "⚪ 持有观察",
    "sell_watch": "⚪ 重点关注卖出",
    "sell_light": "🟡 可以适当卖出",
    "sell_medium": "🟠 可以中仓卖出",
    "sell_heavy": "🔴 可以大量卖出",
    "true_decline": "⛔ 基本面恶化",
}

BUY_SIGNALS = ["buy_heavy", "buy_medium", "buy_light", "buy_watch"]

# ============================================
# 页面
# ============================================

st.title("📊 芒格选股系统")

tab1, tab2, tab3 = st.tabs(["🤖 AI推荐", "📋 持仓管理", "⭐ 重点关注表"])

# ============================================
# Tab1: AI推荐
# ============================================
with tab1:
    st.header("🤖 AI每日推荐")

    results, _ = load_from_github("daily_results.json")

    if not results:
        st.info("暂无数据，等待首次运行后自动更新")
    else:
        st.caption(f"数据更新时间：{results.get('date', '未知')}")

        # 合并所有买入信号
        all_buy = []
        for s in results.get("watchlist_signals", []):
            if s.get("signal") in BUY_SIGNALS:
                s["source"] = "关注表"
                all_buy.append(s)
        for s in results.get("candidates", []):
            if s.get("signal") in BUY_SIGNALS:
                s["source"] = "全市场筛选"
                all_buy.append(s)
        for s in results.get("false_declines", []):
            s["source"] = "假跌机会"
            all_buy.append(s)

        if not all_buy:
            st.info("今日无买入推荐，所有股票在合理区间")
        else:
            # 按信号等级分组显示
            for signal_key in BUY_SIGNALS:
                signal_label = SIGNAL_LABELS.get(signal_key, signal_key)
                group = [s for s in all_buy if s.get("signal") == signal_key]
                if not group:
                    continue

                st.subheader(signal_label)
                for s in group:
                    col1, col2, col3, col4 = st.columns([3, 2, 2, 3])
                    with col1:
                        st.markdown(f"**{s.get('name', '')}**（{s.get('code', '')}）")
                        cat = s.get("category", "")
                        src = s.get("source", "")
                        if cat:
                            st.caption(f"[{cat}] {src}")
                    with col2:
                        pe = s.get("pe", 0)
                        st.metric("PE(TTM)", f"{pe:.1f}" if pe else "—")
                    with col3:
                        price = s.get("price", 0)
                        st.metric("股价", f"¥{price:.2f}" if price else "—")
                    with col4:
                        st.caption(s.get("signal_text", ""))
                st.divider()

        # 持仓信号
        holding_sigs = results.get("holding_signals", [])
        true_declines = results.get("true_declines", [])
        if holding_sigs or true_declines:
            st.subheader("📉 持仓信号")
            for s in holding_sigs + true_declines:
                signal = s.get("signal", "")
                label = SIGNAL_LABELS.get(signal, signal)
                col1, col2, col3 = st.columns([3, 2, 5])
                with col1:
                    st.markdown(f"**{s.get('name', '')}**（{s.get('code', '')}）")
                with col2:
                    st.markdown(f"{label}")
                with col3:
                    st.caption(s.get("signal_text", ""))

# ============================================
# Tab2: 持仓管理
# ============================================
with tab2:
    st.header("📋 我的持仓")

    if "holdings" not in st.session_state:
        h, sha = load_from_github("holdings.json")
        st.session_state["holdings"] = h
        st.session_state["holdings_sha"] = sha

    holdings = st.session_state["holdings"]

    if not holdings:
        st.info("暂无持仓")
    else:
        for i, h in enumerate(holdings):
            col1, col2, col3, col4, col5 = st.columns([2, 1.5, 1.5, 1.5, 1])
            with col1:
                st.markdown(f"**{h.get('name', '未知')}**")
                st.caption(h["code"])
            with col2:
                st.metric("股数", f"{h.get('shares', 0):,}")
            with col3:
                st.metric("成本价", f"¥{h.get('cost', 0):.2f}")
            with col4:
                total = h.get("shares", 0) * h.get("cost", 0)
                st.metric("成本", f"¥{total:,.0f}")
            with col5:
                st.write("")
                if st.button("🗑️", key=f"del_h_{i}"):
                    holdings.pop(i)
                    new_sha = save_to_github("holdings.json", holdings, st.session_state["holdings_sha"])
                    if new_sha:
                        st.session_state["holdings_sha"] = new_sha
                        st.rerun()
            st.divider()

    st.subheader("➕ 添加持仓")
    with st.form("add_holding", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            new_code = st.text_input("股票代码", placeholder="600519")
            new_shares = st.number_input("股数", min_value=1, value=100, step=100)
        with c2:
            new_name = st.text_input("名称", placeholder="贵州茅台")
            new_cost = st.number_input("成本价", min_value=0.01, value=10.0, step=0.01, format="%.2f")
        if st.form_submit_button("添加", use_container_width=True, type="primary"):
            if new_code:
                holdings.append({"code": new_code.strip(), "name": new_name.strip() or new_code.strip(), "shares": int(new_shares), "cost": float(new_cost)})
                new_sha = save_to_github("holdings.json", holdings, st.session_state["holdings_sha"])
                if new_sha:
                    st.session_state["holdings_sha"] = new_sha
                    st.success(f"已添加 {new_name}")
                    st.rerun()

    if holdings:
        st.subheader("✏️ 修改持仓")
        opts = {f"{h['name']}（{h['code']}）": i for i, h in enumerate(holdings)}
        sel = st.selectbox("选择股票", list(opts.keys()), key="edit_holding")
        if sel:
            idx = opts[sel]
            h = holdings[idx]
            with st.form("edit_holding_form"):
                c1, c2 = st.columns(2)
                with c1:
                    es = st.number_input("新股数", min_value=0, value=h.get("shares", 0), step=100)
                with c2:
                    ec = st.number_input("新成本价", min_value=0.01, value=float(h.get("cost", 10)), step=0.01, format="%.2f")
                if st.form_submit_button("更新", use_container_width=True):
                    if es == 0:
                        holdings.pop(idx)
                    else:
                        holdings[idx]["shares"] = int(es)
                        holdings[idx]["cost"] = float(ec)
                    new_sha = save_to_github("holdings.json", holdings, st.session_state["holdings_sha"])
                    if new_sha:
                        st.session_state["holdings_sha"] = new_sha
                        st.success("已更新")
                        st.rerun()

# ============================================
# Tab3: 重点关注表（含实时数据+信号）
# ============================================
with tab3:
    st.header("⭐ 重点关注表")
    st.caption("每日自动更新PE(TTM)和买卖信号")

    if "watchlist" not in st.session_state:
        w, sha = load_from_github("watchlist.json")
        st.session_state["watchlist"] = w
        st.session_state["watchlist_sha"] = sha

    watchlist = st.session_state["watchlist"]

    # 从每日结果中获取实时数据
    if "daily_results" not in st.session_state:
        dr, _ = load_from_github("daily_results.json")
        st.session_state["daily_results"] = dr

    daily = st.session_state["daily_results"]
    watchlist_data = {}
    if daily:
        st.caption(f"数据更新：{daily.get('date', '未知')}")
        for s in daily.get("watchlist_signals", []):
            watchlist_data[s.get("code", "")] = s

    if not watchlist:
        st.info("暂无关注股票")
    else:
        # 按分类显示
        categories = {}
        for item in watchlist:
            cat = item.get("category", "其他")
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(item)

        for cat, items in categories.items():
            st.subheader(f"🏷️ {cat}")
            for item in items:
                code = item["code"]
                global_idx = watchlist.index(item)
                data = watchlist_data.get(code, {})

                pe = data.get("pe", 0)
                price = data.get("price", 0)
                signal = data.get("signal", "")
                signal_text = data.get("signal_text", "")
                signal_label = SIGNAL_LABELS.get(signal, "—")

                col1, col2, col3, col4, col5 = st.columns([2.5, 1.5, 1.5, 3, 0.8])
                with col1:
                    st.markdown(f"**{item['name']}**（{code}）")
                    st.caption(item.get("note", ""))
                with col2:
                    if pe and pe > 0:
                        st.metric("PE(TTM)", f"{pe:.1f}")
                    else:
                        st.metric("PE(TTM)", "—")
                with col3:
                    if price and price > 0:
                        st.metric("股价", f"¥{price:.2f}")
                    else:
                        st.metric("股价", "—")
                with col4:
                    st.markdown(f"{signal_label}")
                    if signal_text:
                        st.caption(signal_text[:60])
                with col5:
                    if st.button("🗑️", key=f"del_w_{global_idx}"):
                        watchlist.pop(global_idx)
                        new_sha = save_to_github("watchlist.json", watchlist, st.session_state["watchlist_sha"])
                        if new_sha:
                            st.session_state["watchlist_sha"] = new_sha
                            st.rerun()
            st.divider()

    st.subheader("➕ 添加关注")
    with st.form("add_watch", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            wcode = st.text_input("股票代码", placeholder="600519", key="w_code")
            wname = st.text_input("名称", placeholder="贵州茅台", key="w_name")
        with c2:
            wcat = st.text_input("分类", placeholder="白酒", key="w_cat")
            wnote = st.text_input("备注", placeholder="品牌+地理+工艺三重垄断", key="w_note")
        if st.form_submit_button("添加到关注表", use_container_width=True, type="primary"):
            if wcode:
                watchlist.append({"code": wcode.strip(), "name": wname.strip() or wcode.strip(), "category": wcat.strip(), "note": wnote.strip()})
                new_sha = save_to_github("watchlist.json", watchlist, st.session_state["watchlist_sha"])
                if new_sha:
                    st.session_state["watchlist_sha"] = new_sha
                    st.success(f"已添加 {wname}")
                    st.rerun()

# ============================================
# 底部
# ============================================
st.divider()
st.caption("""
💡 **使用说明**
- **AI推荐**：每日自动更新，显示模型推荐的买入/卖出信号及PE(TTM)等数据
- **持仓管理**：管理你实际买入的股票和份额
- **重点关注表**：你精选的好公司，显示实时PE和信号
- 系统每个交易日下午5点自动分析，有信号微信通知你
""")
