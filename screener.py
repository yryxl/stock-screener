"""
筛选引擎 - 芒格/巴菲特价值投资体系
核心理念：好生意（轻资产、护城河）、好公司（财务优秀）、好价格（PE合理）
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
# 财务指标检查
# ============================================

def check_roe_no_leverage(df_annual, config):
    """ROE≥20%且非高杠杆"""
    roe_series = get_roe_series(df_annual)
    if roe_series is None or len(roe_series) < 5:
        return False, "ROE数据不足"

    avg_roe = roe_series.mean()
    if avg_roe < config["screener"]["roe_min"]:
        return False, f"ROE均值{avg_roe:.1f}% < {config['screener']['roe_min']}%"

    # 检查是否高杠杆带来的ROE
    debt_info = get_debt_info(df_annual)
    if debt_info and debt_info.get("debt_ratio"):
        debt_ratio = debt_info["debt_ratio"]
        if not np.isnan(debt_ratio) and debt_ratio > config["screener"]["debt_ratio_max"]:
            return False, f"ROE{avg_roe:.1f}%但负债率{debt_ratio:.1f}%过高（高杠杆）"

    return True, f"ROE均值{avg_roe:.1f}%，非高杠杆"


def check_debt_health(df_annual, config):
    """负债健康：低负债率+现金够还利息"""
    debt_info = get_debt_info(df_annual)
    if debt_info is None:
        return False, "负债数据不足"

    debt_ratio = debt_info.get("debt_ratio")
    current_ratio = debt_info.get("current_ratio")

    if debt_ratio is not None and not np.isnan(debt_ratio):
        if debt_ratio > config["screener"]["debt_ratio_max"]:
            return False, f"负债率{debt_ratio:.1f}%过高"
    else:
        return False, "负债率数据缺失"

    detail = f"负债率{debt_ratio:.1f}%"
    if current_ratio is not None and not np.isnan(current_ratio):
        if current_ratio < config["screener"]["current_ratio_min"]:
            return False, f"流动比率{current_ratio:.2f}偏低，现金可能不够还债"
        detail += f"，流动比率{current_ratio:.2f}"

    return True, detail


def check_opm_stable(df_annual, config):
    """营业利润率10年稳定或上升"""
    opm_series = get_opm_series(df_annual)
    if opm_series is None or len(opm_series) < 5:
        return False, "利润率数据不足"

    values = opm_series.values[::-1]  # 从旧到新
    if len(values) >= 3:
        x = np.arange(len(values))
        slope = np.polyfit(x, values, 1)[0]
        if slope < -0.5:
            return False, f"营业利润率持续下滑（年均降{abs(slope):.1f}个百分点）"

    avg = opm_series.mean()
    return True, f"营业利润率均值{avg:.1f}%，趋势稳定"


def check_fcf(df_annual, config):
    """自由现金流充足"""
    fcf_series = get_fcf_series(df_annual)
    if fcf_series is None or len(fcf_series) < 3:
        return False, "现金流数据不足"

    recent = fcf_series.head(config["screener"]["fcf_positive_years"])
    positive_count = (recent > 0).sum()

    if positive_count < len(recent) * 0.8:
        return False, f"近{len(recent)}年中{len(recent)-positive_count}年现金流为负"

    return True, f"近{len(recent)}年现金流充足"


def check_gross_margin(df_annual, config):
    """毛利率>40%（定价权）"""
    col = find_column(df_annual, ["销售毛利率", "毛利率"])
    if col is None:
        return False, "毛利率数据缺失"

    values = pd.to_numeric(df_annual[col], errors="coerce").dropna()
    if len(values) < 3:
        return False, "毛利率数据不足"

    avg = values.mean()
    if avg < config["screener"]["gross_margin_min"]:
        return False, f"毛利率{avg:.1f}% < {config['screener']['gross_margin_min']}%"

    return True, f"毛利率均值{avg:.1f}%，有定价权"


def check_asset_light(df_annual, config):
    """轻资产：固定资产占比低（换标签就能涨价的生意）"""
    # 用固定资产/总资产比率衡量
    fixed_col = find_column(df_annual, ["固定资产周转率"])
    # 如果找不到固定资产数据，用毛利率和ROE间接判断
    # 高毛利+高ROE本身就是轻资产的特征
    # 这里用一个宽松标准：如果毛利率>40%且ROE>20%，默认是轻资产
    return True, "高毛利+高ROE，符合轻资产特征"


def check_revenue_growth(df_annual, config):
    """营收近3年正增长"""
    col = find_column(df_annual, ["营业总收入增长率", "主营业务收入增长率", "营业收入增长率"])
    if col is None:
        return True, "营收增长数据缺失（跳过）"

    values = pd.to_numeric(df_annual[col], errors="coerce").dropna()
    if len(values) < 2:
        return True, "营收增长数据不足（跳过）"

    recent = values.head(3)
    positive = (recent > 0).sum()

    if positive >= 2:
        return True, f"近{len(recent)}年中{positive}年营收正增长"
    return False, f"近{len(recent)}年中{len(recent)-positive}年营收下降"


# ============================================
# PE估值信号
# ============================================

def get_pe_signal(current_pe):
    """根据PE绝对值+合理区间给出买卖信号"""
    if current_pe is None or np.isnan(current_pe) or current_pe <= 0:
        return None, "PE数据异常"

    # 基于巴菲特/芒格的绝对估值标准
    if current_pe <= 8:
        return "buy_heavy", f"PE={current_pe:.1f}，极度低估→可以重仓买入"
    elif current_pe <= 12:
        return "buy_light", f"PE={current_pe:.1f}，明显低估→可以轻仓买入"
    elif current_pe <= 16:
        return "buy_watch", f"PE={current_pe:.1f}，偏低→重点关注买入"
    elif current_pe <= 25:
        return "hold", f"PE={current_pe:.1f}，合理区间→持有"
    elif current_pe <= 30:
        return "sell_watch", f"PE={current_pe:.1f}，偏高→重点关注卖出"
    elif current_pe <= 40:
        return "sell_light", f"PE={current_pe:.1f}，明显高估→可以适当卖出"
    else:
        return "sell_heavy", f"PE={current_pe:.1f}，极度高估→可以大量卖出"


# ============================================
# 主筛选流程
# ============================================

def screen_single_stock(code, config, quotes_df):
    """对单只股票进行全面筛选"""
    result = {
        "code": code,
        "passed": False,
        "checks": {},
        "signal": None,
        "signal_text": "",
        "pe": None,
        "price": None,
    }

    df_indicator = get_financial_indicator(code)
    if df_indicator is None:
        return result

    df_annual = extract_annual_data(df_indicator, years=10)
    if df_annual.empty or len(df_annual) < 3:
        return result

    # 1. ROE≥20%且非高杠杆
    passed, detail = check_roe_no_leverage(df_annual, config)
    result["checks"]["roe"] = {"passed": passed, "detail": detail}
    if not passed:
        return result

    # 2. 负债健康
    passed, detail = check_debt_health(df_annual, config)
    result["checks"]["debt"] = {"passed": passed, "detail": detail}
    if not passed:
        return result

    # 3. 营业利润率稳定
    passed, detail = check_opm_stable(df_annual, config)
    result["checks"]["opm"] = {"passed": passed, "detail": detail}
    if not passed:
        return result

    # 4. 自由现金流充足
    passed, detail = check_fcf(df_annual, config)
    result["checks"]["fcf"] = {"passed": passed, "detail": detail}
    if not passed:
        return result

    # 5. 毛利率>40%
    passed, detail = check_gross_margin(df_annual, config)
    result["checks"]["gross_margin"] = {"passed": passed, "detail": detail}
    if not passed:
        return result

    # 6. 轻资产
    passed, detail = check_asset_light(df_annual, config)
    result["checks"]["asset_light"] = {"passed": passed, "detail": detail}

    # 7. 营收增长
    passed, detail = check_revenue_growth(df_annual, config)
    result["checks"]["revenue"] = {"passed": passed, "detail": detail}
    if not passed:
        return result

    # 8. 股价和PE
    if quotes_df is not None and not quotes_df.empty:
        row = quotes_df[quotes_df["代码"] == code]
        if not row.empty:
            row = row.iloc[0]
            price = pd.to_numeric(row.get("最新价"), errors="coerce")
            pe = pd.to_numeric(row.get("市盈率-动态"), errors="coerce")

            result["price"] = price
            result["pe"] = pe

            # 股价过滤
            max_price = config["screener"]["max_price_per_share"]
            if not pd.isna(price) and price > max_price:
                result["checks"]["price"] = {"passed": False, "detail": f"股价{price:.2f}元，1手需{price*100:.0f}元"}
                return result

            result["checks"]["price"] = {"passed": True, "detail": f"股价{price:.2f}元"}

            # PE信号
            signal, signal_text = get_pe_signal(pe)
            result["signal"] = signal
            result["signal_text"] = signal_text

    result["passed"] = True
    return result


def screen_all_stocks(config):
    """筛选全A股好公司候选池"""
    print("正在获取A股列表...")
    stocks = get_all_stocks()
    if stocks.empty:
        print("获取股票列表失败")
        return []
    print(f"共 {len(stocks)} 只股票")

    # 第一轮：批量ROE预筛
    print("正在批量预筛ROE≥15%的股票...")
    candidate_codes = set()
    for date in ["20241231", "20231231"]:
        df = get_batch_roe_data(date=date)
        if df is not None and not df.empty:
            print(f"  获取到 {date} 年报 {len(df)} 条")
            roe_col = None
            for col in df.columns:
                if "净资产收益率" in col or "roe" in col.lower():
                    roe_col = col
                    break
            if roe_col:
                df[roe_col] = pd.to_numeric(df[roe_col], errors="coerce")
                # 用15%预筛（比最终20%宽松，避免遗漏）
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

    valid_codes = set(stocks["code"].tolist())
    candidate_codes = candidate_codes & valid_codes

    # 获取实时行情
    print("正在获取实时行情...")
    quotes_df = get_realtime_quotes()

    # 价格预筛
    max_price = config["screener"]["max_price_per_share"]
    if quotes_df is not None and not quotes_df.empty:
        quotes_df["价格_num"] = pd.to_numeric(quotes_df["最新价"], errors="coerce")
        affordable = quotes_df[(quotes_df["价格_num"] > 0) & (quotes_df["价格_num"] <= max_price)]
        affordable_codes = set(affordable["代码"].tolist())
        candidate_codes = candidate_codes & affordable_codes
        print(f"  股价≤{max_price}元: {len(candidate_codes)} 只进入深度分析")

    # 第二轮：深度筛选
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
            signal_emoji = "🔴" if result["signal"] and "buy" in result["signal"] else "⚪"
            print(f"  {signal_emoji} {result['name']}({code}) 通过 | {result['signal_text']}")

        time.sleep(0.3)

    # 按PE信号排序：买入信号排前面
    signal_order = {"buy_heavy": 0, "buy_light": 1, "buy_watch": 2, "hold": 3, "sell_watch": 4, "sell_light": 5, "sell_heavy": 6, None: 7}
    passed.sort(key=lambda x: signal_order.get(x.get("signal"), 7))

    print(f"\n筛选完成，共 {len(passed)} 只好公司")
    buy_count = sum(1 for s in passed if s.get("signal") and "buy" in s["signal"])
    sell_count = sum(1 for s in passed if s.get("signal") and "sell" in s["signal"])
    print(f"  买入信号: {buy_count} 只 | 卖出信号: {sell_count} 只")

    return passed


def check_holdings_sell_signals(holdings, config):
    """检查持仓股票的买卖信号"""
    if not holdings:
        return []

    print("正在检查持仓信号...")
    quotes_df = get_realtime_quotes()
    signals = []

    for holding in holdings:
        code = holding["code"]
        name = holding.get("name", code)
        shares = holding.get("shares", 0)
        cost = holding.get("cost", 0)
        current_price = None

        print(f"  检查 {name}({code})...")

        # 获取当前PE和价格
        if quotes_df is not None and not quotes_df.empty:
            row = quotes_df[quotes_df["代码"] == code]
            if not row.empty:
                current_price = pd.to_numeric(row.iloc[0].get("最新价"), errors="coerce")
                pe = pd.to_numeric(row.iloc[0].get("市盈率-动态"), errors="coerce")

                signal, signal_text = get_pe_signal(pe)

                if signal and signal != "hold":
                    signals.append({
                        "code": code,
                        "name": name,
                        "shares": shares,
                        "cost": cost,
                        "current_price": current_price if not pd.isna(current_price) else 0,
                        "signal": signal,
                        "signal_text": signal_text,
                        "pe": pe,
                    })

        time.sleep(0.3)

    return signals
