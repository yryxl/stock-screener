"""
通知推送模块 - 微信测试号客服消息
信号分7类：重仓买入/轻仓买入/关注买入/关注卖出/适当卖出/大量卖出/基本面恶化
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
        return resp.json().get("access_token")
    except Exception as e:
        print(f"获取token异常: {e}")
        return None


def send_text_msg(access_token, openid, text):
    """发送客服文本消息"""
    url = f"https://api.weixin.qq.com/cgi-bin/message/custom/send?access_token={access_token}"
    data = {"touser": openid, "msgtype": "text", "text": {"content": text}}
    try:
        body = json_lib.dumps(data, ensure_ascii=False).encode("utf-8")
        resp = requests.post(url, data=body, headers={"Content-Type": "application/json; charset=utf-8"}, timeout=30)
        result = resp.json()
        if result.get("errcode") == 0:
            print(f"  发送成功")
            return True
        else:
            print(f"  发送失败: errcode={result.get('errcode')} {result.get('errmsg','')}")
            # 客服接口失败时尝试模板消息
            return False
    except Exception as e:
        print(f"  发送异常: {e}")
        return False


def send_template_msg(access_token, openid, template_id, title, content):
    """模板消息备用"""
    url = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={access_token}"
    data = {
        "touser": openid, "template_id": template_id,
        "data": {"title": {"value": title}, "content": {"value": content[:200]}},
    }
    try:
        body = json_lib.dumps(data, ensure_ascii=False).encode("utf-8")
        resp = requests.post(url, data=body, headers={"Content-Type": "application/json; charset=utf-8"}, timeout=30)
        result = resp.json()
        if result.get("errcode") == 0:
            print(f"  模板发送成功")
            return True
        print(f"  模板发送失败: {result}")
        return False
    except Exception as e:
        print(f"  模板异常: {e}")
        return False


def send_msg(access_token, openid, template_id, text):
    """优先客服消息，失败用模板"""
    ok = send_text_msg(access_token, openid, text)
    if not ok:
        lines = text.split("\n")
        title = lines[0] if lines else "选股信号"
        send_template_msg(access_token, openid, template_id, title, text)


# 信号分组
SIGNAL_GROUPS = [
    # 买入信号（关注表+全市场，从强到弱）
    ("buy_heavy", "【可以重仓买入】"),
    ("buy_medium", "【可以中仓买入】"),
    ("buy_light", "【可以轻仓买入】"),
    ("buy_watch", "【重点关注买入】"),
    # 持仓信号
    ("hold_keep", "【建议持续持有】"),
    # 卖出信号（只针对持仓，从轻到重）
    ("sell_watch", "【重点关注卖出】"),
    ("sell_light", "【可以适当卖出】"),
    ("sell_medium", "【可以中仓卖出】"),
    ("sell_heavy", "【可以大量卖出】"),
    ("true_decline", "【基本面恶化警告】"),
]


def format_stock_line(s):
    """一只股票一行"""
    name = s.get("name", "")
    code = s.get("code", "")
    pe = s.get("pe", 0)
    price = s.get("price", 0)
    category = s.get("category", "")
    signal_text = s.get("signal_text", "")

    line = f"{name}({code})"
    if pe and pe > 0:
        line += f" PE={pe:.1f}"
    if price and price > 0:
        line += f" {price:.2f}元"
    if category:
        line += f"\n  [{category}]"
    if signal_text:
        line += f"\n  {signal_text}"
    return line


def send_daily_report(watchlist_signals, candidates, holding_signals,
                      false_declines=None, true_declines=None, config=None):
    """
    每天发送消息
    买入信号：来自关注表PE + 候选池PE（只有假跌或正常下跌才触发）
    卖出信号：来自持仓PE + 持仓真跌（基本面恶化）
    """
    if config is None:
        config = load_config()
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

    if false_declines is None:
        false_declines = []
    if true_declines is None:
        true_declines = []

    # 合并所有买入信号（关注表 + 候选池，只看买入方向）
    all_signals = []
    for s in watchlist_signals:
        if s.get("signal") and "buy" in s["signal"]:
            all_signals.append(s)
    for s in candidates:
        if s.get("signal") and "buy" in s["signal"]:
            all_signals.append(s)

    # 假跌的股票也加入买入信号（按PE级别分配信号）
    for s in false_declines:
        change = abs(s.get("change_pct", 0))
        if change >= 7:
            s["signal"] = "buy_heavy"
        elif change >= 5:
            s["signal"] = "buy_light"
        else:
            s["signal"] = "buy_watch"
        all_signals.append(s)

    # 合并所有卖出信号（只来自持仓）
    for s in holding_signals:
        if s.get("signal") and "sell" in s["signal"]:
            all_signals.append(s)

    # 真跌作为独立的"基本面恶化"信号（只来自持仓）
    for s in true_declines:
        s["signal"] = "true_decline"
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

        send_msg(access_token, openid, template_id, text)

    # 无信号时发"无推荐"
    if not sent_any:
        text = f"芒格选股 {today}\n\n今日无推荐\n关注表均在合理区间\n持仓无异常\n继续观察"
        send_msg(access_token, openid, template_id, text)

    print("推送完成")
