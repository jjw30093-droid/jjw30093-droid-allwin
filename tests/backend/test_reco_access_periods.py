"""每日精选「时段授权」(2026-10,经站长批准):后台按 用户 + 起止日期(北京时间自然日,含首尾)开通。

口径(逐条对应站长的决定):
1. 只覆盖发布时间(published_at 换算北京时间日期)落在 [starts_on, ends_on] 内的精选;
2. 自然到期后,期内发布的精选仍能看(判定看"精选何时发布",不看"现在是何时");
3. 提前撤销 = 全部收回(与撤销单场授权一致);
4. 与单场授权并存,任一有效即可看;全程后台操作、写审计日志,不做兑换码。
"""

from backend.db.connections import connect_rw

from .test_reco import _admin_client, _create_slip, _csrf, _prov_leg, _publish
from .test_reco_access import _grant, _member_uid


def _set_published_at(slip_id: str, published_at: str) -> None:
    conn = connect_rw("platform")
    try:
        conn.execute("UPDATE reco_slips SET published_at=? WHERE id=?", (published_at, slip_id))
        conn.commit()
    finally:
        conn.close()


def _slip(admin, title: str, published_at: str, match_id: int) -> str:
    sid = _create_slip(admin, title=title, legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, match_id)])
    _publish(admin, sid)
    _set_published_at(sid, published_at)
    return sid


def _period(admin, user_id, starts_on, ends_on, note=None, expect=200):
    body = {"user_id": user_id, "starts_on": starts_on, "ends_on": ends_on}
    if note is not None:
        body["note"] = note
    r = admin.post("/api/v1/admin/reco/access-periods", headers=_csrf(admin), json=body)
    assert r.status_code == expect, r.text
    return r.json()


def _revoke_period(admin, period_id, reason=None):
    body = {"reason": reason} if reason is not None else {}
    r = admin.post(f"/api/v1/admin/reco/access-periods/{period_id}/revoke", headers=_csrf(admin), json=body)
    assert r.status_code == 200, r.text


