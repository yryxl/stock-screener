"""
打分模块 - 仅用于展示和排序（不决定买卖信号）
买卖信号由清单筛选+PE位置决定

5个维度各10分，满分50分
用于：通过清单筛选的股票之间比较"先买哪只"
"""

import numpy as np
import pandas as pd
from data_fetcher import (
    extract_annual_data, get_roe_series, get_debt_info,
    get_opm_series, get_fcf_series, find_column,
)
from screener import match_industry_pe


def score_roe_quality(df_annual):
    """维度1：ROE质量（10分）"""
    roe = get_roe_series(df_annual)
    if roe is None or len(roe) < 2:
        return 0, "数据不足"

    avg = roe.mean()
    min_roe = roe.min()
    score = 0
    details = []

    # 均值（0-4分）
    if avg >= 25: score += 4; details.append(f"均值{avg:.1f}%卓越")
    elif avg >= 20: score += 3; details.append(f"均值{avg:.1f}%优秀")
    elif avg >= 15: score += 2; details.append(f"均值{avg:.1f}%良好")
    elif avg >= 10: score += 1; details.append(f"均值{avg:.1f}%一般")
    else: details.append(f"均值{avg:.1f}%不足")

    # 底线（0-3分）
    if min_roe >= 15: score += 3; details.append("从未破15%")
    elif min_roe >= 10: score += 2; details.append(f"最低{min_roe:.1f}%")
    elif min_roe >= 5: score += 1; details.append(f"最低{min_roe:.1f}%曾破线")
    else: details.append(f"最低{min_roe:.1f}%")

    # 趋势（0-2分）
    if len(roe) >= 3:
        slope = np.polyfit(np.arange(len(roe)), roe.values[::-1], 1)[0]
        if slope > 0.5: score += 2; details.append("上升")
        elif slope > -0.3: score += 1; details.append("稳定")
        else: details.append("下滑")

    # 低杠杆加分（0-1分）
    debt = get_debt_info(df_annual)
    if debt and debt.get("debt_ratio"):
        dr = debt["debt_ratio"]
        if not np.isnan(dr) and dr < 40: score += 1; details.append(f"低杠杆{dr:.0f}%")

    return min(score, 10), " | ".join(details)


def score_financial_health(df_annual):
    """维度2：财务健康（10分）"""
    score = 0
    details = []

    debt = get_debt_info(df_annual)
    if debt and debt.get("debt_ratio"):
        dr = debt["debt_ratio"]
        if not np.isnan(dr):
            if dr < 30: score += 3; details.append(f"负债{dr:.0f}%极低")
            elif dr < 45: score += 2; details.append(f"负债{dr:.0f}%健康")
            elif dr < 55: score += 1; details.append(f"负债{dr:.0f}%偏高")
            else: details.append(f"负债{dr:.0f}%高")

    if debt and debt.get("current_ratio"):
        cr = debt["current_ratio"]
        if not np.isnan(cr):
            if cr >= 2.0: score += 2; details.append(f"流动比率{cr:.1f}")
            elif cr >= 1.0: score += 1; details.append(f"流动比率{cr:.1f}")
            else: details.append(f"流动比率{cr:.1f}偏紧")

    fcf = get_fcf_series(df_annual)
    if fcf is not None and len(fcf) >= 3:
        pct = (fcf.head(5) > 0).mean()
        if pct >= 0.9: score += 3; details.append("现金流优")
        elif pct >= 0.7: score += 2; details.append("现金流良")
        elif pct >= 0.5: score += 1; details.append("现金流一般")
        else: details.append("现金流差")

    opm = get_opm_series(df_annual)
    if opm is not None and len(opm) >= 3:
        slope = np.polyfit(np.arange(len(opm)), opm.values[::-1], 1)[0]
        if slope > 0: score += 2; details.append("利润率上升")
        elif slope > -0.5: score += 1; details.append("利润率稳定")
        else: details.append("利润率下滑")

    return min(score, 10), " | ".join(details)


