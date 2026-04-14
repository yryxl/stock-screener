"""
中国特色调整逻辑（REQ-152）

功能：
1. 政策敏感度检查（policy_risk_industries.json）
2. 年轻公司白名单（5-10年降档适用十年王者）
3. 熊市时间计数器
4. 黑天鹅事件检测

注意：这些规则是为对抗查巴理念在中国水土不服的风险。
"""

import json
import os
from datetime import datetime, timedelta, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BEIJING = timezone(timedelta(hours=8))


def _load_json(filename):
    path = os.path.join(SCRIPT_DIR, filename)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


# ============================================================
# 1. 政策敏感度检查
# ============================================================

_POLICY_RISK_CACHE = None


def check_policy_risk(industry, category=""):
    """
    检查某行业是否在政策敏感列表中。

    返回：
      (risk_level, reason)
      risk_level: "high" | "medium" | "low_watch" | "safe"
      reason: 原因描述（None 表示无风险）
    """
    global _POLICY_RISK_CACHE
    if _POLICY_RISK_CACHE is None:
        _POLICY_RISK_CACHE = _load_json("policy_risk_industries.json")

    if not _POLICY_RISK_CACHE:
        return "safe", None

    text = f"{industry} {category}".lower()

    # 高风险
    for item in _POLICY_RISK_CACHE.get("high_risk", {}).get("industries", []):
        for kw in item.get("keywords", []):
            if kw.lower() in text:
                return "high", item.get("warning_text", "")

    # 中风险
    for item in _POLICY_RISK_CACHE.get("medium_risk", {}).get("industries", []):
        for kw in item.get("keywords", []):
            if kw.lower() in text:
                return "medium", item.get("warning_text", "")

    # 低风险但需关注
    for item in _POLICY_RISK_CACHE.get("low_risk_but_watch", {}).get("industries", []):
        for kw in item.get("keywords", []):
            if kw.lower() in text:
                return "low_watch", item.get("warning_text", "")

    return "safe", None


def adjust_signal_by_policy_risk(signal, industry, category=""):
    """
    按政策风险等级降级买入信号。

    降级规则：
      buy_heavy -> buy_medium
      buy_medium -> buy_light
      buy_light -> buy_watch
      buy_watch -> hold
      卖出信号不变
      high 风险降 2 级，medium 降 1 级

    返回：(new_signal, adjustment_note)
    """
    if not signal or "buy" not in signal:
        return signal, None

    risk, reason = check_policy_risk(industry, category)
    if risk == "safe":
        return signal, None

    downgrade_map_1 = {
        "buy_heavy": "buy_medium",
        "buy_medium": "buy_light",
        "buy_light": "buy_watch",
        "buy_watch": "hold",
        "buy_add": "hold_keep",
    }

    if risk == "high":
        # 高风险降 2 级
        new_sig = downgrade_map_1.get(signal, signal)
        new_sig = downgrade_map_1.get(new_sig, new_sig)
        note = f"⚠政策高敏感：{reason}"
    elif risk == "medium":
        new_sig = downgrade_map_1.get(signal, signal)
        note = f"⚠政策中敏感：{reason}"
    elif risk == "low_watch":
        # 只加警告，不降级
        new_sig = signal
        note = f"ℹ 政策关注：{reason}"
    else:
        new_sig = signal
        note = None

    return new_sig, note


# ============================================================
# 2. 年轻公司白名单（5-10年降档适用十年王者）
# ============================================================

def check_young_king(df_annual, min_years=5, max_years=10):
    """
    年轻公司是否符合"降档十年王者"标准。

    规则：
      ✅ 有 5-10 年数据（不足 10 年）
      ✅ 所有年份 ROE ≥ 15%
      ✅ 最低年份 ROE ≥ 12%
      ✅ 最近 2 年 ROE 不能双双 < 13%（年轻公司要求更严）

    返回：
      (is_young_king, avg_roe, data_years)
    """
    if df_annual is None or df_annual.empty:
        return False, None, 0

    # 拿 ROE
    roe_col = None
    for col in df_annual.columns:
        if "净资产收益率" == col:
            roe_col = col
            break
    if not roe_col:
        return False, None, 0

    roes = df_annual[roe_col].dropna().tolist()
    n = len(roes)

    if n < min_years or n >= max_years:
        # 太短或太长都不走这条规则
        return False, None, n

    # 所有年份 ≥ 15%
    if min(roes) < 15:
        return False, None, n

    # 最低年份 ≥ 12%（冗余校验，min(roes) 已经 ≥ 15）
    if min(roes) < 12:
        return False, None, n

    # 最近 2 年都 ≥ 13%
    if n >= 2 and (roes[0] < 13 and roes[1] < 13):
        return False, None, n

    avg = sum(roes) / n
    return True, avg, n


