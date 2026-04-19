"""
H3：换仓建议数值化分析（2026-04-19 用户提出）

输入：sell 股 + buy 股的关键数据（PE / ROE / 股息率 / 当前价）
输出：
  - PE 估值差（卖股高估 X%，买股低估 Y%）
  - 股息率差（年化）
  - ROE 差（年化复利能力）
  - 综合预期年化收益差（粗略）
  - 回收期估算（换仓手续费 / 年化收益差）

设计哲学：
- 巴菲特原则：换仓必须有"非常明确的优势"，不要为换而换
- 故意算保守（用悲观假设），让用户更倾向于"不换"而非"激进换仓"
- 不预测股价，只算"如果两只股都回归合理 PE"的相对优势

工作量约束：
- 不要做 DCF / 蒙特卡洛 / 复杂建模——5 分钟能解释清楚的简单算法
- 数据来源：sell/buy 股已有的 daily_results.json 字段
"""
from typing import Optional, Dict


# 假设：换仓总手续费（卖+买）大约 0.15%（含印花税 0.1% + 佣金 0.025% × 2 + 滑点）
DEFAULT_SWAP_COST_PCT = 0.15

# 估值回归假设（保守）：高估的 PE 1 年内回归 30%（不全部回归，避免过度乐观）
PE_REGRESSION_RATE_PER_YEAR = 0.3


def estimate_swap_metrics(sell_data: Dict, buy_data: Dict,
                            swap_cost_pct: float = DEFAULT_SWAP_COST_PCT) -> Dict:
    """估算换仓的核心指标

    Args:
      sell_data: 卖股数据（dict），需含至少：
        - pe / pe_ttm: 当前 PE
        - roe: ROE %
        - dividend_yield: 股息率 %（可选）
        - pe_fair_low / pe_fair_high: 行业合理 PE 区间（可选）
      buy_data: 买股数据，同上结构
      swap_cost_pct: 换仓手续费（默认 0.15%）

    Returns: {
      'pe_diff_pct': 卖股 PE 高于合理上限的百分比（正=高估，负=低估）
      'buy_pe_diff_pct': 买股 PE 高于合理上限的百分比
      'roe_diff_pp': buy ROE - sell ROE（百分点）
      'div_diff_pct': buy 股息 - sell 股息（百分点）
      'expected_annual_return_diff_pct': 粗略年化收益差（%）
      'payback_years': 换仓回收期（年；None = 无收益差或负收益）
      'recommendation': 'strong' / 'medium' / 'weak' / 'avoid'
      'reasons': [文字解释]
    }
    """
    reasons = []

    # 1. PE 估值差
    sell_pe = _safe_float(sell_data.get('pe') or sell_data.get('pe_ttm'))
    buy_pe = _safe_float(buy_data.get('pe') or buy_data.get('pe_ttm'))
    sell_fair_high = _safe_float(sell_data.get('pe_fair_high'))
    buy_fair_high = _safe_float(buy_data.get('pe_fair_high'))

    pe_diff_pct = None
    buy_pe_diff_pct = None
    if sell_pe and sell_fair_high:
        pe_diff_pct = (sell_pe - sell_fair_high) / sell_fair_high * 100
        if pe_diff_pct > 30:
            reasons.append(f"卖股 PE {sell_pe:.1f} 高于合理上限 {sell_fair_high:.0f} 达 {pe_diff_pct:.0f}%")
        elif pe_diff_pct > 10:
            reasons.append(f"卖股 PE {sell_pe:.1f} 略高于合理价（高 {pe_diff_pct:.0f}%）")
    if buy_pe and buy_fair_high:
        buy_pe_diff_pct = (buy_pe - buy_fair_high) / buy_fair_high * 100
        if buy_pe_diff_pct < -20:
            reasons.append(f"买股 PE {buy_pe:.1f} 低于合理上限 {buy_fair_high:.0f} 达 {abs(buy_pe_diff_pct):.0f}%")

    # 2. ROE 差（百分点）
    sell_roe = _safe_float(sell_data.get('roe'))
    buy_roe = _safe_float(buy_data.get('roe'))
    roe_diff_pp = None
    if sell_roe is not None and buy_roe is not None:
        roe_diff_pp = buy_roe - sell_roe
        if roe_diff_pp > 5:
            reasons.append(f"买股 ROE {buy_roe:.0f}% 高于卖股 {sell_roe:.0f}% (+{roe_diff_pp:.0f}pp)")
        elif roe_diff_pp < -5:
            reasons.append(f"⚠ 买股 ROE {buy_roe:.0f}% 低于卖股 {sell_roe:.0f}% ({roe_diff_pp:.0f}pp)")

    # 3. 股息率差
    sell_div = _safe_float(sell_data.get('dividend_yield')) or 0
    buy_div = _safe_float(buy_data.get('dividend_yield')) or 0
    div_diff_pct = buy_div - sell_div
    if div_diff_pct > 1:
        reasons.append(f"买股股息 {buy_div:.1f}% > 卖股 {sell_div:.1f}% (+{div_diff_pct:.1f}pp)")
    elif div_diff_pct < -1:
        reasons.append(f"⚠ 买股股息 {buy_div:.1f}% < 卖股 {sell_div:.1f}% ({div_diff_pct:.1f}pp)")

    # 4. 预期年化收益差（粗略）
    # 公式（保守）：
    #   PE 估值回归收益（卖股变便宜 / 买股变贵）：
    #     (sell_pe - sell_fair_high) / sell_fair_high * 30%（年回归率）
    #     - (buy_pe - buy_fair_high) / buy_fair_high * 30%
    #   股息率差：直接加
    # 不算 ROE 复利（太长期不可靠）
    expected_return_diff = 0
    if pe_diff_pct is not None:
        # 卖股高估部分回归 → 卖股年化"亏"PE_REGRESSION_RATE 倍 → 我们卖了所以是"赚"
        sell_regression_loss = pe_diff_pct / 100 * PE_REGRESSION_RATE_PER_YEAR
        expected_return_diff += sell_regression_loss * 100  # 转 %
    if buy_pe_diff_pct is not None:
        # 买股低估部分回归 → 买股年化"赚"
        buy_regression_gain = -buy_pe_diff_pct / 100 * PE_REGRESSION_RATE_PER_YEAR
        expected_return_diff += buy_regression_gain * 100

    # 加股息率差
    expected_return_diff += div_diff_pct

    # 5. 回收期（换仓成本 / 年化收益差）
    payback_years = None
    if expected_return_diff > 0.1:  # 年化收益差至少 0.1% 才算
        payback_years = swap_cost_pct / expected_return_diff
    elif expected_return_diff <= 0:
        reasons.append("⚠ 预期收益差 ≤ 0，不建议换仓")

    # 6. 推荐档位
    recommendation = _classify_recommendation(
        expected_return_diff, payback_years, roe_diff_pp,
        pe_diff_pct, buy_pe_diff_pct
    )

    return {
        'pe_diff_pct': round(pe_diff_pct, 1) if pe_diff_pct is not None else None,
        'buy_pe_diff_pct': round(buy_pe_diff_pct, 1) if buy_pe_diff_pct is not None else None,
        'roe_diff_pp': round(roe_diff_pp, 1) if roe_diff_pp is not None else None,
        'div_diff_pct': round(div_diff_pct, 2),
        'expected_annual_return_diff_pct': round(expected_return_diff, 2),
        'payback_years': round(payback_years, 2) if payback_years else None,
        'swap_cost_pct': swap_cost_pct,
        'recommendation': recommendation,
        'reasons': reasons,
    }


