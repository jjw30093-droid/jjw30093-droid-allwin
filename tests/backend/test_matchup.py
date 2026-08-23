"""backend/queries/matchup.py 测试:图4 本场攻防对位聚合。"""

from __future__ import annotations

import json

from backend.db.connections import connect_rw
from backend.queries.matchup import team_matchup_profile
from tests.backend.coreseed import insert_match, seed_core_schema

LEAGUE = 47
TEAM = 5001


def _stats(conn, match_id, team_id, **fields):
    conn.execute(
        "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
        " VALUES (?, ?, 'All', 0, ?)",
        (match_id, team_id, json.dumps(fields)),
    )


def _shot(conn, match_id, team_id, situation, xg, *, x=None, y=None):
    conn.execute(
        "INSERT INTO fact_shotmap (Match_ID, Player_ID, Team_ID, Minute, Period, Situation, xG, X_Coord, Y_Coord)"
        " VALUES (?, 'p1', ?, 10, 'FirstHalf', ?, ?, ?, ?)",
        (match_id, team_id, situation, xg, x, y),
    )


# 标准 FIFA 禁区内一点(与 backend/queries/matchup.py 的 _BOX_X_MIN/_BOX_Y_MIN/
# _BOX_Y_MAX 同一套几何,X_Coord>=88.5、Y_Coord 在 [13.84, 54.16] 之间)。
_BOX_POINT = {"x": 95.0, "y": 34.0}
_OUTSIDE_BOX_POINT = {"x": 60.0, "y": 34.0}


def _seed_window(conn, team_id, *, n=10):
    for j in range(n):
        mid = team_id * 100 + j
        opp = 9600 + j
        insert_match(conn, mid, league_id=LEAGUE, date=f"2025-01-{10+j:02d}",
                     home_id=team_id, away_id=opp, home="队A", away="对手",
                     status="Finish", home_score=1, away_score=0,
                     kickoff_at_utc=f"2025-01-{10+j:02d}T12:00:00Z")
        _stats(conn, mid, team_id, shots_inside_box=6.0)
        _stats(conn, mid, opp, shots_inside_box=4.0)
        # 己方:每场 2 个运动战射门(xG 0.3 each),1 个反击射门
        _shot(conn, mid, team_id, "RegularPlay", 0.3)
        _shot(conn, mid, team_id, "RegularPlay", 0.3)
        _shot(conn, mid, team_id, "FastBreak", 0.5)
        # 对手(被让出):每场 1 个定位球射门,1 个角球射门
        _shot(conn, mid, opp, "SetPiece", 0.1)
        _shot(conn, mid, opp, "FromCorner", 0.15)


