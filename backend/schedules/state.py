"""Production schedule-state schema v1 contracts.

The module has no provider transport, Worker registration, API route, or
frontend integration.  It supplies the migration/schema gate and deterministic
SQLite commands used by the offline design proof.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.db import migrate


SCHEDULE_MIGRATION_VERSION = 3
SCHEDULE_MIGRATION_NAME = "0003_schedule_state_v1.sql"
REST_FEATURE_DEFINITION = "observed_kickoff_gap"
REST_FEATURE_VERSION = "schedule_state_v1"
REST_FEATURE_PROVENANCE = (
    "observed_historical:finished_non_cancelled_exact_verified_competitive:"
    "kickoff_to_kickoff"
)
COMPETITIVE_CLASSES = frozenset(
    {
        "league",
        "domestic_cup",
        "continental",
        "super_cup",
        "international_club",
    }
)
PROVIDER_MAX_LENGTH = 32
PROVIDER_MATCH_ID_MAX_LENGTH = 128
_PROVIDER_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_PROVIDER_MATCH_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")

_STATE_CONTENT_FIELDS = (
    "kickoff_at_utc",
    "kickoff_precision",
    "status",
    "finished",
    "cancelled",
    "home_team_id",
    "home_team_name",
    "away_team_id",
    "away_team_name",
    "competition_id",
    "season_label",
    "round_label",
    "stage_label",
    "competition_class",
    "competition_verified",
)


class ScheduleStateError(RuntimeError):
    """Base class for fixed, caller-safe schedule-state failures."""


class ScheduleStateMigrationError(ScheduleStateError):
    """The formal v1 migration did not apply atomically."""


class ScheduleStateSchemaError(ScheduleStateError):
    """The installed v1 schema differs from the reviewed schema."""


class ScheduleStateDataError(ScheduleStateError):
    """Input does not satisfy the deterministic state contract."""


class ScheduleStateConflictError(ScheduleStateError):
    """An immutable identity or event-time key has conflicting content."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _require_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScheduleStateDataError("invalid schedule state input") from None
    return value.strip()


def normalize_provider(value: Any) -> str:
    """Return one canonical ASCII provider slug."""

    if not isinstance(value, str):
        raise ScheduleStateDataError("invalid schedule state input") from None
    normalized = unicodedata.normalize("NFKC", value).strip().lower()
    if (
        len(normalized) > PROVIDER_MAX_LENGTH
        or _PROVIDER_RE.fullmatch(normalized) is None
    ):
        raise ScheduleStateDataError("invalid schedule state input") from None
    return normalized


def normalize_provider_match_id(value: Any) -> str:
    """Normalize positive numeric IDs and validate supported ASCII IDs."""

    if isinstance(value, bool):
        raise ScheduleStateDataError("invalid schedule state input") from None
    if isinstance(value, int):
        if value <= 0:
            raise ScheduleStateDataError("invalid schedule state input") from None
        return str(value)
    if not isinstance(value, str):
        raise ScheduleStateDataError("invalid schedule state input") from None
    normalized = value.strip()
    if (
        len(normalized) > PROVIDER_MATCH_ID_MAX_LENGTH
        or _PROVIDER_MATCH_ID_RE.fullmatch(normalized) is None
    ):
        raise ScheduleStateDataError("invalid schedule state input") from None
    if normalized.isdecimal():
        numeric = int(normalized)
        if numeric <= 0:
            raise ScheduleStateDataError("invalid schedule state input") from None
        return str(numeric)
    return normalized


def _identifier_text(value: Any) -> str:
    return normalize_provider_match_id(value)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return _require_text(value)


