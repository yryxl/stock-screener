"""
筛选引擎 - 芒格价值投资五大硬指标
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
)


def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def check_roe(roe_series, config):
    """检查ROE：近N年年均 > 阈值"""
    min_years = config["screener"]["roe_years"]
    roe_min = config["screener"]["roe_min"]

    if roe_series is None or len(roe_series) < min_years // 2:
        return False, "数据不足"

    avg_roe = roe_series.mean()
    if avg_roe >= roe_min:
        return True, f"ROE均值 {avg_roe:.1f}%"
    return False, f"ROE均值 {avg_roe:.1f}% < {roe_min}%"


def check_debt(debt_info, config):
    """检查负债：资产负债率低、流动比率高"""
    if debt_info is None:
        return False, "数据不足"

    debt_max = config["screener"]["debt_ratio_max"]
    current_min = config["screener"]["current_ratio_min"]

    debt_ratio = debt_info.get("debt_ratio")
    current_ratio = debt_info.get("current_ratio")

    issues = []
    if debt_ratio is not None and not np.isnan(debt_ratio):
        if debt_ratio > debt_max:
            return False, f"负债率 {debt_ratio:.1f}% > {debt_max}%"
    else:
        issues.append("负债率数据缺失")

    if current_ratio is not None and not np.isnan(current_ratio):
        if current_ratio < current_min:
            return False, f"流动比率 {current_ratio:.2f} < {current_min}"
    else:
        issues.append("流动比率数据缺失")

    if issues:
        return False, "；".join(issues)

    detail = f"负债率 {debt_ratio:.1f}%，流动比率 {current_ratio:.2f}"
    return True, detail


def check_fcf(fcf_series, config):
    """检查自由现金流：近N年为正"""
    years = config["screener"]["fcf_positive_years"]

    if fcf_series is None or len(fcf_series) < years // 2:
        return False, "数据不足"

    recent = fcf_series.head(years)
    positive_count = (recent > 0).sum()

    if positive_count == len(recent):
        return True, f"近{len(recent)}年现金流全部为正"
    return False, f"近{len(recent)}年中{len(recent) - positive_count}年现金流为负"


def check_opm(opm_series, config):
    """检查营业利润率：稳定或持续上升"""
    years = config["screener"]["opm_years"]
    allow_decline = config["screener"]["opm_allow_decline"]

    if opm_series is None or len(opm_series) < years // 2:
        return False, "数据不足"

    recent = opm_series.head(years)

    # 检查是否持续下降：用线性回归斜率判断
    # opm_series是从新到旧排列的，反转后从旧到新
    values = recent.values[::-1]
    if len(values) >= 3:
        x = np.arange(len(values))
        slope = np.polyfit(x, values, 1)[0]

        if slope < -0.5 and not allow_decline:
            return False, f"营业利润率持续下降（年均降{abs(slope):.1f}个百分点）"

    avg = recent.mean()
    std = recent.std()
    return True, f"营业利润率均值 {avg:.1f}%，波动 {std:.1f}个百分点"


def check_valuation(code, quotes_df, config):
    """检查估值：股息率 > 6%"""
    div_min = config["screener"]["dividend_yield_min"]

    if quotes_df is None or quotes_df.empty:
        return False, "行情数据不可用", {}

    row = quotes_df[quotes_df["代码"] == code]
    if row.empty:
        return False, "未找到行情数据", {}

    row = row.iloc[0]
    pe = pd.to_numeric(row.get("市盈率-动态"), errors="coerce")
    div_yield = pd.to_numeric(row.get("股息率"), errors="coerce")
    price = pd.to_numeric(row.get("最新价"), errors="coerce")

    val_info = {"pe": pe, "dividend_yield": div_yield, "price": price}

    if pd.isna(div_yield):
        return False, "股息率数据缺失", val_info

    if div_yield >= div_min:
        detail = f"股息率 {div_yield:.2f}%"
        if not pd.isna(pe):
            detail += f"，PE {pe:.1f}"
        return True, detail, val_info

    return False, f"股息率 {div_yield:.2f}% < {div_min}%", val_info


def screen_single_stock(code, config, quotes_df):
    """对单只股票进行五大指标筛选"""
    result = {
        "code": code,
        "passed": False,
        "checks": {},
        "valuation": {},
    }

    # 获取财务指标
    df_indicator = get_financial_indicator(code)
    if df_indicator is None:
        result["checks"]["error"] = "无法获取财务数据"
        return result

    df_annual = extract_annual_data(df_indicator, years=max(
        config["screener"]["roe_years"],
        config["screener"]["opm_years"],
    ))

    if df_annual.empty:
        result["checks"]["error"] = "无年报数据"
        return result

    # 1. ROE
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

    # 5. 估值（股息率）
    val_pass, val_detail, val_info = check_valuation(code, quotes_df, config)
    result["checks"]["valuation"] = {"passed": val_pass, "detail": val_detail}
    result["valuation"] = val_info
    if not val_pass:
        return result

    result["passed"] = True
    return result


def screen_all_stocks(config):
    """扫描全A股，返回通过筛选的股票列表"""
    print("正在获取A股列表...")
    stocks = get_all_stocks()
    if stocks.empty:
        print("获取股票列表失败")
        return []

    print(f"共 {len(stocks)} 只股票待筛选")

    # 先获取实时行情（批量，速度快）
    print("正在获取实时行情...")
    quotes_df = get_realtime_quotes()

    # 第一轮快速筛选：用实时行情过滤股息率
    div_min = config["screener"]["dividend_yield_min"]
    if quotes_df is not None and not quotes_df.empty and "股息率" in quotes_df.columns:
        quotes_df["股息率_num"] = pd.to_numeric(quotes_df["股息率"], errors="coerce")
        candidates = quotes_df[quotes_df["股息率_num"] >= div_min]
        candidate_codes = set(candidates["代码"].tolist())
        print(f"股息率 >= {div_min}% 的有 {len(candidate_codes)} 只，进入深度筛选")
    else:
        candidate_codes = set(stocks["code"].tolist())
        print("无法预筛股息率，将逐一分析全部股票")

    # 第二轮深度筛选
    passed = []
    total = len(candidate_codes)
    for i, code in enumerate(candidate_codes, 1):
        if i % 20 == 0:
            print(f"进度: {i}/{total}")

        result = screen_single_stock(code, config, quotes_df)
        if result["passed"]:
            # 补充股票名称
            name_row = stocks[stocks["code"] == code]
            result["name"] = name_row.iloc[0]["name"] if not name_row.empty else code
            passed.append(result)
            print(f"  ✓ {result['name']}({code}) 通过筛选")

        # 控制请求频率，避免被封
        time.sleep(0.5)

    print(f"\n筛选完成，共 {len(passed)} 只股票通过")
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
            declining = all(recent_opm[i] < recent_opm[i + 1] for i in range(len(recent_opm) - 1))
            if declining:
                warnings.append(f"营业利润率连续{sell_rules['opm_decline_years']}年下降")

        # 3. 股息率过低
        if quotes_df is not None and not quotes_df.empty:
            row = quotes_df[quotes_df["代码"] == code]
            if not row.empty:
                div_yield = pd.to_numeric(row.iloc[0].get("股息率"), errors="coerce")
                if not pd.isna(div_yield) and div_yield < sell_rules["dividend_yield_low"]:
                    warnings.append(f"股息率降至{div_yield:.2f}%（警戒线{sell_rules['dividend_yield_low']}%）")

                current_price = pd.to_numeric(row.iloc[0].get("最新价"), errors="coerce")
            else:
                current_price = None

        # 4. 负债率恶化
        debt_info = get_debt_info(df_annual)
        if debt_info and debt_info.get("debt_ratio"):
            if debt_info["debt_ratio"] > sell_rules["debt_ratio_danger"]:
                warnings.append(f"负债率升至{debt_info['debt_ratio']:.1f}%（危险线{sell_rules['debt_ratio_danger']}%）")

        if warnings:
            # 根据恶化指标数量决定卖出比例
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

            sell_shares = int(shares * sell_ratio // 100) * 100  # 取整到100股
            if sell_shares < 100:
                sell_shares = shares  # 不足100股就全卖

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

        time.sleep(0.5)

    return sell_signals
