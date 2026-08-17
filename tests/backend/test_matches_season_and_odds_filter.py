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

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.db.connections import connect_rw
from tests.backend.authflow import wechat_scan_login
from tests.backend.coreseed import insert_match, seed_core_schema


@pytest.fixture
def multi_season(data_dir):
    """英超(免费层可见)两个历史赛季 + 一个自然年赛季联赛(瑞典超 67,
    2026-08-11 权限矩阵互换后为 league:lottery,需登录)。"""
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
        """挪超/瑞超是 "2026" 这种自然年赛季,校验正则必须放行——67 不再需要
        登录(2026-08-16 起除"每日精选"外全站比赛内容全部免费),匿名直接验证。"""
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

    def test_season_filter_includes_previously_locked_league_with_full_content(
        self, app, multi_season
    ):
        """2026-08-16 产品权限口径修正:赛季筛选不再有任何联赛门禁需要防止
        绕过——原 league:lottery 联赛(67,瑞典超)的比赛与其它联赛同样出现,
        且响应体不再带 requires_login 这类只为登录门禁服务的字段(该字段
        已从 MatchSummary 整体删除)。这条断言正是要推翻的旧规则(此前
        requires_login=True 且 win_probability 恒为 None)。"""
        conn = connect_rw("core")
        insert_match(conn, 7005, league_id=67, season="2020", date="2021-03-01",
                     status="Finish", home_score=1, away_score=1)
        conn.commit()
        conn.close()
        c = TestClient(app)
        r = c.get("/api/v1/matches?league_id=67&season=2020&status=finished&window=all")
        assert r.status_code == 200
        matches = r.json()["matches"]
        assert {m["match_id"] for m in matches} == {7005}
        assert "requires_login" not in matches[0]
        # 没有赔率数据 → win_probability 诚实为 None(不是因为门禁清空)
        assert matches[0]["win_probability"] is None


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


class TestOddsFreshnessFields:
    """P1.1(用户报告的数据诚实性缺陷):odds_coverage_tier 只回答"这场比赛历史
    上有没有过赔率数据"——最后一次真实观测哪怕是好几天前,只要曾经抓到过,
    tier 依然是 full_timeline。这两个新字段回答"数据是不是新的",不得对陈旧
    比赛也硬编码乐观状态。"""

    def _seed_full_timeline(self, match_id: int, observed_at: str) -> None:
        conn_odds = connect_rw("odds")
        conn_odds.execute(
            """INSERT INTO dim_match_xref
                 (fotmob_match_id, provider, provider_match_id, home_away_inverted,
                  confidence, verified, method, review_status, created_at, updated_at)
               VALUES (?, 'nowgoal', ?, 0, 1.0, 1, 'auto', 'auto_ok',
                       '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z')""",
            (match_id, f"titan-{match_id}"),
        )
        conn_odds.execute(
            """INSERT INTO bronze_ng_odds_snap
                 (provider_match_id, market, company_id, company_name, market_phase,
                  payload_json, payload_hash, observed_at, ingested_at, poll_run_id)
               VALUES (?, '1x2', '8', 'Bet365', 'pre_match',
                       '{"home":2.0,"draw":3.4,"away":3.8}', 'x', ?, ?, 'run1')""",
            (f"titan-{match_id}", observed_at, observed_at),
        )
        conn_odds.commit()
        conn_odds.close()

    def test_stale_full_timeline_reports_stale_not_fresh(self, app, multi_season):
        """核心 bug:7001 的最后一次真实观测远在阈值之外,不能被硬编码显示
        成"完整走势"式的乐观状态——必须是 STALE,且带上真实的最后观测时间。"""
        self._seed_full_timeline(7001, "2020-01-01T00:00:00Z")
        c = TestClient(app)
        d = c.get(
            "/api/v1/matches?league_id=47&season=2020/2021&status=finished&window=all"
        ).json()
        by_id = {m["match_id"]: m for m in d["matches"]}
        assert by_id[7001]["odds_coverage_tier"] == "full_timeline"
        assert by_id[7001]["odds_freshness_state"] == "STALE"
        assert by_id[7001]["odds_last_observed_at"] == "2020-01-01T00:00:00Z"

    def test_no_full_timeline_coverage_is_unavailable(self, app, multi_season):
        """7002 完全没有 NowGoal 完整时间线快照:状态必须是 UNAVAILABLE,
        不得编造一个 last_observed_at。"""
        c = TestClient(app)
        d = c.get(
            "/api/v1/matches?league_id=47&season=2020/2021&status=finished&window=all"
        ).json()
        by_id = {m["match_id"]: m for m in d["matches"]}
        assert by_id[7002]["odds_freshness_state"] == "UNAVAILABLE"
        assert by_id[7002]["odds_last_observed_at"] is None

    def test_match_detail_reports_same_freshness_fields(self, app, multi_season):
        """详情页与列表页同一口径(和 odds_coverage_tier 的既有约定一致)。"""
        self._seed_full_timeline(7001, "2020-01-01T00:00:00Z")
        c = TestClient(app)
        d = c.get("/api/v1/matches/7001").json()
        assert d["match"]["odds_freshness_state"] == "STALE"
        assert d["match"]["odds_last_observed_at"] == "2020-01-01T00:00:00Z"


