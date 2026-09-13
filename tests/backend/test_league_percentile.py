"""backend/queries/league_percentile.py 测试:批量联赛百分位画像。"""

from __future__ import annotations

import json

from backend.db.connections import connect_rw
from backend.queries.league_percentile import (
    GROUPS,
    league_metric_distribution,
    league_window_matches,
    match_data_profile,
)
from backend.queries.window import DEFAULT_MAX_N, DEFAULT_MIN_N, venue_window
from tests.backend.coreseed import insert_match, seed_core_schema

LEAGUE = 47


def _stats(conn, match_id, team_id, **fields):
    conn.execute(
        "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
        " VALUES (?, ?, 'All', 0, ?)",
        (match_id, team_id, json.dumps(fields)),
    )


def _seed_home_match(conn, mid, home_id, away_id, day, **home_fields):
    insert_match(
        conn, mid, league_id=LEAGUE, date=f"2025-01-{day:02d}",
        home_id=home_id, away_id=away_id, home=f"队{home_id}", away=f"队{away_id}",
        status="Finish", home_score=1, away_score=0,
        kickoff_at_utc=f"2025-01-{day:02d}T12:00:00Z",
    )
    _stats(conn, mid, home_id, **home_fields)


class TestLeagueWindowMatches:
    def test_matches_venue_window_per_team(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        team = 3001
        opponents = [4001 + i for i in range(12)]
        for i, opp in enumerate(opponents):
            _seed_home_match(conn, 5000 + i, team, opp, day=i + 1, expected_goals=1.0)
        conn.commit()

        before = "2025-02-01T00:00:00Z"
        batch = league_window_matches(conn, LEAGUE, before, venue="home", max_n=DEFAULT_MAX_N)
        expected = venue_window(conn, team, LEAGUE, before, is_home=True, max_n=DEFAULT_MAX_N, min_n=DEFAULT_MIN_N)

        assert sorted(batch[team]) == sorted(expected.match_ids)
        assert len(batch[team]) == DEFAULT_MAX_N  # 12 场历史,max_n=10 截断


class TestLeagueMetricDistribution:
    def test_own_avg_field_and_min_n_exclusion(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        # team A: 6 场主场,xg 递增 1..6 -> 均值 3.5,达到 min_n(5)
        team_a = 2001
        for i in range(6):
            _seed_home_match(conn, 6000 + i, team_a, 9000 + i, day=i + 1, expected_goals=float(i + 1))
        # team B: 只有 3 场主场 -> 样本不足,整队应被剔除出分布
        team_b = 2002
        for i in range(3):
            _seed_home_match(conn, 6100 + i, team_b, 9100 + i, day=i + 1, expected_goals=9.0)
        conn.commit()

        dist = league_metric_distribution(conn, LEAGUE, "2025-02-01T00:00:00Z", venue="home")
        assert team_a in dist
        assert dist[team_a]["xg"].value == 3.5
        assert dist[team_a]["xg"].matches_with_data == 6
        assert dist[team_a]["xg"].complete is True
        assert team_b not in dist

    def test_missing_value_excluded_not_zero(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        team = 2003
        for i in range(5):
            fields = {} if i == 0 else {"expected_goals": 2.0}
            _seed_home_match(conn, 6200 + i, team, 9200 + i, day=i + 1, **fields)
        conn.commit()

        dist = league_metric_distribution(conn, LEAGUE, "2025-02-01T00:00:00Z", venue="home")
        row = dist[team]["xg"]
        # 5 场里 1 场缺 xg,均值只用 4 场有值的场次算,不把缺失当 0 拉低均值。
        assert row.matches_with_data == 4
        assert row.value == 2.0
        assert row.complete is False

    def test_opponent_field_pulls_opponent_row_not_own(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        team = 2004
        for i in range(5):
            mid = 6300 + i
            opp = 9300 + i
            insert_match(conn, mid, league_id=LEAGUE, date=f"2025-01-{i+1:02d}",
                         home_id=team, away_id=opp, home=f"队{team}", away=f"队{opp}",
                         status="Finish", home_score=1, away_score=0,
                         kickoff_at_utc=f"2025-01-{i+1:02d}T12:00:00Z")
            _stats(conn, mid, team, total_shots=1.0)  # 自己的射门(不应被读到 shots_faced 里)
            _stats(conn, mid, opp, total_shots=9.0)   # 对手射门 -> 这才是 shots_faced
        conn.commit()

        dist = league_metric_distribution(conn, LEAGUE, "2025-02-01T00:00:00Z", venue="home")
        assert dist[team]["shots_faced"].value == 9.0

    def test_ratio_field_pairs_same_match_and_skips_zero_denominator(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        team = 2005
        for i in range(5):
            mid = 6400 + i
            opp = 9400 + i
            insert_match(conn, mid, league_id=LEAGUE, date=f"2025-01-{i+1:02d}",
                         home_id=team, away_id=opp, home=f"队{team}", away=f"队{opp}",
                         status="Finish", home_score=1, away_score=0,
                         kickoff_at_utc=f"2025-01-{i+1:02d}T12:00:00Z")
            if i == 0:
                _stats(conn, mid, team, accurate_passes=10, passes=0)  # 分母为 0,该场不计入配对
            else:
                _stats(conn, mid, team, accurate_passes=8, passes=10)  # 每场 80%
        conn.commit()

        dist = league_metric_distribution(conn, LEAGUE, "2025-02-01T00:00:00Z", venue="home")
        row = dist[team]["pass_completion"]
        assert row.value == 80.0
        assert row.matches_with_data == 4  # 4 场有效配对,分母为 0 那场被排除


class TestMatchDataProfile:
    def _seed_league(self, conn, *, values: dict[int, float], min_n=5):
        """给 `values` 里每支球队各造 min_n 场主场比赛,expected_goals=其分配的值。"""
        mid = 7000
        for team_id, v in values.items():
            for i in range(min_n):
                _seed_home_match(conn, mid, team_id, 9500 + mid, day=(mid % 27) + 1, expected_goals=v)
                mid += 1

    def test_percentile_direction_and_highlight_emitted(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        home_id, away_id = 3101, 3102
        # 6 支主场球队:home_id 的 xg 是最高的(压倒性差距)
        self._seed_league(conn, values={3103: 1.0, 3104: 1.2, 3105: 1.3, 3106: 1.4, 3107: 1.5, home_id: 6.0})
        # away 分布(客场):away_id 垫底
        for i, (tid, v) in enumerate({3203: 4.0, 3204: 4.2, 3205: 4.3, 3206: 4.4, 3207: 4.5, away_id: 0.5}.items()):
            for j in range(5):
                mid = 7100 + i * 10 + j
                insert_match(conn, mid, league_id=LEAGUE, date=f"2025-01-{j+1:02d}",
                             home_id=9600 + mid, away_id=tid, home="路人", away=f"队{tid}",
                             status="Finish", home_score=0, away_score=1,
                             kickoff_at_utc=f"2025-01-{j+1:02d}T12:00:00Z")
                _stats(conn, mid, tid, expected_goals=v)
        conn.commit()

        profile = match_data_profile(conn, LEAGUE, "2025-02-01T00:00:00Z", home_id, away_id)
        assert profile.home_available and profile.away_available

        attack_group = next(g for g in profile.groups if g.key == "attack")
        xg_metric = next(m for m in attack_group.metrics if m.key == "xg")
        assert xg_metric.home_percentile == 100  # home_id 的 6.0 是主场分布里最高的(xg 越高越好)
        assert xg_metric.away_percentile == 0  # away_id 的 0.5 是客场分布里最低的

        # home 100 对 away 0,差距远超 GAP_FLOOR(15),xg 应该出现在 highlights 里
        assert any(h.key == "xg" for h in profile.highlights)

    def test_unavailable_when_both_sides_lack_sample(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        # 两队自己都只有 2 场历史,不足 min_n(5)
        home_id, away_id = 3301, 3302
        for i in range(2):
            _seed_home_match(conn, 7300 + i, home_id, 9700 + i, day=i + 1, expected_goals=1.0)
            insert_match(conn, 7400 + i, league_id=LEAGUE, date=f"2025-01-{i+1:02d}",
                         home_id=9800 + i, away_id=away_id, home="路人", away="队",
                         status="Finish", home_score=0, away_score=1,
                         kickoff_at_utc=f"2025-01-{i+1:02d}T12:00:00Z")
        conn.commit()

        profile = match_data_profile(conn, LEAGUE, "2025-02-01T00:00:00Z", home_id, away_id)
        assert not profile.home_available and not profile.away_available
        assert profile.groups == []
        assert profile.unavailable_reason is not None

    def test_nearest_peers_ranked_by_group_percentile_gap(self, data_dir):
        """2026-09 第二轮:对标球队——站长要的不是排名,是"跟目标队组级
        百分位最接近的 2 支"。这里 shots=10×xg(完全同序),所以组级百分位
        就是单一 xg 百分位本身,方便手算期望值。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        home_id = 3401
        values = {3402: 1.0, 3403: 1.5, home_id: 6.0, 3404: 5.8, 3405: 5.5, 3406: 2.0, 3407: 0.5}
        mid = 8000
        for tid, xg in values.items():
            for i in range(5):
                mid += 1
                _seed_home_match(conn, mid, tid, 9900 + mid, day=(mid % 27) + 1,
                                  expected_goals=xg, total_shots=xg * 10)
        conn.commit()

        profile = match_data_profile(conn, LEAGUE, "2025-02-01T00:00:00Z", home_id, 3402)
        attack = next(g for g in profile.groups if g.key == "attack")
        assert attack.home_group_percentile == 100.0
        # 手算:100 分位的 home 到其余各队组级百分位的距离——3404(83)最近,
        # 3405(67)次近,3406(50)/3403(33)/3402(17)/3407(0)更远。
        assert [p.team_id for p in attack.home_peers] == [3404, 3405]
        assert [p.percentile for p in attack.home_peers] == [83.0, 67.0]
        # 名字/队徽字段必须存在(允许降级值,不允许字段本身缺失或抛错)。
        for peer in attack.home_peers:
            assert isinstance(peer.name, str) and peer.name
            assert peer.crest_url is None or isinstance(peer.crest_url, str)

    def test_peers_come_from_matching_venue_distribution(self, data_dir):
        """主队对标必须来自主场分布,不能混进客场分布的球队——用两套完全
        不相交的球队 id 分别做主场分布和客场分布,确认 home_peers 只可能来自
        主场那一批。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        home_id, away_id = 3501, 3601
        home_side_values = {3502: 1.0, 3503: 2.0, home_id: 6.0, 3504: 5.5, 3505: 5.0, 3506: 3.0}
        mid = 8100
        for tid, xg in home_side_values.items():
            for i in range(5):
                mid += 1
                _seed_home_match(conn, mid, tid, 9900 + mid, day=(mid % 27) + 1,
                                  expected_goals=xg, total_shots=xg * 10)
        away_side_ids = {3602, 3603, 3604, 3605, 3606}
        for i, tid in enumerate(away_side_ids | {away_id}):
            for j in range(5):
                m = 8300 + i * 10 + j
                insert_match(conn, m, league_id=LEAGUE, date=f"2025-01-{j+1:02d}",
                             home_id=9700 + m, away_id=tid, home="路人", away=f"队{tid}",
                             status="Finish", home_score=0, away_score=1,
                             kickoff_at_utc=f"2025-01-{j+1:02d}T12:00:00Z")
                _stats(conn, m, tid, expected_goals=1.0 + (tid % 5), total_shots=10.0)
        conn.commit()

        profile = match_data_profile(conn, LEAGUE, "2025-02-01T00:00:00Z", home_id, away_id)
        attack = next(g for g in profile.groups if g.key == "attack")
        home_peer_ids = {p.team_id for p in attack.home_peers}
        assert home_peer_ids.isdisjoint(away_side_ids)
        assert home_peer_ids.issubset(set(home_side_values) - {home_id})

    def test_target_without_group_percentile_gets_no_peers(self, data_dir):
        """目标队自己样本不足(不在分布里)时,home_peers 必须是空列表,
        不能编造"最接近"的答案。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        home_id, away_id = 3701, 3702
        # home_id 只有 2 场历史,不足 min_n(5),不会进入分布。
        for i in range(2):
            _seed_home_match(conn, 8500 + i, home_id, 9900 + i, day=i + 1, expected_goals=1.0)
        conn.commit()

        profile = match_data_profile(conn, LEAGUE, "2025-02-01T00:00:00Z", home_id, away_id)
        assert profile.groups == []  # 两队都不可用时提前返回,验证前置条件

    def test_all_metric_keys_resolve_in_registry(self):
        # GROUPS 里声明的每个 key 必须能在 registry 里找到——防止拼写漂移
        # (registry.py 目前不是运行时唯一真源,但本模块承诺从这里取元数据)。
        from backend.metrics.registry import get_metric

        for keys in GROUPS.values():
            for key in keys:
                get_metric(key)  # 找不到直接 KeyError,测试即失败


CUP = 42        # 欧冠
DOMESTIC_H = 59  # 挪威超(主队的国内联赛)
DOMESTIC_A = 54  # 德甲(客队的国内联赛)
BEFORE = "2025-02-01T00:00:00Z"


class TestCrossLeagueProfile:
    """欧战等跨联赛赛事:不给百分位,只并排列两队不限赛事的近期原始数值。

    2026-09-10 真实缺陷:欧冠比赛整个「数据→风格」tab 全空。根因是分布按
    本场 League_ID(42)圈,而欧冠联赛阶段每队只踢 8 场(主客各 4),同主客场
    永远到不了 min_n=5,该赛事内也凑不出 5 支够格球队。生产实测:欧冠够格
    球队 0 支,复现比赛两队各自 0 场可用。
    """

    def _seed(self, conn, home_id, away_id, *, n_domestic=5):
        """主队在挪超打 n 场主场 + 欧冠 1 场主场;客队在德甲打 n 场客场 + 欧冠 1 场客场。"""
        mid = 8000
        for i in range(n_domestic):
            insert_match(conn, mid, league_id=DOMESTIC_H, season="2025",
                         date=f"2025-01-{i+1:02d}", home_id=home_id, away_id=8600 + i,
                         home="主队", away=f"挪超对手{i}", status="Finish",
                         home_score=1, away_score=0,
                         kickoff_at_utc=f"2025-01-{i+1:02d}T12:00:00Z")
            _stats(conn, mid, home_id, expected_goals=1.0, total_shots=10)
            mid += 1
        # 欧冠主场那一场:数值刻意不同,便于断言它真的被算进来了
        self.cup_home_mid = mid
        insert_match(conn, mid, league_id=CUP, season="2024/2025", date="2025-01-20",
                     home_id=home_id, away_id=8700, home="主队", away="欧冠对手",
                     status="Finish", home_score=0, away_score=3,
                     kickoff_at_utc="2025-01-20T12:00:00Z")
        _stats(conn, mid, home_id, expected_goals=4.0, total_shots=40)
        mid += 1

        for i in range(n_domestic):
            insert_match(conn, mid, league_id=DOMESTIC_A, season="2024/2025",
                         date=f"2025-01-{i+1:02d}", home_id=8800 + i, away_id=away_id,
                         home=f"德甲对手{i}", away="客队", status="Finish",
                         home_score=0, away_score=1,
                         kickoff_at_utc=f"2025-01-{i+1:02d}T12:00:00Z")
            _stats(conn, mid, away_id, expected_goals=2.0, total_shots=20)
            mid += 1
        insert_match(conn, mid, league_id=CUP, season="2024/2025", date="2025-01-21",
                     home_id=8900, away_id=away_id, home="欧冠对手", away="客队",
                     status="Finish", home_score=1, away_score=1,
                     kickoff_at_utc="2025-01-21T12:00:00Z")
        _stats(conn, mid, away_id, expected_goals=2.0, total_shots=20)

    def test_scoped_to_cup_league_is_empty_which_is_the_bug(self, data_dir):
        """先钉住缺陷本身:圈在欧冠内两队各只有 1 场,达不到 min_n,整块空。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        self._seed(conn, 3901, 3902)
        conn.commit()

        profile = match_data_profile(conn, CUP, BEFORE, 3901, 3902)
        assert profile.groups == []
        assert not profile.home_available and not profile.away_available

    def test_cross_league_gives_raw_values_without_percentiles(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        self._seed(conn, 3901, 3902)
        conn.commit()

        profile = match_data_profile(conn, None, BEFORE, 3901, 3902, cross_league=True)

        assert profile.comparison_mode == "cross_league_raw"
        assert profile.scope_note is not None
        assert profile.unavailable_reason is None
        assert profile.home_available and profile.away_available
        # 三段必须照填——给空列表等于前端 groups.map() 渲染 0 个 section,
        # 攻/守/控整块消失且页面上零解释,正是本次要修的故障形态。
        assert [g.key for g in profile.groups] == list(GROUPS)

        for group in profile.groups:
            assert group.home_group_percentile is None
            assert group.away_group_percentile is None
            assert group.home_peers == [] and group.away_peers == []
            for metric in group.metrics:
                assert metric.home_percentile is None
                assert metric.away_percentile is None
                assert metric.league_sample_size == 0
        # 「最大的差距」榜按百分位差排序,没有百分位就不该有这个榜
        assert profile.highlights == []

        xg = next(m for g in profile.groups if g.key == "attack" for m in g.metrics if m.key == "xg")
        assert xg.home_value is not None and xg.away_value is not None

    def test_window_actually_spans_competitions(self, data_dir):
        """主队 5 场挪超 xg=1.0 + 1 场欧冠 xg=4.0 → 均值 1.5,证明欧冠那场
        真的被算进来了(只算挪超会是 1.0)。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        self._seed(conn, 3901, 3902)
        conn.commit()

        profile = match_data_profile(conn, None, BEFORE, 3901, 3902, cross_league=True)
        xg = next(m for g in profile.groups if g.key == "attack" for m in g.metrics if m.key == "xg")
        assert xg.home_value == 1.5
        assert profile.home_matches == 6

    def test_cross_league_selection_matches_venue_window(self, data_dir):
        """跨赛事窗口不许出现第二份选窗口实现——沿用本文件既有的
        "两条路径不许漂移"范式(见 TestLeagueWindowMatches)。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        self._seed(conn, 3901, 3902)
        conn.commit()

        batch = league_window_matches(conn, None, BEFORE, venue="home", team_ids=(3901,))
        expected = venue_window(conn, 3901, None, BEFORE, is_home=True)
        assert sorted(batch[3901]) == sorted(expected.match_ids)
        assert self.cup_home_mid in expected.match_ids

    def test_team_scoping_keeps_other_teams_out_of_the_query(self, data_dir):
        """没有联赛谓词时必须靠 team_ids 收窄,否则会扫全库每一支球队。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        self._seed(conn, 3901, 3902)
        conn.commit()

        batch = league_window_matches(conn, None, BEFORE, venue="home", team_ids=(3901,))
        assert set(batch) == {3901}

    def test_no_history_at_all_is_reported_honestly(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        conn.commit()

        profile = match_data_profile(conn, None, BEFORE, 4901, 4902, cross_league=True)
        assert profile.groups == []
        assert profile.home_matches == 0 and profile.away_matches == 0
        assert profile.unavailable_reason is not None
        assert profile.comparison_mode == "cross_league_raw"

    def test_regular_league_path_still_reports_percentile_mode(self, data_dir):
        """零回归锚:常规联赛比赛必须仍走百分位模式,且不带杯赛的口径说明。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        home_id, away_id = 3101, 3102
        for i, (tid, v) in enumerate({3103: 1.0, 3104: 1.2, 3105: 1.3, 3106: 1.4, 3107: 1.5, home_id: 6.0}.items()):
            for j in range(5):
                mid = 8500 + i * 10 + j
                _seed_home_match(conn, mid, tid, 9500 + mid, day=j + 1, expected_goals=v)
        conn.commit()

        profile = match_data_profile(conn, LEAGUE, BEFORE, home_id, away_id)
        assert profile.comparison_mode == "league_percentile"
        assert profile.scope_note is None
        xg = next(m for g in profile.groups if g.key == "attack" for m in g.metrics if m.key == "xg")
        assert xg.home_percentile == 100

    def test_missing_cross_league_flag_fails_loudly(self, data_dir):
        """接线错误不能静默返回空画像——那正是本次要修的故障形态。"""
        import pytest

        conn = connect_rw("core")
        seed_core_schema(conn)
        conn.commit()
        with pytest.raises(ValueError):
            match_data_profile(conn, None, BEFORE, 3901, 3902)


class TestVenueModeAndWindowSwitchers:
    """2026-09-13 站长要的两个切换器:「全部 / 相同主客场」×「近 3/5/10 场」。"""

    def _seed_short_league(self, conn, *, per_team: int, teams: dict[int, float]):
        """给每支球队各造 `per_team` 场**主场**比赛(客场同理另造)。"""
        mid = 9000
        for tid, v in teams.items():
            for j in range(per_team):
                _seed_home_match(conn, mid, tid, 9700 + mid, day=j + 1, expected_goals=v)
                mid += 1

    def test_short_window_must_not_blank_the_whole_module(self, data_dir):
        """★ 本次最重要的一条回归:切到「近 3 场」不能让攻/守/控三块整块消失。

        实测过的故障形态:max_n=3 配写死的 min_n=5 → 全联赛够格球队 0 支 →
        两侧 available 双 False → 三块整块空,而且 unavailable_reason 把
        "后端门槛写死"说成"两队比赛不足",是错误陈述。
        """
        conn = connect_rw("core")
        seed_core_schema(conn)
        home_id, away_id = 3401, 3402
        # 每队只有 3 场主场:够 max_n=3,不够写死的 min_n=5
        self._seed_short_league(conn, per_team=3, teams={
            3403: 1.0, 3404: 1.2, 3405: 1.3, 3406: 1.4, 3407: 1.5,
            home_id: 6.0, away_id: 0.5,
        })
        conn.commit()

        p = match_data_profile(conn, LEAGUE, BEFORE, home_id, away_id, max_n=3)
        assert p.home_available, "近 3 场口径下主队必须够格"
        assert p.unavailable_reason is None
        assert len(p.groups) == 3, "攻/守/控三段必须都在"
        assert any(m.home_percentile is not None for g in p.groups for m in g.metrics)

    def test_min_n_coupling_is_what_saves_it(self, data_dir):
        """反证:同样的数据 + 显式写死 min_n=5,必然整块空。

        没有这一条,上面那条测试只能证明"结果是绿的",证明不了绿是 min_n
        联动带来的——数据稍微多种一场就会假绿。
        """
        conn = connect_rw("core")
        seed_core_schema(conn)
        home_id, away_id = 3401, 3402
        self._seed_short_league(conn, per_team=3, teams={
            3403: 1.0, 3404: 1.2, 3405: 1.3, 3406: 1.4, 3407: 1.5,
            home_id: 6.0, away_id: 0.5,
        })
        conn.commit()

        blocked = match_data_profile(conn, LEAGUE, BEFORE, home_id, away_id, max_n=3, min_n=5)
        assert not blocked.home_available and not blocked.away_available
        assert blocked.groups == []

    def test_default_window_keeps_min_n_five(self, data_dir):
        """N=10 时有效 min_n 仍是 5 —— 联动引入前后逐字节等价,零回归。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        home_id = 3501
        # 每队 4 场:min_n=5 下没人够格(不管 max_n 是 10 还是 5)
        self._seed_short_league(conn, per_team=4, teams={
            3503: 1.0, 3504: 1.2, 3505: 1.3, 3506: 1.4, home_id: 6.0,
        })
        conn.commit()

        for max_n in (10, 5):
            p = match_data_profile(conn, LEAGUE, BEFORE, home_id, 3503, max_n=max_n)
            assert not p.home_available, f"max_n={max_n} 的有效 min_n 应为 5,4 场不够格"

    def test_all_venue_merges_home_and_away_into_one_window(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        team = 3601
        mid = 9500
        for j in range(3):  # 3 场主场
            _seed_home_match(conn, mid, team, 9800 + mid, day=j + 1, expected_goals=2.0)
            mid += 1
        for j in range(3):  # 3 场客场
            insert_match(conn, mid, league_id=LEAGUE, date=f"2025-01-{j+10:02d}",
                         home_id=9800 + mid, away_id=team, home="路人", away=f"队{team}",
                         status="Finish", home_score=0, away_score=1,
                         kickoff_at_utc=f"2025-01-{j+10:02d}T12:00:00Z")
            _stats(conn, mid, team, expected_goals=2.0)
            mid += 1
        conn.commit()

        same = league_metric_distribution(conn, LEAGUE, BEFORE, venue="home", max_n=10, min_n=1)
        merged = league_metric_distribution(conn, LEAGUE, BEFORE, venue="any", max_n=10, min_n=1)
        assert same[team]["xg"].window_matches == 3
        assert merged[team]["xg"].window_matches == 6

    def test_all_venue_puts_both_teams_on_one_distribution(self, data_dir):
        """「全部」口径的真实优点:两队 draw 自**同一套**分布,回到同一把尺子上。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        home_id, away_id = 3701, 3702
        self._seed_short_league(conn, per_team=5, teams={
            3703: 1.0, 3704: 1.2, 3705: 1.3, 3706: 1.4, 3707: 1.5,
            home_id: 6.0, away_id: 0.5,
        })
        conn.commit()

        p = match_data_profile(conn, LEAGUE, BEFORE, home_id, away_id, venue_mode="all")
        xg = next(m for g in p.groups if g.key == "attack" for m in g.metrics if m.key == "xg")
        # 同一套分布 ⇒ 两侧的参照人群大小相同(各自把自己排除后同样大)
        assert xg.home_percentile is not None and xg.away_percentile is not None
        assert xg.home_percentile == 100 and xg.away_percentile == 0

    def test_all_venue_halves_the_query_count(self, data_dir):
        """「全部」只算一次分布,不是算两次各取各的——防止将来被"顺手"改回去。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        home_id, away_id = 3801, 3802
        self._seed_short_league(conn, per_team=5, teams={
            3803: 1.0, 3804: 1.2, 3805: 1.3, 3806: 1.4, 3807: 1.5,
            home_id: 6.0, away_id: 0.5,
        })
        conn.commit()

        counts: dict[str, int] = {}
        for mode in ("same_venue", "all"):
            n = 0

            def _count(_sql, _n=None):
                nonlocal n
                n += 1

            conn.set_trace_callback(_count)
            match_data_profile(conn, LEAGUE, BEFORE, home_id, away_id, venue_mode=mode)
            conn.set_trace_callback(None)
            counts[mode] = n
        assert counts["all"] < counts["same_venue"], counts

    def test_unavailable_reason_follows_the_chosen_venue(self, data_dir):
        """用户选了"不分主客场",页面却说"同主客场比赛不足"——那是错误陈述。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        conn.commit()

        same = match_data_profile(conn, LEAGUE, BEFORE, 3901, 3902)
        merged = match_data_profile(conn, LEAGUE, BEFORE, 3901, 3902, venue_mode="all")
        assert "同主客场" in same.unavailable_reason
        assert "同主客场" not in merged.unavailable_reason

    def test_scope_note_only_for_all_venue(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        home_id, away_id = 3101, 3102
        self._seed_short_league(conn, per_team=5, teams={
            3103: 1.0, 3104: 1.2, 3105: 1.3, 3106: 1.4, 3107: 1.5,
            home_id: 6.0, away_id: 0.5,
        })
        conn.commit()

        assert match_data_profile(conn, LEAGUE, BEFORE, home_id, away_id).scope_note is None
        note = match_data_profile(conn, LEAGUE, BEFORE, home_id, away_id, venue_mode="all").scope_note
        assert note is not None and "不分主客场" in note

    def test_chosen_scope_is_echoed_back(self, data_dir):
        """请求什么口径就回什么——前端的措辞完全依赖这两个字段。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        conn.commit()

        p = match_data_profile(conn, LEAGUE, BEFORE, 3901, 3902, max_n=3, venue_mode="all")
        assert p.venue_mode == "all" and p.window_n == 3
        d = match_data_profile(conn, LEAGUE, BEFORE, 3901, 3902)
        assert d.venue_mode == "same_venue" and d.window_n == DEFAULT_MAX_N

    def test_cross_league_honours_the_switchers_too(self, data_dir):
        """欧战比赛同样给切换器——不给的话前端就得让它们凭空消失。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        team = 3901
        mid = 9900
        for j in range(3):
            insert_match(conn, mid, league_id=CUP, date=f"2025-01-{j+1:02d}",
                         home_id=team, away_id=9990 + mid, home=f"队{team}", away="路人",
                         status="Finish", home_score=1, away_score=0,
                         kickoff_at_utc=f"2025-01-{j+1:02d}T12:00:00Z")
            _stats(conn, mid, team, expected_goals=2.0)
            mid += 1
        for j in range(2):
            insert_match(conn, mid, league_id=CUP, date=f"2025-01-{j+10:02d}",
                         home_id=9990 + mid, away_id=team, home="路人", away=f"队{team}",
                         status="Finish", home_score=0, away_score=1,
                         kickoff_at_utc=f"2025-01-{j+10:02d}T12:00:00Z")
            _stats(conn, mid, team, expected_goals=2.0)
            mid += 1
        conn.commit()

        same = match_data_profile(conn, None, BEFORE, team, 3902, cross_league=True)
        merged = match_data_profile(conn, None, BEFORE, team, 3902, cross_league=True, venue_mode="all")
        assert same.home_matches == 3      # 只主场
        assert merged.home_matches == 5    # 主+客
        # 两句口径说明都要在:跨赛事与分不分主客场是两件独立的事,漏一句就是隐瞒
        assert "不限赛事" in merged.scope_note and "不分主客场" in merged.scope_note

    def test_scope_league_name_is_reported_for_league_mode(self, data_dir):
        """窗口是 m.League_ID=? 的硬谓词,欧战/杯赛全被排除——前端要据此写
        "近 N 个英超主场"而不是"近 N 个主场"。名字必须后端给:只有后端知道
        SQL 真的圈了哪个 League_ID。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        home_id, away_id = 3101, 3102
        self._seed_short_league(conn, per_team=5, teams={
            3103: 1.0, 3104: 1.2, 3105: 1.3, 3106: 1.4, 3107: 1.5,
            home_id: 6.0, away_id: 0.5,
        })
        conn.commit()

        assert match_data_profile(conn, LEAGUE, BEFORE, home_id, away_id).scope_league_zh == "英超"
        # 两侧都不够格的早退分支同样要带上,不是只在完整产出时才给
        assert match_data_profile(conn, LEAGUE, BEFORE, 9001, 9002).scope_league_zh == "英超"
        # 「全部」口径只是不分主客场,仍然圈在本联赛内 —— 名字照给
        assert match_data_profile(conn, LEAGUE, BEFORE, home_id, away_id,
                                  venue_mode="all").scope_league_zh == "英超"

    def test_cross_league_reports_no_scope_league(self, data_dir):
        """跨赛事路径本来就没圈联赛,写任何一个联赛名都是假的;
        那条路径的"不限赛事"后缀已经把口径说清楚了。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        conn.commit()
        p = match_data_profile(conn, None, BEFORE, 3901, 3902, cross_league=True)
        assert p.scope_league_zh is None
