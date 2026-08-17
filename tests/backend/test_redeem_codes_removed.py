"""兑换码(CDKEY)功能整体下架验收(站长明确决定,2026-08-17——完整删除,
不是简化或降级)。

删除范围:backend/commands/redeem.py、POST /api/v1/redeem、
POST /api/v1/admin/redeem-codes、GET /api/v1/admin/redeem-codes、
platform.db 的 redeem_codes 表(见 backend/migrations/platform/
0017_drop_redeem_codes.sql)。

每日精选真正的授权路径不受影响,继续保留且是唯一入口:管理员通过
POST /api/v1/admin/reco/access-grants 直接为"用户 + 单条 slip"授权
(backend/commands/reco_access.py::grant_access),验收见
tests/backend/test_reco_access.py——本文件不重复覆盖那条路径,只验证
兑换码路径确实已经不存在。

三个端点必须整体从路由表消失,得到 404(路由不存在),而不是被权限中间件
拦成 401/403(那意味着路由还在,只是访问被拒绝,不满足"已删除"标准)。
"""

import importlib

import pytest
from fastapi.testclient import TestClient


def _route_paths_and_methods(app):
    """FastAPI (0.139) 把 app.include_router() 装配的路由包成
    `_IncludedRouter`,真正的 APIRoute 列表在 `.original_router.routes`
    里,不直接挂在 `app.routes` 上——必须展开这一层,否则"路由表里已经
    没有这个路径"的检查会对任何 include_router 装配的端点都误报"不存在"。
    """
    out = set()
    stack = list(app.routes)
    seen: set[int] = set()
    while stack:
        r = stack.pop()
        if id(r) in seen:
            continue
        seen.add(id(r))
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", None)
        if path and methods:
            for m in methods:
                out.add((path, m))
        original_router = getattr(r, "original_router", None)
        if original_router is not None:
            stack.extend(getattr(original_router, "routes", []))
    return out


def test_redeem_routes_removed_from_route_table(app):
    routes = _route_paths_and_methods(app)
    assert ("/api/v1/redeem", "POST") not in routes
    assert ("/api/v1/admin/redeem-codes", "POST") not in routes
    assert ("/api/v1/admin/redeem-codes", "GET") not in routes


def test_redeem_endpoints_return_404_not_401_or_403(app):
    anon = TestClient(app)

    r1 = anon.post("/api/v1/redeem", json={"code": "AW-AAAA-BBBB-CCCC"})
    assert r1.status_code == 404, r1.text

    r2 = anon.post("/api/v1/admin/redeem-codes", json={"slip_id": "x", "count": 1})
    assert r2.status_code == 404, r2.text

    r3 = anon.get("/api/v1/admin/redeem-codes")
    assert r3.status_code == 404, r3.text


def test_redeem_command_module_deleted():
    """backend/commands/redeem.py 整个文件已删除,不是保留但不再被路由
    调用的死代码。"""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("backend.commands.redeem")


def test_redeem_dtos_removed_from_schemas():
    """RedeemResponse / RedeemCodeCreatedItem / AdminCodesCreatedResponse /
    AdminRedeemCodeItem / AdminCodesListResponse 五个 DTO 已从 schemas.py
    删除。"""
    from backend.api import schemas

    for name in (
        "RedeemResponse",
        "RedeemCodeCreatedItem",
        "AdminCodesCreatedResponse",
        "AdminRedeemCodeItem",
        "AdminCodesListResponse",
    ):
        assert not hasattr(schemas, name), f"schemas.{name} 应已删除"


def test_redeem_codes_table_dropped(data_dir):
    """data_dir fixture 已经把三个库迁移到最新版本(含 0017);redeem_codes
    表必须不存在。"""
    import sqlite3

    from backend.db.paths import db_path

    conn = sqlite3.connect(db_path("platform"))
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        conn.close()
    assert "redeem_codes" not in tables


class TestRecoAccessGrantsStillWorks:
    """每日精选授权在兑换码删除后仍然完整可用——admin 直接为用户 + slip
    授权这条路径不受本次删除影响。只做最小冒烟,完整覆盖见
    tests/backend/test_reco_access.py。"""

    def test_admin_direct_grant_still_unlocks_slip(self, app, data_dir, fresh_ip):
        from .authflow import wechat_scan_login
        from .test_reco import _admin_client, _create_slip, _prov_leg, _publish

        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(
            admin,
            title="兑换码删除后授权冒烟",
            legs=[_prov_leg("A vs B", "1x2", "主胜", 1.9, 850001)],
        )
        _publish(admin, sid)

        member = TestClient(app)
        wechat_scan_login(member, ip=f"{fresh_ip}-stillworks", openid="grant-stillworks")

        assert member.get(f"/api/v1/reco/daily/{sid}").status_code == 403

        csrf = {"X-CSRF-Token": admin.cookies.get("allwin_csrf"), "Origin": "http://localhost:3000"}
        uid = member.get("/api/v1/me").json()["user"]["id"]
        r = admin.post(
            "/api/v1/admin/reco/access-grants",
            json={"user_id": uid, "slip_id": sid},
            headers=csrf,
        )
        assert r.status_code == 200, r.text

        assert member.get(f"/api/v1/reco/daily/{sid}").status_code == 200
