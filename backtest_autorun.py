"""
选股模型验证回测 - 严格按信号买卖，不做任何主观判断

规则：
- 买入：收到重仓/中仓/轻仓买入信号就买，一次到位，不加仓
- 持有：不动，不因价格波动做任何操作
- 卖出：只在下列情况卖出
  1. 信号变为"适当卖出"/"大量卖出"（明显高估）
  2. 护城河松动（基本面恶化，持续校验）
  3. 退市
- 含真实 A 股交易费用、分红复利

目的：验证选股模型在"买入后长期持有"场景下的准确性，
      看信号是否精准，有没有错漏。
"""

import json
import os
import random
import sys

sys.stdout.reconfigure(encoding='utf-8')

from backtest_engine import (
    get_month_signals,
    generate_anonymous_map,
    load_stock_list,
    check_moat,
    _roe_historical_avg,
    _get_recent_gm,
    get_annual_reports_before,
    get_cash_flow_warnings,
    get_hs300_temperature,
)


# ============================================================
# 1. 参数常量
# ============================================================
# -------- 仓位参数 --------
MAX_HOLDINGS = 10         # 同时最多持有数量
CASH_RESERVE = 0.05       # 预留现金比例

# -------- 首次建仓预算（按信号档位，占可投资金比例）--------
BUDGET_HEAVY = 0.40       # 重仓买入
BUDGET_MEDIUM = 0.20      # 中仓买入
BUDGET_LIGHT = 0.10       # 轻仓买入

# -------- 公司质量门槛 --------
# 好公司：ROE 均值 ≥ 15%（巴菲特合格线），大量卖出才清仓
# 超级好公司：ROE 均值 ≥ 25% + 毛利率 ≥ 40%（茅台式），永不清仓只减仓
GOOD_COMPANY_ROE_THRESHOLD = 15.0
SUPER_GOOD_ROE_THRESHOLD = 25.0
SUPER_GOOD_GM_THRESHOLD = 40.0

# -------- A 股真实交易费用 --------
COMMISSION_RATE = 0.00025  # 佣金：双向万 2.5
COMMISSION_MIN = 5.0       # 佣金最低 5 元
STAMP_TAX_OLD = 0.001      # 印花税旧规则：卖出万十
STAMP_TAX_NEW = 0.0005     # 印花税新规则：2023-08 起万五
SLIPPAGE_RATE = 0.002      # 滑点：实际成交价偏离挂单价 0.2%


# ============================================================
# 2. 公司质量判定
# ============================================================

def is_super_good_company(sid, year, month):
    """
    超级好公司判定（永恒持有豁免）
    条件：近 5 年 ROE 均值 ≥ 25% 且 近 3 年毛利率均值 ≥ 40%
    典型：茅台、五粮液、海天味业、片仔癀、恒瑞医药
    作用：大量卖出信号也不清仓，按档位减仓 30/20/10%
    依据：巴菲特从不因 PE 偏高清仓可口可乐
    """
    roe_avg = _roe_historical_avg(sid, year, month, lookback_years=5)
    if not roe_avg or roe_avg < SUPER_GOOD_ROE_THRESHOLD:
        return False
    reports = get_annual_reports_before(sid, year, month, lookback_years=3)
    gms = [r.get("gross_margin") for r in reports if r.get("gross_margin") is not None]
    if not gms:
        return False
    return sum(gms) / len(gms) >= SUPER_GOOD_GM_THRESHOLD


def _transfer_fee_rate(code, year, month):
    """
    过户费率（沪深两市历史规则）
    - 2022-04-01 起：沪深统一十万分之一（0.00001）
    - 此前：上海 万二（0.00002），深圳不收取
    """
    if (year, month) >= (2022, 4):
        return 0.00001
    # 2022-04 之前
    if code.startswith("6") or code.startswith("9") or code.startswith("688"):
        return 0.00002  # 上海
    return 0.0  # 深圳


