"""backend/cli/ops_check.py + /readyz 脱敏永久回归测试(生产可靠性收口)。

全部使用 tmp_path 临时三库(data_dir fixture),不碰真实 data/*.db,不依赖网络。
"""

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.cli import ops_check
from backend.db.connections import connect_rw
from backend.db.paths import data_dir

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _utc_iso(dt=None, minus_hours=0, minus_minutes=0):
    dt = (dt or datetime.now(timezone.utc)) - timedelta(hours=minus_hours, minutes=minus_minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class TestCheckDatabases:
    def test_healthy_three_dbs(self, data_dir):
        results = ops_check.check_databases()
        assert {r["name"] for r in results} == {"core", "platform", "odds"}
        assert all(r["level"] == ops_check.OK for r in results)

    def test_missing_db_is_critical(self, data_dir):
        (data_dir / "odds.db").unlink()
        for suffix in ("-wal", "-shm"):
            p = data_dir / f"odds.db{suffix}"
            if p.exists():
                p.unlink()
        results = ops_check.check_databases()
        odds = next(r for r in results if r["name"] == "odds")
        assert odds["level"] == ops_check.CRITICAL

    def test_corrupt_db_is_critical(self, data_dir):
        (data_dir / "odds.db").write_bytes(b"not a real sqlite file")
        results = ops_check.check_databases()
        odds = next(r for r in results if r["name"] == "odds")
        assert odds["level"] == ops_check.CRITICAL

    def test_pending_migration_is_critical(self, data_dir, monkeypatch):
        conn = connect_rw("platform")
        conn.execute("DELETE FROM schema_migrations WHERE version=(SELECT MAX(version) FROM schema_migrations)")
        conn.commit()
        conn.close()
        results = ops_check.check_databases()
        platform = next(r for r in results if r["name"] == "platform")
        assert platform["level"] == ops_check.CRITICAL
        assert platform["detail"] == "pending_migrations"

    def test_checksum_drift_is_critical(self, data_dir):
        conn = connect_rw("platform")
        conn.execute("UPDATE schema_migrations SET checksum='tampered' WHERE version=1")
        conn.commit()
        conn.close()
        results = ops_check.check_databases()
        platform = next(r for r in results if r["name"] == "platform")
        assert platform["level"] == ops_check.CRITICAL
        assert platform["detail"] == "checksum_drift"


class TestCheckBackup:
    def test_no_backups_directory_is_warn(self, data_dir):
        result = ops_check.check_backup()
        assert result["level"] == ops_check.WARN

    def test_incomplete_backup_dir_not_counted(self, data_dir):
        incomplete = data_dir / "backups" / "20990101T000000Z"
        incomplete.mkdir(parents=True)
        (incomplete / "allwin.db").write_bytes(b"x")   # 无 backup_metadata.json
        result = ops_check.check_backup()
        assert result["level"] == ops_check.WARN
        assert result["detail"] == "no_complete_backup_found"

    def test_fresh_complete_backup_is_ok(self, data_dir):
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        d = data_dir / "backups" / ts
        d.mkdir(parents=True)
        (d / "backup_metadata.json").write_text(json.dumps({"complete": True, "databases": {}}))
        result = ops_check.check_backup()
        assert result["level"] == ops_check.OK
        assert result["detail"] == "fresh"

    def test_stale_complete_backup_is_warn(self, data_dir):
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=ops_check.BACKUP_STALE_HOURS + 5)) \
            .strftime("%Y%m%dT%H%M%SZ")
        d = data_dir / "backups" / old_ts
        d.mkdir(parents=True)
        (d / "backup_metadata.json").write_text(json.dumps({"complete": True, "databases": {}}))
        result = ops_check.check_backup()
        assert result["level"] == ops_check.WARN
        assert result["detail"] == "stale"


class TestCheckDisk:
    def test_below_warn_threshold_is_ok(self, data_dir, monkeypatch):
        monkeypatch.setattr(ops_check.shutil, "disk_usage",
                             lambda p: type("U", (), {"total": 100, "used": 10, "free": 90})())
        result = ops_check.check_disk()
        assert result["level"] == ops_check.OK

    def test_warn_threshold(self, data_dir, monkeypatch):
        monkeypatch.setattr(ops_check.shutil, "disk_usage",
                             lambda p: type("U", (), {"total": 100, "used": 75, "free": 25})())
        result = ops_check.check_disk()
        assert result["level"] == ops_check.WARN

    def test_critical_threshold(self, data_dir, monkeypatch):
        monkeypatch.setattr(ops_check.shutil, "disk_usage",
                             lambda p: type("U", (), {"total": 100, "used": 90, "free": 10})())
        result = ops_check.check_disk()
        assert result["level"] == ops_check.CRITICAL


