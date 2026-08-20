"""/api/v1/matches/{id}/report:完赛事实报告(阵容/事件/射门/统计四 tab 数据源)。

覆盖:可用性三态(有数据/无数据/部分采集)、两个哨兵回归(Period='All' 过滤、
脏 Team_ID 射门过滤)、评分来源正确性(lineup.rating 而非 rating_title)、
中文名回退链、联赛门禁、404、缓存头(匿名公共缓存 / 带 Cookie 强制 no-store)。
"""

import pytest

from backend.db.connections import connect_rw

from .authflow import wechat_scan_login
from .coreseed import insert_match, seed_basic_core, seed_match_report


@pytest.fixture
def seeded_report(data_dir):
    """9002(英超完赛)种满五张事实表;9001 保持 NotStarted 零事实数据。"""
    seed_basic_core(data_dir)
    conn = connect_rw("core")
    seed_match_report(conn, match_id=9002)
    conn.commit()
    conn.close()
    return data_dir


class TestAvailability:
    def test_finished_match_full_report(self, seeded_report, client):
        r = client.get("/api/v1/matches/9002/report")
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is True
        assert body["coverage"] == {
            "lineup": True, "events": True, "shots": True,
            "team_stats": True, "player_stats": True,
        }
        assert len(body["lineups"]) == 2
        home, away = body["lineups"]
        assert home["is_home"] is True and home["formation"] == "4-3-3"
        assert away["is_home"] is False and away["formation"] == "4-4-2"
        assert len(home["starters"]) == 2 and len(home["bench"]) == 1
        assert len(body["events"]) == 9
        assert len(body["team_stats"]) == 2
        assert body["team_stats"][0]["is_home"] is True

    def test_half_kind_and_own_goal_derived_fields(self, seeded_report, client):
        """extra_json 投影(2026-08-21):half_kind 区分中场/全场,is_own_goal
        判据只认顶层 ownGoal——见 backend/queries/match_report.py::HALF_KINDS
        与 coreseed.py 的种子数据注释。"""
        r = client.get("/api/v1/matches/9002/report")
        events = {e["event_index"]: e for e in r.json()["events"]}

        # 非 Half 事件 half_kind 恒为 None
        assert events[0]["half_kind"] is None

        # 两条 Half:中场(HT)与全场(FT)必须能区分,不能都读成"半场"
        assert events[2]["event_type"] == "Half" and events[2]["half_kind"] == "HT"
        assert events[6]["event_type"] == "Half" and events[6]["half_kind"] == "FT"

        # 普通进球不是乌龙球
        assert events[0]["is_own_goal"] is False

        # 顶层 ownGoal=true → 乌龙球;is_home 是受益方(主队),player_name 是
        # 客队球员(p200/Away Defender)踢进本方球门——语义上"客队球员+主队
        # 比分增加"是正确的,不是数据错误。
        assert events[7]["is_own_goal"] is True
        assert events[7]["is_home"] is True
        assert events[7]["player_name"] == "Away Defender"

        # 哨兵:ownGoal=null 但 shotmapEvent.isOwnGoal=true 时不得判为乌龙球
        # ——判据必须只认顶层 ownGoal,这正是选中它而不是 shotmapEvent 的理由。
        assert events[8]["is_own_goal"] is False

    def test_not_started_match_unavailable(self, seeded_report, client):
        r = client.get("/api/v1/matches/9001/report")
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is False
        assert "reason" in body
        # 不可用响应物理上没有任何事实数据 key
        for key in ("lineups", "events", "shots", "team_stats", "player_stats"):
            assert key not in body

    def test_partial_ingest_coverage_flags(self, data_dir, client):
        """只有阵容入库(其余四张空):available=True 但 coverage 如实逐项为 false。"""
        seed_basic_core(data_dir)
        conn = connect_rw("core")
        conn.execute(
            "INSERT INTO fact_match_lineup (Match_ID, Team_ID, is_home, formation,"
            " Player_ID, player_name, is_starter, is_captain)"
            " VALUES (9002, 1001, 1, '4-4-2', 'p1', 'Solo Player', 1, 0)"
        )
        conn.commit()
        conn.close()
        body = client.get("/api/v1/matches/9002/report").json()
        assert body["available"] is True
        assert body["coverage"]["lineup"] is True
        assert body["coverage"]["shots"] is False
        assert body["shots"] == [] and body["events"] == []

    def test_unknown_match_404(self, seeded_report, client):
        assert client.get("/api/v1/matches/424242/report").status_code == 404


