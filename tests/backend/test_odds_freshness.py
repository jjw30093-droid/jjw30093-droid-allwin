"""backend.queries.odds 的赔率新鲜度查询——每场独立的最后观测时间与三态分类。

背景:odds_coverage_tier(见 backend/queries/odds.py::odds_coverage_sets)只回答
"这场比赛历史上有没有过 NowGoal 完整时间线赔率快照",完全不含时间信息——哪怕
最后一次真实观测是好几天前,只要 dim_match_xref 曾经映射成功且抓到过一条
market='1x2' 的行,coverage_tier 依然是 full_timeline。

本文件测的是补上的"数据新不新"半边:
- odds_last_observed_by_match 必须按 fotmob_match_id 分组算出每场各自的
  MAX(observed_at)(不能退化成全库 MAX——那样会把陈旧比赛的最后观测时间显示
  成另一场活跃比赛的最新时间,这正是用户报告的 bug);
- classify_odds_freshness 把"最后观测时间 + 当前时间"分类为仓库统一的
  FRESH/STALE/UNAVAILABLE 三态(与 backend/content_status.py::project_freshness
  和 routes_public.py 的 sync_state 同一套词汇)。
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.db.connections import connect_rw
from backend.queries import odds as q_odds


def _seed_xref(conn_odds, fotmob_match_id, provider_match_id, *, review_status="auto_ok"):
    conn_odds.execute(
        """INSERT INTO dim_match_xref
           (fotmob_match_id, provider, provider_match_id, home_away_inverted, confidence,
            verified, method, review_status, created_at, updated_at)
           VALUES (?, 'nowgoal', ?, 0, 1.0, 1, 'auto', ?,
                   '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z')""",
        (fotmob_match_id, provider_match_id, review_status),
    )


def _seed_snap(conn_odds, provider_match_id, observed_at, *, market="1x2"):
    payload = '{"home": 2.0, "draw": 3.4, "away": 3.8}'
    conn_odds.execute(
        """INSERT INTO bronze_ng_odds_snap
           (provider_match_id, market, company_id, company_name, market_phase,
            payload_json, payload_hash, observed_at, ingested_at, poll_run_id)
           VALUES (?, ?, '8', 'Bet365', 'pre_match', ?, 'x', ?, ?, 'run1')""",
        (provider_match_id, market, payload, observed_at, observed_at),
    )


class TestOddsLastObservedByMatch:
    def test_per_match_not_global_max(self, data_dir):
        """核心 bug 复现:两场比赛必须各自拿到自己的最后观测时间,不能被全库
        MAX 拉到同一个值。"""
        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, 9001, "titan-9001")
        _seed_xref(conn_odds, 9002, "titan-9002")
        _seed_snap(conn_odds, "titan-9001", "2026-08-01T00:00:00Z")
        _seed_snap(conn_odds, "titan-9001", "2026-08-10T00:00:00Z")  # 9001 更新
        _seed_snap(conn_odds, "titan-9002", "2026-08-05T00:00:00Z")  # 9002 更旧
        conn_odds.commit()

        result = q_odds.odds_last_observed_by_match(conn_odds)

        assert result[9001] == "2026-08-10T00:00:00Z"
        assert result[9002] == "2026-08-05T00:00:00Z"
        assert result[9001] != result[9002]  # 不是被全库 MAX 拉到同一个值

    def test_review_status_gate_matches_coverage_sets(self, data_dir):
        """needs_review/rejected 不计入——与 odds_coverage_sets 同一 JOIN 条件。"""
        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, 9003, "titan-9003", review_status="needs_review")
        _seed_snap(conn_odds, "titan-9003", "2026-08-10T00:00:00Z")
        conn_odds.commit()

        result = q_odds.odds_last_observed_by_match(conn_odds)
        assert 9003 not in result

    def test_no_snapshots_absent_from_result(self, data_dir):
        """没有任何 full_timeline 快照的比赛不在返回的 dict 里(不编造 0/None 值)。"""
        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, 9004, "titan-9004")
        conn_odds.commit()
        result = q_odds.odds_last_observed_by_match(conn_odds)
        assert 9004 not in result

    def test_legacy_only_match_absent_from_result(self, data_dir):
        """legacy 两点摘要是一次性历史导入,不在这个函数的覆盖范围内(不伪造
        新鲜度信号)。"""
        conn_odds = connect_rw("odds")
        conn_odds.execute(
            """INSERT INTO bronze_legacy_odds_summary
                 (fotmob_match_id, source, provider, market, period, line,
                  home_or_over, draw, away_or_under, orientation_fixed,
                  source_file, ingested_at)
               VALUES (9005, 'asset_a_json', 'Bet365', '1x2', 'latest', NULL,
                       2.0, 3.4, 3.8, 0, 'test', '2026-08-06T00:00:00Z')"""
        )
        conn_odds.commit()
        result = q_odds.odds_last_observed_by_match(conn_odds)
        assert 9005 not in result


class TestClassifyOddsFreshness:
    def test_none_is_unavailable(self):
        assert q_odds.classify_odds_freshness(None) == "UNAVAILABLE"

    def test_recent_is_fresh(self):
        now = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)
        assert q_odds.classify_odds_freshness("2026-08-16T10:00:00Z", now=now) == "FRESH"

    def test_older_than_threshold_is_stale(self):
        now = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)
        assert q_odds.classify_odds_freshness("2026-08-10T00:00:00Z", now=now) == "STALE"

    def test_threshold_boundary_six_hours(self):
        """阈值取自 backend/cli/ops_check.py 的 SOURCE_STALE_HOURS=6。"""
        now = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)
        assert q_odds.classify_odds_freshness("2026-08-16T06:00:00Z", now=now) == "FRESH"
        assert q_odds.classify_odds_freshness("2026-08-16T05:59:59Z", now=now) == "STALE"
