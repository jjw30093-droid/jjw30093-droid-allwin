"""「每日精选」付费推荐板块测试(docs/design-reco-board.md,用户已确认口径)。

覆盖:四方权限矩阵、draft/未结算不泄漏、结算数学(命中/未中/走水)、
重新结算留痕、作废单列不消失、编辑 edit_count+审计、30 天窗口、
no-store 缓存、与模型 track-record 的分离。
"""

import pytest
from fastapi.testclient import TestClient

from backend.commands.subscriptions import grant_subscription
from backend.db.connections import connect_rw

from .authflow import wechat_scan_login

ORIGIN = {"Origin": "http://localhost:3000"}


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("allwin_csrf"), **ORIGIN}


def _admin_client(app, data_dir, ip, username="reco-admin"):
    from backend.cli.create_admin import create_admin

    conn = connect_rw("platform")
    create_admin(conn, username, "reco-admin-pw-1", reset=True)
    conn.close()
    c = TestClient(app)
    r = c.post("/api/v1/auth/password/login",
               json={"username": username, "password": "reco-admin-pw-1"},
               headers={"x-real-ip": ip})
    assert r.status_code == 200
    return c


def _member_client(app, ip, openid="reco-member"):
    c = TestClient(app)
    wechat_scan_login(c, openid=openid, ip=ip)
    return c


def _paid_client(app, ip, openid="reco-paid"):
    c = _member_client(app, ip, openid=openid)
    uid = c.get("/api/v1/me").json()["user"]["id"]
    conn = connect_rw("platform")
    conn.execute("BEGIN IMMEDIATE")
    grant_subscription(conn, uid, "daily_picks", 30, granted_by=None, source="admin_grant")
    conn.execute("COMMIT")
    conn.close()
    return c


def _prov_leg(match_desc, market, selection, odds, match_id, *, snapshot_ref=None,
              side=None, line=None):
    """provenance_bound 腿(2026-08-16 起发布强制要求,见
    backend/commands/reco.py::require_provenance_bound_legs):odds_format 固定用
    'decimal' 且 source_odds=odds,canonical_decimal_odds 计算结果与旧的
    legacy_manual 路径完全一致(等于 odds 本身),既满足新的发布前置校验,
    又不改变任何既有结算数学断言的期望值。match_id 是纯 platform.db 整数列
    (不跨库外键),测试可任意取值,不需要对应真实 core 比赛。"""
    return {
        "match_desc": match_desc, "market": market, "selection": selection, "odds": odds,
        "match_id": match_id, "source_odds": odds, "odds_format": "decimal",
        "snapshot_ref": snapshot_ref or f"test-snap-{match_id}-{market}-{selection}",
        "side": side, "line": line,
    }


def _create_slip(admin, slip_date="2026-08-10", title="测试二串一", legs=None, board=None):
    """board=None(默认)不下发,后端 RecoSlipCreateBody.board 自己落
    daily_pick——既有调用方不需要感知每日公推(2026-09 新增)的存在。"""
    legs = legs or [
        _prov_leg("A vs B", "1x2", "主胜", 1.9, 910101),
        _prov_leg("C vs D", "ou", "大2.5", 1.8, 910102),
    ]
    body = {"slip_date": slip_date, "title": title, "note": None, "legs": legs}
    if board is not None:
        body["board"] = board
    r = admin.post("/api/v1/admin/reco/slips", headers=_csrf(admin), json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _publish(admin, slip_id):
    assert admin.post(f"/api/v1/admin/reco/slips/{slip_id}/publish",
                      headers=_csrf(admin)).status_code == 200


def _legs_of(admin, slip_id):
    slips = admin.get("/api/v1/admin/reco/slips").json()["slips"]
    return next(s for s in slips if s["id"] == slip_id)["legs"]


def _settle(admin, slip_id, results: list[str]):
    legs = _legs_of(admin, slip_id)
    body = {"leg_results": {l["id"]: r for l, r in zip(legs, results)}}
    r = admin.post(f"/api/v1/admin/reco/slips/{slip_id}/settle",
                   headers=_csrf(admin), json=body)
    assert r.status_code == 200, r.text
    return r.json()


class TestVisibilityMatrix:
    """2026-08-16 产品权限口径修正(经用户批准,取代旧的 reco:daily 付费全局
    布尔权益):每日精选改为按"用户 + 单条 slip"授权(见 test_reco_access.py
    完整覆盖),reco/daily 列表本身只要求登录(不要求任何权益/订阅),
    reco/track-record 改为匿名可见。下面几条断言验证的正是被本次任务明确
    要求推翻的旧规则(旧规则:reco/daily 列表需要 reco:daily 权益才能拿到
    200,reco/track-record 需要登录),已替换为新规则下的正确行为。"""

    def test_anonymous_401_daily_200_track_record(self, app, data_dir):
        """[2026-08-16 替换] 旧断言:reco/track-record 匿名 401(要求登录)。
        新规则:除"每日精选"正文外全站内容免费,含匿名;reco/track-record
        是站点自身公开历史战绩(与模型公开战绩同一先例),匿名 200。
        reco/daily 列表仍然要求登录(它是"每日精选权限查询"的一种)。"""
        anon = TestClient(app)
        assert anon.get("/api/v1/reco/daily").status_code == 401
        assert anon.get("/api/v1/reco/track-record").status_code == 200

    def test_member_sees_daily_list_but_no_content_without_grant(self, app, data_dir, fresh_ip):
        """[2026-08-16 替换] 旧断言:普通登录用户访问 reco/daily 列表 403
        (reco_membership_required)。新规则:登录即可看列表(200),但未被
        admin 显式按 slip 授权的单只给"存在性 + 状态"投影
        (access_required=true),不含标题/摘要/腿/思路说明——受限的是内容,
        不是列表访问本身。完整的按场授权矩阵见 test_reco_access.py。"""
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="未授权可见性检测")
        _publish(admin, sid)

        m = _member_client(app, fresh_ip)
        assert m.get("/api/v1/reco/track-record").status_code == 200
        r = m.get("/api/v1/reco/daily")
        assert r.status_code == 200
        item = next(s for s in r.json()["slips"] if s["id"] == sid)
        assert item["access_required"] is True
        assert "title" not in item
        assert "未授权可见性检测" not in r.text

    def test_paid_sees_both(self, app, data_dir, fresh_ip):
        p = _paid_client(app, fresh_ip)
        assert p.get("/api/v1/reco/daily").status_code == 200
        assert p.get("/api/v1/reco/track-record").status_code == 200
        assert p.get("/api/v1/me").json()["plan"] == "daily_picks"

    def test_admin_role_does_not_auto_grant_reco_content(self, app, data_dir, fresh_ip):
        """[2026-08-16 替换] 旧断言:admin 访问 reco/daily 列表 403(旧规则
        把"列表访问"本身当作受权益门禁的资源)。新规则里列表只要求登录,
        Role⊥Entitlement 的不变量改为在"内容"这一层验证:admin 未被显式
        按场授权时,列表里的单同样只有存在性投影,单条正文端点同样 403
        ——admin 身份本身不自动解锁任何一张未被授权的每日精选。"""
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="admin不应自动可见")
        _publish(admin, sid)

        r = admin.get("/api/v1/reco/daily")
        assert r.status_code == 200
        item = next(s for s in r.json()["slips"] if s["id"] == sid)
        assert item["access_required"] is True
        assert "title" not in item

        assert admin.get(f"/api/v1/reco/daily/{sid}").status_code == 403

    def test_non_admin_cannot_write(self, app, data_dir, fresh_ip):
        p = _paid_client(app, fresh_ip, openid="reco-paid-w")
        r = p.post("/api/v1/admin/reco/slips", headers=_csrf(p),
                   json={"slip_date": "2026-08-10", "title": "x",
                         "legs": [{"match_desc": "a", "market": "1x2", "selection": "主胜", "odds": 1.5}]})
        assert r.status_code == 403

    def test_no_store_on_all_reco_endpoints(self, app, data_dir, fresh_ip):
        """付费内容绝不进共享缓存:成功与失败路径全部 no-store。"""
        p = _paid_client(app, fresh_ip, openid="reco-paid-ns")
        anon = TestClient(app)
        for resp in (
            p.get("/api/v1/reco/daily"),
            p.get("/api/v1/reco/track-record"),
            anon.get("/api/v1/reco/daily"),
        ):
            assert resp.headers["cache-control"] == "private, no-store"