# ============================================================
# 3. 熊市时间计数器
# ============================================================

def is_bear_market(current_percentile=None):
    """
    判断当前是否处于熊市（分位 ≤ 30% 即为偏冷以下）

    参数：
      current_percentile: 市场温度百分位（0-100）

    返回：
      (is_bear, severity) severity: "deep" | "mild" | "normal"
    """
    if current_percentile is None:
        return False, "normal"
    if current_percentile <= 15:
        return True, "deep"
    if current_percentile <= 30:
        return True, "mild"
    return False, "normal"


def get_consecutive_bear_years(percentile_history):
    """
    从历史分位数据算出连续熊市年数。

    参数：
      percentile_history: 按日期排序的历史分位列表 [(date, pct), ...]

    返回：
      连续熊市（≤ 30% 分位）的年数
    """
    if not percentile_history:
        return 0
    # 从最新一天往前数
    sorted_hist = sorted(percentile_history, key=lambda x: x[0], reverse=True)
    bear_days = 0
    for date, pct in sorted_hist:
        if pct <= 30:
            bear_days += 1
        else:
            break
    return bear_days / 365  # 约数年


# ============================================================
# 4. 黑天鹅事件检测
# ============================================================

def get_current_black_swan():
    """
    检测当前是否处于已登记的黑天鹅事件期。

    返回：
      None（无事件）或 dict（事件详情）
    """
    data = _load_json("black_swan_events.json")
    if not data:
        return None

    today = datetime.now(_BEIJING).strftime("%Y-%m-%d")
    for event in data.get("events", []):
        start = event.get("start", "")
        end = event.get("end", "")
        if start <= today <= end:
            return event
    return None


def is_black_swan_now():
    """便捷接口：当前是否黑天鹅期"""
    return get_current_black_swan() is not None


# ============================================================
# 5. 综合调整（在 screener 中调用此入口）
# ============================================================

def apply_china_adjustments(signal, signal_text, industry="", category="",
                             df_annual=None):
    """
    综合应用所有中国特色调整。

    返回：(new_signal, new_signal_text, notes_list)
      notes: 所有触发的警告列表
    """
    notes = []
    new_sig = signal
    new_text = signal_text

    # 1. 政策风险调整
    adj_sig, policy_note = adjust_signal_by_policy_risk(signal, industry, category)
    if policy_note:
        notes.append(policy_note)
    if adj_sig != signal:
        new_sig = adj_sig
        new_text = f"{new_text} | {policy_note}"

    # 2. 黑天鹅期提醒
    bs = get_current_black_swan()
    if bs:
        notes.append(f"🦢 {bs['name']} - {bs.get('market_action_suggested','')}")

    return new_sig, new_text, notes


if __name__ == "__main__":
    # 测试
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    print("=== 政策风险检查测试 ===")
    cases = [
        ("教育", "", "high"),
        ("房地产", "", "high"),
        ("白酒", "", "medium"),
        ("新能源", "锂电", "medium"),
        ("食品饮料", "", "safe"),
        ("家电", "", "safe"),
    ]
    for ind, cat, expected in cases:
        risk, _ = check_policy_risk(ind, cat)
        mark = "✅" if risk == expected else "❌"
        print(f"  {mark} {ind} 风险={risk} (期望{expected})")

    print("\n=== 信号降级测试 ===")
    test_cases = [
        ("buy_heavy", "房地产", "buy_light"),  # high 降 2 级
        ("buy_medium", "白酒", "buy_light"),    # medium 降 1 级
        ("buy_heavy", "食品饮料", "buy_heavy"),  # safe 不降
    ]
    for sig, ind, expected in test_cases:
        new_sig, note = adjust_signal_by_policy_risk(sig, ind)
        mark = "✅" if new_sig == expected else "❌"
        print(f"  {mark} {sig} + {ind} → {new_sig} (期望{expected})")

    print("\n=== 黑天鹅检测测试 ===")
    bs = get_current_black_swan()
    if bs:
        print(f"  ⚠️ 当前处于黑天鹅期：{bs['name']}")
    else:
        print("  ✅ 当前无黑天鹅事件")
