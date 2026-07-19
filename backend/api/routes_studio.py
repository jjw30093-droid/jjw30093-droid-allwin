"""/api/v1/studio/*:analysis_bundle 预览、内容草稿、导出(analyst/admin 专用)。

导出文件落 data/exports/(不进 git);PNG 由前端 DOM→PNG 生成,服务端只登记
export_jobs 审计;TXT/JSON/SRT 服务端生成可下载。全部 private, no-store。
"""

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.db.connections import connect_ro, tx
from backend.db.paths import data_dir
from backend.db.util import new_uuid, utc_now_iso
from backend.studio.bundle import build_analysis_bundle, render_srt, render_txt

from .deps import (
    NO_STORE,
    AuthContext,
    platform_ro,
    platform_rw,
    require_csrf,
    require_user,
)

router = APIRouter(prefix="/api/v1/studio", tags=["studio"])

EXPORT_KINDS = ("png_1080x1920", "png_1080x1350", "txt", "json", "srt")


def require_analyst(ctx: AuthContext = Depends(require_user)) -> AuthContext:
    if ctx.role not in ("analyst", "admin"):
        raise HTTPException(status_code=403, detail="Studio 仅限 analyst/admin 使用")
    return ctx


def require_analyst_csrf(ctx: AuthContext = Depends(require_csrf)) -> AuthContext:
    if ctx.role not in ("analyst", "admin"):
        raise HTTPException(status_code=403, detail="Studio 仅限 analyst/admin 使用")
    return ctx


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = NO_STORE


def _build_bundle_now(match_id: int) -> dict:
    conn_core = connect_ro("core")
    conn_platform = connect_ro("platform")
    try:
        conn_odds = connect_ro("odds")
    except Exception:
        conn_odds = None
    try:
        bundle = build_analysis_bundle(conn_core, conn_platform, conn_odds, match_id)
    finally:
        conn_core.close()
        conn_platform.close()
        if conn_odds is not None:
            conn_odds.close()
    if bundle is None:
        raise HTTPException(status_code=404, detail="比赛不存在")
    return bundle


@router.get("/matches/{match_id}/bundle")
def match_bundle(
    match_id: int,
    response: Response,
    ctx: AuthContext = Depends(require_analyst),
):
    _no_store(response)
    return _build_bundle_now(match_id)


class CreateDraftBody(BaseModel):
    match_id: int
    title: str = ""


@router.post("/drafts")
def create_draft(
    body: CreateDraftBody,
    response: Response,
    ctx: AuthContext = Depends(require_analyst_csrf),
    conn=Depends(platform_rw),
):
    """冻结当前 bundle 为草稿(选择数据快照 = 以此刻数据为准)。"""
    _no_store(response)
    bundle = _build_bundle_now(body.match_id)
    draft_id = new_uuid()
    now = utc_now_iso()
    title = body.title or f"{bundle['match']['home']['name']} vs {bundle['match']['away']['name']}"
    with tx(conn):
        conn.execute(
            "INSERT INTO content_drafts (id, user_id, match_id, title, bundle_json, overrides_json, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, '{}', 'draft', ?, ?)",
            (draft_id, ctx.user_id, body.match_id, title,
             json.dumps(bundle, ensure_ascii=False), now, now),
        )
    return {"draft_id": draft_id, "title": title, "bundle_hash": bundle["bundle_hash"]}


@router.get("/drafts")
def list_drafts(
    response: Response,
    ctx: AuthContext = Depends(require_analyst),
    conn=Depends(platform_ro),
):
    _no_store(response)
    rows = conn.execute(
        "SELECT id, match_id, title, status, created_at, updated_at FROM content_drafts"
        " WHERE user_id=? ORDER BY updated_at DESC LIMIT 100",
        (ctx.user_id,),
    ).fetchall()
    return {"drafts": [dict(r) for r in rows]}


def _get_draft(conn, draft_id: str, user_id: str):
    row = conn.execute(
        "SELECT * FROM content_drafts WHERE id=? AND user_id=?", (draft_id, user_id)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="草稿不存在")
    return row


