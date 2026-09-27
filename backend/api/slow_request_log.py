"""慢请求日志(2026-09-27,配合 backend/cli/fdwatch.py 排查连接堆积)。

纯 ASGI 中间件(与 cache_policy.CachePolicyMiddleware 同一模式:只在 `http.response.start`
上取状态码,不缓冲 body,不改写响应)。耗时超过阈值(默认 2 秒)的请求单独记一行到
`<log_dir>/slow_YYYYMMDD.log`(按天,保留 14 天),内容只有:时间、方法、路径模板、状态码、
耗时——路径模板取 Starlette 路由匹配后写回 `scope["route"]` 的 `.path`(如
`/api/v1/matches/{match_id}`),不是原始 path;**从不读取 `scope["query_string"]`**,
query string 里可能带的任何敏感值(token、搜索词等)不会进日志。记录失败(如目录不可写)
只静默跳过,不影响业务响应。
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_LOG_DIR = "/opt/allwin/shared/logs/slow_requests"
DEFAULT_THRESHOLD_SECONDS = 2.0
DEFAULT_RETENTION_DAYS = 14
_DAY_FILE_RE = re.compile(r"^slow_(\d{8})\.log$")


def format_slow_line(now: datetime, method: str, path_template: str, status: int | None, duration_s: float) -> str:
    status_str = str(status) if status is not None else "-"
    return f"{now.strftime('%Y-%m-%dT%H:%M:%SZ')} {method} {path_template} {status_str} {duration_s:.3f}s\n"


def prune_old_daily_logs(log_dir: Path, now: datetime, retention_days: int = DEFAULT_RETENTION_DAYS) -> list[str]:
    """删除 slow_YYYYMMDD.log 中早于 retention_days 的文件,返回被删文件名列表。"""
    removed = []
    cutoff = (now - timedelta(days=retention_days)).strftime("%Y%m%d")
    for p in sorted(log_dir.glob("slow_*.log")):
        m = _DAY_FILE_RE.fullmatch(p.name)
        if m and m.group(1) < cutoff:
            p.unlink(missing_ok=True)
            removed.append(p.name)
    return removed


def recent_slow_lines(log_dir: Path, now: datetime, minutes: int = 5) -> list[str]:
    """最近 N 分钟的慢请求行(供 fdwatch 重启快照附带)。跨天边界时同时看今天和昨天的文件。
    单行时间戳解析失败的行原样忽略(不让一行坏数据挡住其余)。目录/文件不存在时返回空列表,
    不抛异常——调用方(看门狗)不应该因为这个辅助功能失败而中断。"""
    cutoff = now - timedelta(minutes=minutes)
    out: list[str] = []
    for day in (now.date(), (now - timedelta(days=1)).date()):
        p = log_dir / f"slow_{day.strftime('%Y%m%d')}.log"
        if not p.is_file():
            continue
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                ts = line[:20]
                try:
                    t = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if t >= cutoff:
                    out.append(line)
        except OSError:
            continue
    return out


class SlowRequestLoggingMiddleware:
    """纯 ASGI:测总耗时(含读请求体、业务处理、写响应体全程),超过阈值才落盘一行。"""

    def __init__(
        self,
        app,
        *,
        threshold_seconds: float = DEFAULT_THRESHOLD_SECONDS,
        log_dir: str | Path = DEFAULT_LOG_DIR,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ):
        self.app = app
        self.threshold_seconds = threshold_seconds
        self.log_dir = Path(log_dir)
        self.retention_days = retention_days

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        status_holder: dict[str, int | None] = {"code": None}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["code"] = message["status"]
            await send(message)

        start = time.monotonic()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.monotonic() - start
            if duration >= self.threshold_seconds:
                self._log_slow(scope, status_holder["code"], duration)

    # 已知边界(Starlette 结构性限制,不是本中间件的 bug):`app.add_exception_handler(Exception, ...)`
    # 注册的兜底处理器由 Starlette 接到 ServerErrorMiddleware(build_middleware_stack 对
    # key in (500, Exception) 特判),而 ServerErrorMiddleware 是**固定的最外层**,在任何
    # 经 add_middleware 加入的用户中间件之外——本中间件因此天然看不到它生成的响应,这类请求
    # 记下来的 status 会是 "-"(未知),而不是真实的 500。HTTPException/RequestValidationError
    # 等经 ExceptionMiddleware(在本中间件*内侧*)处理的异常不受影响,状态码正常可见。
    def _log_slow(self, scope, status: int | None, duration: float) -> None:
        method = scope.get("method") or "?"
        route = scope.get("route")
        # 路径模板优先(不带具体 ID);路由没匹配到(如 404)时退回原始 path——ASGI 的
        # scope["path"] 本身从不含 query string(那是独立的 scope["query_string"]),
        # 所以即便退回原始 path,也不会泄漏查询参数。
        path_template = getattr(route, "path", None) or scope.get("path") or "?"
        now = datetime.now(timezone.utc)
        line = format_slow_line(now, method, path_template, status, duration)
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            day_file = self.log_dir / f"slow_{now.strftime('%Y%m%d')}.log"
            with open(day_file, "a", encoding="utf-8") as f:
                f.write(line)
            prune_old_daily_logs(self.log_dir, now, self.retention_days)
        except OSError:
            pass  # 记录失败不影响业务响应,不抛异常、不重试


def install_slow_request_logging(app, *, threshold_seconds: float | None = None) -> None:
    threshold = threshold_seconds
    if threshold is None:
        try:
            threshold = float(os.environ.get("SLOW_REQUEST_THRESHOLD_SECONDS", DEFAULT_THRESHOLD_SECONDS))
        except ValueError:
            threshold = DEFAULT_THRESHOLD_SECONDS
    log_dir = os.environ.get("SLOW_REQUEST_LOG_DIR", DEFAULT_LOG_DIR)
    app.add_middleware(SlowRequestLoggingMiddleware, threshold_seconds=threshold, log_dir=log_dir)