def _positive_int(value: Any, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ScheduleStateDataError("invalid schedule state input") from None
    return value


def _bool_int(value: Any) -> int:
    if not isinstance(value, bool):
        raise ScheduleStateDataError("invalid schedule state input") from None
    return int(value)


def _utc(value: Any) -> str:
    parsed: datetime | None = None
    failed = not isinstance(value, str) or not value.strip()
    if not failed:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            failed = True
    if (
        failed
        or parsed is None
        or parsed.tzinfo is None
        or parsed.utcoffset() != timezone.utc.utcoffset(parsed)
    ):
        raise ScheduleStateDataError("invalid schedule state timestamp") from None
    return (
        parsed.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _optional_utc(value: Any) -> str | None:
    return None if value is None else _utc(value)


def _hash_text(value: Any) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[0-9a-f]{64}", value) is None
    ):
        raise ScheduleStateDataError("invalid schedule state hash") from None
    return value


def _normalize_sql(sql: str) -> str:
    without_comments = migrate._strip_sql_comments(sql)
    return " ".join(
        without_comments.rstrip().rstrip(";").split()
    ).casefold()


def _expected_schema_objects(migrations_dir: Path) -> dict[str, tuple[str, str]]:
    migration_path = migrations_dir / SCHEDULE_MIGRATION_NAME
    sql = migration_path.read_text(encoding="utf-8")
    objects: dict[str, tuple[str, str]] = {}
    pattern = re.compile(
        r"^CREATE\s+(?:UNIQUE\s+)?(TABLE|INDEX|TRIGGER|VIEW)\s+([A-Za-z0-9_]+)\b",
        re.IGNORECASE,
    )
    for statement in migrate.split_statements(sql):
        match = pattern.match(migrate._strip_sql_comments(statement).lstrip())
        if match is None:
            continue
        object_type = match.group(1).casefold()
        name = match.group(2)
        objects[name] = (
            object_type,
            _normalize_sql(migrate._strip_sql_comments(statement)),
        )
    if not objects:
        raise ScheduleStateSchemaError(
            "schedule state schema validation failed"
        ) from None
    return objects


def assert_schedule_state_schema(
    conn: sqlite3.Connection,
    *,
    migrations_dir: Path | str | None = None,
) -> None:
    """Fail closed unless every reviewed v1 table/index/trigger/view is exact."""

    directory = (
        Path(migrations_dir)
        if migrations_dir is not None
        else migrate.MIGRATIONS_ROOT / "core"
    )
    failed = False
    try:
        expected = _expected_schema_objects(directory)
        placeholders = ", ".join("?" for _ in expected)
        rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master "
            f"WHERE name IN ({placeholders})",
            tuple(expected),
        ).fetchall()
        actual = {
            row[1]: (row[0].casefold(), _normalize_sql(row[2] or ""))
            for row in rows
        }
        namespaced = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE name GLOB 'schedule_*' "
                "OR name GLOB 'idx_schedule_*' "
                "OR name GLOB 'trg_schedule_*' "
                "OR name GLOB 'uq_schedule_*' "
                "OR name = 'current_schedule_match_state'"
            )
        }
        if actual != expected or namespaced != set(expected):
            raise ScheduleStateSchemaError(
                "schedule state schema validation failed"
            )
        if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise ScheduleStateSchemaError(
                "schedule state schema validation failed"
            )
        if conn.execute("PRAGMA recursive_triggers").fetchone()[0] != 1:
            raise ScheduleStateSchemaError(
                "schedule state schema validation failed"
            )
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ScheduleStateSchemaError(
                "schedule state schema validation failed"
            )
    except ScheduleStateSchemaError:
        raise
    except Exception:
        failed = True
    if failed:
        raise ScheduleStateSchemaError(
            "schedule state schema validation failed"
        ) from None


def apply_schedule_state_schema_v1(
    db_file: Path | str,
    *,
    migrations_dir: Path | str | None = None,
) -> int:
    """Apply core migrations and then verify the exact reviewed v1 objects."""

    directory = (
        Path(migrations_dir)
        if migrations_dir is not None
        else migrate.MIGRATIONS_ROOT / "core"
    )
    failed = False
    count = 0
    try:
        count = migrate.apply_all(
            "core",
            db_file=db_file,
            migrations_dir=directory,
            quiet=True,
        )
        conn = sqlite3.connect(db_file)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA recursive_triggers = ON")
            assert_schedule_state_schema(conn, migrations_dir=directory)
        finally:
            conn.close()
        return count
    except ScheduleStateSchemaError:
        raise
    except Exception:
        failed = True
    if failed:
        raise ScheduleStateMigrationError(
            "schedule state schema migration failed"
        ) from None
    return count


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA recursive_triggers = ON")
    if (
        conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1
        or conn.execute("PRAGMA recursive_triggers").fetchone()[0] != 1
    ):
        raise ScheduleStateSchemaError(
            "schedule state connection safety configuration failed"
        ) from None


