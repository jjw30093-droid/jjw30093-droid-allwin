"""体能统计(physical_metrics_distance_covered)迟到补采:纯判断 + 落库/告警。

覆盖:
- due_checkpoint() 全部分支;
- is_valid_distance() 全部分支;
- 任务函数(backend/cli/poll_physical_stats.run_due)对 fixture 的端到端行为:
  第一检查点即达标 → resolved,不再重跑;三次检查点均未达标 → exhausted +
  恰好一次告警;非英超比赛即便时间上"到期"也完全不被触碰。
"""

import json

import pytest

from backend import notify as notify_mod
from backend.db.connections import connect_rw
from backend.ingest.physical_stats_poll import (
    CANDIDATE_WINDOW_HOURS,
    MAX_CHECKS,
    VALID_DISTANCE_THRESHOLD_M,
    CheckpointDecision,
    due_checkpoint,
    is_valid_distance,
    within_candidate_window,
)


# ── due_checkpoint() ────────────────────────────────────────────────────

T_KICKOFF = "2026-08-01T12:00:00Z"


class TestDueCheckpoint:
    def test_no_kickoff_never_due(self):
        d = due_checkpoint(None, 0, False, False, "2026-08-05T00:00:00Z")
        assert d.due is False and d.reason == "no_kickoff_at_utc"

    def test_resolved_never_due(self):
        d = due_checkpoint(T_KICKOFF, 1, True, False, "2026-09-01T00:00:00Z")
        assert d.due is False and d.reason == "already_resolved"

    def test_exhausted_never_due(self):
        d = due_checkpoint(T_KICKOFF, 3, False, True, "2026-09-01T00:00:00Z")
        assert d.due is False and d.reason == "already_exhausted"

    def test_checks_done_at_max_transitional_not_due(self):
        d = due_checkpoint(T_KICKOFF, MAX_CHECKS, False, False, "2026-09-01T00:00:00Z")
        assert d.due is False and d.reason == "checks_exhausted_pending_finalize"

    @pytest.mark.parametrize("checks_done,hours,expected_checkpoint", [
        (0, 5.99, 1), (1, 11.99, 2), (2, 23.99, 3),
    ])
    def test_not_yet_due_before_checkpoint(self, checks_done, hours, expected_checkpoint):
        now = f"2026-08-01T{12 + hours:.2f}"  # not used directly; build via offset below
        from datetime import datetime, timedelta, timezone
        now_dt = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(hours=hours)
        now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        d = due_checkpoint(T_KICKOFF, checks_done, False, False, now_iso)
        assert d.due is False
        assert d.checkpoint == expected_checkpoint
        assert d.reason == f"not_yet_due_checkpoint_{expected_checkpoint}"

    @pytest.mark.parametrize("checks_done,hours,expected_checkpoint", [
        (0, 6.0, 1), (0, 7.0, 1),
        (1, 12.0, 2), (1, 13.0, 2),
        (2, 24.0, 3), (2, 30.0, 3),
    ])
    def test_due_at_and_after_checkpoint(self, checks_done, hours, expected_checkpoint):
        from datetime import datetime, timedelta, timezone
        now_dt = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(hours=hours)
        now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        d = due_checkpoint(T_KICKOFF, checks_done, False, False, now_iso)
        assert d.due is True
        assert d.checkpoint == expected_checkpoint
        assert d.reason == f"due_checkpoint_{expected_checkpoint}"

    def test_returns_checkpoint_decision_dataclass(self):
        d = due_checkpoint(T_KICKOFF, 0, False, False, "2026-08-01T18:00:00Z")
        assert isinstance(d, CheckpointDecision)

    def test_multi_year_old_match_never_due_real_incident_regression(self):
        """2026-08-25 真实生产事故的直接回归测试:上线首次运行时,
        _candidate_rows() 没有按 kickoff 时间过滤,库里全部历史 Finish
        英超比赛(2020 年至今)都因为"没有状态行"满足候选条件,due_checkpoint()
        当时也只判断下限(elapsed_hours >= 6),对一场 2020 年的比赛同样返回
        due=True——已经对 42 场历史比赛触发了不必要的 ingest_match() 重抓才
        被人工发现并紧急停服务。这里直接用事故复现的真实开球时间钉住修复:
        checks_done=0(从未检查过,和事故现场完全一致的状态)+ 一场六年前的
        比赛,必须返回 not due,理由是超出候选窗口,而不是"due_checkpoint_1"。
        """
        d = due_checkpoint(
            "2020-09-12T00:00:00Z", 0, False, False, "2026-08-25T18:05:21Z"
        )
        assert d.due is False
        assert d.reason == "kickoff_outside_candidate_window"


