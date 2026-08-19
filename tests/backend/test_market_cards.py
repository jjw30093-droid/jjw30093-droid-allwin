"""GET /api/v1/matches/{id}/markets:门禁矩阵 + 三种数据质量降级 + DTO 形状。

背景:这是赛前之墙唯一能给"这场比赛特有"内容的落点之一——所有赛后事实表
对未开赛比赛精确为 0 行,市场卡靠两队历史聚合 + 离线标定表撑起内容。
本文件验证:门禁与 /report 同级(不分付费档);三种降级路径(有历史/
样本不足/联赛无历史)都不报错;"for"/"against" JSON key 序列化正确
(Pydantic alias,Python 侧字段名是 for_,不能漏配)。
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.db.connections import connect_rw
from tests.backend.coreseed import insert_match, seed_core_schema


def _seed_history(conn, team_id, league_id, *, start_date, n=10, corners=6, yc=3):
    base = date.fromisoformat(start_date)
    for i in range(n):
        mid = 70000 + team_id * 100 + i
        opp = 80000 + team_id * 100 + i
        d = (base + timedelta(days=i)).isoformat()
        insert_match(conn, mid, league_id=league_id, season="2025/2026", date=d,
                     home_id=team_id, away_id=opp, home="队", away=f"陪衬{i}",
                     status="Finish", home_score=1, away_score=0)
        conn.execute(
            "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
            " VALUES (?, ?, 'All', 1, ?)",
            (mid, team_id, json.dumps({"corners": corners, "yellow_cards": yc, "fouls": 12})),
        )
        conn.execute(
            "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
            " VALUES (?, ?, 'All', 0, ?)",
            (mid, opp, json.dumps({"corners": 4, "yellow_cards": 2, "fouls": 10})),
        )


def _seed_calibration(conn_platform, market, league_id, line, *, bucket_hit_rates):
    """5 档,边界按 0/2/4/6/8/999 切,方便测试用小整数预估值精确落档。"""
    bounds = [None, 2.0, 4.0, 6.0, 8.0, None]
    for i, hr in enumerate(bucket_hit_rates):
        conn_platform.execute(
            """INSERT INTO market_calibration
               (market, league_id, line, bucket_index, bucket_lower, bucket_upper,
                hit_rate, sample_size, signal_grade, spread_pp, monotonic,
                train_size, calibrated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 200, ?, 20.0, 1, 1000, '2026-01-01T00:00:00Z')""",
            (market, league_id, line, i, bounds[i], bounds[i + 1], hr, "★★"),
        )


def _seed_xref(conn_odds, titan_id, fotmob_match_id):
    conn_odds.execute(
        """INSERT INTO dim_match_xref
           (fotmob_match_id, provider, provider_match_id, home_away_inverted, confidence,
            verified, method, review_status, created_at, updated_at)
           VALUES (?, 'nowgoal', ?, 0, 1.0, 1, 'auto', 'auto_ok',
                   '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z')""",
        (fotmob_match_id, titan_id),
    )


def _seed_odds_snap(conn_odds, titan_id, market, line, *, company_id="8", observed_at="2026-08-13T09:00:00Z"):
    """写一条扁平形状({home,line,away}/{over,line,under})的真实盘口快照——
    与历史回填 CLI 同一形状,latest_market_line() 靠 normalize_odds_payload()
    读扁平/嵌套两种形状(见 backend/queries/odds.py 头部注释)。"""
    payload = json.dumps({"over": 1.9, "line": line, "under": 1.9})
    conn_odds.execute(
        """INSERT INTO bronze_ng_odds_snap
           (provider_match_id, market, company_id, company_name, market_phase,
            payload_json, payload_hash, observed_at, ingested_at, poll_run_id)
           VALUES (?, ?, ?, 'Bet365', 'pre_match', ?, 'x', ?, ?, 'run1')""",
        (titan_id, market, company_id, payload, observed_at, observed_at),
    )


@pytest.fixture
def market_fixture(data_dir):
    conn = connect_rw("core")
    seed_core_schema(conn)
    kickoff_date = (date.today() + timedelta(days=3)).isoformat()

    # 9500:免费联赛(47)未开赛比赛,双方都有充足历史
    insert_match(conn, 9500, league_id=47, season="2025/2026", date=kickoff_date,
                 home_id=1001, away_id=1002, home="阿队", away="乙队", status="NotStarted")
    _seed_history(conn, 1001, 47, start_date="2025-12-01", corners=6, yc=3)
    _seed_history(conn, 1002, 47, start_date="2025-12-01", corners=5, yc=2)

    # 9501:免费联赛,双方历史都不足(每队只有 2 场,< MIN_SAMPLE=3)
    insert_match(conn, 9501, league_id=47, season="2025/2026", date=kickoff_date,
                 home_id=1003, away_id=1004, home="丙队", away="丁队", status="NotStarted")
    _seed_history(conn, 1003, 47, start_date="2025-12-01", n=2)
    _seed_history(conn, 1004, 47, start_date="2025-12-01", n=2)

    # 9502:英冠(48,需登录),完全没有历史事实表(模拟真实的四个空联赛)
    insert_match(conn, 9502, league_id=48, season="2025/2026", date=kickoff_date,
                 home_id=2001, away_id=2002, home="戊队", away="己队", status="NotStarted")

    # 9503/9504:复用 1001/1002 的历史(角球估算值同 9500,恒为 11.0),
    # 分别验证"真实盘口线命中标定档" / "真实盘口线未命中标定档"两条路径。
    insert_match(conn, 9503, league_id=47, season="2025/2026", date=kickoff_date,
                 home_id=1001, away_id=1002, home="阿队", away="乙队", status="NotStarted")
    insert_match(conn, 9504, league_id=47, season="2025/2026", date=kickoff_date,
                 home_id=1001, away_id=1002, home="阿队", away="乙队", status="NotStarted")

    conn.commit()
    conn.close()

    conn_platform = connect_rw("platform")
    # yellow_cards line=3.5:预估值 = 1001 的 3 + 1002 的 2 = 5,落第 3 档(4<5<=6),
    # 命中率 0.55(刻意避开 0.5 边界,不让测试掺进 lean 的边界语义歧义)
    _seed_calibration(conn_platform, "yellow_cards", 0, 3.5,
                       bucket_hit_rates=[0.30, 0.40, 0.55, 0.60, 0.70])
    # corners line=10.5(真实盘口线,不是 default_line=9.5):估算值 11.0 落最后一档
    # (>8),命中率 0.65——9503 用这条验证"真实盘口线 + 命中标定档"。
    _seed_calibration(conn_platform, "corners", 47, 10.5,
                       bucket_hit_rates=[0.20, 0.30, 0.40, 0.50, 0.65])
    conn_platform.commit()
    conn_platform.close()

    conn_odds = connect_rw("odds")
    # 9503:corners 真实盘口线 10.5(命中上面标定的档),goals 真实盘口线 2.75
    # (无对应 calibration,验证 line_source 赋值与 data_quality 结果互不影响)。
    _seed_xref(conn_odds, "830001", 9503)
    _seed_odds_snap(conn_odds, "830001", "corners_ou", 10.5)
    _seed_odds_snap(conn_odds, "830001", "ou", 2.75)
    # 9504:corners 真实盘口线 11.5,未命中任何已标定档位(只标定了 10.5)。
    _seed_xref(conn_odds, "830002", 9504)
    _seed_odds_snap(conn_odds, "830002", "corners_ou", 11.5)
    conn_odds.commit()
    conn_odds.close()
    yield


class TestMatchMarketsRoute:
    def test_free_league_ok_with_calibrated_card(self, app, market_fixture):
        client = TestClient(app)
        r = client.get("/api/v1/matches/9500/markets")
        assert r.status_code == 200
        assert r.headers["cache-control"].startswith("public")
        body = r.json()
        assert body["match_id"] == 9500
        assert body["window"] == 10

        by_market = {c["market"]: c for c in body["cards"]}
        yc = by_market["yellow_cards"]
        assert yc["data_quality"] == "ok"
        assert yc["estimate"] == pytest.approx(5.0)
        assert yc["signal_grade"] == "★★"
        assert yc["hit_rate"] == pytest.approx(0.55)  # 第 3 档(index 2)命中率
        assert yc["lean"] == "over"

        # "for"/"against" 必须是这两个字面 JSON key(Pydantic alias 序列化),
        # 不能漏配成 Python 字段名 "for_"。
        raw = r.text
        assert '"for_"' not in raw
        assert '"for":' in raw and '"against":' in raw

    def test_market_without_calibration_row_is_honest(self, app, market_fixture):
        """corners/goals 两个市场本测试没有造 market_calibration 行——
        估算值算得出,但查不到标定结果时必须诚实标 no_calibration,
        不能编一个 signal_grade 出来。"""
        client = TestClient(app)
        body = client.get("/api/v1/matches/9500/markets").json()
        by_market = {c["market"]: c for c in body["cards"]}
        corners = by_market["corners"]
        assert corners["estimate"] is not None  # 预估值本身算得出来
        assert corners["data_quality"] == "no_calibration"
        assert corners["signal_grade"] is None
        assert corners["lean"] is None
        # 9500 没有造 bronze_ng_odds_snap 真实盘口——line_source 必须诚实回退到
        # 统计参考线(不能因为查询失败就悄悄留一个不带来源标记的数字)。
        assert corners["line_source"] == "statistical"
        assert corners["line"] == pytest.approx(9.5)

    def test_real_market_line_used_when_odds_present(self, app, market_fixture):
        """9503:corners 真实盘口线 10.5 命中标定档——line_source 必须是
        "market"、line 必须是真实盘口线(不是 default_line=9.5),且标定结果
        照常查得到(exact-match 精确 key 查询,真实线只要等于标定表里的 line
        就和统计参考线走同一套 _bucket_lookup 逻辑)。"""
        client = TestClient(app)
        body = client.get("/api/v1/matches/9503/markets").json()
        by_market = {c["market"]: c for c in body["cards"]}

        corners = by_market["corners"]
        assert corners["line_source"] == "market"
        assert corners["line"] == pytest.approx(10.5)
        assert corners["estimate"] == pytest.approx(11.0)
        assert corners["data_quality"] == "ok"
        assert corners["hit_rate"] == pytest.approx(0.65)
        assert corners["signal_grade"] == "★★"
        assert corners["lean"] == "over"

        # goals 真实盘口线 2.75 也生效(line_source 赋值先于 data_quality 判断,
        # 两队历史没造 expected_goals,预估值算不出来,但线本身必须是真实值)。
        goals = by_market["goals"]
        assert goals["line_source"] == "market"
        assert goals["line"] == pytest.approx(2.75)

        # yellow_cards 在 NowGoal 上没有真实市场(§见 market_cards.py 头注),
        # 恒为统计参考线,不受本场造的真实盘口影响。
        yc = by_market["yellow_cards"]
        assert yc["line_source"] == "statistical"
        assert yc["line"] == pytest.approx(3.5)

    def test_real_market_line_without_matching_calibration_is_honest(self, app, market_fixture):
        """9504:corners 真实盘口线 11.5,标定表只标定了 10.5——真实线优先于
        标定档位存在与否,查不到就诚实降级 no_calibration,不为了凑一个能打
        星的档位悄悄换回统计参考线或换成已标定的 10.5。"""
        client = TestClient(app)
        body = client.get("/api/v1/matches/9504/markets").json()
        corners = {c["market"]: c for c in body["cards"]}["corners"]
        assert corners["line_source"] == "market"
        assert corners["line"] == pytest.approx(11.5)
        assert corners["estimate"] == pytest.approx(11.0)
        assert corners["data_quality"] == "no_calibration"
        assert corners["signal_grade"] is None
        assert corners["lean"] is None

    def test_insufficient_sample_degrades_honestly(self, app, market_fixture):
        client = TestClient(app)
        body = client.get("/api/v1/matches/9501/markets").json()
        for card in body["cards"]:
            assert card["data_quality"] == "insufficient_sample"
            assert card["estimate"] is None
            assert card["signal_grade"] is None

    def test_no_history_league_degrades_honestly(self, app, market_fixture):
        """英冠(48,原 league:lottery)2026-08-16 起匿名即可访问(除"每日精选"
        外全站比赛内容全部免费)——直接验证匿名 no_history 降级路径(不是
        500,也不是 401)。这条断言正是要推翻的旧规则(此前匿名恒 401)。"""
        anon = TestClient(app)
        r = anon.get("/api/v1/matches/9502/markets")
        assert r.status_code == 200
        for card in r.json()["cards"]:
            assert card["data_quality"] == "no_history"
            assert card["estimate"] is None

    def test_unknown_match_404(self, app, market_fixture):
        client = TestClient(app)
        assert client.get("/api/v1/matches/9999999/markets").status_code == 404


class TestMarketCardLinesArray:
    """数据倾向卡片填充(2026-08-19):Fix 1(lines[] 全量已标定线)、Fix 5
    (calibrated_at)、Fix 6(no_calibration 原因细分)。Fix 2(against/n 已在
    payload 里)与 Fix 4(zh 标签)是纯前端改动,不在这里测。

    corners 市场 lines=[8.5, 9.5, 10.5](见 calibrate_markets.py MARKETS),
    market_fixture 只预置了 league_id=47/line=10.5 的标定;这里额外补
    line=8.5,让 8.5(已定级)/9.5(默认线,未定级)/10.5(已定级)三条线覆盖
    A1-A4 需要的全部组合。
    """

    def _seed_line_8_5(self):
        conn_platform = connect_rw("platform")
        _seed_calibration(conn_platform, "corners", 47, 8.5,
                           bucket_hit_rates=[0.25, 0.35, 0.45, 0.55, 0.70])
        conn_platform.commit()
        conn_platform.close()

    def test_card_exposes_all_calibrated_lines(self, app, market_fixture):
        self._seed_line_8_5()
        client = TestClient(app)
        body = client.get("/api/v1/matches/9500/markets").json()
        corners = {c["market"]: c for c in body["cards"]}["corners"]

        lines_by_value = {ln["line"]: ln for ln in corners["lines"]}
        assert set(lines_by_value) == {8.5, 9.5, 10.5}
        assert lines_by_value[9.5]["is_default"] is True
        assert lines_by_value[8.5]["is_default"] is False
        assert lines_by_value[10.5]["is_default"] is False

    def test_ungraded_line_carries_no_hit_rate_number(self, app, market_fixture):
        self._seed_line_8_5()
        client = TestClient(app)
        body = client.get("/api/v1/matches/9500/markets").json()
        corners = {c["market"]: c for c in body["cards"]}["corners"]

        # 9.5(默认线)在本测试从未被标定过——B2 落到数据层:signal_grade
        # 为 None 时,hit_rate/sample_size 必须同为 None,不能只是前端不渲染。
        row_95 = next(ln for ln in corners["lines"] if ln["line"] == 9.5)
        assert row_95["signal_grade"] is None
        assert row_95["hit_rate"] is None
        assert row_95["sample_size"] is None

    def test_graded_non_default_line_keeps_its_real_hit_rate(self, app, market_fixture):
        self._seed_line_8_5()
        client = TestClient(app)
        body = client.get("/api/v1/matches/9500/markets").json()
        corners = {c["market"]: c for c in body["cards"]}["corners"]

        # 8.5 不是默认线,但确实被标定过(本测试新增种子)——估算值 11.0
        # 落最后一档,命中率必须是真实数字 0.70,不能因为不是默认线就被抹掉。
        row_85 = next(ln for ln in corners["lines"] if ln["line"] == 8.5)
        assert row_85["signal_grade"] == "★★"
        assert row_85["hit_rate"] == pytest.approx(0.70)
        assert row_85["sample_size"] == 200

    def test_default_line_verdict_unchanged_when_ungraded(self, app, market_fixture):
        """B6 回归钉:8.5/10.5 有★不能让默认线(9.5)的结论跟着"变好看"——
        结论区判定字段只看 9.5 自己有没有标定,与 lines[] 里其它线的星级
        完全无关。这条在改动前后都应该是绿的(9.5 本来就没被标定过)。"""
        self._seed_line_8_5()
        client = TestClient(app)
        body = client.get("/api/v1/matches/9500/markets").json()
        corners = {c["market"]: c for c in body["cards"]}["corners"]

        assert corners["line"] == pytest.approx(9.5)
        assert corners["line_source"] == "statistical"
        assert corners["signal_grade"] is None
        assert corners["lean"] is None
        assert corners["hit_rate"] is None

    def test_calibrated_at_exposed(self, app, market_fixture):
        """yellow_cards(3.5,跨联赛合并标定)在 market_fixture 里已完整定级,
        calibrated_at 必须是造数时写入的真实 ISO 时间戳,不是 None。"""
        client = TestClient(app)
        body = client.get("/api/v1/matches/9500/markets").json()
        yc = {c["market"]: c for c in body["cards"]}["yellow_cards"]
        assert yc["calibrated_at"] == "2026-01-01T00:00:00Z"

    def test_real_integer_line_reports_line_not_calibrated(self, app, market_fixture):
        """真实盘口线 = 10.0,不在 corners.lines=[8.5, 9.5, 10.5] 内(真实角球
        盘口常见整数线,Fix 6)——必须诚实标注"这条线根本不在已标定集合里"
        这个原因,不能跟"线在集合内、但这次查不到档位"共用同一句笼统文案。"""
        conn_core = connect_rw("core")
        kickoff_date = (date.today() + timedelta(days=3)).isoformat()
        insert_match(conn_core, 9505, league_id=47, season="2025/2026", date=kickoff_date,
                     home_id=1001, away_id=1002, home="阿队", away="乙队", status="NotStarted")
        conn_core.commit()
        conn_core.close()

        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, "830003", 9505)
        _seed_odds_snap(conn_odds, "830003", "corners_ou", 10.0)
        conn_odds.commit()
        conn_odds.close()

        client = TestClient(app)
        body = client.get("/api/v1/matches/9505/markets").json()
        corners = {c["market"]: c for c in body["cards"]}["corners"]
        assert corners["line_source"] == "market"
        assert corners["line"] == pytest.approx(10.0)
        assert corners["data_quality"] == "no_calibration"
        assert corners["no_calibration_reason"] == "line_not_calibrated"

    def test_real_calibrated_line_still_resolves_bucket(self, app, market_fixture):
        """9503:真实盘口线 10.5 恰好是 corners.lines 的成员且已标定——回归钉,
        证明 Fix 6 给 no_calibration 加原因细分,不会误伤这条本来就能查到
        档位的真实盘口路径(与既有 test_real_market_line_used_when_odds_present
        断言同一事实,但只挑不随实现变化的字段,全程保持绿)。"""
        client = TestClient(app)
        body = client.get("/api/v1/matches/9503/markets").json()
        corners = {c["market"]: c for c in body["cards"]}["corners"]
        assert corners["line_source"] == "market"
        assert corners["line"] == pytest.approx(10.5)
        assert corners["data_quality"] == "ok"
        assert corners["signal_grade"] == "★★"
        assert corners["hit_rate"] == pytest.approx(0.65)

    def test_extra_team_stat_driver_keys_present_per_market(self, app, market_fixture):
        """Fix 3:折叠区补充已算好但被丢弃的高阶指标,零新查询——纯 driver_keys
        清单扩充。三个市场各自应该出现计划里列的新增 key。"""
        client = TestClient(app)
        body = client.get("/api/v1/matches/9500/markets").json()
        by_market = {c["market"]: c for c in body["cards"]}

        def keys_of(market_key):
            return {row["key"] for row in by_market[market_key]["driver_factors"]}

        assert {"expected_goals_on_target", "big_chance_missed", "shots_inside_box"} <= keys_of("goals")
        assert {"tackles", "duel_won"} <= keys_of("yellow_cards")
        assert {"shots_outside_box", "shot_blocks"} <= keys_of("corners")
