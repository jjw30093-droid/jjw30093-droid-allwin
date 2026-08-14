"""测试共用:带参数二维码 + webhook 路线的完整扫码登录流(替代旧 mock OAuth 流)。

流程与生产一致(webhook 入站链路不依赖 Provider,离线可完整验证):
1. POST /api/v1/auth/wechat/device            → request_id + 浏览器 secret
2. POST /api/v1/auth/wechat/webhook(签名)    → 模拟微信服务器投递 SCAN 事件
3. POST /api/v1/auth/wechat/device/{id}/claim → 领取会话(Set-Cookie)

签名 Token 用 development 默认值 dev-webhook-token(backend/auth/config.py)。
"""

import time
import uuid

from backend.auth.wechat_webhook import compute_signature

DEV_WEBHOOK_TOKEN = "dev-webhook-token"


def scan_event_xml(scene_str: str, openid: str, event: str = "SCAN") -> str:
    event_key = scene_str if event == "SCAN" else f"qrscene_{scene_str}"
    return (
        "<xml>"
        "<ToUserName><![CDATA[gh_mock_oa]]></ToUserName>"
        f"<FromUserName><![CDATA[{openid}]]></FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        "<MsgType><![CDATA[event]]></MsgType>"
        f"<Event><![CDATA[{event}]]></Event>"
        f"<EventKey><![CDATA[{event_key}]]></EventKey>"
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


def post_scan(client, scene_str: str, openid: str, event: str = "SCAN", **kwargs):
    """向 webhook 投递一条签名合法的扫码事件(kwargs 可覆盖 params/token)。"""
    token = kwargs.pop("token", DEV_WEBHOOK_TOKEN)
    params = kwargs.pop("params", None) or signed_webhook_params(token)
    return client.post(
        "/api/v1/auth/wechat/webhook",
        params=params,
        content=scan_event_xml(scene_str, openid, event=event),
        headers={"Content-Type": "application/xml"},
        **kwargs,
    )


def wechat_scan_login(client, openid: str = "mock-openid-user-1", ip: str = "203.0.113.1"):
    """完整扫码登录;断言各步成功,返回 claim 响应(Set-Cookie 已生效在 client 上)。"""
    r_create = client.post(
        "/api/v1/auth/wechat/device", headers={"x-real-ip": ip}
    )
    assert r_create.status_code == 200, r_create.text
    body = r_create.json()

    r_scan = post_scan(client, body["request_id"], openid)
    assert r_scan.status_code == 200, r_scan.text

    r_claim = client.post(
        f"/api/v1/auth/wechat/device/{body['request_id']}/claim",
        json={"secret": body["secret"]},
        headers={"x-real-ip": ip},
    )
    assert r_claim.status_code == 200, r_claim.text
    assert r_claim.json()["status"] == "claimed"
    return r_claim
