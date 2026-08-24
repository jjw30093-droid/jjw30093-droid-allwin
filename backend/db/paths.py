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
#
# 2026-08-24:改成 PEP 562 模块级 __getattr__ 现算,不再是一次性冻结的模块
# 常量——原来的写法 `DB_PATH = data_dir() / DB_FILES["core"]` 只在本模块
# 本进程第一次被 import 的那一刻执行一次,之后 ALLWIN_DATA_DIR 再怎么变
# (测试用 monkeypatch.setenv 逐用例切换临时目录是标准模式)`DB_PATH` 都不会
# 跟着变。真实生产环境里 ALLWIN_DATA_DIR 进程启动后不会再变,这条改动对
# 生产行为是恒等的;但同一个 pytest 进程内跑多个用例、每个用例切一次
# ALLWIN_DATA_DIR 时,旧写法会让所有经由 `from .paths import DB_PATH`
# (无论是模块顶层还是像 connections.get_connection() 那样的函数内按次
# import)拿到值的调用方,全部悄悄读到"进程里第一个测试用例"的临时目录——
# 具体表现为看似无关的测试互相污染或读写不同物理文件(排查
# backend/scheduler.py 的实体解析测试时踩到过:间歇性 database is locked,
# 或"候选数量对但具体是哪条记录不对"这类诡异现象)。__getattr__ 让每次
# 属性访问都重新算,函数内按次 import(如 get_connection())因此天然拿到
# 当次真实值;模块顶层 `from .paths import DB_PATH` 仍是一次性求值(Python
# import 语义如此),但那些调用方(api_server.py 启动时/verify 工具脚本)
# 本来就只需要进程启动那一刻的值,行为不变。
def __getattr__(name: str):
    if name == "DB_PATH":
        return data_dir() / DB_FILES["core"]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
