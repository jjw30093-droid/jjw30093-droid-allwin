"""测试共用:公众号发码登录的完整流程(2026-10 起取代带参数二维码路线)。

流程与生产一致(webhook 入站链路不依赖 Provider,离线可完整验证):
1. POST /api/v1/auth/wechat/device            → request_id + 浏览器 secret + 4 位 login_code
2. POST /api/v1/auth/wechat/webhook(签名)    → 模拟微信服务器投递"用户把验证码发给公众号"的文本消息
3. POST /api/v1/auth/wechat/device/{id}/claim → 领取会话(Set-Cookie)

签名 Token 用 development 默认值 dev-webhook-token(backend/auth/config.py)。
"""

import time
import uuid

from backend.auth.wechat_webhook import compute_signature

DEV_WEBHOOK_TOKEN = "dev-webhook-token"


def text_message_xml(content: str, openid: str, msg_type: str = "text") -> str:
    body = f"<Content><![CDATA[{content}]]></Content>" if msg_type == "text" else "<MediaId><![CDATA[m1]]></MediaId>"
    return (
        "<xml>"
        "<ToUserName><![CDATA[gh_mock_oa]]></ToUserName>"
        f"<FromUserName><![CDATA[{openid}]]></FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        f"<MsgType><![CDATA[{msg_type}]]></MsgType>"
        f"{body}"
        f"<MsgId>{uuid.uuid4().int % 10**12}</MsgId>"
        "</xml>"
    )


def event_xml(event: str, openid: str) -> str:
    return (
        "<xml>"
        "<ToUserName><![CDATA[gh_mock_oa]]></ToUserName>"
        f"<FromUserName><![CDATA[{openid}]]></FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        "<MsgType><![CDATA[event]]></MsgType>"
        f"<Event><![CDATA[{event}]]></Event>"
        "</xml>"
    )


def signed_webhook_params(token: str = DEV_WEBHOOK_TOKEN, nonce: str | None = None) -> dict:
    timestamp = str(int(time.time()))
    nonce = nonce or uuid.uuid4().hex
    return {
        "signature": compute_signature(token, timestamp, nonce),
        "timestamp": timestamp,
        "nonce": nonce,
    }


def post_webhook(client, xml: str, **kwargs):
    """向 webhook 投递一条签名合法的消息/事件(kwargs 可覆盖 params/token)。"""
    token = kwargs.pop("token", DEV_WEBHOOK_TOKEN)
    params = kwargs.pop("params", None) or signed_webhook_params(token)
    return client.post(
        "/api/v1/auth/wechat/webhook",
        params=params,
        content=xml,
        headers={"Content-Type": "application/xml"},
        **kwargs,
    )


def post_code(client, code: str, openid: str, **kwargs):
    """模拟用户把验证码发给公众号。"""
    return post_webhook(client, text_message_xml(code, openid), **kwargs)


def wechat_code_login(client, openid: str = "mock-openid-user-1", ip: str = "203.0.113.1"):
    """完整发码登录;断言各步成功,返回 claim 响应(Set-Cookie 已生效在 client 上)。"""
    r_create = client.post(
        "/api/v1/auth/wechat/device", headers={"x-real-ip": ip}
    )
    assert r_create.status_code == 200, r_create.text
    body = r_create.json()

    r_msg = post_code(client, body["login_code"], openid)
    assert r_msg.status_code == 200, r_msg.text
    assert "登录成功" in r_msg.text, r_msg.text

    r_claim = client.post(
        f"/api/v1/auth/wechat/device/{body['request_id']}/claim",
        json={"secret": body["secret"]},
        headers={"x-real-ip": ip},
    )
    assert r_claim.status_code == 200, r_claim.text
    assert r_claim.json()["status"] == "claimed"
    return r_claim


# 既有测试大量调用这个名字;流程已改为发码登录,语义不变(拿到一个已登录的 client)
wechat_scan_login = wechat_code_login
