"""
通知推送模块 - 通过微信测试号客服消息接口发送纯文本
按信号等级分条发送，内容直接显示在微信对话框
"""

import json as json_lib
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


def send_text_msg(access_token, openid, text):
    """发送客服文本消息（内容直接显示在对话框）"""
    url = f"https://api.weixin.qq.com/cgi-bin/message/custom/send?access_token={access_token}"
    data = {
        "touser": openid,
        "msgtype": "text",
        "text": {"content": text}
    }
    try:
        # 关键：ensure_ascii=False 让中文正常显示，不被转义
        body = json_lib.dumps(data, ensure_ascii=False).encode("utf-8")
        resp = requests.post(url, data=body, headers={"Content-Type": "application/json; charset=utf-8"}, timeout=30)
        result = resp.json()
        if result.get("errcode") == 0:
            print(f"  发送成功")
            return True
        else:
            print(f"  发送失败: {result}")
            # 如果客服接口不可用，回退到模板消息
            return False
    except Exception as e:
        print(f"  发送异常: {e}")
        return False


def send_template_msg(access_token, openid, template_id, title, content):
    """模板消息（备用方案）"""
    url = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={access_token}"
    data = {
        "touser": openid,
        "template_id": template_id,
        "data": {
            "title": {"value": title},
            "content": {"value": content[:200]},
        },
    }
    try:
        body = json_lib.dumps(data, ensure_ascii=False).encode("utf-8")
        resp = requests.post(url, data=body, headers={"Content-Type": "application/json; charset=utf-8"}, timeout=30)
        result = resp.json()
        if result.get("errcode") == 0:
            print(f"  模板消息发送成功: {title}")
            return True
        else:
            print(f"  模板消息失败: {result}")
            return False
    except Exception as e:
        print(f"  模板消息异常: {e}")
        return False


SIGNAL_GROUPS = [
    ("buy_heavy", "【可以重仓买入】"),
    ("buy_light", "【可以轻仓买入】"),
    ("buy_watch", "【重点关注买入】"),
    ("sell_watch", "【重点关注卖出】"),
    ("sell_light", "【可以适当卖出】"),
    ("sell_heavy", "【可以大量卖出】"),
]


def format_stock_line(s):
    """一只股票一行"""
    name = s.get("name", "")
    code = s.get("code", "")
    pe = s.get("pe", 0)
    price = s.get("price", 0)
    category = s.get("category", "")
    note = s.get("note", "")

    line = f"{name}({code})"
    if pe and pe > 0:
        line += f" PE={pe:.1f}"
    if price and price > 0:
        line += f" {price:.2f}元"
    if category:
        line += f" [{category}]"
    return line


def send_daily_report(watchlist_signals, candidates, holding_signals, config):
    """每天发送消息，按信号分条"""
    wx = config["wechat"]
    if wx["appid"] == "YOUR_APPID":
        print("微信未配置，跳过")
        return

    access_token = get_access_token(wx["appid"], wx["appsecret"])
    if not access_token:
        return

    openid = wx["openid"]
    template_id = wx["template_id"]
    today = datetime.now().strftime("%m-%d")

    # 合并所有信号
    all_signals = []
    for s in watchlist_signals:
        all_signals.append(s)
    for s in candidates:
        if s.get("signal") and s["signal"] != "hold":
            all_signals.append(s)
    for s in holding_signals:
        all_signals.append(s)

    # 按信号分组发送
    sent_any = False
    for signal_key, signal_title in SIGNAL_GROUPS:
        group = [s for s in all_signals if s.get("signal") == signal_key]
        if not group:
            continue

        sent_any = True
        lines = [f"{signal_title} {today}", ""]
        for s in group:
            lines.append(format_stock_line(s))
        text = "\n".join(lines)

        # 先尝试客服消息，失败则用模板消息
        ok = send_text_msg(access_token, openid, text)
        if not ok:
            send_template_msg(access_token, openid, template_id, f"{signal_title} {today}", text)

    # 无信号时发"无推荐"
    if not sent_any:
        text = f"芒格选股 {today}\n\n今日无推荐\n关注表均在合理区间，继续观察"
        ok = send_text_msg(access_token, openid, text)
        if not ok:
            send_template_msg(access_token, openid, template_id, f"芒格选股 {today}", "今日无推荐，继续观察")

    print("推送完成")
