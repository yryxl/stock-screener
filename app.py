"""
Streamlit 管理界面
Tab1: 模型推荐（全市场扫描结果，可一键关注）
Tab2: 持仓管理（含信号状态）
Tab3: 重点关注表（含信号状态）
"""

import json
import streamlit as st
import requests
import base64

st.set_page_config(page_title="芒格选股系统", page_icon="📊", layout="wide")

# ============================================
# 侧边栏导航
# ============================================
page = st.sidebar.radio("导航", ["📊 正式版", "🧪 历史回测"], index=0)

if page == "🧪 历史回测":
    from backtest_page import render_backtest_page
    render_backtest_page()
    st.stop()

# ============================================
# 以下是正式版内容
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
    "hold": "⚪ 继续观望",
    "sell_watch": "⚪ 重点关注卖出",
    "sell_light": "🟡 可以适当卖出",
    "sell_medium": "🟠 可以中仓卖出",
    "sell_heavy": "🔴 可以大量卖出",
    "true_decline": "⛔ 基本面恶化",
}

BUY_SIGNALS = ["buy_heavy", "buy_medium", "buy_light", "buy_watch"]

# 行业PE区间（和screener.py保持一致，用于页面展示）
INDUSTRY_PE_DISPLAY = {
    "银行": "6-9", "保险": "8-12", "煤炭": "7-12", "煤炭开采": "7-12",
    "电力": "10-18", "铁路公路": "10-16", "交通运输": "12-16",
    "白酒": "20-30", "食品饮料": "20-30", "调味品": "22-35", "调味发酵品": "22-35",
    "乳制品": "15-25", "饮料乳品": "15-25",
    "中药": "20-30", "医药": "20-30", "医疗器械": "22-35", "生物制品": "20-30",
    "半导体": "40-65", "芯片": "40-65", "通信": "20-35", "通信服务": "20-35",
    "军工": "35-55", "航空航天": "35-55",
    "锂电": "30-50", "电池": "30-50", "新能源": "30-50",
    "化工": "12-20", "化学制品": "12-20", "农化制品": "12-20",
    "有色金属": "12-20", "工业金属": "12-20", "小金属": "15-25",
    "稀土": "15-25", "矿业": "10-18",
    "免税": "25-40", "旅游零售": "25-40",
    "家电": "15-25", "汽车零部件": "14-22",
    "轨交设备": "13-20", "铁路设备": "13-20", "铁路装备": "13-20",
    "传媒": "20-30", "机械制造": "15-25",
}

INDUSTRY_COMPLEXITY = {
    "白酒": "简单", "食品饮料": "简单", "调味品": "简单", "调味发酵品": "简单",
    "乳制品": "简单", "饮料乳品": "简单", "中药": "简单", "家电": "简单",
    "传媒": "简单", "银行": "简单", "保险": "简单", "免税": "简单",
    "旅游零售": "简单", "医药": "简单", "生物制品": "简单",
    "电力": "中等", "公用事业": "中等", "交通运输": "中等", "铁路公路": "中等",
    "通信": "中等", "通信服务": "中等", "医疗器械": "中等", "软件": "中等",
    "半导体": "复杂", "芯片": "复杂", "军工": "复杂", "航空航天": "复杂",
    "新能源": "复杂", "锂电": "复杂", "电池": "复杂", "光伏": "复杂",
    "轨交设备": "复杂", "铁路设备": "复杂", "机械制造": "复杂",
    "汽车零部件": "复杂", "建筑": "复杂", "钢铁": "复杂",
    "煤炭": "复杂", "煤炭开采": "复杂", "化工": "复杂", "化学制品": "复杂",
    "农化制品": "复杂", "有色金属": "复杂", "工业金属": "复杂",
    "稀土": "复杂", "小金属": "复杂", "矿业": "复杂",
}

def get_pe_range(category):
    if not category:
        return ""
    for key, val in INDUSTRY_PE_DISPLAY.items():
        if key in category:
            return val
    return ""

def get_complexity(category):
    if not category:
        return ""
    for key, val in INDUSTRY_COMPLEXITY.items():
        if key in category:
            return val
    return ""

def format_industry_tag(category):
    """格式化行业标签：行业名+PE区间+复杂度"""
    pe_range = get_pe_range(category)
    complexity = get_complexity(category)
    tag = f"🏷️ {category}"
    if pe_range:
        tag += f"（PE区间:{pe_range}"
    if complexity:
        tag += f" | {complexity}生意"
    if pe_range:
        tag += "）"
    elif complexity:
        tag += "）"
    return tag

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
tab1, tab2, tab3 = st.tabs(["🎯 模型推荐", "📋 持仓管理", "⭐ 重点关注表"])

