"""Read-only, manifest-bound same-origin media routes."""

from fastapi import APIRouter, HTTPException, Query, Response

from backend.media.team_crests import read_team_crest

from .schemas import error_responses

router = APIRouter(
    prefix="/api/v1/media",
    tags=["media"],
    responses=error_responses(404, 422),
)


@router.get(
    "/team-crests/{provider}/{provider_team_id}.png",
    response_class=Response,
    responses={
        200: {
            "description": "Manifest-bound PNG team crest",
            "content": {"image/png": {}},
        }
    },
)
def team_crest(
    provider: str,
    provider_team_id: int,
    v: str = Query(..., min_length=12, max_length=12),
):
    crest = read_team_crest(provider, provider_team_id, version=v)
    if crest is None:
        raise HTTPException(status_code=404, detail="队徽不存在")
    return Response(
        content=crest.content,
        media_type="image/png",
        headers={
            "ETag": f'"{crest.sha256}"',
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
