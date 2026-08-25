"""backend/queries/team_style_preview.py 测试(数据 tab 模块二:风格象限 + 模块三:进攻来源)。"""

from __future__ import annotations

import json

import pytest

from backend.db.connections import connect_rw
from backend.queries import team_style_preview as q
from tests.backend.coreseed import insert_match, seed_core_schema

LEAGUE = 47
SEASON = "2025/2026"  # style_league 夹具里所有比赛用的赛季
# insert_match() 不传 season 时按 (league, date) 推导(2026-08-25 起,
# 见 coreseed._AUTO_SEASON)。本文件各夹具裸日期都在 2026 年 1 月,英超
# 推导即 2025/2026——与 SEASON 相同,但保留两个名字:SEASON 标注"显式
# 指定过赛季"的夹具,DERIVED 标注"由日期推导"的夹具,读者不用倒推。
DEFAULT_SEASON = "2025/2026"


def _stats(conn, match_id, team_id, *, period="All", **fields):
    conn.execute(
        "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
        " VALUES (?, ?, ?, 0, ?)",
        (match_id, team_id, period, json.dumps(fields)),
    )


def _shot(conn, match_id, team_id, situation, xg, *, player_id="pX"):
    conn.execute(
        "INSERT INTO fact_shotmap (Match_ID, Player_ID, Team_ID, Minute, Period,"
        " X_Coord, Y_Coord, xG, xGOT, Situation, Outcome, Shot_Type)"
        " VALUES (?, ?, ?, 10, 'FirstHalf', 90.0, 40.0, ?, NULL, ?, 'Goal', 'RightFoot')",
        (match_id, player_id, team_id, xg, situation),
    )


@pytest.fixture
def style_league(data_dir):
    """英超三队(1001/1002/1003)各 5 场近赛,风格互不相同,供象限/来源拆解测试。

    1001(高控球高快攻):poss 高、fastbreak 射门占比高、xg-for/against 都有数据。
    1002(低控球低快攻,且 expected_goals 缺失 → xg-for-against 视角该队应为 None)。
    1003(样本只有 2 场,验证"每队独立窗口"不会被 1001/1002 的 5 场拖累)。
    """
    conn = connect_rw("core")
    seed_core_schema(conn)
    for i in range(5):
        mid = 8100 + i
        insert_match(conn, mid, league_id=LEAGUE, season="2025/2026",
                     date=f"2026-01-{10+i:02d}", home_id=1001, away_id=1002,
                     home="队A", away="队B", status="Finish", home_score=1, away_score=0)
        _stats(conn, mid, 1001, BallPossesion=60.0, expected_goals=2.0,
               accurate_crosses=5.0, touches_opp_box=20.0)
        _stats(conn, mid, 1002, BallPossesion=40.0,
               accurate_crosses=2.0, touches_opp_box=10.0)  # 故意不给 expected_goals
        # 1001 反击射门占多数,1002 全是运动战
        _shot(conn, mid, 1001, "FastBreak", 0.3, player_id=f"p1001-{i}a")
        _shot(conn, mid, 1001, "RegularPlay", 0.1, player_id=f"p1001-{i}b")
        _shot(conn, mid, 1002, "RegularPlay", 0.2, player_id=f"p1002-{i}")

    for i in range(2):
        mid = 8200 + i
        insert_match(conn, mid, league_id=LEAGUE, season="2025/2026",
                     date=f"2026-02-{10+i:02d}", home_id=1003, away_id=1001,
                     home="队C", away="队A", status="Finish", home_score=0, away_score=1)
        _stats(conn, mid, 1003, BallPossesion=50.0, expected_goals=1.0)
        _stats(conn, mid, 1001, BallPossesion=55.0, expected_goals=1.5)

    conn.commit()
    conn.close()
    yield