# ============================================
# Tab1: 模型推荐（只来自全市场扫描）
# ============================================
with tab1:
    st.header("🎯 模型推荐")
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

    # 获取缓存数据日期
    daily = st.session_state.get("daily", {})
    cache_date = daily.get("date", "无") if daily else "无"

    if is_running:
        st.warning("⏳ 扫描正在运行中...请稍候，完成后刷新页面查看结果")
    else:
        col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 3])
        with col_btn1:
            if st.button("🔄 立即全盘扫描", type="primary"):
                try:
                    cfg = get_github_config()
                    url = f"https://api.github.com/repos/{cfg['repo']}/actions/workflows/daily_screen.yml/dispatches"
                    resp = requests.post(url, json={"ref": "main", "inputs": {"mode": "all"}}, headers=github_headers(cfg["token"]), timeout=10)
                    if resp.status_code == 204:
                        st.success("✅ 已触发！大约需要10-30分钟，完成后刷新页面查看结果。")
                        st.rerun()
                    else:
                        st.error(f"触发失败: {resp.status_code}")
                except Exception as e:
                    st.error(f"触发失败: {e}")
        with col_btn2:
            if st.button("🔧 用缓存调试模型"):
                try:
                    cfg = get_github_config()
                    url = f"https://api.github.com/repos/{cfg['repo']}/actions/workflows/daily_screen.yml/dispatches"
                    resp = requests.post(url, json={"ref": "main", "inputs": {"mode": "reanalyze"}}, headers=github_headers(cfg["token"]), timeout=10)
                    if resp.status_code == 204:
                        st.success("✅ 已触发调试！约1分钟完成，刷新查看。")
                        st.rerun()
                    else:
                        st.error(f"触发失败: {resp.status_code}")
                except Exception as e:
                    st.error(f"触发失败: {e}")
            st.caption(f"缓存数据：{cache_date}")

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
            st.info("暂无模型推荐，点击上方按钮触发全盘扫描")
        else:
            # 先按信号等级分，再按行业分
            for signal_key in BUY_SIGNALS:
                signal_label = SIGNAL_LABELS.get(signal_key, signal_key)
                group = [s for s in ai_recs if s.get("signal") == signal_key]
                if not group:
                    continue

                st.subheader(signal_label)

                # 按行业分组
                cat_groups = {}
                for s in group:
                    cat = s.get("category", "") or "其他"
                    if cat not in cat_groups:
                        cat_groups[cat] = []
                    cat_groups[cat].append(s)

                for cat, stocks in cat_groups.items():
                    st.markdown(f"{format_industry_tag(cat)}")
                    for s in stocks:
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
                                        "category": cat,
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
        # 按行业/类型分组
        category_holdings = {}
        for i, h in enumerate(holdings):
            code = h["code"]
            sig_data = holding_data.get(code, {})
            industry = sig_data.get("industry", "") or h.get("category", "")
            if not industry:
                # 根据名称猜测类型
                name = h.get("name", "")
                if "etf" in name.lower() or "ETF" in name:
                    industry = "ETF基金"
                else:
                    industry = "其他"
            if industry not in category_holdings:
                category_holdings[industry] = []
            category_holdings[industry].append((i, h))

        # 总计
        total_cost_all = sum(h.get("shares", 0) * h.get("cost", 0) for h in holdings)
        st.markdown(f"**持仓总成本：¥{total_cost_all:,.0f}** | 共{len(holdings)}只")

        # 仓位警告
        pos_warnings = daily.get("position_warnings", []) if isinstance(daily, dict) else []
        for w in pos_warnings:
            if w.get("level") == "danger":
                st.error(f"⚠️ {w.get('name','')}（{w.get('code','')}）{w.get('text','')}")
            else:
                st.warning(f"⚠️ {w.get('name','')}（{w.get('code','')}）{w.get('text','')}")

        # 换仓建议
        swap_sug = daily.get("swap_suggestions", []) if isinstance(daily, dict) else []
        if swap_sug:
            st.subheader("💡 换仓建议（机会成本）")
            st.caption("持仓中有卖出信号的股票 vs 关注表中有买入信号的股票")
            for s in swap_sug:
                st.info(
                    f"建议卖出 **{s.get('sell_name','')}** {s.get('sell_ratio','')} "
                    f"→ 买入 **{s.get('buy_name','')}**\n\n"
                    f"卖出原因：{SIGNAL_LABELS.get(s.get('sell_signal',''), '')} | "
                    f"买入原因：{SIGNAL_LABELS.get(s.get('buy_signal',''), '')}"
                )

        for cat, items in category_holdings.items():
            # 分类小计
            cat_cost = sum(h.get("shares", 0) * h.get("cost", 0) for _, h in items)
            cat_pct = (cat_cost / total_cost_all * 100) if total_cost_all > 0 else 0
            pe_range_cat = get_pe_range(cat)
            range_text = f" | PE区间:{pe_range_cat}" if pe_range_cat else ""

            st.subheader(f"🏷️ {cat}（成本¥{cat_cost:,.0f}，占{cat_pct:.1f}%{range_text}）")

            for i, h in items:
                code = h["code"]
                sig_data = holding_data.get(code, {})
                signal = sig_data.get("signal", "")
                signal_label = SIGNAL_LABELS.get(signal, "暂无数据")
                signal_text = sig_data.get("signal_text", "等待下次运行更新")
                pe = sig_data.get("pe", 0)
                price = sig_data.get("price", 0)
                stock_cost = h.get("shares", 0) * h.get("cost", 0)

                col1, col2, col3, col4, col5, col6 = st.columns([2, 1.2, 1.2, 1.2, 3, 0.8])
                with col1:
                    st.markdown(f"**{h.get('name', '未知')}**")
                    st.caption(f"{code} | {h.get('shares',0)}股 × ¥{h.get('cost',0):.2f} = ¥{stock_cost:,.0f}")
                with col2:
                    st.metric("股数", f"{h.get('shares', 0):,}")
                with col3:
                    st.metric("成本价", f"¥{h.get('cost', 0):.3f}")
                with col4:
                    if pe and pe > 0:
                        st.metric("PE(TTM)", f"{pe:.1f}")
                    elif price and price > 0:
                        st.metric("现价", f"¥{price:.2f}")
                    else:
                        st.metric("PE(TTM)", "—")
                with col5:
                    st.markdown(f"{signal_label}")
                    st.caption(signal_text[:80])
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
            new_cost = st.number_input("成本价", min_value=0.01, value=10.0, step=0.01, format="%.3f")
        new_cat = st.text_input("行业/类型（可选）", placeholder="ETF基金、白酒、医药等")
        if st.form_submit_button("添加", use_container_width=True, type="primary"):
            if new_code:
                new_h = {"code": new_code.strip(), "name": new_name.strip() or new_code.strip(), "shares": int(new_shares), "cost": float(new_cost)}
                if new_cat.strip():
                    new_h["category"] = new_cat.strip()
                holdings.append(new_h)
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
            st.subheader(format_industry_tag(cat))
            for item in items:
                code = item["code"]
                global_idx = watchlist.index(item)
                data = watchlist_data.get(code, {})

                pe = data.get("pe", 0)
                price = data.get("price", 0)
                signal = data.get("signal", "")
                signal_text = data.get("signal_text", "")
                signal_label = SIGNAL_LABELS.get(signal, "—")
                total_score = data.get("total_score", 0)
                div_yield = data.get("dividend_yield", 0)
                dims = data.get("dimensions", {})

                col1, col2, col3, col4, col5, col6 = st.columns([2, 1, 1, 1, 3, 0.8])
                with col1:
                    st.markdown(f"**{item['name']}**（{code}）")
                    st.caption(item.get("note", ""))
                with col2:
                    st.metric("PE(TTM)", f"{pe:.1f}" if pe and pe > 0 else "—")
                with col3:
                    st.metric("股息率", f"{div_yield:.1f}%" if div_yield > 0 else "—")
                with col4:
                    st.metric("评分", f"{total_score}/50" if total_score > 0 else "—")
                with col5:
                    st.markdown(f"{signal_label}")
                    if signal_text:
                        st.caption(signal_text[:80])
                    # 展开显示各维度得分
                    if dims:
                        dim_str = " | ".join(f"{k}:{v['score']}" for k, v in dims.items())
                        st.caption(f"📊 {dim_str}")
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
st.caption("💡 模型推荐来自全市场扫描 | 关注表和持仓每日更新PE和信号 | 系统每交易日下午5点自动运行")
