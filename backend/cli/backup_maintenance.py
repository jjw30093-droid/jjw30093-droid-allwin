"""备份分级保留 + 压缩(2026-09-27,由 deploy/scripts/release.sh 与 backup_sqlite.sh 调用)。

背景:每份备份(三库)约 1.5GB,旧策略"保留最近 14 份"占满磁盘(2026-09-26 实测备份目录
21GB、磁盘 89%)。新策略:

  * 保留(按 backup_metadata.json 的 trigger 字段分级,**以元数据为准,不按时间推测**):
      - trigger=daily(systemd 定时器的每日备份):保留最近 7 天,更早的删除;
      - trigger=release(release.sh 发布前备份):保留最近 3 份,其余删除;
      - trigger=manual(人工执行 backup_sqlite.sh 时显式指定):不自动删除,只参与压缩;
      - 没有 trigger 字段的历史备份(本策略上线前产生,元数据里没有类型信息)按 daily 处理
        (7 天后自然淘汰);不可解析/不完整的目录**不动**,只报告;
      - 永远不删除"最新一份完整备份"。
  * 压缩:创建超过 24 小时的备份,三库各压成 <db>.zst(zstd),**校验通过后才删原文件**:
      zstd -t 通过 → 解压出的内容 sha256 与 metadata 记录一致 → PRAGMA integrity_check=ok。
      三库全部校验通过后才一次性删除原文件,并把 compression 信息写回 metadata
      (size/sha256 仍是未压缩文件的值,restore_verify.sh 解压后照旧比对)。
      任一步失败:保留原文件、删掉这次产生的 .zst,只告警不删数据。
  * 对应的 manifests/<时间戳> 目录随备份一起删除。

只做本地目录的清理与压缩;S3 上传发生在备份创建时(backup_sqlite.sh),不受影响。
用法:python -m backend.cli.backup_maintenance [--dry-run] [--data-dir DIR]
退出码:0 = 完成(可能带告警);2 = 压缩校验失败(原文件已保留,需要人工看);1 = 参数错误。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

TS_RE = re.compile(r"^\d{8}T\d{6}Z$")
REQUIRED_DBS = ("allwin.db", "platform.db", "odds.db")

DEFAULT_DAILY_KEEP_DAYS = 7
DEFAULT_RELEASE_KEEP = 3
DEFAULT_COMPRESS_AFTER_HOURS = 24
ZSTD_LEVEL = "3"


@dataclass
class Backup:
    name: str
    path: Path
    created: datetime
    trigger: str            # "daily" | "release" | "manual"
    trigger_legacy: bool    # True = 元数据里没有 trigger 字段,按 daily 处理
    meta: dict
    compressed: bool = False
    warnings: list[str] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_backups(root: Path) -> tuple[list[Backup], list[str]]:
    """扫描备份根目录。返回 (可管理的完整备份, 被跳过目录的说明)。
    只认名字是合法 UTC 时间戳、带 backup_metadata.json 且 complete=true 的目录。"""
    backups: list[Backup] = []
    skipped: list[str] = []
    if not root.is_dir():
        return backups, skipped
    for p in sorted(root.iterdir()):
        if not p.is_dir() or not TS_RE.match(p.name):
            continue
        meta_path = p / "backup_metadata.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            skipped.append(f"{p.name}: 元数据不可读({type(exc).__name__}),不动")
            continue
        if not meta.get("complete"):
            skipped.append(f"{p.name}: complete≠true,不动")
            continue
        try:
            created = datetime.strptime(p.name, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            skipped.append(f"{p.name}: 时间戳不可解析,不动")
            continue
        raw_trigger = meta.get("trigger")
        legacy = raw_trigger not in ("daily", "release", "manual")
        trigger = raw_trigger if not legacy else "daily"
        compressed = bool(meta.get("compression"))
        backups.append(Backup(p.name, p, created, trigger, legacy, meta, compressed))
    return backups, skipped


def plan_retention(
    backups: list[Backup],
    now: datetime,
    daily_keep_days: int = DEFAULT_DAILY_KEEP_DAYS,
    release_keep: int = DEFAULT_RELEASE_KEEP,
) -> tuple[list[tuple[Backup, str]], list[tuple[Backup, str]]]:
    """返回 (待删除[(备份, 原因)], 保留[(备份, 原因)])。永远保留最新一份完整备份。"""
    delete: list[tuple[Backup, str]] = []
    keep: list[tuple[Backup, str]] = []
    newest = max(backups, key=lambda b: b.created) if backups else None

    daily = [b for b in backups if b.trigger == "daily"]
    release = sorted((b for b in backups if b.trigger == "release"), key=lambda b: b.created, reverse=True)

    cutoff = now - timedelta(days=daily_keep_days)
    for b in daily:
        tag = "daily(历史,无 trigger 字段)" if b.trigger_legacy else "daily"
        if b.created < cutoff:
            delete.append((b, f"{tag} 早于 {daily_keep_days} 天"))
        else:
            keep.append((b, f"{tag} 在 {daily_keep_days} 天内"))
    for b in backups:
        if b.trigger == "manual":
            keep.append((b, "manual(人工备份,不自动删除)"))
    for i, b in enumerate(release):
        if i < release_keep:
            keep.append((b, f"release 最近 {release_keep} 份之一(第 {i + 1})"))
        else:
            delete.append((b, f"release 超出最近 {release_keep} 份"))

    # 保护:最新一份完整备份永不删除
    if newest is not None:
        for i, (b, _) in enumerate(delete):
            if b is newest:
                delete.pop(i)
                keep.append((b, "最新一份完整备份(永不删除)"))
                break
    return delete, keep


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _integrity_ok(db_path: Path) -> bool:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute("PRAGMA integrity_check;").fetchall()
    finally:
        conn.close()
    return len(rows) == 1 and rows[0][0] == "ok"


def _compress_one(zstd: str, src: Path, expected_sha: str, workdir: Path) -> Path:
    """压缩并校验单个库文件,成功返回 .zst 路径;失败抛 RuntimeError(并清掉产物)。"""
    dst = src.with_name(src.name + ".zst")
    tmp_plain = workdir / f".verify-{src.name}"
    try:
        r = _run([zstd, "-q", "-T0", f"-{ZSTD_LEVEL}", "-f", str(src), "-o", str(dst)])
        if r.returncode != 0:
            raise RuntimeError(f"zstd 压缩失败: {r.stderr.strip()[:200]}")
        r = _run([zstd, "-q", "-t", str(dst)])
        if r.returncode != 0:
            raise RuntimeError(f"zstd -t 校验失败: {r.stderr.strip()[:200]}")
        with open(tmp_plain, "wb") as out:
            p = subprocess.run([zstd, "-q", "-d", "-c", str(dst)], stdout=out, stderr=subprocess.PIPE)
        if p.returncode != 0:
            raise RuntimeError(f"zstd 解压失败: {p.stderr.decode(errors='replace').strip()[:200]}")
        actual = _sha256_file(tmp_plain)
        if actual != expected_sha:
            raise RuntimeError(f"解压后 sha256 与 metadata 不一致({actual[:12]}… vs {expected_sha[:12]}…)")
        if not _integrity_ok(tmp_plain):
            raise RuntimeError("解压后 PRAGMA integrity_check 未通过")
        os.chmod(dst, 0o600)
        return dst
    except Exception:
        dst.unlink(missing_ok=True)
        raise
    finally:
        tmp_plain.unlink(missing_ok=True)


def compress_backup(b: Backup, zstd: str, log=print) -> bool:
    """三库全部压缩+校验通过后才删原文件并更新 metadata。返回是否压缩成功。"""
    dbs = b.meta.get("databases", {})
    names = [n for n in REQUIRED_DBS if n in dbs]
    if len(names) != len(REQUIRED_DBS) or any(not (b.path / n).is_file() for n in names):
        log(f"  WARNING {b.name}: 三库文件不齐,跳过压缩")
        return False
    largest = max(int(dbs[n].get("size", 0)) for n in names)
    free = shutil.disk_usage(b.path).free
    if free < int(largest * 1.2):
        log(f"  WARNING {b.name}: 可用空间 {free // 1048576}MB 不足以安全压缩(需要约 {int(largest * 1.2) // 1048576}MB),跳过")
        return False

    made: list[Path] = []
    try:
        for n in names:
            made.append(_compress_one(zstd, b.path / n, dbs[n]["sha256"], b.path))
    except Exception as exc:  # noqa: BLE001 —— 任一失败:保留原文件,清掉本次 .zst
        for z in made:
            z.unlink(missing_ok=True)
        log(f"  ERROR {b.name}: 压缩校验失败,原文件已保留: {exc}")
        return False

    sizes = {}
    for n in names:
        z = b.path / f"{n}.zst"
        sizes[n] = {"zst_size": z.stat().st_size}
    # 全部校验通过 → 才删除原文件
    for n in names:
        (b.path / n).unlink()
    meta = dict(b.meta)
    meta["compression"] = {
        "algo": "zstd",
        "level": int(ZSTD_LEVEL),
        "compressed_at": _now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": sizes,
    }
    meta_path = b.path / "backup_metadata.json"
    tmp_meta = meta_path.with_suffix(".json.tmp")
    tmp_meta.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp_meta, 0o600)
    os.replace(tmp_meta, meta_path)
    b.meta = meta
    b.compressed = True
    before = sum(int(dbs[n].get("size", 0)) for n in names)
    after = sum(v["zst_size"] for v in sizes.values())
    log(f"  压缩 {b.name}: {before // 1048576}MB → {after // 1048576}MB(校验:zstd -t / sha256 / integrity_check 全部通过,原文件已删)")
    return True


def _remove(path: Path) -> bool:
    try:
        shutil.rmtree(path)
        return True
    except OSError:
        r = subprocess.run(["sudo", "-n", "rm", "-rf", str(path)], capture_output=True)
        return r.returncode == 0 and not path.exists()


def run(
    data_dir: Path,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
    daily_keep_days: int = DEFAULT_DAILY_KEEP_DAYS,
    release_keep: int = DEFAULT_RELEASE_KEEP,
    compress_after_hours: int = DEFAULT_COMPRESS_AFTER_HOURS,
    compress: bool = True,
    log=print,
) -> int:
    now = now or _now()
    root = data_dir / "backups"
    manifests = root / "manifests"
    backups, skipped = load_backups(root)
    log(f"== backup_maintenance {'(dry-run,不改任何文件)' if dry_run else ''}: {root} ==")
    log(f"完整备份 {len(backups)} 份;策略:daily 保留 {daily_keep_days} 天,release 保留最近 {release_keep} 份,"
        f"超过 {compress_after_hours} 小时压缩(zstd)")
    for s in skipped:
        log(f"  跳过 {s}")

    delete, keep = plan_retention(backups, now, daily_keep_days, release_keep)
    delete_names = {b.name for b, _ in delete}

    log("-- 分类与处置(以 backup_metadata.json 的 trigger 为准)")
    for b in sorted(backups, key=lambda x: x.created):
        size = sum(f.stat().st_size for f in b.path.iterdir() if f.is_file())
        tag = "legacy→daily" if b.trigger_legacy else b.trigger
        act = "删除" if b.name in delete_names else "保留"
        reason = next((r for x, r in (delete + keep) if x is b), "")
        log(f"  {b.name}  {tag:<13} {size // 1048576:>6}MB  {'已压缩' if b.compressed else '未压缩':<4}  {act}: {reason}")

    freed = 0
    for b, reason in delete:
        size = sum(f.stat().st_size for f in b.path.iterdir() if f.is_file())
        if dry_run:
            log(f"  [dry-run] 将删除 {b.name}({size // 1048576}MB)+ manifests/{b.name}")
            freed += size
            continue
        ok = _remove(b.path)
        if (manifests / b.name).exists():
            _remove(manifests / b.name)
        if ok:
            freed += size
            log(f"  已删除 {b.name}({size // 1048576}MB;{reason})")
        else:
            log(f"  WARNING 删除 {b.name} 失败(不影响本次结果)")

    rc = 0
    if compress:
        zstd = shutil.which("zstd")
        cutoff = now - timedelta(hours=compress_after_hours)
        todo = [b for b in backups if b.name not in delete_names and not b.compressed and b.created < cutoff]
        if not zstd:
            log("  WARNING 找不到 zstd,跳过压缩")
        elif not todo:
            log("-- 压缩:没有需要压缩的备份")
        else:
            log(f"-- 压缩 {len(todo)} 份超过 {compress_after_hours} 小时的备份")
            for b in sorted(todo, key=lambda x: x.created):
                if dry_run:
                    size = sum(int(v.get("size", 0)) for v in b.meta.get("databases", {}).values())
                    log(f"  [dry-run] 将压缩 {b.name}(当前 {size // 1048576}MB,zstd 后约 1/3~1/4)")
                    continue
                if not compress_backup(b, zstd, log):
                    rc = 2
    log(f"== 完成:{'预计' if dry_run else '已'}释放(删除部分){freed // 1048576}MB ==")
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", help="数据目录(默认 $ALLWIN_DATA_DIR 或 <repo>/data)")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划,不删不压")
    ap.add_argument("--now", help="覆盖当前时间(测试用,UTC ISO8601)")
    ap.add_argument("--daily-keep-days", type=int, default=int(os.environ.get("BACKUP_DAILY_KEEP_DAYS", DEFAULT_DAILY_KEEP_DAYS)))
    ap.add_argument("--release-keep", type=int, default=int(os.environ.get("BACKUP_RELEASE_KEEP", DEFAULT_RELEASE_KEEP)))
    ap.add_argument("--compress-after-hours", type=int, default=DEFAULT_COMPRESS_AFTER_HOURS)
    ap.add_argument("--no-compress", action="store_true")
    args = ap.parse_args(argv)
    if args.daily_keep_days < 1 or args.release_keep < 1:
        print("ERROR: --daily-keep-days / --release-keep 必须 >= 1", file=sys.stderr)
        return 1
    data_dir = Path(args.data_dir or os.environ.get("ALLWIN_DATA_DIR") or Path(__file__).resolve().parents[2] / "data")
    now = datetime.strptime(args.now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if args.now else None
    return run(
        data_dir,
        dry_run=args.dry_run,
        now=now,
        daily_keep_days=args.daily_keep_days,
        release_keep=args.release_keep,
        compress_after_hours=args.compress_after_hours,
        compress=not args.no_compress,
    )


if __name__ == "__main__":
    raise SystemExit(main())