class TestTeamMatchupProfile:
    def test_own_and_conceded_split_correctly_by_situation(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _seed_window(conn, TEAM, n=10)
        conn.commit()

        result = team_matchup_profile(conn, TEAM, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
        assert result["tier"] == "venue_full"
        by_key = {s["key"]: s for s in result["situations"]}

        regular = by_key["regular_play"]
        assert regular["own_shots_pg"] == 2.0
        assert regular["own_xg_pg"] == 0.6
        assert regular["own_xg_complete"] is True
        assert regular["conceded_shots_pg"] == 0.0  # 对手没在运动战射过门

        fast_break = by_key["fast_break"]
        assert fast_break["own_shots_pg"] == 1.0
        assert fast_break["own_xg_pg"] == 0.5

        set_piece = by_key["set_piece"]  # SetPiece + FromCorner 合并
        assert set_piece["own_shots_pg"] == 0.0
        assert set_piece["conceded_shots_pg"] == 2.0
        assert round(set_piece["conceded_xg_pg"], 2) == 0.25

        box = by_key["box_shots"]
        assert box["own_shots_pg"] == 6.0
        assert box["conceded_shots_pg"] == 4.0
        # 射门次数来自官方 shots_inside_box 字段;fixture 里没有任何射门带
        # 坐标,坐标法(own_xg_pg)因此没有干净场次可用,如实回退 None——
        # 不代表"禁区内射门不产出 xG"(见下面 test_box_xg_from_coordinates)。
        assert box["own_xg_pg"] is None
        assert box["own_xg_complete"] is False

    def test_no_history_returns_unavailable_with_empty_situations(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        result = team_matchup_profile(conn, 66666, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
        assert result["tier"] == "unavailable"
        assert result["matches"] == 0
        for s in result["situations"]:
            assert s["own_shots_pg"] is None
            assert s["conceded_shots_pg"] is None

    def test_partial_xg_drops_the_contaminated_match_and_averages_the_rest(self, data_dir):
        """2026-08-23 站长决定(反馈自 miaomiaodi.vip 生产实测:江原FC 客场
        运动战,52 脚射门里只有 1 脚缺 xG,整格却因此显示"数据不足")——
        单场比赛只要有 1 脚射门缺 xG,就把那一场整场从 xG 统计里剔除,不是
        丢那一脚"部分已知"凑合计,也不是让整个情境类型直接判"不完整"。
        5 场里 j=0 那场缺 xG,应该被整场剔除,剩下 4 场(j=1..4,each 0.3)
        重新算出真实均值,而不是回退成 None。射门次数(shots_pg)不受这次
        剔除影响,仍是全量 5 场的次数。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        for j in range(5):
            mid = TEAM * 10 + j
            opp = 9700 + j
            insert_match(conn, mid, league_id=LEAGUE, date=f"2025-01-{10+j:02d}",
                         home_id=TEAM, away_id=opp, home="队A", away="对手",
                         status="Finish", home_score=1, away_score=0,
                         kickoff_at_utc=f"2025-01-{10+j:02d}T12:00:00Z")
            _shot(conn, mid, TEAM, "RegularPlay", 0.3 if j != 0 else None)
        conn.commit()

        result = team_matchup_profile(conn, TEAM, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
        regular = next(s for s in result["situations"] if s["key"] == "regular_play")
        assert regular["own_shots_pg"] == 1.0  # 5 脚射门/5场,不受 xG 剔除影响
        assert regular["own_xg_complete"] is True
        assert regular["own_xg_matches"] == 4  # 5 场剔除 1 场污染场次,剩 4 场干净
        assert regular["own_xg_pg"] == 0.3  # (0.3*4)/4,不是 (0.3*4)/5

    def test_all_matches_contaminated_still_reports_insufficient(self, data_dir):
        """反例对照:如果窗口里每一场都至少缺 1 脚 xG(干净场次数=0),
        剔除污染场次后没有剩余场次可用,必须诚实回退 None/数据不足,
        不能凭空生成一个基于 0 场的均值。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        for j in range(5):
            mid = TEAM * 10 + j
            opp = 9700 + j
            insert_match(conn, mid, league_id=LEAGUE, date=f"2025-01-{10+j:02d}",
                         home_id=TEAM, away_id=opp, home="队A", away="对手",
                         status="Finish", home_score=1, away_score=0,
                         kickoff_at_utc=f"2025-01-{10+j:02d}T12:00:00Z")
            _shot(conn, mid, TEAM, "RegularPlay", None)  # 每场都缺
        conn.commit()

        result = team_matchup_profile(conn, TEAM, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
        regular = next(s for s in result["situations"] if s["key"] == "regular_play")
        assert regular["own_xg_complete"] is False
        assert regular["own_xg_pg"] is None
        assert regular["own_xg_matches"] is None

    def test_box_xg_from_coordinates(self, data_dir):
        """2026-08-23 FotMob 官方安卓包核实:FotMob 自己也没有"禁区内 xG"这个
        指标,但坐标法(标准 FIFA 禁区几何)已用 25,984 个真实队场样本对官方
        shots_inside_box 计数验证 97.97% 完全一致——因此 allwin 改用坐标法
        从 fact_shotmap 聚合禁区内 xG,与官方射门次数字段并行、互不覆盖。
        这里验证:禁区内(_BOX_POINT)的射门计入 xG,禁区外(_OUTSIDE_BOX_POINT)
        的射门(哪怕 Situation 也是 RegularPlay)不计入。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        for j in range(5):
            mid = TEAM * 10 + j
            opp = 9700 + j
            insert_match(conn, mid, league_id=LEAGUE, date=f"2025-01-{10+j:02d}",
                         home_id=TEAM, away_id=opp, home="队A", away="对手",
                         status="Finish", home_score=1, away_score=0,
                         kickoff_at_utc=f"2025-01-{10+j:02d}T12:00:00Z")
            _shot(conn, mid, TEAM, "RegularPlay", 0.4, **_BOX_POINT)
            _shot(conn, mid, TEAM, "RegularPlay", 9.0, **_OUTSIDE_BOX_POINT)
            _stats(conn, mid, TEAM, shots_inside_box=1.0)
        conn.commit()

        result = team_matchup_profile(conn, TEAM, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
        box = next(s for s in result["situations"] if s["key"] == "box_shots")
        assert box["own_shots_pg"] == 1.0  # 官方字段,不受坐标法影响
        assert box["own_xg_complete"] is True
        assert box["own_xg_matches"] == 5
        assert box["own_xg_pg"] == 0.4  # 只计入禁区内那一脚,禁区外的 9.0 被排除
        assert box["comparison_metric"] == "xg"
        assert box["own_comparison_value"] == 0.4


def _seed_league_baseline_team(conn, team_id, *, regular_xg_per_shot=0.3, n=10, start_id=8000):
    """造一支"干净"的联赛基准对照队:真实同主场历史(venue_full),
    每场固定的运动战/反击/定位球产出与被让出,用于喂 league_situation_baseline。"""
    for j in range(n):
        mid = team_id * 100 + j
        opp = start_id + j
        insert_match(conn, mid, league_id=LEAGUE, date=f"2025-01-{10+j:02d}",
                     home_id=team_id, away_id=opp, home=f"基准队{team_id}", away="对手",
                     status="Finish", home_score=1, away_score=0,
                     kickoff_at_utc=f"2025-01-{10+j:02d}T12:00:00Z")
        _shot(conn, mid, team_id, "RegularPlay", regular_xg_per_shot)
        _shot(conn, mid, opp, "SetPiece", 0.1)
        _shot(conn, mid, opp, "RegularPlay", 0.2)  # 对手也踢运动战,喂"让出"侧样本


def _seed_league_baseline_team_partial_tier(conn, team_id, *, regular_xg_per_shot=0.3, n=7, start_id=8000):
    """跟 `_seed_league_baseline_team` 一样但只给 DEFAULT_MIN_N<=n<DEFAULT_MAX_N
    场主场历史(默认 7,落在 [5,10) 区间),真实 tier=venue_partial——
    venue_partial 是自己的合法档位(样本 >= min_n 但没凑满 max_n),不是
    mixed(< min_n 才会退回混合主客场)。用于验证 venue_partial 目标只能用
    venue_partial 参考队,不能跟 venue_full 参考队混进同一个分布。"""
    for j in range(n):
        mid = team_id * 100 + j
        opp = start_id + j
        insert_match(conn, mid, league_id=LEAGUE, date=f"2025-01-{10+j:02d}",
                     home_id=team_id, away_id=opp, home=f"基准队{team_id}", away="对手",
                     status="Finish", home_score=1, away_score=0,
                     kickoff_at_utc=f"2025-01-{10+j:02d}T12:00:00Z")
        _shot(conn, mid, team_id, "RegularPlay", regular_xg_per_shot)
        _shot(conn, mid, opp, "SetPiece", 0.1)
        _shot(conn, mid, opp, "RegularPlay", 0.2)


class TestLeagueSituationBaseline:
    """验收返工二:关键对位排序此前直接用不同 Situation 的原始 xG 相加,
    运动战天然射门多、xG 基数大,永远排第一。这里验证真正的基准计算:
    同联赛、同主客场景、同 Situation 的己方产出/对手让出各自的联赛均值,
    且必须 tier-conditioned(独立复核第二轮:venue_full 目标只能用
    venue_full 参考队,venue_partial 目标只能用 venue_partial 参考队,
    不能混进同一个分布——venue_full/venue_partial 虽然都算"该队自己真的
    有足够历史",但样本量本身不同,拿它们直接混算基准仍然是口径不纯)。"""

    def test_baseline_requires_exact_tier_match_not_just_compatible_tiers(self, data_dir):
        """验收返工二(P1):venue_full 目标的基准不得混入 venue_partial
        参考队,反之亦然——这是这次独立复核明确否决的旧行为
        (旧版 _COMPATIBLE_TIERS 只要求"in (venue_full, venue_partial)",
        允许两档混进同一分布)。"""
        from backend.queries.matchup import league_situation_baseline

        conn = connect_rw("core")
        seed_core_schema(conn)
        # 5 支 venue_full 参考队(每脚 xG=0.5)
        for i, tid in enumerate(range(9501, 9506)):
            _seed_league_baseline_team(conn, tid, regular_xg_per_shot=0.5, start_id=9000 + i * 100)
        # 5 支 venue_partial 参考队(每脚 xG=9.0,刻意设极端值,若被混入
        # venue_full 的基准分布会把均值严重拉偏)。start_id 从 9700 起跳,
        # 避开 9601~9605 自身的 team_id 区间——之前用 9200+i*100 时,
        # i=4(tid=9605,start_id=9600)的对手区间 9600~9606 会把自己
        # (9605)也当成对手,同一场比赛左右两脚都算成它自己的运动战射门,
        # 场均被多算 0.2,是纯测试 fixture 的自碰撞,不是被测代码的缺陷。
        for i, tid in enumerate(range(9601, 9606)):
            _seed_league_baseline_team_partial_tier(conn, tid, regular_xg_per_shot=9.0, start_id=9700 + i * 100)
        conn.commit()

        full_baseline = league_situation_baseline(
            conn, LEAGUE, "2025-02-01T00:00:00Z", is_home=True, tier="venue_full",
        )
        assert full_baseline["regular_play"]["own_sample"] == 5
        assert round(full_baseline["regular_play"]["own_avg"], 2) == 0.5

        partial_baseline = league_situation_baseline(
            conn, LEAGUE, "2025-02-01T00:00:00Z", is_home=True, tier="venue_partial",
        )
        assert partial_baseline["regular_play"]["own_sample"] == 5
        assert round(partial_baseline["regular_play"]["own_avg"], 2) == 9.0

    def test_baseline_none_when_sample_below_minimum(self, data_dir):
        from backend.queries.matchup import MIN_BASELINE_SAMPLE, league_situation_baseline

        conn = connect_rw("core")
        seed_core_schema(conn)
        # 只造 3 支基准队(< MIN_BASELINE_SAMPLE),不足以给出可靠基准
        assert MIN_BASELINE_SAMPLE >= 4
        for i, tid in enumerate(range(9201, 9204)):
            _seed_league_baseline_team(conn, tid, regular_xg_per_shot=0.3, start_id=8300 + i * 100)
        conn.commit()

        baseline = league_situation_baseline(
            conn, LEAGUE, "2025-02-01T00:00:00Z", is_home=True, tier="venue_full",
        )
        assert baseline["regular_play"]["own_avg"] is None
        assert baseline["regular_play"]["own_sample"] == 3

    def test_mixed_tier_team_excluded_from_baseline(self, data_dir):
        """跟 venue_baseline 的规则一致:mixed 档位(自己同场景样本不足、
        退回混合主客场)不得计入联赛基准,不论目标是 venue_full 还是
        venue_partial。"""
        from backend.queries.matchup import league_situation_baseline

        conn = connect_rw("core")
        seed_core_schema(conn)
        for i, tid in enumerate(range(9301, 9306)):
            _seed_league_baseline_team(conn, tid, regular_xg_per_shot=0.3, start_id=8600 + i * 100)
        # 污染队:主场历史只有 2 场(< DEFAULT_MIN_N),真实 tier=mixed,
        # 运动战场均 xG 故意设成极端值 9.0
        pollutant = 9399
        for j in range(2):
            mid = pollutant * 100 + j
            insert_match(conn, mid, league_id=LEAGUE, date=f"2025-01-{10+j:02d}",
                         home_id=pollutant, away_id=8900 + j, home="污染队", away="对手",
                         status="Finish", home_score=1, away_score=0,
                         kickoff_at_utc=f"2025-01-{10+j:02d}T12:00:00Z")
            _shot(conn, mid, pollutant, "RegularPlay", 9.0)
        conn.commit()

        baseline = league_situation_baseline(
            conn, LEAGUE, "2025-02-01T00:00:00Z", is_home=True, tier="venue_full",
        )
        # 只有 5 支干净基准队参与,污染队被排除
        assert baseline["regular_play"]["own_sample"] == 5
        assert round(baseline["regular_play"]["own_avg"], 2) == 0.3

    def test_team_matchup_profile_carries_explicit_comparison_fields(self, data_dir):
        """验收返工二(P1):team_matchup_profile() 必须携带显式、无歧义的
        比较字段(comparison_metric/own_comparison_value/
        conceded_comparison_value/own_baseline_value/
        conceded_baseline_value/comparison_complete),不再用
        `own_xg_pg ?? own_shots_pg` 这种前端隐式量纲猜测,也不再用单一的
        `baseline_available` 布尔糊住"己方/对手基准是否各自齐全"这两件事。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        for i, tid in enumerate(range(9401, 9406)):
            _seed_league_baseline_team(conn, tid, regular_xg_per_shot=0.3, start_id=8800 + i * 100)
        # TEAM 自己也用同一个 helper 造窗口(而不是 _seed_window)——
        # _seed_window 故意让对手在运动战上零射门(用于测试
        # conceded_shots_pg==0 那条断言),会导致 conceded_xg_complete=False,
        # 这里要验证的是"双方数据都齐全时 comparison_complete=True"这条
        # 正向路径,换一个两侧都有真实运动战数据的 fixture。
        _seed_league_baseline_team(conn, TEAM, regular_xg_per_shot=0.4, start_id=9900)
        conn.commit()

        result = team_matchup_profile(conn, TEAM, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
        regular = next(s for s in result["situations"] if s["key"] == "regular_play")
        assert "own_baseline_avg" not in regular  # 旧的模糊字段必须已被替换
        assert "baseline_available" not in regular
        assert regular["comparison_metric"] == "xg"
        assert regular["own_comparison_value"] == regular["own_xg_pg"]
        assert regular["own_baseline_value"] is not None
        assert regular["comparison_complete"] is True

        box = next(s for s in result["situations"] if s["key"] == "box_shots")
        # 2026-08-23 起禁区内射门的 xG 改用坐标法,comparison_metric 恒为
        # "xg"(与其它三类一致);这份 fixture 没有坐标数据,own_comparison_value
        # 因此是 None,不再借用 own_shots_pg。
        assert box["comparison_metric"] == "xg"
        assert box["own_comparison_value"] is None

    def test_real_data_shape_incomplete_xg_never_falls_back_to_shots(self, data_dir):
        """真实数据复现(比赛 5868022 / 球队 10205,独立复核第二轮报告的
        原始反例):own_xg_complete=False 时,own_comparison_value 必须是
        None,不能悄悄退回 own_shots_pg 去跟 xG 口径的基准比。

        2026-08-23 起,单场缺 xG 只剔除那一场(见
        test_partial_xg_drops_the_contaminated_match_and_averages_the_rest),
        所以这里要真正触发"own_xg_complete=False"这条分支,必须让窗口里
        **每一场**都至少缺 1 脚 xG(剔除污染场次后干净场次数=0),不能再
        像旧版那样只让 1 场缺 xG(那样现在会被剔除后用剩下 9 场算出真实
        均值,不再是 incomplete)。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        for i, tid in enumerate(range(9701, 9706)):
            _seed_league_baseline_team(conn, tid, regular_xg_per_shot=0.3, start_id=9000 + i * 100)
        # TEAM 窗口:运动战射门次数很高,但每一场都至少有 1 脚缺 xG——
        # 剔除污染场次后干净场次数=0,own_xg_complete 必须保持 False。
        for j in range(10):
            mid = TEAM * 100 + j
            opp = 9800 + j
            insert_match(conn, mid, league_id=LEAGUE, date=f"2025-01-{10+j:02d}",
                         home_id=TEAM, away_id=opp, home="队A", away="对手",
                         status="Finish", home_score=1, away_score=0,
                         kickoff_at_utc=f"2025-01-{10+j:02d}T12:00:00Z")
            _shot(conn, mid, TEAM, "RegularPlay", 6.0)  # 极端大的"xG"次数级数值
            _shot(conn, mid, TEAM, "RegularPlay", None)  # 每一场都缺,无干净场次

        conn.commit()

        result = team_matchup_profile(conn, TEAM, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
        regular = next(s for s in result["situations"] if s["key"] == "regular_play")
        assert regular["own_xg_complete"] is False
        assert regular["own_xg_matches"] is None
        assert regular["own_shots_pg"] is not None and regular["own_shots_pg"] > 0
        assert regular["own_comparison_value"] is None  # 不得退回次数
        assert regular["comparison_complete"] is False

    def test_mixed_or_unavailable_target_gets_no_baseline_at_all(self, data_dir):
        """目标自己是 mixed/unavailable 时,不尝试给出任何 Situation 基准
        (不调用 league_situation_baseline,也不产生看似有效的基准数字)。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        for i, tid in enumerate(range(9901, 9906)):
            _seed_league_baseline_team(conn, tid, regular_xg_per_shot=0.3, start_id=9300 + i * 100)
        conn.commit()

        result = team_matchup_profile(conn, 88888, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
        assert result["tier"] == "unavailable"
        for s in result["situations"]:
            assert s["own_baseline_value"] is None
            assert s["conceded_baseline_value"] is None
            assert s["comparison_complete"] is False
