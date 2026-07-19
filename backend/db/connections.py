"""SQLite 连接工厂:读写物理分离(CLAUDE.md §5.3)。"""

import sqlite3
from pathlib import Path

from .paths import DB_FILES, db_path


def _resolve(name_or_path) -> Path:
    if isinstance(name_or_path, Path):
        return name_or_path
    if name_or_path in DB_FILES:
        return db_path(name_or_path)
    return Path(name_or_path)


def connect_rw(name_or_path) -> sqlite3.Connection:
    """写连接:WAL + foreign_keys + busy_timeout,autocommit 模式,调用方显式 BEGIN/COMMIT 短事务。

    check_same_thread=False:FastAPI 同步依赖跑在 anyio 线程池,同一请求的
    生成器 setup/teardown 可能落在不同线程;连接仍是请求内串行使用,不跨线程并发。
    """
    path = _resolve(name_or_path)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def connect_ro(name_or_path) -> sqlite3.Connection:
    """只读连接:URI mode=ro + query_only,物理上无法写。

    check_same_thread=False 理由同 connect_rw(请求内串行,teardown 可能换线程)。
    """
    path = _resolve(name_or_path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


from contextlib import contextmanager


@contextmanager
def tx(conn: sqlite3.Connection):
    """显式短事务(配合 connect_rw 的 autocommit 模式)。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def get_connection() -> sqlite3.Connection:
    """历史兼容:原 backend/db.py 的 allwin.db 读写连接,行为保持不变。"""
    from .paths import DB_PATH

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL:写入时不阻塞并发读(长跑批期间用 sqlite3 CLI 查库是常态场景)。
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn
