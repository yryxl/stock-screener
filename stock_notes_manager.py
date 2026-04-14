"""
持仓股票备注与定时提醒管理模块

功能：
1. 读写 stock_notes.json
2. 检查到期提醒（每次选股流程时调用）
3. 生成待推送的提醒消息

数据存储位置：stock_notes.json（GitHub 同步）

提醒触发规则：
- fire_date <= today 且 active=true → 触发
- 每天早中晚 3 次（由 main.py 定时任务调度）
- 触发后 fired_count +1，last_fired_at 更新
- 直到用户在前端手动点"关闭提醒"，active 才变 false
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
NOTES_FILE = os.path.join(SCRIPT_DIR, "stock_notes.json")
_BEIJING = timezone(timedelta(hours=8))


def _beijing_now():
    return datetime.now(_BEIJING)


def _beijing_today():
    return _beijing_now().strftime("%Y-%m-%d")


def load_notes():
    """读取全部备注"""
    if not os.path.exists(NOTES_FILE):
        return {"notes": {}}
    try:
        with open(NOTES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"notes": {}}


def save_notes(data):
    """写回全部备注"""
    with open(NOTES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_note(code):
    """获取某股票的备注（不存在返回空结构）"""
    data = load_notes()
    return data.get("notes", {}).get(code, {
        "code": code,
        "name": "",
        "notes": "",
        "updated_at": "",
        "reminders": [],
    })


def update_note_text(code, name, text):
    """更新备注正文"""
    data = load_notes()
    notes = data.setdefault("notes", {})
    entry = notes.setdefault(code, {
        "code": code, "name": name,
        "notes": "", "updated_at": "", "reminders": [],
    })
    entry["notes"] = text
    entry["name"] = name or entry.get("name", "")
    entry["updated_at"] = _beijing_now().strftime("%Y-%m-%d %H:%M")
    save_notes(data)
    return entry


def add_reminder(code, name, fire_date, message):
    """
    给某股票添加定时提醒

    参数：
      code: 股票代码
      name: 股票名称
      fire_date: YYYY-MM-DD
      message: 提醒内容
    """
    data = load_notes()
    notes = data.setdefault("notes", {})
    entry = notes.setdefault(code, {
        "code": code, "name": name,
        "notes": "", "updated_at": "", "reminders": [],
    })
    reminder = {
        "id": str(int(time.time() * 1000)),
        "fire_date": fire_date,
        "message": message,
        "active": True,
        "fired_count": 0,
        "last_fired_at": None,
        "created_at": _beijing_now().strftime("%Y-%m-%d %H:%M"),
    }
    entry.setdefault("reminders", []).append(reminder)
    entry["name"] = name or entry.get("name", "")
    save_notes(data)
    return reminder


def delete_reminder(code, reminder_id):
    """删除某提醒"""
    data = load_notes()
    entry = data.get("notes", {}).get(code)
    if not entry:
        return False
    entry["reminders"] = [r for r in entry.get("reminders", [])
                          if r.get("id") != reminder_id]
    save_notes(data)
    return True


def dismiss_reminder(code, reminder_id):
    """关闭提醒（active=False，但保留历史）"""
    data = load_notes()
    entry = data.get("notes", {}).get(code)
    if not entry:
        return False
    found = False
    for r in entry.get("reminders", []):
        if r.get("id") == reminder_id:
            r["active"] = False
            r["dismissed_at"] = _beijing_now().strftime("%Y-%m-%d %H:%M")
            found = True
    if found:
        save_notes(data)
    return found


def has_active_alerts(code):
    """
    检查某股票是否有正在激活的到期提醒。

    返回：
      (has_alert, count) - 是否有激活提醒 + 数量
    """
    entry = get_note(code)
    today = _beijing_today()
    count = 0
    for r in entry.get("reminders", []):
        if r.get("active") and r.get("fire_date", "9999") <= today:
            count += 1
    return count > 0, count


def get_pending_alerts():
    """
    获取所有到期且激活的提醒（供消息推送用）

    返回：
      list of dict: [{code, name, message, fire_date, ...}, ...]
    """
    data = load_notes()
    today = _beijing_today()
    pending = []
    for code, entry in data.get("notes", {}).items():
        for r in entry.get("reminders", []):
            if r.get("active") and r.get("fire_date", "9999") <= today:
                pending.append({
                    "code": code,
                    "name": entry.get("name", code),
                    "message": r.get("message", ""),
                    "fire_date": r.get("fire_date"),
                    "fired_count": r.get("fired_count", 0),
                    "reminder_id": r.get("id"),
                })
    return pending


def mark_fired(code, reminder_id):
    """记录某提醒已被推送（fired_count +1）"""
    data = load_notes()
    entry = data.get("notes", {}).get(code)
    if not entry:
        return
    for r in entry.get("reminders", []):
        if r.get("id") == reminder_id:
            r["fired_count"] = r.get("fired_count", 0) + 1
            r["last_fired_at"] = _beijing_now().strftime("%Y-%m-%d %H:%M")
    save_notes(data)


def format_alerts_for_wechat():
    """
    格式化待推送的提醒为微信消息文本

    返回：消息文本（或 None 如果没有待推送）
    """
    pending = get_pending_alerts()
    if not pending:
        return None

    today = _beijing_today()
    lines = [f"🔔【持仓提醒】{today}", ""]
    for p in pending:
        lines.append(f"📌 {p['name']}（{p['code']}）")
        lines.append(f"   到期日：{p['fire_date']}")
        lines.append(f"   {p['message']}")
        if p['fired_count'] > 0:
            lines.append(f"   ⚠ 已提醒 {p['fired_count']} 次（请到前端手动关闭）")
        lines.append("")

    lines.append("💡 请前往前端持仓管理页，点击对应股票的📝按钮处理提醒")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    print("=== 当前待提醒列表 ===")
    alerts = get_pending_alerts()
    if not alerts:
        print("  无待提醒")
    else:
        for a in alerts:
            print(f"  📌 {a['name']} | 到期 {a['fire_date']} | {a['message']}")

    print("\n=== 格式化为微信消息 ===")
    msg = format_alerts_for_wechat()
    print(msg if msg else "（无消息）")
