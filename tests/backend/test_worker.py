"""P0.11 测试:轻量 Worker(job_runs 生命周期 / 重试 / 幂等键 / 文件锁 / 链依赖 / --from)。

全部用注册表注入的临时测试任务,不依赖真实爬虫或模型脚本。
"""

import sys
import threading

import pytest

from backend.db.connections import connect_ro
from backend.worker import runner


@pytest.fixture
def registry(data_dir):
    """快照/还原 REGISTRY,测试内随意注入临时任务;依赖 data_dir 重定向 platform.db。"""
    saved = dict(runner.REGISTRY)
    yield runner.REGISTRY
    runner.REGISTRY.clear()
    runner.REGISTRY.update(saved)


def _rows(job_name=None):
    conn = connect_ro("platform")
    try:
        if job_name:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM job_runs WHERE job_name=? ORDER BY created_at, id", (job_name,))]
        return [dict(r) for r in conn.execute("SELECT * FROM job_runs ORDER BY created_at, id")]
    finally:
        conn.close()


class TestRunJob:
    def test_success_writes_job_run(self, registry):
        runner.register_job("t_ok", fn=lambda: {"input_count": 3, "output_count": 2, "meta": {"x": 1}})
        res = runner.run_job("t_ok")
        assert res["status"] == "succeeded"
        (row,) = _rows("t_ok")
        assert row["status"] == "succeeded"
        assert row["attempt"] == 1
        assert row["input_count"] == 3 and row["output_count"] == 2
        assert row["started_at"] and row["finished_at"]
        assert '"x": 1' in row["meta_json"]

    def test_retry_increments_attempt_until_failed(self, registry):
        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise ValueError("模拟失败-" + "x" * 600)  # 顺带验证 error_summary 截断

        runner.register_job("t_fail", fn=boom, max_attempts=3, backoff_seconds=0)
        res = runner.run_job("t_fail")
        assert res["status"] == "failed"
        assert calls["n"] == 3
        (row,) = _rows("t_fail")
        assert row["status"] == "failed" and row["attempt"] == 3 and row["max_attempts"] == 3
        assert "ValueError" in row["error_summary"]
        assert len(row["error_summary"]) <= runner.ERROR_SUMMARY_MAX

    def test_idempotency_key_second_run_skipped(self, registry):
        calls = {"n": 0}

        def once():
            calls["n"] += 1
            return {"output_count": 1}

        runner.register_job("t_idem", fn=once)
        first = runner.run_job("t_idem", idempotency_key="2026-07-19")
        second = runner.run_job("t_idem", idempotency_key="2026-07-19")
        assert first["status"] == "succeeded"
        assert second["status"] == "skipped"
        assert calls["n"] == 1
        rows = _rows("t_idem")
        assert [r["status"] for r in rows].count("succeeded") == 1
        assert [r["status"] for r in rows].count("skipped") == 1

    def test_idempotency_key_force_reruns(self, registry):
        calls = {"n": 0}

        def once():
            calls["n"] += 1
            return {}

        runner.register_job("t_force", fn=once)
        runner.run_job("t_force", idempotency_key="k1")
        res = runner.run_job("t_force", idempotency_key="k1", force=True)
        assert res["status"] == "succeeded"
        assert calls["n"] == 2

    def test_idempotency_key_failed_then_rerun_reuses_row(self, registry):
        state = {"fail": True}

        def flaky():
            if state["fail"]:
                raise RuntimeError("第一次失败")
            return {"output_count": 5}

        runner.register_job("t_flaky", fn=flaky, max_attempts=1, backoff_seconds=0)
        first = runner.run_job("t_flaky", idempotency_key="k2")
        assert first["status"] == "failed"
        state["fail"] = False
        second = runner.run_job("t_flaky", idempotency_key="k2")
        assert second["status"] == "succeeded"
        rows = _rows("t_flaky")
        assert len(rows) == 1  # UNIQUE(job_name, key):同键复用同一行
        assert rows[0]["status"] == "succeeded" and rows[0]["output_count"] == 5

    def test_file_lock_blocks_concurrent_second_run(self, registry):
        entered = threading.Event()
        release = threading.Event()
        results = {}

        def slow():
            entered.set()
            assert release.wait(timeout=10)
            return {"output_count": 1}

        runner.register_job("t_lock", fn=slow, timeout_seconds=30)

        t = threading.Thread(target=lambda: results.update(first=runner.run_job("t_lock")))
        t.start()
        assert entered.wait(timeout=10)
        second = runner.run_job("t_lock")  # 锁被持有,进不去
        release.set()
        t.join(timeout=10)

        assert second["status"] == "locked"
        assert second["run_id"] is None
        assert results["first"]["status"] == "succeeded"
        rows = _rows("t_lock")
        assert len(rows) == 1  # 被锁挡掉的那次不产生 job_runs 行

    def test_stale_lock_is_cleared(self, registry):
        runner.register_job("t_stale", fn=lambda: {})
        lock = runner._locks_dir() / "t_stale.lock"
        lock.write_text('{"pid": 999999999, "job": "t_stale"}')  # 不存在的 pid → 陈锁
        res = runner.run_job("t_stale")
        assert res["status"] == "succeeded"
        assert not lock.exists()

    def test_subprocess_success_and_timeout(self, registry):
        runner.register_job("t_sub_ok", kind="subprocess",
                            argv=[sys.executable, "-c", "print('hello-worker')"])
        res = runner.run_job("t_sub_ok")
        assert res["status"] == "succeeded"
        (row,) = _rows("t_sub_ok")
        assert "hello-worker" in row["meta_json"]

        runner.register_job("t_sub_slow", kind="subprocess",
                            argv=[sys.executable, "-c", "import time; time.sleep(30)"],
                            timeout_seconds=1, max_attempts=1)
        res = runner.run_job("t_sub_slow")
        assert res["status"] == "failed"
        assert "超时" in res["error_summary"]

    def test_missing_env_records_failed_not_crash(self, registry, monkeypatch):
        monkeypatch.delenv("NOT_A_REAL_ENV_KEY", raising=False)
        runner.register_job("t_env", kind="subprocess",
                            argv=[sys.executable, "-c", "print('nope')"],
                            require_env=("NOT_A_REAL_ENV_KEY",))
        res = runner.run_job("t_env")
        assert res["status"] == "failed"
        assert "NOT_A_REAL_ENV_KEY" in res["error_summary"]

    def test_optional_job_missing_module_skipped(self, registry):
        runner.register_job("t_opt", kind="optional",
                            candidates=[("backend/does_not_exist_xyz.py", "backend.does_not_exist_xyz")])
        res = runner.run_job("t_opt")
        assert res["status"] == "skipped"
        (row,) = _rows("t_opt")
        assert row["status"] == "skipped"
        assert "尚未交付" in row["meta_json"]


