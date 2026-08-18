"""赛后完赛增量单比赛重试上限(PIPELINE_REDESIGN_V2 P4,3.3 安全要求)。

事件驱动到期条件("kickoff+2.5h 仍未 Finish 即到期")一旦脱离旧的 6h/联赛
节流,对 FotMob 永远不翻 Finish 的比赛(数据源缺陷)会变成永久到期、无限
重试。本模块给每个 match_id 计数,达到 MAX_ATTEMPTS 后停止自动重试、记录
失败原因、经 backend.notify 推一次 CRITICAL 告警(24h dedup,不逐 tick 重推)。

覆盖:
- 未达上限的比赛持续计数、不告警;
- 达到/超过上限的比赛停止计数、恰好告警一次(不是每个 tick 都告警);
- 在触顶前已解决(不再 stale)的比赛不得被误判为耗尽。
"""

import pytest

from backend import notify as notify_mod
from backend.db.connections import connect_rw
from backend.ingest import postmatch_retry

T0 = "2026-08-17T13:47:28Z"


@pytest.fixture
def conn_odds(data_dir):
    conn = connect_rw("odds")
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


class TestUnderCapKeepsRetrying:
    def test_attempts_below_cap_do_not_exhaust(self, conn_odds):
        for i in range(postmatch_retry.MAX_ATTEMPTS - 1):
            result = postmatch_retry.process_match_attempt(conn_odds, 9001, 47, T0)
            assert result["newly_exhausted"] is False
        assert not postmatch_retry.is_exhausted(conn_odds, 9001)
        assert postmatch_retry.exhausted_match_ids(conn_odds, 47) == set()


class TestAtCapStopsAndAlertsOnce:
    def test_reaching_cap_marks_exhausted_with_reason(self, conn_odds, alert_calls, monkeypatch):
        monkeypatch.setenv("NOTIFY_ENABLED", "0")  # 只验证落库+调用,不打真实网络
        result = None
        for _ in range(postmatch_retry.MAX_ATTEMPTS):
            result = postmatch_retry.process_match_attempt(conn_odds, 9002, 47, T0)
        assert result["newly_exhausted"] is True
        assert postmatch_retry.is_exhausted(conn_odds, 9002)

        row = conn_odds.execute(
            "SELECT attempts, exhausted_at, fail_reason FROM postmatch_retry_state WHERE match_id=?",
            (9002,),
        ).fetchone()
        assert row["attempts"] >= postmatch_retry.MAX_ATTEMPTS
        assert row["exhausted_at"] is not None
        assert row["fail_reason"], "必须记录清晰的失败原因,不能静默丢弃"

    def test_alert_dispatched_exactly_once_not_per_tick(self, conn_odds, alert_calls, monkeypatch):
        monkeypatch.setenv("NOTIFY_ENABLED", "0")
        for _ in range(postmatch_retry.MAX_ATTEMPTS):
            postmatch_retry.process_match_attempt(conn_odds, 9003, 47, T0)
        assert len(alert_calls) == 1, "达到上限那一刻应恰好告警一次"

        # 模拟后续多个 tick 仍未解决:调用方应先用 is_exhausted 跳过,
        # 不应继续对同一 match_id 调用 process_match_attempt——但即便误调用,
        # 也不应重复告警或继续计数。
        for _ in range(5):
            result = postmatch_retry.process_match_attempt(conn_odds, 9003, 47, T0)
            assert result["newly_exhausted"] is False
        assert len(alert_calls) == 1, "耗尽后续 tick 不得重复告警"

        row = conn_odds.execute(
            "SELECT attempts FROM postmatch_retry_state WHERE match_id=?", (9003,)
        ).fetchone()
        assert row["attempts"] == postmatch_retry.MAX_ATTEMPTS, "耗尽后不应继续计数"

    def test_alert_uses_critical_level_and_per_match_dedup_key(self, conn_odds, alert_calls, monkeypatch):
        monkeypatch.setenv("NOTIFY_ENABLED", "0")
        for _ in range(postmatch_retry.MAX_ATTEMPTS):
            postmatch_retry.process_match_attempt(conn_odds, 9004, 47, T0)
        assert len(alert_calls) == 1
        kwargs = alert_calls[0]["kwargs"]
        assert kwargs.get("level") == "CRITICAL"
        assert "9004" in kwargs.get("dedup_key", ""), "dedup_key 必须按 match_id 区分,不能笼统合并"

    def test_concurrent_racing_callers_alert_exactly_once(self, conn_odds, alert_calls, monkeypatch):
        """2026-08-17 真实发现:mark_exhausted_and_alert 原来是 check-then-act
        (先无条件 UPDATE exhausted_at,再无条件 notify()),不是原子声明。
        notify() 的 24h dedup 靠不住做这层保护——它先检查 NOTIFY_ENABLED,
        测试/以及生产里真实推送耗时(ServerChan ~10s 网络调用)期间,两个几乎
        同时到达"这场比赛已耗尽"判定的调用者(如 systemd 定时调用与旁路手动
        直接调用 fotmob_incremental_multi.py 的 main(),后者不经过
        runner._acquire_lock)都可能在对方 notify_result='sent' 落库前就已经
        通过了各自的 is_exhausted() 前置检查。这里直接调用两次
        mark_exhausted_and_alert 模拟这种竞态(不经过 process_match_attempt
        的单进程顺序执行 is_exhausted 门禁),必须只有一次真正触发 notify()。
        """
        monkeypatch.setenv("NOTIFY_ENABLED", "0")
        for _ in range(postmatch_retry.MAX_ATTEMPTS):
            postmatch_retry.record_attempt(conn_odds, 9008, 47, T0)
        # 到这里 attempts=MAX_ATTEMPTS、exhausted_at 仍是 NULL(record_attempt
        # 本身不设 exhausted_at)——模拟两个调用者都刚判定完"该标记耗尽了",
        # 几乎同时调用 mark_exhausted_and_alert。
        first = postmatch_retry.mark_exhausted_and_alert(
            conn_odds, 9008, 47, postmatch_retry.MAX_ATTEMPTS, T0
        )
        second = postmatch_retry.mark_exhausted_and_alert(
            conn_odds, 9008, 47, postmatch_retry.MAX_ATTEMPTS, T0
        )
        assert len(alert_calls) == 1, "两个几乎同时声明'已耗尽'的调用者只能有一个真正告警"
        assert first["result"] != "already_claimed"
        assert second["result"] == "already_claimed"


