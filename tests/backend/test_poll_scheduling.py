"""生产可靠性收口:systemd 调度拓扑测试(PIPELINE_REDESIGN_V2 P3)。

覆盖:
- `allwin-worker`/`allwin-poll` 两个旧定时器已被彻底移除,换成 7 个各自
  一个清晰职责的独立定时器(allwin-odds/lineup/fixtures/postmatch/derive/
  gates/maintenance);
- 单任务定时器(odds/lineup/fixtures/gates)直接 `runner --job <name>`;
  多任务定时器(postmatch/derive/maintenance)用 `group_runner --group <name>`
  顺序执行组内任务,任一失败不阻止另一个被尝试,整体退出码如实反映是否有失败;
- 最重要的不变量:`allwin-gates`(pipeline_gates)是完全独立的失败域——
  其它任何定时器这一轮的任务失败,都不得阻止/跳过 pipeline_gates 真正执行;
- 系统单元文件的静态检查(硬化指令、ExecStart 拓扑),不依赖真实 systemd
  (本机无 systemd-analyze,相关真实验证保持 UNVERIFIED)。
"""

from pathlib import Path

import pytest

from backend.worker import group_runner, runner

ROOT = Path(__file__).resolve().parents[2]
SYSTEMD_DIR = ROOT / "deploy" / "systemd"


@pytest.fixture
def registry():
    saved = dict(runner.REGISTRY)
    yield runner.REGISTRY
    runner.REGISTRY.clear()
    runner.REGISTRY.update(saved)


class TestGroupRunner:
    """allwin-postmatch/allwin-derive/allwin-maintenance 三个定时器共用的
    "组内任务彼此独立、顺序执行、汇总退出码"包装——继承自旧 poll_wrapper.py
    对 nowgoal_snapshot+fotmob_snapshot 两个任务已经验证过的同一份契约,
    只是参数化到任意任务组。"""

    @pytest.mark.parametrize("group_name", sorted(group_runner.JOB_GROUPS))
    @pytest.mark.parametrize("fail_index", [0, -1])
    def test_all_jobs_attempted_even_if_one_fails(self, group_name, fail_index, registry, data_dir):
        jobs = group_runner.JOB_GROUPS[group_name]
        boom_job = jobs[fail_index]
        calls = []

        for job in jobs:
            if job == boom_job:
                def make_boom(job=job):
                    def boom():
                        calls.append(job)
                        raise RuntimeError(f"模拟 {job} 失败")
                    return boom
                runner.register_job(job, fn=make_boom(), max_attempts=1)
            else:
                def make_ok(job=job):
                    def ok():
                        calls.append(job)
                        return {"input_count": 0, "output_count": 0}
                    return ok
                runner.register_job(job, fn=make_ok(), max_attempts=1)

        rc, results = group_runner.run_group(group_name)

        assert calls == list(jobs), (
            f"{group_name} 组内 {boom_job} 失败不得阻止其余任务被尝试(实际尝试顺序: {calls})"
        )
        assert rc == 1, "任一任务失败时整体退出码必须非零"
        statuses = {r["job"]: r["status"] for r in results}
        assert statuses[boom_job] == "failed"
        for job in jobs:
            if job != boom_job:
                assert statuses[job] == "succeeded"

    @pytest.mark.parametrize("group_name", sorted(group_runner.JOB_GROUPS))
    def test_all_succeed_gives_zero_exit_code(self, group_name, registry, data_dir):
        for job in group_runner.JOB_GROUPS[group_name]:
            runner.register_job(job, fn=lambda: {"input_count": 0, "output_count": 0}, max_attempts=1)
        rc, results = group_runner.run_group(group_name)
        assert rc == 0
        assert all(r["status"] == "succeeded" for r in results)

    @pytest.mark.parametrize("group_name", sorted(group_runner.JOB_GROUPS))
    def test_skipped_status_does_not_count_as_failure(self, group_name, registry, data_dir, monkeypatch):
        """run_job 返回 skipped(如幂等键已成功过)时,group_runner 不应把它当
        失败——只有 failed/locked 才应该让整体退出码非零。"""

        def fake_run_job(job_name, idempotency_key=None, force=False):
            return {"run_id": "x", "status": "skipped", "attempt": 1, "error_summary": None}

        monkeypatch.setattr(group_runner, "run_job", fake_run_job)
        rc, results = group_runner.run_group(group_name)
        assert rc == 0
        assert all(r["status"] == "skipped" for r in results)

    def test_cli_group_flag_dispatches_to_run_group(self, registry, data_dir, monkeypatch):
        seen = []

        def fake_run_group(name):
            seen.append(name)
            return 0, []

        monkeypatch.setattr(group_runner, "run_group", fake_run_group)
        rc = group_runner.main(["--group", "derive"])
        assert rc == 0
        assert seen == ["derive"]

    def test_cli_rejects_unknown_group(self):
        with pytest.raises(SystemExit):
            group_runner.main(["--group", "not-a-real-group"])


