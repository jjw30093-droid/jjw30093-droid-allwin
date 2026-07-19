"""P0.4 测试:products、订阅授予/延期/撤销、兑换码、admin 权限矩阵、审计日志。"""

import pytest
from fastapi.testclient import TestClient

ORIGIN = {"Origin": "http://localhost:3000"}


def _login_user(client, ip="203.0.113.50"):
    r1 = client.get("/api/v1/auth/wechat/oa/start?next=/", follow_redirects=False,
                    headers={"x-real-ip": ip})
    client.get(r1.headers["location"], follow_redirects=False)
    body = client.get("/api/v1/me").json()
    assert body["authenticated"]
    return body["user"]["id"]


def _login_admin(app, data_dir, ip):
    from backend.cli.create_admin import create_admin
    from backend.db.connections import connect_rw

    conn = connect_rw("platform")
    try:
        create_admin(conn, "boss", "admin-pass-123", reset=True)
    finally:
        conn.close()
    admin = TestClient(app)
    r = admin.post("/api/v1/auth/password/login",
                   json={"username": "boss", "password": "admin-pass-123"},
                   headers={"x-real-ip": ip})
    assert r.status_code == 200
    return admin


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("allwin_csrf"), **ORIGIN}


class TestProducts:
    def test_products_db_driven(self, client):
        r = client.get("/api/v1/products")
        assert r.status_code == 200
        body = r.json()
        assert [p["id"] for p in body["plans"]] == ["free", "pro", "premium"]
        assert len(body["products"]) >= 4
        assert all("price_cents" in p for p in body["products"])
        pro = next(p for p in body["plans"] if p["id"] == "pro")
        assert "prediction:full_wdl" in pro["entitlements"]
        assert "public" in r.headers["cache-control"]


class TestAdminGrant:
    def test_grant_extend_revoke_flow(self, app, client, data_dir, fresh_ip):
        user_id = _login_user(client, ip=fresh_ip)
        admin = _login_admin(app, data_dir, ip=fresh_ip)

        # 授予 30 天 pro
        r = admin.post(f"/api/v1/admin/users/{user_id}/grant",
                       json={"plan_id": "pro", "duration_days": 30},
                       headers=_csrf(admin))
        assert r.status_code == 200, r.text
        first_ends = r.json()["ends_at"]
        assert client.get("/api/v1/me").json()["plan"] == "pro"
        assert "prediction:full_wdl" in client.get("/api/v1/me").json()["entitlements"]

        # 再授 30 天 → 从上次 ends_at 顺延
        r2 = admin.post(f"/api/v1/admin/users/{user_id}/grant",
                        json={"plan_id": "pro", "duration_days": 30},
                        headers=_csrf(admin))
        assert r2.json()["starts_at"] == first_ends

        # 撤销两个订阅 → 回到 free
        subs = client.get("/api/v1/account").json()["subscriptions"]
        for s in subs:
            rr = admin.post(f"/api/v1/admin/subscriptions/{s['id']}/revoke", headers=_csrf(admin))
            assert rr.status_code == 200
        assert client.get("/api/v1/me").json()["plan"] == "free"

        # 全程留审计
        logs = admin.get("/api/v1/admin/audit-logs").json()["logs"]
        actions = [l["action"] for l in logs]
        assert "subscription.grant" in actions and "subscription.revoke" in actions

    def test_admin_endpoints_reject_non_admin(self, app, client, fresh_ip):
        _login_user(client, ip=fresh_ip)
        assert client.get("/api/v1/admin/users").status_code == 403
        assert client.post("/api/v1/admin/redeem-codes",
                           json={"plan_id": "pro", "duration_days": 30, "count": 1},
                           headers=_csrf(client)).status_code == 403
        anon = TestClient(app)
        assert anon.get("/api/v1/admin/users").status_code == 401

    def test_admin_write_requires_csrf(self, app, client, data_dir, fresh_ip):
        user_id = _login_user(client, ip=fresh_ip)
        admin = _login_admin(app, data_dir, ip=fresh_ip)
        r = admin.post(f"/api/v1/admin/users/{user_id}/grant",
                       json={"plan_id": "pro", "duration_days": 30})   # 无 Origin/CSRF
        assert r.status_code == 403

    def test_no_store_on_member_and_admin(self, app, client, data_dir, fresh_ip):
        _login_user(client, ip=fresh_ip)
        admin = _login_admin(app, data_dir, ip=fresh_ip)
        assert client.get("/api/v1/account").headers["cache-control"] == "private, no-store"
        assert admin.get("/api/v1/admin/users").headers["cache-control"] == "private, no-store"


class TestRedeem:
    def _make_codes(self, admin, n=1, **over):
        payload = {"plan_id": "pro", "duration_days": 30, "count": n}
        payload.update(over)
        r = admin.post("/api/v1/admin/redeem-codes", json=payload, headers=_csrf(admin))
        assert r.status_code == 200, r.text
        return [c["code"] for c in r.json()["codes"]]

    def test_redeem_flow_and_reuse_blocked(self, app, client, data_dir, fresh_ip):
        _login_user(client, ip=fresh_ip)
        admin = _login_admin(app, data_dir, ip=fresh_ip)
        code = self._make_codes(admin)[0]

        r = client.post("/api/v1/redeem", json={"code": code}, headers=_csrf(client))
        assert r.status_code == 200, r.text
        assert client.get("/api/v1/me").json()["plan"] == "pro"

        # 同码复用 → used
        r2 = client.post("/api/v1/redeem", json={"code": code}, headers=_csrf(client))
        assert r2.status_code == 400
        assert r2.json()["detail"]["code"] == "used"

    def test_redeem_invalid_and_expired(self, app, client, data_dir, fresh_ip):
        _login_user(client, ip=fresh_ip)
        admin = _login_admin(app, data_dir, ip=fresh_ip)
        r = client.post("/api/v1/redeem", json={"code": "AW-XXXX-XXXX-XXXX"}, headers=_csrf(client))
        assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid"
        expired = self._make_codes(admin, expires_at="2020-01-01T00:00:00Z")[0]
        r2 = client.post("/api/v1/redeem", json={"code": expired}, headers=_csrf(client))
        assert r2.status_code == 400 and r2.json()["detail"]["code"] == "expired"

    def test_redeem_requires_login_and_csrf(self, app, data_dir):
        anon = TestClient(app)
        assert anon.post("/api/v1/redeem", json={"code": "AW-AAAA-BBBB-CCCC"},
                         headers=ORIGIN).status_code == 401
