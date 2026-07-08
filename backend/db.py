"""
db.py — 集中管理本地 SQLite 数据库路径与连接。
切换到服务器部署时,只需改这里的 DB_PATH。
"""

import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "allwin.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL：写入时不阻塞并发读（长跑批任务期间人工用 sqlite3 CLI 查库核对进度
    # 是常态场景，默认 rollback-journal + 5s busy timeout 容易撞上
    # "database is locked"）。WAL 是数据库文件级持久设置，设一次全局生效。
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn
