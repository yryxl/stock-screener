"""
回测引擎 - 用历史月度数据跑模型，输出买卖信号
复用 screener.py 的行业PE区间和评估逻辑
"""

import json
import os
import random
import string

from screener import match_industry_pe, COMPLEXITY_ROE_ADJUST

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_month_data(year, month):
    """加载某月的历史快照"""
    path = os.path.join(SCRIPT_DIR, "backtest_data", "monthly", f"{year}-{month:02d}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def load_stock_list():
    """加载股票列表（含真实信息，不暴露给前端）"""
    path = os.path.join(SCRIPT_DIR, "backtest_stocks.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    stocks = {}
    for cat, items in data["categories"].items():
        for item in items:
            stocks[item["id"]] = {
                "code": item["code"],
                "name": item["name"],
                "category": cat,
            }
    return stocks


def load_events():
    """加载脱敏事件"""
    path = os.path.join(SCRIPT_DIR, "backtest_events.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("events", {})
    return {}


def generate_anonymous_map(stock_ids, seed=None):
    """
    生成匿名编号映射（每次重置随机不同）
    S01 → "K7", S02 → "M3" 等
    """
    if seed is not None:
        random.seed(seed)
    letters = list(string.ascii_uppercase)
    random.shuffle(letters)
    digits = list(range(1, 100))
    random.shuffle(digits)

    mapping = {}
    for i, sid in enumerate(sorted(stock_ids)):
        letter = letters[i % len(letters)]
        digit = digits[i % len(digits)]
        mapping[sid] = f"{letter}{digit:02d}"
    return mapping


def evaluate_stock(stock_data, industry_hint=""):
    """
    用模型逻辑评估单只股票
    输入：某月的股票数据（price, pe_ttm, roe, etc）
    输出：信号 + 评分
    """
    pe = stock_data.get("pe_ttm")
    roe = stock_data.get("roe")
    debt_ratio = stock_data.get("debt_ratio")
    gross_margin = stock_data.get("gross_margin")
    div_yield = stock_data.get("dividend_yield", 0)
    price = stock_data.get("price", 0)

    result = {
        "signal": "hold",
        "signal_text": "数据不足",
        "score": 0,
    }

    # 没有价格的（已退市/未上市）
    if not price or price <= 0:
        result["signal"] = "delisted"
        result["signal_text"] = "该证券已停止交易"
        return result

    # PE信号（复用行业PE区间）
    if pe and pe > 0:
        pe_range = match_industry_pe(industry_hint)
        complexity = pe_range.get("complexity", "medium")

        if pe <= pe_range["low"]:
            signal = "buy_heavy"
            signal_text = f"PE={pe:.1f}，远低于行业底部{pe_range['low']}"
        elif pe <= (pe_range["low"] + pe_range["fair_low"]) / 2:
            signal = "buy_medium"
            signal_text = f"PE={pe:.1f}，明显低于合理区间"
        elif pe <= pe_range["fair_low"]:
            signal = "buy_light"
            signal_text = f"PE={pe:.1f}，低于合理区间{pe_range['fair_low']}-{pe_range['fair_high']}"
        elif pe <= pe_range["fair_high"]:
            mid = (pe_range["fair_low"] + pe_range["fair_high"]) / 2
            if pe <= mid * 0.9:
                signal = "buy_watch"
                signal_text = f"PE={pe:.1f}，合理偏低"
            elif pe >= mid * 1.1:
                signal = "sell_watch"
                signal_text = f"PE={pe:.1f}，合理偏高"
            else:
                signal = "hold"
                signal_text = f"PE={pe:.1f}，合理区间"
        elif pe <= (pe_range["fair_high"] + pe_range["high"]) / 2:
            signal = "sell_light"
            signal_text = f"PE={pe:.1f}，偏高"
        elif pe <= pe_range["high"]:
            signal = "sell_medium"
            signal_text = f"PE={pe:.1f}，明显偏高"
        else:
            signal = "sell_heavy"
            signal_text = f"PE={pe:.1f}，远超行业上限{pe_range['high']}"

        result["signal"] = signal
        result["signal_text"] = signal_text

        # ROE检查（限制买入信号上限）
        if "buy" in signal and roe is not None:
            base_thresh = COMPLEXITY_ROE_ADJUST.get(complexity, COMPLEXITY_ROE_ADJUST["medium"])
            leverage_adj = 0
            if debt_ratio and debt_ratio < 30:
                leverage_adj = -2
            elif debt_ratio and debt_ratio > 50:
                leverage_adj = 5
            roe_heavy = base_thresh["heavy"] + leverage_adj
            roe_light = base_thresh["light"] + leverage_adj
            roe_watch = base_thresh["watch"] + leverage_adj

            if roe < roe_watch:
                result["signal"] = "hold"
                result["signal_text"] += f" 但ROE={roe:.1f}%不达标"
            elif roe < roe_light:
                if signal in ("buy_heavy", "buy_medium", "buy_light"):
                    result["signal"] = "buy_watch"
                    result["signal_text"] += f" (ROE={roe:.1f}%限制)"
            elif roe < roe_heavy:
                if signal in ("buy_heavy", "buy_medium"):
                    result["signal"] = "buy_light"
                    result["signal_text"] += f" (ROE={roe:.1f}%限制)"

        # 财务风险检查
        if "buy" in result["signal"]:
            if debt_ratio and debt_ratio > 70:
                result["signal"] = "hold"
                result["signal_text"] += f" 负债率{debt_ratio:.0f}%过高"
            if gross_margin and gross_margin < 15:
                result["signal"] = "hold"
                result["signal_text"] += f" 毛利率{gross_margin:.0f}%过低"

    # 简单评分（展示用）
    score = 0
    if roe and roe >= 20: score += 8
    elif roe and roe >= 15: score += 6
    elif roe and roe >= 10: score += 4
    if debt_ratio and debt_ratio < 40: score += 6
    elif debt_ratio and debt_ratio < 55: score += 4
    if gross_margin and gross_margin >= 40: score += 6
    elif gross_margin and gross_margin >= 25: score += 4
    if div_yield and div_yield >= 4: score += 6
    elif div_yield and div_yield >= 2: score += 4
    if pe and pe > 0:
        pe_range = match_industry_pe(industry_hint)
        if pe <= pe_range["fair_low"]: score += 8
        elif pe <= pe_range["fair_high"]: score += 5
    result["score"] = min(score, 50)

    return result


def get_month_signals(year, month, anon_map=None, industry_map=None):
    """
    获取某月所有股票的模型信号（匿名化）
    返回：{匿名编号: {price, pe, signal, signal_text, score, events}}
    """
    data = load_month_data(year, month)
    if not data:
        return {}

    events = load_events()
    month_str = f"{year}-{month:02d}"
    stocks = data.get("stocks", {})

    if anon_map is None:
        anon_map = {sid: sid for sid in stocks}
    if industry_map is None:
        industry_map = {}

    results = {}
    for sid, sdata in stocks.items():
        anon_id = anon_map.get(sid, sid)
        industry = industry_map.get(sid, "")

        # 评估信号
        eval_result = evaluate_stock(sdata, industry_hint=industry)

        # 获取当月事件
        stock_events = []
        for evt in events.get(sid, []):
            if evt.get("date", "") == month_str:
                stock_events.append(evt)

        results[anon_id] = {
            "sid": sid,  # 内部ID，不暴露给前端
            "price": sdata.get("price", 0),
            "pe_ttm": sdata.get("pe_ttm"),
            "roe": sdata.get("roe"),
            "debt_ratio": sdata.get("debt_ratio"),
            "gross_margin": sdata.get("gross_margin"),
            "dividend_yield": sdata.get("dividend_yield", 0),
            "change_pct": sdata.get("change_pct", 0),
            "signal": eval_result["signal"],
            "signal_text": eval_result["signal_text"],
            "score": eval_result["score"],
            "events": stock_events,
        }

    return results
