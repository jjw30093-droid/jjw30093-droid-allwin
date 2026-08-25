"""G12/G13/G14 质量门 + migration 0012 数据卫生(2026-08-25)。

覆盖计划里点名的每一条:
- G12:新增赛季漂移(超出基线)CRITICAL、正常数据 OK、制度表缺失 skipped;
- G13:登记外枚举值 WARNING、正常数据 OK;
- G14:extra_json 白名单外新键 WARNING、正常数据 OK;
- 0012:唯一索引真的在约束(灌重复行必须失败)、加时段两表可 join(此前
  恒零行)、时间戳统一后 MAX(updated_at) 语义正确。
"""

import json
import sqlite3

import pytest

from backend.cli import pipeline_gates as pg
from backend.db.connections import connect_rw
from tests.backend.coreseed import insert_match, seed_core_schema

NOW = "2026-08-25T12:00:00Z"


@pytest.fixture
def core(data_dir):
    conn = connect_rw("core")
    seed_core_schema(conn)
    yield conn
    conn.close()


class TestG12SeasonLabelDrift:
    def test_clean_data_ok(self, core):
        insert_match(core, 1, league_id=47, date="2026-08-22", status="Finish")
        core.commit()
        g = pg._gate_season_label_drift(core)
        assert g["level"] == "OK"
        assert g["season_drift"] == 0 and g["new_drift"] == 0

    def test_new_drift_beyond_baseline_critical(self, core, monkeypatch):
        # 基线设 0 模拟"存量已清零"的稳态;造一行漂移(绕过触发器,模拟
        # 触发器被绕过/未部署的场景——这正是本门存在的意义)
        monkeypatch.setattr(pg, "G12_BASELINE_SEASON_DRIFT", 0)
        core.execute("DROP TRIGGER IF EXISTS trg_dim_match_season_insert")
        core.execute(
            "INSERT INTO dim_match (Match_ID, Season, League_ID, Date, status)"
            " VALUES (2, '2019/2020', 47, '2026-08-22', 'Finish')"
        )
        core.commit()
        g = pg._gate_season_label_drift(core)
        assert g["level"] == "CRITICAL"
        assert g["new_drift"] == 1

    def test_drift_within_baseline_is_ok(self, core, monkeypatch):
        monkeypatch.setattr(pg, "G12_BASELINE_SEASON_DRIFT", 5)
        core.execute("DROP TRIGGER IF EXISTS trg_dim_match_season_insert")
        core.execute(
            "INSERT INTO dim_match (Match_ID, Season, League_ID, Date, status)"
            " VALUES (3, '2019/2020', 47, '2026-08-22', 'Finish')"
        )
        core.commit()
        g = pg._gate_season_label_drift(core)
        assert g["level"] == "OK" and g["season_drift"] == 1

    def test_regime_table_missing_skipped(self, core):
        core.execute("DROP TRIGGER IF EXISTS trg_dim_match_season_insert")
        core.execute("DROP TRIGGER IF EXISTS trg_dim_match_season_update")
        core.execute("DROP TABLE dim_league_season_regime")
        core.commit()
        g = pg._gate_season_label_drift(core)
        assert g["level"] == "OK"
        assert g["detail"] == "skipped_regime_table_missing"


class TestG13UnknownEnumValue:
    def test_clean_data_ok(self, core):
        insert_match(core, 1, league_id=47, date="2026-08-22", status="Finish")
        core.execute(
            "INSERT INTO fact_shotmap (Match_ID, Player_ID, Team_ID, Minute, Period,"
            " X_Coord, Y_Coord, Situation, Outcome, Shot_Type)"
            " VALUES (1, 'p1', 1001, 10, 'FirstHalf', 90, 40, 'RegularPlay', 'Goal', 'RightFoot')"
        )
        core.commit()
        g = pg._gate_unknown_enum_value(core)
        assert g["level"] == "OK" and g["unknown"] == {}

    def test_unknown_value_warns_and_names_column(self, core):
        insert_match(core, 1, league_id=47, date="2026-08-22", status="Finish")
        core.execute(
            "INSERT INTO fact_shotmap (Match_ID, Player_ID, Team_ID, Minute, Period,"
            " X_Coord, Y_Coord, Situation, Outcome, Shot_Type)"
            " VALUES (1, 'p1', 1001, 10, 'FirstHalf', 90, 40, 'BrandNewSituation', 'Goal', 'RightFoot')"
        )
        core.commit()
        g = pg._gate_unknown_enum_value(core)
        assert g["level"] == "WARNING"
        assert g["unknown"]["fact_shotmap.Situation"] == ["BrandNewSituation"]

    def test_group_composite_table_type_is_known(self, core):
        core.execute(
            "INSERT INTO fact_league_table (League_ID, Season, table_type, Team_ID)"
            " VALUES (9080, '2026', 'all:K-League 1 Final Group A', 1)"
        )
        core.commit()
        g = pg._gate_unknown_enum_value(core)
        assert "fact_league_table.table_type" not in g["unknown"]