class TestLeagueStyleViews:
    def test_three_views_returned_with_labels(self, style_league):
        conn = connect_rw("core")
        views = q.league_style_views(conn, LEAGUE, SEASON, "2026-01-20", window=5)
        assert [v["id"] for v in views] == ["poss-fastbreak", "cross-box", "xg-for-against"]
        pf = views[0]
        assert pf["x_label"] == "控球率 %"
        assert pf["quadrants"] == ["既控又快", "阵地控球", "纯反击型", "被动型"]

    def test_poss_fastbreak_values(self, style_league):
        conn = connect_rw("core")
        views = q.league_style_views(conn, LEAGUE, SEASON, "2026-01-20", window=5)
        pf = {p["team_id"]: p for p in views[0]["points"]}
        assert pf[1001]["x"] == 60.0
        assert pf[1001]["y"] == 50.0  # 每场 1 FastBreak + 1 RegularPlay,5/10 = 50%
        assert pf[1002]["y"] == 0.0  # 有射门但全非反击 —— 真实 0,不是缺失

    def test_xg_for_against_missing_expected_goals_is_none_not_zero(self, style_league):
        """1002 从未写入 expected_goals —— 该视角必须是 None,不能补 0(补 0 会把
        没数据的球队画成全联赛防守最好)。"""
        conn = connect_rw("core")
        views = q.league_style_views(conn, LEAGUE, SEASON, "2026-01-20", window=5)
        xg_view = {p["team_id"]: p for p in views[2]["points"]}
        assert xg_view[1001]["x"] == 2.0
        assert xg_view[1002]["x"] is None

    def test_per_team_independent_window_not_shared_match_dates(self, style_league):
        """1003 只打了 2 场(对手都是 1001),1001 打了 7 场 —— 1001 的近 5 场窗口
        必须是它自己最近的 5 场(含跟 1003 的两场),不是与 1003 共享的比赛集合。"""
        conn = connect_rw("core")
        views = q.league_style_views(conn, LEAGUE, SEASON, "2026-02-15", window=5)
        pf = {p["team_id"]: p for p in views[0]["points"]}
        # 1001 近 5 场:2026-02-11、02-10、01-14、01-13、01-12 —— 控球率是
        # (55+55+60+60+60)/5 = 58.0,不是全部 7 场的平均
        assert pf[1001]["x"] == 58.0
        assert pf[1003]["x"] == 50.0

    def test_direction_semantics_propagated_and_quadrant_labels_correct(self, style_league):
        """xg-for-against 的 y 轴(让出 xG)是"越低越好"——这个方向语义必须端到端
        传播(query 返回的 dict 必须带 y_lower_is_better),且象限标签数组本身要按
        "x 好/y 好"的真实组合写,不能假定"y 高 = 好"。

        用真值表逐一核对(x=创造xG 越高越好,y=让出xG 越低越好):
          创造多(x高)+ 让出少(y低,方向好) → 两头都强
          创造多(x高)+ 让出多(y高,方向差) → 对攻型
          创造少(x低)+ 让出少(y低,方向好) → 守强攻弱
          创造少(x低)+ 让出多(y高,方向差) → 两头都弱
        这与 quadrants 数组按 [x高y高, x高y低, x低y高, x低y低](原始高低,不是好坏)
        的既有下标约定必须一致换算:index0=x高y高=对攻型,index1=x高y低=两头都强,
        index2=x低y高=两头都弱,index3=x低y低=守强攻弱。
        """
        conn = connect_rw("core")
        views = q.league_style_views(conn, LEAGUE, SEASON, "2026-01-20", window=5)
        xg_view = views[2]
        assert xg_view["id"] == "xg-for-against"
        assert xg_view["y_lower_is_better"] is True
        # 其余两个视角的 y 轴本来就是"越高越偏向该风格",不需要反转
        assert views[0].get("y_lower_is_better", False) is False
        assert views[1].get("y_lower_is_better", False) is False
        assert xg_view["quadrants"] == ["对攻型", "两头都强", "两头都弱", "守强攻弱"]


