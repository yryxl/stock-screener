"""
历史数据采集脚本 - 采集30只股票15年的月度数据
用于回测验证模型准确性

用法:
  python backtest_collector.py          # 采集所有股票
  python backtest_collector.py 600519   # 只采集指定股票
"""

import json
import os
import sys
import time
from datetime import datetime

import akshare as ak
import pandas as pd
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "backtest_data")
MONTHLY_DIR = os.path.join(DATA_DIR, "monthly")


def ensure_dirs():
    os.makedirs(MONTHLY_DIR, exist_ok=True)


def load_stock_list():
    path = os.path.join(SCRIPT_DIR, "backtest_stocks.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    stocks = []
    for cat, items in data["categories"].items():
        for item in items:
            stocks.append(item)
    return stocks


def collect_monthly_prices(code, start="20100101", end="20251231"):
    """采集月K线（不复权），取每月15日附近的收盘价"""
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="monthly", start_date=start, end_date=end, adjust="")
        if df is None or df.empty:
            return {}
        prices = {}
        for _, row in df.iterrows():
            date = str(row["日期"])[:7]  # YYYY-MM
            prices[date] = {
                "price": float(row["收盘"]),
                "open": float(row["开盘"]),
                "high": float(row["最高"]),
                "low": float(row["最低"]),
                "volume": int(row["成交量"]),
                "change_pct": float(row["涨跌幅"]),
            }
        return prices
    except Exception as e:
        print(f"  价格采集失败 {code}: {e}")
        return {}


def collect_pe_history(code):
    """采集PE(TTM)历史"""
    try:
        df = ak.stock_zh_valuation_baidu(symbol=code, indicator="市盈率(TTM)", period="全部")
        if df is None or df.empty:
            return {}
        pe_map = {}
        for _, row in df.iterrows():
            date = str(row["date"])[:7]
            pe_map[date] = float(row["value"])
        return pe_map
    except Exception as e:
        print(f"  PE采集失败 {code}: {e}")
        return {}


def collect_financial_data(code):
    """采集财务数据（ROE/负债率/毛利率等）"""
    try:
        # 优先用同花顺
        df = ak.stock_financial_abstract_ths(symbol=code)
        if df is None or df.empty:
            return []

        cols = list(df.columns)
        records = []
        for _, row in df.iterrows():
            date = str(row[cols[0]])[:7]
            record = {"date": date}

            # 按列索引取数据（同花顺列名可能乱码）
            if len(cols) >= 25:
                record["roe"] = _to_float(row[cols[14]])
                record["gross_margin"] = _to_float(row[cols[13]])
                record["debt_ratio"] = _to_float(row[cols[24]])
                record["current_ratio"] = _to_float(row[cols[20]])
                record["eps"] = _to_float(row[cols[7]])
                record["revenue_growth"] = _to_float(row[cols[6]])

            records.append(record)
        return records
    except Exception as e:
        print(f"  财务采集失败 {code}: {e}")
        return []


def collect_dividend_data(code):
    """采集分红历史"""
    try:
        df = ak.stock_history_dividend_detail(symbol=code, indicator="分红")
        if df is None or df.empty:
            return []

        cols = list(df.columns)
        records = []
        for _, row in df.iterrows():
            date = str(row[cols[0]])[:7]
            div_per_10 = _to_float(row[cols[3]])
            status = str(row[cols[4]])
            records.append({"date": date, "div_per_10": div_per_10, "status": status})
        return records
    except Exception as e:
        print(f"  分红采集失败 {code}: {e}")
        return []


def _to_float(val):
    try:
        s = str(val).replace("%", "").replace("False", "").replace("亿", "").strip()
        if not s:
            return None
        return float(s)
    except (ValueError, TypeError):
        return None


