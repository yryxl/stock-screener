"""
主程序 - 每日运行
1. 重点关注表PE信号 + 真假下跌判断
2. 全市场筛选好公司候选池
3. 持仓信号（PE + 真跌检测）
4. 每天推送（无信号发"无推荐"）
"""

import json
import os
import sys
from datetime import datetime

import pandas as pd
import yaml

from screener import (
    screen_all_stocks, check_holdings_sell_signals,
    get_pe_signal, check_decline_signals,
    check_watchlist_financial_health,
)
from notifier import send_daily_report
from data_fetcher import get_realtime_quotes, get_pe_ttm, safe_fetch
import akshare as ak


def load_config():
    path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_json(filename):
    path = os.path.join(os.path.dirname(__file__), filename)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def is_trading_day():
    return datetime.now().weekday() < 5


def get_stock_industry(code):
    """从东财获取股票的真实行业分类"""
    try:
        df = safe_fetch(ak.stock_individual_info_em, symbol=code)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                if "行业" in str(row.get("item", "")):
                    return str(row["value"])
    except Exception:
        pass
    return ""


def check_watchlist(config, quotes_df):
    """检查重点关注表：自动获取行业 + PE信号 + 财务健康验证 + 真假跌验证"""
    watchlist = load_json("watchlist.json")
    if not watchlist:
        return [], watchlist

    print(f"检查重点关注表（{len(watchlist)}只）...")
    signals = []

    for stock in watchlist:
        code = stock["code"]
        name = stock.get("name", code)
        # 自动获取真实行业（不依赖手动填写）
        category = get_stock_industry(code)
        if not category:
            category = stock.get("category", "")
        else:
            stock["category"] = category  # 更新关注表中的行业

        if quotes_df is not None and not quotes_df.empty:
            row = quotes_df[quotes_df["代码"] == code]
            if not row.empty:
                row = row.iloc[0]
                price = pd.to_numeric(row.get("最新价"), errors="coerce")

                # 优先用PE(TTM)准确数据，失败才用动态PE
                pe = None
                pe_source = ""
                ttm_data = get_pe_ttm(code)
                if ttm_data and ttm_data.get("pe_ttm"):
                    pe = ttm_data["pe_ttm"]
                    pe_source = "TTM"
                else:
                    pe = pd.to_numeric(row.get("市盈率-动态"), errors="coerce")
                    pe_source = "动态"

                industry = category + " " + stock.get("note", "")
                signal, signal_text = get_pe_signal(pe, industry)
                if pe_source == "TTM":
                    signal_text = signal_text.replace(f"PE={pe:.1f}", f"PE(TTM)={pe:.1f}")

                # 如果是买入信号，验证财务健康+ROE等级
                if signal and "buy" in signal:
                    health_ok, health_warning, roe_level = check_watchlist_financial_health(code)

                    # ROE等级限制信号上限
                    signal_cap = {
                        "heavy": "buy_heavy",   # ROE高+低杠杆 允许重仓
                        "light": "buy_light",   # ROE中等 最高轻仓
                        "watch": "buy_watch",   # ROE偏低 最高关注
                        "none": "hold",         # ROE过低 不买
                    }
                    max_signal = signal_cap.get(roe_level, "buy_light")
                    signal_rank = {"buy_heavy": 0, "buy_medium": 1, "buy_light": 2, "buy_watch": 3, "hold": 4}

                    if signal_rank.get(signal, 4) < signal_rank.get(max_signal, 4):
                        # 降级后重写信号文字，避免矛盾
                        signal = max_signal
                        if roe_level == "none":
                            signal_text = f"PE偏低但ROE过低，不建议买入"
                        else:
                            level_names = {"buy_light": "轻仓买入", "buy_watch": "关注买入", "buy_medium": "中仓买入"}
                            signal_text = f"PE偏低，但ROE限制最高{level_names.get(max_signal, '轻仓')}"

                    # 财务风险直接降为hold
                    if not health_ok:
                        signal = "hold"
                        signal_text = f"财务风险: {health_warning}，暂不建议买入"
                        print(f"  {name} 财务风险: {health_warning}")
                    elif health_warning:
                        signal_text += f" ({health_warning})"

                # 买入信号还要验证：不是"真跌"（基本面恶化）
                if signal and "buy" in signal:
                    from screener import check_fundamental_health
                    is_healthy, problems = check_fundamental_health(code)
                    if is_healthy is not None and not is_healthy:
                        signal = "hold"
                        signal_text = f"基本面恶化({','.join(problems[:2])})，不推荐买入"
                        print(f"  {name} 基本面恶化，pass")

                signals.append({
                    "code": code, "name": name,
                    "category": category,
                    "note": stock.get("note", ""),
                    "price": price if not pd.isna(price) else 0,
                    "pe": pe if not pd.isna(pe) else 0,
                    "signal": signal, "signal_text": signal_text,
                })

    return signals, watchlist