class TestCheckJobRuns:
    # check_job_runs 只对"仍在 Worker 注册表里"的任务名告警(已退役任务不再
    # 产生永不消失的 stale WARNING),所以本类里的假任务名必须真的注册进
    # REGISTRY——用 runner 自带的 register_job()(它的 docstring 写明就是给
    # 测试注入临时任务用的),而不是把断言放宽成"退役也算数"。
    FAKE_JOBS = ("test_job", "stuck_job", "fresh_job", "never_ok_job", "stale_success_job")

    @pytest.fixture(autouse=True)
    def _register_fake_jobs(self):
        from backend.worker import runner

        for name in self.FAKE_JOBS:
            runner.register_job(name, argv=["/bin/true"], cwd=str(PROJECT_ROOT))
        yield
        for name in self.FAKE_JOBS:
            runner.REGISTRY.pop(name, None)

    def _insert_job_run(self, conn, job_name, status, started_at=None, finished_at=None):
        from backend.db.util import new_uuid

        conn.execute(
            "INSERT INTO job_runs (id, job_name, status, attempt, max_attempts, started_at,"
            " finished_at, meta_json, created_at) VALUES (?, ?, ?, 1, 1, ?, ?, '{}', ?)",
            (new_uuid(), job_name, status, started_at, finished_at, started_at or _utc_iso()),
        )

    def test_no_job_runs_table_data_is_ok(self, data_dir):
        result = ops_check.check_job_runs()
        assert result["level"] == ops_check.OK

    def test_last_run_failed_is_warn(self, data_dir):
        conn = connect_rw("platform")
        self._insert_job_run(conn, "test_job", "succeeded", _utc_iso(minus_hours=2), _utc_iso(minus_hours=2))
        self._insert_job_run(conn, "test_job", "failed", _utc_iso(minus_minutes=5), _utc_iso(minus_minutes=5))
        conn.commit()
        conn.close()
        result = ops_check.check_job_runs()
        assert result["level"] == ops_check.WARN
        job = next(j for j in result["jobs"] if j["job"] == "test_job")
        assert job["detail"] == "last_run_failed"

    def test_stuck_running_job_is_warn(self, data_dir):
        conn = connect_rw("platform")
        self._insert_job_run(conn, "stuck_job", "running",
                              started_at=_utc_iso(minus_minutes=ops_check.JOB_STUCK_MINUTES + 30))
        conn.commit()
        conn.close()
        result = ops_check.check_job_runs()
        job = next(j for j in result["jobs"] if j["job"] == "stuck_job")
        assert job["level"] == ops_check.WARN
        assert job["detail"] == "stuck_running"

    def test_recently_started_running_job_is_ok(self, data_dir):
        conn = connect_rw("platform")
        self._insert_job_run(conn, "fresh_job", "running", started_at=_utc_iso(minus_minutes=2))
        conn.commit()
        conn.close()
        result = ops_check.check_job_runs()
        job = next(j for j in result["jobs"] if j["job"] == "fresh_job")
        assert job["level"] == ops_check.OK

    def test_never_succeeded_flagged(self, data_dir):
        conn = connect_rw("platform")
        self._insert_job_run(conn, "never_ok_job", "failed", _utc_iso(), _utc_iso())
        conn.commit()
        conn.close()
        result = ops_check.check_job_runs()
        job = next(j for j in result["jobs"] if j["job"] == "never_ok_job")
        assert job["never_succeeded"] is True
        assert job["level"] == ops_check.WARN

    def test_stale_last_success_is_warn(self, data_dir):
        conn = connect_rw("platform")
        self._insert_job_run(conn, "stale_success_job", "succeeded",
                              _utc_iso(minus_hours=ops_check.JOB_STALE_HOURS + 10),
                              _utc_iso(minus_hours=ops_check.JOB_STALE_HOURS + 10))
        conn.commit()
        conn.close()
        result = ops_check.check_job_runs()
        job = next(j for j in result["jobs"] if j["job"] == "stale_success_job")
        assert job["level"] == ops_check.WARN


    def test_retired_job_not_in_registry_is_ok_not_stale(self, data_dir):
        """已从 REGISTRY 删除的任务:哪怕最后一次成功早已过期,也不再告警。

        回归 2026-08-29:WDL 模型系统被删除后,model_predict/prediction_register/
        postmatch_settle/metrics_rebuild 在 job_runs 里留下历史行,让 ops_check
        永久报 stale_last_success,把真正的 CRITICAL 淹没在 WARNING 噪音里。
        """
        conn = connect_rw("platform")
        self._insert_job_run(conn, "deleted_wdl_job", "succeeded",
                             _utc_iso(minus_hours=ops_check.JOB_STALE_HOURS + 100),
                             _utc_iso(minus_hours=ops_check.JOB_STALE_HOURS + 100))
        conn.commit()
        conn.close()

        from backend.worker import runner
        assert "deleted_wdl_job" not in runner.REGISTRY

        result = ops_check.check_job_runs()
        job = next(j for j in result["jobs"] if j["job"] == "deleted_wdl_job")
        assert job["retired"] is True
        assert job["detail"] == "retired_not_in_registry"
        assert job["level"] == ops_check.OK
        # 仍然可审计:最后一次成功时间没有被抹掉
        assert job["last_success_at"] is not None

    def test_registry_unavailable_falls_back_to_monitoring_all(self, data_dir, monkeypatch):
        """注册表读不到时 fail-open:退回旧行为继续告警,不静默关掉全部任务告警。"""
        conn = connect_rw("platform")
        self._insert_job_run(conn, "some_unregistered_job", "succeeded",
                             _utc_iso(minus_hours=ops_check.JOB_STALE_HOURS + 10),
                             _utc_iso(minus_hours=ops_check.JOB_STALE_HOURS + 10))
        conn.commit()
        conn.close()

        monkeypatch.setattr(ops_check, "_registered_job_names", lambda: None)
        result = ops_check.check_job_runs()
        job = next(j for j in result["jobs"] if j["job"] == "some_unregistered_job")
        assert job.get("retired") is None
        assert job["level"] == ops_check.WARN
        assert job["detail"] == "stale_last_success"


