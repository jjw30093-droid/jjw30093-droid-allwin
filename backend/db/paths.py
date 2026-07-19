"""数据库文件路径解析。ALLWIN_DATA_DIR 环境变量可整体重定向(测试/部署用)。"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

DB_FILES = {
    "core": "allwin.db",
    "platform": "platform.db",
    "odds": "odds.db",
}


def data_dir() -> Path:
    override = os.environ.get("ALLWIN_DATA_DIR")
    if override:
        return Path(override)
    return PROJECT_ROOT / "data"


def db_path(name: str) -> Path:
    if name not in DB_FILES:
        raise KeyError(f"unknown database name: {name!r} (expected one of {sorted(DB_FILES)})")
    return data_dir() / DB_FILES[name]


# 历史兼容:旧代码 `from db import DB_PATH` 直接拿 allwin.db 路径。
DB_PATH = data_dir() / DB_FILES["core"]
