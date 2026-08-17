"""每日精选按"用户 + 单条 slip"授权(2026-08-16,产品权限口径修正,经用户
批准)——取代旧的全局 reco:daily 布尔权益(持有 daily_picks 订阅即可看近
30 天全部推荐单)。

按 slip_id(不是 match_id)授权:reco_slips 可能是单场也可能是串关(parlay,
combo_type='parlay',跨多场比赛),内容作为一个整体单元发布和展示,slip 是
这个代码库里最自然的内容寻址单位——用户对一个 slip 的授权自动覆盖它包含的
全部腿(不管单场还是跨场串关),这仍然满足"拿到 A 场权限不能看到 B 场"的
要求:A 场和 B 场如果是两个不同的 reco_slip(两张单场单),天然是两条独立
的授权记录。

全部写操作复用 backend/commands/audit.py::write_audit,不重新实现审计;
数据库层面还有 backend/migrations/platform/0015_reco_access_grants.sql 的
partial unique index(user_id, slip_id) WHERE status='active' 作为幂等的
最后防线。

读取路径(get_grant/list_user_grants/list_grants)统一 LEFT JOIN reco_slips
带出 slip_title/slip_date:授权记录本身已经把 slip_id 暴露给"这条记录所属的
那个用户"(自己的授权记录,含历史撤销),只给一个不透明 UUID 对"每日精选权限
查询"这个允许登录的账户功能(CLAUDE.md §8.1)和 admin 授权面来说不够具体——
标题/日期是识别"是哪一场"所需的最小信息,不是被按场授权保护的正文本身
(赛前腿/赔率/理由仍然只在 RecoSlipDTO 完整投影里,只对当前 active 授权可见,
这里不下发)。
"""

import sqlite3

from backend.db.util import new_uuid, utc_now_iso

from .audit import write_audit


class RecoAccessError(ValueError):
    pass


_GRANT_COLUMNS = (
    "g.id AS id, g.user_id AS user_id, g.slip_id AS slip_id, g.status AS status,"
    " g.granted_at AS granted_at, g.granted_by AS granted_by,"
    " g.revoked_at AS revoked_at, g.revoked_by AS revoked_by, g.note AS note,"
    " g.created_at AS created_at, g.updated_at AS updated_at,"
    " rs.title AS slip_title, rs.slip_date AS slip_date"
)
_GRANT_FROM = "FROM reco_access_grants g LEFT JOIN reco_slips rs ON rs.id = g.slip_id"


def grant_access(
    conn: sqlite3.Connection,
    user_id: str,
    slip_id: str,
    *,
    actor: str,
    note: str | None = None,
) -> str:
    """幂等授权:该用户对该 slip 已有 active 授权时直接返回既有 grant id,
    不重复插入、不重复写审计(重复调用不应制造审计噪音,也不应该在数据库
    里堆出好几条 active 行)。"""
    user = conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
    if user is None:
        raise RecoAccessError(f"用户不存在: {user_id}")
    slip = conn.execute("SELECT id FROM reco_slips WHERE id=?", (slip_id,)).fetchone()
    if slip is None:
        raise RecoAccessError(f"推荐单不存在: {slip_id}")

    existing = conn.execute(
        "SELECT id FROM reco_access_grants WHERE user_id=? AND slip_id=? AND status='active'",
        (user_id, slip_id),
    ).fetchone()
    if existing is not None:
        return existing["id"]

    now = utc_now_iso()
    grant_id = new_uuid()
    conn.execute(
        "INSERT INTO reco_access_grants"
        " (id, user_id, slip_id, status, granted_at, granted_by, note, created_at, updated_at)"
        " VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?)",
        (grant_id, user_id, slip_id, now, actor, note, now, now),
    )
    write_audit(
        conn, "reco_access.grant", actor,
        target_type="reco_access_grant", target_id=grant_id,
        detail={"user_id": user_id, "slip_id": slip_id, "note": note},
    )
    return grant_id


def revoke_access(
    conn: sqlite3.Connection,
    grant_id: str,
    *,
    actor: str,
    reason: str | None = None,
) -> None:
    """把 active 记录标 revoked;非 active(不存在/已撤销)一律拒绝,不允许
    对同一条记录重复撤销(留痕纪律:撤销是一次性状态迁移,不是幂等 no-op)。"""
    row = conn.execute("SELECT * FROM reco_access_grants WHERE id=?", (grant_id,)).fetchone()
    if row is None:
        raise RecoAccessError(f"授权记录不存在: {grant_id}")
    if row["status"] != "active":
        raise RecoAccessError(f"授权当前状态为 {row['status']},不是 active,无法撤销")

    now = utc_now_iso()
    conn.execute(
        "UPDATE reco_access_grants SET status='revoked', revoked_at=?, revoked_by=?,"
        " updated_at=? WHERE id=?",
        (now, actor, now, grant_id),
    )
    write_audit(
        conn, "reco_access.revoke", actor,
        target_type="reco_access_grant", target_id=grant_id,
        detail={"user_id": row["user_id"], "slip_id": row["slip_id"], "reason": reason},
    )


def has_access(conn: sqlite3.Connection, user_id: str, slip_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM reco_access_grants WHERE user_id=? AND slip_id=? AND status='active'",
        (user_id, slip_id),
    ).fetchone()
    return row is not None


def get_grant(conn: sqlite3.Connection, grant_id: str) -> dict | None:
    row = conn.execute(
        f"SELECT {_GRANT_COLUMNS} {_GRANT_FROM} WHERE g.id=?", (grant_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def list_user_grants(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    """该用户当前全部授权记录(含历史撤销,供"每日精选权限查询"这个允许
    登录的个人功能使用——CLAUDE.md §8.1;只含该用户自己的记录,不泄漏其他
    用户的任何信息)。"""
    rows = conn.execute(
        f"SELECT {_GRANT_COLUMNS} {_GRANT_FROM} WHERE g.user_id=? ORDER BY g.created_at DESC",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_grants(
    conn: sqlite3.Connection,
    *,
    user_id: str = "",
    slip_id: str = "",
    status: str = "",
    limit: int = 100,
    offset: int = 0,
) -> tuple[int, list[dict]]:
    """admin 后台展示用:可选 user_id/slip_id/status 筛选 + 分页,不传即不
    筛选。total 是筛选后的计数,不是全库总数(与 admin_slips/list_codes 同
    一惯例)。"""
    where = []
    params: list = []
    if user_id:
        where.append("g.user_id = ?")
        params.append(user_id)
    if slip_id:
        where.append("g.slip_id = ?")
        params.append(slip_id)
    if status:
        where.append("g.status = ?")
        params.append(status)
    where_sql = f" WHERE {' AND '.join(where)}" if where else ""

    total = conn.execute(
        f"SELECT COUNT(*) {_GRANT_FROM}{where_sql}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT {_GRANT_COLUMNS} {_GRANT_FROM}{where_sql}"
        " ORDER BY g.created_at DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return total, [dict(r) for r in rows]
