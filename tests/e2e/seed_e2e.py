"""E2E 种子:构建 data/e2e 独立测试数据目录(绝不触碰真实 data/platform.db / odds.db)。

- allwin.db 用符号链接接入(E2E 期间 API 对 core 只读);
- platform/odds 每次重建并迁移;
- 种子内容:
  * admin:密码登录账号 e2e-admin / e2e-password-123;
  * pro 会员:预绑定 mock 微信身份(Mock 登录 code=mock-user-1 → openid mock-openid-user-1);
  * 一条已发布+锁定的 26/27 正式预测(生成时间早于开球,口径合法);
  * 一个未使用 pro 兑换码,明文写入 data/e2e/redeem_code.txt 供 E2E 用例读取。

用法:.venv/bin/python -m tests.e2e.seed_e2e
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
E2E_DIR = ROOT / "data" / "e2e"

ADMIN_USER = "e2e-admin"
ADMIN_PASSWORD = "e2e-password-123"
MOCK_MEMBER_OPENID = "mock-openid-user-1"   # MockWechatProvider 固定 code=mock-user-1


def main() -> int:
    E2E_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("platform.db", "odds.db"):
        for suffix in ("", "-wal", "-shm"):
            p = E2E_DIR / f"{name}{suffix}"
            if p.exists():
                p.unlink()
    link = E2E_DIR / "allwin.db"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(ROOT / "data" / "allwin.db")

    os.environ["ALLWIN_DATA_DIR"] = str(E2E_DIR)

    from backend.db import migrate
    migrate.apply_all("platform", quiet=True)
    migrate.apply_all("odds", quiet=True)

    from backend.auth import service
    from backend.cli.create_admin import create_admin
    from backend.commands import predictions as pred
    from backend.commands.redeem import create_redeem_codes
    from backend.commands.subscriptions import grant_subscription
    from backend.db.connections import connect_ro, connect_rw, tx

    conn = connect_rw("platform")
    core = connect_ro("core")
    try:
        admin_id = create_admin(conn, ADMIN_USER, ADMIN_PASSWORD, reset=True)

        with tx(conn):
            member_id = service.get_or_create_user_by_identity(
                conn,
                provider="wechat_oa",
                provider_app_id="mock-app",
                provider_subject=MOCK_MEMBER_OPENID,
                display_name="E2E会员",
            )
            grant_subscription(
                conn, user_id=member_id, plan_id="pro", duration_days=30,
                granted_by=admin_id, notes="e2e seed",
            )

        # 选一场 26/27 未开赛比赛,登记→发布→锁定一条正式预测(生成时间早于开球)
        match = core.execute(
            "SELECT Match_ID, Date FROM dim_match"
            " WHERE League_ID=47 AND status='NotStarted' ORDER BY Date LIMIT 1"
        ).fetchone()
        if match is None:
            print("没有 NotStarted 比赛可种子", file=sys.stderr)
            return 1
        kickoff_utc = f"{match['Date']}T12:00:00Z" if len(match["Date"]) == 10 else match["Date"]
        with tx(conn):
            mv = pred.get_or_create_model_version(
                conn, "dc-baseline-1.M.2", "dixon-coles+isotonic",
            )
            snap_id = pred.register_snapshot(
                conn,
                match_id=match["Match_ID"],
                kickoff_at_utc=kickoff_utc,
                model_version_id=mv,
                home_win=0.48, draw=0.27, away_win=0.25,
                confidence="normal",
                status="draft",
            )
        with tx(conn):
            pred.publish_snapshot(conn, snap_id, actor=admin_id)
        with tx(conn):
            pred.lock_snapshot(conn, snap_id, actor=admin_id)

        with tx(conn):
            codes = create_redeem_codes(
                conn, plan_id="pro", duration_days=30, count=1, created_by=admin_id,
                batch_id="e2e",
            )
        (E2E_DIR / "redeem_code.txt").write_text(codes[0]["code"], encoding="utf-8")
        (E2E_DIR / "seed_info.txt").write_text(
            f"match_id={match['Match_ID']}\nsnapshot_id={snap_id}\n", encoding="utf-8"
        )
        print(f"e2e seed ok: match={match['Match_ID']} snapshot={snap_id} member={member_id}")
        return 0
    finally:
        conn.close()
        core.close()


if __name__ == "__main__":
    sys.exit(main())
