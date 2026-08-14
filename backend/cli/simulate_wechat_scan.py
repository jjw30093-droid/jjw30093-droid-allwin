"""development 专用:模拟「用户微信扫带参二维码」——向本地 webhook 投递签名 SCAN 事件。

用法(本地后端跑在 8000 端口,登录页二维码下方可看到 request id):
    python -m backend.cli.simulate_wechat_scan --request-id <request_id>
    python -m backend.cli.simulate_wechat_scan --request-id <id> --openid mock-openid-2

安全:production 环境拒绝运行(APP_ENV=production fail-fast);签名 Token 取
WECHAT_WEBHOOK_TOKEN(缺省用 development 默认值 dev-webhook-token),与后端一致
时签名才会通过——这正是 webhook 的安全模型,本工具不绕过任何校验。
"""

import argparse
import hashlib
import os
import sys
import time
import uuid


def build_scan_xml(scene_str: str, openid: str) -> str:
    return (
        "<xml>"
        "<ToUserName><![CDATA[gh_mock_oa]]></ToUserName>"
        f"<FromUserName><![CDATA[{openid}]]></FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        "<MsgType><![CDATA[event]]></MsgType>"
        "<Event><![CDATA[SCAN]]></Event>"
        f"<EventKey><![CDATA[{scene_str}]]></EventKey>"
        "</xml>"
    )


def main(argv=None) -> int:
    if os.environ.get("APP_ENV") == "production":
        print("production 环境拒绝运行模拟扫码工具", file=sys.stderr)
        return 1

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--request-id", required=True, help="device login request id(二维码场景值)")
    ap.add_argument("--openid", default="mock-openid-user-1")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--token", default=os.environ.get("WECHAT_WEBHOOK_TOKEN", "dev-webhook-token"))
    args = ap.parse_args(argv)

    import httpx

    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    signature = hashlib.sha1(
        "".join(sorted([args.token, timestamp, nonce])).encode()
    ).hexdigest()

    resp = httpx.post(
        f"{args.base_url}/api/v1/auth/wechat/webhook",
        params={"signature": signature, "timestamp": timestamp, "nonce": nonce},
        content=build_scan_xml(args.request_id, args.openid),
        headers={"Content-Type": "application/xml"},
        timeout=10,
    )
    print(f"HTTP {resp.status_code}")
    print(resp.text)
    return 0 if resp.status_code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