def main():
    print(f"=== 芒格选股系统 {datetime.now().strftime('%Y-%m-%d %H:%M')} ===\n")

    if not is_trading_day() and "--force" not in sys.argv:
        print("非交易日，跳过")
        return

    config = load_config()

    # 获取实时行情（后续多处复用）
    print("获取实时行情...")
    quotes_df = get_realtime_quotes()

    # 1. 重点关注表PE信号（买入方向）
    print("\n=== 第一步：重点关注表 ===")
    watchlist_signals, watchlist_raw = check_watchlist(config, quotes_df)
    w_buy = sum(1 for s in watchlist_signals if s.get("signal") and "buy" in s["signal"])
    print(f"  PE买入信号: {w_buy}只")

    # 2. 关注表 + 全A股的假跌检测（买入方向）
    print("\n=== 第二步：真假下跌检测 ===")
    # 对关注表做真假下跌判断
    false_declines_w, true_declines_w = check_decline_signals(watchlist_raw, quotes_df)
    print(f"  关注表: 假跌{len(false_declines_w)}只 真跌{len(true_declines_w)}只")

    # 3. 全市场筛选好公司候选池（买入方向）
    print("\n=== 第三步：全市场筛选 ===")
    candidates = screen_all_stocks(config)

    # 4. 持仓PE信号 + 真跌检测（卖出方向，只针对持仓）
    print("\n=== 第四步：持仓检查 ===")
    holdings = load_json("holdings.json")
    holding_pe_signals = []
    holding_decline_signals = []

    if holdings:
        # PE信号
        holding_pe_signals = check_holdings_sell_signals(holdings, config)
        # 真假下跌（只关注真跌作为卖出警告）
        _, true_declines_h = check_decline_signals(holdings, quotes_df)
        holding_decline_signals = true_declines_h
        print(f"  持仓PE卖出信号: {len(holding_pe_signals)}只")
        print(f"  持仓真跌警告: {len(holding_decline_signals)}只")
    else:
        print("  无持仓")

    # 5. 推送
    print("\n=== 第五步：推送 ===")
    send_daily_report(
        watchlist_signals=watchlist_signals,
        candidates=candidates,
        holding_signals=holding_pe_signals,
        false_declines=false_declines_w,      # 关注表假跌→买入机会
        true_declines=holding_decline_signals, # 持仓真跌→卖出警告
        config=config,
    )

    # 6. 保存每日结果（供Streamlit页面展示）
    print("\n=== 第六步：保存结果 ===")
    daily_data = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "watchlist_signals": watchlist_signals,
        "candidates": [s for s in candidates if s.get("signal") and s["signal"] != "hold"],
        "holding_signals": holding_pe_signals,
        "false_declines": false_declines_w,
        "true_declines": holding_decline_signals,
    }
    results_path = os.path.join(os.path.dirname(__file__), "daily_results.json")
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(daily_data, f, ensure_ascii=False, indent=2, default=str)
    print(f"  结果已保存到 daily_results.json")

    # 总结
    print(f"\n=== 完成 ===")
    print(f"关注表买入信号: {w_buy} | 假跌买入: {len(false_declines_w)}")
    print(f"候选池: {len(candidates)} | 持仓卖出: {len(holding_pe_signals)} | 真跌警告: {len(holding_decline_signals)}")


if __name__ == "__main__":
    main()
