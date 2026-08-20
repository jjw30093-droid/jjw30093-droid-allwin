"""每日精选录入用的比赛候选 + 真实盘口选项(2026-08-14 新增)。

背景:admin 录入推荐单原来要手打「比赛描述」和「赔率」两处自由文本,容易和
真实比赛/真实盘口对不上。本文件覆盖两个新端点:
- GET /admin/reco/match-candidates:从真实 dim_match 选比赛,不再手打描述;
- GET /admin/reco/match-candidates/{id}/odds-options:从真实 bronze_ng_odds_snap
  选赔率(1x2/大小球/角球大小/让球盘),不再手打数字;没有真实数据时诚实返回
  空列表。

让球盘(ah,2026-08-19 新增)此前被排除在自动选项外(担心 admin 记反让球
方向),但 docs/data-sources.md §2.5(2026-08-16)已经用 48 组精确配对 +
2,834 组独立历史样本交叉验证过符号约定(line>0=主队让球/热门,line<0=客队
让球/热门),backend/commands/reco_settlement_math.py::_resolve_ah 也已经在
用这套约定做自动结算——本次只是把同一套已验证的约定接进选项列表,不引入
新语义。选项文案刻意不用符号(不写"-0.5"/"+0.5"),直接写"让"/"受让"大白话,
从根上避开当初怕记反的那个顾虑。
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
    """本类下面几个测试的关注点是搜索/展示逻辑本身,不是 window 参数(window
    参数的默认值/放宽行为见 test_reco.py::TestMatchCandidatesWindowDefault)。
    seed_basic_core() 造的比赛没有精确 kickoff_at_utc(只有自然日 Date),
    2026-08-16 起 window 默认 7 天会把"无精确开球时间"的比赛排除在默认搜索外
    (§6.2.1:不得把缺失的精确时间伪装成"在窗口内"),因此这里显式传
    window=all 保持这些测试原本要验证的内容不受 window 默认值变化影响。"""

    def test_only_upcoming_matches_across_leagues(self, app, data_dir, fresh_ip):
        """9001(英超,NotStarted)入选;9002(英超,Finish)/9101(西甲,Finish)不入选。"""
        seed_basic_core(data_dir)
        admin = _admin_client(app, fresh_ip)
        body = admin.get("/api/v1/admin/reco/match-candidates?window=all").json()
        ids = {m["match_id"] for m in body["matches"]}
        assert 9001 in ids
        assert 9002 not in ids and 9101 not in ids

    def test_uses_chinese_display_names_and_league_name(self, app, data_dir, fresh_ip):
        seed_basic_core(data_dir)
        admin = _admin_client(app, fresh_ip)
        body = admin.get("/api/v1/admin/reco/match-candidates?window=all").json()
        m = next(x for x in body["matches"] if x["match_id"] == 9001)
        assert m["home_name"] == "阿森纳"
        assert m["away_name"] == "切尔西"
        assert m["league_name"] == "英超"
        assert m["league_id"] == 47
        assert m["status"] == "NotStarted"

    def test_query_filters_by_team_name_case_insensitive(self, app, data_dir, fresh_ip):
        seed_basic_core(data_dir)
        admin = _admin_client(app, fresh_ip)
        hit = admin.get("/api/v1/admin/reco/match-candidates?q=chelsea&window=all").json()
        assert {m["match_id"] for m in hit["matches"]} == {9001}
        miss = admin.get("/api/v1/admin/reco/match-candidates?q=liverpool&window=all").json()
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
        body = admin.get("/api/v1/admin/reco/match-candidates?window=all").json()
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

    def test_real_ah_options_home_favorite(self, app, data_dir, fresh_ip):
        """line>0 = 主队让球/热门(docs/data-sources.md §2.5 验证结论)。
        文案不写符号,直接写"让"/"受让",避免 admin 需要记住符号方向。"""
        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, "900010", 9001)
        _seed_snap(conn_odds, "900010", "ah", {"home": 0.90, "line": 1.25, "away": 0.95})
        conn_odds.commit()
        conn_odds.close()

        admin = _admin_client(app, fresh_ip)
        body = admin.get("/api/v1/admin/reco/match-candidates/9001/odds-options").json()
        by_selection = {(o["market"], o["selection"]): o for o in body["options"]}

        home = by_selection[("ah", "主队让1.25球")]
        away = by_selection[("ah", "客队受让1.25球")]
        assert home["odds"] == 0.90
        assert home["side"] == "home"
        assert home["line"] == 1.25
        assert away["odds"] == 0.95
        assert away["side"] == "away"
        assert away["line"] == 1.25
        assert home["market_label"] == "让球盘"
        assert len(body["options"]) == 2

    def test_real_ah_options_away_favorite_matches_verified_real_match(
        self, app, data_dir, fresh_ip
    ):
        """真实场次核对(docs/data-sources.md §2.5 引用的样本):Vålerenga(主)
        1-2 Bodø/Glimt(客),line=-1.25(客队热门,让 1.25 球)——line<0 时
        客队让球、主队受让,不是反过来。"""
        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, "900011", 9001)
        _seed_snap(conn_odds, "900011", "ah", {"home": 0.85, "line": -1.25, "away": 1.00})
        conn_odds.commit()
        conn_odds.close()

        admin = _admin_client(app, fresh_ip)
        body = admin.get("/api/v1/admin/reco/match-candidates/9001/odds-options").json()
        by_selection = {(o["market"], o["selection"]): o for o in body["options"]}

        assert ("ah", "客队让1.25球") in by_selection
        assert ("ah", "主队受让1.25球") in by_selection
        assert by_selection[("ah", "客队让1.25球")]["side"] == "away"
        assert by_selection[("ah", "客队让1.25球")]["line"] == -1.25
        assert by_selection[("ah", "主队受让1.25球")]["side"] == "home"

    def test_real_ah_options_pick_em(self, app, data_dir, fresh_ip):
        """line=0(平手盘)不套"让"/"受让"措辞——那两个字对平手盘没有意义。"""
        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, "900012", 9001)
        _seed_snap(conn_odds, "900012", "ah", {"home": 0.95, "line": 0, "away": 0.95})
        conn_odds.commit()
        conn_odds.close()

        admin = _admin_client(app, fresh_ip)
        body = admin.get("/api/v1/admin/reco/match-candidates/9001/odds-options").json()
        selections = {o["selection"] for o in body["options"] if o["market"] == "ah"}
        assert selections == {"主队(平手盘)", "客队(平手盘)"}

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

    def test_incomplete_ah_payload_yields_no_options_for_that_market_only(
        self, app, data_dir, fresh_ip
    ):
        """ah 缺 line 字段 → 整个 ah 市场跳过,1x2 不受影响(同上一条同一规则,
        ah 是新加的市场,必须单独钉住,不能只靠 1x2/ou 那条覆盖)。"""
        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, "900013", 9001)
        _seed_snap(conn_odds, "900013", "ah", {"home": 0.90, "away": 0.95})  # 缺 line
        _seed_snap(conn_odds, "900013", "1x2", {"home": 1.85, "draw": 3.60, "away": 4.20})
        conn_odds.commit()
        conn_odds.close()

        admin = _admin_client(app, fresh_ip)
        body = admin.get("/api/v1/admin/reco/match-candidates/9001/odds-options").json()
        markets = {o["market"] for o in body["options"]}
        assert "ah" not in markets
        assert "1x2" in markets

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