def _begin(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        raise ScheduleStateDataError("schedule state transaction already active")
    conn.execute("BEGIN IMMEDIATE")


def _normalize_state(state: Mapping[str, Any]) -> dict[str, Any]:
    kickoff_precision = _require_text(state.get("kickoff_precision"))
    if kickoff_precision not in {"exact", "date_only", "unknown"}:
        raise ScheduleStateDataError("invalid schedule state input")
    kickoff = _optional_utc(state.get("kickoff_at_utc"))
    if kickoff_precision == "exact" and kickoff is None:
        raise ScheduleStateDataError("invalid schedule state input")
    finished = _bool_int(state.get("finished"))
    cancelled = _bool_int(state.get("cancelled"))
    if finished and cancelled:
        raise ScheduleStateDataError("invalid schedule state input")
    home_id = _positive_int(state.get("home_team_id"), optional=True)
    away_id = _positive_int(state.get("away_team_id"), optional=True)
    if home_id is not None and home_id == away_id:
        raise ScheduleStateDataError("invalid schedule state input")
    normalized = {
        "kickoff_at_utc": kickoff,
        "kickoff_precision": kickoff_precision,
        "status": _require_text(state.get("status")),
        "finished": finished,
        "cancelled": cancelled,
        "home_team_id": home_id,
        "home_team_name": _optional_text(state.get("home_team_name")),
        "away_team_id": away_id,
        "away_team_name": _optional_text(state.get("away_team_name")),
        "competition_id": _identifier_text(state.get("competition_id")),
        "season_label": _require_text(state.get("season_label")),
        "round_label": _optional_text(state.get("round_label")),
        "stage_label": _optional_text(state.get("stage_label")),
        "competition_class": _require_text(state.get("competition_class")),
        "competition_verified": _bool_int(
            state.get("competition_verified")
        ),
    }
    return normalized


def build_state_content_hash(state: Mapping[str, Any]) -> str:
    """Return the stable business-state hash used by immutable snapshots."""

    if not isinstance(state, Mapping):
        raise ScheduleStateDataError("invalid schedule state input") from None
    normalized = _normalize_state(state)
    return _sha256(
        {field: normalized[field] for field in _STATE_CONTENT_FIELDS}
    )


def _identity_id(
    conn: sqlite3.Connection,
    *,
    provider: str,
    provider_match_id: str,
    canonical_match_id: int | None,
    created_at: str,
    provenance: str,
) -> tuple[int, bool]:
    existing = conn.execute(
        "SELECT id, canonical_match_id, created_at, identity_provenance "
        "FROM schedule_match_identity "
        "WHERE provider=? AND provider_match_id=?",
        (provider, provider_match_id),
    ).fetchone()
    if existing is not None:
        if (
            existing["canonical_match_id"] != canonical_match_id
            or existing["identity_provenance"] != provenance
        ):
            raise ScheduleStateConflictError(
                "schedule match identity conflict"
            )
        return int(existing["id"]), False
    cursor = conn.execute(
        "INSERT INTO schedule_match_identity "
        "(provider, provider_match_id, canonical_match_id, created_at, "
        " identity_provenance) VALUES (?, ?, ?, ?, ?)",
        (
            provider,
            provider_match_id,
            canonical_match_id,
            created_at,
            provenance,
        ),
    )
    return int(cursor.lastrowid), True


def record_match_identity(
    conn: sqlite3.Connection,
    *,
    provider: str,
    provider_match_id: str | int,
    canonical_match_id: int | None,
    identity_created_at: str,
    identity_provenance: str,
) -> dict[str, Any]:
    """Insert or exactly replay a stable identity without inventing state."""

    provider_value = normalize_provider(provider)
    provider_match_value = normalize_provider_match_id(provider_match_id)
    canonical_value = _positive_int(canonical_match_id, optional=True)
    created_value = _utc(identity_created_at)
    provenance_value = _require_text(identity_provenance)

    _configure(conn)
    _begin(conn)
    write_conflict = False
    write_failed = False
    try:
        identity_id, inserted = _identity_id(
            conn,
            provider=provider_value,
            provider_match_id=provider_match_value,
            canonical_match_id=canonical_value,
            created_at=created_value,
            provenance=provenance_value,
        )
        conn.commit()
    except ScheduleStateError:
        conn.rollback()
        raise
    except sqlite3.IntegrityError:
        conn.rollback()
        write_conflict = True
    except Exception:
        conn.rollback()
        write_failed = True

    if write_conflict:
        raise ScheduleStateConflictError(
            "schedule match identity write conflict"
        ) from None
    if write_failed:
        raise ScheduleStateDataError(
            "schedule match identity write failed"
        ) from None
    return {"identity_id": identity_id, "inserted": inserted}


def _snapshot_id(
    conn: sqlite3.Connection,
    *,
    identity_id: int,
    state: Mapping[str, Any],
    observed_at: str,
    ingested_at: str,
    source_updated_at: str | None,
    provenance: str,
) -> tuple[int, str, bool]:
    state_hash = _sha256({field: state[field] for field in _STATE_CONTENT_FIELDS})
    existing = conn.execute(
        "SELECT * FROM schedule_match_state_snapshot "
        "WHERE match_identity_id=? AND state_content_hash=?",
        (identity_id, state_hash),
    ).fetchone()
    if existing is not None:
        if any(existing[field] != state[field] for field in _STATE_CONTENT_FIELDS):
            raise ScheduleStateConflictError("schedule match state conflict")
        return int(existing["id"]), state_hash, False
    columns = (
        "match_identity_id",
        "state_content_hash",
        *_STATE_CONTENT_FIELDS,
        "source_updated_at",
        "first_observed_at",
        "ingested_at",
        "provenance",
    )
    values = (
        identity_id,
        state_hash,
        *(state[field] for field in _STATE_CONTENT_FIELDS),
        source_updated_at,
        observed_at,
        ingested_at,
        provenance,
    )
    cursor = conn.execute(
        f"INSERT INTO schedule_match_state_snapshot ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})",
        values,
    )
    return int(cursor.lastrowid), state_hash, True


def record_match_state(
    conn: sqlite3.Connection,
    *,
    provider: str,
    provider_match_id: str | int,
    canonical_match_id: int | None,
    identity_created_at: str,
    identity_provenance: str,
    state: Mapping[str, Any],
    source_updated_at: str | None,
    snapshot_provenance: str,
    source: str,
    competition_scope: str,
    season_scope: str,
    observed_at: str,
    poll_run_id: str | None,
    payload_hash: str,
    ingested_at: str,
) -> dict[str, Any]:
    """Atomically append identity/state/observation or perform an exact skip."""

    provider_value = normalize_provider(provider)
    provider_match_value = normalize_provider_match_id(provider_match_id)
    canonical_value = _positive_int(canonical_match_id, optional=True)
    created_value = _utc(identity_created_at)
    identity_provenance_value = _require_text(identity_provenance)
    normalized_state = _normalize_state(state)
    source_updated_value = _optional_utc(source_updated_at)
    snapshot_provenance_value = _require_text(snapshot_provenance)
    source_value = _require_text(source)
    competition_scope_value = _require_text(competition_scope)
    season_scope_value = _require_text(season_scope)
    observed_value = _utc(observed_at)
    poll_run_value = _optional_text(poll_run_id)
    payload_hash_value = _hash_text(payload_hash)
    ingested_value = _utc(ingested_at)

    _configure(conn)
    _begin(conn)
    write_conflict = False
    write_failed = False
    try:
        identity_id, identity_inserted = _identity_id(
            conn,
            provider=provider_value,
            provider_match_id=provider_match_value,
            canonical_match_id=canonical_value,
            created_at=created_value,
            provenance=identity_provenance_value,
        )
        snapshot_id, state_hash, snapshot_inserted = _snapshot_id(
            conn,
            identity_id=identity_id,
            state=normalized_state,
            observed_at=observed_value,
            ingested_at=ingested_value,
            source_updated_at=source_updated_value,
            provenance=snapshot_provenance_value,
        )
        existing_observation = conn.execute(
            """
            SELECT
              association.id,
              association.observation_event_id,
              association.snapshot_id,
              event.provider,
              event.source,
              event.competition_scope,
              event.season_scope,
              event.poll_run_id,
              event.payload_hash
            FROM schedule_match_observation AS association
            JOIN schedule_observation_event AS event
              ON event.id = association.observation_event_id
            WHERE association.match_identity_id=?
              AND association.observed_at=?
            """,
            (identity_id, observed_value),
        ).fetchone()
        observation_inserted = False
        if existing_observation is not None:
            expected = {
                "provider": provider_value,
                "source": source_value,
                "competition_scope": competition_scope_value,
                "season_scope": season_scope_value,
                "poll_run_id": poll_run_value,
                "payload_hash": payload_hash_value,
                "snapshot_id": snapshot_id,
            }
            if any(
                existing_observation[field] != value
                for field, value in expected.items()
            ):
                raise ScheduleStateConflictError(
                    "schedule state observation conflict"
                )
            observation_id = int(existing_observation["id"])
            observation_event_id = int(
                existing_observation["observation_event_id"]
            )
        else:
            existing_event = conn.execute(
                """
                SELECT id
                FROM schedule_observation_event
                WHERE provider=?
                  AND source=?
                  AND competition_scope=?
                  AND season_scope=?
                  AND observed_at=?
                  AND poll_run_id IS ?
                  AND payload_hash=?
                """,
                (
                    provider_value,
                    source_value,
                    competition_scope_value,
                    season_scope_value,
                    observed_value,
                    poll_run_value,
                    payload_hash_value,
                ),
            ).fetchone()
            if existing_event is None:
                event_cursor = conn.execute(
                    "INSERT INTO schedule_observation_event "
                    "(provider, source, competition_scope, season_scope, "
                    " observed_at, poll_run_id, payload_hash, ingested_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        provider_value,
                        source_value,
                        competition_scope_value,
                        season_scope_value,
                        observed_value,
                        poll_run_value,
                        payload_hash_value,
                        ingested_value,
                    ),
                )
                observation_event_id = int(event_cursor.lastrowid)
            else:
                observation_event_id = int(existing_event["id"])
            association_cursor = conn.execute(
                "INSERT INTO schedule_match_observation "
                "(observation_event_id, match_identity_id, snapshot_id, "
                " observed_at) VALUES (?, ?, ?, ?)",
                (
                    observation_event_id,
                    identity_id,
                    snapshot_id,
                    observed_value,
                ),
            )
            observation_id = int(association_cursor.lastrowid)
            observation_inserted = True
        conn.commit()
    except ScheduleStateError:
        conn.rollback()
        raise
    except sqlite3.IntegrityError:
        conn.rollback()
        write_conflict = True
    except Exception:
        conn.rollback()
        write_failed = True

    if write_conflict:
        raise ScheduleStateConflictError(
            "schedule state write conflict"
        ) from None
    if write_failed:
        raise ScheduleStateDataError("schedule state write failed") from None

    return {
        "identity_id": identity_id,
        "snapshot_id": snapshot_id,
        "observation_id": observation_id,
        "observation_event_id": observation_event_id,
        "state_content_hash": state_hash,
        "identity_inserted": identity_inserted,
        "snapshot_inserted": snapshot_inserted,
        "observation_inserted": observation_inserted,
    }


