"""
中国国情版 v3 规则单元测试

用 A 股历史造假案例、优质过路费股、资本密集股等真实案例
验证规则的有效性
"""

import sys
sys.stdout.reconfigure(encoding='utf-8')

from data_fetcher import get_financial_indicator, get_stock_industry
from screener import extract_annual_data
from china_adjustments import (
    check_financial_fraud_risk, check_st_delisting_risk,
    calculate_free_cashflow_china, check_toll_bridge_business,
    check_capital_intensive_treadmill, check_tech_surfer,
)


def test_stock(code, expected_issues, name_override=None):
    """测试一只股票"""
    print(f"\n{'='*60}")
    df = get_financial_indicator(code)
    if df is None:
        print(f"  ❌ {code} 获取财务数据失败")
        return
    df_a = extract_annual_data(df, years=8)
    if df_a.empty:
        print(f"  ❌ {code} 无年报数据")
        return

    name = name_override or code
    industry = get_stock_industry(code, fallback="")
    print(f"【{name} ({code})】 行业：{industry}")
    print(f"期望检测到：{expected_issues}")

    # 1. 财务造假风险
    fraud_level, fraud_flags = check_financial_fraud_risk(df_a, code, name)
    print(f"\n  📊 财务造假风险：{fraud_level}")
    for f in fraud_flags:
        print(f"     - {f}")

    # 2. ST 检测
    is_st, st_type = check_st_delisting_risk(name, code)
    if is_st:
        print(f"  ⚠️ ST状态：{st_type}")

    # 3. 自由现金流
    fcf = calculate_free_cashflow_china(df_a)
    if fcf.get("has_data"):
        print(f"  💵 近3年每股经营现金流：{fcf['recent_ocf_per_share']}")
        if fcf.get("warning"):
            print(f"     ⚠️ {fcf['warning']}")

    # 4. 过路费生意
    roes = [float(r.get("净资产收益率", 0) or 0) for _, r in df_a.head(5).iterrows()
            if r.get("净资产收益率") is not None]
    avg_roe = sum(roes)/len(roes) if roes else 0
    debt = df_a.iloc[0].get("资产负债率")
    is_toll, toll_class, toll_reasons = check_toll_bridge_business(
        industry, name, avg_roe, float(debt) if debt else None, None)
    if is_toll:
        print(f"  🛣️ 过路费生意：{toll_class}")
        for r in toll_reasons:
            print(f"     - {r}")

    # 5. 跑步机型
    is_tm, tm_reasons = check_capital_intensive_treadmill(df_a, industry)
    if is_tm:
        print(f"  🏃 跑步机型：")
        for r in tm_reasons:
            print(f"     - {r}")

    # 6. 冲浪者型
    is_sf, sf_reasons = check_tech_surfer(df_a, industry, name)
    if is_sf:
        print(f"  🏄 冲浪者型：")
        for r in sf_reasons:
            print(f"     - {r}")


def main():
    print("=" * 60)
    print("中国国情版 v3 规则测试")
    print("=" * 60)

    # 已知财务造假案例
    print("\n\n### 📕 已知财务造假案例 ###")
    test_stock("600518", "造假史（康美药业）", "康美药业")
    test_stock("002450", "造假史（康得新）", "康得新")

    # 过路费生意
    print("\n\n### 📘 过路费生意（ROE 低但稳定） ###")
    test_stock("600900", "过路费", "长江电力")
    test_stock("601006", "过路费", "大秦铁路")
    test_stock("600018", "过路费", "上港集团")

    # 跑步机型
    print("\n\n### 📒 跑步机型（资本密集） ###")
    test_stock("601600", "跑步机", "中国铝业")
    test_stock("000725", "跑步机", "京东方A")

    # 冲浪者型
    print("\n\n### 📗 冲浪者型（科技迭代） ###")
    test_stock("603501", "冲浪者", "韦尔股份")
    test_stock("300750", "冲浪者？", "宁德时代")

    # 好公司对照组（应该无异常）
    print("\n\n### 📙 好公司对照组（应该干净） ###")
    test_stock("600519", "无异常", "贵州茅台")
    test_stock("000333", "无异常", "美的集团")


if __name__ == "__main__":
    main()
