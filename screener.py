"""
筛选引擎 - 芒格/巴菲特价值投资体系
估值模型：行业分类PE区间 + PEG + 周期股特殊处理
"""

import time
import numpy as np
import pandas as pd
import yaml
from data_fetcher import (
    get_all_stocks,
    get_financial_indicator,
    extract_annual_data,
    get_roe_series,
    get_debt_info,
    get_opm_series,
    get_fcf_series,
    get_realtime_quotes,
    get_batch_roe_data,
    get_pe_ttm,
    find_column,
)


def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ============================================
# 行业PE估值区间（A股参考）
# ============================================
INDUSTRY_PE = {
    # =============================================
    # 简单生意（simple）：一看就懂、涨价换标签、低资本开支
    # 巴菲特最爱：可口可乐、喜诗糖果类
    # =============================================
    "白酒": {"low": 15, "fair_low": 20, "fair_high": 30, "high": 40, "type": "consumer", "complexity": "simple"},
    "食品饮料": {"low": 15, "fair_low": 20, "fair_high": 30, "high": 40, "type": "consumer", "complexity": "simple"},
    "调味品": {"low": 18, "fair_low": 22, "fair_high": 35, "high": 45, "type": "consumer", "complexity": "simple"},
    "调味发酵品": {"low": 18, "fair_low": 22, "fair_high": 35, "high": 45, "type": "consumer", "complexity": "simple"},
    "乳制品": {"low": 12, "fair_low": 15, "fair_high": 25, "high": 30, "type": "consumer", "complexity": "simple"},
    "饮料乳品": {"low": 12, "fair_low": 15, "fair_high": 25, "high": 30, "type": "consumer", "complexity": "simple"},
    "中药": {"low": 15, "fair_low": 20, "fair_high": 30, "high": 40, "type": "consumer", "complexity": "simple"},
    "家电": {"low": 10, "fair_low": 15, "fair_high": 25, "high": 30, "type": "consumer", "complexity": "simple"},
    "传媒": {"low": 15, "fair_low": 20, "fair_high": 30, "high": 40, "type": "consumer", "complexity": "simple"},
    "银行": {"low": 5, "fair_low": 6, "fair_high": 9, "high": 12, "type": "mature", "complexity": "simple"},
    "保险": {"low": 6, "fair_low": 8, "fair_high": 12, "high": 16, "type": "mature", "complexity": "simple"},
    "免税": {"low": 18, "fair_low": 25, "fair_high": 40, "high": 50, "type": "consumer", "complexity": "simple"},
    "旅游零售": {"low": 18, "fair_low": 25, "fair_high": 40, "high": 50, "type": "consumer", "complexity": "simple"},
    "医药": {"low": 15, "fair_low": 20, "fair_high": 30, "high": 40, "type": "growth", "complexity": "simple"},
    "生物制品": {"low": 15, "fair_low": 20, "fair_high": 30, "high": 40, "type": "growth", "complexity": "simple"},

    # =============================================
    # 中等复杂（medium）：能理解但有门槛、资本开支中等
    # =============================================
    "电力": {"low": 8, "fair_low": 10, "fair_high": 18, "high": 22, "type": "utility", "complexity": "medium"},
    "公用事业": {"low": 8, "fair_low": 10, "fair_high": 18, "high": 22, "type": "utility", "complexity": "medium"},
    "交通运输": {"low": 8, "fair_low": 12, "fair_high": 16, "high": 22, "type": "utility", "complexity": "medium"},
    "铁路": {"low": 8, "fair_low": 10, "fair_high": 16, "high": 20, "type": "utility", "complexity": "medium"},
    "铁路公路": {"low": 8, "fair_low": 10, "fair_high": 16, "high": 20, "type": "utility", "complexity": "medium"},
    "高速": {"low": 8, "fair_low": 10, "fair_high": 16, "high": 20, "type": "utility", "complexity": "medium"},
    "通信": {"low": 15, "fair_low": 20, "fair_high": 35, "high": 50, "type": "tech", "complexity": "medium"},
    "通信服务": {"low": 15, "fair_low": 20, "fair_high": 35, "high": 50, "type": "tech", "complexity": "medium"},
    "医疗器械": {"low": 18, "fair_low": 22, "fair_high": 35, "high": 50, "type": "growth", "complexity": "medium"},

    # =============================================
    # 复杂生意（complex）：重资产、技术变化快、需持续大额投入
    # 巴菲特不爱：需要不断烧钱、买设备、盖工厂
    # 买入信号自动降一级
    # =============================================
    "半导体": {"low": 30, "fair_low": 40, "fair_high": 65, "high": 80, "type": "tech", "complexity": "complex"},
    "芯片": {"low": 30, "fair_low": 40, "fair_high": 65, "high": 80, "type": "tech", "complexity": "complex"},
    "软件": {"low": 30, "fair_low": 40, "fair_high": 60, "high": 80, "type": "tech", "complexity": "medium"},
    "军工": {"low": 25, "fair_low": 35, "fair_high": 55, "high": 70, "type": "tech", "complexity": "complex"},
    "航空航天": {"low": 25, "fair_low": 35, "fair_high": 55, "high": 70, "type": "tech", "complexity": "complex"},
    "新能源": {"low": 20, "fair_low": 30, "fair_high": 50, "high": 60, "type": "tech", "complexity": "complex"},
    "锂电": {"low": 20, "fair_low": 30, "fair_high": 50, "high": 60, "type": "tech", "complexity": "complex"},
    "电池": {"low": 20, "fair_low": 30, "fair_high": 50, "high": 60, "type": "tech", "complexity": "complex"},
    "光伏": {"low": 15, "fair_low": 25, "fair_high": 45, "high": 55, "type": "tech", "complexity": "complex"},
    "轨道交通": {"low": 10, "fair_low": 13, "fair_high": 20, "high": 28, "type": "cycle", "complexity": "complex"},
    "轨交设备": {"low": 10, "fair_low": 13, "fair_high": 20, "high": 28, "type": "cycle", "complexity": "complex"},
    "铁路装备": {"low": 10, "fair_low": 13, "fair_high": 20, "high": 28, "type": "cycle", "complexity": "complex"},
    "铁路设备": {"low": 10, "fair_low": 13, "fair_high": 20, "high": 28, "type": "cycle", "complexity": "complex"},
    "机械制造": {"low": 10, "fair_low": 15, "fair_high": 25, "high": 35, "type": "cycle", "complexity": "complex"},
    "汽车玻璃": {"low": 10, "fair_low": 14, "fair_high": 22, "high": 30, "type": "cycle", "complexity": "complex"},
    "汽车零部件": {"low": 10, "fair_low": 14, "fair_high": 22, "high": 30, "type": "cycle", "complexity": "complex"},
    "建筑": {"low": 5, "fair_low": 7, "fair_high": 12, "high": 16, "type": "cycle", "complexity": "complex"},
    "钢铁": {"low": 5, "fair_low": 7, "fair_high": 12, "high": 16, "type": "cycle", "complexity": "complex"},
    "煤炭": {"low": 5, "fair_low": 7, "fair_high": 12, "high": 16, "type": "cycle", "complexity": "complex"},
    "煤炭开采": {"low": 5, "fair_low": 7, "fair_high": 12, "high": 16, "type": "cycle", "complexity": "complex"},
    "化工": {"low": 8, "fair_low": 12, "fair_high": 20, "high": 30, "type": "cycle", "complexity": "complex"},
    "化学制品": {"low": 8, "fair_low": 12, "fair_high": 20, "high": 30, "type": "cycle", "complexity": "complex"},
    "农化制品": {"low": 8, "fair_low": 12, "fair_high": 20, "high": 30, "type": "cycle", "complexity": "complex"},
    "有色金属": {"low": 8, "fair_low": 12, "fair_high": 20, "high": 30, "type": "cycle", "complexity": "complex"},
    "工业金属": {"low": 8, "fair_low": 12, "fair_high": 20, "high": 30, "type": "cycle", "complexity": "complex"},
    "稀土": {"low": 10, "fair_low": 15, "fair_high": 25, "high": 35, "type": "cycle", "complexity": "complex"},
    "小金属": {"low": 10, "fair_low": 15, "fair_high": 25, "high": 35, "type": "cycle", "complexity": "complex"},
    "矿业": {"low": 8, "fair_low": 10, "fair_high": 18, "high": 25, "type": "cycle", "complexity": "complex"},
}

