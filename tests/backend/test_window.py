"""backend/queries/window.py 测试:分级 fallback(venue_full → venue_partial →
mixed → unavailable)、最大回溯上限、确定性排序、边界精确到 kickoff。"""

from __future__ import annotations

import pytest

from backend.db.connections import connect_rw
from backend.queries import window as w
from tests.backend.coreseed import insert_match, seed_core_schema

LEAGUE = 47
TEAM = 1001


def _seed_matches(conn, count, *, home, start_id, date_prefix, is_home_for_team):
    """给 TEAM 造 count 场同一主客场类型的历史比赛,近期在前。"""
    for i in range(count):
        mid = start_id + i
        if is_home_for_team:
            insert_match(conn, mid, league_id=LEAGUE, date=f"{date_prefix}-{10+i:02d}",
                         home_id=TEAM, away_id=3000 + i, home="队A", away=f"对手{i}",
                         status="Finish", home_score=1, away_score=0)
        else:
            insert_match(conn, mid, league_id=LEAGUE, date=f"{date_prefix}-{10+i:02d}",
                         home_id=3000 + i, away_id=TEAM, home=f"对手{i}", away="队A",
                         status="Finish", home_score=0, away_score=1)


class TestVenueWindowTiers:
    def test_venue_full_when_ten_or_more_same_venue_matches(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _seed_matches(conn, 12, home=True, start_id=9000, date_prefix="2026-01", is_home_for_team=True)
        conn.commit()
        r = w.venue_window(conn, TEAM, LEAGUE, "2026-02-01", is_home=True)
        assert r.tier == "venue_full"
        assert r.matches == 10  # 封顶 max_n,不是全部 12 场
        assert r.mixed_venues is False

    def test_venue_partial_when_fewer_than_ten_but_at_least_min_n(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _seed_matches(conn, 7, home=True, start_id=9100, date_prefix="2026-01", is_home_for_team=True)
        conn.commit()
        r = w.venue_window(conn, TEAM, LEAGUE, "2026-02-01", is_home=True)
        assert r.tier == "venue_partial"
        assert r.matches == 7
        assert "样本不足" in r.label_zh

    def test_falls_back_to_mixed_when_venue_specific_below_min_n(self, data_dir):
        """同主客场只有 2 场(< min_n=5)——必须回退到混合主客场,
        且返回值里显式标 tier='mixed',界面据此加"已合并主客场"提示。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _seed_matches(conn, 2, home=True, start_id=9200, date_prefix="2026-01", is_home_for_team=True)
        _seed_matches(conn, 4, home=False, start_id=9210, date_prefix="2026-01", is_home_for_team=False)
        conn.commit()
        r = w.venue_window(conn, TEAM, LEAGUE, "2026-02-01", is_home=True)
        assert r.tier == "mixed"
        assert r.matches == 6  # 2 主场 + 4 客场 混合
        assert r.mixed_venues is True
        assert "已合并主客场" in r.label_zh

    def test_unavailable_when_zero_history_even_mixed(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        conn.commit()
        r = w.venue_window(conn, 9999, LEAGUE, "2026-02-01", is_home=True)
        assert r.tier == "unavailable"
        assert r.matches == 0
        assert r.match_ids == []
        assert r.from_date is None and r.to_date is None

    def test_venue_partial_boundary_exactly_at_min_n(self, data_dir):
        """恰好等于 min_n(5)场:必须走 venue_partial,不回退到 mixed
        (5 >= min_n 应该已经"够用",不该被当成不足)。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _seed_matches(conn, 5, home=True, start_id=9300, date_prefix="2026-01", is_home_for_team=True)
        conn.commit()
        r = w.venue_window(conn, TEAM, LEAGUE, "2026-02-01", is_home=True)
        assert r.tier == "venue_partial"
        assert r.matches == 5


class TestMaxLookback:
    def test_matches_older_than_lookback_are_excluded(self, data_dir):
        """两年前的比赛不该被无条件拉进来凑场次——即使凑不够 min_n,
        也要诚实降级,而不是跨越多个赛季硬凑数字。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        # 3 场"新鲜"历史 + 2 场超过 730 天回溯上限的"古老"历史
        _seed_matches(conn, 3, home=True, start_id=9400, date_prefix="2026-01", is_home_for_team=True)
        insert_match(conn, 9410, league_id=LEAGUE, date="2020-01-10",
                     home_id=TEAM, away_id=4000, home="队A", away="老对手1",
                     status="Finish", home_score=1, away_score=0)
        insert_match(conn, 9411, league_id=LEAGUE, date="2020-01-11",
                     home_id=TEAM, away_id=4001, home="队A", away="老对手2",
                     status="Finish", home_score=1, away_score=0)
        conn.commit()
        r = w.venue_window(conn, TEAM, LEAGUE, "2026-02-01", is_home=True)
        # 只有 3 场新鲜历史进窗口(不足 min_n=5,但也不该混进 2020 年的比赛凑数)
        assert r.matches == 3
        assert r.from_date >= "2024-02-01"  # 远早于 730 天回溯上限的比赛不该出现


class TestDeterministicOrder:
    def test_same_calendar_date_matches_ordered_by_kickoff_not_ambiguous(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        insert_match(conn, 9500, league_id=LEAGUE, date="2026-01-10",
                     home_id=TEAM, away_id=5000, home="队A", away="对手甲",
                     status="Finish", home_score=1, away_score=0,
                     kickoff_at_utc="2026-01-10T08:00:00Z")
        insert_match(conn, 9501, league_id=LEAGUE, date="2026-01-10",
                     home_id=TEAM, away_id=5001, home="队A", away="对手乙",
                     status="Finish", home_score=2, away_score=0,
                     kickoff_at_utc="2026-01-10T20:00:00Z")
        conn.commit()
        r = w.venue_window(conn, TEAM, LEAGUE, "2026-01-11", is_home=True, max_n=1, min_n=1)
        assert r.match_ids == [9501]  # kickoff 更晚的那场


class TestFutureExclusion:
    def test_future_match_on_same_day_as_boundary_is_excluded(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        insert_match(conn, 9600, league_id=LEAGUE, date="2026-01-10",
                     home_id=TEAM, away_id=6000, home="队A", away="过去",
                     status="Finish", home_score=1, away_score=0,
                     kickoff_at_utc="2026-01-10T08:00:00Z")
        insert_match(conn, 9601, league_id=LEAGUE, date="2026-01-10",
                     home_id=TEAM, away_id=6001, home="队A", away="未来",
                     status="Finish", home_score=2, away_score=0,
                     kickoff_at_utc="2026-01-10T22:00:00Z")
        conn.commit()
        r = w.venue_window(conn, TEAM, LEAGUE, "2026-01-10T12:00:00Z", is_home=True, max_n=5, min_n=1)
        assert r.match_ids == [9600]  # 22:00 那场晚于边界(12:00),不算历史


class TestLeagueIsolation:
    def test_other_league_matches_never_leak_in(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _seed_matches(conn, 3, home=True, start_id=9700, date_prefix="2026-01", is_home_for_team=True)
        insert_match(conn, 9710, league_id=87, date="2026-01-15",
                     home_id=TEAM, away_id=7000, home="队A", away="西甲对手",
                     status="Finish", home_score=1, away_score=0)
        conn.commit()
        r = w.venue_window(conn, TEAM, LEAGUE, "2026-02-01", is_home=True)
        assert 9710 not in r.match_ids