class TestWithinCandidateWindow:
    def test_no_kickoff_false(self):
        assert within_candidate_window(None, "2026-08-25T00:00:00Z") is False

    def test_just_inside_window_true(self):
        from datetime import datetime, timedelta, timezone
        now = datetime(2026, 8, 25, 18, 0, 0, tzinfo=timezone.utc)
        kickoff = now - timedelta(hours=CANDIDATE_WINDOW_HOURS - 0.01)
        assert within_candidate_window(
            kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"), now.strftime("%Y-%m-%dT%H:%M:%SZ")
        ) is True

    def test_just_outside_window_false(self):
        from datetime import datetime, timedelta, timezone
        now = datetime(2026, 8, 25, 18, 0, 0, tzinfo=timezone.utc)
        kickoff = now - timedelta(hours=CANDIDATE_WINDOW_HOURS + 0.01)
        assert within_candidate_window(
            kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"), now.strftime("%Y-%m-%dT%H:%M:%SZ")
        ) is False

    def test_years_old_kickoff_false(self):
        assert within_candidate_window("2020-09-12T00:00:00Z", "2026-08-25T18:05:21Z") is False

    def test_future_kickoff_false(self):
        """kickoff 晚于 now(比如时钟偏差/脏数据)同样不该判定在候选窗口内——
        elapsed_hours 为负,不是"刚完赛"的正常状态。"""
        assert within_candidate_window("2026-08-26T00:00:00Z", "2026-08-25T18:00:00Z") is False


# ── is_valid_distance() ─────────────────────────────────────────────────

class TestIsValidDistance:
    def test_both_valid(self):
        assert is_valid_distance(VALID_DISTANCE_THRESHOLD_M, VALID_DISTANCE_THRESHOLD_M) is True
        assert is_valid_distance(105000.0, 98000.0) is True

    def test_home_missing(self):
        assert is_valid_distance(None, 105000.0) is False

    def test_away_missing(self):
        assert is_valid_distance(105000.0, None) is False

    def test_both_missing(self):
        assert is_valid_distance(None, None) is False

    def test_home_below_threshold(self):
        assert is_valid_distance(9000.0, 105000.0) is False

    def test_away_below_threshold(self):
        assert is_valid_distance(105000.0, 9000.0) is False

    def test_both_below_threshold(self):
        assert is_valid_distance(4000.0, 9500.0) is False

    def test_exactly_at_threshold_counts_as_valid(self):
        assert is_valid_distance(VALID_DISTANCE_THRESHOLD_M, VALID_DISTANCE_THRESHOLD_M) is True


# ── 任务函数(端到端,fixture 数据) ─────────────────────────────────────

EPL = 47
NON_EPL = 48  # 英冠:已登记 dim_league_season_regime 但不在 PHYSICAL_STATS_LEAGUE_IDS
HOME_TEAM_ID, AWAY_TEAM_ID = 111, 222


def _seed_match(conn, match_id, league_id, kickoff_at_utc, season="2026/2027"):
    conn.execute(
        """INSERT INTO dim_match
             (Match_ID, Season, League_ID, Date, Home_Team_ID, Away_Team_ID,
              Home_Team_Name, Away_Team_Name, home_score, away_score, status,
              kickoff_at_utc, kickoff_precision, kickoff_source)
           VALUES (?, ?, ?, ?, ?, ?, 'Home', 'Away', 1, 0, 'Finish', ?, 'exact', 'fotmob')""",
        (match_id, season, league_id, kickoff_at_utc[:10], HOME_TEAM_ID, AWAY_TEAM_ID, kickoff_at_utc),
    )
    conn.commit()