class TestSeasonScoping:
    """2026-08-25 真实事故回归:富勒姆 vs 切尔西「分析」tab 的风格象限画出了
    31 支球队(英超单赛季只有 20 支)——League_ID 跨赛季持久,早年打过这个
    联赛、此后已降级再没打过顶级联赛的球队,只要有一场历史记录早于
    before_date 就会被当前赛季的散点图选中。必须同时按 League_ID 与 Season
    过滤,已降级球队的陈旧历史记录不能出现在当前赛季的联赛画像里。"""

    def test_relegated_team_from_prior_season_excluded_from_current_season_scatter(
        self, data_dir
    ):
        conn = connect_rw("core")
        seed_core_schema(conn)
        # 1001/1002 本赛季(2026/2027)近期打过球——应该出现。
        insert_match(conn, 9600, league_id=LEAGUE, season="2026/2027",
                     date="2026-08-10", home_id=1001, away_id=1002,
                     home="队A", away="队B", status="Finish", home_score=1, away_score=0)
        _stats(conn, 9600, 1001, BallPossesion=60.0)
        _stats(conn, 9600, 1002, BallPossesion=40.0)
        # 9999 是上赛季(2025/2026)打过英超、本赛季已降级的球队——它在
        # before_date 之前确实有一场历史记录,若只按 League_ID 过滤会被
        # ROW_NUMBER 选中当作"它自己最近的一场",不该出现在本赛季画像里。
        insert_match(conn, 9601, league_id=LEAGUE, season="2025/2026",
                     date="2026-05-01", home_id=9999, away_id=1002,
                     home="已降级队", away="队B", status="Finish",
                     home_score=0, away_score=2)
        _stats(conn, 9601, 9999, BallPossesion=35.0)
        conn.commit()

        views = q.league_style_views(conn, LEAGUE, "2026/2027", "2026-08-20", window=5)
        team_ids = {p["team_id"] for p in views[0]["points"]}
        assert 1001 in team_ids
        assert 9999 not in team_ids

    def test_league_style_views_team_count_matches_current_season_roster_not_full_history(
        self, data_dir
    ):
        """更贴近真实场景:同一个 League_ID 横跨 3 个赛季,每季各自 20 支球队
        (含跨赛季部分重叠),只有"最新赛季"入选;换个更早的赛季查询,结果换成
        那一季自己的球队,不是三季的并集。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        # 每季日期取该赛季真实存在的月份(2026-08-25 起触发器校验
        # Season 与日期一致,不能再用"三个赛季共用 2026 年头三个月"的假日期)
        seasons = ["2024/2025", "2025/2026", "2026/2027"]
        season_month = {"2024/2025": "2025-01", "2025/2026": "2026-01",
                        "2026/2027": "2026-08"}
        mid = 9700
        for si, season in enumerate(seasons):
            for i in range(20):
                home_id = 100 + si * 100 + i
                away_id = 100 + si * 100 + (i + 1) % 20
                insert_match(conn, mid, league_id=LEAGUE, season=season,
                             date=f"{season_month[season]}-{10 + (i % 15):02d}",
                             home_id=home_id, away_id=away_id,
                             home=f"队{home_id}", away=f"队{away_id}",
                             status="Finish", home_score=1, away_score=0)
                _stats(conn, mid, home_id, BallPossesion=50.0)
                _stats(conn, mid, away_id, BallPossesion=50.0)
                mid += 1
        conn.commit()

        latest = q.league_style_views(conn, LEAGUE, "2026/2027", "2026-12-31", window=5)
        latest_ids = {p["team_id"] for p in latest[0]["points"]}
        assert len(latest_ids) == 20
        assert all(300 <= tid < 320 for tid in latest_ids)  # 只有第 3 季(si=2)的球队

        earliest = q.league_style_views(conn, LEAGUE, "2024/2025", "2026-12-31", window=5)
        earliest_ids = {p["team_id"] for p in earliest[0]["points"]}
        assert len(earliest_ids) == 20
        assert all(100 <= tid < 120 for tid in earliest_ids)  # 只有第 1 季(si=0)的球队


class TestFastbreakShare:
    def test_team_with_no_shots_absent_from_dict(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        insert_match(conn, 8300, league_id=LEAGUE, date="2026-01-10",
                     home_id=1001, away_id=1002, status="Finish", home_score=0, away_score=0)
        conn.commit()
        out = q._fastbreak_share_by_team(conn, LEAGUE, DEFAULT_SEASON, "2026-01-20", 5)
        assert 1001 not in out


class TestTeamWindowBounds:
    def test_returns_real_date_range(self, style_league):
        conn = connect_rw("core")
        b = q.team_window_bounds(conn, 1001, LEAGUE, SEASON, "2026-01-20", window=5)
        assert b == {"matches": 5, "from": "2026-01-10", "to": "2026-01-14"}

    def test_partial_history_returns_actual_count_not_none(self, style_league):
        conn = connect_rw("core")
        b = q.team_window_bounds(conn, 1003, LEAGUE, SEASON, "2026-02-15", window=5)
        assert b == {"matches": 2, "from": "2026-02-10", "to": "2026-02-11"}

    def test_no_history_returns_none(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        conn.commit()
        assert q.team_window_bounds(conn, 9999, LEAGUE, DEFAULT_SEASON, "2026-01-20") is None

    def test_same_calendar_date_ties_broken_by_kickoff_then_match_id(self, data_dir):
        """两条历史记录日期字符串相同(测试用极端场景,隔离排序逻辑本身)——
        仅靠 `ORDER BY Date DESC` 无法确定"谁更晚",结果依赖 SQLite 内部
        扫描顺序而不是查询语义保证的确定性。必须先比 kickoff_at_utc,
        窗口选择才不会随查询计划漂移。用两场射门数不同的比赛直接观察
        "被选中的到底是哪一场"。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        insert_match(conn, 9800, league_id=LEAGUE, date="2026-01-10",
                     home_id=1001, away_id=2001, home="队A", away="对手甲",
                     status="Finish", home_score=1, away_score=0,
                     kickoff_at_utc="2026-01-10T08:00:00Z")
        insert_match(conn, 9801, league_id=LEAGUE, date="2026-01-10",
                     home_id=1001, away_id=2002, home="队A", away="对手乙",
                     status="Finish", home_score=2, away_score=0,
                     kickoff_at_utc="2026-01-10T20:00:00Z")
        # 两场射门数不同,可以直接看出 window=1 选中了哪一场
        for _ in range(3):
            _shot(conn, 9800, 1001, "RegularPlay", 0.1)
        for _ in range(7):
            _shot(conn, 9801, 1001, "RegularPlay", 0.1)
        conn.commit()
        # window=1:必须选到 kickoff 更晚(20:00,7 脚射门)的那场,
        # 不能因为 Date 字符串相同就选中 08:00(3 脚射门)那场。
        b = q.team_window_bounds(conn, 1001, LEAGUE, DEFAULT_SEASON, "2026-01-11", window=1)
        assert b == {"matches": 1, "from": "2026-01-10", "to": "2026-01-10"}
        sources = q.team_attack_sources(conn, 1001, LEAGUE, DEFAULT_SEASON, "2026-01-11", window=1)
        assert sources[0]["shots"] == 7

    def test_same_day_later_kickoff_is_excluded_as_future_not_history(self, data_dir):
        """目标比赛与另一场"已完赛"记录是同一自然日,但那场的精确开球时刻
        晚于目标比赛——用日期字符串比较会把它当"更早的历史"混进窗口
        (Date 相同,不满足 Date<Date,天然被排除,这一步本身是对的;但如果
        换算成完整时间戳比较,必须确认没有反而变成误纳入)。用精确 kickoff
        比较必须仍然排除它,窗口只剩另一场真正更早的比赛。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        # 真正的历史:前一天
        insert_match(conn, 9810, league_id=LEAGUE, date="2026-01-09",
                     home_id=1001, away_id=2001, home="队A", away="对手甲",
                     status="Finish", home_score=1, away_score=0,
                     kickoff_at_utc="2026-01-09T18:00:00Z")
        # 同一自然日但开球晚于目标比赛(目标定在 2026-01-10T12:00:00Z)——
        # 即便状态是 Finish(数据可能有误标),精确时间上仍晚于目标,不该算历史。
        insert_match(conn, 9811, league_id=LEAGUE, date="2026-01-10",
                     home_id=1001, away_id=2002, home="队A", away="对手乙",
                     status="Finish", home_score=2, away_score=0,
                     kickoff_at_utc="2026-01-10T20:00:00Z")
        for _ in range(3):
            _shot(conn, 9810, 1001, "RegularPlay", 0.1)
        for _ in range(9):
            _shot(conn, 9811, 1001, "RegularPlay", 0.1)
        conn.commit()
        b = q.team_window_bounds(conn, 1001, LEAGUE, DEFAULT_SEASON, "2026-01-10T12:00:00Z", window=5)
        assert b == {"matches": 1, "from": "2026-01-09", "to": "2026-01-09"}
        sources = q.team_attack_sources(conn, 1001, LEAGUE, DEFAULT_SEASON, "2026-01-10T12:00:00Z", window=5)
        assert sources[0]["shots"] == 3  # 只有前一天那场,晚开球的 9 脚射门不能混进来


class TestTeamAttackSources:
    def test_sorted_by_shots_desc_with_pct_and_xg(self, style_league):
        conn = connect_rw("core")
        out = q.team_attack_sources(conn, 1001, LEAGUE, SEASON, "2026-01-20", window=5)
        by_label = {r["label"]: r for r in out}
        assert set(by_label) == {"反击", "运动战"}  # 两者场次并列(各 5),不断言谁在前
        assert by_label["反击"]["shots"] == 5
        assert by_label["运动战"]["shots"] == 5
        total_shots = sum(r["shots"] for r in out)
        assert total_shots == 10  # 5 场 × (1 FastBreak + 1 RegularPlay)
        pct_sum = round(sum(r["shot_pct"] for r in out), 1)
        assert pct_sum == 100.0
        for r in out:
            assert r["xg"] is not None  # 每来源全部射门都有 xG

    def test_no_shots_returns_empty_list(self, style_league):
        conn = connect_rw("core")
        out = q.team_attack_sources(conn, 1003, LEAGUE, SEASON, "2026-01-20", window=5)
        assert out == []

    def test_partial_xg_in_bucket_gives_none_not_underestimate(self, data_dir):
        """同一来源里有的射门有 xG 有的没有 —— 只对有值的求和会低估,必须诚实给 None。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        insert_match(conn, 8400, league_id=LEAGUE, date="2026-01-10",
                     home_id=1001, away_id=1002, status="Finish", home_score=0, away_score=0)
        conn.execute(
            "INSERT INTO fact_shotmap (Match_ID, Player_ID, Team_ID, Minute, Period,"
            " X_Coord, Y_Coord, xG, xGOT, Situation, Outcome, Shot_Type)"
            " VALUES (8400, 'p1', 1001, 10, 'FirstHalf', 90.0, 40.0, 0.3, NULL, 'RegularPlay', 'Goal', 'RightFoot')"
        )
        conn.execute(
            "INSERT INTO fact_shotmap (Match_ID, Player_ID, Team_ID, Minute, Period,"
            " X_Coord, Y_Coord, xG, xGOT, Situation, Outcome, Shot_Type)"
            " VALUES (8400, 'p2', 1001, 20, 'FirstHalf', 90.0, 40.0, NULL, NULL, 'RegularPlay', 'Miss', 'RightFoot')"
        )
        conn.commit()
        out = q.team_attack_sources(conn, 1001, LEAGUE, DEFAULT_SEASON, "2026-01-20", window=5)
        assert len(out) == 1
        assert out[0]["shots"] == 2
        assert out[0]["xg"] is None

    def test_unmapped_situation_falls_back_to_raw_value(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        insert_match(conn, 8500, league_id=LEAGUE, date="2026-01-10",
                     home_id=1001, away_id=1002, status="Finish", home_score=0, away_score=0)
        _shot(conn, 8500, 1001, "SomeNewSituation", 0.2)
        conn.commit()
        out = q.team_attack_sources(conn, 1001, LEAGUE, DEFAULT_SEASON, "2026-01-20", window=5)
        assert out[0]["label"] == "SomeNewSituation"
