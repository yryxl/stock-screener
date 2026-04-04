"""
主程序 - 每日运行
1. 优先检查重点关注表的PE信号
2. 全市场筛选好公司候选池
3. 检查持仓信号
4. 推送通知
"""

import json
import os
import sys
from datetime import datetime

import pandas as pd
import yaml

from screener import screen_single_stock, screen_all_stocks, check_holdings_sell_signals, get_pe_signal
from notifier import send_daily_report, send_wechat, SIGNAL_LABELS
from data_fetcher import get_realtime_quotes


def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_json(filename):
    path = os.path.join(os.path.dirname(__file__), filename)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def is_trading_day():
    today = datetime.now()
    return today.weekday() < 5


def check_watchlist(config):
    """检查重点关注表的PE信号"""
    watchlist = load_json("watchlist.json")
    if not watchlist:
        print("重点关注表为空")
        return []

    print(f"正在检查重点关注表（{len(watchlist)}只）...")
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

                signal, signal_text = get_pe_signal(pe)

                signals.append({
                    "code": code,
                    "name": name,
                    "category": category,
                    "note": stock.get("note", ""),
                    "price": price if not pd.isna(price) else 0,
                    "pe": pe if not pd.isna(pe) else 0,
                    "signal": signal,
                    "signal_text": signal_text,
                })

                if signal and signal != "hold":
                    label = SIGNAL_LABELS.get(signal, "")
                    print(f"  {label} {name}({code}) PE={pe:.1f} 价格{price:.2f}")

    return signals


def format_watchlist_signals(signals):
    """格式化关注表信号"""
    buy_signals = [s for s in signals if s.get("signal") and "buy" in s["signal"]]
    sell_signals = [s for s in signals if s.get("signal") and "sell" in s["signal"]]

    if not buy_signals and not sell_signals:
        return ""

    lines = ["【重点关注表信号】\n"]

    for s in buy_signals:
        label = SIGNAL_LABELS.get(s["signal"], "")
        lines.append(f"{label}")
        lines.append(f"  {s['name']}（{s['code']}）")
        lines.append(f"  {s['signal_text']}")
        lines.append(f"  分类:{s['category']} | {s['note']}")
        lines.append("")

    for s in sell_signals:
        label = SIGNAL_LABELS.get(s["signal"], "")
        lines.append(f"{label}")
        lines.append(f"  {s['name']}（{s['code']}）")
        lines.append(f"  {s['signal_text']}")
        lines.append("")

    return "\n".join(lines)


def main():
    print(f"=== 芒格选股系统 {datetime.now().strftime('%Y-%m-%d %H:%M')} ===\n")

    if not is_trading_day():
        print("今天不是交易日（周末），跳过")
        if "--force" not in sys.argv:
            return

    config = load_config()

    # 1. 优先检查重点关注表
    print("=== 第一步：重点关注表 ===")
    watchlist_signals = check_watchlist(config)
    watchlist_buy = sum(1 for s in watchlist_signals if s.get("signal") and "buy" in s["signal"])
    watchlist_sell = sum(1 for s in watchlist_signals if s.get("signal") and "sell" in s["signal"])
    print(f"关注表: 买入信号{watchlist_buy}只，卖出信号{watchlist_sell}只")

    # 2. 全市场筛选候选池
    print("\n=== 第二步：全市场筛选好公司 ===")
    candidates = screen_all_stocks(config)

    # 3. 检查持仓信号
    print("\n=== 第三步：检查持仓信号 ===")
    holdings = load_json("holdings.json")
    if holdings:
        holding_signals = check_holdings_sell_signals(holdings, config)
    else:
        holding_signals = []
        print("无持仓数据")

    # 4. 推送通知
    print("\n=== 第四步：推送通知 ===")

    today = datetime.now().strftime("%Y-%m-%d")
    title = f"芒格选股 {today}"

    has_watchlist_signal = watchlist_buy > 0 or watchlist_sell > 0
    has_candidate_signal = any(s.get("signal") and s["signal"] != "hold" for s in candidates)
    has_holding_signal = len(holding_signals) > 0

    content = ""

    if has_watchlist_signal:
        content += format_watchlist_signals(watchlist_signals)

    if has_holding_signal:
        from notifier import format_holdings_signals
        content += "\n" + format_holdings_signals(holding_signals)

    if has_candidate_signal:
        from notifier import format_candidate_list
        content += "\n" + format_candidate_list(candidates)

    # 无任何信号时显示"无推荐"
    if not has_watchlist_signal and not has_candidate_signal and not has_holding_signal:
        content = "今日无推荐\n\n关注表和候选池均在合理区间，继续持有观察。"

    content += f"\n\n关注表{len(watchlist_signals)}只 | 候选池{len(candidates)}只"
    content += "\n仅供参考，不构成投资建议。"

    send_wechat(title, content, config)

    # 5. 总结
    print(f"\n=== 运行完成 ===")
    print(f"关注表信号: 买入{watchlist_buy} 卖出{watchlist_sell}")
    print(f"候选池: {len(candidates)}只好公司")
    print(f"持仓信号: {len(holding_signals)}只")


if __name__ == "__main__":
    main()
