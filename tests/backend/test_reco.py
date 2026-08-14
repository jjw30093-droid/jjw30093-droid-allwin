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


def _create_slip(admin, slip_date="2026-08-10", title="测试二串一", legs=None):
    legs = legs or [
        {"match_desc": "A vs B", "market": "1x2", "selection": "主胜", "odds": 1.9},
        {"match_desc": "C vs D", "market": "ou", "selection": "大2.5", "odds": 1.8},
    ]
    r = admin.post("/api/v1/admin/reco/slips", headers=_csrf(admin),
                   json={"slip_date": slip_date, "title": title, "note": None, "legs": legs})
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
    """四方矩阵:匿名 401 / member 403(daily)+200(track)/ 付费 200 / admin 无 reco 403。"""

    def test_anonymous_401_both(self, app, data_dir):
        anon = TestClient(app)
        assert anon.get("/api/v1/reco/daily").status_code == 401
        assert anon.get("/api/v1/reco/track-record").status_code == 401

    def test_member_track_ok_daily_403(self, app, data_dir, fresh_ip):
        m = _member_client(app, fresh_ip)
        assert m.get("/api/v1/reco/track-record").status_code == 200
        r = m.get("/api/v1/reco/daily")
        assert r.status_code == 403
        assert r.json()["code"] == "reco_membership_required"

    def test_paid_sees_both(self, app, data_dir, fresh_ip):
        p = _paid_client(app, fresh_ip)
        assert p.get("/api/v1/reco/daily").status_code == 200
        assert p.get("/api/v1/reco/track-record").status_code == 200
        assert p.get("/api/v1/me").json()["plan"] == "daily_picks"

    def test_admin_role_does_not_grant_reco(self, app, data_dir, fresh_ip):
        """Role⊥Entitlement:admin 管内容,不自动获得付费板块可见性。"""
        admin = _admin_client(app, data_dir, fresh_ip)
        assert admin.get("/api/v1/reco/daily").status_code == 403

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

    def test_separate_from_model_track_record(self, app, data_dir):
        """与模型公开战绩分离:/api/v1/track-record 匿名可访问且不含 reco 字段。"""
        anon = TestClient(app)
        r = anon.get("/api/v1/track-record")
        assert r.status_code == 200
        assert "reco" not in r.text
        assert "slip" not in r.text


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


class TestPublishedMatchIdsExistenceOnly:
    """推荐"存在性"公开(2026-08-11 站长授权):published 单覆盖的 match_id
    可匿名可见(overview.published_match_ids / 详情 reco_published),但绝不
    泄漏方向/赔率/标题;draft 不可见;settled 不再算"赛前已发布"。"""

    def test_ids_track_slip_lifecycle(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, legs=[
            {"match_desc": "A vs B", "market": "1x2", "selection": "主胜",
             "odds": 1.9, "match_id": 777001},
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
            {"match_desc": "E2E Home vs E2E Away", "market": "1x2",
             "selection": "主胜", "odds": 2.1, "match_id": 9001},
        ])
        _publish(admin, sid)
        body = anon.get("/api/v1/matches/9001").json()
        assert body["reco_published"] is True
        # 详情响应同样不携带任何推荐内容字段
        assert "selection" not in body and "odds" not in body
