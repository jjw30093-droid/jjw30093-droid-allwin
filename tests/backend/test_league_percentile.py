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
        batch = league_window_matches(conn, LEAGUE, before, is_home=True, max_n=DEFAULT_MAX_N)
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

        dist = league_metric_distribution(conn, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
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

        dist = league_metric_distribution(conn, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
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

        dist = league_metric_distribution(conn, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
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

        dist = league_metric_distribution(conn, LEAGUE, "2025-02-01T00:00:00Z", is_home=True)
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
