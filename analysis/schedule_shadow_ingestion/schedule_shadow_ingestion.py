"""Offline-only schedule shadow ingestion with durable local recovery.

The module deliberately has no provider transport.  Its only accepted input is
an immutable, caller-supplied artifact and a guarded temporary database copy.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import stat
from collections import defaultdict
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from analysis.schedule_state_migration_trial import (
    schedule_state_migration_trial as trial,
)
from backend.schedules import fotmob_schedule
from backend.schedules import state as schedule
from backend.schedules.pagination import inspect_known_pagination


MAX_SHADOW_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_SHADOW_CONTROL_BYTES = 1024 * 1024
MAX_SHADOW_JSON_DEPTH = 64
MAX_SHADOW_JSON_NODES = 500_000

FAULT_AFTER_ARTIFACT_VALIDATED = "AFTER_ARTIFACT_VALIDATED"
FAULT_STATE_TRANSACTION_MID = "STATE_TRANSACTION_MID"
FAULT_AFTER_STATE_COMMIT_BEFORE_MANIFEST = (
    "AFTER_STATE_COMMIT_BEFORE_MANIFEST"
)
FAULT_AFTER_STATE_APPLIED = "AFTER_STATE_APPLIED"
FAULT_FEATURE_TRANSACTION_MID = "FEATURE_TRANSACTION_MID"
FAULT_AFTER_FEATURE_COMMIT_BEFORE_MANIFEST = (
    "AFTER_FEATURE_COMMIT_BEFORE_MANIFEST"
)

_FAULT_POINTS = frozenset(
    {
        FAULT_AFTER_ARTIFACT_VALIDATED,
        FAULT_STATE_TRANSACTION_MID,
        FAULT_AFTER_STATE_COMMIT_BEFORE_MANIFEST,
        FAULT_AFTER_STATE_APPLIED,
        FAULT_FEATURE_TRANSACTION_MID,
        FAULT_AFTER_FEATURE_COMMIT_BEFORE_MANIFEST,
    }
)
_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}")
_HEX64_PATTERN = re.compile(r"[0-9a-f]{64}")
_MANIFEST_SCHEMA_VERSION = 2
_SESSION_SCHEMA_VERSION = 1
_STATE_PHASES = (
    "NEW",
    "ARTIFACT_VALIDATED",
    "STATE_APPLIED",
    "FEATURES_APPLIED",
    "COMPLETED",
)
_LEGAL_FORWARD = {
    "NEW": "ARTIFACT_VALIDATED",
    "ARTIFACT_VALIDATED": "STATE_APPLIED",
    "STATE_APPLIED": "FEATURES_APPLIED",
    "FEATURES_APPLIED": "COMPLETED",
}
_KNOWN_DB_SUFFIXES = frozenset({"-wal", "-shm", "-journal"})


class ShadowError(RuntimeError):
    """Base class for fixed-surface shadow errors."""


class ShadowArtifactError(ShadowError):
    """The immutable artifact or its envelope failed validation."""


class ShadowSessionError(ShadowError):
    """The temporary-copy session failed its safety contract."""


class ShadowIngestionError(ShadowError):
    """A shadow transaction failed closed."""


class ShadowRunConflictError(ShadowError):
    """A run id was reused with a different immutable identity."""


class ShadowInjectedCrash(ShadowError):
    """A permanent-test fault boundary interrupted the run."""


@dataclass(frozen=True)
class ArtifactEnvelope:
    artifact_sha256: str
    payload_canonical_sha256: str
    completeness_evidence_sha256: str
    provider: str
    source_operation: str
    competition_id: int
    requested_season: str
    observed_at: str
    artifact_schema_version: str
    completeness_status: str
    completeness_evidence: Mapping[str, Any]
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class ValidatedScheduleBatch:
    envelope: ArtifactEnvelope
    rows: tuple[fotmob_schedule.NormalizedScheduleRow, ...]


@dataclass(frozen=True)
class PreparedShadowSession:
    session_id: str
    capability: str
    descriptor_path: Path


@dataclass(frozen=True)
class _SessionBinding:
    session_id: str
    capability: str
    descriptor_path: Path
    run_directory: Path
    workspace: Path
    runs_directory: Path
    lock_path: Path
    database_path: Path
    recovery_path: Path
    source_path: Path
    descriptor: Mapping[str, Any]


_SESSIONS: dict[str, _SessionBinding] = {}


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fixed_artifact_error() -> ShadowArtifactError:
    return ShadowArtifactError("artifact validation failed")


def _fixed_session_error(message: str = "shadow session validation failed") -> ShadowSessionError:
    return ShadowSessionError(message)


def _fixed_ingestion_error() -> ShadowIngestionError:
    return ShadowIngestionError("offline shadow ingestion failed")


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(_: str) -> Any:
    raise ValueError("non-finite JSON number")


def _check_json_depth(raw: bytes) -> None:
    depth = 0
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
            continue
        if byte == 0x22:
            in_string = True
        elif byte in (0x5B, 0x7B):
            depth += 1
            if depth > MAX_SHADOW_JSON_DEPTH:
                raise ValueError("JSON nesting limit")
        elif byte in (0x5D, 0x7D):
            depth -= 1
            if depth < 0:
                raise ValueError("invalid JSON nesting")
    if depth != 0 or in_string:
        raise ValueError("invalid JSON nesting")


def _check_json_nodes(value: Any) -> None:
    stack = [value]
    count = 0
    while stack:
        item = stack.pop()
        count += 1
        if count > MAX_SHADOW_JSON_NODES:
            raise ValueError("JSON node limit")
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)


def _strict_json_loads(raw: bytes, *, top_object: bool = True) -> Any:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("UTF-8 BOM is forbidden")
    _check_json_depth(raw)
    text = raw.decode("utf-8", errors="strict")
    value = json.loads(
        text,
        object_pairs_hook=_strict_pairs,
        parse_constant=_reject_constant,
    )
    _check_json_nodes(value)
    if top_object and not isinstance(value, dict):
        raise ValueError("top-level object required")
    return value


def _read_regular_file_once(
    path: Path | str,
    *,
    maximum_bytes: int,
    required_mode: int | None = None,
    allow_empty: bool = False,
) -> tuple[bytes, os.stat_result]:
    candidate = Path(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(candidate, flags)
    try:
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or mode & 0o022
            or (required_mode is not None and mode != required_mode)
            or (before.st_size <= 0 and not allow_empty)
            or before.st_size > maximum_bytes
        ):
            raise ValueError("unsafe file")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError("short read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ValueError("file grew")
        after = os.fstat(descriptor)
        stable = (
            "st_dev",
            "st_ino",
            "st_uid",
            "st_mode",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
        )
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            raise ValueError("file changed")
        return b"".join(chunks), after
    finally:
        os.close(descriptor)


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid text")
    return value.strip()


def _positive_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("invalid positive integer")
    return value


def _hex64(value: Any) -> str:
    if not isinstance(value, str) or _HEX64_PATTERN.fullmatch(value) is None:
        raise ValueError("invalid digest")
    return value


def _plain_json(value: Any) -> bool:
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if not all(isinstance(key, str) for key in item):
                return False
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
        elif item is not None and not isinstance(item, (str, int, float, bool)):
            return False
        elif isinstance(item, float) and not (item == item and abs(item) != float("inf")):
            return False
    return True


def load_artifact_envelope(
    artifact_path: Path | str,
    *,
    expected_sha256: str,
    provider: str,
    source_operation: str,
    competition_id: int,
    requested_season: str,
    observed_at: str,
    artifact_schema_version: str,
    completeness_status: str,
    completeness_evidence: Mapping[str, Any],
) -> ArtifactEnvelope:
    """Hash and strictly parse one immutable regular artifact exactly once."""

    result: ArtifactEnvelope | None = None
    try:
        expected = _hex64(expected_sha256)
        raw, _ = _read_regular_file_once(
            artifact_path,
            maximum_bytes=MAX_SHADOW_ARTIFACT_BYTES,
        )
        if not hmac.compare_digest(_sha256_bytes(raw), expected):
            raise ValueError("artifact digest mismatch")
        payload = _strict_json_loads(raw)
        result = ArtifactEnvelope(
            artifact_sha256=expected,
            payload_canonical_sha256=_sha256_bytes(
                _canonical_json(payload)
            ),
            completeness_evidence_sha256=_sha256_bytes(
                _canonical_json(dict(completeness_evidence))
            ),
            provider=schedule.normalize_provider(provider),
            source_operation=_text(source_operation).casefold(),
            competition_id=_positive_int(competition_id),
            requested_season=_text(requested_season),
            observed_at=schedule._utc(observed_at),
            artifact_schema_version=_text(artifact_schema_version),
            completeness_status=_text(completeness_status),
            completeness_evidence=dict(completeness_evidence),
            payload=payload,
        )
    except Exception:
        pass
    if result is None:
        raise _fixed_artifact_error() from None
    return result


def validate_artifact_envelope(
    envelope: ArtifactEnvelope,
) -> ValidatedScheduleBatch:
    """Cross-check raw provider truth, completeness evidence, and normalization."""

    result: ValidatedScheduleBatch | None = None
    try:
        if not isinstance(envelope, ArtifactEnvelope) or not _plain_json(
            envelope.payload
        ):
            raise ValueError("invalid envelope")
        if (
            _sha256_bytes(_canonical_json(envelope.payload))
            != envelope.payload_canonical_sha256
            or _sha256_bytes(
                _canonical_json(dict(envelope.completeness_evidence))
            )
            != envelope.completeness_evidence_sha256
        ):
            raise ValueError("envelope content changed")
        if (
            envelope.provider != "fotmob"
            or envelope.artifact_schema_version
            != "cwc_schedule_raw_projection_v1"
            or envelope.completeness_status != "COMPLETE"
        ):
            raise ValueError("unsupported envelope")
        evidence = envelope.completeness_evidence
        expected_keys = frozenset(
            {
                "competition_identity_verified",
                "competition_name",
                "competition_class",
                "competition_class_verified",
                "returned_season",
                "fixture_schema_valid",
                "fixture_count",
                "pagination_status",
                "pagination_detected_evidence",
                "pagination_unresolved_evidence",
                "pagination_unknown_evidence",
                "observation_time_provenance",
            }
        )
        if not isinstance(evidence, dict) or frozenset(evidence) != expected_keys:
            raise ValueError("invalid evidence")
        pagination = inspect_known_pagination(envelope.payload)
        if (
            evidence["competition_identity_verified"] is not True
            or evidence["competition_class_verified"] is not True
            or evidence["fixture_schema_valid"] is not True
            or evidence["competition_class"] not in schedule.COMPETITIVE_CLASSES
            or evidence["returned_season"] != envelope.requested_season
            or evidence["pagination_status"] != pagination["status"]
            or evidence["pagination_detected_evidence"]
            != pagination["detected_evidence"]
            or evidence["pagination_unresolved_evidence"]
            != pagination["unresolved_evidence"]
            or evidence["pagination_unknown_evidence"]
            != pagination.get("unknown_evidence", [])
            or pagination["status"] != "NOT_DETECTED"
            or not isinstance(evidence["fixture_count"], int)
            or isinstance(evidence["fixture_count"], bool)
            or evidence["fixture_count"] <= 0
            or not _text(evidence["observation_time_provenance"])
        ):
            raise ValueError("invalid completeness")
        rows = fotmob_schedule.normalize_raw_schedule_payload(
            envelope.payload,
            expected_competition_id=envelope.competition_id,
            expected_competition_name=_text(evidence["competition_name"]),
            requested_season=envelope.requested_season,
            competition_class=_text(evidence["competition_class"]),
            competition_class_verified=True,
            artifact_schema_version=envelope.artifact_schema_version,
        )
        if len(rows) != evidence["fixture_count"]:
            raise ValueError("fixture count mismatch")
        result = ValidatedScheduleBatch(envelope=envelope, rows=rows)
    except Exception:
        pass
    if result is None:
        raise _fixed_artifact_error() from None
    return result


def _file_fingerprint(path: Path) -> dict[str, Any]:
    raw, metadata = _read_regular_file_once(
        path,
        maximum_bytes=max(MAX_SHADOW_ARTIFACT_BYTES, 1024 * 1024 * 1024),
        required_mode=0o600,
        allow_empty=True,
    )
    return {
        "device": int(metadata.st_dev),
        "inode": int(metadata.st_ino),
        "owner_uid": int(metadata.st_uid),
        "mode": stat.S_IMODE(metadata.st_mode),
        "link_count": int(metadata.st_nlink),
        "size": int(metadata.st_size),
        "mtime_ns": int(metadata.st_mtime_ns),
        "sha256": _sha256_bytes(raw),
    }


def _directory_fingerprint(path: Path) -> dict[str, int]:
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ValueError("unsafe directory")
    return {
        "device": int(metadata.st_dev),
        "inode": int(metadata.st_ino),
        "owner_uid": int(metadata.st_uid),
        "mode": stat.S_IMODE(metadata.st_mode),
    }


def _companion_fingerprints(path: Path) -> dict[str, Any]:
    entries: dict[str, Any] = {}
    with os.scandir(path.parent) as scan:
        for entry in scan:
            if entry.name == path.name or not entry.name.startswith(path.name):
                continue
            suffix = entry.name[len(path.name) :]
            if suffix not in _KNOWN_DB_SUFFIXES:
                raise ValueError("unknown database companion")
            candidate = path.parent / entry.name
            entries[entry.name] = _file_fingerprint(candidate)
    return dict(sorted(entries.items()))


def _database_binding(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve(strict=True)),
        "main": _file_fingerprint(path),
        "companions": _companion_fingerprints(path),
    }


def _descriptor_unsigned(document: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in document.items() if key != "signature"}


def _sign_document(document: Mapping[str, Any], capability: str) -> str:
    return hmac.new(
        bytes.fromhex(capability),
        _canonical_json(_descriptor_unsigned(document)),
        hashlib.sha256,
    ).hexdigest()


def _atomic_private_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _directory_fingerprint(path.parent)
    temporary = path.parent / f".{path.name}.tmp-{secrets.token_hex(12)}"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short control write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    parent_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _write_signed_document(
    path: Path,
    document: Mapping[str, Any],
    capability: str,
) -> None:
    unsigned = _descriptor_unsigned(document)
    signed = unsigned | {"signature": _sign_document(unsigned, capability)}
    _atomic_private_write(path, _canonical_json(signed) + b"\n")


def _read_signed_document(
    path: Path,
    capability: str,
) -> dict[str, Any]:
    raw, _ = _read_regular_file_once(
        path,
        maximum_bytes=MAX_SHADOW_CONTROL_BYTES,
        required_mode=0o600,
    )
    document = _strict_json_loads(raw)
    signature = document.get("signature")
    if not isinstance(signature, str) or not hmac.compare_digest(
        signature,
        _sign_document(document, capability),
    ):
        raise ValueError("control signature mismatch")
    return document


def _descriptor_payload(
    prepared: trial.PreparedTrialCopy,
    *,
    session_id: str,
    capability: str,
    workspace: Path,
    runs_directory: Path,
    lock_path: Path,
    descriptor_path: Path,
    revision: int,
) -> dict[str, Any]:
    return {
        "schema_version": _SESSION_SCHEMA_VERSION,
        "session_id": session_id,
        "capability_sha256": _sha256_bytes(bytes.fromhex(capability)),
        "creator_pid": os.getpid(),
        "creator_uid": os.getuid(),
        "run_directory": str(prepared.run_directory.resolve(strict=True)),
        "run_directory_fingerprint": _directory_fingerprint(
            prepared.run_directory
        ),
        "workspace": str(workspace.resolve(strict=True)),
        "workspace_fingerprint": _directory_fingerprint(workspace),
        "runs_directory": str(runs_directory.resolve(strict=True)),
        "runs_directory_fingerprint": _directory_fingerprint(runs_directory),
        "lock_path": str(lock_path.resolve(strict=True)),
        "lock_fingerprint": _file_fingerprint(lock_path),
        "descriptor_path": str(descriptor_path.absolute()),
        "source": _database_binding(prepared.source.path),
        "database": _database_binding(prepared.destination),
        "recovery": _database_binding(prepared.recovery_image),
        "revision": revision,
    }


def _safe_private_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise ValueError("unsafe private file")


def open_shadow_session(
    prepared: trial.PreparedTrialCopy,
) -> PreparedShadowSession:
    """Consume a process-bound PreparedTrialCopy and issue a durable capability."""

    result: PreparedShadowSession | None = None
    try:
        if not isinstance(prepared, trial.PreparedTrialCopy):
            raise ValueError("prepared copy required")
        trial.migrate_prepared_trial_copy(prepared)
        workspace = prepared.run_directory / "shadow"
        workspace.mkdir(mode=0o700)
        runs_directory = workspace / "runs"
        runs_directory.mkdir(mode=0o700)
        lock_path = workspace / "session.lock"
        lock_descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        os.close(lock_descriptor)
        session_id = secrets.token_hex(16)
        capability = secrets.token_hex(32)
        descriptor_path = workspace / "session.json"
        document = _descriptor_payload(
            prepared,
            session_id=session_id,
            capability=capability,
            workspace=workspace,
            runs_directory=runs_directory,
            lock_path=lock_path,
            descriptor_path=descriptor_path,
            revision=0,
        )
        _write_signed_document(descriptor_path, document, capability)
        session = PreparedShadowSession(
            session_id=session_id,
            capability=capability,
            descriptor_path=descriptor_path,
        )
        binding = _binding_from_descriptor(session)
        _SESSIONS[session_id] = binding
        result = session
    except Exception:
        pass
    if result is None:
        raise _fixed_session_error() from None
    return result


def _fingerprint_matches(
    actual: Mapping[str, Any],
    expected: Mapping[str, Any],
    *,
    allow_mtime_change: bool = False,
) -> bool:
    ignored = {"mtime_ns"} if allow_mtime_change else set()
    return all(
        actual.get(key) == value
        for key, value in expected.items()
        if key not in ignored
    ) and frozenset(actual) == frozenset(expected)


def _database_binding_matches(
    actual: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> bool:
    if (
        actual.get("path") != expected.get("path")
        or not _fingerprint_matches(actual["main"], expected["main"])
    ):
        return False
    # WAL/SHM are connection-lifecycle files, so a clean process exit may
    # remove them and a later SQLite open may recreate them with new inodes.
    # The durable boundary is the unchanged main file plus a zero-byte WAL;
    # a committed WAL or any rollback journal is never accepted.
    for name, fingerprint in actual["companions"].items():
        if name.endswith("-journal"):
            return False
        if name.endswith("-wal") and (
            fingerprint["size"] != 0
            or fingerprint["sha256"] != hashlib.sha256(b"").hexdigest()
        ):
            return False
        if name.endswith("-shm") and fingerprint["size"] != 32_768:
            return False
    for name, fingerprint in expected["companions"].items():
        if name.endswith("-journal"):
            return False
        if name.endswith("-wal") and fingerprint["size"] != 0:
            return False
    return True


def _assert_binding_snapshot(
    document: Mapping[str, Any],
    *,
    include_database: bool = True,
) -> None:
    if document.get("schema_version") != _SESSION_SCHEMA_VERSION:
        raise ValueError("descriptor schema")
    run_directory = Path(_text(document["run_directory"]))
    workspace = Path(_text(document["workspace"]))
    runs_directory = Path(_text(document["runs_directory"]))
    lock_path = Path(_text(document["lock_path"]))
    descriptor_path = Path(_text(document["descriptor_path"]))
    if (
        workspace != run_directory / "shadow"
        or runs_directory != workspace / "runs"
        or lock_path != workspace / "session.lock"
        or descriptor_path != workspace / "session.json"
        or Path(document["database"]["path"]) != run_directory / "trial.db"
        or Path(document["recovery"]["path"]) != run_directory / "recovery.db"
    ):
        raise ValueError("descriptor path binding")
    if {entry.name for entry in workspace.iterdir()} != {
        "runs",
        "session.json",
        "session.lock",
    }:
        raise ValueError("descriptor workspace pathset")
    for key in (
        "run_directory",
        "workspace",
        "runs_directory",
    ):
        path = Path(_text(document[key]))
        expected = document[f"{key}_fingerprint"]
        if _directory_fingerprint(path) != expected:
            raise ValueError("directory changed")
    database_keys = (
        ("source", "database", "recovery")
        if include_database
        else ("source", "recovery")
    )
    for key in database_keys:
        expected = document[key]
        path = Path(_text(expected["path"]))
        actual = _database_binding(path)
        if not _database_binding_matches(actual, expected):
            raise ValueError("database changed")
    if include_database:
        database_path = Path(document["database"]["path"])
        conn = sqlite3.connect(
            f"{database_path.as_uri()}?mode=ro&immutable=1",
            uri=True,
        )
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA recursive_triggers = ON")
            schedule.assert_schedule_state_schema(conn)
            ledger = [
                int(row[0])
                for row in conn.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            ]
            if ledger != [1, 2, 3]:
                raise ValueError("descriptor schema ledger")
        finally:
            conn.close()
    for key in ("lock_path", "descriptor_path"):
        _safe_private_file(Path(_text(document[key])))
    if _file_fingerprint(lock_path) != document.get("lock_fingerprint"):
        raise ValueError("descriptor lock changed")


def _binding_from_descriptor(
    session: PreparedShadowSession,
) -> _SessionBinding:
    capability = _hex64(session.capability)
    document = _read_signed_document(session.descriptor_path, capability)
    if (
        document.get("session_id") != session.session_id
        or document.get("capability_sha256")
        != _sha256_bytes(bytes.fromhex(capability))
        or Path(document.get("descriptor_path", "")) != session.descriptor_path
        or document.get("creator_uid") != os.getuid()
    ):
        raise ValueError("descriptor identity")
    _assert_binding_snapshot(document)
    runs_directory = Path(document["runs_directory"])
    for path in runs_directory.iterdir():
        if (
            not path.name.endswith(".json")
            or _RUN_ID_PATTERN.fullmatch(path.name[:-5]) is None
        ):
            raise ValueError("manifest pathset")
        manifest = _read_signed_document(path, capability)
        if manifest.get("run_id") != path.name[:-5]:
            raise ValueError("manifest identity")
    return _SessionBinding(
        session_id=session.session_id,
        capability=capability,
        descriptor_path=session.descriptor_path,
        run_directory=Path(document["run_directory"]),
        workspace=Path(document["workspace"]),
        runs_directory=Path(document["runs_directory"]),
        lock_path=Path(document["lock_path"]),
        database_path=Path(document["database"]["path"]),
        recovery_path=Path(document["recovery"]["path"]),
        source_path=Path(document["source"]["path"]),
        descriptor=document,
    )


def reopen_shadow_session(
    descriptor_path: Path | str,
    capability: str,
) -> PreparedShadowSession:
    """Reconstruct a durable session without trusting a creator process PID."""

    result: PreparedShadowSession | None = None
    try:
        path = Path(descriptor_path).resolve(strict=True)
        capability_value = _hex64(capability)
        document = _read_signed_document(path, capability_value)
        session = PreparedShadowSession(
            session_id=_text(document["session_id"]),
            capability=capability_value,
            descriptor_path=path,
        )
        binding = _binding_from_descriptor(session)
        _SESSIONS[session.session_id] = binding
        result = session
    except Exception:
        pass
    if result is None:
        raise _fixed_session_error() from None
    return result


def _session_binding(session: PreparedShadowSession) -> _SessionBinding:
    result: _SessionBinding | None = None
    try:
        if not isinstance(session, PreparedShadowSession):
            raise ValueError("invalid session")
        binding = _binding_from_descriptor(session)
        _SESSIONS[session.session_id] = binding
        result = binding
    except Exception:
        pass
    if result is None:
        raise _fixed_session_error() from None
    return result


def _refresh_session(session: PreparedShadowSession) -> _SessionBinding:
    binding = _SESSIONS.get(session.session_id)
    if (
        binding is None
        or binding.capability != session.capability
        or binding.descriptor_path != session.descriptor_path
    ):
        raise _fixed_session_error() from None
    try:
        document = _read_signed_document(
            binding.descriptor_path,
            session.capability,
        )
        _assert_binding_snapshot(document, include_database=False)
        if (
            document.get("session_id") != session.session_id
            or document.get("database", {}).get("path")
            != str(binding.database_path)
        ):
            raise ValueError("descriptor changed")
    except Exception:
        raise _fixed_session_error() from None
    document = dict(document)
    document["database"] = _database_binding(binding.database_path)
    document["source"] = _database_binding(binding.source_path)
    document["recovery"] = _database_binding(binding.recovery_path)
    document["revision"] = int(document["revision"]) + 1
    document["creator_pid"] = os.getpid()
    _write_signed_document(binding.descriptor_path, document, session.capability)
    refreshed = _binding_from_descriptor(session)
    _SESSIONS[session.session_id] = refreshed
    return refreshed


@contextmanager
def _session_lock(session: PreparedShadowSession) -> Iterator[None]:
    binding = _session_binding(session)
    flags = os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(binding.lock_path, flags)
    try:
        metadata = os.fstat(descriptor)
        expected = binding.descriptor["lock_fingerprint"]
        if (
            int(metadata.st_dev) != expected["device"]
            or int(metadata.st_ino) != expected["inode"]
            or metadata.st_uid != expected["owner_uid"]
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise _fixed_session_error() from None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise _fixed_session_error(
                "shadow session is already active"
            ) from None
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _run_id(value: Any) -> str:
    if not isinstance(value, str) or _RUN_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("invalid run id")
    return value


def _manifest_path(session: PreparedShadowSession, run_id: str) -> Path:
    binding = _session_binding(session)
    return binding.runs_directory / f"{_run_id(run_id)}.json"


def _manifest_identity(
    envelope: ArtifactEnvelope,
    run_id: str,
) -> dict[str, Any]:
    return {
        "manifest_schema_version": _MANIFEST_SCHEMA_VERSION,
        "run_id": _run_id(run_id),
        "artifact_sha256": envelope.artifact_sha256,
        "payload_canonical_sha256": envelope.payload_canonical_sha256,
        "completeness_evidence_sha256": (
            envelope.completeness_evidence_sha256
        ),
        "provider": envelope.provider,
        "source_operation": envelope.source_operation,
        "competition_id": envelope.competition_id,
        "requested_season": envelope.requested_season,
        "observed_at": envelope.observed_at,
        "artifact_schema_version": envelope.artifact_schema_version,
    }


def _manifest_capability_for_path(path: Path) -> str:
    descriptor_path = path.parent.parent / "session.json"
    for binding in _SESSIONS.values():
        if binding.descriptor_path == descriptor_path:
            return binding.capability
    raise ValueError("manifest session unavailable")


def _write_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    try:
        capability = _manifest_capability_for_path(path)
        if path.exists():
            _read_signed_document(path, capability)
        _write_signed_document(path, dict(manifest), capability)
    except Exception:
        raise _fixed_session_error() from None


def read_shadow_run_manifest(
    session: PreparedShadowSession,
    run_id: str,
) -> dict[str, Any]:
    result: dict[str, Any] | None = None
    try:
        path = _manifest_path(session, run_id)
        document = _read_signed_document(path, session.capability)
        if document.get("run_id") != _run_id(run_id):
            raise ValueError("manifest identity")
        result = document
    except Exception:
        pass
    if result is None:
        raise _fixed_session_error() from None
    return result


def _validate_phase_transition(current: str, target: str) -> None:
    if target == "FAILED" and current in _STATE_PHASES[:-1]:
        return
    if _LEGAL_FORWARD.get(current) != target:
        raise _fixed_session_error("illegal shadow phase transition") from None


def _new_manifest(envelope: ArtifactEnvelope, run_id: str) -> dict[str, Any]:
    return {
        **_manifest_identity(envelope, run_id),
        "status": "RUNNING",
        "started": True,
        "completed": False,
        "failed": False,
        "resume_count": 0,
        "phase": "NEW",
        "last_successful_phase": "NEW",
        "history": [],
        "normalized_match_count": None,
    }


def _advance_manifest(
    session: PreparedShadowSession,
    manifest: Mapping[str, Any],
    target: str,
    **updates: Any,
) -> dict[str, Any]:
    current = _text(manifest["phase"])
    _validate_phase_transition(current, target)
    result = dict(manifest)
    result.update(updates)
    result["phase"] = target
    result["failed"] = target == "FAILED"
    result["completed"] = target == "COMPLETED"
    result["status"] = (
        "COMPLETED"
        if target == "COMPLETED"
        else "FAILED"
        if target == "FAILED"
        else "RUNNING"
    )
    if target != "FAILED":
        result["last_successful_phase"] = target
        result["history"] = [*manifest.get("history", []), target]
    _write_manifest(_manifest_path(session, result["run_id"]), result)
    return result


def _failed_manifest(
    session: PreparedShadowSession,
    manifest: Mapping[str, Any],
    error_code: str,
) -> dict[str, Any]:
    current = _text(manifest["phase"])
    if current == "FAILED":
        result = dict(manifest)
        result["safe_error_code"] = error_code
        _write_manifest(_manifest_path(session, result["run_id"]), result)
        return result
    return _advance_manifest(
        session,
        manifest,
        "FAILED",
        last_successful_phase=manifest.get("last_successful_phase", current),
        safe_error_code=error_code,
    )


def _retry_manifest(
    session: PreparedShadowSession,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    if manifest.get("phase") != "FAILED":
        return dict(manifest)
    last = manifest.get("last_successful_phase")
    if last not in _STATE_PHASES[:-1]:
        raise _fixed_session_error() from None
    result = dict(manifest)
    result.update(
        {
            "phase": last,
            "status": "RUNNING",
            "failed": False,
            "completed": False,
            "resume_count": int(manifest.get("resume_count", 0)) + 1,
            "history": [*manifest.get("history", []), f"RETRY:{last}"],
        }
    )
    result.pop("safe_error_code", None)
    _write_manifest(_manifest_path(session, result["run_id"]), result)
    return result


def _connect_writable(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA recursive_triggers = ON")
    schedule.assert_schedule_state_schema(conn)
    return conn


def _state_inputs(validated: ValidatedScheduleBatch) -> list[dict[str, Any]]:
    return [row.as_state_input() for row in validated.rows]


def _state_row_matches(
    snapshot: sqlite3.Row,
    row: fotmob_schedule.NormalizedScheduleRow,
) -> bool:
    expected = schedule._normalize_state(row.state)
    return (
        snapshot["state_content_hash"] == row.state_content_hash
        and all(snapshot[field] == expected[field] for field in expected)
        and snapshot["source_updated_at"] == row.source_updated_at
        and snapshot["provenance"] == row.snapshot_provenance
    )


def _event_row(
    conn: sqlite3.Connection,
    validated: ValidatedScheduleBatch,
    run_id: str,
) -> sqlite3.Row | None:
    envelope = validated.envelope
    return conn.execute(
        """
        SELECT * FROM schedule_observation_event
        WHERE provider=? AND source=? AND competition_scope=? AND season_scope=?
          AND observed_at=? AND poll_run_id=? AND payload_hash=?
        """,
        (
            envelope.provider,
            envelope.source_operation,
            str(envelope.competition_id),
            envelope.requested_season,
            envelope.observed_at,
            run_id,
            envelope.artifact_sha256,
        ),
    ).fetchone()


def _state_truth(
    conn: sqlite3.Connection,
    validated: ValidatedScheduleBatch,
    run_id: str,
) -> str:
    envelope = validated.envelope
    event = _event_row(conn, validated, run_id)
    associations = 0
    matched = 0
    conflict = False
    for row in validated.rows:
        identity = conn.execute(
            "SELECT * FROM schedule_match_identity "
            "WHERE provider=? AND provider_match_id=?",
            (envelope.provider, row.provider_match_id),
        ).fetchone()
        if identity is None:
            if event is not None:
                conflict = True
            continue
        if (
            identity["canonical_match_id"] != row.canonical_match_id
            or identity["identity_provenance"]
            != "offline_shadow:validated_provider_match_id"
        ):
            conflict = True
            continue
        snapshot = conn.execute(
            "SELECT * FROM schedule_match_state_snapshot "
            "WHERE match_identity_id=? AND state_content_hash=?",
            (identity["id"], row.state_content_hash),
        ).fetchone()
        association = conn.execute(
            "SELECT * FROM schedule_match_observation "
            "WHERE match_identity_id=? AND observed_at=?",
            (identity["id"], envelope.observed_at),
        ).fetchone()
        if association is not None:
            associations += 1
        if (
            snapshot is None
            or association is None
            or event is None
            or int(association["observation_event_id"]) != int(event["id"])
            or int(association["snapshot_id"]) != int(snapshot["id"])
            or not _state_row_matches(snapshot, row)
        ):
            if association is not None or event is not None:
                conflict = True
            continue
        matched += 1
    if conflict:
        return "PARTIAL_OR_CONFLICTING"
    if event is None and associations == 0:
        return "NO_STATE"
    if event is not None and matched == len(validated.rows):
        return "STATE_COMPLETE"
    return "PARTIAL_OR_CONFLICTING"


def _feature_plan(
    conn: sqlite3.Connection,
    as_of_observed_at: str,
) -> list[dict[str, Any]]:
    rows = schedule._states_as_of(conn, as_of_observed_at)
    by_team: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        if (
            row["finished"] != 1
            or row["cancelled"] != 0
            or row["kickoff_precision"] != "exact"
            or row["kickoff_at_utc"] is None
            or row["competition_verified"] != 1
            or row["competition_class"] not in schedule.COMPETITIVE_CLASSES
        ):
            continue
        for team_id in (row["home_team_id"], row["away_team_id"]):
            if team_id is not None:
                by_team[int(team_id)].append(row)
    plan: list[dict[str, Any]] = []
    for team_id in sorted(by_team):
        timeline = sorted(
            by_team[team_id],
            key=lambda row: (
                schedule._utc(row["kickoff_at_utc"]),
                int(row["match_identity_id"]),
            ),
        )
        previous: datetime | None = None
        for index, row in enumerate(timeline):
            kickoff = datetime.fromisoformat(
                schedule._utc(row["kickoff_at_utc"]).replace("Z", "+00:00")
            )
            if previous is not None and kickoff <= previous:
                raise ValueError("non-strict team schedule")
            prefix = timeline[: index + 1]
            stable_inputs = [
                {
                    "input_ordinal": ordinal,
                    "match_identity_id": int(item["match_identity_id"]),
                    "snapshot_id": int(item["id"]),
                    "state_content_hash": item["state_content_hash"],
                    "kickoff_at_utc": item["kickoff_at_utc"],
                    "home_team_id": item["home_team_id"],
                    "away_team_id": item["away_team_id"],
                }
                for ordinal, item in enumerate(prefix)
            ]
            input_hash = schedule._sha256(stable_inputs)
            if previous is None:
                gap_hours = None
                previous_snapshot_id = None
            else:
                gap_hours = (kickoff - previous).total_seconds() / 3600.0
                previous_snapshot_id = int(prefix[-2]["id"])
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
                    for prior in prefix[:-1]
                    if 0
                    < (
                        kickoff
                        - datetime.fromisoformat(
                            schedule._utc(prior["kickoff_at_utc"]).replace(
                                "Z", "+00:00"
                            )
                        )
                    ).total_seconds()
                    <= 7 * 86400
                ),
                "matches_last_14d": sum(
                    1
                    for prior in prefix[:-1]
                    if 0
                    < (
                        kickoff
                        - datetime.fromisoformat(
                            schedule._utc(prior["kickoff_at_utc"]).replace(
                                "Z", "+00:00"
                            )
                        )
                    ).total_seconds()
                    <= 14 * 86400
                ),
            }
            plan.append(
                {
                    "team_id": team_id,
                    "target_match_identity_id": int(row["match_identity_id"]),
                    "target_snapshot_id": int(row["id"]),
                    "input_set_hash": input_hash,
                    "input_match_identity_ids": [
                        int(item["match_identity_id"]) for item in prefix
                    ],
                    "input_snapshot_ids": [
                        int(item["id"]) for item in prefix
                    ],
                    "feature_value_json": schedule._canonical_json(value),
                    "feature_payload_hash": schedule._sha256(
                        {
                            "team_id": team_id,
                            "target_match_identity_id": int(
                                row["match_identity_id"]
                            ),
                            "target_snapshot_id": int(row["id"]),
                            "feature_definition": (
                                schedule.REST_FEATURE_DEFINITION
                            ),
                            "feature_version": schedule.REST_FEATURE_VERSION,
                            "input_set_hash": input_hash,
                            "input_count": len(prefix),
                            "feature_value_json": schedule._canonical_json(
                                value
                            ),
                            "computation_status": "computed",
                            "provenance": schedule.REST_FEATURE_PROVENANCE,
                        }
                    ),
                }
            )
            previous = kickoff
    return plan


def _feature_item_truth(
    conn: sqlite3.Connection,
    expected: Mapping[str, Any],
) -> str:
    lineage = conn.execute(
        """
        SELECT * FROM schedule_rest_lineage_set
        WHERE team_id=? AND target_match_identity_id=? AND target_snapshot_id=?
          AND feature_definition=? AND feature_version=? AND input_set_hash=?
        """,
        (
            expected["team_id"],
            expected["target_match_identity_id"],
            expected["target_snapshot_id"],
            schedule.REST_FEATURE_DEFINITION,
            schedule.REST_FEATURE_VERSION,
            expected["input_set_hash"],
        ),
    ).fetchone()
    if lineage is None:
        return "ABSENT"
    inputs = conn.execute(
        "SELECT input_ordinal, input_match_identity_id, input_snapshot_id "
        "FROM schedule_rest_lineage_input WHERE lineage_set_id=? "
        "ORDER BY input_ordinal",
        (lineage["id"],),
    ).fetchall()
    feature = conn.execute(
        "SELECT * FROM schedule_rest_feature WHERE lineage_set_id=?",
        (lineage["id"],),
    ).fetchone()
    expected_inputs = [
        (ordinal, identity_id, snapshot_id)
        for ordinal, (identity_id, snapshot_id) in enumerate(
            zip(
                expected["input_match_identity_ids"],
                expected["input_snapshot_ids"],
                strict=True,
            )
        )
    ]
    if (
        int(lineage["expected_input_count"]) != len(expected_inputs)
        or [tuple(item) for item in inputs] != expected_inputs
        or lineage["feature_definition"] != schedule.REST_FEATURE_DEFINITION
        or lineage["feature_version"] != schedule.REST_FEATURE_VERSION
        or feature is None
        or feature["team_id"] != expected["team_id"]
        or feature["target_match_identity_id"]
        != expected["target_match_identity_id"]
        or feature["target_snapshot_id"] != expected["target_snapshot_id"]
        or feature["input_set_hash"] != expected["input_set_hash"]
        or feature["input_count"] != len(expected_inputs)
        or feature["feature_definition"] != schedule.REST_FEATURE_DEFINITION
        or feature["feature_version"] != schedule.REST_FEATURE_VERSION
        or feature["feature_payload_hash"]
        != expected["feature_payload_hash"]
        or feature["feature_value_json"] != expected["feature_value_json"]
        or feature["computation_status"] != "computed"
        or feature["provenance"] != schedule.REST_FEATURE_PROVENANCE
    ):
        return "CONFLICT"
    return "COMPLETE"


def derive_shadow_db_state(
    conn: sqlite3.Connection,
    validated: ValidatedScheduleBatch,
    run_id: str,
) -> str:
    """Classify durable database truth without mutating it."""

    state_truth = _state_truth(conn, validated, run_id)
    if state_truth != "STATE_COMPLETE":
        return state_truth
    plan = _feature_plan(conn, validated.envelope.observed_at)
    states = [_feature_item_truth(conn, item) for item in plan]
    if "CONFLICT" in states:
        return "PARTIAL_OR_CONFLICTING"
    if all(item == "COMPLETE" for item in states):
        return "FEATURES_COMPLETE"
    return "STATE_COMPLETE"


def _verified_result(
    conn: sqlite3.Connection,
    validated: ValidatedScheduleBatch,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    if derive_shadow_db_state(conn, validated, manifest["run_id"]) != "FEATURES_COMPLETE":
        raise ValueError("database truth incomplete")
    feature_plan = _feature_plan(conn, validated.envelope.observed_at)
    return {
        "status": "COMPLETED",
        "phase": "COMPLETED",
        "run_id": manifest["run_id"],
        "artifact_sha256": validated.envelope.artifact_sha256,
        "observed_at": validated.envelope.observed_at,
        "normalized_match_count": len(validated.rows),
        "state_summary": {
            "identity_verified": len(validated.rows),
            "snapshot_verified": len(validated.rows),
            "event_verified": 1,
            "association_verified": len(validated.rows),
        },
        "feature_summary": {
            "feature_verified": len(feature_plan),
            "lineage_input_verified": sum(
                len(item["input_snapshot_ids"]) for item in feature_plan
            ),
        },
    }


def _load_or_create_manifest(
    session: PreparedShadowSession,
    envelope: ArtifactEnvelope,
    run_id: str,
) -> dict[str, Any]:
    path = _manifest_path(session, run_id)
    identity = _manifest_identity(envelope, run_id)
    if not path.exists():
        manifest = _new_manifest(envelope, run_id)
        _write_manifest(path, manifest)
        return manifest
    manifest = read_shadow_run_manifest(session, run_id)
    if any(manifest.get(key) != value for key, value in identity.items()):
        raise ShadowRunConflictError("shadow run identity conflict") from None
    if manifest.get("phase") == "FAILED":
        return _retry_manifest(session, manifest)
    if manifest.get("phase") != "COMPLETED":
        resumed = dict(manifest)
        resumed["resume_count"] = int(manifest.get("resume_count", 0)) + 1
        resumed["history"] = [
            *manifest.get("history", []),
            f"RESUME:{manifest['phase']}",
        ]
        _write_manifest(path, resumed)
        return resumed
    return manifest


def _inject(fault_point: str | None, expected: str) -> None:
    if fault_point == expected:
        raise ShadowInjectedCrash("offline shadow injected crash") from None


def _run_shadow_ingestion_inner(
    session: PreparedShadowSession,
    envelope: ArtifactEnvelope,
    *,
    run_id: str,
    fault_point: str | None = None,
) -> dict[str, Any]:
    """Apply one immutable artifact to a migrated temporary-copy session."""

    if fault_point is not None and fault_point not in _FAULT_POINTS:
        raise _fixed_ingestion_error() from None
    try:
        with _session_lock(session):
            binding = _session_binding(session)
            run_id_value = _run_id(run_id)
            manifest = _load_or_create_manifest(
                session,
                envelope,
                run_id_value,
            )
            validated = validate_artifact_envelope(envelope)

            conn = _connect_writable(binding.database_path)
            try:
                if manifest["phase"] == "COMPLETED":
                    truth = derive_shadow_db_state(
                        conn,
                        validated,
                        run_id_value,
                    )
                    if truth == "PARTIAL_OR_CONFLICTING":
                        raise ShadowIngestionError(
                            "offline shadow database truth conflict"
                        )
                    result = _verified_result(conn, validated, manifest)
                    cached = manifest.get("result")
                    if cached != result:
                        raise ShadowIngestionError(
                            "offline shadow completed result conflict"
                        )
                    return result

                if manifest["phase"] == "NEW":
                    manifest = _advance_manifest(
                        session,
                        manifest,
                        "ARTIFACT_VALIDATED",
                        normalized_match_count=len(validated.rows),
                    )
                    _inject(fault_point, FAULT_AFTER_ARTIFACT_VALIDATED)

                truth = derive_shadow_db_state(conn, validated, run_id_value)
                if manifest["phase"] == "ARTIFACT_VALIDATED":
                    if truth == "NO_STATE":
                        try:
                            state_summary = schedule.record_match_states_batch(
                                conn,
                                provider=envelope.provider,
                                identity_created_at=envelope.observed_at,
                                identity_provenance=(
                                    "offline_shadow:validated_provider_match_id"
                                ),
                                matches=_state_inputs(validated),
                                source=envelope.source_operation,
                                competition_scope=str(envelope.competition_id),
                                season_scope=envelope.requested_season,
                                observed_at=envelope.observed_at,
                                poll_run_id=run_id_value,
                                payload_hash=envelope.artifact_sha256,
                                ingested_at=envelope.observed_at,
                                _fault_after_associations=(
                                    max(1, len(validated.rows) // 2)
                                    if fault_point
                                    == FAULT_STATE_TRANSACTION_MID
                                    else None
                                ),
                            )
                        except Exception:
                            manifest = _failed_manifest(
                                session,
                                manifest,
                                "STATE_APPLY_FAILED",
                            )
                            raise _fixed_ingestion_error() from None
                        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                        _refresh_session(session)
                        _inject(
                            fault_point,
                            FAULT_AFTER_STATE_COMMIT_BEFORE_MANIFEST,
                        )
                    elif truth in {"STATE_COMPLETE", "FEATURES_COMPLETE"}:
                        state_summary = manifest.get("state_apply_summary") or {
                            "reconciled_from_db": True,
                            "identity_verified": len(validated.rows),
                            "snapshot_verified": len(validated.rows),
                            "event_verified": 1,
                            "association_verified": len(validated.rows),
                        }
                    else:
                        _failed_manifest(
                            session,
                            manifest,
                            "STATE_APPLY_FAILED",
                        )
                        raise ShadowIngestionError(
                            "offline shadow database truth conflict"
                        )
                    manifest = _advance_manifest(
                        session,
                        manifest,
                        "STATE_APPLIED",
                        state_apply_summary=state_summary,
                    )
                    _inject(fault_point, FAULT_AFTER_STATE_APPLIED)

                truth = derive_shadow_db_state(conn, validated, run_id_value)
                if truth not in {"STATE_COMPLETE", "FEATURES_COMPLETE"}:
                    raise ShadowIngestionError(
                        "offline shadow database truth conflict"
                    )
                if manifest["phase"] == "STATE_APPLIED":
                    try:
                        feature_summary = (
                            schedule.build_observed_rest_features_as_of(
                                conn,
                                as_of_observed_at=envelope.observed_at,
                                computed_at=envelope.observed_at,
                                _fault_after_features=(
                                    1
                                    if fault_point
                                    == FAULT_FEATURE_TRANSACTION_MID
                                    else None
                                ),
                            )
                        )
                    except Exception:
                        manifest = _failed_manifest(
                            session,
                            manifest,
                            "FEATURE_APPLY_FAILED",
                        )
                        raise _fixed_ingestion_error() from None
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    _refresh_session(session)
                    _inject(
                        fault_point,
                        FAULT_AFTER_FEATURE_COMMIT_BEFORE_MANIFEST,
                    )
                    manifest = _advance_manifest(
                        session,
                        manifest,
                        "FEATURES_APPLIED",
                        feature_apply_summary=feature_summary,
                    )

                if (
                    derive_shadow_db_state(conn, validated, run_id_value)
                    != "FEATURES_COMPLETE"
                ):
                    raise ShadowIngestionError(
                        "offline shadow database truth conflict"
                    )
                if manifest["phase"] == "FEATURES_APPLIED":
                    provisional = dict(manifest)
                    provisional["phase"] = "COMPLETED"
                    result = _verified_result(conn, validated, provisional)
                    manifest = _advance_manifest(
                        session,
                        manifest,
                        "COMPLETED",
                        result=result,
                    )
                result = _verified_result(conn, validated, manifest)
                if manifest.get("result") != result:
                    raise ShadowIngestionError(
                        "offline shadow completed result conflict"
                    )
                return result
            finally:
                conn.close()
    except ShadowInjectedCrash:
        raise
    except ShadowRunConflictError:
        raise
    except ShadowSessionError:
        raise
    except ShadowError:
        raise _fixed_ingestion_error() from None
    except Exception:
        raise _fixed_ingestion_error() from None


def run_shadow_ingestion(
    session: PreparedShadowSession,
    envelope: ArtifactEnvelope,
    *,
    run_id: str,
    fault_point: str | None = None,
) -> dict[str, Any]:
    """Fixed-surface public wrapper around the durable shadow state machine."""

    result: dict[str, Any] | None = None
    failure_kind: str | None = None
    failure_message: str | None = None
    try:
        result = _run_shadow_ingestion_inner(
            session,
            envelope,
            run_id=run_id,
            fault_point=fault_point,
        )
    except ShadowInjectedCrash:
        failure_kind = "crash"
    except ShadowRunConflictError:
        failure_kind = "conflict"
    except ShadowSessionError as exc:
        failure_kind = "session"
        failure_message = str(exc)
    except Exception:
        failure_kind = "ingestion"
    if failure_kind == "crash":
        raise ShadowInjectedCrash("offline shadow injected crash") from None
    if failure_kind == "conflict":
        raise ShadowRunConflictError("shadow run identity conflict") from None
    if failure_kind == "session":
        raise ShadowSessionError(
            failure_message or "shadow session validation failed"
        ) from None
    if failure_kind is not None or result is None:
        raise _fixed_ingestion_error() from None
    return result