def record_match_states_batch(
    conn: sqlite3.Connection,
    *,
    provider: str,
    identity_created_at: str,
    identity_provenance: str,
    matches: Sequence[Mapping[str, Any]],
    source: str,
    competition_scope: str,
    season_scope: str,
    observed_at: str,
    poll_run_id: str | None,
    payload_hash: str,
    ingested_at: str,
    _fault_after_associations: int | None = None,
) -> dict[str, int]:
    """Atomically append one response event and all of its match states.

    The private fault argument exists only for offline transaction-boundary
    proof. It raises a fixed project error inside the active transaction.
    """

    provider_value = normalize_provider(provider)
    created_value = _utc(identity_created_at)
    identity_provenance_value = _require_text(identity_provenance)
    source_value = _require_text(source)
    competition_scope_value = _require_text(competition_scope)
    season_scope_value = _require_text(season_scope)
    observed_value = _utc(observed_at)
    poll_run_value = _optional_text(poll_run_id)
    payload_hash_value = _hash_text(payload_hash)
    ingested_value = _utc(ingested_at)
    if (
        not isinstance(matches, Sequence)
        or isinstance(matches, (str, bytes, bytearray))
        or not matches
    ):
        raise ScheduleStateDataError("invalid schedule state batch")
    if (
        _fault_after_associations is not None
        and (
            isinstance(_fault_after_associations, bool)
            or not isinstance(_fault_after_associations, int)
            or _fault_after_associations <= 0
        )
    ):
        raise ScheduleStateDataError("invalid schedule state batch")

    normalized_matches: list[dict[str, Any]] = []
    seen_match_ids: set[str] = set()
    for raw in matches:
        if not isinstance(raw, Mapping):
            raise ScheduleStateDataError("invalid schedule state batch")
        provider_match_id = normalize_provider_match_id(
            raw.get("provider_match_id")
        )
        if provider_match_id in seen_match_ids:
            raise ScheduleStateDataError("invalid schedule state batch")
        state = raw.get("state")
        if not isinstance(state, Mapping):
            raise ScheduleStateDataError("invalid schedule state batch")
        normalized_matches.append(
            {
                "provider_match_id": provider_match_id,
                "canonical_match_id": _positive_int(
                    raw.get("canonical_match_id"),
                    optional=True,
                ),
                "state": _normalize_state(state),
                "source_updated_at": _optional_utc(
                    raw.get("source_updated_at")
                ),
                "snapshot_provenance": _require_text(
                    raw.get("snapshot_provenance")
                ),
            }
        )
        seen_match_ids.add(provider_match_id)

    _configure(conn)
    _begin(conn)
    write_conflict = False
    write_failed = False
    identity_inserted = 0
    snapshot_inserted = 0
    association_inserted = 0
    event_inserted = 0
    try:
        existing_event = conn.execute(
            """
            SELECT id
            FROM schedule_observation_event
            WHERE provider=?
              AND source=?
              AND competition_scope=?
              AND season_scope=?
              AND observed_at=?
              AND poll_run_id IS ?
              AND payload_hash=?
            """,
            (
                provider_value,
                source_value,
                competition_scope_value,
                season_scope_value,
                observed_value,
                poll_run_value,
                payload_hash_value,
            ),
        ).fetchone()
        if existing_event is None:
            event_cursor = conn.execute(
                "INSERT INTO schedule_observation_event "
                "(provider, source, competition_scope, season_scope, "
                " observed_at, poll_run_id, payload_hash, ingested_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    provider_value,
                    source_value,
                    competition_scope_value,
                    season_scope_value,
                    observed_value,
                    poll_run_value,
                    payload_hash_value,
                    ingested_value,
                ),
            )
            observation_event_id = int(event_cursor.lastrowid)
            event_inserted = 1
        else:
            observation_event_id = int(existing_event["id"])

        for item in normalized_matches:
            identity_id, was_identity_inserted = _identity_id(
                conn,
                provider=provider_value,
                provider_match_id=item["provider_match_id"],
                canonical_match_id=item["canonical_match_id"],
                created_at=created_value,
                provenance=identity_provenance_value,
            )
            snapshot_id, _, was_snapshot_inserted = _snapshot_id(
                conn,
                identity_id=identity_id,
                state=item["state"],
                observed_at=observed_value,
                ingested_at=ingested_value,
                source_updated_at=item["source_updated_at"],
                provenance=item["snapshot_provenance"],
            )
            existing_association = conn.execute(
                "SELECT id, observation_event_id, snapshot_id "
                "FROM schedule_match_observation "
                "WHERE match_identity_id=? AND observed_at=?",
                (identity_id, observed_value),
            ).fetchone()
            if existing_association is None:
                conn.execute(
                    "INSERT INTO schedule_match_observation "
                    "(observation_event_id, match_identity_id, snapshot_id, "
                    " observed_at) VALUES (?, ?, ?, ?)",
                    (
                        observation_event_id,
                        identity_id,
                        snapshot_id,
                        observed_value,
                    ),
                )
                association_inserted += 1
            elif (
                int(existing_association["observation_event_id"])
                != observation_event_id
                or int(existing_association["snapshot_id"]) != snapshot_id
            ):
                raise ScheduleStateConflictError(
                    "schedule state observation conflict"
                )
            identity_inserted += int(was_identity_inserted)
            snapshot_inserted += int(was_snapshot_inserted)
            if (
                _fault_after_associations is not None
                and association_inserted == _fault_after_associations
            ):
                raise ScheduleStateDataError(
                    "schedule state batch interrupted"
                )
        conn.commit()
    except ScheduleStateError:
        conn.rollback()
        raise
    except sqlite3.IntegrityError:
        conn.rollback()
        write_conflict = True
    except Exception:
        conn.rollback()
        write_failed = True

    if write_conflict:
        raise ScheduleStateConflictError(
            "schedule state batch conflict"
        ) from None
    if write_failed:
        raise ScheduleStateDataError("schedule state batch failed") from None

    match_count = len(normalized_matches)
    return {
        "identity_inserted": identity_inserted,
        "identity_skipped": match_count - identity_inserted,
        "snapshot_inserted": snapshot_inserted,
        "snapshot_skipped": match_count - snapshot_inserted,
        "event_inserted": event_inserted,
        "event_skipped": 1 - event_inserted,
        "association_inserted": association_inserted,
        "association_skipped": match_count - association_inserted,
    }


