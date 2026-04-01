"""
筛选引擎 - 芒格价值投资五大硬指标
优化版：先用批量数据快速预筛，再对少量候选深度分析
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
# 五大指标检查函数
# ============================================

def check_roe(roe_series, config):
    min_years = config["screener"]["roe_years"]
    roe_min = config["screener"]["roe_min"]
    if roe_series is None or len(roe_series) < min_years // 2:
        return False, "数据不足"
    avg_roe = roe_series.mean()
    if avg_roe >= roe_min:
        return True, f"ROE均值 {avg_roe:.1f}%"
    return False, f"ROE均值 {avg_roe:.1f}% < {roe_min}%"


def check_debt(debt_info, config):
    if debt_info is None:
        return False, "数据不足"
    debt_max = config["screener"]["debt_ratio_max"]
    current_min = config["screener"]["current_ratio_min"]
    debt_ratio = debt_info.get("debt_ratio")
    current_ratio = debt_info.get("current_ratio")

    if debt_ratio is not None and not np.isnan(debt_ratio):
        if debt_ratio > debt_max:
            return False, f"负债率 {debt_ratio:.1f}% > {debt_max}%"
    else:
        return False, "负债率数据缺失"

    if current_ratio is not None and not np.isnan(current_ratio):
        if current_ratio < current_min:
            return False, f"流动比率 {current_ratio:.2f} < {current_min}"
    else:
        return False, "流动比率数据缺失"

    return True, f"负债率 {debt_ratio:.1f}%，流动比率 {current_ratio:.2f}"


def check_fcf(fcf_series, config):
    years = config["screener"]["fcf_positive_years"]
    if fcf_series is None or len(fcf_series) < years // 2:
        return False, "数据不足"
    recent = fcf_series.head(years)
    positive_count = (recent > 0).sum()
    if positive_count == len(recent):
        return True, f"近{len(recent)}年现金流全部为正"
    return False, f"近{len(recent)}年中{len(recent) - positive_count}年现金流为负"


def check_opm(opm_series, config):
    years = config["screener"]["opm_years"]
    allow_decline = config["screener"]["opm_allow_decline"]
    if opm_series is None or len(opm_series) < years // 2:
        return False, "数据不足"
    recent = opm_series.head(years)
    values = recent.values[::-1]
    if len(values) >= 3:
        x = np.arange(len(values))
        slope = np.polyfit(x, values, 1)[0]
        if slope < -0.5 and not allow_decline:
            return False, f"营业利润率持续下降（年均降{abs(slope):.1f}个百分点）"
    avg = recent.mean()
    std = recent.std()
    return True, f"营业利润率均值 {avg:.1f}%，波动 {std:.1f}个百分点"


# ============================================
# 批量预筛 + 深度分析
# ============================================

def batch_prefilter(config):
    """
    第一轮：用批量数据快速预筛
    用最新年报的ROE过滤，大幅缩小候选范围
    """
    roe_min = config["screener"]["roe_min"]

    print("正在批量获取业绩数据（用于ROE预筛）...")

    # 尝试多个年报日期
    candidates = set()
    for date in ["20241231", "20231231"]:
        df = get_batch_roe_data(date=date)
        if df is not None and not df.empty:
            print(f"  获取到 {date} 年报数据 {len(df)} 条")
            # 打印列名帮助调试
            print(f"  列名: {list(df.columns)}")

            # 找ROE列
            roe_col = None
            for col in df.columns:
                if "净资产收益率" in col or "roe" in col.lower():
                    roe_col = col
                    break

            if roe_col:
                df[roe_col] = pd.to_numeric(df[roe_col], errors="coerce")
                filtered = df[df[roe_col] >= roe_min]

                # 找股票代码列
                code_col = None
                for col in df.columns:
                    if "代码" in col or "股票代码" in col or col == "code":
                        code_col = col
                        break

                if code_col:
                    new_codes = set(filtered[code_col].astype(str).tolist())
                    candidates.update(new_codes)
                    print(f"  ROE >= {roe_min}% 的有 {len(new_codes)} 只")

            break  # 只要获取到一个年份的就够了

    return candidates


def screen_single_stock(code, config, quotes_df):
    """对单只股票进行五大指标深度筛选"""
    result = {
        "code": code,
        "passed": False,
        "checks": {},
        "valuation": {},
    }

    df_indicator = get_financial_indicator(code)
    if df_indicator is None:
        return result

    df_annual = extract_annual_data(df_indicator, years=max(
        config["screener"]["roe_years"],
        config["screener"]["opm_years"],
    ))

    if df_annual.empty:
        return result

    # 1. ROE（深度检查：看10年均值，不只是最新一年）
    roe_series = get_roe_series(df_annual)
    roe_pass, roe_detail = check_roe(roe_series, config)
    result["checks"]["roe"] = {"passed": roe_pass, "detail": roe_detail}
    if not roe_pass:
        return result

    # 2. 负债
    debt_info = get_debt_info(df_annual)
    debt_pass, debt_detail = check_debt(debt_info, config)
    result["checks"]["debt"] = {"passed": debt_pass, "detail": debt_detail}
    if not debt_pass:
        return result

    # 3. 自由现金流
    fcf_series = get_fcf_series(df_annual)
    fcf_pass, fcf_detail = check_fcf(fcf_series, config)
    result["checks"]["fcf"] = {"passed": fcf_pass, "detail": fcf_detail}
    if not fcf_pass:
        return result

    # 4. 营业利润率
    opm_series = get_opm_series(df_annual)
    opm_pass, opm_detail = check_opm(opm_series, config)
    result["checks"]["opm"] = {"passed": opm_pass, "detail": opm_detail}
    if not opm_pass:
        return result

    # 5. 估值（股息率）— 从实时行情获取
    div_min = config["screener"]["dividend_yield_min"]
    if quotes_df is not None and not quotes_df.empty:
        row = quotes_df[quotes_df["代码"] == code]
        if not row.empty:
            row = row.iloc[0]
            pe = pd.to_numeric(row.get("市盈率-动态"), errors="coerce")
            price = pd.to_numeric(row.get("最新价"), errors="coerce")

            # 尝试多种可能的股息率列名
            div_yield = None
            for col_name in ["股息率", "股息率(%)", "dividend_yield"]:
                if col_name in quotes_df.columns:
                    div_yield = pd.to_numeric(row.get(col_name), errors="coerce")
                    if not pd.isna(div_yield):
                        break

            result["valuation"] = {"pe": pe, "dividend_yield": div_yield, "price": price}

            # 如果实时行情没有股息率，尝试从财务指标中获取
            if div_yield is None or pd.isna(div_yield):
                # 用每股股利/股价估算
                div_col = find_column(df_annual, ["每股股利", "每股派息"])
                if div_col is not None and not pd.isna(price) and price > 0:
                    latest_div = pd.to_numeric(df_annual.iloc[0].get(div_col), errors="coerce")
                    if not pd.isna(latest_div):
                        div_yield = (latest_div / price) * 100
                        result["valuation"]["dividend_yield"] = div_yield

            if div_yield is not None and not pd.isna(div_yield) and div_yield >= div_min:
                detail = f"股息率 {div_yield:.2f}%"
                if not pd.isna(pe):
                    detail += f"，PE {pe:.1f}"
                result["checks"]["valuation"] = {"passed": True, "detail": detail}
                result["passed"] = True
                return result
            else:
                div_str = f"{div_yield:.2f}%" if (div_yield is not None and not pd.isna(div_yield)) else "未知"
                result["checks"]["valuation"] = {
                    "passed": False,
                    "detail": f"股息率 {div_str} < {div_min}%"
                }
                return result

    result["checks"]["valuation"] = {"passed": False, "detail": "无行情数据"}
    return result


def screen_all_stocks(config):
    """
    扫描全A股，返回通过筛选的股票列表
    优化策略：先批量预筛ROE，再逐个深度分析
    """
    print("正在获取A股列表...")
    stocks = get_all_stocks()
    if stocks.empty:
        print("获取股票列表失败")
        return []
    print(f"共 {len(stocks)} 只股票")

    # 第一轮：批量ROE预筛（几秒搞定）
    candidate_codes = batch_prefilter(config)

    if not candidate_codes:
        print("批量预筛未找到候选股，将使用全量扫描（可能较慢）")
        candidate_codes = set(stocks["code"].tolist())
    else:
        # 只保留在A股列表中的（过滤ST等）
        valid_codes = set(stocks["code"].tolist())
        candidate_codes = candidate_codes & valid_codes
        print(f"预筛后 {len(candidate_codes)} 只候选股进入深度分析")

    # 获取实时行情
    print("正在获取实时行情...")
    quotes_df = get_realtime_quotes()

    # 第二轮：逐个深度分析
    passed = []
    total = len(candidate_codes)
    for i, code in enumerate(candidate_codes, 1):
        if i % 10 == 0:
            print(f"深度分析进度: {i}/{total}")

        result = screen_single_stock(code, config, quotes_df)
        if result["passed"]:
            name_row = stocks[stocks["code"] == code]
            result["name"] = name_row.iloc[0]["name"] if not name_row.empty else code
            passed.append(result)
            print(f"  ✓ {result['name']}({code}) 通过全部5项筛选")

        time.sleep(0.3)

    print(f"\n筛选完成，共 {len(passed)} 只股票通过全部指标")
    return passed


def check_holdings_sell_signals(holdings, config):
    """检查持仓股票是否需要卖出"""
    if not holdings:
        return []

    print("正在检查持仓卖出信号...")
    quotes_df = get_realtime_quotes()
    sell_signals = []
    sell_rules = config["sell_rules"]

    for holding in holdings:
        code = holding["code"]
        name = holding.get("name", code)
        shares = holding.get("shares", 0)
        cost = holding.get("cost", 0)
        current_price = None

        print(f"  检查 {name}({code})...")

        df_indicator = get_financial_indicator(code)
        if df_indicator is None:
            continue

        df_annual = extract_annual_data(df_indicator, years=10)
        if df_annual.empty:
            continue

        warnings = []

        # 1. ROE跌破警戒线
        roe_series = get_roe_series(df_annual)
        if roe_series is not None and len(roe_series) > 0:
            latest_roe = roe_series.iloc[0]
            if latest_roe < sell_rules["roe_warning"]:
                warnings.append(f"ROE降至{latest_roe:.1f}%（警戒线{sell_rules['roe_warning']}%）")

        # 2. 营业利润率连续下降
        opm_series = get_opm_series(df_annual)
        if opm_series is not None and len(opm_series) >= sell_rules["opm_decline_years"]:
            recent_opm = opm_series.head(sell_rules["opm_decline_years"]).values
            declining = all(recent_opm[j] < recent_opm[j + 1] for j in range(len(recent_opm) - 1))
            if declining:
                warnings.append(f"营业利润率连续{sell_rules['opm_decline_years']}年下降")

        # 3. 股息率过低 & 获取当前价格
        if quotes_df is not None and not quotes_df.empty:
            row = quotes_df[quotes_df["代码"] == code]
            if not row.empty:
                current_price = pd.to_numeric(row.iloc[0].get("最新价"), errors="coerce")

                for col_name in ["股息率", "股息率(%)"]:
                    if col_name in quotes_df.columns:
                        div_yield = pd.to_numeric(row.iloc[0].get(col_name), errors="coerce")
                        if not pd.isna(div_yield) and div_yield < sell_rules["dividend_yield_low"]:
                            warnings.append(f"股息率降至{div_yield:.2f}%（警戒线{sell_rules['dividend_yield_low']}%）")
                        break

        # 4. 负债率恶化
        debt_info = get_debt_info(df_annual)
        if debt_info and debt_info.get("debt_ratio"):
            if debt_info["debt_ratio"] > sell_rules["debt_ratio_danger"]:
                warnings.append(f"负债率升至{debt_info['debt_ratio']:.1f}%（危险线{sell_rules['debt_ratio_danger']}%）")

        if warnings:
            n = len(warnings)
            if n >= 3:
                sell_ratio = 1.0
                action = "建议清仓"
            elif n == 2:
                sell_ratio = 2 / 3
                action = "建议减仓2/3"
            else:
                sell_ratio = 1 / 3
                action = "建议减仓1/3"

            sell_shares = int(shares * sell_ratio // 100) * 100
            if sell_shares < 100:
                sell_shares = shares

            sell_signals.append({
                "code": code,
                "name": name,
                "shares": shares,
                "cost": cost,
                "current_price": current_price if current_price else 0,
                "sell_shares": sell_shares,
                "action": action,
                "warnings": warnings,
            })

        time.sleep(0.3)

    return sell_signals
