"""backend/cli/backup_maintenance.py:备份分级保留 + zstd 压缩(2026-09-27)。

策略(以 backup_metadata.json 的 trigger 为准,不按时间推测):
  daily 保留 7 天;release 保留最近 3 份;无 trigger 字段的历史备份按 daily;manual 不自动删;
  最新一份完整备份永不删;不完整/不可解析目录不动;对应 manifests 目录随备份删除;
  创建超过 24 小时的备份三库压成 .zst,zstd -t + sha256 + integrity_check 全部通过才删原文件。
全部测试用 tmp_path 里的真实小型 SQLite 库 + 真实 zstd/sqlite3,不碰真实数据。
"""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.cli import backup_maintenance as bm

ROOT = Path(__file__).resolve().parents[2]
RESTORE_SH = ROOT / "deploy" / "scripts" / "restore_verify.sh"

NOW = datetime(2026, 9, 26, 16, 30, 0, tzinfo=timezone.utc)

needs_zstd = pytest.mark.skipif(shutil.which("zstd") is None, reason="需要 zstd 命令")


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _make_db(path: Path, marker: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO t (v) VALUES (?)", [(f"{marker}-{i}" * 20,) for i in range(500)])
    conn.commit()
    conn.close()


def make_backup(data_dir: Path, age: timedelta, trigger: str | None, *, complete: bool = True,
                with_manifest: bool = True) -> Path:
    created = NOW - age
    name = _stamp(created)
    d = data_dir / "backups" / name
    d.mkdir(parents=True)
    dbs = {}
    for db in bm.REQUIRED_DBS:
        _make_db(d / db, db)
        data = (d / db).read_bytes()
        dbs[db] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "integrity_check": "ok"}
    meta = {"created_at": name, "databases": dbs, "complete": complete}
    if trigger is not None:
        meta["trigger"] = trigger
    (d / "backup_metadata.json").write_text(json.dumps(meta))
    if with_manifest:
        m = data_dir / "backups" / "manifests" / name
        m.mkdir(parents=True)
        (m / "prediction_manifests.json").write_text("[]")
    return d


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    (d / "backups").mkdir(parents=True)
    return d


def _names(data_dir: Path) -> set[str]:
    return {p.name for p in (data_dir / "backups").iterdir() if bm.TS_RE.match(p.name)}


class TestRetentionPlan:
    def test_daily_keeps_seven_days_deletes_older(self, data_dir):
        keep_recent = make_backup(data_dir, timedelta(days=6, hours=23), "daily")
        old = make_backup(data_dir, timedelta(days=7, hours=1), "daily")
        newest = make_backup(data_dir, timedelta(hours=2), "daily")
        rc = bm.run(data_dir, now=NOW, compress=False, log=lambda *_: None)
        assert rc == 0
        assert not old.exists()
        assert keep_recent.exists() and newest.exists()

    def test_release_keeps_latest_three_only(self, data_dir):
        dirs = [make_backup(data_dir, timedelta(hours=h), "release") for h in (5, 4, 3, 2, 1)]  # 旧→新
        bm.run(data_dir, now=NOW, compress=False, log=lambda *_: None)
        assert [d.exists() for d in dirs] == [False, False, True, True, True]

    def test_release_and_daily_classified_independently(self, data_dir):
        # 5 份 release(只留 3)+ 3 份 daily(都在 7 天内,全留)
        rel = [make_backup(data_dir, timedelta(hours=h), "release") for h in (30, 20, 10, 5, 1)]
        day = [make_backup(data_dir, timedelta(days=d), "daily") for d in (1, 2, 3)]
        bm.run(data_dir, now=NOW, compress=False, log=lambda *_: None)
        assert [d.exists() for d in rel] == [False, False, True, True, True]
        assert all(d.exists() for d in day)

    def test_legacy_backup_without_trigger_treated_as_daily_by_age(self, data_dir):
        legacy_old = make_backup(data_dir, timedelta(days=9), None)
        legacy_new = make_backup(data_dir, timedelta(days=2), None)
        newest = make_backup(data_dir, timedelta(hours=1), "daily")
        bm.run(data_dir, now=NOW, compress=False, log=lambda *_: None)
        assert not legacy_old.exists()
        assert legacy_new.exists() and newest.exists()

    def test_manual_backup_never_auto_deleted(self, data_dir):
        manual = make_backup(data_dir, timedelta(days=90), "manual")
        newest = make_backup(data_dir, timedelta(hours=1), "daily")
        bm.run(data_dir, now=NOW, compress=False, log=lambda *_: None)
        assert manual.exists() and newest.exists()

    def test_newest_complete_backup_never_deleted_even_if_old(self, data_dir):
        # 唯一一份备份且已超过 7 天:仍保留(否则会把自己清成零备份)
        only = make_backup(data_dir, timedelta(days=30), "daily")
        bm.run(data_dir, now=NOW, compress=False, log=lambda *_: None)
        assert only.exists()

    def test_manifests_removed_with_backup(self, data_dir):
        old = make_backup(data_dir, timedelta(days=9), "daily")
        make_backup(data_dir, timedelta(hours=1), "daily")
        bm.run(data_dir, now=NOW, compress=False, log=lambda *_: None)
        assert not (data_dir / "backups" / "manifests" / old.name).exists()
        assert len(list((data_dir / "backups" / "manifests").iterdir())) == 1

    def test_incomplete_or_unreadable_directories_untouched(self, data_dir):
        inc = make_backup(data_dir, timedelta(days=30), "daily", complete=False)
        broken = data_dir / "backups" / _stamp(NOW - timedelta(days=40))
        broken.mkdir()
        (broken / "backup_metadata.json").write_text("{not json")
        make_backup(data_dir, timedelta(hours=1), "daily")
        bm.run(data_dir, now=NOW, compress=False, log=lambda *_: None)
        assert inc.exists() and broken.exists()

    def test_dry_run_changes_nothing(self, data_dir):
        old = make_backup(data_dir, timedelta(days=9), "daily")
        make_backup(data_dir, timedelta(hours=30), "daily")
        before = _names(data_dir)
        lines = []
        rc = bm.run(data_dir, now=NOW, dry_run=True, log=lines.append)
        assert rc == 0
        assert _names(data_dir) == before and old.exists()
        text = "\n".join(lines)
        assert "[dry-run] 将删除" in text and "[dry-run] 将压缩" in text
        assert (data_dir / "backups" / old.name / "allwin.db").exists()  # 没压缩也没删


