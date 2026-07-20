"""
轻量 SQL migration runner(不引入 Alembic/ORM/Prisma)。

用法:
  python -m backend.db.migrate --all                 # 三库全部迁移
  python -m backend.db.migrate --db platform         # 单库
  python -m backend.db.migrate --status              # 查看各库版本
  python -m backend.db.migrate --all --data-dir /tmp/x   # 重定向数据目录(测试)

规则:
- core/platform/odds 各有独立 migrations 目录与各自库内的 schema_migrations 表;
- 迁移文件命名 NNNN_name.sql,按版本号升序应用;
- 每个迁移在单个事务内执行,失败整体回滚,不留半成品;
- 已应用迁移的内容被改动(checksum 漂移)时报错拒绝继续;
- 重复运行幂等。
"""

import argparse
import hashlib
import re
import sqlite3
import sys
from pathlib import Path

from .paths import PROJECT_ROOT, db_path
from .util import utc_now_iso

MIGRATIONS_ROOT = PROJECT_ROOT / "backend" / "migrations"
DATABASES = ("core", "platform", "odds")

_FILENAME_RE = re.compile(r"^(\d{4})_([A-Za-z0-9_\-]+)\.sql$")


def split_statements(sql: str) -> list[str]:
    """按完整语句切分 SQL 文件,正确处理 trigger 的 BEGIN...END 块。"""
    statements: list[str] = []
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            stmt = buf.strip()
            if stmt:
                statements.append(stmt)
            buf = ""
    tail = _strip_sql_comments(buf).strip()
    if tail:
        raise ValueError(f"migration 末尾存在未以分号结束的语句: {tail[:80]!r}")
    return statements


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", "", sql)
    return sql


def migration_files(migrations_dir: Path) -> list[tuple[int, str, Path]]:
    if not migrations_dir.is_dir():
        return []
    out = []
    for p in sorted(migrations_dir.glob("*.sql")):
        m = _FILENAME_RE.match(p.name)
        if not m:
            raise ValueError(f"非法迁移文件名(应为 NNNN_name.sql): {p.name}")
        out.append((int(m.group(1)), p.name, p))
    versions = [v for v, _, _ in out]
    if len(set(versions)) != len(versions):
        raise ValueError(f"{migrations_dir} 存在重复版本号")
    return out


def _open(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _ensure_meta(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               version    INTEGER PRIMARY KEY,
               name       TEXT NOT NULL,
               checksum   TEXT NOT NULL,
               applied_at TEXT NOT NULL
           )"""
    )


def apply_all(
    db_name: str,
    db_file: Path | str | None = None,
    migrations_dir: Path | str | None = None,
    quiet: bool = False,
) -> int:
    """应用 db_name 的全部未应用迁移,返回新应用的数量。"""
    path = Path(db_file) if db_file else db_path(db_name)
    mdir = Path(migrations_dir) if migrations_dir else MIGRATIONS_ROOT / db_name
    conn = _open(path)
    try:
        _ensure_meta(conn)
        applied = {
            row[0]: (row[1], row[2])
            for row in conn.execute("SELECT version, name, checksum FROM schema_migrations")
        }
        count = 0
        for version, name, p in migration_files(mdir):
            sql = p.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            if version in applied:
                if applied[version][1] != checksum:
                    raise RuntimeError(
                        f"[{db_name}] 已应用迁移 {name} 的内容发生改动(checksum 漂移)。"
                        "禁止篡改历史迁移;请新增迁移文件。"
                    )
                continue
            statements = split_statements(sql)
            conn.execute("BEGIN IMMEDIATE")
            try:
                for stmt in statements:
                    conn.execute(stmt)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, checksum, applied_at)"
                    " VALUES (?, ?, ?, ?)",
                    (version, name, checksum, utc_now_iso()),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            count += 1
            if not quiet:
                print(f"[{db_name}] applied {name}")
        if not quiet:
            total = len(applied) + count
            print(f"[{db_name}] up to date: {total} applied (+{count} new) @ {path}")
        return count
    finally:
        conn.close()


def status(db_name: str, db_file: Path | str | None = None) -> dict:
    """三库状态:applied 数、pending 迁移名、checksum_drift(已应用但内容与当前
    迁移文件不一致的迁移名——正常运行下永远为空;`--restore-verify`/`ops_check`
    用它在恢复出的库副本上校验,不依赖真的去跑一次 apply_all 才能发现漂移。"""
    path = Path(db_file) if db_file else db_path(db_name)
    mdir = MIGRATIONS_ROOT / db_name
    available = migration_files(mdir)
    checksums_by_version = {v: hashlib.sha256(p.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
                             for v, _, p in available}
    applied_names: dict[int, str] = {}
    applied_checksums: dict[int, str] = {}
    if path.exists():
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            has_meta = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            if has_meta:
                for v, name, checksum in conn.execute(
                    "SELECT version, name, checksum FROM schema_migrations"
                ):
                    applied_names[v] = name
                    applied_checksums[v] = checksum
        finally:
            conn.close()
    pending = [name for v, name, _ in available if v not in applied_names]
    checksum_drift = [
        applied_names[v] for v, current in checksums_by_version.items()
        if v in applied_checksums and applied_checksums[v] != current
    ]
    return {
        "db": db_name, "path": str(path), "applied": len(applied_names),
        "pending": pending, "checksum_drift": checksum_drift,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="allwin SQL migration runner")
    ap.add_argument("--all", action="store_true", help="迁移全部三库")
    ap.add_argument("--db", choices=DATABASES, help="只迁移指定库")
    ap.add_argument("--db-file", help="覆盖数据库文件路径(仅配合 --db)")
    ap.add_argument("--data-dir", help="重定向数据目录(测试用,等价于 ALLWIN_DATA_DIR)")
    ap.add_argument("--status", action="store_true", help="只查看状态,不迁移")
    args = ap.parse_args(argv)

    if args.data_dir:
        import os

        os.environ["ALLWIN_DATA_DIR"] = args.data_dir

    targets = list(DATABASES) if (args.all or (not args.db and args.status)) else ([args.db] if args.db else [])
    if not targets:
        ap.error("需要 --all 或 --db <name>")

    if args.status:
        for name in targets:
            st = status(name, db_file=args.db_file if args.db else None)
            print(f"[{st['db']}] {st['applied']} applied, pending={st['pending'] or 'none'} @ {st['path']}")
        return 0

    for name in targets:
        apply_all(name, db_file=args.db_file if args.db else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
