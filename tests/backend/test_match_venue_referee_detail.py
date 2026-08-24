"""场地明细/天气枚举/裁判信息卡 + 乌龙球归属 + 最高评分(2026-08-24,
migrations/core/0010,比赛详情页对齐 FotMob)。

覆盖四层:
- FotMobClient.parse_match_dim 新字段解析(真实 fixture + 节点缺失退化);
- match_by_id() 的 referee_stats JSON 投影(camelCase→snake_case、坏 JSON 容错);
- GET /api/v1/matches/{id} 端到端透出;
- match_report() 的 is_own_goal(采集值优先/推断兜底/inferred 标志)与
  top_rated(全场最高评分、无评分为 None)。
"""

import json
import os

import pytest
from fastapi.testclient import TestClient

from backend.db.connections import connect_rw
from backend.fotmob_client import FotMobClient
from backend.queries.matches import match_by_id
from backend.queries.match_report import match_report

from .coreseed import seed_basic_core, seed_match_report

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_PATH = os.path.join(
    REPO_ROOT, "tests", "fixtures", "fotmob", "prematch-5104961.json"
)


def _load_fixture() -> dict:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


class TestParseMatchDimVenueRefereeDetail:
    def test_real_payload_extracts_stadium_detail_and_weather_enum(self):
        row = FotMobClient(proxy="").parse_match_dim(
            _load_fixture(), match_id=5104961, league_id=59, season="2026"
        )
        assert row["Venue_Capacity"] == 12565
        assert row["Venue_Surface"] == "artificial turf"
        assert row["Venue_Lat"] == pytest.approx(59.21306014)
        assert row["Venue_Long"] == pytest.approx(10.928134918)
        assert row["Weather_Localized_Key"] == "weather_condition_windy"
        assert row["Weather_Icon_Code"] == 24
        # 该 fixture 的 Referee 没有 id/stats(挪超),country 有值——各自独立。
        assert row["Referee_ID"] is None
        assert row["Referee_Country"] == "Norway"
        assert row["Referee_Country_Code"] == "NOR"
        assert row["Referee_Stats_Json"] is None

    def test_referee_with_stats_lands_as_verbatim_json(self):
        stats = [
            {"type": "yellowCards", "value": 4.11, "valueType": "perMatch",
             "average": 4.49, "total": 156, "averageType": "below",
             "fillPercentage": 37.07, "averagePercentage": 50},
        ]
        payload = {"general": {}, "header": {}, "content": {"matchFacts": {"infoBox": {
            "Referee": {"text": "R", "id": 7, "stats": stats},
        }}}}
        row = FotMobClient(proxy="").parse_match_dim(payload, match_id=1)
        assert row["Referee_ID"] == 7
        assert json.loads(row["Referee_Stats_Json"]) == stats

    def test_missing_nodes_all_none_not_crash(self):
        row = FotMobClient(proxy="").parse_match_dim(
            {"general": {}, "header": {}, "content": {}}, match_id=1
        )
        for col in ("Venue_Capacity", "Venue_Surface", "Venue_Lat", "Venue_Long",
                    "Weather_Localized_Key", "Weather_Icon_Code", "Referee_ID",
                    "Referee_Country", "Referee_Country_Code", "Referee_Stats_Json"):
            assert row[col] is None, col


@pytest.fixture
def core_conn(data_dir):
    conn = connect_rw("core")
    yield conn
    conn.close()


_STATS_JSON = json.dumps([
    {"type": "matches", "value": 38, "valueType": "total"},
    {"type": "yellowCards", "value": 4.11, "valueType": "perMatch",
     "average": 4.49, "total": 156, "averageType": "below",
     "fillPercentage": 37.07, "averagePercentage": 50},
])


def _seed_detail(conn, match_id=9001):
    conn.execute(
        "UPDATE dim_match SET Venue_Capacity=23576, Venue_Surface='grass',"
        " Venue_Lat=42.796676994, Venue_Long=-1.637141258,"
        " Weather_Localized_Key='weather_condition_partly_cloudy',"
        " Weather_Icon_Code=30, Referee_ID=1001072330, Referee_Country='Spain',"
        " Referee_Country_Code='ESP', Referee_Stats_Json=? WHERE Match_ID=?",
        (_STATS_JSON, match_id),
    )
    conn.commit()


class TestMatchByIdVenueRefereeDetail:
    def test_populated_fields_project(self, data_dir, core_conn):
        seed_basic_core(data_dir)
        _seed_detail(core_conn)

        m = match_by_id(core_conn, 9001)
        assert m["venue_capacity"] == 23576
        assert m["venue_surface"] == "grass"
        assert m["venue_lat"] == pytest.approx(42.796676994)
        assert m["venue_long"] == pytest.approx(-1.637141258)
        assert m["weather_localized_key"] == "weather_condition_partly_cloudy"
        assert m["referee_id"] == 1001072330
        # camelCase→snake_case 投影;total 项没有均值扩展字段,如实 None。
        assert m["referee_stats"][0] == {
            "type": "matches", "value": 38, "value_type": "total",
            "average": None, "total": None, "average_type": None,
            "fill_percentage": None, "average_percentage": None,
        }
        assert m["referee_stats"][1]["average_type"] == "below"
        assert m["referee_stats"][1]["fill_percentage"] == 37.07

    def test_missing_fields_none_and_empty_list(self, data_dir, core_conn):
        seed_basic_core(data_dir)  # 不 seed detail
        m = match_by_id(core_conn, 9001)
        assert m["venue_capacity"] is None
        assert m["referee_id"] is None
        assert m["referee_stats"] == []

    def test_corrupt_stats_json_degrades_to_empty_list(self, data_dir, core_conn):
        seed_basic_core(data_dir)
        core_conn.execute(
            "UPDATE dim_match SET Referee_Stats_Json='not json{' WHERE Match_ID=9001"
        )
        core_conn.commit()
        assert match_by_id(core_conn, 9001)["referee_stats"] == []


