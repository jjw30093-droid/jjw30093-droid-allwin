"""
db_verify.py — 4 大联赛验证专用 DB 连接,指向独立的 data/verify_leagues.db。

🔴 与 backend/db.py(指向生产 data/allwin.db)完全隔离——这批验证数据绝不
写进 allwin.db,英超生产库必须保持干净不动。
"""

import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VERIFY_DB_PATH = PROJECT_ROOT / "data" / "verify_leagues.db"


def get_verify_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(VERIFY_DB_PATH, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn
