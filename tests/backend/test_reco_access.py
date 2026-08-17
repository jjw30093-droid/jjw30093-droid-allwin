"""每日精选按"用户 + 单条 slip"授权(2026-08-16,产品权限口径修正,经用户
批准)——取代旧的全局 reco:daily 布尔权益(持有 daily_picks 订阅即可看近
30 天全部推荐单)。

新规则(CLAUDE.md §8 三段可见性修订):除"每日精选"外全站比赛内容免费
(含匿名);"每日精选"是全站唯一需要 admin 授权的内容,必须按"用户 + 单条
slip"授予;用户获得一场的权限不得因此看到其它场次;登录本身不解锁内容,
admin 角色本身也不自动获得访问(除非显式被授予)。

覆盖(逐条对应任务验收标准):
1. 匿名访问每日精选内容(列表 + 单条)→ 401。
2. 登录但未获授权访问单条内容 → 403,响应体不含赛果/赔率/理由/概率/正文。
3. admin 授权用户 U 访问 slip A 后,U 访问 A → 200,内容完整。
4. 同一用户 U 访问从未被授权过的 slip B → 仍 403(A 的权限不外溢到 B)。
5. 撤销 U 对 A 的授权后,U 再次访问 A → 403(不依赖缓存,读当前 active 状态)。
6. 未授权时列表响应同样不泄漏标题/摘要/赔率/理由(逐字段断言)。
7. 管理员本身(未被显式授予任何 slip)访问普通用户接口 → 同样受按场授权约束。
8. Admin 授权/撤销操作各自在 audit_logs 留下可查询记录。
9. 旧的 daily_picks 订阅(全局布尔权益)不再让持有者无条件访问任何未被
   显式按场授权的 slip。
10. reco:track_record 战绩归档端点匿名访问 → 200(见 test_reco.py 里被
    替换的旧断言,本文件只覆盖新增的按场授权部分)。
"""

from fastapi.testclient import TestClient

from backend.commands.subscriptions import grant_subscription
from backend.db.connections import connect_rw

from .test_reco import _admin_client, _create_slip, _csrf, _member_client, _prov_leg, _publish


def _grant(admin, user_id, slip_id, note=None):
    body = {"user_id": user_id, "slip_id": slip_id}
    if note is not None:
        body["note"] = note
    r = admin.post("/api/v1/admin/reco/access-grants", headers=_csrf(admin), json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _revoke(admin, grant_id, reason=None):
    body = {"reason": reason} if reason is not None else {}
    r = admin.post(f"/api/v1/admin/reco/access-grants/{grant_id}/revoke",
                   headers=_csrf(admin), json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _member_uid(app, ip, openid):
    m = _member_client(app, ip, openid=openid)
    uid = m.get("/api/v1/me").json()["user"]["id"]
    return m, uid


class TestAnonymousDenied:
    """1. 匿名访问每日精选内容(列表 + 单条)→ 401。"""

    def test_anonymous_401_on_list(self, app, data_dir):
        anon = TestClient(app)
        assert anon.get("/api/v1/reco/daily").status_code == 401

    def test_anonymous_401_on_detail(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 811001)])
        _publish(admin, sid)
        anon = TestClient(app)
        assert anon.get(f"/api/v1/reco/daily/{sid}").status_code == 401


class TestUnauthorizedMemberNoLeak:
    """2 + 6. 登录但未获授权:单条内容 403 不泄漏任何正文字段;列表同样不
    泄漏标题/摘要/赔率/理由,只给存在性 + 状态的中性投影。"""

    def test_detail_403_no_content_fields_leak(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="泄漏检测标题", legs=[
            _prov_leg("A vs B", "1x2", "主胜", 1.9, 811002),
        ])
        _publish(admin, sid)

        m, _ = _member_uid(app, f"{fresh_ip}-m", "reco-access-m1")
        r = m.get(f"/api/v1/reco/daily/{sid}")
        assert r.status_code == 403
        body = r.json()
        # 403 响应体只含中性说明,不是完整 RecoSlipDTO 形状降级成 null——这些
        # key 必须整体不出现(不是取值 null)。
        for leaked_key in ("title", "note", "legs", "return_units", "result", "combo_type"):
            assert leaked_key not in body, f"403 响应体不应包含字段 {leaked_key}"
        assert "泄漏检测标题" not in r.text
        assert "主胜" not in r.text

    def test_list_hides_content_fields_for_unauthorized_slip(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="列表泄漏检测标题", legs=[
            _prov_leg("A vs B", "1x2", "主胜", 1.9, 811003),
        ])
        _publish(admin, sid)

        m, _ = _member_uid(app, f"{fresh_ip}-m2", "reco-access-m2")
        r = m.get("/api/v1/reco/daily")
        assert r.status_code == 200
        item = next(s for s in r.json()["slips"] if s["id"] == sid)
        assert item["access_required"] is True
        for leaked_key in ("title", "note", "legs", "return_units", "result", "combo_type",
                           "published_at", "edit_count"):
            assert leaked_key not in item, f"未授权 slip 的列表投影不应包含字段 {leaked_key}"
        assert "列表泄漏检测标题" not in r.text
        assert "主胜" not in r.text
        # 存在性 + 状态本身允许出现
        assert item["slip_date"] == "2026-08-10"
        assert item["status"] == "published"

    def test_detail_404_for_nonexistent_slip(self, app, data_dir, fresh_ip):
        m, _ = _member_uid(app, f"{fresh_ip}-m3", "reco-access-m3")
        assert m.get("/api/v1/reco/daily/does-not-exist-xyz").status_code == 404

    def test_detail_404_for_draft_slip_even_authenticated(self, app, data_dir, fresh_ip):
        """draft 单永不外泄给普通用户面(既有规则):即便某种异常路径拿到了
        授权,draft 状态本身在这个端点也应表现为"不存在"。"""
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 811004)])
        m, _ = _member_uid(app, f"{fresh_ip}-m4", "reco-access-m4")
        assert m.get(f"/api/v1/reco/daily/{sid}").status_code == 404


