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
# 0. 策略模式切换（用于 A/B 对比实验）
# ============================================================
# 控制 backtest_autorun 在三种规则下运行，便于回测对照：
#
#   "baseline" - 当前精简版 D（commit 4320375 起作为 baseline）
#                4 条规则：MAX=10、个股 PE>fair_high×1.2 否决、
#                市场极热统一减仓 25%、保留小资金兜底
#
#   "path_a"   - 取消"市场极热减仓 25%"
#                目的：让模型在大牛市能跟上沪深 300（巴菲特原则
#                "牛市跟平就够"），代价是 2007/2015 顶部不主动卖
#                影响：回测的 if market_temp == 2 减仓块整体跳过
#
#   "path_b"   - 大底加仓 + 暂停卖出
#                目的：在沪深 300 历史 PE 分位 ≤ 15%（market_temp=-2）时，
#                所有买入预算翻倍 + 所有 sell_* 信号忽略
#                这是巴菲特"在恐惧时贪婪"的具体落地
#                影响：买入预算 ×2，卖出全部跳过
#
#   "path_c"   - path_a + path_b 同时启用
#                同时具备"取消牛顶减仓"和"大底加仓+暂停卖出"两种行为
#                A 和 B 是正交的（一个解决牛顶不该卖、一个解决熊底要重仓）
#
# 切换方式：在 if __name__ == "__main__" 处设置 STRATEGY_MODE
# 或 import 时通过 backtest_autorun.STRATEGY_MODE = "path_a" 修改
STRATEGY_MODE = "path_c"  # 默认 path_c（2026-04-12 经 A/B/C 对比实验确认最优）


# ============================================================
# 1. 参数常量
# ============================================================
# -------- 仓位参数 --------
# 回滚到 10 只：A 股环境找不到 5 只真正的"十年王者"，强制集中到 5 只
# 反而在回测里让中期起点大幅退步（-30 pp）。MAX=10 让模型可以把
# 风险分散到 8-10 只中等品质股票，而不是赌前 5 名全对。
# A/B 实验结论见 2026-04-11 commits。
MAX_HOLDINGS = 10         # 同时最多持有数量
CASH_RESERVE = 0.05       # 预留现金比例

# -------- 首次建仓预算（按信号档位，占可投资金比例）--------
BUDGET_HEAVY = 0.40       # 重仓买入
BUDGET_MEDIUM = 0.20      # 中仓买入
BUDGET_LIGHT = 0.10       # 轻仓买入

# -------- 个股 PE 硬否决阈值（替代"大盘温度计硬否决"）--------
# 巴菲特原则：好东西买在贵价就变坏。单只股票的 PE 严重超过行业合理区间
# 上限（fair_high），无论大盘温度如何、无论它有多"优秀"，都拒绝买入。
# 2026-04-11 实验记录：
#   1.5× 太宽：2019-08 时点白酒 PE 30-40 还没触发（45 才拦），
#              最难起点未能拯救
#   1.2× 更紧：白酒 fair_high=30 → PE > 36 拦，真正拦住 2019 白酒顶部
#              银行 fair_high=9 → PE > 10.8 拦
#              医药 fair_high=30 → PE > 36 拦（2021 医药顶部）
INDIVIDUAL_PE_HARD_VETO_MULTIPLIER = 1.2  # 单股 PE > 行业 fair_high × 1.2 硬否决

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
    Tier 1 · 超级好公司（= 永恒持有豁免的最高门槛）

    【好公司 3 档分级体系】—— 详见 docs/REQUIREMENTS.md "好公司分级"
      Tier 1 超级好公司：近 5 年 ROE 均值 ≥ 25% + 近 3 年毛利 ≥ 40%（最严）
                      → 用于"永恒持有豁免"（大量卖出也只减仓不清仓）
      Tier 2 十年王者  ：近 10 年 ROE 均值 ≥ 15% + 7 年 ≥ 15% + 近 2 年不双低
                      → 用于 ROE 门槛豁免（check_10_year_king）
      Tier 3 好公司    ：Tier 2 OR 近5年ROE≥20% + 毛利≥30%（是最常用的"好公司"）
                      → 用于"合理价格买好公司"（is_good_quality_company）
      辅助·合格公司    ：最新 ROE ≥ 15% + 毛利 ≥ 50%（近期表现合格）
                      → 现金流比值短期异常时豁免（原"消费龙头豁免"）

    本函数对应 Tier 1（最严），用于持仓的永恒豁免逻辑。
    典型：茅台、五粮液、海天味业、片仔癀、恒瑞医药
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


