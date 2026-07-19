"""backend.worker — 轻量任务 Worker(无 Celery)。

入口见 runner.py:`python -m backend.worker.runner --list | --job <name> | --chain`。
(不在包 __init__ 里 re-export runner:避免 `python -m backend.worker.runner`
的 runpy 双重导入警告;代码里直接 `from backend.worker import runner`。)
"""
