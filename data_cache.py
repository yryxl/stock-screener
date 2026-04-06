"""
数据缓存模块
采集一次原始数据保存到本地，调试时直接读取，不重复抓网络
"""

import json
import os
import time
from datetime import datetime

import akshare as ak
import pandas as pd

from data_fetcher import (
    get_all_stocks, get_realtime_quotes, get_financial_indicator,
    get_pe_ttm, safe_fetch, extract_annual_data, find_column,
    get_roe_series, get_debt_info, get_opm_series, get_fcf_series,
)

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")


def ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)


def cache_path(name):
    return os.path.join(CACHE_DIR, f"{name}.json")


def is_cache_fresh(name, max_hours=24):
    """检查缓存是否新鲜（默认24小时内有效）"""
    path = cache_path(name)
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cached_time = datetime.strptime(data.get("_cached_at", ""), "%Y-%m-%d %H:%M")
        hours = (datetime.now() - cached_time).total_seconds() / 3600
        return hours < max_hours
    except Exception:
        return False


def save_cache(name, data):
    ensure_cache_dir()
    data["_cached_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(cache_path(name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    print(f"  缓存已保存: {name}")


def load_cache(name):
    path = cache_path(name)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def collect_all_data(codes=None, force=False):
    """
    采集所有原始数据并缓存
    codes: 指定股票代码列表，None则采集关注表+持仓的所有股票
    force: True则强制重新采集
    """
    ensure_cache_dir()

    # 1. 实时行情（所有股票）
    if force or not is_cache_fresh("quotes", max_hours=4):
        print("采集实时行情...")
        quotes = get_realtime_quotes()
        if quotes is not None and not quotes.empty:
            save_cache("quotes", {"data": quotes.to_dict(orient="records")})
    else:
        print("实时行情缓存有效，跳过")

    # 2. 确定要采集的股票列表
    if codes is None:
        # 从关注表和持仓读取
        codes = set()
        for f in ["watchlist.json", "holdings.json"]:
            path = os.path.join(os.path.dirname(__file__), f)
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as fp:
                    items = json.load(fp)
                for item in items:
                    if item.get("code"):
                        codes.add(item["code"])
        codes = list(codes)

    print(f"需要采集 {len(codes)} 只股票的详细数据")

    # 3. 逐只采集PE(TTM)+财务指标
    for i, code in enumerate(codes, 1):
        cache_name = f"stock_{code}"
        if not force and is_cache_fresh(cache_name, max_hours=24):
            continue

        print(f"  [{i}/{len(codes)}] 采集 {code}...")
        stock_data = {"code": code}

        # PE(TTM)
        ttm = get_pe_ttm(code)
        if ttm:
            stock_data["pe_ttm"] = ttm.get("pe_ttm")

        # 行业
        try:
            df = safe_fetch(ak.stock_individual_info_em, symbol=code)
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    item = str(row.get("item", ""))
                    if "行业" in item:
                        stock_data["industry"] = str(row["value"])
        except Exception:
            pass

        # 财务指标
        df_indicator = get_financial_indicator(code)
        if df_indicator is not None and not df_indicator.empty:
            df_annual = extract_annual_data(df_indicator, years=10)
            if not df_annual.empty:
                # ROE
                roe = get_roe_series(df_annual)
                if roe is not None:
                    stock_data["roe_values"] = roe.tolist()
                    stock_data["roe_avg"] = float(roe.mean())

                # 负债
                debt = get_debt_info(df_annual)
                if debt:
                    stock_data["debt_ratio"] = debt.get("debt_ratio")
                    stock_data["current_ratio"] = debt.get("current_ratio")

                # 毛利率
                gm_col = find_column(df_annual, ["销售毛利率", "毛利率"])
                if gm_col:
                    gm = pd.to_numeric(df_annual[gm_col], errors="coerce").dropna()
                    if len(gm) > 0:
                        stock_data["gross_margin"] = float(gm.mean())

                # 营业利润率
                opm = get_opm_series(df_annual)
                if opm is not None:
                    stock_data["opm_values"] = opm.tolist()

                # 现金流
                fcf = get_fcf_series(df_annual)
                if fcf is not None:
                    stock_data["fcf_values"] = fcf.tolist()

                stock_data["data_years"] = len(df_annual)

        save_cache(cache_name, stock_data)
        time.sleep(0.5)

    print(f"\n数据采集完成，缓存目录: {CACHE_DIR}")


def get_cached_stock(code):
    """获取缓存的单只股票数据"""
    return load_cache(f"stock_{code}")


def get_cached_quotes():
    """获取缓存的实时行情"""
    data = load_cache("quotes")
    if data and "data" in data:
        return pd.DataFrame(data["data"])
    return None


if __name__ == "__main__":
    """直接运行此文件来采集数据：python data_cache.py"""
    import sys
    force = "--force" in sys.argv
    print(f"=== 数据采集 {'(强制刷新)' if force else '(增量)'} ===")
    collect_all_data(force=force)
    print("\n完成！现在可以用缓存数据调试模型了。")
    print(f"用法: python data_cache.py          # 增量采集（跳过24小时内的）")
    print(f"      python data_cache.py --force  # 强制全部重新采集")
