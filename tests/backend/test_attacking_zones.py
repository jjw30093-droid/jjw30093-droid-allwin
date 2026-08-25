"""进攻区域采集/投影 + 球队体能转正(2026-08-25)。

覆盖:
1. parse_team_stats_records 把 content.attackingZones(content 顶层 key,
   整数百分比)按时段并进对应 Period 行;来源缺失时 key 整个不出现(不补 0,
   CLAUDE.md §6.2)。离线 fixture,不访问外部服务。
2. TEAM_STAT_KEYS 拆分(CORE/PHYSICAL/ZONES)的结构不变量:三份互不重叠、
   合并视图恰好等于并集;team_form 只用 CORE(低覆盖键不进赛前聚合)。
3. /matches/{id}/report 端到端:extra_json 里的体能/进攻区域键投影进
   MatchReportTeamStat 字段;缺失时如实为 None。
"""

import json

from backend.db.connections import connect_rw
from backend.fotmob_client import FotMobClient

from .coreseed import seed_basic_core, seed_match_report

ZONES_PAYLOAD = {
    "home": {
        "total": {"left": 33, "center": 29, "right": 38},
        "firstHalf": {"left": 40, "center": 25, "right": 35},
        "secondHalf": {"left": 26, "center": 33, "right": 41},
    },
    "away": {
        "total": {"left": 30, "center": 34, "right": 36},
        "firstHalf": {"left": 28, "center": 36, "right": 36},
        # secondHalf 故意缺失:该时段 key 必须整个不出现,不是三个 0
    },
}


def _pp(with_zones: bool) -> dict:
    content: dict = {
        "stats": {"Periods": {
            "All": {"stats": [{"stats": [{"key": "corners", "stats": [7, 3]}]}]},
            "FirstHalf": {"stats": [{"stats": [{"key": "corners", "stats": [4, 1]}]}]},
            "SecondHalf": {"stats": [{"stats": [{"key": "corners", "stats": [3, 2]}]}]},
        }},
    }
    if with_zones:
        content["attackingZones"] = ZONES_PAYLOAD
    return {
        "general": {"homeTeam": {"id": 1, "name": "H"},
                    "awayTeam": {"id": 2, "name": "A"}},
        "header": {"status": {"scoreStr": "2 - 0"}},
        "content": content,
    }


def _by_period_side(records):
    return {(r["Period"], r["Team_ID"]): r for r in records}


class TestParserAttackingZones:
    def test_zones_injected_per_period_and_side(self):
        recs = FotMobClient(proxy="").parse_team_stats_records(_pp(True), 1)
        idx = _by_period_side(recs)

        home_all = idx[("All", 1)]
        assert home_all["attacking_zone_left"] == 33
        assert home_all["attacking_zone_center"] == 29
        assert home_all["attacking_zone_right"] == 38

        away_first = idx[("FirstHalf", 2)]
        assert away_first["attacking_zone_left"] == 28
        assert away_first["attacking_zone_center"] == 36
        assert away_first["attacking_zone_right"] == 36

        # 主队下半场存在
        assert idx[("SecondHalf", 1)]["attacking_zone_right"] == 41
        # 客队 secondHalf 来源缺失:key 必须整个不出现,不是 0
        away_second = idx[("SecondHalf", 2)]
        for k in ("attacking_zone_left", "attacking_zone_center", "attacking_zone_right"):
            assert k not in away_second

    def test_absent_zones_leave_records_untouched(self):
        recs = FotMobClient(proxy="").parse_team_stats_records(_pp(False), 1)
        for r in recs:
            for k in r:
                assert not k.startswith("attacking_zone_")
        # 常规统计不受影响
        idx = _by_period_side(recs)
        assert idx[("All", 1)]["corners"] == 7.0