# 默认PE区间（找不到行业时用）
DEFAULT_PE = {"low": 10, "fair_low": 15, "fair_high": 25, "high": 35, "type": "default", "complexity": "medium"}

# 复杂度对ROE门槛（基于巴菲特1987年股东信）
# 巴菲特原话：10年均值≥20%，且单年不低于15%
# 简单生意：按巴菲特标准（20%重仓/15%轻仓/12%关注）
# 中等复杂：略严（需更多利润缓冲技术门槛）
# 复杂生意：最严（重资产持续烧钱，必须赚足够多）
COMPLEXITY_ROE_ADJUST = {
    "simple": {"heavy": 20, "light": 15, "watch": 12},   # 巴菲特标准
    "medium": {"heavy": 22, "light": 17, "watch": 14},   # 略严
    "complex": {"heavy": 25, "light": 20, "watch": 15},  # 最严
}


def match_industry_pe(industry_str):
    """根据股票所属行业匹配PE区间"""
    if not industry_str:
        return DEFAULT_PE
    for key, val in INDUSTRY_PE.items():
        if key in industry_str:
            return val
    return DEFAULT_PE


def get_pe_signal(current_pe, industry="", net_profit_growth=None):
    """
    行业感知的PE估值信号
    1. 按行业PE区间判断
    2. 结合PEG（如果有增速数据）
    3. 周期股特殊处理
    """
    if current_pe is None or np.isnan(current_pe):
        return None, "PE数据缺失"

    if current_pe <= 0:
        return None, "PE为负（亏损），不适用PE估值"

    pe_range = match_industry_pe(industry)
    industry_type = pe_range["type"]

    # 周期股特殊处理：PE极低可能是周期顶部
    if industry_type == "cycle":
        if current_pe < pe_range["low"]:
            # 周期股PE极低反而可能是卖点（盈利暴增的顶部）
            return "sell_watch", f"PE={current_pe:.1f}（周期股PE极低，可能是周期顶部，注意风险）"
        elif current_pe > pe_range["high"] * 2:
            # 周期股PE极高反而可能是买点（盈利低谷）
            return "buy_watch", f"PE={current_pe:.1f}（周期股PE极高，可能是周期底部，关注拐点）"

    # PEG判断（如有增速数据）
    peg_hint = ""
    if net_profit_growth and net_profit_growth > 0 and industry_type in ("growth", "tech", "consumer"):
        peg = current_pe / net_profit_growth
        if peg <= 0.8:
            peg_hint = f" PEG={peg:.1f}极低"
        elif peg <= 1.0:
            peg_hint = f" PEG={peg:.1f}合理"
        elif peg <= 1.5:
            peg_hint = f" PEG={peg:.1f}偏高"
        else:
            peg_hint = f" PEG={peg:.1f}高估"

    # 基于行业PE区间判断
    if current_pe <= pe_range["low"]:
        return "buy_heavy", f"PE={current_pe:.1f}，远低于行业底部{pe_range['low']}{peg_hint}→可以重仓买入"
    elif current_pe <= (pe_range["low"] + pe_range["fair_low"]) / 2:
        return "buy_medium", f"PE={current_pe:.1f}，明显低于合理区间{peg_hint}→可以中仓买入"
    elif current_pe <= pe_range["fair_low"]:
        return "buy_light", f"PE={current_pe:.1f}，低于行业合理区间{pe_range['fair_low']}-{pe_range['fair_high']}{peg_hint}→可以轻仓买入"
    elif current_pe <= pe_range["fair_high"]:
        mid = (pe_range["fair_low"] + pe_range["fair_high"]) / 2
        if current_pe <= mid * 0.9:
            return "buy_watch", f"PE={current_pe:.1f}，合理偏低{peg_hint}→重点关注买入"
        elif current_pe >= mid * 1.1:
            return "sell_watch", f"PE={current_pe:.1f}，合理偏高{peg_hint}→重点关注卖出"
        else:
            return "hold", f"PE={current_pe:.1f}，处于合理区间{pe_range['fair_low']}-{pe_range['fair_high']}{peg_hint}"
    elif current_pe <= (pe_range["fair_high"] + pe_range["high"]) / 2:
        return "sell_light", f"PE={current_pe:.1f}，高于合理区间{peg_hint}→可以适当卖出"
    elif current_pe <= pe_range["high"]:
        return "sell_medium", f"PE={current_pe:.1f}，明显高于合理区间{peg_hint}→可以中仓卖出"
    else:
        return "sell_heavy", f"PE={current_pe:.1f}，远高于行业上限{pe_range['high']}{peg_hint}→可以大量卖出"


