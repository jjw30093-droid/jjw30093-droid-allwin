"""认证核心逻辑(纯函数 + 显式连接,便于测试)。

安全约定(CLAUDE.md §7):
- 会话 token ≥256bit 随机,DB 只存 SHA-256;
- oauth state / 设备登录 secret 同样只存 hash,一次性消费;
- 所有一次性消费用 UPDATE ... WHERE <未消费条件> 的 rowcount 判定,天然防重放/并发双花。
"""

import secrets
import sqlite3
from datetime import timedelta

from backend.db.util import new_token, new_uuid, sha256_hex, utc_now, utc_now_iso

SESSION_TOKEN_BYTES = 32   # 256 bit
DEVICE_SECRET_BYTES = 32
STATE_BYTES = 32
LOGIN_CODE_DIGITS = 4
SHORT_CODE_BYTES = 3       # 6 位大写十六进制用户编号


class LoginCodeExhausted(RuntimeError):
    """同一时刻等待中的登录请求把 4 位码几乎占满(正常流量下不可能发生)。"""


def _iso(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── 用户与身份 ─────────────────────────────────────────────

def get_or_create_user_by_identity(
    conn: sqlite3.Connection,
    provider: str,
    provider_app_id: str,
    provider_subject: str,
    union_id: str | None = None,
    display_name: str | None = None,
) -> str:
    """按 (provider, app_id, subject) 找用户;没有则创建。返回内部 user_id(UUID)。"""
    now = utc_now_iso()
    row = conn.execute(
        "SELECT user_id FROM auth_identities"
        " WHERE provider=? AND provider_app_id=? AND provider_subject=?",
        (provider, provider_app_id, provider_subject),
    ).fetchone()
    if row:
        user_id = row["user_id"] if isinstance(row, sqlite3.Row) else row[0]
        conn.execute(
            "UPDATE auth_identities SET last_used_at=?, union_id=COALESCE(?, union_id)"
            " WHERE provider=? AND provider_app_id=? AND provider_subject=?",
            (now, union_id, provider, provider_app_id, provider_subject),
        )
        return user_id

    user_id = new_uuid()
    short_code = new_short_code(conn)
    # 未认证公众号拿不到微信昵称/头像(微信 2021-12-27 起不再输出);默认昵称带用户编号,
    # 用户首次登录可改,后台按编号认人
    name = display_name or f"球迷 {short_code}"
    conn.execute(
        "INSERT INTO users (id, display_name, role, status, created_at, updated_at, short_code)"
        " VALUES (?, ?, 'user', 'active', ?, ?, ?)",
        (user_id, name, now, now, short_code),
    )
    conn.execute(
        "INSERT INTO auth_identities"
        " (user_id, provider, provider_app_id, provider_subject, union_id, created_at, last_used_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, provider, provider_app_id, provider_subject, union_id, now, now),
    )
    return user_id


def new_short_code(conn: sqlite3.Connection) -> str:
    """6 位大写十六进制用户编号,避开已有编号(唯一索引 uq_users_short_code 兜底)。"""
    for _ in range(50):
        code = secrets.token_hex(SHORT_CODE_BYTES).upper()
        if conn.execute("SELECT 1 FROM users WHERE short_code=?", (code,)).fetchone() is None:
            return code
    raise RuntimeError("用户编号生成失败")


NICKNAME_MIN = 2
NICKNAME_MAX = 16


def normalize_nickname(raw: str) -> str | None:
    """昵称:NFKC 规范化、去首尾空白、压缩内部连续空白;2–16 个字;不允许控制字符。
    不合法返回 None。昵称不是身份凭证,允许重名(后台以用户编号为准)。"""
    import unicodedata

    s = " ".join(unicodedata.normalize("NFKC", raw or "").split())
    if not (NICKNAME_MIN <= len(s) <= NICKNAME_MAX):
        return None
    if any(unicodedata.category(ch).startswith("C") for ch in s):
        return None
    return s


def set_profile(conn: sqlite3.Connection, user_id: str, display_name: str | None) -> None:
    """首次登录的昵称框:填了就改昵称,跳过就保留默认昵称;两种都记为"已设置",不再弹框。"""
    now = utc_now_iso()
    if display_name is None:
        conn.execute("UPDATE users SET nickname_set=1, updated_at=? WHERE id=?", (now, user_id))
    else:
        conn.execute(
            "UPDATE users SET display_name=?, nickname_set=1, updated_at=? WHERE id=?",
            (display_name, now, user_id),
        )


# ── 网站会话(opaque token) ────────────────────────────────

def create_session(
    conn: sqlite3.Connection,
    user_id: str,
    ttl_days: int,
    user_agent: str | None = None,
    ip_hash: str | None = None,
) -> dict:
    """创建会话,返回原始 token/csrf(只此一次,之后只有 hash)。"""
    token = new_token(SESSION_TOKEN_BYTES)
    csrf = new_token(SESSION_TOKEN_BYTES)
    session_id = new_uuid()
    now = utc_now()
    expires = now + timedelta(days=ttl_days)
    conn.execute(
        "INSERT INTO auth_sessions"
        " (id, user_id, token_hash, csrf_token_hash, created_at, expires_at, last_seen_at, user_agent, ip_hash)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, user_id, sha256_hex(token), sha256_hex(csrf),
         _iso(now), _iso(expires), _iso(now), (user_agent or "")[:300], ip_hash),
    )
    conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (_iso(now), user_id))
    return {
        "session_id": session_id,
        "token": token,
        "csrf_token": csrf,
        "expires_at": _iso(expires),
    }


