"""P0.3 认证安全测试:OAuth state 一次性、Device Login、CSRF、开放重定向、
production fail-fast、cookie 属性、no-store、限流。"""

from urllib.parse import parse_qs, urlparse

import pytest

from backend.auth.config import AuthConfigError, load_auth_settings

from .conftest import BASE_ENV, make_settings

ORIGIN = {"Origin": "http://localhost:3000"}


def _login(client, ip="203.0.113.1", next_path="/"):
    """走完整 mock OAuth 登录,返回最终 302 响应(带 Set-Cookie)。"""
    r1 = client.get(
        f"/api/v1/auth/wechat/oa/start?next={next_path}",
        follow_redirects=False,
        headers={"x-real-ip": ip},
    )
    assert r1.status_code == 302
    callback_url = r1.headers["location"]
    r2 = client.get(callback_url, follow_redirects=False)
    return r2


class TestOAuthLogin:
    def test_full_mock_login_flow(self, client, fresh_ip):
        r2 = _login(client, ip=fresh_ip, next_path="/account")
        assert r2.status_code == 302
        assert r2.headers["location"] == "/account"
        assert "allwin_session" in r2.cookies
        me = client.get("/api/v1/me")
        body = me.json()
        assert body["authenticated"] is True
        assert body["plan"] == "free"
        assert "prediction:top_probability" in body["entitlements"]
        assert "prediction:full_wdl" not in body["entitlements"]

    def test_state_replay_rejected(self, client, fresh_ip):
        r1 = client.get(
            "/api/v1/auth/wechat/oa/start?next=/",
            follow_redirects=False,
            headers={"x-real-ip": fresh_ip},
        )
        callback_url = r1.headers["location"]
        first = client.get(callback_url, follow_redirects=False)
        assert first.status_code == 302
        replay = client.get(callback_url, follow_redirects=False)
        assert replay.status_code == 400

    def test_forged_state_rejected(self, client):
        r = client.get(
            "/api/v1/auth/wechat/oa/callback?code=mock-x&state=forged-state",
            follow_redirects=False,
        )
        assert r.status_code == 400

    @pytest.mark.parametrize("evil", ["https://evil.com", "//evil.com", "/\\evil", "javascript:alert(1)"])
    def test_open_redirect_blocked(self, client, fresh_ip, evil):
        r2 = _login(client, ip=fresh_ip, next_path=evil)
        assert r2.status_code == 302
        assert r2.headers["location"] == "/"   # 非法 next 一律回首页

    def test_session_cookie_attributes(self, client, fresh_ip):
        r2 = _login(client, ip=fresh_ip)
        set_cookie = "; ".join(r2.headers.get_list("set-cookie"))
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
        r1 = client.get(
            "/api/v1/auth/wechat/oa/start", follow_redirects=False, headers={"x-real-ip": fresh_ip}
        )
        assert r1.headers["cache-control"] == "private, no-store"


class TestDeviceLogin:
    def _create(self, client, ip):
        r = client.post("/api/v1/auth/wechat/device", headers={"x-real-ip": ip})
        assert r.status_code == 200
        return r.json()

    def _approve(self, client, req):
        """模拟手机扫码:qr_url 里只有公开 request_id。"""
        assert req["secret"] not in req["qr_url"]
        r1 = client.get(req["qr_url"], follow_redirects=False)
        assert r1.status_code == 302
        r2 = client.get(r1.headers["location"], follow_redirects=False)
        assert r2.status_code == 200
        assert r2.json()["status"] == "approved"
        # 批准动作不给手机端种网站会话 cookie
        assert "allwin_session" not in r2.cookies

    def test_full_device_flow(self, client, fresh_ip):
        req = self._create(client, fresh_ip)
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
        req = self._create(client, fresh_ip)
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
        req = self._create(client, fresh_ip)
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


class TestCsrfAndLogout:
    def test_logout_requires_csrf(self, client, fresh_ip):
        _login(client, ip=fresh_ip)
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

    def test_production_requires_https_base(self):
        env = dict(
            BASE_ENV,
            APP_ENV="production",
            WECHAT_AUTH_PROVIDER="real",
            WECHAT_AUTH_ENABLED="1",
            WECHAT_OA_APP_ID="wx123",
            WECHAT_OA_APP_SECRET="s",
            PUBLIC_BASE_URL="http://insecure.example.com",
        )
        with pytest.raises(AuthConfigError, match="https"):
            load_auth_settings(env)

    def test_production_real_disabled_ok(self):
        env = dict(BASE_ENV, APP_ENV="production", WECHAT_AUTH_PROVIDER="real",
                   WECHAT_AUTH_ENABLED="0", PUBLIC_BASE_URL="https://allwin.example.com")
        s = load_auth_settings(env)
        assert s.cookie_secure is True


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


class TestSessionRevocation:
    def test_revoked_session_rejected(self, client, fresh_ip, data_dir):
        _login(client, ip=fresh_ip)
        assert client.get("/api/v1/me").json()["authenticated"] is True
        from backend.db.connections import connect_rw

        conn = connect_rw("platform")
        conn.execute("UPDATE auth_sessions SET revoked_at='2026-01-01T00:00:00Z'")
        conn.close()
        assert client.get("/api/v1/me").json()["authenticated"] is False

    def test_expired_session_rejected(self, client, fresh_ip, data_dir):
        _login(client, ip=fresh_ip)
        from backend.db.connections import connect_rw

        conn = connect_rw("platform")
        conn.execute("UPDATE auth_sessions SET expires_at='2020-01-01T00:00:00Z'")
        conn.close()
        assert client.get("/api/v1/me").json()["authenticated"] is False


class TestLegacyGateRemoval:
    def test_simulate_membership_param_is_dead(self, client):
        """?simulate_membership=paid 不再有任何效果(匿名照样被拒)。"""
        r = client.get("/api/league/47/betting?simulate_membership=paid")
        assert r.status_code == 401

    def test_source_contains_no_simulate_membership(self):
        import pathlib

        src = pathlib.Path("backend/api_server.py").read_text()
        assert 'query_params.get("simulate_membership")' not in src