# ============================================
# 财务指标检查（同之前）
# ============================================

def check_roe_no_leverage(df_annual, config):
    roe_series = get_roe_series(df_annual)
    if roe_series is None or len(roe_series) < 5:
        return False, "ROE数据不足"
    avg_roe = roe_series.mean()
    if avg_roe < config["screener"]["roe_min"]:
        return False, f"ROE均值{avg_roe:.1f}%"
    debt_info = get_debt_info(df_annual)
    if debt_info and debt_info.get("debt_ratio"):
        debt_ratio = debt_info["debt_ratio"]
        if not np.isnan(debt_ratio) and debt_ratio > config["screener"]["debt_ratio_max"]:
            return False, f"ROE{avg_roe:.1f}%但负债率{debt_ratio:.1f}%（高杠杆）"
    return True, f"ROE均值{avg_roe:.1f}%"


def check_debt_health(df_annual, config):
    debt_info = get_debt_info(df_annual)
    if debt_info is None:
        return False, "负债数据不足"
    debt_ratio = debt_info.get("debt_ratio")
    current_ratio = debt_info.get("current_ratio")
    if debt_ratio is None or np.isnan(debt_ratio) or debt_ratio > config["screener"]["debt_ratio_max"]:
        return False, f"负债率{'%.1f' % debt_ratio if debt_ratio else '?'}%"
    detail = f"负债率{debt_ratio:.1f}%"
    if current_ratio and not np.isnan(current_ratio):
        if current_ratio < config["screener"]["current_ratio_min"]:
            return False, f"流动比率{current_ratio:.2f}偏低"
        detail += f" 流动比率{current_ratio:.2f}"
    return True, detail