def get_current_match_state(
    conn: sqlite3.Connection,
    match_identity_id: int,
) -> dict[str, Any] | None:
    _configure(conn)
    row = conn.execute(
        "SELECT * FROM current_schedule_match_state "
        "WHERE match_identity_id=?",
        (_positive_int(match_identity_id),),
    ).fetchone()
    return dict(row) if row is not None else None


def get_match_state_as_of(
    conn: sqlite3.Connection,
    match_identity_id: int,
    as_of_observed_at: str,
) -> dict[str, Any] | None:
    """Select by event time; observation id is the stable final tie-breaker."""

    _configure(conn)
    row = conn.execute(
        """
        SELECT
          identity.id AS match_identity_id,
          identity.provider,
          identity.provider_match_id,
          identity.canonical_match_id,
          snapshot.id AS snapshot_id,
          snapshot.*,
          association.id AS observation_id,
          event.id AS observation_event_id,
          association.observed_at,
          event.poll_run_id,
          event.payload_hash,
          event.ingested_at AS observation_ingested_at
        FROM schedule_match_identity AS identity
        JOIN schedule_match_observation AS association
          ON association.match_identity_id = identity.id
        JOIN schedule_observation_event AS event
          ON event.id = association.observation_event_id
        JOIN schedule_match_state_snapshot AS snapshot
          ON snapshot.id = association.snapshot_id
        WHERE identity.id=? AND association.observed_at<=?
        ORDER BY association.observed_at DESC, association.id DESC
        LIMIT 1
        """,
        (_positive_int(match_identity_id), _utc(as_of_observed_at)),
    ).fetchone()
    return dict(row) if row is not None else None


