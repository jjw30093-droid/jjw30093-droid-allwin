"""/api/v1/matches/{id}/preview:赛前预览(数据 tab 阵容/风格/球员三个子tab 数据源)。

覆盖:各模块独立降级(两队都没阵容快照/有伤停/无伤停)、风格视角与进攻来源
真实数值、联赛门禁、404、缓存头——与 test_match_report.py 同一套验证方式。
"""

import json

import pytest

from backend.db.connections import connect_rw

from .authflow import wechat_scan_login
from .coreseed import insert_match, seed_basic_core, seed_core_schema


def _seed_lineup(conn, fotmob_match_id, payload, *, observed_at="2026-08-15T09:00:00Z"):
    conn.execute(
        """INSERT INTO bronze_fm_lineup_snap
           (fotmob_match_id, payload_json, payload_hash, observed_at, ingested_at, poll_run_id)
           VALUES (?, ?, 'x', ?, ?, 'run1')""",
        (fotmob_match_id, json.dumps(payload), observed_at, observed_at),
    )


def _seed_sideline(conn, fotmob_match_id, team_id, sidelined, *, observed_at="2026-08-15T09:00:00Z"):
    conn.execute(
        """INSERT INTO bronze_fm_sideline_snap
           (fotmob_match_id, fotmob_team_id, payload_json, payload_hash, observed_at, ingested_at, poll_run_id)
           VALUES (?, ?, ?, 'x', ?, ?, 'run1')""",
        (fotmob_match_id, team_id, json.dumps({"team_id": team_id, "sidelined": sidelined}),
         observed_at, observed_at),
    )


def _stats(conn, match_id, team_id, **fields):
    conn.execute(
        "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
        " VALUES (?, ?, 'All', 0, ?)",
        (match_id, team_id, json.dumps(fields)),
    )


@pytest.fixture
def seeded_preview(data_dir):
    """9002(英超,1001 vs 1002,已完赛)近 3 场历史 + 阵容/伤停快照。"""
    seed_basic_core(data_dir)
    conn = connect_rw("core")
    for i in range(3):
        mid = 9500 + i
        insert_match(conn, mid, league_id=47, season="2025/2026",
                     date=f"2026-04-{10+i:02d}", home_id=1001, away_id=1002,
                     home="Arsenal", away="Chelsea", status="Finish",
                     home_score=1, away_score=0)
        _stats(conn, mid, 1001, BallPossesion=60.0, expected_goals=2.0)
        _stats(conn, mid, 1002, BallPossesion=40.0, expected_goals=1.0)
    conn.commit()
    conn.close()

    conn_odds = connect_rw("odds")
    _seed_lineup(conn_odds, 9002, {
        "lineup_type": "lastStarting11", "source": "lastStartingLineups",
        "home": {"team_id": 1001, "formation": "4-3-3",
                  "starters": [{"id": 1, "name": "Home Striker", "shirt_number": "9"}], "subs": []},
        "away": {"team_id": 1002, "formation": "4-4-2",
                  "starters": [{"id": 2, "name": "Away Striker", "shirt_number": "10"}], "subs": []},
    })
    _seed_sideline(conn_odds, 9002, 1001, [
        {"id": 3, "name": "Injured Home Player", "reason": "injury", "expected_return": "A few days"},
    ])
    _seed_sideline(conn_odds, 9002, 1002, [])
    conn_odds.commit()
    conn_odds.close()
    return data_dir