def check_opm_stable(df_annual, config):
    opm_series = get_opm_series(df_annual)
    if opm_series is None or len(opm_series) < 5:
        return False, "利润率数据不足"
    values = opm_series.values[::-1]
    if len(values) >= 3:
        slope = np.polyfit(np.arange(len(values)), values, 1)[0]
        if slope < -0.5:
            return False, f"利润率下滑（年均降{abs(slope):.1f}个百分点）"
    return True, f"利润率均值{opm_series.mean():.1f}%稳定"


def check_fcf(df_annual, config):
    fcf_series = get_fcf_series(df_annual)
    if fcf_series is None or len(fcf_series) < 3:
        return False, "现金流数据不足"
    recent = fcf_series.head(config["screener"]["fcf_positive_years"])
    positive = (recent > 0).sum()
    if positive < len(recent) * 0.8:
        return False, f"近{len(recent)}年中{len(recent)-positive}年现金流为负"
    return True, f"现金流充足"


def check_gross_margin(df_annual, config):
    col = find_column(df_annual, ["销售毛利率", "毛利率"])
    if col is None:
        return False, "毛利率缺失"
    values = pd.to_numeric(df_annual[col], errors="coerce").dropna()
    if len(values) < 3:
        return False, "毛利率不足"
    avg = values.mean()
    if avg < config["screener"]["gross_margin_min"]:
        return False, f"毛利率{avg:.1f}%"
    return True, f"毛利率{avg:.1f}%"


# ============================================
# 主筛选
# ============================================

def screen_single_stock(code, config, quotes_df):
    result = {"code": code, "passed": False, "checks": {}, "signal": None, "signal_text": "", "pe": None, "price": None}

    df_indicator = get_financial_indicator(code)
    if df_indicator is None:
        return result
    df_annual = extract_annual_data(df_indicator, years=10)
    if df_annual.empty or len(df_annual) < 3:
        return result

    for check_name, check_func in [
        ("roe", lambda: check_roe_no_leverage(df_annual, config)),
        ("debt", lambda: check_debt_health(df_annual, config)),
        ("opm", lambda: check_opm_stable(df_annual, config)),
        ("fcf", lambda: check_fcf(df_annual, config)),
        ("gross_margin", lambda: check_gross_margin(df_annual, config)),
    ]:
        passed, detail = check_func()
        result["checks"][check_name] = {"passed": passed, "detail": detail}
        if not passed:
            return result

    # 股价预筛（用实时行情的价格，不查PE）
    if quotes_df is not None and not quotes_df.empty:
        row = quotes_df[quotes_df["代码"] == code]
        if not row.empty:
            row = row.iloc[0]
            price = pd.to_numeric(row.get("最新价"), errors="coerce")
            result["price"] = price

            max_price = config["screener"]["max_price_per_share"]
            if not pd.isna(price) and price > max_price:
                return result

            # 财务全部通过后，才查PE(TTM)（节省API调用）
            pe = None
            ttm_data = get_pe_ttm(code)
            if ttm_data and ttm_data.get("pe_ttm"):
                pe = ttm_data["pe_ttm"]
            else:
                pe = pd.to_numeric(row.get("市盈率-动态"), errors="coerce")
            result["pe"] = pe

            industry = str(row.get("所属行业", "")) if "所属行业" in quotes_df.columns else ""
            signal, signal_text = get_pe_signal(pe, industry)
            result["signal"] = signal
            result["signal_text"] = signal_text

    result["passed"] = True
    return result


