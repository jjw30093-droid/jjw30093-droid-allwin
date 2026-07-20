"""deploy/scripts/backup_sqlite.sh + restore_verify.sh 永久回归测试
(生产可靠性收口:三库备份/恢复的原子性、完整性、并发锁)。

背景(本轮审计,真实执行确认,不是假设):
- 修复前 backup_sqlite.sh 缺任意一至两个库仍然 exit 0(只在"一个都没有"时失败);
- 修复前 restore_verify.sh 用裸 glob `*.db` 遍历,缺库/缺 manifest/缺 checksum
  都不会被发现,同样 exit 0;
- 修复前两个并发 backup_sqlite.sh 调用没有任何锁保护,只靠 SQLite 自身的文件锁
  报错("database is locked"),且同一 UTC 秒(1 秒粒度时间戳)会计算出相同的
  目标目录名。

全部测试只用 tmp_path 临时三库 + 真实子进程调用两个脚本本体(不 mock 脚本逻辑),
不碰真实 data/*.db,不依赖网络(S3 用假 aws 可执行文件模拟离线行为)。
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKUP_SH = ROOT / "deploy" / "scripts" / "backup_sqlite.sh"
RESTORE_SH = ROOT / "deploy" / "scripts" / "restore_verify.sh"


def _migrate_all(data_dir: Path) -> None:
    """真实迁移三库(不是手搓 CREATE TABLE)——子进程运行,避免污染当前
    pytest 进程的 ALLWIN_DATA_DIR/模块状态。"""
    env = dict(os.environ, ALLWIN_DATA_DIR=str(data_dir))
    for name in ("core", "platform", "odds"):
        r = subprocess.run(
            [sys.executable, "-m", "backend.db.migrate", "--db", name],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )
        assert r.returncode == 0, f"迁移 {name} 失败:\n{r.stdout}\n{r.stderr}"


def _run_backup(data_dir: Path, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ, ALLWIN_DATA_DIR=str(data_dir))
    env.update(env_extra or {})
    return subprocess.run(
        ["bash", str(BACKUP_SH)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
    )


def _run_restore_verify(data_dir: Path, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ, ALLWIN_DATA_DIR=str(data_dir))
    env.update(env_extra or {})
    return subprocess.run(
        ["bash", str(RESTORE_SH)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
    )


def _backup_dirs(data_dir: Path) -> list[Path]:
    root = data_dir / "backups"
    if not root.exists():
        return []
    import re

    pat = re.compile(r"^\d{8}T\d{6}Z$")
    return sorted(p for p in root.iterdir() if p.is_dir() and pat.match(p.name))


@pytest.fixture
def migrated_data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    _migrate_all(d)
    return d


def _real_db_snapshot(names=("allwin.db", "platform.db", "odds.db")) -> dict:
    snap = {}
    for name in names:
        p = ROOT / "data" / name
        if p.exists():
            st = p.stat()
            snap[name] = (st.st_size, st.st_mtime)
    return snap


class TestBackupCompleteness:
    def test_all_three_dbs_backed_up_successfully(self, migrated_data_dir):
        before = _real_db_snapshot()
        r = _run_backup(migrated_data_dir)
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        dirs = _backup_dirs(migrated_data_dir)
        assert len(dirs) == 1
        meta = json.loads((dirs[0] / "backup_metadata.json").read_text())
        assert meta["complete"] is True
        assert set(meta["databases"].keys()) == {"allwin.db", "platform.db", "odds.db"}
        for name in ("allwin.db", "platform.db", "odds.db"):
            assert (dirs[0] / name).exists()
            assert meta["databases"][name]["integrity_check"] == "ok"
        assert _real_db_snapshot() == before, "备份脚本不得改动真实 data/*.db"

    def test_missing_one_db_fails_not_silently_succeeds(self, migrated_data_dir):
        (migrated_data_dir / "odds.db").unlink()
        r = _run_backup(migrated_data_dir)
        assert r.returncode != 0, "缺一个库时必须失败,不能静默成功"
        assert _backup_dirs(migrated_data_dir) == [], "失败时不得留下看似完整的备份目录"

    def test_missing_two_dbs_fails(self, migrated_data_dir):
        (migrated_data_dir / "odds.db").unlink()
        (migrated_data_dir / "platform.db").unlink()
        r = _run_backup(migrated_data_dir)
        assert r.returncode != 0
        assert _backup_dirs(migrated_data_dir) == []

    def test_no_incomplete_staging_dir_left_after_failure(self, migrated_data_dir):
        (migrated_data_dir / "odds.db").unlink()
        _run_backup(migrated_data_dir)
        leftover = list((migrated_data_dir / "backups").glob(".incomplete-*"))
        assert leftover == [], f"失败后不得残留 staging 目录: {leftover}"


class TestBackupAtomicity:
    def test_corrupt_source_db_fails_integrity_check(self, migrated_data_dir):
        # 源库本身损坏(不是备份文件损坏)——.backup 出的副本 integrity_check 必须失败
        (migrated_data_dir / "odds.db").write_bytes(b"not a real sqlite file at all")
        r = _run_backup(migrated_data_dir)
        assert r.returncode != 0
        assert "integrity_check" in r.stdout or "integrity_check" in r.stderr
        assert _backup_dirs(migrated_data_dir) == []


class TestRestoreVerifyRejectsIncomplete:
    def test_accepts_genuine_complete_backup(self, migrated_data_dir):
        assert _run_backup(migrated_data_dir).returncode == 0
        r = _run_restore_verify(migrated_data_dir)
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert "恢复演练通过" in r.stdout

    def test_rejects_backup_dir_missing_metadata(self, migrated_data_dir, tmp_path):
        # 手工构造一个"看起来像备份"但没有 backup_metadata.json 的目录
        fake = migrated_data_dir / "backups" / "20990101T000000Z"
        fake.mkdir(parents=True)
        for name in ("allwin.db", "platform.db", "odds.db"):
            (fake / name).write_bytes((migrated_data_dir / name).read_bytes())
        r = _run_restore_verify(migrated_data_dir)
        assert r.returncode != 0, "无 backup_metadata.json 的目录不得被当作有效备份"

    def test_rejects_backup_missing_one_db_file(self, migrated_data_dir):
        assert _run_backup(migrated_data_dir).returncode == 0
        dirs = _backup_dirs(migrated_data_dir)
        (dirs[0] / "odds.db").unlink()
        r = _run_restore_verify(migrated_data_dir)
        assert r.returncode != 0, "备份目录事后缺失一个库文件时必须拒绝"

    def test_rejects_tampered_checksum(self, migrated_data_dir):
        assert _run_backup(migrated_data_dir).returncode == 0
        dirs = _backup_dirs(migrated_data_dir)
        with open(dirs[0] / "platform.db", "ab") as f:
            f.write(b"tampered-bytes-appended-after-backup")
        r = _run_restore_verify(migrated_data_dir)
        assert r.returncode != 0, "备份文件内容与 metadata 记录的 SHA-256 不一致时必须拒绝"

    def test_rejects_metadata_missing_db_entry(self, migrated_data_dir):
        assert _run_backup(migrated_data_dir).returncode == 0
        dirs = _backup_dirs(migrated_data_dir)
        meta_path = dirs[0] / "backup_metadata.json"
        meta = json.loads(meta_path.read_text())
        del meta["databases"]["odds.db"]
        meta_path.write_text(json.dumps(meta))
        r = _run_restore_verify(migrated_data_dir)
        assert r.returncode != 0

    def test_no_backups_at_all_fails_clearly(self, tmp_path):
        d = tmp_path / "data"
        (d / "backups").mkdir(parents=True)
        r = _run_restore_verify(d)
        assert r.returncode != 0

    def test_source_backup_and_real_db_untouched_by_restore(self, migrated_data_dir):
        before_real = _real_db_snapshot()
        assert _run_backup(migrated_data_dir).returncode == 0
        dirs_before = _backup_dirs(migrated_data_dir)
        backup_snapshot_before = {
            p.name: p.stat().st_mtime for p in dirs_before[0].iterdir()
        }
        r = _run_restore_verify(migrated_data_dir)
        assert r.returncode == 0
        backup_snapshot_after = {
            p.name: p.stat().st_mtime for p in dirs_before[0].iterdir()
        }
        assert backup_snapshot_before == backup_snapshot_after, "restore_verify 不得修改备份源"
        assert _real_db_snapshot() == before_real, "restore_verify 不得修改真实 data/*.db"


class TestMigrationDriftDetection:
    def test_restore_verify_fails_on_pending_migration(self, migrated_data_dir):
        """备份出的库缺少最新迁移(模拟"备份时用的是旧 schema")时,restore_verify
        必须报告 pending 并失败,而不是只检查 integrity_check。"""
        assert _run_backup(migrated_data_dir).returncode == 0
        dirs = _backup_dirs(migrated_data_dir)
        import sqlite3

        conn = sqlite3.connect(dirs[0] / "platform.db")
        conn.execute("DELETE FROM schema_migrations WHERE version=(SELECT MAX(version) FROM schema_migrations)")
        conn.commit()
        conn.close()
        # 篡改后重算 checksum/size,让"文件层面"看起来一致,只有 migration 状态本身有问题
        _rewrite_metadata_for(dirs[0])
        r = _run_restore_verify(migrated_data_dir)
        assert r.returncode != 0
        assert "pending" in r.stdout or "pending" in r.stderr

    def test_restore_verify_fails_on_checksum_drift(self, migrated_data_dir):
        assert _run_backup(migrated_data_dir).returncode == 0
        dirs = _backup_dirs(migrated_data_dir)
        import sqlite3

        conn = sqlite3.connect(dirs[0] / "platform.db")
        conn.execute("UPDATE schema_migrations SET checksum='deliberately-wrong' WHERE version=1")
        conn.commit()
        conn.close()
        _rewrite_metadata_for(dirs[0])
        r = _run_restore_verify(migrated_data_dir)
        assert r.returncode != 0
        assert "drift" in r.stdout or "drift" in r.stderr


def _rewrite_metadata_for(backup_dir: Path) -> None:
    """测试专用:在直接改写库文件内容后,重算 metadata 里的 size/sha256,
    使 checksum 校验本身不成为这些"migration 状态"测试的干扰变量——
    这里只想单独验证 migration pending/drift 检测,不是在测 checksum 校验。"""
    import hashlib

    meta_path = backup_dir / "backup_metadata.json"
    meta = json.loads(meta_path.read_text())
    for name in meta["databases"]:
        data = (backup_dir / name).read_bytes()
        meta["databases"][name]["size"] = len(data)
        meta["databases"][name]["sha256"] = hashlib.sha256(data).hexdigest()
    meta_path.write_text(json.dumps(meta))


class TestBackupKeepValidation:
    def test_non_numeric_backup_keep_rejected(self, migrated_data_dir):
        r = _run_backup(migrated_data_dir, {"BACKUP_KEEP": "abc"})
        assert r.returncode != 0

    def test_zero_backup_keep_rejected(self, migrated_data_dir):
        r = _run_backup(migrated_data_dir, {"BACKUP_KEEP": "0"})
        assert r.returncode != 0

    def test_negative_backup_keep_rejected(self, migrated_data_dir):
        r = _run_backup(migrated_data_dir, {"BACKUP_KEEP": "-3"})
        assert r.returncode != 0

    def test_backup_keep_prunes_old_complete_backups_only(self, migrated_data_dir):
        """连续多次备份(每次跨秒边界),只保留最近 KEEP 份;.incomplete-* 与
        manifests 目录不应被误当成一份数据库备份计数。"""
        for _ in range(4):
            r = _run_backup(migrated_data_dir, {"BACKUP_KEEP": "2"})
            assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
            time.sleep(1.1)   # 保证下一次 UTC 秒级时间戳不同
        dirs = _backup_dirs(migrated_data_dir)
        assert len(dirs) == 2, f"BACKUP_KEEP=2 应只保留 2 份,实际 {len(dirs)}: {dirs}"


class TestConcurrentBackups:
    def test_concurrent_invocations_do_not_corrupt_or_duplicate(self, migrated_data_dir):
        """多进程并发调用:恰好一个成功(exit 0),其余明确跳过(exit 75,
        不是失败也不是静默成功),结果目录里不出现半成品或混合内容。"""
        import concurrent.futures

        def _one():
            return _run_backup(migrated_data_dir)

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
            results = list(ex.map(lambda _: _one(), range(5)))

        codes = sorted(r.returncode for r in results)
        successes = [r for r in results if r.returncode == 0]
        skips = [r for r in results if r.returncode == 75]
        assert len(successes) == 1, f"应恰好一个成功,实际退出码: {codes}"
        assert len(successes) + len(skips) == 5, f"其余必须是明确跳过(75),不是别的退出码: {codes}"

        dirs = _backup_dirs(migrated_data_dir)
        assert len(dirs) == 1
        meta = json.loads((dirs[0] / "backup_metadata.json").read_text())
        assert meta["complete"] is True
        for name in ("allwin.db", "platform.db", "odds.db"):
            assert (dirs[0] / name).exists()

        leftover_locks = list((migrated_data_dir / "backups").glob(".backup.lock"))
        assert leftover_locks == [], "备份完成后不应残留锁文件"
        leftover_incomplete = list((migrated_data_dir / "backups").glob(".incomplete-*"))
        assert leftover_incomplete == [], "备份完成后不应残留 staging 目录"


class TestS3Offline:
    def test_unconfigured_is_local_only(self, migrated_data_dir):
        r = _run_backup(migrated_data_dir)
        assert r.returncode == 0
        assert "LOCAL_ONLY" in r.stdout

    def test_configured_without_aws_cli_fails(self, migrated_data_dir):
        env = dict(os.environ)
        env["PATH"] = "/usr/bin:/bin"   # 剥离掉可能存在的 aws
        env["ALLWIN_DATA_DIR"] = str(migrated_data_dir)
        env["S3_BACKUP_BUCKET"] = "fake-bucket"
        r = subprocess.run(["bash", str(BACKUP_SH)], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=60)
        assert r.returncode != 0
        assert "aws" in r.stdout or "aws" in r.stderr

    def test_configured_with_failing_fake_aws_fails(self, migrated_data_dir, tmp_path):
        fake_bin = tmp_path / "fakebin"
        fake_bin.mkdir()
        (fake_bin / "aws").write_text("#!/usr/bin/env bash\nexit 1\n")
        (fake_bin / "aws").chmod(0o755)
        env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}",
                   S3_BACKUP_BUCKET="fake-bucket")
        r = _run_backup(migrated_data_dir, env)
        assert r.returncode != 0
        assert "S3" in r.stdout or "S3" in r.stderr

    def test_configured_with_succeeding_fake_aws_succeeds(self, migrated_data_dir, tmp_path):
        fake_bin = tmp_path / "fakebin"
        fake_bin.mkdir()
        (fake_bin / "aws").write_text("#!/usr/bin/env bash\necho \"$@\" >> \"$FAKE_AWS_LOG\"\nexit 0\n")
        (fake_bin / "aws").chmod(0o755)
        log_file = tmp_path / "aws_calls.log"
        env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}",
                   S3_BACKUP_BUCKET="fake-bucket", FAKE_AWS_LOG=str(log_file))
        r = _run_backup(migrated_data_dir, env)
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert log_file.exists()
        assert "s3://fake-bucket" in log_file.read_text()


class TestPermissions:
    def test_backup_files_not_group_or_other_readable(self, migrated_data_dir):
        assert _run_backup(migrated_data_dir).returncode == 0
        dirs = _backup_dirs(migrated_data_dir)
        for p in dirs[0].iterdir():
            mode = p.stat().st_mode & 0o777
            assert mode & 0o077 == 0, f"{p} 权限过宽: {oct(mode)}(不得组/其他用户可读写)"
