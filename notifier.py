"""
通知推送模块 - 通过微信测试号模板消息推送到个人微信
"""

import requests
import yaml
from datetime import datetime


def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_access_token(appid, appsecret):
    """获取微信 access_token"""
    url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={appid}&secret={appsecret}"
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if "access_token" in data:
            return data["access_token"]
        else:
            print(f"获取 access_token 失败: {data}")
            return None
    except Exception as e:
        print(f"获取 access_token 异常: {e}")
        return None


def send_wechat(title, content, config=None):
    """通过微信测试号发送模板消息"""
    if config is None:
        config = load_config()

    wx = config["wechat"]
    appid = wx["appid"]
    appsecret = wx["appsecret"]
    openid = wx["openid"]
    template_id = wx["template_id"]

    if appid == "YOUR_APPID":
        print("微信测试号未配置，跳过推送")
        print(f"标题: {title}")
        print(f"内容:\n{content}")
        return False

    access_token = get_access_token(appid, appsecret)
    if not access_token:
        return False

    url = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={access_token}"
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
            print(f"微信推送成功: {title}")
            return True
        else:
            print(f"微信推送失败: {result}")
            return False
    except Exception as e:
        print(f"微信推送异常: {e}")
        return False


def format_buy_signals(buy_list):
    """格式化买入信号"""
    if not buy_list:
        return ""

    lines = ["【买入信号】\n"]
    for i, stock in enumerate(buy_list, 1):
        checks = stock.get("checks", {})
        val = stock.get("valuation", {})

        lines.append(f"{i}. {stock.get('name', '')}（{stock['code']}）")

        for key, label in [("roe", "ROE"), ("debt", "负债"), ("fcf", "现金流"), ("opm", "利润率"), ("valuation", "估值")]:
            if key in checks:
                detail = checks[key].get("detail", "")
                lines.append(f"   {label}: {detail}")

        if val.get("price"):
            lines.append(f"   当前价: {val['price']:.2f}元")
        lines.append("")

    return "\n".join(lines)


def format_sell_signals(sell_list):
    """格式化卖出信号"""
    if not sell_list:
        return ""

    lines = ["【卖出信号】\n"]
    for i, sig in enumerate(sell_list, 1):
        lines.append(f"{i}. {sig['name']}（{sig['code']}）— 持有{sig['shares']}股")
        lines.append(f"   {sig['action']}：卖出 {sig['sell_shares']}股")

        if sig.get("cost") and sig.get("current_price"):
            pnl = (sig["current_price"] - sig["cost"]) * sig["shares"]
            pnl_pct = (sig["current_price"] / sig["cost"] - 1) * 100 if sig["cost"] > 0 else 0
            pnl_sign = "+" if pnl >= 0 else ""
            lines.append(f"   成本: {sig['cost']:.2f} | 现价: {sig['current_price']:.2f} | 盈亏: {pnl_sign}{pnl:,.0f}元({pnl_sign}{pnl_pct:.1f}%)")

        lines.append(f"   原因: {'；'.join(sig.get('warnings', []))}")
        lines.append("")

    return "\n".join(lines)


def send_daily_report(buy_list, sell_list, config=None):
    """发送每日报告"""
    if not buy_list and not sell_list:
        print("今日无买卖信号，不推送")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    title = f"芒格选股信号 {today}"

    content = ""
    if buy_list:
        content += format_buy_signals(buy_list)
    if sell_list:
        content += format_sell_signals(sell_list)

    content += "\n仅供参考，不构成投资建议。"

    send_wechat(title, content, config)
