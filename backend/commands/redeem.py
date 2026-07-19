"""兑换码:生成(只此一次展示明文)与原子消费。

- DB 只存 SHA-256;
- 消费用 UPDATE ... WHERE status='active' 的 rowcount 判定,防重复使用/并发双花;
- 兑换成功即授予订阅(source=redeem_code)。
"""

import secrets
import sqlite3

from backend.db.util import new_uuid, sha256_hex, utc_now_iso

from .audit import write_audit
from .subscriptions import grant_subscription

_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # 去易混淆字符


def _generate_code() -> str:
    chunk = lambda: "".join(secrets.choice(_ALPHABET) for _ in range(4))
    return f"AW-{chunk()}-{chunk()}-{chunk()}"


class RedeemError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason   # invalid / expired / used


def create_redeem_codes(
    conn: sqlite3.Connection,
    plan_id: str,
    duration_days: int,
    count: int,
    created_by: str,
    expires_at: str | None = None,
    batch_id: str | None = None,
) -> list[dict]:
    if not (1 <= count <= 500):
        raise ValueError("count 需在 1..500")
    if duration_days <= 0:
        raise ValueError("duration_days 必须为正")
    if conn.execute("SELECT 1 FROM plans WHERE id=? AND is_active=1", (plan_id,)).fetchone() is None:
        raise ValueError(f"未知或停用的 plan: {plan_id}")

    batch = batch_id or new_uuid()[:8]
    now = utc_now_iso()
    out = []
    for _ in range(count):
        code = _generate_code()
        code_id = new_uuid()
        conn.execute(
            "INSERT INTO redeem_codes (id, code_hash, plan_id, duration_days, batch_id, status, created_by, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)",
            (code_id, sha256_hex(code), plan_id, duration_days, batch, created_by, now, expires_at),
        )
        out.append({"id": code_id, "code": code})
    write_audit(
        conn,
        action="redeem_code.create",
        actor_user_id=created_by,
        target_type="redeem_batch",
        target_id=batch,
        detail={"plan": plan_id, "days": duration_days, "count": count, "expires_at": expires_at},
    )
    return out


def redeem_code(conn: sqlite3.Connection, code: str, user_id: str) -> dict:
    """原子消费;成功返回授予的订阅信息。失败抛 RedeemError(invalid/expired/used)。"""
    h = sha256_hex((code or "").strip().upper())
    row = conn.execute("SELECT * FROM redeem_codes WHERE code_hash=?", (h,)).fetchone()
    if row is None:
        raise RedeemError("invalid")
    now = utc_now_iso()
    if row["expires_at"] is not None and row["expires_at"] <= now:
        conn.execute(
            "UPDATE redeem_codes SET status='expired' WHERE id=? AND status='active'", (row["id"],)
        )
        raise RedeemError("expired")
    cur = conn.execute(
        "UPDATE redeem_codes SET status='used', used_by=?, used_at=? WHERE id=? AND status='active'",
        (user_id, now, row["id"]),
    )
    if cur.rowcount != 1:
        raise RedeemError("used")
    grant = grant_subscription(
        conn,
        user_id=user_id,
        plan_id=row["plan_id"],
        duration_days=row["duration_days"],
        granted_by=None,
        source="redeem_code",
        source_ref=row["id"],
    )
    write_audit(
        conn,
        action="redeem_code.use",
        actor_user_id=user_id,
        actor_type="user",
        target_type="redeem_code",
        target_id=row["id"],
        detail={"plan": row["plan_id"], "days": row["duration_days"]},
    )
    return grant
