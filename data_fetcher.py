"""
数据获取模块 - 使用 AKShare 获取A股财务和估值数据
"""

import time
import akshare as ak
import pandas as pd
import numpy as np


def safe_fetch(func, *args, retry=2, delay=2, **kwargs):
    """带重试的安全请求"""
    for i in range(retry):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if i < retry - 1:
                time.sleep(delay)
            else:
                return None


def get_all_stocks():
    """获取全部A股列表，过滤ST和北交所"""
    df = safe_fetch(ak.stock_info_a_code_name)
    if df is None:
        return pd.DataFrame()
    df = df[~df["name"].str.contains("ST|退市", na=False)]
    df = df[df["code"].str.match(r"^(00|30|60)")]
    return df.reset_index(drop=True)


def get_financial_indicator(stock_code):
    """获取财务分析指标（含ROE、负债率、利润率、现金流等）"""
    df = safe_fetch(ak.stock_financial_analysis_indicator, symbol=stock_code)
    if df is None or df.empty:
        return None
    df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
    df = df.dropna(subset=["日期"])
    return df


def extract_annual_data(df, years=10):
    """从财务指标中提取年报数据"""
    df_annual = df[df["日期"].dt.month == 12].copy()
    df_annual = df_annual.sort_values("日期", ascending=False).head(years)
    return df_annual


def find_column(df, keywords):
    """模糊匹配列名"""
    for kw in keywords:
        matches = [c for c in df.columns if kw in c]
        if matches:
            return matches[0]
    return None


def get_roe_series(df_annual):
    """提取ROE序列"""
    col = find_column(df_annual, ["净资产收益率"])
    if col is None:
        return None
    return pd.to_numeric(df_annual[col], errors="coerce").dropna()


def get_debt_info(df_annual):
    """提取负债信息（最新年报）"""
    if df_annual.empty:
        return None
    latest = df_annual.iloc[0]
    result = {}

    col = find_column(df_annual, ["资产负债率"])
    if col:
        result["debt_ratio"] = pd.to_numeric(latest.get(col), errors="coerce")

    col = find_column(df_annual, ["流动比率"])
    if col:
        result["current_ratio"] = pd.to_numeric(latest.get(col), errors="coerce")

    return result if result else None


def get_opm_series(df_annual):
    """提取营业利润率序列"""
    col = find_column(df_annual, ["营业利润率", "销售利润率"])
    if col is None:
        return None
    return pd.to_numeric(df_annual[col], errors="coerce").dropna()


def get_fcf_series(df_annual):
    """提取每股经营现金流序列（近似自由现金流）"""
    col = find_column(df_annual, ["每股经营性现金流", "每股经营现金流量"])
    if col is None:
        col = find_column(df_annual, ["经营.*现金"])
    if col is None:
        return None
    return pd.to_numeric(df_annual[col], errors="coerce").dropna()


def get_realtime_quotes():
    """获取全A股实时行情（含PE、股息率等）"""
    df = safe_fetch(ak.stock_zh_a_spot_em)
    if df is None:
        return pd.DataFrame()
    return df


def get_stock_name(code):
    """根据代码获取股票名称"""
    try:
        df = safe_fetch(ak.stock_individual_info_em, symbol=code)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                if "股票简称" in str(row.get("item", "")):
                    return str(row["value"])
    except Exception:
        pass
    return code