def _stamp_tax_rate(year, month):
    """印花税率（2023-08 前后差异）"""
    if (year, month) >= (2023, 8):
        return STAMP_TAX_NEW
    return STAMP_TAX_OLD


def _dividend_tax_rate(hold_months):
    """
    股息红利差别化税率（国家为鼓励长期投资）
    - 持股 > 12 个月：免税
    - 持股 1 ~ 12 个月：10%
    - 持股 < 1 个月：20%
    """
    if hold_months >= 12:
        return 0.0
    if hold_months >= 1:
        return 0.10
    return 0.20


def calc_buy_cost(price, shares, code, year, month):
    """买入总花费（含佣金 + 过户费）"""
    amount = price * shares
    commission = max(amount * COMMISSION_RATE, COMMISSION_MIN)
    transfer = amount * _transfer_fee_rate(code, year, month)
    fee = commission + transfer
    return amount + fee, fee


def calc_sell_revenue(price, shares, code, year, month):
    """卖出实际到手（扣佣金 + 过户费 + 印花税）"""
    amount = price * shares
    commission = max(amount * COMMISSION_RATE, COMMISSION_MIN)
    transfer = amount * _transfer_fee_rate(code, year, month)
    stamp = amount * _stamp_tax_rate(year, month)
    fee = commission + transfer + stamp
    return amount - fee, fee


def months_between(y1, m1, y2, m2):
    return (y2 - y1) * 12 + (m2 - m1)


def analyze_swaps(swap_log, final_signals):
    """
    识别换股事件（同一月份既卖出又买入）并评估换股质量
    评估标准：对比卖出的股票和买入的股票在换股后到回测结束的价格变化
      - 买入的涨幅 > 卖出的后续涨幅 → 换对了
      - 反之 → 换错了
    不考虑后续分红和再次卖出，只看纯价格走势
    """
    by_month = {}
    for evt in swap_log:
        y, m = evt[0], evt[1]
        by_month.setdefault((y, m), []).append(evt)

    # 2025-12 每只股票的终值价格（按匿名编号）
    final_price = {}
    for anon, s in (final_signals or {}).items():
        final_price[anon] = s.get("price", 0) or 0

    swap_events = []
    for (y, m), events in sorted(by_month.items()):
        sells = [e for e in events if e[2] == "sell"]
        buys = [e for e in events if e[2] == "buy"]
        if sells and buys:
            for se in sells:
                for be in buys:
                    sell_anon, sell_price, sell_reason = se[3], se[4], se[6]
                    buy_anon, buy_price, buy_sig = be[3], be[4], be[6]
                    s_final = final_price.get(sell_anon, 0)
                    b_final = final_price.get(buy_anon, 0)
                    if s_final > 0 and b_final > 0:
                        sell_change = (s_final / sell_price - 1) * 100
                        buy_change = (b_final / buy_price - 1) * 100
                        diff = buy_change - sell_change
                        if diff > 5:
                            verdict = "✓ 换对"
                        elif diff < -5:
                            verdict = "✗ 换错"
                        else:
                            verdict = "≈ 持平"
                    else:
                        sell_change = 0
                        buy_change = 0
                        verdict = "- 无法评估"
                    swap_events.append({
                        "date": f"{y}-{m:02d}",
                        "sell_anon": sell_anon,
                        "sell_price": sell_price,
                        "sell_reason": sell_reason,
                        "sell_final": s_final,
                        "sell_change": sell_change,
                        "buy_anon": buy_anon,
                        "buy_price": buy_price,
                        "buy_sig": buy_sig,
                        "buy_final": b_final,
                        "buy_change": buy_change,
                        "verdict": verdict,
                    })
    return swap_events


