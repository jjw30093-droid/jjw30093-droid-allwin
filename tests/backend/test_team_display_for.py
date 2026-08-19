"""team_display_for(conn, team_ids):team_display_map 的按需(scoped)版本
(2026-08-19,详情页性能修复)。

真实缺陷:team_display_map() 为了拿到 dim_team_i18n 里 304 条球队译名,每次
都额外 UNION ALL 扫 dim_match 两遍(33,868 行)+ 扫 fact_league_table,再对
约 39,000 行逐行跑正则——生产实测单次 66–75ms,且被 match_by_id/recent_form
(×2)/list_matches/recent_shot_map_spec/league_style_views 各自独立调用,
一个比赛详情请求里跑 3 次、analysis 请求里跑 4 次(占该端点 88–93% 耗时)。

team_display_for 只需要请求方点名的少数几个 team_id,却必须对这些 id 给出
与 team_display_map() 逐字段相同的结果——包括三层来源优先级(dim_team_i18n
name_zh → dim_match 同名字段 → fact_league_table.Team_Name,先到先得、不
覆盖已有值)和 _usable_name 的三种排除规则(空串/纯数字/"team \\d+" 占位符)。
本文件只验证等价性,不引入新语义。
"""

from __future__ import annotations

import sqlite3

from backend.queries.teams import team_display_for, team_display_map


def _core() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE dim_match (
          Match_ID INTEGER PRIMARY KEY,
          League_ID INTEGER,
          Home_Team_ID INTEGER, Home_Team_Name TEXT,
          Away_Team_ID INTEGER, Away_Team_Name TEXT
        );
        CREATE TABLE dim_team_i18n (
          Team_ID INTEGER PRIMARY KEY, name_en TEXT, name_zh TEXT
        );
        CREATE TABLE fact_league_table (
          Team_ID INTEGER, Team_Name TEXT
        );
        """
    )
    return conn


class TestTeamDisplayForEquivalence:
    def test_i18n_hit_matches_full_map(self):
        """有中文译名的球队:两个函数对该 id 的结果必须逐字段相等。"""
        conn = _core()
        conn.execute(
            "INSERT INTO dim_team_i18n VALUES (8455, 'Chelsea', '切尔西')"
        )
        conn.execute(
            "INSERT INTO dim_match VALUES (1, 47, 8455, 'Chelsea', 9825, 'Arsenal')"
        )
        full = team_display_map(conn)
        scoped = team_display_for(conn, {8455})
        assert scoped[8455] == full[8455]

    def test_missing_i18n_falls_back_to_dim_match_provider_name(self):
        """没有中文译名、但 dim_match 里有可用英文名的球队:第二层来源一致。"""
        conn = _core()
        conn.execute(
            "INSERT INTO dim_match VALUES (1, 47, 9001, 'Some New Club', 9825, 'Arsenal')"
        )
        full = team_display_map(conn)
        scoped = team_display_for(conn, {9001})
        assert scoped[9001] == full[9001]
        assert scoped[9001]["name_zh"] is None
        assert scoped[9001]["provider_name"] == "Some New Club"

    def test_dim_match_placeholder_falls_back_to_fact_league_table(self):
        """dim_match 里这一行本身是占位符名("Team 9002"),但 fact_league_table
        里有真名——两个函数都要能挖到 fact_league_table 那一层。"""
        conn = _core()
        conn.execute(
            "INSERT INTO dim_match VALUES (1, 47, 9002, 'Team 9002', 9825, 'Arsenal')"
        )
        conn.execute("INSERT INTO fact_league_table VALUES (9002, 'Real Name FC')")
        full = team_display_map(conn)
        scoped = team_display_for(conn, {9002})
        assert scoped[9002] == full[9002]
        assert scoped[9002]["provider_name"] == "Real Name FC"

    def test_first_source_wins_not_overwritten_by_later_source(self):
        """dim_match 先给出可用名时,fact_league_table 的名字不应覆盖它——
        两个函数的"先到先得"优先级必须一致。"""
        conn = _core()
        conn.execute(
            "INSERT INTO dim_match VALUES (1, 47, 9003, 'First Name FC', 9825, 'Arsenal')"
        )
        conn.execute("INSERT INTO fact_league_table VALUES (9003, 'Second Name FC')")
        full = team_display_map(conn)
        scoped = team_display_for(conn, {9003})
        assert scoped[9003]["provider_name"] == "First Name FC"
        assert scoped[9003] == full[9003]

    def test_completely_unknown_team_id_absent_from_both(self):
        """两个函数对完全没见过的 id 都不应该编造条目。"""
        conn = _core()
        full = team_display_map(conn)
        scoped = team_display_for(conn, {424242})
        assert 424242 not in full
        assert 424242 not in scoped

    def test_multiple_ids_at_once_each_equal_to_full_map(self):
        conn = _core()
        conn.execute("INSERT INTO dim_team_i18n VALUES (8455,'Chelsea','切尔西')")
        conn.execute("INSERT INTO dim_match VALUES (1,47,8455,'Chelsea',9825,'Arsenal')")
        conn.execute("INSERT INTO dim_match VALUES (2,47,9001,'New Club',8455,'Chelsea')")
        full = team_display_map(conn)
        scoped = team_display_for(conn, {8455, 9825, 9001})
        for tid in (8455, 9825, 9001):
            assert scoped[tid] == full[tid], tid

    def test_empty_team_ids_returns_empty(self):
        conn = _core()
        assert team_display_for(conn, set()) == {}

    def test_i18n_table_missing_degrades_like_full_map(self):
        """dim_team_i18n 表不存在时(极端/迁移中状态),两者都必须不抛错,
        只退回 dim_match/fact_league_table 来源。"""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE dim_match (
              Match_ID INTEGER PRIMARY KEY, League_ID INTEGER,
              Home_Team_ID INTEGER, Home_Team_Name TEXT,
              Away_Team_ID INTEGER, Away_Team_Name TEXT
            );
            CREATE TABLE fact_league_table (Team_ID INTEGER, Team_Name TEXT);
            """
        )
        conn.execute("INSERT INTO dim_match VALUES (1,47,9001,'Some Club',9825,'Arsenal')")
        full = team_display_map(conn)
        scoped = team_display_for(conn, {9001})
        assert scoped[9001] == full[9001]
