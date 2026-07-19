"""小工具:UTC 时间戳、UUID、哈希。全项目统一从这里取,避免各处自造格式。"""

import hashlib
import secrets
import uuid
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """UTC ISO8601,秒级,Z 后缀:2026-07-19T12:00:00Z"""
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def new_uuid() -> str:
    return str(uuid.uuid4())


def sha256_hex(data) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def new_token(nbytes: int = 32) -> str:
    """≥256bit 随机 token(urlsafe)。数据库只允许存它的 sha256_hex。"""
    return secrets.token_urlsafe(nbytes)
