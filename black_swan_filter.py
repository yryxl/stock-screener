"""
个股级黑天鹅过滤器（REQ-203，2026-04-24）

与 REQ-152 政策风险标签不同 —— 这是**单股级**的突发事件：
董事长被立案/业绩暴雷/产品召回等，纯数据模型（看年报/季报）看不到。
由 AI 舆情筛查（news_screen skill）发现并登记到 black_swan_events.json
的 company_events 数组。

用法：screener.py 在给出 buy_* 信号后调用 check_company_black_swan()
如果命中：**不改 signal（保留模型的基本面评分）**，但加一个
`black_swan_warning` 字段，前端用红色警示条展示"xxxx-xx-xx 出现 xxx，
不建议买入"，让用户自己判断是否要顶风买。

设计思路（用户 2026-04-24 反馈）：
- 不直接跳过 —— 保留股票在推荐列表
- 加备注说明时间 + 原因 —— 用户能看到模型原本怎么评价 + 黑天鹅警示
- 让用户自己决策 —— 符合"宁可错过不犯错"+ skill 不代人决策原则
"""
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_EVENTS_FILE = os.path.join(_SCRIPT_DIR, "black_swan_events.json")
_BEIJING = timezone(timedelta(hours=8))

# 进程内缓存，避免每只股都读一次 json
_CACHE: Dict[str, object] = {"events": None, "loaded_at": 0}


def _load_events() -> List[dict]:
    """加载 company_events 列表。失败返回空列表不阻塞主流程。"""
    # 5 分钟缓存
    import time
    now_ts = time.time()
    cached_at = _CACHE.get("loaded_at") or 0
    if _CACHE["events"] is not None and (now_ts - cached_at) < 300:
        return _CACHE["events"]

    if not os.path.exists(_EVENTS_FILE):
        _CACHE["events"] = []
        _CACHE["loaded_at"] = now_ts
        return []
    try:
        with open(_EVENTS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        events = data.get("company_events", []) or []
        _CACHE["events"] = events
        _CACHE["loaded_at"] = now_ts
        return events
    except Exception as e:
        print(f"  [black_swan] 加载 events 失败: {e}")
        return []


def check_company_black_swan(code: str,
                              today: Optional[datetime] = None
                              ) -> Optional[dict]:
    """检查某只股当前是否处于黑天鹅期。

    Args:
      code: 6 位股票代码
      today: 当前日期（默认北京时间今天）

    Returns:
      None - 无黑天鹅 / 已过期
      dict - 命中事件，包含：
        code / name / start / end / type / impact / desc /
        action_suggested / source_url / added_at
    """
    if today is None:
        today = datetime.now(_BEIJING)
    today_str = today.strftime("%Y-%m-%d")
    code = str(code).zfill(6)

    for event in _load_events():
        if str(event.get("code", "")).zfill(6) != code:
            continue
        start = event.get("start", "")
        end = event.get("end", "9999-12-31")
        if start <= today_str <= end:
            return event
    return None


def format_warning_text(event: dict) -> str:
    """把 event dict 格式化成一行红色警示文字，供前端展示。

    例：🚨 2026-04-23 五粮液董事长被立案调查（严重违纪违法）...
    """
    start = event.get("start", "?")
    desc = event.get("desc", "")
    action = event.get("action_suggested", "")
    impact_emoji = {
        "severe": "🚨🚨",
        "major": "🚨",
        "moderate": "⚠",
    }.get(event.get("impact", ""), "⚠")

    # 截短 desc 防止太长
    if len(desc) > 120:
        desc = desc[:120] + "..."

    parts = [f"{impact_emoji} **{start}** 出现个股黑天鹅", desc]
    if action:
        parts.append(f"**建议：{action}**")
    return " | ".join(parts)


def attach_warning_to_result(result: dict, code: str,
                              today: Optional[datetime] = None) -> dict:
    """给一条扫描结果加上 black_swan_warning 字段（如果命中）。

    不修改 signal / signal_text（保留模型原始评分），只叠加 warning 字段。
    前端检测到 result["black_swan_warning"] 非空时展示红色警示条。
    """
    event = check_company_black_swan(code, today)
    if event is None:
        return result
    result["black_swan_warning"] = {
        "start": event.get("start"),
        "end": event.get("end"),
        "type": event.get("type"),
        "impact": event.get("impact"),
        "desc": event.get("desc"),
        "action_suggested": event.get("action_suggested"),
        "source_url": event.get("source_url"),
        "warning_text": format_warning_text(event),
    }
    return result


# 自检
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    print("==== 个股黑天鹅过滤器自检 ====\n")

    # 测 3 只已登记的
    for code in ("000858", "603277", "603439"):
        r = check_company_black_swan(code)
        if r:
            print(f"{code} {r['name']}：🚨 命中")
            print(f"  类型: {r['type']}")
            print(f"  描述: {r['desc'][:80]}...")
            print(f"  建议: {r['action_suggested']}")
        else:
            print(f"{code}：未命中")

    # 测一只未登记的
    print()
    for code in ("600519", "510330"):
        r = check_company_black_swan(code)
        status = "🚨" if r else "✓"
        print(f"{code}：{status} 未登记（这是正确的）" if not r else f"{code}：命中（不该命中？）")

    # 测 warning_text 格式
    print("\n---- warning_text 示例 ----")
    r = check_company_black_swan("000858")
    if r:
        result = {}
        attach_warning_to_result(result, "000858")
        print(result["black_swan_warning"]["warning_text"])

    # 测日期范围边界
    print("\n---- 日期范围测试 ----")
    past = datetime(2025, 1, 1, tzinfo=_BEIJING)
    r_past = check_company_black_swan("000858", past)
    future = datetime(2027, 1, 1, tzinfo=_BEIJING)
    r_future = check_company_black_swan("000858", future)
    print(f"2025-01-01 查 000858: {'命中（bug）' if r_past else '未命中 ✓'}")
    print(f"2027-01-01 查 000858: {'命中（bug）' if r_future else '未命中 ✓（已过期）'}")