def _seed_stats(conn, match_id, home_distance, away_distance):
    """写 fact_team_match_stats(Period='All')两队行,physical_metrics_distance_covered
    落在 extra_json(与真实 ingest_match 落库形状一致:非核心列进 extra_json)。"""
    conn.execute("DELETE FROM fact_team_match_stats WHERE Match_ID=?", (match_id,))
    for team_id, dist in ((HOME_TEAM_ID, home_distance), (AWAY_TEAM_ID, away_distance)):
        extra = {} if dist is None else {"physical_metrics_distance_covered": dist}
        conn.execute(
            "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
            " VALUES (?, ?, 'All', 1, ?)",
            (match_id, team_id, json.dumps(extra)),
        )
    conn.commit()


@pytest.fixture
def conn_core(data_dir):
    conn = connect_rw("core")
    yield conn
    conn.close()


@pytest.fixture
def alert_calls(monkeypatch):
    calls = []
    real_notify = notify_mod.notify

    def spy(*args, **kwargs):
        res = real_notify(*args, **kwargs)
        calls.append({"args": args, "kwargs": kwargs, "result": res})
        return res

    monkeypatch.setattr(notify_mod, "notify", spy)
    return calls


def _stub_ingest_match(monkeypatch, module, apply_side_effect):
    """替身 ingest_match:不打真实网络,只按测试场景写入 fact_team_match_stats。
    调用签名与真实 ingest_match(match_id, league_id=...) 一致。"""
    calls = []

    def fake(match_id, league_id=None, date=None):
        calls.append(match_id)
        apply_side_effect(match_id)

    monkeypatch.setattr("backend.ingest.ingest_match.ingest_match", fake)
    return calls


class TestJobResolvesAtFirstValidCheckpoint:
    def test_epl_match_valid_at_checkpoint_one_resolves_and_stops(
        self, conn_core, monkeypatch, alert_calls
    ):
        monkeypatch.setenv("NOTIFY_ENABLED", "0")
        match_id = 700001
        _seed_match(conn_core, match_id, EPL, "2026-08-01T12:00:00Z")
        _seed_stats(conn_core, match_id, None, None)  # 初始缺失

        def side_effect(mid):
            _seed_stats(conn_core, mid, 105000.0, 98000.0)  # 抓回后已达标

        calls = _stub_ingest_match(monkeypatch, None, side_effect)

        from backend.cli.poll_physical_stats import run_due

        now_iso = "2026-08-01T18:00:00Z"  # kickoff+6h,checkpoint 1 到期
        result = run_due(now_iso=now_iso)
        assert result["acted"] == 1
        assert calls == [match_id]

        row = conn_core.execute(
            "SELECT checks_done, resolved_at, exhausted_at FROM physical_stats_poll_state"
            " WHERE match_id=?", (match_id,),
        ).fetchone()
        assert row["checks_done"] == 1
        assert row["resolved_at"] is not None
        assert row["exhausted_at"] is None

        # 第二次跑(哪怕仍在"到期"时间窗内)不应再触发 ingest_match
        result2 = run_due(now_iso="2026-08-02T00:00:00Z")
        assert calls == [match_id], "已 resolved 的比赛不应再被处理"
        assert result2["acted"] == 0
        assert len(alert_calls) == 0


