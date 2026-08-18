"""赔率三段递进 + FotMob 阵容按联赛分档 + last_call(PIPELINE_REDESIGN_V2 P1,全离线)。

钉住:分档间隔、last_call 跨窗强制、重启/重放幂等、per-source 隔离
(NowGoal 新三档 72/24/6h checkpoint;FotMob 阵容改用按联赛查表的
watch-window-start,不再是 CADENCE_LEGACY)、每档 ≥ §6.3 下限。
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.ingest.poll_windows import (
    BIG5_LEAGUE_IDS,
    CADENCE_LEGACY,
    CADENCE_NOWGOAL_ODDS,
    FOTMOB_LINEUP_CANDIDATE_WINDOW_HOURS,
    FOTMOB_LINEUP_FAR_INTERVAL_SECONDS,
    FOTMOB_LINEUP_PRE_WATCH_INTERVAL_SECONDS,
    FOTMOB_LINEUP_PRE_WATCH_UPPER_HOURS,
    FOTMOB_LINEUP_WATCH_WINDOW_START_HOURS,
    FOTMOB_LINEUP_WATCH_WINDOW_START_HOURS_DEFAULT,
    FOTMOB_LINEUP_WINDOW_HOURS,
    INTERVAL_FAR_SECONDS,
    INTERVAL_NEAR_SECONDS,
    SOURCE_FOTMOB_SNAPSHOT,
    SOURCE_NOWGOAL_ODDS,
    cadence_fotmob_lineup,
    poll_decision,
)

NOW = datetime(2026, 8, 10, tzinfo=timezone.utc)
EXACT, SRC = "exact", "fotmob:fixtures"


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _kickoff(hours=0, minutes=0):
    return _iso(NOW + timedelta(hours=hours, minutes=minutes))


def _decide(kickoff, last_polled_at, source=SOURCE_NOWGOAL_ODDS, league_id=None):
    return poll_decision(source, kickoff, EXACT, SRC, last_polled_at, _iso(NOW), league_id=league_id)


class TestNowgoalTiers:
    @pytest.mark.parametrize("hours,expected_interval", [
        (1.0, 3600),    # 0-6h → 1h
        (6.0, 3600),
        (12.0, 21600),  # 6-24h → 6h
        (24.0, 21600),
        (36.0, 86400),  # 24-72h → 24h
        (72.0, 86400),
    ])
    def test_tier_interval(self, hours, expected_interval):
        # 从未轮询 → due;刚轮询过(now)→ not_due(节流生效),间隔体现在 reason
        kickoff = _kickoff(hours=hours)
        assert _decide(kickoff, None).due is True
        # 上次轮询在 interval-1 秒前 → 仍节流;interval 秒前 → 到期
        just_before = _iso(NOW - timedelta(seconds=expected_interval - 1))
        exactly = _iso(NOW - timedelta(seconds=expected_interval))
        assert _decide(kickoff, just_before).due is False
        assert _decide(kickoff, exactly).due is True

    def test_boundary_72h_exactly_in_window(self):
        """T-72h 恰好落在窗口边界内(旧三档最后一档),仍是常规 checkpoint,不是
        first_discovery——window_hours 本身仍是 72,没有变。"""
        d = _decide(_kickoff(hours=72), None)
        assert d.due is True and d.tier == "checkpoint_72h" and d.interval_seconds == 86400

    def test_boundary_24h_switches_tier(self):
        just_inside_24h = _decide(_kickoff(hours=24), _iso(NOW))
        just_outside_24h = _decide(_kickoff(hours=24, minutes=1), _iso(NOW))
        # 24h 整点属于 6-24h 档(21600s);24h01min 已跨进 24-72h 档(86400s)——
        # 同一次"1 分钟前轮询过"在两侧节流结论不同,间隔档已经切换。
        assert just_inside_24h.interval_seconds == 21600
        assert just_outside_24h.interval_seconds == 86400

    def test_boundary_6h_switches_tier(self):
        just_inside_6h = _decide(_kickoff(hours=6), _iso(NOW))
        just_outside_6h = _decide(_kickoff(hours=6, minutes=1), _iso(NOW))
        assert just_inside_6h.interval_seconds == 3600
        assert just_outside_6h.interval_seconds == 21600

    def test_out_of_window_first_discovery_still_polls_once(self):
        """T+7 首次发现即采(CLAUDE.md §6.3):distance >72h 但从未轮询过
        (last_polled_at=None)→ 仍然 due 一次;已经轮询过之后(last_polled_at
        非空)再问,同样 >72h → 不再重复,回到正常 out-of-window。"""
        far = _kickoff(hours=80)
        first = _decide(far, None)
        assert first.due is True and first.tier == "first_discovery"
        already_polled = _iso(NOW - timedelta(hours=1))
        assert _decide(far, already_polled).due is False

    def test_kicked_off_never_due_even_unpolled(self):
        assert _decide(_kickoff(hours=-1), None).due is False   # 已开球

    def test_no_exact_kickoff_never_due(self):
        d = poll_decision(SOURCE_NOWGOAL_ODDS, _kickoff(hours=2), "date_only", None, None, _iso(NOW))
        assert d.due is False and d.reason == "no_exact_kickoff"


class TestLastCall:
    def test_boundary_15min_exactly_forces(self):
        """T-15min 恰好是 last_call 边界:s<=900 才强制,900s 那一刻已经在窗内。"""
        kickoff = _kickoff(minutes=15)
        d = _decide(kickoff, None)
        assert d.due is True and d.tier == "last_call" and d.interval_seconds == 900

    def test_boundary_just_outside_last_call_uses_normal_tier(self):
        kickoff = _iso(NOW + timedelta(minutes=15, seconds=1))
        d = _decide(kickoff, None)
        # 15min01s 仍 <=6h 档,常规 first_poll,不再是 last_call
        assert d.due is True and d.tier == "checkpoint_6h" and d.reason == "first_poll"

    def test_forces_when_crossed_window_without_poll(self):
        """距开球 10min、上次轮询在 T-40min(>15min 窗口)→ 强制补一枪。"""
        kickoff = _kickoff(minutes=10)
        last = _iso(NOW - timedelta(minutes=30))   # 那时距开球 40min
        d = _decide(kickoff, last)
        assert d.due is True and d.tier == "last_call"

    def test_last_call_never_polled(self):
        d = _decide(_kickoff(minutes=8), None)
        assert d.due is True and d.tier == "last_call"

    def test_no_double_last_call(self):
        """已在 15min 窗内轮询过(T-12min),再判不重复强制(走正常节流)。"""
        kickoff = _kickoff(minutes=10)
        last = _iso(NOW - timedelta(minutes=2))    # 那时距开球 12min,已在窗内
        d = _decide(kickoff, last)
        assert d.due is False


class TestFotmobLineupTiers:
    """CADENCE_FOTMOB_LINEUP(cadence_fotmob_lineup()):cadence 窗口 72h(用户
    决策 A),三档——72h–24h 每 24h、24h–watch-window-start 每 12h、
    watch-window-start–T0 每 5 分钟到底(无早停);watch-window-start 按联赛
    查表(BIG-5=1.5h,其余=1.25h)。候选池宽度独立于 cadence 窗口,为 168h
    (见 test_poll_fotmob_snapshots_due.py::TestCandidatePoolWidth),只为落实
    §6.3「首次发现即采」下限的结构可达性。"""

    BIG5_SAMPLE = 47
    OTHER_LEAGUE = 999

    def test_big5_league_ids_are_the_confirmed_five(self):
        assert BIG5_LEAGUE_IDS == frozenset({47, 87, 55, 54, 53})

    def test_lineup_window_is_72h(self):
        """cadence 窗口从 24h 放宽到 72h(用户决策 A)——FOTMOB_LINEUP_DISCOVERY_HOURS
        一个名字曾身兼三职(cadence 窗口/12h 档上界/候选池宽度)正是 bug 的结构性
        成因,现整体删除、不留别名,拆成三个语义单一的常量。"""
        assert FOTMOB_LINEUP_WINDOW_HOURS == 72

    def test_far_tier_interval_is_24h(self):
        assert FOTMOB_LINEUP_FAR_INTERVAL_SECONDS == 86400

    def test_pre_watch_upper_hours_is_24h(self):
        assert FOTMOB_LINEUP_PRE_WATCH_UPPER_HOURS == 24

    def test_pre_watch_interval_is_12h_and_unchanged(self):
        assert FOTMOB_LINEUP_PRE_WATCH_INTERVAL_SECONDS == 43200

    def test_candidate_window_is_wider_than_cadence_window(self):
        """候选池(168h)必须严格宽于 cadence 窗口(72h)——这是 §6.3「首次发现
        即采」分支结构上可达的前提:poll_decision() 的 first_discovery 分支只在
        s > cadence.window_hours*3600 时触发。放宽前候选池与 cadence 窗口相等
        (都是 24h),{s: s>86400} ∩ {s: s<=86400} = ∅,该分支对阵容结构性不可达,
        这正是 99 场比赛零快照能发生的根因之一。"""
        assert FOTMOB_LINEUP_CANDIDATE_WINDOW_HOURS > FOTMOB_LINEUP_WINDOW_HOURS

    def test_cadence_has_three_tiers_ascending_and_monotone(self):
        for league_id in (self.BIG5_SAMPLE, self.OTHER_LEAGUE, None):
            cadence = cadence_fotmob_lineup(league_id)
            assert len(cadence.tiers) == 3
            upper_hours = [t[0] for t in cadence.tiers]
            intervals = [t[1] for t in cadence.tiers]
            assert upper_hours == sorted(upper_hours), (league_id, upper_hours)
            assert intervals == sorted(intervals), (league_id, intervals)

    def test_default_watch_window_start_is_1_25h(self):
        assert FOTMOB_LINEUP_WATCH_WINDOW_START_HOURS_DEFAULT == 1.25

    def test_boundary_24h_is_regular_checkpoint_not_discovery_entry(self):
        """恰好 T-24h:仍在 72h cadence 窗口深处,从未轮询过 → due,常规
        checkpoint_24h。这不再是"discovery 入口"——放宽后窗口入口是 T-72h,
        T-24h 只是 24h 档与 12h 档的切换点(旧名字在窗口放宽后变成了假话,
        故重命名并改写 docstring;三行断言逐字不变)。"""
        d = _decide(_kickoff(hours=24), None, source=SOURCE_FOTMOB_SNAPSHOT,
                    league_id=self.OTHER_LEAGUE)
        assert d.due is True and d.tier == "checkpoint_24h" and d.interval_seconds == 43200

    def test_beyond_72h_and_never_polled_is_first_discovery(self):
        """T-72h01min:跨出 72h cadence 窗口(仍在 168h 候选池内)。引擎的
        first_discovery 分支与来源无关——只要从未轮询过就补一枪,这一点
        FotMob 和 NowGoal 共用同一条逻辑。"""
        far = _iso(NOW + timedelta(hours=72, minutes=1))
        d = _decide(far, None, source=SOURCE_FOTMOB_SNAPSHOT, league_id=self.OTHER_LEAGUE)
        assert d.due is True and d.tier == "first_discovery"

    def test_beyond_72h_and_already_polled_is_not_due(self):
        far = _iso(NOW + timedelta(hours=72, minutes=1))
        already = _iso(NOW - timedelta(hours=1))
        d = _decide(far, already, source=SOURCE_FOTMOB_SNAPSHOT, league_id=self.OTHER_LEAGUE)
        assert d.due is False and d.tier is None

    @pytest.mark.parametrize("hours", [24.5, 38.5, 48.0, 62.5, 72.0])
    @pytest.mark.parametrize("league_id", [47, 999])
    def test_far_band_interval_is_24h(self, hours, league_id):
        """72h–24h 档:每 24h(86400s)。38.5h/62.5h 是 2026-08-18 真实网络探测过的
        地平线(生产最近一场比赛开球在 T-38.5h),不是凭空取的边界。"""
        kickoff = _kickoff(hours=hours)
        d = _decide(kickoff, None, source=SOURCE_FOTMOB_SNAPSHOT, league_id=league_id)
        assert d.due is True and d.tier == "checkpoint_72h" and d.interval_seconds == 86400
        just_before = _iso(NOW - timedelta(seconds=86399))
        exactly = _iso(NOW - timedelta(seconds=86400))
        assert _decide(kickoff, just_before, source=SOURCE_FOTMOB_SNAPSHOT,
                        league_id=league_id).due is False
        assert _decide(kickoff, exactly, source=SOURCE_FOTMOB_SNAPSHOT,
                        league_id=league_id).due is True

    def test_boundary_24h_switches_far_to_pre_watch(self):
        """T-24h 整 → checkpoint_24h/43200(12h 档);T-24h01m → checkpoint_72h/86400
        (24h-72h 档)——闭区间在 24h 整点这一侧。"""
        at_24h = _decide(_kickoff(hours=24), None, source=SOURCE_FOTMOB_SNAPSHOT,
                          league_id=self.OTHER_LEAGUE)
        just_beyond = _decide(_iso(NOW + timedelta(hours=24, minutes=1)), None,
                               source=SOURCE_FOTMOB_SNAPSHOT, league_id=self.OTHER_LEAGUE)
        assert at_24h.tier == "checkpoint_24h" and at_24h.interval_seconds == 43200
        assert just_beyond.tier == "checkpoint_72h" and just_beyond.interval_seconds == 86400

    def test_boundary_72h_exactly_in_window(self):
        """T-72h 整落在 cadence 窗口边界内(闭区间),仍是常规 checkpoint_72h,
        不是 first_discovery——window_hours 就是 72。"""
        d = _decide(_kickoff(hours=72), None, source=SOURCE_FOTMOB_SNAPSHOT,
                    league_id=self.OTHER_LEAGUE)
        assert d.due is True and d.tier == "checkpoint_72h" and d.interval_seconds == 86400

    def test_pre_watch_tier_is_12h(self):
        """T-24h..watch-window-start:每 12h(43200s)——用非 BIG-5 联赛,10h 处于该档。"""
        kickoff = _kickoff(hours=10)
        just_before = _iso(NOW - timedelta(seconds=43199))
        exactly = _iso(NOW - timedelta(seconds=43200))
        d = _decide(kickoff, None, source=SOURCE_FOTMOB_SNAPSHOT, league_id=self.OTHER_LEAGUE)
        assert d.due is True and d.tier == "checkpoint_24h" and d.interval_seconds == 43200
        assert _decide(kickoff, just_before, source=SOURCE_FOTMOB_SNAPSHOT,
                        league_id=self.OTHER_LEAGUE).due is False
        assert _decide(kickoff, exactly, source=SOURCE_FOTMOB_SNAPSHOT,
                        league_id=self.OTHER_LEAGUE).due is True

    def test_watch_window_tier_is_5min(self):
        """watch-window-start..T-0:每 5 分钟(300s)——用非 BIG-5 联赛,30min 处于该档。"""
        kickoff = _kickoff(minutes=30)
        just_before = _iso(NOW - timedelta(seconds=299))
        exactly = _iso(NOW - timedelta(seconds=300))
        d = _decide(kickoff, None, source=SOURCE_FOTMOB_SNAPSHOT, league_id=self.OTHER_LEAGUE)
        assert d.due is True and d.interval_seconds == 300
        assert _decide(kickoff, just_before, source=SOURCE_FOTMOB_SNAPSHOT,
                        league_id=self.OTHER_LEAGUE).due is False
        assert _decide(kickoff, exactly, source=SOURCE_FOTMOB_SNAPSHOT,
                        league_id=self.OTHER_LEAGUE).due is True

    def test_watch_window_start_boundary_diverges_by_big5(self):
        """同一 s(距开球 1.5h),BIG-5 联赛已经进 5 分钟档,其它联赛(默认 1.25h)
        还在 12h 档——这是本次改造要求的核心分歧点,必须逐联赛查表而非常量。"""
        kickoff = _kickoff(hours=1, minutes=30)   # 距开球恰好 1.5h
        big5 = _decide(kickoff, _iso(NOW), source=SOURCE_FOTMOB_SNAPSHOT,
                        league_id=self.BIG5_SAMPLE)
        other = _decide(kickoff, _iso(NOW), source=SOURCE_FOTMOB_SNAPSHOT,
                         league_id=self.OTHER_LEAGUE)
        assert big5.interval_seconds == 300
        assert other.interval_seconds == 43200

    def test_default_watch_window_start_boundary_at_1_25h(self):
        """非 BIG-5 联赛自己的 1.25h 边界:恰好 1.25h 已进 5 分钟档。"""
        kickoff = _iso(NOW + timedelta(hours=1, minutes=15))
        d = _decide(kickoff, _iso(NOW), source=SOURCE_FOTMOB_SNAPSHOT,
                    league_id=self.OTHER_LEAGUE)
        assert d.interval_seconds == 300
        just_outside = _iso(NOW + timedelta(hours=1, minutes=16))
        d2 = _decide(just_outside, _iso(NOW), source=SOURCE_FOTMOB_SNAPSHOT,
                     league_id=self.OTHER_LEAGUE)
        assert d2.interval_seconds == 43200

    def test_big5_watch_window_start_hours_is_1_5(self):
        assert FOTMOB_LINEUP_WATCH_WINDOW_START_HOURS[self.BIG5_SAMPLE] == 1.5
        assert all(hours == 1.5 for hours in FOTMOB_LINEUP_WATCH_WINDOW_START_HOURS.values())

    def test_big5_watch_window_start_boundary_at_1_5h(self):
        """BIG-5 自己的 1.5h 边界,双侧验证(此前只在 test_watch_window_start_
        boundary_diverges_by_big5 里验证了 AT 边界那一个时刻,没验证刚过 1.5h 是否
        正确退回 12h 档——两侧都要查,避免误把 BIG-5 的门槛设宽/设窄却测不出来)。"""
        kickoff = _iso(NOW + timedelta(hours=1, minutes=30))
        d = _decide(kickoff, _iso(NOW), source=SOURCE_FOTMOB_SNAPSHOT,
                    league_id=self.BIG5_SAMPLE)
        assert d.interval_seconds == 300
        just_outside = _iso(NOW + timedelta(hours=1, minutes=31))
        d2 = _decide(just_outside, _iso(NOW), source=SOURCE_FOTMOB_SNAPSHOT,
                     league_id=self.BIG5_SAMPLE)
        assert d2.interval_seconds == 43200

    def test_no_league_id_falls_back_to_default_watch_window(self):
        """未传 league_id(如老调用点忘记传)→ 落回默认档,不是异常也不是静默用
        BIG-5 更宽的窗口。"""
        cadence = cadence_fotmob_lineup(None)
        assert cadence.tiers[0][0] == FOTMOB_LINEUP_WATCH_WINDOW_START_HOURS_DEFAULT

    def test_kicked_off_never_due(self):
        assert _decide(_kickoff(hours=-1), None, source=SOURCE_FOTMOB_SNAPSHOT,
                       league_id=self.BIG5_SAMPLE).due is False


class TestPerSourceIsolation:
    def test_fotmob_snapshot_no_longer_uses_legacy_two_tier(self):
        """FotMob 阵容改用按联赛查表的 cadence_fotmob_lineup,不再是 CADENCE_LEGACY
        的 24h→900s/1h→300s 两档——这条旧断言本身就是本次要收敛掉的行为。"""
        kickoff = _kickoff(hours=24)
        exactly_900 = _iso(NOW - timedelta(seconds=900))
        d = _decide(kickoff, exactly_900, source=SOURCE_FOTMOB_SNAPSHOT, league_id=999)
        # 新档在 T-24h 是 checkpoint_24h(43200s),900s 前轮询过远不够到期
        assert d.due is False
        assert d.interval_seconds == 43200

    def test_nowgoal_schedule_still_uses_legacy(self):
        """日程发现节流(SOURCE_NOWGOAL_SCHEDULE)本次任务未涉及,仍是旧两档。"""
        from backend.ingest.poll_windows import SOURCE_NOWGOAL_SCHEDULE
        kickoff = _kickoff(hours=24)
        exactly_900 = _iso(NOW - timedelta(seconds=900))
        d = _decide(kickoff, exactly_900, source=SOURCE_NOWGOAL_SCHEDULE)
        assert d.due is True and d.interval_seconds == 900

class TestConstitutionFloors:
    def test_every_cadence_respects_constitution_floors(self):
        """CLAUDE.md §6.3 下限:2–72h ≥900s、0–2h ≥300s。所有 cadence(含按联赛
        查表的 FotMob 阵容,BIG-5 与默认档都要过)逐档不得低于下限。"""
        cadences = [
            CADENCE_LEGACY, CADENCE_NOWGOAL_ODDS,
            cadence_fotmob_lineup(47), cadence_fotmob_lineup(999), cadence_fotmob_lineup(None),
        ]
        for cadence in cadences:
            for upper_hours, interval in cadence.tiers:
                if upper_hours <= 2:
                    assert interval >= INTERVAL_NEAR_SECONDS, (cadence.name, upper_hours, interval)
                else:
                    assert interval >= INTERVAL_FAR_SECONDS, (cadence.name, upper_hours, interval)
            if cadence.last_call_seconds is not None:
                assert cadence.last_call_seconds >= INTERVAL_NEAR_SECONDS


class TestRestartIdempotency:
    def test_replay_same_now_is_idempotent(self):
        """同一 now 下:首次 due → mark 后(last_polled_at=now)第二次必 not_due。"""
        kickoff = _kickoff(hours=6)
        assert _decide(kickoff, None).due is True
        # 模拟 mark_polled 写入 last_polled_at = now
        assert _decide(kickoff, _iso(NOW)).due is False
