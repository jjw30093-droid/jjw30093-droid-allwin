"""后端测试公共 fixture:临时数据目录 + development/mock 认证的 app。"""

import itertools

import pytest
from fastapi.testclient import TestClient

from backend.auth.config import load_auth_settings
from backend.db import migrate

_ip_counter = itertools.count(1)

BASE_ENV = {
    "APP_ENV": "development",
    "WECHAT_AUTH_PROVIDER": "mock",
    "WECHAT_AUTH_ENABLED": "1",
    "PUBLIC_BASE_URL": "http://testserver",   # 与 TestClient 默认 host 一致,cookie 才会回传
    "ALLOWED_ORIGINS": "http://localhost:3000,http://127.0.0.1:3000",
    "SESSION_TTL_DAYS": "30",
}


def make_settings(**overrides):
    env = dict(BASE_ENV)
    env.update({k: str(v) for k, v in overrides.items()})
    return load_auth_settings(env)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLWIN_DATA_DIR", str(tmp_path))
    for name in ("core", "platform", "odds"):
        migrate.apply_all(name, quiet=True)
    return tmp_path


@pytest.fixture
def app(data_dir):
    from backend.api.app import create_app

    return create_app(make_settings())


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def fresh_ip():
    """每个测试独立限流键(限流器是进程级单例)。"""
    return f"10.0.0.{next(_ip_counter)}"
