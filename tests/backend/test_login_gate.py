"""2026-10-02 登录门禁 + 防爬限流 + 管理员停用账号(经站长批准)。

- 未登录只能看英超的比赛详情/联赛数据,其他联赛 401 login_required;
- 比赛列表对任何人一致(列表看得到,点进去才要登录);
- 登录用户短时间狂刷数据端点 → 429,并在 audit_logs 留痕供后台判断;
- 管理员停用账号 → 会话立即失效、再次登录领取会话被拒;
- 模拟器非英超参数与跨联赛真实比赛索引只给登录用户。
"""

import json

from fastapi.testclient import TestClient

from backend.api import data_access
from backend.db.connections import connect_ro, connect_rw

from .authflow import post_code, wechat_scan_login
from .coreseed import seed_basic_core

ORIGIN = {"Origin": "http://localhost:3000"}


def _admin(app, ip):
    from backend.cli.create_admin import create_admin

    conn = connect_rw("platform")
    try:
        create_admin(conn, "gate-admin", "admin-pass-123", reset=True)
    finally:
        conn.close()
    admin = TestClient(app)
    r = admin.post("/api/v1/auth/password/login",
                   json={"username": "gate-admin", "password": "admin-pass-123"},
                   headers={"x-real-ip": ip})
    assert r.status_code == 200
    return admin


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("allwin_csrf"), **ORIGIN}


class TestLoginGate:
    def test_match_detail_family_epl_open_others_401(self, app, data_dir):
        seed_basic_core(data_dir)
        c = TestClient(app)
        for sub in ("", "/analysis", "/odds", "/report", "/preview", "/data-profile", "/markets"):
            assert c.get(f"/api/v1/matches/9002{sub}").status_code == 200, sub
            r = c.get(f"/api/v1/matches/9101{sub}")
            assert r.status_code == 401, sub
            assert r.json()["code"] == "login_required"
            assert r.headers["cache-control"] == "private, no-store"

    def test_401_body_carries_no_detail_data(self, app, data_dir):
        """401 只带列表页本来就公开的赛事标识,不带比分/赔率/概率等详情字段。"""
        seed_basic_core(data_dir)
        r = TestClient(app).get("/api/v1/matches/9101/odds")
        body = r.json()
        assert set(body) == {"code", "message", "details"}
        assert set(body["details"]) == {"league_id", "league_name", "home_team", "away_team", "kickoff_at_utc"}
        assert body["details"]["league_id"] == 87

    def test_logged_in_sees_all(self, app, data_dir, fresh_ip):
        seed_basic_core(data_dir)
        c = TestClient(app)
        wechat_scan_login(c, ip=fresh_ip)
        for sub in ("", "/odds", "/report", "/markets"):
            assert c.get(f"/api/v1/matches/9101{sub}").status_code == 200, sub

    def test_match_list_same_for_anonymous_and_logged_in(self, app, data_dir, fresh_ip):
        seed_basic_core(data_dir)
        anon = TestClient(app).get("/api/v1/matches?status=finished").json()
        user = TestClient(app)
        wechat_scan_login(user, ip=fresh_ip)
        logged = user.get("/api/v1/matches?status=finished").json()
        assert {m["league_id"] for m in anon["matches"]} >= {47, 87}
        assert anon["matches"] == logged["matches"]

    def test_legacy_endpoints_gated(self, app, data_dir):
        seed_basic_core(data_dir)
        c = TestClient(app)
        for section in ("overview", "betting", "matches"):
            r = c.get(f"/api/league/87/{section}")
            assert r.status_code == 401, section
            assert r.json()["code"] == "login_required"


