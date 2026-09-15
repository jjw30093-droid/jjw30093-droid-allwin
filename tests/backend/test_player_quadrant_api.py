"""backend/queries/player_quadrant.py + /api/v1/leagues/{id}/player-quadrant
(2026-09-15)。端到端:seed fact_player_match_stats → 跑 build_player_season_*
把结果写进 silver 表(与生产 build_silver.py 同一条链路)→ 查询层/API 只测
接线是否正确(赛季解析、宽松下限裁剪、excluded_below_floor 如实披露、DTO
字段形状)——具体聚合正确性由 test_player_season.py 单独覆盖。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.db.connections import connect_rw
from backend.queries import player_quadrant as q
from backend.schema import SILVER_PLAYER_SEASON_COLUMNS, SILVER_PLAYER_SEASON_RATIOS_COLUMNS, _quote
from backend.silver.player_season import build_player_season_ratios, build_player_season_stats
from tests.backend.coreseed import insert_match, seed_core_schema

LEAGUE = 47
SEASON = "2025/2026"


def _stats(conn, match_id, player_id, team_id, **fields):
    cols = ["Match_ID", "Player_ID", "Team_ID"] + list(fields.keys())
    quoted = [f'"{c}"' if "." in c else c for c in cols]
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO fact_player_match_stats ({', '.join(quoted)}) VALUES ({placeholders})",
        [match_id, player_id, team_id, *fields.values()],
    )


def _insert_many(conn, table, columns, rows):
    if not rows:
        return
    names = [n for n, _ in columns if n != "updated_at"]
    cols_sql = ", ".join(_quote(n) for n in names)
    placeholders = ", ".join("?" for _ in names)
    conn.executemany(
        f"INSERT INTO {table} ({cols_sql}, updated_at) VALUES ({placeholders}, ?)",
        [tuple(row.get(n) for n in names) + ("2026-09-15T00:00:00Z",) for row in rows],
    )


def _rebuild_silver(conn, league_id, season):
    """与 build_silver.py 同一条链路:fact 表聚合 → DELETE+INSERT 进 silver 表。"""
    stats_rows = build_player_season_stats(conn, league_id, season)
    ratio_rows = build_player_season_ratios(conn, league_id, season)
    conn.execute("DELETE FROM silver_player_season WHERE League_ID=? AND Season=?", (league_id, season))
    _insert_many(conn, "silver_player_season", SILVER_PLAYER_SEASON_COLUMNS, stats_rows)
    conn.execute("DELETE FROM silver_player_season_ratios WHERE League_ID=? AND Season=?", (league_id, season))
    _insert_many(conn, "silver_player_season_ratios", SILVER_PLAYER_SEASON_RATIOS_COLUMNS, ratio_rows)


def _seed_season(conn, n_matches=4):
    for i in range(n_matches):
        mid = 9100 + i
        insert_match(
            conn, mid, league_id=LEAGUE, season=SEASON, date=f"2025-09-{10+i:02d}",
            home_id=1001, away_id=1002, status="Finish", home_score=1, away_score=0,
        )
        _stats(
            conn, mid, "p1", 1001, minutes_played=90, usual_position="2",
            player_name="Regular MF", chances_created=1, expected_assists=0.1,
        )
    return list(range(9100, 9100 + n_matches))


@pytest.fixture
def core_conn(data_dir):
    conn = connect_rw("core")
    seed_core_schema(conn)
    yield conn
    conn.close()


class TestPlayerQuadrantStats:
    def test_no_seasons_returns_empty(self, core_conn):
        data = q.player_quadrant_stats(core_conn, LEAGUE)
        assert data["rows"] == []
        assert data["available_seasons"] == []

    def test_season_too_early_returns_empty_reason(self, core_conn):
        """MIN_MATCHES_FOR_SEASON_DATA=4,球员只出场 2 场时视为赛季刚开始。"""
        _seed_season(core_conn, n_matches=2)
        _rebuild_silver(core_conn, LEAGUE, SEASON)
        core_conn.commit()
        data = q.player_quadrant_stats(core_conn, LEAGUE, SEASON)
        assert data["rows"] == []
        assert "empty_reason" in data

    def test_full_season_returns_row_with_ratios(self, core_conn):
        _seed_season(core_conn, n_matches=4)
        _rebuild_silver(core_conn, LEAGUE, SEASON)
        core_conn.commit()
        data = q.player_quadrant_stats(core_conn, LEAGUE, SEASON)
        assert data["season"] == SEASON
        assert len(data["rows"]) == 1
        row = data["rows"][0]
        assert row["player"]["player_id"] == "p1"
        assert row["usual_position"] == 2
        assert row["minutes_played"] == 360
        assert row["ratios"]["chances_created_per90"]["numerator"] == 4
        assert row["ratios"]["chances_created_per90"]["value"] == pytest.approx(90 * 4 / 360)

    def test_minutes_share_below_floor_excluded_and_counted(self, core_conn):
        """出场占比低于 MINUTES_SHARE_FLOOR(0.15)的球员被后端裁掉,
        excluded_below_floor 必须如实数出裁了几个,不静默丢弃。"""
        matches = _seed_season(core_conn, n_matches=4)
        # 替补:只在第一场出场 5 分钟(可用分钟 4*90=360,占比 5/360≈1.4% < 15%)
        _stats(core_conn, matches[0], "p2", 1001, minutes_played=5, usual_position="3", player_name="Sub")
        _rebuild_silver(core_conn, LEAGUE, SEASON)
        core_conn.commit()

        data = q.player_quadrant_stats(core_conn, LEAGUE, SEASON)
        pids = {r["player"]["player_id"] for r in data["rows"]}
        assert "p2" not in pids
        assert data["excluded_below_floor"] == 1

    def test_player_above_floor_but_missing_ratio_field_still_included(self, core_conn):
        """出场占比达标但某个字段全场缺失(如非门将球员的 goals_prevented)时,
        该球员仍应出现在 rows 里——per-90 类指标的分母是出场分钟数(恒有值),
        所以这一行本身还在,只是 value/numerator 是 None(不是整行消失,
        也不是被静默补 0)。"""
        _seed_season(core_conn, n_matches=4)
        _rebuild_silver(core_conn, LEAGUE, SEASON)
        core_conn.commit()
        data = q.player_quadrant_stats(core_conn, LEAGUE, SEASON)
        row = data["rows"][0]
        gk_ratio = row["ratios"]["goals_prevented_per90"]
        assert gk_ratio["value"] is None
        assert gk_ratio["numerator"] is None
        assert gk_ratio["denominator"] == 360.0  # 分母(出场分钟数)恒有值,不受数据缺失影响

    def test_chinese_name_prefers_short_then_full_then_english(self, core_conn):
        _seed_season(core_conn, n_matches=4)
        _rebuild_silver(core_conn, LEAGUE, SEASON)
        core_conn.execute(
            "INSERT INTO dim_player_i18n (Player_ID, name_zh, name_zh_short)"
            " VALUES ('p1', '测试中场全名', '测试中场')"
        )
        core_conn.commit()
        data = q.player_quadrant_stats(core_conn, LEAGUE, SEASON)
        assert data["rows"][0]["player"]["name"] == "测试中场"

    def test_team_ref_and_color_present(self, core_conn):
        _seed_season(core_conn, n_matches=4)
        _rebuild_silver(core_conn, LEAGUE, SEASON)
        core_conn.execute(
            "INSERT OR REPLACE INTO dim_team_i18n VALUES (1001,'Arsenal','阿森纳','t','')"
        )
        core_conn.commit()
        data = q.player_quadrant_stats(core_conn, LEAGUE, SEASON)
        assert data["rows"][0]["team"]["name"] == "阿森纳"

    def test_all_ratio_metric_keys_resolvable_via_registry(self, core_conn):
        """RATIO_METRIC_KEYS 里每个 key 都必须真的能在 registry 里解析(与
        test_player_season.py 的同款接线测试互相独立覆盖,防止两处任一方
        漏改时另一方还能兜住)。"""
        from backend.metrics.registry import get_metric

        for key in q.RATIO_METRIC_KEYS:
            get_metric(key)  # 找不到直接抛 KeyError,测试即失败


@pytest.fixture
def seeded_min(core_conn):
    """给端点测试用的最小可行布景:一名中场 4 场出场,并把聚合结果写进
    silver 表(端点查询层只读 silver 表,不读 fact 表)。"""
    _seed_season(core_conn, n_matches=4)
    _rebuild_silver(core_conn, LEAGUE, SEASON)
    core_conn.commit()
    return core_conn


class TestPlayerQuadrantEndpoint:
    def test_unknown_league_404(self, app, seeded_min):
        client = TestClient(app)
        assert client.get("/api/v1/leagues/9999/player-quadrant").status_code == 404

    def test_anonymous_access_ok_and_cache_header(self, app, seeded_min):
        """回归测试:本端点必须在 backend/api/cache_policy.py::PUBLIC_ALLOWLIST
        里登记,否则全局 default-deny 中间件会把 endpoint 自己设置的
        PUBLIC_CACHE 强制改写成 private, no-store(真实踩过的坑,2026-09-15,
        见该文件里给这条路径新增的登记)。"""
        client = TestClient(app)
        r = client.get("/api/v1/leagues/47/player-quadrant")
        assert r.status_code == 200
        assert r.headers["cache-control"].startswith("public")

    def test_response_shape_has_no_position_param_and_returns_all_rows(self, app, seeded_min):
        """端点不接受 position 参数(位置过滤留在前端)——传了也被 FastAPI
        忽略(未声明的 query 参数默认不报错,不影响响应)。"""
        client = TestClient(app)
        r = client.get("/api/v1/leagues/47/player-quadrant?position=gk")
        assert r.status_code == 200
        body = r.json()
        assert "rows" in body
        assert "excluded_below_floor" in body
        assert len(body["rows"]) == 1
