"""
数据获取模块 - 使用 AKShare 获取A股财务和估值数据
优化版：使用批量接口，避免逐个股票查询
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
                print(f"  获取数据失败: {e}")
                return None


def get_all_stocks():
    """获取全部A股列表，过滤ST和北交所"""
    df = safe_fetch(ak.stock_info_a_code_name)
    if df is None:
        return pd.DataFrame()
    df = df[~df["name"].str.contains("ST|退市", na=False)]
    df = df[df["code"].str.match(r"^(00|30|60)")]
    return df.reset_index(drop=True)


def get_realtime_quotes():
    """获取全A股实时行情"""
    df = safe_fetch(ak.stock_zh_a_spot_em)
    if df is None:
        return pd.DataFrame()
    return df


def get_batch_roe_data(date="20241231"):
    """批量获取全A股业绩报表（含ROE），一次调用获取所有股票"""
    df = safe_fetch(ak.stock_yjbb_em, date=date)
    if df is None:
        return pd.DataFrame()
    return df


def get_batch_dividend_data():
    """批量获取A股分红数据"""
    # 尝试从东财获取股息率排行
    try:
        # 用实时行情的方式获取，某些AKShare版本可能包含股息率
        df = safe_fetch(ak.stock_zh_a_spot_em)
        if df is not None and not df.empty:
            # 打印列名帮助调试
            print(f"  实时行情列名: {list(df.columns)}")
            return df
    except Exception as e:
        print(f"  获取股息率数据失败: {e}")
    return pd.DataFrame()


def get_financial_indicator(stock_code):
    """
    获取单只股票的财务分析指标
    多数据源自动切换，确保拿到数据：
    源1: stock_financial_analysis_indicator（新浪）
    源2: stock_financial_abstract_ths（同花顺）
    源3: stock_financial_benefit_ths（同花顺利润表）
    """
    # 源1: 新浪财务指标（最全，但部分股票返回空）
    df = safe_fetch(ak.stock_financial_analysis_indicator, symbol=stock_code)
    if df is not None and not df.empty:
        df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
        df = df.dropna(subset=["日期"])
        if not df.empty:
            return df

    # 源2: 同花顺财务摘要（覆盖面更广）
    try:
        df_ths = safe_fetch(ak.stock_financial_abstract_ths, symbol=stock_code)
        if df_ths is not None and not df_ths.empty:
            # 同花顺列名可能是乱码，按位置映射为标准列名
            cols = list(df_ths.columns)
            col_map = {}
            if len(cols) >= 25:
                col_map = {
                    cols[0]: "日期",
                    cols[12]: "销售净利率(%)",
                    cols[13]: "销售毛利率(%)",
                    cols[14]: "净资产收益率(%)",
                    cols[15]: "净资产收益率(扣非)(%)",
                    cols[7]: "每股收益(元)",
                    cols[8]: "每股净资产(元)",
                    cols[11]: "每股经营性现金流(元)",
                    cols[20]: "流动比率",
                    cols[24]: "资产负债率(%)",
                    cols[5]: "营业总收入(元)",
                    cols[6]: "营业总收入同比增长率(%)",
                }
            df_ths = df_ths.rename(columns=col_map)

            # 处理日期
            df_ths["日期"] = pd.to_datetime(df_ths["日期"], errors="coerce")
            df_ths = df_ths.dropna(subset=["日期"])

            # 清洗百分比字段（去掉%号转数字）
            for col in df_ths.columns:
                if "%" in col or "率" in col or "比率" in col:
                    df_ths[col] = df_ths[col].astype(str).str.replace("%", "").str.replace("False", "")
                    df_ths[col] = pd.to_numeric(df_ths[col], errors="coerce")

            if not df_ths.empty:
                return df_ths
    except Exception as e:
        pass

    return None


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


def get_dividend_yield(stock_code, price, industry=""):
    """
    计算股息率：最新2次分红加总（含预案）
    银行/铁路/电力等高频分红行业取3次
    多数据源：stock_history_dividend_detail
    """
    if not price or price <= 0:
        return 0
    try:
        df = safe_fetch(ak.stock_history_dividend_detail, symbol=stock_code, indicator="分红")
        if df is None or df.empty:
            return 0
        cols = list(df.columns)
        df[cols[0]] = pd.to_datetime(df[cols[0]], errors="coerce")
        df = df.sort_values(cols[0], ascending=False)
        df[cols[3]] = pd.to_numeric(df[cols[3]], errors="coerce")

        # 银行/铁路/电力等一年分3次的行业，取3次
        high_freq_industries = ["银行", "铁路", "电力", "高速", "公路"]
        take_n = 2
        for kw in high_freq_industries:
            if kw in str(industry):
                take_n = 3
                break

        top = df.head(take_n)
        total_per_10 = top[cols[3]].sum()
        div_per_share = total_per_10 / 10
        if div_per_share <= 0:
            return 0
        return round((div_per_share / price) * 100, 2)
    except Exception:
        return 0


def get_pe_ttm(stock_code):
    """获取准确的PE(TTM)，数据来源：百度股市通"""
    try:
        df = safe_fetch(
            ak.stock_zh_valuation_baidu,
            symbol=stock_code,
            indicator="市盈率(TTM)",
            period="近一年",
        )
        if df is not None and not df.empty:
            pe_ttm = pd.to_numeric(df.iloc[-1]["value"], errors="coerce")
            if not pd.isna(pe_ttm):
                return {"pe_ttm": pe_ttm}
    except Exception as e:
        print(f"  获取{stock_code} PE_TTM失败: {e}")
    return None
