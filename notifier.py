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
    """模板消息备用

    2026-04-20 BUG-021 修：模板消息字段在微信公众号后台已定义，必须按字段名传。
    若不知字段名，至少把 content 拼到 title 里防止内容空白。
    """
    url = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={access_token}"
    # 拼接 title+content 作为完整内容，防止 content 字段名不匹配导致空白
    safe_content = (content or '')[:200].strip()
    if not safe_content:
        safe_content = title  # 兜底，避免发空白消息
    data = {
        "touser": openid, "template_id": template_id,
        "data": {
            "title": {"value": title[:60]},   # 标题截短防止过长
            "content": {"value": safe_content},
        },
    }
    try:
        body = json_lib.dumps(data, ensure_ascii=False).encode("utf-8")
        resp = requests.post(url, data=body, headers={"Content-Type": "application/json; charset=utf-8"}, timeout=30)
        result = resp.json()
        if result.get("errcode") == 0:
            print(f"  ✅ 模板发送成功（msgid={result.get('msgid')}）")
            return True
        # 失败的明显标记（之前是 print 但被混在大量输出里看不到）
        print(f"  🚨 模板发送也失败！errcode={result.get('errcode')} {result.get('errmsg')}")
        print(f"     完整响应: {result}")
        print(f"     提示：用户可能取消订阅 / 模板被禁用 / 字段名不匹配")
        return False
    except Exception as e:
        print(f"  🚨 模板异常: {e}")
        return False


def send_msg(access_token, openid, template_id, text):
    """优先客服消息，失败用模板

    2026-04-20 BUG-021 修：之前 fallback 模板消息后没拿返回值，
    导致客服+模板都失败时仍打印"已推送"假象。
    现在返回 True/False 给上层，让上层准确知道是否真的发出去了。
    """
    ok = send_text_msg(access_token, openid, text)
    if ok:
        return True
    # 客服失败 → 走模板
    lines = text.split("\n")
    title = lines[0] if lines and lines[0].strip() else "选股信号"
    template_ok = send_template_msg(access_token, openid, template_id, title, text)
    if not template_ok:
        # 客服+模板双失败 — 明显标记，让用户从日志能立即看到
        print(f"  🚨🚨🚨 客服+模板双失败，本条消息未送达！")
        print(f"     原始内容前 200 字: {text[:200]}")
    return template_ok


# 信号分组（用emoji区分紧急程度，从安全→危险）
SIGNAL_GROUPS = [
    # 买入信号（绿色系，从深到浅）
    ("buy_heavy", "🟢🟢🟢【可以重仓买入】"),
    ("buy_medium", "🟢🟢【可以中仓买入】"),
    ("buy_light", "🟢【可以轻仓买入】"),
    ("buy_watch", "👀【重点关注买入】"),
    # 持仓加仓信号
    ("buy_add", "🟢📈【持仓可加仓】"),
    # 持仓信号（蓝色）
    ("hold_keep", "🔵【建议持续持有】"),
    # 卖出信号（从黄到红，越来越紧急）
    ("sell_watch", "🟡【重点关注卖出】"),
    ("sell_light", "🟠【可以适当卖出】"),
    ("sell_medium", "🔴【可以中仓卖出】"),
    ("sell_heavy", "🔴🔴【可以大量卖出】"),
    ("true_decline", "🚨🚨🚨【基本面恶化！】"),
]


def format_stock_line(s):
    """一只股票一行"""
    name = s.get("name", "")
    code = s.get("code", "")
    pe = s.get("pe", 0)
    price = s.get("price", 0)
    category = s.get("category", "")
    signal_text = s.get("signal_text", "")
    total_score = s.get("total_score", 0)
    div_yield = s.get("dividend_yield", 0)

    line = f"{name}({code})"
    if pe and pe > 0:
        line += f" PE={pe:.1f}"
    if div_yield and div_yield > 0:
        line += f" 股息{div_yield:.1f}%"
    if price and price > 0:
        line += f" {price:.2f}元"
    if total_score > 0:
        line += f" [{total_score}分]"
    if category:
        line += f"\n  [{category}]"
    if signal_text:
        line += f"\n  {signal_text}"
    return line


