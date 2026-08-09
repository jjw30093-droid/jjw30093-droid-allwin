"""瑞典超(Allsvenskan)Playwright 验收专用 setup(不是新功能,只补齐上一轮遗漏的
浏览器自动化验收)。

在真实 FotMob/NowGoal ingest 产出的隔离实验库副本上跑,只做两件事:
  1. 创建管理员账号(供 Studio 选中瑞典超比赛验收);
  2. 预置一个 pro 会员身份,绑定到 MockWechatProvider 固定返回的
     mock-openid-user-1(provider=wechat_oa, provider_app_id=mock-app)——
     与 frontend/e2e/auth.spec.ts 复用同一套 mock 身份约定,不额外发明机制。

绝不碰真实 data/*.db;调用方必须显式设置 ALLWIN_DATA_DIR 指向隔离实验库副本。

用法:
    ALLWIN_DATA_DIR=<isolated copy> python -m tests.e2e.seed_allsvenskan_pw
"""

from backend.cli.create_admin import create_admin
from backend.commands.subscriptions import grant_subscription
from backend.db.connections import connect_rw
from backend.db.util import new_uuid, utc_now_iso


def run() -> None:
    conn = connect_rw("platform")
    try:
        create_admin(conn, "pw-admin", "pw-admin-pass-12345", reset=True)

        existing = conn.execute(
            "SELECT user_id FROM auth_identities"
            " WHERE provider='wechat_oa' AND provider_app_id='mock-app' AND provider_subject='mock-openid-user-1'"
        ).fetchone()
        if existing:
            user_id = existing["user_id"]
        else:
            user_id = new_uuid()
            now = utc_now_iso()
            conn.execute(
                "INSERT INTO users (id, display_name, role, status, created_at, updated_at)"
                " VALUES (?, ?, 'user', 'active', ?, ?)",
                (user_id, f"pw-mock-{user_id[:8]}", now, now),
            )
            conn.execute(
                "INSERT INTO auth_identities (user_id, provider, provider_app_id, provider_subject, created_at, last_used_at)"
                " VALUES (?, 'wechat_oa', 'mock-app', 'mock-openid-user-1', ?, ?)",
                (user_id, now, now),
            )
        grant_subscription(conn, user_id, "pro", 30, granted_by=None, source="admin_grant")
        conn.commit()
        print(f"seed_allsvenskan_pw: admin ok, pro user_id={user_id}")
    finally:
        conn.close()


if __name__ == "__main__":
    run()
