"""
Streamlit 管理界面
Tab1: 模型推荐（全市场扫描结果，可一键关注）
Tab2: 持仓管理（含信号状态）
Tab3: 重点关注表（含信号状态）
"""

import json
import os
import streamlit as st
import requests
import base64
from datetime import datetime

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
    "buy_add": "🟢📈 持仓可加仓",
    "hold_keep": "🟢 建议持续持有",
    "hold": "⚪ 继续观望",
    "sell_watch": "⚪ 重点关注卖出",
    "sell_light": "🟡 可以适当卖出",
    "sell_medium": "🟠 可以中仓卖出",
    "sell_heavy": "🔴 可以大量卖出",
    "true_decline": "⛔ 基本面恶化",
}

BUY_SIGNALS = ["buy_heavy", "buy_medium", "buy_light", "buy_watch"]

# 全部信号展示顺序（和消息推送 notifier.py 的 SIGNAL_GROUPS 对齐）
ALL_SIGNAL_ORDER = [
    "buy_heavy", "buy_medium", "buy_light", "buy_watch",
    "sell_watch", "sell_light", "sell_medium", "sell_heavy",
    "true_decline",
]

# 行业 PE 区间和复杂度：从 screener.INDUSTRY_PE 动态生成，避免两处硬编码不同步
# 单一真相源：screener.INDUSTRY_PE
# 以前是 app.py 自己维护一份 INDUSTRY_PE_DISPLAY 和 INDUSTRY_COMPLEXITY，
# 改 screener 时漏改 app.py 会导致分类标题和 signal_text 展示出不一致的区间。
_COMPLEXITY_LABELS = {"simple": "简单", "medium": "中等", "complex": "复杂"}


def _build_industry_display():
    """从 screener.INDUSTRY_PE 构建 {行业名: (PE区间文字, 复杂度文字)}"""
    try:
        from screener import INDUSTRY_PE
    except Exception:
        return {}, {}
    pe_display = {}
    complexity_display = {}
    for key, cfg in INDUSTRY_PE.items():
        fl = cfg.get("fair_low")
        fh = cfg.get("fair_high")
        if fl is not None and fh is not None:
            pe_display[key] = f"{fl}-{fh}"
        cplx = cfg.get("complexity", "")
        if cplx in _COMPLEXITY_LABELS:
            complexity_display[key] = _COMPLEXITY_LABELS[cplx]
    return pe_display, complexity_display


INDUSTRY_PE_DISPLAY, INDUSTRY_COMPLEXITY = _build_industry_display()


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

def load_all_data(force=False):
    """
    从 GitHub 加载数据。
    自动刷新：每 10 分钟重新拉取一次，确保前端数据和推送同步。
    force=True 时强制刷新（用于手动刷新按钮）。
    """
    import time as _time
    now = _time.time()
    last_load = st.session_state.get("data_loaded_at", 0)
    stale = (now - last_load) > 600  # 10 分钟过期

    if force or stale or "data_loaded" not in st.session_state:
        st.session_state["holdings"], st.session_state["holdings_sha"] = load_from_github("holdings.json")
        st.session_state["watchlist"], st.session_state["watchlist_sha"] = load_from_github("watchlist.json")
        st.session_state["daily"], _ = load_from_github("daily_results.json")
        st.session_state["data_loaded"] = True
        st.session_state["data_loaded_at"] = now

load_all_data()

# ============================================
# 页面
# ============================================

st.title("📊 芒格选股系统")


# 市场温度计 banner（沪深300指数 PE 历史分位）
# 先尝试从 daily_results 读取（快）, 没有则实时拉取（缓存 1 小时）
# 重要：label/description 总是从 market_temperature.TEMP_LEVELS 动态查找
# 这样即使 daily_results.json 里缓存的是旧文案，前端也会用最新版
@st.cache_data(ttl=3600, show_spinner=False)
def _get_cached_market_temperature():
    try:
        from market_temperature import get_realtime_market_temperature
        return get_realtime_market_temperature()
    except Exception as e:
        return {"level": 0, "current_pe_median": None, "percentile": None, "as_of": ""}


