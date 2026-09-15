"""创建/重置账号(密码只经 getpass 或环境变量,不进命令行历史)。

用法:
  python -m backend.cli.create_admin --username admin                    # 建管理员
  python -m backend.cli.create_admin --username someone --role user      # 建普通用户
  python -m backend.cli.create_admin --username admin --reset-password   # 只重置密码
  ALLWIN_ADMIN_PASSWORD=... python -m backend.cli.create_admin --username admin   # 非交互(CI)

密码使用 Argon2(argon2-cffi 默认参数)。

⚠ 2026-09-16 前这个 CLI 只能建 admin(role 硬编码两处),而且 --reset-password
一个已存在的**普通用户**会把他静默提权成管理员。两处都已修:建号角色由
--role 决定(默认仍是 admin,向后兼容),重置密码只改密码、不动角色。
文件名保留 create_admin 不改,避免动 9 个后端测试与既有运维脚本的 import。
"""

import argparse
import getpass
import os
import sys

from argon2 import PasswordHasher

from backend.db.connections import connect_rw, tx
from backend.db.util import new_uuid, utc_now_iso


def _read_password() -> str:
    env_pw = os.environ.get("ALLWIN_ADMIN_PASSWORD")
    if env_pw:
        return env_pw
    pw = getpass.getpass("管理员密码(输入不回显): ")
    pw2 = getpass.getpass("再输一次确认: ")
    if pw != pw2:
        print("两次输入不一致,退出。", file=sys.stderr)
        sys.exit(1)
    return pw


VALID_ROLES = ("user", "analyst", "admin")


def create_admin(
    conn,
    username: str,
    password: str,
    reset: bool = False,
    *,
    role: str = "admin",
) -> str:
    """建号/重置密码。

    `role` 是**关键字专用**参数(前面那个 `*`):本函数被 9 个后端测试文件按
    位置参数调用,新增位置参数会全部炸掉。默认仍是 admin,保持向后兼容。
    """
    if len(password) < 8:
        raise ValueError("密码至少 8 位")
    if role not in VALID_ROLES:
        raise ValueError(f"role 必须是 {VALID_ROLES} 之一,收到 {role!r}")
    ph = PasswordHasher()
    password_hash = ph.hash(password)
    now = utc_now_iso()

    row = conn.execute(
        "SELECT user_id FROM auth_identities WHERE provider='password' AND provider_subject=?",
        (username,),
    ).fetchone()
    if row:
        if not reset:
            raise ValueError(f"用户名 {username!r} 已存在;要重置密码请加 --reset-password")
        user_id = row[0]
        with tx(conn):
            # 2026-09-16 修复真实 bug:这里此前一并写死 role='admin',
            # 也就是给任意一个**已存在的普通用户**重置密码,会把他静默提权成
            # 管理员。重置密码就只该改密码,不该动权限。
            conn.execute(
                "UPDATE users SET password_hash=?, updated_at=? WHERE id=?",
                (password_hash, now, user_id),
            )
        return user_id

    user_id = new_uuid()
    with tx(conn):
        conn.execute(
            "INSERT INTO users (id, display_name, role, password_hash, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, 'active', ?, ?)",
            (user_id, username, role, password_hash, now, now),
        )
        conn.execute(
            "INSERT INTO auth_identities (user_id, provider, provider_app_id, provider_subject, created_at)"
            " VALUES (?, 'password', '', ?, ?)",
            (user_id, username, now),
        )
        conn.execute(
            "INSERT INTO audit_logs (actor_user_id, actor_type, action, target_type, target_id, created_at)"
            " VALUES (?, 'system', ?, 'user', ?, ?)",
            (user_id, f"{role}.create", user_id, now),
        )
    return user_id


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="创建/重置 allwin 账号")
    ap.add_argument("--username", required=True)
    ap.add_argument("--reset-password", action="store_true")
    ap.add_argument(
        "--role",
        choices=VALID_ROLES,
        default="admin",
        help="新建账号的角色;默认 admin(保持与历史行为一致)。"
        "给普通用户开号请显式传 --role user。重置密码时本参数不生效,"
        "重置只改密码、不动角色。",
    )
    args = ap.parse_args(argv)

    password = _read_password()
    conn = connect_rw("platform")
    try:
        user_id = create_admin(
            conn,
            args.username,
            password,
            reset=args.reset_password,
            role=args.role,
        )
    finally:
        conn.close()
    action = "reset" if args.reset_password else f"created role={args.role}"
    print(f"OK {action} username={args.username} user_id={user_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
