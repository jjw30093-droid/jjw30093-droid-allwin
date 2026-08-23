"""backend/fotmob_client.py::parse_shotmap_records 测试。

2026-08-23 对照 FotMob 官方安卓包核实:原始 payload 每脚射门带 29-30 个
字段,此前解析器只取 11 个,丢弃了 isBlocked/isOnTarget/isFromInsideBox/
id/minAdded/keeperId——这些字段本地在 fact_match_events.extra_json.
shotmapEvent(进球事件里 FotMob 原样内嵌的射门对象)里被实测证实真实存在,
不是猜测。本测试用同构的原始字段名(playerId/teamId/min/period/x/y/
expectedGoals/expectedGoalsOnTarget/situation/eventType/shotType/id/
isBlocked/isOnTarget/isFromInsideBox/minAdded/keeperId)构造 fixture,
覆盖字段名到列名的映射,不依赖真实网络请求。
"""

from __future__ import annotations

from backend.fotmob_client import FotMobClient


def _page_props(shots: list[dict]) -> dict:
    return {"content": {"shotmap": {"shots": shots}}}


class TestParseShotmapRecords:
    def test_extracts_all_18_columns_from_raw_shot(self):
        raw_shot = {
            "playerId": "12345",
            "teamId": 10205,
            "min": 90,
            "minAdded": 3,
            "period": "SecondHalf",
            "x": 95.2,
            "y": 34.1,
            "expectedGoals": 0.42,
            "expectedGoalsOnTarget": 0.61,
            "situation": "RegularPlay",
            "eventType": "AttemptSaved",
            "shotType": "RightFoot",
            "id": 2835583501,
            "isBlocked": False,
            "isOnTarget": True,
            "isFromInsideBox": True,
            "keeperId": 215168,
        }
        client = FotMobClient()
        records = client.parse_shotmap_records(_page_props([raw_shot]), match_id=5868022)
        assert len(records) == 1
        r = records[0]
        assert r["Match_ID"] == 5868022
        assert r["Player_ID"] == "12345"
        assert r["Team_ID"] == 10205
        assert r["Minute"] == 90
        assert r["Period"] == "SecondHalf"
        assert r["X_Coord"] == 95.2
        assert r["Y_Coord"] == 34.1
        assert r["xG"] == 0.42
        assert r["xGOT"] == 0.61
        assert r["Situation"] == "RegularPlay"
        assert r["Outcome"] == "AttemptSaved"
        assert r["Shot_Type"] == "RightFoot"
        # 2026-08-23 新增的 6 个字段——此前这些键存在于原始 payload 但从未
        # 被读取,fact_shotmap 对应列此前恒为 NULL。
        assert r["Shot_ID"] == 2835583501
        assert r["Is_Blocked"] is False
        assert r["Is_On_Target"] is True
        assert r["Is_From_Inside_Box"] is True
        assert r["Minute_Added"] == 3
        assert r["Keeper_ID"] == 215168

    def test_blocked_shot_flag_distinguishes_from_keeper_save(self):
        """AttemptSaved 单独看无法区分"门将扑出"和"被后卫封堵"——这正是
        射正口径虚高(库内实测 7.725 vs 官方 4.356/队场)的根因。isBlocked
        补上后,查询层才能精确拆分,不用再靠 xGOT IS NULL 这种约 86-90%
        准的代理猜测。"""
        client = FotMobClient()
        keeper_save = {"eventType": "AttemptSaved", "isBlocked": False, "isOnTarget": True}
        defender_block = {"eventType": "AttemptSaved", "isBlocked": True, "isOnTarget": False}
        records = client.parse_shotmap_records(_page_props([keeper_save, defender_block]), match_id=1)
        assert records[0]["Is_Blocked"] is False
        assert records[0]["Is_On_Target"] is True
        assert records[1]["Is_Blocked"] is True
        assert records[1]["Is_On_Target"] is False

    def test_missing_new_fields_default_to_none_not_crash(self):
        """旧场次/字段确实缺失时(不是这次要修的丢弃,是数据源本身没给),
        新增字段必须诚实为 None,不能抛异常,也不能编造 0/False。"""
        client = FotMobClient()
        minimal_shot = {"playerId": "1", "teamId": 1, "eventType": "Miss"}
        records = client.parse_shotmap_records(_page_props([minimal_shot]), match_id=1)
        r = records[0]
        assert r["Shot_ID"] is None
        assert r["Is_Blocked"] is None
        assert r["Is_On_Target"] is None
        assert r["Is_From_Inside_Box"] is None
        assert r["Minute_Added"] is None
        assert r["Keeper_ID"] is None

    def test_empty_shotmap_returns_empty_list(self):
        client = FotMobClient()
        assert client.parse_shotmap_records(_page_props([]), match_id=1) == []
