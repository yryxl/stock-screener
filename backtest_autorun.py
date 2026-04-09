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
MAX_POSITION_PCT = 0.35   # 单只最多占35%
MAX_TOTAL_INVESTED = 0.85 # 总持仓不超过85%
MIN_HOLD_MONTHS = 12      # 最少持有12个月才考虑卖出
MAX_HOLDINGS = 5          # 打卡选股：最多同时持有5只（巴菲特：集中投资少数好公司）
LOSS_STOP_PCT = -30       # 持有超3年且亏损超30%触发护城河消失警告
LOSS_STOP_MONTHS = 36     # 3年

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
    total_dividends = 0
    monthly_values = []

    year, month = start_year, start_month

    while year < 2025 or (year == 2025 and month <= 12):
        signals = get_month_signals(year, month, anon_map=anon_map, industry_map=industry_map)
        if not signals:
            if month >= 12: month = 1; year += 1
            else: month += 1
            continue

        # 分红复利：检查当月是否有分红，有则现金入账
        month_str = f"{year}-{month:02d}"
        for sid, h in list(holdings.items()):
            # 从raw数据读取分红记录
            raw_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_data", f"raw_{sid}.json")
            if os.path.exists(raw_path):
                with open(raw_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for div in raw.get("dividends", []):
                    div_date = str(div.get("date", ""))[:7]
                    if div_date == month_str and div.get("status") != "预案":
                        div_per_10 = div.get("div_per_10", 0) or 0
                        if div_per_10 > 0:
                            dividend_cash = (div_per_10 / 10) * h["shares"]
                            # 扣20%个人所得税（持有<1年）
                            dividend_cash *= 0.8
                            cash += dividend_cash
                            total_dividends += dividend_cash
                            trade_log.append(f"{month_str} 分红 {h['anon']} 每股{div_per_10/10:.3f}元 到手¥{dividend_cash:.0f}（税后）")

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
        # 卖出逻辑（巴菲特式）
        # 1. 退市→必须卖
        # 2. 严重高估+持有超1年+近6个月没加仓→减仓1/2
        # 3. 持有超3年+亏损超30%→护城河可能消失，清仓
        # 不因轻微高估卖出（巴菲特从不因PE偏高卖可口可乐）
        # =============================================
        sids_to_sell = []
        for sid, h in list(holdings.items()):
            for anon_id, sdata in signals.items():
                if sdata.get("sid") == sid:
                    sig = sdata.get("signal", "")
                    hold_months = months_between(h["buy_year"], h["buy_month"], year, month)
                    months_since_add = hold_months - h.get("last_add_month", 0)
                    price = sdata.get("price", h["cost"])
                    pnl_pct = (price / h["cost"] - 1) * 100 if h["cost"] > 0 else 0

                    if sig == "delisted":
                        sids_to_sell.append((sid, sdata, "退市清仓", h["shares"]))

                    elif sig == "sell_heavy" and hold_months >= MIN_HOLD_MONTHS and months_since_add >= 6:
                        # 严重高估+持有超1年+近6月没加仓→减半
                        sell_n = max(h["shares"] // 2, 100) if h["shares"] > 100 else h["shares"]
                        sids_to_sell.append((sid, sdata, "严重高估减仓", sell_n))

                    elif hold_months >= LOSS_STOP_MONTHS and pnl_pct <= LOSS_STOP_PCT:
                        # 持有超3年且亏损超30%→护城河可能消失
                        sids_to_sell.append((sid, sdata, "护城河消失止损", h["shares"]))

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
        # 买入逻辑（不犯错前提下尽可能高收益）
        # 首次买入：重仓/中仓/轻仓都可以建仓
        # 加仓：已持有+信号仍是买入+持有超3个月+仓位未满
        # =============================================
        investable_cash = cash - total * (1 - MAX_TOTAL_INVESTED)
        if investable_cash < 0:
            investable_cash = 0

        for anon_id, sdata in signals.items():
            sig = sdata.get("signal", "")
            sid = sdata["sid"]
            price = sdata.get("price", 0)
            if price <= 0:
                continue

            already_held = sid in holdings
            is_buy_signal = sig in ("buy_heavy", "buy_medium", "buy_light")

            if not is_buy_signal:
                continue

            # ---- 首次买入：重仓/中仓/轻仓都可建仓 ----
            if not already_held:
                # 打卡选股：最多同时持有MAX_HOLDINGS只
                if len(holdings) >= MAX_HOLDINGS:
                    continue

                if investable_cash < price * 100 + COMMISSION_MIN:
                    continue
                if price * 100 / max(total, 1) > MAX_POSITION_PCT:
                    continue

                # 不同信号，首次建仓比例不同
                if sig == "buy_heavy":
                    budget = investable_cash * 0.25  # 极度低估，大仓
                elif sig == "buy_medium":
                    budget = investable_cash * 0.12  # 明显低估，中仓
                else:  # buy_light
                    budget = investable_cash * 0.06  # 轻度低估，小仓试探

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
                    "last_add_month": 0,
                }
                trade_log.append(f"{year}-{month:02d} 首次买入 {anon_id} {shares}股 @¥{price:.2f} 花费¥{buy_cost:.0f} 手续费¥{fee:.1f} ({sig})")

            # ---- 加仓：已持有+信号仍是buy+间隔≥3个月+仓位未满 ----
            else:
                h = holdings[sid]
                hold_months = months_between(h["buy_year"], h["buy_month"], year, month)
                months_since_last_add = hold_months - h.get("last_add_month", 0)

                # 加仓条件：持有>3个月 + 上次加仓间隔>3个月 + 仓位未超限
                if hold_months < 3 or months_since_last_add < 3:
                    continue

                current_value = h["shares"] * price
                if current_value / max(total, 1) >= MAX_POSITION_PCT:
                    continue  # 仓位已满，不加

                if investable_cash < price * 100 + COMMISSION_MIN:
                    continue

                # 加仓力度：当前信号越强加越多
                if sig == "buy_heavy":
                    budget = investable_cash * 0.15
                elif sig == "buy_medium":
                    budget = investable_cash * 0.08
                else:  # 轻仓买入
                    budget = investable_cash * 0.05

                shares = int(budget / price // 100) * 100
                if shares < 100:
                    continue

                buy_cost, fee = calc_buy_cost(price, shares)
                if buy_cost > cash:
                    continue

                cash -= buy_cost
                total_fees += fee
                investable_cash -= buy_cost

                old_total = h["shares"] * h["cost"]
                h["shares"] += shares
                h["cost"] = (old_total + price * shares) / h["shares"]
                h["last_add_month"] = hold_months

                trade_log.append(f"{year}-{month:02d} 加仓 {anon_id} +{shares}股 @¥{price:.2f} 累计{h['shares']}股 手续费¥{fee:.1f} ({sig})")

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
    print(f"  累计分红:   ¥{total_dividends:,.0f}（税后）")
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
