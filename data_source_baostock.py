"""
Baostock 财务指标源

2026-04-23 引入：作为新浪 `stock_financial_analysis_indicator` + 同花顺
`stock_financial_abstract_ths` 都失败时的兜底。

Baostock 数据来自证券宝自维护落库（不是爬虫），接口稳定性高于 akshare
的爬虫源，但字段分散在 4 个接口里，本模块负责合并成一张 akshare 风格的
DataFrame。

限制：
  1. 只取年报（quarter=4），项目的 extract_annual_data 也只用年报数据
  2. 字段覆盖：ROE / 毛利率 / 净利率 / 资产负债率 / 流动比率 / 速动比率
     / 净利润同比 / 每股收益 / 净资产同比 等核心指标
  3. 不覆盖：营业利润率、每股经营现金流明细、固定资产周转率等偏门指标
     （项目主流程也用不到）
  4. Baostock 每次调用前要 login、完再 logout（走同一进程内复用，本模块自己管理）
"""
from typing import Optional
import pandas as pd

# login 状态在进程内全局保持（懒登录）
_LOGGED_IN = False


def _ensure_login() -> bool:
    """首次调用时登录 Baostock。已登录则直接返回 True。

    D-008（2026-04-24）：临时收紧 socket timeout 到 10 秒保护 bs.login()
    Baostock 的 login 走 TCP 直连证券宝服务器，如果服务器挂了 / GHA IP 被屏蔽，
    默认可能 hang 好几分钟。临时设 10s 让 login 快速失败，外层 fallback 到
    akshare 主源或直接返回 None。登录成功后恢复 data_fetcher 设置的 30s 默认值。
    """
    global _LOGGED_IN
    if _LOGGED_IN:
        return True
    import socket as _socket
    _orig_timeout = _socket.getdefaulttimeout()
    try:
        import baostock as bs
        _socket.setdefaulttimeout(10)  # login 限 10s
        r = bs.login()
        if r.error_code == "0":
            _LOGGED_IN = True
            return True
        print(f"  [baostock] 登录失败: {r.error_code} {r.error_msg}", flush=True)
        return False
    except Exception as e:
        print(f"  [baostock] 登录异常: {e}", flush=True)
        return False
    finally:
        _socket.setdefaulttimeout(_orig_timeout)


def _logout():
    """进程结束前调用（本模块里一般不主动调，让进程退出时自然释放）"""
    global _LOGGED_IN
    if _LOGGED_IN:
        try:
            import baostock as bs
            bs.logout()
        except Exception:
            pass
        _LOGGED_IN = False


def _format_code(stock_code: str) -> str:
    """6 位代码 → Baostock 的 `sh.600519` / `sz.000538` 格式"""
    c = str(stock_code).zfill(6)
    if c.startswith(("6", "5", "9", "68")):
        return f"sh.{c}"
    return f"sz.{c}"


def _fetch_rs(rs) -> list:
    """Baostock 的 ResultSet 转成 dict 列表"""
    fields = rs.fields
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(dict(zip(fields, rs.get_row_data())))
    return rows


def _to_float(v) -> Optional[float]:
    """Baostock 返回的是字符串，空串转 None"""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _to_percent(v) -> Optional[float]:
    """Baostock 百分比字段 → × 100 转百分数。

    所有字段一律按"存的是小数"处理。实测：
    - ROE / 毛利 / 净利 / 同比增长率：Baostock 一直存小数（如 0.384 = 38.4%）
    - liabilityToAsset（资产负债率）：2023 及之前存小数，2024/2025 数据
      源端脏（存成了"百分数再除以 100"，如 0.009201 表示 92.01%）
      → 这个字段本模块**不采集**，交给主源

    所以这里简单 × 100 即可，单位判断不需要。
    """
    f = _to_float(v)
    if f is None:
        return None
    return round(f * 100, 4)


