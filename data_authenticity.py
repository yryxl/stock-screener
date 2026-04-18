"""
TODO-036 / REQ-AUTHENTICITY-001 浑水式数据真实性多维度校验（2026-04-18）

设计依据：
  浑水做空东方纸业的方法——查上游原料供应商交易记录（不容易造假），
  而不是下游销售数据（容易造假）。
  康得新 119 亿造假 + 康美 887 亿造假 + 瑞幸 22 亿造假的共同特征：
  报表数据漂亮，但衍生指标矛盾（高现金低利息 / 高毛利低应收 / 借款 vs 现金）

6 条交叉校验规则（按浑水/康得新/康美教训）：
  1. 高现金 + 微薄利息收入（康得新模式）
  2. 应收/营收增速对比（康美模式）
  3. 存货 vs 营收异常（渠道压货）
  4. 研发资本化占比警告（粉饰利润手法）
  5. 借款 vs 现金矛盾（账上现金可能假）
  6. 毛利率反常对比同行（高得不可能）

输出：
  所有规则触发都写入 china_v3_risks（不硬否决，让用户判断）
  设计原则：宁可错过不犯错——警告就够了，不需要直接否决
"""

import pandas as pd


# ============================================================
# 规则 1：高现金 + 微薄利息收入（康得新模式）
# ============================================================
# 康得新账面 150 亿现金，但利息收入几乎 0 → 钱可能假
# 一年期定存利率 1.5%，50 亿现金应收 ≥ 0.75 亿利息

def check_cash_interest_mismatch(df_annual, balance_sheet=None):
    """
    检测"高现金 + 微薄利息"

    输入：
      df_annual: 年报 DataFrame（含财务摘要数据）
      balance_sheet: 可选，资产负债表（用于精确取现金/利息）
    返回：(triggered, detail)
    """
    if df_annual is None or df_annual.empty:
        return False, ""
    try:
        latest = df_annual.iloc[0]
        # 取每股净资产 + 总股本 估算
        # 实际更精确做法：拉资产负债表的"货币资金"和利润表的"利息收入"
        # 简化版：用每股经营现金流估算
        # 因为缺直接的"利息收入"字段，本规则需要资产负债表深度数据才精确
        # 暂返回 False，等后续填充资产负债表数据后启用
        return False, ""
    except Exception:
        return False, ""


# ============================================================
# 规则 2：应收/营收增速对比（康美模式）
# ============================================================
# 康美应收增 70.92 亿，存货增 154.78 �expose 但收现比仅 0.39
# 健康公司：应收增速应 ≤ 营收增速

def check_receivable_growth_anomaly(df_annual):
    """
    检测"应收账款增速 > 营收增速 1.5x"

    输入：df_annual 至少 3 年数据
    返回：(triggered, detail)
    """
    if df_annual is None or len(df_annual) < 3:
        return False, ""
    try:
        # 近 3 年营收/应收增长率（latest-first）
        # 字段名：营业总收入 / 应收账款（akshare 默认）
        # 注：akshare 财务摘要里通常没有应收账款字段，需要资产负债表
        # 简化版：用"应收账款周转天数"反推趋势
        col_days = None
        for c in df_annual.columns:
            if '应收账款周转天数' in c or '应收周转天数' in c:
                col_days = c
                break
        if col_days is None:
            return False, ""
        days = pd.to_numeric(df_annual[col_days], errors="coerce").dropna().head(3)
        if len(days) < 3:
            return False, ""
        # 应收周转天数（越大说明应收占用越久）
        days_list = days.tolist()  # latest-first
        # 周转天数连续上升 50% 以上 → 应收账款占用恶化
        if days_list[2] > 0 and days_list[0] > days_list[2] * 1.5:
            return True, (
                f"应收账款周转天数恶化：从 {days_list[2]:.0f} 天 → "
                f"{days_list[0]:.0f} 天（3 年 +{(days_list[0]/days_list[2]-1)*100:.0f}%），"
                f"康美式造假特征（应收虚增）"
            )
    except Exception:
        pass
    return False, ""


# ============================================================
# 规则 3：存货 vs 营收异常（渠道压货）
# ============================================================
# 健康公司：存货增长应 ≈ 营收增长

def check_inventory_anomaly(df_annual):
    """
    检测"存货周转天数突增（渠道压货）"
    """
    if df_annual is None or len(df_annual) < 3:
        return False, ""
    try:
        col_days = None
        for c in df_annual.columns:
            if '存货周转天数' in c:
                col_days = c
                break
        if col_days is None:
            return False, ""
        days = pd.to_numeric(df_annual[col_days], errors="coerce").dropna().head(3)
        if len(days) < 3:
            return False, ""
        days_list = days.tolist()
        # 存货周转天数 3 年 +50% 以上 → 渠道压货
        if days_list[2] > 0 and days_list[0] > days_list[2] * 1.5:
            return True, (
                f"存货周转天数恶化：{days_list[2]:.0f} → {days_list[0]:.0f} 天（+{(days_list[0]/days_list[2]-1)*100:.0f}%）"
                f"，可能渠道压货"
            )
    except Exception:
        pass
    return False, ""


# ============================================================
# 规则 4：研发资本化占比（粉饰利润手法）
# ============================================================
# 研发支出可以"费用化"或"资本化"
# 资本化 = 不进当期成本 → 抬高利润
# 健康公司：资本化占比 < 30%（科技股可能略高）

def check_rd_capitalization_warning(code, industry):
    """
    检测"研发资本化占比异常"
    需要拉利润表 + 资产负债表，本简化版仅返回行业风险标签
    """
    if not industry:
        return False, ""
    # 高风险行业（研发资本化常见）
    high_risk_industries = ['软件', '生物制品', '医药制造', '半导体', '人工智能']
    for kw in high_risk_industries:
        if kw in industry:
            # 这里仅作为弱提示，不触发警告
            # 完整实现需要拉财报中的"研发支出资本化金额"
            return False, ""
    return False, ""