class TestGrantUnlocksExactlyOneSlip:
    """3 + 4. 授权后完整可见;从未被授权的另一张单仍然 403(权限不外溢)。"""

    def test_granted_slip_returns_full_content(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="授权可见单", legs=[
            _prov_leg("A vs B", "1x2", "主胜", 1.9, 811005),
        ])
        _publish(admin, sid)
        m, uid = _member_uid(app, f"{fresh_ip}-m5", "reco-access-m5")

        assert m.get(f"/api/v1/reco/daily/{sid}").status_code == 403
        _grant(admin, uid, sid)
        r = m.get(f"/api/v1/reco/daily/{sid}")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == sid
        assert body["title"] == "授权可见单"
        assert body["legs"][0]["selection"] == "主胜"

        # 列表面同一张单现在也应给完整投影
        lst = m.get("/api/v1/reco/daily").json()
        item = next(s for s in lst["slips"] if s["id"] == sid)
        assert item["access_required"] is False
        assert item["title"] == "授权可见单"

    def test_access_to_one_slip_does_not_unlock_another(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid_a = _create_slip(admin, title="A单", legs=[
            _prov_leg("A vs B", "1x2", "主胜", 1.9, 811006),
        ])
        sid_b = _create_slip(admin, title="B单", legs=[
            _prov_leg("C vs D", "1x2", "客胜", 2.0, 811007),
        ])
        _publish(admin, sid_a)
        _publish(admin, sid_b)
        m, uid = _member_uid(app, f"{fresh_ip}-m6", "reco-access-m6")
        _grant(admin, uid, sid_a)

        assert m.get(f"/api/v1/reco/daily/{sid_a}").status_code == 200
        r_b = m.get(f"/api/v1/reco/daily/{sid_b}")
        assert r_b.status_code == 403
        assert "B单" not in r_b.text
        assert "客胜" not in r_b.text

    def test_grant_is_idempotent(self, app, data_dir, fresh_ip):
        """重复授权同一 (user, slip) 幂等返回同一条 active 记录,不产生重复行。"""
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 811008)])
        _publish(admin, sid)
        m, uid = _member_uid(app, f"{fresh_ip}-m7", "reco-access-m7")

        g1 = _grant(admin, uid, sid)
        g2 = _grant(admin, uid, sid)
        assert g1["id"] == g2["id"]

        rows = admin.get(
            f"/api/v1/admin/reco/access-grants?user_id={uid}&slip_id={sid}"
        ).json()
        active = [g for g in rows["grants"] if g["status"] == "active"]
        assert len(active) == 1


class TestRevokeIsImmediateAndNotCached:
    """5. 撤销后立即再次访问变回 403,不依赖任何缓存。"""

    def test_revoke_then_403_again(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 811009)])
        _publish(admin, sid)
        m, uid = _member_uid(app, f"{fresh_ip}-m8", "reco-access-m8")

        grant = _grant(admin, uid, sid)
        assert m.get(f"/api/v1/reco/daily/{sid}").status_code == 200

        _revoke(admin, grant["id"], reason="测试撤销")
        r = m.get(f"/api/v1/reco/daily/{sid}")
        assert r.status_code == 403
        assert r.headers["cache-control"] == "private, no-store"

        # 撤销后重新授权应重新生效(新的 active 行,不是复活旧行)
        grant2 = _grant(admin, uid, sid)
        assert grant2["id"] != grant["id"]
        assert m.get(f"/api/v1/reco/daily/{sid}").status_code == 200

    def test_revoke_already_revoked_grant_rejected(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 811010)])
        _publish(admin, sid)
        m, uid = _member_uid(app, f"{fresh_ip}-m9", "reco-access-m9")
        grant = _grant(admin, uid, sid)
        _revoke(admin, grant["id"])
        r = admin.post(f"/api/v1/admin/reco/access-grants/{grant['id']}/revoke",
                       headers=_csrf(admin), json={})
        assert r.status_code == 400


