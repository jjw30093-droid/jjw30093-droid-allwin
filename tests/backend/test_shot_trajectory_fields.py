"""射门轨迹线字段(2026-08-24,复刻 FotMob 点击射门画轨迹线的效果)。

覆盖:
- `_shots()`/`/report` 端点对未回填的射门(fact_shotmap 既有行,新列恒 NULL)
  如实返回全 None,不编造;
- 已回填的射门(新列有真实值)原样透传到响应体,不做任何镜像/换算;
- `shot_id` 同样原样透出(fact_shotmap.Shot_ID 早已存在,这次顺手补上
  SELECT/DTO 投影)。
"""

import pytest

from backend.db.connections import connect_rw

from .coreseed import seed_basic_core, seed_match_report


@pytest.fixture
def seeded_report(data_dir):
    """9002(英超完赛)种满五张事实表;9001 保持 NotStarted 零事实数据。
    与 test_match_report.py 同一个 fixture 定义(不同测试文件各自独立定义,
    不共享 fixture 是本仓库既有惯例——两边各自维护一份小体量、显式的种子)。
    """
    seed_basic_core(data_dir)
    conn = connect_rw("core")
    seed_match_report(conn, match_id=9002)
    conn.commit()
    conn.close()
    return data_dir


def _seed_trajectory(conn, match_id=9002, player_id="p100", minute=24):
    """给既有的一条射门(seed_match_report 种的 p100 24' Goal)补上轨迹字段。
    按 Match_ID+Player_ID+Minute 定位,不新插入行——这条射门已经存在,只是
    新列此前恒为 NULL。"""
    conn.execute(
        "UPDATE fact_shotmap SET Shot_ID=?, Blocked_X=?, Blocked_Y=?,"
        " Goal_Crossed_Y=?, Goal_Crossed_Z=?, On_Goal_Shot_X=?,"
        " On_Goal_Shot_Y=?, On_Goal_Shot_Zoom_Ratio=?"
        " WHERE Match_ID=? AND Player_ID=? AND Minute=?",
        (2958277893, None, None, 35.75, 0.69, 0.54, 0.18, 1.0, match_id, player_id, minute),
    )
    conn.commit()


class TestShotsTrajectoryFields:
    def test_unbackfilled_shots_return_none_not_fabricated(self, seeded_report, client):
        """seed_match_report 种的既有射门从未跑过这次的新解析逻辑,新列
        必须如实为 None,不能编造成 0 或某个默认坐标。"""
        r = client.get("/api/v1/matches/9002/report")
        assert r.status_code == 200
        shots = r.json()["shots"]
        assert len(shots) > 0
        for s in shots:
            assert s["shot_id"] is None
            assert s["blocked_x"] is None
            assert s["blocked_y"] is None
            assert s["goal_crossed_y"] is None
            assert s["goal_crossed_z"] is None
            assert s["on_goal_shot_x"] is None
            assert s["on_goal_shot_y"] is None
            assert s["on_goal_shot_zoom_ratio"] is None

    def test_backfilled_shot_fields_pass_through_unmodified(self, seeded_report, client):
        conn = connect_rw("core")
        try:
            _seed_trajectory(conn)
        finally:
            conn.close()

        r = client.get("/api/v1/matches/9002/report")
        assert r.status_code == 200
        shots = r.json()["shots"]
        goal_shot = next(s for s in shots if s["player_id"] == "p100" and s["minute"] == 24)
        assert goal_shot["shot_id"] == 2958277893
        assert goal_shot["blocked_x"] is None
        assert goal_shot["blocked_y"] is None
        assert goal_shot["goal_crossed_y"] == 35.75
        assert goal_shot["goal_crossed_z"] == 0.69
        assert goal_shot["on_goal_shot_x"] == 0.54
        assert goal_shot["on_goal_shot_y"] == 0.18
        assert goal_shot["on_goal_shot_zoom_ratio"] == 1.0

        # 其它未被 _seed_trajectory 触碰的射门必须原样保持全 None——
        # UPDATE 语句按 Player_ID+Minute 精确定位,不能误伤同场其它射门。
        other_shot = next(s for s in shots if s["player_id"] == "p200")
        assert other_shot["shot_id"] is None
        assert other_shot["goal_crossed_y"] is None

    def test_blocked_coords_pass_through_when_present(self, seeded_report, client):
        """有真实封堵坐标时同样原样透传(与上一条互补:那条测试
        blocked_x/y 为 None 的分支,这条测试非 None 分支)。"""
        conn = connect_rw("core")
        try:
            conn.execute(
                "UPDATE fact_shotmap SET Blocked_X=?, Blocked_Y=?"
                " WHERE Match_ID=? AND Player_ID=? AND Minute=?",
                (81.13, 33.16, 9002, "p100", 24),
            )
            conn.commit()
        finally:
            conn.close()

        r = client.get("/api/v1/matches/9002/report")
        shot = next(s for s in r.json()["shots"] if s["player_id"] == "p100" and s["minute"] == 24)
        assert shot["blocked_x"] == 81.13
        assert shot["blocked_y"] == 33.16
