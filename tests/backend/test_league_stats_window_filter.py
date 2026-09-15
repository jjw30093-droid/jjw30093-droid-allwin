"""backend/queries/league_stats.py::team_season_stats() 的"最近 N 场/主客场"
筛选专用测试(2026-09-14 新增)。

只测 team_season_stats() 这一层的接线是否正确(物化表路径 vs 现算路径的
分支选择、matches_played 随筛选变化、avg_expected_goals_conceded 筛选时
降级为 None、来源方榜单筛选时整段隐藏、赛季就绪门槛不受筛选窗口影响)——
不重复测 resolve_match_window()/match_window_join_sql()/
build_team_season_stats()/build_team_season_ratios() 各自的聚合正确性,
那些已经分别在 test_window.py/test_silver_ratios.py/
test_ratio_cadence_parity.py 覆盖。
"""

from __future__ import annotations

import json

import pytest

from backend.db.connections import connect_rw
from backend.queries import league_stats as q
from tests.backend.coreseed import insert_match, seed_core_schema

LEAGUE = 47
SEASON = "2025/2026"
TEAM = 1001
OPP = 1002


def _team_stats_row(conn, match_id, team_id, total_shots, extra=None):
    payload = {"total_shots": total_shots}
    if extra:
        payload.update(extra)
    conn.execute(
        "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
        " VALUES (?, ?, 'All', 1, ?)",
        (match_id, team_id, json.dumps(payload)),
    )


def _seed_silver_row(conn, team_id, matches_played, avg_total_shots):
    conn.execute(
        """INSERT INTO silver_team_season_stats
           (League_ID, Season, Team_ID, matches_played, avg_total_shots)
           VALUES (?, ?, ?, ?, ?)""",
        (LEAGUE, SEASON, team_id, matches_played, avg_total_shots),
    )


def _seed_six_matches(conn):
    """给 TEAM 造 3 主 3 客、共 6 场完赛比赛,按时间顺序交替主客场,
    total_shots 依次 10..15(越晚的比赛数值越大)——同一批数据可以同时验证
    recency(按时间取最近 N 场)与 venue(按主客场切片)两个维度,互不干扰:

      i     0   1   2   3   4   5
      date  10  11  12  13  14  15
      venue 主  客  主  客  主  客
      shots 10  11  12  13  14  15

    最近 3 场(i=3,4,5)= 13,14,15,均值 14;全部主场(i=0,2,4)= 10,12,14,
    均值 12;全部客场(i=1,3,5)= 11,13,15,均值 13。
    """
    for i in range(6):
        mid = 8100 + i
        is_home = i % 2 == 0
        date = f"2025-09-{10 + i:02d}"
        shots = 10 + i
        if is_home:
            insert_match(
                conn, mid, league_id=LEAGUE, season=SEASON, date=date,
                home_id=TEAM, away_id=OPP, home="队A", away="队B",
                status="Finish", home_score=1, away_score=0,
            )
        else:
            insert_match(
                conn, mid, league_id=LEAGUE, season=SEASON, date=date,
                home_id=OPP, away_id=TEAM, home="队B", away="队A",
                status="Finish", home_score=0, away_score=1,
            )
        _team_stats_row(conn, mid, TEAM, shots)
        _team_stats_row(conn, mid, OPP, 5)


@pytest.fixture
def core_conn(data_dir):
    conn = connect_rw("core")
    seed_core_schema(conn)
    _seed_six_matches(conn)
    _seed_silver_row(conn, TEAM, 6, 12.5)
    yield conn
    conn.close()


class TestUnfilteredPathUnchanged:
    def test_no_filter_reads_materialized_table(self, core_conn):
        """默认参数(recency=None, venue='all')必须继续读物化表的赛季均值,
        不是现算——两者对同一批 fixture 会算出不同的 avg_total_shots
        (物化表哨兵值 12.5 vs 逐场均值 12.5 恰好相等时测不出区别,所以哨兵
        值故意设成与逐场均值不同的 999.9,证明确实读的是物化表)。"""
        core_conn.execute(
            "UPDATE silver_team_season_stats SET avg_total_shots=999.9"
            " WHERE League_ID=? AND Season=? AND Team_ID=?",
            (LEAGUE, SEASON, TEAM),
        )
        core_conn.commit()
        data = q.team_season_stats(core_conn, LEAGUE, SEASON)
        row = next(r for r in data["rows"] if r["team"]["team_id"] == TEAM)
        assert row["matches_played"] == 6
        assert row["avg_total_shots"] == pytest.approx(999.9)


