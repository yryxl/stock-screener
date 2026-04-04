"""
主程序 - 每日运行
1. 检查重点关注表PE信号（行业感知）
2. 全市场筛选好公司候选池
3. 检查持仓信号
4. 推送通知（每天都发，无信号发"无推荐"）
"""

import json
import os
import sys
from datetime import datetime

import pandas as pd
import yaml

from screener import screen_all_stocks, check_holdings_sell_signals, get_pe_signal
from notifier import send_daily_report
from data_fetcher import get_realtime_quotes


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


def check_watchlist(config):
    """检查重点关注表（行业感知PE信号）"""
    watchlist = load_json("watchlist.json")
    if not watchlist:
        return []

    print(f"检查重点关注表（{len(watchlist)}只）...")
    quotes_df = get_realtime_quotes()
    signals = []

    for stock in watchlist:
        code = stock["code"]
        name = stock.get("name", code)
        category = stock.get("category", "")

        if quotes_df is not None and not quotes_df.empty:
            row = quotes_df[quotes_df["代码"] == code]
            if not row.empty:
                row = row.iloc[0]
                price = pd.to_numeric(row.get("最新价"), errors="coerce")
                pe = pd.to_numeric(row.get("市盈率-动态"), errors="coerce")

                # 用分类/备注匹配行业
                industry = category + " " + stock.get("note", "")
                signal, signal_text = get_pe_signal(pe, industry)

                signals.append({
                    "code": code, "name": name,
                    "category": category,
                    "note": stock.get("note", ""),
                    "price": price if not pd.isna(price) else 0,
                    "pe": pe if not pd.isna(pe) else 0,
                    "signal": signal, "signal_text": signal_text,
                })

                if signal and signal != "hold":
                    print(f"  {signal_text[:50]}... {name}({code})")

    return signals


def main():
    print(f"=== 芒格选股系统 {datetime.now().strftime('%Y-%m-%d %H:%M')} ===\n")

    if not is_trading_day() and "--force" not in sys.argv:
        print("非交易日，跳过")
        return

    config = load_config()

    # 1. 重点关注表
    print("=== 第一步：重点关注表 ===")
    watchlist_signals = check_watchlist(config)

    # 2. 全市场筛选
    print("\n=== 第二步：全市场筛选 ===")
    candidates = screen_all_stocks(config)

    # 3. 持仓信号
    print("\n=== 第三步：持仓信号 ===")
    holdings = load_json("holdings.json")
    holding_signals = check_holdings_sell_signals(holdings, config) if holdings else []

    # 4. 推送（每天都发）
    print("\n=== 第四步：推送 ===")
    send_daily_report(watchlist_signals, candidates, holding_signals, config)

    # 总结
    w_buy = sum(1 for s in watchlist_signals if s.get("signal") and "buy" in s["signal"])
    w_sell = sum(1 for s in watchlist_signals if s.get("signal") and "sell" in s["signal"])
    print(f"\n=== 完成 ===")
    print(f"关注表: 买入{w_buy} 卖出{w_sell} | 候选池: {len(candidates)} | 持仓信号: {len(holding_signals)}")


if __name__ == "__main__":
    main()
