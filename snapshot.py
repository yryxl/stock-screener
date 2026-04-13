"""
模型快照系统
每周自动保存一份完整快照到 snapshots/ 目录
包含：模型推荐结果 + 模型参数 + 当时股价 + 市场环境 + 护城河状态
用于未来纵向回测验证模型准确性

快照存储在GitHub仓库中 = 云端永久备份
"""

import json
import os
from datetime import datetime

from screener import INDUSTRY_PE, COMPLEXITY_ROE_ADJUST


def load_json(filename):
    path = os.path.join(os.path.dirname(__file__), filename)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_market_context():
    """构建市场环境快照：温度计 + ETF 估值"""
    ctx = {}
    # 市场温度
    try:
        from market_temperature import get_realtime_market_temperature
        temp = get_realtime_market_temperature()
        if temp:
            ctx["market_temperature"] = {
                "level": temp.get("level"),
                "label": temp.get("label", ""),
                "description": temp.get("description", ""),
                "current_pe_median": temp.get("current_pe_median"),
                "percentile": temp.get("percentile"),
                "as_of": temp.get("as_of", ""),
            }
    except Exception as e:
        ctx["market_temperature"] = {"error": str(e)}

    # 从 daily_results 读取已缓存的温度（兜底）
    if "market_temperature" not in ctx or "error" in ctx.get("market_temperature", {}):
        daily = load_json("daily_results.json")
        if isinstance(daily, dict) and "market_temperature" in daily:
            ctx["market_temperature"] = daily["market_temperature"]

    return ctx


def _enrich_holding_signals(holding_signals):
    """
    为每条持仓信号补充环境数据：护城河状态、核心财务指标
    这样日后分析时不光知道"信号是什么"，还知道"当时公司状况如何"
    """
    enriched = []
    for hs in holding_signals:
        item = dict(hs)  # 浅拷贝，不改原始数据

        code = hs.get("code", "")
        if not code:
            enriched.append(item)
            continue

        # 护城河状态
        try:
            from live_rules import check_moat_live
            from screener import get_financial_indicator, extract_annual_data
            df_indicator = get_financial_indicator(code)
            if df_indicator is not None:
                df_annual = extract_annual_data(df_indicator, years=10)
                if not df_annual.empty:
                    is_intact, probs = check_moat_live(df_annual, industry=hs.get("industry", ""))
                    item["moat_status"] = "完好" if is_intact else f"松动（{'; '.join(probs[:2])}）"
                    item["moat_problems"] = probs[:3] if not is_intact else []
        except Exception:
            pass

        # 核心财务指标（从最新年报提取）
        try:
            from screener import get_financial_indicator, extract_annual_data
            df_indicator = get_financial_indicator(code)
            if df_indicator is not None:
                df_annual = extract_annual_data(df_indicator, years=3)
                if not df_annual.empty:
                    latest = df_annual.iloc[0]
                    roe = latest.get("roe")
                    if roe is not None:
                        item["roe"] = round(float(roe), 1)
                    gm = latest.get("gross_margin")
                    if gm is not None:
                        item["gross_margin"] = round(float(gm), 1)
                    debt = latest.get("debt_ratio")
                    if debt is not None:
                        item["debt_ratio"] = round(float(debt), 1)
        except Exception:
            pass

        enriched.append(item)

    return enriched


def _enrich_watchlist_signals(watchlist_signals):
    """
    为关注表信号补充护城河状态和关键财务指标
    """
    enriched = []
    for ws in watchlist_signals:
        item = dict(ws)
        code = ws.get("code", "")
        if not code:
            enriched.append(item)
            continue

        # 护城河状态
        try:
            from live_rules import check_moat_live
            from screener import get_financial_indicator, extract_annual_data
            df_indicator = get_financial_indicator(code)
            if df_indicator is not None:
                df_annual = extract_annual_data(df_indicator, years=10)
                if not df_annual.empty:
                    industry = ws.get("category", "")
                    is_intact, probs = check_moat_live(df_annual, industry=industry)
                    item["moat_status"] = "完好" if is_intact else f"松动（{'; '.join(probs[:2])}）"
                    item["moat_problems"] = probs[:3] if not is_intact else []
        except Exception:
            pass

        # 核心指标（dimensions 里已有评分，这里补充原始数值）
        try:
            from screener import get_financial_indicator, extract_annual_data
            df_indicator = get_financial_indicator(code)
            if df_indicator is not None:
                df_annual = extract_annual_data(df_indicator, years=3)
                if not df_annual.empty:
                    latest = df_annual.iloc[0]
                    roe = latest.get("roe")
                    if roe is not None:
                        item["roe"] = round(float(roe), 1)
                    gm = latest.get("gross_margin")
                    if gm is not None:
                        item["gross_margin"] = round(float(gm), 1)
                    debt = latest.get("debt_ratio")
                    if debt is not None:
                        item["debt_ratio"] = round(float(debt), 1)
        except Exception:
            pass

        enriched.append(item)

    return enriched


