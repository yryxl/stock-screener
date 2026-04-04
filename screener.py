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
    find_column,
)


def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ============================================
# 行业PE估值区间（A股参考）
# ============================================
INDUSTRY_PE = {
    # 成熟低增长行业
    "银行": {"low": 5, "fair_low": 6, "fair_high": 9, "high": 12, "type": "mature"},
    "保险": {"low": 6, "fair_low": 8, "fair_high": 12, "high": 16, "type": "mature"},
    "建筑": {"low": 5, "fair_low": 7, "fair_high": 12, "high": 16, "type": "cycle"},
    "钢铁": {"low": 5, "fair_low": 7, "fair_high": 12, "high": 16, "type": "cycle"},
    "煤炭": {"low": 5, "fair_low": 7, "fair_high": 12, "high": 16, "type": "cycle"},
    "电力": {"low": 8, "fair_low": 10, "fair_high": 18, "high": 22, "type": "utility"},
    "公用事业": {"low": 8, "fair_low": 10, "fair_high": 18, "high": 22, "type": "utility"},
    "交通运输": {"low": 8, "fair_low": 12, "fair_high": 16, "high": 22, "type": "utility"},
    "铁路": {"low": 8, "fair_low": 10, "fair_high": 16, "high": 20, "type": "utility"},
    "高速": {"low": 8, "fair_low": 10, "fair_high": 16, "high": 20, "type": "utility"},

    # 消费/稳健成长
    "白酒": {"low": 15, "fair_low": 20, "fair_high": 30, "high": 40, "type": "consumer"},
    "食品饮料": {"low": 15, "fair_low": 20, "fair_high": 30, "high": 40, "type": "consumer"},
    "调味品": {"low": 18, "fair_low": 22, "fair_high": 35, "high": 45, "type": "consumer"},
    "乳制品": {"low": 12, "fair_low": 15, "fair_high": 25, "high": 30, "type": "consumer"},
    "医药": {"low": 15, "fair_low": 20, "fair_high": 30, "high": 40, "type": "growth"},
    "医疗器械": {"low": 18, "fair_low": 22, "fair_high": 35, "high": 50, "type": "growth"},
    "中药": {"low": 15, "fair_low": 20, "fair_high": 30, "high": 40, "type": "consumer"},
    "家电": {"low": 10, "fair_low": 15, "fair_high": 25, "high": 30, "type": "consumer"},
    "传媒": {"low": 15, "fair_low": 20, "fair_high": 30, "high": 40, "type": "consumer"},

    # 科技/高成长
    "半导体": {"low": 30, "fair_low": 40, "fair_high": 65, "high": 80, "type": "tech"},
    "芯片": {"low": 30, "fair_low": 40, "fair_high": 65, "high": 80, "type": "tech"},
    "软件": {"low": 30, "fair_low": 40, "fair_high": 60, "high": 80, "type": "tech"},
    "通信": {"low": 15, "fair_low": 20, "fair_high": 35, "high": 50, "type": "tech"},
    "军工": {"low": 25, "fair_low": 35, "fair_high": 55, "high": 70, "type": "tech"},
    "航空航天": {"low": 25, "fair_low": 35, "fair_high": 55, "high": 70, "type": "tech"},
    "新能源": {"low": 20, "fair_low": 30, "fair_high": 50, "high": 60, "type": "tech"},
    "锂电": {"low": 20, "fair_low": 30, "fair_high": 50, "high": 60, "type": "tech"},
    "光伏": {"low": 15, "fair_low": 25, "fair_high": 45, "high": 55, "type": "tech"},

    # 化工/资源（周期）
    "化工": {"low": 8, "fair_low": 12, "fair_high": 20, "high": 30, "type": "cycle"},
    "有色金属": {"low": 8, "fair_low": 12, "fair_high": 20, "high": 30, "type": "cycle"},
    "稀土": {"low": 10, "fair_low": 15, "fair_high": 25, "high": 35, "type": "cycle"},
    "矿业": {"low": 8, "fair_low": 10, "fair_high": 18, "high": 25, "type": "cycle"},
    "免税": {"low": 18, "fair_low": 25, "fair_high": 40, "high": 50, "type": "consumer"},
}

# 默认PE区间（找不到行业时用）
DEFAULT_PE = {"low": 10, "fair_low": 15, "fair_high": 25, "high": 35, "type": "default"}


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
    elif current_pe <= pe_range["fair_low"]:
        return "buy_light", f"PE={current_pe:.1f}，低于行业合理区间{pe_range['fair_low']}-{pe_range['fair_high']}{peg_hint}→可以轻仓买入"
    elif current_pe <= pe_range["fair_high"]:
        # 在合理区间内，看是偏低还是偏高
        mid = (pe_range["fair_low"] + pe_range["fair_high"]) / 2
        if current_pe <= mid * 0.9:
            return "buy_watch", f"PE={current_pe:.1f}，合理偏低{peg_hint}→重点关注买入"
        elif current_pe >= mid * 1.1:
            return "sell_watch", f"PE={current_pe:.1f}，合理偏高{peg_hint}→重点关注卖出"
        else:
            return "hold", f"PE={current_pe:.1f}，处于合理区间{pe_range['fair_low']}-{pe_range['fair_high']}{peg_hint}"
    elif current_pe <= pe_range["high"]:
        return "sell_light", f"PE={current_pe:.1f}，高于合理区间{peg_hint}→可以适当卖出"
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

    # 股价和PE
    if quotes_df is not None and not quotes_df.empty:
        row = quotes_df[quotes_df["代码"] == code]
        if not row.empty:
            row = row.iloc[0]
            price = pd.to_numeric(row.get("最新价"), errors="coerce")
            pe = pd.to_numeric(row.get("市盈率-动态"), errors="coerce")
            result["price"] = price
            result["pe"] = pe

            max_price = config["screener"]["max_price_per_share"]
            if not pd.isna(price) and price > max_price:
                return result

            # 尝试获取行业信息
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

    signal_order = {"buy_heavy": 0, "buy_light": 1, "buy_watch": 2, "hold": 3, "sell_watch": 4, "sell_light": 5, "sell_heavy": 6, None: 7}
    passed.sort(key=lambda x: signal_order.get(x.get("signal"), 7))
    print(f"\n候选池: {len(passed)} 只好公司")
    return passed


def check_holdings_sell_signals(holdings, config):
    if not holdings:
        return []
    print("检查持仓信号...")
    quotes_df = get_realtime_quotes()
    signals = []
    for h in holdings:
        code = h["code"]
        name = h.get("name", code)
        if quotes_df is not None and not quotes_df.empty:
            row = quotes_df[quotes_df["代码"] == code]
            if not row.empty:
                row = row.iloc[0]
                price = pd.to_numeric(row.get("最新价"), errors="coerce")
                pe = pd.to_numeric(row.get("市盈率-动态"), errors="coerce")
                industry = str(row.get("所属行业", "")) if "所属行业" in quotes_df.columns else ""
                signal, signal_text = get_pe_signal(pe, industry)
                if signal and signal != "hold":
                    signals.append({
                        "code": code, "name": name,
                        "shares": h.get("shares", 0), "cost": h.get("cost", 0),
                        "price": price if not pd.isna(price) else 0,
                        "pe": pe if not pd.isna(pe) else 0,
                        "signal": signal, "signal_text": signal_text,
                    })
        time.sleep(0.3)
    return signals
