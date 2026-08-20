"""backend/cli/poll_fotmob_snapshots.py --due 分支收敛到 poll_decision()
(PIPELINE_REDESIGN_V2 P1):mark_polled 不再恒为 tier=NULL/ok=1,失败尝试必须
如实记录,不能靠 finally 块的默认参数把失败悄悄记成成功。
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.cli.poll_fotmob_snapshots import run_snapshot_poll
from backend.db.connections import connect_ro, connect_rw

NOW = datetime(2026, 8, 10, tzinfo=timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


MATCH_ID = 909201


def _seed_match(conn_core, hours_to_kickoff=5.0, league_id=87):
    kickoff = _iso(NOW + timedelta(hours=hours_to_kickoff))
    conn_core.execute(
        "INSERT INTO dim_match (Match_ID, Season, League_ID, Date, Home_Team_ID,"
        " Away_Team_ID, Home_Team_Name, Away_Team_Name, status, kickoff_at_utc,"
        " kickoff_precision, kickoff_source)"
        " VALUES (?, '2026/2027', ?, ?, 111, 222, 'Home', 'Away', 'NotStarted', ?,"
        " 'exact', 'fotmob:fixtures')",
        (MATCH_ID, league_id, kickoff[:10], kickoff),
    )
    conn_core.commit()


def _last_attempt(conn_odds):
    return conn_odds.execute(
        "SELECT tier, ok FROM poll_attempt_log WHERE source='fotmob_snapshot'"
        " AND subject=? ORDER BY id DESC LIMIT 1",
        (str(MATCH_ID),),
    ).fetchone()


def test_failed_fetch_records_honest_ok_false_and_real_tier(data_dir):
    """payload 缺失 → 抓取失败,catch 住继续其它场;但 poll_attempt_log 不能把这次
    失败尝试记成 ok=1、tier=NULL(旧 mark_polled 在 finally 块用默认参数掩盖失败,
    tier 恒 NULL 是因为旧路径从不产出 poll_decision 的 tier)。"""
    conn_core = connect_rw("core")
    try:
        _seed_match(conn_core)
    finally:
        conn_core.close()

    summary = run_snapshot_poll(now_iso=_iso(NOW), offline_payloads={})  # 故意不含该 match

    assert summary["failures"], "预期本场因缺 offline payload 而失败"

    conn_odds = connect_ro("odds")
    try:
        row = _last_attempt(conn_odds)
    finally:
        conn_odds.close()
    assert row is not None
    assert row["ok"] == 0
    assert row["tier"] is not None


def test_successful_fetch_records_real_tier_not_null(data_dir):
    """成功抓取时同样必须写入真实 tier(旧路径恒为 NULL,因为从不调用
    poll_decision(),只算了一个裸 int 间隔)。"""
    conn_core = connect_rw("core")
    try:
        _seed_match(conn_core)
    finally:
        conn_core.close()

    summary = run_snapshot_poll(
        now_iso=_iso(NOW),
        offline_payloads={str(MATCH_ID): {"content": {"lineup": {}}}},
    )
    assert summary["failures"] == []

    conn_odds = connect_ro("odds")
    try:
        row = _last_attempt(conn_odds)
    finally:
        conn_odds.close()
    assert row is not None
    assert row["ok"] == 1
    assert row["tier"] is not None


def _seed_match_id(conn_core, match_id: int, hours_to_kickoff: float, league_id: int = 87) -> None:
    kickoff = _iso(NOW + timedelta(hours=hours_to_kickoff))
    conn_core.execute(
        "INSERT INTO dim_match (Match_ID, Season, League_ID, Date, Home_Team_ID,"
        " Away_Team_ID, Home_Team_Name, Away_Team_Name, status, kickoff_at_utc,"
        " kickoff_precision, kickoff_source)"
        " VALUES (?, '2026/2027', ?, ?, 111, 222, 'Home', 'Away', 'NotStarted', ?,"
        " 'exact', 'fotmob:fixtures')",
        (match_id, league_id, kickoff[:10], kickoff),
    )
    conn_core.commit()


def _seed_poll_state(conn_odds, match_id: int, last_polled_at: str) -> None:
    conn_odds.execute(
        "INSERT INTO poll_state (source, subject, last_polled_at, updated_at)"
        " VALUES ('fotmob_snapshot', ?, ?, ?)",
        (str(match_id), last_polled_at, last_polled_at),
    )
    conn_odds.commit()


class TestCandidatePoolWidth:
    """候选池宽度独立于 cadence 窗口,为 168h(FOTMOB_LINEUP_CANDIDATE_WINDOW_HOURS)
    ——这是唯一能真正防住"候选池与 cadence 窗口相等导致 first_discovery 结构性
    不可达"这次生产事故的测试组。放宽前 poll_fotmob_snapshots.py 用
    FOTMOB_LINEUP_DISCOVERY_HOURS(24)同时充当候选池宽度,今天生产 99 场未来
    比赛(最近一场 T-38.5h)全部落在候选池之外,套件里没有任何测试钉过候选池
    宽度这件事——`window_hours=FOTMOB_LINEUP_DISCOVERY_HOURS` 改成任何值都是绿的。
    """

    def test_match_38h_out_is_a_candidate_and_gets_polled(self, data_dir):
        """T-38.5h 是 2026-08-18 生产只读审计里最近一场比赛的真实开球距离——
        放宽前它结构性地落在候选池(彼时=cadence 窗口=24h)之外。"""
        conn_core = connect_rw("core")
        try:
            _seed_match(conn_core, hours_to_kickoff=38.5)
        finally:
            conn_core.close()

        summary = run_snapshot_poll(
            now_iso=_iso(NOW),
            offline_payloads={str(MATCH_ID): {"content": {"lineup": {}}}},
        )
        assert summary["window_candidates"] == 1
        assert summary["due_matches"] == 1
        assert summary["snapshots_inserted"] >= 1

    @pytest.mark.parametrize("hours", [24.5, 48.0, 71.9])
    def test_whole_24h_to_72h_band_is_in_the_candidate_pool(self, data_dir, hours):
        conn_core = connect_rw("core")
        try:
            _seed_match(conn_core, hours_to_kickoff=hours)
        finally:
            conn_core.close()

        summary = run_snapshot_poll(
            now_iso=_iso(NOW),
            offline_payloads={str(MATCH_ID): {"content": {"lineup": {}}}},
        )
        assert summary["window_candidates"] == 1
        assert summary["due_matches"] == 1
        assert summary["snapshots_inserted"] >= 1

    def test_beyond_72h_first_discovery_reaches_the_poller(self, data_dir):
        """T-80h、poll_state 无记录 → 候选池(168h)把它带进来,poll_decision 给出
        first_discovery,真的抓了一次并记入 poll_attempt_log。同一场比赛 +1h 再跑
        (仍 >72h,但已经轮询过)→ due_matches 归零,证明"一生只多一枪",不是
        无界重复采集。"""
        conn_core = connect_rw("core")
        try:
            _seed_match(conn_core, hours_to_kickoff=80.0)
        finally:
            conn_core.close()

        summary = run_snapshot_poll(
            now_iso=_iso(NOW),
            offline_payloads={str(MATCH_ID): {"content": {"lineup": {}}}},
        )
        assert summary["window_candidates"] == 1
        assert summary["due_matches"] == 1

        conn_odds = connect_ro("odds")
        try:
            row = _last_attempt(conn_odds)
        finally:
            conn_odds.close()
        assert row is not None and row["tier"] == "first_discovery"

        later = _iso(NOW + timedelta(hours=1))
        summary2 = run_snapshot_poll(
            now_iso=later,
            offline_payloads={str(MATCH_ID): {"content": {"lineup": {}}}},
        )
        assert summary2["due_matches"] == 0

    def test_beyond_168h_is_not_a_candidate(self, data_dir):
        """168h01m 之外的比赛根本不进候选池——168h 不是重开被否决的"7 天全覆盖",
        只是首次发现即采那一枪的触达上限。"""
        conn_core = connect_rw("core")
        try:
            _seed_match(conn_core, hours_to_kickoff=168.1)
        finally:
            conn_core.close()

        summary = run_snapshot_poll(
            now_iso=_iso(NOW),
            offline_payloads={str(MATCH_ID): {"content": {"lineup": {}}}},
        )
        assert summary["window_candidates"] == 0
        assert summary["due_matches"] == 0

    def test_counters_sum_to_window_candidates(self, data_dir):
        """window_candidates == due_matches + not_due_skipped + out_of_window_skipped。
        168h 候选池下,"窗内被节流"(not_due_skipped)与"窗外未到首次发现"
        (out_of_window_skipped)是两件不同的事,必须分开计数,否则大多数窗外
        场次无处归属(旧实现下这个不变量会在 168h 池下失败)。"""
        due_id, throttled_id, out_of_window_id = MATCH_ID, MATCH_ID + 1, MATCH_ID + 2

        conn_core = connect_rw("core")
        try:
            _seed_match_id(conn_core, due_id, hours_to_kickoff=38.5)       # 首次 → due
            _seed_match_id(conn_core, throttled_id, hours_to_kickoff=10.0)  # 窗内,已采过
            _seed_match_id(conn_core, out_of_window_id, hours_to_kickoff=100.0)  # 窗外,已采过
        finally:
            conn_core.close()

        conn_odds = connect_rw("odds")
        try:
            _seed_poll_state(conn_odds, throttled_id, _iso(NOW))
            _seed_poll_state(conn_odds, out_of_window_id, _iso(NOW - timedelta(hours=1)))
        finally:
            conn_odds.close()

        summary = run_snapshot_poll(
            now_iso=_iso(NOW),
            offline_payloads={str(due_id): {"content": {"lineup": {}}}},
        )
        assert summary["window_candidates"] == 3
        assert summary["due_matches"] == 1
        assert summary["not_due_skipped"] == 1
        assert summary["out_of_window_skipped"] == 1
        assert (
            summary["due_matches"] + summary["not_due_skipped"] + summary["out_of_window_skipped"]
            == summary["window_candidates"]
        )
