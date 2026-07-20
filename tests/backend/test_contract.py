"""P0-5 API 契约测试(Pydantic 单一真源,CLAUDE.md §3.1 / §10):

1. 每个返回 application/json 的 2xx operation 都必须有非空 response schema
   (即每个端点都声明了 response_model);redirect/204/文件流路径为显式白名单,
   并验证它们确实不声明 2xx JSON body。
2. 免费概率边界(§8.2)在 schema 层可证:PredictionFreeDTO 物理上不存在
   draw/away 概率字段;完整三项只在 PredictionFullDTO。
3. frontend/lib/openapi.json 与 app.openapi() 逐字段一致,防契约漂移。
4. 全站统一错误契约:除 /readyz 503(独立运维 allowlist)外,全部已声明的
   4xx/5xx JSON 响应引用 ApiErrorDTO;不允许 ErrorDetail/AuthDisabledDTO/
   LegacyErrorResponse/HTTPValidationError 残留;真实请求验证多类异常源
   (字符串/dict HTTPException、422、未知路由、AUTH_DISABLED、未捕获异常、
   legacy 400/401/403/422)顶层精确为 {code,message,details}。
"""

import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 白名单:2xx 不返回 JSON body 的路径(decorator responses={} 中已显式声明语义)
NON_JSON_2XX_ALLOWED = {
    "/api/v1/auth/wechat/oa/start",              # 302 跳转微信授权页,无 body
    "/api/v1/analytics/events",                  # 204 无 body
    "/api/v1/studio/exports/{job_id}/download",  # application/octet-stream 文件流
}

# 唯一独立运维 allowlist:/readyz 的 503 是 {ok, problems},不是错误契约,不经统一处理器。
READYZ_503_ALLOWLIST = {("/readyz", "get", "503")}

# 契约收口前的旧错误 DTO,全部已删除,不得再被任何 operation 引用。
_RETIRED_ERROR_SCHEMAS = (
    "ErrorDetail", "AuthDisabledDTO", "LegacyErrorResponse", "LegacyErrorDetail",
    "HTTPValidationError",
)


def _schema_nonempty(schema: dict) -> bool:
    return bool(
        schema
        and (
            schema.get("$ref")
            or schema.get("properties")
            or schema.get("anyOf")
            or schema.get("oneOf")
            or schema.get("items")
            or schema.get("type")
        )
    )


class TestEveryJsonOperationHasSchema:
    def test_all_2xx_json_responses_have_nonempty_schema(self, app):
        spec = app.openapi()
        missing = []
        for path, ops in spec["paths"].items():
            for method, op in ops.items():
                for code, resp in op.get("responses", {}).items():
                    if not code.startswith("2"):
                        continue
                    content = resp.get("content", {})
                    if "application/json" not in content:
                        continue
                    schema = content["application/json"].get("schema", {})
                    if not _schema_nonempty(schema):
                        missing.append(f"{method.upper()} {path} {code}")
        assert not missing, (
            "以下 operation 缺少 response_model(2xx JSON 无 schema):\n" + "\n".join(missing)
        )

    def test_whitelisted_paths_declare_no_2xx_json_body(self, app):
        """白名单路径必须真的是 redirect/204/文件流,不允许借白名单绕过 schema。"""
        spec = app.openapi()
        for path in NON_JSON_2XX_ALLOWED:
            assert path in spec["paths"], f"白名单路径 {path} 不存在,请更新白名单"
            for method, op in spec["paths"][path].items():
                for code, resp in op.get("responses", {}).items():
                    if code.startswith("2"):
                        assert "application/json" not in resp.get("content", {}), (
                            f"{method.upper()} {path} {code} 声明了 JSON body,不应在白名单中"
                        )


class TestFreePredictionSchemaGate:
    """§8.2:免费层受限字段在 schema 层就不存在(不是运行时遮挡)。"""

    def test_prediction_union_contains_free_and_full_variants(self, app):
        spec = app.openapi()
        comps = spec["components"]["schemas"]
        prediction = comps["PredictionResponse"]["properties"]["prediction"]
        refs = {r["$ref"] for r in prediction.get("anyOf", []) if "$ref" in r}
        assert "#/components/schemas/PredictionFreeDTO" in refs
        assert "#/components/schemas/PredictionFullDTO" in refs

    def test_free_variant_physically_lacks_restricted_fields(self, app):
        comps = app.openapi()["components"]["schemas"]
        free_props = comps["PredictionFreeDTO"]["properties"]
        for key in (
            "home_probability", "draw_probability", "away_probability",
            "home_win", "draw", "away_win",
            "expected_home_goals", "expected_away_goals",
        ):
            assert key not in free_props, f"免费 variant 泄漏受限字段 {key}"
        assert "top_outcome" in free_props and "top_probability" in free_props

    def test_full_variant_has_complete_wdl(self, app):
        comps = app.openapi()["components"]["schemas"]
        full_props = comps["PredictionFullDTO"]["properties"]
        for key in ("home_probability", "draw_probability", "away_probability"):
            assert key in full_props, f"pro/premium variant 缺少 {key}"


