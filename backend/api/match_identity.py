"""Resolve canonical match URLs at the legacy API boundary.

Numeric URLs remain independent of the optional registry. UUID URLs reuse the
same endpoint, access checks and response DTOs; no redirects or alternate data
authorization path. This adapter does not migrate underlying fact queries.
"""

import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException

from .standard_identity import configured_store, source_match_id


def legacy_match_id(match_id: int | UUID) -> int:
    if isinstance(match_id, int):
        return match_id
    standard_store = configured_store()
    if standard_store:
        return source_match_id(match_id, standard_store)
    configured = os.environ.get("VIP_CANONICAL_REGISTRY")
    if not configured:
        raise HTTPException(503, "比赛编号服务暂不可用")
    identity = str(match_id)
    try:
        uri = Path(configured).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as conn:
            conn.execute("PRAGMA query_only=ON")
            # Never silently choose a canonical/alias collision or corrupt xref.
            identities = {r[0] for r in conn.execute(
                "SELECT id FROM matches WHERE id=? UNION "
                "SELECT match_id FROM match_aliases WHERE alias=?",
                (identity, identity),
            )}
            if not identities:
                raise HTTPException(404, "比赛不存在")
            if len(identities) != 1:
                raise ValueError("ambiguous_identity")
            rows = conn.execute(
                "SELECT s.source_id FROM match_sources s "
                "JOIN matches m ON m.id=s.match_id "
                "WHERE s.provider='fotmob' AND s.match_id=?",
                (next(iter(identities)),),
            ).fetchall()
            if not rows:
                raise HTTPException(404, "比赛尚未接入此接口")
            if len(rows) != 1 or not re.fullmatch(r"[1-9][0-9]{0,18}", str(rows[0][0])):
                raise ValueError("invalid_source_identity")
            result = int(rows[0][0])
            if result > 9223372036854775807:
                raise ValueError("source_identity_overflow")
            return result
    except (sqlite3.Error, OSError, ValueError):
        raise HTTPException(503, "比赛编号服务暂不可用") from None


LegacyMatchId = Annotated[int, Depends(legacy_match_id)]
