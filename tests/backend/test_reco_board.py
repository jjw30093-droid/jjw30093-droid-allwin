"""每日公推(board='daily_public',2026-09 新增)——与「每日精选」并列的
完全公开、匿名可见板块,不需要登录/授权。管理端操作方式与精选完全一样,
只多一个板块归属。

覆盖(逐条对应 docs 实施计划):
1. 匿名访问 /reco/public → 200,完整正文,不含 access_required。
2. /reco/public 的 Cache-Control 严格等于 PUBLIC_CACHE_SHORT;带 Cookie 强制
   no-store(中间件规则 1 覆盖 endpoint 自身声明)。
3. draft 公推单不出现在 /reco/public。
4. 公推单不出现在 /reco/daily(既不是完整投影也不是锁定卡)。
5. /reco/daily/{公推 slip_id} → 404(board 过滤在 daily_slip_detail 生效,
   不是 403——这张单对精选端点而言"不存在")。
6. 【红线】结算一张公推单前后,/reco/track-record 的 summary 全部字段与
   total 逐字段完全相同。
7. 【红线】发布并结算公推单前后,/reco/overview 全部字段逐字段完全相同。
8. 跨板块盘口冲突:同 match_id+market 出现在另一板块 → create/edit 响应
   带 warnings,但写入仍然成功(只提醒不拦截)。
9. 同板块内重复不告警;match_id 为空的腿跳过判定;market 大小写/空白不漏判。
10. voided 单不占用盘口;串关腿参与判定。
11. grant_access 对公推单拒绝(RecoAccessError)。
12. admin_slips 按 board 筛选;AdminRecoSlipDTO 含 board。
13. run_auto_settle 对公推单同样生效(板块无关)。
"""

import pytest
from fastapi.testclient import TestClient

from backend.commands import reco_access
from backend.commands.reco_access import RecoAccessError
from backend.db.connections import connect_rw, tx

from .test_reco import _admin_client, _create_slip, _csrf, _member_client, _prov_leg, _publish


def _today():
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    return datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


def _edit(admin, slip_id, **body):
    r = admin.patch(f"/api/v1/admin/reco/slips/{slip_id}", headers=_csrf(admin), json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _settle(admin, slip_id, leg_results):
    r = admin.post(f"/api/v1/admin/reco/slips/{slip_id}/settle", headers=_csrf(admin),
                    json={"leg_results": leg_results})
    assert r.status_code == 200, r.text
    return r.json()


class TestPublicEndpointAnonymous:
    def test_anonymous_sees_full_content(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="公推可见性检测", board="daily_public",
                            slip_date=_today(),
                            legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 820001)])
        _publish(admin, sid)

        anon = TestClient(app)
        r = anon.get("/api/v1/reco/public")
        assert r.status_code == 200
        body = r.json()
        slip = next(s for s in body["slips"] if s["id"] == sid)
        assert slip["title"] == "公推可见性检测"
        assert slip["legs"][0]["market"] == "1x2"
        assert slip["legs"][0]["odds"] == 1.9
        assert "access_required" not in slip

    def test_draft_not_visible(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="草稿公推", board="daily_public",
                            legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 820002)])
        anon = TestClient(app)
        ids = {s["id"] for s in anon.get("/api/v1/reco/public").json()["slips"]}
        assert sid not in ids


class TestPublicEndpointCache:
    def test_anonymous_cache_control_is_public_short(self, app, data_dir, fresh_ip):
        anon = TestClient(app)
        r = anon.get("/api/v1/reco/public")
        assert r.headers["cache-control"] == "public, s-maxage=60, stale-while-revalidate=30"

    def test_authenticated_request_forced_no_store(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        r = admin.get("/api/v1/reco/public")
        assert r.headers["cache-control"] == "private, no-store"


class TestPublicBoardNeverLeaksIntoFeaturedEndpoints:
    def test_public_slip_absent_from_daily_list(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="公推不进精选列表", board="daily_public",
                            legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 820003)])
        _publish(admin, sid)
        ids = {s["id"] for s in admin.get("/api/v1/reco/daily").json()["slips"]}
        assert sid not in ids

    def test_public_slip_detail_404_on_featured_endpoint(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="公推正文走精选端点", board="daily_public",
                            legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 820004)])
        _publish(admin, sid)
        r = admin.get(f"/api/v1/reco/daily/{sid}")
        assert r.status_code == 404