class TestHomepageHeroBoostFreePredicted:
    """首页重点位确定性选场(2026-08-16 真实数据完整性 bug)。

    背景:frontend/components/home/HomeMatchExperienceLive.tsx 和
    app/page.tsx 都只请求 `/api/v1/matches?status=upcoming&window=7d&limit=8`,
    然后在浏览器/RSC 里用 selectHeroPair() 从这 8 条里挑"免费且已发布概率"
    的一场。selectHeroPair 的挑选逻辑本身没问题——问题是它只能在 API 已经
    截断到 8 条的结果里选。/api/v1/matches 默认排序早就会把"有赔率/有已发布
    预测"的比赛整体顶到前面(analysis_match_ids | odds_match_ids 这一档
    priority_match_ids),但那一档不区分"免费"还是"需登录"——当这一档人数
    超过 limit 且恰好都是锁定联赛或凑巧排在真正免费可看的那场前面时,
    limit=8 这一页就可能完全不含任何"免费且有概率"的比赛,即使完整 7 天
    窗口里其实存在。

    2026-08-16 产品权限口径修正后(除"每日精选"外全站比赛内容与登录/付费
    彻底解耦),boost=free_predicted 不再按"联赛是否需要登录"筛选——这里
    的"free"字面上过时但作为既有 API 查询参数值保留,不在本任务范围内
    重命名(避免连带修改前端契约)。这里用真实(不注入 now)的 /api/v1/matches
    端点重现:未来 7 天窗口共 12 场比赛,按开球时间由近到远排列的前 8 场
    只有非 1x2 市场的赔率快照(同样进入既有 priority 档,但算不出
    win_probability),第 9-12 场里第 11 场(9111)才是一场有真实已发布
    1x2 概率的比赛。
    """

    LEAGUE_ID = 47   # 任意联赛均可——2026-08-16 起访问权不再与联赛挂钩

    def _seed_matches(self, data_dir) -> datetime:
        now = datetime.now(timezone.utc)
        conn = connect_rw("core")
        seed_core_schema(conn)
        # 前 8 场(开球 now+1h..+8h):只有非 1x2 市场的赔率快照——进入既有
        # priority_match_ids(analysis|odds)那一档,但算不出 win_probability
        # (latest_1x2_by_match 只认 market='1x2')。
        for i in range(1, 9):
            match_id = 9100 + i
            kickoff = now + timedelta(hours=i)
            insert_match(
                conn, match_id, league_id=self.LEAGUE_ID, season="2026",
                date=kickoff.date().isoformat(),
                home_id=100 + i, away_id=200 + i,
                home=f"Other Market Home {i}", away=f"Other Market Away {i}",
                status="NotStarted", kickoff_at_utc=_iso(kickoff),
            )
        # 第 9 场:还没有任何赔率/概率(还没算出来的比赛)。
        insert_match(
            conn, 9109, league_id=self.LEAGUE_ID, season="2026",
            date=(now + timedelta(hours=9)).date().isoformat(),
            home_id=301, away_id=302, home="No Odds Home", away="No Odds Away",
            status="NotStarted", kickoff_at_utc=_iso(now + timedelta(hours=9)),
        )
        # 第 10 场:无赔率(纯赛程壳,填充位)。
        insert_match(
            conn, 9110, league_id=self.LEAGUE_ID, season="2026",
            date=(now + timedelta(hours=10)).date().isoformat(),
            home_id=303, away_id=304, home="Filler Home 10", away="Filler Away 10",
            status="NotStarted", kickoff_at_utc=_iso(now + timedelta(hours=10)),
        )
        # 第 11 场:目标比赛——真实已发布 1x2 概率。
        insert_match(
            conn, 9111, league_id=self.LEAGUE_ID, season="2026",
            date=(now + timedelta(hours=11)).date().isoformat(),
            home_id=401, away_id=402, home="Target Home", away="Target Away",
            status="NotStarted", kickoff_at_utc=_iso(now + timedelta(hours=11)),
        )
        # 第 12 场:无赔率(填充位)。
        insert_match(
            conn, 9112, league_id=self.LEAGUE_ID, season="2026",
            date=(now + timedelta(hours=12)).date().isoformat(),
            home_id=305, away_id=306, home="Filler Home 12", away="Filler Away 12",
            status="NotStarted", kickoff_at_utc=_iso(now + timedelta(hours=12)),
        )
        conn.commit()
        conn.close()

        conn_odds = connect_rw("odds")
        observed_at = _iso(now - timedelta(hours=2))
        for i in range(1, 9):
            _seed_non_1x2_odds(conn_odds, 9100 + i, observed_at)
        _seed_1x2_odds(conn_odds, 9111, observed_at)
        conn_odds.commit()
        conn_odds.close()
        return now

    def test_default_endpoint_truncates_the_only_predicted_match_out_of_page(
        self, app, data_dir
    ):
        """基线(改动前后都应成立,证明我们没有偷偷改动默认排序/分页契约):
        不带新参数时,limit=8 这一页仍然只是"既有 priority 档内按开球时间
        最靠前的 8 场",9111 不在其中——即使它是全窗口里唯一一场已发布
        胜平负概率的比赛。"""
        self._seed_matches(data_dir)
        c = TestClient(app)
        r = c.get("/api/v1/matches?status=upcoming&window=7d&limit=8")
        assert r.status_code == 200
        body = r.json()
        ids = [m["match_id"] for m in body["matches"]]
        assert len(ids) == 8
        assert ids == [9101, 9102, 9103, 9104, 9105, 9106, 9107, 9108]
        assert 9111 not in ids  # 真实回归现象:目标比赛被截断线挡在外面

    def test_boost_free_predicted_guarantees_the_predicted_match_survives_the_page(
        self, app, data_dir
    ):
        """修复目标:加上 boost=free_predicted 后,同一个 limit=8 请求必须
        确定性地包含 9111——它是完整 7 天窗口里唯一一场已发布胜平负概率的
        比赛。响应体不再带 requires_login(该字段已从 MatchSummary 整体
        删除,访问权不再与联赛挂钩)。"""
        self._seed_matches(data_dir)
        c = TestClient(app)
        r = c.get(
            "/api/v1/matches?status=upcoming&window=7d&limit=8&boost=free_predicted"
        )
        assert r.status_code == 200
        body = r.json()
        matches_by_id = {m["match_id"]: m for m in body["matches"]}
        assert len(body["matches"]) == 8
        assert 9111 in matches_by_id, (
            "已发布概率的比赛应该被顶进 limit=8 截断线以内,"
            f"实际返回 {sorted(matches_by_id)}"
        )
        target = matches_by_id[9111]
        assert "requires_login" not in target
        assert target["win_probability"] is not None

    def test_boost_free_predicted_is_honest_empty_state_when_nothing_qualifies(
        self, app, data_dir
    ):
        """真的没有任何"已发布概率"的比赛时(比如只种没有赔率的赛程),
        boost 参数不得为了凑数而挑一场不满足条件的比赛顶到最前——必须诚实地
        对 win_probability 不做任何伪造,行为等同未加 boost。"""
        now = datetime.now(timezone.utc)
        conn = connect_rw("core")
        seed_core_schema(conn)
        insert_match(
            conn, 9201, league_id=self.LEAGUE_ID, season="2026",
            date=now.date().isoformat(), home_id=501, away_id=502,
            home="Only Filler Home", away="Only Filler Away",
            status="NotStarted", kickoff_at_utc=_iso(now + timedelta(hours=1)),
        )
        conn.commit()
        conn.close()
        c = TestClient(app)
        r = c.get(
            "/api/v1/matches?status=upcoming&window=7d&limit=8&boost=free_predicted"
        )
        assert r.status_code == 200
        body = r.json()
        assert [m["match_id"] for m in body["matches"]] == [9201]
        assert "requires_login" not in body["matches"][0]
        assert body["matches"][0]["win_probability"] is None


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _seed_1x2_odds(conn_odds, match_id: int, observed_at: str) -> None:
    """给一场比赛种一条真实形状的 Bet365 1x2 赔率快照(与
    TestOddsFreshnessFields._seed_full_timeline 同一套 schema/字段)。"""
    conn_odds.execute(
        """INSERT INTO dim_match_xref
             (fotmob_match_id, provider, provider_match_id, home_away_inverted,
              confidence, verified, method, review_status, created_at, updated_at)
           VALUES (?, 'nowgoal', ?, 0, 1.0, 1, 'auto', 'auto_ok',
                   '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z')""",
        (match_id, f"boost-titan-{match_id}"),
    )
    conn_odds.execute(
        """INSERT INTO bronze_ng_odds_snap
             (provider_match_id, market, company_id, company_name, market_phase,
              payload_json, payload_hash, observed_at, ingested_at, poll_run_id)
           VALUES (?, '1x2', '8', 'Bet365', 'pre_match',
                   '{"home":2.0,"draw":3.4,"away":3.8}', 'x', ?, ?, 'run1')""",
        (f"boost-titan-{match_id}", observed_at, observed_at),
    )