def collect_single_stock(stock):
    """采集单只股票的全部数据"""
    code = stock["code"]
    sid = stock["id"]
    print(f"\n[{sid}] 采集 {code}...")

    result = {
        "id": sid,
        "code": code,
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    # 1. 月K线
    print(f"  月K线...")
    result["monthly_prices"] = collect_monthly_prices(code)
    print(f"  → {len(result['monthly_prices'])} 个月")
    time.sleep(1)

    # 2. PE(TTM)历史
    print(f"  PE(TTM)...")
    result["pe_history"] = collect_pe_history(code)
    print(f"  → {len(result['pe_history'])} 个月")
    time.sleep(1)

    # 3. 财务数据
    print(f"  财务指标...")
    result["financial_data"] = collect_financial_data(code)
    print(f"  → {len(result['financial_data'])} 条")
    time.sleep(1)

    # 4. 分红数据
    print(f"  分红...")
    result["dividends"] = collect_dividend_data(code)
    print(f"  → {len(result['dividends'])} 条")
    time.sleep(1)

    return result


def build_monthly_snapshots(all_data, stocks):
    """
    从各只股票的原始数据构建月度快照
    每月一个JSON文件，包含所有股票当月的数据
    """
    print("\n=== 构建月度快照 ===")

    # 生成2010-01到2025-12的所有月份
    months = []
    for y in range(2010, 2026):
        for m in range(1, 13):
            months.append(f"{y}-{m:02d}")

    for month in months:
        snapshot = {"date": f"{month}-15", "stocks": {}}

        for stock_data in all_data:
            sid = stock_data["id"]
            code = stock_data["code"]

            # 取当月价格
            price_data = stock_data.get("monthly_prices", {}).get(month, {})
            if not price_data:
                continue

            # 取当月PE（精确匹配或最近的）
            pe = stock_data.get("pe_history", {}).get(month)

            # 取最近的财务数据（年报/季报，取最新的<=当月的）
            fin = {}
            for f in stock_data.get("financial_data", []):
                if f["date"] <= month:
                    fin = f
                    break  # 已按日期降序

            # 取股息率（用最近2次分红/当月价格）
            div_yield = 0
            divs = stock_data.get("dividends", [])
            recent_divs = [d for d in divs if d["date"] <= month][:2]
            if recent_divs and price_data.get("price", 0) > 0:
                total = sum(d.get("div_per_10", 0) or 0 for d in recent_divs)
                div_yield = round((total / 10 / price_data["price"]) * 100, 2)

            snapshot["stocks"][sid] = {
                "price": price_data.get("price", 0),
                "change_pct": price_data.get("change_pct", 0),
                "pe_ttm": pe,
                "roe": fin.get("roe"),
                "gross_margin": fin.get("gross_margin"),
                "debt_ratio": fin.get("debt_ratio"),
                "current_ratio": fin.get("current_ratio"),
                "revenue_growth": fin.get("revenue_growth"),
                "dividend_yield": div_yield,
            }

        # 保存月度快照
        if snapshot["stocks"]:
            path = os.path.join(MONTHLY_DIR, f"{month}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"  共生成 {len(months)} 个月度快照")


def main():
    ensure_dirs()
    stocks = load_stock_list()

    # 支持只采集指定股票
    target_codes = sys.argv[1:] if len(sys.argv) > 1 else None

    if target_codes:
        stocks = [s for s in stocks if s["code"] in target_codes]

    print(f"=== 历史数据采集 ({len(stocks)}只股票) ===")

    all_data = []
    for i, stock in enumerate(stocks, 1):
        print(f"\n[{i}/{len(stocks)}]", end="")
        data = collect_single_stock(stock)

        # 保存原始数据
        raw_path = os.path.join(DATA_DIR, f"raw_{stock['id']}.json")
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

        all_data.append(data)
        time.sleep(2)  # 避免请求过快

    # 构建月度快照
    build_monthly_snapshots(all_data, stocks)

    print(f"\n=== 采集完成 ===")
    print(f"原始数据: {DATA_DIR}/raw_*.json")
    print(f"月度快照: {MONTHLY_DIR}/*.json")


if __name__ == "__main__":
    main()
