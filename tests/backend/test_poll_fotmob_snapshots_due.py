"""backend/cli/poll_fotmob_snapshots.py --due 分支收敛到 poll_decision()
(PIPELINE_REDESIGN_V2 P1):mark_polled 不再恒为 tier=NULL/ok=1,失败尝试必须
如实记录,不能靠 finally 块的默认参数把失败悄悄记成成功。
"""

from datetime import datetime, timedelta, timezone

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
