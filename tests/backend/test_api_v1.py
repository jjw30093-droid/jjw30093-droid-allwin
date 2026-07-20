"""P0.6 测试:/api/v1 字段级门禁矩阵、联赛门禁、track record、模型指标、缓存头。"""

import json

import pytest
from fastapi.testclient import TestClient

from backend.commands.predictions import (
    get_or_create_model_version,
    lock_snapshot,
    publish_snapshot,
    register_snapshot,
)
from backend.commands.subscriptions import grant_subscription
from backend.db.connections import connect_rw

from .coreseed import seed_basic_core

FORBIDDEN_FREE_KEYS = [
    "home_probability", "draw_probability", "away_probability",
    "home_win", "away_win", '"draw"',
    "expected_home_goals", "expected_away_goals",
]


@pytest.fixture
def seeded(data_dir):
    seed_basic_core(data_dir)
    conn = connect_rw("platform")
    get_or_create_model_version(conn, "m-api", "dixon-coles")
    # 9001:已发布的公开预测(exact kickoff 2027-04-01 未来 + 可追溯来源)
    sid = register_snapshot(
        conn, match_id=9001, kickoff_at_utc="2027-04-01T14:30:00Z",
        kickoff_precision="exact", kickoff_source="fotmob:fixtures",
        model_version_id="m-api", home_win=0.48, draw=0.29, away_win=0.23,
        expected_home_goals=1.62, expected_away_goals=1.01, status="draft",
    )
    publish_snapshot(conn, sid, actor=None)   # 只发布不锁定:published 可对外,但不是正式样本
    # 9002:draft(绝不能对外)
    register_snapshot(
        conn, match_id=9002, kickoff_at_utc="2027-05-01T14:00:00Z",
        kickoff_precision="exact", kickoff_source="fotmob:fixtures",
        model_version_id="m-api", home_win=0.5, draw=0.3, away_win=0.2, status="draft",
    )
    conn.close()
    return data_dir


def _login_user(client, ip="203.0.113.99"):
    r1 = client.get("/api/v1/auth/wechat/oa/start?next=/", follow_redirects=False,
                    headers={"x-real-ip": ip})
    client.get(r1.headers["location"], follow_redirects=False)
    return client.get("/api/v1/me").json()["user"]["id"]


def _grant(user_id, plan="pro"):
    conn = connect_rw("platform")
    grant_subscription(conn, user_id, plan, 30, granted_by=None, source="admin_grant")
    conn.close()


class TestPredictionFieldGate:
    def test_anonymous_gets_only_top_probability(self, app, seeded):
        client = TestClient(app)
        r = client.get("/api/v1/matches/9001/prediction")
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is True
        pred = body["prediction"]
        assert pred["tier"] == "free"
        assert pred["top_outcome"] == "home"
        assert pred["top_probability"] == 0.48
        # 全 JSON 扫描:受限字段连 key 都不存在(不是 null)
        raw = r.text
        for key in FORBIDDEN_FREE_KEYS:
            assert key not in raw, f"免费响应泄漏受限字段 {key}: {raw}"
        assert r.headers["cache-control"] == "private, no-store"

    def test_free_logged_in_same_as_anonymous(self, app, seeded, fresh_ip):
        client = TestClient(app)
        _login_user(client, ip=fresh_ip)
        r = client.get("/api/v1/matches/9001/prediction")
        assert r.json()["prediction"]["tier"] == "free"
        for key in FORBIDDEN_FREE_KEYS:
            assert key not in r.text

    def test_pro_gets_full_wdl(self, app, seeded, fresh_ip):
        client = TestClient(app)
        user_id = _login_user(client, ip=fresh_ip)
        _grant(user_id, "pro")
        r = client.get("/api/v1/matches/9001/prediction")
        pred = r.json()["prediction"]
        assert pred["tier"] == "full"
        assert pred["home_probability"] == 0.48
        assert pred["draw_probability"] == 0.29
        assert pred["away_probability"] == 0.23
        assert pred["expected_home_goals"] == 1.62
        assert pred["prediction_hash"]

    def test_premium_gets_full_wdl(self, app, seeded, fresh_ip):
        client = TestClient(app)
        user_id = _login_user(client, ip=fresh_ip)
        _grant(user_id, "premium")
        assert client.get("/api/v1/matches/9001/prediction").json()["prediction"]["tier"] == "full"

    def test_draft_never_exposed(self, app, seeded, fresh_ip):
        client = TestClient(app)
        user_id = _login_user(client, ip=fresh_ip)
        _grant(user_id, "premium")
        r = client.get("/api/v1/matches/9002/prediction")
        body = r.json()
        assert body["available"] is False
        assert body["prediction"] is None
        assert "0.5" not in r.text

    def test_no_snapshot_honest_empty(self, app, seeded):
        client = TestClient(app)
        body = client.get("/api/v1/matches/9101/prediction").json()   # 西甲场无预测
        # 匿名无 league:top5 → 先撞联赛门禁
        assert body.get("available") is None or body.get("detail")