class TestAdminRoleNotAutoGranted:
    """7. 管理员本身(未被显式授予任何 slip)访问普通用户接口的每日精选
    内容 → 同样受按场授权约束,不因 role=admin 绕过。"""

    def test_admin_without_explicit_grant_gets_403_on_member_endpoint(
        self, app, data_dir, fresh_ip
    ):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 811011)])
        _publish(admin, sid)
        # 同一个 admin session 直接访问普通用户内容端点(不是 admin 预览端点)
        r = admin.get(f"/api/v1/reco/daily/{sid}")
        assert r.status_code == 403

    def test_admin_preview_endpoint_remains_independent(self, app, data_dir, fresh_ip):
        """admin 预览端点(/admin/reco/slips/{id}/preview)不受这里的按场授权
        约束——两个端点服务不同场景,互不污染。"""
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="admin预览专用", legs=[
            _prov_leg("A vs B", "1x2", "主胜", 1.9, 811012),
        ])
        r = admin.get(f"/api/v1/admin/reco/slips/{sid}/preview")
        assert r.status_code == 200
        assert r.json()["slip"]["title"] == "admin预览专用"


class TestAuditTrail:
    """8. Admin 授权/撤销操作各自在 audit_logs 留下可查询的记录。"""

    def test_grant_and_revoke_each_write_audit_log(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 811013)])
        _publish(admin, sid)
        m, uid = _member_uid(app, f"{fresh_ip}-m10", "reco-access-m10")

        grant = _grant(admin, uid, sid, note="测试授权理由")
        logs_after_grant = admin.get(
            f"/api/v1/admin/audit-logs?target_type=reco_access_grant&target_id={grant['id']}"
        ).json()["logs"]
        grant_actions = [entry["action"] for entry in logs_after_grant]
        assert "reco_access.grant" in grant_actions

        _revoke(admin, grant["id"], reason="测试撤销理由")
        logs_after_revoke = admin.get(
            f"/api/v1/admin/audit-logs?target_type=reco_access_grant&target_id={grant['id']}"
        ).json()["logs"]
        revoke_actions = [entry["action"] for entry in logs_after_revoke]
        assert "reco_access.revoke" in revoke_actions
        assert "reco_access.grant" in revoke_actions  # 两条记录都还在,不互相覆盖


class TestLegacyGlobalSubscriptionGrantsNothing:
    """9. 旧的 daily_picks 订阅(全局布尔权益)不会让持有者无条件访问任何
    未被显式按场授权的 slip。"""

    def test_daily_picks_subscriber_without_grant_still_403(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 811014)])
        _publish(admin, sid)

        m, uid = _member_uid(app, f"{fresh_ip}-m11", "reco-access-legacy-sub")
        conn = connect_rw("platform")
        conn.execute("BEGIN IMMEDIATE")
        grant_subscription(conn, uid, "daily_picks", 30, granted_by=None, source="admin_grant")
        conn.execute("COMMIT")
        conn.close()

        assert m.get("/api/v1/me").json()["plan"] == "daily_picks"
        r_detail = m.get(f"/api/v1/reco/daily/{sid}")
        assert r_detail.status_code == 403
        r_list = m.get("/api/v1/reco/daily")
        assert r_list.status_code == 200
        item = next(s for s in r_list.json()["slips"] if s["id"] == sid)
        assert item["access_required"] is True


