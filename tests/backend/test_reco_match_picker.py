"""每日精选录入用的比赛候选 + 真实盘口选项(2026-08-14 新增)。

背景:admin 录入推荐单原来要手打「比赛描述」和「赔率」两处自由文本,容易和
真实比赛/真实盘口对不上。本文件覆盖两个新端点:
- GET /admin/reco/match-candidates:从真实 dim_match 选比赛,不再手打描述;
- GET /admin/reco/match-candidates/{id}/odds-options:从真实 bronze_ng_odds_snap
  选赔率(1x2/大小球/角球大小),不再手打数字;没有真实数据时诚实返回空列表。
"""

import json

import pytest
from fastapi.testclient import TestClient

from backend.db.connections import connect_rw

from .authflow import wechat_scan_login
from .coreseed import seed_basic_core

ORIGIN = {"Origin": "http://localhost:3000"}


def _admin_client(app, ip, username="picker-admin"):
    from backend.cli.create_admin import create_admin

    conn = connect_rw("platform")
    create_admin(conn, username, "picker-admin-pw-1", reset=True)
    conn.close()
    c = TestClient(app)
    r = c.post("/api/v1/auth/password/login",
               json={"username": username, "password": "picker-admin-pw-1"},
               headers={"x-real-ip": ip})
    assert r.status_code == 200
    return c


def _member_client(app, ip, openid="picker-member"):
    c = TestClient(app)
    wechat_scan_login(c, openid=openid, ip=ip)
    return c


def _seed_xref(conn_odds, titan_id, fotmob_match_id):
    conn_odds.execute(
        """INSERT INTO dim_match_xref
           (fotmob_match_id, provider, provider_match_id, home_away_inverted, confidence,
            verified, method, review_status, created_at, updated_at)
           VALUES (?, 'nowgoal', ?, 0, 1.0, 1, 'auto', 'auto_ok',
                   '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z')""",
        (fotmob_match_id, titan_id),
    )


def _seed_snap(conn_odds, titan_id, market, payload, *, company_id="8",
                company_name="Bet365", observed_at="2026-08-13T09:00:00Z"):
    conn_odds.execute(
        """INSERT INTO bronze_ng_odds_snap
           (provider_match_id, market, company_id, company_name, market_phase,
            payload_json, payload_hash, observed_at, ingested_at, poll_run_id)
           VALUES (?, ?, ?, ?, 'pre_match', ?, 'x', ?, ?, 'run1')""",
        (titan_id, market, company_id, company_name, json.dumps(payload), observed_at, observed_at),
    )


class TestMatchCandidatesAuth:
    def test_anonymous_401(self, app, data_dir):
        anon = TestClient(app)
        assert anon.get("/api/v1/admin/reco/match-candidates").status_code == 401

    def test_member_403(self, app, data_dir, fresh_ip):
        m = _member_client(app, fresh_ip)
        assert m.get("/api/v1/admin/reco/match-candidates").status_code == 403

    def test_admin_200(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, fresh_ip)
        r = admin.get("/api/v1/admin/reco/match-candidates")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "private, no-store"


