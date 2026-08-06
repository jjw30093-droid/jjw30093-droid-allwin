"""全站比赛列表的赛季筛选 + "已有赔率"筛选口径。

背景(2026-08-06):
- /api/v1/matches 一直没有 season 参数,而 q_matches.list_matches 早就支持
  season 下推。结果是库里 5 大联赛各 6 个历史赛季、10,735 场已完赛比赛
  在全站比赛列表页"无路可达"(默认 status=upcoming + window=7d)。
- content=odds 只统计 bronze_ng_odds_snap(完整时间线),漏掉只有两点摘要
  (bronze_legacy_odds_summary)的 8,336 场——这些比赛详情页确实能看到赔率,
  却被筛选排除,筛选口径与实际可见内容不一致。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.db.connections import connect_rw
from tests.backend.coreseed import insert_match, seed_core_schema


@pytest.fixture
def multi_season(data_dir):
    """英超(免费层可见)两个历史赛季 + 一个自然年赛季联赛。"""
    conn = connect_rw("core")
    seed_core_schema(conn)
    # EPL 2020/2021:2 场已完赛
    insert_match(conn, 7001, league_id=47, season="2020/2021", date="2021-01-10",
                 status="Finish", home_score=1, away_score=0,
                 kickoff_at_utc="2021-01-10T15:00:00Z")
    insert_match(conn, 7002, league_id=47, season="2020/2021", date="2021-02-10",
                 status="Finish", home_score=2, away_score=2,
                 kickoff_at_utc="2021-02-10T15:00:00Z")
    # EPL 2022/2023:1 场已完赛
    insert_match(conn, 7003, league_id=47, season="2022/2023", date="2023-01-10",
                 status="Finish", home_score=3, away_score=1,
                 kickoff_at_utc="2023-01-10T15:00:00Z")
    # 自然年赛季联赛(瑞典超 67,free 可见)
    insert_match(conn, 7004, league_id=67, season="2026", date="2026-05-01",
                 status="Finish", home_score=0, away_score=0,
                 kickoff_at_utc="2026-05-01T15:00:00Z")
    conn.commit()
    conn.close()
    return data_dir


class TestSeasonFilter:
    def test_season_narrows_to_that_season_only(self, app, multi_season):
        c = TestClient(app)
        r = c.get("/api/v1/matches?league_id=47&season=2020/2021&status=finished&window=all")
        assert r.status_code == 200
        ids = {m["match_id"] for m in r.json()["matches"]}
        assert ids == {7001, 7002}       # 不含同联赛其它赛季的 7003

    def test_other_season_reachable(self, app, multi_season):
        c = TestClient(app)
        ids = {
            m["match_id"]
            for m in c.get(
                "/api/v1/matches?league_id=47&season=2022/2023&status=finished&window=all"
            ).json()["matches"]
        }
        assert ids == {7003}

    def test_calendar_year_season_accepted(self, app, multi_season):
        """挪超/瑞超是 "2026" 这种自然年赛季,校验正则必须放行。"""
        c = TestClient(app)
        r = c.get("/api/v1/matches?league_id=67&season=2026&status=finished&window=all")
        assert r.status_code == 200
        assert {m["match_id"] for m in r.json()["matches"]} == {7004}

    def test_no_season_returns_all_seasons(self, app, multi_season):
        c = TestClient(app)
        ids = {
            m["match_id"]
            for m in c.get("/api/v1/matches?league_id=47&status=finished&window=all").json()["matches"]
        }
        assert ids == {7001, 7002, 7003}

    def test_malformed_season_rejected(self, app, multi_season):
        c = TestClient(app)
        assert c.get("/api/v1/matches?season=not-a-season").status_code == 422
        assert c.get("/api/v1/matches?season=2020/2021/2022").status_code == 422

    def test_leagues_endpoint_exposes_available_seasons(self, app, multi_season):
        """前端赛季筛选的选项来自这里,不得在前端写死赛季名单。"""
        c = TestClient(app)
        by_id = {l["league_id"]: l for l in c.get("/api/v1/leagues").json()}
        assert by_id[47]["available_seasons"] == ["2020/2021", "2022/2023"]
        assert by_id[67]["available_seasons"] == ["2026"]

    def test_season_filter_respects_entitlement(self, app, multi_season, fresh_ip):
        """赛季参数不能成为绕过联赛门禁的旁路:匿名请求西甲赛季仍拿不到数据。"""
        conn = connect_rw("core")
        insert_match(conn, 7005, league_id=87, season="2020/2021", date="2021-03-01",
                     status="Finish", home_score=1, away_score=1)
        conn.commit()
        conn.close()
        c = TestClient(app)
        r = c.get("/api/v1/matches?league_id=87&season=2020/2021&status=finished&window=all")
        assert r.status_code == 200
        assert r.json()["matches"] == []      # 匿名只有 league:epl


class TestOddsContentFilter:
    """content=odds 必须同时认完整时间线与两点摘要两种赔率。"""

    def _seed_legacy_odds(self, match_id: int) -> None:
        conn = connect_rw("odds")
        conn.execute(
            """INSERT INTO bronze_legacy_odds_summary
                 (fotmob_match_id, source, provider, market, period, line,
                  home_or_over, draw, away_or_under, orientation_fixed,
                  source_file, ingested_at)
               VALUES (?, 'asset_a_json', 'Bet365', '1x2', 'latest', NULL,
                       2.0, 3.4, 3.8, 0, 'test', '2026-08-06T00:00:00Z')""",
            (match_id,),
        )
        conn.commit()
        conn.close()

    def test_legacy_only_match_included_in_odds_filter(self, app, multi_season):
        self._seed_legacy_odds(7001)
        c = TestClient(app)
        ids = {
            m["match_id"]
            for m in c.get(
                "/api/v1/matches?league_id=47&status=finished&window=all&content=odds"
            ).json()["matches"]
        }
        assert 7001 in ids          # 只有两点摘要也算"已有赔率"
        assert 7002 not in ids      # 完全没有赔率的不算

    def test_odds_filter_composes_with_season(self, app, multi_season):
        self._seed_legacy_odds(7001)
        self._seed_legacy_odds(7003)
        c = TestClient(app)
        ids = {
            m["match_id"]
            for m in c.get(
                "/api/v1/matches?league_id=47&season=2020/2021"
                "&status=finished&window=all&content=odds"
            ).json()["matches"]
        }
        assert ids == {7001}        # 7003 有赔率但不在该赛季


class TestSeasonEchoAndCoverageTier:
    """D4:MatchListResponse 回显 season/available_seasons;D8:逐场赔率档位。"""

    def test_response_echoes_season_and_available(self, app, multi_season):
        c = TestClient(app)
        d = c.get(
            "/api/v1/matches?league_id=47&season=2020/2021&status=finished&window=all"
        ).json()
        assert d["season"] == "2020/2021"
        assert d["available_seasons"] == ["2020/2021", "2022/2023"]

    def test_valid_but_absent_season_is_explainable(self, app, multi_season):
        """合法但库里没有的赛季:total=0,但 available_seasons 告诉用户有什么。"""
        c = TestClient(app)
        d = c.get(
            "/api/v1/matches?league_id=47&season=2019/2020&status=finished&window=all"
        ).json()
        assert d["total"] == 0
        assert d["season"] == "2019/2020"
        assert d["available_seasons"] == ["2020/2021", "2022/2023"]

    def test_coverage_tier_per_match(self, app, multi_season):
        conn = connect_rw("odds")
        conn.execute(
            """INSERT INTO bronze_legacy_odds_summary
                 (fotmob_match_id, source, provider, market, period, line,
                  home_or_over, draw, away_or_under, orientation_fixed,
                  source_file, ingested_at)
               VALUES (7001, 'asset_a_json', 'Bet365', '1x2', 'latest', NULL,
                       2.0, 3.4, 3.8, 0, 'test', '2026-08-06T00:00:00Z')""",
        )
        conn.commit()
        conn.close()
        c = TestClient(app)
        d = c.get(
            "/api/v1/matches?league_id=47&season=2020/2021&status=finished&window=all"
        ).json()
        tiers = {m["match_id"]: m["odds_coverage_tier"] for m in d["matches"]}
        assert tiers[7001] == "open_close_only"
        assert tiers[7002] == "none"