class TestContentBoundaries:
    def test_draft_invisible_everywhere_except_admin(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin)
        p = _paid_client(app, f"{fresh_ip}-p", openid="reco-b1")
        assert all(s["id"] != sid for s in p.get("/api/v1/reco/daily").json()["slips"])
        assert any(s["id"] == sid
                   for s in admin.get("/api/v1/admin/reco/slips").json()["slips"])

    def test_unsettled_published_not_in_track_record(self, app, data_dir, fresh_ip):
        """未结算的赛前内容属付费面,不得经战绩面泄漏给普通登录用户。"""
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin)
        _publish(admin, sid)
        m = _member_client(app, f"{fresh_ip}-m", openid="reco-b2")
        tr = m.get("/api/v1/reco/track-record")
        assert all(s["id"] != sid for s in tr.json()["slips"])
        # 响应体层面也不含选项文本(受限字段物理不下发的同款纪律)
        assert "主胜" not in tr.text

    def test_settled_appears_in_track_record_for_member(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin)
        _publish(admin, sid)
        _settle(admin, sid, ["win", "win"])
        m = _member_client(app, f"{fresh_ip}-m", openid="reco-b3")
        body = m.get("/api/v1/reco/track-record").json()
        target = next(s for s in body["slips"] if s["id"] == sid)
        assert target["result"] == "win"
        assert {l["result"] for l in target["legs"]} == {"win"}

    def test_daily_window_30_days(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        old_id = _create_slip(admin, slip_date="2026-06-01", title="窗口外旧单")
        _publish(admin, old_id)
        new_id = _create_slip(admin, slip_date="2026-08-10", title="窗口内新单")
        _publish(admin, new_id)
        p = _paid_client(app, f"{fresh_ip}-p", openid="reco-b4")
        body = p.get("/api/v1/reco/daily").json()
        ids = {s["id"] for s in body["slips"]}
        assert new_id in ids and old_id not in ids
        assert body["window_days"] == 30


class TestSettleMath:
    """结算口径:任一 lose → 0;全 push → 1;其余 win = 有效赔率乘积(push 腿计 1.0)。"""

    @pytest.mark.parametrize("results,expect_result,expect_return", [
        (["win", "win"], "win", pytest.approx(1.9 * 1.8, abs=1e-4)),
        (["win", "lose"], "lose", 0.0),
        (["win", "push"], "win", pytest.approx(1.9, abs=1e-4)),
        (["push", "push"], "push", 1.0),
        (["lose", "lose"], "lose", 0.0),
    ])
    def test_matrix(self, app, data_dir, fresh_ip, results, expect_result, expect_return):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin)
        _publish(admin, sid)
        out = _settle(admin, sid, results)
        assert out["result"] == expect_result
        assert out["return_units"] == expect_return

    def test_resettle_leaves_trace(self, app, data_dir, fresh_ip):
        """结算修正:允许,但 edit_count+1 且审计含 prev_result(不静默覆盖)。"""
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin)
        _publish(admin, sid)
        _settle(admin, sid, ["win", "lose"])
        _settle(admin, sid, ["win", "win"])   # 修正
        slip = next(s for s in admin.get("/api/v1/admin/reco/slips").json()["slips"]
                    if s["id"] == sid)
        assert slip["result"] == "win"
        assert slip["edit_count"] == 1
        logs = admin.get("/api/v1/admin/audit-logs").json()["logs"]
        settles = [l for l in logs if l["action"] == "reco.settle"]
        assert len(settles) >= 2
        assert '"resettle": true' in settles[0]["detail_json"]
        assert '"prev_result": "lose"' in settles[0]["detail_json"]

    def test_settle_requires_all_legs(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin)
        _publish(admin, sid)
        legs = _legs_of(admin, sid)
        r = admin.post(f"/api/v1/admin/reco/slips/{sid}/settle", headers=_csrf(admin),
                       json={"leg_results": {legs[0]["id"]: "win"}})
        assert r.status_code == 400


