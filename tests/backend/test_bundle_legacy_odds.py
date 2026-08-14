"""analysis bundle 的赔率覆盖档位 + legacy 两点摘要(2026-08-06 审计 B6)。

此前 build_analysis_bundle 只认 dim_match_xref→bronze_ng_odds_snap 一条路,
对 8,336 场只有 bronze_legacy_odds_summary 的比赛返回空 odds_timeline——
而同一场比赛 /odds 端点能给出真实点位,两个端点自相矛盾。

不变量:
- 两点摘要无观测时间戳,只进 odds_summary_points,绝不混入 odds_timeline
  (BundleOddsPoint.observed_at 必填,§6.2 不伪装);
- 公开 /analysis 按 odds:history_full 投影:非 Premium 的 odds_summary_points
  为 None(受限数据物理不下发,不是空数组占位)。
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
    def test_legacy_only_match_anonymous_projection(self, app, bundle_matches):
        c = TestClient(app)
        d = c.get("/api/v1/matches/8101/analysis").json()
        assert d["odds_coverage_tier"] == "open_close_only"
        assert d["odds_summary_points"] is None      # 非 Premium:物理不下发
        assert d["odds_timeline"] == []              # 两点摘要绝不混入时间线

    def test_legacy_only_match_premium_gets_both_periods(self, app, bundle_matches, fresh_ip):
        c = TestClient(app)
        _login(c, fresh_ip)   # 三段可见性:登录即 member 基线(odds:history_full)
        d = c.get("/api/v1/matches/8101/analysis").json()
        assert d["odds_coverage_tier"] == "open_close_only"
        pts = d["odds_summary_points"]
        assert pts is not None
        assert {p["period"] for p in pts} == {"initial", "latest"}
        # 每个点都没有任何时间字段(§6.2:无观测时间戳,不伪装)
        assert all("observed_at" not in p and "ingested_at" not in p for p in pts)
        assert d["odds_timeline"] == []              # 时间线仍为空,不造假点

    def test_no_odds_match_tier_none(self, app, bundle_matches):
        c = TestClient(app)
        d = c.get("/api/v1/matches/8102/analysis").json()
        assert d["odds_coverage_tier"] == "none"
        assert d["odds_summary_points"] is None
