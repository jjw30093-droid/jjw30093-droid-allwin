"""odds_coverage_for_match / odds_last_observed_for_match(2026-08-19,
比赛详情页性能修复)。

真实缺陷:单场详情端点 GET /api/v1/matches/{id} 为了标注这**一场**比赛的
odds_coverage_tier 和 odds_last_observed_at,调用 odds_coverage_sets() 和
odds_last_observed_by_match() —— 这两个函数遍历全部 737K 条 bronze_ng_odds_snap
索引项、算出全站约 2,291 场比赛的结果,然后只取其中一场、扔掉其余 2,290 场。
生产实测这两个调用合计 209ms,占 match_detail 端点 550ms 的 38%。

本文件的两个新函数只回答"这一场"的问题,必须与整表版对同一个 match_id 的
取值逐位相等——这是纯粹的等价性重构,不引入新语义。列表端点仍然用整表版
(那里"一次算全部、避免 N+1"是正确的优化,不在本次改动范围)。
"""

from __future__ import annotations

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


def _seed_legacy(conn_odds, fotmob_match_id):
    conn_odds.execute(
        """INSERT INTO bronze_legacy_odds_summary
           (fotmob_match_id, source, provider, market, period,
            home_or_over, draw, away_or_under, ingested_at)
           VALUES (?, 'asset_a_json', 'Bet365', '1x2', 'latest',
                   2.0, 3.4, 3.8, '2020-01-01T00:00:00Z')""",
        (fotmob_match_id,),
    )


class TestOddsCoverageForMatchEquivalence:
    def test_full_timeline_match_equals_full_table_result(self, data_dir):
        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, 9001, "titan-9001")
        _seed_snap(conn_odds, "titan-9001", "2026-08-10T00:00:00Z")
        _seed_xref(conn_odds, 9002, "titan-9002")  # 另一场,证明不会被牵连进来
        _seed_snap(conn_odds, "titan-9002", "2026-08-05T00:00:00Z")
        conn_odds.commit()

        full_set, legacy_set = q_odds.odds_coverage_sets(conn_odds)
        assert (9001 in full_set) == q_odds.odds_coverage_for_match(conn_odds, 9001)[0]
        assert (9001 in legacy_set) == q_odds.odds_coverage_for_match(conn_odds, 9001)[1]

    def test_legacy_only_match_equals_full_table_result(self, data_dir):
        conn_odds = connect_rw("odds")
        _seed_legacy(conn_odds, 9010)
        conn_odds.commit()

        full_set, legacy_set = q_odds.odds_coverage_sets(conn_odds)
        full_hit, legacy_hit = q_odds.odds_coverage_for_match(conn_odds, 9010)
        assert full_hit == (9010 in full_set) == False
        assert legacy_hit == (9010 in legacy_set) == True

    def test_needs_review_excluded_same_as_full_table(self, data_dir):
        """review_status 门槛必须与整表版一致——不能因为改成单场查询就悄悄
        放宽/收紧这道门。"""
        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, 9020, "titan-9020", review_status="needs_review")
        _seed_snap(conn_odds, "titan-9020", "2026-08-10T00:00:00Z")
        conn_odds.commit()

        full_set, _ = q_odds.odds_coverage_sets(conn_odds)
        assert 9020 not in full_set
        full_hit, _ = q_odds.odds_coverage_for_match(conn_odds, 9020)
        assert full_hit is False

    def test_no_coverage_at_all(self, data_dir):
        conn_odds = connect_rw("odds")
        conn_odds.commit()
        full_hit, legacy_hit = q_odds.odds_coverage_for_match(conn_odds, 999999)
        assert (full_hit, legacy_hit) == (False, False)

    def test_both_full_and_legacy_present(self, data_dir):
        """同场既有完整时间线又有旧两点摘要——两个布尔独立为真,与整表版
        odds_coverage_sets 的"full 优先展示但两个集合各自独立"语义一致。"""
        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, 9030, "titan-9030")
        _seed_snap(conn_odds, "titan-9030", "2026-08-10T00:00:00Z")
        _seed_legacy(conn_odds, 9030)
        conn_odds.commit()
        full_hit, legacy_hit = q_odds.odds_coverage_for_match(conn_odds, 9030)
        assert (full_hit, legacy_hit) == (True, True)


class TestOddsLastObservedForMatchEquivalence:
    def test_equals_full_table_max(self, data_dir):
        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, 9001, "titan-9001")
        _seed_snap(conn_odds, "titan-9001", "2026-08-01T00:00:00Z")
        _seed_snap(conn_odds, "titan-9001", "2026-08-10T00:00:00Z")
        conn_odds.commit()

        full = q_odds.odds_last_observed_by_match(conn_odds)
        scoped = q_odds.odds_last_observed_for_match(conn_odds, 9001)
        assert scoped == full[9001] == "2026-08-10T00:00:00Z"

    def test_no_snapshot_returns_none_not_kerror(self, data_dir):
        """整表版对没有快照的比赛是"这个 key 不存在";单场版必须是 None,
        不能是抛异常或编造一个假时间。"""
        conn_odds = connect_rw("odds")
        conn_odds.commit()
        assert q_odds.odds_last_observed_for_match(conn_odds, 999999) is None

    def test_needs_review_excluded_same_as_full_table(self, data_dir):
        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, 9040, "titan-9040", review_status="needs_review")
        _seed_snap(conn_odds, "titan-9040", "2026-08-10T00:00:00Z")
        conn_odds.commit()
        full = q_odds.odds_last_observed_by_match(conn_odds)
        assert 9040 not in full
        assert q_odds.odds_last_observed_for_match(conn_odds, 9040) is None

    def test_two_matches_do_not_bleed_into_each_other(self, data_dir):
        """与 test_odds_freshness.py 的既有回归同一个 bug 类别:确认单场版
        对每场各自独立,不会被拉到全库 MAX。"""
        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, 9001, "titan-9001")
        _seed_xref(conn_odds, 9002, "titan-9002")
        _seed_snap(conn_odds, "titan-9001", "2026-08-10T00:00:00Z")
        _seed_snap(conn_odds, "titan-9002", "2026-08-05T00:00:00Z")
        conn_odds.commit()

        assert q_odds.odds_last_observed_for_match(conn_odds, 9001) == "2026-08-10T00:00:00Z"
        assert q_odds.odds_last_observed_for_match(conn_odds, 9002) == "2026-08-05T00:00:00Z"


class TestScopedByMatchIdsEquivalence:
    """列表端点的性能修复(2026-08-19):odds_last_observed_by_match /
    latest_1x2_by_match 加一个可选 match_ids 参数,只有非 boost=free_predicted
    的常见路径才会用它,把结果收窄到本页真正返回的比赛。

    传 None(不传)时必须与现状逐字节相同——这条不变量最重要,因为
    boost=free_predicted 路径明确要求"完整候选窗口内确定性地找",不能被
    这次的性能改动悄悄收窄。"""

    def test_odds_last_observed_none_means_unrestricted_as_before(self, data_dir):
        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, 9001, "titan-9001")
        _seed_xref(conn_odds, 9002, "titan-9002")
        _seed_snap(conn_odds, "titan-9001", "2026-08-10T00:00:00Z")
        _seed_snap(conn_odds, "titan-9002", "2026-08-05T00:00:00Z")
        conn_odds.commit()

        assert q_odds.odds_last_observed_by_match(conn_odds) == \
            q_odds.odds_last_observed_by_match(conn_odds, match_ids=None)

    def test_odds_last_observed_scoped_matches_full_result_restricted(self, data_dir):
        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, 9001, "titan-9001")
        _seed_xref(conn_odds, 9002, "titan-9002")
        _seed_snap(conn_odds, "titan-9001", "2026-08-10T00:00:00Z")
        _seed_snap(conn_odds, "titan-9002", "2026-08-05T00:00:00Z")
        conn_odds.commit()

        full = q_odds.odds_last_observed_by_match(conn_odds)
        scoped = q_odds.odds_last_observed_by_match(conn_odds, match_ids={9001})
        assert scoped == {9001: full[9001]}
        assert 9002 not in scoped

    def test_odds_last_observed_scoped_empty_set_gives_empty_dict(self, data_dir):
        conn_odds = connect_rw("odds")
        conn_odds.commit()
        assert q_odds.odds_last_observed_by_match(conn_odds, match_ids=set()) == {}


def _seed_snap_1x2(conn_odds, provider_match_id, observed_at, company_id="8"):
    payload = '{"home": 2.0, "draw": 3.4, "away": 3.8}'
    conn_odds.execute(
        """INSERT INTO bronze_ng_odds_snap
           (provider_match_id, market, company_id, company_name, market_phase,
            payload_json, payload_hash, observed_at, ingested_at, poll_run_id)
           VALUES (?, '1x2', ?, 'Bet365', 'pre_match', ?, 'x', ?, ?, 'run1')""",
        (provider_match_id, company_id, payload, observed_at, observed_at),
    )


class TestLatest1x2ScopedByMatchIds:
    def test_none_means_unrestricted_as_before(self, data_dir):
        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, 9001, "titan-9001")
        _seed_xref(conn_odds, 9002, "titan-9002")
        _seed_snap_1x2(conn_odds, "titan-9001", "2026-08-10T00:00:00Z")
        _seed_snap_1x2(conn_odds, "titan-9002", "2026-08-05T00:00:00Z")
        conn_odds.commit()

        assert q_odds.latest_1x2_by_match(conn_odds) == \
            q_odds.latest_1x2_by_match(conn_odds, match_ids=None)

    def test_scoped_matches_full_result_restricted(self, data_dir):
        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, 9001, "titan-9001")
        _seed_xref(conn_odds, 9002, "titan-9002")
        _seed_snap_1x2(conn_odds, "titan-9001", "2026-08-10T00:00:00Z")
        _seed_snap_1x2(conn_odds, "titan-9002", "2026-08-05T00:00:00Z")
        conn_odds.commit()

        full = q_odds.latest_1x2_by_match(conn_odds)
        scoped = q_odds.latest_1x2_by_match(conn_odds, match_ids={9001})
        assert scoped == {9001: full[9001]}
        assert 9002 not in scoped

    def test_scoped_empty_set_gives_empty_dict(self, data_dir):
        conn_odds = connect_rw("odds")
        conn_odds.commit()
        assert q_odds.latest_1x2_by_match(conn_odds, match_ids=set()) == {}
