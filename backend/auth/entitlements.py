"""Plan/Subscription/Entitlement 解析(CLAUDE.md §8,2026-08 三段可见性修订)。

- Role 只表达身份(user/analyst/admin),不承载付费能力;
- 匿名 = free plan 的 entitlement 集合(英超/竞彩联赛 + 最高一项概率 + 延迟摘要);
- 任何已登录用户 = 'member' 基线(全部足球数据)∪ 有效订阅 plan 的权益——
  登录本身即解锁足球数据,订阅只为付费板块(如独家推荐)追加权益;
- 有效订阅 = status=active 且未到期,取 rank 最高者作为展示 plan_id。
"""

import sqlite3

FREE_PLAN_ID = "free"
MEMBER_PLAN_ID = "member"


def effective_plan_id(conn: sqlite3.Connection, user_id: str | None, now_iso: str) -> str:
    """展示用 plan_id:匿名 free;已登录默认 member,有更高订阅时显示订阅 plan。

    只考虑 is_active=1 的 plan(已下架的 pro/premium 存量订阅不再作为展示身份,
    其权益集本就是 member 基线的子集,不损失内容)。
    """
    if user_id is None:
        return FREE_PLAN_ID
    row = conn.execute(
        """SELECT s.plan_id
           FROM subscriptions s JOIN plans p ON p.id = s.plan_id
           WHERE s.user_id=? AND s.status='active' AND s.starts_at <= ? AND s.ends_at > ?
             AND p.is_active = 1
           ORDER BY p.rank DESC, s.ends_at DESC LIMIT 1""",
        (user_id, now_iso, now_iso),
    ).fetchone()
    return row[0] if row else MEMBER_PLAN_ID


def plan_entitlements(conn: sqlite3.Connection, plan_id: str) -> frozenset[str]:
    rows = conn.execute(
        "SELECT entitlement FROM plan_entitlements WHERE plan_id=?", (plan_id,)
    ).fetchall()
    return frozenset(r[0] for r in rows)


def resolve_entitlements(conn: sqlite3.Connection, user_id: str | None, now_iso: str) -> tuple[str, frozenset[str]]:
    plan_id = effective_plan_id(conn, user_id, now_iso)
    ents = plan_entitlements(conn, plan_id)
    if user_id is not None and plan_id != MEMBER_PLAN_ID:
        # 已登录基线恒并入:订阅(付费板块)权益是追加,不能反而少于普通登录用户
        ents = ents | plan_entitlements(conn, MEMBER_PLAN_ID)
    return plan_id, ents