class TestRecencyAndVenueFilter:
    def test_recency_filter_recomputes_from_recent_matches_only(self, core_conn):
        core_conn.commit()
        data = q.team_season_stats(core_conn, LEAGUE, SEASON, recency=3, venue="all")
        row = next(r for r in data["rows"] if r["team"]["team_id"] == TEAM)
        assert row["matches_played"] == 3
        assert row["avg_total_shots"] == pytest.approx(14.0)

    def test_venue_filter_recomputes_from_home_matches_only(self, core_conn):
        core_conn.commit()
        data = q.team_season_stats(core_conn, LEAGUE, SEASON, recency=None, venue="home")
        row = next(r for r in data["rows"] if r["team"]["team_id"] == TEAM)
        assert row["matches_played"] == 3
        assert row["avg_total_shots"] == pytest.approx(12.0)

    def test_venue_filter_recomputes_from_away_matches_only(self, core_conn):
        core_conn.commit()
        data = q.team_season_stats(core_conn, LEAGUE, SEASON, recency=None, venue="away")
        row = next(r for r in data["rows"] if r["team"]["team_id"] == TEAM)
        assert row["matches_played"] == 3
        assert row["avg_total_shots"] == pytest.approx(13.0)

    def test_recency_and_venue_combine(self, core_conn):
        """最近 2 场主场:i=2,4(日期 12、14),不是最近 2 场里再挑主场——
        venue 先把比赛集合限定成"该队是主队",recency 再在这个子集里按时间
        取最近 N 场,顺序不能反(方案明确:主场筛选是"该队自己视角的主场
        比赛",不是全局概念)。"""
        core_conn.commit()
        data = q.team_season_stats(core_conn, LEAGUE, SEASON, recency=2, venue="home")
        row = next(r for r in data["rows"] if r["team"]["team_id"] == TEAM)
        assert row["matches_played"] == 2
        assert row["avg_total_shots"] == pytest.approx(13.0)  # (12+14)/2

    def test_venue_filter_does_not_affect_other_teams_home_count(self, core_conn):
        """OPP 在这批数据里是 TEAM 的全部 6 场比赛的对手,但 OPP 自己的主场
        次数是相反的(TEAM 主场时 OPP 客场,反之亦然)——验证 venue 是按队
        独立视角算的,不是"联赛里发生在主场的比赛"这种全局概念。"""
        core_conn.commit()
        data = q.team_season_stats(core_conn, LEAGUE, SEASON, recency=None, venue="home")
        opp_row = next(r for r in data["rows"] if r["team"]["team_id"] == OPP)
        assert opp_row["matches_played"] == 3  # OPP 客场时(即 TEAM 主场)= 3 场


class TestSeasonReadinessGateIgnoresFilter:
    def test_gate_uses_unfiltered_season_matches_played(self, core_conn):
        """赛季就绪门槛(MIN_MATCHES_FOR_SEASON_DATA=4)看整赛季样本,不受
        筛选窗口大小影响——筛"最近 1 场"不代表这个赛季只踢了 1 场。"""
        core_conn.commit()
        data = q.team_season_stats(core_conn, LEAGUE, SEASON, recency=1, venue="all")
        assert "empty_reason" not in data
        row = next(r for r in data["rows"] if r["team"]["team_id"] == TEAM)
        assert row["matches_played"] == 1

    def test_gate_still_blocks_immature_season_even_with_filter(self, core_conn):
        """反方向:整赛季本身就不够成熟(物化表 matches_played < 4)时,
        筛选参数不能绕过这道门槛。"""
        core_conn.execute(
            "UPDATE silver_team_season_stats SET matches_played=2"
            " WHERE League_ID=? AND Season=? AND Team_ID=?",
            (LEAGUE, SEASON, TEAM),
        )
        core_conn.commit()
        data = q.team_season_stats(core_conn, LEAGUE, SEASON, recency=1, venue="all")
        assert data["rows"] == []
        assert "empty_reason" in data


