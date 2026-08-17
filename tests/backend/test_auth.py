"""认证安全测试(带参数二维码 + webhook 路线,2026-08 起):

webhook 签名/时间窗/nonce 防重放、扫码批准(SCAN + subscribe)、Device Login
一次性 secret/原子领取、CSRF、production fail-fast(含 WECHAT_WEBHOOK_TOKEN)、
cookie 属性、no-store、限流、access_token 缓存(httpx.MockTransport 离线)。
"""

import time

import pytest
from fastapi.testclient import TestClient

from backend.auth.config import AuthConfigError, load_auth_settings

from .authflow import (
    DEV_WEBHOOK_TOKEN,
    post_scan,
    scan_event_xml,
    signed_webhook_params,
    wechat_scan_login,
)
from .conftest import BASE_ENV, make_settings

ORIGIN = {"Origin": "http://localhost:3000"}

# production + WECHAT_AUTH_ENABLED=0:无任何微信凭证(CLAUDE.md §7.3 三态之一)
PROD_DISABLED_ENV = dict(
    BASE_ENV,
    APP_ENV="production",
    WECHAT_AUTH_PROVIDER="real",
    WECHAT_AUTH_ENABLED="0",
    PUBLIC_BASE_URL="https://testserver",   # production Cookie 恒 Secure,走 https base
)


@pytest.fixture
def disabled_client(data_dir):
    """production + ENABLED=0(real、无凭证)的应用客户端。"""
    from backend.api.app import create_app

    settings = load_auth_settings(PROD_DISABLED_ENV)
    return TestClient(create_app(settings), base_url="https://testserver")


def _create_device(client, ip):
    r = client.post("/api/v1/auth/wechat/device", headers={"x-real-ip": ip})
    assert r.status_code == 200
    return r.json()


# ── webhook 校验层 ─────────────────────────────────────────

class TestWebhookVerification:
    def test_get_echo_with_valid_signature(self, client):
        params = signed_webhook_params()
        r = client.get(
            "/api/v1/auth/wechat/webhook",
            params={**params, "echostr": "echo-me-123"},
        )
        assert r.status_code == 200
        assert r.text == "echo-me-123"
        assert r.headers["cache-control"] == "private, no-store"

    def test_get_echo_with_bad_signature_403(self, client):
        params = signed_webhook_params()
        params["signature"] = "0" * 40
        r = client.get(
            "/api/v1/auth/wechat/webhook", params={**params, "echostr": "x"}
        )
        assert r.status_code == 403

    def test_post_bad_signature_403(self, client, fresh_ip):
        req = _create_device(client, fresh_ip)
        params = signed_webhook_params(token="wrong-token")
        r = post_scan(client, req["request_id"], "mock-openid-a", params=params)
        assert r.status_code == 403
        # 未被批准:claim 仍 pending
        r2 = client.post(
            f"/api/v1/auth/wechat/device/{req['request_id']}/claim",
            json={"secret": req["secret"]},
        )
        assert r2.json()["status"] == "pending"

    def test_post_stale_timestamp_403(self, client, fresh_ip):
        from backend.auth.wechat_webhook import compute_signature

        req = _create_device(client, fresh_ip)
        stale_ts = str(int(time.time()) - 3600)
        nonce = "stale-nonce-1"
        params = {
            "signature": compute_signature(DEV_WEBHOOK_TOKEN, stale_ts, nonce),
            "timestamp": stale_ts,
            "nonce": nonce,
        }
        r = post_scan(client, req["request_id"], "mock-openid-a", params=params)
        assert r.status_code == 403

    def test_nonce_replay_returns_success_without_side_effects(self, client, fresh_ip):
        """微信同一请求重试(同 nonce)→ 200 success,不二次处理。"""
        req = _create_device(client, fresh_ip)
        params = signed_webhook_params()
        r1 = post_scan(client, req["request_id"], "mock-openid-a", params=params)
        assert r1.status_code == 200
        assert "登录成功" in r1.text
        # 同一签名整体重放
        r2 = post_scan(client, req["request_id"], "mock-openid-a", params=params)
        assert r2.status_code == 200
        assert r2.text == "success"

    def test_junk_xml_acknowledged_not_crashing(self, client):
        r = client.post(
            "/api/v1/auth/wechat/webhook",
            params=signed_webhook_params(),
            content=b"this is not xml",
            headers={"Content-Type": "application/xml"},
        )
        assert r.status_code == 200
        assert r.text == "success"

    def test_oversized_body_rejected(self, client):
        r = client.post(
            "/api/v1/auth/wechat/webhook",
            params=signed_webhook_params(),
            content=b"<xml>" + b"a" * (70 * 1024) + b"</xml>",
            headers={"Content-Type": "application/xml"},
        )
        assert r.status_code == 400

    def test_non_login_event_acknowledged(self, client):
        """无场景值的普通事件(如取关)→ success,零副作用。"""
        xml = (
            "<xml><ToUserName><![CDATA[gh_x]]></ToUserName>"
            "<FromUserName><![CDATA[openid-x]]></FromUserName>"
            "<CreateTime>123</CreateTime><MsgType><![CDATA[event]]></MsgType>"
            "<Event><![CDATA[unsubscribe]]></Event></xml>"
        )
        r = client.post(
            "/api/v1/auth/wechat/webhook",
            params=signed_webhook_params(),
            content=xml,
            headers={"Content-Type": "application/xml"},
        )
        assert r.status_code == 200
        assert r.text == "success"


