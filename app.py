"""
Streamlit 持仓管理界面
功能：查看持仓、添加股票、修改份额、删除股票
数据通过 GitHub API 读写 holdings.json
"""

import json
import streamlit as st
import requests
import base64

# ============================================
# 页面配置
# ============================================
st.set_page_config(
    page_title="芒格选股 - 持仓管理",
    page_icon="📊",
    layout="centered",
)

# ============================================
# GitHub API 读写 holdings.json
# ============================================

def get_github_config():
    """从 Streamlit secrets 获取 GitHub 配置"""
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


def load_holdings_from_github():
    """从 GitHub 仓库读取 holdings.json"""
    try:
        cfg = get_github_config()
        url = f"https://api.github.com/repos/{cfg['repo']}/contents/{cfg['file']}"
        resp = requests.get(url, headers=github_headers(cfg["token"]), timeout=10)

        if resp.status_code == 200:
            data = resp.json()
            content = base64.b64decode(data["content"]).decode("utf-8")
            st.session_state["holdings_sha"] = data["sha"]
            return json.loads(content)
        elif resp.status_code == 404:
            # 文件不存在，返回空列表
            st.session_state["holdings_sha"] = None
            return []
        else:
            st.error(f"读取持仓数据失败: {resp.status_code}")
            return []
    except Exception as e:
        st.error(f"连接 GitHub 失败: {e}")
        return []


def save_holdings_to_github(holdings):
    """将 holdings.json 保存到 GitHub 仓库"""
    try:
        cfg = get_github_config()
        url = f"https://api.github.com/repos/{cfg['repo']}/contents/{cfg['file']}"
        content = json.dumps(holdings, ensure_ascii=False, indent=2)
        encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")

        payload = {
            "message": f"更新持仓数据 ({len(holdings)}只股票)",
            "content": encoded,
        }

        # 如果文件已存在，需要提供sha
        sha = st.session_state.get("holdings_sha")
        if sha:
            payload["sha"] = sha

        resp = requests.put(url, json=payload, headers=github_headers(cfg["token"]), timeout=10)

        if resp.status_code in (200, 201):
            st.session_state["holdings_sha"] = resp.json()["content"]["sha"]
            return True
        else:
            st.error(f"保存失败: {resp.status_code} - {resp.text}")
            return False
    except Exception as e:
        st.error(f"保存失败: {e}")
        return False


# ============================================
# 页面内容
# ============================================

st.title("📊 芒格选股 - 持仓管理")
st.caption("管理你的持仓股票，系统每天自动分析并通过微信推送买卖信号")

# 加载数据
if "holdings" not in st.session_state:
    st.session_state["holdings"] = load_holdings_from_github()

holdings = st.session_state["holdings"]

# ============================================
# 当前持仓列表
# ============================================

st.header("📋 当前持仓")

if not holdings:
    st.info("暂无持仓，请在下方添加股票")
else:
    for i, h in enumerate(holdings):
        col1, col2, col3, col4, col5 = st.columns([2, 1.5, 1.5, 1.5, 1])

        with col1:
            st.markdown(f"**{h.get('name', '未知')}**")
            st.caption(h["code"])
        with col2:
            st.metric("持有股数", f"{h.get('shares', 0):,}")
        with col3:
            st.metric("成本价", f"¥{h.get('cost', 0):.2f}")
        with col4:
            total = h.get("shares", 0) * h.get("cost", 0)
            st.metric("持仓成本", f"¥{total:,.0f}")
        with col5:
            st.write("")  # 占位
            if st.button("🗑️", key=f"del_{i}", help="删除此持仓"):
                holdings.pop(i)
                if save_holdings_to_github(holdings):
                    st.success("已删除")
                    st.rerun()

        st.divider()

    # 持仓汇总
    total_cost = sum(h.get("shares", 0) * h.get("cost", 0) for h in holdings)
    st.markdown(f"**持仓总计：{len(holdings)} 只股票，总成本 ¥{total_cost:,.0f}**")

# ============================================
# 添加持仓
# ============================================

st.header("➕ 添加持仓")

with st.form("add_form", clear_on_submit=True):
    col_a, col_b = st.columns(2)
    with col_a:
        new_code = st.text_input("股票代码", placeholder="例如：600519")
        new_shares = st.number_input("持有股数", min_value=1, value=100, step=100)
    with col_b:
        new_name = st.text_input("股票名称", placeholder="例如：贵州茅台")
        new_cost = st.number_input("买入成本价（元）", min_value=0.01, value=10.00, step=0.01, format="%.2f")

    submitted = st.form_submit_button("添加", use_container_width=True, type="primary")

    if submitted:
        if not new_code:
            st.error("请输入股票代码")
        elif any(h["code"] == new_code for h in holdings):
            st.error(f"股票 {new_code} 已在持仓中，请在下方修改份额")
        else:
            new_holding = {
                "code": new_code.strip(),
                "name": new_name.strip() or new_code.strip(),
                "shares": int(new_shares),
                "cost": float(new_cost),
            }
            holdings.append(new_holding)
            if save_holdings_to_github(holdings):
                st.success(f"已添加 {new_holding['name']}（{new_code}），{new_shares}股 @ ¥{new_cost:.2f}")
                st.rerun()

# ============================================
# 修改持仓
# ============================================

if holdings:
    st.header("✏️ 修改持仓")

    stock_options = {f"{h['name']}（{h['code']}）": i for i, h in enumerate(holdings)}
    selected = st.selectbox("选择要修改的股票", options=list(stock_options.keys()))

    if selected:
        idx = stock_options[selected]
        h = holdings[idx]

        with st.form("edit_form"):
            col_e1, col_e2 = st.columns(2)
            with col_e1:
                edit_shares = st.number_input(
                    "新股数",
                    min_value=0,
                    value=h.get("shares", 0),
                    step=100,
                    help="设为0将删除该持仓",
                )
            with col_e2:
                edit_cost = st.number_input(
                    "新成本价（元）",
                    min_value=0.01,
                    value=float(h.get("cost", 10)),
                    step=0.01,
                    format="%.2f",
                )

            if st.form_submit_button("更新", use_container_width=True):
                if edit_shares == 0:
                    holdings.pop(idx)
                    if save_holdings_to_github(holdings):
                        st.success(f"已删除 {h['name']}")
                        st.rerun()
                else:
                    holdings[idx]["shares"] = int(edit_shares)
                    holdings[idx]["cost"] = float(edit_cost)
                    if save_holdings_to_github(holdings):
                        st.success(f"已更新 {h['name']}：{edit_shares}股 @ ¥{edit_cost:.2f}")
                        st.rerun()

# ============================================
# 底部说明
# ============================================

st.divider()
st.caption("""
💡 **使用说明**
- 在此页面管理你的持仓股票（增/删/改）
- 系统每个交易日下午自动分析全A股 + 你的持仓
- 出现买入或卖出信号时，自动推送到你的微信
- 没有信号时不会打扰你
""")