def _feature_input_rows(
    conn: sqlite3.Connection,
    *,
    team_id: int,
    target_snapshot_id: int,
    as_of_observed_at: str,
    input_snapshot_ids: Sequence[int],
) -> list[sqlite3.Row]:
    if not input_snapshot_ids:
        raise ScheduleStateDataError("schedule rest feature input is empty")
    rows: list[sqlite3.Row] = []
    seen_identities: set[int] = set()
    previous_kickoff: datetime | None = None
    for raw_snapshot_id in input_snapshot_ids:
        snapshot_id = _positive_int(raw_snapshot_id)
        row = conn.execute(
            "SELECT * FROM schedule_match_state_snapshot WHERE id=?",
            (snapshot_id,),
        ).fetchone()
        if row is None:
            raise ScheduleStateDataError("invalid schedule rest feature input")
        identity_id = int(row["match_identity_id"])
        if identity_id in seen_identities:
            raise ScheduleStateDataError("invalid schedule rest feature input")
        if team_id not in (row["home_team_id"], row["away_team_id"]):
            raise ScheduleStateDataError("invalid schedule rest feature input")
        if row["kickoff_precision"] != "exact" or row["kickoff_at_utc"] is None:
            raise ScheduleStateDataError("invalid schedule rest feature input")
        kickoff = datetime.fromisoformat(
            _utc(row["kickoff_at_utc"]).replace("Z", "+00:00")
        )
        if previous_kickoff is not None and kickoff <= previous_kickoff:
            raise ScheduleStateDataError("invalid schedule rest feature input")
        observed = conn.execute(
            "SELECT 1 FROM schedule_match_observation "
            "WHERE match_identity_id=? AND snapshot_id=? AND observed_at<=? "
            "LIMIT 1",
            (identity_id, snapshot_id, as_of_observed_at),
        ).fetchone()
        if observed is None:
            raise ScheduleStateDataError("invalid schedule rest feature input")
        seen_identities.add(identity_id)
        previous_kickoff = kickoff
        rows.append(row)
    if int(rows[-1]["id"]) != target_snapshot_id:
        raise ScheduleStateDataError("invalid schedule rest feature target")
    return rows