# ── 扫码登录全流程 ─────────────────────────────────────────

class TestScanLogin:
    def test_full_scan_login_flow(self, client, fresh_ip):
        wechat_scan_login(client, ip=fresh_ip)
        body = client.get("/api/v1/me").json()
        assert body["authenticated"] is True
        assert body["plan"] == "member"     # 三段可见性:登录即 member 基线
        assert "prediction:top_probability" in body["entitlements"]
        assert "prediction:full_wdl" in body["entitlements"]
        # 0010:战绩归档登录即可(reco:track_record 属 member 基线);
        # 赛前推荐内容(reco:daily)仍是付费专属
        assert "reco:track_record" in body["entitlements"]
        assert "reco:daily" not in body["entitlements"]

    def test_qr_url_contains_no_secret(self, client, fresh_ip):
        req = _create_device(client, fresh_ip)
        assert req["secret"] not in req["qr_url"]
        assert req["qr_url"]           # mock provider 也必须给出可渲染内容
        assert req["request_id"] in req["qr_url"]

    def test_subscribe_event_with_qrscene_prefix_approves(self, client, fresh_ip):
        """未关注用户扫码后关注:EventKey=qrscene_<scene_str> 同样批准。"""
        req = _create_device(client, fresh_ip)
        r = post_scan(client, req["request_id"], "mock-openid-new", event="subscribe")
        assert r.status_code == 200
        assert "登录成功" in r.text
        r2 = client.post(
            f"/api/v1/auth/wechat/device/{req['request_id']}/claim",
            json={"secret": req["secret"]},
        )
        assert r2.json()["status"] == "claimed"

    def test_unknown_scene_str_polite_reply_no_side_effect(self, client):
        r = post_scan(client, "no-such-request-id", "mock-openid-a")
        assert r.status_code == 200
        assert "无效" in r.text

    def test_expired_request_rejected(self, client, fresh_ip, data_dir):
        from backend.db.connections import connect_rw

        req = _create_device(client, fresh_ip)
        conn = connect_rw("platform")
        conn.execute(
            "UPDATE device_login_requests SET expires_at='2020-01-01T00:00:00Z' WHERE id=?",
            (req["request_id"],),
        )
        conn.close()
        r = post_scan(client, req["request_id"], "mock-openid-a")
        assert r.status_code == 200
        assert "过期" in r.text

    def test_rescan_after_approve_is_idempotent(self, client, fresh_ip):
        req = _create_device(client, fresh_ip)
        r1 = post_scan(client, req["request_id"], "mock-openid-a")
        assert "登录成功" in r1.text
        r2 = post_scan(client, req["request_id"], "mock-openid-b")   # 他人再扫
        assert "已确认" in r2.text
        # 批准者不变:领取后会话属于第一个 openid 的用户
        client.post(
            f"/api/v1/auth/wechat/device/{req['request_id']}/claim",
            json={"secret": req["secret"]},
        )
        me = client.get("/api/v1/me").json()
        assert me["authenticated"] is True

    def test_same_openid_reuses_same_user(self, client, fresh_ip, data_dir):
        wechat_scan_login(client, openid="mock-openid-stable", ip=fresh_ip)
        uid1 = client.get("/api/v1/me").json()["user"]["id"]
        csrf = client.cookies.get("allwin_csrf")
        client.post("/api/v1/auth/logout", headers={**ORIGIN, "X-CSRF-Token": csrf})
        wechat_scan_login(client, openid="mock-openid-stable", ip=fresh_ip)
        uid2 = client.get("/api/v1/me").json()["user"]["id"]
        assert uid1 == uid2

    def test_session_cookie_attributes(self, client, fresh_ip):
        r = wechat_scan_login(client, ip=fresh_ip)
        set_cookie = "; ".join(r.headers.get_list("set-cookie"))
        assert "HttpOnly" in set_cookie
        assert "SameSite=lax" in set_cookie
        assert "Path=/api/v1" in set_cookie
        assert "Domain=" not in set_cookie          # host-only
        # csrf cookie JS 可读(非 HttpOnly),Path=/
        assert "allwin_csrf" in set_cookie

    def test_secure_flag_in_production_style_settings(self):
        s = make_settings(COOKIE_SECURE="1")
        assert s.cookie_secure is True

    def test_no_store_on_auth_endpoints(self, client, fresh_ip):
        me = client.get("/api/v1/me")
        assert me.headers["cache-control"] == "private, no-store"
        r1 = client.post("/api/v1/auth/wechat/device", headers={"x-real-ip": fresh_ip})
        assert r1.headers["cache-control"] == "private, no-store"