def _admin_actor_id(app, data_dir, ip):
    admin = _admin_client(app, data_dir, ip)
    return admin, admin.get("/api/v1/me").json()["user"]["id"]


class TestHkOddsContractBugRepro:
    """核心 bug 复现 + 修复验证:NowGoal ou/ah/corners_ou 是港赔(HK odds),
    旧代码把它当十进制直接相乘。真实样本(data/odds.db 只读确认,2026-08-16):
    港盘 1.03 的真实十进制应为 2.03(港盘+1.0),不是 1.03。

    经 admin 真实录入管线(HTTP /admin/reco/slips,而非绕过 API 直接调
    command 层)端到端验证——这正是 admin 从 raw_market_options 选真实报价、
    routes_reco._legs() 组装 LegInput 的真实触发路径。
    """

    def test_hk_1_03_settles_to_2_03_not_1_03_via_admin_api(self, app, data_dir, fresh_ip):
        from backend.commands import reco as cmd
        from backend.db.connections import tx

        admin = _admin_client(app, data_dir, fresh_ip)
        r = admin.post(
            "/api/v1/admin/reco/slips", headers=_csrf(admin),
            json={
                "slip_date": "2026-08-16", "title": "港盘复现", "note": None,
                "legs": [{
                    "match_desc": "X vs Y", "market": "ou", "selection": "大2.75",
                    "odds": 1.03, "match_id": 990001,
                    "source_odds": 1.03, "odds_format": "hk",
                    "provider": "nowgoal", "company_id": "8", "company_name": "Bet365",
                    "snapshot_ref": "555001", "observed_at": "2026-08-15T12:00:00Z",
                    "line": 2.75, "side": "over", "payload_hash": "deadbeef",
                }],
            },
        )
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        _publish(admin, sid)
        legs = _legs_of(admin, sid)
        assert len(legs) == 1
        out = _settle(admin, sid, ["win"])
        assert out["return_units"] == pytest.approx(2.03, abs=1e-4), (
            "港盘 1.03 win 必须结算成十进制 2.03(港盘+1.0),不是把 1.03 当十进制直接相乘"
        )
        assert out["return_units"] != pytest.approx(1.03, abs=1e-4)

    def test_legacy_manual_path_unaffected_no_provenance_fields(self, app, data_dir, fresh_ip):
        """向后兼容:不传溯源字段的手打十进制赔率入口,一旦进入 published 状态,
        settle_slip() 对它的结算数学必须继续工作、行为不变。

        2026-08-16 起新增的发布前置校验(backend/commands/reco.py::
        require_provenance_bound_legs,只挂在 admin HTTP 写面
        backend/api/routes_reco.py::admin_publish_slip)禁止全新
        draft→published 转换携带 legacy_manual 腿,因此这里改用 command 层
        直接 publish(绕过 HTTP 写面的新校验),模拟"已经是 published 状态的
        老单"这一被新规则明确排除在外的场景(CLAUDE.md 变更:不追溯校验历史
        发布过的单;commands.reco.publish_slip() 本身不知道这条新规则,校验
        只在 admin 路由层)。继续验证的是同一件事:legacy_manual 腿的
        canonical_decimal_odds=odds、settle_slip 乘法结果不受这次改动影响。
        """
        from backend.commands import reco as cmd
        from backend.db.connections import tx

        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[
            {"match_desc": "A vs B", "market": "1x2", "selection": "主胜", "odds": 1.9},
        ])
        actor = admin.get("/api/v1/me").json()["user"]["id"]
        conn = connect_rw("platform")
        try:
            with tx(conn):
                cmd.publish_slip(conn, sid, actor=actor)
        finally:
            conn.close()
        out = _settle(admin, sid, ["win"])
        assert out["return_units"] == pytest.approx(1.9, abs=1e-4)