def run_backtest(start_year, start_month, initial_capital=100000, verbose=True,
                 subset_ids=None,
                 initial_random_n_stocks=0,
                 initial_random_seed=42,
                 initial_random_cash_pct=0.20,
                 initial_stock_ids=None):
    """
    严格按选股模型执行，返回统计结果

    Args:
      subset_ids: 可选的股票 ID 子集（如 ['S01','S05',...]），
                  用于多批次不重叠抽样回测。None 时用全股票池。
      initial_random_n_stocks: 随机初始化股票数量。
                  0 (默认) = 纯现金启动
                  >0 = 在起始月随机抽 N 只股票等值建仓，模拟"半路接管"
      initial_random_seed: 随机种子，固定后结果可复现
      initial_random_cash_pct: 初始现金比例（0.20 表示 20% 现金 + 80% 股票）
    """
    stocks = load_stock_list(subset_ids=subset_ids)
    anon_map = generate_anonymous_map(list(stocks.keys()), seed=42)

    cash = float(initial_capital)
    holdings = {}  # {sid: {shares, cost, anon, buy_year, buy_month}}
    trade_log = []
    swap_log = []  # 按月记录所有买卖，用于识别换股事件 (year, month, action, anon, price, cash_flow, note)
    total_fees = 0
    total_dividends = 0
    monthly_values = []
    market_pe_history = []  # 历史中位数 PE 序列（市场温度计）
    moat_broken_registry = {}  # {sid: {broken_at, roe_at_break, problems}} 松动标签

    # ---- 半路接管：配置初始持仓 ----
    # 模拟用户已经持有一些股票后才用模型的真实场景
    # 模型从这里开始接管，可能立即卖掉它认为高估的股票
    #
    # 两种模式：
    #   initial_stock_ids 传入固定 sid 列表 → 指定质量初始化（好股/垃圾/普通）
    #   initial_random_n_stocks > 0 且无 ids → 随机抽样初始化
    if initial_random_n_stocks > 0 or initial_stock_ids:
        init_signals = get_month_signals(
            start_year, start_month, anon_map=anon_map, industry_map={}, subset_ids=subset_ids
        )

        if initial_stock_ids:
            # 指定质量初始化：用传入的 sid 列表，只保留当月有价格的
            picked_sids = [
                sid for sid in initial_stock_ids
                if any(sd["sid"] == sid and sd.get("price", 0) > 0
                       for sd in init_signals.values())
            ]
        else:
            # 随机抽样初始化
            valid_sids = [
                sdata["sid"] for sdata in init_signals.values()
                if sdata.get("price", 0) > 0
            ]
            rng = random.Random(initial_random_seed)
            picked_sids = rng.sample(valid_sids, min(initial_random_n_stocks, len(valid_sids)))

        if len(picked_sids) >= 1:
            stock_budget_total = initial_capital * (1 - initial_random_cash_pct)
            per_stock_budget = stock_budget_total / len(picked_sids)

            for sid in picked_sids:
                # 找到 sdata
                sdata = None
                for sd in init_signals.values():
                    if sd["sid"] == sid:
                        sdata = sd
                        break
                if not sdata:
                    continue
                price = sdata.get("price", 0) or 0
                if price <= 0:
                    continue
                # 滑点
                exec_price = price * (1 + SLIPPAGE_RATE)
                shares = int(per_stock_budget / exec_price // 100) * 100
                if shares < 100:
                    continue
                code = stocks[sid]["code"]
                buy_cost, fee = calc_buy_cost(exec_price, shares, code, start_year, start_month)
                if buy_cost > cash:
                    continue
                cash -= buy_cost
                total_fees += fee
                # 找 anon
                anon = sdata.get("anon")
                for a, sd in init_signals.items():
                    if sd["sid"] == sid:
                        anon = a
                        break
                holdings[sid] = {
                    "shares": shares, "cost": exec_price, "anon": anon, "code": code,
                    "buy_year": start_year, "buy_month": start_month,
                }
                trade_log.append(
                    f"{start_year}-{start_month:02d} 【初始配置】{anon} {shares}股 @¥{exec_price:.2f} 花费¥{buy_cost:.0f}"
                )

    year, month = start_year, start_month
    while year < 2025 or (year == 2025 and month <= 12):
        signals = get_month_signals(year, month, anon_map=anon_map, industry_map={}, subset_ids=subset_ids)
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
        #
        # 2026-04-11 实验记录：
        #   尝试过"分级减仓"（浮盈>100%砍半、50-100%砍30%、<50%砍25%），
        #   结果让 2015-06 起点相对原版 -50 pp —— 减仓减早了/减多了，
        #   后续牛市恢复阶段没买回来。回滚为统一 25% 的原版策略。
        sids_to_sell = []

        # 路径 B / C 大底加仓 + 暂停卖出：market_temp == -2 时跳过所有卖出
        # B5 提取：判定逻辑改用 backtest_engine.should_skip_pe_sells_for_cold_market
        from backtest_engine import (should_skip_pe_sells_for_cold_market,
                                       should_apply_hot_market_reduction)
        # 巴菲特原话："Be greedy when others are fearful."
        # 在历史最低 15% 分位区间，任何卖出都是错的
        skip_all_sells_for_path_b = should_skip_pe_sells_for_cold_market(
            STRATEGY_MODE, market_temp
        )

        # 市场极热系统性减仓（每持仓每年一次）
        # 路径 A / C 取消此规则：让模型在牛市能跟得上沪深 300
        if should_apply_hot_market_reduction(STRATEGY_MODE, market_temp):
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

        # 路径 B 大底时跳过所有 PE 类卖出（保留退市/护城河松动那种"必须卖"）
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

            # 路径 B 大底逻辑：跳过所有 PE 类卖出信号（退市/护城河仍要卖）
            if skip_all_sells_for_path_b and sig in (
                "sell_heavy", "sell_medium", "sell_light", "sell_watch"
            ):
                continue

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

            # 护城河松动分级检查（黄色观察 vs 红色行动）
            #
            # ⚠ 黄色观察：首次触发 或 只有 1 条规则 → 不卖，等 2 个月确认
            # 🚨 红色行动：连续 ≥2 月触发 或 ≥2 条规则同时 → 执行清仓
            #
            # 巴菲特原则："chronically leaking boat" 要换船，
            # 但 "一次漏水" 可能只是暂时——等它自己修好
            is_intact, probs = check_moat(sid, year, month)
            if not is_intact:
                # 计数器：连续几个月触发松动
                prev_count = h.get("moat_alert_months", 0)
                h["moat_alert_months"] = prev_count + 1
                n_problems = len(probs)

                if h["moat_alert_months"] >= 2 or n_problems >= 2:
                    # 🚨 红色行动：确认恶化，清仓 + 打上松动标签
                    reason = f"🚨护城河确认恶化({h['moat_alert_months']}月连续·{n_problems}条规则：{'; '.join(probs[:2])})"
                    sids_to_sell.append((sid, sdata_match, reason, None))
                    # 打上"松动标签"，记住这只股票松动时的 ROE 和时间
                    moat_broken_registry[sid] = {
                        "broken_at": f"{year}-{month:02d}",
                        "roe_at_break": sdata_match.get("roe"),
                        "problems": probs[:3],
                    }
                else:
                    # ⚠ 黄色观察：首次触发，暂不卖，日志记录
                    trade_log.append(
                        f"{year}-{month:02d} ⚠观察 {h['anon']} 护城河首次松动({probs[0]})，暂不卖出"
                    )
                continue
            else:
                # 护城河完好 → 清除计数器
                h["moat_alert_months"] = 0

            # ---- 买入后 ROE 监测（长春高新教训）----
            # 对比当前 ROE 与加权基准 roe_baseline，捕捉"温水煮蛙"式衰退
            roe_baseline = h.get("roe_baseline")
            if roe_baseline and sdata_match:
                cur_roe = sdata_match.get("roe")
                if cur_roe is not None and roe_baseline > 0:
                    roe_drop = roe_baseline - cur_roe
                    if roe_drop >= 15 or cur_roe < 15:
                        # 🔴 严重衰退：下降≥15pp 或跌破15%底线 → 清仓
                        reason = (
                            f"🔴ROE严重衰退(基准{roe_baseline:.1f}%→当前{cur_roe:.1f}%，"
                            f"降{roe_drop:.1f}pp)"
                        )
                        sids_to_sell.append((sid, sdata_match, reason, None))
                        trade_log.append(
                            f"{year}-{month:02d} 🔴ROE监测 {h['anon']} "
                            f"基准{roe_baseline:.1f}%→{cur_roe:.1f}% 降{roe_drop:.1f}pp 触发清仓"
                        )
                    elif roe_drop >= 10:
                        # 🟠 明显下滑：减半仓
                        half = h["shares"] // 2
                        if half >= 100:
                            half = half // 100 * 100
                            sids_to_sell.append((sid, sdata_match,
                                f"🟠ROE明显下滑(基准{roe_baseline:.1f}%→{cur_roe:.1f}%，降{roe_drop:.1f}pp)",
                                half))
                        trade_log.append(
                            f"{year}-{month:02d} 🟠ROE监测 {h['anon']} "
                            f"基准{roe_baseline:.1f}%→{cur_roe:.1f}% 降{roe_drop:.1f}pp 建议减仓"
                        )
                    elif roe_drop >= 5:
                        # 🟡 轻微下滑：观察，记录日志
                        trade_log.append(
                            f"{year}-{month:02d} ⚠ROE监测 {h['anon']} "
                            f"基准{roe_baseline:.1f}%→{cur_roe:.1f}% 降{roe_drop:.1f}pp 持续观察"
                        )

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

            # ---- 松动标签检查（巴菲特铁律：10 年连续 ROE ≥ 15% 才重新考虑）----
            # 原理：巴菲特要求好公司连续 10-15 年 ROE ≥ 15%。
            # 一旦公司 ROE 跌破这条线（触发护城河松动），
            # 必须从那一刻起重新积累 10 年的连续达标记录，
            # 才能证明"这不是暂时回光返照，而是真正恢复了护城河"。
            #
            # 在 17 年回测周期中，这基本等于永久封禁——
            # 和巴菲特实际操作一致：卖掉的股票极少回购。
            MOAT_RECOVERY_YEARS = 10  # 恢复所需的连续达标年数
            if sid in moat_broken_registry:
                broken_info = moat_broken_registry[sid]
                broken_year = int(broken_info["broken_at"][:4])

                # 提取松动后的 ROE 序列
                reports_check = get_annual_reports_before(
                    sid, year, month, lookback_years=MOAT_RECOVERY_YEARS + 1
                )
                post_break = [
                    r for r in reports_check
                    if int(str(r["date"])[:4]) >= broken_year
                ]
                roes = [r.get("roe") for r in post_break if r.get("roe") is not None]

                # B2 提取的判定函数：是否已恢复
                from backtest_engine import check_moat_recovery
                recovered = check_moat_recovery(broken_year, year, roes,
                                                  recovery_years=MOAT_RECOVERY_YEARS,
                                                  threshold=15)

                if recovered:
                    del moat_broken_registry[sid]
                    trade_log.append(
                        f"{year}-{month:02d} ✅恢复 {anon} 护城河恢复确认"
                        f"（松动后连续{MOAT_RECOVERY_YEARS}年ROE≥15%），重新允许买入"
                    )
                else:
                    # 未达标，跳过买入
                    continue
            if price <= 0:
                continue

            # ---- 个股 PE 硬否决（替代之前的"大盘温度计硬否决"）----
            # 巴菲特原则：好东西买在贵价就变坏。
            # 之前用的"大盘温度计硬否决"对 2019 年那种"大盘便宜 + 单行业
            # 泡沫"场景无效（沪深300 PE 12 倍=正常，但白酒 PE 45 倍泡沫），
            # A/B 回测证明没拦住 2019 起点（+6% → +10% 微改善）。
            #
            # 改用"个股 PE > 行业 fair_high × 1.5 硬否决"：
            # 不管大盘状态如何，单只股票 PE 严重超过行业合理上限就拒绝买入。
            # 示例：白酒 fair_high=30，PE > 45 拒买（2019-2020 年白酒顶部）；
            #       银行 fair_high=9，PE > 13.5 拒买；
            #       医药 fair_high=30，PE > 45 拒买（2021 年医药顶部）。
            pe_ttm = sdata.get("pe_ttm")
            pe_fair_high = sdata.get("pe_fair_high")
            if (
                pe_ttm is not None and pe_ttm > 0
                and pe_fair_high is not None and pe_fair_high > 0
                and pe_ttm > pe_fair_high * INDIVIDUAL_PE_HARD_VETO_MULTIPLIER
            ):
                # 静默拒绝（不记 log，避免刷屏）
                continue

            # 按信号强度分配预算 × 温度系数（别人贪婪时我恐惧）
            # 温度系数只在 -2/-1/0 档生效，+1/+2 档已被硬性规则拦截
            #
            # 路径 B / C 大底加仓：market_temp == -2 时预算翻倍（1.30 → 2.00）
            # 这是巴菲特"别人恐惧时我贪婪"的具体落地
            if STRATEGY_MODE in ("path_b", "path_c") and market_temp == -2:
                temp_multiplier = 2.00
            else:
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
            # 2026-04-11 实验：取消兜底让 ¥1万 档均值从 +268% 崩到 +139%，
            # 因为 ¥1万 × 40% heavy = ¥4000，在股价 >40 元的好股票上
            # 连一手都买不起 → 小本金彻底错过好机会。恢复兜底。
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
            # 记录买入时的 ROE（供"买入后 ROE 监测"用）
            buy_roe = sdata.get("roe")
            holdings[sid] = {
                "shares": shares, "cost": exec_price, "anon": anon, "code": code,
                "buy_year": year, "buy_month": month,
                "roe_at_buy": buy_roe,       # 首次建仓时的 ROE
                "roe_baseline": buy_roe,     # 加权基准 ROE（加仓时更新）
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


def run_suite(time_points, capitals, verbose_first=True, subset_ids=None, subset_label=""):
    """
    跑多个起始时间 × 多档本金的完整套件

    Args:
      subset_ids: 可选的股票 ID 子集，用于多批次不重叠抽样回测
      subset_label: 子集的显示名称（如 "批次1 30只"）
    """
    all_runs = {}  # {(year, month): (years, [results...])}
    if subset_label:
        print(f"\n{'▓'*95}")
        print(f"▓  股票子集：{subset_label}（{len(subset_ids) if subset_ids else '全部'} 只）")
        print(f"{'▓'*95}")
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
            r = run_backtest(sy, sm, initial_capital=cap, verbose=verbose, subset_ids=subset_ids)
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

    return all_runs


def run_multi_batch(time_points, capitals, total_pool=None,
                    batch_size=30, n_batches=3, rng_seed=None):
    """
    多批次不重叠抽样回测 —— 验证模型在不同股票子集上的鲁棒性

    做法：
      1. 从全股票池随机洗牌
      2. 按顺序切成 n_batches 批，每批 batch_size 只，互不重叠
      3. 每批独立跑一次完整 run_suite
      4. 汇总三批结果看模型是否稳定

    Args:
      time_points: 起点列表 [(y,m), ...]
      capitals: 本金列表
      total_pool: 全股票 ID 池（None 时从 load_stock_list 自动获取）
      batch_size: 每批抽样多少只（默认 30）
      n_batches: 抽样多少批（默认 3）
      rng_seed: 随机种子（固定后可复现，None 时真随机）

    Returns:
      batches: [{"label": str, "ids": [...], "summary": all_runs}, ...]
    """
    if total_pool is None:
        total_pool = list(load_stock_list().keys())

    if len(total_pool) < batch_size * n_batches:
        raise ValueError(
            f"股票池太小：共 {len(total_pool)} 只，无法抽取 "
            f"{n_batches} 批 × {batch_size} 只（需 {batch_size*n_batches} 只）"
        )

    if rng_seed is not None:
        rng = random.Random(rng_seed)
    else:
        rng = random.Random()

    pool_copy = list(total_pool)
    rng.shuffle(pool_copy)

    batches = []
    for i in range(n_batches):
        start = i * batch_size
        end = start + batch_size
        ids = pool_copy[start:end]
        label = f"批次{i+1}（{batch_size}只·不重叠）"
        print(f"\n{'#'*95}")
        print(f"#  {label}   抽样 ID: {sorted(ids)}")
        print(f"{'#'*95}")
        summary = run_suite(
            time_points, capitals,
            verbose_first=False,
            subset_ids=ids, subset_label=label,
        )
        batches.append({"label": label, "ids": ids, "summary": summary})

    # 跨批次汇总：看模型在不同子集上的均值是否稳定
    print(f"\n\n{'═'*95}")
    print(f"  跨批次汇总（{n_batches} 批 × {batch_size} 只 × {len(time_points)} 起点）")
    print(f"{'═'*95}")
    print(f"{'批次':>8} | " + " | ".join(f"¥{c:>10,}" for c in capitals))
    print(f"{'-'*95}")
    batch_means = []
    for bi, b in enumerate(batches):
        cells = []
        for i, cap in enumerate(capitals):
            pnls = [runs[i]['final_pnl'] for _, (_, runs) in b["summary"].items()]
            mean_pnl = sum(pnls) / len(pnls)
            cells.append(f"{mean_pnl:>+9.1f}%")
        print(f"  批次{bi+1}  | " + " | ".join(cells))
        batch_means.append(cells)

    print(f"{'-'*95}")
    # 最大偏差 = 三批均值中最大减最小
    print(f"{'偏差':>8} | ", end="")
    for i, cap in enumerate(capitals):
        pnls_all_batches = []
        for b in batches:
            pnls = [runs[i]['final_pnl'] for _, (_, runs) in b["summary"].items()]
            pnls_all_batches.append(sum(pnls)/len(pnls))
        spread = max(pnls_all_batches) - min(pnls_all_batches)
        if i > 0:
            print(" | ", end="")
        print(f"{spread:>+9.1f}%", end="")
    print()
    print(f"\n  提示：偏差越小说明模型在不同股票子集上越稳定")
    return batches


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
