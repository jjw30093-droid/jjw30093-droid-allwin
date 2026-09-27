"""backend/api/slow_request_log.py:慢请求日志中间件(2026-09-27)。

覆盖:纯函数(格式化、按天保留、最近 N 分钟提取)+ 真实 ASGI 请求(用 fastapi.TestClient,
构造带路径参数的路由,断言日志里出现的是路由模板而不是具体 ID、且从不包含 query string)。
"""

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.error_handlers import register_error_handlers
from backend.api.slow_request_log import (
    DEFAULT_RETENTION_DAYS,
    format_slow_line,
    install_slow_request_logging,
    prune_old_daily_logs,
    recent_slow_lines,
)

UTC = timezone.utc


def test_format_slow_line_shape():
    now = datetime(2026, 9, 27, 10, 30, 5, tzinfo=UTC)
    line = format_slow_line(now, "GET", "/api/v1/matches/{match_id}", 200, 2.345)
    assert line == "2026-09-27T10:30:05Z GET /api/v1/matches/{match_id} 200 2.345s\n"


def test_format_slow_line_missing_status():
    line = format_slow_line(datetime(2026, 9, 27, tzinfo=UTC), "GET", "/x", None, 3.0)
    assert " - 3.000s\n" in line


class TestPruneOldDailyLogs:
    def test_removes_only_files_older_than_retention(self, tmp_path):
        now = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
        for d in ("20260912", "20260913", "20260914", "20260927"):
            (tmp_path / f"slow_{d}.log").write_text("x")
        (tmp_path / "not_a_day_file.txt").write_text("keep")
        removed = prune_old_daily_logs(tmp_path, now, retention_days=14)
        assert removed == ["slow_20260912.log"]
        assert (tmp_path / "slow_20260913.log").exists()
        assert (tmp_path / "not_a_day_file.txt").exists()

    def test_default_retention_is_14_days(self):
        assert DEFAULT_RETENTION_DAYS == 14


class TestRecentSlowLines:
    def test_filters_by_minutes_and_spans_midnight(self, tmp_path):
        now = datetime(2026, 9, 27, 0, 2, 0, tzinfo=UTC)  # 刚过午夜
        today = tmp_path / "slow_20260927.log"
        yesterday = tmp_path / "slow_20260926.log"
        today.write_text(
            "2026-09-27T00:01:30Z GET /api/v1/a 200 2.100s\n"
            "2026-09-27T00:00:30Z GET /api/v1/b 200 2.200s\n"  # 2 分钟前,在 5 分钟窗口内
        )
        yesterday.write_text(
            "2026-09-26T23:58:00Z GET /api/v1/c 200 2.300s\n"  # 4 分钟前,跨天但仍在窗口内
            "2026-09-26T23:50:00Z GET /api/v1/d 200 2.400s\n"  # 12 分钟前,窗口外
        )
        lines = recent_slow_lines(tmp_path, now, minutes=5)
        joined = "".join(lines)
        assert "/api/v1/a" in joined and "/api/v1/b" in joined and "/api/v1/c" in joined
        assert "/api/v1/d" not in joined

    def test_missing_dir_returns_empty_not_raise(self, tmp_path):
        assert recent_slow_lines(tmp_path / "nope", datetime.now(UTC)) == []

    def test_malformed_line_ignored(self, tmp_path):
        (tmp_path / f"slow_{datetime.now(UTC):%Y%m%d}.log").write_text("garbage line without timestamp\n")
        assert recent_slow_lines(tmp_path, datetime.now(UTC)) == []


def _app(tmp_path, threshold=0.0):
    app = FastAPI()

    @app.get("/items/{item_id}")
    def get_item(item_id: str):
        return {"item_id": item_id}

    @app.get("/slow")
    async def slow():
        time.sleep(0.03)
        return {"ok": True}

    @app.get("/boom")
    def boom():
        raise RuntimeError("kaboom")

    register_error_handlers(app)  # 与生产 create_app() 一致:未捕获异常在这里转成真实 500 JSON,
    # 中间件因此能看到 http.response.start 里的真实状态码(而不是 Starlette 默认的
    # ServerErrorMiddleware——那一层在我们的中间件*外面*,我们看不到它生成的响应)。
    install_slow_request_logging(app, threshold_seconds=threshold)
    # 覆盖中间件实例的 log_dir(install_slow_request_logging 只读环境变量默认路径,
    # 测试需要重定向到 tmp_path,而不是真的写 /opt/allwin/...)
    app.user_middleware[0].kwargs["log_dir"] = tmp_path
    app.middleware_stack = None  # 强制 Starlette 下次请求时用新 kwargs 重建栈
    return app