class TestQuarterLineSettleMath:
    """四分之一盘口半赢半输(half_win/half_loss)结算数学,直接用 command 层
    (cmd.create_slip/publish_slip/settle_slip)构造 canonical_decimal_odds 已知
    的腿,精确验证乘数公式,不依赖 HTTP 层的 JSON 编解码噪音。"""

    def _slip_with_legs(self, conn, actor, leg_inputs):
        from backend.commands import reco as cmd
        from backend.db.connections import tx

        with tx(conn):
            sid = cmd.create_slip(
                conn, slip_date="2026-08-16", title="quarter-line test",
                legs=leg_inputs, note=None, actor=actor,
            )
            cmd.publish_slip(conn, sid, actor=actor)
        leg_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM reco_legs WHERE slip_id=? ORDER BY sort_order", (sid,)
        ).fetchall()]
        return sid, leg_ids

    def test_single_leg_half_win(self, app, data_dir, fresh_ip):
        from backend.commands import reco as cmd
        from backend.db.connections import tx

        _, actor = _admin_actor_id(app, data_dir, fresh_ip)
        conn = connect_rw("platform")
        try:
            leg = cmd.LegInput(
                match_desc="X vs Y", market="ah", selection="受让0.25",
                odds=1.83, match_id=990002,
                source_odds=0.83, odds_format="hk", provider="nowgoal",
                snapshot_ref="555002", observed_at="2026-08-15T12:00:00Z",
                line=-0.25, side="home", payload_hash="beadfeed",
            )
            sid, leg_ids = self._slip_with_legs(conn, actor, [leg])
            with tx(conn):
                out = cmd.settle_slip(conn, sid, {leg_ids[0]: "half_win"}, actor=actor)
            # canonical = 0.83 + 1.0 = 1.83; half_win 乘数 = 1 + (1.83-1)/2 = 1.415
            assert out["return_units"] == pytest.approx(1.415, abs=1e-4)
        finally:
            conn.close()

    def test_single_leg_half_loss_returns_half_unit_regardless_of_odds(self, app, data_dir, fresh_ip):
        from backend.commands import reco as cmd
        from backend.db.connections import tx

        _, actor = _admin_actor_id(app, data_dir, fresh_ip)
        conn = connect_rw("platform")
        try:
            leg = cmd.LegInput(match_desc="X vs Y", market="1x2", selection="主胜", odds=3.5)
            sid, leg_ids = self._slip_with_legs(conn, actor, [leg])
            with tx(conn):
                out = cmd.settle_slip(conn, sid, {leg_ids[0]: "half_loss"}, actor=actor)
            assert out["return_units"] == pytest.approx(0.5, abs=1e-4)
        finally:
            conn.close()

    def test_three_leg_parlay_win_half_loss_push(self, app, data_dir, fresh_ip):
        """真实串关连乘例子:win(2.0) × half_loss(固定0.5) × push(1.0) = 1.0。"""
        from backend.commands import reco as cmd
        from backend.db.connections import tx

        _, actor = _admin_actor_id(app, data_dir, fresh_ip)
        conn = connect_rw("platform")
        try:
            legs_in = [
                cmd.LegInput(match_desc="A vs B", market="1x2", selection="主胜", odds=2.0),
                cmd.LegInput(match_desc="C vs D", market="1x2", selection="客胜", odds=1.5),
                cmd.LegInput(match_desc="E vs F", market="1x2", selection="平局", odds=3.2),
            ]
            sid, leg_ids = self._slip_with_legs(conn, actor, legs_in)
            with tx(conn):
                out = cmd.settle_slip(
                    conn, sid,
                    {leg_ids[0]: "win", leg_ids[1]: "half_loss", leg_ids[2]: "push"},
                    actor=actor,
                )
            assert out["return_units"] == pytest.approx(1.0, abs=1e-4)
        finally:
            conn.close()

    def test_any_lose_overrides_half_win_legs_to_zero(self, app, data_dir, fresh_ip):
        """任一腿 lose 时整单直接判负,哪怕其它腿是净赚的 half_win——lose 规则
        只对真正整仓 lose 生效,不受 half_win/half_loss 的存在影响。"""
        from backend.commands import reco as cmd
        from backend.db.connections import tx

        _, actor = _admin_actor_id(app, data_dir, fresh_ip)
        conn = connect_rw("platform")
        try:
            legs_in = [
                cmd.LegInput(match_desc="A vs B", market="1x2", selection="主胜", odds=1.83),
                cmd.LegInput(match_desc="C vs D", market="1x2", selection="客胜", odds=2.0),
            ]
            sid, leg_ids = self._slip_with_legs(conn, actor, legs_in)
            with tx(conn):
                out = cmd.settle_slip(
                    conn, sid, {leg_ids[0]: "half_win", leg_ids[1]: "lose"}, actor=actor,
                )
            assert out["result"] == "lose"
            assert out["return_units"] == 0.0
        finally:
            conn.close()


class TestVoidAndEdit:
    def test_void_requires_reason_and_stays_visible(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin)
        _publish(admin, sid)
        r = admin.post(f"/api/v1/admin/reco/slips/{sid}/void", headers=_csrf(admin),
                       json={"reason": ""})
        assert r.status_code in (400, 422)
        r2 = admin.post(f"/api/v1/admin/reco/slips/{sid}/void", headers=_csrf(admin),
                        json={"reason": "赛事延期"})
        assert r2.status_code == 200
        # 作废单在战绩面单列可见,且不进任何分母
        m = _member_client(app, f"{fresh_ip}-m", openid="reco-v1")
        body = m.get("/api/v1/reco/track-record").json()
        assert any(s["id"] == sid and s["status"] == "voided" for s in body["slips"])
        assert body["summary"]["voided_count"] == 1
        assert body["summary"]["settled_count"] == 0
        assert body["summary"]["hit_rate"] is None

    def test_edit_draft_leaves_trace(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin)
        r = admin.patch(f"/api/v1/admin/reco/slips/{sid}", headers=_csrf(admin),
                        json={"title": "改标题"})
        assert r.status_code == 200
        slip = next(s for s in admin.get("/api/v1/admin/reco/slips").json()["slips"]
                    if s["id"] == sid)
        assert slip["title"] == "改标题"
        assert slip["edit_count"] == 1
        actions = [l["action"] for l in admin.get("/api/v1/admin/audit-logs").json()["logs"]]
        assert "reco.edit" in actions

    def test_settled_slip_rejects_edit(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin)
        _publish(admin, sid)
        _settle(admin, sid, ["win", "win"])
        r = admin.patch(f"/api/v1/admin/reco/slips/{sid}", headers=_csrf(admin),
                        json={"title": "改"})
        assert r.status_code == 400


