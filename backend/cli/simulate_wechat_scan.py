"""development 专用:模拟「用户把验证码发给公众号」——向本地 webhook 投递签名的文本消息。

(文件名沿用旧的 simulate_wechat_scan;2026-10 起登录路线改为公众号发码登录,
不再有扫码事件。)

用法(本地后端跑在 8000 端口,登录页上能看到 4 位验证码):
    python -m backend.cli.simulate_wechat_scan --code 1234
    python -m backend.cli.simulate_wechat_scan --code 1234 --openid mock-openid-2

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
from xml.sax.saxutils import escape


def build_text_xml(content: str, openid: str) -> str:
    return (
        "<xml>"
        "<ToUserName><![CDATA[gh_mock_oa]]></ToUserName>"
        f"<FromUserName><![CDATA[{escape(openid)}]]></FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{content.replace(']]>', '')}]]></Content>"
        f"<MsgId>{uuid.uuid4().int % 10**12}</MsgId>"
        "</xml>"
    )


def main(argv=None) -> int:
    if os.environ.get("APP_ENV") == "production":
        print("production 环境拒绝运行模拟发码工具", file=sys.stderr)
        return 1

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--code", required=True, help="登录页上显示的 4 位验证码(也可故意发错,看回复)")
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
        content=build_text_xml(args.code, args.openid),
        headers={"Content-Type": "application/xml"},
        timeout=10,
    )
    print(f"HTTP {resp.status_code}")
    print(resp.text)
    return 0 if resp.status_code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
