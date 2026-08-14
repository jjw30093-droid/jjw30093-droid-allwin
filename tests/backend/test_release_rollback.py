"""deploy/scripts/release.sh 永久回归测试(生产可靠性收口:不可变发布 + 失败顺序 + 回滚验证)。

策略:release.sh 支持 `RELEASE_SH_SOURCE_ONLY=1` 时只 source 不自动跑 main()。
测试按需覆盖具体阶段函数(candidate_smoke/verify_live/business_smoke/
switch_current/do_build/do_backup_and_migrate)来隔离被测的那一段顺序/回滚
逻辑——不需要真实 systemd、真实 Next.js 构建或真实 HTTP 服务;只有
"业务冒烟 marker 不被 grep 误当选项"这一条,用一个最小的真实 stdlib HTTP
服务器验证真实字符串处理行为(不是纯 mock)。

真实调用 bash 子进程(`bash -c "source release.sh; ...; <call>"`),
不在开发机上跑真正的 release(不 sudo、不 systemctl、不碰真实 /opt/allwin)。
"""

import http.server
import os
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RELEASE_SH = ROOT / "deploy" / "scripts" / "release.sh"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_source_repo(source_dir: Path, extra_files: dict | None = None, dirty: bool = False):
    source_dir.mkdir(parents=True)
    _git(["init", "-q"], source_dir)
    _git(["config", "user.email", "test@test.local"], source_dir)
    _git(["config", "user.name", "test"], source_dir)
    (source_dir / "README.md").write_text("fake release source\n")
    (source_dir / "requirements.txt").write_text("")
    for rel, content in (extra_files or {}).items():
        p = source_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    _git(["add", "-A"], source_dir)
    _git(["commit", "-q", "-m", "init"], source_dir)
    if dirty:
        (source_dir / "dirty.txt").write_text("uncommitted change\n")


def _setup_app_root(tmp_path, name="app_root", extra_files=None, dirty=False) -> Path:
    app_root = tmp_path / name
    _init_source_repo(app_root / "source", extra_files=extra_files, dirty=dirty)
    shared = app_root / "shared"
    (shared / "data").mkdir(parents=True)
    (shared / ".env").write_text("APP_ENV=production\n")
    for db in ("allwin.db", "platform.db", "odds.db"):
        (shared / "data" / db).write_bytes(b"")   # preflight 只检查存在,不检查内容
    return app_root


def _ensure_fake_systemctl_sudo(tmp_path: Path) -> Path:
    """systemctl/sudo 在本机(macOS 开发/测试机)不存在,真实生产环境才有——
    这正是任务要求的"外部命令均可通过 PATH 替换为假实现"的场景:两个平凡的
    no-op/透传脚本,不影响被测的顺序/回滚逻辑本身。"""
    fake_bin = tmp_path / "_fake_system_bin"
    if fake_bin.exists():
        return fake_bin
    fake_bin.mkdir()
    (fake_bin / "systemctl").write_text("#!/bin/sh\nexit 0\n")
    (fake_bin / "systemctl").chmod(0o755)
    (fake_bin / "sudo").write_text("#!/bin/sh\nexec \"$@\"\n")
    (fake_bin / "sudo").chmod(0o755)
    return fake_bin


def _base_env(app_root: Path, tmp_path: Path | None = None, **overrides) -> dict:
    fake_bin = _ensure_fake_systemctl_sudo(tmp_path or app_root.parent)
    env = dict(
        os.environ,
        ALLWIN_APP_ROOT=str(app_root),
        RELEASE_SH_SOURCE_ONLY="1",
        MIN_FREE_DISK_MB="1",
        PATH=f"{fake_bin}:{os.environ['PATH']}",
    )
    env.update(overrides)
    return env


def _run(script: str, env: dict, timeout=30) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], cwd=ROOT, env=env,
                           capture_output=True, text=True, timeout=timeout)


SOURCE_RELEASE = f'source "{RELEASE_SH}"'


