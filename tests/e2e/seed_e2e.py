"""E2E 种子:构建 data/e2e 独立测试数据目录(绝不触碰真实 data/platform.db / odds.db)。

- allwin.db 用符号链接接入(E2E 期间 API 对 core 只读);
- platform/odds 每次重建并迁移;
- 种子内容:
  * admin:密码登录账号 e2e-admin / e2e-password-123;
  * pro 会员:预绑定 mock 微信身份(Mock 登录 code=mock-user-1 → openid mock-openid-user-1);
  * 一条已发布+锁定的 26/27 正式预测(生成时间早于开球,口径合法);
  * 一个未使用 pro 兑换码,明文写入 data/e2e/redeem_code.txt 供 E2E 用例读取。

kickoff provenance(本轮修复,不放宽任何生产门禁 —— CLAUDE.md §6.2.1):
`_require_precise_kickoff`/`normalize_exact_kickoff`/publish_snapshot/lock_snapshot/
正式样本资格查询/migration 触发器均未改动一行。E2E 是明确的合成测试环境,允许用
显式测试来源 kickoff_source="e2e_fixture:synthetic_exact" 诚实标注:这不是真实
FotMob/NowGoal 观测到的精确开球时间,只是测试环境为了让 publish/lock 合法通过而
构造的、来源可追溯的合成时间,同时仍然满足 kickoff_precision="exact"+显式时区+
可严格解析+来源非空这四个门禁条件(见 backend/db/util.normalize_exact_kickoff)。
这个 e2e_fixture 前缀不会、也不允许出现在任何生产自动识别或绕过逻辑里——它只是
一个普通字符串,判断资格的仍然是 normalize_exact_kickoff 的四项硬性条件,不是对
"e2e_fixture:" 前缀的特殊放行。

用法:.venv/bin/python -m tests.e2e.seed_e2e
"""

import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
E2E_DIR = ROOT / "data" / "e2e"

ADMIN_USER = "e2e-admin"
ADMIN_PASSWORD = "e2e-password-123"
MOCK_MEMBER_OPENID = "mock-openid-user-1"   # MockWechatProvider 固定 code=mock-user-1

# CLAUDE.md §6.2.1 允许的合法 kickoff_precision 之一;仅在 E2E 合成场景使用,
# 诚实标注来源不是真实数据源(见模块顶部说明)。
E2E_KICKOFF_SOURCE = "e2e_fixture:synthetic_exact"


def _pick_future_epl_match(core_conn):
    """挑一场真实 NotStarted 且 Date 严格晚于"今天"的 26/27 英超比赛。

    只看 status='NotStarted' 不够——比赛可能因为核心库数据陈旧,Date 已经过去但
    status 字段还没更新(源头脏数据);这里额外用 Date > 今天 过滤,防止种子
    悄悄选中一场已经"过期"的比赛,导致构造出的 kickoff 是过去时间从而被
    _require_pre_kickoff 拒绝(post_kickoff)。核心库没有任何满足条件的比赛时,
    必须让调用方明确失败,不能静默改用 legacy_unverified 或放宽判断。
    """
    today = date.today().isoformat()
    return core_conn.execute(
        "SELECT Match_ID, Date FROM dim_match"
        " WHERE League_ID=47 AND status='NotStarted' AND Date > ?"
        " ORDER BY Date LIMIT 1",
        (today,),
    ).fetchone()


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

        # 选一场 26/27 未开赛、且真实晚于今天的英超比赛,登记→发布→锁定一条正式
        # 预测(合成 exact kickoff,provenance 明确标注为 e2e_fixture,不冒充真实源)。
        match = _pick_future_epl_match(core)
        if match is None:
            print(
                "核心库没有 Date 严格晚于今天且 status='NotStarted' 的英超比赛,"
                "无法构造合法的 E2E 正式预测种子(不会静默改用 legacy_unverified"
                "或放宽 kickoff 门禁)",
                file=sys.stderr,
            )
            return 1
        # 15:00 UTC 只是一个合理的比赛日内时刻;真正决定是否满足"精确开球"资格的
        # 是 kickoff_precision="exact" + kickoff_source 非空 + 显式时区,不是这串
        # 数字本身(见 backend.db.util.normalize_exact_kickoff)。
        kickoff_utc = f"{match['Date']}T15:00:00Z"
        generated_at = pred.utc_now_iso()
        with tx(conn):
            mv = pred.get_or_create_model_version(
                conn, "dc-baseline-1.M.2", "dixon-coles+isotonic",
            )
            snap_id = pred.register_snapshot(
                conn,
                match_id=match["Match_ID"],
                kickoff_at_utc=kickoff_utc,
                kickoff_precision="exact",
                kickoff_source=E2E_KICKOFF_SOURCE,
                model_version_id=mv,
                home_win=0.48, draw=0.27, away_win=0.25,
                confidence="normal",
                status="draft",
                generated_at=generated_at,
                input_cutoff_at=generated_at,
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
            f"match_id={match['Match_ID']}\n"
            f"snapshot_id={snap_id}\n"
            f"kickoff_at_utc={kickoff_utc}\n"
            f"kickoff_source={E2E_KICKOFF_SOURCE}\n",
            encoding="utf-8",
        )
        print(f"e2e seed ok: match={match['Match_ID']} snapshot={snap_id} member={member_id}")
        return 0
    finally:
        conn.close()
        core.close()


if __name__ == "__main__":
    sys.exit(main())