def screen_all_stocks(config):
    print("正在获取A股列表...")
    stocks = get_all_stocks()
    if stocks.empty:
        return []
    print(f"共 {len(stocks)} 只股票")

    # 批量ROE预筛
    candidate_codes = set()
    for date in ["20241231", "20231231"]:
        df = get_batch_roe_data(date=date)
        if df is not None and not df.empty:
            roe_col = None
            for col in df.columns:
                if "净资产收益率" in col:
                    roe_col = col
                    break
            if roe_col:
                df[roe_col] = pd.to_numeric(df[roe_col], errors="coerce")
                filtered = df[df[roe_col] >= 15]
                code_col = None
                for col in df.columns:
                    if "代码" in col or "股票代码" in col:
                        code_col = col
                        break
                if code_col:
                    candidate_codes = set(filtered[code_col].astype(str).tolist())
                    print(f"  ROE≥15%: {len(candidate_codes)} 只")
            break

    if not candidate_codes:
        candidate_codes = set(stocks["code"].tolist())
    candidate_codes &= set(stocks["code"].tolist())

    quotes_df = get_realtime_quotes()

    max_price = config["screener"]["max_price_per_share"]
    if quotes_df is not None and not quotes_df.empty:
        quotes_df["价格_num"] = pd.to_numeric(quotes_df["最新价"], errors="coerce")
        affordable = quotes_df[(quotes_df["价格_num"] > 0) & (quotes_df["价格_num"] <= max_price)]
        candidate_codes &= set(affordable["代码"].tolist())
        print(f"  股价≤{max_price}元: {len(candidate_codes)} 只")

    passed = []
    total = len(candidate_codes)
    for i, code in enumerate(candidate_codes, 1):
        if i % 10 == 0:
            print(f"深度分析: {i}/{total}")
        result = screen_single_stock(code, config, quotes_df)
        if result["passed"]:
            name_row = stocks[stocks["code"] == code]
            result["name"] = name_row.iloc[0]["name"] if not name_row.empty else code
            passed.append(result)
        time.sleep(0.3)

    signal_order = {"buy_heavy": 0, "buy_medium": 1, "buy_light": 2, "buy_watch": 3, "hold_keep": 4, "hold": 5, "sell_watch": 6, "sell_light": 7, "sell_medium": 8, "sell_heavy": 9, "true_decline": 10, None: 11}
    passed.sort(key=lambda x: signal_order.get(x.get("signal"), 7))
    print(f"\n候选池: {len(passed)} 只好公司")
    return passed


def check_holdings_sell_signals(holdings, config):
    """
    检查持仓信号：
    1. 自动获取真实行业
    2. 判断真跌/假跌
    3. 真跌→基本面恶化警告
    4. 假跌或判定不清→按PE给关注/适当/中仓/大量卖出信号
    """
    if not holdings:
        return []
    print("检查持仓信号...")
    quotes_df = get_realtime_quotes()
    signals = []

    for h in holdings:
        code = h["code"]
        name = h.get("name", code)
        if quotes_df is None or quotes_df.empty:
            continue

        row = quotes_df[quotes_df["代码"] == code]
        if row.empty:
            continue
        row = row.iloc[0]

        price = pd.to_numeric(row.get("最新价"), errors="coerce")

        # 1. 自动获取真实行业
        industry = ""
        if "所属行业" in quotes_df.columns:
            industry = str(row.get("所属行业", ""))

        # 2. 获取PE(TTM)
        pe = None
        ttm_data = get_pe_ttm(code)
        if ttm_data and ttm_data.get("pe_ttm"):
            pe = ttm_data["pe_ttm"]
        else:
            pe = pd.to_numeric(row.get("市盈率-动态"), errors="coerce")

        # 3. PE信号
        signal, signal_text = get_pe_signal(pe, industry)

        # 4. 如果是卖出信号，先判断真跌还是假跌
        if signal and "sell" in signal:
            is_healthy, problems = check_fundamental_health(code)

            if is_healthy is not None and not is_healthy:
                # 真跌：基本面恶化，直接发最严重警告
                signal = "true_decline"
                signal_text = f"基本面恶化({', '.join(problems[:3])})，建议卖出"
                print(f"  {name} 真跌→基本面恶化")
            else:
                # 假跌或判定不清：按PE级别给卖出信号（关注/适当/中仓/大量）
                # 检查同行业是否也在跌
                if industry:
                    peers = quotes_df[quotes_df.get("所属行业", pd.Series()) == industry]
                    if len(peers) > 3:
                        peer_changes = pd.to_numeric(peers.get("涨跌幅", pd.Series()), errors="coerce").dropna()
                        if peer_changes.mean() < -1:
                            signal_text += "（同行业普跌，市场因素）"
                print(f"  {name} PE卖出信号: {signal}")

        # 5. 持仓股：hold变成"建议持续持有"
        if signal == "hold":
            signal = "hold_keep"
            signal_text += " →建议持续持有"

        signals.append({
            "code": code, "name": name,
            "shares": h.get("shares", 0), "cost": h.get("cost", 0),
            "price": price if not pd.isna(price) else 0,
            "pe": pe if not pd.isna(pe) else 0,
            "signal": signal, "signal_text": signal_text,
            "industry": industry,
        })
        time.sleep(0.3)

    return signals


