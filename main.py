"""
主程序 - 每日运行，筛选股票并推送通知
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
    """加载持仓数据"""
    holdings_path = os.path.join(os.path.dirname(__file__), "holdings.json")
    if not os.path.exists(holdings_path):
        return []
    with open(holdings_path, "r", encoding="utf-8") as f:
        return json.load(f)


def is_trading_day():
    """简单判断是否为交易日（排除周末）"""
    today = datetime.now()
    # 周六=5，周日=6
    if today.weekday() >= 5:
        return False
    return True


def main():
    print(f"=== 芒格选股系统 {datetime.now().strftime('%Y-%m-%d %H:%M')} ===\n")

    # 检查是否交易日
    if not is_trading_day():
        print("今天不是交易日（周末），跳过运行")
        # GitHub Actions中通过参数可以强制运行
        if "--force" not in sys.argv:
            return

    config = load_config()

    # 1. 扫描全A股，寻找买入机会
    print("=== 第一步：全市场扫描（买入信号）===")
    buy_list = screen_all_stocks(config)

    # 2. 检查持仓，寻找卖出信号
    print("\n=== 第二步：持仓检查（卖出信号）===")
    holdings = load_holdings()
    if holdings:
        sell_list = check_holdings_sell_signals(holdings, config)
    else:
        sell_list = []
        print("无持仓数据，跳过卖出检查")

    # 3. 推送通知
    print("\n=== 第三步：推送通知 ===")
    send_daily_report(buy_list, sell_list, config)

    # 4. 输出总结
    print(f"\n=== 运行完成 ===")
    print(f"买入信号: {len(buy_list)} 只")
    print(f"卖出信号: {len(sell_list)} 只")


if __name__ == "__main__":
    main()