class TestOpenapiExportSync:
    def test_frontend_openapi_json_matches_app(self, app):
        exported_path = PROJECT_ROOT / "frontend" / "lib" / "openapi.json"
        assert exported_path.exists(), (
            "frontend/lib/openapi.json 不存在;请运行 python -m backend.cli.export_openapi"
        )
        exported = json.loads(exported_path.read_text(encoding="utf-8"))
        current = json.loads(json.dumps(app.openapi(), ensure_ascii=False))  # JSON 归一
        assert current == exported, (
            "frontend/lib/openapi.json 与后端 OpenAPI 不一致;"
            "请运行 python -m backend.cli.export_openapi && cd frontend && npm run gen:api"
        )


class TestUnifiedErrorSchemaDeclarations:
    """全站已声明的 4xx/5xx JSON 响应必须全部引用 ApiErrorDTO(唯一 allowlist:
    /readyz 503),且旧错误 DTO 不得再被任何 operation 引用或以组件形式残留。"""

    def test_every_declared_error_response_refs_api_error_dto(self, app):
        spec = app.openapi()
        bad = []
        for path, ops in spec["paths"].items():
            for method, op in ops.items():
                for code, resp in op.get("responses", {}).items():
                    if not (code[:1] in "45"):
                        continue
                    if (path, method, code) in READYZ_503_ALLOWLIST:
                        continue
                    content = resp.get("content", {})
                    if "application/json" not in content:
                        continue
                    schema = content["application/json"].get("schema", {})
                    ref = schema.get("$ref", "")
                    if not ref.endswith("/ApiErrorDTO"):
                        bad.append(f"{method.upper()} {path} {code} -> {schema}")
        assert not bad, (
            "以下 4xx/5xx JSON 响应未引用 ApiErrorDTO(除 /readyz 503 外不允许其他形状):\n"
            + "\n".join(bad)
        )

    def test_retired_error_schemas_not_defined(self, app):
        comps = app.openapi().get("components", {}).get("schemas", {})
        present = [name for name in _RETIRED_ERROR_SCHEMAS if name in comps]
        assert not present, f"以下已废弃错误 DTO 不应再出现在 OpenAPI 组件中: {present}"

    def test_retired_error_schemas_not_referenced_anywhere(self, app):
        """双重保险:即便组件定义被遗漏未删,也不允许任何 operation 通过 $ref 指向它们。"""
        raw = json.dumps(app.openapi())
        bad = [name for name in _RETIRED_ERROR_SCHEMAS if f"/{name}\"" in raw or f"/{name}'" in raw]
        assert not bad, f"OpenAPI 中仍有 $ref 指向已废弃错误 DTO: {bad}"

    def test_readyz_503_is_the_only_allowlisted_non_unified_shape(self, app):
        """/readyz 503 必须仍是 {ok, problems} 的 ReadyzProblemsDTO,不是 ApiErrorDTO,
        且是全站唯一一个不遵循统一错误契约的已声明响应。"""
        spec = app.openapi()
        op = spec["paths"]["/readyz"]["get"]
        schema = op["responses"]["503"]["content"]["application/json"]["schema"]
        assert schema.get("$ref", "").endswith("/ReadyzProblemsDTO")

    def test_api_error_dto_details_is_required_but_nullable(self, app):
        """details 必须始终出现在 JSON 里(不是 optional 的 key),但值允许 object
        或 null——统一错误处理器本来就始终返回 details 键(运行时行为不变),这里
        是让 OpenAPI/生成 TypeScript 如实反映这一点,而不是把它标成可省略。"""
        comps = app.openapi()["components"]["schemas"]
        dto = comps["ApiErrorDTO"]
        assert set(dto["required"]) == {"code", "message", "details"}, dto["required"]
        details_schema = dto["properties"]["details"]
        variants = details_schema.get("anyOf", [])
        types = {v.get("type") for v in variants}
        assert types == {"object", "null"}, details_schema


