"""
数据获取模块 - 使用 AKShare 获取A股财务和估值数据
优化版：使用批量接口，避免逐个股票查询
"""

import json
import os
import time
import platform
import akshare as ak
import pandas as pd
import numpy as np

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_INDUSTRY_CACHE_FILE = os.path.join(_SCRIPT_DIR, "stock_industry_cache.json")
_industry_cache_mem = None  # 进程内存缓存，避免反复读文件


# BUG-034 原方案：safe_fetch 内层 SIGALRM 已被 BUG-037 取代
# BUG-037：把 SIGALRM 移到 screener.screen_all_stocks 循环外层（每只股 60s）
# 原因：china_adjustments.py 里 9 处直接 ak.xxx() 调用没走 safe_fetch
#       内层 SIGALRM 保护不到 → 某只股进入这些函数会被永远卡死（log 显示 69 分钟无进度）
# 解决：safe_fetch 回到"无 timeout"的原始设计，外层在调用 screen_single_stock 时
#       统一用 SIGALRM(60) 包住，不论内部调用走没走 safe_fetch

# 给 screener.py 共享的工具（screener.py 会 from data_fetcher import _USE_ALARM, _alarm_handler）
_USE_ALARM = platform.system() == 'Linux'
if _USE_ALARM:
    import signal

    class _FetchTimeout(Exception):
        pass

    def _alarm_handler(signum, frame):
        raise _FetchTimeout("call timeout")
else:
    class _FetchTimeout(Exception):
        pass


def safe_fetch(func, *args, retry=2, delay=2, timeout=None, **kwargs):
    """带重试的安全请求（原始版本）
    BUG-037：内层 timeout 改为不实际生效（参数保留向后兼容），
             实际超时控制由外层 screen_single_stock 包装的 SIGALRM(60s) 统一管
    """
    for i in range(retry):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if i < retry - 1:
                time.sleep(delay)
            else:
                print(f"  获取数据失败: {e}")
                return None


def batch_fetch_with_timeout(func, *args, timeout_sec=90, retry=3, delay=5,
                              alt_funcs=None, **kwargs):
    """BUG-040：批量接口专用包装 - 超时 + retry + 多源 fallback

    修复 BUG-039 回归：
    - BUG-039 的实现遇到 ConnectionResetError 等网络异常直接 return None
    - 实际 GHA runner 访问东财时经常被 reset connection（IP 段限流）
    - 一次 reset 就放弃 → 候选 0 只 → 整个 run 白跑

    新策略：
    1. 主源（func）用 SIGALRM timeout 包 + retry N 次，每次间隔 `delay + i*2` 秒
    2. 超时 / ConnectionReset / 其他网络异常都触发 retry
    3. 主源全失败 → 依次尝试 alt_funcs 备用源（不带 timeout，带 retry）
    4. 所有源全失败才 return None

    Args:
        func: 主数据源函数（比如 ak.stock_zh_a_spot_em）
        timeout_sec: 主源每次调用的硬超时（默认 90s）
        retry: 每个源重试次数（默认 3 次）
        delay: 重试间隔基础秒数（默认 5s，递增 backoff）
        alt_funcs: 备用源函数列表（如 [ak.stock_zh_a_spot]）
    """
    sources = [('主源-em', func)] + [(f'备用源-{i+1}', f)
                                       for i, f in enumerate(alt_funcs or [])]
    last_err = None
    for src_label, src in sources:
        for attempt in range(retry):
            try:
                # 仅主源用 SIGALRM timeout；备用源用 safe_fetch 内部重试
                use_timeout = (src is func) and _USE_ALARM
                if use_timeout:
                    signal.signal(signal.SIGALRM, _alarm_handler)
                    signal.alarm(timeout_sec)
                try:
                    result = src(*args, **kwargs)
                finally:
                    if use_timeout:
                        signal.alarm(0)
                # 成功（非空 DataFrame 或非 None）
                if result is not None and (not hasattr(result, 'empty') or not result.empty):
                    if src is not func:
                        print(f"  [OK] {src_label} 成功取到数据", flush=True)
                    return result
                else:
                    last_err = "返回空"
                    print(f"  [{src_label} attempt {attempt+1}/{retry}] 返回空数据", flush=True)
            except _FetchTimeout:
                last_err = f"timeout {timeout_sec}s"
                print(f"  [{src_label} 超时{timeout_sec}s attempt {attempt+1}/{retry}]",
                      flush=True)
            except Exception as e:
                last_err = e
                err_type = type(e).__name__
                print(f"  [{src_label} {err_type} attempt {attempt+1}/{retry}]: {e}",
                      flush=True)
            # 不是最后一次 → sleep backoff 后重试
            if attempt < retry - 1:
                time.sleep(delay + attempt * 2)
        # 当前源全失败，进入下一个备用源
        if src is not sources[-1][1]:
            print(f"  [!] {src_label} 全部重试失败，切换备用源", flush=True)

    print(f"  [X] 所有源都失败: {last_err}", flush=True)
    return None