class TestSummaryAggregation:
    def test_summary_math(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        # win(1.9×1.8=3.42, 净+2.42) + lose(净-1) + push(净0)
        for results in (["win", "win"], ["lose", "lose"], ["push", "push"]):
            sid = _create_slip(admin)
            _publish(admin, sid)
            _settle(admin, sid, results)
        m = _member_client(app, f"{fresh_ip}-m", openid="reco-s1")
        s = m.get("/api/v1/reco/track-record").json()["summary"]
        assert s["settled_count"] == 3
        assert (s["win_count"], s["lose_count"], s["push_count"]) == (1, 1, 1)
        assert s["hit_rate"] == 0.5          # push 不计分母
        assert s["net_units"] == pytest.approx(2.42 - 1 + 0, abs=1e-4)

    def test_half_loss_settled_slip_visible_in_breakdown_not_swallowed(self, app, data_dir, fresh_ip):
        """半输(half_loss,2026-08-16 四分之一盘口扩展)是合法结算结果——单腿单
        以 half_loss 结算时,乘数固定 0.5<1.0,整单判定同样是 half_loss(见
        backend/commands/reco.py::settle_slip)。这类记录必须和 win/lose/push
        一样在战绩汇总里可见,不能因为汇总 SQL 硬编码三值 CASE 就从统计里
        消失——那等价于 CLAUDE.md 严禁的"选择性丢失公开战绩记录",哪怕不是
        故意删除,只是新枚举值没被汇总口径覆盖到,后果一样。"""
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[
            _prov_leg("A vs B", "1x2", "主胜", 1.9, 910201),
        ])
        _publish(admin, sid)
        out = _settle(admin, sid, ["half_loss"])
        assert out["result"] == "half_loss"

        m = _member_client(app, f"{fresh_ip}-hl", openid="reco-halfloss-summary")
        s = m.get("/api/v1/reco/track-record").json()["summary"]
        assert s["settled_count"] == 1
        assert s["half_win_count"] == 0
        assert s["half_loss_count"] == 1
        # 五分类之和必须等于已结算总数——half_loss 不能既不算赢、也不算输、
        # 也不算走水,凭空从任何一个分类桶里消失。
        assert (
            s["win_count"] + s["lose_count"] + s["push_count"]
            + s["half_win_count"] + s["half_loss_count"]
        ) == s["settled_count"]
        # half_loss 计入命中率分母(已判定)但不贡献命中权重:0 / 1 = 0.0——
        # 不能被误算成 win(命中率虚高)也不能凭空消失(分母漏计)。
        assert s["hit_rate"] == 0.0


class TestPublicOverview:
    """匿名聚合面 /reco/overview:只有计数与聚合,绝无单据内容;draft 不计入。"""

    def test_anonymous_counts_no_content_leak(self, app, data_dir, fresh_ip):
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo

        admin = _admin_client(app, data_dir, fresh_ip)
        today = (
            datetime.now(timezone.utc)
            .astimezone(ZoneInfo("Asia/Shanghai"))
            .strftime("%Y-%m-%d")
        )
        sid = _create_slip(admin, slip_date=today, title="泄漏检测标题甲")

        anon = TestClient(app)
        r0 = anon.get("/api/v1/reco/overview")
        assert r0.status_code == 200
        assert r0.json()["today_published_count"] == 0, "draft 不得计入今日发布"

        _publish(admin, sid)
        r = anon.get("/api/v1/reco/overview")
        assert r.status_code == 200
        body = r.json()
        assert body["today_date"] == today
        assert body["today_published_count"] == 1
        assert body["today_latest_published_at"] is not None
        # 未结算单只贡献计数:任何单据内容字段都不得出现在响应里
        assert "泄漏检测标题甲" not in r.text
        assert "主胜" not in r.text
        assert "slips" not in body and "legs" not in r.text
        assert "no-store" in r.headers["Cache-Control"]

        _settle(admin, sid, ["win", "win"])
        r2 = anon.get("/api/v1/reco/overview").json()
        assert r2["settled_count"] == 1
        assert r2["win_count"] == 1
        assert r2["net_units"] > 0

    def test_half_loss_visible_in_anonymous_aggregate(self, app, data_dir, fresh_ip):
        """匿名聚合面同样不能吞掉 half_loss(命中率造假的另一个入口——公开
        首页摘要比登录战绩页触达面更大,同一个 bug 在这里危害更大)。"""
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo

        admin = _admin_client(app, data_dir, fresh_ip)
        today = (
            datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
        )
        sid = _create_slip(admin, slip_date=today, legs=[
            _prov_leg("A vs B", "1x2", "主胜", 1.9, 910202),
        ])
        _publish(admin, sid)
        _settle(admin, sid, ["half_loss"])

        anon = TestClient(app)
        body = anon.get("/api/v1/reco/overview").json()
        assert body["settled_count"] == 1
        assert body["half_loss_count"] == 1
        assert (
            body["win_count"] + body["lose_count"] + body["push_count"]
            + body["half_win_count"] + body["half_loss_count"]
        ) == body["settled_count"]


