"""来源方赛季球队榜(/api/v1/leagues/{id}/team-stats 的 boards 字段)。

2026-09-10 新增。这一组榜与同一响应里的 rows 是**两套口径**:rows 是我们自己
从单场数据聚合的 silver_team_season_stats,boards 是 FotMob 自己算的赛季榜。
两者不可能逐位相等,所以本模块要守住的头号不变量是:

  boards 里的维度,不得与 TeamSeasonStatRow 已有的字段语义重复。

同一个指标在同一页出现两个不同的数字,比少一张卡片糟糕得多。
"""

import json

import pytest

from backend.api.schemas import TeamSeasonStatRow
from backend.db.connections import connect_rw
from backend.queries import league_stats as q

from .coreseed import seed_basic_core


@pytest.fixture
def core_conn(data_dir):
    """带布景的 core 读写连接(部分用例要 DROP/UPDATE 造降级场景)。"""
    seed_basic_core(data_dir)
    conn = connect_rw("core")
    try:
        yield conn
    finally:
        conn.close()


class TestBoardSelection:
    def test_no_overlap_with_our_own_aggregation(self):
        """来源榜不得撞上我们自己算的字段(逐条人工核对过的对照表)。"""
        # key = 来源 stat_name,value = TeamSeasonStatRow 里的同义字段。
        # 这张表是"已知会撞车、故意不收"的清单,不是全部来源维度。
        known_overlaps = {
            "possession_percentage_team": "avg_possession",
            "ontarget_scoring_att_team": "avg_shots_on_target",
            "expected_goals_team": "avg_expected_goals",
            "expected_goals_conceded_team": "avg_expected_goals_conceded",
            "corner_taken_team": "avg_corners",
            "fk_foul_lost_team": "avg_fouls",
            "total_yel_card_team": "avg_yellow_cards",
            "total_red_card_team": "avg_red_cards",
            "clean_sheet_team": "clean_sheets",
        }
        dto_fields = set(TeamSeasonStatRow.model_fields)
        # 对照表本身必须是活的:右边的字段名如果哪天从 DTO 里删了/改名了,
        # 这条清单就失效,必须有人重新核对,而不是继续挡着一个不存在的字段。
        assert set(known_overlaps.values()) <= dto_fields

        selected = {name for name, _ in q.FREE_TEAM_BOARDS}
        assert selected.isdisjoint(known_overlaps)

    def test_target_metric_is_present(self):
        """本次改造的直接目标:进攻三区赢得球权必须在榜里。"""
        labels = dict(q.FREE_TEAM_BOARDS)
        assert labels["poss_won_att_3rd_team"] == "前场反抢"

    def test_label_matches_player_side_wording(self):
        """同一个指标在球员榜和球队榜必须同名,不能一处'前场反抢'一处'前场夺回'。"""
        player_labels = dict(q.FREE_PLAYER_BOARDS)
        team_labels = dict(q.FREE_TEAM_BOARDS)
        assert player_labels["poss_won_att_3rd"] == team_labels["poss_won_att_3rd_team"]

    def test_no_duplicate_stat_names(self):
        names = [name for name, _ in q.FREE_TEAM_BOARDS]
        assert len(names) == len(set(names))


class TestBoardProjection:
    def test_boards_ride_along_team_stats_response(self, core_conn):
        data = q.team_season_stats(core_conn, 47, "2025/2026")
        by_name = {b["stat_name"]: b for b in data["boards"]}
        assert set(by_name) == {"poss_won_att_3rd_team", "big_chance_team"}

        poss = by_name["poss_won_att_3rd_team"]
        assert poss["label_zh"] == "前场反抢"
        assert poss["entries"][0]["value"] == 5.1
        assert poss["entries"][0]["rank"] == 1
        assert poss["entries"][0]["team"]["team_id"] == 1001
        # 单位从来源标题派生,不是写死的
        assert poss["per_match"] is True
        assert poss["stat_format"] == "fraction"
        assert poss["stat_decimals"] == 1

    def test_season_total_board_is_not_labelled_per_match(self, core_conn):
        data = q.team_season_stats(core_conn, 47, "2025/2026")
        big = next(b for b in data["boards"] if b["stat_name"] == "big_chance_team")
        assert big["per_match"] is False
        assert big["entries"][0]["value"] == 120.0

    def test_league_without_source_boards_returns_empty(self, core_conn):
        data = q.team_season_stats(core_conn, 87, "2025/2026")
        assert data["rows"], "布景里 87 是有 rows 的,这条测的是 boards 单独为空"
        assert data["boards"] == []

    def test_boards_follow_the_resolved_season_of_rows(self, core_conn):
        """不传 season 时,boards 必须跟 rows 用同一个解析结果,不能各解析各的。"""
        data = q.team_season_stats(core_conn, 47, None)
        assert data["season"] == "2025/2026"
        assert data["boards"], "解析出的赛季有榜单数据时不应为空"

    def test_missing_table_degrades_to_empty_not_500(self, core_conn):
        """没跑过 0016 的库(表不存在)必须整块降级,不能让联赛页崩掉。"""
        core_conn.execute("DROP TABLE fact_season_team_stats")
        data = q.team_season_stats(core_conn, 47, "2025/2026")
        assert data["boards"] == []
        assert data["rows"]

    def test_unparseable_extra_json_does_not_crash(self, core_conn):
        core_conn.execute(
            "UPDATE fact_season_team_stats SET extra_json='not json' "
            "WHERE stat_name='poss_won_att_3rd_team'"
        )
        data = q.team_season_stats(core_conn, 47, "2025/2026")
        poss = next(
            b for b in data["boards"] if b["stat_name"] == "poss_won_att_3rd_team"
        )
        # 元数据丢失时不猜单位:per_match 为 None,值本身照常下发
        assert poss["per_match"] is None
        assert poss["stat_title"] is None
        assert poss["entries"][0]["value"] == 5.1

    def test_extra_json_stays_valid_json_in_seed(self, core_conn):
        """布景本身必须是合法 JSON —— 否则上面几条断言测的是降级路径。"""
        raw = core_conn.execute(
            "SELECT extra_json FROM fact_season_team_stats LIMIT 1"
        ).fetchone()[0]
        assert isinstance(json.loads(raw), dict)