def get_all_stocks():
    """获取全部A股列表，过滤ST和北交所
    BUG-039：批量接口用独立 SIGALRM 超时 90s（批量调用在 screener for 循环之前，不冲突）
    """
    df = batch_fetch_with_timeout(ak.stock_info_a_code_name, timeout_sec=90)
    if df is None:
        return pd.DataFrame()
    df = df[~df["name"].str.contains("ST|退市", na=False)]
    df = df[df["code"].str.match(r"^(00|30|60)")]
    return df.reset_index(drop=True)


def get_realtime_quotes():
    """获取全A股实时行情（不含行业字段，行业请用 get_stock_industry）

    BUG-040：主源东财（stock_zh_a_spot_em）失败时走新浪源（stock_zh_a_spot）
    - 两个接口列名都有"代码"和"最新价"
    - em 代码格式："000001"（无前缀），sina 代码格式："sz000001"（带 sh/sz/bj）
    - 兜底时自动去前缀，保持 screener 调用端格式一致
    """
    df = batch_fetch_with_timeout(
        ak.stock_zh_a_spot_em,
        timeout_sec=90,
        retry=3,
        delay=5,
        alt_funcs=[ak.stock_zh_a_spot],  # 新浪源兜底
    )
    if df is None or df.empty:
        return pd.DataFrame()

    # 规范化代码格式：若走了新浪源，代码带 sh/sz/bj 前缀，去掉保持和 em 一致
    try:
        if "代码" in df.columns:
            sample = str(df["代码"].iloc[0])
            if sample[:2].lower() in ("sh", "sz", "bj") and len(sample) == 8:
                df = df.copy()
                df["代码"] = df["代码"].astype(str).str[2:]
                print(f"  [规范化] 去掉代码前缀（新浪源）", flush=True)
    except Exception as _e:
        print(f"  [!] 代码规范化失败（不影响主流程）: {_e}", flush=True)
    return df


_etf_quotes_cache = {"data": None, "fetched_at": 0}


def get_etf_realtime_quotes(ttl_sec=1800):
    """
    获取全 A 股 ETF 实时行情（用 ak.fund_etf_spot_em）

    注意：stock_zh_a_spot_em 只含个股，不含 ETF。ETF 的最新价、昨收、
    涨跌幅必须走这个独立接口。

    返回 DataFrame，列包括：代码、名称、最新价、涨跌幅、昨收、开盘价 等

    进程内缓存 ttl_sec 秒（默认 30 分钟），避免同一次 main.py 运行里
    被持仓检查、ETF 监测等多次调用时反复拉取（这个接口比较慢，要 1-2 分钟）
    """
    global _etf_quotes_cache
    now = time.time()
    if (_etf_quotes_cache["data"] is not None
            and now - _etf_quotes_cache["fetched_at"] < ttl_sec):
        return _etf_quotes_cache["data"]

    # BUG-039：ETF 批量接口独立 SIGALRM 90s 超时
    df = batch_fetch_with_timeout(ak.fund_etf_spot_em, timeout_sec=90)
    if df is None or df.empty:
        return pd.DataFrame()

    _etf_quotes_cache["data"] = df
    _etf_quotes_cache["fetched_at"] = now
    return df