def render_market_temperature_banner():
    """渲染市场温度计 banner（在所有 tab 上方）"""
    _daily = st.session_state.get("daily", {})
    _temp = _daily.get("market_temperature") if isinstance(_daily, dict) else None
    # 如果 daily_results 没有，实时拉
    if not _temp:
        _temp = _get_cached_market_temperature()
    if not _temp:
        return

    level = _temp.get("level", 0)
    pe = _temp.get("current_pe_median")
    pct = _temp.get("percentile")
    as_of = _temp.get("as_of", "")

    # 从 market_temperature.TEMP_LEVELS 动态查最新 label 和 description
    # 而不是用 daily_results 里可能过时的文案
    try:
        from market_temperature import TEMP_LEVELS
        label, desc = TEMP_LEVELS.get(level, ("⚪ 正常市", ""))
    except Exception:
        label = _temp.get("label", "⚪ 正常市")
        desc = _temp.get("description", "")

    bg_colors = {2: "#ffebee", 1: "#fff3e0", 0: "#f5f5f5", -1: "#e3f2fd", -2: "#e8f5e9"}
    bd_colors = {2: "#d32f2f", 1: "#f57c00", 0: "#9e9e9e", -1: "#1976d2", -2: "#388e3c"}
    bg = bg_colors.get(level, "#f5f5f5")
    bd = bd_colors.get(level, "#9e9e9e")
    info = f"沪深300中位数PE={pe} | 历史10年{pct}%分位" if pe else ""
    # HTML 必须是单行/无 4 格以上缩进，否则会被 Markdown 当成代码块
    banner_html = (
        f'<div style="background:{bg};padding:14px 20px;border-left:5px solid {bd};border-radius:6px;margin-bottom:15px;">'
        f'<div style="font-size:19px;font-weight:bold;margin-bottom:4px;">{label}</div>'
        f'<div style="color:#333;font-size:14px;line-height:1.6;">{desc}</div>'
        f'<div style="color:#888;font-size:12px;margin-top:6px;">{info}（数据截至 {as_of}，沪深300指数）</div>'
        f'</div>'
    )
    st.markdown(banner_html, unsafe_allow_html=True)

    # REQ-187：极冷温度"鳄鱼出击"显性提示
    # 依据：芒格"等几年十年一次的机会"、巴菲特 2008《我在买入美国》
    # 总仓位上限 80%（对齐伯克希尔 15-30% 现金实操，不是 90%）
    if level == -2:
        croc_html = (
            '<div style="background:#c8e6c9;padding:12px 18px;border-left:5px solid #2e7d32;'
            'border-radius:6px;margin-bottom:15px;">'
            '<div style="font-size:17px;font-weight:bold;color:#1b5e20;">🐊 鳄鱼出击机会（REQ-187）</div>'
            '<div style="color:#222;font-size:14px;line-height:1.6;margin-top:4px;">'
            '市场在历史最冷 15% 分位，属于芒格"等几年十年一次"的级别。'
            '建议总仓位上限 <b>80%</b>（保留 20% 现金应对极端流动性——对齐伯克希尔 2020-03 约 36% 现金实操）。'
            '现金充裕应集中买入优质低估标的；不是梭哈清空现金。</div></div>'
        )
        st.markdown(croc_html, unsafe_allow_html=True)

        # TODO-001：大底熔断状态展示（2026-04-17）
        # 用户反馈"前端没看到熔断机制相关内容"，把回测 path_c 策略的两条规则
        # 显式告诉用户，让他知道极冷温度下系统在做什么
        # 来源：backtest_autorun.py path_b/path_c（market_temp == -2 触发）
        meltdown_html = (
            '<div style="background:#fff3cd;padding:12px 18px;border-left:5px solid #f57f17;'
            'border-radius:6px;margin-bottom:15px;">'
            '<div style="font-size:17px;font-weight:bold;color:#bf360c;">'
            '🚨 大底熔断已激活（path_c 策略）</div>'
            '<div style="color:#222;font-size:14px;line-height:1.7;margin-top:6px;">'
            '系统在回测中对极冷温度（沪深300 PE ≤ 历史 15% 分位）执行的两条特殊规则：'
            '<ul style="margin:6px 0 0 0;padding-left:20px;">'
            '<li><b>⛔ 跳过"贵了卖出"信号</b>：极冷区任何 PE 类减仓都是错的（保留必须卖：退市/护城河松动）</li>'
            '<li><b>💰 买入预算翻倍</b>：常规 1.30 万/月 → 大底 2.00 万/月（54% 加仓力度）</li>'
            '</ul>'
            '<div style="color:#5d4037;font-size:13px;margin-top:8px;">'
            '💡 实操建议：这是回测策略的自动行为，实际操作仍按"宁可错过不犯错"原则。'
            '回测验证：path_c 比基线（baseline）+ 28.9pp（25 年累计）'
            '</div>'
            '</div></div>'
        )
        st.markdown(meltdown_html, unsafe_allow_html=True)

    # REQ-182：利率环境监测（利率冲击 → PE 区间收紧）
    # 巴菲特：利率是万物的引力。利率 12 个月上升 >1.5pp 会系统性压低股票估值
    try:
        from china_adjustments import check_interest_rate_shock
        rate_info = _get_cached_interest_rate()
        if rate_info and rate_info.get("yield_data"):
            y = rate_info["yield_data"]
            current = y["current"]
            delta = y["delta_pp"]
            if rate_info["shock"]:
                # 利率冲击：红色警告
                rate_html = (
                    '<div style="background:#ffe0b2;padding:10px 16px;border-left:5px solid #ef6c00;'
                    'border-radius:6px;margin-bottom:15px;">'
                    '<div style="font-size:15px;font-weight:bold;color:#e65100;">⚡ 利率冲击警告（REQ-182）</div>'
                    f'<div style="color:#333;font-size:13px;line-height:1.6;margin-top:3px;">'
                    f'10 年国债 12 个月上升 <b>+{delta:.2f}pp</b>（{y["past"]:.2f}% → {current:.2f}%）。'
                    f'利率是万物的引力——PE 合理区间建议内部乘 <b>0.85</b>。'
                    '</div></div>'
                )
                st.markdown(rate_html, unsafe_allow_html=True)
            else:
                # 正常：蓝色小提示（低调展示）
                rate_caption = (
                    f'<div style="color:#666;font-size:12px;margin-bottom:8px;">'
                    f'🏦 10 年国债 {current:.2f}%（12 个月 {delta:+.2f}pp，利率环境正常）'
                    '</div>'
                )
                st.markdown(rate_caption, unsafe_allow_html=True)
    except Exception:
        pass


# REQ-182：国债利率缓存（调用 china_adjustments）
@st.cache_data(ttl=86400, show_spinner=False)
def _get_cached_interest_rate():
    try:
        from china_adjustments import check_interest_rate_shock
        return check_interest_rate_shock()
    except Exception:
        return None


# REQ-187：按市场温度动态计算总仓位上限（用于持仓页健康度提示）
# 阈值来源：
#   - 温度 -2（极冷 PE≤10% 分位）：80%（鳄鱼出击期，但仍保 20% 现金）
#   - 温度 -1（偏冷 10-30% 分位）：70%（继续加仓，保留弹药）
#   - 温度 0（正常）：60%（标准股债平衡）
#   - 温度 1（偏热）：50%（停止加仓，现金回升）
#   - 温度 2（极热）：40%（卖出盈利部分，现金充足）
SUGGESTED_POSITION_CAP = {-2: 80, -1: 70, 0: 60, 1: 50, 2: 40}


def get_suggested_position_cap(level):
    """REQ-187：按温度档位返回建议股票总仓位上限（%）"""
    return SUGGESTED_POSITION_CAP.get(level, 60)


render_market_temperature_banner()

# 数据刷新提示 + 手动刷新按钮
_daily = st.session_state.get("daily", {})
_data_date = _daily.get("date", "") if _daily else ""
_rc1, _rc2 = st.columns([4, 1])
with _rc1:
    if _data_date:
        st.caption(f"📅 数据：{_data_date} | {_daily.get('data_source', '')} | 每10分钟自动刷新")
with _rc2:
    if st.button("🔄 刷新数据"):
        load_all_data(force=True)
        st.rerun()

