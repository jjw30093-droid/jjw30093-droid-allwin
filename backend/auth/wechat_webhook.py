"""微信公众号消息推送(webhook)的校验与解析:纯函数为主,便于离线测试。

安全模型(docs/auth-wechat.md §3):
- 微信服务器与本站共享一个后台配置的 Token;每次请求带 signature/timestamp/nonce,
  signature = sha1(按字典序排序后拼接 [token, timestamp, nonce])。
- 签名只证明"发起方知道 Token",不防重放 → 额外要求 timestamp 在 ±TIMESTAMP_TOLERANCE
  秒内,且 nonce 一次性登记(wechat_webhook_nonces,原子 INSERT OR IGNORE)。
- 本轮为明文模式(公众号后台三种模式中的明文/兼容均可接);安全模式(AES)未实现,
  如实标注于 docs/auth-wechat.md。
"""

import hashlib
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import timedelta

from backend.db.util import utc_now, utc_now_iso

TIMESTAMP_TOLERANCE_SECONDS = 300
NONCE_RETENTION_MINUTES = 15
MAX_BODY_BYTES = 64 * 1024      # 事件 XML 远小于此;超限直接拒绝,防解析放大

# 带参二维码扫码事件里,未关注用户的 EventKey 前缀(微信约定)
QRSCENE_PREFIX = "qrscene_"


def compute_signature(token: str, timestamp: str, nonce: str) -> str:
    joined = "".join(sorted([token, timestamp, nonce]))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def verify_signature(token: str, timestamp: str, nonce: str, signature: str) -> bool:
    if not token or not timestamp or not nonce or not signature:
        return False
    return compute_signature(token, timestamp, nonce) == signature.lower()


def timestamp_fresh(timestamp: str, now_epoch: int) -> bool:
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    return abs(now_epoch - ts) <= TIMESTAMP_TOLERANCE_SECONDS


def register_nonce(conn: sqlite3.Connection, nonce: str) -> bool:
    """一次性登记 nonce。返回 True=首次出现;False=重放(调用方应静默吞掉而非报错,
    因为微信 5 秒未收到应答会用同一请求重试,重试不该被当成攻击)。"""
    cutoff = (utc_now() - timedelta(minutes=NONCE_RETENTION_MINUTES)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    conn.execute("DELETE FROM wechat_webhook_nonces WHERE seen_at < ?", (cutoff,))
    cur = conn.execute(
        "INSERT OR IGNORE INTO wechat_webhook_nonces (nonce, seen_at) VALUES (?, ?)",
        (nonce, utc_now_iso()),
    )
    return cur.rowcount == 1


@dataclass(frozen=True)
class WechatEvent:
    msg_type: str                 # event / text / ...
    event: str | None             # SCAN / subscribe / unsubscribe / ...(仅 msg_type=event)
    openid: str                   # FromUserName
    to_user: str                  # ToUserName(公众号原始 ID,透传给被动回复)
    scene_str: str | None         # 带参二维码场景值(SCAN 直接取;subscribe 去 qrscene_ 前缀)


class WebhookParseError(ValueError):
    pass


def parse_event_xml(body: bytes) -> WechatEvent:
    """解析微信事件 XML。stdlib ElementTree 不解析外部实体(无 XXE);
    体积上限由调用方(MAX_BODY_BYTES)先行约束。"""
    if len(body) > MAX_BODY_BYTES:
        raise WebhookParseError("body 超过大小上限")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise WebhookParseError(f"XML 解析失败: {e}") from e

    def text(tag: str) -> str | None:
        node = root.find(tag)
        return node.text if node is not None else None

    openid = text("FromUserName")
    to_user = text("ToUserName")
    msg_type = (text("MsgType") or "").lower()
    if not openid or not to_user or not msg_type:
        raise WebhookParseError("缺少 FromUserName/ToUserName/MsgType")

    event = text("Event")
    event_key = text("EventKey")

    scene_str: str | None = None
    if msg_type == "event" and event:
        ev = event.lower()
        if ev == "scan":
            # 已关注用户扫带参二维码:EventKey 就是 scene_str
            scene_str = event_key or None
        elif ev == "subscribe" and event_key:
            # 未关注用户扫码后关注:EventKey = qrscene_<scene_str>
            if event_key.startswith(QRSCENE_PREFIX):
                scene_str = event_key[len(QRSCENE_PREFIX):] or None

    return WechatEvent(
        msg_type=msg_type,
        event=event.lower() if event else None,
        openid=openid,
        to_user=to_user,
        scene_str=scene_str,
    )


def _cdata_safe(s: str) -> str:
    """CDATA 内唯一的非法序列是 ']]>',按标准拆段技巧断开。"""
    return s.replace("]]>", "]]]]><![CDATA[>")


def build_text_reply(event: WechatEvent, content: str, create_time_epoch: int) -> str:
    """被动回复文本消息(明文模式)。To/From 与来件互换。"""
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{_cdata_safe(event.openid)}]]></ToUserName>"
        f"<FromUserName><![CDATA[{_cdata_safe(event.to_user)}]]></FromUserName>"
        f"<CreateTime>{create_time_epoch}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{_cdata_safe(content)}]]></Content>"
        "</xml>"
    )
