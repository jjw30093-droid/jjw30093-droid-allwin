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
        conn, match_id=9001, kickoff_at_utc="2027-04-01T14:30:00Z",
        kickoff_precision="exact", kickoff_source="fotmob:fixtures",
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

    def test_bundle_kickoff_precision_uncertainty_by_provenance(self, app, seeded, fresh_ip):
        """15. Studio 的 kickoff 数据质量提示按 kickoff_precision 判定,不能只看
        kickoff_at_utc 是否非空——date_only/unknown 必须提示,exact 则不提示。

        kickoff_precision/kickoff_source 列由 core migration(0002)保证存在,
        seed_core_schema 的 CREATE TABLE IF NOT EXISTS 只在空库时生效,不覆盖。
        """
        core = connect_rw("core")
        # 9001:kickoff_at_utc 本身为 NULL(seed_basic_core 默认),precision 显式 unknown
        core.execute("UPDATE dim_match SET kickoff_precision='unknown' WHERE Match_ID=9001")
        # 9101:人为给一个"形似有效"的 kickoff_at_utc,但 precision=date_only——
        # 验证提示逻辑真的按 precision 判断,不是只看 kickoff_at_utc 是否有值。
        core.execute(
            "UPDATE dim_match SET kickoff_at_utc='2026-05-10T14:00:00Z', kickoff_precision='date_only'"
            " WHERE Match_ID=9101"
        )
        core.commit()
        core.close()

        client = _analyst_client(app, fresh_ip)
        b1 = client.get("/api/v1/studio/matches/9001/bundle").json()
        u1 = next(u for u in b1["uncertainty"] if u["kind"] == "kickoff_precision")
        # unknown 不能谎称"只精确到比赛日",用更通用表述
        assert "缺少可验证的精确开球时间" in u1["text"]
        b2 = client.get("/api/v1/studio/matches/9101/bundle").json()
        u2 = next(u for u in b2["uncertainty"] if u["kind"] == "kickoff_precision")
        # date_only 才如实说"只精确到比赛日"
        assert "只精确到比赛日" in u2["text"]

    def _set_kickoff(self, ko, precision, source, mid=9001):
        core = connect_rw("core")
        core.execute(
            "UPDATE dim_match SET kickoff_at_utc=?, kickoff_precision=?, kickoff_source=?"
            " WHERE Match_ID=?", (ko, precision, source, mid),
        )
        core.commit()
        core.close()

    def test_bundle_exact_source_null_shows_uncertainty(self, app, seeded, fresh_ip):
        """16. exact + source=NULL:precision 字段看似精确,但缺可追溯来源 → 必须提示。"""
        self._set_kickoff("2027-04-01T14:30:00Z", "exact", None)
        client = _analyst_client(app, fresh_ip)
        b = client.get("/api/v1/studio/matches/9001/bundle").json()
        u = next(u for u in b["uncertainty"] if u["kind"] == "kickoff_precision")
        assert "缺少可验证的精确开球时间" in u["text"]

    def test_bundle_exact_naive_datetime_shows_uncertainty(self, app, seeded, fresh_ip):
        """17. exact + naive(无显式时区)→ 不可信,必须提示。"""
        self._set_kickoff("2027-04-01T14:30:00", "exact", "fotmob:fixtures")   # 无 Z/offset
        client = _analyst_client(app, fresh_ip)
        b = client.get("/api/v1/studio/matches/9001/bundle").json()
        u = next(u for u in b["uncertainty"] if u["kind"] == "kickoff_precision")
        assert "缺少可验证的精确开球时间" in u["text"]

    def test_bundle_exact_invalid_time_shows_uncertainty(self, app, seeded, fresh_ip):
        """18. exact + 非法时间字符串 → 必须提示,不得当成精确。"""
        self._set_kickoff("not-a-real-time", "exact", "fotmob:fixtures")
        client = _analyst_client(app, fresh_ip)
        b = client.get("/api/v1/studio/matches/9001/bundle").json()
        assert "kickoff_precision" in {u["kind"] for u in b["uncertainty"]}

    def test_bundle_exact_kickoff_no_precision_uncertainty(self, app, seeded, fresh_ip):
        """精确 kickoff(exact + 来源)不再出现开球精度不确定性提示。"""
        core = connect_rw("core")
        core.execute(
            "UPDATE dim_match SET kickoff_at_utc='2027-04-01T14:30:00Z',"
            " kickoff_precision='exact', kickoff_source='fotmob:fixtures' WHERE Match_ID=9001"
        )
        core.commit()
        core.close()

        client = _analyst_client(app, fresh_ip)
        b = client.get("/api/v1/studio/matches/9001/bundle").json()
        assert "kickoff_precision" not in {u["kind"] for u in b["uncertainty"]}

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
