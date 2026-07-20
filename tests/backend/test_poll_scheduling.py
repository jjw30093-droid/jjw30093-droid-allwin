"""生产可靠性收口:systemd 调度拓扑测试。

覆盖:
- allwin-poll.service 用 poll_wrapper 顺序执行两个采集任务,任一失败不阻止
  另一个被尝试,整体退出码如实反映是否有失败;
- `--chain --periodic` 跳过 nowgoal_snapshot/fotmob_snapshot(避免与
  allwin-poll.timer 的独立调度重复竞争同一把锁),裸 `--chain` 保持完整手动链
  不变;
- 系统单元文件的静态检查(硬化指令、ExecStart 拓扑),不依赖真实 systemd
  (本机无 systemd-analyze,相关真实验证保持 UNVERIFIED)。
"""

from pathlib import Path

import pytest

from backend.worker import poll_wrapper, runner

ROOT = Path(__file__).resolve().parents[2]
SYSTEMD_DIR = ROOT / "deploy" / "systemd"


@pytest.fixture
def registry():
    saved = dict(runner.REGISTRY)
    yield runner.REGISTRY
    runner.REGISTRY.clear()
    runner.REGISTRY.update(saved)


class TestPeriodicChainExcludesSnapshotJobs:
    def test_plain_chain_still_includes_snapshot_jobs(self):
        assert "nowgoal_snapshot" in runner.DEFAULT_CHAIN
        assert "fotmob_snapshot" in runner.DEFAULT_CHAIN

    def test_periodic_exclude_set_matches_the_two_snapshot_jobs(self):
        assert runner.PERIODIC_CHAIN_EXCLUDE == {"nowgoal_snapshot", "fotmob_snapshot"}

    def test_cli_periodic_flag_filters_out_snapshot_jobs(self, registry, data_dir, monkeypatch):
        seen_chains = []

        def fake_run_chain(names=None, start_from=None):
            seen_chains.append(list(names) if names else list(runner.DEFAULT_CHAIN))
            return []

        monkeypatch.setattr(runner, "run_chain", fake_run_chain)
        rc = runner.main(["--chain", "--periodic"])
        assert rc == 0
        assert len(seen_chains) == 1
        assert "nowgoal_snapshot" not in seen_chains[0]
        assert "fotmob_snapshot" not in seen_chains[0]
        # 其余步骤原样保留,顺序不变
        assert seen_chains[0] == [n for n in runner.DEFAULT_CHAIN
                                   if n not in runner.PERIODIC_CHAIN_EXCLUDE]

    def test_cli_without_periodic_uses_full_default_chain(self, registry, data_dir, monkeypatch):
        seen_chains = []

        def fake_run_chain(names=None, start_from=None):
            seen_chains.append(names)
            return []

        monkeypatch.setattr(runner, "run_chain", fake_run_chain)
        rc = runner.main(["--chain"])
        assert rc == 0
        assert seen_chains == [None]   # None → run_chain 内部用完整 DEFAULT_CHAIN


