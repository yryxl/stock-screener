"""
自动回测脚本 - 按巴菲特/芒格理念买卖
含真实A股交易费用

巴菲特核心理念：
1. 买入后长期持有，不频繁交易
2. 只在极度便宜时重仓，合理时轻仓
3. 只在护城河消失/严重高估时卖出
4. 宁可错过，不可犯错
"""

import json
import os
import random
import sys

sys.stdout.reconfigure(encoding='utf-8')

from backtest_engine import get_month_signals, generate_anonymous_map, load_stock_list

INITIAL_CAPITAL = 10000
MAX_POSITION_PCT = 0.30   # 单只最多占30%
MAX_TOTAL_INVESTED = 0.80 # 总持仓不超过80%（留20%现金应对机会）
MIN_HOLD_MONTHS = 12      # 最少持有12个月才考虑卖出（巴菲特：不愿持有10年就别持有10分钟）

# A股真实交易费用
COMMISSION_RATE = 0.00025
COMMISSION_MIN = 5.0
TRANSFER_FEE = 0.00001
STAMP_TAX_OLD = 0.001
STAMP_TAX_NEW = 0.0005


def calc_buy_cost(price, shares):
    """买入总花费（含手续费）"""
    amount = price * shares
    commission = max(amount * COMMISSION_RATE, COMMISSION_MIN)
    transfer = amount * TRANSFER_FEE
    total_fee = commission + transfer
    return amount + total_fee, total_fee


def calc_sell_revenue(price, shares, year, month):
    """卖出实际到手（扣手续费）"""
    amount = price * shares
    commission = max(amount * COMMISSION_RATE, COMMISSION_MIN)
    transfer = amount * TRANSFER_FEE
    if year > 2023 or (year == 2023 and month >= 8):
        stamp = amount * STAMP_TAX_NEW
    else:
        stamp = amount * STAMP_TAX_OLD
    total_fee = commission + transfer + stamp
    return amount - total_fee, total_fee


def months_between(y1, m1, y2, m2):
    return (y2 - y1) * 12 + (m2 - m1)