class TestPublishedMatchIdsExistenceOnly:
    """推荐"存在性"公开(2026-08-11 站长授权):published 单覆盖的 match_id
    可匿名可见(overview.published_match_ids / 详情 reco_published),但绝不
    泄漏方向/赔率/标题;draft 不可见;settled 不再算"赛前已发布"。"""

    def test_ids_track_slip_lifecycle(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[
            _prov_leg("A vs B", "1x2", "主胜", 1.9, 777001),
        ])
        anon = TestClient(app)

        # draft:不可见
        assert 777001 not in anon.get("/api/v1/reco/overview").json()["published_match_ids"]

        # published:存在性可见,但响应里没有任何单据内容
        _publish(admin, sid)
        r = anon.get("/api/v1/reco/overview")
        assert 777001 in r.json()["published_match_ids"]
        assert "主胜" not in r.text and "1.9" not in r.text and "测试二串一" not in r.text

        # settled:不再是"赛前已发布"
        _settle(admin, sid, ["win"])
        assert 777001 not in anon.get("/api/v1/reco/overview").json()["published_match_ids"]

    def test_match_detail_carries_reco_published_flag(self, app, data_dir, fresh_ip):
        from .coreseed import seed_basic_core

        seed_basic_core(data_dir)   # 造出 match 9001(英超,匿名可见)
        admin = _admin_client(app, data_dir, fresh_ip)
        anon = TestClient(app)

        assert anon.get("/api/v1/matches/9001").json()["reco_published"] is False

        sid = _create_slip(admin, legs=[
            _prov_leg("E2E Home vs E2E Away", "1x2", "主胜", 2.1, 9001),
        ])
        _publish(admin, sid)
        body = anon.get("/api/v1/matches/9001").json()
        assert body["reco_published"] is True
        # 详情响应同样不携带任何推荐内容字段
        assert "selection" not in body and "odds" not in body


class TestPublishRequiresProvenance:
    """发布前置校验(2026-08-16 新增,backend/commands/reco.py::
    require_provenance_bound_legs,只挂在 admin HTTP 写面
    backend/api/routes_reco.py::admin_publish_slip):draft→published 只放行
    全部腿都是 entry_type='provenance_bound' 的单,任何一条腿是 legacy_manual
    (缺 match_id 或缺真实盘口溯源)就拒绝,防止无真实依据的内容进入付费/公开面。

    这条规则只在这一次 draft→published 转换生效——已经是 published/settled
    的老单不追溯校验(见 TestHkOddsContractBugRepro.
    test_legacy_manual_path_unaffected_no_provenance_fields,用 command 层
    直接 publish 覆盖这条"老单不受影响"的场景)。"""

    def test_publish_rejected_when_any_leg_is_legacy_manual(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[
            _prov_leg("A vs B", "1x2", "主胜", 1.9, 930001),
            {"match_desc": "站外赛事 C vs D", "market": "1x2", "selection": "客胜", "odds": 2.0},
        ])
        r = admin.post(f"/api/v1/admin/reco/slips/{sid}/publish", headers=_csrf(admin))
        assert r.status_code == 400
        # 必须说明具体哪条腿不满足条件,不是笼统的"发布失败"
        assert "C vs D" in r.text or "第2条" in r.text
        assert "legacy_manual" in r.text or "溯源" in r.text
        # 校验失败必须整单原子拒绝——不能一部分放行,状态必须仍是 draft
        slip = next(s for s in admin.get("/api/v1/admin/reco/slips").json()["slips"]
                    if s["id"] == sid)
        assert slip["status"] == "draft"

    def test_publish_succeeds_when_all_legs_provenance_bound(self, app, data_dir, fresh_ip):
        """不能因为加了新校验就让正常路径(全部腿都有真实溯源)也挂掉。"""
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[
            _prov_leg("A vs B", "1x2", "主胜", 1.9, 930002),
            _prov_leg("C vs D", "ou", "大2.5", 1.8, 930003),
        ])
        r = admin.post(f"/api/v1/admin/reco/slips/{sid}/publish", headers=_csrf(admin))
        assert r.status_code == 200, r.text
        slip = next(s for s in admin.get("/api/v1/admin/reco/slips").json()["slips"]
                    if s["id"] == sid)
        assert slip["status"] == "published"


class TestMatchCandidatesWindowDefault:
    """/admin/reco/match-candidates 的 window 参数(2026-08-16):默认 7 天,
    显式放宽(window=all)才能搜到更远的比赛——语义直接复用
    backend/queries/matches.py::list_matches 已有的 window 解析,不新发明
    窗口表示法。"""

    def test_default_7d_excludes_far_future_match_widened_by_window_all(
        self, app, data_dir, fresh_ip
    ):
        from datetime import datetime, timedelta, timezone

        from .coreseed import insert_match, seed_core_schema

        def _iso(dt):
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        conn = connect_rw("core")
        seed_core_schema(conn)
        now = datetime.now(timezone.utc)
        insert_match(conn, 930101, date=(now + timedelta(days=3)).date().isoformat(),
                     status="NotStarted", kickoff_at_utc=_iso(now + timedelta(days=3)))
        insert_match(conn, 930102, date=(now + timedelta(days=30)).date().isoformat(),
                     status="NotStarted", kickoff_at_utc=_iso(now + timedelta(days=30)))
        conn.commit()
        conn.close()

        admin = _admin_client(app, data_dir, fresh_ip)
        default_ids = {
            m["match_id"]
            for m in admin.get("/api/v1/admin/reco/match-candidates").json()["matches"]
        }
        assert 930101 in default_ids, "默认窗口(7 天)内的比赛必须可见"
        assert 930102 not in default_ids, "默认窗口不应搜到 30 天后的比赛"

        wide_ids = {
            m["match_id"]
            for m in admin.get("/api/v1/admin/reco/match-candidates?window=all")
            .json()["matches"]
        }
        assert 930101 in wide_ids and 930102 in wide_ids, "window=all 必须能显式搜到更远的比赛"