def apply_dividends(holdings, year, month, trade_log):
    """
    分红现金入账，按持股时长差别化扣税：
    - 持股 > 12 月：免税
    - 1 ~ 12 月：10%
    - < 1 月：20%
    """
    month_str = f"{year}-{month:02d}"
    total_div = 0
    for sid, h in list(holdings.items()):
        raw_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "backtest_data", f"raw_{sid}.json"
        )
        if not os.path.exists(raw_path):
            continue
        with open(raw_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for div in raw.get("dividends", []):
            div_date = str(div.get("date", ""))[:7]
            if div_date == month_str and div.get("status") != "预案":
                div_per_10 = div.get("div_per_10", 0) or 0
                if div_per_10 > 0:
                    hold_m = months_between(h["buy_year"], h["buy_month"], year, month)
                    tax_rate = _dividend_tax_rate(hold_m)
                    gross = (div_per_10 / 10) * h["shares"]
                    net = gross * (1 - tax_rate)
                    total_div += net
                    tax_note = "免税" if tax_rate == 0 else f"扣{int(tax_rate*100)}%税"
                    trade_log.append(
                        f"{month_str} 分红 {h['anon']} 每股¥{div_per_10/10:.3f} "
                        f"到手¥{net:.0f}（持{hold_m}月·{tax_note}）"
                    )
    return total_div


def _combine_temperatures(pool_temp, index_temp):
    """
    融合股票池温度计和沪深 300 指数温度计
    取两者平均后四舍五入到最近档位（偏保守）
    返回：-2（极冷）~ +2（极热）
    """
    avg = (pool_temp + index_temp) / 2
    if avg >= 1.5: return 2
    if avg >= 0.5: return 1
    if avg <= -1.5: return -2
    if avg <= -0.5: return -1
    return 0


def _check_cash_flow_warnings(holdings, year, month, trade_log):
    """
    消费龙头现金流警示（已豁免规则 7 但需重点关注）
    只在 4 月（年报披露期）检查，且只在状态变化时输出
    直接修改 holdings[sid]["cf_warned"] 状态
    """
    if month != 4:
        return
    for sid, h in holdings.items():
        warnings = get_cash_flow_warnings(sid, year, month)
        was_warned = h.get("cf_warned", False)
        if warnings and not was_warned:
            for w in warnings:
                trade_log.append(
                    f"{year}-{month:02d} ⚠重点关注 {h['anon']}：{w}"
                )
            h["cf_warned"] = True
        elif not warnings and was_warned:
            trade_log.append(
                f"{year}-{month:02d} ✓警示解除 {h['anon']}：现金流恢复正常"
            )
            h["cf_warned"] = False


def get_market_temperature(market_pe_history):
    """
    返回市场温度（-2 极冷 / -1 偏冷 / 0 正常 / 1 偏热 / 2 极热）
    基于历史全股票池中位数 PE 的分位。
    至少需要 12 个月的历史才能判定，否则返回 0。

    极热 (2)：当前中位数 PE 高于历史 80% 分位
    偏热 (1)：高于 65% 分位
    偏冷 (-1)：低于 35% 分位
    极冷 (-2)：低于 20% 分位

    依据：巴菲特/芒格"别人贪婪时我恐惧，别人恐惧时我贪婪"
    """
    if len(market_pe_history) < 12:
        return 0
    current = market_pe_history[-1]
    sorted_hist = sorted(market_pe_history)
    n = len(sorted_hist)
    pct_80 = sorted_hist[int(n * 0.80)]
    pct_65 = sorted_hist[int(n * 0.65)]
    pct_35 = sorted_hist[int(n * 0.35)]
    pct_20 = sorted_hist[int(n * 0.20)]
    if current >= pct_80:
        return 2
    if current >= pct_65:
        return 1
    if current <= pct_20:
        return -2
    if current <= pct_35:
        return -1
    return 0


def compute_market_pe_median(signals):
    """计算当前月份全股票池的市盈率中位数"""
    pes = [
        s.get("pe_ttm")
        for s in signals.values()
        if s.get("pe_ttm") and s.get("pe_ttm") > 0
    ]
    if len(pes) < 5:
        return None
    pes.sort()
    return pes[len(pes) // 2]


def run_backtest(start_year, start_month, initial_capital=100000, verbose=True):
    """严格按选股模型执行，返回统计结果"""
    stocks = load_stock_list()
    anon_map = generate_anonymous_map(list(stocks.keys()), seed=42)

    cash = float(initial_capital)
    holdings = {}  # {sid: {shares, cost, anon, buy_year, buy_month}}
    trade_log = []
    swap_log = []  # 按月记录所有买卖，用于识别换股事件 (year, month, action, anon, price, cash_flow, note)
    total_fees = 0
    total_dividends = 0
    monthly_values = []
    market_pe_history = []  # 历史中位数 PE 序列（市场温度计）

    year, month = start_year, start_month
    while year < 2025 or (year == 2025 and month <= 12):
        signals = get_month_signals(year, month, anon_map=anon_map, industry_map={})
        if not signals:
            if month >= 12:
                month = 1; year += 1
            else:
                month += 1
            continue

        # ---- 0. 双温度计融合（股票池温度 + 沪深300指数温度）----
        current_market_pe = compute_market_pe_median(signals)
        if current_market_pe is not None:
            market_pe_history.append(current_market_pe)
        pool_temp = get_market_temperature(market_pe_history)
        index_temp = get_hs300_temperature(year, month)
        market_temp = _combine_temperatures(pool_temp, index_temp)

        # ---- 1. 分红入账 + 消费龙头现金流警示 ----
        div_cash = apply_dividends(holdings, year, month, trade_log)
        cash += div_cash
        total_dividends += div_cash
        _check_cash_flow_warnings(holdings, year, month, trade_log)

        # 2. 计算当前总资产
        portfolio_value = 0
        for sid, h in holdings.items():
            price = h["cost"]
            for a, sdata in signals.items():
                if sdata.get("sid") == sid:
                    p = sdata.get("price", 0) or 0
                    if p > 0:
                        price = p
                    break
            portfolio_value += h["shares"] * price
        total = cash + portfolio_value
        monthly_values.append({"date": f"{year}-{month:02d}", "total": total})

        # ---- 3. 卖出检查（严格按模型信号）----
        # 触发条件：退市、大量/中仓/适当/关注卖出信号、护城河松动
        # 决策规则：
        #   超级好公司（ROE均值≥25%+毛利≥40%）：按档位减仓（30/20/10%），永不清仓
        #   好公司（ROE均值≥15%）：只在大量卖出时清仓
        #   普通公司：大量卖出 + 中仓卖出 都清仓
        #   市场极热：所有持仓额外系统性减仓 25%（每年最多一次）
        sids_to_sell = []

        # 市场极热系统性减仓（每持仓每年一次）
        if market_temp == 2:
            for sid, h in list(holdings.items()):
                if h.get("sys_reduce_year") == year:
                    continue  # 今年已经减过了
                shares_cut = (h["shares"] // 400) * 100  # 减仓 25%（向下取整到100股）
                if shares_cut >= 100:
                    # 构造一个"市场极热"的虚拟卖出条目
                    sdata_match = None
                    for a, sdata in signals.items():
                        if sdata.get("sid") == sid:
                            sdata_match = sdata
                            break
                    if sdata_match:
                        sids_to_sell.append(
                            (sid, sdata_match, "市场极热系统性减仓25%(别人贪婪我恐惧)", shares_cut)
                        )
                        h["sys_reduce_year"] = year

        for sid, h in list(holdings.items()):
            sdata_match = None
            for a, sdata in signals.items():
                if sdata.get("sid") == sid:
                    sdata_match = sdata
                    break
            if not sdata_match:
                continue

            sig = sdata_match.get("signal", "")
            price = sdata_match.get("price", 0) or 0

            # 退市 → 清仓
            if sig == "delisted" or price <= 0:
                sids_to_sell.append((sid, sdata_match, "退市清仓", None))
                continue

            # 卖出信号分级处理（超级好公司减仓 / 好公司清仓 / 普通公司清仓）
            if sig in ("sell_heavy", "sell_medium", "sell_light", "sell_watch"):
                holding = holdings[sid]
                super_good = is_super_good_company(sid, year, month)
                hist_avg_roe = _roe_historical_avg(sid, year, month)
                is_good_company = (
                    hist_avg_roe is not None
                    and hist_avg_roe >= GOOD_COMPANY_ROE_THRESHOLD
                )

                if super_good:
                    # 超级好公司：按档位减仓（保守版）
                    reduce_ratio = {
                        "sell_heavy": 0.30,
                        "sell_medium": 0.20,
                        "sell_light": 0.10,
                        "sell_watch": 0.0,
                    }.get(sig, 0.0)
                    if reduce_ratio > 0:
                        shares_to_sell = int(holding["shares"] * reduce_ratio / 100) * 100
                        if shares_to_sell >= 100:
                            reason_map = {
                                "sell_heavy": "超级好公司·大量卖出→减仓30%",
                                "sell_medium": "超级好公司·中仓卖出→减仓20%",
                                "sell_light": "超级好公司·适当卖出→减仓10%",
                            }
                            sids_to_sell.append(
                                (sid, sdata_match, reason_map[sig], shares_to_sell)
                            )
                elif is_good_company:
                    # 好公司：只在大量卖出时清仓，其他不动
                    if sig == "sell_heavy":
                        sids_to_sell.append(
                            (sid, sdata_match, "好公司·大量卖出→清仓", None)
                        )
                else:
                    # 普通公司：大量卖出 + 中仓卖出 都清仓
                    if sig == "sell_heavy":
                        sids_to_sell.append(
                            (sid, sdata_match, "大量卖出(远超行业上限)", None)
                        )
                    elif sig == "sell_medium":
                        sids_to_sell.append(
                            (sid, sdata_match, "适当卖出(明显偏高)", None)
                        )
                continue

            # 护城河松动 → 清仓（兜底检查，防止基本面恶化）
            is_intact, probs = check_moat(sid, year, month)
            if not is_intact:
                reason = f"护城河松动({'; '.join(probs[:2])})"
                sids_to_sell.append((sid, sdata_match, reason, None))
                continue

        for sid, sdata, reason, sell_shares in sids_to_sell:
            h = holdings[sid]
            # None 表示全部卖出
            if sell_shares is None or sell_shares > h["shares"]:
                sell_shares = h["shares"]
            quote_price = sdata.get("price", 0) or h["cost"]
            if quote_price <= 0:
                # 退市无价，直接清空
                del holdings[sid]
                trade_log.append(f"{year}-{month:02d} 清仓 {h['anon']} {h['shares']}股 (退市无价)")
                continue
            # 滑点：卖出实际成交价低于挂单价 0.2%
            exec_price = quote_price * (1 - SLIPPAGE_RATE)
            revenue, fee = calc_sell_revenue(exec_price, sell_shares, h["code"], year, month)
            cash += revenue
            total_fees += fee
            pnl_pct = (exec_price / h["cost"] - 1) * 100 if h["cost"] > 0 else 0
            trade_log.append(
                f"{year}-{month:02d} 卖出 {h['anon']} {sell_shares}股 @¥{exec_price:.2f} "
                f"到手¥{revenue:.0f} 盈亏{pnl_pct:+.1f}% ({reason})"
            )
            # 换股事件识别用：记录卖出方向
            swap_log.append((year, month, "sell", h["anon"], exec_price, revenue, reason))
            # 减仓（部分卖出保留剩余持仓）
            h["shares"] -= sell_shares
            if h["shares"] <= 0:
                del holdings[sid]

        # ---- 4. 买入检查 ----
        # 排序优先级：信号强度 > 十年王者 > 简单生意 > 回购加分 > 高评分
        # 预算按信号档位分配（重仓40% / 中仓20% / 轻仓10% × 温度系数）
        # 兜底：算出不够 100 股但手头钱够，买 1 手（小资金友好）
        investable_cash = cash * (1 - CASH_RESERVE)

        def _buy_priority(item):
            _, s = item
            sig_rank = {"buy_heavy": 0, "buy_medium": 1, "buy_light": 2}.get(
                s.get("signal", ""), 9
            )
            # 十年王者优先（巴菲特"只买王者"）
            king_rank = 0 if s.get("is_10y_king") else 1
            comp_rank = {"simple": 0, "medium": 1, "complex": 2}.get(
                s.get("complexity", "medium"), 1
            )
            buyback_desc = -(s.get("buyback_score") or 0)  # 回购分高的排前
            score_desc = -(s.get("score") or 0)            # 评分高的排前
            return (sig_rank, king_rank, comp_rank, buyback_desc, score_desc)

        for anon, sdata in sorted(signals.items(), key=_buy_priority):
            if len(holdings) >= MAX_HOLDINGS:
                break
            sig = sdata.get("signal", "")
            sid = sdata["sid"]
            price = sdata.get("price", 0) or 0

            if sig not in ("buy_heavy", "buy_medium", "buy_light"):
                continue
            if sid in holdings:
                continue
            if price <= 0:
                continue

            # 按信号强度分配预算 × 温度系数（别人贪婪时我恐惧）
            # 极冷 +30%、偏冷 +15%、正常 0%、偏热 -20%、极热 -40%
            temp_multiplier = {-2: 1.30, -1: 1.15, 0: 1.0, 1: 0.80, 2: 0.60}.get(market_temp, 1.0)
            if sig == "buy_heavy":
                budget = investable_cash * BUDGET_HEAVY * temp_multiplier
                sig_name = "重仓买入"
            elif sig == "buy_medium":
                budget = investable_cash * BUDGET_MEDIUM * temp_multiplier
                sig_name = "中仓买入"
            else:
                budget = investable_cash * BUDGET_LIGHT * temp_multiplier
                sig_name = "轻仓买入"

            exec_price = price * (1 + SLIPPAGE_RATE)  # 买入滑点 +0.2%
            shares = int(budget / exec_price // 100) * 100
            code = stocks[sid]["code"]

            # 小资金兜底：比例不够 100 股但手头钱够就买 1 手
            if shares < 100:
                tentative_cost, _ = calc_buy_cost(exec_price, 100, code, year, month)
                if cash >= tentative_cost:
                    shares = 100
                else:
                    continue

            buy_cost, fee = calc_buy_cost(exec_price, shares, code, year, month)
            if buy_cost > cash:
                continue

            cash -= buy_cost
            total_fees += fee
            investable_cash -= buy_cost
            holdings[sid] = {
                "shares": shares, "cost": exec_price, "anon": anon, "code": code,
                "buy_year": year, "buy_month": month,
            }
            trade_log.append(
                f"{year}-{month:02d} 买入 {anon} {shares}股 @¥{exec_price:.2f} "
                f"花费¥{buy_cost:.0f} ({sig_name})"
            )
            swap_log.append((year, month, "buy", anon, exec_price, buy_cost, sig_name))

        # 前进
        if month >= 12:
            month = 1; year += 1
        else:
            month += 1

    # ============ 最终结算 ============
    final_signals = get_month_signals(2025, 12, anon_map=anon_map, industry_map={})
    final_portfolio = 0
    holding_summary = []
    for sid, h in holdings.items():
        price = h["cost"]
        for a, sdata in (final_signals or {}).items():
            if sdata.get("sid") == sid:
                p = sdata.get("price", 0) or 0
                if p > 0:
                    price = p
                break
        value = h["shares"] * price
        pnl = (price / h["cost"] - 1) * 100
        hold_m = months_between(h["buy_year"], h["buy_month"], 2025, 12)
        final_portfolio += value
        holding_summary.append({
            "anon": h["anon"], "shares": h["shares"],
            "cost": h["cost"], "price": price,
            "pnl_pct": pnl, "hold_months": hold_m, "value": value,
        })

    final_total = cash + final_portfolio
    final_pnl = (final_total / initial_capital - 1) * 100

    # 换股事件分析
    swap_events = analyze_swaps(swap_log, final_signals)

    if verbose:
        print(f"\n=== 交易记录（共{len(trade_log)}条） ===")
        for log in trade_log:
            print(f"  {log}")

        print(f"\n=== 最终持仓 ===")
        for h in holding_summary:
            print(f"  {h['anon']}: {h['shares']}股 成本¥{h['cost']:.2f} "
                  f"现价¥{h['price']:.2f} 盈亏{h['pnl_pct']:+.1f}% "
                  f"持{h['hold_months']}月 市值¥{h['value']:,.0f}")

        if swap_events:
            print(f"\n=== 换股事件分析（共{len(swap_events)}次） ===")
            for e in swap_events:
                print(f"  [{e['date']}] 卖 {e['sell_anon']}@¥{e['sell_price']:.2f} → "
                      f"买 {e['buy_anon']}@¥{e['buy_price']:.2f}  {e['verdict']}")
                print(f"     卖出原因: {e['sell_reason']}")
                print(f"     买入信号: {e['buy_sig']}")
                if e['sell_final'] > 0:
                    print(f"     到25-12: 卖出股{e['sell_change']:+.1f}% | 买入股{e['buy_change']:+.1f}%")

        print(f"\n=== 最终结果 ===")
        print(f"  初始资金:   ¥{initial_capital:,}")
        print(f"  可用现金:   ¥{cash:,.0f}")
        print(f"  持仓市值:   ¥{final_portfolio:,.0f}")
        print(f"  总资产:     ¥{final_total:,.0f}")
        print(f"  总收益:     {final_pnl:+.1f}%")
        print(f"  累计分红:   ¥{total_dividends:,.0f}（税后）")
        print(f"  累计手续费: ¥{total_fees:,.1f}")

    return {
        "initial_capital": initial_capital,
        "final_total": final_total,
        "final_pnl": final_pnl,
        "total_dividends": total_dividends,
        "total_fees": total_fees,
        "trade_count": len(trade_log),
        "holdings": holding_summary,
        "cash_left": cash,
        "trade_log": trade_log,
        "swap_events": swap_events,
    }


def _years_between(start_y, start_m, end_y=2025, end_m=12):
    """计算两个年月之间的年数（带小数）"""
    return round(((end_y - start_y) * 12 + (end_m - start_m)) / 12, 1)


def _print_capital_summary(time_label, results, years):
    print(f"\n{'='*95}")
    print(f"  {time_label} 横向对比（测试 {years} 年）")
    print(f"{'='*95}")
    print(f"{'本金':>12} | {'总资产':>14} | {'收益率':>8} | {'年化':>7} | {'分红':>10} | {'手续费':>8} | {'换股':>10}")
    print(f"{'-'*95}")
    for r in results:
        swaps = r.get("swap_events", [])
        right = sum(1 for e in swaps if "换对" in e["verdict"])
        wrong = sum(1 for e in swaps if "换错" in e["verdict"])
        flat = sum(1 for e in swaps if "持平" in e["verdict"])
        swap_stat = f"{right}对{wrong}错{flat}平" if swaps else "-"
        # 年化收益率：(1+总收益)^(1/年数) - 1
        if years > 0 and r['final_total'] > 0:
            annual = ((r['final_total'] / r['initial_capital']) ** (1 / years) - 1) * 100
        else:
            annual = 0
        print(f"¥{r['initial_capital']:>11,} | ¥{r['final_total']:>13,.0f} | "
              f"{r['final_pnl']:>+7.1f}% | {annual:>+6.1f}% | ¥{r['total_dividends']:>9,.0f} | "
              f"¥{r['total_fees']:>7,.0f} | {swap_stat:>10}")


def run_suite(time_points, capitals, verbose_first=True):
    """跑多个起始时间 × 多档本金的完整套件"""
    all_runs = {}  # {(year, month): (years, [results...])}
    for idx, (sy, sm) in enumerate(time_points):
        years = _years_between(sy, sm)
        print(f"\n\n{'█'*95}")
        print(f"█  起始时间 {sy}-{sm:02d}（测试 {years} 年）")
        print(f"{'█'*95}")
        results = []
        for cap in capitals:
            print(f"\n{'─'*60}")
            print(f"  本金 ¥{cap:,}")
            print(f"{'─'*60}")
            # 只对第一个时间点 + 100万本金输出详细交易日志
            verbose = verbose_first and idx == 0 and cap == capitals[-1]
            r = run_backtest(sy, sm, initial_capital=cap, verbose=verbose)
            results.append(r)
            if not verbose:
                print(f"  收益 {r['final_pnl']:+.1f}% | "
                      f"交易 {r['trade_count']} 笔 | "
                      f"分红 ¥{r['total_dividends']:,.0f} | "
                      f"手续费 ¥{r['total_fees']:,.0f}")
        _print_capital_summary(f"起始 {sy}-{sm:02d}", results, years)
        all_runs[(sy, sm)] = (years, results)

    # 总览
    print(f"\n\n{'='*95}")
    print(f"  总览：{len(time_points)} 个起始时间 × {len(capitals)} 档本金")
    print(f"{'='*95}")
    print(f"{'起始':>8} | {'年数':>5} | " + " | ".join(f"¥{c:>10,}" for c in capitals))
    print(f"{'-'*95}")
    for (sy, sm), (years, runs) in all_runs.items():
        row = f"{sy}-{sm:02d} | {years:>5.1f} | " + " | ".join(
            f"{r['final_pnl']:>+9.1f}%" for r in runs
        )
        print(row)

    # 均值 + 最低/最高 统计
    print(f"\n{'-'*95}")
    print(f"{'统计':>8} | {'':>5} | " + " | ".join(f"¥{c:>10,}" for c in capitals))
    print(f"{'-'*95}")
    for stat_name, stat_fn in [("均值", lambda xs: sum(xs)/len(xs)),
                                ("最低", min),
                                ("最高", max)]:
        cells = []
        for i, cap in enumerate(capitals):
            pnls = [runs[i]['final_pnl'] for _, (_, runs) in all_runs.items()]
            cells.append(f"{stat_fn(pnls):>+9.1f}%")
        print(f"{stat_name:>8} | {'':>5} | " + " | ".join(cells))


if __name__ == "__main__":
    capitals = [10000, 100000, 500000, 1000000]

    # 支持命令行参数：
    #   python backtest_autorun.py 2019 11       → 单个起始时间
    #   python backtest_autorun.py --suite 10    → 10 个随机起始时间
    #   python backtest_autorun.py               → 默认 3 个随机起始时间
    if len(sys.argv) >= 3 and sys.argv[1] != "--suite":
        sy = int(sys.argv[1])
        sm = int(sys.argv[2])
        run_suite([(sy, sm)], capitals)
    else:
        # 支持命令行指定时间点数量，默认 3
        n_points = 3
        if len(sys.argv) >= 3 and sys.argv[1] == "--suite":
            n_points = int(sys.argv[2])

        random.seed()
        time_points = []
        used = set()
        while len(time_points) < n_points:
            # 数据范围 2001-2025，为了保证至少 5 年回测窗口，起点取 2001-2020
            y = random.randint(2001, 2020)
            m = random.randint(1, 12)
            if (y, m) not in used:
                time_points.append((y, m))
                used.add((y, m))
        # 按时间排序便于阅读
        time_points.sort()
        print(f"{'='*95}")
        print(f"  选股模型验证回测")
        print(f"  起始时间（{n_points}个）: {', '.join(f'{y}-{m:02d}' for y,m in time_points)}")
        print(f"  规则: 严格按信号买入，买后除非卖出/护城河松动/退市，否则不动")
        print(f"{'='*95}")
        run_suite(time_points, capitals, verbose_first=(n_points <= 3))