ORIGIN = {"Origin": "http://localhost:3000"}


def _assert_unified_error_body(r, *, expect_code: str | None = None):
    """通用断言:Content-Type application/json;顶层 keys 精确 {code,message,details};
    code/message 非空字符串;details 是 null 或 object。"""
    assert r.headers["content-type"].startswith("application/json"), r.headers.get("content-type")
    body = r.json()
    assert set(body.keys()) == {"code", "message", "details"}, body.keys()
    assert isinstance(body["code"], str) and body["code"], body
    assert isinstance(body["message"], str) and body["message"], body
    assert body["details"] is None or isinstance(body["details"], dict), body
    if expect_code is not None:
        assert body["code"] == expect_code, body
    return body


class TestUnifiedErrorContractRuntime:
    """真实 HTTP 请求验证多类异常源都归一成同一顶层结构(不是只看 OpenAPI 声明)。"""

    def test_unknown_route_404(self, client):
        r = client.get("/api/v1/this-route-does-not-exist")
        _assert_unified_error_body(r, expect_code="NOT_FOUND")

    def test_string_http_exception_404(self, client):
        """HTTPException(detail=str) 的稳定回退:message=原字符串,code 按状态码。"""
        r = client.get("/api/v1/matches/999999999")
        body = _assert_unified_error_body(r, expect_code="NOT_FOUND")
        assert body["message"] == "比赛不存在"
        assert body["details"] is None

    def test_request_validation_error_422(self, client):
        r = client.get("/api/v1/leagues/not-an-int/standings")
        body = _assert_unified_error_body(r, expect_code="VALIDATION_ERROR")
        assert isinstance(body["details"]["errors"], list) and body["details"]["errors"]

    def test_legacy_path_param_422(self, client):
        r = client.get("/api/league/not-an-int/overview")
        _assert_unified_error_body(r, expect_code="VALIDATION_ERROR")

    def test_legacy_400_invalid_season(self, client):
        r = client.get("/api/league/47/matches?season=not-a-real-season")
        body = _assert_unified_error_body(r, expect_code="invalid_season")
        assert body["details"] is None

    def test_legacy_membership_required_401_dict_detail_preserved(self, client):
        """dict-shaped HTTPException(detail={code,message,entitlement}):code 不丢失,
        entitlement 进入 details,不再整体嵌套在 detail 下。"""
        r = client.get("/api/league/47/betting")
        body = _assert_unified_error_body(r, expect_code="membership_required")
        assert body["details"] == {"entitlement": "report:deep"}

    def test_legacy_membership_required_403_dict_detail_via_normalizer(self):
        """已登录但缺少 report:deep 权益的 403 分支(`status_code=403 if
        ctx.authenticated else 401`)在真实浏览器/HTTP 客户端下**不可达**:会话
        Cookie 的 Path=/api/v1,不会随请求发送到 /api/league/*,因此
        `auth_context_for` 永远看到匿名态(这是 Opus 复核已记录的既存问题——
        §八:PRE-EXISTING,本轮不修 Cookie Path,不扩大到 '/')。
        这里改为直接单测 normalize_http_exception_detail 本身对"403 + dict
        detail"的归一行为,验证 code 不丢失、entitlement 进入 details——这正是
        真实 403 分支若被触发时会执行的同一段归一逻辑(与 401 分支完全共用)。"""
        from backend.api.error_handlers import normalize_http_exception_detail

        body = normalize_http_exception_detail(
            403, {"code": "membership_required", "message": "需要 report:deep 权益才能访问该数据",
                  "entitlement": "report:deep"},
        )
        assert body == {
            "code": "membership_required",
            "message": "需要 report:deep 权益才能访问该数据",
            "details": {"entitlement": "report:deep"},
        }

    def test_v1_role_mismatch_403_reachable_via_real_cookie(self, app, fresh_ip):
        """v1 API 不受 legacy 的 Cookie Path 限制:非 admin 已登录用户访问 admin
        端点真实走 HTTP 拿到 403(require_admin 的字符串 detail 归一)。"""
        from fastapi.testclient import TestClient

        c = TestClient(app)
        r1 = c.get("/api/v1/auth/wechat/oa/start?next=/", follow_redirects=False,
                   headers={"x-real-ip": fresh_ip})
        c.get(r1.headers["location"], follow_redirects=False)
        r = c.get("/api/v1/admin/users")
        body = _assert_unified_error_body(r, expect_code="FORBIDDEN")
        assert r.status_code == 403
        assert body["message"] == "需要管理员权限"

    def test_auth_disabled_503(self):
        """WECHAT_AUTH_ENABLED=0(real provider)→ 503 AUTH_DISABLED,details=null。"""
        from fastapi.testclient import TestClient

        from backend.api.app import create_app

        from .conftest import BASE_ENV, make_settings

        settings = make_settings(WECHAT_AUTH_PROVIDER="real", WECHAT_AUTH_ENABLED="0")
        c = TestClient(create_app(settings))
        r = c.get("/api/v1/auth/wechat/oa/start?next=/", follow_redirects=False)
        body = _assert_unified_error_body(r, expect_code="AUTH_DISABLED")
        assert r.status_code == 503
        assert body["details"] is None

    def test_uncaught_exception_500_sanitized(self, app, monkeypatch):
        """未捕获异常:500 INTERNAL_ERROR,响应体不泄露异常原文、SQL 或绝对路径。
        TestClient 默认 raise_server_exceptions=True 会把服务端异常重新在测试进程
        抛出(便于调试);这里显式关闭,验证的是真实 HTTP 客户端会收到的响应体。"""
        from fastapi.testclient import TestClient

        import backend.api.routes_public as rp

        secret_marker = "super-secret-sql-and-path-xyz123"

        def _boom(conn, match_id):
            raise RuntimeError(f"{secret_marker}: /Users/x/data/allwin.db SELECT * FROM users")

        monkeypatch.setattr(rp.q_matches, "match_by_id", _boom)
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/api/v1/matches/1")
        body = _assert_unified_error_body(r, expect_code="INTERNAL_ERROR")
        assert r.status_code == 500
        assert body["message"] == "服务器内部错误"
        assert body["details"] is None
        raw = r.text
        assert secret_marker not in raw
        assert "allwin.db" not in raw
        assert "Traceback" not in raw
        assert "RuntimeError" not in raw


