"""创建/重置管理员账号(密码只经 getpass 或环境变量,不进命令行历史)。

用法:
  python -m backend.cli.create_admin --username admin
  python -m backend.cli.create_admin --username admin --reset-password
  ALLWIN_ADMIN_PASSWORD=... python -m backend.cli.create_admin --username admin   # 非交互(CI)

密码使用 Argon2(argon2-cffi 默认参数)。
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


def create_admin(conn, username: str, password: str, reset: bool = False) -> str:
    if len(password) < 8:
        raise ValueError("密码至少 8 位")
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
            conn.execute(
                "UPDATE users SET password_hash=?, role='admin', updated_at=? WHERE id=?",
                (password_hash, now, user_id),
            )
        return user_id

    user_id = new_uuid()
    with tx(conn):
        conn.execute(
            "INSERT INTO users (id, display_name, role, password_hash, status, created_at, updated_at)"
            " VALUES (?, ?, 'admin', ?, 'active', ?, ?)",
            (user_id, username, password_hash, now, now),
        )
        conn.execute(
            "INSERT INTO auth_identities (user_id, provider, provider_app_id, provider_subject, created_at)"
            " VALUES (?, 'password', '', ?, ?)",
            (user_id, username, now),
        )
        conn.execute(
            "INSERT INTO audit_logs (actor_user_id, actor_type, action, target_type, target_id, created_at)"
            " VALUES (?, 'system', 'admin.create', 'user', ?, ?)",
            (user_id, user_id, now),
        )
    return user_id


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="创建/重置 allwin 管理员")
    ap.add_argument("--username", required=True)
    ap.add_argument("--reset-password", action="store_true")
    args = ap.parse_args(argv)

    password = _read_password()
    conn = connect_rw("platform")
    try:
        user_id = create_admin(conn, args.username, password, reset=args.reset_password)
    finally:
        conn.close()
    print(f"OK admin username={args.username} user_id={user_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
