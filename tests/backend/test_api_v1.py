"""P0.6 测试:/api/v1 字段级门禁矩阵、联赛门禁、track record、模型指标、缓存头。"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.commands.predictions import (
    get_or_create_model_version,
    lock_snapshot,
    publish_snapshot,
    register_snapshot,
)
from backend.db.connections import connect_rw

from .coreseed import seed_basic_core

from .authflow import wechat_scan_login

FORBIDDEN_FREE_KEYS = [
    "home_probability", "draw_probability", "away_probability",
    "home_win", "away_win", '"draw"',
    "expected_home_goals", "expected_away_goals",
]


@pytest.fixture
def seeded(data_dir):
    now_before_seed = datetime.now(timezone.utc)
    kickoff = (now_before_seed + timedelta(days=3)).replace(microsecond=0)
    kickoff_at_utc = kickoff.isoformat().replace("+00:00", "Z")
    match_date = kickoff.date().isoformat()
    season_start = kickoff.year if kickoff.month >= 7 else kickoff.year - 1
    season = f"{season_start}/{season_start + 1}"

    seed_basic_core(data_dir)
    conn_core = connect_rw("core")
    conn_core.execute(
        "UPDATE dim_match SET Season=?, Date=?, kickoff_at_utc=?, "
        "kickoff_precision='exact', kickoff_source='test:dynamic_fixture' "
        "WHERE Match_ID=9001",
        (season, match_date, kickoff_at_utc),
    )
    conn_core.commit()
    conn_core.close()

    conn = connect_rw("platform")
    get_or_create_model_version(conn, "m-api", "dixon-coles")
    # 9001:已发布的公开预测(执行时 UTC now +3 天 + 可追溯来源)
    sid = register_snapshot(
        conn, match_id=9001, kickoff_at_utc=kickoff_at_utc,
        kickoff_precision="exact", kickoff_source="fotmob:fixtures",
        model_version_id="m-api", home_win=0.48, draw=0.29, away_win=0.23,
        expected_home_goals=1.62, expected_away_goals=1.01, status="draft",
    )
    assert kickoff > datetime.now(timezone.utc)
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
    wechat_scan_login(client, ip=ip)
    return client.get("/api/v1/me").json()["user"]["id"]


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

    def test_logged_in_gets_full_projection(self, app, seeded, fresh_ip):
        """三段可见性(CLAUDE.md §8):登录即 member 基线 → 完整投影。"""
        client = TestClient(app)
        _login_user(client, ip=fresh_ip)
        r = client.get("/api/v1/matches/9001/prediction")
        assert r.json()["prediction"]["tier"] == "full"

    def test_member_gets_full_wdl(self, app, seeded, fresh_ip):
        client = TestClient(app)
        _login_user(client, ip=fresh_ip)
        r = client.get("/api/v1/matches/9001/prediction")
        pred = r.json()["prediction"]
        assert pred["tier"] == "full"
        assert pred["home_probability"] == 0.48
        assert pred["draw_probability"] == 0.29
        assert pred["away_probability"] == 0.23
        assert pred["expected_home_goals"] == 1.62
        assert pred["prediction_hash"]

    def test_member_baseline_gets_full_wdl_alias(self, app, seeded, fresh_ip):
        client = TestClient(app)
        _login_user(client, ip=fresh_ip)
        assert client.get("/api/v1/matches/9001/prediction").json()["prediction"]["tier"] == "full"

    def test_draft_never_exposed(self, app, seeded, fresh_ip):
        client = TestClient(app)
        _login_user(client, ip=fresh_ip)
        r = client.get("/api/v1/matches/9002/prediction")
        body = r.json()
        assert body["available"] is False
        assert body["prediction"] is None
        assert "0.5" not in r.text

    def test_no_snapshot_honest_empty(self, app, seeded):
        client = TestClient(app)
        # 9101 是西甲(87,top5,权限矩阵互换后匿名可访问)场次,无预测快照
        # → 直接诚实空态,不再撞联赛门禁
        r = client.get("/api/v1/matches/9101/prediction")
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is False
        assert body["prediction"] is None


class TestLeagueGate:
    def test_anonymous_sees_epl_and_top5_matches(self, app, seeded):
        """2026-08-11 权限矩阵互换:free 现持有 league:epl + league:top5 +
        league:european_cup(platform 0012),87(西甲)随五大联赛一起对匿名开放。"""
        client = TestClient(app)
        r = client.get("/api/v1/matches")
        ids = {m["league_id"] for m in r.json()["matches"]}
        assert ids <= {47, 87}
        assert client.get("/api/v1/matches/9101").status_code == 200
        assert client.get("/api/v1/leagues/87/standings").status_code == 200
        leagues = client.get("/api/v1/leagues").json()
        acc = {l["league_id"]: l["accessible"] for l in leagues}
        assert acc[47] is True and acc[87] is True
        assert acc[67] is False   # league:lottery 现在需登录

    def test_member_sees_top5(self, app, seeded, fresh_ip):
        client = TestClient(app)
        _login_user(client, ip=fresh_ip)
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


# silver_team_season_stats 是混合表:付费深度字段(角球/牌/零封/BTTS)绝不能
# 出现在 team-stats 免费投影响应里——布景造了哨兵值,全 JSON 扫描断言
FORBIDDEN_TEAM_STAT_KEYS = [
    "avg_corners", "avg_yellow_cards", "avg_red_cards",
    "clean_sheets", "btts_matches", "btts_pct",
    "777.7", "888.8", "999.9",  # 布景哨兵值本身也不能泄漏
]


class TestLeagueSeasonStats:
    """/leagues/{id}/team-stats 与 /players:门禁矩阵 + 免费字段纪律 + 缓存头。"""

    def test_anonymous_epl_team_stats_ok(self, app, seeded):
        client = TestClient(app)
        r = client.get("/api/v1/leagues/47/team-stats")
        assert r.status_code == 200
        body = r.json()
        assert body["season"] == "2025/2026"
        assert len(body["rows"]) == 1
        row = body["rows"][0]
        assert row["team"]["name"] == "阿森纳"
        assert row["avg_total_shots"] == 15.2
        for key in FORBIDDEN_TEAM_STAT_KEYS:
            assert key not in r.text, f"免费投影泄漏付费字段/哨兵值 {key}"
        assert r.headers["cache-control"].startswith("public")

    def test_xg_breakdown_and_conceded_for_quadrant_chart(self, app, seeded):
        """象限图取数:xG 拆解 + 每场被创造 xG。

        - 运动战/定位球/非点球是 xG(免费字段)的细分,不是付费深度字段;
        - 每场被创造 xG 由 fact_league_table 的 xg 档换算(76.0/38=2.0),
          与 xG 运气榜同源;
        - **87 没有 xg 档 → 必须是 null,不能补 0** ——补 0 会把没有数据的
          球队画成全联赛防守最好,是彻底的假信息。
        """
        client = TestClient(app)
        row = client.get("/api/v1/leagues/47/team-stats").json()["rows"][0]
        assert row["avg_expected_goals_open_play"] == 1.42
        assert row["avg_expected_goals_set_play"] == 0.54
        assert row["avg_expected_goals_non_penalty"] == 1.96
        assert row["avg_expected_goals_conceded"] == pytest.approx(2.0)

        missing = client.get("/api/v1/leagues/87/team-stats").json()["rows"][0]
        assert missing["avg_expected_goals_conceded"] is None

    def test_anonymous_epl_players_ok(self, app, seeded):
        client = TestClient(app)
        r = client.get("/api/v1/leagues/47/players")
        assert r.status_code == 200
        body = r.json()
        boards = {b["stat_name"]: b for b in body["boards"]}
        assert set(boards) == {"goals", "goal_assist", "expected_goals",
                               "expected_goalsontarget", "rating"}
        top = boards["goals"]["entries"][0]
        assert top["name"] == "测试前锋"          # 中文短名优先
        assert top["name_en"] == "Test Striker"
        assert top["team"]["name"] == "阿森纳"
        assert top["value"] == 27.0
        assert r.headers["cache-control"].startswith("public")

    def test_anonymous_top5_team_stats_ok(self, app, seeded):
        """2026-08-11 权限矩阵互换:top5(87)现对匿名开放,与 EPL 同一投影规则。"""
        client = TestClient(app)
        r = client.get("/api/v1/leagues/87/team-stats")
        assert r.status_code == 200
        assert r.json()["rows"][0]["avg_possession"] == 58.3
        for key in FORBIDDEN_TEAM_STAT_KEYS:
            assert key not in r.text, f"免费投影泄漏付费字段/哨兵值 {key}"
        assert r.headers["cache-control"].startswith("public")

    def test_anonymous_lottery_league_gated(self, app, seeded):
        """2026-08-11 权限矩阵互换:top5(87)现对匿名开放,league:lottery(67)
        改为登录门槛——本用例随之从"匿名验证 top5 被挡"改为验证 67 被挡。"""
        client = TestClient(app)
        assert client.get("/api/v1/leagues/67/team-stats").status_code == 401
        assert client.get("/api/v1/leagues/67/players").status_code == 401
        # 门禁响应体也不能带出任何统计数据
        r = client.get("/api/v1/leagues/67/team-stats")
        assert "avg_total_shots" not in r.text and "58.3" not in r.text

    def test_logged_in_top5_stats_ok(self, app, seeded, fresh_ip):
        """三段可见性:top5 统计登录即得(匿名 401 由 test_epl_public_endpoints_ok 钉死)。"""
        client = TestClient(app)
        _login_user(client, ip=fresh_ip)
        assert client.get("/api/v1/leagues/87/team-stats").status_code == 200
        assert client.get("/api/v1/leagues/87/players").status_code == 200

    def test_member_top5_team_stats_and_players_ok(self, app, seeded, fresh_ip):
        client = TestClient(app)
        _login_user(client, ip=fresh_ip)
        ts = client.get("/api/v1/leagues/87/team-stats")
        assert ts.status_code == 200
        assert ts.json()["rows"][0]["avg_possession"] == 58.3
        # 登录拿到的也是本端点的基础字段投影(深度字段属 report:deep 面,不在本端点)
        for key in FORBIDDEN_TEAM_STAT_KEYS:
            assert key not in ts.text
        assert ts.headers["cache-control"] == "private, no-store"
        pl = client.get("/api/v1/leagues/87/players")
        assert pl.status_code == 200
        top = {b["stat_name"]: b for b in pl.json()["boards"]}["goals"]["entries"][0]
        assert top["name"] == "Laliga Striker"   # 无中文映射 → 回退来源英文名
        assert top["value"] == 30.0

    def test_unknown_league_404(self, app, seeded):
        client = TestClient(app)
        assert client.get("/api/v1/leagues/9999/team-stats").status_code == 404
        assert client.get("/api/v1/leagues/9999/players").status_code == 404

    def test_fixtures_season_filter_applies_before_sql_limit(self, app, seeded):
        """回归:赛季过滤必须在 SQL LIMIT 之前。曾经的实现先 LIMIT 再 Python 筛
        赛季——多赛季联赛下小 limit 页面可能全是旧赛季的行,目标赛季 0 命中。"""
        conn = connect_rw("core")
        # 旧赛季灌 30 场(id 靠前,kickoff 全 NULL 时排序靠它们);新赛季 2 场
        for i in range(30):
            conn.execute(
                "INSERT OR REPLACE INTO dim_match (Match_ID, Season, League_ID, Date,"
                " Home_Team_ID, Away_Team_ID, Home_Team_Name, Away_Team_Name,"
                " home_score, away_score, status, Match_Round)"
                " VALUES (?, '2019/2020', 47, '2020-05-01', 1001, 1002,"
                " 'Arsenal', 'Chelsea', 1, 0, 'Finish', '1')",
                (8000 + i,),
            )
        conn.commit()
        conn.close()

        client = TestClient(app)
        # 不显式传 season → 端点取最新赛季;limit=5 远小于旧赛季行数,
        # 若过滤发生在 LIMIT 之后,这页会被 2019/2020 占满 → total=0
        r = client.get("/api/v1/leagues/47/fixtures?limit=5&status=finished")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] >= 1
        assert all(m["season"] != "2019/2020" for m in body["matches"])
        # 显式请求旧赛季也必须拿到该赛季的行
        r2 = client.get("/api/v1/leagues/47/fixtures?season=2019/2020&limit=5")
        assert r2.json()["total"] == 30
        assert all(m["season"] == "2019/2020" for m in r2.json()["matches"])

    def test_fixtures_response_carries_season_and_available_seasons(self, app, seeded):
        """LeagueFixturesResponse 必须自带 season/available_seasons(问题①②的
        前端切换器依赖这两个字段;此前赛程端点返回 MatchListResponse,两者都没有,
        赛季徽章只能从首场比赛猜,列表为空时徽章直接消失)。"""
        client = TestClient(app)
        r = client.get("/api/v1/leagues/47/fixtures")
        assert r.status_code == 200
        body = r.json()
        assert body["league_id"] == 47
        assert body["season"] == "2026/2027"
        assert "2026/2027" in body["available_seasons"]

    def test_fixtures_default_season_prefers_earliest_upcoming_over_max_season_string(
        self, app, seeded
    ):
        """默认赛季 = 最早一场未开赛比赛所在赛季,不是字符串意义上最大的 Season。
        种一个字符串更大但全部已完赛的赛季,证明它不会被选为默认(否则用户
        点进联赛会看到一个已经打完、什么新鲜事都没有的"最新"赛季——这正是
        用户报告的问题②的另一种触发方式)。"""
        conn = connect_rw("core")
        # "2099/2100"字符串上最大,但全部 Finish——不应该成为默认
        conn.execute(
            "INSERT OR REPLACE INTO dim_match (Match_ID, Season, League_ID, Date,"
            " Home_Team_ID, Away_Team_ID, Home_Team_Name, Away_Team_Name,"
            " home_score, away_score, status, Match_Round)"
            " VALUES (9500, '2099/2100', 47, '2099-01-01', 1001, 1002,"
            " 'Arsenal', 'Chelsea', 3, 0, 'Finish', '1')"
        )
        conn.commit()
        conn.close()

        client = TestClient(app)
        r = client.get("/api/v1/leagues/47/fixtures")
        assert r.status_code == 200
        body = r.json()
        # seeded fixture 里 47 真正有未开赛比赛的赛季是 2026/2027(见 coreseed)
        assert body["season"] == "2026/2027"
        assert body["season"] != "2099/2100"
        assert "2099/2100" in body["available_seasons"]  # 仍然可被显式请求

    def test_fixtures_unknown_season_no_longer_400s(self, app, seeded):
        """未知赛季不再 400——与 standings/team-stats/players 三个端点统一策略,
        静默回退并由响应体的 season 字段如实告知实际展示的是哪个赛季。"""
        client = TestClient(app)
        r = client.get("/api/v1/leagues/47/fixtures?season=1999/2000")
        assert r.status_code == 200
        body = r.json()
        assert body["season"] != "1999/2000"
        assert body["season"] in body["available_seasons"]

    def test_matches_order_by_coalesced_kickoff_not_precision_bucket(self, app, seeded):
        """回归(问题④):精确 kickoff 与仅有自然日(date_only,kickoff_at_utc
        为 NULL)的比赛混排时,必须按真实时间顺序交替,不能把"没有精确
        kickoff"的比赛整体压到最后再按 Match_ID 排——那样会让英超 2026/2027
        这种整赛季只有自然日粒度的联赛,在多联赛混排列表里失去时间顺序,
        排到任何有精确 kickoff 的比赛后面,哪怕那些比赛是几个月后才踢。"""
        conn = connect_rw("core")
        # A:只有自然日(2030-01-10);B/C:精确 kickoff,分别早于/晚于 A。
        # 三者都不在 seeded 已有的赛季区间内,互不干扰。
        for match_id, date, kickoff in (
            (9301, "2030-01-10", None),
            (9302, "2030-01-05", "2030-01-05T12:00:00Z"),
            (9303, "2030-01-15", "2030-01-15T12:00:00Z"),
        ):
            conn.execute(
                "INSERT OR REPLACE INTO dim_match (Match_ID, Season, League_ID, Date,"
                " Home_Team_ID, Away_Team_ID, Home_Team_Name, Away_Team_Name,"
                " home_score, away_score, status, Match_Round, kickoff_at_utc)"
                " VALUES (?, '2029/2030', 47, ?, 1001, 1002, 'Arsenal', 'Chelsea',"
                " NULL, NULL, 'NotStarted', '1', ?)",
                (match_id, date, kickoff),
            )
        conn.commit()
        conn.close()

        client = TestClient(app)
        r = client.get("/api/v1/matches?league_id=47&status=upcoming&limit=200")
        assert r.status_code == 200
        ids_in_order = [m["match_id"] for m in r.json()["matches"]]
        # 只看这三场的相对顺序,忽略其它已布景比赛的位置
        relative = [mid for mid in ids_in_order if mid in (9301, 9302, 9303)]
        assert relative == [9302, 9301, 9303], (
            f"期望按真实时间顺序 B(01-05)→A(01-10,仅自然日)→C(01-15),"
            f"实际={relative}(旧实现会把 A 整体排到 B/C 之后)"
        )

    def test_no_data_league_honest_empty(self, app, seeded):
        # 42(欧冠,league:european_cup,权限矩阵互换后匿名可访问)未布景统计数据
        # → 诚实空态
        client = TestClient(app)
        ts = client.get("/api/v1/leagues/42/team-stats")
        assert ts.status_code == 200
        assert ts.json()["rows"] == [] and ts.json()["empty_reason"]
        pl = client.get("/api/v1/leagues/42/players")
        assert pl.status_code == 200
        assert pl.json()["boards"] == [] and pl.json()["empty_reason"]


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


@pytest.fixture
def seeded_allsvenskan(seeded):
    """瑞典超(league 67)接入(2A/2B/2C)验收布景:一场未来比赛 + 已发布预测。"""
    from .coreseed import insert_match

    now_before_seed = datetime.now(timezone.utc)
    kickoff = (now_before_seed + timedelta(days=3)).replace(microsecond=0)
    kickoff_at_utc = kickoff.isoformat().replace("+00:00", "Z")
    match_date = kickoff.date().isoformat()
    season = str(kickoff.year)
    assert kickoff > datetime.now(timezone.utc)

    conn_core = connect_rw("core")
    insert_match(
        conn_core, 9201, league_id=67, season=season, date=match_date,
        home_id=3001, away_id=3002, home="Vasteras SK", away="Orgryte IS",
        status="NotStarted", kickoff_at_utc=kickoff_at_utc,
    )
    conn_core.execute("INSERT OR REPLACE INTO dim_team_i18n VALUES (3001,'Vasteras SK','韦斯特罗斯','t','')")
    conn_core.execute("INSERT OR REPLACE INTO dim_team_i18n VALUES (3002,'Orgryte IS','厄尔格里特','t','')")
    conn_core.commit()
    conn_core.close()

    conn = connect_rw("platform")
    get_or_create_model_version(conn, "m-allsvenskan-api", "dixon-coles", applicable_league_ids=[67])
    sid = register_snapshot(
        conn, match_id=9201, kickoff_at_utc=kickoff_at_utc,
        kickoff_precision="exact", kickoff_source="fotmob:fixtures",
        model_version_id="m-allsvenskan-api", league_id=67,
        home_win=0.4, draw=0.32, away_win=0.28, status="draft",
    )
    publish_snapshot(conn, sid, actor=None)
    assert conn.execute(
        "SELECT status FROM prediction_snapshots WHERE id=?",
        (sid,),
    ).fetchone()[0] == "published"
    conn.close()
    return seeded


class TestAllsvenskanLeagueOnboarding:
    """瑞典超(FotMob league id 67)接入:元数据/entitlement/API 联赛门禁/
    概率投影必须与既有联赛遵守同一套规则,不额外开洞、不遗漏。

    2026-08-11 权限矩阵互换(platform 0012):league:lottery 改为登录门槛,
    67 与其余 8 个原免费小联赛一样,现在匿名 401、登录后可访问——本类断言
    随之从"验证匿名可访问"改为"验证登录门槛正确生效"。"""

    def test_league_67_listed_with_lottery_entitlement_requires_login(self, app, seeded):
        client = TestClient(app)
        leagues = {l["league_id"]: l for l in client.get("/api/v1/leagues").json()}
        assert 67 in leagues
        assert leagues[67]["entitlement"] == "league:lottery"
        assert leagues[67]["accessible"] is False   # 互换后匿名需登录(与 top5 相反)
        assert leagues[67]["name_zh"] == "瑞典超"

    def test_unknown_league_id_still_404(self, app, seeded):
        client = TestClient(app)
        assert client.get("/api/v1/leagues/9999/standings").status_code == 404
        assert client.get("/api/v1/leagues/9999/fixtures").status_code == 404

    def test_fixtures_and_standings_require_login(self, app, seeded_allsvenskan):
        client = TestClient(app)
        assert client.get("/api/v1/leagues/67/fixtures").status_code == 401
        assert client.get("/api/v1/leagues/67/standings").status_code == 401

    def test_anonymous_sees_league_67_in_list_but_locked(self, app, seeded_allsvenskan):
        """2026-08-13 用户拍板撤销"未持有权限的联赛整场从列表隐藏":瑞典超仍是
        login-gated 联赛,但比赛本身现在应该出现在匿名 /matches 列表里,只是
        标记 requires_login=True 且不下发概率——不是从结果集里整场消失。"""
        client = TestClient(app)
        matches = client.get("/api/v1/matches").json()["matches"]
        by_league = {m["league_id"]: m for m in matches}
        assert 67 in by_league
        assert by_league[67]["requires_login"] is True
        assert by_league[67]["win_probability"] is None
        assert 47 in by_league
        assert by_league[47]["requires_login"] is False

    def test_member_sees_league_67_match_detail_with_chinese_names(
        self, app, seeded_allsvenskan, fresh_ip
    ):
        client = TestClient(app)
        _login_user(client, ip=fresh_ip)
        det = client.get("/api/v1/matches/9201").json()
        assert det["match"]["home"]["name"] == "韦斯特罗斯"
        assert det["match"]["away"]["name"] == "厄尔格里特"

    def test_anonymous_gets_401_for_league_67_match_prediction(self, app, seeded_allsvenskan):
        """联赛门禁先于概率投影门禁生效:匿名对瑞典超场次直接 401,
        不会先看到任何概率字段。"""
        client = TestClient(app)
        r = client.get("/api/v1/matches/9201/prediction")
        assert r.status_code == 401
        raw = r.text
        for key in FORBIDDEN_FREE_KEYS:
            assert key not in raw, f"瑞典超门禁响应泄漏受限字段 {key}: {raw}"

    def test_member_gets_full_wdl_for_league_67_match(self, app, seeded_allsvenskan, fresh_ip):
        client = TestClient(app)
        _login_user(client, ip=fresh_ip)
        pred = client.get("/api/v1/matches/9201/prediction").json()["prediction"]
        assert pred["tier"] == "full"
        assert pred["home_probability"] == 0.4
        assert pred["draw_probability"] == 0.32
        assert pred["away_probability"] == 0.28


class TestLeagueSeasonProfile:
    """/leagues/{id}/season-profile:四张银层表的联合投影(2026-08-12 新增)。

    这四张表(silver_goal_minute_buckets / silver_score_distribution /
    silver_over_under_thresholds / silver_league_season_summary)早已构建完成,
    但此前前端零消费、/api/v1 下也没有对应路由 —— legacy 的
    /api/league/{id}/betting 查过同一批数据,而 §10.1 禁止继续扩展 legacy。
    """

    def _seed_profile(self, league_id=47, season="2025/2026"):
        conn = connect_rw("core")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS silver_league_season_summary"
            " (League_ID INTEGER, Season TEXT, total_matches INTEGER, home_win_pct REAL,"
            "  draw_pct REAL, away_win_pct REAL, btts_pct REAL, clean_sheet_pct REAL,"
            "  avg_total_goals REAL, home_away_goal_diff REAL, updated_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS silver_goal_minute_buckets"
            " (League_ID INTEGER, Season TEXT, bucket TEXT, goal_count INTEGER, pct REAL, updated_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS silver_over_under_thresholds"
            " (League_ID INTEGER, Season TEXT, threshold REAL, over_count INTEGER,"
            "  under_count INTEGER, over_pct REAL, under_pct REAL, updated_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS silver_score_distribution"
            " (League_ID INTEGER, Season TEXT, home_score INTEGER, away_score INTEGER,"
            "  match_count INTEGER, pct REAL, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO silver_league_season_summary VALUES (?,?,380,42.63,27.37,30.0,56.05,43.95,2.75,0.3026,'')",
            (league_id, season),
        )
        conn.execute(
            "INSERT INTO silver_goal_minute_buckets VALUES (?,?,'76-90',270,25.84,'')",
            (league_id, season),
        )
        conn.execute(
            "INSERT INTO silver_over_under_thresholds VALUES (?,?,2.5,209,171,55.0,45.0,'')",
            (league_id, season),
        )
        conn.execute(
            "INSERT INTO silver_score_distribution VALUES (?,?,1,1,47,12.37,'')",
            (league_id, season),
        )
        conn.commit()
        conn.close()

    def test_anonymous_free_league_gets_full_profile(self, app, seeded):
        self._seed_profile()
        client = TestClient(app)
        r = client.get("/api/v1/leagues/47/season-profile")
        assert r.status_code == 200
        body = r.json()
        assert body["league_id"] == 47
        assert body["summary"]["total_matches"] == 380
        assert body["goal_minutes"][0]["bucket"] == "76-90"
        assert body["over_under"][0]["threshold"] == 2.5
        assert body["score_distribution"][0]["pct"] == 12.37
        # 公开面必须可进共享缓存(§10.2)
        assert r.headers["cache-control"].startswith("public")

    def test_gated_league_requires_login(self, app, seeded):
        """league:lottery 联赛(权限矩阵互换后需登录)匿名拿不到,且响应体不带统计。"""
        self._seed_profile(league_id=67, season="2026")
        client = TestClient(app)
        r = client.get("/api/v1/leagues/67/season-profile")
        assert r.status_code == 401
        assert "42.63" not in r.text and "total_matches" not in r.text

    def test_no_data_league_honest_empty(self, app, seeded):
        """没有银层数据的联赛返回诚实空态,不是 500、也不补零。"""
        client = TestClient(app)
        r = client.get("/api/v1/leagues/42/season-profile")
        assert r.status_code == 200
        body = r.json()
        assert body["summary"] is None
        assert body["goal_minutes"] == []
        assert body["empty_reason"]

    def test_unknown_league_404(self, app, seeded):
        client = TestClient(app)
        assert client.get("/api/v1/leagues/9999/season-profile").status_code == 404


class TestStandingsTableType:
    """standings 的 table_type 五档(2026-08-12 打开)。

    此前 queries/matches.standings 硬编码 `table_type='all'`,
    home(747 行)/ away(747)/ form(703)/ xg(695)共 2,892 行连 API 都出不去。
    xg 档还带 x_points / x_position 与 extra_json 预算好的 xPointsDiff,
    是"实际拿分 vs 按 xG 应得分"的唯一数据来源。
    """

    def _seed_types(self):
        conn = connect_rw("core")
        # 先清掉基础布景里同键的行,再造本类自己的四档 —— 否则基础布景的
        # all/xg 行与这里的行并存,rows[0] 取到哪一条取决于插入顺序。
        conn.execute(
            "DELETE FROM fact_league_table WHERE League_ID=47 AND Season='2025/2026'"
        )
        # 同一队在四档里给不同 points,证明档位真的切换了数据而不是都读 all
        for tt, pts in (("all", 90), ("home", 50), ("away", 40), ("form", 12)):
            conn.execute(
                "INSERT INTO fact_league_table (League_ID, Season, table_type, Team_ID,"
                " Team_Name, position, played, wins, draws, losses, goals_for,"
                " goals_against, goal_diff, points, qual_color)"
                " VALUES (47,'2025/2026',?,1001,'Arsenal',1,38,28,6,4,88,29,59,?,'')",
                (tt, pts),
            )
        conn.execute(
            "INSERT INTO fact_league_table (League_ID, Season, table_type, Team_ID,"
            " Team_Name, position, played, points, xg, xg_conceded, x_points,"
            " x_position, extra_json)"
            " VALUES (47,'2025/2026','xg',1001,'Arsenal',1,38,85,65.6,28.3,77.8,1,?)",
            (json.dumps({"xPointsDiff": 7.18, "xPositionDiff": 0}),),
        )
        conn.commit()
        conn.close()

    def test_each_table_type_returns_its_own_rows(self, app, seeded):
        self._seed_types()
        client = TestClient(app)
        got = {}
        for tt in ("all", "home", "away", "form"):
            r = client.get(f"/api/v1/leagues/47/standings?table_type={tt}")
            assert r.status_code == 200, tt
            body = r.json()
            assert body["table_type"] == tt
            got[tt] = body["rows"][0]["points"]
        # 四档读到的是各自的行,不是同一份 all
        assert got == {"all": 90, "home": 50, "away": 40, "form": 12}

    def test_xg_table_exposes_x_points_diff(self, app, seeded):
        self._seed_types()
        client = TestClient(app)
        row = client.get("/api/v1/leagues/47/standings?table_type=xg").json()["rows"][0]
        assert row["x_points"] == 77.8
        assert row["x_position"] == 1
        # 口径:实际积分 − 按 xG 应得积分(正 = 多拿分)
        assert row["x_points_diff"] == 7.18
        assert row["x_position_diff"] == 0

    def test_non_xg_rows_have_null_diff_not_zero(self, app, seeded):
        """缺该数据时必须是 None —— 0 在"多拿/少拿分"语境下是有意义的真实取值
        (正好兑现),不能和"没有这个数据"混为一谈。"""
        self._seed_types()
        client = TestClient(app)
        row = client.get("/api/v1/leagues/47/standings?table_type=all").json()["rows"][0]
        assert row.get("x_points_diff") is None

    def test_invalid_table_type_rejected(self, app, seeded):
        client = TestClient(app)
        assert client.get("/api/v1/leagues/47/standings?table_type=bogus").status_code == 422
