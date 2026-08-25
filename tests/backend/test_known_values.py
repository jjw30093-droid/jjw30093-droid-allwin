"""backend/known_values.py 封闭词表登记(2026-08-25,Part 2 P1)。

登记表与写侧常量/迁移后的真实拼写交叉钉住——本模块存在的意义就是"单一
出处",出处之间漂移必须在 CI 就红。
"""

from backend import known_values as kv


class TestCrossSourceConsistency:
    def test_status_matches_fotmob_client_write_side_constants(self):
        from backend.fotmob_client import (
            STATUS_CANCELLED,
            STATUS_FINISH,
            STATUS_IN_PLAY,
            STATUS_NOT_STARTED,
        )

        assert kv.DIM_MATCH_STATUS == {
            STATUS_FINISH, STATUS_NOT_STARTED, STATUS_IN_PLAY, STATUS_CANCELLED
        }

    def test_team_stats_period_uses_unified_spelling(self):
        # 0012 迁移 + parse_team_stats_records 归一之后,唯一合法拼写是
        # APK MatchPeriod 系(FirstHalfExtra);旧拼写不得再进登记表
        assert "FirstHalfExtra" in kv.TEAM_STATS_PERIOD
        assert "FirstExtraHalf" not in kv.TEAM_STATS_PERIOD

    def test_extra_json_known_unprojected_disjoint_from_projected(self):
        from backend.queries.match_report import TEAM_STAT_KEYS

        overlap = set(kv.TEAM_EXTRA_JSON_KNOWN_UNPROJECTED) & set(TEAM_STAT_KEYS)
        assert overlap == set(), (
            f"键已投影就该从 KNOWN_UNPROJECTED 移除(转正流程): {overlap}"
        )


class TestTableType:
    def test_base_and_group_composites_known(self):
        assert kv.table_type_is_known("all")
        assert kv.table_type_is_known("xg")
        # 分组赛制的复合形式是真实结构,不是脏数据(生产 16 种)
        assert kv.table_type_is_known("all:K-League 1 Final Group A")
        assert kv.table_type_is_known("form:100 Year Vision League East")

    def test_unknown_base_rejected(self):
        assert not kv.table_type_is_known("overall")
        assert not kv.table_type_is_known("xg2:whatever")
