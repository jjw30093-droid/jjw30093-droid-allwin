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
        """英冠(48)需要登录门禁——先验证匿名 401,再登录后验证
        no_history 降级路径(不是 500)。"""
        anon = TestClient(app)
        assert anon.get("/api/v1/matches/9502/markets").status_code == 401

        from .authflow import wechat_scan_login
        client = TestClient(app)
        wechat_scan_login(client, ip="10.9.9.9")
        r = client.get("/api/v1/matches/9502/markets")
        assert r.status_code == 200
        for card in r.json()["cards"]:
            assert card["data_quality"] == "no_history"
            assert card["estimate"] is None

    def test_unknown_match_404(self, app, market_fixture):
        client = TestClient(app)
        assert client.get("/api/v1/matches/9999999/markets").status_code == 404