class TestMatchCandidatesContent:
    def test_only_upcoming_matches_across_leagues(self, app, data_dir, fresh_ip):
        """9001(英超,NotStarted)入选;9002(英超,Finish)/9101(西甲,Finish)不入选。"""
        seed_basic_core(data_dir)
        admin = _admin_client(app, fresh_ip)
        body = admin.get("/api/v1/admin/reco/match-candidates").json()
        ids = {m["match_id"] for m in body["matches"]}
        assert 9001 in ids
        assert 9002 not in ids and 9101 not in ids

    def test_uses_chinese_display_names_and_league_name(self, app, data_dir, fresh_ip):
        seed_basic_core(data_dir)
        admin = _admin_client(app, fresh_ip)
        body = admin.get("/api/v1/admin/reco/match-candidates").json()
        m = next(x for x in body["matches"] if x["match_id"] == 9001)
        assert m["home_name"] == "阿森纳"
        assert m["away_name"] == "切尔西"
        assert m["league_name"] == "英超"
        assert m["league_id"] == 47
        assert m["status"] == "NotStarted"

    def test_query_filters_by_team_name_case_insensitive(self, app, data_dir, fresh_ip):
        seed_basic_core(data_dir)
        admin = _admin_client(app, fresh_ip)
        hit = admin.get("/api/v1/admin/reco/match-candidates?q=chelsea").json()
        assert {m["match_id"] for m in hit["matches"]} == {9001}
        miss = admin.get("/api/v1/admin/reco/match-candidates?q=liverpool").json()
        assert miss["matches"] == []

    def test_candidates_cover_all_leagues_not_just_anonymous_free_set(self, app, data_dir, fresh_ip):
        """admin_match_candidates() 用 LEAGUE_META.keys() 而不是
        accessible_league_ids(ctx.entitlements)——录入内容要看到全部已收录
        联赛,不能被"匿名能看到哪些联赛"这条门禁顺带缩小候选池。用 league:lottery
        档(48,需登录)的比赛验证这条路径确实没有被过滤掉。"""
        from datetime import date, timedelta

        from .coreseed import insert_match, seed_core_schema

        conn = connect_rw("core")
        seed_core_schema(conn)
        kickoff_date = (date.today() + timedelta(days=3)).isoformat()
        insert_match(conn, 9502, league_id=48, season="2025/2026", date=kickoff_date,
                     home_id=2001, away_id=2002, home="戊队", away="己队", status="NotStarted")
        conn.commit()
        conn.close()

        admin = _admin_client(app, fresh_ip)
        body = admin.get("/api/v1/admin/reco/match-candidates").json()
        assert 9502 in {m["match_id"] for m in body["matches"]}


class TestOddsOptionsAuth:
    def test_anonymous_401(self, app, data_dir):
        anon = TestClient(app)
        assert anon.get("/api/v1/admin/reco/match-candidates/9001/odds-options").status_code == 401

    def test_member_403(self, app, data_dir, fresh_ip):
        m = _member_client(app, fresh_ip)
        assert m.get("/api/v1/admin/reco/match-candidates/9001/odds-options").status_code == 403

    def test_admin_200_empty_without_real_odds(self, app, data_dir, fresh_ip):
        admin = _admin_client(app, fresh_ip)
        r = admin.get("/api/v1/admin/reco/match-candidates/9001/odds-options")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "private, no-store"
        assert r.json() == {"match_id": 9001, "options": []}


