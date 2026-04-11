"""
ETF 估值监测模块

双通道数据源：
  - 宽基（沪深300/上证50/中证500 等）：乐股网 stock_index_pe_lg 有 15-21 年全历史
  - 策略/行业（红利低波/消费50/电网等）：中证官方 stock_zh_index_value_csindex 每次只返回 20 条
    → 靠每日增量追加，逐步积累自己的历史分位

本模块与 market_temperature.py 的关系：
  - market_temperature.py 监测"沪深300 大盘温度"（整体市场冷热）
  - etf_monitor.py 监测"每只 ETF 的独立温度"（具体持仓的冷热）
  - 两者都复用 TEMP_LEVELS 的 5 档文案，保持体验一致

重要提醒（写入注释永久记住）：
  宽基 ETF 不是类固收，它仍是权益资产。2008 年标普 500 跌 37%，
  2015 年沪深 300 半年跌 43%。本模块只做估值监测，不改变 ETF 的
  风险属性。前端展示时也必须把 ETF 计入股票总仓位。
"""

import json
import os
import time
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd

from market_temperature import TEMP_LEVELS

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ETF_DATA_DIR = os.path.join(SCRIPT_DIR, "backtest_data", "etf_valuation")
ETF_MAP_FILE = os.path.join(SCRIPT_DIR, "etf_index_map.json")

# 乐股网仅支持这几只宽基（名称为 akshare 的调用参数）
LEGU_BROAD_NAMES = {
    "000300": "沪深300",
    "000016": "上证50",
    "000905": "中证500",
    "000852": "中证1000",
    "000903": "中证100",
    "000010": "上证180",
}


# ============================================================
# 映射表读取
# ============================================================