class TestJobExhaustsAfterThreeCheckpoints:
    def test_never_valid_exhausts_and_alerts_exactly_once(self, conn_core, monkeypatch, alert_calls):
        monkeypatch.setenv("NOTIFY_ENABLED", "0")
        match_id = 700002
        _seed_match(conn_core, match_id, EPL, "2026-08-01T12:00:00Z")
        _seed_stats(conn_core, match_id, None, None)

        def side_effect(mid):
            _seed_stats(conn_core, mid, 4000.0, 5000.0)  # 始终是 partial 值,永不达标

        calls = _stub_ingest_match(monkeypatch, None, side_effect)

        from backend.cli.poll_physical_stats import run_due

        run_due(now_iso="2026-08-01T18:00:00Z")   # checkpoint 1 (+6h)
        run_due(now_iso="2026-08-02T00:00:00Z")   # checkpoint 2 (+12h)
        result3 = run_due(now_iso="2026-08-02T12:00:00Z")  # checkpoint 3 (+24h)

        assert calls == [match_id] * 3
        assert result3["acted"] == 1
        assert result3["results"][0]["action"] == "exhausted"

        row = conn_core.execute(
            "SELECT checks_done, resolved_at, exhausted_at, fail_reason"
            " FROM physical_stats_poll_state WHERE match_id=?", (match_id,),
        ).fetchone()
        assert row["checks_done"] == 3
        assert row["resolved_at"] is None
        assert row["exhausted_at"] is not None
        assert row["fail_reason"]
        assert len(alert_calls) == 1, "耗尽必须恰好告警一次"
        assert alert_calls[0]["kwargs"]["level"] == "CRITICAL"
        assert str(match_id) in alert_calls[0]["kwargs"]["dedup_key"]

        # 后续再跑,不应重复计数或重复告警
        result4 = run_due(now_iso="2026-08-05T00:00:00Z")
        assert result4["acted"] == 0
        assert calls == [match_id] * 3, "耗尽后不应继续调用 ingest_match"
        assert len(alert_calls) == 1, "耗尽后续 tick 不得重复告警"


class TestNonEplMatchUntouched:
    def test_non_epl_match_never_processed(self, conn_core, monkeypatch, alert_calls):
        monkeypatch.setenv("NOTIFY_ENABLED", "0")
        match_id = 700003
        _seed_match(conn_core, match_id, NON_EPL, "2026-08-01T12:00:00Z")
        _seed_stats(conn_core, match_id, None, None)

        calls = _stub_ingest_match(monkeypatch, None, lambda mid: None)

        from backend.cli.poll_physical_stats import run_due

        result = run_due(now_iso="2026-08-02T12:00:00Z")  # 时间上早已过 24h 检查点
        assert result["acted"] == 0
        assert calls == [], "非英超比赛不得触发 ingest_match"

        row = conn_core.execute(
            "SELECT 1 FROM physical_stats_poll_state WHERE match_id=?", (match_id,)
        ).fetchone()
        assert row is None, "非英超比赛不应创建状态行"


class TestOldMatchNeverTouchedRealIncidentRegression:
    """2026-08-25 真实生产事故的端到端回归:直接复现事故现场——一场没有
    状态行(从未被本任务处理过)、开球时间是六年前的英超已完赛比赛,跑
    run_due() 必须完全不触碰它(既不建状态行,也不调用 ingest_match())。
    这条测试覆盖的是 _candidate_rows() 的 SQL 层过滤,不是 due_checkpoint()
    纯函数本身——事故当时正是 SQL 层完全没有时间过滤,才把 due_checkpoint()
    从未设计要处理的输入(六年前的 kickoff)喂了进去。"""

    def test_ancient_finished_match_untouched_on_first_ever_run(
        self, conn_core, monkeypatch, alert_calls
    ):
        monkeypatch.setenv("NOTIFY_ENABLED", "0")
        match_id = 700004
        _seed_match(conn_core, match_id, EPL, "2020-09-12T00:00:00Z", season="2020/2021")
        _seed_stats(conn_core, match_id, None, None)

        calls = _stub_ingest_match(monkeypatch, None, lambda mid: None)

        from backend.cli.poll_physical_stats import run_due

        # 首次上线运行,状态表整表为空——正是事故复现的初始条件。
        result = run_due(now_iso="2026-08-25T18:05:21Z")
        assert result["acted"] == 0
        assert calls == [], "开球时间早已超出候选窗口的比赛不得触发 ingest_match"

        row = conn_core.execute(
            "SELECT 1 FROM physical_stats_poll_state WHERE match_id=?", (match_id,)
        ).fetchone()
        assert row is None, "超出候选窗口的比赛不应创建状态行"
        assert len(alert_calls) == 0