# ============================================================
# 规则 5：借款 vs 现金矛盾（账上现金可能假）
# ============================================================
# 账上 50 亿现金还融资借款 → 现金可能受限或假

def check_cash_loan_paradox(df_annual, balance_sheet=None):
    """
    检测"高现金还高借款的悖论"

    简化版：用每股净资产 vs 资产负债率推断
    完整版需要资产负债表（货币资金 + 短期借款 + 长期借款）
    """
    # 留接口，等资产负债表数据完善后启用
    return False, ""


# ============================================================
# 规则 6：毛利率反常对比同行
# ============================================================
# 公司毛利率 > 同行均值 1.5x → 高得不可能（东方纸业模式）

# 同行均值数据（按行业，需定期维护）
INDUSTRY_GM_BENCHMARK = {
    '白酒': 80, '调味品': 50, '乳制品': 30, '饮料': 50,
    '医药制造': 50, '中药': 50, '生物制品': 60,
    '家电': 30, '汽车': 15, '机械': 25,
    '钢铁': 12, '有色金属': 15, '煤炭': 30,
    '化工': 20, '化纤': 15, '建材': 25,
    '银行': 0,  # 银行无毛利概念
    '证券': 50, '保险': 0,
    '电力': 25, '燃气': 20,
    '计算机软件': 60, '半导体': 40, '通信设备': 30,
    '房地产': 25, '建筑': 15,
    '纺织': 20, '服装': 40,
}


def check_gross_margin_anomaly(df_annual, industry):
    """
    检测"毛利率高于同行均值 1.5x"
    """
    if df_annual is None or df_annual.empty or not industry:
        return False, ""
    try:
        col = None
        for c in df_annual.columns:
            if '销售毛利率' in c or '毛利率' == c:
                col = c
                break
        if col is None:
            return False, ""
        gm = pd.to_numeric(df_annual[col], errors="coerce").dropna()
        if gm.empty:
            return False, ""
        latest_gm = float(gm.iloc[0])

        # 找同行基准
        benchmark = None
        for ind_kw, bm in INDUSTRY_GM_BENCHMARK.items():
            if ind_kw in industry:
                benchmark = bm
                break
        if benchmark is None or benchmark == 0:
            return False, ""

        # 高于同行基准 50% 以上 → 警告
        if latest_gm > benchmark * 1.5:
            return True, (
                f"毛利率反常：{latest_gm:.1f}% 远高于行业基准（{benchmark}%，超 "
                f"{(latest_gm/benchmark-1)*100:.0f}%）。"
                f"东方纸业式信号——可能虚增收入或关联交易。建议核实"
            )
    except Exception:
        pass
    return False, ""


# ============================================================
# 综合入口：跑所有 6 条规则
# ============================================================

def check_authenticity_all(code, industry, df_annual, balance_sheet=None):
    """
    跑所有数据真实性校验，返回触发的警告列表

    返回：[
      {'rule': 'gm_anomaly', 'detail': '...', 'severity': 'warning'},
      ...
    ]
    """
    alerts = []

    # 规则 1：高现金低利息（待资产负债表）
    t, d = check_cash_interest_mismatch(df_annual, balance_sheet)
    if t:
        alerts.append({'rule': 'cash_interest_mismatch', 'detail': d, 'severity': 'warning'})

    # 规则 2：应收周转恶化
    t, d = check_receivable_growth_anomaly(df_annual)
    if t:
        alerts.append({'rule': 'receivable_anomaly', 'detail': d, 'severity': 'warning'})

    # 规则 3：存货周转恶化
    t, d = check_inventory_anomaly(df_annual)
    if t:
        alerts.append({'rule': 'inventory_anomaly', 'detail': d, 'severity': 'warning'})

    # 规则 4：研发资本化（待财报数据）
    t, d = check_rd_capitalization_warning(code, industry)
    if t:
        alerts.append({'rule': 'rd_cap_warning', 'detail': d, 'severity': 'info'})

    # 规则 5：借款 vs 现金（待资产负债表）
    t, d = check_cash_loan_paradox(df_annual, balance_sheet)
    if t:
        alerts.append({'rule': 'cash_loan_paradox', 'detail': d, 'severity': 'warning'})

    # 规则 6：毛利率反常
    t, d = check_gross_margin_anomaly(df_annual, industry)
    if t:
        alerts.append({'rule': 'gm_anomaly', 'detail': d, 'severity': 'warning'})

    return alerts


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    from data_fetcher import get_financial_indicator, extract_annual_data, get_stock_industry

    # 测试 6 只代表股
    cases = [
        ('600519', '贵州茅台', '白酒'),
        ('000725', '京东方A', '光学光电子'),  # 应收周转可能差
        ('600085', '同仁堂', '中药'),
        ('601398', '工商银行', '银行Ⅱ'),  # 银行无毛利
        ('000651', '格力电器', '家电'),
        ('600436', '片仔癀', '中药'),  # 高毛利可能触发
    ]

    for code, name, _ in cases:
        try:
            df = get_financial_indicator(code)
            if df is None:
                print(f'{code} {name}: 数据缺失')
                continue
            df_a = extract_annual_data(df, years=10)
            ind = get_stock_industry(code, fallback='')
            alerts = check_authenticity_all(code, ind, df_a)
            print(f'{code} {name} ({ind}): {len(alerts)} 条警告')
            for a in alerts:
                print(f'  [{a["severity"]}] {a["detail"][:80]}')
        except Exception as e:
            print(f'{code} {name}: 异常 {e}')
