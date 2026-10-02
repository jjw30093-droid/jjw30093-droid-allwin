"""模拟器参数下发(2026-10-02 登录门禁):backend/queries/simulator_params.py
+ /api/v1/simulator/*。

切片规则与 frontend/app/simulator/loadParams.ts 一一对应,下面的用例与
frontend/tests/simulator-load-params.test.ts 的"按联赛下发"用同一组布景,
改其中一边必须同步另一边。
"""

import json

import pytest
from fastapi.testclient import TestClient

from backend.queries import simulator_params as q_sim

from .authflow import wechat_scan_login


def _params(generated_at="2026-09-30T00:00:00Z"):
    def team(tid, lg, prefix):
        return {
            "team_id": tid, "league_id": lg, "name_zh": f"队{tid}",
            "squad": [f"{prefix}1", f"{prefix}2"],
            "last_lineup": {"match_id": 1, "date": "2026-09-20", "formation": "4-3-3",
                            "starters": [{"player_id": f"{prefix}1", "position_id": 11}]},
        }
    return {
        "meta": {"generated_at": generated_at, "model_version": "v0.3"},
        "leagues": {"47": {}, "87": {}, "55": {}, "54": {}, "53": {}},
        "formations": {},
        "position_map": {"4-3-3": {"11": "GK"}},
        "teams": {"1": team(1, 47, "a"), "2": team(2, 47, "b"), "3": team(3, 87, "c"), "4": team(4, 87, "d")},
        "players": {k: {} for k in ("a1", "a2", "b1", "b2", "c1", "c2", "d1", "d2")},
        "fixtures": {
            "10": {"match_id": 10, "league_id": 47, "home_team_id": 1, "away_team_id": 2,
                   "kickoff_at_utc": "2026-10-01T19:00:00Z", "status": "未开赛", "final_score": None,
                   "ah": {"line": 0.5}, "ou": None, "market_lambda": {"home": 1.5, "away": 1}},
            "11": {"match_id": 11, "league_id": 87, "home_team_id": 3, "away_team_id": 4,
                   "kickoff_at_utc": "2026-10-01T19:00:00Z", "status": "未开赛", "final_score": None,
                   "ah": {"line": -0.25}, "ou": {"line": 2.5}, "market_lambda": {"home": 1.2, "away": 1.1}},
            "12": {"match_id": 12, "league_id": 87, "home_team_id": 3, "away_team_id": 4,
                   "kickoff_at_utc": "2026-10-02T19:00:00Z", "status": "未开赛", "final_score": None,
                   "ah": None, "ou": None, "market_lambda": None},
        },
        "calibration": {},
    }


class TestSlice:
    def test_slice_keeps_only_league(self):
        s = q_sim.slice_for_league(_params(), 47)
        assert sorted(s["teams"]) == ["1", "2"]
        assert sorted(s["players"]) == ["a1", "a2", "b1", "b2"]
        assert list(s["fixtures"]) == ["10"]
        assert len(s["leagues"]) == 5

    def test_fixture_index_only_market_fixtures_and_league_filter(self):
        idx = q_sim.fixture_index(_params())
        assert [f["match_id"] for f in idx] == [10, 11]
        assert idx[1] == {
            "match_id": 11, "league_id": 87, "home_team_id": 3, "away_team_id": 4,
            "home_name": "队3", "away_name": "队4", "kickoff_at_utc": "2026-10-01T19:00:00Z",
            "status": "未开赛", "ah_line": -0.25, "ou_line": 2.5, "final_score": None,
        }
        assert [f["match_id"] for f in q_sim.fixture_index(_params(), {47})] == [10]

    def test_load_current_then_dated_fallback(self, tmp_path):
        (tmp_path / "simulator_params_20260929.json").write_text(json.dumps(_params("2026-09-29T00:00:00Z")))
        (tmp_path / "current.json").write_text("{}")
        p = q_sim.load_params({"SIMULATOR_PARAMS_DIR": str(tmp_path)})
        assert p["meta"]["generated_at"] == "2026-09-29T00:00:00Z"
        assert q_sim.load_params({"SIMULATOR_PARAMS_DIR": str(tmp_path / "x")}) is None
        assert q_sim.load_params({}) is None


@pytest.fixture
def sim_env(tmp_path, monkeypatch):
    f = tmp_path / "sim.json"
    f.write_text(json.dumps(_params()))
    monkeypatch.setenv("SIMULATOR_PARAMS_PATH", str(f))
    monkeypatch.setenv("SIMULATOR_ENABLED", "1")
    return f


class TestSimulatorApi:
    def test_anonymous_epl_ok_non_epl_401(self, app, sim_env):
        c = TestClient(app)
        r = c.get("/api/v1/simulator/params?league_id=47")
        assert r.status_code == 200
        body = r.json()
        assert sorted(body["params"]["teams"]) == ["1", "2"]
        # 匿名的真实比赛索引只有英超(盘口线是赔率数据)
        assert [f["league_id"] for f in body["fixture_index"]] == [47]
        assert r.headers["cache-control"] == "private, no-store"
        r = c.get("/api/v1/simulator/params?league_id=87")
        assert r.status_code == 401
        assert r.json()["code"] == "login_required"
        assert [f["league_id"] for f in c.get("/api/v1/simulator/fixtures").json()["fixture_index"]] == [47]

    def test_logged_in_gets_league_and_full_index(self, app, sim_env, fresh_ip):
        c = TestClient(app)
        wechat_scan_login(c, ip=fresh_ip)
        body = c.get("/api/v1/simulator/params?league_id=87").json()
        assert sorted(body["params"]["teams"]) == ["3", "4"]
        assert list(body["params"]["fixtures"]) == ["11", "12"]
        assert sorted(f["league_id"] for f in body["fixture_index"]) == [47, 87]
        assert sorted(f["league_id"] for f in c.get("/api/v1/simulator/fixtures").json()["fixture_index"]) == [47, 87]

    def test_unknown_league_404_and_switch_off_404(self, app, sim_env, monkeypatch):
        c = TestClient(app)
        assert c.get("/api/v1/simulator/params?league_id=67").status_code == 404
        monkeypatch.setenv("SIMULATOR_ENABLED", "0")
        assert c.get("/api/v1/simulator/params?league_id=47").status_code == 404

    def test_params_unavailable_503(self, app, monkeypatch, tmp_path):
        monkeypatch.setenv("SIMULATOR_ENABLED", "1")
        monkeypatch.setenv("SIMULATOR_PARAMS_PATH", str(tmp_path / "missing.json"))
        assert TestClient(app).get("/api/v1/simulator/params?league_id=47").status_code == 503