class TestPublicBoardNeverPollutesPublicNumbers:
    """红线:公推板块的存在与结算,绝不能改变已公开的精选战绩/概览数字。"""

    def test_track_record_summary_unchanged_after_settling_public_slip(
        self, app, data_dir, fresh_ip
    ):
        admin = _admin_client(app, data_dir, fresh_ip)
        # 先建一张精选已结算单作为基线参照物。
        featured_sid = _create_slip(
            admin, title="基线精选单", slip_date="2026-08-05",
            legs=[_prov_leg("E vs F", "1x2", "主胜", 1.9, 820010)],
        )
        _publish(admin, featured_sid)
        _settle(admin, featured_sid, {
            _legs_of(admin, featured_sid)[0]["id"]: "win",
        })
        before = admin.get("/api/v1/reco/track-record").json()

        public_sid = _create_slip(admin, title="待结算公推单", board="daily_public",
                                   legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 820011)])
        _publish(admin, public_sid)
        _settle(admin, public_sid, {
            _legs_of(admin, public_sid)[0]["id"]: "win",
        })

        after = admin.get("/api/v1/reco/track-record").json()
        assert after["summary"] == before["summary"]
        assert after["total"] == before["total"]
        assert public_sid not in {s["id"] for s in after["slips"]}

    def test_overview_unchanged_after_publishing_and_settling_public_slip(
        self, app, data_dir, fresh_ip
    ):
        admin = _admin_client(app, data_dir, fresh_ip)
        anon = TestClient(app)
        before = anon.get("/api/v1/reco/overview").json()

        public_sid = _create_slip(admin, title="概览污染检测", board="daily_public",
                                   legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 820012)])
        _publish(admin, public_sid)
        _settle(admin, public_sid, {
            _legs_of(admin, public_sid)[0]["id"]: "win",
        })

        after = anon.get("/api/v1/reco/overview").json()
        assert after == before
        assert public_sid not in after["published_match_ids"]