class TestRateLimit:
    def test_user_burst_429_and_audit_once(self, app, data_dir, fresh_ip, monkeypatch):
        seed_basic_core(data_dir)
        monkeypatch.setattr(data_access, "USER_PER_MIN", 5)
        c = TestClient(app)
        wechat_scan_login(c, ip=fresh_ip)
        user_id = c.get("/api/v1/me").json()["user"]["id"]
        codes = [c.get("/api/v1/matches/9002").status_code for _ in range(8)]
        assert codes[:5] == [200] * 5
        assert codes[5:] == [429] * 3
        assert c.get("/api/v1/matches/9002").json()["code"] == "rate_limited"
        conn = connect_ro("platform")
        try:
            trips = conn.execute(
                "SELECT detail_json FROM audit_logs WHERE action='ratelimit.trip' AND target_id=?",
                (user_id,),
            ).fetchall()
        finally:
            conn.close()
        assert len(trips) == 1   # 冷却期内只留痕一次
        assert json.loads(trips[0]["detail_json"]) == {"window": "minute"}

    def test_hour_and_day_windows(self, app, data_dir, fresh_ip, monkeypatch):
        seed_basic_core(data_dir)
        c = TestClient(app)
        wechat_scan_login(c, ip=fresh_ip)
        monkeypatch.setattr(data_access, "USER_PER_HOUR", 3)
        codes = [c.get("/api/v1/matches/9002").status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]
        data_access._last_trip_audit.clear()
        monkeypatch.setattr(data_access, "USER_PER_HOUR", 300)
        monkeypatch.setattr(data_access, "USER_PER_DAY", 5)
        codes = [c.get("/api/v1/matches/9002").status_code for _ in range(3)]
        # 日窗已记 3 次(被小时窗拒掉的那次在日窗之前就挡下,不计入),额度 5 → 再放 2 次
        assert codes == [200, 200, 429]
        assert c.get("/api/v1/matches/9002").json()["message"] == "今天的访问次数已用完,请明天再来"

    def test_defaults_match_owner_decision(self):
        """站长 2026-10-02:正常人浏览频率(生产日志 p95:4/5/15 页)× 3,一页按 6 次请求计。"""
        assert data_access.PAGE_COST == 6
        assert (data_access.USER_PER_MIN, data_access.USER_PER_HOUR, data_access.USER_PER_DAY) == (72, 90, 270)

    def test_public_match_list_not_metered(self, app, data_dir, fresh_ip, monkeypatch):
        seed_basic_core(data_dir)
        monkeypatch.setattr(data_access, "USER_PER_MIN", 2)
        c = TestClient(app)
        wechat_scan_login(c, ip=fresh_ip)
        assert all(c.get("/api/v1/matches").status_code == 200 for _ in range(5))
        assert [c.get("/api/v1/matches/9002").status_code for _ in range(3)] == [200, 200, 429]

    def test_anonymous_via_nginx_limited_by_ip_but_ssr_exempt(self, app, data_dir, monkeypatch):
        seed_basic_core(data_dir)
        monkeypatch.setattr(data_access, "ANON_IP_PER_MIN", 3)
        c = TestClient(app)
        hdr = {"x-real-ip": "198.51.100.77"}
        codes = [c.get("/api/v1/matches/9002", headers=hdr).status_code for _ in range(5)]
        assert codes == [200, 200, 200, 429, 429]
        # 没有 X-Real-IP = Next 服务端直连(SSR),不计入
        assert all(c.get("/api/v1/matches/9002").status_code == 200 for _ in range(5))

    def test_admin_list_shows_trips(self, app, data_dir, fresh_ip, monkeypatch):
        seed_basic_core(data_dir)
        monkeypatch.setattr(data_access, "USER_PER_MIN", 2)
        c = TestClient(app)
        wechat_scan_login(c, openid="gate-scraper", ip=fresh_ip)
        uid = c.get("/api/v1/me").json()["user"]["id"]
        for _ in range(4):
            c.get("/api/v1/matches/9002")
        monkeypatch.setattr(data_access, "USER_PER_MIN", 120)
        admin = _admin(app, f"{fresh_ip}-a")
        users = {u["id"]: u for u in admin.get("/api/v1/admin/users").json()["users"]}
        assert users[uid]["rate_limit_trips_7d"] == 1
        assert users[uid]["last_rate_limited_at"]


class TestAdminDisableUser:
    def test_disable_revokes_sessions_and_blocks_relogin(self, app, data_dir, fresh_ip):
        seed_basic_core(data_dir)
        user = TestClient(app)
        wechat_scan_login(user, openid="gate-bad-actor", ip=fresh_ip)
        uid = user.get("/api/v1/me").json()["user"]["id"]
        admin = _admin(app, f"{fresh_ip}-a")

        r = admin.post(f"/api/v1/admin/users/{uid}/status",
                       json={"status": "disabled", "note": "狂刷赔率"}, headers=_csrf(admin))
        assert r.status_code == 200, r.text
        assert user.get("/api/v1/me").json()["authenticated"] is False
        assert user.get("/api/v1/matches/9101").status_code == 401

        # 再走一遍发码登录:领取会话被拒,不发一个立即失效的 Cookie
        again = TestClient(app)
        d = again.post("/api/v1/auth/wechat/device", headers={"x-real-ip": f"{fresh_ip}-b"}).json()
        post_code(again, d["login_code"], "gate-bad-actor")
        r = again.post(f"/api/v1/auth/wechat/device/{d['request_id']}/claim",
                       json={"secret": d["secret"]}, headers={"x-real-ip": f"{fresh_ip}-b"})
        assert r.status_code == 403
        assert r.json()["code"] == "account_disabled"
        assert "allwin_session" not in r.cookies

        conn = connect_ro("platform")
        try:
            row = conn.execute(
                "SELECT detail_json FROM audit_logs WHERE action='user.disable' AND target_id=?", (uid,)
            ).fetchone()
        finally:
            conn.close()
        assert json.loads(row["detail_json"])["note"] == "狂刷赔率"

        r = admin.post(f"/api/v1/admin/users/{uid}/status",
                       json={"status": "active"}, headers=_csrf(admin))
        assert r.status_code == 200
        wechat_scan_login(TestClient(app), openid="gate-bad-actor", ip=f"{fresh_ip}-c")

    def test_cannot_disable_admin_and_needs_csrf(self, app, data_dir, fresh_ip):
        admin = _admin(app, fresh_ip)
        me = admin.get("/api/v1/me").json()["user"]["id"]
        r = admin.post(f"/api/v1/admin/users/{me}/status",
                       json={"status": "disabled"}, headers=_csrf(admin))
        assert r.status_code == 400
        r = admin.post(f"/api/v1/admin/users/{me}/status", json={"status": "disabled"})
        assert r.status_code == 403

    def test_non_admin_forbidden(self, app, data_dir, fresh_ip):
        user = TestClient(app)
        wechat_scan_login(user, openid="gate-normal", ip=fresh_ip)
        uid = user.get("/api/v1/me").json()["user"]["id"]
        r = user.post(f"/api/v1/admin/users/{uid}/status",
                      json={"status": "disabled"}, headers=_csrf(user))
        assert r.status_code == 403