# ============================================
# 关注表财务健康验证（买入前必须过关）
# ============================================

def check_watchlist_financial_health(code, industry=""):
    """
    对关注表股票做财务健康检查+ROE等级判定
    根据行业复杂度+杠杆率动态调整ROE门槛：
    - 简单生意+低杠杆：ROE 12%可重仓（巴菲特最爱）
    - 复杂生意+高杠杆：ROE 25%才可重仓（必须赚够多）
    """
    df = get_financial_indicator(code)
    if df is None:
        return True, "财务数据不可用", "watch"  # 保守：最高关注

    df_annual = extract_annual_data(df, years=5)
    if df_annual.empty:
        return True, "无年报数据", "watch"  # 保守：最高关注

    warnings = []
    roe_level = "heavy"

    # 获取行业复杂度
    pe_range = match_industry_pe(industry)
    complexity = pe_range.get("complexity", "medium")

    # 基础ROE门槛（按行业复杂度）
    base_thresholds = COMPLEXITY_ROE_ADJUST.get(complexity, COMPLEXITY_ROE_ADJUST["medium"])

    # 再根据杠杆率微调
    roe_series = get_roe_series(df_annual)
    debt_info_for_roe = get_debt_info(df_annual)
    debt_ratio_val = 50
    if debt_info_for_roe and debt_info_for_roe.get("debt_ratio"):
        dr = debt_info_for_roe["debt_ratio"]
        if not np.isnan(dr):
            debt_ratio_val = dr

    # 杠杆调整：低杠杆降2%门槛，高杠杆加5%门槛
    leverage_adj = 0
    if debt_ratio_val < 30:
        leverage_adj = -2  # 低杠杆，放宽
    elif debt_ratio_val > 50:
        leverage_adj = 5   # 高杠杆，加严

    roe_thresholds = {
        "heavy": base_thresholds["heavy"] + leverage_adj,
        "light": base_thresholds["light"] + leverage_adj,
        "watch": base_thresholds["watch"] + leverage_adj,
    }

    complexity_label = {"simple": "简单生意", "medium": "中等复杂", "complex": "复杂生意"}.get(complexity, "")
    if complexity == "complex":
        warnings.append(f"复杂生意(重仓需ROE≥{roe_thresholds['heavy']}%)")

    if roe_series is not None and len(roe_series) >= 2:
        avg_roe = roe_series.mean()
        data_years = len(roe_series)

        # 数据不足8年，自动降一级
        downgrade = 1 if data_years < 8 else 0

        if avg_roe >= roe_thresholds["heavy"]:
            levels = ["heavy", "light", "watch", "watch"]
            roe_level = levels[min(downgrade, len(levels)-1)]
        elif avg_roe >= roe_thresholds["light"]:
            levels = ["light", "watch", "watch"]
            roe_level = levels[min(downgrade, len(levels)-1)]
        elif avg_roe >= roe_thresholds["watch"]:
            roe_level = "watch"
        else:
            roe_level = "none"
            warnings.append(f"ROE={avg_roe:.1f}%过低")

        if data_years < 8:
            warnings.append(f"仅{data_years}年数据")
        if roe_level != "heavy" and roe_level != "none":
            debt_note = f"负债率{debt_ratio_val:.0f}%" if debt_ratio_val < 30 else ""
            warnings.append(f"ROE={avg_roe:.1f}% {debt_note}")
    else:
        roe_level = "watch"
        warnings.append("ROE数据缺失")

    # 1. 负债率检查（>55%警告）
    debt_info = get_debt_info(df_annual)
    if debt_info and debt_info.get("debt_ratio"):
        dr = debt_info["debt_ratio"]
        if not np.isnan(dr) and dr > 55:
            warnings.append(f"负债率{dr:.0f}%偏高")

    # 2. 流动比率检查（<1.0警告）
    if debt_info and debt_info.get("current_ratio"):
        cr = debt_info["current_ratio"]
        if not np.isnan(cr) and cr < 1.0:
            warnings.append(f"流动比率{cr:.2f}偏低")

    # 3. 营业利润率是否下滑
    opm = get_opm_series(df_annual)
    if opm is not None and len(opm) >= 3:
        values = opm.values[::-1]
        slope = np.polyfit(np.arange(len(values)), values, 1)[0]
        if slope < -1.0:
            warnings.append("利润率持续下滑")

    # 4. 现金流检查
    fcf = get_fcf_series(df_annual)
    if fcf is not None and len(fcf) >= 2:
        if (fcf.head(2) <= 0).all():
            warnings.append("现金流连续为负")

    # 4. 现金流检查
    fcf = get_fcf_series(df_annual)
    if fcf is not None and len(fcf) >= 2:
        if (fcf.head(2) <= 0).all():
            warnings.append("现金流连续为负")

    has_risk = any(w for w in warnings if "负债率" in w or "流动比率" in w or "现金流" in w or "利润率" in w)
    return not has_risk, "、".join(warnings) if warnings else "", roe_level