class TestLeagueGate:
    def test_anonymous_sees_only_epl_matches(self, app, seeded):
        client = TestClient(app)
        r = client.get("/api/v1/matches")
        ids = {m["league_id"] for m in r.json()["matches"]}
        assert ids <= {47}
        assert client.get("/api/v1/matches/9101").status_code == 401
        assert client.get("/api/v1/leagues/87/standings").status_code == 401
        leagues = client.get("/api/v1/leagues").json()
        acc = {l["league_id"]: l["accessible"] for l in leagues}
        assert acc[47] is True and acc[87] is False

    def test_pro_sees_top5(self, app, seeded, fresh_ip):
        client = TestClient(app)
        user_id = _login_user(client, ip=fresh_ip)
        _grant(user_id, "pro")
        assert client.get("/api/v1/matches/9101").status_code == 200
        ids = {m["league_id"] for m in client.get("/api/v1/matches?status=finished").json()["matches"]}
        assert 87 in ids

    def test_epl_public_endpoints_ok(self, app, seeded):
        client = TestClient(app)
        st = client.get("/api/v1/leagues/47/standings")
        assert st.status_code == 200
        assert st.json()["rows"][0]["team"]["name"] == "阿森纳"
        fx = client.get("/api/v1/leagues/47/fixtures")
        assert fx.status_code == 200 and fx.json()["total"] >= 1
        det = client.get("/api/v1/matches/9001").json()
        assert det["match"]["home"]["name"] == "阿森纳"


class TestTrackRecordApi:
    def test_empty_state_honest(self, app, seeded):
        client = TestClient(app)
        r = client.get("/api/v1/track-record")
        body = r.json()
        assert body["total"] == 0
        assert "暂无符合口径的正式样本" in body["empty_reason"]
        assert "public" in r.headers["cache-control"]

    def test_locked_official_sample_listed_draft_not(self, app, seeded):
        # 造一个已完赛的官方样本(直接 SQL,kickoff 过去、发布在开球前)
        conn = connect_rw("platform")
        conn.execute(
            """INSERT INTO prediction_snapshots
               (id, match_id, kickoff_at_utc, model_version_id, generated_at, published_at, locked_at,
                prediction_hash, home_win, draw, away_win, visibility, status, is_official, created_at)
               VALUES ('tr1', 9002, '2026-05-01T00:00:00Z', 'm-api', '2026-04-30T10:00:00Z',
                       '2026-04-30T10:00:00Z', '2026-04-30T11:00:00Z', 'h',
                       0.6, 0.25, 0.15, 'public', 'locked', 1, '2026-04-30T10:00:00Z')"""
        )
        conn.execute(
            "INSERT INTO prediction_outcomes (match_id, home_goals, away_goals, outcome, settled_at)"
            " VALUES (9002, 2, 0, 'home', '2026-05-02T00:00:00Z')"
        )
        conn.close()
        client = TestClient(app)
        body = client.get("/api/v1/track-record").json()
        assert body["total"] == 1
        s = body["samples"][0]
        assert s["match_id"] == 9002 and s["hit"] is True
        assert s["home"]["name"] == "阿森纳"


class TestModelMetrics:
    def test_metrics_endpoint_honest(self, app, seeded):
        client = TestClient(app)
        body = client.get("/api/v1/model/metrics").json()
        assert body["market_baseline"]["status"] == "UNVERIFIED"
        assert body["official_evaluation"] is None
        assert "暂无正式样本评估" in body["official_evaluation_note"]


class TestProbes:
    def test_healthz_readyz(self, app, seeded):
        client = TestClient(app)
        assert client.get("/healthz").json() == {"ok": True}
        assert client.get("/readyz").json()["ok"] is True