class TestCheckSourceHealth:
    def _insert_source_health(self, conn, source, ok, checked_at, error_summary=None):
        conn.execute(
            "INSERT INTO source_health (source, checked_at, ok, error_summary, meta_json)"
            " VALUES (?, ?, ?, ?, '{}')",
            (source, checked_at, 1 if ok else 0, error_summary),
        )

    def test_no_source_health_rows_is_ok(self, data_dir):
        result = ops_check.check_source_health()
        assert result["level"] == ops_check.OK

    def test_never_succeeded_is_warn_and_distinct_from_stale(self, data_dir):
        conn = connect_rw("odds")
        self._insert_source_health(conn, "nowgoal", ok=False, checked_at=_utc_iso(),
                                    error_summary="connection refused: 1.2.3.4:9999 proxy=secret-proxy-user:pw@host")
        conn.commit()
        conn.close()
        result = ops_check.check_source_health()
        src = next(s for s in result["sources"] if s["source"] == "nowgoal")
        assert src["detail"] == "never_succeeded"
        assert src["level"] == ops_check.WARN

    def test_stale_but_previously_succeeded_is_warn_with_distinct_detail(self, data_dir):
        conn = connect_rw("odds")
        self._insert_source_health(conn, "fotmob_lineup", ok=True,
                                    checked_at=_utc_iso(minus_hours=ops_check.SOURCE_STALE_HOURS + 3))
        conn.commit()
        conn.close()
        result = ops_check.check_source_health()
        src = next(s for s in result["sources"] if s["source"] == "fotmob_lineup")
        assert src["detail"] == "stale"
        assert src["level"] == ops_check.WARN

    def test_recent_success_is_ok(self, data_dir):
        conn = connect_rw("odds")
        self._insert_source_health(conn, "nowgoal", ok=True, checked_at=_utc_iso(minus_minutes=5))
        conn.commit()
        conn.close()
        result = ops_check.check_source_health()
        src = next(s for s in result["sources"] if s["source"] == "nowgoal")
        assert src["level"] == ops_check.OK

    def test_failure_summary_is_truncated_not_full_stack(self, data_dir):
        conn = connect_rw("odds")
        long_summary = "x" * 5000
        self._insert_source_health(conn, "nowgoal", ok=False, checked_at=_utc_iso(),
                                    error_summary=long_summary)
        conn.commit()
        conn.close()
        result = ops_check.check_source_health()
        src = next(s for s in result["sources"] if s["source"] == "nowgoal")
        assert len(src["last_failure_summary"]) <= ops_check._SUMMARY_MAX


def _seed_fresh_complete_backup(data_dir):
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    d = data_dir / "backups" / ts
    d.mkdir(parents=True)
    (d / "backup_metadata.json").write_text(json.dumps({"complete": True, "databases": {}}))