def _log_file(tmp_path: Path) -> Path:
    return tmp_path / f"slow_{datetime.now(UTC):%Y%m%d}.log"


class TestMiddlewareIntegration:
    def test_logs_path_template_not_raw_id(self, tmp_path):
        app = _app(tmp_path, threshold=0.0)
        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/items/12345")
        assert r.status_code == 200
        text = _log_file(tmp_path).read_text()
        assert "/items/{item_id}" in text
        assert "/items/12345" not in text

    def test_never_logs_query_string(self, tmp_path):
        app = _app(tmp_path, threshold=0.0)
        client = TestClient(app, raise_server_exceptions=False)
        client.get("/items/1?token=super-secret&q=private+search")
        text = _log_file(tmp_path).read_text()
        assert "super-secret" not in text
        assert "token" not in text
        assert "private" not in text

    def test_below_threshold_not_logged(self, tmp_path):
        app = _app(tmp_path, threshold=999.0)
        client = TestClient(app, raise_server_exceptions=False)
        client.get("/items/1")
        assert not _log_file(tmp_path).exists()

    def test_status_code_recorded(self, tmp_path):
        app = FastAPI()

        @app.get("/missing-route-target")
        def _x():
            return {}

        install_slow_request_logging(app, threshold_seconds=0.0)
        app.user_middleware[0].kwargs["log_dir"] = tmp_path
        app.middleware_stack = None
        client = TestClient(app, raise_server_exceptions=False)
        client.get("/does-not-exist")
        text = _log_file(tmp_path).read_text()
        assert " 404 " in text
        # 未匹配到路由时没有 route 模板,退回原始 path——不是敏感信息(不含 query string)
        assert "/does-not-exist" in text

    def test_measures_real_elapsed_time(self, tmp_path):
        app = _app(tmp_path, threshold=0.0)
        client = TestClient(app, raise_server_exceptions=False)
        client.get("/slow")
        line = _log_file(tmp_path).read_text().strip().splitlines()[-1]
        duration = float(line.rsplit(" ", 1)[-1].rstrip("s"))
        assert duration >= 0.02  # 真实 sleep 了 0.03s,不是恒为 0

    def test_exception_path_still_logged_even_without_real_status(self, tmp_path):
        # Starlette 结构性限制(见 slow_request_log.py 对应注释):`Exception` 兜底处理器挂在
        # ServerErrorMiddleware 上,那是固定的最外层,在任何 add_middleware 加入的中间件之外——
        # 本中间件天然看不到它生成的响应/状态码。仍然要断言:请求本身完成得到 500、耗时路径
        # 没有被吞掉(方法/路径模板/耗时照常记录),只是 status 字段诚实地是 "-"(未知),不是
        # 编造一个看似正确实则永远拿不到的 500。
        app = _app(tmp_path, threshold=0.0)
        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/boom")
        assert r.status_code == 500
        text = _log_file(tmp_path).read_text()
        assert "GET /boom - " in text  # 方法 + 路径模板都记到了,status 字段诚实是 "-"

    def test_write_failure_does_not_break_request(self, tmp_path, monkeypatch):
        # log_dir 指向一个已存在的普通文件路径,mkdir(parents=True) 必然失败(NotADirectoryError,
        # 是 OSError 子类)——中间件必须吞掉这个异常,不影响业务响应。
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        bad_dir = blocker / "sub"
        app = _app(tmp_path, threshold=0.0)
        app.user_middleware[0].kwargs["log_dir"] = bad_dir
        app.middleware_stack = None
        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/items/1")
        assert r.status_code == 200

    def test_non_http_scope_passthrough(self):
        """lifespan/websocket 之类的非 http scope 直接透传,不解析路由/写文件。"""
        from backend.api.slow_request_log import SlowRequestLoggingMiddleware

        calls = []

        async def inner(scope, receive, send):
            calls.append(scope["type"])

        mw = SlowRequestLoggingMiddleware(inner, threshold_seconds=0.0, log_dir="/nonexistent")
        import asyncio

        asyncio.run(mw({"type": "lifespan"}, None, None))
        assert calls == ["lifespan"]