def _record_rest_feature_tx(
    conn: sqlite3.Connection,
    *,
    team_id: int,
    target_snapshot_id: int,
    feature_definition: str,
    feature_version: str,
    as_of_observed_at: str,
    input_snapshot_ids: Sequence[int],
    feature_value: Mapping[str, Any],
    computation_status: str,
    provenance: str,
    computed_at: str,
) -> tuple[int, str, bool]:
    target = conn.execute(
        "SELECT match_identity_id FROM schedule_match_state_snapshot WHERE id=?",
        (target_snapshot_id,),
    ).fetchone()
    if target is None:
        raise ScheduleStateDataError("invalid schedule rest feature target")
    rows = _feature_input_rows(
        conn,
        team_id=team_id,
        target_snapshot_id=target_snapshot_id,
        as_of_observed_at=as_of_observed_at,
        input_snapshot_ids=input_snapshot_ids,
    )
    stable_inputs = [
        {
            "input_ordinal": index,
            "match_identity_id": int(row["match_identity_id"]),
            "snapshot_id": int(row["id"]),
            "state_content_hash": row["state_content_hash"],
            "kickoff_at_utc": row["kickoff_at_utc"],
            "home_team_id": row["home_team_id"],
            "away_team_id": row["away_team_id"],
        }
        for index, row in enumerate(rows)
    ]
    input_set_hash = _sha256(stable_inputs)
    value_json = _canonical_json(feature_value)
    target_identity_id = int(target["match_identity_id"])
    stable_feature = {
        "team_id": team_id,
        "target_match_identity_id": target_identity_id,
        "target_snapshot_id": target_snapshot_id,
        "feature_definition": feature_definition,
        "feature_version": feature_version,
        "input_set_hash": input_set_hash,
        "input_count": len(rows),
        "feature_value_json": value_json,
        "computation_status": computation_status,
        "provenance": provenance,
    }
    feature_payload_hash = _sha256(stable_feature)
    existing_lineage = conn.execute(
        "SELECT * FROM schedule_rest_lineage_set "
        "WHERE team_id=? AND target_match_identity_id=? "
        "AND target_snapshot_id=? AND feature_definition=? "
        "AND feature_version=? AND input_set_hash=?",
        (
            team_id,
            target_identity_id,
            target_snapshot_id,
            feature_definition,
            feature_version,
            input_set_hash,
        ),
    ).fetchone()
    if existing_lineage is None:
        lineage_cursor = conn.execute(
            "INSERT INTO schedule_rest_lineage_set "
            "(team_id, target_match_identity_id, target_snapshot_id, "
            " feature_definition, feature_version, as_of_observed_at, "
            " input_set_hash, expected_input_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                team_id,
                target_identity_id,
                target_snapshot_id,
                feature_definition,
                feature_version,
                as_of_observed_at,
                input_set_hash,
                len(rows),
            ),
        )
        lineage_set_id = int(lineage_cursor.lastrowid)
        lineage_as_of = as_of_observed_at
        for index, row in enumerate(rows):
            conn.execute(
                "INSERT INTO schedule_rest_lineage_input "
                "(lineage_set_id, input_ordinal, input_match_identity_id, "
                " input_snapshot_id) VALUES (?, ?, ?, ?)",
                (
                    lineage_set_id,
                    index,
                    int(row["match_identity_id"]),
                    int(row["id"]),
                ),
            )
    else:
        lineage_set_id = int(existing_lineage["id"])
        lineage_as_of = existing_lineage["as_of_observed_at"]
        expected_lineage = {
            "team_id": team_id,
            "target_match_identity_id": target_identity_id,
            "target_snapshot_id": target_snapshot_id,
            "feature_definition": feature_definition,
            "feature_version": feature_version,
            "input_set_hash": input_set_hash,
            "expected_input_count": len(rows),
        }
        if any(
            existing_lineage[field] != value
            for field, value in expected_lineage.items()
        ):
            raise ScheduleStateConflictError(
                "schedule rest lineage conflict"
            )

    actual_inputs = conn.execute(
        "SELECT input_ordinal, input_match_identity_id, input_snapshot_id "
        "FROM schedule_rest_lineage_input WHERE lineage_set_id=? "
        "ORDER BY input_ordinal",
        (lineage_set_id,),
    ).fetchall()
    expected_inputs = [
        (index, int(row["match_identity_id"]), int(row["id"]))
        for index, row in enumerate(rows)
    ]
    if [tuple(row) for row in actual_inputs] != expected_inputs:
        raise ScheduleStateConflictError("schedule rest feature input conflict")

    existing_feature = conn.execute(
        "SELECT * FROM schedule_rest_feature WHERE lineage_set_id=?",
        (lineage_set_id,),
    ).fetchone()
    inserted = False
    if existing_feature is None:
        cursor = conn.execute(
            "INSERT INTO schedule_rest_feature "
            "(lineage_set_id, team_id, target_match_identity_id, target_snapshot_id, "
            " feature_definition, feature_version, as_of_observed_at, "
            " input_set_hash, input_count, feature_payload_hash, "
            " feature_value_json, computation_status, provenance, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                lineage_set_id,
                team_id,
                target_identity_id,
                target_snapshot_id,
                feature_definition,
                feature_version,
                lineage_as_of,
                input_set_hash,
                len(rows),
                feature_payload_hash,
                value_json,
                computation_status,
                provenance,
                computed_at,
            ),
        )
        feature_id = int(cursor.lastrowid)
        inserted = True
    else:
        expected_feature = stable_feature | {
            "lineage_set_id": lineage_set_id,
            "as_of_observed_at": lineage_as_of,
            "feature_payload_hash": feature_payload_hash,
        }
        if any(
            existing_feature[field] != value
            for field, value in expected_feature.items()
        ):
            raise ScheduleStateConflictError("schedule rest feature conflict")
        feature_id = int(existing_feature["id"])
    return feature_id, input_set_hash, inserted


def record_rest_feature(
    conn: sqlite3.Connection,
    *,
    team_id: int,
    target_snapshot_id: int,
    feature_definition: str,
    feature_version: str,
    as_of_observed_at: str,
    input_snapshot_ids: Sequence[int],
    feature_value: Mapping[str, Any],
    computation_status: str,
    provenance: str,
    computed_at: str,
) -> dict[str, Any]:
    team = _positive_int(team_id)
    target = _positive_int(target_snapshot_id)
    definition = _require_text(feature_definition)
    version = _require_text(feature_version)
    as_of = _utc(as_of_observed_at)
    snapshots = tuple(_positive_int(item) for item in input_snapshot_ids)
    if not isinstance(feature_value, Mapping):
        raise ScheduleStateDataError("invalid schedule rest feature value")
    if computation_status not in {"computed", "excluded", "failed"}:
        raise ScheduleStateDataError("invalid schedule rest feature status")
    provenance_value = _require_text(provenance)
    computed = _utc(computed_at)
    _configure(conn)
    _begin(conn)
    write_conflict = False
    write_failed = False
    try:
        feature_id, input_hash, inserted = _record_rest_feature_tx(
            conn,
            team_id=team,
            target_snapshot_id=target,
            feature_definition=definition,
            feature_version=version,
            as_of_observed_at=as_of,
            input_snapshot_ids=snapshots,
            feature_value=feature_value,
            computation_status=computation_status,
            provenance=provenance_value,
            computed_at=computed,
        )
        conn.commit()
    except ScheduleStateError:
        conn.rollback()
        raise
    except sqlite3.IntegrityError:
        conn.rollback()
        write_conflict = True
    except Exception:
        conn.rollback()
        write_failed = True

    if write_conflict:
        raise ScheduleStateConflictError(
            "schedule rest feature write conflict"
        ) from None
    if write_failed:
        raise ScheduleStateDataError(
            "schedule rest feature write failed"
        ) from None
    return {
        "feature_id": feature_id,
        "input_set_hash": input_hash,
        "inserted": inserted,
    }