class TestOddsOptionsContent:
    def test_real_1x2_ou_corners_options(self, app, data_dir, fresh_ip):
        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, "900001", 9001)
        _seed_snap(conn_odds, "900001", "1x2", {"home": 1.85, "draw": 3.60, "away": 4.20})
        _seed_snap(conn_odds, "900001", "ou", {"over": 1.9, "line": 2.5, "under": 1.9})
        _seed_snap(conn_odds, "900001", "corners_ou", {"over": 0.9, "line": 10.5, "under": 0.9})
        conn_odds.commit()
        conn_odds.close()

        admin = _admin_client(app, fresh_ip)
        body = admin.get("/api/v1/admin/reco/match-candidates/9001/odds-options").json()
        by_selection = {(o["market"], o["selection"]): o for o in body["options"]}

        assert by_selection[("1x2", "主胜")]["odds"] == 1.85
        assert by_selection[("1x2", "平局")]["odds"] == 3.60
        assert by_selection[("1x2", "客胜")]["odds"] == 4.20
        assert by_selection[("1x2", "主胜")]["market_label"] == "胜平负"
        assert by_selection[("1x2", "主胜")]["company_name"] == "Bet365"

        assert by_selection[("ou", "大2.5")]["odds"] == 1.9
        assert by_selection[("ou", "小2.5")]["odds"] == 1.9
        assert by_selection[("ou", "大2.5")]["market_label"] == "大小球"

        assert by_selection[("corners_ou", "大角球10.5")]["odds"] == 0.9
        assert by_selection[("corners_ou", "小角球10.5")]["odds"] == 0.9
        assert by_selection[("corners_ou", "大角球10.5")]["market_label"] == "角球大小"

        assert len(body["options"]) == 7   # 3 + 2 + 2,不多不少

    def test_realtime_company_wins_over_legacy_backfill(self, app, data_dir, fresh_ip):
        """公司优先级同 latest_1x2_by_match:实时轮询 '8' 优先于历史回填 '281'。"""
        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, "900002", 9001)
        _seed_snap(conn_odds, "900002", "1x2", {"home": 9.99, "draw": 9.99, "away": 9.99},
                   company_id="281", company_name="历史回填源")
        _seed_snap(conn_odds, "900002", "1x2", {"home": 1.85, "draw": 3.60, "away": 4.20},
                   company_id="8", company_name="Bet365")
        conn_odds.commit()
        conn_odds.close()

        admin = _admin_client(app, fresh_ip)
        body = admin.get("/api/v1/admin/reco/match-candidates/9001/odds-options").json()
        home_option = next(o for o in body["options"] if o["selection"] == "主胜")
        assert home_option["odds"] == 1.85
        assert home_option["company_name"] == "Bet365"

    def test_latest_snapshot_wins_over_stale(self, app, data_dir, fresh_ip):
        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, "900003", 9001)
        _seed_snap(conn_odds, "900003", "1x2", {"home": 2.00, "draw": 3.00, "away": 3.50},
                   observed_at="2026-08-10T00:00:00Z")
        _seed_snap(conn_odds, "900003", "1x2", {"home": 1.85, "draw": 3.60, "away": 4.20},
                   observed_at="2026-08-13T09:00:00Z")
        conn_odds.commit()
        conn_odds.close()

        admin = _admin_client(app, fresh_ip)
        body = admin.get("/api/v1/admin/reco/match-candidates/9001/odds-options").json()
        home_option = next(o for o in body["options"] if o["selection"] == "主胜")
        assert home_option["odds"] == 1.85

    def test_incomplete_market_payload_yields_no_options_for_that_market_only(
        self, app, data_dir, fresh_ip
    ):
        """1x2 缺 draw 字段 → 整个 1x2 市场跳过(不编造),ou 市场不受影响。"""
        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, "900004", 9001)
        _seed_snap(conn_odds, "900004", "1x2", {"home": 1.85, "away": 4.20})   # 缺 draw
        _seed_snap(conn_odds, "900004", "ou", {"over": 1.9, "line": 2.5, "under": 1.9})
        conn_odds.commit()
        conn_odds.close()

        admin = _admin_client(app, fresh_ip)
        body = admin.get("/api/v1/admin/reco/match-candidates/9001/odds-options").json()
        markets = {o["market"] for o in body["options"]}
        assert "1x2" not in markets
        assert "ou" in markets

    def test_unresolved_xref_yields_empty(self, app, data_dir, fresh_ip):
        """review_status 非 auto_ok/confirmed 的映射不可信,不得当真实盘口用。"""
        conn_odds = connect_rw("odds")
        conn_odds.execute(
            """INSERT INTO dim_match_xref
               (fotmob_match_id, provider, provider_match_id, home_away_inverted, confidence,
                verified, method, review_status, created_at, updated_at)
               VALUES (9001, 'nowgoal', '900005', 0, 0.4, 0, 'auto', 'needs_review',
                       '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z')"""
        )
        _seed_snap(conn_odds, "900005", "1x2", {"home": 1.85, "draw": 3.60, "away": 4.20})
        conn_odds.commit()
        conn_odds.close()

        admin = _admin_client(app, fresh_ip)
        body = admin.get("/api/v1/admin/reco/match-candidates/9001/odds-options").json()
        assert body["options"] == []
