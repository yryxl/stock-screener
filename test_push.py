"""
测试企业微信推送 - 发送一条测试消息验证通道是否正常
"""
import yaml
import requests


def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_push():
    config = load_config()

    corpid = config["wecom"]["corpid"]
    secret = config["wecom"]["secret"]
    agentid = config["wecom"]["agentid"]

    print(f"企业ID: {corpid}")
    print(f"AgentId: {agentid}")

    # 1. 获取 access_token
    url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={corpid}&corpsecret={secret}"
    resp = requests.get(url, timeout=10)
    data = resp.json()
    print(f"获取token结果: {data}")

    if data.get("errcode") != 0:
        print(f"获取 access_token 失败: {data.get('errmsg')}")
        return

    access_token = data["access_token"]

    # 2. 发送测试消息
    send_url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={access_token}"
    msg = {
        "touser": "@all",
        "msgtype": "text",
        "agentid": agentid,
        "text": {
            "content": "芒格选股系统 - 推送测试\n\n如果你在微信看到这条消息，说明企业微信推送配置成功！\n\n以后有买入/卖出信号时，会通过这个渠道通知你。"
        },
    }

    resp = requests.post(send_url, json=msg, timeout=30)
    result = resp.json()
    print(f"发送结果: {result}")

    if result.get("errcode") == 0:
        print("推送成功！请检查微信是否收到消息。")
    else:
        print(f"推送失败: {result.get('errmsg')}")


if __name__ == "__main__":
    test_push()
