"""积分榜 season_finished(2026-09-26):"冠军"只能在赛季结束后显示。

规则:至少一场 Finish,且不存在没结束的比赛。已结束 = Finish/Cancelled/Awarded;
没结束 = NotStarted/InPlay/Postponed,以及任何未登记值与 NULL(fail closed)。
"""

import sqlite3

import pytest

from backend.queries import matches as q
from tests.backend.coreseed import seed_core_schema


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    seed_core_schema(c)
    yield c
    c.close()


def _match(c, mid, league, season, status):
    c.execute(
        "INSERT INTO dim_match (Match_ID, League_ID, Season, status) VALUES (?,?,?,?)",
        (mid, league, season, status),
    )


def test_all_finished_is_true(conn):
    for i in range(3):
        _match(conn, i, 47, "2025/2026", "Finish")
    assert q.season_finished(conn, 47, "2025/2026") is True


def test_one_not_started_is_false(conn):
    _match(conn, 1, 47, "2026/2027", "Finish")
    _match(conn, 2, 47, "2026/2027", "NotStarted")
    assert q.season_finished(conn, 47, "2026/2027") is False


def test_cancelled_and_awarded_count_as_ended(conn):
    _match(conn, 1, 47, "2025/2026", "Finish")
    _match(conn, 2, 47, "2025/2026", "Cancelled")
    _match(conn, 3, 47, "2025/2026", "Awarded")
    assert q.season_finished(conn, 47, "2025/2026") is True


@pytest.mark.parametrize("status", ["NotStarted", "InPlay", "Postponed"])
def test_any_not_ended_status_blocks(conn, status):
    _match(conn, 1, 47, "2025/2026", "Finish")
    _match(conn, 2, 47, "2025/2026", "Cancelled")
    _match(conn, 3, 47, "2025/2026", status)
    assert q.season_finished(conn, 47, "2025/2026") is False


def test_unknown_or_null_status_fails_closed(conn):
    _match(conn, 1, 47, "2025/2026", "Finish")
    _match(conn, 2, 47, "2025/2026", "SomethingNew")
    assert q.season_finished(conn, 47, "2025/2026") is False
    conn.execute("UPDATE dim_match SET status=NULL WHERE Match_ID=2")
    assert q.season_finished(conn, 47, "2025/2026") is False


def test_only_cancelled_without_any_finish_is_not_ended(conn):
    _match(conn, 1, 47, "2025/2026", "Cancelled")
    assert q.season_finished(conn, 47, "2025/2026") is False


def test_every_registered_status_is_classified():
    """known_values 里登记的每个 status 都必须有归属:新增一个值就得回来决定
    它算不算"赛季已结束",不能凭空落进"未知"。"""
    from backend.known_values import DIM_MATCH_STATUS

    classified = q.SEASON_ENDED_STATUSES | q.SEASON_NOT_ENDED_STATUSES
    assert DIM_MATCH_STATUS <= classified
    assert not (q.SEASON_ENDED_STATUSES & q.SEASON_NOT_ENDED_STATUSES)


def test_no_matches_is_false_not_vacuous_true(conn):
    assert q.season_finished(conn, 47, "2025/2026") is False


def test_none_season_is_false(conn):
    assert q.season_finished(conn, 47, None) is False


def test_other_league_or_season_does_not_leak(conn):
    _match(conn, 1, 47, "2025/2026", "Finish")
    _match(conn, 2, 47, "2026/2027", "NotStarted")  # 别的赛季
    _match(conn, 3, 54, "2025/2026", "NotStarted")  # 别的联赛
    assert q.season_finished(conn, 47, "2025/2026") is True


def test_standings_payload_carries_flag(conn):
    conn.execute(
        """INSERT INTO fact_league_table (League_ID, Season, table_type, Team_ID, Team_Name,
                position, played, points) VALUES (47,'2026/2027','all',1,'A',1,1,3)"""
    )
    _match(conn, 1, 47, "2026/2027", "Finish")
    _match(conn, 2, 47, "2026/2027", "NotStarted")
    data = q.standings(conn, 47, "2026/2027")
    assert data["season_finished"] is False
    conn.execute("UPDATE dim_match SET status='Finish'")
    assert q.standings(conn, 47, "2026/2027")["season_finished"] is True