def get_financial_analysis_via_baostock(stock_code: str,
                                         lookback_years: int = 10
                                         ) -> Optional[pd.DataFrame]:
    """取单只股票近 N 年年报财务指标，返回对齐 akshare 风格的 DataFrame。

    列（命名对齐 akshare `stock_financial_analysis_indicator`，
    这样项目的 find_column 子串匹配能直接命中）：
      日期 / 净资产收益率(%) / 加权净资产收益率(%) / 销售毛利率(%) /
      销售净利率(%) / 资产负债率(%) / 流动比率 / 速动比率 /
      净利润增长率(%) / 净资产增长率(%) / 总资产增长率(%) /
      每股收益TTM(元) / 主营业务收入(元) / 净利润(元)

    行按日期倒序（最新在前），和 akshare 一致。

    失败返回 None（登录失败 / 接口异常）。
    """
    if not _ensure_login():
        return None

    try:
        import baostock as bs
        from datetime import datetime
        bs_code = _format_code(stock_code)
        this_year = datetime.now().year

        rows_by_date = {}  # statDate → dict

        def _merge(rows):
            for r in rows:
                d = r.get("statDate")
                if not d:
                    continue
                rows_by_date.setdefault(d, {"日期": d}).update(r)

        # 近 N 年年报（quarter=4）。上市 < N 年的股票早期年份会返回空，无所谓
        # balance_data 只用来取 currentRatio/quickRatio，liabilityToAsset 故意舍弃
        for yr in range(this_year, this_year - lookback_years - 1, -1):
            try:
                _merge(_fetch_rs(bs.query_profit_data(code=bs_code, year=yr, quarter=4)))
                _merge(_fetch_rs(bs.query_balance_data(code=bs_code, year=yr, quarter=4)))
                _merge(_fetch_rs(bs.query_growth_data(code=bs_code, year=yr, quarter=4)))
            except Exception as e:
                print(f"  [baostock] {stock_code} {yr} 查询异常: {e}", flush=True)
                continue

        if not rows_by_date:
            print(f"  [baostock] {stock_code} 无数据", flush=True)
            return None

        # 转换字段。百分比字段用 _to_percent 做单位智能修正
        out_rows = []
        for d in sorted(rows_by_date.keys(), reverse=True):  # 最新在前
            r = rows_by_date[d]
            roe_pct = _to_percent(r.get("roeAvg"))

            out_rows.append({
                "日期": d,
                "净资产收益率(%)": roe_pct,
                "加权净资产收益率(%)": roe_pct,
                "销售毛利率(%)": _to_percent(r.get("gpMargin")),
                "销售净利率(%)": _to_percent(r.get("npMargin")),
                # "资产负债率(%)" 故意不给 —— Baostock 2024/2025 年数据脏
                # 项目 find_column 找不到这列时会跳过负债率相关规则，安全
                "流动比率": _to_float(r.get("currentRatio")),
                "速动比率": _to_float(r.get("quickRatio")),
                "净利润增长率(%)": _to_percent(r.get("YOYNI")),
                "净资产增长率(%)": _to_percent(r.get("YOYEquity")),
                "总资产增长率(%)": _to_percent(r.get("YOYAsset")),
                "每股收益TTM(元)": _to_float(r.get("epsTTM")),
                "主营业务收入(元)": _to_float(r.get("MBRevenue")),
                "净利润(元)": _to_float(r.get("netProfit")),
            })

        df = pd.DataFrame(out_rows)
        df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
        df = df.dropna(subset=["日期"])
        return df.reset_index(drop=True)
    except Exception as e:
        print(f"  [baostock] {stock_code} 失败: {e}", flush=True)
        return None


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    # 测 3 只典型股票
    for code in ("600519", "000538", "601398"):  # 白酒/中药/银行
        print(f"\n==== {code} ====")
        df = get_financial_analysis_via_baostock(code, lookback_years=5)
        if df is None or df.empty:
            print("失败")
        else:
            print(df[["日期", "净资产收益率(%)", "销售毛利率(%)",
                      "流动比率", "速动比率", "净利润增长率(%)"]].to_string(index=False))
    _logout()