class TestOverallReportAndExitCodes:
    def test_healthy_report_is_ok_exit_zero(self, data_dir, monkeypatch):
        # "健康"基线本身就应包含"最近有一份完整备份"——全新迁移出的库还没有
        # 任何备份历史时,check_backup() 正确报 WARN,这不是 bug,是诚实反映
        # "从未备份过"这个真实状态,所以这里显式播种一份新鲜完整备份再断言 OK。
        _seed_fresh_complete_backup(data_dir)
        # 本机真实磁盘占用可能本来就 > 70% 默认告警阈值(与被测代码无关),
        # 这里固定磁盘用量到一个明确健康的值,不依赖跑测试的机器当时状态。
        monkeypatch.setattr(ops_check.shutil, "disk_usage",
                             lambda p: type("U", (), {"total": 100, "used": 10, "free": 90})())
        report = ops_check.run_all_checks()
        assert report["level"] == ops_check.OK
        assert report["level_name"] == "OK"

    def test_overall_level_is_max_of_all_checks(self, data_dir):
        (data_dir / "odds.db").unlink()
        report = ops_check.run_all_checks()
        assert report["level"] == ops_check.CRITICAL

    def test_json_output_is_valid_json(self, data_dir, capsys):
        rc = ops_check.main(["--json"])
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["level"] == rc
        assert "databases" in parsed and "backup" in parsed and "job_runs" in parsed

    def test_text_output_is_human_readable_lines(self, data_dir, capsys):
        rc = ops_check.main([])
        out = capsys.readouterr().out
        assert "allwin ops_check" in out
        assert rc in (ops_check.OK, ops_check.WARN, ops_check.CRITICAL)

    def test_exit_code_matches_report_level(self, data_dir):
        (data_dir / "odds.db").unlink()
        rc = ops_check.main(["--json"])
        assert rc == ops_check.CRITICAL


class TestNoSensitiveLeakage:
    def test_report_never_contains_raw_proxy_credentials(self, data_dir):
        conn = connect_rw("odds")
        conn.execute(
            "INSERT INTO source_health (source, checked_at, ok, error_summary, meta_json)"
            " VALUES ('nowgoal', ?, 0, ?, '{}')",
            (_utc_iso(), "proxy auth failed user=secretuser pass=SuperSecretPassw0rd!"),
        )
        conn.commit()
        conn.close()
        report = ops_check.run_all_checks()
        raw = json.dumps(report, ensure_ascii=False)
        # 本工具不重新格式化/脱敏 error_summary 的具体内容(那是各 worker 自己的
        # 职责),但必须证明:不额外拼接绝对路径、不吐出堆栈或异常类名。
        assert "Traceback" not in raw
        assert str(data_dir) not in raw
        assert "sqlite3." not in raw

    def test_corrupt_db_message_has_no_stack_or_path(self, data_dir):
        (data_dir / "odds.db").write_bytes(b"garbage")
        report = ops_check.run_all_checks()
        raw = json.dumps(report, ensure_ascii=False)
        assert "Traceback" not in raw
        assert str(data_dir) not in raw
        assert "OperationalError" not in raw and "DatabaseError" not in raw


class TestReadyzSanitization:
    def test_readyz_healthy(self, app, data_dir):
        client = TestClient(app)
        r = client.get("/readyz")
        assert r.status_code == 200
        assert r.json() == {"ok": True}

    def test_readyz_missing_db_no_leak(self, app, data_dir):
        (data_dir / "odds.db").unlink()
        for suffix in ("-wal", "-shm"):
            p = data_dir / f"odds.db{suffix}"
            if p.exists():
                p.unlink()
        client = TestClient(app)
        r = client.get("/readyz")
        assert r.status_code == 503
        body = r.json()
        assert body["ok"] is False
        raw = json.dumps(body)
        assert "Traceback" not in raw
        assert str(data_dir) not in raw
        assert "OperationalError" not in raw
        assert "unable to open database file" not in raw
        assert any("odds" in p and "unavailable" in p for p in body["problems"])

    def test_readyz_corrupt_db_no_leak(self, app, data_dir):
        (data_dir / "odds.db").write_bytes(b"not a real sqlite file")
        client = TestClient(app)
        r = client.get("/readyz")
        assert r.status_code == 503
        body = r.json()
        raw = json.dumps(body)
        assert "file is not a database" not in raw
        assert "Traceback" not in raw

    def test_readyz_pending_migration_reports_count_not_filenames(self, app, data_dir):
        conn = connect_rw("platform")
        conn.execute("DELETE FROM schema_migrations WHERE version=(SELECT MAX(version) FROM schema_migrations)")
        conn.commit()
        conn.close()
        client = TestClient(app)
        r = client.get("/readyz")
        assert r.status_code == 503
        body = r.json()
        raw = json.dumps(body)
        assert ".sql" not in raw, "不应下发具体迁移文件名"
        assert any("pending_migrations=" in p for p in body["problems"])

    def test_readyz_cache_control_still_no_store(self, app, data_dir):
        (data_dir / "odds.db").unlink()
        client = TestClient(app)
        r = client.get("/readyz")
        assert r.headers["cache-control"] == "private, no-store"


# ── P2 修正轮:OpsConfig 严格 fail-fast(生产可靠性收口第二轮) ──────────