def get_session_by_token(conn: sqlite3.Connection, token: str):
    """有效会话(未过期未撤销)→ 行;否则 None。"""
    if not token:
        return None
    row = conn.execute(
        "SELECT s.*, u.role AS user_role, u.display_name, u.status AS user_status,"
        " u.short_code AS user_short_code, u.nickname_set AS user_nickname_set"
        " FROM auth_sessions s JOIN users u ON u.id = s.user_id"
        " WHERE s.token_hash=?",
        (sha256_hex(token),),
    ).fetchone()
    if row is None:
        return None
    now = utc_now_iso()
    if row["revoked_at"] is not None or row["expires_at"] <= now:
        return None
    if row["user_status"] != "active":
        return None
    return row


def verify_csrf(session_row, csrf_token: str | None) -> bool:
    if not csrf_token:
        return False
    return session_row["csrf_token_hash"] == sha256_hex(csrf_token)


def revoke_session(conn: sqlite3.Connection, session_id: str) -> bool:
    cur = conn.execute(
        "UPDATE auth_sessions SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
        (utc_now_iso(), session_id),
    )
    return cur.rowcount == 1


def revoke_all_sessions(conn: sqlite3.Connection, user_id: str) -> int:
    cur = conn.execute(
        "UPDATE auth_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
        (utc_now_iso(), user_id),
    )
    return cur.rowcount


# ── OAuth state(一次性) ──────────────────────────────────

def create_oauth_state(
    conn: sqlite3.Connection,
    kind: str,
    next_path: str,
    ttl_seconds: int,
    device_request_id: str | None = None,
) -> str:
    state = new_token(STATE_BYTES)
    now = utc_now()
    conn.execute(
        "INSERT INTO oauth_states (state_hash, kind, next_path, device_request_id, created_at, expires_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (sha256_hex(state), kind, next_path, device_request_id,
         _iso(now), _iso(now + timedelta(seconds=ttl_seconds))),
    )
    return state


def consume_oauth_state(conn: sqlite3.Connection, state: str):
    """原子一次性消费:重放/过期返回 None。"""
    if not state:
        return None
    now = utc_now_iso()
    h = sha256_hex(state)
    cur = conn.execute(
        "UPDATE oauth_states SET used_at=? WHERE state_hash=? AND used_at IS NULL AND expires_at > ?",
        (now, h, now),
    )
    if cur.rowcount != 1:
        return None
    return conn.execute("SELECT * FROM oauth_states WHERE state_hash=?", (h,)).fetchone()


def is_safe_next_path(next_path: str) -> bool:
    """`next` 只允许本站相对路径:以单个 / 开头,禁止 //、\\ 与 scheme。"""
    if not next_path or not next_path.startswith("/"):
        return False
    if next_path.startswith("//") or "\\" in next_path:
        return False
    if "://" in next_path:
        return False
    return True


# ── 公众号发码登录(device request + 4 位验证码) ────────────