def _classify_recommendation(expected_return, payback_years, roe_diff,
                              pe_diff, buy_pe_diff):
    """分级：strong / medium / weak / avoid"""
    # 如果预期收益 ≤ 0 → avoid
    if expected_return is None or expected_return <= 0:
        return 'avoid'

    # 极强信号：卖股极度高估 + 买股极度低估
    strong_pe = (pe_diff is not None and pe_diff > 50) or \
                (buy_pe_diff is not None and buy_pe_diff < -30)

    # 综合：年化收益差 + 回收期 + ROE 差
    if strong_pe and expected_return > 5 and (payback_years and payback_years < 0.5):
        return 'strong'
    if expected_return > 2 and (payback_years and payback_years < 1.5):
        if roe_diff is None or roe_diff >= -3:  # ROE 不能差太多
            return 'medium'
    if expected_return > 0.5:
        return 'weak'
    return 'avoid'


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ============================================================
# 自检
# ============================================================
if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    # 案例 1：明显应该换（卖股贵+ROE 低，买股便宜+ROE 高）
    sell = {'pe': 60, 'roe': 12, 'dividend_yield': 1, 'pe_fair_high': 30}
    buy = {'pe': 12, 'roe': 22, 'dividend_yield': 4, 'pe_fair_high': 25}
    r = estimate_swap_metrics(sell, buy)
    print('=== 案例 1：贵卖便宜买（应推荐 strong）===')
    for k, v in r.items():
        print(f'  {k}: {v}')

    print()
    # 案例 2：买股 ROE 反而低 → 应慎重
    sell = {'pe': 35, 'roe': 25, 'dividend_yield': 3, 'pe_fair_high': 30}
    buy = {'pe': 18, 'roe': 12, 'dividend_yield': 5, 'pe_fair_high': 20}
    r = estimate_swap_metrics(sell, buy)
    print('=== 案例 2：买股 ROE 低 ===')
    print(f'  recommendation: {r["recommendation"]}')
    print(f'  reasons: {r["reasons"]}')

    print()
    # 案例 3：两只股差不多 → avoid
    sell = {'pe': 25, 'roe': 18, 'dividend_yield': 2, 'pe_fair_high': 30}
    buy = {'pe': 24, 'roe': 19, 'dividend_yield': 2.2, 'pe_fair_high': 25}
    r = estimate_swap_metrics(sell, buy)
    print('=== 案例 3：差不多 ===')
    print(f'  expected_return: {r["expected_annual_return_diff_pct"]}%')
    print(f'  recommendation: {r["recommendation"]}')