def _states_as_of(
    conn: sqlite3.Connection,
    as_of_observed_at: str,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        WITH ranked AS (
          SELECT
            snapshot.*,
            observation.observed_at,
            ROW_NUMBER() OVER (
              PARTITION BY observation.match_identity_id
              ORDER BY observation.observed_at DESC, observation.id DESC
            ) AS state_rank
          FROM schedule_match_observation AS observation
          JOIN schedule_match_state_snapshot AS snapshot
            ON snapshot.id = observation.snapshot_id
          WHERE observation.observed_at <= ?
        )
        SELECT * FROM ranked WHERE state_rank=1
        """,
        (as_of_observed_at,),
    ).fetchall()


def build_observed_rest_features_as_of(
    conn: sqlite3.Connection,
    *,
    as_of_observed_at: str,
    computed_at: str,
    _fault_after_features: int | None = None,
) -> dict[str, int]:
    """Build append-only observed rest lineage from point-in-time current state."""

    as_of = _utc(as_of_observed_at)
    computed = _utc(computed_at)
    if (
        _fault_after_features is not None
        and (
            isinstance(_fault_after_features, bool)
            or not isinstance(_fault_after_features, int)
            or _fault_after_features <= 0
        )
    ):
        raise ScheduleStateDataError("invalid schedule rest feature batch")
    _configure(conn)
    rows = _states_as_of(conn, as_of)
    by_team: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        if (
            row["finished"] != 1
            or row["cancelled"] != 0
            or row["kickoff_precision"] != "exact"
            or row["kickoff_at_utc"] is None
            or row["competition_verified"] != 1
            or row["competition_class"] not in COMPETITIVE_CLASSES
        ):
            continue
        for team_id in (row["home_team_id"], row["away_team_id"]):
            if team_id is not None:
                by_team[int(team_id)].append(row)

    _begin(conn)
    inserted = 0
    skipped = 0
    write_conflict = False
    write_failed = False
    processed = 0
    try:
        for team_id in sorted(by_team):
            timeline = sorted(
                by_team[team_id],
                key=lambda row: (
                    _utc(row["kickoff_at_utc"]),
                    int(row["match_identity_id"]),
                ),
            )
            previous_kickoff: datetime | None = None
            for index, row in enumerate(timeline):
                kickoff = datetime.fromisoformat(
                    _utc(row["kickoff_at_utc"]).replace("Z", "+00:00")
                )
                if previous_kickoff is not None and kickoff <= previous_kickoff:
                    raise ScheduleStateDataError(
                        "team schedule kickoff order is not strict"
                    )
                earlier = timeline[:index]
                if previous_kickoff is None:
                    gap_hours = None
                    previous_snapshot_id = None
                else:
                    gap_hours = (
                        kickoff - previous_kickoff
                    ).total_seconds() / 3600.0
                    previous_snapshot_id = int(earlier[-1]["id"])
                value = {
                    "scope": "observed_historical",
                    "previous_snapshot_id": previous_snapshot_id,
                    "kickoff_gap_hours": gap_hours,
                    "calendar_gap_days": (
                        gap_hours / 24.0 if gap_hours is not None else None
                    ),
                    "short_gap_72h": (
                        gap_hours < 72.0 if gap_hours is not None else None
                    ),
                    "short_gap_96h": (
                        gap_hours < 96.0 if gap_hours is not None else None
                    ),
                    "matches_last_7d": sum(
                        1
                        for prior in earlier
                        if 0
                        < (
                            kickoff
                            - datetime.fromisoformat(
                                _utc(prior["kickoff_at_utc"]).replace(
                                    "Z", "+00:00"
                                )
                            )
                        ).total_seconds()
                        <= 7 * 86400
                    ),
                    "matches_last_14d": sum(
                        1
                        for prior in earlier
                        if 0
                        < (
                            kickoff
                            - datetime.fromisoformat(
                                _utc(prior["kickoff_at_utc"]).replace(
                                    "Z", "+00:00"
                                )
                            )
                        ).total_seconds()
                        <= 14 * 86400
                    ),
                }
                _, _, was_inserted = _record_rest_feature_tx(
                    conn,
                    team_id=team_id,
                    target_snapshot_id=int(row["id"]),
                    feature_definition=REST_FEATURE_DEFINITION,
                    feature_version=REST_FEATURE_VERSION,
                    as_of_observed_at=as_of,
                    input_snapshot_ids=tuple(
                        int(item["id"]) for item in timeline[: index + 1]
                    ),
                    feature_value=value,
                    computation_status="computed",
                    provenance=REST_FEATURE_PROVENANCE,
                    computed_at=computed,
                )
                if was_inserted:
                    inserted += 1
                else:
                    skipped += 1
                processed += 1
                if (
                    _fault_after_features is not None
                    and processed == _fault_after_features
                ):
                    raise ScheduleStateDataError(
                        "schedule rest feature batch interrupted"
                    )
                previous_kickoff = kickoff
        conn.commit()
    except ScheduleStateError:
        conn.rollback()
        raise
    except sqlite3.IntegrityError:
        conn.rollback()
        write_conflict = True
    except Exception:
        conn.rollback()
        write_failed = True

    if write_conflict:
        raise ScheduleStateConflictError(
            "schedule rest feature batch conflict"
        ) from None
    if write_failed:
        raise ScheduleStateDataError(
            "schedule rest feature batch failed"
        ) from None
    return {"inserted": inserted, "skipped": skipped}