tab1, tab2, tab3, tab4 = st.tabs(["🎯 模型推荐", "📋 持仓管理", "⭐ 重点关注表", "🧊 ETF 监测"])

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

        # 合并全部信号源（和消息推送完全对齐）
        # 优先级：holding_signals > watchlist_signals(非hold) > ai_recommendations
        # 持仓信号最优先——模型知道你持有，信号更精准
        # 同一股票只出现一次，避免"一个说买一个说持有"的矛盾
        holding_sigs = daily.get("holding_signals", [])
        watchlist_sigs = daily.get("watchlist_signals", [])
        ai_recs = daily.get("ai_recommendations", [])

        all_recs = []
        seen_codes = set()
        # 第一优先：持仓信号（最了解你的仓位状况）
        for hs in holding_sigs:
            code = hs.get("code")
            if code and hs.get("signal") and code not in seen_codes:
                all_recs.append(hs)
                seen_codes.add(code)
        # 第二优先：关注表中非"hold"的信号
        for ws in watchlist_sigs:
            code = ws.get("code")
            sig = ws.get("signal", "")
            if code and sig and sig != "hold" and code not in seen_codes:
                all_recs.append(ws)
                seen_codes.add(code)
        # 第三优先：全市场扫描推荐
        for ar in ai_recs:
            code = ar.get("code")
            if code and ar.get("signal") and code not in seen_codes:
                all_recs.append(ar)
                seen_codes.add(code)

        if not all_recs:
            st.info("暂无模型推荐，点击上方按钮触发全盘扫描")
        else:
            # 按信号等级分组（和消息推送顺序一致）
            for signal_key in ALL_SIGNAL_ORDER:
                signal_label = SIGNAL_LABELS.get(signal_key, signal_key)
                group = [s for s in all_recs if s.get("signal") == signal_key]
                if not group:
                    continue

                st.subheader(signal_label)

                # 按行业分组
                cat_groups = {}
                for s in group:
                    cat = s.get("category", "") or s.get("industry", "") or "其他"
                    if cat not in cat_groups:
                        cat_groups[cat] = []
                    cat_groups[cat].append(s)

                for cat, stocks in cat_groups.items():
                    st.markdown(f"{format_industry_tag(cat)}")
                    for s in stocks:
                        code = s.get("code", "")
                        # 构建指标摘要行
                        metrics = []
                        pe = s.get("pe", 0)
                        if pe and pe > 0:
                            metrics.append(f"市盈率 {pe:.1f}")
                        roe = s.get("roe")
                        if roe is not None:
                            metrics.append(f"净收益率 {roe}%")
                        gm = s.get("gross_margin")
                        if gm is not None:
                            metrics.append(f"毛利 {gm}%")
                        debt = s.get("debt_ratio")
                        if debt is not None:
                            metrics.append(f"负债 {debt}%")
                        div_y = s.get("dividend_yield", 0)
                        if div_y and div_y > 0:
                            metrics.append(f"股息 {div_y:.1f}%")
                        metrics_str = " | ".join(metrics)

                        col1, col2, col3, col4 = st.columns([3, 1.2, 1.2, 1.5])
                        with col1:
                            st.markdown(f"**{s.get('name', '')}**（{code}）")
                            st.caption(metrics_str)
                        with col2:
                            price = s.get("price", 0)
                            st.metric("股价", f"¥{price:.2f}" if price else "—")
                        with col3:
                            score = s.get("total_score", 0)
                            st.metric("评分", f"{score}/50" if score else "—")
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

            # 持仓附加信号（加仓/持有建议）
            extra_sigs = [s for s in all_recs
                          if s.get("signal") in ("buy_add", "hold_keep")]
            if extra_sigs:
                st.subheader("📋 持仓信号")
                for s in extra_sigs:
                    code = s.get("code", "")
                    sig_label = SIGNAL_LABELS.get(s.get("signal", ""), "")
                    metrics = []
                    pe = s.get("pe", 0)
                    if pe and pe > 0:
                        metrics.append(f"市盈率 {pe:.1f}")
                    roe = s.get("roe")
                    if roe is not None:
                        metrics.append(f"净收益率 {roe}%")
                    gm = s.get("gross_margin")
                    if gm is not None:
                        metrics.append(f"毛利 {gm}%")
                    debt = s.get("debt_ratio")
                    if debt is not None:
                        metrics.append(f"负债 {debt}%")
                    div_y = s.get("dividend_yield", 0)
                    if div_y and div_y > 0:
                        metrics.append(f"股息 {div_y:.1f}%")

                    col1, col2, col3 = st.columns([3, 1.2, 4])
                    with col1:
                        st.markdown(f"**{s.get('name', '')}**（{code}）")
                        st.caption(" | ".join(metrics))
                    with col2:
                        price = s.get("price", 0)
                        st.metric("股价", f"¥{price:.2f}" if price else "—")
                    with col3:
                        st.markdown(f"{sig_label}")
                        st.caption(s.get("signal_text", ""))
                st.divider()

            # 仓位警告（REQ-189 分档：十年王者+小资金/大资金/普通标的）
            # 实时基于当前 holdings + holding_signals 重算（不用 daily.position_warnings 可能不同步）
            _holdings_live = st.session_state.get("holdings", [])
            _sig_map = {s.get("code"): s for s in daily.get("holding_signals", [])}
            _total_mv = 0
            _items_mv = []
            for _h in _holdings_live:
                _c = str(_h.get("code", "")).zfill(6)
                _s = _sig_map.get(_c) or _sig_map.get(_h.get("code"))
                _p = (_s or {}).get("price", 0) or _h.get("cost", 0)
                _v = _p * _h.get("shares", 0)
                _total_mv += _v
                _items_mv.append({
                    "code": _c, "name": _h.get("name", ""), "value": _v,
                    "is_king": (_s or {}).get("is_10y_king", False),
                })
            # 判断资金规模（<100 万算小资金）
            _is_small_capital = _total_mv < 1_000_000
            _warnings_live = []
            for _it in _items_mv:
                _pct = _it["value"] / _total_mv * 100 if _total_mv > 0 else 0
                _is_king = _it["is_king"]
                if _is_king and _is_small_capital:
                    _warn, _danger, _tier = 35, 45, "十年王者+小资金"
                elif _is_king:
                    _warn, _danger, _tier = 25, 35, "十年王者+大资金"
                else:
                    _warn, _danger, _tier = 20, 30, "普通标的"
                if _pct >= _danger:
                    _warnings_live.append({
                        "code": _it["code"], "name": _it["name"],
                        "pct": _pct, "level": "danger", "tier": _tier,
                        "warn_line": _warn, "danger_line": _danger,
                    })
                elif _pct >= _warn:
                    _warnings_live.append({
                        "code": _it["code"], "name": _it["name"],
                        "pct": _pct, "level": "warning", "tier": _tier,
                        "warn_line": _warn, "danger_line": _danger,
                    })
            if _warnings_live:
                st.subheader("⚠️ 集中度警告（REQ-189 分档）")
                for w in _warnings_live:
                    if w["level"] == "danger":
                        st.error(
                            f"🚨 **{w['name']}**（{w['code']}）"
                            f"仓位 {w['pct']:.1f}% ≥ {w['danger_line']}%"
                            f"（{w['tier']}危险线），严重偏重！建议减仓"
                        )
                    else:
                        st.warning(
                            f"⚠️ **{w['name']}**（{w['code']}）"
                            f"仓位 {w['pct']:.1f}% ≥ {w['warn_line']}%"
                            f"（{w['tier']}警告线），注意分散"
                        )
                st.divider()