class TestOddsOptionsFreshness:
    """真实盘口选项的新鲜度标记(2026-08-16):复用既有
    backend/queries/odds.py::classify_odds_freshness/ODDS_FRESHNESS_STALE_HOURS,
    不新发明阈值。"""

    def test_recent_observed_at_is_fresh(self, app, data_dir, fresh_ip):
        from datetime import datetime, timedelta, timezone

        from .test_reco_match_picker import _seed_snap, _seed_xref

        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, "930201", 9001)
        fresh_obs = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        _seed_snap(conn_odds, "930201", "1x2", {"home": 1.85, "draw": 3.60, "away": 4.20},
                   observed_at=fresh_obs)
        conn_odds.commit()
        conn_odds.close()

        admin = _admin_client(app, data_dir, fresh_ip)
        body = admin.get("/api/v1/admin/reco/match-candidates/9001/odds-options").json()
        home_option = next(o for o in body["options"] if o["selection"] == "主胜")
        assert home_option.get("freshness") == "FRESH"

    def test_old_observed_at_is_stale(self, app, data_dir, fresh_ip):
        from datetime import datetime, timedelta, timezone

        from .test_reco_match_picker import _seed_snap, _seed_xref

        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, "930202", 9001)
        stale_obs = (datetime.now(timezone.utc) - timedelta(hours=12)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        _seed_snap(conn_odds, "930202", "1x2", {"home": 1.85, "draw": 3.60, "away": 4.20},
                   observed_at=stale_obs)
        conn_odds.commit()
        conn_odds.close()

        admin = _admin_client(app, data_dir, fresh_ip)
        body = admin.get("/api/v1/admin/reco/match-candidates/9001/odds-options").json()
        home_option = next(o for o in body["options"] if o["selection"] == "主胜")
        assert home_option.get("freshness") == "STALE"


class TestAdminSlipsListFilters:
    """GET /admin/reco/slips 的 date_from/date_to/status 筛选(2026-08-16),
    与既有 limit/offset 配合;total 必须反映筛选后的计数,不是全库总数。"""

    def test_status_filter(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        draft_id = _create_slip(admin, title="草稿单-filter")
        pub_id = _create_slip(admin, title="已发布单-filter")
        _publish(admin, pub_id)

        body = admin.get("/api/v1/admin/reco/slips?status=draft").json()
        ids = {s["id"] for s in body["slips"]}
        assert draft_id in ids
        assert pub_id not in ids
        assert body["total"] == len(ids)

    def test_date_range_filter(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        old_id = _create_slip(admin, slip_date="2026-01-01", title="旧单-filter")
        new_id = _create_slip(admin, slip_date="2026-08-10", title="新单-filter")

        body = admin.get(
            "/api/v1/admin/reco/slips?date_from=2026-08-01&date_to=2026-08-31"
        ).json()
        ids = {s["id"] for s in body["slips"]}
        assert new_id in ids
        assert old_id not in ids
        assert body["total"] == 1


class TestAdminSlipsSettleSourceVisible:
    """settle_source/settled_at 对 admin 可见(2026-08-16 起 settle_source 补进
    响应;settled_at 本来就有,这里一并断言确认)。"""

    def test_settle_source_and_settled_at_present_after_manual_settle(
        self, app, data_dir, fresh_ip
    ):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin)
        _publish(admin, sid)
        _settle(admin, sid, ["win", "win"])
        slip = next(s for s in admin.get("/api/v1/admin/reco/slips").json()["slips"]
                    if s["id"] == sid)
        assert slip.get("settle_source") == "manual"
        assert slip.get("settled_at") is not None


class TestAdminSlipsMatchResultAndCorners:
    """已结算腿的比分/角球现算展示(2026-08-16):不新增存储,现场 JOIN
    dim_match(+ corners_ou 腿的 fact_team_match_stats),只对已结算腿计算。"""

    def test_settled_1x2_leg_shows_match_result(self, app, data_dir, fresh_ip):
        from .coreseed import insert_match, seed_core_schema

        conn = connect_rw("core")
        seed_core_schema(conn)
        insert_match(conn, 930301, status="Finish", home_score=2, away_score=1)
        conn.commit()
        conn.close()

        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 930301)])
        _publish(admin, sid)
        _settle(admin, sid, ["win"])

        legs = _legs_of(admin, sid)
        assert legs[0].get("match_result") == {"home_score": 2, "away_score": 1}

    def test_settled_corners_ou_leg_shows_corner_sum(self, app, data_dir, fresh_ip):
        import json

        from .coreseed import insert_match, seed_core_schema

        conn = connect_rw("core")
        seed_core_schema(conn)
        insert_match(conn, 930302, status="Finish", home_score=1, away_score=1)
        conn.execute(
            "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
            " VALUES (930302, 1001, 'All', 1, ?)", (json.dumps({"corners": 6}),),
        )
        conn.execute(
            "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
            " VALUES (930302, 1002, 'All', 1, ?)", (json.dumps({"corners": 4}),),
        )
        conn.commit()
        conn.close()

        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[
            _prov_leg("A vs B", "corners_ou", "大9.5", 1.9, 930302, side="over", line=9.5),
        ])
        _publish(admin, sid)
        _settle(admin, sid, ["win"])

        legs = _legs_of(admin, sid)
        assert legs[0].get("corners") == {"home": 6.0, "away": 4.0}

    def test_unsettled_leg_has_no_match_result_or_corners_yet(self, app, data_dir, fresh_ip):
        """未结算的腿不现算(避免徒增开销)——即使比赛已完赛也保持 None,
        由 needs_review 单独提示,不在这里编造一个"提前结算"的比分展示。"""
        from .coreseed import insert_match, seed_core_schema

        conn = connect_rw("core")
        seed_core_schema(conn)
        insert_match(conn, 930303, status="Finish", home_score=1, away_score=0)
        conn.commit()
        conn.close()

        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 930303)])
        _publish(admin, sid)

        legs = _legs_of(admin, sid)
        assert legs[0]["result"] is None
        assert legs[0].get("match_result") is None
        assert legs[0].get("corners") is None


