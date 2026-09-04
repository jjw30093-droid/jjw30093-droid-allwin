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

from .coreseed import insert_match, seed_core_schema
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


# ── 首页 banner 数据面 GET /api/v1/reco/public/current(2026-09)────────
#
# 只放 published 公推 + 每条腿的精确开球时刻,**不含赔率**。
# 注意:「开球 +2 小时撤下」不在这里测——那条判定刻意留在前端(服务端算出
# 来的结果会被 CDN/ISR 缓存住变陈旧),纯函数测试在
# frontend/tests/reco-banner.test.ts。本类测的是"下发了正确的事实"。


def _seed_core_match(match_id, kickoff_at_utc, *, status="NotStarted"):
    """在 core 库种一场比赛,供 reco 腿的 match_id 关联出 kickoff。"""
    conn = connect_rw("core")
    seed_core_schema(conn)
    insert_match(conn, match_id, date="2027-04-01", status=status,
                 kickoff_at_utc=kickoff_at_utc)
    conn.commit()
    conn.close()


class TestPublicCurrentEndpoint:
    def test_anonymous_sees_published_slip_with_kickoff(self, app, data_dir, fresh_ip):
        _seed_core_match(830001, "2027-04-01T12:00:00Z")
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="banner 公推", board="daily_public",
                            slip_date=_today(),
                            legs=[_prov_leg("A vs B 04-01 20:00", "ou", "大2.5", 1.9, 830001)])
        _publish(admin, sid)

        anon = TestClient(app)
        r = anon.get("/api/v1/reco/public/current")
        assert r.status_code == 200
        body = r.json()
        assert body["hide_after_kickoff_hours"] == 2.0
        slip = next(s for s in body["slips"] if s["id"] == sid)
        leg = slip["legs"][0]
        assert leg["match_desc"] == "A vs B 04-01 20:00"
        assert leg["market"] == "ou"
        assert leg["selection"] == "大2.5"
        # kickoff 逐字节透传,不做任何时区/格式转换
        assert leg["kickoff_at_utc"] == "2027-04-01T12:00:00Z"

    def test_response_never_contains_odds_or_provenance(self, app, data_dir, fresh_ip):
        """红线:banner 的产品要求是不展示赔率。用响应**原始文本**扫描,
        连嵌在别处的 source_odds/odds_format 也一并挡住。"""
        _seed_core_match(830002, "2027-04-01T12:00:00Z")
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="不含赔率", board="daily_public",
                            slip_date=_today(),
                            legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 830002)])
        _publish(admin, sid)

        text = TestClient(app).get("/api/v1/reco/public/current").text
        for forbidden in ("odds", "result", "return_units", "entry_type",
                          "snapshot_ref", "provider"):
            assert forbidden not in text, f"banner 响应不得出现 {forbidden}"

    def test_draft_settled_voided_all_absent(self, app, data_dir, fresh_ip):
        _seed_core_match(830003, "2027-04-01T12:00:00Z")
        _seed_core_match(830004, "2027-04-01T12:00:00Z")
        _seed_core_match(830005, "2027-04-01T12:00:00Z")
        admin = _admin_client(app, data_dir, fresh_ip)
        anon = TestClient(app)

        draft_id = _create_slip(admin, title="草稿", board="daily_public",
                                 slip_date=_today(),
                                 legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 830003)])
        settled_id = _create_slip(admin, title="待结算", board="daily_public",
                                   slip_date=_today(),
                                   legs=[_prov_leg("C vs D", "1x2", "主胜", 1.9, 830004)])
        voided_id = _create_slip(admin, title="待作废", board="daily_public",
                                  slip_date=_today(),
                                  legs=[_prov_leg("E vs F", "1x2", "主胜", 1.9, 830005)])
        _publish(admin, settled_id)
        _publish(admin, voided_id)

        ids = {s["id"] for s in anon.get("/api/v1/reco/public/current").json()["slips"]}
        assert draft_id not in ids, "draft 永不外泄"
        assert settled_id in ids and voided_id in ids

        _settle(admin, settled_id, {_legs_of(admin, settled_id)[0]["id"]: "win"})
        assert admin.post(f"/api/v1/admin/reco/slips/{voided_id}/void",
                           headers=_csrf(admin), json={"reason": "测试"}).status_code == 200

        ids_after = {s["id"] for s in anon.get("/api/v1/reco/public/current").json()["slips"]}
        assert settled_id not in ids_after, "结算即撤下"
        assert voided_id not in ids_after, "作废即撤下"

    def test_voided_slip_still_visible_on_reco_public(self, app, data_dir, fresh_ip):
        """红线回归:banner 撤下作废单,不代表 /reco 页记录面也跟着丢——
        那边"作废不消失"的纪律必须原样成立。"""
        _seed_core_match(830006, "2027-04-01T12:00:00Z")
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="作废但记录面可见", board="daily_public",
                            slip_date=_today(),
                            legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 830006)])
        _publish(admin, sid)
        admin.post(f"/api/v1/admin/reco/slips/{sid}/void",
                    headers=_csrf(admin), json={"reason": "测试"})

        anon = TestClient(app)
        assert sid not in {s["id"] for s in
                            anon.get("/api/v1/reco/public/current").json()["slips"]}
        assert sid in {s["id"] for s in anon.get("/api/v1/reco/public").json()["slips"]}

    def test_missing_kickoff_reported_as_null_not_faked(self, app, data_dir, fresh_ip):
        """§6.2.1:来源只给自然日时 kickoff_at_utc 必须是 NULL,绝不能用
        Date 列顶替成当天 00:00。端点如实下发 null,由前端 fail-closed。"""
        _seed_core_match(830007, None)
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="缺开球时间", board="daily_public",
                            slip_date=_today(),
                            legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 830007)])
        _publish(admin, sid)

        slip = next(s for s in TestClient(app).get("/api/v1/reco/public/current")
                     .json()["slips"] if s["id"] == sid)
        assert slip["legs"][0]["kickoff_at_utc"] is None

    def test_match_absent_from_core_does_not_break_endpoint(self, app, data_dir, fresh_ip):
        """腿的 match_id 在 core 库里根本查不到时,该腿 kickoff 为 null,
        端点仍然 200(不能 KeyError/500)。"""
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="core 里没有这场", board="daily_public",
                            slip_date=_today(),
                            legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 839999)])
        _publish(admin, sid)

        r = TestClient(app).get("/api/v1/reco/public/current")
        assert r.status_code == 200
        slip = next(s for s in r.json()["slips"] if s["id"] == sid)
        assert slip["legs"][0]["kickoff_at_utc"] is None

    def test_parlay_legs_keep_order_and_own_kickoffs(self, app, data_dir, fresh_ip):
        _seed_core_match(830010, "2027-04-01T12:00:00Z")
        _seed_core_match(830011, "2027-04-01T15:00:00Z")
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="二串一", board="daily_public",
                            slip_date=_today(), legs=[
                                _prov_leg("早场 A vs B", "1x2", "主胜", 1.9, 830010),
                                _prov_leg("晚场 C vs D", "ou", "大2.5", 1.8, 830011),
                            ])
        _publish(admin, sid)

        slip = next(s for s in TestClient(app).get("/api/v1/reco/public/current")
                     .json()["slips"] if s["id"] == sid)
        assert slip["combo_type"] == "parlay"
        assert [l["match_desc"] for l in slip["legs"]] == ["早场 A vs B", "晚场 C vs D"]
        assert [l["kickoff_at_utc"] for l in slip["legs"]] == [
            "2027-04-01T12:00:00Z", "2027-04-01T15:00:00Z",
        ]

    def test_leg_carries_league_and_team_facts_for_badges(self, app, data_dir, fresh_ip):
        """2026-09 横条改版:腿要能画联赛徽与两枚队徽,所以下发 league_id +
        主客队(中文名 + 同源队徽地址)。

        `insert_match` 默认 League_ID=47(英超)、1001 Arsenal vs 1002 Chelsea。
        """
        _seed_core_match(830040, "2027-04-01T12:00:00Z")
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="带队徽的公推", board="daily_public",
                            slip_date=_today(),
                            legs=[_prov_leg("A vs B", "ou", "大2.5", 1.9, 830040)])
        _publish(admin, sid)

        slip = next(s for s in TestClient(app).get("/api/v1/reco/public/current")
                     .json()["slips"] if s["id"] == sid)
        leg = slip["legs"][0]
        assert leg["league_id"] == 47
        assert leg["league_name_zh"] == "英超"
        assert leg["home"]["team_id"] == 1001
        assert leg["away"]["team_id"] == 1002
        assert leg["home"]["name"]
        assert leg["away"]["name"]
        # 队徽没被媒体管线采过 → crest_url 为 None 是合法状态,不是错误;
        # 前端 TeamBadge 走两字缩写兜底。字段必须存在(而不是整个 home 缺失)。
        assert "crest_url" in leg["home"]
        # 不下发 name_en:banner 上没有位置展示英文名。
        assert "name_en" not in leg["home"]

    def test_leg_without_core_match_row_degrades_but_is_not_dropped(
        self, app, data_dir, fresh_ip
    ):
        """core 库里查不到这场比赛(legacy 数据/尚未同步)时,联赛与主客队
        字段全部为 None,但**这条腿本身照常下发**——缺图标只是少画一个图标,
        绝不能因此把腿藏起来。"""
        admin = _admin_client(app, data_dir, fresh_ip)
        # 刻意不 _seed_core_match:match_id 在 core 库里不存在
        sid = _create_slip(admin, title="core 无此场", board="daily_public",
                            slip_date=_today(),
                            legs=[_prov_leg("孤儿场 A vs B", "1x2", "主胜", 1.9, 839999)])
        _publish(admin, sid)

        slip = next(s for s in TestClient(app).get("/api/v1/reco/public/current")
                     .json()["slips"] if s["id"] == sid)
        leg = slip["legs"][0]
        assert leg["match_desc"] == "孤儿场 A vs B"   # 腿在,文本兜底在
        assert leg["selection"] == "主胜"
        assert leg["league_id"] is None
        assert leg["league_name_zh"] is None
        assert leg["home"] is None
        assert leg["away"] is None
        assert leg["kickoff_at_utc"] is None

    def test_league_outside_league_meta_keeps_leg_but_drops_zh_name(
        self, app, data_dir, fresh_ip
    ):
        """未登记进 LEAGUE_META 的联赛:league_name_zh 为 None(不能把内部
        league_id 当名字露给用户 §11.2),但腿与 league_id 照常下发——
        前端 LeagueBadge 找不到图就静默不渲染。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        # dim_match 有赛季守卫(迁移 0011 §6.3):联赛必须先登记进制度表,
        # 否则 INSERT 直接被触发器拒绝。所以"不在 LEAGUE_META"这个场景只能
        # 由"制度表有、LEAGUE_META 没有"构造出来——这也正是它在生产上唯一
        # 可能的形态(新联赛先接数据、中文名后补)。
        conn.execute(
            "INSERT OR REPLACE INTO dim_league_season_regime"
            " (league_id, effective_from, season_kind, cutover_month, note)"
            " VALUES (999999, '1900-01-01', 'cross_year', 7, '尚未登记中文名的新联赛')"
        )
        insert_match(conn, 830041, league_id=999999,
                     date="2027-04-01", status="NotStarted",
                     kickoff_at_utc="2027-04-01T12:00:00Z")
        conn.commit()
        conn.close()
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="未登记联赛", board="daily_public",
                            slip_date=_today(),
                            legs=[_prov_leg("X vs Y", "1x2", "主胜", 1.9, 830041)])
        _publish(admin, sid)

        slip = next(s for s in TestClient(app).get("/api/v1/reco/public/current")
                     .json()["slips"] if s["id"] == sid)
        leg = slip["legs"][0]
        assert leg["league_id"] == 999999
        assert leg["league_name_zh"] is None
        assert leg["match_desc"] == "X vs Y"

    def test_featured_board_slip_never_leaks(self, app, data_dir, fresh_ip):
        """最严重的越权可能:每日精选(需授权)混进完全公开的 banner 面。"""
        _seed_core_match(830020, "2027-04-01T12:00:00Z")
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="精选不得进 banner", slip_date=_today(),
                            legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 830020)])
        _publish(admin, sid)

        text = TestClient(app).get("/api/v1/reco/public/current").text
        assert sid not in text
        assert "精选不得进 banner" not in text

    def test_window_excludes_old_slips(self, app, data_dir, fresh_ip):
        from datetime import datetime, timedelta, timezone
        from zoneinfo import ZoneInfo

        _seed_core_match(830030, "2027-04-01T12:00:00Z")
        _seed_core_match(830031, "2027-04-01T12:00:00Z")
        beijing = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai"))
        recent = (beijing - timedelta(days=1)).strftime("%Y-%m-%d")
        old = (beijing - timedelta(days=5)).strftime("%Y-%m-%d")

        admin = _admin_client(app, data_dir, fresh_ip)
        recent_id = _create_slip(admin, title="窗口内", board="daily_public",
                                  slip_date=recent,
                                  legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 830030)])
        old_id = _create_slip(admin, title="窗口外", board="daily_public",
                               slip_date=old,
                               legs=[_prov_leg("C vs D", "1x2", "主胜", 1.9, 830031)])
        _publish(admin, recent_id)
        _publish(admin, old_id)

        ids = {s["id"] for s in TestClient(app).get("/api/v1/reco/public/current")
                .json()["slips"]}
        assert recent_id in ids
        assert old_id not in ids

    def test_cache_control_public_anonymous_and_no_store_with_cookie(
        self, app, data_dir, fresh_ip
    ):
        anon = TestClient(app)
        assert anon.get("/api/v1/reco/public/current").headers["cache-control"] == (
            "public, s-maxage=60, stale-while-revalidate=30"
        )
        admin = _admin_client(app, data_dir, fresh_ip)
        assert admin.get("/api/v1/reco/public/current").headers["cache-control"] == (
            "private, no-store"
        )

    def test_does_not_disturb_existing_reco_endpoints(self, app, data_dir, fresh_ip):
        """红线:新端点不得改变 /reco/public、/reco/overview、
        /reco/track-record 任何一个字段。"""
        _seed_core_match(830040, "2027-04-01T12:00:00Z")
        anon = TestClient(app)
        before = (
            anon.get("/api/v1/reco/public").json(),
            anon.get("/api/v1/reco/overview").json(),
            anon.get("/api/v1/reco/track-record").json(),
        )
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="不影响既有端点", board="daily_public",
                            slip_date=_today(),
                            legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 830040)])
        _publish(admin, sid)
        anon.get("/api/v1/reco/public/current")

        after_overview = anon.get("/api/v1/reco/overview").json()
        after_track = anon.get("/api/v1/reco/track-record").json()

        # /reco/track-record 是精选战绩面,必须逐字段完全不变。
        assert after_track == before[2]

        # /reco/overview 的**统计口径**逐字段不变(公推不进精选战绩数字)。
        stat_keys = [
            "settled_count", "win_count", "lose_count", "push_count",
            "half_win_count", "half_loss_count", "voided_count",
            "hit_rate", "net_units", "today_published_count",
            "today_latest_published_at", "window_days",
        ]
        for k in stat_keys:
            assert after_overview[k] == before[1][k], f"overview.{k} 被公推污染了"

        # published_match_ids 是唯一会变的字段,且这是**既定设计**:它只表达
        # "这场比赛有已发布推荐"的存在性(不含方向/赔率/标题),两个板块合计。
        # 公推本身就完全公开,把它的场次标出来不构成任何泄漏。
        assert 830040 in after_overview["published_match_ids"]
