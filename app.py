"""
Streamlit 管理界面
Tab1: AI推荐（全市场扫描结果，可一键关注）
Tab2: 持仓管理（含信号状态）
Tab3: 重点关注表（含信号状态）
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
    return {"token": st.secrets["github"]["token"], "repo": st.secrets["github"]["repo"]}


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
        return None
    except Exception:
        return None


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
# 加载数据
# ============================================

def load_all_data():
    if "data_loaded" not in st.session_state:
        st.session_state["holdings"], st.session_state["holdings_sha"] = load_from_github("holdings.json")
        st.session_state["watchlist"], st.session_state["watchlist_sha"] = load_from_github("watchlist.json")
        st.session_state["daily"], _ = load_from_github("daily_results.json")
        st.session_state["data_loaded"] = True

load_all_data()

# ============================================
# 页面
# ============================================

st.title("📊 芒格选股系统")
tab1, tab2, tab3 = st.tabs(["🤖 AI推荐", "📋 持仓管理", "⭐ 重点关注表"])

# ============================================
# Tab1: AI推荐（只来自全市场扫描）
# ============================================
with tab1:
    st.header("🤖 AI推荐")
    st.caption("来自全市场扫描，每周一自动更新 | 也可手动触发")

    # 检查是否有正在运行的workflow
    def check_running_workflow():
        try:
            cfg = get_github_config()
            url = f"https://api.github.com/repos/{cfg['repo']}/actions/runs?status=in_progress&per_page=1"
            resp = requests.get(url, headers=github_headers(cfg["token"]), timeout=10)
            if resp.status_code == 200:
                runs = resp.json().get("workflow_runs", [])
                if runs:
                    return True, runs[0].get("created_at", "")
            # 也检查queued状态
            url2 = f"https://api.github.com/repos/{cfg['repo']}/actions/runs?status=queued&per_page=1"
            resp2 = requests.get(url2, headers=github_headers(cfg["token"]), timeout=10)
            if resp2.status_code == 200:
                runs2 = resp2.json().get("workflow_runs", [])
                if runs2:
                    return True, runs2[0].get("created_at", "")
        except Exception:
            pass
        return False, ""

    is_running, run_time = check_running_workflow()

    if is_running:
        st.warning("⏳ 全盘扫描正在运行中...请稍候，完成后刷新页面查看结果")
    else:
        col_btn1, col_btn2 = st.columns([1, 4])
        with col_btn1:
            if st.button("🔄 立即全盘扫描", type="primary"):
                try:
                    cfg = get_github_config()
                    url = f"https://api.github.com/repos/{cfg['repo']}/actions/workflows/daily_screen.yml/dispatches"
                    resp = requests.post(url, json={"ref": "main"}, headers=github_headers(cfg["token"]), timeout=10)
                    if resp.status_code == 204:
                        st.success("✅ 已触发！大约需要10-30分钟，完成后刷新页面查看结果。")
                        st.rerun()
                    else:
                        st.error(f"触发失败: {resp.status_code}")
                except Exception as e:
                    st.error(f"触发失败: {e}")

    daily = st.session_state.get("daily", {})
    watchlist = st.session_state.get("watchlist", [])
    watchlist_codes = set(w["code"] for w in watchlist)

    if not daily:
        st.info("暂无数据，点击上方按钮触发首次扫描")
    else:
        data_info = f"数据更新：{daily.get('date', '未知')}"
        if daily.get("data_source"):
            data_info += f" | {daily['data_source']}"
        st.caption(data_info)

        ai_recs = daily.get("ai_recommendations", [])
        if not ai_recs:
            st.info("暂无AI推荐，点击上方按钮触发全盘扫描")
        else:
            for signal_key in BUY_SIGNALS:
                signal_label = SIGNAL_LABELS.get(signal_key, signal_key)
                group = [s for s in ai_recs if s.get("signal") == signal_key]
                if not group:
                    continue

                st.subheader(signal_label)
                for s in group:
                    code = s.get("code", "")
                    col1, col2, col3, col4, col5 = st.columns([2.5, 1.5, 1.5, 3, 1.5])
                    with col1:
                        st.markdown(f"**{s.get('name', '')}**（{code}）")
                    with col2:
                        pe = s.get("pe", 0)
                        st.metric("PE(TTM)", f"{pe:.1f}" if pe else "—")
                    with col3:
                        price = s.get("price", 0)
                        st.metric("股价", f"¥{price:.2f}" if price else "—")
                    with col4:
                        st.caption(s.get("signal_text", ""))
                    with col5:
                        if code in watchlist_codes:
                            st.button("已关注", key=f"ai_{code}", disabled=True)
                        else:
                            if st.button("➕关注", key=f"ai_{code}"):
                                watchlist.append({
                                    "code": code,
                                    "name": s.get("name", ""),
                                    "category": s.get("category", ""),
                                    "note": s.get("signal_text", "")[:30],
                                })
                                new_sha = save_to_github("watchlist.json", watchlist, st.session_state["watchlist_sha"])
                                if new_sha:
                                    st.session_state["watchlist_sha"] = new_sha
                                    st.session_state["watchlist"] = watchlist
                                    st.success(f"已添加 {s.get('name','')} 到关注表")
                                    st.rerun()
                st.divider()

# ============================================
# Tab2: 持仓管理（含信号状态）
# ============================================
with tab2:
    st.header("📋 我的持仓")

    holdings = st.session_state.get("holdings", [])
    daily = st.session_state.get("daily", {})

    # 持仓信号数据
    holding_data = {}
    for s in daily.get("holding_signals", []):
        holding_data[s.get("code", "")] = s

    if not holdings:
        st.info("暂无持仓")
    else:
        for i, h in enumerate(holdings):
            code = h["code"]
            sig_data = holding_data.get(code, {})
            signal = sig_data.get("signal", "")
            signal_label = SIGNAL_LABELS.get(signal, "—")
            pe = sig_data.get("pe", 0)

            col1, col2, col3, col4, col5, col6 = st.columns([2, 1.2, 1.2, 1.2, 2.5, 0.8])
            with col1:
                st.markdown(f"**{h.get('name', '未知')}**")
                st.caption(code)
            with col2:
                st.metric("股数", f"{h.get('shares', 0):,}")
            with col3:
                st.metric("成本", f"¥{h.get('cost', 0):.2f}")
            with col4:
                if pe and pe > 0:
                    st.metric("PE(TTM)", f"{pe:.1f}")
                else:
                    st.metric("PE(TTM)", "—")
            with col5:
                st.markdown(f"{signal_label}")
                st.caption(sig_data.get("signal_text", "")[:50])
            with col6:
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
                    st.success(f"已添加")
                    st.rerun()

    if holdings:
        st.subheader("✏️ 修改持仓")
        opts = {f"{h['name']}（{h['code']}）": i for i, h in enumerate(holdings)}
        sel = st.selectbox("选择", list(opts.keys()), key="edit_h")
        if sel:
            idx = opts[sel]
            h = holdings[idx]
            with st.form("edit_h_form"):
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
                        st.rerun()

# ============================================
# Tab3: 重点关注表（含信号状态）
# ============================================
with tab3:
    st.header("⭐ 重点关注表")
    st.caption("你精选的好公司，每日自动更新PE和信号状态")

    watchlist = st.session_state.get("watchlist", [])
    daily = st.session_state.get("daily", {})

    watchlist_data = {}
    for s in daily.get("watchlist_signals", []):
        watchlist_data[s.get("code", "")] = s

    if daily:
        data_info = f"数据更新：{daily.get('date', '未知')}"
        if daily.get("data_source"):
            data_info += f" | {daily['data_source']}"
        st.caption(data_info)

    if not watchlist:
        st.info("暂无关注股票")
    else:
        categories = {}
        for item in watchlist:
            cat = item.get("industry_auto", "") or item.get("category", "其他")
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

                col1, col2, col3, col4, col5 = st.columns([2.5, 1.3, 1.3, 3, 0.8])
                with col1:
                    st.markdown(f"**{item['name']}**（{code}）")
                    st.caption(item.get("note", ""))
                with col2:
                    st.metric("PE(TTM)", f"{pe:.1f}" if pe and pe > 0 else "—")
                with col3:
                    st.metric("股价", f"¥{price:.2f}" if price and price > 0 else "—")
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
            wnote = st.text_input("备注", placeholder="品牌+地理垄断", key="w_note")
        if st.form_submit_button("添加到关注表", use_container_width=True, type="primary"):
            if wcode:
                watchlist.append({"code": wcode.strip(), "name": wname.strip() or wcode.strip(), "note": wnote.strip()})
                new_sha = save_to_github("watchlist.json", watchlist, st.session_state["watchlist_sha"])
                if new_sha:
                    st.session_state["watchlist_sha"] = new_sha
                    st.success(f"已添加 {wname}")
                    st.rerun()

st.divider()
st.caption("💡 AI推荐来自全市场扫描(每周一) | 关注表和持仓每日更新PE和信号 | 系统每交易日下午5点自动运行")