class TestSentinels:
    def test_first_half_team_stats_never_leak(self, seeded_report, client):
        """哨兵:Period='FirstHalf' 行的 111.1/222.2 绝不能出现(只取 Period='All')。"""
        r = client.get("/api/v1/matches/9002/report")
        assert "111.1" not in r.text and "222.2" not in r.text
        home_stats = r.json()["team_stats"][0]
        assert home_stats["possession"] == 61.0
        assert home_stats["tackles"] == 12.0     # 带点列名 "matchstats.headers.tackles"

    def test_orphan_team_shot_filtered(self, seeded_report, client):
        """哨兵:Team_ID=99999 的脏射门行(xG=0.9999)绝不能出现,也不猜归属。"""
        r = client.get("/api/v1/matches/9002/report")
        assert "0.9999" not in r.text and "99999" not in r.text
        shots = r.json()["shots"]
        assert len(shots) == 3    # 2 常规 + 1 点球大战(后端保留,前端排除出图)
        assert {s["period"] for s in shots} == {"FirstHalf", "SecondHalf", "PenaltyShootout"}

    def test_rating_from_lineup_not_rating_title(self, seeded_report, client):
        """评分唯一真源是 lineup.rating(7.7);player_stats.rating_title(9.99)不外露。"""
        r = client.get("/api/v1/matches/9002/report")
        assert "9.99" not in r.text
        striker = next(p for p in r.json()["player_stats"] if p["player_id"] == "p100")
        assert striker["rating"] == 7.7

    def test_shot_is_home_derivation(self, seeded_report, client):
        shots = client.get("/api/v1/matches/9002/report").json()["shots"]
        by_pid = {s["player_id"]: s for s in shots if s["period"] != "PenaltyShootout"}
        assert by_pid["p100"]["is_home"] is True
        assert by_pid["p200"]["is_home"] is False


class TestI18nFallback:
    def test_mapped_player_gets_chinese_name(self, seeded_report, client):
        """p100 在 dim_player_i18n 有映射(seed_basic_core 已种)→ 中文短名。"""
        body = client.get("/api/v1/matches/9002/report").json()
        striker = next(p for t in body["lineups"] for p in t["starters"]
                       if p["player_id"] == "p100")
        assert striker["name"] == "测试前锋"
        assert striker["name_en"] == "Test Striker"

    def test_unmapped_player_falls_back_to_source_name(self, seeded_report, client):
        body = client.get("/api/v1/matches/9002/report").json()
        keeper = next(p for t in body["lineups"] for p in t["starters"]
                      if p["player_id"] == "p101")
        assert keeper["name"] == "Home Keeper"   # 无映射 → 来源英文名,绝不空白


class TestGateAndCache:
    def test_previously_lottery_league_open_to_anonymous(self, seeded_report, client, fresh_ip):
        """瑞典超 9301(原 league:lottery):2026-08-16 起除"每日精选"外全站
        比赛内容全部免费,匿名与登录后同样 200——这条断言正是要推翻的旧规则
        (此前匿名 401)。"""
        conn = connect_rw("core")
        insert_match(conn, 9301, league_id=67, season="2026", date="2026-05-10",
                     home_id=2001, away_id=2002, home="Vasteras SK", away="Orgryte IS")
        seed_match_report(conn, match_id=9301, home_id=2001, away_id=2002)
        conn.commit()
        conn.close()
        assert client.get("/api/v1/matches/9301/report").status_code == 200
        wechat_scan_login(client, ip=fresh_ip)
        assert client.get("/api/v1/matches/9301/report").status_code == 200

    def test_anonymous_epl_gets_public_cache(self, seeded_report, client):
        r = client.get("/api/v1/matches/9002/report")
        assert "public" in r.headers["Cache-Control"]
        assert "s-maxage" in r.headers["Cache-Control"]

    def test_cookie_request_forced_no_store(self, seeded_report, client, fresh_ip):
        """带 session cookie 的请求必须被缓存中间件强制 private, no-store。"""
        wechat_scan_login(client, ip=fresh_ip)
        r = client.get("/api/v1/matches/9002/report")
        assert r.status_code == 200
        assert r.headers["Cache-Control"] == "private, no-store"