def score_profitability(df_annual):
    """维度3：盈利能力（10分）"""
    score = 0
    details = []

    gm_col = find_column(df_annual, ["销售毛利率", "毛利率"])
    if gm_col:
        gm = pd.to_numeric(df_annual[gm_col], errors="coerce").dropna()
        if len(gm) >= 1:
            avg_gm = gm.mean()
            if avg_gm >= 60: score += 4; details.append(f"毛利率{avg_gm:.0f}%极强")
            elif avg_gm >= 40: score += 3; details.append(f"毛利率{avg_gm:.0f}%强")
            elif avg_gm >= 25: score += 2; details.append(f"毛利率{avg_gm:.0f}%")
            elif avg_gm >= 15: score += 1; details.append(f"毛利率{avg_gm:.0f}%偏低")
            else: details.append(f"毛利率{avg_gm:.0f}%弱")

    rev_col = find_column(df_annual, ["营业总收入增长率", "主营业务收入增长率"])
    if rev_col:
        rev = pd.to_numeric(df_annual[rev_col], errors="coerce").dropna()
        if len(rev) >= 2:
            avg_rev = rev.head(3).mean()
            if avg_rev >= 15: score += 3; details.append(f"增长{avg_rev:.0f}%高速")
            elif avg_rev >= 5: score += 2; details.append(f"增长{avg_rev:.0f}%稳健")
            elif avg_rev >= 0: score += 1; details.append(f"增长{avg_rev:.0f}%低速")
            else: details.append(f"下滑{avg_rev:.0f}%")

    data_years = len(df_annual)
    if data_years >= 10: score += 3; details.append(f"{data_years}年数据")
    elif data_years >= 7: score += 2
    elif data_years >= 4: score += 1

    return min(score, 10), " | ".join(details)


def score_dividend(df_annual, price=None):
    """维度4：股息率（10分）"""
    score = 0
    details = []
    div_yield = 0

    div_col = find_column(df_annual, ["每股股利", "每股派息"])
    if div_col and price and price > 0:
        divs = pd.to_numeric(df_annual[div_col], errors="coerce").dropna()
        if len(divs) >= 1:
            latest_div = divs.iloc[0]
            div_yield = (latest_div / price) * 100 if latest_div > 0 else 0

            if div_yield >= 6: score += 4; details.append(f"股息率{div_yield:.1f}%极高")
            elif div_yield >= 4: score += 3; details.append(f"股息率{div_yield:.1f}%高")
            elif div_yield >= 2.5: score += 2; details.append(f"股息率{div_yield:.1f}%")
            elif div_yield >= 1: score += 1; details.append(f"股息率{div_yield:.1f}%偏低")
            else: details.append(f"股息率{div_yield:.1f}%极低")

            positive_divs = (divs > 0).sum()
            if positive_divs >= 8: score += 3; details.append(f"连续{positive_divs}年")
            elif positive_divs >= 5: score += 2
            elif positive_divs >= 3: score += 1

            if len(divs) >= 3:
                rd = divs.head(3).values
                if len(rd) >= 2 and rd[-1] > 0:
                    growth = (rd[0] / rd[-1] - 1) * 100
                    if growth > 10: score += 3; details.append("股息增长")
                    elif growth > 0: score += 2; details.append("股息稳定")
                    elif growth > -10: score += 1
                    else: details.append("股息下降")
    else:
        details.append("无股息数据")

    return min(score, 10), " | ".join(details), div_yield


def score_valuation(pe, industry=""):
    """维度5：估值（10分）"""
    if pe is None or np.isnan(pe) or pe <= 0:
        return 0, "PE异常"

    pe_range = match_industry_pe(industry)
    complexity = pe_range.get("complexity", "medium")
    score = 0
    details = []

    if pe <= pe_range["low"]: score += 7; details.append(f"PE={pe:.1f}极低估")
    elif pe <= pe_range["fair_low"]: score += 5; details.append(f"PE={pe:.1f}低估")
    elif pe <= (pe_range["fair_low"] + pe_range["fair_high"]) / 2: score += 4; details.append(f"PE={pe:.1f}合理偏低")
    elif pe <= pe_range["fair_high"]: score += 2; details.append(f"PE={pe:.1f}合理偏高")
    elif pe <= pe_range["high"]: score += 1; details.append(f"PE={pe:.1f}偏高")
    else: details.append(f"PE={pe:.1f}过高")

    if complexity == "simple": score += 3; details.append("简单生意")
    elif complexity == "medium": score += 2; details.append("中等复杂")
    else: score += 1; details.append("复杂生意")

    return min(score, 10), " | ".join(details)


def score_stock_for_display(code, df_annual, pe=None, price=None, industry=""):
    """
    为展示和排序打分（不决定买卖信号）
    返回：总分、各维度分数、股息率
    """
    s1, d1 = score_roe_quality(df_annual)
    s2, d2 = score_financial_health(df_annual)
    s3, d3 = score_profitability(df_annual)
    s4, d4, div_yield = score_dividend(df_annual, price)
    s5, d5 = score_valuation(pe, industry)

    total = s1 + s2 + s3 + s4 + s5

    return {
        "total_score": total,
        "dividend_yield": div_yield,
        "dimensions": {
            "ROE质量": {"score": s1, "max": 10, "detail": d1},
            "财务健康": {"score": s2, "max": 10, "detail": d2},
            "盈利能力": {"score": s3, "max": 10, "detail": d3},
            "股息率": {"score": s4, "max": 10, "detail": d4},
            "估值": {"score": s5, "max": 10, "detail": d5},
        },
    }