class TestMatchDetailApiExposesVenueRefereeDetail:
    def test_detail_endpoint_includes_new_fields(self, app, data_dir):
        seed_basic_core(data_dir)
        conn = connect_rw("core")
        try:
            _seed_detail(conn)
        finally:
            conn.close()

        r = TestClient(app).get("/api/v1/matches/9001")
        assert r.status_code == 200
        m = r.json()["match"]
        assert m["venue_capacity"] == 23576
        assert m["venue_surface"] == "grass"
        assert m["referee_id"] == 1001072330
        assert m["referee_stats"][1]["average_type"] == "below"


class TestOwnGoalProjection:
    def test_inferred_own_goal_from_null_xg_goal(self, data_dir, core_conn):
        """历史行(无采集值):xG NULL + Goal → is_own_goal=True 且
        inferred=True——推断值与采集值必须可区分(CLAUDE.md §2.2)。"""
        seed_basic_core(data_dir)
        seed_match_report(core_conn, match_id=9002)
        core_conn.execute(
            "UPDATE fact_shotmap SET xG=NULL, Outcome='Goal'"
            " WHERE Match_ID=9002 AND Player_ID='p200'"
        )
        core_conn.commit()

        report = match_report(core_conn, 9002)
        by_pid = {s["player_id"]: s for s in report["shots"]}
        assert by_pid["p200"]["is_own_goal"] is True
        assert by_pid["p200"]["is_own_goal_inferred"] is True
        # 正常进球(有 xG)不受影响
        p100 = [s for s in report["shots"]
                if s["player_id"] == "p100" and s["period"] == "FirstHalf"][0]
        assert p100["is_own_goal"] is False
        assert p100["is_own_goal_inferred"] is False

    def test_collected_flag_wins_over_inference(self, data_dir, core_conn):
        """采集值优先:Is_Own_Goal=0 且 xG NULL + Goal(理论矛盾数据)时,
        以采集值为准,不再推断。"""
        seed_basic_core(data_dir)
        seed_match_report(core_conn, match_id=9002)
        core_conn.execute(
            "UPDATE fact_shotmap SET xG=NULL, Outcome='Goal', Is_Own_Goal=0"
            " WHERE Match_ID=9002 AND Player_ID='p200'"
        )
        core_conn.commit()

        report = match_report(core_conn, 9002)
        by_pid = {s["player_id"]: s for s in report["shots"]}
        assert by_pid["p200"]["is_own_goal"] is False
        assert by_pid["p200"]["is_own_goal_inferred"] is False

    def test_collected_true_flag(self, data_dir, core_conn):
        seed_basic_core(data_dir)
        seed_match_report(core_conn, match_id=9002)
        core_conn.execute(
            "UPDATE fact_shotmap SET Is_Own_Goal=1"
            " WHERE Match_ID=9002 AND Player_ID='p200'"
        )
        core_conn.commit()

        report = match_report(core_conn, 9002)
        by_pid = {s["player_id"]: s for s in report["shots"]}
        assert by_pid["p200"]["is_own_goal"] is True
        assert by_pid["p200"]["is_own_goal_inferred"] is False


class TestTopRated:
    def test_highest_rating_wins(self, data_dir, core_conn):
        """seed 里评分:p100=7.7(主) p101=7.0 p102=6.4 p200=6.6(客)
        p201=None → 最高分是 p100。"""
        seed_basic_core(data_dir)
        seed_match_report(core_conn, match_id=9002)

        top = match_report(core_conn, 9002)["top_rated"]
        assert top["player_id"] == "p100"
        assert top["rating"] == 7.7
        assert top["is_home"] is True
        assert top["team_id"] == 1001
        assert top["shirt_number"] == "9"

    def test_tie_breaks_by_player_id(self, data_dir, core_conn):
        seed_basic_core(data_dir)
        seed_match_report(core_conn, match_id=9002)
        core_conn.execute(
            "UPDATE fact_match_lineup SET rating=7.7"
            " WHERE Match_ID=9002 AND Player_ID='p200'"
        )
        core_conn.commit()

        top = match_report(core_conn, 9002)["top_rated"]
        assert top["player_id"] == "p100"  # 'p100' < 'p200' 字典序,确定性

    def test_no_ratings_yields_none(self, data_dir, core_conn):
        seed_basic_core(data_dir)
        seed_match_report(core_conn, match_id=9002)
        core_conn.execute("UPDATE fact_match_lineup SET rating=NULL WHERE Match_ID=9002")
        core_conn.commit()

        assert match_report(core_conn, 9002)["top_rated"] is None

    def test_report_endpoint_exposes_top_rated(self, app, data_dir):
        seed_basic_core(data_dir)
        conn = connect_rw("core")
        try:
            seed_match_report(conn, match_id=9002)
        finally:
            conn.close()

        r = TestClient(app).get("/api/v1/matches/9002/report")
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is True
        assert body["top_rated"]["player_id"] == "p100"
        assert body["top_rated"]["rating"] == 7.7
