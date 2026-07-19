"""FastAPI 应用装配(生产入口:uvicorn backend.api.app:app --host 127.0.0.1 --port 8000)。

- /api/v1/*:新版本化 API(auth / 数据 / admin / studio)。
- /api/league/*:旧兼容层(deprecated,前端迁移完成后移除,不再扩展)。
- /healthz /readyz:部署探针。
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.auth.config import AuthSettings, load_auth_settings
from backend.auth.providers import build_provider
from backend.db import migrate
from backend.db.connections import connect_ro

log = logging.getLogger("allwin.api")


def create_app(settings: AuthSettings | None = None) -> FastAPI:
    settings = settings or load_auth_settings()   # production 配置缺失在这里 fail-fast
    provider = build_provider(settings)

    app = FastAPI(
        title="allwin API",
        version="1.0.0",
        docs_url="/api/v1/docs" if not settings.is_production else None,
        openapi_url="/api/v1/openapi.json",
    )
    app.state.auth_settings = settings
    app.state.wechat_provider = provider

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )

    from . import routes_auth

    app.include_router(routes_auth.router)

    # v1 数据/产品/预测/admin/studio 路由(分文件,逐步装配)
    for module_name in (
        "routes_public",
        "routes_member",
        "routes_admin",
        "routes_admin_odds",
        "routes_studio",
        "routes_analytics",
    ):
        try:
            module = __import__(f"backend.api.{module_name}", fromlist=["router"])
            app.include_router(module.router)
        except ImportError:
            log.info("router %s 尚未创建,跳过", module_name)

    # 旧兼容层:只挑 /api/league/* 路由并标 deprecated(不再扩展)
    from fastapi import APIRouter

    from backend import api_server as legacy

    legacy_router = APIRouter()
    for route in legacy.app.router.routes:
        if getattr(route, "path", "").startswith("/api/league"):
            route.deprecated = True
            route.tags = ["legacy-deprecated"]
            legacy_router.routes.append(route)
    app.include_router(legacy_router)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/readyz")
    def readyz():
        """三库可读 + migration 无 pending 才 ready。"""
        problems = []
        for name in ("core", "platform", "odds"):
            try:
                st = migrate.status(name)
                if st["pending"]:
                    problems.append(f"{name}: pending migrations {st['pending']}")
                conn = connect_ro(name)
                conn.execute("SELECT 1").fetchone()
                conn.close()
            except Exception as exc:  # pragma: no cover - 故障路径
                problems.append(f"{name}: {exc}")
        if problems:
            from fastapi.responses import JSONResponse

            return JSONResponse({"ok": False, "problems": problems}, status_code=503)
        return {"ok": True}

    return app


app = create_app()
