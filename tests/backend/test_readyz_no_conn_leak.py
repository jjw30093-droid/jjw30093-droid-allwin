"""/readyz 异常路径必须关闭连接(2026-09-27)+ allwin-api unit 的并发/keep-alive/fd 设置。

背景:API 曾多次文件描述符耗尽(Errno 24)。/readyz 每 5 分钟被健康检查调用,原实现
`conn.close()` 只在成功路径,`conn.execute` 抛异常时连接泄漏(每次约 3 个 fd)。
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api import app as app_module

ROOT = Path(__file__).resolve().parents[2]
UNIT = ROOT / "deploy" / "systemd" / "allwin-api.service"
DROPIN = ROOT / "deploy" / "systemd" / "allwin-api.service.d" / "limits.conf"


class _SpyConn:
    def __init__(self, fail: bool):
        self.fail = fail
        self.closed = False

    def execute(self, *_a, **_k):
        if self.fail:
            raise RuntimeError("database is locked")
        return self

    def fetchone(self):
        return (1,)

    def close(self):
        self.closed = True


@pytest.fixture
def client():
    return TestClient(app_module.create_app())


def _patch(monkeypatch, conns, status=None):
    monkeypatch.setattr(app_module.migrate, "status",
                        lambda name: status or {"pending": [], "checksum_drift": []})

    def fake_connect(name):
        c = conns[name]
        return c

    monkeypatch.setattr(app_module, "connect_ro", fake_connect)


def test_connection_closed_when_execute_raises(client, monkeypatch):
    conns = {"core": _SpyConn(False), "platform": _SpyConn(True), "odds": _SpyConn(False)}
    _patch(monkeypatch, conns)
    r = client.get("/readyz")
    assert r.status_code == 503
    assert r.json()["problems"] == ["platform: unavailable"]
    # 关键:抛异常的那个连接也被关闭了;其余连接同样关闭
    assert all(c.closed for c in conns.values()), {k: v.closed for k, v in conns.items()}


def test_all_connections_closed_on_success(client, monkeypatch):
    conns = {n: _SpyConn(False) for n in ("core", "platform", "odds")}
    _patch(monkeypatch, conns)
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert all(c.closed for c in conns.values())


def test_no_connection_opened_when_migrate_status_raises(client, monkeypatch):
    opened = []
    monkeypatch.setattr(app_module.migrate, "status",
                        lambda name: (_ for _ in ()).throw(RuntimeError("migration ledger contains a version absent from manifest")))
    monkeypatch.setattr(app_module, "connect_ro", lambda name: opened.append(name))
    r = client.get("/readyz")
    assert r.status_code == 503
    assert len(r.json()["problems"]) == 3 and opened == []  # 没打开连接,也就没有需要关闭的


def test_pending_migration_still_reported_and_connection_closed(client, monkeypatch):
    conns = {n: _SpyConn(False) for n in ("core", "platform", "odds")}
    _patch(monkeypatch, conns, status={"pending": ["0099_x.sql"], "checksum_drift": []})
    r = client.get("/readyz")
    assert r.status_code == 503
    assert "core: pending_migrations=1" in r.json()["problems"]
    assert all(c.closed for c in conns.values())


class TestApiUnit:
    def test_uvicorn_flags_present(self):
        text = UNIT.read_text()
        m = re.search(r"^ExecStart=.*uvicorn.*$", text, re.M)
        assert m, "找不到 uvicorn ExecStart"
        line = m.group(0)
        assert "--timeout-keep-alive 5" in line
        assert "--limit-concurrency 200" in line
        assert "--host 127.0.0.1" in line and "--port 8000" in line  # 仍只监听本机

    def test_fd_limit_raised_in_unit_and_dropin(self):
        assert re.search(r"^LimitNOFILE=65536$", UNIT.read_text(), re.M)
        assert re.search(r"^LimitNOFILE=65536$", DROPIN.read_text(), re.M)
