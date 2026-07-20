"""会员权益投影 + HTTP/Cloudflare 缓存隔离——永久回归测试(本轮新增)。

背景(本轮审计,真实 TestClient 请求验证过,不是假设性加固):
- backend/api/cache_policy.py 新增前:大部分异常路径(401/403/404/422/500)完全没有
  Cache-Control;/api/v1/products 等"公开"端点对携带 Cookie/Authorization 的请求
  同样返回 public 缓存头(未检查凭证);backend/api_server.py 的 4 个 legacy 端点
  从不设置任何 Cache-Control。
- backend/api/routes_public.py 的 match_analysis 在免费层下,counter_evidence 里
  的 draw_risk 条目、以及由它预渲染出的 "risk" script_section 文本会原样携带真实
  平局概率,绕过 prediction:full_wdl 门禁(本轮已在 routes_public.py 修复)。

只用临时三库 fixture(data_dir/app/client 来自 conftest.py),不碰真实 data/*.db,
不依赖网络。Cache-Control 断言优先使用严格 `==`,不用子串包含。
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.cli.create_admin import create_admin
from backend.commands.predictions import get_or_create_model_version, publish_snapshot, register_snapshot
from backend.commands.subscriptions import grant_subscription
from backend.db.connections import connect_rw

from .coreseed import seed_basic_core

STRICT_NO_STORE = "private, no-store"


# ── 通用 helper ──────────────────────────────────────────────

def _login(client, ip):
    r1 = client.get("/api/v1/auth/wechat/oa/start?next=/", follow_redirects=False,
                     headers={"x-real-ip": ip})
    client.get(r1.headers["location"], follow_redirects=False)
    return client.get("/api/v1/me").json()["user"]["id"]


def _grant(user_id, plan_id, days=30):
    conn = connect_rw("platform")
    grant_subscription(conn, user_id, plan_id, days, granted_by=None, source="admin_grant")
    conn.close()


def _pro_client(app, ip):
    c = TestClient(app)
    uid = _login(c, ip)
    _grant(uid, "pro")
    return c


def _premium_client(app, ip):
    c = TestClient(app)
    uid = _login(c, ip)
    _grant(uid, "premium")
    return c


def _admin_free_client(app, ip, username="audit-admin"):
    """管理员角色 + 无任何订阅(=free plan)。用于证明 role≠entitlement。"""
    conn = connect_rw("platform")
    create_admin(conn, username, "audit-admin-pw-1", reset=True)
    conn.close()
    c = TestClient(app)
    r = c.post("/api/v1/auth/password/login", json={"username": username, "password": "audit-admin-pw-1"},
               headers={"x-real-ip": ip})
    assert r.status_code == 200
    return c


def _seed_prediction(match_id, home_win, draw, away_win, kickoff="2027-04-01T14:30:00Z"):
    conn = connect_rw("platform")
    get_or_create_model_version(conn, "m-cache", "dixon-coles")
    sid = register_snapshot(
        conn, match_id=match_id, kickoff_at_utc=kickoff,
        kickoff_precision="exact", kickoff_source="fotmob:fixtures",
        model_version_id="m-cache", home_win=home_win, draw=draw, away_win=away_win,
        expected_home_goals=1.5, expected_away_goals=1.1, status="draft",
    )
    publish_snapshot(conn, sid, actor=None)
    conn.close()


def _insert_xref(conn, provider_match_id, fotmob_match_id, status="auto_ok"):
    conn.execute(
        """INSERT INTO dim_match_xref
           (fotmob_match_id, provider, provider_match_id, home_away_inverted, confidence,
            verified, method, kickoff_diff_seconds, review_status, created_at, updated_at)
           VALUES (?, 'nowgoal', ?, 0, 1.0, 1, 'manual', NULL, ?, '2026-07-19T00:00:00Z',
                   '2026-07-19T00:00:00Z')""",
        (fotmob_match_id, provider_match_id, status),
    )


def _insert_odds_snap(conn, provider_match_id, observed_at, sentinel, market="1x2", company_id="8"):
    conn.execute(
        """INSERT INTO bronze_ng_odds_snap
           (provider_match_id, market, company_id, company_name, market_phase, payload_json,
            payload_hash, source_updated_at, observed_at, ingested_at, poll_run_id)
           VALUES (?, ?, ?, 'TestCo', 'pre_match', ?, ?, NULL, ?, ?, 'r1')""",
        (provider_match_id, market, company_id, json.dumps({"sentinel": sentinel}), sentinel,
         observed_at, observed_at),
    )


def _recursive_scan(obj, needle: str, path="$") -> list[str]:
    """递归扫描任意深度的 dict/list,返回命中位置——用于全 JSON 泄漏检测,
    不能只查看顶层已知字段。"""
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            hits += _recursive_scan(v, needle, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits += _recursive_scan(v, needle, f"{path}[{i}]")
    else:
        if needle in str(obj):
            hits.append(path)
    return hits


# ── A. 会员权益投影矩阵 ──────────────────────────────────────

class TestEntitlementMatrixLeagueAccess:
    """A.1 / A.2:英超免费可见,非英超 top5 仅 pro/premium;admin 角色本身不授予。"""

    def test_anonymous_and_free_epl_ok_top5_denied(self, app, data_dir, fresh_ip):
        seed_basic_core(data_dir)
        client = TestClient(app)
        assert client.get("/api/v1/leagues/47/standings").status_code == 200
        r = client.get("/api/v1/matches/9101")   # 西甲,top5-only
        assert r.status_code == 401

        logged_in = TestClient(app)
        _login(logged_in, fresh_ip)
        assert logged_in.get("/api/v1/leagues/47/standings").status_code == 200
        r2 = logged_in.get("/api/v1/matches/9101")
        assert r2.status_code == 403   # 已登录但 free → 403(不是 401)

    def test_pro_and_premium_get_top5(self, app, data_dir, fresh_ip):
        seed_basic_core(data_dir)
        pro = _pro_client(app, fresh_ip)
        assert pro.get("/api/v1/matches/9101").status_code == 200
        premium = _premium_client(app, f"{fresh_ip}-b")
        assert premium.get("/api/v1/matches/9101").status_code == 200

    def test_admin_role_alone_does_not_grant_top5(self, app, data_dir, fresh_ip):
        """CLAUDE.md §7/§8:admin 角色只管后台访问,不代替 premium/pro entitlement。"""
        seed_basic_core(data_dir)
        admin = _admin_free_client(app, fresh_ip)
        me = admin.get("/api/v1/me").json()
        assert me["user"]["role"] == "admin"
        assert me["plan"] == "free"
        assert "league:top5" not in me["entitlements"]
        r = admin.get("/api/v1/matches/9101")
        assert r.status_code == 403   # 已登录(admin)但 plan=free → 403,不是 200


class TestEntitlementMatrixPrediction:
    """A.3:免费层任意 JSON 深度只有 top_outcome/top_probability;pro/premium 完整三项;
    admin+free plan 仍是免费投影。"""

    SENTINELS = ("0.612", "0.239", "0.149", "61.2", "23.9", "14.9")

    @pytest.fixture
    def seeded(self, data_dir):
        seed_basic_core(data_dir)
        _seed_prediction(9001, 0.612, 0.239, 0.149)
        return data_dir

    def _assert_free_projection(self, r):
        assert r.status_code == 200
        body = r.json()
        assert body["prediction"]["tier"] == "free"
        assert body["prediction"]["top_outcome"] == "home"
        assert body["prediction"]["top_probability"] == 0.61
        raw = r.text
        for s in ("0.239", "0.149", "23.9", "14.9"):
            assert s not in raw, f"免费预测响应泄漏受限概率 {s}"
        assert r.headers["cache-control"] == STRICT_NO_STORE

    def test_anonymous_free_projection(self, app, seeded):
        self._assert_free_projection(TestClient(app).get("/api/v1/matches/9001/prediction"))

    def test_registered_free_same_as_anonymous(self, app, seeded, fresh_ip):
        c = TestClient(app)
        _login(c, fresh_ip)
        self._assert_free_projection(c.get("/api/v1/matches/9001/prediction"))

    def test_admin_with_free_plan_still_free_projection(self, app, seeded, fresh_ip):
        admin = _admin_free_client(app, fresh_ip)
        self._assert_free_projection(admin.get("/api/v1/matches/9001/prediction"))

    def test_pro_and_premium_get_full_wdl(self, app, seeded, fresh_ip):
        for maker, ip_suffix in ((_pro_client, "-p"), (_premium_client, "-m")):
            c = maker(app, f"{fresh_ip}{ip_suffix}")
            r = c.get("/api/v1/matches/9001/prediction")
            pred = r.json()["prediction"]
            assert pred["tier"] == "full"
            assert pred["home_probability"] == 0.612
            assert pred["draw_probability"] == 0.239
            assert pred["away_probability"] == 0.149
            assert r.headers["cache-control"] == STRICT_NO_STORE


class TestEntitlementMatrixAnalysisBundle:
    """A.4:analysis bundle 在任意嵌套位置(prediction_member/chart_specs/script_sections/
    counter_evidence/subtitle_cues 等)都不得向免费层泄漏完整概率;pro 有 report:deep
    与完整 WDL,但没有 premium 的 odds:history_full;premium 两者都有。"""

    HOME, DRAW, AWAY = 0.501, 0.317, 0.182   # draw>=0.28 触发 bundle.py 的 draw_risk 分支
    NUMERIC_SENTINELS = ("0.501", "0.317", "0.182", "50.1", "31.7", "18.2")

    @pytest.fixture
    def seeded(self, data_dir):
        seed_basic_core(data_dir)
        _seed_prediction(9001, self.HOME, self.DRAW, self.AWAY)
        conn = connect_rw("odds")
        _insert_xref(conn, "700001", 9001)
        conn.commit()
        conn.close()
        return data_dir

    def test_free_leaks_nothing_at_any_depth(self, app, seeded):
        r = TestClient(app).get("/api/v1/matches/9001/analysis")
        assert r.status_code == 200
        body = r.json()
        assert body["prediction_member"] is None
        raw = json.dumps(body, ensure_ascii=False)
        for s in self.NUMERIC_SENTINELS:
            hits = _recursive_scan(body, s)
            assert not hits, f"免费层 analysis bundle 在 {hits} 泄漏受限数值 {s}\n{raw[:500]}"
        assert "subtitle_cues" not in body   # 页面用不到,整体不下发
        assert r.headers["cache-control"] == STRICT_NO_STORE

    def test_pro_full_wdl_and_report_deep_but_not_full_odds(self, app, seeded, fresh_ip):
        c = _pro_client(app, fresh_ip)
        r = c.get("/api/v1/matches/9001/analysis")
        body = r.json()
        assert body["prediction_member"]["home_probability"] == self.HOME
        assert body["prediction_member"]["draw_probability"] == self.DRAW
        assert any(cs["type"] == "probability_bar" for cs in body["chart_specs"])
        assert body["odds_timeline"] == []   # pro 没有 odds:history_full

    def test_premium_gets_full_odds_timeline(self, app, seeded, fresh_ip):
        conn = connect_rw("odds")
        _insert_odds_snap(conn, "700001", "2026-07-19T09:00:00Z", "PREMIUM_ODDS_SENTINEL_X1")
        conn.commit()
        conn.close()
        c = _premium_client(app, fresh_ip)
        body = c.get("/api/v1/matches/9001/analysis").json()
        assert len(body["odds_timeline"]) == 1
        assert body["odds_timeline"][0]["payload"]["sentinel"] == "PREMIUM_ODDS_SENTINEL_X1"

    def test_admin_with_free_plan_gets_free_projection(self, app, seeded, fresh_ip):
        admin = _admin_free_client(app, fresh_ip)
        body = admin.get("/api/v1/matches/9001/analysis").json()
        assert body["prediction_member"] is None
        for s in self.NUMERIC_SENTINELS:
            assert not _recursive_scan(body, s)


class TestEntitlementMatrixOdds:
    """A.5:free/pro 只有延迟摘要,premium 完整时间线——用"延迟阈值前/后"两条快照区分,
    不是只比较数组长度(数组长度相等也可能两者内容不同/都错)。"""

    @pytest.fixture
    def seeded(self, data_dir):
        seed_basic_core(data_dir)
        conn = connect_rw("odds")
        _insert_xref(conn, "700002", 9001)
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        fresh = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        _insert_odds_snap(conn, "700002", old, "OLD_BEFORE_THRESHOLD_S1")
        conn.commit()
        conn.close()
        return old, fresh

    def _add_fresh_snapshot(self, fresh_ts):
        conn = connect_rw("odds")
        _insert_odds_snap(conn, "700002", fresh_ts, "FRESH_AFTER_THRESHOLD_S2")
        conn.commit()
        conn.close()

    def test_free_and_pro_only_see_delayed_summary(self, app, seeded, fresh_ip):
        _old, fresh = seeded
        self._add_fresh_snapshot(fresh)
        anon = TestClient(app)
        pro = _pro_client(app, fresh_ip)
        for c in (anon, pro):
            r = c.get("/api/v1/matches/9001/odds")
            assert r.status_code == 200
            body = r.json()
            assert body["tier"] == "delayed_summary"
            raw = r.text
            assert "OLD_BEFORE_THRESHOLD_S1" in raw
            assert "FRESH_AFTER_THRESHOLD_S2" not in raw, "免费/pro 不应看到 1 小时内的新鲜快照"
            assert r.headers["cache-control"] == STRICT_NO_STORE

    def test_premium_sees_full_timeline_including_fresh_snapshot(self, app, seeded, fresh_ip):
        _old, fresh = seeded
        self._add_fresh_snapshot(fresh)
        premium = _premium_client(app, fresh_ip)
        r = premium.get("/api/v1/matches/9001/odds")
        body = r.json()
        assert body["tier"] == "full"
        sentinels = {s["payload"]["sentinel"] for s in body["snapshots"]}
        assert sentinels == {"OLD_BEFORE_THRESHOLD_S1", "FRESH_AFTER_THRESHOLD_S2"}


class TestEntitlementMatrixCooccurrence:
    """A.6:free/pro-without-report:deep 见计数或 null;report:deep(pro/premium)见明细;
    admin+free 不因角色自动拿到明细。"""

    @pytest.fixture
    def seeded(self, data_dir):
        seed_basic_core(data_dir)
        conn = connect_rw("odds")
        _insert_xref(conn, "700003", 9001)
        conn.execute(
            "INSERT INTO silver_odds_moves (fotmob_match_id, provider, company_id, market, field,"
            " prev_value, new_value, to_snapshot_id, moved_at, created_at)"
            " VALUES (9001,'nowgoal','8','1x2','home','2.05','1.95',1,'2026-07-19T10:30:00Z','2026-07-19T10:30:00Z')"
        )
        conn.execute(
            "INSERT INTO silver_event_moves (fotmob_match_id, event_type, detail_json, to_snapshot_id,"
            " moved_at, created_at)"
            " VALUES (9001,'lineup_change','{\"sentinel\":\"COOC_DETAIL_SENTINEL\"}',1,'2026-07-19T10:40:00Z','2026-07-19T10:40:00Z')"
        )
        conn.execute(
            "INSERT INTO gold_move_cooccurrence (fotmob_match_id, odds_move_id, event_move_id,"
            " window_seconds, delta_seconds, computed_at) VALUES (9001,1,1,3600,600,'2026-07-19T10:40:00Z')"
        )
        conn.commit()
        conn.close()
        return data_dir

    def test_anonymous_and_free_get_count_only(self, app, seeded):
        r = TestClient(app).get("/api/v1/matches/9001/cooccurrence")
        body = r.json()
        assert body["count"] == 1
        assert body["items"] is None
        assert "COOC_DETAIL_SENTINEL" not in r.text
        assert r.headers["cache-control"] == STRICT_NO_STORE

    def test_pro_and_premium_get_detail(self, app, seeded, fresh_ip):
        for maker, suffix in ((_pro_client, "-p"), (_premium_client, "-m")):
            c = maker(app, f"{fresh_ip}{suffix}")
            body = c.get("/api/v1/matches/9001/cooccurrence").json()
            assert body["items"] is not None
            assert body["items"][0]["detail_json"] and "COOC_DETAIL_SENTINEL" in body["items"][0]["detail_json"]

    def test_admin_with_free_plan_does_not_auto_get_detail(self, app, seeded, fresh_ip):
        admin = _admin_free_client(app, fresh_ip)
        body = admin.get("/api/v1/matches/9001/cooccurrence").json()
        assert body["items"] is None


class TestEntitlementMatrixSubscriptionState:
    """A.7:只有 active 且未到期的订阅计数;expired/revoked/future-start 不计入;
    多条有效订阅取 rank 最高的 plan;Role 与 Entitlement 正交。"""

    def _me(self, c):
        return c.get("/api/v1/me").json()

    def test_expired_subscription_does_not_count(self, app, data_dir, fresh_ip):
        c = TestClient(app)
        uid = _login(c, fresh_ip)
        _grant(uid, "pro")
        conn = connect_rw("platform")
        conn.execute("UPDATE subscriptions SET ends_at='2020-01-01T00:00:00Z' WHERE user_id=?", (uid,))
        conn.close()
        assert self._me(c)["plan"] == "free"

    def test_revoked_subscription_does_not_count(self, app, data_dir, fresh_ip):
        c = TestClient(app)
        uid = _login(c, fresh_ip)
        _grant(uid, "premium")
        conn = connect_rw("platform")
        conn.execute("UPDATE subscriptions SET status='revoked' WHERE user_id=?", (uid,))
        conn.close()
        assert self._me(c)["plan"] == "free"

    def test_future_start_subscription_does_not_count_yet(self, app, data_dir, fresh_ip):
        c = TestClient(app)
        uid = _login(c, fresh_ip)
        far_future_start = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        far_future_end = (datetime.now(timezone.utc) + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn = connect_rw("platform")
        conn.execute(
            "INSERT INTO subscriptions (id, user_id, plan_id, status, starts_at, ends_at, source, created_at)"
            " VALUES ('future-sub', ?, 'premium', 'active', ?, ?, 'admin_grant', ?)",
            (uid, far_future_start, far_future_end, far_future_start),
        )
        conn.close()
        assert self._me(c)["plan"] == "free"

    def test_multiple_valid_subscriptions_resolve_to_highest_rank(self, app, data_dir, fresh_ip):
        c = TestClient(app)
        uid = _login(c, fresh_ip)
        _grant(uid, "pro")
        _grant(uid, "premium")
        assert self._me(c)["plan"] == "premium"

    def test_role_and_entitlement_orthogonal(self, app, data_dir, fresh_ip):
        """admin 角色 + premium 订阅同时存在时,plan/entitlements 只由订阅决定,
        role 字段与其并列存在,互不改写对方。"""
        conn = connect_rw("platform")
        create_admin(conn, "orth-admin", "orth-admin-pw-1")
        conn.close()
        c = TestClient(app)
        r = c.post("/api/v1/auth/password/login",
                    json={"username": "orth-admin", "password": "orth-admin-pw-1"},
                    headers={"x-real-ip": fresh_ip})
        assert r.status_code == 200
        uid = c.get("/api/v1/me").json()["user"]["id"]
        _grant(uid, "premium")
        me = self._me(c)
        assert me["user"]["role"] == "admin"
        assert me["plan"] == "premium"
        assert "odds:history_full" in me["entitlements"]


# ── B. HTTP/Cloudflare 缓存隔离矩阵 ───────────────────────────

class TestCacheMatrixPublicWhitelist:
    def test_anonymous_public_endpoint_gets_shared_cache_no_set_cookie(self, app, data_dir):
        r = TestClient(app).get("/api/v1/products")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "public, s-maxage=300, stale-while-revalidate=60"
        assert "set-cookie" not in {k.lower() for k in r.headers.keys()}


class TestCacheMatrixCredentialsForcePrivate:
    """同一公开白名单路径,只要请求带任何凭证(有效/无效 session、CSRF cookie、
    Authorization),一律被强制降级为 private, no-store。"""

    def test_valid_session_cookie_forces_private(self, app, data_dir, fresh_ip):
        c = TestClient(app)
        _login(c, fresh_ip)
        r = c.get("/api/v1/products")
        assert r.headers["cache-control"] == STRICT_NO_STORE

    def test_invalid_session_cookie_forces_private(self, app, data_dir):
        r = TestClient(app).get("/api/v1/products", headers={"Cookie": "allwin_session=totally-bogus-token"})
        assert r.headers["cache-control"] == STRICT_NO_STORE

    def test_csrf_only_cookie_forces_private(self, app, data_dir):
        r = TestClient(app).get("/api/v1/products", headers={"Cookie": "allwin_csrf=some-csrf-value"})
        assert r.headers["cache-control"] == STRICT_NO_STORE

    def test_authorization_header_forces_private(self, app, data_dir):
        r = TestClient(app).get("/api/v1/products", headers={"Authorization": "Bearer arbitrary-token"})
        assert r.headers["cache-control"] == STRICT_NO_STORE


class TestCacheMatrixEntitlementVaryingAlwaysPrivate:
    @pytest.fixture
    def seeded(self, data_dir):
        seed_basic_core(data_dir)
        _seed_prediction(9001, 0.4, 0.3, 0.3)
        return data_dir

    @pytest.mark.parametrize("path", [
        "/api/v1/matches/9001/prediction",
        "/api/v1/matches/9001/analysis",
        "/api/v1/matches/9001/odds",
        "/api/v1/matches/9001/cooccurrence",
    ])
    def test_all_tiers_get_no_store(self, app, seeded, fresh_ip, path):
        clients = [
            TestClient(app),
            _pro_client(app, f"{fresh_ip}-{path[-3:]}-p"),
            _premium_client(app, f"{fresh_ip}-{path[-3:]}-m"),
        ]
        for c in clients:
            r = c.get(path)
            assert r.headers["cache-control"] == STRICT_NO_STORE, f"{path} 未强制 no-store"


class TestCacheMatrixMemberAdminAuthAlwaysPrivate:
    def test_auth_endpoints_success_and_failure(self, app, data_dir, fresh_ip):
        c = TestClient(app)
        r_start = c.get("/api/v1/auth/wechat/oa/start", follow_redirects=False,
                         headers={"x-real-ip": fresh_ip})
        assert r_start.headers["cache-control"] == STRICT_NO_STORE
        r_bad = c.get("/api/v1/auth/wechat/oa/callback?code=x&state=forged", follow_redirects=False)
        assert r_bad.status_code == 400
        assert r_bad.headers["cache-control"] == STRICT_NO_STORE

    def test_member_endpoints_success_and_failure(self, app, data_dir, fresh_ip):
        c = TestClient(app)
        _login(c, fresh_ip)
        ok = c.get("/api/v1/account")
        assert ok.headers["cache-control"] == STRICT_NO_STORE
        anon = TestClient(app)
        fail = anon.get("/api/v1/account")
        assert fail.status_code == 401
        assert fail.headers["cache-control"] == STRICT_NO_STORE

    def test_admin_endpoints_success_and_failure(self, app, data_dir, fresh_ip):
        admin = _admin_free_client(app, fresh_ip)
        ok = admin.get("/api/v1/admin/users")
        assert ok.headers["cache-control"] == STRICT_NO_STORE
        anon = TestClient(app)
        fail = anon.get("/api/v1/admin/users")
        assert fail.status_code == 401
        assert fail.headers["cache-control"] == STRICT_NO_STORE


class TestCacheMatrixErrorResponses:
    def test_404_private(self, app, data_dir):
        r = TestClient(app).get("/api/v1/matches/999999999")
        assert r.status_code == 404
        assert r.headers["cache-control"] == STRICT_NO_STORE
        assert set(r.json().keys()) == {"code", "message", "details"}

    def test_422_private(self, app, data_dir):
        r = TestClient(app).get("/api/v1/leagues/not-an-int/standings")
        assert r.status_code == 422
        assert r.headers["cache-control"] == STRICT_NO_STORE
        assert set(r.json().keys()) == {"code", "message", "details"}

    def test_401_private(self, app, data_dir):
        r = TestClient(app).get("/api/v1/account")
        assert r.status_code == 401
        assert r.headers["cache-control"] == STRICT_NO_STORE

    def test_403_private(self, app, data_dir, fresh_ip):
        c = TestClient(app)
        _login(c, fresh_ip)
        r = c.get("/api/v1/admin/users")
        assert r.status_code == 403
        assert r.headers["cache-control"] == STRICT_NO_STORE

    def test_429_private(self, app, data_dir, fresh_ip):
        c = TestClient(app)
        for _ in range(5):
            c.post("/api/v1/auth/password/login", json={"username": "nobody", "password": "x"},
                   headers={"x-real-ip": fresh_ip})
        r = c.post("/api/v1/auth/password/login", json={"username": "nobody", "password": "x"},
                   headers={"x-real-ip": fresh_ip})
        assert r.status_code == 429
        assert r.headers["cache-control"] == STRICT_NO_STORE
        assert set(r.json().keys()) == {"code", "message", "details"}

    def test_500_private_and_body_still_unified(self, app, data_dir, monkeypatch):
        import backend.api.routes_public as rp

        def _boom(conn, match_id):
            raise RuntimeError("boom-for-cache-test")

        monkeypatch.setattr(rp.q_matches, "match_by_id", _boom)
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/api/v1/matches/1")
        assert r.status_code == 500
        assert r.headers["cache-control"] == STRICT_NO_STORE
        assert set(r.json().keys()) == {"code", "message", "details"}
        assert "boom-for-cache-test" not in r.text

    def test_auth_disabled_503_private(self, data_dir):
        from backend.api.app import create_app

        from .conftest import make_settings

        settings = make_settings(WECHAT_AUTH_PROVIDER="real", WECHAT_AUTH_ENABLED="0")
        c = TestClient(create_app(settings))
        r = c.get("/api/v1/auth/wechat/oa/start?next=/", follow_redirects=False)
        assert r.status_code == 503
        assert r.headers["cache-control"] == STRICT_NO_STORE


class TestCacheMatrixSetCookieResponses:
    def test_oauth_callback_login_no_store(self, app, data_dir, fresh_ip):
        c = TestClient(app)
        r1 = c.get("/api/v1/auth/wechat/oa/start?next=/", follow_redirects=False,
                    headers={"x-real-ip": fresh_ip})
        r2 = c.get(r1.headers["location"], follow_redirects=False)
        assert "allwin_session" in r2.cookies
        assert r2.headers["cache-control"] == STRICT_NO_STORE

    def test_password_login_no_store(self, app, data_dir, fresh_ip):
        conn = connect_rw("platform")
        create_admin(conn, "cookie-test-admin", "cookie-test-pw-1")
        conn.close()
        c = TestClient(app)
        r = c.post("/api/v1/auth/password/login",
                    json={"username": "cookie-test-admin", "password": "cookie-test-pw-1"},
                    headers={"x-real-ip": fresh_ip})
        assert "allwin_session" in r.cookies
        assert r.headers["cache-control"] == STRICT_NO_STORE

    def test_logout_no_store(self, app, data_dir, fresh_ip):
        c = TestClient(app)
        _login(c, fresh_ip)
        csrf = c.cookies.get("allwin_csrf")
        r = c.post("/api/v1/auth/logout", headers={"Origin": "http://localhost:3000",
                                                    "X-CSRF-Token": csrf})
        assert r.status_code == 200
        assert r.headers["cache-control"] == STRICT_NO_STORE


class TestCacheMatrixLegacyBothApps:
    """B.7:legacy /api/league/* 在主 app(挂载路由对象)与独立 api_server.app
    两种运行方式下,都不得进入共享缓存——覆盖 200/401/422。"""

    def _legacy_core(self, data_dir, monkeypatch, mod):
        conn = connect_rw("core")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS dim_team_i18n (Team_ID INTEGER PRIMARY KEY, name_zh TEXT);
            CREATE TABLE IF NOT EXISTS dim_player_i18n (Player_ID TEXT PRIMARY KEY, name_zh TEXT, name_zh_short TEXT);
            CREATE TABLE IF NOT EXISTS fact_league_table (
                League_ID INTEGER, Season TEXT, table_type TEXT, Team_ID INTEGER,
                position INTEGER, played INTEGER, wins INTEGER, draws INTEGER, losses INTEGER,
                goals_for INTEGER, goals_against INTEGER, goal_diff INTEGER, points INTEGER, qual_color TEXT);
            CREATE TABLE IF NOT EXISTS fact_season_player_stats (
                League_ID INTEGER, Season TEXT, stat_name TEXT, Player_ID TEXT, Player_Name TEXT,
                Team_ID INTEGER, Team_Name TEXT, rank INTEGER, value REAL);
            CREATE TABLE IF NOT EXISTS silver_league_season_summary (
                League_ID INTEGER, Season TEXT, total_matches INTEGER,
                home_win_pct REAL, draw_pct REAL, away_win_pct REAL, avg_total_goals REAL);
            CREATE TABLE IF NOT EXISTS silver_team_season_stats (
                League_ID INTEGER, Season TEXT, Team_ID INTEGER, matches_played INTEGER,
                avg_total_shots REAL, avg_shots_on_target REAL, avg_possession REAL,
                avg_expected_goals REAL, avg_expected_goals_on_target REAL,
                avg_corners REAL, avg_yellow_cards REAL, avg_red_cards REAL,
                clean_sheets INTEGER, btts_matches INTEGER, btts_pct REAL);
            CREATE TABLE IF NOT EXISTS silver_over_under_thresholds (
                League_ID INTEGER, Season TEXT, threshold REAL,
                over_count INTEGER, under_count INTEGER, over_pct REAL, under_pct REAL);
            CREATE TABLE IF NOT EXISTS silver_score_distribution (
                League_ID INTEGER, Season TEXT, home_score INTEGER, away_score INTEGER,
                match_count INTEGER, pct REAL);
            CREATE TABLE IF NOT EXISTS silver_goal_minute_buckets (
                League_ID INTEGER, Season TEXT, bucket TEXT, goal_count INTEGER, pct REAL);
            CREATE TABLE IF NOT EXISTS gold_wdl_predictions (
                match_id INTEGER PRIMARY KEY, league_id INTEGER, season TEXT,
                p_home REAL, p_draw REAL, p_away REAL, confidence TEXT, reason TEXT);
            """
        )
        conn.execute(
            "INSERT INTO dim_match (Match_ID, Season, League_ID, Date, Home_Team_ID, Away_Team_ID,"
            " Home_Team_Name, Away_Team_Name, home_score, away_score, status, Match_Round)"
            " VALUES (1,'2025/2026',47,'2026-01-01',100,200,'A','B',2,1,'Finish',NULL)"
        )
        conn.commit()
        conn.close()
        from backend.db.paths import db_path

        monkeypatch.setattr(mod, "DB_PATH", db_path("core"))

    def test_main_app_legacy_never_shared_cacheable(self, app, data_dir, monkeypatch):
        import backend.api_server as legacy_mod

        self._legacy_core(data_dir, monkeypatch, legacy_mod)
        c = TestClient(app)
        ok = c.get("/api/league/47/overview?season=2025/2026")
        assert ok.status_code == 200
        assert ok.headers["cache-control"] == STRICT_NO_STORE
        unauth = c.get("/api/league/47/betting")
        assert unauth.status_code == 401
        assert unauth.headers["cache-control"] == STRICT_NO_STORE
        bad = c.get("/api/league/not-an-int/overview")
        assert bad.status_code == 422
        assert bad.headers["cache-control"] == STRICT_NO_STORE

    def test_standalone_app_legacy_never_shared_cacheable(self, data_dir, monkeypatch):
        import backend.api_server as legacy_mod

        self._legacy_core(data_dir, monkeypatch, legacy_mod)
        c = TestClient(legacy_mod.app, raise_server_exceptions=False)
        ok = c.get("/api/league/47/overview?season=2025/2026")
        assert ok.status_code == 200
        assert ok.headers["cache-control"] == STRICT_NO_STORE
        unauth = c.get("/api/league/47/betting")
        assert unauth.status_code == 401
        assert unauth.headers["cache-control"] == STRICT_NO_STORE
        bad = c.get("/api/league/not-an-int/overview")
        assert bad.status_code == 422
        assert bad.headers["cache-control"] == STRICT_NO_STORE


class TestCacheMatrixUnclassifiedDefaultsToNoStore:
    """B.8:未分类/新增路径默认 no-store——用一个当场注册、完全不设置任何
    Cache-Control 的临时路由,证明这是中间件的兜底效果,不是这个端点自己写对了。"""

    def test_brand_new_route_without_any_header_defaults_private(self, app, data_dir):
        @app.get("/api/v1/__test_only_unclassified_route")
        def _unclassified():
            return {"ok": True}

        r = TestClient(app).get("/api/v1/__test_only_unclassified_route")
        assert r.status_code == 200
        assert r.headers["cache-control"] == STRICT_NO_STORE