class TestWhitelistSplit:
    def test_merged_view_is_exact_union_and_disjoint(self):
        from backend.queries.match_report import (
            TEAM_STAT_KEYS,
            TEAM_STAT_KEYS_CORE,
            TEAM_STAT_KEYS_PHYSICAL,
            TEAM_STAT_KEYS_ZONES,
        )

        core, phys, zones = (
            set(TEAM_STAT_KEYS_CORE), set(TEAM_STAT_KEYS_PHYSICAL), set(TEAM_STAT_KEYS_ZONES),
        )
        assert core & phys == set()
        assert core & zones == set()
        assert phys & zones == set()
        assert set(TEAM_STAT_KEYS) == core | phys | zones
        assert phys == {
            "physical_metrics_distance_covered", "physical_metrics_running",
            "physical_metrics_sprinting", "physical_metrics_walking",
            "physical_metrics_number_of_sprints",
        }
        assert zones == {
            "attacking_zone_left", "attacking_zone_center", "attacking_zone_right",
        }

    def test_team_form_aggregates_core_only(self):
        """赛前近 N 场聚合刻意不含低覆盖键(体能仅英超少量场次、进攻区域为
        新采集)——算进去永远是 n=0 的空指标,纯噪声(match_report.py 拆分
        注释同一口径)。"""
        import backend.queries.team_form as tf
        from backend.queries.match_report import TEAM_STAT_KEYS_CORE

        assert tf.TEAM_STAT_KEYS is TEAM_STAT_KEYS_CORE

    def test_gate_g14_still_knows_promoted_keys(self):
        """转正后 8 键从 KNOWN_UNPROJECTED 移除,但 G14 的白名单 =
        TEAM_STAT_KEYS ∪ KNOWN_UNPROJECTED,合并视图必须仍然覆盖它们。"""
        from backend.known_values import TEAM_EXTRA_JSON_KNOWN_UNPROJECTED
        from backend.queries.match_report import TEAM_STAT_KEYS

        allowed = set(TEAM_STAT_KEYS) | set(TEAM_EXTRA_JSON_KNOWN_UNPROJECTED)
        for key in (
            "physical_metrics_distance_covered", "physical_metrics_running",
            "physical_metrics_sprinting", "physical_metrics_walking",
            "physical_metrics_number_of_sprints",
            "attacking_zone_left", "attacking_zone_center", "attacking_zone_right",
        ):
            assert key in allowed


class TestReportProjection:
    def test_report_surfaces_physical_and_zones(self, data_dir, client):
        seed_basic_core(data_dir)
        conn = connect_rw("core")
        seed_match_report(conn, match_id=9002)
        # 给主队 All 行追加体能 + 进攻区域(seed 的 extra_json 不含它们)
        extra = json.loads(conn.execute(
            "SELECT extra_json FROM fact_team_match_stats"
            " WHERE Match_ID=9002 AND Period='All' AND Team_ID=1001"
        ).fetchone()[0])
        extra.update({
            "physical_metrics_distance_covered": 106440,
            "physical_metrics_walking": 35523,
            "physical_metrics_running": 69251,
            "physical_metrics_sprinting": 1666,
            "physical_metrics_number_of_sprints": 78,
            "attacking_zone_left": 33,
            "attacking_zone_center": 29,
            "attacking_zone_right": 38,
        })
        conn.execute(
            "UPDATE fact_team_match_stats SET extra_json=?"
            " WHERE Match_ID=9002 AND Period='All' AND Team_ID=1001",
            (json.dumps(extra),),
        )
        conn.commit()
        conn.close()

        body = client.get("/api/v1/matches/9002/report").json()
        home = next(t for t in body["team_stats"] if t["is_home"])
        away = next(t for t in body["team_stats"] if not t["is_home"])

        assert home["physical_metrics_distance_covered"] == 106440.0
        assert home["physical_metrics_number_of_sprints"] == 78.0
        assert home["attacking_zone_left"] == 33.0
        assert home["attacking_zone_center"] == 29.0
        assert home["attacking_zone_right"] == 38.0
        # 客队来源缺失:如实 None,不补 0
        assert away["physical_metrics_distance_covered"] is None
        assert away["attacking_zone_left"] is None