class TestJobLevelRetryDoesNotDoubleCountMatchRetries:
    def test_fotmob_incremental_multi_has_no_job_level_retry(self):
        """2026-08-17 真实发现:REGISTRY["fotmob_incremental_multi"] 原来
        max_attempts=2、backoff_seconds=120。main() 只要有一个联赛的抓取
        抛异常就整体 exit 1(_record_match_retry_attempts 对所有已尝试联赛
        无条件跑过,不按 league_ok 门禁)——runner 的任务级重试于是在约 120
        秒后重新整体跑一遍 --due,同一批仍然 stale 的比赛(包括那些本来已经
        成功抓取、只是恰好跟失败联赛同一轮被处理的)会在同一个 30 分钟
        tick 内被 _record_match_retry_attempts 计数两次,MAX_ATTEMPTS=20
        的"约 10 小时/kickoff+12.5h 放弃"这个安全余量因此可能被吃掉一半,
        且可能对仍在正常工作、只是这一轮撞上别的联赛瞬时代理故障的比赛
        提前触发"已耗尽"告警。

        比赛级别的重试计数(postmatch_retry)已经是这件事正确的责任层——
        allwin-postmatch.timer 本身每 30 分钟才 tick 一次,这就是"再试一次"
        该发生的天然节奏,任务级快速重试(120 秒后)只会在同一个逻辑 tick
        内重复计数,不提供任何额外价值。修复是让这个任务退回这个仓库里
        绝大多数任务已经在用的默认值 max_attempts=1(backend/worker/
        runner.py:555 `spec.setdefault("max_attempts", 1)`),不是新发明的
        特例。"""
        from backend.worker.runner import REGISTRY

        spec = REGISTRY["fotmob_incremental_multi"]
        assert spec.get("max_attempts", 1) == 1, (
            "fotmob_incremental_multi 不得有任务级重试——比赛级重试计数"
            "(postmatch_retry)已经是正确的责任层,任务级快速重试只会在"
            "同一个 30 分钟 tick 内让计数翻倍,提前吃掉 MAX_ATTEMPTS 余量"
        )


class TestResolvedBeforeCapNotFalselyFlagged:
    def test_match_that_resolves_before_cap_is_cleared_not_exhausted(self, conn_odds, alert_calls, monkeypatch):
        monkeypatch.setenv("NOTIFY_ENABLED", "0")
        for _ in range(postmatch_retry.MAX_ATTEMPTS - 3):
            postmatch_retry.process_match_attempt(conn_odds, 9005, 47, T0)
        assert not postmatch_retry.is_exhausted(conn_odds, 9005)

        # 比赛已解决(不再 stale)——调用方(fotmob_incremental_multi)应清空其重试状态
        postmatch_retry.clear_retry_state(conn_odds, 9005)

        assert not postmatch_retry.is_exhausted(conn_odds, 9005)
        assert postmatch_retry.exhausted_match_ids(conn_odds, 47) == set()
        row = conn_odds.execute(
            "SELECT * FROM postmatch_retry_state WHERE match_id=?", (9005,)
        ).fetchone()
        assert row is None, "已解决的比赛不应继续占用重试状态行"
        assert len(alert_calls) == 0, "从未达到上限,不应有任何告警"

    def test_tracked_match_ids_reflects_only_active_rows(self, conn_odds):
        postmatch_retry.process_match_attempt(conn_odds, 9006, 47, T0)
        postmatch_retry.process_match_attempt(conn_odds, 9007, 47, T0)
        assert postmatch_retry.tracked_match_ids(conn_odds, 47) == {9006, 9007}
        postmatch_retry.clear_retry_state(conn_odds, 9006)
        assert postmatch_retry.tracked_match_ids(conn_odds, 47) == {9007}