# ============================================
# Tab2: 持仓管理（含信号状态）
# ============================================
with tab2:
    st.header("📋 我的持仓")

    holdings = st.session_state.get("holdings", [])
    daily = st.session_state.get("daily", {})

    # 持仓信号数据（个股来自 holding_signals，ETF 来自 etf_signals）
    holding_data = {}
    for s in daily.get("holding_signals", []):
        holding_data[s.get("code", "")] = s

    # ETF 信号数据（按 code 索引，用于持仓 tab 的 ETF 行展示）
    etf_data = {}
    for e in daily.get("etf_signals", []):
        etf_data[str(e.get("code", "")).zfill(6)] = e

    # ETF kind → 中文分类映射（宽基/策略/行业）
    _ETF_KIND_LABELS = {
        "broad": "🛡 宽基 ETF",
        "strategy_dividend": "💎 策略 ETF",
        "strategy": "💎 策略 ETF",
        "sector": "⚡ 行业 ETF",
    }

    if not holdings:
        st.info("暂无持仓")
    else:
        # 按行业/类型分组
        # 分类优先级：
        #   1. ETF → 按 etf_signals 的 kind 分成宽基/策略/行业
        #   2. 个股 → 按 holding_signals.industry（真实行业，来自 get_stock_industry）
        #   3. 兜底 → holdings.json 里手填的 category
        category_holdings = {}
        for i, h in enumerate(holdings):
            code = str(h["code"]).zfill(6)

            # 先判断是不是 ETF
            if code in etf_data:
                kind = etf_data[code].get("kind", "sector")
                industry = _ETF_KIND_LABELS.get(kind, "📊 ETF 基金")
            elif code[0] in ("1", "5"):
                # 未在 etf_signals 中（映射表缺失）但看起来像 ETF
                industry = "📊 未识别 ETF（需补映射）"
            else:
                # 个股：走真实行业
                sig_data = holding_data.get(code, {})
                industry = sig_data.get("industry", "") or h.get("category", "") or "其他"

            if industry not in category_holdings:
                category_holdings[industry] = []
            category_holdings[industry].append((i, h))

        # 总计
        total_cost_all = sum(h.get("shares", 0) * h.get("cost", 0) for h in holdings)
        # 计算持仓市值（用于含现金占比计算）
        total_market_value = 0
        for h in holdings:
            code_h = str(h["code"]).zfill(6)
            sig = holding_data.get(code_h, {}) if not (code_h in etf_data) else etf_data.get(code_h, {})
            _p = sig.get("price", 0) or h.get("cost", 0)
            total_market_value += _p * h.get("shares", 0)

        # 加载可投资现金
        try:
            cash_path = os.path.join(os.path.dirname(__file__), "user_cash.json")
            with open(cash_path, encoding="utf-8") as _f:
                cash_data = json.load(_f)
        except Exception:
            cash_data = {"amount": 0, "updated_at": "", "note": ""}
        investable_cash = float(cash_data.get("amount", 0))
        total_assets = total_market_value + investable_cash

        # 顶部展示：持仓市值 + 可投资现金 + 总资产 + 股债比（REQ-187 建议上限）
        _ta_col1, _ta_col2, _ta_col3, _ta_col4 = st.columns(4)
        with _ta_col1:
            st.metric("持仓市值", f"¥{total_market_value:,.0f}",
                      f"共{len(holdings)}只")
        with _ta_col2:
            st.metric("可投资现金", f"¥{investable_cash:,.0f}",
                      f"占{investable_cash/total_assets*100:.1f}%" if total_assets > 0 else None)
        with _ta_col3:
            st.metric("总资产", f"¥{total_assets:,.0f}")
        with _ta_col4:
            _stock_pct = total_market_value / total_assets * 100 if total_assets > 0 else 0
            # REQ-187：按市场温度取建议上限
            _mt = st.session_state.get("daily", {}).get("market_temperature") or {}
            _mt_level = _mt.get("level", 0)
            _cap = get_suggested_position_cap(_mt_level)
            # 判断是否超限
            _delta_str = f"建议上限 {_cap}%（按温度）"
            _delta_color = "normal"
            if _stock_pct > _cap + 5:
                _delta_str = f"⚠超上限 {_cap}% {_stock_pct - _cap:+.1f}pp"
                _delta_color = "inverse"
            elif _stock_pct < _cap * 0.6 and total_assets > 0:
                _delta_str = f"低于上限 {_cap}%（可加仓）"
            st.metric("股债比", f"{_stock_pct:.1f}%",
                      _delta_str, delta_color=_delta_color)

        # 可编辑现金金额
        with st.expander("✏️ 修改可投资现金"):
            _new_cash = st.number_input(
                "可投资现金（货币基金/国债ETF等随时可动用资金）",
                min_value=0.0, value=investable_cash, step=100.0, format="%.2f",
                help="用于机动加仓的弹药。建议保持总资产的 30-60% 在机会到来时能投入"
            )
            _new_note = st.text_input("备注（可选）", value=cash_data.get("note", ""))
            if st.button("💾 保存现金金额"):
                cash_data = {
                    "amount": _new_cash,
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "note": _new_note,
                }
                try:
                    with open(cash_path, "w", encoding="utf-8") as _f:
                        json.dump(cash_data, _f, ensure_ascii=False, indent=2)
                    # 同步到 GitHub
                    save_to_github("user_cash.json", cash_data, None)
                    st.success(f"已保存：¥{_new_cash:,.0f}")
                    st.rerun()
                except Exception as _e:
                    st.error(f"保存失败：{_e}")
            if cash_data.get("updated_at"):
                st.caption(f"上次更新：{cash_data.get('updated_at')}")

        st.caption(f"持仓成本总计：¥{total_cost_all:,.0f}")

        # 组合分类简报 & 仓位警告 —— 实时基于当前 holdings 计算
        # 关键修复：不再依赖 daily_results 的 portfolio_classification（可能和 holdings 不同步）
        # ETF 分类规则：
        #   宽基：跟踪沪深300/上证50/中证500/中证1000/科创50/创业板
        #   策略：跟踪红利/价值/成长/质量等策略指数
        #   行业：其他行业/主题 ETF
        #   个股：非 ETF 代码
        try:
            with open(os.path.join(os.path.dirname(__file__), "etf_index_map.json"), encoding="utf-8") as _f:
                _etf_map = json.load(_f).get("map", {})
        except Exception:
            _etf_map = {}

        BROAD_INDICES = {"000300", "000016", "000905", "000852", "000010",
                         "000688", "399673", "000903", "399006"}  # 宽基指数代码
        STRATEGY_KINDS = {"strategy", "strategy_dividend"}

        _buckets_live = {
            "broad_etf": {"value": 0, "items": []},
            "strategy_etf": {"value": 0, "items": []},
            "sector_etf": {"value": 0, "items": []},
            "single_stock": {"value": 0, "items": []},
        }
        for h in holdings:
            _code = str(h.get("code", "")).zfill(6)
            _sig = holding_data.get(_code, {}) if _code not in etf_data else etf_data.get(_code, {})
            _price = _sig.get("price", 0) or h.get("cost", 0)
            _value = _price * h.get("shares", 0)
            _item = {"code": _code, "name": h.get("name", ""), "value": _value}

            # 分类
            if _code in _etf_map:
                _info = _etf_map[_code]
                _idx = _info.get("index", "")
                _kind = _info.get("kind", "")
                if _idx in BROAD_INDICES or _kind == "broad":
                    _buckets_live["broad_etf"]["items"].append(_item)
                    _buckets_live["broad_etf"]["value"] += _value
                elif _kind in STRATEGY_KINDS:
                    _buckets_live["strategy_etf"]["items"].append(_item)
                    _buckets_live["strategy_etf"]["value"] += _value
                else:
                    _buckets_live["sector_etf"]["items"].append(_item)
                    _buckets_live["sector_etf"]["value"] += _value
            elif _code[0] in ("1", "5"):
                # 未映射但看起来是 ETF，归为行业 ETF
                _buckets_live["sector_etf"]["items"].append(_item)
                _buckets_live["sector_etf"]["value"] += _value
            else:
                _buckets_live["single_stock"]["items"].append(_item)
                _buckets_live["single_stock"]["value"] += _value

        _total_live = sum(b["value"] for b in _buckets_live.values())
        if _total_live > 0:
            bucket_labels = [
                ("broad_etf", "🛡 宽基ETF", "#e8f5e9"),
                ("strategy_etf", "💎 策略ETF", "#e3f2fd"),
                ("sector_etf", "⚡ 行业ETF", "#fff3e0"),
                ("single_stock", "📌 个股", "#fce4ec"),
            ]
            cols = st.columns(4)
            for (bkey, blabel, bcolor), col in zip(bucket_labels, cols):
                b = _buckets_live.get(bkey, {"value": 0, "items": []})
                items_count = len(b.get("items", []))
                _pct = b["value"] / _total_live * 100 if _total_live > 0 else 0
                with col:
                    bucket_html = (
                        f'<div style="background:{bcolor};padding:10px;border-radius:6px;text-align:center;">'
                        f'<div style="font-size:13px;color:#666;">{blabel}</div>'
                        f'<div style="font-size:20px;font-weight:bold;">{_pct:.1f}%</div>'
                        f'<div style="font-size:11px;color:#888;">{items_count}只 · ¥{b["value"]:,.0f}</div>'
                        f'</div>'
                    )
                    st.markdown(bucket_html, unsafe_allow_html=True)

            # 防御型（宽基+高股息）vs 进攻型（策略+行业+个股）
            _defensive_value = _buckets_live["broad_etf"]["value"]
            _defensive_pct = _defensive_value / _total_live * 100
            st.caption(
                f"⚠ 提醒：宽基 ETF 是低波动权益资产，不是类固收。历史上沪深300也出现过单年跌 40% 的情况。"
                f"防御型占比 {_defensive_pct:.1f}% | 进攻型占比 {100-_defensive_pct:.1f}%"
            )

        # 仓位警告 —— 实时基于当前 holdings 重新校验（不用 daily_results 里的老数据）
        for _code_key, b_list in [("broad_etf", _buckets_live["broad_etf"]["items"]),
                                    ("strategy_etf", _buckets_live["strategy_etf"]["items"]),
                                    ("sector_etf", _buckets_live["sector_etf"]["items"]),
                                    ("single_stock", _buckets_live["single_stock"]["items"])]:
            for _it in b_list:
                _p = _it["value"] / _total_live * 100 if _total_live > 0 else 0
                if _p >= 40:
                    st.error(
                        f"⚠️ **{_it['name']}（{_it['code']}）** "
                        f"仓位 {_p:.1f}% ≥ 40%，严重偏重！建议分散"
                    )
                elif _p >= 30:
                    st.warning(
                        f"⚠️ **{_it['name']}（{_it['code']}）** "
                        f"仓位 {_p:.1f}% ≥ 30%，偏重警戒"
                    )

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
            # 分类小计（用市值算，保持和顶部卡片的分母一致）
            cat_value = 0
            for _, h in items:
                code_h = str(h["code"]).zfill(6)
                _sig = holding_data.get(code_h, {}) if code_h not in etf_data else etf_data.get(code_h, {})
                _price = _sig.get("price", 0) or h.get("cost", 0)
                cat_value += _price * h.get("shares", 0)
            cat_cost = sum(h.get("shares", 0) * h.get("cost", 0) for _, h in items)
            # 占比用市值/持仓市值总和（和顶部分类卡片一致）
            cat_pct = (cat_value / total_market_value * 100) if total_market_value > 0 else 0
            pe_range_cat = get_pe_range(cat)
            range_text = f" | PE区间:{pe_range_cat}" if pe_range_cat else ""

            st.subheader(f"🏷️ {cat}（市值¥{cat_value:,.0f}，占{cat_pct:.1f}%{range_text}）")

            for i, h in items:
                code = str(h["code"]).zfill(6)
                stock_cost = h.get("shares", 0) * h.get("cost", 0)

                # ETF 与个股分开取数据
                is_etf = code in etf_data
                sig_data = {}  # 个股情况下会被覆盖
                if is_etf:
                    # ETF 行：数据来自 etf_signals（PE 是跟踪指数的 PE）
                    e = etf_data[code]
                    temp = e.get("temperature", {}) or {}
                    pe = temp.get("current_pe")
                    pe_label = "指数PE"
                    percentile = temp.get("percentile")
                    signal_label = temp.get("label") or "📊 数据积累中"
                    signal_text = e.get("signal_text", "") or temp.get("note", "数据积累中")
                    # ETF 用 current_price 做盈亏
                    current_price = e.get("current_price") or 0
                    pnl_pct = e.get("pnl_pct")
                    pnl_label = e.get("pnl_label", "")
                    pnl_advice = e.get("pnl_advice", "")
                    must_sell = e.get("must_sell", False)
                    index_name = e.get("index_name", "")
                else:
                    # 个股行：数据来自 holding_signals
                    sig_data = holding_data.get(code, {})
                    signal = sig_data.get("signal", "")
                    signal_label = SIGNAL_LABELS.get(signal, "暂无数据")
                    signal_text = sig_data.get("signal_text", "等待下次运行更新")
                    pe = sig_data.get("pe", 0)
                    pe_label = "PE(TTM)"
                    current_price = sig_data.get("price", 0)
                    percentile = None
                    index_name = ""
                    pnl_pct = sig_data.get("pnl_pct")
                    pnl_label = sig_data.get("pnl_label", "")
                    pnl_advice = sig_data.get("pnl_advice", "")
                    must_sell = sig_data.get("must_sell", False)

                # 检查是否有激活的提醒
                try:
                    from stock_notes_manager import has_active_alerts
                    _has_alert, _alert_count = has_active_alerts(code)
                except Exception:
                    _has_alert, _alert_count = False, 0

                col1, col2, col3, col4, col5, col6, col7 = st.columns([2, 1.2, 1.2, 1.2, 2.5, 0.6, 0.6])
                with col1:
                    # 股票名 + 可能的提醒铃铛
                    _name_display = h.get('name', '未知')
                    if _has_alert:
                        _name_display = f"🔔 {_name_display}"
                    st.markdown(f"**{_name_display}**")
                    caption = f"{code} | {h.get('shares',0)}股 × ¥{h.get('cost',0):.2f} = ¥{stock_cost:,.0f}"
                    if is_etf and index_name:
                        caption += f" | 跟踪 {index_name}"
                    # 财务指标摘要（和回测版对齐）
                    _fm = []
                    _roe = sig_data.get("roe")
                    if _roe is not None:
                        _fm.append(f"净收益率 {_roe}%")
                    _gm = sig_data.get("gross_margin")
                    if _gm is not None:
                        _fm.append(f"毛利 {_gm}%")
                    _dr = sig_data.get("debt_ratio")
                    if _dr is not None:
                        _fm.append(f"负债 {_dr}%")
                    _dy = sig_data.get("dividend_yield", 0)
                    if _dy and _dy > 0:
                        _fm.append(f"股息 {_dy:.1f}%")
                    if _fm:
                        caption += f"\n{' | '.join(_fm)}"
                    if _has_alert:
                        caption += f"\n⚠ {_alert_count} 条到期提醒待处理"
                    st.caption(caption)
                with col2:
                    # 现价 + 浮盈百分比（Streamlit 会自动上涨绿色、下跌红色）
                    if current_price and current_price > 0:
                        delta_str = f"{pnl_pct:+.1f}%" if pnl_pct is not None else None
                        st.metric("现价", f"¥{current_price:.3f}", delta_str)
                    else:
                        st.metric("现价", "—")
                with col3:
                    st.metric("成本价", f"¥{h.get('cost', 0):.3f}")
                with col4:
                    if pe and pe > 0:
                        if is_etf and percentile is not None:
                            # 分位不用 st.metric 的 delta（字符串时颜色反转失效）
                            # 改用纯文字+emoji，对齐"贵红便宜绿"的直觉
                            st.metric(pe_label, f"{pe:.1f}")
                            if percentile >= 85:
                                _badge = f"🔴 分位{percentile:.0f}% 泡沫"
                                _color = "#e74c3c"
                            elif percentile >= 70:
                                _badge = f"🟠 分位{percentile:.0f}% 偏贵"
                                _color = "#f39c12"
                            elif percentile >= 30:
                                _badge = f"⚪ 分位{percentile:.0f}% 合理"
                                _color = "#95a5a6"
                            elif percentile >= 15:
                                _badge = f"🟢 分位{percentile:.0f}% 偏便宜"
                                _color = "#27ae60"
                            else:
                                _badge = f"🟢🟢 分位{percentile:.0f}% 低估"
                                _color = "#1e8449"
                            st.markdown(
                                f'<div style="color:{_color};font-size:12px;margin-top:-10px;">{_badge}</div>',
                                unsafe_allow_html=True
                            )
                        else:
                            st.metric(pe_label, f"{pe:.1f}")
                    else:
                        st.metric(pe_label, "—")
                with col5:
                    st.markdown(f"{signal_label}")
                    st.caption(signal_text[:120])
                with col6:
                    _notes_btn = "📝" + ("🔔" if _has_alert else "")
                    if st.button(_notes_btn, key=f"notes_h_{i}", help="查看/编辑备注和提醒"):
                        st.session_state[f"show_notes_{code}"] = not st.session_state.get(f"show_notes_{code}", False)
                        st.rerun()
                with col7:
                    if st.button("🗑️", key=f"del_h_{i}", help="删除持仓"):
                        holdings.pop(i)
                        new_sha = save_to_github("holdings.json", holdings, st.session_state["holdings_sha"])
                        if new_sha:
                            st.session_state["holdings_sha"] = new_sha
                            st.rerun()

                # ============ 备注面板（点击📝按钮展开）============
                if st.session_state.get(f"show_notes_{code}", False):
                    try:
                        from stock_notes_manager import (
                            get_note, update_note_text, add_reminder,
                            delete_reminder, dismiss_reminder
                        )
                    except Exception as _e:
                        st.error(f"备注模块加载失败：{_e}")
                    else:
                        _note = get_note(code)
                        with st.container():
                            st.markdown("---")
                            st.markdown(f"### 📝 {h.get('name', '')} 持有契约 & 提醒")

                            # Tab 1: 备注  Tab 2: 提醒
                            _tab_n1, _tab_n2 = st.tabs(["📜 持有契约", "🔔 定时提醒"])

                            with _tab_n1:
                                _text = st.text_area(
                                    "记录你对这只股票的认知、买入条件、卖出条件等",
                                    value=_note.get("notes", ""),
                                    height=280,
                                    key=f"note_text_{code}",
                                    help="支持 Markdown 格式。参考模板：持有条件 / 加仓条件 / 清仓条件 / 绝对不做的事"
                                )
                                if _note.get("updated_at"):
                                    st.caption(f"上次更新：{_note.get('updated_at')}")
                                _cnb1, _cnb2 = st.columns([1, 3])
                                with _cnb1:
                                    if st.button("💾 保存", key=f"save_note_{code}"):
                                        update_note_text(code, h.get('name', ''), _text)
                                        # 同步到 GitHub
                                        try:
                                            import json as _j
                                            with open(os.path.join(os.path.dirname(__file__), "stock_notes.json"), encoding="utf-8") as _f:
                                                _notes_all = _j.load(_f)
                                            save_to_github("stock_notes.json", _notes_all, None)
                                        except Exception:
                                            pass
                                        st.success("已保存")
                                        st.rerun()

                            with _tab_n2:
                                # 展示现有提醒
                                _reminders = _note.get("reminders", [])
                                if _reminders:
                                    st.markdown("**现有提醒：**")
                                    for _r in _reminders:
                                        _is_due = _r.get("active") and _r.get("fire_date", "9999") <= datetime.now().strftime("%Y-%m-%d")
                                        _status_text = "🔔 到期" if _is_due else ("⏳ 未到期" if _r.get("active") else "✅ 已关闭")
                                        _row = st.columns([3, 2, 1.5, 1.5])
                                        with _row[0]:
                                            st.markdown(f"**{_r.get('message','')[:40]}**")
                                            st.caption(f"到期日 {_r.get('fire_date')} · {_status_text}")
                                            if _r.get("fired_count", 0):
                                                st.caption(f"已推送 {_r.get('fired_count')} 次")
                                        with _row[1]:
                                            st.caption(f"创建 {_r.get('created_at','')[:10]}")
                                        with _row[2]:
                                            if _r.get("active"):
                                                if st.button("关闭", key=f"dismiss_{code}_{_r.get('id')}"):
                                                    dismiss_reminder(code, _r.get("id"))
                                                    st.success("已关闭提醒")
                                                    st.rerun()
                                        with _row[3]:
                                            if st.button("删除", key=f"del_r_{code}_{_r.get('id')}"):
                                                delete_reminder(code, _r.get("id"))
                                                st.rerun()
                                else:
                                    st.info("暂无提醒")

                                # 新增提醒
                                st.markdown("---")
                                st.markdown("**➕ 新增提醒**")
                                _nr1, _nr2 = st.columns([1, 2])
                                with _nr1:
                                    from datetime import timedelta as _td
                                    _default_date = datetime.now() + _td(days=30)
                                    _fd = st.date_input(
                                        "到期日", value=_default_date,
                                        key=f"new_r_date_{code}"
                                    )
                                with _nr2:
                                    _msg = st.text_input(
                                        "提醒内容", placeholder="如：查看2026Q1财报，核对ROE趋势",
                                        key=f"new_r_msg_{code}"
                                    )
                                if st.button("✅ 添加提醒", key=f"add_r_{code}"):
                                    if _msg.strip():
                                        add_reminder(code, h.get('name',''), _fd.strftime("%Y-%m-%d"), _msg.strip())
                                        # 同步 GitHub
                                        try:
                                            import json as _j
                                            with open(os.path.join(os.path.dirname(__file__), "stock_notes.json"), encoding="utf-8") as _f:
                                                _notes_all = _j.load(_f)
                                            save_to_github("stock_notes.json", _notes_all, None)
                                        except Exception:
                                            pass
                                        st.success(f"已添加提醒，{_fd} 开始推送")
                                        st.rerun()
                                    else:
                                        st.warning("请填写提醒内容")

                            # 关闭面板
                            if st.button("收起 📝", key=f"close_notes_{code}"):
                                st.session_state[f"show_notes_{code}"] = False
                                st.rerun()
                            st.markdown("---")

                # 🚨 "必须卖"警告（基本面恶化触发，即使割肉也要卖）
                if must_sell:
                    st.error(
                        f"🚨 **{h.get('name', '未知')} 必须卖出**\n\n{pnl_advice}"
                    )
                # ⚠ "卖出无意义" 软提示（持仓几乎平本或浮亏但只是估值偏高）
                elif pnl_advice and ("⚠" in pnl_advice or "无意义" in pnl_advice):
                    st.info(f"💡 {h.get('name', '未知')}：{pnl_advice}")

                # 合格公司现金流警示（已豁免护城河规则但需重点关注）
                cf_warning = sig_data.get("cf_warning") if not is_etf else None
                if cf_warning:
                    st.warning(
                        f"⚠️ **{h.get('name', '未知')} 重点关注**：该股触发现金流异常但因高ROE+高毛利被豁免，"
                        f"建议用以下多维线索判断真假跌：\n\n"
                        f"{cf_warning}"
                    )
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
                    # 财务指标摘要
                    _fm = []
                    if pe and pe > 0:
                        _fm.append(f"市盈率 {pe:.1f}")
                    _roe = data.get("roe")
                    if _roe is not None:
                        _fm.append(f"净收益率 {_roe}%")
                    _gm = data.get("gross_margin")
                    if _gm is not None:
                        _fm.append(f"毛利 {_gm}%")
                    _dr = data.get("debt_ratio")
                    if _dr is not None:
                        _fm.append(f"负债 {_dr}%")
                    if div_yield and div_yield > 0:
                        _fm.append(f"股息 {div_yield:.1f}%")
                    st.caption(" | ".join(_fm) if _fm else item.get("note", ""))
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

