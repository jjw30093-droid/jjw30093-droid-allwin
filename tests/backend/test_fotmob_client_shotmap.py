"""backend/fotmob_client.py::parse_shotmap_records 测试。

2026-08-23 对照 FotMob 官方安卓包核实:原始 payload 每脚射门带 29-30 个
字段,此前解析器只取 11 个,丢弃了 isBlocked/isOnTarget/isFromInsideBox/
id/minAdded/keeperId——这些字段本地在 fact_match_events.extra_json.
shotmapEvent(进球事件里 FotMob 原样内嵌的射门对象)里被实测证实真实存在,
不是猜测。本测试用同构的原始字段名(playerId/teamId/min/period/x/y/
expectedGoals/expectedGoalsOnTarget/situation/eventType/shotType/id/
isBlocked/isOnTarget/isFromInsideBox/minAdded/keeperId)构造 fixture,
覆盖字段名到列名的映射,不依赖真实网络请求。

2026-08-24 同一手法补齐画射门轨迹线用的 7 个字段(blockedX/blockedY/
goalCrossedY/goalCrossedZ/onGoalShot.{x,y,zoomRatio}),同样已通过本地
fact_match_events.extra_json.shotmapEvent 实测证实存在。唯一未被本地数据
验证过的风险点:真正被封堵(isBlocked=True)的射门里 blockedX/blockedY
是否确实非空——本地样本 isBlocked 均为 False,见
test_blocked_shot_missing_blocked_coords_parses_to_none_not_crash。
"""

from __future__ import annotations

from backend.fotmob_client import FotMobClient


def _page_props(shots: list[dict]) -> dict:
    return {"content": {"shotmap": {"shots": shots}}}


class TestParseShotmapRecords:
    def test_extracts_all_26_columns_from_raw_shot(self):
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
            "isBlocked": True,
            "isOnTarget": True,
            "isFromInsideBox": True,
            "keeperId": 215168,
            "blockedX": 81.13,
            "blockedY": 33.16,
            "goalCrossedY": 32.55,
            "goalCrossedZ": 1.22,
            "onGoalShot": {"x": 0.75, "y": 0.4, "zoomRatio": 0.9},
            "isOwnGoal": False,
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
        assert r["Is_Blocked"] is True
        assert r["Is_On_Target"] is True
        assert r["Is_From_Inside_Box"] is True
        assert r["Minute_Added"] == 3
        assert r["Keeper_ID"] == 215168
        # 2026-08-24 新增的 7 个轨迹字段。
        assert r["Blocked_X"] == 81.13
        assert r["Blocked_Y"] == 33.16
        assert r["Goal_Crossed_Y"] == 32.55
        assert r["Goal_Crossed_Z"] == 1.22
        assert r["On_Goal_Shot_X"] == 0.75
        assert r["On_Goal_Shot_Y"] == 0.4
        assert r["On_Goal_Shot_Zoom_Ratio"] == 0.9
        # 2026-08-24 新增乌龙球标志(migrations/core/0010)。
        assert r["Is_Own_Goal"] is False

    def test_own_goal_flag_true_and_missing(self):
        """isOwnGoal=True 原样透传;键缺失时为 None 不为 False——"来源没给"
        和"来源明确说不是乌龙"必须可区分,query 层只对 None 走推断兜底。"""
        client = FotMobClient()
        own_goal = {"eventType": "Goal", "isOwnGoal": True}
        missing = {"eventType": "Goal"}
        records = client.parse_shotmap_records(_page_props([own_goal, missing]), match_id=1)
        assert records[0]["Is_Own_Goal"] is True
        assert records[1]["Is_Own_Goal"] is None

    def test_blocked_shot_missing_blocked_coords_parses_to_none_not_crash(self):
        """本地样本(Is_Blocked 均为 False)未能验证真正被封堵的射门是否
        必然带非空 blockedX/blockedY——这条覆盖"isBlocked=True 但坐标缺失"
        的场景,必须干净解析成 None,不崩溃、不编造坐标。"""
        client = FotMobClient()
        shot = {"eventType": "AttemptSaved", "isBlocked": True, "blockedX": None, "blockedY": None}
        records = client.parse_shotmap_records(_page_props([shot]), match_id=1)
        assert records[0]["Is_Blocked"] is True
        assert records[0]["Blocked_X"] is None
        assert records[0]["Blocked_Y"] is None

    def test_on_goal_shot_key_entirely_missing_not_crash(self):
        """onGoalShot 键整体缺失(不是空字典,是完全没有这个键)——
        (s.get('onGoalShot') or {}).get(...) 必须安全退化,不抛异常。"""
        client = FotMobClient()
        shot = {"eventType": "Goal"}  # 没有 onGoalShot 键
        records = client.parse_shotmap_records(_page_props([shot]), match_id=1)
        assert records[0]["On_Goal_Shot_X"] is None
        assert records[0]["On_Goal_Shot_Y"] is None
        assert records[0]["On_Goal_Shot_Zoom_Ratio"] is None

    def test_on_goal_shot_key_present_but_null_not_crash(self):
        """onGoalShot 键存在但值是 None(而非缺失/字典)——同样必须安全
        退化,这是 `s.get(...) or {}` 而非 `s.get(..., {})` 的原因。"""
        client = FotMobClient()
        shot = {"eventType": "Goal", "onGoalShot": None}
        records = client.parse_shotmap_records(_page_props([shot]), match_id=1)
        assert records[0]["On_Goal_Shot_X"] is None

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
        assert r["Blocked_X"] is None
        assert r["Blocked_Y"] is None
        assert r["Goal_Crossed_Y"] is None
        assert r["Goal_Crossed_Z"] is None
        assert r["On_Goal_Shot_X"] is None
        assert r["On_Goal_Shot_Y"] is None
        assert r["On_Goal_Shot_Zoom_Ratio"] is None

    def test_empty_shotmap_returns_empty_list(self):
        client = FotMobClient()
        assert client.parse_shotmap_records(_page_props([]), match_id=1) == []