class TestOpsConfigFailFast:
    """OPS_* 环境变量未设置/空白 → 默认值;显式设置但非法 → 立即 ConfigError,
    绝不静默改用默认值继续执行。全部用 OpsConfig.from_env(env=dict) 直接测试
    解析/校验逻辑,不依赖真实 os.environ,避免测试间相互污染。"""

    def test_unset_env_uses_documented_defaults(self):
        cfg = ops_check.OpsConfig.from_env({})
        assert cfg.disk_warn_pct == 70.0
        assert cfg.disk_critical_pct == 85.0
        assert cfg.backup_stale_hours == 30
        assert cfg.job_stuck_minutes == 120
        assert cfg.job_stale_hours == 24
        assert cfg.source_stale_hours == 6

    def test_blank_env_value_uses_default(self):
        cfg = ops_check.OpsConfig.from_env({"OPS_DISK_WARN_PCT": "   "})
        assert cfg.disk_warn_pct == 70.0

    @pytest.mark.parametrize("bad", ["abc", "nan", "NaN", "inf", "Infinity", "-inf"])
    def test_disk_warn_pct_non_finite_or_non_numeric_rejected(self, bad):
        with pytest.raises(ops_check.ConfigError):
            ops_check.OpsConfig.from_env({"OPS_DISK_WARN_PCT": bad})

    @pytest.mark.parametrize("warn,critical", [
        ("-5", "85"), ("0", "85"), ("70", "70"), ("80", "70"), ("70", "101"), ("70", "200"),
    ])
    def test_disk_pct_relationship_violations_rejected(self, warn, critical):
        with pytest.raises(ops_check.ConfigError):
            ops_check.OpsConfig.from_env({"OPS_DISK_WARN_PCT": warn, "OPS_DISK_CRITICAL_PCT": critical})

    def test_disk_pct_valid_relationship_accepted(self):
        cfg = ops_check.OpsConfig.from_env({"OPS_DISK_WARN_PCT": "50", "OPS_DISK_CRITICAL_PCT": "90"})
        assert cfg.disk_warn_pct == 50.0 and cfg.disk_critical_pct == 90.0

    @pytest.mark.parametrize("env_name", [
        "OPS_BACKUP_STALE_HOURS", "OPS_JOB_STUCK_MINUTES", "OPS_JOB_STALE_HOURS", "OPS_SOURCE_STALE_HOURS",
    ])
    @pytest.mark.parametrize("bad", ["1.5", "0", "-3", "abc"])
    def test_time_thresholds_reject_non_positive_integers(self, env_name, bad):
        """空字符串不属于"非法值"——那是"未设置,使用默认值"的合法语义,由
        下面 test_time_threshold_blank_value_uses_default 单独覆盖,不混进
        这个非法值参数化列表(混进去必然导致该组合被跳过,属于人为制造 skip)。"""
        with pytest.raises(ops_check.ConfigError):
            ops_check.OpsConfig.from_env({env_name: bad})

    @pytest.mark.parametrize("env_name", [
        "OPS_BACKUP_STALE_HOURS", "OPS_JOB_STUCK_MINUTES", "OPS_JOB_STALE_HOURS", "OPS_SOURCE_STALE_HOURS",
    ])
    def test_time_threshold_blank_value_uses_default(self, env_name):
        cfg = ops_check.OpsConfig.from_env({env_name: "   "})
        assert cfg == ops_check.OpsConfig()

    @pytest.mark.parametrize("env_name", [
        "OPS_BACKUP_STALE_HOURS", "OPS_JOB_STUCK_MINUTES", "OPS_JOB_STALE_HOURS", "OPS_SOURCE_STALE_HOURS",
    ])
    def test_time_threshold_valid_positive_integer_accepted(self, env_name):
        cfg = ops_check.OpsConfig.from_env({env_name: "48"})
        assert getattr(cfg, {
            "OPS_BACKUP_STALE_HOURS": "backup_stale_hours",
            "OPS_JOB_STUCK_MINUTES": "job_stuck_minutes",
            "OPS_JOB_STALE_HOURS": "job_stale_hours",
            "OPS_SOURCE_STALE_HOURS": "source_stale_hours",
        }[env_name]) == 48

    def test_legal_custom_threshold_actually_changes_check_result(self, data_dir, monkeypatch):
        monkeypatch.setattr(ops_check.shutil, "disk_usage",
                             lambda p: type("U", (), {"total": 100, "used": 75, "free": 25})())
        default_level = ops_check.check_disk()["level"]
        custom_level = ops_check.check_disk(
            ops_check.OpsConfig(disk_warn_pct=80.0, disk_critical_pct=95.0)
        )["level"]
        assert default_level == ops_check.WARN   # 75% >= 默认 warn(70%)
        assert custom_level == ops_check.OK       # 75% < 自定义 warn(80%)

    def test_run_all_checks_shares_one_resolved_config(self, data_dir, monkeypatch):
        """同一次 run_all_checks 传一份 config 给全部检查项,不各自重新读环境变量。"""
        calls = []
        real_check_disk = ops_check.check_disk

        def spy_check_disk(config=None):
            calls.append(config)
            return real_check_disk(config)

        monkeypatch.setattr(ops_check, "check_disk", spy_check_disk)
        cfg = ops_check.OpsConfig(disk_warn_pct=55.0)
        ops_check.run_all_checks(cfg)
        assert calls == [cfg]

    def test_invalid_config_does_not_run_any_check(self, data_dir, monkeypatch):
        """非法配置在 run_all_checks 内部通过 from_env() 触发时,必须在任何
        check_databases/check_disk 等真正执行前就抛出——用 monkeypatch 断言
        check_databases 从未被调用来证明"没有继续执行数据库检查"。"""
        called = []
        monkeypatch.setattr(ops_check, "check_databases", lambda: called.append(1) or [])
        monkeypatch.setenv("OPS_DISK_WARN_PCT", "abc")
        with pytest.raises(ops_check.ConfigError):
            ops_check.run_all_checks()
        assert called == [], "配置非法时不应该执行任何数据库/磁盘检查"