class TestAvgExpectedGoalsConcededUnderFilter:
    def test_falls_back_to_none_when_filtered(self, core_conn):
        """被创造 xG(avg_expected_goals_conceded)来自 fact_league_table 的
        赛季级 xg 档,没有逐场明细,筛选窗口下算不出对应版本——必须如实标
        None,不能继续显示未筛选时算出的赛季值(那会让用户以为这是"最近
        3 场"的被创造 xG,实际是整季的)。"""
        core_conn.execute(
            "INSERT INTO fact_league_table (League_ID, Season, table_type, Team_ID,"
            " Team_Name, position, played, xg_conceded)"
            " VALUES (?, ?, 'xg', ?, '队A', 1, 6, 12.0)",
            (LEAGUE, SEASON, TEAM),
        )
        core_conn.commit()

        unfiltered = q.team_season_stats(core_conn, LEAGUE, SEASON)
        row = next(r for r in unfiltered["rows"] if r["team"]["team_id"] == TEAM)
        assert row["avg_expected_goals_conceded"] == pytest.approx(2.0)

        filtered = q.team_season_stats(core_conn, LEAGUE, SEASON, recency=3, venue="all")
        frow = next(r for r in filtered["rows"] if r["team"]["team_id"] == TEAM)
        assert frow["avg_expected_goals_conceded"] is None


class TestSourceBoardsHiddenUnderFilter:
    def test_boards_present_unfiltered_empty_when_filtered(self, core_conn):
        core_conn.execute(
            "INSERT INTO fact_season_team_stats (League_ID, Season, stat_name, Team_ID,"
            " Team_Name, rank, value, extra_json)"
            " VALUES (?, ?, 'poss_won_att_3rd_team', ?, '队A', 1, 5.1,"
            " '{\"stat_title\": \"Poss won Att 3rd per match\","
            " \"stat_format\": \"fraction\", \"stat_decimals\": 1}')",
            (LEAGUE, SEASON, TEAM),
        )
        core_conn.commit()

        unfiltered = q.team_season_stats(core_conn, LEAGUE, SEASON)
        assert unfiltered["boards"], "未筛选时来源方榜单应正常展示"

        for recency, venue in ((3, "all"), (None, "home"), (5, "away")):
            filtered = q.team_season_stats(
                core_conn, LEAGUE, SEASON, recency=recency, venue=venue
            )
            assert filtered["boards"] == [], f"筛选激活时(recency={recency}, venue={venue})来源方榜单必须整段隐藏"


class TestRatiosUnderFilter:
    def test_ratios_recomputed_and_wired_through(self, core_conn):
        """筛选路径的 ratios 走现算(build_team_season_ratios),不报错、且
        matches_played 随筛选变化——具体各指标的聚合正确性已由
        test_silver_ratios.py/test_ratio_cadence_parity.py 覆盖,这里只测
        team_season_stats() 是否正确把 window_pairs/matches_played_by_team
        接进 _team_ratios()。"""
        for mid in range(8100, 8106):
            core_conn.execute(
                "UPDATE fact_team_match_stats SET extra_json=json_set(extra_json,"
                " '$.opposition_half_passes', 100, '$.accurate_passes', 200)"
                " WHERE Match_ID=? AND Team_ID=?",
                (mid, TEAM),
            )
        core_conn.commit()

        data = q.team_season_stats(core_conn, LEAGUE, SEASON, recency=3, venue="all")
        row = next(r for r in data["rows"] if r["team"]["team_id"] == TEAM)
        assert row["ratios"] is not None
        ratio = row["ratios"]["opp_half_pass_share"]
        assert ratio["value"] == pytest.approx(50.0)
        assert ratio["paired_matches"] == 3
        assert ratio["matches_played"] == 3
