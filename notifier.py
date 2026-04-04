"""
通知推送模块 - 通过微信测试号模板消息推送
支持买卖信号分级推送
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
        if "access_token" in data:
            return data["access_token"]
        else:
            print(f"获取 access_token 失败: {data}")
            return None
    except Exception as e:
        print(f"获取 access_token 异常: {e}")
        return None


def send_wechat(title, content, config=None):
    if config is None:
        config = load_config()

    wx = config["wechat"]
    if wx["appid"] == "YOUR_APPID":
        print("微信未配置，跳过推送")
        print(f"标题: {title}")
        print(f"内容:\n{content}")
        return False

    access_token = get_access_token(wx["appid"], wx["appsecret"])
    if not access_token:
        return False

    url = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={access_token}"
    data = {
        "touser": wx["openid"],
        "template_id": wx["template_id"],
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


SIGNAL_LABELS = {
    "buy_heavy": "🔴 可以重仓买入",
    "buy_light": "🟠 可以轻仓买入",
    "buy_watch": "🟡 重点关注买入",
    "hold": "⚪ 持有",
    "sell_watch": "🟡 重点关注卖出",
    "sell_light": "🟠 可以适当卖出",
    "sell_heavy": "🔴 可以大量卖出",
}


def format_candidate_list(candidates):
    """格式化候选池"""
    if not candidates:
        return ""

    lines = []
    buy_stocks = [s for s in candidates if s.get("signal") and "buy" in s["signal"]]
    hold_stocks = [s for s in candidates if s.get("signal") == "hold"]
    sell_stocks = [s for s in candidates if s.get("signal") and "sell" in s["signal"]]

    if buy_stocks:
        lines.append("【买入信号】\n")
        for s in buy_stocks:
            label = SIGNAL_LABELS.get(s["signal"], "")
            lines.append(f"{label}")
            lines.append(f"  {s['name']}（{s['code']}）")
            lines.append(f"  股价:{s.get('price', 0):.2f}元 | {s['signal_text']}")
            # 关键指标
            for key in ["roe", "gross_margin", "debt", "opm", "fcf"]:
                if key in s.get("checks", {}):
                    lines.append(f"  {s['checks'][key]['detail']}")
            lines.append("")

    if sell_stocks:
        lines.append("【卖出信号】\n")
        for s in sell_stocks:
            label = SIGNAL_LABELS.get(s["signal"], "")
            lines.append(f"{label}")
            lines.append(f"  {s['name']}（{s['code']}）")
            lines.append(f"  股价:{s.get('price', 0):.2f}元 | {s['signal_text']}")
            lines.append("")

    if hold_stocks:
        lines.append(f"【持有观察】共{len(hold_stocks)}只\n")
        for s in hold_stocks:
            lines.append(f"  {s['name']}（{s['code']}）PE={s.get('pe', 0):.1f} 股价{s.get('price', 0):.2f}元")

    return "\n".join(lines)


def format_holdings_signals(signals):
    """格式化持仓信号"""
    if not signals:
        return ""

    lines = ["【持仓信号】\n"]
    for sig in signals:
        label = SIGNAL_LABELS.get(sig["signal"], "")
        lines.append(f"{label}")
        lines.append(f"  {sig['name']}（{sig['code']}）持有{sig['shares']}股")
        lines.append(f"  {sig['signal_text']}")

        if sig.get("cost") and sig.get("current_price") and sig["cost"] > 0:
            pnl = (sig["current_price"] - sig["cost"]) * sig["shares"]
            pnl_pct = (sig["current_price"] / sig["cost"] - 1) * 100
            pnl_sign = "+" if pnl >= 0 else ""
            lines.append(f"  成本:{sig['cost']:.2f} 现价:{sig['current_price']:.2f} 盈亏:{pnl_sign}{pnl:,.0f}元({pnl_sign}{pnl_pct:.1f}%)")
        lines.append("")

    return "\n".join(lines)


def send_daily_report(buy_list, sell_list, config=None):
    """发送每日报告
    buy_list: 候选池股票列表（含信号）
    sell_list: 持仓信号列表
    """
    # 只有存在买卖信号时才推送
    has_buy_signal = any(s.get("signal") and "buy" in s["signal"] for s in buy_list)
    has_sell_signal = any(s.get("signal") and "sell" in s["signal"] for s in buy_list)
    has_holding_signal = len(sell_list) > 0

    if not has_buy_signal and not has_sell_signal and not has_holding_signal:
        print("今日无买卖信号，不推送")
        print(f"候选池共{len(buy_list)}只好公司，均在合理区间")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    title = f"选股信号 {today}"

    content = format_candidate_list(buy_list)

    if sell_list:
        content += "\n" + format_holdings_signals(sell_list)

    content += f"\n候选池共{len(buy_list)}只好公司"
    content += "\n\n仅供参考，不构成投资建议。"

    send_wechat(title, content, config)