def save_snapshot():
    """保存本周快照"""
    now = datetime.now()
    snapshot_dir = os.path.join(os.path.dirname(__file__), "snapshots")
    os.makedirs(snapshot_dir, exist_ok=True)

    # 快照文件名：按周编号，同一周只保存一份
    week_id = now.strftime("%Y-W%W")
    snapshot_file = os.path.join(snapshot_dir, f"{week_id}.json")

    # 如果本周已有快照，跳过
    if os.path.exists(snapshot_file):
        print(f"本周快照已存在: {week_id}，跳过")
        return snapshot_file

    # 读取当前数据
    daily = load_json("daily_results.json")
    watchlist = load_json("watchlist.json")
    holdings = load_json("holdings.json")

    # 获取原始信号列表
    raw_watchlist_signals = daily.get("watchlist_signals", []) if isinstance(daily, dict) else []
    raw_holding_signals = daily.get("holding_signals", []) if isinstance(daily, dict) else []

    # 补充环境数据（护城河、财务指标）
    print("快照：补充护城河和财务指标...")
    enriched_watchlist = _enrich_watchlist_signals(raw_watchlist_signals)
    enriched_holdings = _enrich_holding_signals(raw_holding_signals)

    # 市场环境快照
    print("快照：获取市场环境...")
    market_ctx = _build_market_context()

    # 构建快照
    snapshot = {
        # 元信息
        "snapshot_date": now.strftime("%Y-%m-%d %H:%M"),
        "week_id": week_id,
        "data_source_date": daily.get("date", "") if isinstance(daily, dict) else "",

        # 市场环境（温度计 + 大盘状态）
        "market_context": market_ctx,

        # 模型参数（用于追溯当时的判断标准）
        "model_params": {
            "industry_pe_ranges": {k: {
                "fair_low": v["fair_low"],
                "fair_high": v["fair_high"],
                "complexity": v.get("complexity", "medium"),
            } for k, v in INDUSTRY_PE.items()},
            "roe_thresholds_by_complexity": COMPLEXITY_ROE_ADJUST,
        },

        # 模型推荐结果（核心：未来回测对比用）
        "recommendations": daily.get("ai_recommendations", []) if isinstance(daily, dict) else [],

        # 关注表状态（含护城河和核心指标）
        "watchlist_signals": enriched_watchlist,

        # 持仓状态
        "holdings": holdings if isinstance(holdings, list) else [],
        "holding_signals": enriched_holdings,

        # 关注表配置
        "watchlist_config": watchlist if isinstance(watchlist, list) else [],
    }

    # 保存
    with open(snapshot_file, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2, default=str)

    print(f"快照已保存: {snapshot_file}")
    print(f"  推荐: {len(snapshot['recommendations'])}只")
    print(f"  关注表: {len(snapshot['watchlist_signals'])}只"
          f"（含护城河状态和财务指标）")
    print(f"  持仓: {len(snapshot['holdings'])}只")
    if market_ctx.get("market_temperature"):
        mt = market_ctx["market_temperature"]
        print(f"  市场温度: {mt.get('label', '未知')}")

    return snapshot_file


def list_snapshots():
    """列出所有快照"""
    snapshot_dir = os.path.join(os.path.dirname(__file__), "snapshots")
    if not os.path.exists(snapshot_dir):
        return []
    files = sorted([f for f in os.listdir(snapshot_dir) if f.endswith(".json")])
    return files


if __name__ == "__main__":
    print("=== 保存模型快照 ===")
    save_snapshot()
    print(f"\n历史快照: {list_snapshots()}")