def get_etf_price(code, etf_quotes_df=None):
    """
    获取单只 ETF 的最新价
    Args:
      code: 6 位 ETF 代码
      etf_quotes_df: 可选，传入已拉好的 get_etf_realtime_quotes 结果
                     避免对每只 ETF 都调接口
    Returns:
      float 最新价，没数据返回 None
    """
    if etf_quotes_df is None:
        etf_quotes_df = get_etf_realtime_quotes()
    if etf_quotes_df is None or etf_quotes_df.empty:
        return None
    code = str(code).zfill(6)
    row = etf_quotes_df[etf_quotes_df["代码"] == code]
    if row.empty:
        return None
    try:
        price = float(row.iloc[0]["最新价"])
        if price > 0:
            return price
    except (ValueError, TypeError):
        pass
    return None


def _load_industry_cache():
    """读本地行业缓存到内存"""
    global _industry_cache_mem
    if _industry_cache_mem is not None:
        return _industry_cache_mem
    if os.path.exists(_INDUSTRY_CACHE_FILE):
        try:
            with open(_INDUSTRY_CACHE_FILE, "r", encoding="utf-8") as f:
                _industry_cache_mem = json.load(f)
        except Exception:
            _industry_cache_mem = {}
    else:
        _industry_cache_mem = {}
    return _industry_cache_mem


