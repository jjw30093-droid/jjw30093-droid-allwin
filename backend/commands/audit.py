"""AuditLog 写入(所有 Admin/系统敏感操作必须留痕,CLAUDE.md §8.3)。"""

import json
import sqlite3

from backend.db.util import utc_now_iso


def write_audit(
    conn: sqlite3.Connection,
    action: str,
    actor_user_id: str | None,
    actor_type: str = "admin",
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_logs (actor_user_id, actor_type, action, target_type, target_id, detail_json, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            actor_user_id,
            actor_type,
            action,
            target_type,
            str(target_id) if target_id is not None else None,
            json.dumps(detail or {}, ensure_ascii=False),
            utc_now_iso(),
        ),
    )