class TestGatesIndependentOfOtherTimers:
    """七定时器拓扑里最重要的正确性属性:allwin-gates(pipeline_gates)是
    只读告警,必须完全独立于其它任何定时器这一轮的成败——它不再是
    DEFAULT_CHAIN 链尾、不再经过 run_chain() 的 cascade-skip,而是自己独立
    的定时器,直接 `runner --job pipeline_gates`,和其它任何 group_runner
    调用之间没有共享的锁/进程内状态/CLI 标志。"""

    def test_pipeline_gates_runs_after_upstream_group_job_fails(self, registry, data_dir):
        def model_predict_boom():
            raise RuntimeError("模拟 allwin-postmatch 本轮 model_predict 崩溃")

        for job in group_runner.JOB_GROUPS["postmatch"]:
            if job == "model_predict":
                runner.register_job(job, fn=model_predict_boom, max_attempts=1)
            else:
                runner.register_job(job, fn=lambda: {"input_count": 0, "output_count": 0}, max_attempts=1)

        rc, results = group_runner.run_group("postmatch")
        assert rc == 1
        statuses = {r["job"]: r["status"] for r in results}
        assert statuses["model_predict"] == "failed"

        gates_calls = []

        def gates_ok():
            gates_calls.append("pipeline_gates")
            return {"input_count": 1, "output_count": 0, "meta": {"level": "OK"}}

        runner.register_job("pipeline_gates", fn=gates_ok, max_attempts=1)

        res = runner.run_job("pipeline_gates")
        assert gates_calls == ["pipeline_gates"], (
            "上游 allwin-postmatch 这一轮出现失败之后,allwin-gates 自己独立的下一次 "
            "tick 必须仍然真正执行 pipeline_gates,而不是被任何跨定时器状态跳过"
        )
        assert res["status"] == "succeeded"

    def test_pipeline_gates_runs_after_upstream_job_is_locked(self, registry, data_dir, monkeypatch):
        """`locked`(文件锁被占用)和 `failed` 在 run_chain() 里被同等处理会
        级联跳过——但 pipeline_gates 现在完全不经过 run_chain(),独立
        runner.run_job() 调用不受任何其它任务当前是否持锁影响。"""

        def slow_job():
            return {"input_count": 0, "output_count": 0}

        runner.register_job("core_silver_build", fn=slow_job, max_attempts=1)

        lock_path = runner._acquire_lock("core_silver_build")
        assert lock_path is not None
        try:
            res = runner.run_job("core_silver_build")
            assert res["status"] == "locked"

            gates_calls = []

            def gates_ok():
                gates_calls.append("pipeline_gates")
                return {"input_count": 1, "output_count": 0, "meta": {"level": "OK"}}

            runner.register_job("pipeline_gates", fn=gates_ok, max_attempts=1)
            gates_res = runner.run_job("pipeline_gates")
            assert gates_calls == ["pipeline_gates"]
            assert gates_res["status"] == "succeeded"
        finally:
            runner._release_lock(lock_path)


