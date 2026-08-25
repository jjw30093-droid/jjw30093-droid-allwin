"""recent_shot_map_spec 的官方口径射正修复。

背景(2026-08-12):赛前"射门落点"聚合图把逐次射门(fact_shotmap.Outcome)
按 Goal+AttemptSaved 算"射正",而 AttemptSaved 混了门将扑救与被后卫封堵的
射门——实测 26,067 个队场:场均 7.75 vs 官方 ShotsOnTarget 4.36,只有 6.8%
完全吻合。改法:recent_shot_map_spec 额外返回 fact_team_match_stats 的官方
口径(ShotsOnTarget/blocked_shots/total_shots),前端汇总数字改用它,不再
从逐次射门里数 AttemptSaved。
"""

from __future__ import annotations

import pytest

from backend.db.connections import connect_rw
from backend.queries.matches import recent_shot_map_spec
from tests.backend.coreseed import insert_match, seed_core_schema


def _seed_shot(conn, match_id, player_id, team_id, minute, period, x, y, xg, outcome):
    conn.execute(
        "INSERT INTO fact_shotmap (Match_ID, Player_ID, Team_ID, Minute, Period,"
        " X_Coord, Y_Coord, xG, xGOT, Situation, Outcome, Shot_Type)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 'RegularPlay', ?, 'RightFoot')",
        (match_id, player_id, team_id, minute, period, x, y, xg, outcome),
    )


def _seed_team_stats(conn, match_id, team_id, *, sot, blocked, total):
    conn.execute(
        "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
        " VALUES (?, ?, 'All', 0, ?)",
        (
            match_id,
            team_id,
            f'{{"ShotsOnTarget": {sot}, "blocked_shots": {blocked}, "total_shots": {total}}}',
        ),
    )


@pytest.fixture
def recent_form(data_dir):
    """目标比赛 9500(未开赛,阿队 vs 乙队)之前,阿队打了两场同联赛比赛:
    每场逐次射门里 Goal+AttemptSaved 数都明显高于官方 ShotsOnTarget
    (模拟真实数据里 AttemptSaved 混入被封堵射门的情况)。"""
    conn = connect_rw("core")
    seed_core_schema(conn)

    # 阿队(1001)近两场:8001 主场对丙队(1003),8002 客场对丁队(1004)
    insert_match(conn, 8001, league_id=47, date="2026-01-01",
                 home_id=1001, away_id=1003, home="阿队", away="丙队",
                 status="Finish", home_score=2, away_score=1)
    insert_match(conn, 8002, league_id=47, date="2026-01-08",
                 home_id=1004, away_id=1001, home="丁队", away="阿队",
                 status="Finish", home_score=0, away_score=1)
    # 目标比赛:未开赛,不应被纳入"近期"集合
    insert_match(conn, 9500, league_id=47, date="2026-01-20",
                 home_id=1001, away_id=1002, home="阿队", away="乙队",
                 status="NotStarted")

    # 8001:阿队逐次射门 4 次(2 进球 + 2 AttemptSaved),但官方口径只有 3 次射正
    # (说明 AttemptSaved 里有 1 次其实是被封堵,不该算射正)
    for i, (minute, x, y, xg, outcome) in enumerate([
        (10, 95.0, 40.0, 0.3, "Goal"),
        (20, 90.0, 35.0, 0.2, "Goal"),
        (30, 88.0, 30.0, 0.1, "AttemptSaved"),
        (40, 92.0, 45.0, 0.15, "AttemptSaved"),
    ]):
        _seed_shot(conn, 8001, f"p{i}", 1001, minute, "FirstHalf", x, y, xg, outcome)
    _seed_team_stats(conn, 8001, 1001, sot=3, blocked=1, total=8)
    _seed_team_stats(conn, 8001, 1003, sot=2, blocked=0, total=5)

    # 8002:阿队逐次射门 2 次(1 进球 + 1 AttemptSaved),官方口径 2 次射正
    # (这场两个口径一致,验证不是永远打折,只是不能默认相等)
    _seed_shot(conn, 8002, "p10", 1001, 15, "FirstHalf", 96.0, 41.0, 0.4, "Goal")
    _seed_shot(conn, 8002, "p11", 1001, 60, "SecondHalf", 89.0, 33.0, 0.12, "AttemptSaved")
    _seed_team_stats(conn, 8002, 1001, sot=2, blocked=0, total=4)
    # 丁队(1004)故意不给官方数据,模拟 fact_team_match_stats 缺失的场次

    conn.commit()
    conn.close()
    yield


class TestRecentShotMapOfficialStats:
    def test_official_stats_present_for_covered_matches(self, recent_form):
        conn = connect_rw("core")
        spec = recent_shot_map_spec(conn, 9500, window=5)
        assert spec is not None

        official = {(o["match_id"], o["team_id"]): o for o in spec["official_stats"]}
        assert official[(8001, 1001)]["shots_on_target"] == 3
        assert official[(8001, 1003)]["shots_on_target"] == 2
        assert official[(8002, 1001)]["shots_on_target"] == 2
        # 丁队(1004)在 8002 场没有官方数据,不应该凭空造一行
        assert (8002, 1004) not in official

    def test_official_sum_differs_from_naive_shotmap_count(self, recent_form):
        """这是本次修复要防的回归:两个口径必须不同,否则说明修复没生效
        (或者布景本身没能复现超算场景)。"""
        conn = connect_rw("core")
        spec = recent_shot_map_spec(conn, 9500, window=5)

        # 朴素口径:逐次射门里 Goal+AttemptSaved 计数(旧 bug 的算法)
        naive_on_target = sum(
            1
            for s in spec["shots"]
            if s["team_id"] == 1001 and s["outcome"] in ("Goal", "AttemptSaved")
        )
        # 官方口径:两场 ShotsOnTarget 相加
        official_sum = sum(
            o["shots_on_target"]
            for o in spec["official_stats"]
            if o["team_id"] == 1001 and o["shots_on_target"] is not None
        )
        assert naive_on_target == 6  # 4(8001) + 2(8002),旧算法会展示这个数
        assert official_sum == 5  # 3(8001) + 2(8002),真实官方口径
        assert naive_on_target != official_sum

    def test_target_match_itself_excluded_from_recent_set(self, recent_form):
        conn = connect_rw("core")
        spec = recent_shot_map_spec(conn, 9500, window=5)
        match_ids = {m["match_id"] for m in spec["matches"]}
        assert 9500 not in match_ids
