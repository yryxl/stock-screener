"""
自动回测脚本 - 从随机起点跑到2025年
按模型信号自动买卖，检查最终收益
"""

import json
import os
import random
import sys

sys.stdout.reconfigure(encoding='utf-8')

from backtest_engine import get_month_signals, generate_anonymous_map, load_stock_list

INITIAL_CAPITAL = 10000
MAX_POSITION_PCT = 0.3  # 单只最多占30%


def run_backtest(start_year, start_month):
    print(f"=== 自动回测 起始:{start_year}-{start_month:02d} 资金:¥{INITIAL_CAPITAL:,} ===\n")

    stocks = load_stock_list()
    anon_map = generate_anonymous_map(list(stocks.keys()), seed=42)
    industry_map = {}

    cash = INITIAL_CAPITAL
    holdings = {}  # {sid: {"shares": N, "cost": X, "anon": "K07"}}
    trade_log = []
    monthly_values = []

    year, month = start_year, start_month

    while year < 2025 or (year == 2025 and month <= 12):
        signals = get_month_signals(year, month, anon_map=anon_map, industry_map=industry_map)
        if not signals:
            # 前进
            if month >= 12:
                month = 1; year += 1
            else:
                month += 1
            continue

        # 计算当前持仓市值
        portfolio_value = 0
        for sid, h in holdings.items():
            price = h["cost"]
            for anon_id, sdata in signals.items():
                if sdata.get("sid") == sid:
                    price = sdata.get("price", h["cost"])
                    break
            portfolio_value += h["shares"] * price

        total = cash + portfolio_value
        monthly_values.append({"date": f"{year}-{month:02d}", "total": total, "cash": cash, "portfolio": portfolio_value})

        # 执行卖出（持仓中有卖出信号的）
        sids_to_sell = []
        for sid, h in holdings.items():
            for anon_id, sdata in signals.items():
                if sdata.get("sid") == sid:
                    sig = sdata.get("signal", "")
                    if "sell" in sig or sig == "delisted":
                        sids_to_sell.append((sid, sdata, sig))
                    break

        for sid, sdata, sig in sids_to_sell:
            h = holdings[sid]
            price = sdata.get("price", h["cost"])
            if price <= 0:
                continue

            if sig in ("sell_heavy", "delisted"):
                sell_shares = h["shares"]
            elif sig == "sell_medium":
                sell_shares = int(h["shares"] * 2 / 3 // 100) * 100
            elif sig == "sell_light":
                sell_shares = int(h["shares"] / 2 // 100) * 100
            else:  # sell_watch
                sell_shares = int(h["shares"] / 3 // 100) * 100

            if sell_shares <= 0:
                sell_shares = h["shares"]  # 不足100股全卖

            cash += price * sell_shares
            h["shares"] -= sell_shares
            trade_log.append(f"{year}-{month:02d} 卖出 {h['anon']} {sell_shares}股 @¥{price:.2f} ({sig})")

            if h["shares"] <= 0:
                del holdings[sid]

        # 执行买入（模型推荐买入的，按优先级）
        buy_priority = ["buy_heavy", "buy_medium", "buy_light", "buy_watch"]
        for sig_level in buy_priority:
            for anon_id, sdata in signals.items():
                if sdata.get("signal") != sig_level:
                    continue

                sid = sdata["sid"]
                price = sdata.get("price", 0)
                if price <= 0 or price * 100 > cash:
                    continue

                # 单只仓位控制
                existing = holdings.get(sid, {}).get("shares", 0) * price
                if (existing + price * 100) / max(total, 1) > MAX_POSITION_PCT:
                    continue

                # 计算买入股数
                if sig_level == "buy_heavy":
                    budget = cash * 0.25
                elif sig_level == "buy_medium":
                    budget = cash * 0.15
                elif sig_level == "buy_light":
                    budget = cash * 0.10
                else:
                    budget = cash * 0.05

                shares = int(budget / price // 100) * 100
                if shares < 100:
                    continue

                cost = shares * price
                cash -= cost

                if sid in holdings:
                    old_cost = holdings[sid]["shares"] * holdings[sid]["cost"]
                    holdings[sid]["shares"] += shares
                    holdings[sid]["cost"] = (old_cost + cost) / holdings[sid]["shares"]
                else:
                    holdings[sid] = {"shares": shares, "cost": price, "anon": anon_id}

                trade_log.append(f"{year}-{month:02d} 买入 {anon_id} {shares}股 @¥{price:.2f} ({sig_level})")

        # 前进
        if month >= 12:
            month = 1; year += 1
        else:
            month += 1

    # 最终结算
    print("=== 交易记录 ===")
    for log in trade_log:
        print(f"  {log}")

    # 最终市值
    final_signals = get_month_signals(2025, 12, anon_map=anon_map, industry_map=industry_map)
    final_portfolio = 0
    print("\n=== 最终持仓 ===")
    for sid, h in holdings.items():
        price = h["cost"]
        for anon_id, sdata in (final_signals or {}).items():
            if sdata.get("sid") == sid:
                price = sdata.get("price", h["cost"])
                break
        value = h["shares"] * price
        pnl = (price / h["cost"] - 1) * 100
        final_portfolio += value
        print(f"  {h['anon']}: {h['shares']}股 成本¥{h['cost']:.2f} 现价¥{price:.2f} 盈亏{pnl:+.1f}% 市值¥{value:,.0f}")

    final_total = cash + final_portfolio
    final_pnl = (final_total / INITIAL_CAPITAL - 1) * 100

    print(f"\n=== 最终结果 ===")
    print(f"  初始资金: ¥{INITIAL_CAPITAL:,}")
    print(f"  可用资金: ¥{cash:,.0f}")
    print(f"  持仓市值: ¥{final_portfolio:,.0f}")
    print(f"  总资产:   ¥{final_total:,.0f}")
    print(f"  总收益:   {final_pnl:+.1f}%")
    print(f"  交易笔数: {len(trade_log)}")

    # 每年资产变化
    print(f"\n=== 年度资产 ===")
    for mv in monthly_values:
        if mv["date"].endswith("-12") or mv["date"].endswith("-01"):
            pct = (mv["total"] / INITIAL_CAPITAL - 1) * 100
            print(f"  {mv['date']}: ¥{mv['total']:,.0f} ({pct:+.1f}%)")

    return final_total, final_pnl


if __name__ == "__main__":
    # 随机选10-15年之间的起点
    start_year = random.randint(2010, 2015)
    start_month = random.randint(1, 12)
    run_backtest(start_year, start_month)