class TestAvailability:
    def test_finished_match_full_preview(self, seeded_preview, client):
        r = client.get("/api/v1/matches/9002/preview")
        assert r.status_code == 200
        body = r.json()
        assert body["match_id"] == 9002
        assert body["lineups"]["lineup_type"] == "lastStarting11"
        assert body["lineups"]["home"]["formation"] == "4-3-3"
        assert body["lineups"]["home"]["starters"][0]["name"] == "Home Striker"
        assert len(body["sidelined"]["home"]) == 1
        assert body["sidelined"]["home"][0]["expected_return"] == "A few days"
        assert body["sidelined"]["away"] == []  # 确认无伤停,是合法结果不是缺失
        assert body["home_window"] == {"matches": 3, "from": "2026-04-10", "to": "2026-04-12"}

    def test_no_lineup_snapshot_gives_null_not_empty_sides(self, data_dir, client):
        """从未采集过阵容快照的比赛:lineups.home/away 必须是 None,
        不能伪装成"两队都零阵容"的空侧结构(那会被误读成已核实为空)。"""
        seed_basic_core(data_dir)
        r = client.get("/api/v1/matches/9002/preview")
        assert r.status_code == 200
        body = r.json()
        assert body["lineups"]["lineup_type"] is None
        assert body["lineups"]["home"] is None
        assert body["lineups"]["away"] is None
        assert body["sidelined"] == {"home": [], "away": []}

    def test_no_recent_history_gives_none_window_and_empty_lists(self, data_dir, client):
        """没有历史比赛的队伍(如全新升班马):窗口为 None,各聚合列表诚实为空,
        不是 500 或补 0。"""
        seed_core_schema(connect_rw("core"))
        conn = connect_rw("core")
        insert_match(conn, 9600, league_id=47, date="2026-05-01",
                     home_id=5001, away_id=5002, home="新军甲", away="新军乙", status="NotStarted")
        conn.commit()
        conn.close()
        r = client.get("/api/v1/matches/9600/preview")
        assert r.status_code == 200
        body = r.json()
        assert body["home_window"] is None
        assert body["away_window"] is None
        assert body["style_views"][0]["points"] == []
        assert body["key_players"]["home"] == [
            {"id": "dribbles", "title": "破局点", "metric": "过人成功占全队", "rows": []},
            {"id": "creating", "title": "组织核心", "metric": "创造机会占全队", "rows": []},
            {"id": "defending", "title": "防守支柱", "metric": "抢断+拦截占全队", "rows": []},
        ]
        assert body["keepers"]["home"] == []

    def test_unknown_match_404(self, seeded_preview, client):
        assert client.get("/api/v1/matches/424242/preview").status_code == 404


class TestStyleAndAttackSources:
    def test_style_views_reflect_seeded_stats(self, seeded_preview, client):
        body = client.get("/api/v1/matches/9002/preview").json()
        pf = next(v for v in body["style_views"] if v["id"] == "poss-fastbreak")
        home_point = next(p for p in pf["points"] if p["team_id"] == 1001)
        assert home_point["x"] == 60.0

    def test_attack_sources_empty_without_shotmap(self, seeded_preview, client):
        """本 fixture 没造 fact_shotmap 行——诚实返回空列表,不是报错。"""
        body = client.get("/api/v1/matches/9002/preview").json()
        assert body["attack_sources"]["home"] == []
        assert body["attack_sources"]["away"] == []


class TestGateAndCache:
    def test_previously_lottery_league_open_to_anonymous(self, seeded_preview, client, fresh_ip):
        """2026-08-16 起:除"每日精选"外全站比赛内容全部免费,原
        league:lottery 联赛(67)不再需要登录——匿名与登录后返回同一状态码。
        这条断言正是要推翻的旧规则(此前匿名 401)。"""
        conn = connect_rw("core")
        insert_match(conn, 9301, league_id=67, season="2026", date="2026-05-10",
                     home_id=2001, away_id=2002, home="Vasteras SK", away="Orgryte IS")
        conn.commit()
        conn.close()
        assert client.get("/api/v1/matches/9301/preview").status_code == 200
        wechat_scan_login(client, ip=fresh_ip)
        assert client.get("/api/v1/matches/9301/preview").status_code == 200

    def test_anonymous_epl_gets_public_cache(self, seeded_preview, client):
        r = client.get("/api/v1/matches/9002/preview")
        assert "public" in r.headers["Cache-Control"]
        assert "s-maxage" in r.headers["Cache-Control"]

    def test_cookie_request_forced_no_store(self, seeded_preview, client, fresh_ip):
        wechat_scan_login(client, ip=fresh_ip)
        r = client.get("/api/v1/matches/9002/preview")
        assert r.status_code == 200
        assert r.headers["Cache-Control"] == "private, no-store"
