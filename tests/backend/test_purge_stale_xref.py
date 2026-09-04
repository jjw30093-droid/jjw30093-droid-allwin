"""backend/cli/purge_stale_xref.py(2026-08-24)——一次性清理 kickoff 严重偏差的
孤儿 dim_match_xref 行(单边模糊匹配 bug 时代遗留,配到了完全不相关的比赛)。
钉住:严格谓词只删中靶行、默认 dry-run 不写库、绝不触碰 confirmed/rejected/
verified=1、写后不变量核对。
"""

import pytest

from backend.cli.purge_stale_xref import purge
from backend.db import migrate
from backend.db.connections import connect_ro, connect_rw
from backend.ingest.entity_resolution import (
    HARD_REJECT_KICKOFF_SECONDS,
    KICKOFF_TOLERANCE_SECONDS,
)

from .coreseed import seed_core_schema


@pytest.fixture
def dbs(data_dir):
    migrate.apply_all("odds", quiet=True)
    conn_core = connect_rw("core")
    seed_core_schema(conn_core)
    conn_core.executemany(
        "INSERT INTO dim_match (Match_ID, Season, League_ID, Date, Home_Team_ID, Away_Team_ID,"
        " Home_Team_Name, Away_Team_Name, status, kickoff_at_utc)"
        " VALUES (?, '2026/2027', 55, '2026-08-24', 9857, 8543, ?, ?, 'NotStarted', '2026-08-24T16:30:00Z')",
        [
            (9001, "Bologna", "Lazio"),
            (9002, "Osasuna", "Levante"),
            (9003, "Fulham", "Chelsea"),
            (9004, "Gil Vicente", "Casa Pia AC"),
        ],
    )
    conn_core.commit()
    conn_core.close()
    yield


def _insert_xref(conn, xref_id, fotmob_match_id, provider_match_id, *,
                  review_status="needs_review", verified=0, method="auto",
                  confidence=0.35, kickoff_diff_seconds=None):
    conn.execute(
        """INSERT INTO dim_match_xref
           (id, fotmob_match_id, provider, provider_match_id, home_away_inverted,
            confidence, verified, method, kickoff_diff_seconds, review_status,
            created_at, updated_at)
           VALUES (?, ?, 'nowgoal', ?, 0, ?, ?, ?, ?, ?, '2026-08-17T05:28:06Z',
                   '2026-08-17T05:28:06Z')""",
        (xref_id, fotmob_match_id, provider_match_id, confidence, verified, method,
         kickoff_diff_seconds, review_status),
    )
    conn.commit()


# 6 小时(HARD_REJECT_KICKOFF_SECONDS)以内/以外的偏差样本
OVER_THRESHOLD_DIFF = HARD_REJECT_KICKOFF_SECONDS + 3600     # 7 小时
UNDER_THRESHOLD_DIFF = 5 * 3600                               # 5 小时,不该被删


