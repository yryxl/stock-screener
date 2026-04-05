"""
主程序 - 每日运行
优化版：日常只跑关注表+持仓（5-10分钟），全市场扫描每周一跑
"""

import json
import os
import sys
from datetime import datetime

import pandas as pd
import numpy as np
import yaml

from screener import (
    screen_all_stocks, check_holdings_sell_signals,
    get_pe_signal, check_decline_signals,
    check_watchlist_financial_health, check_fundamental_health,
)
from notifier import send_daily_report
from data_fetcher import get_realtime_quotes, get_pe_ttm, safe_fetch
import akshare as ak


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


def save_json(filename, data):
    path = os.path.join(os.path.dirname(__file__), filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_trading_day():
    return datetime.now().weekday() < 5


def is_monday():
    return datetime.now().weekday() == 0


def get_stock_industry(code):
    """从东财获取真实行业"""
    try:
        df = safe_fetch(ak.stock_individual_info_em, symbol=code)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                if "行业" in str(row.get("item", "")):
                    return str(row["value"])
    except Exception:
        pass
    return ""


def update_watchlist_industries(watchlist):
    """更新关注表行业（有缓存则跳过，无缓存才查询）"""
    updated = False
    for stock in watchlist:
        # 如果已有行业且不为空，跳过（缓存）
        if stock.get("industry_auto") and len(stock["industry_auto"]) > 0:
            continue
        code = stock["code"]
        industry = get_stock_industry(code)
        if industry:
            stock["industry_auto"] = industry
            updated = True
            print(f"  {stock.get('name','')} 行业: {industry}")
        import time
        time.sleep(0.3)
    return updated


def check_watchlist(config, quotes_df):
    """检查关注表：PE(TTM)信号 + 财务验证 + 基本面验证"""
    watchlist = load_json("watchlist.json")
    if not watchlist:
        return [], watchlist

    print(f"检查重点关注表（{len(watchlist)}只）...")

    # 更新行业缓存（只查没缓存的）
    if update_watchlist_industries(watchlist):
        save_json("watchlist.json", watchlist)

    signals = []
    for stock in watchlist:
        code = stock["code"]
        name = stock.get("name", code)
        # 使用自动获取的行业，回退到手动填的
        category = stock.get("industry_auto", "") or stock.get("category", "")

        if quotes_df is None or quotes_df.empty:
            continue

        row = quotes_df[quotes_df["代码"] == code]
        if row.empty:
            continue
        row = row.iloc[0]

        price = pd.to_numeric(row.get("最新价"), errors="coerce")

        # 获取PE(TTM)
        pe = None
        ttm_data = get_pe_ttm(code)
        if ttm_data and ttm_data.get("pe_ttm"):
            pe = ttm_data["pe_ttm"]
        else:
            pe = pd.to_numeric(row.get("市盈率-动态"), errors="coerce")

        # 行业PE区间判断
        signal, signal_text = get_pe_signal(pe, category)
        if pe and ttm_data:
            signal_text = signal_text.replace(f"PE={pe:.1f}", f"PE(TTM)={pe:.1f}")

        # 买入信号：ROE+财务+基本面三重验证
        if signal and "buy" in signal:
            # ROE+杠杆验证
            health_ok, health_warning, roe_level = check_watchlist_financial_health(code)

            signal_cap = {"heavy": "buy_heavy", "light": "buy_light", "watch": "buy_watch", "none": "hold"}
            max_signal = signal_cap.get(roe_level, "buy_light")
            signal_rank = {"buy_heavy": 0, "buy_medium": 1, "buy_light": 2, "buy_watch": 3, "hold": 4}

            if signal_rank.get(signal, 4) < signal_rank.get(max_signal, 4):
                signal = max_signal
                if roe_level == "none":
                    signal_text = f"PE偏低但ROE过低，不建议买入"
                else:
                    level_names = {"buy_light": "轻仓", "buy_watch": "关注", "buy_medium": "中仓"}
                    signal_text = f"PE偏低，ROE限制最高{level_names.get(max_signal, '轻仓')}"

            if not health_ok:
                signal = "hold"
                signal_text = f"财务风险({health_warning})，暂不建议买入"
            elif health_warning:
                signal_text += f" ({health_warning})"

            # 基本面验证（真跌pass）
            if signal and "buy" in signal:
                is_healthy, problems = check_fundamental_health(code)
                if is_healthy is not None and not is_healthy:
                    signal = "hold"
                    signal_text = f"基本面恶化({','.join(problems[:2])})，不推荐买入"

        signals.append({
            "code": code, "name": name,
            "category": category,
            "note": stock.get("note", ""),
            "price": price if not pd.isna(price) else 0,
            "pe": pe if (pe and not pd.isna(pe)) else 0,
            "signal": signal, "signal_text": signal_text,
        })

    return signals, watchlist


def main():
    print(f"=== 芒格选股系统 {datetime.now().strftime('%Y-%m-%d %H:%M')} ===\n")

    if not is_trading_day() and "--force" not in sys.argv:
        print("非交易日，跳过")
        return

    config = load_config()

    # 获取实时行情
    print("获取实时行情...")
    quotes_df = get_realtime_quotes()

    # 1. 关注表（每天跑）
    print("\n=== 第一步：关注表 ===")
    watchlist_signals, watchlist_raw = check_watchlist(config, quotes_df)
    w_buy = sum(1 for s in watchlist_signals if s.get("signal") and "buy" in s["signal"])
    print(f"  买入信号: {w_buy}只")

    # 2. 全市场扫描（周一或手动--full时跑，其他天用缓存）
    candidates = []
    if is_monday() or "--full" in sys.argv:
        print("\n=== 第二步：全市场扫描（执行中）===")
        candidates = screen_all_stocks(config)
        # 缓存结果供非扫描日使用
        save_json("market_scan_cache.json", {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "candidates": candidates,
        })
    else:
        print("\n=== 第二步：全市场扫描（用上次缓存）===")
        cache = load_json("market_scan_cache.json")
        if cache and isinstance(cache, dict):
            candidates = cache.get("candidates", [])
            print(f"  缓存日期: {cache.get('date', '未知')}，{len(candidates)}只候选")

    # 3. 持仓检查（每天跑）
    print("\n=== 第三步：持仓检查 ===")
    holdings = load_json("holdings.json")
    holding_signals = check_holdings_sell_signals(holdings, config) if holdings else []

    # 4. 推送
    print("\n=== 第四步：推送 ===")
    send_daily_report(
        watchlist_signals=watchlist_signals,
        candidates=candidates,
        holding_signals=holding_signals,
        config=config,
    )

    # 5. 保存结果
    print("\n=== 第五步：保存结果 ===")

    # AI推荐：只来自全市场扫描（不含关注表）
    ai_recommendations = []
    for s in candidates:
        if s.get("signal") and s["signal"] not in ("hold", None):
            s["source"] = "全市场筛选"
            ai_recommendations.append(s)

    daily_data = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "ai_recommendations": ai_recommendations,        # AI推荐（全市场）
        "watchlist_signals": watchlist_signals,            # 关注表（含信号状态）
        "holding_signals": holding_signals,                # 持仓信号
    }
    save_json("daily_results.json", daily_data)
    print(f"  AI推荐: {len(ai_recommendations)}只 | 关注表: {len(watchlist_signals)}只 | 已保存")

    # 总结
    print(f"\n=== 完成 ===")
    print(f"关注表买入: {w_buy} | 候选池: {len(candidates)} | 持仓信号: {len(holding_signals)}")


if __name__ == "__main__":
    main()