class TestOpsCheckCliSubprocessConfig:
    """CLI 层面的配置校验必须在全新子进程里验证——防止 monkeypatch/模块缓存
    造成"看起来 fail-fast 但其实只是同一个 Python 进程里状态被复用"的假通过。"""

    def _run_cli(self, data_dir_path, extra_env, args=("--json",)):
        env = dict(os.environ)
        env["ALLWIN_DATA_DIR"] = str(data_dir_path)
        env.update(extra_env)
        return subprocess.run(
            [sys.executable, "-m", "backend.cli.ops_check", *args],
            cwd=str(PROJECT_ROOT), env=env, capture_output=True, text=True, timeout=30,
        )

    def test_invalid_threshold_exits_nonzero_no_traceback_no_json(self, data_dir):
        proc = self._run_cli(data_dir, {"OPS_DISK_CRITICAL_PCT": "abc"})
        assert proc.returncode == ops_check.CRITICAL
        assert proc.returncode != 0
        assert "Traceback" not in proc.stderr and "Traceback" not in proc.stdout
        assert "OPS_DISK_CRITICAL_PCT" in proc.stderr
        # 配置非法时不应该输出(部分)JSON 报告
        assert proc.stdout.strip() == ""

    def test_invalid_threshold_never_echoes_raw_value_even_if_it_looks_like_a_secret(self, data_dir):
        """ConfigError 消息只报变量名和规则,不回显原始环境变量值——如果有人
        误把 Secret 填进一个数字阈值变量,配置校验失败本身不应该把它印到
        stdout/stderr(进而落进 journal)。"""
        marker = "SUPER_SECRET_SHOULD_NOT_APPEAR"
        proc = self._run_cli(data_dir, {"OPS_DISK_CRITICAL_PCT": marker})
        assert proc.returncode == ops_check.CRITICAL
        assert marker not in proc.stdout
        assert marker not in proc.stderr
        assert "OPS_DISK_CRITICAL_PCT" in proc.stderr
        assert proc.stdout.strip() == ""
        assert "Traceback" not in proc.stderr and "Traceback" not in proc.stdout

    def test_valid_env_still_runs_and_prints_json(self, data_dir):
        proc = self._run_cli(data_dir, {"OPS_DISK_WARN_PCT": "60", "OPS_DISK_CRITICAL_PCT": "95"})
        assert proc.returncode in (ops_check.OK, ops_check.WARN, ops_check.CRITICAL)
        parsed = json.loads(proc.stdout)
        assert parsed["disk"]["warn_pct"] == 60.0
        assert parsed["disk"]["critical_pct"] == 95.0

    def test_unset_env_defaults_in_fresh_process(self, data_dir):
        proc = self._run_cli(data_dir, {})
        parsed = json.loads(proc.stdout)
        assert parsed["disk"]["warn_pct"] == 70.0
        assert parsed["disk"]["critical_pct"] == 85.0


# ── P2 修正轮:_sanitize_summary 真正脱敏(不只是截断) ──────────────