class TestG14ExtraJsonUnknownKey:
    def _team_stats(self, core, mid, extra):
        core.execute(
            "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
            " VALUES (?, 1001, 'All', 0, ?)", (mid, json.dumps(extra)),
        )

    def test_whitelisted_and_known_unprojected_ok(self, core):
        insert_match(core, 1, league_id=47, date="2026-08-22", status="Finish")
        self._team_stats(core, 1, {
            "BallPossesion": 60.0,                      # 已投影(CORE)
            "physical_metrics_distance_covered": 1.0,   # 已投影(2026-08-25 体能转正)
            "physical_metrics": None,                   # 已知未投影(分组表头伪键)
        })
        core.commit()
        g = pg._gate_extra_json_unknown_key(core, NOW)
        assert g["level"] == "OK" and g["unknown_count"] == 0

    def test_new_key_warns(self, core):
        insert_match(core, 1, league_id=47, date="2026-08-22", status="Finish")
        self._team_stats(core, 1, {"brand_new_source_key": 1.0})
        core.commit()
        g = pg._gate_extra_json_unknown_key(core, NOW)
        assert g["level"] == "WARNING"
        assert g["unknown_keys"] == ["brand_new_source_key"]

    def test_old_matches_outside_window_ignored(self, core):
        insert_match(core, 1, league_id=47, date="2020-09-12", status="Finish",
                     season="2020/2021")
        self._team_stats(core, 1, {"ancient_key": 1.0})
        core.commit()
        g = pg._gate_extra_json_unknown_key(core, NOW)
        assert g["level"] == "OK"


class TestMigration0012Hygiene:
    def test_unique_index_actually_enforces(self, core):
        """计划点名:灌入重复自然键必须失败——证明索引真的在约束。"""
        self_insert = (
            "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals)"
            " VALUES (1, 1001, 'All', 0)"
        )
        core.execute(self_insert)
        with pytest.raises(sqlite3.IntegrityError):
            core.execute(self_insert)
        core.rollback()

    def test_extra_time_periods_join_across_tables(self, core):
        """此前 shotmap 用 FirstHalfExtra、team_stats 用 FirstExtraHalf,
        加时段 join 恒零行;0012 统一拼写 + 写侧归一后必须能 join 上。"""
        insert_match(core, 1, league_id=47, date="2026-08-22", status="Finish")
        core.execute(
            "INSERT INTO fact_shotmap (Match_ID, Player_ID, Team_ID, Minute, Period,"
            " X_Coord, Y_Coord, Situation, Outcome, Shot_Type)"
            " VALUES (1, 'p1', 1001, 100, 'FirstHalfExtra', 90, 40, 'RegularPlay', 'Goal', 'RightFoot')"
        )
        core.execute(
            "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals)"
            " VALUES (1, 1001, 'FirstHalfExtra', 1)"
        )
        core.commit()
        n = core.execute(
            "SELECT COUNT(*) FROM fact_shotmap s"
            " JOIN fact_team_match_stats t"
            "   ON t.Match_ID = s.Match_ID AND t.Period = s.Period"
        ).fetchone()[0]
        assert n == 1

    def test_parser_normalizes_source_period_spelling(self):
        from backend.fotmob_client import FotMobClient

        pp = {
            "general": {"homeTeam": {"id": 1, "name": "H"},
                        "awayTeam": {"id": 2, "name": "A"}},
            "header": {"status": {}},
            "content": {"stats": {"Periods": {
                "FirstExtraHalf": {"stats": [
                    {"stats": [{"key": "corners", "stats": [1, 2]}]}
                ]},
            }}},
        }
        recs = FotMobClient(proxy="").parse_team_stats_records(pp, 1)
        assert {r["Period"] for r in recs} == {"FirstHalfExtra"}

    def test_timestamp_hygiene_after_migration(self, core):
        """0012 把 19 字符无时区行补全为 ISO Z;MAX() 语义随之正确。
        在触发器/迁移已应用的库上模拟迁移前混排数据,重放 0012 的 UPDATE。"""
        # 同一天内混排才暴露缺陷:日期前缀相同时,比较落在第 10 位的分隔符,
        # 'T'(0x54) > ' '(0x20) → 更早的 ISO 行反而赢得 MAX
        core.execute(
            "INSERT INTO dim_team_i18n (Team_ID, name_en, name_zh, source, updated_at)"
            " VALUES (1, 'A', '甲', 't', '2026-08-20 23:00:00')"   # 旧格式,时间更晚
        )
        core.execute(
            "INSERT INTO dim_team_i18n (Team_ID, name_en, name_zh, source, updated_at)"
            " VALUES (2, 'B', '乙', 't', '2026-08-20T01:00:00Z')"  # ISO Z,时间更早
        )
        wrong = core.execute("SELECT MAX(updated_at) FROM dim_team_i18n").fetchone()[0]
        assert wrong == "2026-08-20T01:00:00Z"   # 语义错误:更早的行赢了
        core.execute(
            "UPDATE dim_team_i18n SET updated_at = replace(updated_at, ' ', 'T') || 'Z'"
            " WHERE length(updated_at) = 19"
        )
        fixed = core.execute("SELECT MAX(updated_at) FROM dim_team_i18n").fetchone()[0]
        assert fixed == "2026-08-20T23:00:00Z"
        core.rollback()
