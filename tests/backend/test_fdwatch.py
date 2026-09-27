"""backend/cli/fdwatch.py:fd 采样 + 看门狗(2026-09-27)。判断逻辑是纯函数 decide;
run_once 用注入的 restart 回调与临时目录验证:不真的重启任何服务。"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.cli import fdwatch as fw

T0 = 1_800_000_000.0


class TestDecide:
    def test_healthy_resets_failures_and_does_nothing(self):
        st = fw.State(fails=2)
        d = fw.decide(st, readyz_ok=True, fd_count=10, soft_limit=1024, now=T0)
        assert d.action == "none" and st.fails == 0

    def test_three_consecutive_failures_restart(self):
        st = fw.State()
        assert fw.decide(st, readyz_ok=False, fd_count=10, soft_limit=1024, now=T0).action == "none"
        assert fw.decide(st, readyz_ok=False, fd_count=10, soft_limit=1024, now=T0 + 60).action == "none"
        d = fw.decide(st, readyz_ok=False, fd_count=10, soft_limit=1024, now=T0 + 120)
        assert d.action == "restart" and "连续 3 次" in d.reasons[0] and d.fails == 3
        assert st.fails == 0 and st.last_restart_ts == T0 + 120

    def test_a_single_success_breaks_the_streak(self):
        st = fw.State()
        for i, ok in enumerate([False, False, True, False, False]):
            d = fw.decide(st, readyz_ok=ok, fd_count=10, soft_limit=1024, now=T0 + 60 * i)
            assert d.action == "none", i

    def test_fd_over_70_percent_restarts_immediately(self):
        st = fw.State()
        assert fw.decide(st, readyz_ok=True, fd_count=716, soft_limit=1024, now=T0).action == "none"  # 70.0%
        d = fw.decide(st, readyz_ok=True, fd_count=717, soft_limit=1024, now=T0 + 60)
        assert d.action == "restart" and "超过软上限 1024 的 70%" in d.reasons[0]

    def test_threshold_scales_with_soft_limit(self):
        st = fw.State()
        assert fw.decide(st, readyz_ok=True, fd_count=45000, soft_limit=65536, now=T0).action == "none"
        assert fw.decide(st, readyz_ok=True, fd_count=45876, soft_limit=65536, now=T0 + 60).action == "restart"

    def test_cooldown_only_one_restart_per_15_minutes(self):
        st = fw.State()
        first = fw.decide(st, readyz_ok=True, fd_count=900, soft_limit=1024, now=T0)
        assert first.action == "restart"
        again = fw.decide(st, readyz_ok=True, fd_count=900, soft_limit=1024, now=T0 + 14 * 60 + 59)
        assert again.action == "suppressed" and again.reasons
        assert st.last_restart_ts == T0, "被抑制时不得刷新最后重启时间"
        after = fw.decide(st, readyz_ok=True, fd_count=900, soft_limit=1024, now=T0 + 15 * 60)
        assert after.action == "restart"

    def test_failure_streak_reset_after_suppressed_so_no_instant_retrigger(self):
        st = fw.State(last_restart_ts=T0)
        for i in range(3):
            d = fw.decide(st, readyz_ok=False, fd_count=5, soft_limit=1024, now=T0 + 60 * (i + 1))
        assert d.action == "suppressed" and st.fails == 0

    def test_missing_fd_or_limit_never_triggers_fd_rule(self):
        st = fw.State()
        assert fw.decide(st, readyz_ok=True, fd_count=None, soft_limit=None, now=T0).action == "none"
        assert fw.decide(st, readyz_ok=True, fd_count=5000, soft_limit=None, now=T0).action == "none"


class TestParsing:
    def test_soft_limit(self):
        text = "Limit    Soft Limit    Hard Limit   Units\nMax open files            1024                 524288               files\n"
        assert fw.parse_soft_limit(text) == 1024
        assert fw.parse_soft_limit("") is None
        assert fw.parse_soft_limit("Max open files  unlimited  unlimited files") is None

    def test_normalize_target(self):
        assert fw.normalize_target("127.0.0.1:8000->127.0.0.1:50826 (ESTABLISHED)") == "127.0.0.1:8000->127.0.0.1:* (ESTABLISHED)"
        assert fw.normalize_target("127.0.0.1:8000 (LISTEN)") == "127.0.0.1:8000 (LISTEN)"
        assert fw.normalize_target("/opt/allwin/shared/data/allwin.db") == "/opt/allwin/shared/data/allwin.db"

    def test_top_targets_only_counts_real_fds_and_aggregates(self):
        lsof = "\n".join([
            "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME",
            "uvicorn 1 allwin cwd DIR 8,1 4096 2 /opt/allwin/current",
            "uvicorn 1 allwin mem REG 8,1 100 2 /usr/lib/libc.so.6",
            "uvicorn 1 allwin 3u IPv4 111 0t0 TCP 127.0.0.1:8000 (LISTEN)",
            "uvicorn 1 allwin 5u IPv4 112 0t0 TCP 127.0.0.1:8000->127.0.0.1:50001 (ESTABLISHED)",
            "uvicorn 1 allwin 6u IPv4 113 0t0 TCP 127.0.0.1:8000->127.0.0.1:50002 (ESTABLISHED)",
            "uvicorn 1 allwin 7u IPv4 114 0t0 TCP 127.0.0.1:8000->127.0.0.1:50003 (CLOSE_WAIT)",
            "uvicorn 1 allwin 8r REG 8,1 100 2 /opt/allwin/shared/data/odds.db",
        ])
        top = fw.top_targets(lsof)
        assert (2, "127.0.0.1:8000->127.0.0.1:* (ESTABLISHED)") in top
        assert (1, "127.0.0.1:8000->127.0.0.1:* (CLOSE_WAIT)") in top
        assert all("libc" not in name and "current" not in name for _, name in top)  # cwd/mem 不算 fd
        assert top[0][0] == 2

    def test_top_targets_limited_to_n(self):
        lines = ["H"] + [f"p 1 u {i}u REG 1 1 1 /f{i}" for i in range(40)]
        assert len(fw.top_targets("\n".join(lines), n=20)) == 20

    def test_summarize_ss_counts_states_for_port(self):
        ss = "\n".join([
            "State Recv-Q Send-Q Local:Port Peer:Port",
            "LISTEN 0 2048 127.0.0.1:8000 0.0.0.0:*",
            "ESTAB 0 0 127.0.0.1:8000 127.0.0.1:50001",
            "ESTAB 0 0 127.0.0.1:50002 127.0.0.1:8000",
            "TIME-WAIT 0 0 127.0.0.1:8000 127.0.0.1:50003",
            "ESTAB 0 0 127.0.0.1:3000 1.2.3.4:9999",
        ])
        assert fw.summarize_ss(ss, 8000) == {"LISTEN": 1, "ESTAB": 2, "TIME-WAIT": 1}


class TestRetention:
    def test_prunes_only_old_daily_files(self, tmp_path):
        now = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
        for d in ("20260912", "20260913", "20260914", "20260927"):
            (tmp_path / f"fd_{d}.log").write_text("x")
        (tmp_path / "watchdog.log").write_text("keep")
        (tmp_path / "restart_snapshots").mkdir()
        removed = fw.prune_old(tmp_path, now, retention_days=14)
        assert removed == ["fd_20260912.log"]  # cutoff = 20260913
        assert (tmp_path / "fd_20260913.log").exists() and (tmp_path / "watchdog.log").exists()
        assert (tmp_path / "restart_snapshots").is_dir()


class TestRunOnce:
    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        calls = {"restart": []}
        monkeypatch.setattr(fw, "service_state", lambda s: "active")
        monkeypatch.setattr(fw, "main_pid", lambda s: 4242)
        monkeypatch.setattr(fw, "read_limits", lambda pid: "Max open files            1024                 524288               files\n")
        monkeypatch.setattr(fw, "_run", lambda cmd, timeout=15: "")
        return calls, tmp_path

    def _run(self, env, monkeypatch, *, fds, ready_ok, now, dry_run=False):
        calls, tmp = env
        monkeypatch.setattr(fw, "fd_count", lambda pid: fds)
        monkeypatch.setattr(fw, "check_readyz", lambda url, timeout=5: (ready_ok, "200" if ready_ok else "503", 7))
        return fw.run_once(
            log_dir=tmp / "logs", state_file=tmp / "state.json", now=now, dry_run=dry_run,
            restart=lambda s: calls["restart"].append(s),
        )

    def test_writes_daily_sample_file(self, env, monkeypatch):
        now = datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc)
        d = self._run(env, monkeypatch, fds=11, ready_ok=True, now=now)
        assert d.action == "none" and env[0]["restart"] == []
        text = (env[1] / "logs" / "fd_20260927.log").read_text()
        assert "pid=4242 fd=11 soft=1024" in text and "readyz=200(7ms)" in text

    def test_restart_saves_snapshot_and_calls_restart(self, env, monkeypatch):
        now = datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc)
        d = self._run(env, monkeypatch, fds=900, ready_ok=True, now=now)
        assert d.action == "restart" and env[0]["restart"] == ["allwin-api"]
        snap = env[1] / "logs" / "restart_snapshots" / "20260927T030000Z"
        assert (snap / "reasons.txt").exists() and (snap / "sample.txt").exists()
        assert (snap / "ss_tanp.txt").exists() and (snap / "journal_tail.txt").exists()
        # 最近 5 分钟慢请求:目录不存在时如实说明,不是报错也不是留空文件
        assert "没有慢请求记录" in (snap / "slow_requests_last5min.txt").read_text()
        assert "RESTART allwin-api" in (env[1] / "logs" / "watchdog.log").read_text()
        assert "DECISION: restart" in (env[1] / "logs" / "fd_20260927.log").read_text()

    def test_restart_snapshot_includes_recent_slow_requests(self, env, monkeypatch):
        calls, tmp = env
        slow_dir = tmp / "slow_requests"
        slow_dir.mkdir()
        now = datetime(2026, 9, 27, 3, 0, 0, tzinfo=timezone.utc)
        (slow_dir / "slow_20260927.log").write_text(
            "2026-09-27T02:57:00Z GET /api/v1/matches/{match_id} 200 2.500s\n"  # 3 分钟前,在窗口内
            "2026-09-27T02:40:00Z GET /api/v1/leagues 200 3.100s\n"  # 20 分钟前,窗口外
        )
        monkeypatch.setattr(fw, "fd_count", lambda pid: 900)
        monkeypatch.setattr(fw, "check_readyz", lambda url, timeout=5: (True, "200", 7))
        d = fw.run_once(
            log_dir=tmp / "logs", state_file=tmp / "state.json", now=now,
            restart=lambda s: calls["restart"].append(s), slow_log_dir=slow_dir,
        )
        assert d.action == "restart"
        text = (tmp / "logs" / "restart_snapshots" / "20260927T030000Z" / "slow_requests_last5min.txt").read_text()
        assert "/api/v1/matches/{match_id}" in text
        assert "/api/v1/leagues" not in text

    def test_second_trigger_within_15_minutes_is_only_logged(self, env, monkeypatch):
        t = datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc)
        self._run(env, monkeypatch, fds=900, ready_ok=True, now=t)
        d = self._run(env, monkeypatch, fds=900, ready_ok=True, now=t + timedelta(minutes=5))
        assert d.action == "suppressed" and len(env[0]["restart"]) == 1
        assert "SUPPRESSED" in (env[1] / "logs" / "watchdog.log").read_text()

    def test_consecutive_readyz_failures_persist_across_runs_via_state_file(self, env, monkeypatch):
        t = datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc)
        acts = [self._run(env, monkeypatch, fds=10, ready_ok=False, now=t + timedelta(minutes=i)).action for i in range(3)]
        assert acts == ["none", "none", "restart"]
        assert json.loads((env[1] / "state.json").read_text())["last_restart_ts"] is not None

    def test_dry_run_does_not_restart(self, env, monkeypatch):
        t = datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc)
        d = self._run(env, monkeypatch, fds=900, ready_ok=True, now=t, dry_run=True)
        assert d.action == "restart" and env[0]["restart"] == []
        assert (env[1] / "logs" / "restart_snapshots" / "20260927T030000Z").exists()

    def test_activating_service_is_not_counted_as_failure(self, env, monkeypatch):
        monkeypatch.setattr(fw, "service_state", lambda s: "activating")
        monkeypatch.setattr(fw, "main_pid", lambda s: 0)
        t = datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc)
        for i in range(5):
            d = self._run(env, monkeypatch, fds=None, ready_ok=False, now=t + timedelta(minutes=i))
            assert d.action == "none"
        assert env[0]["restart"] == []


class TestUnits:
    def test_units_exist_and_run_every_minute_as_root(self):
        root = Path(__file__).resolve().parents[2] / "deploy" / "systemd"
        svc = (root / "allwin-fdwatch.service").read_text()
        timer = (root / "allwin-fdwatch.timer").read_text()
        assert "User=root" in svc and "backend/cli/fdwatch.py" in svc and "Type=oneshot" in svc
        assert "OnCalendar=minutely" in timer