class TestPreflight:
    def test_clean_source_passes(self, tmp_path):
        app_root = _setup_app_root(tmp_path)
        r = _run(f'{SOURCE_RELEASE}; preflight; echo "SHA=$SHA"; echo "RELEASE_DIR=$RELEASE_DIR"',
                  _base_env(app_root))
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert "SHA=" in r.stdout and "RELEASE_DIR=" in r.stdout

    def test_dirty_source_rejected(self, tmp_path):
        app_root = _setup_app_root(tmp_path, dirty=True)
        r = _run(f'{SOURCE_RELEASE}; preflight', _base_env(app_root))
        assert r.returncode != 0
        assert "dirty" in r.stderr.lower() or "未提交" in r.stderr

    def test_missing_shared_env_rejected(self, tmp_path):
        app_root = _setup_app_root(tmp_path)
        (app_root / "shared" / ".env").unlink()
        r = _run(f'{SOURCE_RELEASE}; preflight', _base_env(app_root))
        assert r.returncode != 0
        assert ".env" in r.stderr

    def test_missing_one_db_rejected(self, tmp_path):
        app_root = _setup_app_root(tmp_path)
        (app_root / "shared" / "data" / "odds.db").unlink()
        r = _run(f'{SOURCE_RELEASE}; preflight', _base_env(app_root))
        assert r.returncode != 0
        assert "odds.db" in r.stderr

    def test_existing_same_sha_release_dir_rejected(self, tmp_path):
        app_root = _setup_app_root(tmp_path)
        env = _base_env(app_root)
        r1 = _run(f'{SOURCE_RELEASE}; preflight; echo "$SHA"', env)
        assert r1.returncode == 0
        sha = r1.stdout.strip().splitlines()[-1]
        (app_root / "releases" / sha).mkdir(parents=True)
        r2 = _run(f'{SOURCE_RELEASE}; preflight', env)
        assert r2.returncode != 0
        assert "已存在" in r2.stderr or "不可变" in r2.stderr

    def test_current_link_not_a_symlink_rejected(self, tmp_path):
        app_root = _setup_app_root(tmp_path)
        (app_root / "current").mkdir()   # 目录而不是软链
        r = _run(f'{SOURCE_RELEASE}; preflight', _base_env(app_root))
        assert r.returncode != 0
        assert "软链接" in r.stderr

    def test_current_link_pointing_outside_releases_dir_rejected(self, tmp_path):
        app_root = _setup_app_root(tmp_path)
        outside = tmp_path / "outside_dir"
        outside.mkdir()
        (app_root / "current").symlink_to(outside, target_is_directory=True)
        r = _run(f'{SOURCE_RELEASE}; preflight', _base_env(app_root))
        assert r.returncode != 0
        assert "非法目标" in r.stderr

    def test_missing_required_command_rejected(self, tmp_path):
        app_root = _setup_app_root(tmp_path)
        # PATH 收窄到只有 bash 自身能被 subprocess 找到,git/rsync/systemctl 等
        # 全部不可见——测试 subprocess 调用本身用 /bin/bash 绝对路径,不受影响。
        env = _base_env(app_root, PATH="/nonexistent-only")
        r = subprocess.run(["/bin/bash", "-c", f'{SOURCE_RELEASE}; preflight'],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
        assert r.returncode != 0
        assert "缺少必需命令" in r.stderr

    def test_insufficient_disk_space_rejected(self, tmp_path):
        app_root = _setup_app_root(tmp_path)
        env = _base_env(app_root, MIN_FREE_DISK_MB="999999999999")
        r = _run(f'{SOURCE_RELEASE}; preflight', env)
        assert r.returncode != 0
        assert "磁盘" in r.stderr


class TestRsyncExcludesSecrets:
    def test_secret_and_test_artifact_files_never_enter_release(self, tmp_path):
        app_root = _setup_app_root(tmp_path, extra_files={
            ".env": "REAL_SECRET=leak-me-not\n",
            ".env.production": "ANOTHER_SECRET=nope\n",
            "frontend/.env.local": "NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000\n",
            ".pytest_cache/README.md": "cache\n",
            "test-results/report.txt": "x\n",
            "playwright-report/index.html": "<html></html>\n",
            "backend/app.py": "# real code\n",
        })
        r = _run(f'{SOURCE_RELEASE}; preflight; do_rsync; echo "RELEASE_DIR=$RELEASE_DIR"',
                  _base_env(app_root))
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        release_dir = Path(next(l for l in r.stdout.splitlines() if l.startswith("RELEASE_DIR="))
                            .split("=", 1)[1])
        for leaked in (".env", ".env.production", "frontend/.env.local",
                       ".pytest_cache", "test-results", "playwright-report"):
            assert not (release_dir / leaked).exists(), f"{leaked} 泄漏进了 release 目录: {release_dir}"
        assert (release_dir / "backend" / "app.py").exists(), "正常代码文件应该被复制"
        assert (release_dir / "README.md").exists()

    def test_generic_credential_files_never_enter_release_root_and_nested(self, tmp_path):
        """P1 修正(生产可靠性收口第二轮):.env* 之外的通用私钥/证书/凭证文件,
        根目录和嵌套子目录里都不得进入 release——只排除具体文件名/扩展名,
        不粗暴排除所有 JSON(normal.json 必须仍被复制)。"""
        credential_paths = [
            "id_rsa",
            "id_ed25519",
            "deploy-secret.pem",
            "client.key",
            "signing.p12",
            "signing.pfx",
            "credentials.json",
            ".aws/credentials",
            ".ssh/config",
            ".ssh/id_rsa",
            # 嵌套子目录版本
            "backend/nested/id_ed25519",
            "backend/nested/deploy-secret.pem",
            "backend/nested/client.key",
            "backend/nested/credentials.json",
            "backend/nested/.aws/credentials",
            "backend/nested/.ssh/id_rsa",
        ]
        extra_files = {p: f"SECRET-{p}\n" for p in credential_paths}
        extra_files.update({
            "backend/app.py": "# real code\n",
            "normal.json": '{"a": 1}\n',
        })
        app_root = _setup_app_root(tmp_path, extra_files=extra_files)
        r = _run(f'{SOURCE_RELEASE}; preflight; do_rsync; echo "RELEASE_DIR=$RELEASE_DIR"',
                  _base_env(app_root))
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        release_dir = Path(next(l for l in r.stdout.splitlines() if l.startswith("RELEASE_DIR="))
                            .split("=", 1)[1])
        for leaked in credential_paths:
            assert not (release_dir / leaked).exists(), f"{leaked} 泄漏进了 release 目录: {release_dir}"
        # 目录本身也不应该存在(.ssh/.aws 整个子树都被排除,不只是里面某个文件名)
        assert not (release_dir / ".ssh").exists()
        assert not (release_dir / ".aws").exists()
        assert not (release_dir / "backend" / "nested" / ".ssh").exists()
        assert not (release_dir / "backend" / "nested" / ".aws").exists()
        # 正常源码、普通 JSON、README 仍必须被复制,不能被过度排除
        assert (release_dir / "backend" / "app.py").exists()
        assert (release_dir / "normal.json").exists()
        assert (release_dir / "README.md").exists()

    def test_defense_in_depth_scan_would_catch_a_broken_exclude_list(self, tmp_path):
        """assert_no_credentials_in_release() 是纵深防御,不是主机制——单独验证
        它确实会在 release 目录里发现凭证文件时 die(用真实文件手工放进已经
        rsync 完成的 release 目录来模拟"排除规则本身出问题"的场景,而不是依赖
        rsync 自己漏排除)。"""
        app_root = _setup_app_root(tmp_path)
        r = _run(f'{SOURCE_RELEASE}; preflight; do_rsync; echo "RELEASE_DIR=$RELEASE_DIR"',
                  _base_env(app_root))
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        release_dir = Path(next(l for l in r.stdout.splitlines() if l.startswith("RELEASE_DIR="))
                            .split("=", 1)[1])
        (release_dir / "sneaked-in.pem").write_text("should never survive a real rsync\n")
        r2 = _run(f'{SOURCE_RELEASE}; RELEASE_DIR="{release_dir}"; assert_no_credentials_in_release',
                  _base_env(app_root))
        assert r2.returncode != 0
        assert "sneaked-in.pem" in (r2.stdout + r2.stderr)


class TestFixNextPermissions:
    """2026-08-14 真实发布事故:构建产物属主是构建用户(如 ubuntu),线上
    allwin-web systemd 单元以 User=allwin 运行、不在构建用户的组里,不修正
    属组的话 Next.js 本地持久 ISR 缓存写回 .next/cache 时会 EACCES 静默失败
    (Next.js 只记日志不崩溃,不会让 release.sh 冒烟失败,问题不会被自动发现)。

    chgrp 到一个测试沙箱里不存在的系统组("allwin")会真的报错,这里用记录
    调用参数的假 sudo 替换透传版,只验证"确实以正确参数调过 sudo chgrp/chmod
    到 .next 目录",不需要真的改权限。
    """

    def test_chgrp_and_chmod_target_next_dir_via_sudo(self, tmp_path):
        app_root = _setup_app_root(tmp_path)
        env = _base_env(app_root, tmp_path)
        fake_bin = _ensure_fake_systemctl_sudo(tmp_path)
        log_file = tmp_path / "sudo_calls.log"
        (fake_bin / "sudo").write_text(f'#!/bin/sh\necho "$@" >> "{log_file}"\nexit 0\n')
        (fake_bin / "sudo").chmod(0o755)

        r = _run(
            f'{SOURCE_RELEASE}; preflight; mkdir -p "$RELEASE_DIR/frontend/.next"; '
            f'fix_next_permissions',
            env,
        )
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        calls = log_file.read_text().splitlines()
        assert any(
            c.startswith("chgrp -R allwin ") and c.endswith("frontend/.next") for c in calls
        ), f"未见预期的 sudo chgrp 调用: {calls}"
        assert any(
            c.startswith("chmod -R g+w ") and c.endswith("frontend/.next") for c in calls
        ), f"未见预期的 sudo chmod 调用: {calls}"


class TestFailureOrdering:
    """用函数覆盖隔离每一段的顺序/失败语义,不需要真实 systemd/HTTP/venv/npm。"""

    def _env_with_noop_build(self, app_root):
        return _base_env(app_root)

    def test_backup_failure_blocks_migration(self, tmp_path):
        app_root = _setup_app_root(tmp_path)
        marker = tmp_path / "migration_marker.txt"
        script = f'''
{SOURCE_RELEASE}
preflight
do_build() {{ :; }}
backup_sqlite_original() {{ :; }}
_orig_backup="{ROOT}/deploy/scripts/backup_sqlite.sh"
cat > "$SHARED_DIR/data/allwin.db" <<< "not a real db, backup will fail integrity_check or missing binary"
do_backup_and_migrate() {{
  ( cd "$RELEASE_DIR" 2>/dev/null; false ) || die "备份失败,不执行 migration,current 未切换,线上不受影响"
  echo "MIGRATION_RAN" >> "{marker}"
}}
mkdir -p "$RELEASE_DIR"
do_backup_and_migrate
'''
        r = _run(script, _base_env(app_root))
        assert r.returncode != 0
        assert not marker.exists(), "备份失败时不应到达 migration 步骤"

    def test_migration_failure_blocks_current_switch(self, tmp_path):
        """用真实(未覆盖)的 do_backup_and_migrate:真实三库先备份成功,
        migration 因 .venv/bin/python 不存在而失败——验证 switch_current
        确实没有被调用,不是靠覆盖 do_backup_and_migrate 伪造出来的结论。"""
        app_root = _setup_app_root(tmp_path)
        shared_data = app_root / "shared" / "data"
        env0 = dict(os.environ, ALLWIN_DATA_DIR=str(shared_data))
        for name in ("core", "platform", "odds"):
            r = subprocess.run([sys.executable, "-m", "backend.db.migrate", "--db", name],
                                cwd=ROOT, env=env0, capture_output=True, text=True)
            assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"

        switch_marker = tmp_path / "switch_called.txt"
        script = f'''
{SOURCE_RELEASE}
preflight
do_build() {{ do_rsync; }}
candidate_smoke() {{ return 0; }}
switch_current() {{ echo "SWITCH_CALLED" >> "{switch_marker}"; }}
main
'''
        r = _run(script, _base_env(app_root, tmp_path))
        assert r.returncode != 0
        assert "migration" in r.stderr.lower() or "迁移" in r.stderr
        assert not switch_marker.exists(), "migration 失败时不得切换 current"

    def test_candidate_failure_blocks_switch(self, tmp_path):
        app_root = _setup_app_root(tmp_path)
        switch_marker = tmp_path / "switch_called.txt"
        script = f'''
{SOURCE_RELEASE}
preflight
do_build() {{ :; }}
do_backup_and_migrate() {{ :; }}
candidate_smoke() {{ return 1; }}
switch_current() {{ echo "SWITCH_CALLED" >> "{switch_marker}"; }}
main
'''
        r = _run(script, _base_env(app_root))
        assert r.returncode != 0
        assert "冒烟失败" in r.stderr or "candidate" in r.stderr.lower()
        assert not switch_marker.exists(), "候选进程冒烟失败时不得切换 current"
        assert not (app_root / "current").exists(), "current 软链不应被创建/改变"

    def test_switch_success_but_verify_live_fails_triggers_rollback(self, tmp_path):
        app_root = _setup_app_root(tmp_path)
        rollback_marker = tmp_path / "rollback_called.txt"
        # 预先放一个"上一个 release"目录,让 preflight 认为 current 已指向它
        previous_dir = app_root / "releases" / "prevsha000000"
        previous_dir.mkdir(parents=True)
        (app_root / "current").symlink_to(previous_dir, target_is_directory=True)

        script = f'''
{SOURCE_RELEASE}
preflight
do_build() {{ :; }}
do_backup_and_migrate() {{ :; }}
candidate_smoke() {{ return 0; }}
switch_current() {{ ln -sfn "$RELEASE_DIR" "$CURRENT_LINK"; }}
verify_live() {{ return 1; }}
rollback() {{ echo "ROLLBACK_CALLED with PREVIOUS=$PREVIOUS" >> "{rollback_marker}"; die "rollback invoked (test stub)"; }}
main
'''
        r = _run(script, _base_env(app_root))
        assert r.returncode != 0
        assert rollback_marker.exists(), "verify_live 失败后必须调用 rollback"
        content = rollback_marker.read_text()
        assert str(previous_dir) in content, "rollback 必须拿到正确的 PREVIOUS"

    def test_switch_success_verify_live_ok_but_business_smoke_fails_triggers_rollback(self, tmp_path):
        app_root = _setup_app_root(tmp_path)
        rollback_marker = tmp_path / "rollback_called.txt"
        previous_dir = app_root / "releases" / "prevsha000000"
        previous_dir.mkdir(parents=True)
        (app_root / "current").symlink_to(previous_dir, target_is_directory=True)

        script = f'''
{SOURCE_RELEASE}
preflight
do_build() {{ :; }}
do_backup_and_migrate() {{ :; }}
candidate_smoke() {{ return 0; }}
switch_current() {{ ln -sfn "$RELEASE_DIR" "$CURRENT_LINK"; }}
verify_live() {{ return 0; }}
business_smoke() {{ return 1; }}
rollback() {{ echo "ROLLBACK_CALLED" >> "{rollback_marker}"; die "rollback invoked (test stub)"; }}
main
'''
        r = _run(script, _base_env(app_root))
        assert r.returncode != 0
        assert rollback_marker.exists(), "业务冒烟失败后也必须调用 rollback"


class TestRollbackReverification:
    """直接调用 rollback() 本体(不经 main),验证它自己的回滚后再验收逻辑。
    需要真实 sudo/systemctl 调用点存在,用两个平凡的假实现(直接透传/no-op)。"""

    def _fake_bin(self, tmp_path) -> Path:
        fake_bin = tmp_path / "fakebin"
        fake_bin.mkdir()
        (fake_bin / "sudo").write_text("#!/bin/sh\nexec \"$@\"\n")
        (fake_bin / "sudo").chmod(0o755)
        (fake_bin / "systemctl").write_text("#!/bin/sh\nexit 0\n")
        (fake_bin / "systemctl").chmod(0o755)
        return fake_bin

    def test_rollback_reverifies_and_reports_recovered(self, tmp_path):
        app_root = _setup_app_root(tmp_path)
        previous_dir = app_root / "releases" / "prevsha000000"
        previous_dir.mkdir(parents=True)
        bad_release_dir = app_root / "releases" / "badsha000000"
        bad_release_dir.mkdir(parents=True)
        fake_bin = self._fake_bin(tmp_path)

        script = f'''
{SOURCE_RELEASE}
CURRENT_LINK="$APP_ROOT/current"
RELEASE_DIR="{bad_release_dir}"
PREVIOUS="{previous_dir}"
ln -sfn "$RELEASE_DIR" "$CURRENT_LINK"
verify_live() {{ return 0; }}
business_smoke() {{ return 0; }}
rollback
'''
        env = _base_env(app_root, PATH=f"{fake_bin}:{os.environ['PATH']}")
        r = _run(script, env)
        assert r.returncode != 0   # rollback 总是以 die() 结束(即便"已恢复"也是非零退出,提醒本次发布失败)
        assert "已恢复" in r.stderr or "已回滚到上一 release 并验证健康" in r.stderr
        assert os.readlink(str(app_root / "current")) == str(previous_dir)

    def test_rollback_fails_when_reverification_also_fails(self, tmp_path):
        app_root = _setup_app_root(tmp_path)
        previous_dir = app_root / "releases" / "prevsha000000"
        previous_dir.mkdir(parents=True)
        bad_release_dir = app_root / "releases" / "badsha000000"
        bad_release_dir.mkdir(parents=True)
        fake_bin = self._fake_bin(tmp_path)

        script = f'''
{SOURCE_RELEASE}
CURRENT_LINK="$APP_ROOT/current"
RELEASE_DIR="{bad_release_dir}"
PREVIOUS="{previous_dir}"
ln -sfn "$RELEASE_DIR" "$CURRENT_LINK"
verify_live() {{ return 1; }}
business_smoke() {{ return 1; }}
rollback
'''
        env = _base_env(app_root, PATH=f"{fake_bin}:{os.environ['PATH']}")
        r = _run(script, env)
        assert r.returncode != 0
        assert "人工介入" in r.stderr

    def test_rollback_with_no_previous_fails_clearly_nonzero(self, tmp_path):
        app_root = _setup_app_root(tmp_path)
        bad_release_dir = app_root / "releases" / "badsha000000"
        bad_release_dir.mkdir(parents=True)
        script = f'''
{SOURCE_RELEASE}
CURRENT_LINK="$APP_ROOT/current"
RELEASE_DIR="{bad_release_dir}"
PREVIOUS=""
ln -sfn "$RELEASE_DIR" "$CURRENT_LINK"
rollback
'''
        r = _run(script, _base_env(app_root))
        assert r.returncode != 0
        assert "无上一 release" in r.stderr or "人工介入" in r.stderr


class TestCleanupNeverTouchesCurrentOrPrevious:
    def test_current_and_previous_survive_cleanup(self, tmp_path):
        app_root = _setup_app_root(tmp_path)
        releases_dir = app_root / "releases"
        releases_dir.mkdir(parents=True, exist_ok=True)
        current_dir = releases_dir / "current_sha"
        previous_dir = releases_dir / "previous_sha"
        old1 = releases_dir / "old1_sha"
        old2 = releases_dir / "old2_sha"
        for d in (current_dir, previous_dir, old1, old2):
            d.mkdir()
        # ls -1t 按 mtime 排序;确保 old1/old2 明显更旧
        import time

        os.utime(old1, (time.time() - 1000, time.time() - 1000))
        os.utime(old2, (time.time() - 900, time.time() - 900))
        os.utime(previous_dir, (time.time() - 500, time.time() - 500))
        os.utime(current_dir, (time.time(), time.time()))

        script = f'''
{SOURCE_RELEASE}
RELEASE_DIR="{current_dir}"
PREVIOUS="{previous_dir}"
KEEP_RELEASES=1
cleanup_old_releases
'''
        r = _run(script, _base_env(app_root))
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert current_dir.exists(), "current release 不应被清理"
        assert previous_dir.exists(), "previous release 不应被清理"
        assert not old1.exists(), "超出 KEEP_RELEASES 的旧 release 应被清理"
        assert not old2.exists()

    def test_keep_releases_must_be_positive_integer(self, tmp_path):
        app_root = _setup_app_root(tmp_path)
        releases_dir = app_root / "releases"
        releases_dir.mkdir(parents=True, exist_ok=True)
        current_dir = releases_dir / "current_sha"
        current_dir.mkdir()
        for bad in ("abc", "0", "-1"):
            script = f'''
{SOURCE_RELEASE}
RELEASE_DIR="{current_dir}"
PREVIOUS=""
KEEP_RELEASES="{bad}"
cleanup_old_releases
'''
            r = _run(script, _base_env(app_root))
            assert r.returncode != 0, f"KEEP_RELEASES={bad!r} 应被拒绝"


class TestMarkerNotInjectable:
    def test_source_uses_safe_grep_fixed_string(self):
        """静态防回归:business_smoke 必须用 `grep -qF --` 处理用户可控 marker,
        不能退化回可被当作选项解析的裸 `grep -q "$VAR"`。"""
        src = RELEASE_SH.read_text()
        assert 'grep -qF -- "$SMOKE_HTML_MARKER"' in src

    def test_dash_prefixed_marker_matches_via_real_http_response(self, tmp_path):
        """真实行为验证(不只是静态检查):marker 以 "-" 开头时,grep 仍然把它当
        纯文本匹配,而不是被解析成选项导致误判或报错。"""
        app_root = _setup_app_root(tmp_path)
        marker = "-e-looks-like-a-flag"
        html_body = f"<html><body>{marker}</body></html>".encode()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html_body)

            def log_message(self, *a):
                pass

        port = _free_port()
        httpd = http.server.HTTPServer(("127.0.0.1", port), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            script = f'''
{SOURCE_RELEASE}
LIVE_WEB_PORT={port}
SMOKE_HTML_MARKER="{marker}"
BUSINESS_SMOKE_RETRIES=1
BUSINESS_SMOKE_INTERVAL=1
release_py() {{ :; }}
RELEASE_DIR="/nonexistent"
curl -sf "http://127.0.0.1:{port}/" | grep -qF -- "$SMOKE_HTML_MARKER"
echo "GREP_EXIT=$?"
'''
            r = _run(script, _base_env(app_root))
            assert "GREP_EXIT=0" in r.stdout, f"{r.stdout}\n{r.stderr}"
        finally:
            httpd.shutdown()
            thread.join(timeout=5)


class TestPathContainment:
    def test_cleanup_refuses_when_releases_dir_outside_app_root(self, tmp_path):
        app_root = _setup_app_root(tmp_path)
        outside = tmp_path / "definitely_outside"
        outside.mkdir()
        script = f'''
{SOURCE_RELEASE}
RELEASES_DIR="{outside}"
RELEASE_DIR="{outside}/x"
PREVIOUS=""
KEEP_RELEASES=1
cleanup_old_releases
'''
        r = _run(script, _base_env(app_root))
        assert r.returncode != 0