# ============================================
# 真假下跌判断
# ============================================

def check_fundamental_health(code):
    """检查公司基本面是否健康（用于区分真假下跌）"""
    df = get_financial_indicator(code)
    if df is None:
        return None, []

    df_annual = extract_annual_data(df, years=5)
    if df_annual.empty or len(df_annual) < 2:
        return None, []

    problems = []  # 基本面问题列表
    healthy = []   # 健康指标列表

    # 1. 营收利润是否连续下滑
    rev_col = find_column(df_annual, ["营业总收入增长率", "主营业务收入增长率"])
    if rev_col:
        rev_growth = pd.to_numeric(df_annual[rev_col], errors="coerce").dropna()
        if len(rev_growth) >= 2:
            recent2 = rev_growth.head(2).values
            if all(v < 0 for v in recent2):
                problems.append(f"营收连续{len([v for v in recent2 if v<0])}年下滑")
            else:
                healthy.append("营收正增长")

    # 2. 净利润是否连续下滑
    profit_col = find_column(df_annual, ["净利润增长率", "净利润同比增长率"])
    if profit_col:
        profit_growth = pd.to_numeric(df_annual[profit_col], errors="coerce").dropna()
        if len(profit_growth) >= 2:
            recent2 = profit_growth.head(2).values
            if all(v < 0 for v in recent2):
                problems.append(f"净利润连续下滑")
            else:
                healthy.append("利润正增长")

    # 3. 毛利率/净利率是否稳定
    gm_col = find_column(df_annual, ["销售毛利率", "毛利率"])
    if gm_col:
        gm = pd.to_numeric(df_annual[gm_col], errors="coerce").dropna()
        if len(gm) >= 3:
            gm_values = gm.values[::-1]
            slope = np.polyfit(np.arange(len(gm_values)), gm_values, 1)[0]
            if slope < -1.0:
                problems.append(f"毛利率持续下降")
            else:
                healthy.append(f"毛利率稳定{gm.iloc[0]:.1f}%")

    # 4. 现金流是否健康
    fcf = get_fcf_series(df_annual)
    if fcf is not None and len(fcf) >= 2:
        recent_fcf = fcf.head(2)
        if (recent_fcf <= 0).all():
            problems.append("现金流连续为负")
        else:
            healthy.append("现金流健康")

    # 5. 应收账款是否暴增
    ar_col = find_column(df_annual, ["应收账款周转率"])
    if ar_col:
        ar = pd.to_numeric(df_annual[ar_col], errors="coerce").dropna()
        if len(ar) >= 2:
            if ar.iloc[0] < ar.iloc[1] * 0.7:
                problems.append("应收账款周转率大幅下降")

    # 6. ROE连续下滑（巴菲特清仓信号）
    # 从20%+掉到<15%且持续2-3年 → 基本面恶化
    roe_series = get_roe_series(df_annual)
    if roe_series is not None and len(roe_series) >= 3:
        recent_roe = roe_series.head(3).values
        # 连续3年下滑
        if all(recent_roe[i] > recent_roe[i+1] for i in range(len(recent_roe)-1)):
            if recent_roe[-1] < 15:
                problems.append(f"ROE连续下滑至{recent_roe[-1]:.1f}%（破15%底线）")
            elif recent_roe[0] - recent_roe[-1] > 5:
                problems.append(f"ROE连续下滑（从{recent_roe[0]:.1f}%降至{recent_roe[-1]:.1f}%）")

    # 7. 高ROE但现金流远低于净利润（虚假ROE）
    # 巴菲特：经营现金流应≥净利润
    if fcf is not None and len(fcf) >= 1:
        profit_col2 = find_column(df_annual, ["净利润增长率"])
        # 如果现金流远低于0但ROE还在20%以上，说明ROE是虚的
        if roe_series is not None and len(roe_series) >= 1:
            latest_roe = roe_series.iloc[0]
            latest_fcf = fcf.iloc[0]
            if latest_roe > 15 and latest_fcf < 0:
                problems.append(f"ROE={latest_roe:.1f}%但现金流为负（虚假ROE）")

    is_healthy = len(problems) == 0
    return is_healthy, problems if problems else healthy


def check_decline_signals(stock_list, quotes_df):
    """
    对关注表/持仓中近期下跌的股票进行真假下跌判断
    返回：假跌买入机会 + 真跌卖出警告
    """
    if quotes_df is None or quotes_df.empty:
        return [], []

    false_declines = []  # 假跌（买入机会）
    true_declines = []   # 真跌（卖出警告）

    for stock in stock_list:
        code = stock["code"]
        name = stock.get("name", code)
        category = stock.get("category", "")

        row = quotes_df[quotes_df["代码"] == code]
        if row.empty:
            continue
        row = row.iloc[0]

        # 检查涨跌幅（当日+近期）
        change_pct = pd.to_numeric(row.get("涨跌幅"), errors="coerce")
        price = pd.to_numeric(row.get("最新价"), errors="coerce")

        # 只关注下跌超过3%的股票
        if pd.isna(change_pct) or change_pct > -3:
            continue

        print(f"  {name}({code}) 下跌{change_pct:.1f}%，分析真假...")

        # 检查基本面
        is_healthy, details = check_fundamental_health(code)
        if is_healthy is None:
            continue

        # 检查同行业是否也在跌
        industry = ""
        if "所属行业" in quotes_df.columns:
            industry = str(row.get("所属行业", ""))

        industry_also_down = False
        if industry:
            peers = quotes_df[quotes_df.get("所属行业", pd.Series()) == industry]
            if len(peers) > 3:
                peer_changes = pd.to_numeric(peers["涨跌幅"], errors="coerce").dropna()
                avg_peer_change = peer_changes.mean()
                industry_also_down = avg_peer_change < -1

        stock_info = {
            "code": code,
            "name": name,
            "category": category,
            "price": price if not pd.isna(price) else 0,
            "change_pct": change_pct,
            "details": details,
            "industry": industry,
            "industry_also_down": industry_also_down,
        }

        if is_healthy:
            # 基本面健康 + 下跌 = 假跌（买入机会）
            reason = "基本面健康"
            if industry_also_down:
                reason += "，同行业普跌（市场原因）"
            reason += "：" + "、".join(details[:3])
            stock_info["signal"] = "false_decline"
            stock_info["signal_text"] = f"假跌{change_pct:.1f}% {reason}→逢低关注"
            false_declines.append(stock_info)
            print(f"    -> 假跌（买入机会）")
        else:
            # 基本面恶化 + 下跌 = 真跌（卖出警告）
            reason = "基本面恶化"
            if not industry_also_down and industry:
                reason += "，同行未跌（公司自身问题）"
            reason += "：" + "、".join(details[:3])
            stock_info["signal"] = "true_decline"
            stock_info["signal_text"] = f"真跌{change_pct:.1f}% {reason}→建议卖出"
            true_declines.append(stock_info)
            print(f"    -> 真跌（卖出警告）")

        time.sleep(0.5)

    return false_declines, true_declines