class TestAdminAccessGrantsWriteFace:
    def test_non_admin_cannot_grant(self, app, data_dir, fresh_ip):
        m, uid = _member_uid(app, f"{fresh_ip}-m12", "reco-access-m12")
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 811015)])
        _publish(admin, sid)
        r = m.post("/api/v1/admin/reco/access-grants", headers=_csrf(m),
                   json={"user_id": uid, "slip_id": sid})
        assert r.status_code == 403

    def test_grant_unknown_user_or_slip_rejected(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 811016)])
        _publish(admin, sid)
        r1 = admin.post("/api/v1/admin/reco/access-grants", headers=_csrf(admin),
                        json={"user_id": "no-such-user", "slip_id": sid})
        assert r1.status_code == 400
        r2 = admin.post("/api/v1/admin/reco/access-grants", headers=_csrf(admin),
                        json={"user_id": admin.get("/api/v1/me").json()["user"]["id"],
                              "slip_id": "no-such-slip"})
        assert r2.status_code == 400

    def test_admin_list_filters_by_user_and_slip_and_status(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid1 = _create_slip(admin, legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 811017)])
        sid2 = _create_slip(admin, legs=[_prov_leg("C vs D", "1x2", "客胜", 1.9, 811018)])
        _publish(admin, sid1)
        _publish(admin, sid2)
        m1, uid1 = _member_uid(app, f"{fresh_ip}-m13", "reco-access-m13")
        m2, uid2 = _member_uid(app, f"{fresh_ip}-m14", "reco-access-m14")
        g1 = _grant(admin, uid1, sid1)
        _grant(admin, uid2, sid2)
        _revoke(admin, g1["id"])

        by_user = admin.get(f"/api/v1/admin/reco/access-grants?user_id={uid1}").json()["grants"]
        assert {g["slip_id"] for g in by_user} == {sid1}

        by_slip = admin.get(f"/api/v1/admin/reco/access-grants?slip_id={sid2}").json()["grants"]
        assert {g["user_id"] for g in by_slip} == {uid2}

        by_status = admin.get("/api/v1/admin/reco/access-grants?status=revoked").json()["grants"]
        assert any(g["id"] == g1["id"] for g in by_status)
        assert all(g["status"] == "revoked" for g in by_status)

    def test_admin_write_endpoints_no_store(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 811019)])
        _publish(admin, sid)
        uid = admin.get("/api/v1/me").json()["user"]["id"]
        r = admin.post("/api/v1/admin/reco/access-grants", headers=_csrf(admin),
                       json={"user_id": uid, "slip_id": sid})
        assert r.headers["cache-control"] == "private, no-store"
        r2 = admin.get("/api/v1/admin/reco/access-grants")
        assert r2.headers["cache-control"] == "private, no-store"


class TestMyAccessPersonalQuery:
    """个人"每日精选权限查询"(CLAUDE.md §8.1 允许要求登录的账户类个人
    功能之一):只暴露当前用户自己的授权记录。"""

    def test_anonymous_401(self, app, data_dir):
        anon = TestClient(app)
        assert anon.get("/api/v1/reco/my-access").status_code == 401

    def test_lists_only_own_grants(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 811020)])
        _publish(admin, sid)
        m1, uid1 = _member_uid(app, f"{fresh_ip}-m15", "reco-access-m15")
        m2, uid2 = _member_uid(app, f"{fresh_ip}-m16", "reco-access-m16")
        _grant(admin, uid1, sid)

        grants1 = m1.get("/api/v1/reco/my-access").json()["grants"]
        assert any(g["slip_id"] == sid for g in grants1)
        grants2 = m2.get("/api/v1/reco/my-access").json()["grants"]
        assert all(g["slip_id"] != sid for g in grants2)


class TestAccessGrantDtoIncludesSlipContext:
    """RecoAccessGrantDTO 带 slip_title/slip_date(供"每日精选权限查询"和
    admin 授权面把一条授权记录具体到场次,不是只给一个不透明 UUID)。

    2026-08-17 从已删除的 tests/backend/test_redeem_reco_access.py 原样迁移
    到本文件:那个文件整体是兑换码功能的测试(已随兑换码功能一起下架),
    但这两个用例断言的是 admin 直接授权(POST /admin/reco/access-grants)
    这条路径的 DTO 形状,和兑换码无关——兑换码下架不能连带丢失这部分覆盖。
    """

    def test_my_access_includes_slip_title_and_date(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="个人权限查询场次标签",
                            legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 811021)])
        _publish(admin, sid)
        member, uid = _member_uid(app, f"{fresh_ip}-myaccess", "reco-access-myaccess")

        r = _grant(admin, uid, sid)
        assert r["slip_title"] == "个人权限查询场次标签"

        grants = member.get("/api/v1/reco/my-access").json()["grants"]
        g = next(g for g in grants if g["slip_id"] == sid)
        assert g["slip_title"] == "个人权限查询场次标签"
        assert g["slip_date"]

    def test_admin_grants_list_includes_slip_title_and_date(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="admin列表场次标签",
                            legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 811022)])
        _publish(admin, sid)
        _, uid = _member_uid(app, f"{fresh_ip}-adminlist", "reco-access-adminlist")
        _grant(admin, uid, sid)

        rows = admin.get(f"/api/v1/admin/reco/access-grants?slip_id={sid}").json()["grants"]
        assert len(rows) == 1
        assert rows[0]["slip_title"] == "admin列表场次标签"
        assert rows[0]["slip_date"]