class TestCoverage:
    def test_covers_only_slips_published_within_beijing_dates(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        # 北京时间 10-02 00:30(UTC 还是 10-01 16:30)→ 属于 10-02
        inside = _slip(admin, "期内首日凌晨", "2026-10-01T16:30:00Z", 812001)
        # 北京时间 10-08 23:59 → 属于 10-08,期末当天(含)
        last_day = _slip(admin, "期末当天", "2026-10-08T15:59:00Z", 812002)
        # 北京时间 10-01 23:59 → 前一天,不在期内
        before = _slip(admin, "开始前一天", "2026-10-01T15:59:00Z", 812003)
        # 北京时间 10-09 00:00 → 后一天,不在期内
        after = _slip(admin, "结束后一天", "2026-10-08T16:00:00Z", 812004)
        m, uid = _member_uid(app, f"{fresh_ip}-p1", "reco-period-p1")

        _period(admin, uid, "2026-10-02", "2026-10-08")

        assert m.get(f"/api/v1/reco/daily/{inside}").status_code == 200
        assert m.get(f"/api/v1/reco/daily/{last_day}").status_code == 200
        assert m.get(f"/api/v1/reco/daily/{before}").status_code == 403
        assert m.get(f"/api/v1/reco/daily/{after}").status_code == 403

        # 列表面同口径:期内的给完整投影,期外的只给存在性
        slips = {s["id"]: s for s in m.get("/api/v1/reco/daily").json()["slips"]}
        assert slips[inside]["access_required"] is False and slips[inside]["title"] == "期内首日凌晨"
        assert slips[before]["access_required"] is True and "title" not in slips[before]

    def test_expired_period_still_shows_its_slips(self, app, data_dir, fresh_ip):
        """自然到期不收回:一个早已结束的时段,期内发布的单现在仍能看。"""
        admin = _admin_client(app, data_dir, fresh_ip)
        old = _slip(admin, "年初的单", "2026-01-03T04:00:00Z", 812011)
        m, uid = _member_uid(app, f"{fresh_ip}-p2", "reco-period-p2")
        _period(admin, uid, "2026-01-01", "2026-01-07")
        assert m.get(f"/api/v1/reco/daily/{old}").status_code == 200

    def test_revoke_takes_everything_back(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _slip(admin, "撤销测试", "2026-03-10T04:00:00Z", 812021)
        m, uid = _member_uid(app, f"{fresh_ip}-p3", "reco-period-p3")
        p = _period(admin, uid, "2026-03-01", "2026-03-31")
        assert m.get(f"/api/v1/reco/daily/{sid}").status_code == 200
        _revoke_period(admin, p["id"], reason="提前结束")
        assert m.get(f"/api/v1/reco/daily/{sid}").status_code == 403
        # 不允许重复撤销
        r = admin.post(f"/api/v1/admin/reco/access-periods/{p['id']}/revoke", headers=_csrf(admin), json={})
        assert r.status_code == 400

    def test_period_and_single_grant_coexist(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        in_period = _slip(admin, "时段内", "2026-04-02T04:00:00Z", 812031)
        single = _slip(admin, "单场开", "2026-05-20T04:00:00Z", 812032)
        m, uid = _member_uid(app, f"{fresh_ip}-p4", "reco-period-p4")
        _period(admin, uid, "2026-04-01", "2026-04-07")
        _grant(admin, uid, single)
        assert m.get(f"/api/v1/reco/daily/{in_period}").status_code == 200
        assert m.get(f"/api/v1/reco/daily/{single}").status_code == 200

    def test_period_of_one_user_does_not_leak_to_another(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _slip(admin, "只给 A", "2026-06-02T04:00:00Z", 812041)
        _, uid_a = _member_uid(app, f"{fresh_ip}-p5a", "reco-period-p5a")
        m_b, _ = _member_uid(app, f"{fresh_ip}-p5b", "reco-period-p5b")
        _period(admin, uid_a, "2026-06-01", "2026-06-30")
        assert m_b.get(f"/api/v1/reco/daily/{sid}").status_code == 403


class TestAdminFace:
    def test_validation(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        _, uid = _member_uid(app, f"{fresh_ip}-p6", "reco-period-p6")
        _period(admin, uid, "2026-10-08", "2026-10-01", expect=400)        # 结束早于开始
        _period(admin, uid, "2026/10/01", "2026-10-08", expect=400)        # 格式不对
        _period(admin, uid, "2026-02-30", "2026-03-01", expect=400)        # 不存在的日期
        _period(admin, uid, "2026-01-01", "2027-06-01", expect=400)        # 超过 366 天
        _period(admin, "no-such-user", "2026-10-01", "2026-10-07", expect=400)
        one_day = _period(admin, uid, "2026-10-01", "2026-10-01")          # 单日可以
        assert one_day["starts_on"] == one_day["ends_on"] == "2026-10-01"

    def test_non_admin_cannot_grant_and_csrf_required(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        m, uid = _member_uid(app, f"{fresh_ip}-p7", "reco-period-p7")
        r = m.post("/api/v1/admin/reco/access-periods", headers=_csrf(m),
                   json={"user_id": uid, "starts_on": "2026-10-01", "ends_on": "2026-10-07"})
        assert r.status_code == 403
        r2 = admin.post("/api/v1/admin/reco/access-periods",
                        json={"user_id": uid, "starts_on": "2026-10-01", "ends_on": "2026-10-07"})
        assert r2.status_code == 403
        assert m.get("/api/v1/admin/reco/access-periods").status_code == 403

    def test_audit_logs_for_grant_and_revoke(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        _, uid = _member_uid(app, f"{fresh_ip}-p8", "reco-period-p8")
        p = _period(admin, uid, "2026-07-01", "2026-07-07", note="周卡")
        _revoke_period(admin, p["id"])
        conn = connect_rw("platform")
        try:
            actions = [
                r[0] for r in conn.execute(
                    "SELECT action FROM audit_logs WHERE target_id=? ORDER BY id", (p["id"],)
                ).fetchall()
            ]
        finally:
            conn.close()
        assert actions == ["reco_access.period_grant", "reco_access.period_revoke"]

    def test_admin_list_shows_user_name_and_number(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        m, uid = _member_uid(app, f"{fresh_ip}-p9", "reco-period-p9")
        me = m.get("/api/v1/me").json()["user"]
        _period(admin, uid, "2026-08-01", "2026-08-31")
        body = admin.get("/api/v1/admin/reco/access-periods", params={"user_id": uid}).json()
        assert body["total"] == 1
        row = body["periods"][0]
        assert row["user_short_code"] == me["short_code"]
        assert row["user_display_name"] == me["display_name"]
        assert row["status"] == "active"

    def test_admin_user_search_by_short_code(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        m, uid = _member_uid(app, f"{fresh_ip}-p10", "reco-period-p10")
        code = m.get("/api/v1/me").json()["user"]["short_code"]
        users = admin.get("/api/v1/admin/users", params={"query": code.lower()}).json()["users"]
        assert [u["id"] for u in users] == [uid]
        assert users[0]["short_code"] == code


class TestMyAccess:
    def test_my_access_lists_own_periods_only(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        m_a, uid_a = _member_uid(app, f"{fresh_ip}-p11a", "reco-period-p11a")
        _, uid_b = _member_uid(app, f"{fresh_ip}-p11b", "reco-period-p11b")
        _period(admin, uid_a, "2026-09-01", "2026-09-30")
        _period(admin, uid_b, "2026-09-01", "2026-09-07")
        body = m_a.get("/api/v1/reco/my-access").json()
        assert [(p["starts_on"], p["ends_on"]) for p in body["periods"]] == [("2026-09-01", "2026-09-30")]


class TestSlipGrantListShowsUser:
    def test_slip_grant_list_has_name_and_number_and_filters_by_number(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _slip(admin, "单场列表", "2026-09-02T04:00:00Z", 812051)
        m, uid = _member_uid(app, f"{fresh_ip}-p12", "reco-period-p12")
        me = m.get("/api/v1/me").json()["user"]
        _grant(admin, uid, sid)
        body = admin.get(
            "/api/v1/admin/reco/access-grants", params={"user_id": me["short_code"].lower()}
        ).json()
        assert body["total"] == 1
        row = body["grants"][0]
        assert row["user_short_code"] == me["short_code"]
        assert row["user_display_name"] == me["display_name"]


class TestPasswordAccountsGetNumber:
    def test_create_admin_assigns_short_code_and_skips_nickname_prompt(self, app, data_dir, fresh_ip):
        from backend.cli.create_admin import create_admin

        conn = connect_rw("platform")
        try:
            uid = create_admin(conn, f"num-admin-{fresh_ip}", "admin-pass-123")
            row = conn.execute("SELECT short_code, nickname_set FROM users WHERE id=?", (uid,)).fetchone()
        finally:
            conn.close()
        assert row[0] and len(row[0]) == 6 and row[0] == row[0].upper()
        assert row[1] == 1


def test_admin_user_search_by_partial_short_code(app, data_dir, fresh_ip):
    """后台按编号前几位也能搜到(不区分大小写)。"""
    admin = _admin_client(app, data_dir, fresh_ip)
    m, uid = _member_uid(app, f"{fresh_ip}-p13", "reco-period-p13")
    code = m.get("/api/v1/me").json()["user"]["short_code"]
    users = admin.get("/api/v1/admin/users", params={"query": code[:4].lower()}).json()["users"]
    assert uid in [u["id"] for u in users]
