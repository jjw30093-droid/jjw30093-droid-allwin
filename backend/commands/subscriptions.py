"""订阅授予/延期/撤销(MVP 无真实支付:管理员手动 + 兑换码,支付走 Provider Adapter 预留)。"""

import sqlite3
from datetime import datetime, timedelta, timezone

from backend.db.util import new_uuid, utc_now_iso

from .audit import write_audit


def _parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def grant_subscription(
    conn: sqlite3.Connection,
    user_id: str,
    plan_id: str,
    duration_days: int,
    granted_by: str | None,
    source: str = "admin_grant",
    source_ref: str | None = None,
    notes: str = "",
) -> dict:
    """授予/延期:同 plan 已有未到期订阅时,从其 ends_at 顺延(不浪费剩余时长)。"""
    if duration_days <= 0:
        raise ValueError("duration_days 必须为正")
    plan = conn.execute("SELECT id FROM plans WHERE id=? AND is_active=1", (plan_id,)).fetchone()
    if plan is None:
        raise ValueError(f"未知或停用的 plan: {plan_id}")
    user = conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
    if user is None:
        raise ValueError("用户不存在")

    now_iso = utc_now_iso()
    latest = conn.execute(
        "SELECT ends_at FROM subscriptions"
        " WHERE user_id=? AND plan_id=? AND status='active' AND ends_at > ?"
        " ORDER BY ends_at DESC LIMIT 1",
        (user_id, plan_id, now_iso),
    ).fetchone()
    starts = _parse_iso(latest["ends_at"]) if latest else _parse_iso(now_iso)
    ends = starts + timedelta(days=duration_days)

    sub_id = new_uuid()
    conn.execute(
        "INSERT INTO subscriptions (id, user_id, plan_id, status, starts_at, ends_at, source, source_ref, granted_by, notes, created_at)"
        " VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)",
        (sub_id, user_id, plan_id, _iso(starts), _iso(ends), source, source_ref, granted_by, notes, now_iso),
    )
    write_audit(
        conn,
        action="subscription.grant",
        actor_user_id=granted_by,
        actor_type="admin" if source == "admin_grant" else "system",
        target_type="user",
        target_id=user_id,
        detail={"plan": plan_id, "days": duration_days, "source": source,
                "source_ref": source_ref, "starts_at": _iso(starts), "ends_at": _iso(ends)},
    )
    return {"subscription_id": sub_id, "plan_id": plan_id, "starts_at": _iso(starts), "ends_at": _iso(ends)}


def revoke_subscription(conn: sqlite3.Connection, subscription_id: str, actor_user_id: str | None) -> bool:
    cur = conn.execute(
        "UPDATE subscriptions SET status='revoked' WHERE id=? AND status='active'",
        (subscription_id,),
    )
    if cur.rowcount != 1:
        return False
    write_audit(
        conn,
        action="subscription.revoke",
        actor_user_id=actor_user_id,
        target_type="subscription",
        target_id=subscription_id,
    )
    return True