def _save_industry_cache():
    """把内存缓存写回文件"""
    if _industry_cache_mem is None:
        return
    try:
        with open(_INDUSTRY_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_industry_cache_mem, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  行业缓存写入失败: {e}")


def get_stock_industry(code, fallback=""):
    """
    获取 A 股个股的真实行业（带本地持久化缓存）

    数据源：ak.stock_individual_info_em 的"行业"字段（如"中药Ⅱ"、"白酒"、"银行"等）
    缓存：stock_industry_cache.json，结构 {code: {"industry": str, "name": str, "updated": "YYYY-MM-DD"}}

    Args:
      code: 股票代码（6位）
      fallback: 查不到时返回的兜底值（通常传用户在 holdings.json 手填的 category）

    Returns:
      行业字符串（永不返回 None）

    注意：
      - 只对 A 股个股有效；ETF/基金代码直接返回 fallback，不会调接口
      - 行业变化极慢，缓存永久有效（手动删 cache 文件触发刷新）
    """
    code = str(code).zfill(6)

    # ETF/基金（1/5 开头）和北交所等不走这个接口
    if not code or not code[0].isdigit():
        return fallback
    if code[0] in ("1", "5"):
        return fallback  # ETF，不需要行业
    if not code.startswith(("00", "30", "60", "68")):
        return fallback  # 只处理沪深主板/创业板/科创

    cache = _load_industry_cache()
    if code in cache and cache[code].get("industry"):
        return cache[code]["industry"]

    # 未命中缓存，调接口
    try:
        df = safe_fetch(ak.stock_individual_info_em, symbol=code)
        if df is None or df.empty:
            return fallback
        kv = dict(zip(df["item"].astype(str), df["value"].astype(str)))
        industry = kv.get("行业", "").strip()
        name = kv.get("股票简称", "").strip()
        if not industry:
            return fallback
        cache[code] = {
            "industry": industry,
            "name": name,
            "updated": time.strftime("%Y-%m-%d"),
        }
        _save_industry_cache()
        return industry
    except Exception as e:
        print(f"  获取 {code} 行业失败: {e}")
        return fallback


def get_batch_roe_data(date="20241231"):
    """批量获取全A股业绩报表（含ROE），一次调用获取所有股票

    BUG-039：批量 ROE 接口用独立 SIGALRM 90s 超时
    """
    df = batch_fetch_with_timeout(ak.stock_yjbb_em, date=date, timeout_sec=90)
    if df is None:
        return pd.DataFrame()
    return df


def get_batch_dividend_data():
    """批量获取A股分红数据"""
    # 尝试从东财获取股息率排行
    try:
        # 用实时行情的方式获取，某些AKShare版本可能包含股息率
        # BUG-036：批量接口给 90s 超时
        df = safe_fetch(ak.stock_zh_a_spot_em, timeout=90)
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
    源1: stock_financial_analysis_indicator（新浪，列名"净资产收益率(%)"等）
    源2: stock_financial_abstract_ths（同花顺，列名"净资产收益率"等）

    注意：以前同花顺版本列名是乱码需要按位置映射，当前版本直接返回中文
    列名，无需 col_map。银行股列数 17（没有毛利率/流动比率是正常的），
    铁路股 23 列。不再强制 len(cols) >= 25。
    """
    # 源1: 新浪财务指标（最全，但部分股票返回空，如银行/铁路）
    df = safe_fetch(ak.stock_financial_analysis_indicator, symbol=stock_code)
    if df is not None and not df.empty:
        df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
        df = df.dropna(subset=["日期"])
        if not df.empty:
            return df

    # 源2: 同花顺财务摘要（银行/铁路/保险等覆盖面更广）
    try:
        df_ths = safe_fetch(ak.stock_financial_abstract_ths, symbol=stock_code)
        if df_ths is not None and not df_ths.empty:
            # 统一列名：报告期 → 日期（后续 extract_annual_data 用的是"日期"）
            if "报告期" in df_ths.columns and "日期" not in df_ths.columns:
                df_ths = df_ths.rename(columns={"报告期": "日期"})

            df_ths["日期"] = pd.to_datetime(df_ths["日期"], errors="coerce")
            df_ths = df_ths.dropna(subset=["日期"])

            # 清洗：
            # 1. False（akshare 把缺失值返回字符串 False）→ NaN
            # 2. 去掉 "%" 和 "亿"/"万" 单位
            # 3. 转数字
            # 同花顺所有"率/比率"列都带 %，经营现金流/每股收益是纯数字
            for col in df_ths.columns:
                if col == "日期":
                    continue
                s = df_ths[col]
                # 先统一为字符串再清洗
                s = s.astype(str).replace({"False": None, "nan": None, "None": None})
                # 百分比和纯数字两种都有，统一去掉 % 符号
                s = s.str.replace("%", "", regex=False)
                # "亿" / "万" 单位：这些字段（营业收入、净利润）打分用不到，
                # 转不了数字就让它是 NaN 即可
                df_ths[col] = pd.to_numeric(s, errors="coerce")

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
    """提取利润率序列
    优先级：营业利润率 > 销售利润率 > 销售净利率（akshare 默认字段名）
    备注：akshare stock_financial_analysis_indicator 实际只返回"销售净利率"和"销售毛利率"
    """
    col = find_column(df_annual, ["营业利润率", "销售利润率", "销售净利率"])
    if col is None:
        return None
    return pd.to_numeric(df_annual[col], errors="coerce").dropna()


def get_fcf_series(df_annual):
    """提取每股经营现金流序列（近似自由现金流）
    优先级：每股经营性现金流 > 每股经营现金流量 > 每股经营现金流（akshare 默认字段名）
    备注：find_column 是子串匹配不支持正则，必须列出所有可能字段名
    """
    col = find_column(df_annual, ["每股经营性现金流", "每股经营现金流量", "每股经营现金流"])
    if col is None:
        col = find_column(df_annual, ["经营现金", "经营性现金"])
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
