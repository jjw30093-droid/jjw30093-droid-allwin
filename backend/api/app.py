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

from .cache_policy import install_cache_policy
from .error_handlers import register_error_handlers

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

    # 缓存隔离(CLAUDE.md §10.2):app 层 default-deny——请求带凭证或响应带
    # Set-Cookie 强制 private,no-store;非白名单路径同样强制 no-store;
    # 详见 cache_policy.py 顶部说明。
    install_cache_policy(app)

    # 全站统一错误契约(CLAUDE.md §10):HTTPException/422 校验失败/未捕获异常
    # 全部归一成 {code, message, details}(/readyz 503 是独立运维 allowlist,不经此处)。
    register_error_handlers(app)

    from . import routes_auth

    app.include_router(routes_auth.router)
    # 认证三态(CLAUDE.md §7.3):WECHAT_AUTH_ENABLED=0 时微信端点统一
    # 503 + {"code":"AUTH_DISABLED","message","details":null}(provider 由
    # build_provider 装配为 Disabled;body 已是全站统一形状,直接透传)。
    app.add_exception_handler(
        routes_auth.WechatDisabledException,
        lambda request, exc: exc.to_response(),
    )

    # v1 数据/产品/预测/admin/studio 路由(分文件,逐步装配)
    for module_name in (
        "routes_public",
        "routes_media",
        "routes_member",
        "routes_reco",
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

    from .schemas import HealthzDTO, ReadyzProblemsDTO

    @app.get("/healthz", response_model=HealthzDTO)
    def healthz():
        return {"ok": True}

    @app.get(
        "/readyz",
        response_model=HealthzDTO,
        responses={503: {"model": ReadyzProblemsDTO, "description": "数据库不可读或存在 pending migration"}},
    )
    def readyz():
        """三库可读 + migration 无 pending 才 ready。

        公网未认证响应只返回稳定、清理过的问题标识——不返回原始异常文本、
        SQL、绝对路径、迁移文件名或堆栈(这些是内部实现细节,即便不算"秘密"
        也不该暴露给任意匿名调用者当侦察信息)。真实异常详情记录到服务端日志。
        """
        problems = []
        for name in ("core", "platform", "odds"):
            try:
                st = migrate.status(name)
                if st["pending"]:
                    problems.append(f"{name}: pending_migrations={len(st['pending'])}")
                if st["checksum_drift"]:
                    problems.append(f"{name}: checksum_drift={len(st['checksum_drift'])}")
                conn = connect_ro(name)
                conn.execute("SELECT 1").fetchone()
                conn.close()
            except Exception:  # pragma: no cover - 故障路径
                log.exception("readyz check failed for db=%s", name)
                problems.append(f"{name}: unavailable")
        if problems:
            from fastapi.responses import JSONResponse

            return JSONResponse({"ok": False, "problems": problems}, status_code=503)
        return {"ok": True}

    return app


app = create_app()
