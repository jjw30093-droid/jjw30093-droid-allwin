"""favorites 三端点(GET/POST/DELETE /api/v1/favorites)——此前零测试覆盖。

2026-08 QA 抽查发现「关注比赛」按钮只写 localStorage、后端三端点全是死代码
(生产库 favorites 表 0 行)。前端改造(FollowButton 真实写后端)落地之前,
先把后端契约钉死:匿名 401、CSRF 双提交、幂等写入、用户隔离、no-store。
这些断言全部针对现有未改动的 backend/api/routes_member.py——本提交只加
测试不改生产代码。
"""

from .authflow import wechat_scan_login

ORIGIN = {"Origin": "http://localhost:3000"}


def _csrf_headers(client) -> dict:
    return {**ORIGIN, "X-CSRF-Token": client.cookies.get("allwin_csrf")}


def _logout(client) -> None:
    r = client.post("/api/v1/auth/logout", headers=_csrf_headers(client))
    assert r.status_code == 200, r.text


class TestAnonymous:
    def test_get_requires_login(self, client, data_dir):
        r = client.get("/api/v1/favorites")
        assert r.status_code == 401

    def test_post_requires_login(self, client, data_dir):
        r = client.post("/api/v1/favorites", json={"match_id": 100}, headers=ORIGIN)
        assert r.status_code == 401

    def test_delete_requires_login(self, client, data_dir):
        r = client.delete("/api/v1/favorites/100", headers=ORIGIN)
        assert r.status_code == 401


class TestCsrf:
    def test_post_without_csrf_token_403(self, client, fresh_ip):
        wechat_scan_login(client, ip=fresh_ip)
        r = client.post("/api/v1/favorites", json={"match_id": 100}, headers=ORIGIN)
        assert r.status_code == 403

    def test_post_without_origin_403(self, client, fresh_ip):
        wechat_scan_login(client, ip=fresh_ip)
        r = client.post(
            "/api/v1/favorites",
            json={"match_id": 100},
            headers={"X-CSRF-Token": client.cookies.get("allwin_csrf")},
        )
        assert r.status_code == 403

    def test_delete_without_csrf_token_403(self, client, fresh_ip):
        wechat_scan_login(client, ip=fresh_ip)
        r = client.delete("/api/v1/favorites/100", headers=ORIGIN)
        assert r.status_code == 403


class TestCrud:
    def test_post_reflected_in_get(self, client, fresh_ip):
        wechat_scan_login(client, ip=fresh_ip)
        r = client.post(
            "/api/v1/favorites", json={"match_id": 4813754}, headers=_csrf_headers(client)
        )
        assert r.status_code == 200, r.text
        body = client.get("/api/v1/favorites").json()
        assert [f["match_id"] for f in body["favorites"]] == [4813754]
        assert body["favorites"][0]["created_at"]

    def test_duplicate_post_is_idempotent(self, client, fresh_ip):
        wechat_scan_login(client, ip=fresh_ip)
        for _ in range(3):
            r = client.post(
                "/api/v1/favorites", json={"match_id": 200}, headers=_csrf_headers(client)
            )
            assert r.status_code == 200, r.text
        body = client.get("/api/v1/favorites").json()
        assert [f["match_id"] for f in body["favorites"]] == [200]

    def test_delete_removes(self, client, fresh_ip):
        wechat_scan_login(client, ip=fresh_ip)
        headers = _csrf_headers(client)
        client.post("/api/v1/favorites", json={"match_id": 300}, headers=headers)
        client.post("/api/v1/favorites", json={"match_id": 301}, headers=headers)
        r = client.delete("/api/v1/favorites/300", headers=headers)
        assert r.status_code == 200
        body = client.get("/api/v1/favorites").json()
        assert [f["match_id"] for f in body["favorites"]] == [301]

    def test_delete_nonexistent_is_noop_200(self, client, fresh_ip):
        # 前端迁移会重放 localStorage 里的 id,重放到已删除的 id 不应报错。
        wechat_scan_login(client, ip=fresh_ip)
        r = client.delete("/api/v1/favorites/999999", headers=_csrf_headers(client))
        assert r.status_code == 200
        assert client.get("/api/v1/favorites").json()["favorites"] == []


class TestUserIsolation:
    def test_two_users_do_not_see_each_other(self, client, fresh_ip):
        wechat_scan_login(client, openid="fav-user-a", ip=fresh_ip)
        client.post(
            "/api/v1/favorites", json={"match_id": 111}, headers=_csrf_headers(client)
        )
        _logout(client)

        wechat_scan_login(client, openid="fav-user-b", ip=fresh_ip)
        assert client.get("/api/v1/favorites").json()["favorites"] == []
        client.post(
            "/api/v1/favorites", json={"match_id": 222}, headers=_csrf_headers(client)
        )
        body_b = client.get("/api/v1/favorites").json()
        assert [f["match_id"] for f in body_b["favorites"]] == [222]
        _logout(client)

        # 用户 A 再登录,自己的关注还在且没混入 B 的
        wechat_scan_login(client, openid="fav-user-a", ip=fresh_ip)
        body_a = client.get("/api/v1/favorites").json()
        assert [f["match_id"] for f in body_a["favorites"]] == [111]


class TestCachePolicy:
    def test_all_three_endpoints_no_store(self, client, fresh_ip):
        wechat_scan_login(client, ip=fresh_ip)
        headers = _csrf_headers(client)
        r_post = client.post("/api/v1/favorites", json={"match_id": 400}, headers=headers)
        r_get = client.get("/api/v1/favorites")
        r_del = client.delete("/api/v1/favorites/400", headers=headers)
        for r in (r_post, r_get, r_del):
            assert r.headers["cache-control"] == "private, no-store"