def _seed_non_1x2_odds(conn_odds, match_id: int, observed_at: str) -> None:
    """给一场比赛种一条大小球市场('ou',非 1x2)的赔率快照:计入
    odds_match_ids(任意市场都算"已有赔率"),但 latest_1x2_by_match 只认
    market='1x2',算不出 win_probability——与 _seed_1x2_odds 互补,用于
    区分"有赔率但没有真实胜平负概率"与"有真实胜平负概率"两种比赛。"""
    conn_odds.execute(
        """INSERT INTO dim_match_xref
             (fotmob_match_id, provider, provider_match_id, home_away_inverted,
              confidence, verified, method, review_status, created_at, updated_at)
           VALUES (?, 'nowgoal', ?, 0, 1.0, 1, 'auto', 'auto_ok',
                   '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z')""",
        (match_id, f"boost-titan-{match_id}"),
    )
    conn_odds.execute(
        """INSERT INTO bronze_ng_odds_snap
             (provider_match_id, market, company_id, company_name, market_phase,
              payload_json, payload_hash, observed_at, ingested_at, poll_run_id)
           VALUES (?, 'ou', '8', 'Bet365', 'pre_match',
                   '{"line":2.5,"over":1.9,"under":1.9}', 'x', ?, ?, 'run1')""",
        (f"boost-titan-{match_id}", observed_at, observed_at),
    )
