"""比赛详情页"比赛信息"卡片:球场/天气/裁判(2026-08-20,站长要求参照本地
miaomiaodi.cc 的比赛详情页加球场/温度等信息)。

覆盖:
- match_by_id() 在 dim_match 有真实场馆/天气/裁判时正确投影这 7 个新字段,
  且不影响既有字段;
- 字段缺失(NULL)时如实返回 None,不编造(CLAUDE.md §6.2);
- Temperature/Wind_Speed 从 TEXT 容错解析为 int,解析不出来时为 None,不崩溃;
- GET /api/v1/matches/{id} 端到端把这些字段透出到 match 对象里;
- GET /api/v1/matches(列表端点)不携带这些字段——详情专属,不污染列表 payload
  (MatchDetailSummary 是 MatchSummary 的子类,不是反过来)。
"""

import pytest
from fastapi.testclient import TestClient

from backend.db.connections import connect_rw
from backend.queries.matches import match_by_id

from .coreseed import seed_basic_core


@pytest.fixture
def core_conn(data_dir):
    conn = connect_rw("core")
    yield conn
    conn.close()


def _seed_full_details(conn, match_id=9001):
    conn.execute(
        "UPDATE dim_match SET Referee=?, Temperature=?, Wind_Speed=?, "
        "Venue_Name=?, Venue_City=?, Venue_Country=?, Weather_Description=? "
        "WHERE Match_ID=?",
        (
            "Mischa Kellerhals", "18", "9",
            "Nye Fredrikstad Stadion", "Fredrikstad", "Norway",
            "Partly Cloudy/Wind",
            match_id,
        ),
    )
    conn.commit()


class TestMatchByIdVenueWeather:
    def test_populated_fields_project_correctly(self, data_dir, core_conn):
        seed_basic_core(data_dir)
        _seed_full_details(core_conn)

        m = match_by_id(core_conn, 9001)
        assert m["referee"] == "Mischa Kellerhals"
        assert m["temperature_c"] == 18
        assert m["wind_speed_kmh"] == 9
        assert m["venue_name"] == "Nye Fredrikstad Stadion"
        assert m["venue_city"] == "Fredrikstad"
        assert m["venue_country"] == "Norway"
        assert m["weather_description"] == "Partly Cloudy/Wind"
        # 既有字段不受影响
        assert m["match_id"] == 9001
        assert m["home"]["name"] == "阿森纳"

    def test_missing_fields_are_none_not_fabricated(self, data_dir, core_conn):
        seed_basic_core(data_dir)  # 不调用 _seed_full_details:全部留空

        m = match_by_id(core_conn, 9001)
        assert m["referee"] is None
        assert m["temperature_c"] is None
        assert m["wind_speed_kmh"] is None
        assert m["venue_name"] is None
        assert m["venue_city"] is None
        assert m["venue_country"] is None
        assert m["weather_description"] is None

    def test_non_numeric_temperature_parses_to_none_not_crash(self, data_dir, core_conn):
        seed_basic_core(data_dir)
        core_conn.execute(
            "UPDATE dim_match SET Temperature=?, Wind_Speed=? WHERE Match_ID=9001",
            ("not-a-number", ""),
        )
        core_conn.commit()

        m = match_by_id(core_conn, 9001)
        assert m["temperature_c"] is None
        assert m["wind_speed_kmh"] is None


class TestMatchDetailApiExposesVenueWeather:
    def test_detail_endpoint_includes_venue_weather_referee(self, app, data_dir):
        seed_basic_core(data_dir)
        conn = connect_rw("core")
        try:
            _seed_full_details(conn)
        finally:
            conn.close()

        client = TestClient(app)
        r = client.get("/api/v1/matches/9001")
        assert r.status_code == 200
        m = r.json()["match"]
        assert m["referee"] == "Mischa Kellerhals"
        assert m["temperature_c"] == 18
        assert m["wind_speed_kmh"] == 9
        assert m["venue_name"] == "Nye Fredrikstad Stadion"
        assert m["venue_city"] == "Fredrikstad"
        assert m["venue_country"] == "Norway"
        assert m["weather_description"] == "Partly Cloudy/Wind"

    def test_list_endpoint_does_not_carry_venue_weather_fields(self, app, data_dir):
        """详情专属字段不得出现在列表卡片里——列表端点从不查询这些列,
        避免每张卡片都多出几个恒为 null 的键(契约纪律,§10.3)。"""
        seed_basic_core(data_dir)
        conn = connect_rw("core")
        try:
            _seed_full_details(conn)
        finally:
            conn.close()

        client = TestClient(app)
        r = client.get("/api/v1/matches")
        assert r.status_code == 200
        matches = r.json()["matches"]
        assert len(matches) >= 1
        for m in matches:
            assert "venue_name" not in m
            assert "weather_description" not in m
            assert "referee" not in m
