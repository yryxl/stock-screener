"""
主程序 - 每日运行，筛选好公司+买卖信号推送
"""

import json
import os
import sys
from datetime import datetime

import yaml

from screener import screen_all_stocks, check_holdings_sell_signals
from notifier import send_daily_report


def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_holdings():
    holdings_path = os.path.join(os.path.dirname(__file__), "holdings.json")
    if not os.path.exists(holdings_path):
        return []
    with open(holdings_path, "r", encoding="utf-8") as f:
        return json.load(f)


def is_trading_day():
    today = datetime.now()
    if today.weekday() >= 5:
        return False
    return True


def main():
    print(f"=== 芒格选股系统 {datetime.now().strftime('%Y-%m-%d %H:%M')} ===\n")

    if not is_trading_day():
        print("今天不是交易日（周末），跳过运行")
        if "--force" not in sys.argv:
            return

    config = load_config()

    # 1. 筛选好公司候选池 + PE信号
    print("=== 第一步：筛选好公司候选池 ===")
    candidates = screen_all_stocks(config)

    # 2. 检查持仓信号
    print("\n=== 第二步：检查持仓信号 ===")
    holdings = load_holdings()
    if holdings:
        holding_signals = check_holdings_sell_signals(holdings, config)
    else:
        holding_signals = []
        print("无持仓数据，跳过")

    # 3. 推送
    print("\n=== 第三步：推送通知 ===")
    send_daily_report(candidates, holding_signals, config)

    # 4. 总结
    print(f"\n=== 运行完成 ===")
    print(f"候选池: {len(candidates)} 只好公司")
    buy_count = sum(1 for s in candidates if s.get("signal") and "buy" in s["signal"])
    sell_count = sum(1 for s in candidates if s.get("signal") and "sell" in s["signal"])
    print(f"买入信号: {buy_count} 只 | 卖出信号: {sell_count} 只")
    print(f"持仓信号: {len(holding_signals)} 只")


if __name__ == "__main__":
    main()
