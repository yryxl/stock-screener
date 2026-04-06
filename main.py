"""
主程序 - 支持多种运行模式
--mode holdings   持仓检查（中午+收盘后）
--mode watchlist  关注表检查（收盘后）
--mode full       AI全市场扫描（凌晨）
--mode send_ai    发送AI推荐结果（早上9点）
--mode holiday    休市日消息
--mode all        全部运行
"""

import json
import os
import sys
import time
from datetime import datetime

import pandas as pd
import numpy as np
import yaml

from screener import (
    screen_all_stocks, check_holdings_sell_signals,
    get_pe_signal, check_decline_signals,
    check_watchlist_financial_health, check_fundamental_health,
)
from notifier import send_daily_report, send_msg, get_access_token
from data_fetcher import get_realtime_quotes, get_pe_ttm, safe_fetch
import akshare as ak


def load_config():
    path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_json(filename):
    path = os.path.join(os.path.dirname(__file__), filename)
    if not os.path.exists(path):
        return [] if filename.endswith(".json") else {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
        return data


def save_json(filename, data):
    path = os.path.join(os.path.dirname(__file__), filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def is_trading_day():
    """通过AKShare交易日历判断今天是否交易日（考虑节假日）"""
    today_str = datetime.now().strftime("%Y%m%d")
    try:
        # 获取交易日历
        df = safe_fetch(ak.tool_trade_date_hist_sina)
        if df is not None and not df.empty:
            trade_dates = set(df["trade_date"].astype(str).str.replace("-", ""))
            return today_str in trade_dates
    except Exception as e:
        print(f"  获取交易日历失败: {e}")
    # 失败时回退到简单判断（排除周末）
    return datetime.now().weekday() < 5


def get_stock_industry(code):
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
    updated = False
    for stock in watchlist:
        if stock.get("industry_auto") and len(stock["industry_auto"]) > 0:
            continue
        industry = get_stock_industry(stock["code"])
        if industry:
            stock["industry_auto"] = industry
            updated = True
        time.sleep(0.3)
    return updated


def check_watchlist(config, quotes_df):
    """关注表：只给买入方向信号，PE>=合理区间=观望"""
    watchlist = load_json("watchlist.json")
    if not watchlist:
        return [], watchlist

    print(f"检查关注表（{len(watchlist)}只）...")
    if update_watchlist_industries(watchlist):
        save_json("watchlist.json", watchlist)

    signals = []
    for stock in watchlist:
        code = stock["code"]
        name = stock.get("name", code)
        category = stock.get("industry_auto", "") or stock.get("category", "")

        if quotes_df is None or quotes_df.empty:
            continue
        row = quotes_df[quotes_df["代码"] == code]
        if row.empty:
            continue
        row = row.iloc[0]
        price = pd.to_numeric(row.get("最新价"), errors="coerce")

        pe = None
        ttm_data = get_pe_ttm(code)
        if ttm_data and ttm_data.get("pe_ttm"):
            pe = ttm_data["pe_ttm"]
        else:
            pe = pd.to_numeric(row.get("市盈率-动态"), errors="coerce")

        signal, signal_text = get_pe_signal(pe, category)
        if pe and ttm_data:
            signal_text = signal_text.replace(f"PE={pe:.1f}", f"PE(TTM)={pe:.1f}")

        # 关注表：PE>=合理区间一律"观望"
        if signal and ("sell" in signal or signal == "hold"):
            signal = "hold"
            signal_text = f"PE(TTM)={pe:.1f}，观望" if pe and not pd.isna(pe) else "观望"

        # 买入信号验证
        if signal and "buy" in signal:
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

            if signal and "buy" in signal:
                is_healthy, problems = check_fundamental_health(code)
                if is_healthy is not None and not is_healthy:
                    signal = "hold"
                    signal_text = f"基本面恶化({','.join(problems[:2])})，不推荐买入"

        signals.append({
            "code": code, "name": name, "category": category,
            "note": stock.get("note", ""),
            "price": price if not pd.isna(price) else 0,
            "pe": pe if (pe and not pd.isna(pe)) else 0,
            "signal": signal, "signal_text": signal_text,
        })

    return signals, watchlist


def send_simple_msg(config, text):
    """发送简单文本消息"""
    wx = config["wechat"]
    if wx["appid"] == "YOUR_APPID":
        print(f"微信未配置: {text}")
        return
    access_token = get_access_token(wx["appid"], wx["appsecret"])
    if access_token:
        send_msg(access_token, wx["openid"], wx["template_id"], text)


def has_today_data():
    """检查是否已有今天（或最近交易日）的数据"""
    results = load_json("daily_results.json")
    if not results or not isinstance(results, dict):
        return False
    date_str = results.get("date", "")
    if not date_str:
        return False
    try:
        data_date = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
        today = datetime.now().date()
        # 如果数据日期是今天或昨天（周末看周五的），认为有数据
        diff = (today - data_date).days
        return diff <= 2
    except Exception:
        return False


def get_mode():
    """从命令行获取运行模式"""
    for i, arg in enumerate(sys.argv):
        if arg == "--mode" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    if "--force" in sys.argv:
        return "all"
    return "all"


def run_holdings(config):
    """持仓检查"""
    print("=== 持仓检查 ===")
    holdings = load_json("holdings.json")
    if not holdings:
        print("  无持仓")
        return []
    holding_signals = check_holdings_sell_signals(holdings, config)
    print(f"  信号: {len(holding_signals)}只")
    return holding_signals


def run_watchlist(config):
    """关注表检查"""
    print("=== 关注表检查 ===")
    quotes_df = get_realtime_quotes()
    watchlist_signals, _ = check_watchlist(config, quotes_df)
    w_buy = sum(1 for s in watchlist_signals if s.get("signal") and "buy" in s["signal"])
    print(f"  买入信号: {w_buy}只")
    return watchlist_signals


def run_full_scan(config):
    """全市场扫描"""
    print("=== 全市场扫描 ===")
    candidates = screen_all_stocks(config)
    ai_recs = [s for s in candidates if s.get("signal") and s["signal"] not in ("hold", None)]
    save_json("market_scan_cache.json", {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "ai_recommendations": ai_recs,
    })
    print(f"  AI推荐: {len(ai_recs)}只")
    return ai_recs


def main():
    mode = get_mode()
    now = datetime.now()
    print(f"=== 芒格选股系统 {now.strftime('%Y-%m-%d %H:%M')} 模式:{mode} ===\n")

    config = load_config()
    today = now.strftime("%m-%d")
    trading = is_trading_day()
    print(f"交易日: {'是' if trading else '否（休市）'}")

    # 休市日处理（节假日、周末都走这里）
    if not trading and mode != "all":
        if has_today_data():
            print("休市日，已有数据，跳过")
        else:
            print("休市日，无数据，尝试获取上个交易日数据")
            # 跑一次获取数据
            quotes_df = get_realtime_quotes()
            if quotes_df is not None and not quotes_df.empty:
                watchlist_signals, _ = check_watchlist(config, quotes_df)
                holding_signals = run_holdings(config)
                daily_data = {
                    "date": now.strftime("%Y-%m-%d %H:%M"),
                    "watchlist_signals": watchlist_signals,
                    "holding_signals": holding_signals,
                    "ai_recommendations": load_json("market_scan_cache.json").get("ai_recommendations", []) if isinstance(load_json("market_scan_cache.json"), dict) else [],
                }
                save_json("daily_results.json", daily_data)
        # 发休市消息（保持48小时互动）
        send_simple_msg(config, f"芒格选股 {today}\n\n本日休市\n关注表和持仓数据已同步\n下个交易日自动运行")
        return

    # 非交易日且不是强制模式，跳过
    if not trading and "--force" not in sys.argv and mode != "all":
        print("非交易日，跳过")
        return

    # ========================================
    # 各模式执行
    # ========================================

    if mode == "holdings":
        # 持仓检查（中午/收盘后）
        holding_signals = run_holdings(config)
        # 更新daily_results中的持仓部分
        daily_data = load_json("daily_results.json")
        if not isinstance(daily_data, dict):
            daily_data = {}
        daily_data["holding_signals"] = holding_signals
        daily_data["date"] = now.strftime("%Y-%m-%d %H:%M")
        save_json("daily_results.json", daily_data)
        # 推送
        send_daily_report(
            watchlist_signals=[],
            candidates=[],
            holding_signals=holding_signals,
            config=config,
        )

    elif mode == "watchlist":
        # 关注表检查（收盘后）
        watchlist_signals = run_watchlist(config)
        holding_signals = run_holdings(config)
        # 更新daily_results
        daily_data = load_json("daily_results.json")
        if not isinstance(daily_data, dict):
            daily_data = {}
        daily_data["watchlist_signals"] = watchlist_signals
        daily_data["holding_signals"] = holding_signals
        daily_data["date"] = now.strftime("%Y-%m-%d %H:%M")
        # 保留AI推荐缓存
        cache = load_json("market_scan_cache.json")
        if isinstance(cache, dict):
            daily_data["ai_recommendations"] = cache.get("ai_recommendations", [])
        save_json("daily_results.json", daily_data)
        # 推送
        send_daily_report(
            watchlist_signals=watchlist_signals,
            candidates=[],
            holding_signals=holding_signals,
            config=config,
        )

    elif mode == "full":
        # 全市场扫描（凌晨）
        ai_recs = run_full_scan(config)
        # 更新daily_results
        daily_data = load_json("daily_results.json")
        if not isinstance(daily_data, dict):
            daily_data = {}
        daily_data["ai_recommendations"] = ai_recs
        daily_data["date"] = now.strftime("%Y-%m-%d %H:%M")
        save_json("daily_results.json", daily_data)
        print(f"  已保存，等待9点发送")

    elif mode == "send_ai":
        # 发送AI推荐（早上9点）
        cache = load_json("market_scan_cache.json")
        ai_recs = cache.get("ai_recommendations", []) if isinstance(cache, dict) else []
        if ai_recs:
            send_daily_report(
                watchlist_signals=[],
                candidates=ai_recs,
                holding_signals=[],
                config=config,
            )
        else:
            send_simple_msg(config, f"芒格选股 {today}\n\nAI全市场扫描暂无推荐\n继续观察")

    elif mode == "all":
        # 全部运行
        print("获取实时行情...")
        quotes_df = get_realtime_quotes()

        watchlist_signals, _ = check_watchlist(config, quotes_df)
        candidates = screen_all_stocks(config)
        holding_signals = run_holdings(config)

        ai_recs = [s for s in candidates if s.get("signal") and s["signal"] not in ("hold", None)]

        daily_data = {
            "date": now.strftime("%Y-%m-%d %H:%M"),
            "ai_recommendations": ai_recs,
            "watchlist_signals": watchlist_signals,
            "holding_signals": holding_signals,
        }
        save_json("daily_results.json", daily_data)
        save_json("market_scan_cache.json", {"date": now.strftime("%Y-%m-%d"), "ai_recommendations": ai_recs})

        send_daily_report(
            watchlist_signals=watchlist_signals,
            candidates=candidates,
            holding_signals=holding_signals,
            config=config,
        )

    print(f"\n=== 完成 ===")


if __name__ == "__main__":
    main()