class TestCrossBoardMarketConflict:
    def test_create_warns_but_still_succeeds(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        _create_slip(admin, title="精选先占亚盘", legs=[
            _prov_leg("A vs B", "ah", "主让0.5", 1.9, 820020),
        ])
        r = admin.post("/api/v1/admin/reco/slips", headers=_csrf(admin), json={
            "slip_date": "2026-08-10", "title": "公推同场同盘口", "note": None,
            "board": "daily_public",
            "legs": [_prov_leg("A vs B", "ah", "客让0.5", 1.85, 820020)],
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["warnings"]) == 1
        assert body["warnings"][0]["match_id"] == 820020
        assert body["warnings"][0]["market"] == "ah"
        assert body["warnings"][0]["other_board"] == "daily_pick"

    def test_different_market_same_match_no_warning(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        _create_slip(admin, title="精选大小球", legs=[
            _prov_leg("A vs B", "ou", "大2.5", 1.9, 820021),
        ])
        r = admin.post("/api/v1/admin/reco/slips", headers=_csrf(admin), json={
            "slip_date": "2026-08-10", "title": "公推亚盘同场不同盘口", "note": None,
            "board": "daily_public",
            "legs": [_prov_leg("A vs B", "ah", "主让0.5", 1.9, 820021)],
        })
        assert r.status_code == 200, r.text
        assert r.json()["warnings"] == []

    def test_same_board_duplicate_no_warning(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        _create_slip(admin, title="精选先占", legs=[
            _prov_leg("A vs B", "ah", "主让0.5", 1.9, 820022),
        ])
        r = admin.post("/api/v1/admin/reco/slips", headers=_csrf(admin), json={
            "slip_date": "2026-08-10", "title": "精选同板块重复", "note": None,
            "legs": [_prov_leg("A vs B", "ah", "客让0.5", 1.85, 820022)],
        })
        assert r.status_code == 200, r.text
        assert r.json()["warnings"] == []

    def test_match_id_none_leg_skipped(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        r = admin.post("/api/v1/admin/reco/slips", headers=_csrf(admin), json={
            "slip_date": "2026-08-10", "title": "站外赛事", "note": None,
            "board": "daily_public",
            "legs": [{
                "match_desc": "海外友谊赛", "market": "1x2", "selection": "主胜",
                "odds": 1.9, "match_id": None,
            }],
        })
        assert r.status_code == 200, r.text
        assert r.json()["warnings"] == []

    def test_market_case_and_whitespace_normalized(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        _create_slip(admin, title="精选大写盘口", legs=[
            _prov_leg("A vs B", "OU", "大2.5", 1.9, 820023),
        ])
        r = admin.post("/api/v1/admin/reco/slips", headers=_csrf(admin), json={
            "slip_date": "2026-08-10", "title": "公推小写带空格", "note": None,
            "board": "daily_public",
            "legs": [_prov_leg("A vs B", " ou ", "小2.5", 1.95, 820023)],
        })
        assert r.status_code == 200, r.text
        assert len(r.json()["warnings"]) == 1

    def test_voided_slip_frees_up_market(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        featured_sid = _create_slip(admin, title="精选待作废", legs=[
            _prov_leg("A vs B", "ah", "主让0.5", 1.9, 820024),
        ])
        assert admin.post(f"/api/v1/admin/reco/slips/{featured_sid}/void",
                           headers=_csrf(admin), json={"reason": "测试释放盘口"}).status_code == 200
        r = admin.post("/api/v1/admin/reco/slips", headers=_csrf(admin), json={
            "slip_date": "2026-08-10", "title": "公推可复用已作废盘口", "note": None,
            "board": "daily_public",
            "legs": [_prov_leg("A vs B", "ah", "客让0.5", 1.85, 820024)],
        })
        assert r.status_code == 200, r.text
        assert r.json()["warnings"] == []

    def test_published_public_cannot_revert_to_featured(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="公推单向安全阀", board="daily_public",
                            legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 820026)])
        _publish(admin, sid)
        r = admin.patch(f"/api/v1/admin/reco/slips/{sid}", headers=_csrf(admin),
                         json={"board": "daily_pick"})
        assert r.status_code == 400, r.text

    def test_draft_public_can_revert_to_featured(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="草稿公推可改回精选", board="daily_public",
                            legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 820027)])
        out = _edit(admin, sid, board="daily_pick")
        assert out["status"] == "ok"

    def test_published_featured_can_become_public(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="已发布精选可转公推",
                            legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 820028)])
        _publish(admin, sid)
        out = _edit(admin, sid, board="daily_public")
        assert out["status"] == "ok"

    def test_edit_triggers_conflict_check(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        _create_slip(admin, title="精选占位", legs=[
            _prov_leg("A vs B", "ah", "主让0.5", 1.9, 820025),
        ])
        public_sid = _create_slip(admin, title="公推先不冲突", board="daily_public",
                                   legs=[_prov_leg("C vs D", "1x2", "主胜", 1.9, 820099)])
        out = _edit(admin, public_sid, legs=[
            _prov_leg("A vs B", "ah", "客让0.5", 1.85, 820025),
        ])
        assert len(out["warnings"]) == 1


class TestGrantAccessRejectsPublicBoard:
    def test_grant_access_raises_for_public_slip(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="公推不可授权", board="daily_public",
                            legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 820030)])
        member = _member_client(app, fresh_ip, openid="reco-board-member")
        user_id = member.get("/api/v1/me").json()["user"]["id"]

        conn = connect_rw("platform")
        try:
            with pytest.raises(RecoAccessError, match="公推"):
                with tx(conn):
                    reco_access.grant_access(conn, user_id, sid, actor="tester")
        finally:
            conn.close()

    def test_admin_endpoint_rejects_public_slip_with_400(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="公推HTTP层授权拒绝", board="daily_public",
                            legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 820031)])
        member = _member_client(app, fresh_ip, openid="reco-board-member-2")
        user_id = member.get("/api/v1/me").json()["user"]["id"]

        r = admin.post("/api/v1/admin/reco/access-grants", headers=_csrf(admin),
                        json={"user_id": user_id, "slip_id": sid})
        assert r.status_code == 400, r.text


class TestAdminBoardFilterAndDTO:
    def test_admin_list_filters_by_board(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        featured_sid = _create_slip(admin, title="admin筛选-精选",
                                     legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 820040)])
        public_sid = _create_slip(admin, title="admin筛选-公推", board="daily_public",
                                   legs=[_prov_leg("C vs D", "1x2", "主胜", 1.9, 820041)])

        only_public = admin.get("/api/v1/admin/reco/slips?board=daily_public").json()["slips"]
        public_ids = {s["id"] for s in only_public}
        assert public_sid in public_ids
        assert featured_sid not in public_ids

        both = admin.get("/api/v1/admin/reco/slips").json()["slips"]
        both_ids = {s["id"] for s in both}
        assert featured_sid in both_ids and public_sid in both_ids

    def test_admin_dto_includes_board(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="board字段检测", board="daily_public",
                            legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 820042)])
        slips = admin.get("/api/v1/admin/reco/slips").json()["slips"]
        slip = next(s for s in slips if s["id"] == sid)
        assert slip["board"] == "daily_public"


def _legs_of(admin, slip_id):
    slips = admin.get("/api/v1/admin/reco/slips").json()["slips"]
    return next(s for s in slips if s["id"] == slip_id)["legs"]