# ============================================
# Tab4: ETF 监测（持仓里每只 ETF 的估值/分位/买卖信号）
# ============================================
with tab4:
    st.header("🧊 ETF 监测")
    st.caption("持仓中每只 ETF 的跟踪指数估值、历史分位和买卖信号")

    daily = st.session_state.get("daily", {})
    etf_signals = daily.get("etf_signals", []) if isinstance(daily, dict) else []
    etf_unmapped = daily.get("etf_unmapped", []) if isinstance(daily, dict) else []

    if not etf_signals and not etf_unmapped:
        st.info(
            "暂无 ETF 监测数据。在持仓里加入 ETF（5/1 开头代码）后，下一次"
            "系统运行会自动开始采集跟踪指数的估值。"
        )
    else:
        # 价值观提示（永久固定）
        st.warning(
            "⚠ **宽基 ETF 不是类固收**。它仍然是权益资产，2008 年标普 500 跌 37%，"
            "2015 年沪深 300 半年跌 43%。本页面只帮你判断估值高低，不代表"
            "ETF 可以零风险持有。"
        )

        # 按温度档位分组展示
        by_level = {2: [], 1: [], 0: [], -1: [], -2: [], None: []}
        for r in etf_signals:
            level = r.get("temperature", {}).get("level")
            pct = r.get("temperature", {}).get("percentile")
            if pct is None:
                by_level[None].append(r)
            else:
                by_level[level].append(r)

        level_meta = [
            (2, "🔴 泡沫区·卖盈利保底仓", "#ffebee"),
            (1, "🔥 偏热·暂停加仓·新钱转便宜标的", "#fff3e0"),
            (0, "⚪ 正常·按节奏", "#f5f5f5"),
            (-1, "🧊 偏冷·重点加仓", "#e3f2fd"),
            (-2, "❄️ 低估区·全力加仓", "#e8f5e9"),
            (None, "📊 数据积累中（新 ETF 首次采集，需 ≥60 条才能判分位）", "#fafafa"),
        ]

        for level, title, bg in level_meta:
            items = by_level[level]
            if not items:
                continue
            st.markdown(f"### {title}")
            for r in items:
                temp = r.get("temperature", {})
                cost = r.get("cost", 0) or 0
                shares = r.get("shares", 0) or 0
                value = cost * shares
                kind_cn = {
                    "broad": "宽基",
                    "strategy_dividend": "策略·红利",
                    "strategy": "策略",
                    "sector": "行业",
                }.get(r.get("kind", ""), "-")
                pe = temp.get("current_pe")
                pct = temp.get("percentile")
                div = temp.get("current_dividend_yield")
                dp = temp.get("data_points", 0)
                note = temp.get("note", "")
                signal_text = r.get("signal_text", "")
                # 浮盈字段
                current_price = r.get("current_price")
                pnl_pct = r.get("pnl_pct")
                pnl_label = r.get("pnl_label", "")
                pnl_advice = r.get("pnl_advice", "")
                must_sell = r.get("must_sell", False)

                info_parts = []
                if pe is not None:
                    info_parts.append(f"PE={pe}")
                if div is not None:
                    info_parts.append(f"股息率={div}%")
                if pct is not None:
                    info_parts.append(f"历史分位={pct}%")
                info_parts.append(f"数据点={dp}")
                info_line = " | ".join(info_parts)

                # 浮盈行
                if current_price and pnl_pct is not None:
                    pnl_color = "#d32f2f" if pnl_pct < 0 else "#388e3c"
                    pnl_line = (
                        f'现价 ¥{current_price:.3f} · '
                        f'<span style="color:{pnl_color};font-weight:bold;">浮盈 {pnl_pct:+.1f}%</span> · '
                        f'<span style="color:#888;">{pnl_label}</span>'
                    )
                else:
                    pnl_line = ""

                # HTML 必须顶格无缩进（Markdown 4 格缩进陷阱，见历史 bug）
                name_str = f"{r.get('name','')} ({r.get('code','')})"
                sub_str = f"· {kind_cn} · 跟踪 {r.get('index_name','')}"
                note_html = f'<div style="color:#999;font-size:11px;margin-top:4px;">{note}</div>' if note else ''
                pnl_html = f'<div style="color:#555;font-size:12px;margin-top:4px;">{pnl_line}</div>' if pnl_line else ''
                card_html = (
                    f'<div style="background:{bg};padding:12px 16px;border-radius:6px;margin-bottom:8px;">'
                    f'<div style="font-weight:bold;font-size:15px;">{name_str} '
                    f'<span style="color:#888;font-size:12px;font-weight:normal;">{sub_str}</span></div>'
                    f'<div style="color:#444;font-size:13px;margin-top:4px;">{info_line}</div>'
                    f'<div style="color:#666;font-size:12px;margin-top:4px;">'
                    f'持仓：{shares:,} 股 · 成本 ¥{cost} · 市值 ¥{value:,.0f}</div>'
                    f'{pnl_html}'
                    f'<div style="color:#1976d2;font-size:13px;margin-top:6px;font-weight:bold;">{signal_text}</div>'
                    f'{note_html}'
                    f'</div>'
                )
                st.markdown(card_html, unsafe_allow_html=True)

                # "必须卖"红色警告框（基本面恶化，即使割肉也卖）
                if must_sell:
                    st.error(f"🚨 **{r.get('name','')} 必须卖出**\n\n{pnl_advice}")

        if etf_unmapped:
            st.divider()
            st.warning(
                f"**以下 {len(etf_unmapped)} 只 ETF 未在 etf_index_map.json 中映射**，"
                f"需要手工补充才能开始估值监测："
            )
            for e in etf_unmapped:
                st.write(f"- {e.get('code','')} {e.get('name','')}")
            st.caption("补充方式：在 etf_index_map.json 的 map 字段里追加条目，格式：`\"510300\": {\"index\": \"000300\", \"name\": \"沪深300\", \"kind\": \"broad\"}`")

st.divider()
st.caption("💡 模型推荐来自全市场扫描 | 关注表和持仓每日更新PE和信号 | 系统每交易日下午5点自动运行")