class TestPurgeStaleXref:
    def test_dry_run_does_not_write(self, dbs):
        conn = connect_rw("odds")
        _insert_xref(conn, 1, 9001, "3017480", kickoff_diff_seconds=-OVER_THRESHOLD_DIFF)
        conn.close()

        result = purge(commit=False)
        assert result["mode"] == "dry-run"
        assert result["would_delete"] == 1
        assert result["targets"][0]["fotmob_match_id"] == 9001
        assert result["targets"][0]["fotmob_teams"] == "Bologna vs Lazio"

        conn = connect_ro("odds")
        n = conn.execute("SELECT COUNT(*) FROM dim_match_xref").fetchone()[0]
        conn.close()
        assert n == 1   # 什么都没删

    def test_commit_deletes_only_matching_rows(self, dbs):
        conn = connect_rw("odds")
        _insert_xref(conn, 1, 9001, "3017480", kickoff_diff_seconds=-OVER_THRESHOLD_DIFF)
        # 同样 needs_review,但偏差在容差以外、硬拒阈值以内——不该被删
        _insert_xref(conn, 2, 9002, "9999999", kickoff_diff_seconds=UNDER_THRESHOLD_DIFF)
        conn.close()

        result = purge(commit=True)
        assert result["mode"] == "commit"
        assert result["deleted"] == 1

        conn = connect_ro("odds")
        remaining = {r[0] for r in conn.execute("SELECT id FROM dim_match_xref")}
        conn.close()
        assert remaining == {2}

    def test_never_deletes_confirmed_rejected_or_verified(self, dbs):
        conn = connect_rw("odds")
        # 四种"看起来像目标但实际受保护"的行,全部满足极端 kickoff 偏差
        _insert_xref(conn, 1, 9001, "a1", review_status="confirmed",
                      kickoff_diff_seconds=-OVER_THRESHOLD_DIFF)
        _insert_xref(conn, 2, 9002, "a2", review_status="rejected",
                      kickoff_diff_seconds=-OVER_THRESHOLD_DIFF)
        _insert_xref(conn, 3, 9003, "a3", verified=1,
                      kickoff_diff_seconds=-OVER_THRESHOLD_DIFF)
        _insert_xref(conn, 4, 9004, "a4", method="manual",
                      kickoff_diff_seconds=-OVER_THRESHOLD_DIFF)
        conn.close()

        result = purge(commit=True)
        assert result["deleted"] == 0
        assert result["protected_before"] == 3   # confirmed/rejected/verified=1(id 1/2/3)
        assert result["protected_after"] == 3

        conn = connect_ro("odds")
        remaining = {r[0] for r in conn.execute("SELECT id FROM dim_match_xref")}
        conn.close()
        assert remaining == {1, 2, 3, 4}   # 一行都没删

    def test_kickoff_diff_null_not_deleted(self, dbs):
        """kickoff_diff_seconds IS NULL(provenance 缺失,不是"确认配错了")—— 不在
        本脚本清理范围,交给正常的 needs_review 重评分/人工审核流程。"""
        conn = connect_rw("odds")
        _insert_xref(conn, 1, 9001, "3017480", kickoff_diff_seconds=None)
        conn.close()

        result = purge(commit=True)
        assert result["deleted"] == 0

    def test_dry_run_and_commit_report_same_target_count(self, dbs):
        conn = connect_rw("odds")
        _insert_xref(conn, 1, 9001, "3017480", kickoff_diff_seconds=-OVER_THRESHOLD_DIFF)
        conn.close()

        dry = purge(commit=False)
        result = purge(commit=True)
        assert dry["would_delete"] == result["deleted"] == 1


class TestUnpromotableMode:
    """`--unpromotable`(2026-09-04):把阈值降到 KICKOFF_TOLERANCE_SECONDS。

    判据是一条可证明的性质——auto_ok 要求 |kickoff_diff| ≤ 容差,所以超出容差的
    needs_review 行永远不可能被提升,只会占死 UNIQUE(provider, fotmob_match_id)
    名额。生产上正是这批(女足/青年队错配,时差 1.5~5.75 小时)让 9 场比赛永久
    无赔率,而默认的 6 小时阈值够不着它们。
    """

    def test_default_threshold_leaves_sub_hard_reject_rows(self, dbs):
        """回归:默认模式必须仍然够不着 5 小时那条——否则就不是新增开关,
        而是偷偷放宽了既有行为。"""
        conn = connect_rw("odds")
        _insert_xref(conn, 1, 9001, "3017480", kickoff_diff_seconds=UNDER_THRESHOLD_DIFF)
        conn.close()
        assert purge(commit=False)["would_delete"] == 0

    def test_unpromotable_catches_them(self, dbs):
        conn = connect_rw("odds")
        _insert_xref(conn, 1, 9001, "3017480", kickoff_diff_seconds=UNDER_THRESHOLD_DIFF)
        conn.close()
        result = purge(commit=False, threshold=KICKOFF_TOLERANCE_SECONDS)
        assert result["would_delete"] == 1
        assert result["threshold_seconds"] == KICKOFF_TOLERANCE_SECONDS

    def test_within_tolerance_never_deleted(self, dbs):
        """容差**以内**的行还有机会被提升为 auto_ok,任何模式都不许删。"""
        conn = connect_rw("odds")
        _insert_xref(conn, 1, 9001, "3017480",
                     kickoff_diff_seconds=KICKOFF_TOLERANCE_SECONDS - 60)
        conn.close()
        assert purge(commit=False, threshold=KICKOFF_TOLERANCE_SECONDS)["would_delete"] == 0

    def test_protected_rows_still_untouched(self, dbs):
        """放宽阈值不得削弱既有保护:confirmed / verified=1 一律不碰。"""
        conn = connect_rw("odds")
        _insert_xref(conn, 1, 9001, "3017480", kickoff_diff_seconds=UNDER_THRESHOLD_DIFF,
                     review_status="confirmed")
        _insert_xref(conn, 2, 9002, "9999999", kickoff_diff_seconds=UNDER_THRESHOLD_DIFF,
                     verified=1)
        conn.close()
        result = purge(commit=True, threshold=KICKOFF_TOLERANCE_SECONDS)
        assert result["deleted"] == 0
        assert result["protected_before"] == result["protected_after"] == 2
