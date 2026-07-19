"""Plan/Subscription/Entitlement 解析(CLAUDE.md §8)。

- Role 只表达身份(user/analyst/admin),不承载付费能力;
- 付费能力 = 有效订阅(status=active 且未到期)中 rank 最高的 plan 的 entitlement 集合;
- 匿名与无订阅用户 = free plan 的 entitlement 集合。
"""

import sqlite3

FREE_PLAN_ID = "free"


def effective_plan_id(conn: sqlite3.Connection, user_id: str | None, now_iso: str) -> str:
    if user_id is None:
        return FREE_PLAN_ID
    row = conn.execute(
        """SELECT s.plan_id
           FROM subscriptions s JOIN plans p ON p.id = s.plan_id
           WHERE s.user_id=? AND s.status='active' AND s.starts_at <= ? AND s.ends_at > ?
           ORDER BY p.rank DESC, s.ends_at DESC LIMIT 1""",
        (user_id, now_iso, now_iso),
    ).fetchone()
    return row[0] if row else FREE_PLAN_ID


def plan_entitlements(conn: sqlite3.Connection, plan_id: str) -> frozenset[str]:
    rows = conn.execute(
        "SELECT entitlement FROM plan_entitlements WHERE plan_id=?", (plan_id,)
    ).fetchall()
    return frozenset(r[0] for r in rows)


def resolve_entitlements(conn: sqlite3.Connection, user_id: str | None, now_iso: str) -> tuple[str, frozenset[str]]:
    plan_id = effective_plan_id(conn, user_id, now_iso)
    return plan_id, plan_entitlements(conn, plan_id)