class TestStandaloneApiServerAppUsesUnifiedHandlers:
    """backend.api_server.app 是另一个可独立运行的 FastAPI app(文件末尾自带
    `if __name__ == "__main__": uvicorn.run(app, ...)`),不是只作为路由对象仓库
    被 backend.api.app 借用。它自己的 OpenAPI 早就声明了 ApiErrorDTO(response_model/
    error_responses),必须自己也注册同一套统一处理器,否则该独立运行模式下
    schema 与运行时不一致——生产入口 backend.api.app:app 借用其路由对象时走的是
    主 app 自己的处理器,不受这里影响,但这不能成为这个 app 自身内部不一致的借口。
    """

    def _standalone_client(self):
        from fastapi.testclient import TestClient

        from backend import api_server

        return TestClient(api_server.app, raise_server_exceptions=False)

    def test_legacy_422_unified_on_standalone_app(self):
        c = self._standalone_client()
        r = c.get("/api/league/not-int/overview")
        assert r.status_code == 422
        body = r.json()
        assert set(body.keys()) == {"code", "message", "details"}
        assert body["code"] == "VALIDATION_ERROR"
        assert isinstance(body["details"]["errors"], list) and body["details"]["errors"]

    def test_unknown_route_404_unified_on_standalone_app(self):
        c = self._standalone_client()
        r = c.get("/missing-route")
        assert r.status_code == 404
        body = r.json()
        assert set(body.keys()) == {"code", "message", "details"}
        assert body["code"] == "NOT_FOUND"
        assert body["details"] is None

    def test_legacy_400_dict_detail_unified_on_standalone_app(self, data_dir, monkeypatch):
        """dict-shaped HTTPException(_resolve_season 的 no_data_for_league)在
        standalone app 上同样被归一;临时目录里 core.db 已迁移但无任何比赛行,
        league_id=999999 自然触发"无数据"分支,不需要额外造数据、不碰真实
        data/*.db。"""
        from backend import api_server as legacy_mod
        from backend.db.paths import db_path

        monkeypatch.setattr(legacy_mod, "DB_PATH", db_path("core"))
        c = self._standalone_client()
        r = c.get("/api/league/999999/overview")
        assert r.status_code == 400
        body = r.json()
        assert set(body.keys()) == {"code", "message", "details"}
        assert body["code"] == "no_data_for_league"
        assert body["details"] is None

    def test_legacy_401_anonymous_betting_unified_on_standalone_app(self, data_dir):
        """匿名访问付费 legacy 端点:401 + membership_required,entitlement 保留
        在 details 里。require_membership 的鉴权检查先于任何 core db 访问,
        不需要 monkeypatch DB_PATH;data_dir 提供已迁移(含 plan/entitlement 种子)
        的临时 platform.db。"""
        c = self._standalone_client()
        r = c.get("/api/league/47/betting")
        assert r.status_code == 401
        body = r.json()
        assert set(body.keys()) == {"code", "message", "details"}
        assert body["code"] == "membership_required"
        assert body["details"] == {"entitlement": "report:deep"}

    def test_uncaught_exception_500_unified_on_standalone_app(self, data_dir, monkeypatch):
        """未捕获异常在 standalone app 上同样归一为 500 INTERNAL_ERROR,响应体
        不泄露异常原文、SQL、绝对路径或堆栈(不依赖网络,不碰真实 data/*.db)。"""
        from backend import api_server as legacy_mod

        secret_marker = "super-secret-standalone-xyz789"

        def _boom():
            raise RuntimeError(f"{secret_marker}: /Users/x/data/allwin.db SELECT * FROM users")

        monkeypatch.setattr(legacy_mod, "get_readonly_connection", _boom)
        c = self._standalone_client()
        r = c.get("/api/league/47/overview")
        assert r.status_code == 500
        body = r.json()
        assert set(body.keys()) == {"code", "message", "details"}
        assert body["code"] == "INTERNAL_ERROR"
        assert body["message"] == "服务器内部错误"
        assert body["details"] is None
        raw = r.text
        assert secret_marker not in raw
        assert "allwin.db" not in raw
        assert "Traceback" not in raw
        assert "RuntimeError" not in raw

    def test_standalone_app_openapi_already_declared_api_error_dto(self):
        """确认这不是"降低标准迁就运行时"——OpenAPI 早就声明了 ApiErrorDTO,
        本次是让运行时补上与既有声明一致的行为。"""
        from backend import api_server

        spec = api_server.app.openapi()
        op = spec["paths"]["/api/league/{league_id}/overview"]["get"]
        for code in ("400", "422"):
            schema = op["responses"][code]["content"]["application/json"]["schema"]
            assert schema.get("$ref", "").endswith("/ApiErrorDTO")

    def test_main_app_still_unaffected(self, client):
        """回归防护:main app(backend.api.app:app)的行为不因本次改动变化——
        它本来就借用同一份处理器逻辑(通过自己的 register_error_handlers 调用),
        与 api_server.app 是否注册无关。"""
        r = client.get("/api/league/not-int/overview")
        assert r.status_code == 422
        body = r.json()
        assert set(body.keys()) == {"code", "message", "details"}
        assert body["code"] == "VALIDATION_ERROR"