def send_daily_report(watchlist_signals, candidates, holding_signals,
                      position_warnings=None, swap_suggestions=None, config=None):
    """每天发送消息，按信号分组+仓位警告+换仓建议"""
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

    # 合并所有信号（持仓优先去重，避免同一股票两条矛盾信号）
    # 优先级：holding_signals > candidates > watchlist_signals
    # 同一 code 只保留最优先的那条
    #
    # F-1（2026-04-24 用户反馈）：推送规则
    #   - holding_signals 全发（持仓的买卖都要知道）
    #   - candidates / watchlist_signals 只发买入信号（sell_* 是全市场扫出来的
    #     非持仓股，推给用户没意义还添堵）
    all_signals = []
    seen_codes = set()
    for s in holding_signals:
        code = s.get("code")
        if s.get("signal") and s["signal"] not in (None,):
            all_signals.append(s)
            if code:
                seen_codes.add(code)
    for s in candidates:
        code = s.get("code")
        sig = s.get("signal") or ""
        # 只保留买入信号（buy_heavy/buy_medium/buy_light/buy_watch/buy_add）
        # 跳过 sell_* / hold / None
        if code and code not in seen_codes and sig.startswith("buy"):
            all_signals.append(s)
            seen_codes.add(code)
    for s in watchlist_signals:
        code = s.get("code")
        sig = s.get("signal") or ""
        # 同 candidates，只保留买入信号
        if code and code not in seen_codes and sig.startswith("buy"):
            all_signals.append(s)
            seen_codes.add(code)

    # 按信号分组发送
    sent_any = False
    for signal_key, signal_title in SIGNAL_GROUPS:
        group = [s for s in all_signals if s.get("signal") == signal_key]
        if not group:
            continue
        lines = [f"{signal_title} {today}", ""]
        for s in group:
            _line = format_stock_line(s)
            if _line and _line.strip():
                lines.append(_line)
                lines.append("")  # 每只股票之间空一行
        # 防空消息保护：标题+内容至少要有 3 行（标题+空+至少一条股票）
        _msg = "\n".join(lines).strip()
        # 只发送有实质内容的消息（至少 20 字符）
        if len(_msg) >= 20:
            # BUG-021：拿真实返回值，避免发送失败时还打"已推送"假象
            sent = send_msg(access_token, openid, template_id, _msg)
            if sent:
                sent_any = True
                print(f"  ✅ 已推送 {signal_title}：{len(group)} 只")
            else:
                print(f"  🚨 推送失败 {signal_title}：{len(group)} 只（未送达）")
        else:
            print(f"  ⚠ 跳过 {signal_title}：消息内容过短（{len(_msg)}字）")

    # 仓位警告
    if position_warnings:
        lines = [f"⚠️⚠️【仓位警告】 {today}", ""]
        for w in position_warnings:
            emoji = "🚨" if w.get("level") == "danger" else "⚠️"
            _name = w.get('name', '').strip()
            _code = w.get('code', '').strip()
            _text = w.get('text', '').strip()
            if _name or _code or _text:
                lines.append(f"{emoji} {_name}({_code})")
                lines.append(f"  {_text}")
                lines.append("")
        _msg = "\n".join(lines).strip()
        if len(_msg) >= 20:
            sent = send_msg(access_token, openid, template_id, _msg)
            if sent:
                sent_any = True
                print(f"  ✅ 已推送仓位警告：{len(position_warnings)} 条")
            else:
                print(f"  🚨 仓位警告推送失败：{len(position_warnings)} 条（未送达）")
        else:
            print(f"  ⚠ 跳过仓位警告：内容过短")

    # 换仓建议
    if swap_suggestions:
        lines = [f"💡💡【换仓建议】 {today}", ""]
        for s in swap_suggestions:
            _sn = s.get('sell_name', '').strip()
            _bn = s.get('buy_name', '').strip()
            if _sn and _bn:
                lines.append(f"📤 卖出 {_sn} {s.get('sell_ratio','')}")
                lines.append(f"📥 买入 {_bn}")
                lines.append("")
        _msg = "\n".join(lines).strip()
        if len(_msg) >= 20:
            send_msg(access_token, openid, template_id, _msg)
            sent_any = True
            print(f"  已推送换仓建议：{len(swap_suggestions)} 条")
        else:
            print(f"  ⚠ 跳过换仓建议：内容过短")

    # 持仓备注定时提醒（到期提醒）
    try:
        from stock_notes_manager import get_pending_alerts, mark_fired, format_alerts_for_wechat
        alerts_msg = format_alerts_for_wechat()
        if alerts_msg:
            send_msg(access_token, openid, template_id, alerts_msg)
            # 标记已推送（fired_count +1）
            for a in get_pending_alerts():
                mark_fired(a["code"], a["reminder_id"])
            sent_any = True
            print(f"  已推送 {len(get_pending_alerts())} 条到期提醒")
    except Exception as _e:
        print(f"  提醒推送失败: {_e}")

    if not sent_any:
        send_msg(access_token, openid, template_id,
                 f"芒格选股 {today}\n\n今日无推荐\n关注表均在合理区间\n持仓无异常\n继续观察")

    print("推送完成")
