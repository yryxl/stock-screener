"""
通知推送模块 - 通过 PushPlus 推送消息到微信
"""

import requests
import yaml
from datetime import datetime


def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def send_wechat(title, content, config=None):
    """通过 PushPlus 发送微信消息"""
    if config is None:
        config = load_config()

    token = config["pushplus"]["token"]
    if token == "YOUR_PUSHPLUS_TOKEN":
        print("PushPlus token 未配置，跳过推送")
        print(f"标题: {title}")
        print(f"内容:\n{content}")
        return False

    url = "http://www.pushplus.plus/send"
    data = {
        "token": token,
        "title": title,
        "content": content,
        "template": "html",
    }

    try:
        resp = requests.post(url, json=data, timeout=30)
        result = resp.json()
        if result.get("code") == 200:
            print(f"微信推送成功: {title}")
            return True
        else:
            print(f"微信推送失败: {result.get('msg', '未知错误')}")
            return False
    except Exception as e:
        print(f"微信推送异常: {e}")
        return False


def format_buy_signals(buy_list):
    """格式化买入信号为HTML"""
    if not buy_list:
        return ""

    html = "<h2>买入信号</h2>"
    for i, stock in enumerate(buy_list, 1):
        checks = stock.get("checks", {})
        val = stock.get("valuation", {})

        html += f"""
        <div style="border:1px solid #4CAF50;border-radius:8px;padding:12px;margin:8px 0;background:#f9fff9;">
            <h3 style="color:#4CAF50;">
                {i}. {stock.get('name', '')}（{stock['code']}）
            </h3>
            <table style="width:100%;font-size:14px;">
        """

        for key, label in [("roe", "ROE"), ("debt", "负债"), ("fcf", "现金流"), ("opm", "利润率"), ("valuation", "估值")]:
            if key in checks:
                detail = checks[key].get("detail", "")
                html += f"<tr><td><b>{label}</b></td><td>{detail}</td></tr>"

        if val.get("price"):
            html += f"<tr><td><b>当前价</b></td><td>{val['price']:.2f} 元</td></tr>"

        html += "</table></div>"

    return html


def format_sell_signals(sell_list):
    """格式化卖出信号为HTML"""
    if not sell_list:
        return ""

    html = "<h2>卖出信号</h2>"
    for i, sig in enumerate(sell_list, 1):
        color = "#f44336"
        html += f"""
        <div style="border:1px solid {color};border-radius:8px;padding:12px;margin:8px 0;background:#fff9f9;">
            <h3 style="color:{color};">
                {i}. {sig['name']}（{sig['code']}）— 持有 {sig['shares']}股
            </h3>
            <p style="font-size:16px;font-weight:bold;">
                {sig['action']}：卖出 {sig['sell_shares']}股
            </p>
        """

        if sig.get("cost") and sig.get("current_price"):
            pnl = (sig["current_price"] - sig["cost"]) * sig["shares"]
            pnl_pct = (sig["current_price"] / sig["cost"] - 1) * 100 if sig["cost"] > 0 else 0
            pnl_color = "#4CAF50" if pnl >= 0 else "#f44336"
            html += f"""
            <p>成本价: {sig['cost']:.2f} | 现价: {sig['current_price']:.2f} |
            <span style="color:{pnl_color};">浮动盈亏: {pnl:+,.0f}元 ({pnl_pct:+.1f}%)</span></p>
            """

        html += "<p><b>触发原因：</b></p><ul>"
        for w in sig.get("warnings", []):
            html += f"<li>{w}</li>"
        html += "</ul></div>"

    return html


def send_daily_report(buy_list, sell_list, config=None):
    """发送每日报告"""
    if not buy_list and not sell_list:
        print("今日无买卖信号，不推送")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    title = f"选股信号 {today}"

    html = f"<h1>芒格选股系统 - {today}</h1>"

    if buy_list:
        html += format_buy_signals(buy_list)

    if sell_list:
        html += format_sell_signals(sell_list)

    html += """
    <hr>
    <p style="color:#999;font-size:12px;">
        本消息由芒格价值投资选股系统自动生成，仅供参考，不构成投资建议。
    </p>
    """

    send_wechat(title, html, config)