class TestGeneratedTypeScriptApiErrorDTO:
    """生成的 frontend/lib/api-types.ts 里 ApiErrorDTO.details 不得是 optional
    (`details?:`),必须是必填字段(`details:`),值仍允许 null——与 OpenAPI
    required 集合、Pydantic 模型三处一致,防止三者中任一处单独漂移。"""

    def _api_error_dto_block(self) -> str:
        ts_path = PROJECT_ROOT / "frontend" / "lib" / "api-types.ts"
        assert ts_path.exists(), "frontend/lib/api-types.ts 不存在;请运行 npm run gen:api"
        text = ts_path.read_text(encoding="utf-8")
        start = text.index("ApiErrorDTO: {")
        end = text.index("};", start)
        return text[start:end]

    def test_details_field_is_not_optional(self):
        block = self._api_error_dto_block()
        assert "details?:" not in block, (
            "api-types.ts 的 ApiErrorDTO.details 不应是 optional(details?:);"
            "请确认 backend/api/schemas.py 的 details 字段无默认值,并重新运行 "
            "python -m backend.cli.export_openapi && npm run gen:api"
        )

    def test_details_field_present_and_allows_null(self):
        block = self._api_error_dto_block()
        assert "details:" in block
        # openapi-typescript 对 anyOf[object,null] 渲染为跨多行的形式:
        #   details: {
        #       [key: string]: unknown;
        #   } | null;
        # details 是 ApiErrorDTO 的最后一个字段(schemas.py 里 code/message/details
        # 的声明顺序,Pydantic 保留字段顺序),故从 "details:" 到 block 末尾(已在
        # _api_error_dto_block 里截到 "};" 之前)就是完整片段,不必猜哪个分号收尾
        # ——嵌套的 index signature `[key: string]: unknown;` 自身也带分号,不能
        # 简单找第一个分号。
        details_field = block[block.index("details:") :]
        assert "null" in details_field, details_field
