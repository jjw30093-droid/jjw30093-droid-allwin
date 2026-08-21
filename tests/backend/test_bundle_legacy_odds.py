"""analysis bundle 的赔率覆盖档位 + legacy 两点摘要(2026-08-06 审计 B6)。

此前 build_analysis_bundle 只认 dim_match_xref→bronze_ng_odds_snap 一条路,
对 8,336 场只有 bronze_legacy_odds_summary 的比赛返回空 odds_timeline——
而同一场比赛 /odds 端点能给出真实点位,两个端点自相矛盾。

不变量:
- 两点摘要无观测时间戳,只进 odds_summary_points,绝不混入 odds_timeline
  (BundleOddsPoint.observed_at 必填,§6.2 不伪装);
- 公开 /analysis 恒完整下发 odds_summary_points(2026-08-16 起除"每日精选"外
  全站比赛内容与登录/付费彻底解耦,不再有 odds:history_full 投影裁剪)。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.db.connections import connect_rw

from .coreseed import insert_match, seed_core_schema

from .authflow import wechat_scan_login


def _seed_legacy(match_id: int, period: str, price: float) -> None:
    conn = connect_rw("odds")
    conn.execute(
        """INSERT INTO bronze_legacy_odds_summary
             (fotmob_match_id, source, provider, market, period, line,
              home_or_over, draw, away_or_under, orientation_fixed,
              source_file, ingested_at)
           VALUES (?, 'asset_a_json', 'Bet365', '1x2', ?, NULL,
                   ?, 3.5, 4.2, 0, 'test', '2026-08-06T00:00:00Z')""",
        (match_id, period, price),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def bundle_matches(data_dir):
    conn = connect_rw("core")
    seed_core_schema(conn)
    # EPL(免费联赛,匿名可访问 analysis 端点)
    insert_match(conn, 8101, league_id=47, season="2021/2022", date="2022-01-10",
                 status="Finish", home_score=1, away_score=0,
                 kickoff_at_utc="2022-01-10T15:00:00Z")   # legacy-only
    insert_match(conn, 8102, league_id=47, season="2021/2022", date="2022-01-17",
                 status="Finish", home_score=2, away_score=2,
                 kickoff_at_utc="2022-01-17T15:00:00Z")   # no odds
    conn.commit()
    conn.close()
    _seed_legacy(8101, "initial", 2.10)
    _seed_legacy(8101, "latest", 1.95)
    return data_dir


def _login(client, ip):
    wechat_scan_login(client, ip=ip)
    return client.get("/api/v1/me").json()["user"]["id"]


class TestBundleLegacyOdds:
    def test_legacy_only_match_anonymous_gets_both_periods(self, app, bundle_matches):
        """2026-08-16 产品权限口径修正:除"每日精选"外普通比赛内容全部免费,
        包括匿名——两点摘要不再要求 odds:history_full。这条断言正是要推翻
        的旧规则(此前匿名 odds_summary_points 恒为 None)。"""
        c = TestClient(app)
        d = c.get("/api/v1/matches/8101/analysis").json()
        assert d["odds_coverage_tier"] == "open_close_only"
        pts = d["odds_summary_points"]
        assert pts is not None
        assert {p["period"] for p in pts} == {"initial", "latest"}
        # 每个点都没有任何时间字段(§6.2:无观测时间戳,不伪装)
        assert all("observed_at" not in p and "ingested_at" not in p for p in pts)
        assert d["odds_timeline"] == []              # 两点摘要绝不混入时间线

    def test_legacy_only_match_member_gets_identical_content(self, app, bundle_matches, fresh_ip):
        """登录与内容分层彻底解耦:登录后的内容必须与匿名逐字段一致。"""
        anon = TestClient(app).get("/api/v1/matches/8101/analysis").json()
        c = TestClient(app)
        _login(c, fresh_ip)
        member = c.get("/api/v1/matches/8101/analysis").json()
        assert member["odds_summary_points"] == anon["odds_summary_points"]
        assert member["odds_timeline"] == anon["odds_timeline"]

    def test_no_odds_match_tier_none(self, app, bundle_matches):
        c = TestClient(app)
        d = c.get("/api/v1/matches/8102/analysis").json()
        assert d["odds_coverage_tier"] == "none"
        assert d["odds_summary_points"] is None


class TestLegacyOddsFloatNoise:
    """真实用户报告(2026-08-21):赔率时间轴显示 "1.9300000000000002"。

    根因是 bronze_legacy_odds_summary 的存量数据里,少数行在写入源头就带
    IEEE754 ULP 噪声(与真实值偏差恒为 1-2 个 ULP,如 1.93 存成
    1.9300000000000002)——数据已按 2 位小数一次性清洗过(见
    backend.cli.fix_legacy_odds_float_noise),这里钉住 legacy_summary_points
    的第二道防线:即便某一行仍然/再次带噪声,/odds 与 /analysis 两个端点
    也必须把它清洗成两位小数再下发,不能把噪声原样透传给前端。
    """

    def test_odds_endpoint_cleans_float_noise(self, app, bundle_matches):
        conn = connect_rw("odds")
        conn.execute(
            """UPDATE bronze_legacy_odds_summary
                  SET home_or_over=1.9300000000000002
                WHERE fotmob_match_id=8101 AND period='initial'"""
        )
        conn.commit()
        conn.close()

        c = TestClient(app)
        d = c.get("/api/v1/matches/8101/odds").json()
        points = d["summary_points"]
        initial = next(p for p in points if p["period"] == "initial")
        assert initial["home_or_over"] == 1.93

    def test_analysis_bundle_cleans_float_noise(self, app, bundle_matches):
        conn = connect_rw("odds")
        conn.execute(
            """UPDATE bronze_legacy_odds_summary
                  SET home_or_over=1.9300000000000002
                WHERE fotmob_match_id=8101 AND period='initial'"""
        )
        conn.commit()
        conn.close()

        c = TestClient(app)
        d = c.get("/api/v1/matches/8101/analysis").json()
        initial = next(p for p in d["odds_summary_points"] if p["period"] == "initial")
        assert initial["home_or_over"] == 1.93
