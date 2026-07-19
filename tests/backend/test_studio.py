"""P0.9/P0.10 测试:analysis_bundle、Studio 草稿/导出、埋点。"""

import json

import pytest
from fastapi.testclient import TestClient

from backend.commands.predictions import (
    get_or_create_model_version,
    publish_snapshot,
    register_snapshot,
)
from backend.db.connections import connect_rw

from .coreseed import seed_basic_core

ORIGIN = {"Origin": "http://localhost:3000"}


@pytest.fixture
def seeded(data_dir):
    seed_basic_core(data_dir)
    conn = connect_rw("platform")
    get_or_create_model_version(conn, "m-studio", "dixon-coles")
    sid = register_snapshot(
        conn, match_id=9001, kickoff_at_utc="2027-04-01T00:00:00Z",
        model_version_id="m-studio", home_win=0.48, draw=0.29, away_win=0.23,
        expected_home_goals=1.6, expected_away_goals=1.0, status="draft",
    )
    publish_snapshot(conn, sid, actor=None)
    conn.close()
    return data_dir


def _analyst_client(app, ip):
    client = TestClient(app)
    r1 = client.get("/api/v1/auth/wechat/oa/start?next=/", follow_redirects=False,
                    headers={"x-real-ip": ip})
    client.get(r1.headers["location"], follow_redirects=False)
    user_id = client.get("/api/v1/me").json()["user"]["id"]
    conn = connect_rw("platform")
    conn.execute("UPDATE users SET role='analyst' WHERE id=?", (user_id,))
    conn.close()
    return client


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("allwin_csrf"), **ORIGIN}


class TestBundle:
    def test_bundle_structure_and_honesty(self, app, seeded, fresh_ip):
        client = _analyst_client(app, fresh_ip)
        r = client.get("/api/v1/studio/matches/9001/bundle")
        assert r.status_code == 200
        b = r.json()
        assert b["bundle_version"] == "1"
        assert [s["id"] for s in b["script_sections"]] == [
            "hook", "context", "data", "probability", "risk", "outro"]
        assert b["prediction_member"]["home_probability"] == 0.48
        assert b["prediction_public"] == {"top_outcome": "home", "top_probability": 0.48}
        # 诚实性:无特征数据 → uncertainty 明示
        kinds = {u["kind"] for u in b["uncertainty"]}
        assert "features_missing" in kinds and "kickoff_precision" in kinds
        assert b["subtitle_cues"] and b["bundle_hash"]
        assert r.headers["cache-control"] == "private, no-store"

    def test_bundle_without_prediction_is_honest(self, app, seeded, fresh_ip):
        client = _analyst_client(app, fresh_ip)
        b = client.get("/api/v1/studio/matches/9101/bundle").json()
        assert b["prediction_public"] is None and b["prediction_member"] is None
        prob_sec = next(s for s in b["script_sections"] if s["id"] == "probability")
        assert "暂无已发布的模型概率" in prob_sec["text"]

    def test_studio_requires_analyst_role(self, app, seeded, fresh_ip):
        client = TestClient(app)
        r1 = client.get("/api/v1/auth/wechat/oa/start?next=/", follow_redirects=False,
                        headers={"x-real-ip": fresh_ip})
        client.get(r1.headers["location"], follow_redirects=False)
        assert client.get("/api/v1/studio/matches/9001/bundle").status_code == 403
        anon = TestClient(app)
        assert anon.get("/api/v1/studio/matches/9001/bundle").status_code == 401


class TestDraftsAndExports:
    def test_full_draft_export_flow(self, app, seeded, fresh_ip):
        client = _analyst_client(app, fresh_ip)
        # 冻结草稿
        r = client.post("/api/v1/studio/drafts", json={"match_id": 9001}, headers=_csrf(client))
        assert r.status_code == 200, r.text
        draft_id = r.json()["draft_id"]

        # 编辑标题与口播稿覆盖
        r = client.post(f"/api/v1/studio/drafts/{draft_id}",
                        json={"title": "阿森纳能赢吗", "overrides": {"risks": ["平局风险"]}},
                        headers=_csrf(client))
        assert r.status_code == 200
        d = client.get(f"/api/v1/studio/drafts/{draft_id}").json()
        assert d["title"] == "阿森纳能赢吗" and d["overrides"]["risks"] == ["平局风险"]

        # 状态流转
        for st in ("reviewed", "published"):
            assert client.post(f"/api/v1/studio/drafts/{draft_id}/status",
                               json={"status": st}, headers=_csrf(client)).status_code == 200

        # 服务端导出:txt / json / srt
        for kind, marker in (("txt", "数据截止"), ("json", "bundle"), ("srt", "-->")):
            r = client.post(f"/api/v1/studio/drafts/{draft_id}/export",
                            json={"kind": kind}, headers=_csrf(client))
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["side"] == "server" and body["model_version"] == "m-studio"
            dl = client.get(body["download_url"])
            assert dl.status_code == 200
            assert marker in dl.text

        # PNG:客户端渲染,服务端登记审计
        r = client.post(f"/api/v1/studio/drafts/{draft_id}/export",
                        json={"kind": "png_1080x1920"}, headers=_csrf(client))
        assert r.json()["side"] == "client"

        conn = connect_rw("platform")
        kinds = [row[0] for row in conn.execute("SELECT kind FROM export_jobs ORDER BY created_at")]
        conn.close()
        assert set(kinds) == {"txt", "json", "srt", "png_1080x1920"}

    def test_export_download_requires_owner(self, app, seeded, fresh_ip):
        client = _analyst_client(app, fresh_ip)
        r = client.post("/api/v1/studio/drafts", json={"match_id": 9001}, headers=_csrf(client))
        draft_id = r.json()["draft_id"]
        job = client.post(f"/api/v1/studio/drafts/{draft_id}/export",
                          json={"kind": "txt"}, headers=_csrf(client)).json()
        # 第二个用户(CLI admin,具备 analyst 权限但非文件所有者)
        from backend.cli.create_admin import create_admin

        conn = connect_rw("platform")
        create_admin(conn, "other-admin", "pass-12345678", reset=True)
        conn.close()
        other = TestClient(app)
        other.post("/api/v1/auth/password/login",
                   json={"username": "other-admin", "password": "pass-12345678"},
                   headers={"x-real-ip": fresh_ip + "1"})
        assert other.get(job["download_url"]).status_code == 404


class TestAnalytics:
    def test_event_recorded_minimal(self, app, seeded, fresh_ip):
        client = TestClient(app)
        r = client.post("/api/v1/analytics/events",
                        json={"event": "landing_view", "anon_id": "anon-1", "path": "/"},
                        headers={"x-real-ip": fresh_ip})
        assert r.status_code == 204
        conn = connect_rw("platform")
        row = conn.execute("SELECT * FROM analytics_events").fetchone()
        conn.close()
        assert row["event"] == "landing_view" and row["user_id"] is None
        # 表结构本身不含 IP/openid 字段
        assert "ip" not in row.keys()

    def test_unknown_event_rejected(self, app, seeded, fresh_ip):
        client = TestClient(app)
        r = client.post("/api/v1/analytics/events", json={"event": "evil_event"},
                        headers={"x-real-ip": fresh_ip})
        assert r.status_code == 400
