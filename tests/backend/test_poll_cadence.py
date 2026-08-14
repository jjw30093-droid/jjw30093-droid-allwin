"""赔率五段递进节流 + last_call(数据管道重建 Phase 4,全离线)。

钉住:分档间隔、last_call 跨窗强制、重启/重放幂等、per-source 隔离
(NowGoal 用新阶梯,FotMob 快照/日程与 required_interval_seconds 一字不改)、
每档 ≥ §6.3 下限。
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.ingest.poll_windows import (
    CADENCE_LEGACY,
    CADENCE_NOWGOAL_ODDS,
    INTERVAL_FAR_SECONDS,
    INTERVAL_NEAR_SECONDS,
    SOURCE_FOTMOB_SNAPSHOT,
    SOURCE_NOWGOAL_ODDS,
    poll_decision,
    required_interval_seconds,
)

NOW = datetime(2026, 8, 10, tzinfo=timezone.utc)
EXACT, SRC = "exact", "fotmob:fixtures"


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _kickoff(hours=0, minutes=0):
    return _iso(NOW + timedelta(hours=hours, minutes=minutes))


def _decide(kickoff, last_polled_at, source=SOURCE_NOWGOAL_ODDS):
    return poll_decision(source, kickoff, EXACT, SRC, last_polled_at, _iso(NOW))


class TestNowgoalTiers:
    @pytest.mark.parametrize("hours,expected_interval", [
        (0.5, 600),    # ≤1h → 10min
        (1.0, 600),
        (2.0, 1200),   # 1-3h → 20min
        (3.0, 1200),
        (6.0, 3600),   # 3-12h → 1h
        (12.0, 3600),
        (18.0, 43200), # 12-24h → 12h
        (36.0, 86400), # 24-48h → 24h
        (60.0, 86400), # 48-72h → 24h
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

    def test_out_of_window_first_discovery_still_polls_once(self):
        """T+7 首次发现即采(2026-08-11 新增,CLAUDE.md §6.3):distance >72h 但
        从未轮询过(last_polled_at=None)→ 仍然 due 一次;已经轮询过之后
        (last_polled_at 非空)再问,同样 >72h → 不再重复,回到正常 out-of-window。"""
        far = _kickoff(hours=80)
        first = _decide(far, None)
        assert first.due is True and first.tier == "first_discovery"
        # 已经在很久以前(>72h 前)轮询过一次:仍在 >72h 窗口外,不再重复触发
        already_polled = _iso(NOW - timedelta(hours=1))
        assert _decide(far, already_polled).due is False

    def test_kicked_off_never_due_even_unpolled(self):
        assert _decide(_kickoff(hours=-1), None).due is False   # 已开球

    def test_no_exact_kickoff_never_due(self):
        d = poll_decision(SOURCE_NOWGOAL_ODDS, _kickoff(hours=2), "date_only", None, None, _iso(NOW))
        assert d.due is False and d.reason == "no_exact_kickoff"


class TestLastCall:
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
        """已在 15min 窗内轮询过(T-12min),再判不重复强制(走正常 10min 节流)。"""
        kickoff = _kickoff(minutes=10)
        last = _iso(NOW - timedelta(minutes=2))    # 那时距开球 12min,已在窗内
        d = _decide(kickoff, last)
        # 距上次仅 2min < 10min 间隔 → 节流,不再 last_call
        assert d.due is False


class TestPerSourceIsolation:
    def test_fotmob_snapshot_uses_legacy(self):
        # 快照来源仍是旧两档:24h → 900s、1h → 300s
        kickoff = _kickoff(hours=24)
        exactly_900 = _iso(NOW - timedelta(seconds=900))
        assert _decide(kickoff, exactly_900, source=SOURCE_FOTMOB_SNAPSHOT).due is True
        just_before_900 = _iso(NOW - timedelta(seconds=899))
        assert _decide(kickoff, just_before_900, source=SOURCE_FOTMOB_SNAPSHOT).due is False

    def test_required_interval_seconds_unchanged(self):
        """required_interval_seconds 输出必须与历史一字不改(FotMob/content_pipeline 依赖)。"""
        now = _iso(NOW)
        assert required_interval_seconds(_kickoff(hours=24), EXACT, SRC, now) == 900
        assert required_interval_seconds(_kickoff(hours=1), EXACT, SRC, now) == 300
        assert required_interval_seconds(_kickoff(hours=80), EXACT, SRC, now) is None
        assert required_interval_seconds(_kickoff(hours=-1), EXACT, SRC, now) is None


class TestConstitutionFloors:
    def test_every_cadence_respects_constitution_floors(self):
        """CLAUDE.md §6.3 下限:2–72h ≥900s、0–2h ≥300s。所有 cadence 逐档不得低于下限。"""
        for cadence in (CADENCE_LEGACY, CADENCE_NOWGOAL_ODDS):
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