class TestRunChain:
    def _register_abc(self, fail_step=None, log=None):
        for name in ("t_a", "t_b", "t_c"):
            def make(n=name):
                def fn():
                    if log is not None:
                        log.append(n)
                    if n == fail_step:
                        raise RuntimeError(f"{n} 挂了")
                    return {"output_count": 1}
                return fn
            runner.register_job(name, fn=make(), max_attempts=1, backoff_seconds=0)

    def test_upstream_failed_marks_downstream_skipped(self, registry):
        log = []
        self._register_abc(fail_step="t_b", log=log)
        results = runner.run_chain(["t_a", "t_b", "t_c"])
        assert [r["status"] for r in results] == ["succeeded", "failed", "skipped"]
        assert log == ["t_a", "t_b"]  # t_c 没有真正执行
        (row_c,) = _rows("t_c")
        assert row_c["status"] == "skipped"
        assert "t_b" in row_c["error_summary"]

    def test_chain_from_starts_mid_chain(self, registry):
        log = []
        self._register_abc(log=log)
        results = runner.run_chain(["t_a", "t_b", "t_c"], start_from="t_b")
        assert [r["job"] for r in results] == ["t_b", "t_c"]
        assert [r["status"] for r in results] == ["succeeded", "succeeded"]
        assert log == ["t_b", "t_c"]
        assert _rows("t_a") == []  # --from 之前的步骤完全未运行

    def test_chain_from_unknown_step_raises(self, registry):
        self._register_abc()
        with pytest.raises(KeyError):
            runner.run_chain(["t_a", "t_b"], start_from="t_zzz")
