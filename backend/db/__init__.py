"""
backend.db — 数据库路径、连接工厂与 migration。

历史兼容:原 backend/db.py 模块升级为本包,`DB_PATH` / `get_connection`
原样保留导出,旧脚本 `from db import get_connection` 行为不变。

新代码约定(CLAUDE.md §5.3):
- 只读查询用 connect_ro(name),URI mode=ro + query_only;
- 写路径(command/admin/migration/ingest)用 connect_rw(name),显式短事务;
- 三个逻辑库:core=data/allwin.db, platform=data/platform.db, odds=data/odds.db。
"""

from .paths import PROJECT_ROOT, DB_PATH, db_path, data_dir
from .connections import connect_ro, connect_rw, get_connection

__all__ = [
    "PROJECT_ROOT",
    "DB_PATH",
    "db_path",
    "data_dir",
    "connect_ro",
    "connect_rw",
    "get_connection",
]