# ── Device Login 一次性 secret / 原子领取(骨架保留) ───────

class TestDeviceLogin:
    def _approve(self, client, req):
        """模拟微信服务器投递扫码事件(scene_str 只含公开 request_id)。"""
        r = post_scan(client, req["request_id"], "mock-openid-device")
        assert r.status_code == 200
        assert "登录成功" in r.text
        # 批准动作不给任何一方种网站会话 cookie
        assert "allwin_session" not in r.cookies

    def test_full_device_flow(self, client, fresh_ip):
        req = _create_device(client, fresh_ip)
        # 未批准前轮询 → pending
        r = client.post(
            f"/api/v1/auth/wechat/device/{req['request_id']}/claim",
            json={"secret": req["secret"]},
        )
        assert r.json()["status"] == "pending"
        self._approve(client, req)
        r = client.post(
            f"/api/v1/auth/wechat/device/{req['request_id']}/claim",
            json={"secret": req["secret"]},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "claimed"
        assert "allwin_session" in r.cookies
        assert client.get("/api/v1/me").json()["authenticated"] is True

    def test_claim_without_correct_secret_forbidden(self, client, fresh_ip):
        req = _create_device(client, fresh_ip)
        self._approve(client, req)
        r = client.post(
            f"/api/v1/auth/wechat/device/{req['request_id']}/claim",
            json={"secret": "wrong"},
        )
        assert r.status_code == 403
        # 正确 secret 仍可领取(错误尝试不烧毁请求)
        r2 = client.post(
            f"/api/v1/auth/wechat/device/{req['request_id']}/claim",
            json={"secret": req["secret"]},
        )
        assert r2.json()["status"] == "claimed"

    def test_claim_only_once(self, client, fresh_ip):
        req = _create_device(client, fresh_ip)
        self._approve(client, req)
        first = client.post(
            f"/api/v1/auth/wechat/device/{req['request_id']}/claim",
            json={"secret": req["secret"]},
        )
        assert first.json()["status"] == "claimed"
        second = client.post(
            f"/api/v1/auth/wechat/device/{req['request_id']}/claim",
            json={"secret": req["secret"]},
        )
        assert second.status_code == 410

    def test_unknown_request_gone(self, client):
        r = client.post("/api/v1/auth/wechat/device/nope/claim", json={"secret": "x"})
        assert r.status_code == 410

    def test_qr_ticket_persisted(self, client, fresh_ip, data_dir):
        from backend.db.connections import connect_ro

        req = _create_device(client, fresh_ip)
        conn = connect_ro("platform")
        row = conn.execute(
            "SELECT qr_ticket, qr_url FROM device_login_requests WHERE id=?",
            (req["request_id"],),
        ).fetchone()
        conn.close()
        assert row["qr_ticket"] == f"mock-ticket-{req['request_id']}"
        assert row["qr_url"] == req["qr_url"]


# ── access_token 缓存与真实 Provider(httpx.MockTransport 离线) ──

class TestRealProviderAccessToken:
    def _provider_and_conn(self, data_dir, handler):
        import httpx

        from backend.auth.providers import RealWechatQrProvider
        from backend.db.connections import connect_rw

        provider = RealWechatQrProvider(
            "wx-test-app", "secret", transport=httpx.MockTransport(handler)
        )
        return provider, connect_rw("platform")

    def test_token_fetched_once_then_cached(self, data_dir):
        import httpx

        calls = {"token": 0}

        def handler(request):
            if "cgi-bin/token" in str(request.url):
                calls["token"] += 1
                return httpx.Response(200, json={"access_token": f"tok-{calls['token']}", "expires_in": 7200})
            raise AssertionError(f"unexpected url {request.url}")

        provider, conn = self._provider_and_conn(data_dir, handler)
        try:
            t1 = provider.get_access_token(conn)
            t2 = provider.get_access_token(conn)
            assert t1 == t2 == "tok-1"
            assert calls["token"] == 1
        finally:
            conn.close()

    def test_token_refreshed_when_near_expiry(self, data_dir):
        import httpx

        calls = {"token": 0}

        def handler(request):
            calls["token"] += 1
            # expires_in=60 < 刷新余量 300s → 每次都触发刷新
            return httpx.Response(200, json={"access_token": f"tok-{calls['token']}", "expires_in": 60})

        provider, conn = self._provider_and_conn(data_dir, handler)
        try:
            provider.get_access_token(conn)
            provider.get_access_token(conn)
            assert calls["token"] == 2
        finally:
            conn.close()

    def test_token_error_raises_with_errcode(self, data_dir):
        import httpx

        from backend.auth.providers import AuthProviderError

        def handler(request):
            return httpx.Response(200, json={"errcode": 40013, "errmsg": "invalid appid"})

        provider, conn = self._provider_and_conn(data_dir, handler)
        try:
            with pytest.raises(AuthProviderError) as ei:
                provider.get_access_token(conn)
            assert ei.value.errcode == 40013
        finally:
            conn.close()

    def test_qrcode_create_success(self, data_dir):
        import httpx

        def handler(request):
            url = str(request.url)
            if "cgi-bin/token" in url:
                return httpx.Response(200, json={"access_token": "tok", "expires_in": 7200})
            if "qrcode/create" in url:
                import json as _json

                body = _json.loads(request.content)
                assert body["action_name"] == "QR_STR_SCENE"
                assert body["action_info"]["scene"]["scene_str"] == "req-1"
                return httpx.Response(200, json={
                    "ticket": "TICKET1", "expire_seconds": 300,
                    "url": "http://weixin.qq.com/q/abc",
                })
            raise AssertionError(url)

        provider, conn = self._provider_and_conn(data_dir, handler)
        try:
            qr = provider.create_login_qrcode(conn, "req-1", 300)
            assert qr.ticket == "TICKET1"
            assert qr.url == "http://weixin.qq.com/q/abc"
        finally:
            conn.close()

    def test_qrcode_create_retries_once_on_stale_token(self, data_dir):
        import httpx

        calls = {"token": 0, "qr": 0}

        def handler(request):
            url = str(request.url)
            if "cgi-bin/token" in url:
                calls["token"] += 1
                return httpx.Response(200, json={"access_token": f"tok-{calls['token']}", "expires_in": 7200})
            calls["qr"] += 1
            if calls["qr"] == 1:
                return httpx.Response(200, json={"errcode": 42001, "errmsg": "token expired"})
            return httpx.Response(200, json={"ticket": "T2", "expire_seconds": 300, "url": "u"})

        provider, conn = self._provider_and_conn(data_dir, handler)
        try:
            qr = provider.create_login_qrcode(conn, "req-2", 300)
            assert qr.ticket == "T2"
            assert calls["token"] == 2       # 失效后清缓存重取了一次
        finally:
            conn.close()

    def test_qrcode_48001_surfaces_errcode(self, data_dir):
        """年审过期单点:48001 结构化抛出,不做降级通道(本轮如实)。"""
        import httpx

        from backend.auth.providers import AuthProviderError

        def handler(request):
            url = str(request.url)
            if "cgi-bin/token" in url:
                return httpx.Response(200, json={"access_token": "tok", "expires_in": 7200})
            return httpx.Response(200, json={"errcode": 48001, "errmsg": "api unauthorized"})

        provider, conn = self._provider_and_conn(data_dir, handler)
        try:
            with pytest.raises(AuthProviderError) as ei:
                provider.create_login_qrcode(conn, "req-3", 300)
            assert ei.value.errcode == 48001
        finally:
            conn.close()

    def test_device_create_502_when_provider_fails(self, data_dir, fresh_ip):
        """QR 创建失败 → 502,request 留给 TTL 过期,不半吊子返回。"""
        import httpx

        from backend.api.app import create_app
        from backend.auth.providers import RealWechatQrProvider

        def handler(request):
            return httpx.Response(200, json={"errcode": 48001, "errmsg": "api unauthorized"})

        settings = make_settings(
            WECHAT_AUTH_PROVIDER="real", WECHAT_AUTH_ENABLED="1",
            WECHAT_OA_APP_ID="wx-x", WECHAT_OA_APP_SECRET="s",
        )
        app = create_app(settings)
        app.state.wechat_provider = RealWechatQrProvider(
            "wx-x", "s", transport=httpx.MockTransport(handler)
        )
        c = TestClient(app)
        r = c.post("/api/v1/auth/wechat/device", headers={"x-real-ip": fresh_ip})
        assert r.status_code == 502


# ── CSRF / 登出 ────────────────────────────────────────────

class TestCsrfAndLogout:
    def test_logout_requires_csrf(self, client, fresh_ip):
        wechat_scan_login(client, ip=fresh_ip)
        # 无 Origin 无 CSRF → 403
        assert client.post("/api/v1/auth/logout").status_code == 403
        # 有 Origin 无 CSRF token → 403
        assert client.post("/api/v1/auth/logout", headers=ORIGIN).status_code == 403
        # Origin + 正确 CSRF → 200,会话撤销
        csrf = client.cookies.get("allwin_csrf")
        r = client.post(
            "/api/v1/auth/logout", headers={**ORIGIN, "X-CSRF-Token": csrf}
        )
        assert r.status_code == 200
        assert client.get("/api/v1/me").json()["authenticated"] is False

    def test_logout_without_session_401(self, client):
        assert client.post("/api/v1/auth/logout", headers=ORIGIN).status_code == 401

    def test_webhook_needs_no_csrf(self, client, fresh_ip):
        """webhook 是服务器对服务器通道:签名即凭证,不应要求 Cookie/CSRF。"""
        req = _create_device(client, fresh_ip)
        r = post_scan(client, req["request_id"], "mock-openid-csrf")
        assert r.status_code == 200


# ── production fail-fast ───────────────────────────────────

class TestProductionFailFast:
    def test_production_with_mock_provider_refuses(self):
        env = dict(BASE_ENV, APP_ENV="production", WECHAT_AUTH_PROVIDER="mock")
        with pytest.raises(AuthConfigError, match="Mock"):
            load_auth_settings(env)

    def test_production_enabled_missing_credentials_refuses(self):
        env = dict(
            BASE_ENV,
            APP_ENV="production",
            WECHAT_AUTH_PROVIDER="real",
            WECHAT_AUTH_ENABLED="1",
            PUBLIC_BASE_URL="https://allwin.example.com",
        )
        with pytest.raises(AuthConfigError, match="WECHAT_OA_APP_ID"):
            load_auth_settings(env)

    def test_production_enabled_missing_webhook_token_refuses(self):
        env = dict(
            BASE_ENV,
            APP_ENV="production",
            WECHAT_AUTH_PROVIDER="real",
            WECHAT_AUTH_ENABLED="1",
            WECHAT_OA_APP_ID="wx123",
            WECHAT_OA_APP_SECRET="s",
            PUBLIC_BASE_URL="https://allwin.example.com",
        )
        with pytest.raises(AuthConfigError, match="WECHAT_WEBHOOK_TOKEN"):
            load_auth_settings(env)

    def test_production_requires_https_base(self):
        env = dict(
            BASE_ENV,
            APP_ENV="production",
            WECHAT_AUTH_PROVIDER="real",
            WECHAT_AUTH_ENABLED="1",
            WECHAT_OA_APP_ID="wx123",
            WECHAT_OA_APP_SECRET="s",
            WECHAT_WEBHOOK_TOKEN="tok",
            PUBLIC_BASE_URL="http://insecure.example.com",
        )
        with pytest.raises(AuthConfigError, match="https"):
            load_auth_settings(env)

    def test_production_real_disabled_ok(self):
        env = dict(BASE_ENV, APP_ENV="production", WECHAT_AUTH_PROVIDER="real",
                   WECHAT_AUTH_ENABLED="0", PUBLIC_BASE_URL="https://allwin.example.com")
        s = load_auth_settings(env)
        assert s.cookie_secure is True

    def test_dev_webhook_token_default_not_in_production(self):
        """development 默认 dev-webhook-token;production 不给默认值(缺失即 fail-fast)。"""
        s = make_settings()
        assert s.wechat_webhook_token == "dev-webhook-token"
        env = dict(BASE_ENV, APP_ENV="production", WECHAT_AUTH_PROVIDER="real",
                   WECHAT_AUTH_ENABLED="0", PUBLIC_BASE_URL="https://allwin.example.com")
        assert load_auth_settings(env).wechat_webhook_token == ""


# ── 密码登录(admin)──────────────────────────────────────

class TestPasswordAdminLogin:
    def test_create_admin_and_login(self, client, data_dir, fresh_ip):
        from backend.cli.create_admin import create_admin
        from backend.db.connections import connect_rw

        conn = connect_rw("platform")
        create_admin(conn, "admin", "correct-horse-battery")
        conn.close()

        bad = client.post(
            "/api/v1/auth/password/login",
            json={"username": "admin", "password": "wrong"},
            headers={"x-real-ip": fresh_ip},
        )
        assert bad.status_code == 401
        ok = client.post(
            "/api/v1/auth/password/login",
            json={"username": "admin", "password": "correct-horse-battery"},
            headers={"x-real-ip": fresh_ip},
        )
        assert ok.status_code == 200
        me = client.get("/api/v1/me").json()
        assert me["user"]["role"] == "admin"

    def test_password_login_rate_limited(self, client, data_dir, fresh_ip):
        for _ in range(5):
            client.post(
                "/api/v1/auth/password/login",
                json={"username": "nobody", "password": "x"},
                headers={"x-real-ip": fresh_ip},
            )
        r = client.post(
            "/api/v1/auth/password/login",
            json={"username": "nobody", "password": "x"},
            headers={"x-real-ip": fresh_ip},
        )
        assert r.status_code == 429


# ── 会话撤销 ───────────────────────────────────────────────

class TestSessionRevocation:
    def test_revoked_session_rejected(self, client, fresh_ip, data_dir):
        wechat_scan_login(client, ip=fresh_ip)
        assert client.get("/api/v1/me").json()["authenticated"] is True
        from backend.db.connections import connect_rw

        conn = connect_rw("platform")
        conn.execute("UPDATE auth_sessions SET revoked_at='2026-01-01T00:00:00Z'")
        conn.close()
        assert client.get("/api/v1/me").json()["authenticated"] is False

    def test_expired_session_rejected(self, client, fresh_ip, data_dir):
        wechat_scan_login(client, ip=fresh_ip)
        from backend.db.connections import connect_rw

        conn = connect_rw("platform")
        conn.execute("UPDATE auth_sessions SET expires_at='2020-01-01T00:00:00Z'")
        conn.close()
        assert client.get("/api/v1/me").json()["authenticated"] is False


# ── 认证三态 ───────────────────────────────────────────────

class TestAuthTriState:
    """CLAUDE.md §7.3 认证三态:
    ① production+ENABLED=0 → 无凭证可启动,微信端点 503 AUTH_DISABLED;
    ② production+ENABLED=1 缺凭证 → fail-fast(TestProductionFailFast 已覆盖);
    ③ production+mock → fail-fast(同上);
    ④ development+mock → 可用(TestScanLogin 全流程已覆盖)。"""

    def test_production_disabled_boots_without_credentials(self, data_dir):
        from backend.api.app import create_app

        settings = load_auth_settings(PROD_DISABLED_ENV)
        assert settings.wechat_login_available is False
        app = create_app(settings)   # 不得尝试实例化真实 Provider
        assert app.state.wechat_provider.kind == "disabled"

    WECHAT_ENDPOINTS = [
        ("POST", "/api/v1/auth/wechat/device"),
        ("POST", "/api/v1/auth/wechat/device/some-id/claim"),
        ("GET", "/api/v1/auth/wechat/webhook?signature=x&timestamp=1&nonce=n&echostr=e"),
        ("POST", "/api/v1/auth/wechat/webhook?signature=x&timestamp=1&nonce=n"),
    ]

    @pytest.mark.parametrize("method,path", WECHAT_ENDPOINTS)
    def test_disabled_wechat_endpoints_503_structured(self, disabled_client, method, path):
        if method == "POST":
            r = disabled_client.post(path, json={"secret": "x"})
        else:
            r = disabled_client.get(path, follow_redirects=False)
        assert r.status_code == 503
        body = r.json()
        assert body["code"] == "AUTH_DISABLED"
        assert body["message"]
        assert r.headers["cache-control"] == "private, no-store"

    def test_auth_methods_reports_disabled(self, disabled_client):
        r = disabled_client.get("/api/v1/auth/methods")
        assert r.status_code == 200
        assert r.json() == {"wechat_enabled": False}
        assert r.headers["cache-control"] == "private, no-store"

    def test_auth_methods_reports_enabled_in_dev_mock(self, client):
        r = client.get("/api/v1/auth/methods")
        assert r.status_code == 200
        assert r.json() == {"wechat_enabled": True}

    def test_disabled_password_login_logout_me_unaffected(self, disabled_client, data_dir, fresh_ip):
        from backend.cli.create_admin import create_admin
        from backend.db.connections import connect_rw

        conn = connect_rw("platform")
        create_admin(conn, "tri-admin", "tri-state-password-1")
        conn.close()

        me = disabled_client.get("/api/v1/me")
        assert me.status_code == 200
        assert me.json()["authenticated"] is False

        ok = disabled_client.post(
            "/api/v1/auth/password/login",
            json={"username": "tri-admin", "password": "tri-state-password-1"},
            headers={"x-real-ip": fresh_ip},
        )
        assert ok.status_code == 200
        assert disabled_client.get("/api/v1/me").json()["authenticated"] is True

        csrf = disabled_client.cookies.get("allwin_csrf")
        r = disabled_client.post(
            "/api/v1/auth/logout",
            headers={"Origin": "https://testserver", "X-CSRF-Token": csrf},
        )
        assert r.status_code == 200
        assert disabled_client.get("/api/v1/me").json()["authenticated"] is False


class TestProductionDisabledUvicornSmoke:
    """真实进程冒烟:production + ENABLED=0 无凭证下 uvicorn 能启动,
    healthz 200,POST device 503 AUTH_DISABLED,然后终止进程。"""

    def test_uvicorn_boots_and_gates_wechat(self, data_dir):
        import os
        import socket
        import subprocess
        import sys
        import time as _time
        from pathlib import Path

        import httpx

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        root = Path(__file__).resolve().parents[2]
        env = dict(os.environ)
        env.update(
            {
                "ALLWIN_DATA_DIR": str(data_dir),
                "APP_ENV": "production",
                "WECHAT_AUTH_PROVIDER": "real",
                "WECHAT_AUTH_ENABLED": "0",
            }
        )
        for k in ("WECHAT_OA_APP_ID", "WECHAT_OA_APP_SECRET", "WECHAT_WEBHOOK_TOKEN"):
            env.pop(k, None)

        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.api.app:app",
             "--host", "127.0.0.1", "--port", str(port)],
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            deadline = _time.monotonic() + 30
            last_err = None
            while True:
                if proc.poll() is not None:
                    out = proc.stdout.read().decode(errors="replace")
                    raise AssertionError(
                        f"uvicorn 提前退出(rc={proc.returncode}):\n{out[-2000:]}"
                    )
                try:
                    r = httpx.get(f"{base}/healthz", timeout=2)
                    if r.status_code == 200:
                        break
                except Exception as exc:  # 启动期连接被拒,继续等
                    last_err = exc
                if _time.monotonic() > deadline:
                    raise AssertionError(f"healthz 在 30s 内未就绪:{last_err}")
                _time.sleep(0.3)

            r = httpx.post(f"{base}/api/v1/auth/wechat/device", timeout=5)
            assert r.status_code == 503
            assert r.json()["code"] == "AUTH_DISABLED"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)


class TestLegacyGateRemoval:
    def test_simulate_membership_param_is_dead(self, client):
        """?simulate_membership=paid 不再有任何效果——2026-08-16 起
        /api/league/{id}/betting 本身也不再有 entitlement 门禁(除"每日精选"
        外全站比赛内容全部免费),携带该参数与不携带响应完全一致,不会让
        请求绕过或触发任何特殊行为。这条断言正是要推翻的旧规则(此前恒 401)。"""
        with_param = client.get("/api/league/47/betting?simulate_membership=paid")
        without_param = client.get("/api/league/47/betting")
        assert with_param.status_code == without_param.status_code
        assert with_param.json() == without_param.json()

    def test_source_contains_no_simulate_membership(self):
        import pathlib

        src = pathlib.Path("backend/api_server.py").read_text()
        assert 'query_params.get("simulate_membership")' not in src

    def test_web_oauth_endpoints_removed(self, client):
        """网页授权路线已废弃:oa/start、oa/callback 不复存在(404)。"""
        assert client.get(
            "/api/v1/auth/wechat/oa/start", follow_redirects=False
        ).status_code == 404
        assert client.get(
            "/api/v1/auth/wechat/oa/callback?code=x&state=y", follow_redirects=False
        ).status_code == 404


# ── webhook 纯函数单测 ─────────────────────────────────────

class TestWebhookPureFunctions:
    def test_signature_matches_wechat_algorithm(self):
        from backend.auth.wechat_webhook import compute_signature, verify_signature

        # sha1(sorted 拼接) 的独立样例
        sig = compute_signature("token", "123", "abc")
        import hashlib

        assert sig == hashlib.sha1("".join(sorted(["token", "123", "abc"])).encode()).hexdigest()
        assert verify_signature("token", "123", "abc", sig.upper()) is True
        assert verify_signature("token", "123", "abc", "bad") is False
        assert verify_signature("", "123", "abc", sig) is False

    def test_parse_scan_event(self):
        from backend.auth.wechat_webhook import parse_event_xml

        ev = parse_event_xml(scan_event_xml("req-9", "openid-9").encode())
        assert ev.event == "scan"
        assert ev.scene_str == "req-9"
        assert ev.openid == "openid-9"

    def test_parse_subscribe_event_strips_qrscene_prefix(self):
        from backend.auth.wechat_webhook import parse_event_xml

        ev = parse_event_xml(scan_event_xml("req-8", "openid-8", event="subscribe").encode())
        assert ev.event == "subscribe"
        assert ev.scene_str == "req-8"

    def test_parse_plain_subscribe_without_scene(self):
        from backend.auth.wechat_webhook import parse_event_xml

        xml = (
            "<xml><ToUserName><![CDATA[gh]]></ToUserName>"
            "<FromUserName><![CDATA[o]]></FromUserName>"
            "<CreateTime>1</CreateTime><MsgType><![CDATA[event]]></MsgType>"
            "<Event><![CDATA[subscribe]]></Event><EventKey><![CDATA[]]></EventKey></xml>"
        )
        ev = parse_event_xml(xml.encode())
        assert ev.scene_str is None

    def test_reply_xml_swaps_to_from(self):
        from backend.auth.wechat_webhook import build_text_reply, parse_event_xml

        ev = parse_event_xml(scan_event_xml("r", "openid-x").encode())
        xml = build_text_reply(ev, "hello", 1700000000)
        assert "<ToUserName><![CDATA[openid-x]]></ToUserName>" in xml
        assert "<FromUserName><![CDATA[gh_mock_oa]]></FromUserName>" in xml
        assert "hello" in xml
