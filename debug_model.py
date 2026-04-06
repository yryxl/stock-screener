"""
快速调试模型 - 用缓存数据，几秒出结果
用法：
  python debug_model.py                    # 对关注表所有股票跑模型
  python debug_model.py 600096             # 只看云天化
  python debug_model.py 600096 000538      # 看多只
"""

import json
import os
import sys

from data_cache import get_cached_stock, get_cached_quotes, collect_all_data
from screener import get_pe_signal, match_industry_pe, COMPLEXITY_ROE_ADJUST

SIGNAL_LABELS = {
    "buy_heavy": "🔴 可以重仓",
    "buy_medium": "🟠 可以中仓",
    "buy_light": "🟡 可以轻仓",
    "buy_watch": "⚪ 重点关注",
    "hold": "⚪ 继续观望",
    "hold_keep": "🟢 持续持有",
    "sell_watch": "⚪ 关注卖出",
    "sell_light": "🟡 适当卖出",
    "sell_medium": "🟠 中仓卖出",
    "sell_heavy": "🔴 大量卖出",
    "true_decline": "⛔ 基本面恶化",
}


def analyze_stock(code):
    """用缓存数据分析单只股票"""
    data = get_cached_stock(code)
    if not data:
        print(f"\n{code}: 无缓存数据，请先运行 python data_cache.py")
        return

    name = data.get("name", code)
    industry = data.get("industry", "")
    pe_ttm = data.get("pe_ttm")
    roe_avg = data.get("roe_avg")
    roe_values = data.get("roe_values", [])
    debt_ratio = data.get("debt_ratio")
    current_ratio = data.get("current_ratio")
    gross_margin = data.get("gross_margin")
    opm_values = data.get("opm_values", [])
    fcf_values = data.get("fcf_values", [])
    data_years = data.get("data_years", 0)

    # 行业PE区间
    pe_range = match_industry_pe(industry)
    complexity = pe_range.get("complexity", "medium")

    # PE信号
    signal, signal_text = get_pe_signal(pe_ttm, industry) if pe_ttm else (None, "无PE数据")

    # ROE门槛计算
    base_thresh = COMPLEXITY_ROE_ADJUST.get(complexity, COMPLEXITY_ROE_ADJUST["medium"])
    leverage_adj = 0
    if debt_ratio and debt_ratio < 30:
        leverage_adj = -2
    elif debt_ratio and debt_ratio > 50:
        leverage_adj = 5
    roe_thresh = {k: v + leverage_adj for k, v in base_thresh.items()}

    # ROE等级
    roe_level = "none"
    if roe_avg:
        if roe_avg >= roe_thresh["heavy"]:
            roe_level = "heavy"
        elif roe_avg >= roe_thresh["light"]:
            roe_level = "light"
        elif roe_avg >= roe_thresh["watch"]:
            roe_level = "watch"

    # 买入信号降级
    if signal and "buy" in signal:
        signal_rank = {"buy_heavy": 0, "buy_medium": 1, "buy_light": 2, "buy_watch": 3, "hold": 4}
        cap_map = {"heavy": "buy_heavy", "light": "buy_light", "watch": "buy_watch", "none": "hold"}
        max_signal = cap_map.get(roe_level, "hold")
        if signal_rank.get(signal, 4) < signal_rank.get(max_signal, 4):
            signal = max_signal

    # 关注表：卖出方向改观望
    if signal and "sell" in signal:
        signal = "hold"

    label = SIGNAL_LABELS.get(signal, "?")
    complexity_cn = {"simple": "简单", "medium": "中等", "complex": "复杂"}.get(complexity, "?")

    print(f"\n{'='*60}")
    print(f"  {code} | 行业: {industry} | 复杂度: {complexity_cn}")
    print(f"{'='*60}")
    print(f"  PE(TTM):     {pe_ttm:.1f}" if pe_ttm else "  PE(TTM):     无数据")
    print(f"  PE合理区间:  {pe_range.get('fair_low','?')}-{pe_range.get('fair_high','?')}")
    print(f"  ROE均值:     {roe_avg:.1f}%" if roe_avg else "  ROE均值:     无数据")
    print(f"  ROE历史:     {['%.1f' % r for r in roe_values[:5]]}" if roe_values else "")
    print(f"  ROE门槛:     重仓≥{roe_thresh['heavy']}% 轻仓≥{roe_thresh['light']}% 关注≥{roe_thresh['watch']}%")
    print(f"  ROE等级:     {roe_level}")
    print(f"  负债率:      {debt_ratio:.1f}%" if debt_ratio else "  负债率:      无数据")
    print(f"  流动比率:    {current_ratio:.2f}" if current_ratio else "  流动比率:    无数据")
    print(f"  毛利率:      {gross_margin:.1f}%" if gross_margin else "  毛利率:      无数据")
    print(f"  数据年数:    {data_years}年")
    print(f"  现金流:      {['%.2f' % f for f in fcf_values[:5]]}" if fcf_values else "")
    print(f"  ────────────────────────────────")
    print(f"  模型结论:    {label} | {signal_text}")
    print(f"{'='*60}")


def main():
    # 确保有缓存数据
    if not os.path.exists(os.path.join(os.path.dirname(__file__), "cache")):
        print("首次运行，需要先采集数据...")
        collect_all_data()

    # 获取要分析的股票
    codes = sys.argv[1:]
    if not codes:
        # 默认分析关注表
        path = os.path.join(os.path.dirname(__file__), "watchlist.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                watchlist = json.load(f)
            codes = [s["code"] for s in watchlist]
            print(f"分析关注表 {len(codes)} 只股票（使用缓存数据）")
        else:
            print("无关注表，请指定股票代码：python debug_model.py 600096")
            return

    for code in codes:
        analyze_stock(code)


if __name__ == "__main__":
    main()
