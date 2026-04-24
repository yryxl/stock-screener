"""
行业周期 vs 结构性判断（D-011 / REQ-204，2026-04-24）

背景：用户 2026-04-24 提出核心洞察——
"某股只它跌同行不跌 → 有问题；它跌整个行业都跌 + 它是好公司 → 抄底"。
这符合巴菲特 2008 买高盛/富国银行的操作（行业崩塌时买十年王者）。
项目 MODEL_RULES.md 第 373 行早就把这个列成 TODO "行业周期指标：同行业对比"。

但要区分两种"行业下跌"：
  - 周期性：需求端还在、只是短期疲软 → 可抄底（白酒、银行、家电）
  - 结构性：需求永久萎缩 / 政策根本转向（教培、传统地产）→ 不能抄底

判定标准（芒格 inversion 反向思维）：
  问 "3 年后这个行业还存在且规模不退吗？"
    是 → cyclical（周期性）
    否 → structural_decline（结构性衰退）

用法：screener.py 给完 signal 后，对十年王者 + 周期性行业 触发
抄底候选标签 cycle_opportunity；对结构性衰退行业触发行业风险警示。
不改 signal（保留模型基本面评分），只加字段让前端叠加展示。
"""
import json
import os
from typing import Optional, Dict

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_CLASSIFICATION_FILE = os.path.join(_SCRIPT_DIR, "industry_classification.json")

_CACHE: Dict[str, object] = {"data": None, "loaded_at": 0}


def _load() -> dict:
    """加载 industry_classification.json。失败返回空 dict 不阻塞主流程。"""
    import time
    now_ts = time.time()
    cached_at = _CACHE.get("loaded_at") or 0
    if _CACHE["data"] is not None and (now_ts - cached_at) < 300:
        return _CACHE["data"]

    if not os.path.exists(_CLASSIFICATION_FILE):
        _CACHE["data"] = {}
        _CACHE["loaded_at"] = now_ts
        return {}
    try:
        with open(_CLASSIFICATION_FILE, encoding="utf-8") as f:
            data = json.load(f)
        _CACHE["data"] = data
        _CACHE["loaded_at"] = now_ts
        return data
    except Exception as e:
        print(f"  [industry_cycle] 加载失败: {e}")
        return {}


def classify_industry_trend(industry: str) -> str:
    """行业周期分类。

    Args:
      industry: 行业字符串（如"白酒"、"银行"、"教育培训"）

    Returns:
      "cyclical" - 周期性（可抄底）
      "structural_decline" - 结构性衰退（不可抄底）
      "neutral" - 中性（未分类 / 模糊地带）
    """
    if not industry:
        return "neutral"
    data = _load()
    cyclical = data.get("cyclical_industries", []) or []
    structural = data.get("structural_decline_industries", []) or []

    # 子串匹配（行业字符串可能是"白酒Ⅱ"/"中药Ⅱ"这种带级别后缀的）
    industry = str(industry)
    for kw in structural:
        if kw in industry:
            return "structural_decline"
    for kw in cyclical:
        if kw in industry:
            return "cyclical"
    return "neutral"


