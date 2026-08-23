"""P0.9/P0.10 测试:analysis_bundle、Studio 草稿/导出、埋点。"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.commands.predictions import (
    get_or_create_model_version,
    publish_snapshot,
    register_snapshot,
)
from backend.db.connections import connect_rw

from .coreseed import seed_basic_core

from .authflow import wechat_scan_login

ORIGIN = {"Origin": "http://localhost:3000"}


@pytest.fixture
def seeded(data_dir):
    kickoff = (
        datetime.now(timezone.utc) + timedelta(days=3)
    ).replace(microsecond=0)
    kickoff_at_utc = kickoff.isoformat().replace("+00:00", "Z")

    seed_basic_core(data_dir)
    conn = connect_rw("platform")
    get_or_create_model_version(conn, "m-studio", "dixon-coles")
    sid = register_snapshot(
        conn, match_id=9001, kickoff_at_utc=kickoff_at_utc,
        kickoff_precision="exact", kickoff_source="fotmob:fixtures",
        model_version_id="m-studio", home_win=0.48, draw=0.29, away_win=0.23,
        expected_home_goals=1.6, expected_away_goals=1.0, status="draft",
    )
    assert kickoff > datetime.now(timezone.utc)
    publish_snapshot(conn, sid, actor=None)
    conn.close()
    return data_dir


def _analyst_client(app, ip):
    client = TestClient(app)
    wechat_scan_login(client, ip=ip)
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
        note = next(n for n in b["source_notes"] if n["kind"] == "probability_source")
        # 内部枚举值不得原样出现在用户可见文案里(CLAUDE.md §11.2);
        # 必须换成中文标签"模型"。
        assert "MODEL" not in note["text"]
        assert "模型" in note["text"]
        assert "概率来自预测登记簿的已发布快照" in note["text"]

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
        # 2026-08-12 修复的真 bug:UNAVAILABLE 曾经落进"概率来自...已发布
        # 快照"分支,同一句话里自相矛盾(UNAVAILABLE = 没有快照)。
        note = next(n for n in b["source_notes"] if n["kind"] == "probability_source")
        # 2026-08-23 修复的真 bug:生产实测发现这里把原始枚举值 UNAVAILABLE
        # 原样显示给了用户(CLAUDE.md §11.2 禁止内部枚举值出现在用户界面);
        # 必须换成中文标签"暂无"。
        assert "UNAVAILABLE" not in note["text"]
        assert "暂无" in note["text"]
        # 旧 bug 的确切措辞是"概率来自预测登记簿的已发布快照"(声称快照存在);
        # 新文案改成"暂无已发布的预测快照"(否定句),两者不能同时出现。
        assert "概率来自预测登记簿的已发布快照" not in note["text"]
        assert "暂无已发布的预测快照" in note["text"]

    def test_studio_requires_analyst_role(self, app, seeded, fresh_ip):
        client = TestClient(app)
        wechat_scan_login(client, ip=fresh_ip)
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

    def test_douyin_safe_export_physically_excludes_full_bundle_fields(
        self, app, seeded, fresh_ip
    ):
        client = _analyst_client(app, fresh_ip)
        created = client.post(
            "/api/v1/studio/drafts",
            json={"match_id": 9001},
            headers=_csrf(client),
        ).json()
        draft_id = created["draft_id"]
        conn = connect_rw("platform")
        row = conn.execute(
            "SELECT bundle_json FROM content_drafts WHERE id=?", (draft_id,)
        ).fetchone()
        bundle = json.loads(row["bundle_json"])
        bundle["social_profiles"] = {
            "douyin-safe-v1": {
                "profile_id": "douyin-safe-v1",
                "profile_version": 1,
                "source_hash": "abc123",
                "data_cutoff_at": "2026-07-20T00:00:00Z",
                "match": {
                    "match_id": 9001,
                    "league_name": "测试联赛",
                    "season": "2026",
                    "round": "1",
                    "kickoff_at_utc": "2026-08-01T12:00:00Z",
                    "home": {"team_id": 1, "name": "甲队", "crest_url": None},
                    "away": {"team_id": 2, "name": "乙队", "crest_url": None},
                },
                "scenes": [],
                "script_sections": [
                    {"id": "opening", "title": "开场", "text": "两队打法数据拆解。"}
                ],
                "subtitle_cues": [
                    {"start": 0.0, "end": 2.5, "text": "两队打法数据拆解。"}
                ],
                "titles": ["两队打法差在哪？"],
                "xiaohongshu_text": "从控球与禁区触球看比赛方式。",
                "wechat_summary": "赛季球队风格摘要。",
                "source_note": "真实赛季统计。",
            }
        }
        conn.execute(
            "UPDATE content_drafts SET bundle_json=? WHERE id=?",
            (json.dumps(bundle, ensure_ascii=False), draft_id),
        )
        conn.commit()
        conn.close()

        response = client.post(
            f"/api/v1/studio/drafts/{draft_id}/export",
            json={"kind": "json", "profile": "douyin-safe-v1"},
            headers=_csrf(client),
        )
        assert response.status_code == 200, response.text
        exported = client.get(response.json()["download_url"]).text
        assert '"profile"' in exported
        for key in (
            "prediction_member",
            "prediction_public",
            "odds_timeline",
            "market_baseline",
            "probability_source",
        ):
            assert key not in exported
        assert "0.48" not in exported


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