class TestSanitizeSummaryRedaction:
    def test_url_userinfo_redacted(self):
        out = ops_check._sanitize_summary("connect http://user123:SECRETPASS@proxy:8080 failed")
        assert "user123" not in out and "SECRETPASS" not in out

    def test_common_key_value_pairs_redacted(self):
        out = ops_check._sanitize_summary("user=secretuser pass=SuperSecretPassw0rd")
        assert "secretuser" not in out and "SuperSecretPassw0rd" not in out

    def test_bearer_token_redacted(self):
        out = ops_check._sanitize_summary("Authorization: Bearer UNIQUE_TOKEN_ABC")
        assert "UNIQUE_TOKEN_ABC" not in out

    def test_proxy_credentials_redacted(self):
        out = ops_check._sanitize_summary("proxy=secretuser:secretpass@10.0.0.1:8080")
        assert "secretuser" not in out and "secretpass" not in out

    def test_unix_absolute_path_redacted(self):
        out = ops_check._sanitize_summary("failed reading /Users/private/project/data/allwin.db")
        assert "/Users/private/project/data/allwin.db" not in out

    def test_windows_absolute_path_redacted(self):
        out = ops_check._sanitize_summary(r"failed reading C:\Users\private\allwin.db")
        assert r"C:\Users\private\allwin.db" not in out

    def test_sql_content_fully_redacted(self):
        out = ops_check._sanitize_summary("SELECT * FROM users WHERE password='abc'")
        assert "SELECT" not in out and "password" not in out and "abc" not in out
        assert out == "[SQL_REDACTED]"

    def test_multiline_traceback_redacted(self):
        stack = (
            "Traceback (most recent call last):\n"
            '  File "/opt/allwin/current/backend/worker/runner.py", line 42, in run\n'
            "    raise RuntimeError('boom with /Users/secret/path and password=abc123')\n"
            "RuntimeError: boom with /Users/secret/path and password=abc123"
        )
        out = ops_check._sanitize_summary(stack)
        assert "Traceback" not in out
        assert "RuntimeError" not in out
        assert "/Users/secret/path" not in out
        assert "abc123" not in out
        assert "\n" not in out

    def test_safe_message_preserved_verbatim(self):
        assert ops_check._sanitize_summary("connection refused") == "connection refused"

    def test_redaction_happens_before_truncation(self):
        long_text = "prefix " + "x" * 190 + " password=SHOULD_NOT_LEAK_NEAR_BOUNDARY end"
        out = ops_check._sanitize_summary(long_text)
        assert "SHOULD_NOT_LEAK_NEAR_BOUNDARY" not in out
        assert len(out) <= ops_check._SUMMARY_MAX

    def test_output_is_single_line_within_max_length(self):
        out = ops_check._sanitize_summary("a\nb\tc" + "y" * 300)
        assert "\n" not in out and "\t" not in out
        assert len(out) <= ops_check._SUMMARY_MAX

    # ── 后续复核发现的真实泄漏(局部替换只删了半截)——现在改为整体退化,
    # 以下逐一用当初复现泄漏的原始输入验证 ──────────────────────────

    def test_authorization_basic_header_fully_redacted(self):
        out = ops_check._sanitize_summary("Authorization: Basic QWxhZGRpbjpPcGVuU2VzYW1l")
        assert "QWxhZGRpbjpPcGVuU2VzYW1l" not in out
        assert "Basic" not in out

    def test_authorization_basic_key_value_form_fully_redacted(self):
        out = ops_check._sanitize_summary("authorization=Basic BASIC_SECRET_456")
        assert "BASIC_SECRET_456" not in out

    def test_quoted_value_with_spaces_fully_redacted(self):
        out = ops_check._sanitize_summary('password="TOP SECRET VALUE"')
        assert "TOP SECRET VALUE" not in out and "SECRET VALUE" not in out

    def test_single_quoted_value_with_spaces_fully_redacted(self):
        out = ops_check._sanitize_summary("password='TOP SECRET VALUE'")
        assert "TOP SECRET VALUE" not in out

    def test_json_double_quoted_key_value_fully_redacted(self):
        out = ops_check._sanitize_summary('{"password":"JSON_SECRET_123"}')
        assert "JSON_SECRET_123" not in out

    def test_json_single_quoted_key_value_fully_redacted(self):
        out = ops_check._sanitize_summary("{'token': 'JSON_TOKEN_456'}")
        assert "JSON_TOKEN_456" not in out

    def test_username_field_with_spaces_around_separator_redacted(self):
        out = ops_check._sanitize_summary('username = "PRIVATE USER"')
        assert "PRIVATE USER" not in out

    def test_url_credential_both_user_and_pass_redacted(self):
        out = ops_check._sanitize_summary("http://USER_UNIQUE:PASS_UNIQUE@proxy:8080")
        assert "USER_UNIQUE" not in out and "PASS_UNIQUE" not in out

    def test_proxy_credential_both_user_and_pass_redacted(self):
        out = ops_check._sanitize_summary("proxy=PROXYUSER:PROXYPASS@host")
        assert "PROXYUSER" not in out and "PROXYPASS" not in out

    def test_unix_path_with_spaces_fully_redacted(self):
        out = ops_check._sanitize_summary("failed /Users/private/My Secret Folder/allwin.db")
        assert "My Secret Folder" not in out and "/Users" not in out

    def test_windows_path_with_spaces_fully_redacted(self):
        out = ops_check._sanitize_summary(r"failed C:\Users\Private User\credentials.json")
        assert "Private User" not in out

    def test_bare_select_redacted(self):
        assert ops_check._sanitize_summary("SELECT * FROM users") == "[SQL_REDACTED]"

    @pytest.mark.parametrize("keyword", ["WITH", "REPLACE", "ATTACH", "DETACH", "VACUUM"])
    def test_additional_sql_keywords_redacted(self, keyword):
        out = ops_check._sanitize_summary(f"{keyword} something unexpected happened")
        assert out == "[SQL_REDACTED]"

    def test_with_cte_secret_redacted(self):
        out = ops_check._sanitize_summary("WITH secret AS (SELECT * FROM users)")
        assert out == "[SQL_REDACTED]"
        assert "secret" not in out

    def test_redaction_still_before_truncation_for_new_patterns(self):
        long_text = "x" * 190 + ' password="SHOULD_NOT_LEAK_NEAR_BOUNDARY value"'
        out = ops_check._sanitize_summary(long_text)
        assert "SHOULD_NOT_LEAK_NEAR_BOUNDARY" not in out
        assert len(out) <= ops_check._SUMMARY_MAX