def attach_industry_trend_to_result(result: dict, industry: str,
                                     is_10y_king: bool) -> dict:
    """给扫描结果加行业周期标签。

    触发条件：
      A. 行业 = cyclical + 公司是十年王者 + 当前是买入信号
         → cycle_opportunity = True，加金色⭐标签
         文案："⭐ 行业逆势抄底候选：十年王者 + 行业周期性下行"
      B. 行业 = structural_decline（不论是否十年王者）
         → structural_decline_warning = True，加红色警示
         文案："⚠ 行业结构性衰退，即使便宜也不建议买入（参考巴菲特 Waumbec Mills 反例）"
      C. 其它情况 → 不加任何字段（中性）

    不修改 signal / signal_text，只叠加字段。前端检测字段展示。
    """
    trend = classify_industry_trend(industry)

    # B：结构性衰退警示（不管是不是十年王者都给）
    if trend == "structural_decline":
        result["industry_trend_warning"] = {
            "type": "structural_decline",
            "industry": industry,
            "text": (f"⚠ **行业结构性衰退**（{industry}）：需求被永久摧毁 / "
                     f"政策根本转向。参考巴菲特 1979 年 Waumbec Mills 纺织厂案例，"
                     f"即使低于营运资金抄底也救不了结构性衰退行业。"),
        }
        return result

    # A：周期性 + 十年王者 → 抄底候选
    signal = result.get("signal", "") or ""
    is_buy_signal = signal.startswith("buy_")
    if trend == "cyclical" and is_10y_king and is_buy_signal:
        result["cycle_opportunity"] = {
            "type": "cyclical_opportunity",
            "industry": industry,
            "text": (f"⭐ **巴菲特式抄底候选**：十年王者 + 行业（{industry}）"
                     f"周期性下行。参考巴菲特 2008 年金融危机买高盛/富国银行："
                     f"危机前 10 年 ROE ≥ 15% 的王者，在行业整体崩塌时进场。"
                     f"**前提：你能承受 1-2 年浮亏等行业拐点**。"),
        }
    return result


# 自检
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    print("==== 行业周期分类器自检 ====\n")

    cases = [
        ("白酒", "cyclical"),
        ("白酒Ⅱ", "cyclical"),  # 带后缀
        ("银行", "cyclical"),
        ("家电", "cyclical"),
        ("白色家电", "cyclical"),
        ("汽车零部件", "cyclical"),
        ("教育培训", "structural_decline"),
        ("K12教育", "structural_decline"),
        ("传统媒体", "structural_decline"),
        ("互联网平台", "neutral"),
        ("医药", "neutral"),
        ("新能源", "neutral"),
        ("", "neutral"),
    ]
    for industry, expected in cases:
        actual = classify_industry_trend(industry)
        ok = "✓" if actual == expected else "✗"
        print(f"  {ok} {industry or '(空)':16s} → {actual:20s}（期望 {expected}）")

    print("\n---- attach_industry_trend_to_result 场景测试 ----")

    # 场景 1：白酒 + 十年王者 + buy_heavy → 应触发 cycle_opportunity
    r1 = {"signal": "buy_heavy", "signal_text": "PE 低估"}
    attach_industry_trend_to_result(r1, "白酒", is_10y_king=True)
    has_co = "cycle_opportunity" in r1
    print(f"  {'✓' if has_co else '✗'} 白酒+十年王者+buy_heavy → cycle_opportunity={has_co}")
    if has_co:
        print(f"      文案：{r1['cycle_opportunity']['text'][:60]}...")

    # 场景 2：白酒 + 非王者 + buy_light → 不触发
    r2 = {"signal": "buy_light"}
    attach_industry_trend_to_result(r2, "白酒", is_10y_king=False)
    has_co = "cycle_opportunity" in r2
    print(f"  {'✓' if not has_co else '✗'} 白酒+非王者 → cycle_opportunity={has_co}（期望 False）")

    # 场景 3：白酒 + 十年王者 + sell_heavy → 不触发（不是买入信号）
    r3 = {"signal": "sell_heavy"}
    attach_industry_trend_to_result(r3, "白酒", is_10y_king=True)
    has_co = "cycle_opportunity" in r3
    print(f"  {'✓' if not has_co else '✗'} 白酒+王者+sell_heavy → cycle_opportunity={has_co}（期望 False）")

    # 场景 4：教培 → 触发 structural_decline_warning
    r4 = {"signal": "buy_light"}
    attach_industry_trend_to_result(r4, "K12教育", is_10y_king=True)
    has_sw = "industry_trend_warning" in r4
    print(f"  {'✓' if has_sw else '✗'} K12教育 → industry_trend_warning={has_sw}")

    # 场景 5：中性（医药）→ 都不加
    r5 = {"signal": "buy_light"}
    attach_industry_trend_to_result(r5, "医药", is_10y_king=True)
    print(f"  {'✓' if 'cycle_opportunity' not in r5 and 'industry_trend_warning' not in r5 else '✗'} "
          f"医药 → 中性不加字段")