class TestSystemdUnitTopology:
    """静态检查(bash -n 已在部署脚本测试里覆盖;这里检查 systemd 单元文件本身
    的关键指令是否存在)。真实 systemd-analyze verify:本机无 systemd,UNVERIFIED。"""

    @pytest.mark.parametrize("filename", [
        "allwin-worker.service", "allwin-worker.timer",
        "allwin-poll.service", "allwin-poll.timer",
    ])
    def test_old_worker_and_poll_units_removed(self, filename):
        assert not (SYSTEMD_DIR / filename).exists(), (
            f"{filename} 应已被 7 个新独立定时器取代删除,不是保留/清空"
        )

    @pytest.mark.parametrize("unit,job_name", [
        ("allwin-odds.service", "nowgoal_snapshot"),
        ("allwin-lineup.service", "fotmob_snapshot"),
        ("allwin-fixtures.service", "schedule_sync_multi"),
        ("allwin-gates.service", "pipeline_gates"),
    ])
    def test_single_job_timers_invoke_runner_job_directly(self, unit, job_name):
        content = (SYSTEMD_DIR / unit).read_text()
        execstart = [l for l in content.splitlines() if l.strip().startswith("ExecStart=")]
        assert len(execstart) == 1, f"{unit} 应只有一条 ExecStart=,实际: {execstart}"
        assert "backend.worker.runner" in execstart[0]
        assert f"--job {job_name}" in execstart[0]
        assert "group_runner" not in execstart[0]

    @pytest.mark.parametrize("unit,group_name", [
        ("allwin-postmatch.service", "postmatch"),
        ("allwin-derive.service", "derive"),
        ("allwin-maintenance.service", "maintenance"),
    ])
    def test_multi_job_timers_invoke_group_runner(self, unit, group_name):
        content = (SYSTEMD_DIR / unit).read_text()
        execstart = [l for l in content.splitlines() if l.strip().startswith("ExecStart=")]
        assert len(execstart) == 1, (
            f"应只有一条 ExecStart=(用 group_runner 顺序执行组内任务),实际: {execstart}"
        )
        assert "backend.worker.group_runner" in execstart[0]
        assert f"--group {group_name}" in execstart[0]

    def test_gates_service_has_no_cross_unit_dependency(self):
        """allwin-gates 必须是独立失败域:不能在自己的 unit 文件里依赖/等待
        任何其它 allwin-* 管道单元——它是"出问题时最需要它跑"的只读告警。"""
        content = (SYSTEMD_DIR / "allwin-gates.service").read_text()
        for other in ("allwin-postmatch", "allwin-derive", "allwin-fixtures",
                      "allwin-odds", "allwin-lineup", "allwin-maintenance",
                      "allwin-worker", "allwin-poll"):
            assert other not in content, (
                f"allwin-gates.service 不得提到 {other}:质量门必须独立于其它单元的成败"
            )

    @pytest.mark.parametrize("unit", [
        "allwin-odds.service", "allwin-lineup.service", "allwin-fixtures.service",
        "allwin-postmatch.service", "allwin-derive.service", "allwin-gates.service",
        "allwin-maintenance.service",
        "allwin-api.service", "allwin-web.service", "allwin-backup.service",
        "allwin-opscheck.service", "allwin-digest.service",
    ])
    def test_hardening_directives_present(self, unit):
        content = (SYSTEMD_DIR / unit).read_text()
        for directive in ("NoNewPrivileges=true", "PrivateTmp=true", "ProtectSystem=full", "UMask="):
            assert directive in content, f"{unit} 缺少加固指令: {directive}"

    @pytest.mark.parametrize("unit", [
        "allwin-odds.service", "allwin-lineup.service", "allwin-fixtures.service",
        "allwin-postmatch.service", "allwin-derive.service", "allwin-gates.service",
        "allwin-maintenance.service",
        "allwin-api.service", "allwin-web.service", "allwin-backup.service",
        "allwin-opscheck.service", "allwin-digest.service",
    ])
    def test_timeout_directives_present(self, unit):
        content = (SYSTEMD_DIR / unit).read_text()
        assert "TimeoutStartSec=" in content, f"{unit} 缺少 TimeoutStartSec"
        assert "TimeoutStopSec=" in content, f"{unit} 缺少 TimeoutStopSec"

    @pytest.mark.parametrize("unit", [
        "allwin-odds.service", "allwin-lineup.service", "allwin-fixtures.service",
        "allwin-postmatch.service", "allwin-derive.service", "allwin-gates.service",
        "allwin-maintenance.service",
    ])
    def test_new_units_use_shared_env_file_and_user(self, unit):
        content = (SYSTEMD_DIR / unit).read_text()
        assert "EnvironmentFile=/opt/allwin/shared/.env" in content
        assert "User=allwin" in content
        assert "Group=allwin" in content
        assert "[Install]" in content and "WantedBy=multi-user.target" in content

    @pytest.mark.parametrize("unit,expected", [
        ("allwin-odds.timer", "OnUnitActiveSec=5min"),
        ("allwin-lineup.timer", "OnUnitActiveSec=5min"),
        ("allwin-fixtures.timer", "OnUnitActiveSec=30min"),
        ("allwin-postmatch.timer", "OnUnitActiveSec=30min"),
        ("allwin-derive.timer", "OnUnitActiveSec=30min"),
        ("allwin-gates.timer", "OnUnitActiveSec=30min"),
    ])
    def test_timer_intervals_documented(self, unit, expected):
        content = (SYSTEMD_DIR / unit).read_text()
        assert expected in content

    def test_maintenance_timer_is_daily_and_persistent(self):
        content = (SYSTEMD_DIR / "allwin-maintenance.timer").read_text()
        assert "OnCalendar=" in content
        assert "Persistent=true" in content

    def test_opscheck_units(self):
        """opscheck:30 分钟节奏、--json --notify、WARN/CRITICAL 退出码不算 unit 失败。"""
        service = (SYSTEMD_DIR / "allwin-opscheck.service").read_text()
        timer = (SYSTEMD_DIR / "allwin-opscheck.timer").read_text()
        execstart = [l for l in service.splitlines() if l.strip().startswith("ExecStart=")]
        assert len(execstart) == 1
        assert "backend.cli.ops_check" in execstart[0]
        assert "--json" in execstart[0] and "--notify" in execstart[0]
        assert "SuccessExitStatus=1 2" in service, (
            "ops_check 用退出码 1/2 表达 WARN/CRITICAL(告警已由 --notify 推送),"
            "不声明 SuccessExitStatus 会让每次 WARN 都被 systemd 记成 unit failure"
        )
        assert "OnUnitActiveSec=30min" in timer

    def test_digest_units(self):
        """digest:每天 23:30 Asia/Shanghai、Persistent 补跑、--key 北京日期幂等。"""
        service = (SYSTEMD_DIR / "allwin-digest.service").read_text()
        timer = (SYSTEMD_DIR / "allwin-digest.timer").read_text()
        execstart = [l for l in service.splitlines() if l.strip().startswith("ExecStart=")]
        assert len(execstart) == 1
        assert "daily_digest" in execstart[0]
        assert "--key" in execstart[0], "必须以 --key <日期> 幂等,天然每天恰好一次"
        assert "TZ=Asia/Shanghai" in execstart[0], "幂等键必须按北京日期取"
        assert "%%Y-%%m-%%d" in execstart[0], "unit 文件里 % 必须写成 %%(systemd 转义)"
        assert "OnCalendar=*-*-* 23:30:00 Asia/Shanghai" in timer
        assert "Persistent=true" in timer
