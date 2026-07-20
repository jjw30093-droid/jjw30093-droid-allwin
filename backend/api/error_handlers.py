"""集中式错误归一(CLAUDE.md §10 API 契约单一真源)。

全站(除 `/readyz` 的 503——独立运维 allowlist,`ok/problems` 结构,不经此处)所有
非 2xx JSON 响应,顶层统一只有三个字段:

    {"code": "...", "message": "...", "details": null 或 object}

不在各 endpoint 里重复手写转换逻辑;本模块的处理器一次性拦截:

- Starlette/FastAPI `HTTPException`(含未知路由 404、各 endpoint 主动 raise 的
  字符串/dict detail);
- FastAPI `RequestValidationError`(路径/查询/请求体参数校验失败,原生 422);
- `routes_auth.WechatDisabledException`(认证三态 AUTH_DISABLED 503);
- 未捕获的其他 `Exception`(500,服务端记录堆栈,响应体绝不泄露堆栈/SQL/路径)。
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("allwin.api.errors")

# HTTPException.detail 是裸字符串、或 dict 缺 code/message 时的稳定回退。
_STATUS_CODE_FALLBACK: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    410: "GONE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    502: "BAD_GATEWAY",
    503: "SERVICE_UNAVAILABLE",
}

_STATUS_MESSAGE_FALLBACK: dict[int, str] = {
    400: "请求参数有误",
    401: "需要登录",
    403: "权限不足",
    404: "资源不存在",
    409: "请求存在冲突",
    410: "资源已失效",
    422: "请求参数校验失败",
    429: "请求过于频繁,请稍后再试",
    500: "服务器内部错误",
    502: "上游服务暂时不可用",
    503: "服务暂不可用",
}


def _fallback_code(status_code: int) -> str:
    return _STATUS_CODE_FALLBACK.get(status_code, f"HTTP_{status_code}")


def _fallback_message(status_code: int) -> str:
    return _STATUS_MESSAGE_FALLBACK.get(status_code, "请求失败")


def normalize_http_exception_detail(status_code: int, detail: Any) -> dict:
    """把 HTTPException.detail 归一成 {code, message, details}。

    - detail 是字符串:message=原字符串,code 按状态码稳定回退;
    - detail 是 dict:已有 code/message 时保留领域含义,缺失时按状态码回退;
      其余键(entitlement、reason 等)整体放进 details,不再整体嵌套在 detail 下;
    - 其他类型(理论上不会出现,不静默吞掉):如实包进 details.raw。
    """
    if isinstance(detail, dict):
        code = detail.get("code") or _fallback_code(status_code)
        message = detail.get("message") or _fallback_message(status_code)
        extra = {k: v for k, v in detail.items() if k not in ("code", "message")}
        return {"code": code, "message": message, "details": jsonable_encoder(extra) or None}
    if isinstance(detail, str):
        return {"code": _fallback_code(status_code), "message": detail or _fallback_message(status_code),
                "details": None}
    return {
        "code": _fallback_code(status_code),
        "message": _fallback_message(status_code),
        "details": {"raw": jsonable_encoder(detail)} if detail is not None else None,
    }


def register_error_handlers(app: FastAPI) -> None:
    async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
        body = normalize_http_exception_detail(exc.status_code, exc.detail)
        headers = getattr(exc, "headers", None)
        return JSONResponse(body, status_code=exc.status_code, headers=headers)

    async def _validation_exception_handler(request: Request, exc: RequestValidationError):
        body = {
            "code": "VALIDATION_ERROR",
            "message": "请求参数校验失败",
            "details": {"errors": jsonable_encoder(exc.errors())},
        }
        return JSONResponse(body, status_code=422)

    async def _unhandled_exception_handler(request: Request, exc: Exception):
        # 服务端记录真实异常与堆栈;响应体绝不包含异常原文、SQL、绝对路径或堆栈。
        log.exception("unhandled exception on %s %s", request.method, request.url.path)
        body = {"code": "INTERNAL_ERROR", "message": "服务器内部错误", "details": None}
        # Starlette ServerErrorMiddleware 是最外层中间件,这个 handler 产出的响应经
        # 顶层原始 send 直接发出,不经过任何 user_middleware(包括 cache_policy 的
        # CachePolicyMiddleware)——header 必须在这里直接设置,不能指望中间件兜底。
        return JSONResponse(body, status_code=500, headers={"Cache-Control": "private, no-store"})

    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