class TestPollWrapper:
    def test_both_jobs_attempted_even_if_first_fails(self, registry, data_dir):
        calls = []

        def nowgoal_boom():
            calls.append("nowgoal_snapshot")
            raise RuntimeError("模拟 NowGoal 失败")

        def fotmob_ok():
            calls.append("fotmob_snapshot")
            return {"input_count": 1, "output_count": 1}

        runner.register_job("nowgoal_snapshot", fn=nowgoal_boom, max_attempts=1)
        runner.register_job("fotmob_snapshot", fn=fotmob_ok, max_attempts=1)

        rc, results = poll_wrapper.run_all()
        assert calls == ["nowgoal_snapshot", "fotmob_snapshot"], (
            "NowGoal 失败不得阻止 FotMob 被尝试"
        )
        assert rc == 1, "任一任务失败时整体退出码必须非零"
        statuses = {r["job"]: r["status"] for r in results}
        assert statuses["nowgoal_snapshot"] == "failed"
        assert statuses["fotmob_snapshot"] == "succeeded"

    def test_both_jobs_attempted_when_second_fails(self, registry, data_dir):
        calls = []

        def nowgoal_ok():
            calls.append("nowgoal_snapshot")
            return {"input_count": 1, "output_count": 1}

        def fotmob_boom():
            calls.append("fotmob_snapshot")
            raise RuntimeError("模拟 FotMob 失败(如缺 THORDATA_PROXY)")

        runner.register_job("nowgoal_snapshot", fn=nowgoal_ok, max_attempts=1)
        runner.register_job("fotmob_snapshot", fn=fotmob_boom, max_attempts=1)

        rc, results = poll_wrapper.run_all()
        assert calls == ["nowgoal_snapshot", "fotmob_snapshot"]
        assert rc == 1
        statuses = {r["job"]: r["status"] for r in results}
        assert statuses["nowgoal_snapshot"] == "succeeded"
        assert statuses["fotmob_snapshot"] == "failed"

    def test_both_succeed_gives_zero_exit_code(self, registry, data_dir):
        runner.register_job("nowgoal_snapshot", fn=lambda: {"input_count": 0, "output_count": 0})
        runner.register_job("fotmob_snapshot", fn=lambda: {"input_count": 0, "output_count": 0})
        rc, results = poll_wrapper.run_all()
        assert rc == 0
        assert all(r["status"] == "succeeded" for r in results)

    def test_skipped_status_does_not_count_as_failure(self, registry, data_dir, monkeypatch):
        """run_job 返回 skipped(如幂等键已成功过)时,poll_wrapper 不应把它当
        失败——只有 failed/locked 才应该让整体退出码非零。"""

        def fake_run_job(job_name, idempotency_key=None, force=False):
            return {"run_id": "x", "status": "skipped", "attempt": 1, "error_summary": None}

        monkeypatch.setattr(poll_wrapper, "run_job", fake_run_job)
        rc, results = poll_wrapper.run_all()
        assert rc == 0
        assert all(r["status"] == "skipped" for r in results)


class TestSystemdUnitTopology:
    """静态检查(bash -n 已在部署脚本测试里覆盖;这里检查 systemd 单元文件本身
    的关键指令是否存在)。真实 systemd-analyze verify:本机无 systemd,UNVERIFIED。"""

    def test_poll_service_uses_wrapper_not_two_execstart_lines(self):
        content = (SYSTEMD_DIR / "allwin-poll.service").read_text()
        execstart_lines = [l for l in content.splitlines() if l.strip().startswith("ExecStart=")]
        assert len(execstart_lines) == 1, (
            f"应只有一条 ExecStart=(用 poll_wrapper 顺序执行两任务),实际: {execstart_lines}"
        )
        assert "poll_wrapper" in execstart_lines[0]

    def test_worker_service_uses_periodic_flag(self):
        content = (SYSTEMD_DIR / "allwin-worker.service").read_text()
        assert "--periodic" in content, "周期性 worker 链必须带 --periodic,避免重复调度 snapshot 任务"

    @pytest.mark.parametrize("unit", [
        "allwin-poll.service", "allwin-worker.service",
        "allwin-api.service", "allwin-web.service", "allwin-backup.service",
    ])
    def test_hardening_directives_present(self, unit):
        content = (SYSTEMD_DIR / unit).read_text()
        for directive in ("NoNewPrivileges=true", "PrivateTmp=true", "ProtectSystem=full", "UMask="):
            assert directive in content, f"{unit} 缺少加固指令: {directive}"

    @pytest.mark.parametrize("unit,expect_timeout_stop", [
        ("allwin-poll.service", True),
        ("allwin-worker.service", True),
        ("allwin-api.service", True),
        ("allwin-web.service", True),
        ("allwin-backup.service", True),
    ])
    def test_timeout_directives_present(self, unit, expect_timeout_stop):
        content = (SYSTEMD_DIR / unit).read_text()
        assert "TimeoutStartSec=" in content, f"{unit} 缺少 TimeoutStartSec"
        if expect_timeout_stop:
            assert "TimeoutStopSec=" in content, f"{unit} 缺少 TimeoutStopSec"

    def test_poll_timer_and_worker_timer_intervals_documented(self):
        poll_timer = (SYSTEMD_DIR / "allwin-poll.timer").read_text()
        worker_timer = (SYSTEMD_DIR / "allwin-worker.timer").read_text()
        assert "OnUnitActiveSec=5min" in poll_timer
        assert "OnUnitActiveSec=15min" in worker_timer
