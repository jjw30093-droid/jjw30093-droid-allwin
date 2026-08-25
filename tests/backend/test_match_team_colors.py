"""比赛详情页图表配色(2026-08-24,站长要求比赛详情图表接入真实球队配色、
不再固定用品牌青绿/蓝两色;经反编译 FotMob APK + 生产 API 实测确认后落地)。

覆盖:
- FotMobClient.parse_match_dim 在 general.teamColors 存在/缺失两种情况下的
  解析结果(真实 fixture,不是合成);
- match_by_id() 在 dim_match 有真实配色时正确投影成嵌套的
  {light, dark} 对象,且不影响既有字段;
- 双方都缺失时该字段为 None(不是 {light:null,dark:null}),供前端一次性
  判断"这场比赛完全没有配色数据";
- GET /api/v1/matches/{id} 端到端把这两个字段透出到 match 对象里;
- GET /api/v1/matches(列表端点)不携带这些字段——详情专属,不污染列表 payload。
"""

import json
import os

import pytest
from fastapi.testclient import TestClient

from backend.db.connections import connect_rw
from backend.fotmob_client import FotMobClient
from backend.queries.matches import match_by_id

from .coreseed import seed_basic_core

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_PATH = os.path.join(
    REPO_ROOT, "tests", "fixtures", "fotmob", "prematch-5104961.json"
)


def _load_fixture() -> dict:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


class TestParseMatchDimTeamColors:
    def test_real_payload_extracts_all_four_colors(self):
        """真实赛前 payload(5104961)的 general.teamColors 已实测确认存在,
        四个值必须原样提取,不得漏取或错位(light/dark、home/away 四组
        不能互相搭错)。"""
        client = FotMobClient(proxy="")
        row = client.parse_match_dim(_load_fixture(), match_id=5104961, league_id=59)

        assert row["Home_Team_Color_Light"] == "#f13c26"
        assert row["Home_Team_Color_Dark"] == "#f13c26"
        assert row["Away_Team_Color_Light"] == "#104070"
        assert row["Away_Team_Color_Dark"] == "#035db8"

    def test_missing_team_colors_node_yields_all_none_not_crash(self):
        """teamColors 节点整体缺失(旧赛季/未覆盖的 payload)必须优雅退化为
        全 None,不得抛异常,也不得编造默认色。"""
        payload = _load_fixture()
        payload["general"].pop("teamColors", None)
        client = FotMobClient(proxy="")
        row = client.parse_match_dim(payload, match_id=5104961, league_id=59)

        assert row["Home_Team_Color_Light"] is None
        assert row["Home_Team_Color_Dark"] is None
        assert row["Away_Team_Color_Light"] is None
        assert row["Away_Team_Color_Dark"] is None

    def test_partial_team_colors_only_light_mode_present(self):
        """只有 lightMode(没有 darkMode)时,light 两列有值、dark 两列为
        None——不得用 light 值顶替缺失的 dark 值。"""
        payload = _load_fixture()
        payload["general"]["teamColors"] = {"lightMode": {"home": "#111111", "away": "#222222"}}
        client = FotMobClient(proxy="")
        row = client.parse_match_dim(payload, match_id=5104961, league_id=59)

        assert row["Home_Team_Color_Light"] == "#111111"
        assert row["Away_Team_Color_Light"] == "#222222"
        assert row["Home_Team_Color_Dark"] is None
        assert row["Away_Team_Color_Dark"] is None


@pytest.fixture
def core_conn(data_dir):
    conn = connect_rw("core")
    yield conn
    conn.close()


def _seed_colors(conn, match_id=9001):
    conn.execute(
        "UPDATE dim_match SET Home_Team_Color_Light=?, Home_Team_Color_Dark=?, "
        "Away_Team_Color_Light=?, Away_Team_Color_Dark=? WHERE Match_ID=?",
        ("#f13c26", "#f13c26", "#104070", "#035db8", match_id),
    )
    conn.commit()


class TestMatchByIdTeamColors:
    def test_populated_fields_project_as_nested_pairs(self, data_dir, core_conn):
        seed_basic_core(data_dir)
        _seed_colors(core_conn)

        m = match_by_id(core_conn, 9001)
        assert m["home_team_color"] == {"light": "#f13c26", "dark": "#f13c26"}
        assert m["away_team_color"] == {"light": "#104070", "dark": "#035db8"}
        # 既有字段不受影响
        assert m["match_id"] == 9001
        assert m["home"]["name"] == "阿森纳"

    def test_missing_fields_are_none_not_fabricated(self, data_dir, core_conn):
        seed_basic_core(data_dir)  # 不调用 _seed_colors:全部留空

        m = match_by_id(core_conn, 9001)
        assert m["home_team_color"] is None
        assert m["away_team_color"] is None


class TestMatchDetailApiExposesTeamColors:
    def test_detail_endpoint_includes_team_colors(self, app, data_dir):
        seed_basic_core(data_dir)
        conn = connect_rw("core")
        try:
            _seed_colors(conn)
        finally:
            conn.close()

        client = TestClient(app)
        r = client.get("/api/v1/matches/9001")
        assert r.status_code == 200
        m = r.json()["match"]
        assert m["home_team_color"] == {"light": "#f13c26", "dark": "#f13c26"}
        assert m["away_team_color"] == {"light": "#104070", "dark": "#035db8"}

    def test_list_endpoint_does_not_carry_team_color_fields(self, app, data_dir):
        """详情专属字段不得出现在列表卡片里(契约纪律,§10.3),同
        test_match_venue_weather.py 的既有先例。"""
        seed_basic_core(data_dir)
        conn = connect_rw("core")
        try:
            _seed_colors(conn)
        finally:
            conn.close()

        client = TestClient(app)
        r = client.get("/api/v1/matches")
        assert r.status_code == 200
        matches = r.json()["matches"]
        assert len(matches) >= 1
        for m in matches:
            assert "home_team_color" not in m
            assert "away_team_color" not in m