def _expire_stale_pending(conn: sqlite3.Connection, now_iso: str) -> None:
    """把已过期但仍是 pending 的请求标 expired,释放它们占着的验证码
    (部分唯一索引只约束 status='pending' 的行)。"""
    conn.execute(
        "UPDATE device_login_requests SET status='expired' WHERE status='pending' AND expires_at <= ?",
        (now_iso,),
    )


def create_device_request(conn: sqlite3.Connection, ttl_seconds: int) -> dict:
    """创建登录请求:浏览器 secret(只回浏览器,DB 只存 hash)+ 4 位验证码。

    验证码用安全随机数生成,并避开当前所有等待中的码——同一时刻不会有两个相同的码,
    用户把码发给公众号时不会对错人。调用方须在写事务内调用(单写者,避免并发撞码;
    部分唯一索引 uq_device_login_code_pending 兜底)。"""
    request_id = new_uuid()
    secret = new_token(DEVICE_SECRET_BYTES)
    now = utc_now()
    now_iso = _iso(now)
    _expire_stale_pending(conn, now_iso)
    taken = {
        r[0]
        for r in conn.execute(
            "SELECT login_code FROM device_login_requests WHERE status='pending' AND login_code IS NOT NULL"
        ).fetchall()
    }
    space = 10 ** LOGIN_CODE_DIGITS
    if len(taken) >= space * 0.9:
        raise LoginCodeExhausted("等待中的登录请求过多")
    code = None
    for _ in range(200):
        candidate = f"{secrets.randbelow(space):0{LOGIN_CODE_DIGITS}d}"
        if candidate not in taken:
            code = candidate
            break
    if code is None:
        raise LoginCodeExhausted("验证码生成失败")
    expires_iso = _iso(now + timedelta(seconds=ttl_seconds))
    conn.execute(
        "INSERT INTO device_login_requests (id, secret_hash, status, created_at, expires_at, login_code)"
        " VALUES (?, ?, 'pending', ?, ?, ?)",
        (request_id, sha256_hex(secret), now_iso, expires_iso, code),
    )
    return {
        "request_id": request_id,
        "secret": secret,
        "login_code": code,
        "expires_at": expires_iso,
    }


def get_pending_request_by_code(conn: sqlite3.Connection, code: str):
    """等待中且未过期、验证码等于 code 的登录请求(至多一条:部分唯一索引保证);没有返回 None。"""
    return conn.execute(
        "SELECT * FROM device_login_requests"
        " WHERE login_code=? AND status='pending' AND expires_at > ?",
        (code, utc_now_iso()),
    ).fetchone()


def get_device_request(conn: sqlite3.Connection, request_id: str):
    return conn.execute(
        "SELECT * FROM device_login_requests WHERE id=?", (request_id,)
    ).fetchone()


def approve_device_request(conn: sqlite3.Connection, request_id: str, user_id: str) -> bool:
    now = utc_now_iso()
    cur = conn.execute(
        "UPDATE device_login_requests SET status='approved', user_id=?, approved_at=?"
        " WHERE id=? AND status='pending' AND expires_at > ?",
        (user_id, now, request_id, now),
    )
    return cur.rowcount == 1


def claim_device_request(conn: sqlite3.Connection, request_id: str, secret: str) -> tuple[str, str | None]:
    """电脑端轮询领取。返回 (status, user_id|None)。

    status ∈ pending / claimed / forbidden / expired / gone
    - 必须携带浏览器 secret;
    - approved → claimed 是原子单次转移,第二次领取得 gone。
    """
    now = utc_now_iso()
    row = conn.execute(
        "SELECT * FROM device_login_requests WHERE id=?", (request_id,)
    ).fetchone()
    if row is None:
        return ("gone", None)
    if row["secret_hash"] != sha256_hex(secret or ""):
        return ("forbidden", None)
    if row["expires_at"] <= now and row["status"] in ("pending", "approved"):
        conn.execute(
            "UPDATE device_login_requests SET status='expired' WHERE id=? AND status IN ('pending','approved')",
            (request_id,),
        )
        return ("expired", None)
    if row["status"] == "pending":
        return ("pending", None)
    if row["status"] == "approved":
        cur = conn.execute(
            "UPDATE device_login_requests SET status='claimed', claimed_at=?"
            " WHERE id=? AND status='approved'",
            (now, request_id),
        )
        if cur.rowcount == 1:
            return ("claimed", row["user_id"])
        return ("gone", None)
    return ("gone", None)