class TestAdminSlipsNeedsReview:
    """"待确认"标记(2026-08-16,不得自动判输):published 单里比赛已经正式
    完赛但 leg.result 仍是 NULL 的腿必须被标记出来,供人工发现;绝不能有任何
    代码路径把这种情况自动写成 lose 或任何确定结果——这里只做只读断言,不触发
    任何结算写入。"""

    def test_published_finished_unsettled_leg_flagged(self, app, data_dir, fresh_ip):
        from .coreseed import insert_match, seed_core_schema

        conn = connect_rw("core")
        seed_core_schema(conn)
        insert_match(conn, 930401, status="Finish", home_score=1, away_score=0)
        conn.commit()
        conn.close()

        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 930401)])
        _publish(admin, sid)
        # 刻意不结算(不调用 auto-settle 也不调用人工 settle)

        legs = _legs_of(admin, sid)
        assert legs[0].get("needs_review") is True
        assert legs[0]["result"] is None, "绝不能被自动写成任何确定结果"

    def test_not_finished_match_not_flagged(self, app, data_dir, fresh_ip):
        from .coreseed import insert_match, seed_core_schema

        conn = connect_rw("core")
        seed_core_schema(conn)
        insert_match(conn, 930402, status="NotStarted")
        conn.commit()
        conn.close()

        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 930402)])
        _publish(admin, sid)

        legs = _legs_of(admin, sid)
        assert legs[0].get("needs_review", False) is False

    def test_draft_slip_never_flagged_even_if_match_finished(self, app, data_dir, fresh_ip):
        """needs_review 只对 published 单生效——draft 单尚未上线,不需要
        "待确认"提示。"""
        from .coreseed import insert_match, seed_core_schema

        conn = connect_rw("core")
        seed_core_schema(conn)
        insert_match(conn, 930403, status="Finish", home_score=3, away_score=0)
        conn.commit()
        conn.close()

        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 930403)])
        # 不发布

        legs = _legs_of(admin, sid)
        assert legs[0].get("needs_review", False) is False

    def test_settled_leg_not_flagged(self, app, data_dir, fresh_ip):
        from .coreseed import insert_match, seed_core_schema

        conn = connect_rw("core")
        seed_core_schema(conn)
        insert_match(conn, 930404, status="Finish", home_score=1, away_score=0)
        conn.commit()
        conn.close()

        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 930404)])
        _publish(admin, sid)
        _settle(admin, sid, ["win"])

        legs = _legs_of(admin, sid)
        assert legs[0].get("needs_review", False) is False


class TestAdminSlipMemberPreview:
    """GET /admin/reco/slips/{slip_id}/preview(2026-08-16 新增):复用会员端
    daily_slips 同一套 _slip_dto/_legs_by_slip 投影,只是换成读取这一张
    (可能还是 draft)特定单,不受状态/30 天窗口限制。"""

    def test_preview_matches_member_shape_for_draft_slip(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="预览测试单")
        r = admin.get(f"/api/v1/admin/reco/slips/{sid}/preview")
        assert r.status_code == 200, r.text
        assert r.headers["cache-control"] == "private, no-store"
        slip = r.json()["slip"]
        assert slip["id"] == sid
        assert slip["title"] == "预览测试单"
        assert slip["status"] == "draft"
        # 与会员端 RecoSlipDTO 同一投影:不含 admin 专属字段
        assert "entry_type" not in slip["legs"][0]
        assert "needs_review" not in slip["legs"][0]
        assert "settle_source" not in slip

    def test_preview_nonexistent_slip_404(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        r = admin.get("/api/v1/admin/reco/slips/does-not-exist-xyz/preview")
        assert r.status_code == 404

    def test_preview_requires_admin(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin)
        m = _member_client(app, f"{fresh_ip}-m", openid="reco-preview-member")
        assert m.get(f"/api/v1/admin/reco/slips/{sid}/preview").status_code == 403

    def test_preview_anonymous_401(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin)
        anon = TestClient(app)
        assert anon.get(f"/api/v1/admin/reco/slips/{sid}/preview").status_code == 401


class TestAuditLogsFilterByTarget:
    """GET /admin/audit-logs 的 target_type/target_id 过滤(2026-08-16 新增,
    数据早已在 audit_logs 里,只是补一个查询能力,不重新发明审计存储)。"""

    def test_filter_by_reco_slip_target(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="审计过滤目标单")
        other_sid = _create_slip(admin, title="审计过滤对照单")

        r = admin.get(f"/api/v1/admin/audit-logs?target_type=reco_slip&target_id={sid}")
        assert r.status_code == 200
        logs = r.json()["logs"]
        assert logs, "至少应该有 reco.create 一条"
        assert all(l["target_type"] == "reco_slip" and l["target_id"] == sid for l in logs)
        assert not any(l["target_id"] == other_sid for l in logs)
