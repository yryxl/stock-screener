"""
通知推送模块 - 通过微信测试号模板消息推送
按6个信号等级分块发送，每条消息简短直接
"""

import requests
import yaml
from datetime import datetime


def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_access_token(appid, appsecret):
    url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={appid}&secret={appsecret}"
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        return data.get("access_token")
    except Exception as e:
        print(f"获取token异常: {e}")
        return None


def send_template_msg(access_token, openid, template_id, title, content):
    """发送一条模板消息"""
    url = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={access_token}"
    # 截断content避免超长
    if len(content) > 800:
        content = content[:800] + "..."
    data = {
        "touser": openid,
        "template_id": template_id,
        "data": {
            "title": {"value": title, "color": "#173177"},
            "content": {"value": content, "color": "#333333"},
        },
    }
    try:
        resp = requests.post(url, json=data, timeout=30)
        result = resp.json()
        if result.get("errcode") == 0:
            print(f"  推送成功: {title}")
            return True
        else:
            print(f"  推送失败: {result}")
            return False
    except Exception as e:
        print(f"  推送异常: {e}")
        return False


SIGNAL_LABELS = {
    "buy_heavy": "🔴 可以重仓买入",
    "buy_light": "🟠 可以轻仓买入",
    "buy_watch": "🟡 重点关注买入",
    "hold": "⚪ 持有观察",
    "sell_watch": "🟡 重点关注卖出",
    "sell_light": "🟠 可以适当卖出",
    "sell_heavy": "🔴 可以大量卖出",
}

# 信号分组顺序
SIGNAL_GROUPS = [
    ("buy_heavy", "可以重仓买入"),
    ("buy_light", "可以轻仓买入"),
    ("buy_watch", "重点关注买入"),
    ("sell_watch", "重点关注卖出"),
    ("sell_light", "可以适当卖出"),
    ("sell_heavy", "可以大量卖出"),
]


def format_stock_line(s):
    """格式化一只股票为一行文字"""
    name = s.get("name", "")
    code = s.get("code", "")
    pe = s.get("pe", 0)
    price = s.get("price", 0)
    note = s.get("note", "")
    category = s.get("category", "")

    line = f"{name}({code})"
    if pe and pe > 0:
        line += f" PE={pe:.1f}"
    if price and price > 0:
        line += f" ¥{price:.2f}"
    if category:
        line += f"\n  [{category}]"
    if note:
        line += f" {note}"
    return line


def send_daily_report(watchlist_signals, candidates, holding_signals, config):
    """
    每天发送消息
    按6个信号等级分块发送
    无信号时发送"无推荐"
    """
    wx = config["wechat"]
    if wx["appid"] == "YOUR_APPID":
        print("微信未配置，跳过")
        return

    access_token = get_access_token(wx["appid"], wx["appsecret"])
    if not access_token:
        return

    openid = wx["openid"]
    template_id = wx["template_id"]
    today = datetime.now().strftime("%Y-%m-%d")

    # 合并所有信号源
    all_signals = []
    for s in watchlist_signals:
        s["source"] = "关注表"
        all_signals.append(s)
    for s in candidates:
        if s.get("signal") and s["signal"] != "hold":
            s["source"] = "候选池"
            all_signals.append(s)
    for s in holding_signals:
        s["source"] = "持仓"
        all_signals.append(s)

    # 按信号分组
    has_any_signal = False
    for signal_key, signal_name in SIGNAL_GROUPS:
        group = [s for s in all_signals if s.get("signal") == signal_key]
        if not group:
            continue

        has_any_signal = True
        title = f"{signal_name} {today}"
        lines = []
        for s in group:
            lines.append(format_stock_line(s))
        content = "\n\n".join(lines)

        send_template_msg(access_token, openid, template_id, title, content)

    # 无任何信号时发送"无推荐"
    if not has_any_signal:
        title = f"芒格选股 {today}"
        content = f"今日无推荐\n\n关注表{len(watchlist_signals)}只均在合理区间\n继续持有观察"
        send_template_msg(access_token, openid, template_id, title, content)

    print(f"推送完成")
