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


def collect_monthly_prices(code, start="20010101", end="20251231"):
    """采集月度价格（从新浪日K线中提取每月15日附近的数据）"""
    for attempt in range(3):
        try:
            time.sleep(2)
            # 新浪格式：sh开头上证，sz开头深证
            prefix = "sh" if code.startswith("6") else "sz"
            df = ak.stock_zh_a_daily(symbol=f"{prefix}{code}", start_date=start, end_date=end, adjust="")
            if df is None or df.empty:
                return {}

            df["date"] = pd.to_datetime(df["date"])
            df["month"] = df["date"].dt.strftime("%Y-%m")

            # 每月取15日或之后最近的一个交易日
            prices = {}
            for month_key, group in df.groupby("month"):
                # 找15日或之后最近的
                target_day = 15
                candidates = group[group["date"].dt.day >= target_day]
                if candidates.empty:
                    candidates = group  # 如果15日之后没有，取最后一天
                row = candidates.iloc[0]

                prev_month = group.iloc[0]
                last_month = group.iloc[-1]
                change_pct = 0
                if prev_month["open"] > 0:
                    change_pct = round((last_month["close"] / prev_month["open"] - 1) * 100, 2)

                prices[month_key] = {
                    "price": float(row["close"]),
                    "open": float(row["open"]),
                    "high": float(group["high"].max()),
                    "low": float(group["low"].min()),
                    "volume": int(group["volume"].sum()),
                    "change_pct": change_pct,
                    "date": str(row["date"])[:10],
                }
            return prices
        except Exception as e:
            print(f"  价格采集重试{attempt+1}/3: {e}")
            time.sleep(5)
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
    """
    采集财务数据（ROE/负债率/毛利率/净利率等）
    按列名精确匹配，兼容银行股（列数和列序与工商业股不同）
    """
    try:
        df = ak.stock_financial_abstract_ths(symbol=code)
        if df is None or df.empty:
            return []

        cols = list(df.columns)
        # 按列名找索引，找不到返回 None
        def find(names):
            for name in names:
                if name in cols:
                    return name
            return None

        col_date = find(["报告期"])
        col_roe = find(["净资产收益率", "净资产收益率-摊薄"])
        col_gm = find(["销售毛利率"])           # 银行股没有
        col_nm = find(["销售净利率"])            # 银行股关键指标
        col_debt = find(["资产负债率"])
        col_current = find(["流动比率"])         # 银行股没有
        col_eps = find(["基本每股收益"])
        col_rev = find(["营业总收入同比增长率", "营业收入同比增长率"])
        col_profit = find(["净利润同比增长率"])  # 净利润增长率
        # 巴菲特核心：每股经营现金流（用于识别"账面利润是否真变成现金"）
        col_ocf = find(["每股经营现金流", "每股经营性现金流"])

        records = []
        for _, row in df.iterrows():
            date = str(row[col_date])[:7] if col_date else ""
            record = {"date": date}
            if col_roe:     record["roe"] = _to_float(row[col_roe])
            if col_gm:      record["gross_margin"] = _to_float(row[col_gm])
            if col_nm:      record["net_margin"] = _to_float(row[col_nm])
            if col_debt:    record["debt_ratio"] = _to_float(row[col_debt])
            if col_current: record["current_ratio"] = _to_float(row[col_current])
            if col_eps:     record["eps"] = _to_float(row[col_eps])
            if col_rev:     record["revenue_growth"] = _to_float(row[col_rev])
            if col_profit:  record["profit_growth"] = _to_float(row[col_profit])
            if col_ocf:     record["ocf_per_share"] = _to_float(row[col_ocf])
            records.append(record)
        return records
    except Exception as e:
        print(f"  财务采集失败 {code}: {e}")
        return []


def collect_all_buybacks():
    """
    一次性采集全 A 股回购历史（东方财富）
    返回：{code: [{start_date, status, amount, notice_date}]}
    只保留"完成实施"或有实际回购金额的记录
    """
    try:
        df = ak.stock_repurchase_em()
        if df is None or df.empty:
            return {}
        result = {}
        for _, row in df.iterrows():
            code = str(row.get("股票代码", "")).strip()
            if not code:
                continue
            status = str(row.get("实施进度", ""))
            amount = _to_float(row.get("已回购金额"))  # 万元或元，需要检查
            start_date = str(row.get("回购起始时间", ""))[:10]
            notice_date = str(row.get("最新公告日期", ""))[:10]
            record = {
                "start_date": start_date,
                "status": status,
                "amount": amount,
                "notice_date": notice_date,
            }
            result.setdefault(code, []).append(record)
        return result
    except Exception as e:
        print(f"回购数据采集失败: {e}")
        return {}


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

    # 生成2001-01到2025-12的所有月份（部分股票上市晚，早期月份会跳过）
    months = []
    for y in range(2001, 2026):
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

            # 只取最新已披露的年报（避免季度ROE被当年化ROE误判）
            # 年报披露规则：X年12月年报在X+1年4月后才公开
            all_fin = stock_data.get("financial_data", []) or []
            cy, cm = int(month[:4]), int(month[5:7])
            fin = {}
            best_date = ""
            for f in all_fin:
                fd_str = str(f.get("date", ""))[:7]
                if len(fd_str) != 7 or not fd_str.endswith("-12"):
                    continue
                try:
                    fy = int(fd_str[:4])
                except ValueError:
                    continue
                # 披露可见时间：次年4月起
                if (fy + 1, 4) <= (cy, cm):
                    if fd_str > best_date:
                        best_date = fd_str
                        fin = f

            # 取股息率（用最近2次分红/当月价格）
            div_yield = 0
            divs = stock_data.get("dividends", [])
            recent_divs = sorted(
                [d for d in divs if str(d.get("date", ""))[:7] <= month],
                key=lambda d: str(d.get("date", "")),
                reverse=True,
            )[:2]
            if recent_divs and price_data.get("price", 0) > 0:
                total = sum(d.get("div_per_10", 0) or 0 for d in recent_divs)
                div_yield = round((total / 10 / price_data["price"]) * 100, 2)

            snapshot["stocks"][sid] = {
                "price": price_data.get("price", 0),
                "change_pct": price_data.get("change_pct", 0),
                "pe_ttm": pe,
                "roe": fin.get("roe"),
                "gross_margin": fin.get("gross_margin"),
                "net_margin": fin.get("net_margin"),
                "debt_ratio": fin.get("debt_ratio"),
                "current_ratio": fin.get("current_ratio"),
                "revenue_growth": fin.get("revenue_growth"),
                "profit_growth": fin.get("profit_growth"),
                "eps": fin.get("eps"),
                "ocf_per_share": fin.get("ocf_per_share"),  # 巴菲特：真金白银
                "dividend_yield": div_yield,
                "fin_date": best_date,  # 记录所用财报期，便于诊断
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