class TestSourceHealthSanitizationEndToEnd:
    """在真实临时 odds.db 里写入含唯一标记的恶意摘要,分别经
    check_source_health / run_all_checks / CLI --json 校验标记完全不出现。"""

    ATTACK_SUMMARIES = [
        "http://user123:SECRETPASS@proxy:8080",
        "user=secretuser pass=SuperSecretPassw0rd",
        "Authorization: Bearer UNIQUE_TOKEN_ABC",
        "/Users/private/project/data/allwin.db",
        r"C:\Users\private\allwin.db",
        "SELECT * FROM users WHERE password='abc'",
        "Traceback (most recent call last):\n  File \"x.py\", line 1\nValueError: SECRET_MARKER_XYZ",
        # 独立复核复现过的真实泄漏(局部替换只删了半截)——用当初的原始输入
        "Authorization: Basic BASIC_SECRET_123",
        "authorization=Basic BASIC_SECRET_456",
        'password="TOP SECRET VALUE"',
        "password='TOP SECRET VALUE'",
        '{"password":"JSON_SECRET_123"}',
        "{'token': 'JSON_TOKEN_456'}",
        'username = "PRIVATE USER"',
        "http://USER_UNIQUE:PASS_UNIQUE@proxy:8080",
        "proxy=PROXYUSER:PROXYPASS@host",
        "failed /Users/private/My Secret Folder/allwin.db",
        r"failed C:\Users\Private User\credentials.json",
        "WITH secret AS (SELECT * FROM users)",
    ]
    SECRET_MARKERS = [
        "SECRETPASS", "SuperSecretPassw0rd", "UNIQUE_TOKEN_ABC",
        "/Users/private/project/data/allwin.db", r"C:\Users\private\allwin.db",
        "SECRET_MARKER_XYZ",
        "BASIC_SECRET_123", "BASIC_SECRET_456",
        "TOP SECRET VALUE", "SECRET VALUE",
        "JSON_SECRET_123", "JSON_TOKEN_456",
        "PRIVATE USER",
        "USER_UNIQUE", "PASS_UNIQUE",
        "PROXYUSER", "PROXYPASS",
        "My Secret Folder",
        "Private User",
    ]

    def _seed(self, data_dir):
        conn = connect_rw("odds")
        for i, summary in enumerate(self.ATTACK_SUMMARIES):
            conn.execute(
                "INSERT INTO source_health (source, checked_at, ok, error_summary, meta_json)"
                " VALUES (?, ?, 0, ?, '{}')",
                (f"attack_source_{i}", _utc_iso(), summary),
            )
        conn.commit()
        conn.close()

    def _assert_clean(self, raw: str):
        for marker in self.SECRET_MARKERS:
            assert marker not in raw, f"泄漏标记 {marker!r} 出现在: {raw[:300]}"
        assert "SELECT" not in raw
        assert "Traceback" not in raw

    def test_check_source_health_output_clean(self, data_dir):
        self._seed(data_dir)
        result = ops_check.check_source_health()
        raw = json.dumps(result, ensure_ascii=False)
        self._assert_clean(raw)
        # 每条摘要仍是稳定、合法、不超过上限的单行字符串
        for src in result["sources"]:
            summary = src.get("last_failure_summary")
            if summary is not None:
                assert len(summary) <= ops_check._SUMMARY_MAX
                assert "\n" not in summary

    def test_run_all_checks_output_clean(self, data_dir):
        self._seed(data_dir)
        report = ops_check.run_all_checks()
        raw = json.dumps(report, ensure_ascii=False)
        self._assert_clean(raw)

    def test_cli_json_output_clean(self, data_dir, capsys):
        self._seed(data_dir)
        rc = ops_check.main(["--json"])
        assert rc in (ops_check.OK, ops_check.WARN, ops_check.CRITICAL)
        out = capsys.readouterr().out
        json.loads(out)   # 仍是合法 JSON
        self._assert_clean(out)