def run_backtest(start_year, start_month):
    print(f"=== 巴菲特式回测 起始:{start_year}-{start_month:02d} 资金:¥{INITIAL_CAPITAL:,} ===\n")

    stocks = load_stock_list()
    anon_map = generate_anonymous_map(list(stocks.keys()), seed=42)
    industry_map = {}

    cash = INITIAL_CAPITAL
    holdings = {}  # {sid: {"shares", "cost", "anon", "buy_year", "buy_month"}}
    trade_log = []
    total_fees = 0
    monthly_values = []

    year, month = start_year, start_month

    while year < 2025 or (year == 2025 and month <= 12):
        signals = get_month_signals(year, month, anon_map=anon_map, industry_map=industry_map)
        if not signals:
            if month >= 12: month = 1; year += 1
            else: month += 1
            continue

        # 当前总资产
        portfolio_value = 0
        for sid, h in holdings.items():
            price = h["cost"]
            for anon_id, sdata in signals.items():
                if sdata.get("sid") == sid:
                    price = sdata.get("price", h["cost"])
                    break
            portfolio_value += h["shares"] * price
        total = cash + portfolio_value
        monthly_values.append({"date": f"{year}-{month:02d}", "total": total})

        # =============================================
        # 卖出逻辑（巴菲特式：极少卖出）
        # 只在以下情况卖：
        # 1. 严重高估（sell_heavy）且持有超1年
        # 2. 退市/基本面恶化
        # 不因为轻微高估就卖（巴菲特从不因PE偏高卖可口可乐）
        # =============================================
        sids_to_sell = []
        for sid, h in list(holdings.items()):
            for anon_id, sdata in signals.items():
                if sdata.get("sid") == sid:
                    sig = sdata.get("signal", "")
                    hold_months = months_between(h["buy_year"], h["buy_month"], year, month)

                    if sig == "delisted":
                        # 退市必须卖（不得不）
                        sids_to_sell.append((sid, sdata, "delisted", h["shares"]))
                    elif sig == "sell_heavy" and hold_months >= MIN_HOLD_MONTHS:
                        # 严重高估+持有超1年→减仓1/2（不全卖，留底仓）
                        sell_n = max(h["shares"] // 2, 100) if h["shares"] > 100 else h["shares"]
                        sids_to_sell.append((sid, sdata, sig, sell_n))
                    # sell_medium/sell_light/sell_watch → 不卖！巴菲特不因小波动卖出
                    break

        for sid, sdata, sig, sell_shares in sids_to_sell:
            h = holdings[sid]
            price = sdata.get("price", h["cost"])
            if price <= 0:
                continue
            revenue, fee = calc_sell_revenue(price, sell_shares, year, month)
            cash += revenue
            total_fees += fee
            h["shares"] -= sell_shares
            trade_log.append(f"{year}-{month:02d} 卖出 {h['anon']} {sell_shares}股 @¥{price:.2f} 到手¥{revenue:.0f} 手续费¥{fee:.1f} ({sig})")
            if h["shares"] <= 0:
                del holdings[sid]

        # =============================================
        # 买入逻辑（巴菲特式：耐心等待好价格）
        # 1. 只买buy_heavy和buy_medium（极度/明显低估才动手）
        # 2. 分批建仓，不一次all in
        # 3. 已持有的不加仓（避免频繁交易）
        # 4. 留至少20%现金
        # =============================================
        investable_cash = cash - total * (1 - MAX_TOTAL_INVESTED)
        if investable_cash < 0:
            investable_cash = 0

        for anon_id, sdata in signals.items():
            sig = sdata.get("signal", "")
            if sig not in ("buy_heavy", "buy_medium"):
                continue  # 轻仓/关注不买，等更好价格

            sid = sdata["sid"]
            price = sdata.get("price", 0)
            if price <= 0:
                continue

            # 已持有的不重复买（减少交易次数）
            if sid in holdings:
                continue

            # 资金检查
            if investable_cash < price * 100 + COMMISSION_MIN:
                continue

            # 仓位控制
            if price * 100 / max(total, 1) > MAX_POSITION_PCT:
                continue

            # 计算买入股数
            if sig == "buy_heavy":
                budget = investable_cash * 0.30  # 极度低估用30%可投资金
            else:
                budget = investable_cash * 0.15  # 明显低估用15%

            shares = int(budget / price // 100) * 100
            if shares < 100:
                continue

            buy_cost, fee = calc_buy_cost(price, shares)
            if buy_cost > cash:
                continue

            cash -= buy_cost
            total_fees += fee
            investable_cash -= buy_cost

            holdings[sid] = {
                "shares": shares, "cost": price, "anon": anon_id,
                "buy_year": year, "buy_month": month,
            }
            trade_log.append(f"{year}-{month:02d} 买入 {anon_id} {shares}股 @¥{price:.2f} 花费¥{buy_cost:.0f} 手续费¥{fee:.1f} ({sig})")

        # 前进
        if month >= 12: month = 1; year += 1
        else: month += 1

    # 最终结算
    print("=== 交易记录 ===")
    for log in trade_log:
        print(f"  {log}")

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
        hold_m = months_between(h["buy_year"], h["buy_month"], 2025, 12)
        final_portfolio += value
        print(f"  {h['anon']}: {h['shares']}股 成本¥{h['cost']:.2f} 现价¥{price:.2f} 盈亏{pnl:+.1f}% 持有{hold_m}月 市值¥{value:,.0f}")

    final_total = cash + final_portfolio
    final_pnl = (final_total / INITIAL_CAPITAL - 1) * 100

    print(f"\n=== 最终结果 ===")
    print(f"  初始资金:   ¥{INITIAL_CAPITAL:,}")
    print(f"  可用资金:   ¥{cash:,.0f}")
    print(f"  持仓市值:   ¥{final_portfolio:,.0f}")
    print(f"  总资产:     ¥{final_total:,.0f}")
    print(f"  总收益:     {final_pnl:+.1f}%")
    print(f"  累计手续费: ¥{total_fees:,.1f}")
    print(f"  交易笔数:   {len(trade_log)}")
    print(f"  持仓只数:   {len(holdings)}")

    # 年度资产
    print(f"\n=== 年度资产 ===")
    for mv in monthly_values:
        if mv["date"].endswith("-12"):
            pct = (mv["total"] / INITIAL_CAPITAL - 1) * 100
            print(f"  {mv['date']}: ¥{mv['total']:,.0f} ({pct:+.1f}%)")

    return final_total, final_pnl


if __name__ == "__main__":
    start_year = random.randint(2010, 2015)
    start_month = random.randint(1, 12)
    run_backtest(start_year, start_month)
