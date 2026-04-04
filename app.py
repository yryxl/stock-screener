"""
Streamlit 管理界面
功能：持仓管理 + 重点关注表管理
数据通过 GitHub API 读写
"""

import json
import streamlit as st
import requests
import base64

# ============================================
# 页面配置
# ============================================
st.set_page_config(
    page_title="芒格选股系统",
    page_icon="📊",
    layout="centered",
)

# ============================================
# GitHub API
# ============================================

def get_github_config():
    return {
        "token": st.secrets["github"]["token"],
        "repo": st.secrets["github"]["repo"],
        "file": st.secrets["github"].get("holdings_file", "holdings.json"),
    }


def github_headers(token):
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }


def load_from_github(filename):
    try:
        cfg = get_github_config()
        url = f"https://api.github.com/repos/{cfg['repo']}/contents/{filename}"
        resp = requests.get(url, headers=github_headers(cfg["token"]), timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            content = base64.b64decode(data["content"]).decode("utf-8")
            return json.loads(content), data["sha"]
        elif resp.status_code == 404:
            return [], None
        else:
            st.error(f"读取失败: {resp.status_code}")
            return [], None
    except Exception as e:
        st.error(f"连接失败: {e}")
        return [], None


def save_to_github(filename, data, sha):
    try:
        cfg = get_github_config()
        url = f"https://api.github.com/repos/{cfg['repo']}/contents/{filename}"
        content = json.dumps(data, ensure_ascii=False, indent=2)
        encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        payload = {
            "message": f"更新{filename}",
            "content": encoded,
        }
        if sha:
            payload["sha"] = sha
        resp = requests.put(url, json=payload, headers=github_headers(cfg["token"]), timeout=10)
        if resp.status_code in (200, 201):
            return resp.json()["content"]["sha"]
        else:
            st.error(f"保存失败: {resp.status_code}")
            return None
    except Exception as e:
        st.error(f"保存失败: {e}")
        return None


# ============================================
# 页面
# ============================================

st.title("📊 芒格选股系统")

tab1, tab2 = st.tabs(["📋 持仓管理", "⭐ 重点关注表"])

# ============================================
# Tab1: 持仓管理
# ============================================
with tab1:
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
# Tab2: 重点关注表
# ============================================
with tab2:
    st.header("⭐ 重点关注表")
    st.caption("你精选的好公司，系统每天优先查看这些股票的PE买卖信号")

    if "watchlist" not in st.session_state:
        w, sha = load_from_github("watchlist.json")
        st.session_state["watchlist"] = w
        st.session_state["watchlist_sha"] = sha

    watchlist = st.session_state["watchlist"]

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
            for i, item in enumerate(items):
                global_idx = watchlist.index(item)
                col1, col2, col3 = st.columns([3, 5, 1])
                with col1:
                    st.markdown(f"**{item['name']}**")
                    st.caption(item["code"])
                with col2:
                    st.caption(item.get("note", ""))
                with col3:
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
            wcat = st.text_input("分类", placeholder="绝密配方/文化壁垒", key="w_cat")
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
- **持仓管理**：管理你实际买入的股票和份额
- **重点关注表**：你精选的好公司，系统每天优先监控PE信号
- 系统每个交易日自动分析，有买卖信号时微信通知你
""")
