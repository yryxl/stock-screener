"""
Pytdx 实时行情源（通达信协议，走 TCP 直连券商服务器）

2026-04-23 引入：作为东财 / 新浪两源都挂时的最后兜底。
通达信协议和 HTTP 爬虫完全不同路径，能有效对抗"东财+新浪同挂"的极端场景
（昨天 BUG-040 就差点因此候选 0 只）。

返回 DataFrame 对齐 akshare `stock_zh_a_spot_em` 的字段：
  代码 / 名称 / 最新价 / 涨跌幅 / 涨跌额 / 昨收 / 今开 / 最高 / 最低 / 成交量 / 成交额

限制：
  1. Pytdx 的 `get_security_list` 单服务器只返回约 7000 条（含基金/债/指数），
     过滤后 A 股主板+创业+科创大概 1500+ 只，**少于 akshare 的 5000+ 只**
  2. 仅覆盖主流活跃股，北交所和部分小盘可能缺。作为兜底源可接受
  3. ETF 价格在通达信协议里是 /1000 单位（分）不是元，个股是 /100（分），
     需要分别处理（本模块只拉 A 股个股）
"""
from typing import List, Optional
import time

import pandas as pd

# 多个通达信服务器，首个连不上自动尝试下一个
# 取自 pytdx 源码里内置的主流券商行情服务器
_PYTDX_HOSTS = [
    ("115.238.56.198", 7709),   # 杭州
    ("119.147.212.81", 7709),   # 广州
    ("180.153.18.170", 7709),   # 上海
    ("218.108.47.69", 7709),    # 杭州 2
    ("123.125.108.14", 7709),   # 北京
]

_CONNECT_TIMEOUT = 3.0


def _connect():
    """连上一个可用的 Pytdx 服务器。失败返回 None。"""
    from pytdx.hq import TdxHq_API
    api = TdxHq_API()
    for host, port in _PYTDX_HOSTS:
        try:
            if api.connect(host, port, time_out=_CONNECT_TIMEOUT):
                return api, (host, port)
        except Exception:
            continue
    return None, None


def _market_of(code: str) -> int:
    """沪 1 / 深 0"""
    c = str(code).zfill(6)
    if c.startswith(("6", "5", "9")):
        return 1
    return 0


def get_realtime_quotes_pytdx() -> Optional[pd.DataFrame]:
    """全 A 股实时行情，接口对齐 `ak.stock_zh_a_spot_em`。

    返回 DataFrame，列：代码、名称、最新价、涨跌幅、涨跌额、昨收、今开、最高、最低、
    成交量、成交额。失败返回 None。
    """
    api, host = _connect()
    if api is None:
        print("  [pytdx] 所有行情服务器都连不上", flush=True)
        return None

    try:
        # 1. 拉全市场代码表（沪 + 深 各自分页）
        t0 = time.time()
        stocks_meta = []  # [(market, code, name), ...]
        for market in (0, 1):
            start = 0
            while True:
                try:
                    batch = api.get_security_list(market, start)
                except Exception as e:
                    print(f"  [pytdx] get_security_list(market={market}, start={start}) "
                          f"失败: {e}", flush=True)
                    break
                if not batch:
                    break
                for s in batch:
                    stocks_meta.append((market, s["code"], s["name"]))
                start += len(batch)
                if len(batch) < 1000:
                    break

        # 过滤 A 股主板 / 创业板 / 科创板（剔除基金/指数/债券）
        # 沪：60/68 开头；深：00/30 开头
        stocks_meta = [
            (m, c, n) for m, c, n in stocks_meta
            if ((m == 1 and c.startswith(("60", "68")))
                or (m == 0 and c.startswith(("00", "30"))))
            and "ST" not in (n or "")
            and "退" not in (n or "")
        ]
        if not stocks_meta:
            print("  [pytdx] 过滤后无股票", flush=True)
            return None

        # 2. 分批查实时行情（每批 50 只）
        BATCH = 50
        all_quotes = []
        for i in range(0, len(stocks_meta), BATCH):
            chunk = [(m, c) for m, c, _ in stocks_meta[i:i + BATCH]]
            try:
                res = api.get_security_quotes(chunk)
            except Exception as e:
                print(f"  [pytdx] 批次 {i}~{i+BATCH} 查行情失败: {e}", flush=True)
                continue
            if res:
                # 把 name 拼回去（pytdx 行情响应不含 name）
                for j, q in enumerate(res):
                    meta_idx = i + j
                    if meta_idx < len(stocks_meta):
                        q["_name"] = stocks_meta[meta_idx][2]
                all_quotes.extend(res)

        elapsed = time.time() - t0
        print(f"  [pytdx] 全市场行情耗时 {elapsed:.1f}s，共 {len(all_quotes)} 条"
              f"（源 {host[0]}）", flush=True)

        # 3. 转成 akshare 风格的 DataFrame
        rows = []
        for q in all_quotes:
            price = q.get("price", 0.0) or 0.0
            last_close = q.get("last_close", 0.0) or 0.0
            chg = price - last_close
            chg_pct = (chg / last_close * 100) if last_close else 0.0
            rows.append({
                "代码": q.get("code", ""),
                "名称": q.get("_name", ""),
                "最新价": round(price, 4),
                "涨跌幅": round(chg_pct, 2),
                "涨跌额": round(chg, 4),
                "昨收": round(last_close, 4),
                "今开": round(q.get("open", 0.0) or 0.0, 4),
                "最高": round(q.get("high", 0.0) or 0.0, 4),
                "最低": round(q.get("low", 0.0) or 0.0, 4),
                "成交量": int(q.get("vol", 0) or 0),
                "成交额": float(q.get("amount", 0.0) or 0.0),
            })
        df = pd.DataFrame(rows)
        # 丢弃停牌/零价（last_close=0 或 price=0）
        df = df[df["最新价"] > 0]
        return df.reset_index(drop=True)
    finally:
        try:
            api.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    df = get_realtime_quotes_pytdx()
    if df is None or df.empty:
        print("失败")
    else:
        print(f"成功 {len(df)} 只")
        print(df.head(10))