class TestParseMomentumRecords:
    """2026-08-23 对照 FotMob 官方安卓包核实:content.momentum.main.data
    (逐分钟"势头"曲线)此前从未解析。fixture 结构照抄真实比赛(5107575)
    实测到的原始 payload 形状。"""

    def test_extracts_minute_value_pairs(self):
        page_props = {
            "content": {
                "momentum": {
                    "main": {
                        "data": [
                            {"minute": 0, "value": 0},
                            {"minute": 1, "value": 5},
                            {"minute": 45.5, "value": -62},
                        ]
                    },
                    "alternateModels": [],
                }
            }
        }
        client = FotMobClient()
        records = client.parse_momentum_records(page_props, match_id=5107575)
        assert records == [
            {"Match_ID": 5107575, "Minute": 0, "Value": 0},
            {"Match_ID": 5107575, "Minute": 1, "Value": 5},
            {"Match_ID": 5107575, "Minute": 45.5, "Value": -62},
        ]

    def test_missing_momentum_key_returns_empty_list(self):
        """赛前/未完赛比赛通常没有这个字段(或为 False),不是异常。"""
        client = FotMobClient()
        assert client.parse_momentum_records({"content": {}}, match_id=1) == []

    def test_momentum_false_returns_empty_list(self):
        """真实赛前 fixture(prematch-5104961.json)里 content.momentum 是
        字面量 False,不是 dict——必须能安全处理,不能当 dict 解引用崩溃。"""
        client = FotMobClient()
        assert client.parse_momentum_records({"content": {"momentum": False}}, match_id=1) == []

    def test_malformed_points_are_skipped_not_crashed(self):
        page_props = {
            "content": {
                "momentum": {
                    "main": {
                        "data": [
                            {"minute": 1, "value": 5},
                            {"minute": None, "value": 5},
                            {"minute": 2, "value": None},
                            "not-a-dict",
                        ]
                    }
                }
            }
        }
        client = FotMobClient()
        records = client.parse_momentum_records(page_props, match_id=1)
        assert records == [{"Match_ID": 1, "Minute": 1, "Value": 5}]
