"""
实时市场温度计 - 沪深300指数 PE 历史分位判断
巴菲特/芒格："别人贪婪时我恐惧，别人恐惧时我贪婪"
"""

import json
import os
import time
from datetime import datetime

import akshare as ak

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


TEMP_LEVELS = {
    2: (
        "🔴 泡沫区·贵到不舒服",
        "估值在历史最高 15% 区间。芒格原话：贵到让我不舒服，宁愿持有现金。"
        "处置建议：停止任何新增买入；卖出盈利 50% 以上的仓位，保留底仓；"
        "长期持有的好公司继续拿。注意：不是全部清仓，是卖盈利部分。",
    ),
    1: (
        "🔥 偏热·暂停加仓",
        "估值处于历史 70%-85% 分位，还没到极端泡沫，但安全边际已经很薄。"
        "处置建议：停止往高估标的加钱，新钱转向仍然便宜的品种（红利/低估行业）；"
        "老仓位继续持有不动。巴菲特原则：不在偏贵时加码，等便宜了再重仓。",
    ),
    0: (
        "⚪ 正常·按节奏",
        "估值处于合理区间（历史 30%-70% 分位）。按自己的定投/选股节奏执行，"
        "不因估值做大调整。巴菲特大多数时候都处在这个状态。",
    ),
    -1: (
        "🧊 偏冷·重点加仓",
        "估值进入历史 15%-30% 分位，机会开始显现。处置建议："
        "把手里闲钱和新工资重点投向被低估的好公司和宽基，适当超配股票。"
        "芒格原则：便宜是硬道理。",
    ),
    -2: (
        "❄️ 低估区·全力加仓",
        "估值在历史最低 15% 区间，历史级别的建仓机会。处置建议："
        "清空闲置现金，全力加仓优质低估标的；动用任何可动用资金，越跌越买。"
        "2008 年巴菲特写下《我在买入美国》就是这种时刻。",
    ),
}


def fetch_realtime_hs300_pe():
    """
    从 akshare 拉取最新的沪深300指数PE数据
    返回：(latest_pe_median, latest_date) 或 (None, None)
    """
    for attempt in range(3):
        try:
            df = ak.stock_index_pe_lg(symbol="沪深300")
            if df is None or df.empty:
                return None, None
            latest = df.iloc[-1]
            pe_median = float(latest.get("滚动市盈率中位数") or 0)
            date = str(latest.get("日期") or "")[:10]
            return pe_median, date
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
                continue
            print(f"沪深300 PE 拉取失败: {e}")
            return None, None
    return None, None


def fetch_hs300_pe_history():
    """
    拉取沪深300指数PE历史数据（所有日数据）
    按月聚合为月度数据（每月最后一个交易日的 PE 中位数）
    """
    try:
        df = ak.stock_index_pe_lg(symbol="沪深300")
        if df is None or df.empty:
            return {}
        df["日期"] = df["日期"].astype(str)
        df["month"] = df["日期"].str[:7]
        monthly = df.groupby("month").tail(1)[["日期", "month", "滚动市盈率", "滚动市盈率中位数"]]
        result = {}
        for _, row in monthly.iterrows():
            try:
                result[row["month"]] = {
                    "pe": float(row["滚动市盈率"]),
                    "pe_median": float(row["滚动市盈率中位数"]),
                }
            except (ValueError, TypeError):
                continue
        return result
    except Exception as e:
        print(f"沪深300历史 PE 拉取失败: {e}")
        return {}


def compute_temperature_from_pe(current_pe, pe_history_values):
    """
    根据历史 PE 分位计算温度等级
    传入：当前 PE 值 + 历史 PE 值列表
    返回：(温度等级 -2~+2, 分位百分比 0-100)
    """
    if not pe_history_values or len(pe_history_values) < 60:
        return 0, 50
    sorted_hist = sorted(pe_history_values)
    # 计算当前值的百分位
    below = sum(1 for v in sorted_hist if v < current_pe)
    percentile = below / len(sorted_hist) * 100
    # 温度分级
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
    return level, round(percentile, 1)


def get_realtime_market_temperature():
    """
    主函数：获取实时市场温度
    返回：{
        "level": -2~2,
        "label": "🔥偏热",
        "description": "...",
        "current_pe": 15.87,
        "percentile": 72.3,
        "lookback_years": 10,
        "as_of": "2026-04-10",
    }
    数据不可用时返回 level=0 和提示
    """
    # 从 akshare 拉历史（一次性），计算当前位置
    history = fetch_hs300_pe_history()
    if not history:
        return {
            "level": 0,
            "label": "⚪正常",
            "description": "温度计数据拉取失败",
            "current_pe": None,
            "percentile": None,
            "as_of": datetime.now().strftime("%Y-%m-%d"),
        }

    sorted_months = sorted(history.keys())
    latest_month = sorted_months[-1]
    current_pe = history[latest_month]["pe_median"]

    # 取过去 10 年（120 个月）的历史
    y, m = int(latest_month[:4]), int(latest_month[5:])
    cutoff_month = f"{y - 10}-{m:02d}"
    history_values = [
        history[mm]["pe_median"]
        for mm in sorted_months
        if mm < latest_month and mm >= cutoff_month
    ]

    level, percentile = compute_temperature_from_pe(current_pe, history_values)
    label, desc = TEMP_LEVELS.get(level, ("⚪未知", ""))

    return {
        "level": level,
        "label": label,
        "description": desc,
        "current_pe_median": round(current_pe, 2),
        "percentile": percentile,
        "lookback_years": 10,
        "as_of": latest_month,
        "data_points": len(history_values),
    }


def save_temperature_snapshot():
    """把当前温度快照保存到本地文件，便于前端展示"""
    temp = get_realtime_market_temperature()
    path = os.path.join(SCRIPT_DIR, "market_temperature.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(temp, f, ensure_ascii=False, indent=2)
    return temp


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    temp = save_temperature_snapshot()
    print(f"当前市场温度：{temp['label']}")
    print(f"描述：{temp['description']}")
    print(f"沪深300中位数PE：{temp.get('current_pe_median')}")
    print(f"历史分位：{temp.get('percentile')}%")
    print(f"数据截至：{temp.get('as_of')}")
