"""Read the shared standard store's published IDs at the website boundary.

This is an additive v1 migration: legacy query/storage IDs remain internal to
existing queries. Never allocate identities here or infer cross-source matches.
The sync writer atomically replaces the store; each call reads one snapshot.
"""

import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException


def configured_store() -> str | None:
    return os.environ.get("VIP_STANDARD_STORE") or None


@contextmanager
def _read(path: str):
    conn = None
    try:
        conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
        deadline = time.monotonic() + 1
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        yield conn
    except (sqlite3.Error, OSError, ValueError, TypeError):
        raise HTTPException(503, "比赛编号服务暂不可用") from None
    finally:
        if conn is not None:
            conn.close()


def _uuid(value: str) -> str:
    parsed = str(UUID(value))
    if parsed != value:
        raise ValueError("noncanonical_uuid")
    return parsed


def _source_entity_id(identity: UUID, path: str, kind: str) -> int:
    """Resolve canonical/alias IDs, then reuse v1's existing authorization path."""
    label = {"match": "比赛", "player": "球员", "competition": "赛事", "team": "球队"}[kind]
    with _read(path) as conn:
        current = str(identity)
        seen = set()
        for _ in range(32):
            if current in seen:
                raise ValueError("identity_alias_cycle")
            seen.add(current)
            row = conn.execute("SELECT kind FROM entities WHERE entity_id=?", (current,)).fetchone()
            if row is None:
                if len(seen) == 1:
                    raise HTTPException(404, f"{label}不存在")
                raise ValueError("dangling_alias")
            if row[0] != kind:
                if len(seen) == 1:
                    raise HTTPException(404, f"{label}不存在")
                raise ValueError("alias_kind_conflict")
            alias = conn.execute(
                "SELECT entity_id,kind FROM identity_aliases WHERE alias_id=?", (current,)
            ).fetchone()
            if alias is None:
                break
            if alias[1] != kind:
                raise ValueError("alias_kind_conflict")
            current = _uuid(alias[0])
        else:
            raise ValueError("identity_alias_depth")
        rows = conn.execute(
            "SELECT source_id FROM source_ids WHERE kind=? AND provider='fotmob' AND entity_id=?",
            (kind, current),
        ).fetchall()
        if not rows:
            raise HTTPException(404, f"{label}尚未接入此接口")
        if len(rows) != 1 or not re.fullmatch(r"[1-9][0-9]{0,18}", str(rows[0][0])):
            raise ValueError("invalid_source_identity")
        result = int(rows[0][0])
        if result > 9223372036854775807:
            raise ValueError("source_identity_overflow")
        return result


def source_match_id(identity: UUID, path: str) -> int:
    return _source_entity_id(identity, path, "match")

def source_player_id(identity: UUID, path: str) -> int:
    return _source_entity_id(identity, path, "player")

def source_team_id(identity: UUID, path: str) -> int:
    return _source_entity_id(identity, path, "team")


def source_competition_id(identity: UUID, path: str) -> int:
    return _source_entity_id(identity, path, "competition")

def project_match_ids(matches: list[dict]) -> list[dict]:
    """One indexed page lookup, not one connection/query per match or a full scan.

    A new source match may precede the hourly sync: null means no published ID
    yet, so old URLs continue to work. A configured but broken store fails closed.
    """
    path = configured_store()
    identities: dict[int, str] = {}
    if path and matches:
        source_ids = sorted({str(match["match_id"]) for match in matches})
        if len(source_ids) > 400:
            raise ValueError("identity_page_limit")
        with _read(path) as conn:
            rows = conn.execute(
                "SELECT s.source_id,s.entity_id,e.kind,a.alias_id FROM source_ids s "
                "LEFT JOIN entities e ON e.entity_id=s.entity_id "
                "LEFT JOIN identity_aliases a ON a.alias_id=s.entity_id "
                "WHERE s.kind='match' AND s.provider='fotmob' AND s.source_id IN ("
                + ",".join("?" for _ in source_ids) + ")",
                source_ids,
            ).fetchall()
            for source, identity, kind, alias in rows:
                if kind != "match" or alias is not None or int(source) in identities:
                    raise ValueError("invalid_active_identity")
                identities[int(source)] = _uuid(identity)
    return [dict(match, canonical_match_id=identities.get(match["match_id"])) for match in matches]