@needs_zstd
class TestCompression:
    def _restore_verify(self, data_dir: Path):
        env = dict(os.environ, ALLWIN_DATA_DIR=str(data_dir))
        return subprocess.run(["bash", str(RESTORE_SH)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)

    def test_only_backups_older_than_24h_compressed_and_originals_removed(self, data_dir):
        fresh = make_backup(data_dir, timedelta(hours=5), "daily")
        aged = make_backup(data_dir, timedelta(hours=30), "daily")
        assert bm.run(data_dir, now=NOW, log=lambda *_: None) == 0
        for db in bm.REQUIRED_DBS:
            assert (fresh / db).exists() and not (fresh / f"{db}.zst").exists()
            assert not (aged / db).exists() and (aged / f"{db}.zst").exists()
        meta = json.loads((aged / "backup_metadata.json").read_text())
        assert meta["compression"]["algo"] == "zstd"
        assert set(meta["compression"]["files"]) == set(bm.REQUIRED_DBS)
        # metadata 里的 size/sha256 仍是未压缩值
        assert meta["databases"]["allwin.db"]["size"] > meta["compression"]["files"]["allwin.db"]["zst_size"]
        assert meta["complete"] is True
        assert (aged / "allwin.db.zst").stat().st_mode & 0o077 == 0  # 不放宽权限

    def test_second_run_is_idempotent(self, data_dir):
        aged = make_backup(data_dir, timedelta(hours=30), "daily")
        bm.run(data_dir, now=NOW, log=lambda *_: None)
        lines = []
        assert bm.run(data_dir, now=NOW, log=lines.append) == 0
        assert "没有需要压缩的备份" in "\n".join(lines)
        assert (aged / "allwin.db.zst").exists()

    def test_compressed_backup_decompresses_to_identical_databases(self, data_dir):
        aged = make_backup(data_dir, timedelta(hours=30), "daily")
        meta = json.loads((aged / "backup_metadata.json").read_text())
        bm.run(data_dir, now=NOW, log=lambda *_: None)
        for db in bm.REQUIRED_DBS:
            plain = subprocess.run(["zstd", "-q", "-d", "-c", str(aged / f"{db}.zst")], capture_output=True).stdout
            assert hashlib.sha256(plain).hexdigest() == meta["databases"][db]["sha256"]

    def test_sha_mismatch_keeps_originals_and_removes_zst(self, data_dir):
        aged = make_backup(data_dir, timedelta(hours=30), "daily")
        meta_path = aged / "backup_metadata.json"
        meta = json.loads(meta_path.read_text())
        meta["databases"]["odds.db"]["sha256"] = "0" * 64   # 模拟"备份文件与记录不一致"
        meta_path.write_text(json.dumps(meta))
        lines = []
        rc = bm.run(data_dir, now=NOW, log=lines.append)
        assert rc == 2
        for db in bm.REQUIRED_DBS:
            assert (aged / db).exists(), "校验失败必须保留原文件"
            assert not (aged / f"{db}.zst").exists(), "校验失败必须清掉本次产生的 .zst"
        assert "sha256 与 metadata 不一致" in "\n".join(lines)
        assert "compression" not in json.loads(meta_path.read_text())

    def test_corrupt_source_integrity_failure_keeps_originals(self, data_dir):
        aged = make_backup(data_dir, timedelta(hours=30), "daily")
        db = aged / "platform.db"
        raw = bytearray(db.read_bytes())
        for i in range(2048, 2048 + 4096):
            raw[i] = (raw[i] + 1) % 256   # 损坏页内容(sha 用损坏后的重算,只让 integrity_check 挡住)
        db.write_bytes(bytes(raw))
        meta_path = aged / "backup_metadata.json"
        meta = json.loads(meta_path.read_text())
        meta["databases"]["platform.db"]["sha256"] = hashlib.sha256(bytes(raw)).hexdigest()
        meta["databases"]["platform.db"]["size"] = len(raw)
        meta_path.write_text(json.dumps(meta))
        rc = bm.run(data_dir, now=NOW, log=lambda *_: None)
        assert rc == 2
        for name in bm.REQUIRED_DBS:
            assert (aged / name).exists() and not (aged / f"{name}.zst").exists()

    def test_missing_db_file_skips_compression(self, data_dir):
        aged = make_backup(data_dir, timedelta(hours=30), "daily")
        (aged / "odds.db").unlink()
        rc = bm.run(data_dir, now=NOW, log=lambda *_: None)
        assert rc == 2
        assert (aged / "allwin.db").exists() and not (aged / "allwin.db.zst").exists()

    def test_deleted_backups_are_not_compressed(self, data_dir):
        old = make_backup(data_dir, timedelta(days=9), "daily")
        make_backup(data_dir, timedelta(hours=1), "daily")
        bm.run(data_dir, now=NOW, log=lambda *_: None)
        assert not old.exists()

    def test_no_compress_flag(self, data_dir):
        aged = make_backup(data_dir, timedelta(hours=30), "daily")
        bm.run(data_dir, now=NOW, compress=False, log=lambda *_: None)
        assert (aged / "allwin.db").exists() and not (aged / "allwin.db.zst").exists()

    def test_insufficient_free_space_skips_compression(self, data_dir, monkeypatch):
        aged = make_backup(data_dir, timedelta(hours=30), "daily")
        monkeypatch.setattr(bm.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(100, 99, 1))
        lines = []
        rc = bm.run(data_dir, now=NOW, log=lines.append)
        assert rc == 2
        assert "可用空间" in "\n".join(lines)
        assert (aged / "allwin.db").exists()

    def test_restore_verify_accepts_compressed_backup(self, migrated_backup_dir):
        data_dir, name = migrated_backup_dir
        r0 = self._restore_verify(data_dir)
        assert r0.returncode == 0, r0.stdout + r0.stderr
        # 压缩(把"现在"设到备份 30 小时之后)
        created = datetime.strptime(name, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        assert bm.run(data_dir, now=created + timedelta(hours=30), log=lambda *_: None) == 0
        assert not (data_dir / "backups" / name / "allwin.db").exists()
        r = self._restore_verify(data_dir)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "从 allwin.db.zst 解压" in r.stdout
        assert "checksum 一致,integrity_check=ok" in r.stdout

    def test_restore_verify_rejects_corrupt_zst(self, migrated_backup_dir):
        data_dir, name = migrated_backup_dir
        created = datetime.strptime(name, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        bm.run(data_dir, now=created + timedelta(hours=30), log=lambda *_: None)
        z = data_dir / "backups" / name / "odds.db.zst"
        z.write_bytes(z.read_bytes()[: len(z.read_bytes()) // 2])
        r = self._restore_verify(data_dir)
        assert r.returncode != 0


@pytest.fixture
def migrated_backup_dir(tmp_path):
    """真实迁移三库 + 真实 backup_sqlite.sh 产生一份备份,返回 (data_dir, 备份目录名)。"""
    d = tmp_path / "data"
    d.mkdir()
    env = dict(os.environ, ALLWIN_DATA_DIR=str(d))
    for name in ("core", "platform", "odds"):
        r = subprocess.run(["python3", "-m", "backend.db.migrate", "--db", name], cwd=ROOT, env=env,
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    r = subprocess.run(["bash", str(ROOT / "deploy" / "scripts" / "backup_sqlite.sh")], cwd=ROOT,
                       env=dict(env, BACKUP_SKIP_MAINTENANCE="1"), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    names = [p.name for p in (d / "backups").iterdir() if bm.TS_RE.match(p.name)]
    assert len(names) == 1
    return d, names[0]


class TestCli:
    def test_invalid_keep_values_rejected(self, data_dir):
        assert bm.main(["--data-dir", str(data_dir), "--daily-keep-days", "0"]) == 1
        assert bm.main(["--data-dir", str(data_dir), "--release-keep", "0"]) == 1

    def test_empty_backup_dir_is_ok(self, data_dir):
        assert bm.main(["--data-dir", str(data_dir), "--dry-run"]) == 0

    def test_now_flag_and_output_lists_classification(self, data_dir, capsys):
        make_backup(data_dir, timedelta(days=1), "release")
        make_backup(data_dir, timedelta(days=1, hours=1), None)
        rc = bm.main(["--data-dir", str(data_dir), "--dry-run", "--now", "2026-09-26T16:30:00Z"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "release" in out and "legacy→daily" in out and "保留" in out
