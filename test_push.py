"""
测试微信推送 - 发送一条测试消息
"""
import yaml
import requests


def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_push():
    config = load_config()
    wx = config["wechat"]

    print(f"appID: {wx['appid']}")
    print(f"openID: {wx['openid']}")
    print(f"template_id: {wx['template_id']}")

    # 1. 获取 access_token
    url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={wx['appid']}&secret={wx['appsecret']}"
    resp = requests.get(url, timeout=10)
    data = resp.json()
    print(f"获取token结果: {data}")

    if "access_token" not in data:
        print("获取 access_token 失败")
        return

    access_token = data["access_token"]

    # 2. 发送测试消息
    send_url = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={access_token}"
    msg = {
        "touser": wx["openid"],
        "template_id": wx["template_id"],
        "data": {
            "title": {"value": "芒格选股系统 - 推送测试", "color": "#173177"},
            "content": {"value": "如果你在微信看到这条消息，说明推送配置成功！以后有买入/卖出信号时，会通过这个渠道通知你。", "color": "#333333"},
        },
    }

    resp = requests.post(send_url, json=msg, timeout=30)
    result = resp.json()
    print(f"发送结果: {result}")

    if result.get("errcode") == 0:
        print("推送成功！请检查微信。")
    else:
        print(f"推送失败: {result.get('errmsg')}")


if __name__ == "__main__":
    test_push()