def load_etf_index_map():
    """读 etf_index_map.json 返回 {etf_code: {index, name, kind}}"""
    if not os.path.exists(ETF_MAP_FILE):
        return {}
    with open(ETF_MAP_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return raw.get("map", {})


# ============================================================
# 数据采集（双通道）
# ============================================================

def _fetch_csindex(index_code, retries=3):
    """中证官方接口：20 条最近估值"""
    for i in range(retries):
        try:
            df = ak.stock_zh_index_value_csindex(symbol=index_code)
            if df is None or df.empty:
                return []
            records = []
            for _, row in df.iterrows():
                date = str(row.get("日期", ""))[:10]
                pe = row.get("市盈率2")
                if pe is None or pd.isna(pe):
                    pe = row.get("市盈率1")
                if pe is None or pd.isna(pe):
                    continue
                div = row.get("股息率2")
                if div is None or pd.isna(div):
                    div = row.get("股息率1")
                records.append({
                    "date": date,
                    "pe": float(pe),
                    "dividend_yield": float(div) if div and not pd.isna(div) else None,
                    "source": "csindex",
                })
            return records
        except Exception as e:
            if i < retries - 1:
                time.sleep(2)
                continue
            print(f"  中证官方拉取 {index_code} 失败: {e}")
            return []
    return []


def _fetch_legu(index_legu_name, retries=3):
    """乐股网接口：宽基全历史日频 PE"""
    for i in range(retries):
        try:
            df = ak.stock_index_pe_lg(symbol=index_legu_name)
            if df is None or df.empty:
                return []
            records = []
            for _, row in df.iterrows():
                date = str(row.get("日期", ""))[:10]
                pe = row.get("滚动市盈率")
                if pe is None or pd.isna(pe):
                    continue
                pe_median = row.get("滚动市盈率中位数")
                records.append({
                    "date": date,
                    "pe": float(pe),
                    "pe_median": float(pe_median) if pe_median and not pd.isna(pe_median) else None,
                    "source": "legu",
                })
            return records
        except Exception as e:
            if i < retries - 1:
                time.sleep(2)
                continue
            print(f"  乐股网拉取 {index_legu_name} 失败: {e}")
            return []
    return []


# ============================================================
# 本地存储（增量追加，从不覆盖）
# ============================================================

def _store_path(index_code):
    return os.path.join(ETF_DATA_DIR, f"{index_code}.json")


def load_index_history(index_code):
    """读本地历史，不存在时返回空结构"""
    path = _store_path(index_code)
    if not os.path.exists(path):
        return {
            "index_code": index_code,
            "index_name": "",
            "kind": "",
            "data": [],
            "last_updated": None,
        }
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_index_history(store):
    """存本地历史"""
    os.makedirs(ETF_DATA_DIR, exist_ok=True)
    with open(_store_path(store["index_code"]), "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


# ============================================================
# 增量更新主函数
# ============================================================

def update_index_valuation(index_code, index_name, kind):
    """
    更新单只指数的估值历史
    返回 (store, new_records_count)
    """
    store = load_index_history(index_code)
    store["index_code"] = index_code
    store["index_name"] = index_name
    store["kind"] = kind

    existing_dates = {r["date"] for r in store.get("data", [])}

    # 宽基且本地数据不足时，用乐股网冷启动一次性拉全历史
    cold_start_threshold = 100
    if kind == "broad" and len(existing_dates) < cold_start_threshold:
        legu_name = LEGU_BROAD_NAMES.get(index_code)
        if legu_name:
            legu_records = _fetch_legu(legu_name)
            if legu_records:
                added_cold = 0
                for r in legu_records:
                    if r["date"] not in existing_dates:
                        store["data"].append(r)
                        existing_dates.add(r["date"])
                        added_cold += 1
                print(f"  {index_code} {index_name}: 乐股网冷启动 +{added_cold} 条")

    # 所有指数都跑一次中证官方拿最新 20 条做增量
    cs_records = _fetch_csindex(index_code)
    added = 0
    for r in cs_records:
        if r["date"] not in existing_dates:
            store["data"].append(r)
            existing_dates.add(r["date"])
            added += 1

    store["data"].sort(key=lambda x: x["date"])
    store["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    save_index_history(store)
    return store, added


# ============================================================
# 分位和温度计算
# ============================================================

def compute_etf_temperature(store, lookback_years=10):
    """
    根据历史数据计算当前温度档位和分位
    返回 dict: level, percentile, current_pe, current_dividend_yield, as_of, data_points, note
    """
    data = store.get("data", [])
    if not data:
        return {
            "level": 0,
            "percentile": None,
            "current_pe": None,
            "current_dividend_yield": None,
            "as_of": None,
            "data_points": 0,
            "note": "无数据",
        }

    sorted_data = sorted(data, key=lambda x: x["date"])
    latest = sorted_data[-1]
    current_pe = latest["pe"]
    current_div = latest.get("dividend_yield")
    latest_date = latest["date"]

    # 数据不足 60 条时不判分位（保守：不误导）
    if len(sorted_data) < 60:
        return {
            "level": 0,
            "percentile": None,
            "current_pe": current_pe,
            "current_dividend_yield": current_div,
            "as_of": latest_date,
            "data_points": len(sorted_data),
            "note": f"数据积累不足（{len(sorted_data)}条，需≥60条才能判定历史分位）",
        }

    # 10 年窗口
    try:
        cutoff_date = (datetime.strptime(latest_date, "%Y-%m-%d")
                       - timedelta(days=365 * lookback_years)).strftime("%Y-%m-%d")
    except Exception:
        cutoff_date = "1900-01-01"

    hist_pe = [r["pe"] for r in sorted_data[:-1] if r["date"] >= cutoff_date]
    if len(hist_pe) < 60:
        # 10 年窗口数据不够，退化到用全部历史
        hist_pe = [r["pe"] for r in sorted_data[:-1]]

    below = sum(1 for v in hist_pe if v < current_pe)
    percentile = below / len(hist_pe) * 100 if hist_pe else 50.0

    if percentile >= 85:
        level = 2
    elif percentile >= 70:
        level = 1
    elif percentile <= 15:
        level = -2
    elif percentile <= 30:
        level = -1
    else:
        level = 0

    label, desc = TEMP_LEVELS.get(level, ("⚪ 未知", ""))

    return {
        "level": level,
        "label": label,
        "description": desc,
        "percentile": round(percentile, 1),
        "current_pe": round(current_pe, 2),
        "current_dividend_yield": round(current_div, 2) if current_div else None,
        "as_of": latest_date,
        "data_points": len(hist_pe) + 1,
        "lookback_years": lookback_years,
    }


# ============================================================
# 卖出信号
# ============================================================

def get_etf_action_signal(temp):
    """
    基于温度档位给出 ETF 买卖建议（按巴菲特/芒格理念调整文案）

    文案原则：
    - 70-85% 分位：只说"暂停加仓"，不说"卖" —— 巴菲特的做法是停止加码而非减仓
    - >85% 分位：说"卖盈利保底仓"，不说"全部清仓" —— 芒格的"贵到不舒服"做法
    - <15% 分位：说"全力加仓"，呼应 2008 年巴菲特"我在买入美国"

    signal key 保持 sell_heavy / sell_light / buy_heavy / buy_light / hold 不变，
    避免影响个股/回测系统的下游判断。仅改 signal_text 的表达。
    """
    level = temp.get("level", 0)
    pct = temp.get("percentile")

    if pct is None:
        return "hold", f"数据积累中（{temp.get('data_points',0)}条，需≥60条才能判分位）"

    if level == 2:
        return "sell_heavy", (
            f"PE分位{pct}%·泡沫区·卖盈利部分·保留底仓"
        )
    if level == 1:
        return "sell_light", (
            f"PE分位{pct}%·偏热·暂停加仓·新钱转便宜标的"
        )
    if level == -2:
        return "buy_heavy", (
            f"PE分位{pct}%·低估区·全力加仓"
        )
    if level == -1:
        return "buy_light", (
            f"PE分位{pct}%·偏冷·重点加仓"
        )
    return "hold", f"PE分位{pct}%·正常·按节奏"


# ============================================================
# 主流程：扫描持仓中的 ETF 并更新
# ============================================================

def _is_etf_code(code):
    """粗判是否 ETF：A股 ETF 代码一般以 1/5 开头"""
    if not code:
        return False
    c = str(code).zfill(6)
    return c[0] in ("1", "5")


def extract_etfs_from_holdings(holdings):
    """从持仓列表中挑出 ETF（code + name）"""
    etfs = []
    for h in holdings or []:
        code = str(h.get("code", "")).zfill(6)
        if _is_etf_code(code):
            etfs.append({
                "code": code,
                "name": h.get("name", ""),
                "shares": h.get("shares", 0),
                "cost": h.get("cost", 0),
            })
    return etfs


def scan_and_update_holdings_etfs(holdings):
    """
    对持仓中的每只 ETF 更新估值并返回监测结果
    返回 list，每项含 ETF 基本信息 + 温度 + 信号
    未映射的 ETF 单独返回警告
    """
    etf_map = load_etf_index_map()
    etfs = extract_etfs_from_holdings(holdings)
    if not etfs:
        return [], []

    results = []
    unmapped = []

    for etf in etfs:
        code = etf["code"]
        if code not in etf_map:
            unmapped.append(etf)
            continue

        mapping = etf_map[code]
        index_code = mapping["index"]
        index_name = mapping["name"]
        kind = mapping.get("kind", "strategy")

        try:
            store, added = update_index_valuation(index_code, index_name, kind)
            temp = compute_etf_temperature(store)
            signal, signal_text = get_etf_action_signal(temp)
            results.append({
                **etf,
                "index_code": index_code,
                "index_name": index_name,
                "kind": kind,
                "temperature": temp,
                "signal": signal,
                "signal_text": signal_text,
                "new_records": added,
            })
            print(f"  {code} {etf['name']}: {index_name} PE={temp.get('current_pe')} "
                  f"分位={temp.get('percentile')}% {temp.get('label','')} [+{added}]")
        except Exception as e:
            print(f"  {code} {etf['name']} 更新失败: {e}")
            results.append({
                **etf,
                "index_code": index_code,
                "index_name": index_name,
                "kind": kind,
                "error": str(e),
            })

    if unmapped:
        print(f"\n⚠ 以下 ETF 未在 etf_index_map.json 中映射，请手工补充：")
        for e in unmapped:
            print(f"   {e['code']} {e['name']}")

    return results, unmapped


# ============================================================
# 组合仓位分类
# ============================================================

def classify_portfolio(holdings):
    """
    对持仓做分类统计（宽基 ETF / 策略 ETF / 行业 ETF / 个股）
    返回 dict：
      total_value / broad_etf / strategy_etf / sector_etf / single_stock
      每项含 value（无最新价则 =成本*股数）和 pct
    注意：宽基 ETF 依然计入股票总仓位，不降级为"类固收"
    """
    etf_map = load_etf_index_map()
    buckets = {
        "broad_etf": {"value": 0, "items": []},
        "strategy_etf": {"value": 0, "items": []},
        "sector_etf": {"value": 0, "items": []},
        "single_stock": {"value": 0, "items": []},
    }

    for h in holdings or []:
        code = str(h.get("code", "")).zfill(6)
        shares = h.get("shares", 0) or 0
        cost = h.get("cost", 0) or 0
        value = shares * cost  # 如需最新价在调用处替换

        if _is_etf_code(code):
            mapping = etf_map.get(code, {})
            kind = mapping.get("kind", "")
            if kind == "broad":
                bucket = "broad_etf"
            elif kind.startswith("strategy"):
                bucket = "strategy_etf"
            elif kind == "sector":
                bucket = "sector_etf"
            else:
                bucket = "sector_etf"  # 未映射的 ETF 默认归行业
        else:
            bucket = "single_stock"

        buckets[bucket]["value"] += value
        buckets[bucket]["items"].append({
            "code": code,
            "name": h.get("name", ""),
            "value": value,
        })

    total = sum(b["value"] for b in buckets.values())
    for b in buckets.values():
        b["pct"] = (b["value"] / total * 100) if total > 0 else 0

    # 防御型（宽基）vs 进攻型（策略/行业/个股）
    defensive = buckets["broad_etf"]["value"]
    offensive = (buckets["strategy_etf"]["value"]
                 + buckets["sector_etf"]["value"]
                 + buckets["single_stock"]["value"])

    return {
        "total_value": total,
        "defensive_value": defensive,
        "offensive_value": offensive,
        "defensive_pct": (defensive / total * 100) if total > 0 else 0,
        "offensive_pct": (offensive / total * 100) if total > 0 else 0,
        "buckets": buckets,
    }


# ============================================================
# CLI：手动跑一次
# ============================================================

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    holdings_path = os.path.join(SCRIPT_DIR, "holdings.json")
    if not os.path.exists(holdings_path):
        print("holdings.json 不存在")
        sys.exit(1)

    with open(holdings_path, "r", encoding="utf-8") as f:
        holdings = json.load(f)

    print(f"=== ETF 监测 {datetime.now().strftime('%Y-%m-%d %H:%M')} ===\n")
    print(f"持仓 {len(holdings)} 只，开始扫描 ETF...\n")
    results, unmapped = scan_and_update_holdings_etfs(holdings)

    print(f"\n--- 组合分类 ---")
    cls = classify_portfolio(holdings)
    print(f"总持仓（按成本）: ¥{cls['total_value']:,.0f}")
    print(f"  防御型（宽基 ETF）: ¥{cls['defensive_value']:,.0f} ({cls['defensive_pct']:.1f}%)")
    print(f"  进攻型（策略/行业/个股）: ¥{cls['offensive_value']:,.0f} ({cls['offensive_pct']:.1f}%)")
    for bucket_name, cn_name in [("broad_etf", "宽基 ETF"),
                                  ("strategy_etf", "策略 ETF"),
                                  ("sector_etf", "行业 ETF"),
                                  ("single_stock", "个股")]:
        b = cls["buckets"][bucket_name]
        if b["items"]:
            print(f"  {cn_name}: ¥{b['value']:,.0f} ({b['pct']:.1f}%) - "
                  f"{', '.join(i['name'] for i in b['items'])}")

    print(f"\n监测完成，ETF {len(results)} 只")
    if unmapped:
        print(f"未映射 ETF {len(unmapped)} 只，需手工补充 etf_index_map.json")