@router.get("/drafts/{draft_id}")
def get_draft(
    draft_id: str,
    response: Response,
    ctx: AuthContext = Depends(require_analyst),
    conn=Depends(platform_ro),
):
    _no_store(response)
    row = _get_draft(conn, draft_id, ctx.user_id)
    return {
        "id": row["id"],
        "match_id": row["match_id"],
        "title": row["title"],
        "status": row["status"],
        "bundle": json.loads(row["bundle_json"]),
        "overrides": json.loads(row["overrides_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class UpdateDraftBody(BaseModel):
    title: str | None = None
    overrides: dict | None = None      # 标题/证据/风险/口播稿等编辑覆盖


@router.post("/drafts/{draft_id}")
def update_draft(
    draft_id: str,
    body: UpdateDraftBody,
    response: Response,
    ctx: AuthContext = Depends(require_analyst_csrf),
    conn=Depends(platform_rw),
):
    _no_store(response)
    row = _get_draft(conn, draft_id, ctx.user_id)
    overrides = json.loads(row["overrides_json"])
    if body.overrides is not None:
        overrides.update(body.overrides)
    with tx(conn):
        conn.execute(
            "UPDATE content_drafts SET title=COALESCE(?, title), overrides_json=?, updated_at=? WHERE id=?",
            (body.title, json.dumps(overrides, ensure_ascii=False), utc_now_iso(), draft_id),
        )
    return {"status": "ok"}


class DraftStatusBody(BaseModel):
    status: str = Field(pattern="^(draft|reviewed|published)$")


@router.post("/drafts/{draft_id}/status")
def set_draft_status(
    draft_id: str,
    body: DraftStatusBody,
    response: Response,
    ctx: AuthContext = Depends(require_analyst_csrf),
    conn=Depends(platform_rw),
):
    _no_store(response)
    _get_draft(conn, draft_id, ctx.user_id)
    with tx(conn):
        conn.execute(
            "UPDATE content_drafts SET status=?, updated_at=? WHERE id=?",
            (body.status, utc_now_iso(), draft_id),
        )
    return {"status": "ok"}


class ExportBody(BaseModel):
    kind: str


@router.post("/drafts/{draft_id}/export")
def export_draft(
    draft_id: str,
    body: ExportBody,
    response: Response,
    ctx: AuthContext = Depends(require_analyst_csrf),
    conn=Depends(platform_rw),
):
    _no_store(response)
    if body.kind not in EXPORT_KINDS:
        raise HTTPException(status_code=400, detail=f"kind 需为 {EXPORT_KINDS}")
    row = _get_draft(conn, draft_id, ctx.user_id)
    bundle = json.loads(row["bundle_json"])
    overrides = json.loads(row["overrides_json"])
    job_id = new_uuid()
    now = utc_now_iso()

    if body.kind.startswith("png_"):
        # PNG 由前端 DOM→PNG 生成;服务端登记导出审计,导出物含 cutoff+模型版本由前端渲染保证
        with tx(conn):
            conn.execute(
                "INSERT INTO export_jobs (id, user_id, draft_id, match_id, kind, status, meta_json, created_at, finished_at)"
                " VALUES (?, ?, ?, ?, ?, 'succeeded', ?, ?, ?)",
                (job_id, ctx.user_id, draft_id, row["match_id"], body.kind,
                 json.dumps({"side": "client", "bundle_hash": bundle.get("bundle_hash")}), now, now),
            )
        return {"job_id": job_id, "side": "client",
                "data_cutoff_at": bundle.get("data_cutoff_at"),
                "model_version": bundle.get("model_version")}

    if body.kind == "txt":
        content = render_txt(bundle, overrides)
        ext = "txt"
    elif body.kind == "srt":
        content = render_srt(bundle, overrides)
        ext = "srt"
    else:
        content = json.dumps({"bundle": bundle, "overrides": overrides}, ensure_ascii=False, indent=1)
        ext = "json"

    export_dir = data_dir() / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    file_path = export_dir / f"{job_id}.{ext}"
    file_path.write_text(content, encoding="utf-8")
    with tx(conn):
        conn.execute(
            "INSERT INTO export_jobs (id, user_id, draft_id, match_id, kind, status, file_path, meta_json, created_at, finished_at)"
            " VALUES (?, ?, ?, ?, ?, 'succeeded', ?, ?, ?, ?)",
            (job_id, ctx.user_id, draft_id, row["match_id"], body.kind, str(file_path),
             json.dumps({"bundle_hash": bundle.get("bundle_hash")}), now, now),
        )
    return {"job_id": job_id, "side": "server",
            "download_url": f"/api/v1/studio/exports/{job_id}/download",
            "data_cutoff_at": bundle.get("data_cutoff_at"),
            "model_version": bundle.get("model_version")}


@router.get("/exports/{job_id}/download")
def download_export(
    job_id: str,
    ctx: AuthContext = Depends(require_analyst),
    conn=Depends(platform_ro),
):
    row = conn.execute(
        "SELECT * FROM export_jobs WHERE id=? AND user_id=?", (job_id, ctx.user_id)
    ).fetchone()
    if row is None or not row["file_path"]:
        raise HTTPException(status_code=404, detail="导出不存在或为客户端导出")
    path = Path(row["file_path"])
    if not path.exists():
        raise HTTPException(status_code=410, detail="导出文件已清理")
    return FileResponse(
        path,
        filename=path.name,
        headers={"Cache-Control": NO_STORE},
    )
