"""backend/fotmob_client.py::parse_player_stats_records 测试(新增字段)。

2026-08-23:_build_stat_lookup() 补上 total 捕获(accurate_passes 的分母),
体能字段从驼峰猜测改成实测确认的真实 key,并新增 jogging。fixture 结构
照抄真实比赛(5795369 英超、5205834 欧冠决赛)实测到的原始 payload 形状。
"""

from __future__ import annotations

from backend.fotmob_client import FotMobClient


def _new_format_payload(player_id: str, stats_sections: list) -> dict:
    return {
        "content": {
            "playerStats": {
                player_id: {
                    "id": player_id,
                    "name": {"fullName": "Test Player"},
                    "isGoalkeeper": False,
                    "teamId": 1001,
                    "minutesPlayed": 90,
                    "stats": stats_sections,
                }
            }
        }
    }


class TestAccuratePassesTotal:
    def test_extracts_value_and_total_from_fraction_stat(self):
        """真实结构(5795369 实测):
        {"key":"accurate_passes","stat":{"value":37,"total":40,"type":"fractionWithPercentage"}}
        """
        payload = _new_format_payload("1", [
            {
                "title": "Top stats",
                "stats": {
                    "Accurate passes": {
                        "key": "accurate_passes",
                        "stat": {"value": 37, "total": 40, "type": "fractionWithPercentage"},
                    },
                },
            },
        ])
        client = FotMobClient()
        records = client.parse_player_stats_records(payload, match_id=1)
        assert len(records) == 1
        assert records[0]["accurate_passes"] == 37
        assert records[0]["accurate_passes_total"] == 40

    def test_total_absent_when_stat_has_no_total(self):
        payload = _new_format_payload("1", [
            {
                "title": "Top stats",
                "stats": {
                    "Goals": {"key": "goals", "stat": {"value": 1, "type": "integer"}},
                },
            },
        ])
        client = FotMobClient()
        records = client.parse_player_stats_records(payload, match_id=1)
        assert records[0]["accurate_passes_total"] is None


class TestPhysicalMetrics:
    def test_extracts_real_keys_not_camel_case_guesses(self):
        """真实结构(欧冠决赛 5205834 实测):Physical metrics 组用下划线 key,
        不是驼峰。此前解析器查 topSpeed/distanceCovered 等驼峰,永远查不中——
        这条测试锁定修复后的行为。"""
        payload = _new_format_payload("1", [
            {
                "title": "Physical metrics",
                "stats": {
                    "Top speed": {"key": "physical_metrics_topspeed", "stat": {"value": 29.9, "type": "speed"}},
                    "Distance covered": {"key": "physical_metrics_distance_covered", "stat": {"value": 10442, "type": "distance"}},
                    "Walking": {"key": "physical_metrics_walking", "stat": {"value": 4210, "total": 10442, "type": "distanceWithPercentage"}},
                    "Jogging": {"key": "physical_metrics_jogging", "stat": {"value": 4683, "total": 10442, "type": "distanceWithPercentage"}},
                    "Running": {"key": "physical_metrics_running", "stat": {"value": 1450, "total": 10442, "type": "distanceWithPercentage"}},
                    "Sprinting": {"key": "physical_metrics_sprinting", "stat": {"value": 99, "total": 10442, "type": "distanceWithPercentage"}},
                    "Number of sprints": {"key": "physical_metrics_number_of_sprints", "stat": {"value": 5, "type": "integer"}},
                },
            },
        ])
        client = FotMobClient()
        records = client.parse_player_stats_records(payload, match_id=1)
        r = records[0]
        assert r["physical_metrics_topspeed"] == 29.9
        assert r["physical_metrics_distance_covered"] == 10442
        assert r["physical_metrics_walking"] == 4210
        assert r["physical_metrics_jogging"] == 4683
        assert r["physical_metrics_running"] == 1450
        assert r["physical_metrics_sprinting"] == 99
        assert r["physical_metrics_number_of_sprints"] == 5

    def test_missing_physical_group_gives_none_not_zero(self):
        """大多数联赛没有这个组(实测:13/14 已接入联赛 0 覆盖)——必须诚实
        给 None,不能编造 0(那会让"没跑动数据"看起来像"跑动距离是 0 米")。"""
        payload = _new_format_payload("1", [
            {"title": "Top stats", "stats": {"Goals": {"key": "goals", "stat": {"value": 0, "type": "integer"}}}},
        ])
        client = FotMobClient()
        records = client.parse_player_stats_records(payload, match_id=1)
        r = records[0]
        for key in (
            "physical_metrics_topspeed", "physical_metrics_distance_covered",
            "physical_metrics_walking", "physical_metrics_jogging",
            "physical_metrics_running", "physical_metrics_sprinting",
            "physical_metrics_number_of_sprints",
        ):
            assert r[key] is None
