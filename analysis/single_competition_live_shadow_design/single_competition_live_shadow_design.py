"""Offline-only acquisition design for one explicitly allowed competition.

There is deliberately no live transport implementation in this module.  A
caller must inject a transport, and the permanent proof uses ``FakeTransport``.
Every possible dispatch is durably budgeted before control reaches that
transport.  Validated response artifacts are then handed to the already
validated offline schedule-shadow state machine.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analysis.schedule_state_migration_trial import (
    schedule_state_migration_trial as trial,
)
from analysis.schedule_shadow_ingestion import (
    schedule_shadow_ingestion as shadow,
)
from backend.schedules import state as schedule


MAX_ACQUISITION_RESPONSE_BYTES = shadow.MAX_SHADOW_ARTIFACT_BYTES

FAULT_AFTER_INTENT_BEFORE_TRANSPORT = "AFTER_INTENT_BEFORE_TRANSPORT"
FAULT_AFTER_TRANSPORT_BEFORE_RECEIPT = "AFTER_TRANSPORT_BEFORE_RECEIPT"
FAULT_AFTER_RECEIPT_BEFORE_ARTIFACT_RENAME = (
    "AFTER_RECEIPT_BEFORE_ARTIFACT_RENAME"
)
FAULT_AFTER_ARTIFACT_RENAME_BEFORE_LEDGER = (
    "AFTER_ARTIFACT_RENAME_BEFORE_LEDGER"
)
FAULT_AFTER_ARTIFACT_VALIDATED_BEFORE_APPLY = (
    "AFTER_ARTIFACT_VALIDATED_BEFORE_APPLY"
)

_LOCAL_FAULTS = frozenset(
    {
        FAULT_AFTER_INTENT_BEFORE_TRANSPORT,
        FAULT_AFTER_TRANSPORT_BEFORE_RECEIPT,
        FAULT_AFTER_RECEIPT_BEFORE_ARTIFACT_RENAME,
        FAULT_AFTER_ARTIFACT_RENAME_BEFORE_LEDGER,
        FAULT_AFTER_ARTIFACT_VALIDATED_BEFORE_APPLY,
    }
)
_SHADOW_FAULTS = frozenset(
    {
        shadow.FAULT_AFTER_ARTIFACT_VALIDATED,
        shadow.FAULT_STATE_TRANSACTION_MID,
        shadow.FAULT_AFTER_STATE_COMMIT_BEFORE_MANIFEST,
        shadow.FAULT_AFTER_STATE_APPLIED,
        shadow.FAULT_FEATURE_TRANSACTION_MID,
        shadow.FAULT_AFTER_FEATURE_COMMIT_BEFORE_MANIFEST,
    }
)
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}")
_OPERATION = re.compile(r"[a-z][a-z0-9_]{0,63}")
_CONTROL_SCHEMA_VERSION = 1
_DESCRIPTOR_SCHEMA_VERSION = 1


class AcquisitionError(RuntimeError):
    """Base class for fixed, non-sensitive acquisition errors."""


class AcquisitionPolicyError(AcquisitionError):
    """The requested operation is outside the explicit scope."""


class AcquisitionBudgetError(AcquisitionError):
    """The durable request-attempt budget is exhausted."""


class AcquisitionInjectedCrash(AcquisitionError):
    """A permanent-test crash boundary interrupted the state machine."""


class AcquisitionOutcomeUnknownError(AcquisitionError):
    """A request may have been dispatched and must not be retried."""


class AcquisitionArtifactError(AcquisitionError):
    """The response artifact failed its immutable validation contract."""


class AcquisitionSessionError(AcquisitionError):
    """The durable acquisition session failed validation."""


class AcquisitionConcurrencyError(AcquisitionError):
    """Another process owns the single-writer acquisition lock."""


class FakeTransportFailure(RuntimeError):
    """A test transport result known not to have produced a response."""


@dataclass(frozen=True)
class AcquisitionConfig:
    provider: str
    competition_id: int
    competition_name: str
    requested_season: str
    allowed_operations: tuple[str, ...]
    budget_max: int
    expected_fixture_count: int
    competition_class: str
    artifact_schema_version: str


@dataclass(frozen=True)
class AcquisitionSession:
    session_id: str
    capability: str
    descriptor_path: Path


@dataclass(frozen=True)
class _Binding:
    session: AcquisitionSession
    config: AcquisitionConfig
    workspace: Path
    artifacts_directory: Path
    control_path: Path
    lock_path: Path
    shadow_session: shadow.PreparedShadowSession


class FakeTransport:
    """Deterministic offline transport; it cannot open a network connection."""

    def __init__(self, outcomes: Sequence[bytes | FakeTransportFailure]):
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        *,
        operation: str,
        competition_id: int,
        requested_season: str,
    ) -> bytes:
        self.calls.append(
            {
                "operation": operation,
                "competition_id": competition_id,
                "requested_season": requested_season,
            }
        )
        if not self._outcomes:
            raise FakeTransportFailure("no fake response configured")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, FakeTransportFailure):
            raise outcome
        if not isinstance(outcome, bytes):
            raise FakeTransportFailure("invalid fake response")
        return outcome


def _policy_error() -> AcquisitionPolicyError:
    return AcquisitionPolicyError("acquisition request is outside the allowed scope")


def _budget_error() -> AcquisitionBudgetError:
    return AcquisitionBudgetError("acquisition request budget exhausted")


def _artifact_error() -> AcquisitionArtifactError:
    return AcquisitionArtifactError("acquisition response artifact validation failed")


def _session_error() -> AcquisitionSessionError:
    return AcquisitionSessionError("acquisition session validation failed")


def _outcome_unknown_error() -> AcquisitionOutcomeUnknownError:
    return AcquisitionOutcomeUnknownError("acquisition request outcome is unknown")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid text")
    return value.strip()


def _validate_config(config: Any) -> AcquisitionConfig:
    if not isinstance(config, AcquisitionConfig):
        raise ValueError("config required")
    provider = schedule.normalize_provider(config.provider)
    competition_name = _text(config.competition_name)
    requested_season = _text(config.requested_season)
    competition_class = _text(config.competition_class)
    artifact_schema_version = _text(config.artifact_schema_version)
    if (
        isinstance(config.competition_id, bool)
        or not isinstance(config.competition_id, int)
        or config.competition_id <= 0
        or isinstance(config.budget_max, bool)
        or not isinstance(config.budget_max, int)
        or config.budget_max <= 0
        or isinstance(config.expected_fixture_count, bool)
        or not isinstance(config.expected_fixture_count, int)
        or config.expected_fixture_count <= 0
        or not isinstance(config.allowed_operations, tuple)
        or not config.allowed_operations
    ):
        raise ValueError("invalid config")
    operations = tuple(_text(item).casefold() for item in config.allowed_operations)
    if (
        len(set(operations)) != len(operations)
        or any(_OPERATION.fullmatch(item) is None for item in operations)
    ):
        raise ValueError("invalid operations")
    return AcquisitionConfig(
        provider=provider,
        competition_id=config.competition_id,
        competition_name=competition_name,
        requested_season=requested_season,
        allowed_operations=operations,
        budget_max=config.budget_max,
        expected_fixture_count=config.expected_fixture_count,
        competition_class=competition_class,
        artifact_schema_version=artifact_schema_version,
    )


def _config_document(config: AcquisitionConfig) -> dict[str, Any]:
    return {
        "provider": config.provider,
        "competition_id": config.competition_id,
        "competition_name": config.competition_name,
        "requested_season": config.requested_season,
        "allowed_operations": list(config.allowed_operations),
        "budget_max": config.budget_max,
        "expected_fixture_count": config.expected_fixture_count,
        "competition_class": config.competition_class,
        "artifact_schema_version": config.artifact_schema_version,
    }


def _config_from_document(document: Any) -> AcquisitionConfig:
    if not isinstance(document, dict):
        raise ValueError("invalid config document")
    expected = {
        "provider",
        "competition_id",
        "competition_name",
        "requested_season",
        "allowed_operations",
        "budget_max",
        "expected_fixture_count",
        "competition_class",
        "artifact_schema_version",
    }
    if set(document) != expected or not isinstance(
        document["allowed_operations"], list
    ):
        raise ValueError("invalid config document")
    return _validate_config(
        AcquisitionConfig(
            provider=document["provider"],
            competition_id=document["competition_id"],
            competition_name=document["competition_name"],
            requested_season=document["requested_season"],
            allowed_operations=tuple(document["allowed_operations"]),
            budget_max=document["budget_max"],
            expected_fixture_count=document["expected_fixture_count"],
            competition_class=document["competition_class"],
            artifact_schema_version=document["artifact_schema_version"],
        )
    )


def _safe_private_directory(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ValueError("unsafe directory")


def _safe_private_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise ValueError("unsafe file")


def _create_private_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _connect_control(path: Path) -> sqlite3.Connection:
    _safe_private_file(path)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA recursive_triggers = ON")
    conn.execute("PRAGMA synchronous = FULL")
    mode = conn.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
    if str(mode).casefold() != "delete":
        conn.close()
        raise ValueError("control journal mode")
    return conn


_CONTROL_SCHEMA = """
CREATE TABLE acquisition_meta (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    config_sha256 TEXT NOT NULL
) STRICT;
CREATE TABLE acquisition_run (
    run_id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    phase TEXT NOT NULL,
    result_json TEXT,
    CHECK (phase IN (
        'NEW', 'ARTIFACT_BOUND', 'ARTIFACT_VALIDATED',
        'COMPLETED', 'OUTCOME_UNKNOWN'
    ))
) STRICT;
CREATE TABLE request_attempt (
    run_id TEXT NOT NULL REFERENCES acquisition_run(run_id),
    request_id TEXT NOT NULL UNIQUE,
    attempt_ordinal INTEGER NOT NULL,
    operation TEXT NOT NULL,
    provider TEXT NOT NULL,
    competition_id INTEGER NOT NULL,
    competition_name TEXT NOT NULL,
    requested_season TEXT NOT NULL,
    budget_maximum INTEGER NOT NULL,
    intent_recorded_at TEXT NOT NULL,
    dispatch_state TEXT NOT NULL,
    response_receipt_state TEXT NOT NULL,
    terminal_outcome TEXT,
    response_sha256 TEXT,
    response_size INTEGER,
    artifact_sha256 TEXT,
    artifact_size INTEGER,
    staging_name TEXT,
    PRIMARY KEY (run_id, attempt_ordinal),
    CHECK (dispatch_state IN (
        'INTENT_RECORDED', 'DISPATCH_STARTED', 'FAILED_SAFE',
        'RESPONSE_STAGED', 'RESPONSE_REJECTED',
        'ARTIFACT_BOUND', 'OUTCOME_UNKNOWN'
    )),
    CHECK (response_receipt_state IN (
        'NOT_RECORDED', 'NO_RESPONSE', 'RECORDED', 'UNKNOWN'
    )),
    CHECK (
        terminal_outcome IS NULL
        OR terminal_outcome IN (
            'FAILED_SAFE', 'REJECTED', 'SUCCEEDED', 'OUTCOME_UNKNOWN'
        )
    )
) STRICT;
CREATE TABLE response_artifact (
    run_id TEXT PRIMARY KEY REFERENCES acquisition_run(run_id),
    attempt_ordinal INTEGER NOT NULL,
    artifact_name TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    FOREIGN KEY (run_id, attempt_ordinal)
        REFERENCES request_attempt(run_id, attempt_ordinal)
) STRICT;
"""


def _initialize_control(
    path: Path,
    *,
    session_id: str,
    config: AcquisitionConfig,
) -> None:
    conn = _connect_control(path)
    try:
        conn.executescript(_CONTROL_SCHEMA)
        conn.execute(
            """
            INSERT INTO acquisition_meta(
                singleton, schema_version, session_id, config_sha256
            ) VALUES(1, ?, ?, ?)
            """,
            (
                _CONTROL_SCHEMA_VERSION,
                session_id,
                _sha256(_canonical_json(_config_document(config))),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _validate_control(binding: _Binding) -> None:
    conn = _connect_control(binding.control_path)
    try:
        row = conn.execute(
            """
            SELECT schema_version, session_id, config_sha256
            FROM acquisition_meta WHERE singleton=1
            """
        ).fetchone()
        expected_tables = {
            "acquisition_meta",
            "acquisition_run",
            "request_attempt",
            "response_artifact",
        }
        actual_tables = {
            item[0]
            for item in conn.execute(
                """
                SELECT name FROM sqlite_schema
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                """
            )
        }
        if (
            row is None
            or row["schema_version"] != _CONTROL_SCHEMA_VERSION
            or row["session_id"] != binding.session.session_id
            or row["config_sha256"]
            != _sha256(_canonical_json(_config_document(binding.config)))
            or actual_tables != expected_tables
            or conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
        ):
            raise ValueError("control database mismatch")
    finally:
        conn.close()


def _descriptor_document(
    *,
    session_id: str,
    capability: str,
    config: AcquisitionConfig,
    workspace: Path,
    artifacts_directory: Path,
    control_path: Path,
    lock_path: Path,
    shadow_session: shadow.PreparedShadowSession,
    descriptor_path: Path,
) -> dict[str, Any]:
    return {
        "schema_version": _DESCRIPTOR_SCHEMA_VERSION,
        "session_id": session_id,
        "capability_sha256": _sha256(bytes.fromhex(capability)),
        "creator_uid": os.getuid(),
        "workspace": str(workspace.resolve(strict=True)),
        "artifacts_directory": str(artifacts_directory.resolve(strict=True)),
        "control_path": str(control_path.resolve(strict=True)),
        "lock_path": str(lock_path.resolve(strict=True)),
        "shadow_descriptor_path": str(
            shadow_session.descriptor_path.resolve(strict=True)
        ),
        "descriptor_path": str(descriptor_path.absolute()),
        "config": _config_document(config),
    }


def prepare_acquisition_session(
    prepared: trial.PreparedTrialCopy,
    config: AcquisitionConfig,
) -> AcquisitionSession:
    """Create a durable acquisition session around one guarded trial copy."""

    result: AcquisitionSession | None = None
    try:
        if not isinstance(prepared, trial.PreparedTrialCopy):
            raise ValueError("prepared copy required")
        validated_config = _validate_config(config)
        shadow_session = shadow.open_shadow_session(prepared)
        workspace = prepared.run_directory / "single-competition-live-shadow"
        workspace.mkdir(mode=0o700)
        artifacts_directory = workspace / "artifacts"
        artifacts_directory.mkdir(mode=0o700)
        control_path = workspace / "control.sqlite3"
        lock_path = workspace / "session.lock"
        _create_private_file(control_path)
        _create_private_file(lock_path)
        session_id = secrets.token_hex(16)
        capability = shadow_session.capability
        descriptor_path = workspace / "session.json"
        _initialize_control(
            control_path,
            session_id=session_id,
            config=validated_config,
        )
        document = _descriptor_document(
            session_id=session_id,
            capability=capability,
            config=validated_config,
            workspace=workspace,
            artifacts_directory=artifacts_directory,
            control_path=control_path,
            lock_path=lock_path,
            shadow_session=shadow_session,
            descriptor_path=descriptor_path,
        )
        shadow._write_signed_document(descriptor_path, document, capability)
        result = AcquisitionSession(
            session_id=session_id,
            capability=capability,
            descriptor_path=descriptor_path,
        )
        _binding(result)
    except Exception:
        result = None
    if result is None:
        raise _session_error() from None
    return result


def _binding(session: AcquisitionSession) -> _Binding:
    result: _Binding | None = None
    try:
        if not isinstance(session, AcquisitionSession):
            raise ValueError("session required")
        path = session.descriptor_path.resolve(strict=True)
        if path != session.descriptor_path.resolve():
            raise ValueError("descriptor path")
        document = shadow._read_signed_document(path, session.capability)
        expected_keys = {
            "schema_version",
            "session_id",
            "capability_sha256",
            "creator_uid",
            "workspace",
            "artifacts_directory",
            "control_path",
            "lock_path",
            "shadow_descriptor_path",
            "descriptor_path",
            "config",
            "signature",
        }
        if (
            set(document) != expected_keys
            or document["schema_version"] != _DESCRIPTOR_SCHEMA_VERSION
            or document["session_id"] != session.session_id
            or document["creator_uid"] != os.getuid()
            or document["capability_sha256"]
            != _sha256(bytes.fromhex(session.capability))
            or Path(document["descriptor_path"]) != path
        ):
            raise ValueError("descriptor identity")
        workspace = Path(document["workspace"])
        artifacts_directory = Path(document["artifacts_directory"])
        control_path = Path(document["control_path"])
        lock_path = Path(document["lock_path"])
        if (
            workspace.resolve(strict=True) != workspace
            or artifacts_directory.resolve(strict=True) != artifacts_directory
            or control_path.resolve(strict=True) != control_path
            or lock_path.resolve(strict=True) != lock_path
            or artifacts_directory.parent != workspace
            or control_path.parent != workspace
            or lock_path.parent != workspace
        ):
            raise ValueError("descriptor path binding")
        _safe_private_directory(workspace)
        _safe_private_directory(artifacts_directory)
        _safe_private_file(control_path)
        _safe_private_file(lock_path)
        shadow_session = shadow.reopen_shadow_session(
            Path(document["shadow_descriptor_path"]),
            session.capability,
        )
        result = _Binding(
            session=session,
            config=_config_from_document(document["config"]),
            workspace=workspace,
            artifacts_directory=artifacts_directory,
            control_path=control_path,
            lock_path=lock_path,
            shadow_session=shadow_session,
        )
        _validate_control(result)
    except Exception:
        result = None
    if result is None:
        raise _session_error() from None
    return result


def reopen_acquisition_session(
    descriptor_path: Path | str,
    capability: str,
) -> AcquisitionSession:
    """Reopen a signed acquisition handoff in a different process."""

    result: AcquisitionSession | None = None
    try:
        path = Path(descriptor_path).resolve(strict=True)
        if not isinstance(capability, str) or re.fullmatch(
            r"[0-9a-f]{64}", capability
        ) is None:
            raise ValueError("capability")
        document = shadow._read_signed_document(path, capability)
        result = AcquisitionSession(
            session_id=_text(document["session_id"]),
            capability=capability,
            descriptor_path=path,
        )
        _binding(result)
    except Exception:
        result = None
    if result is None:
        raise _session_error() from None
    return result


@contextmanager
def _acquisition_lock(session: AcquisitionSession) -> Iterator[None]:
    binding = _binding(session)
    flags = os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    locked = False
    try:
        descriptor = os.open(binding.lock_path, flags)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise _session_error() from None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except BlockingIOError:
            pass
        if not locked:
            raise AcquisitionConcurrencyError(
                "acquisition session is already active"
            ) from None
        yield
    finally:
        if descriptor >= 0:
            if locked:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _run_id(value: Any) -> str:
    if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
        raise ValueError("run id")
    return value


def _artifact_name(run_id: str) -> str:
    return f"{_run_id(run_id)}.response.json"


def expected_artifact_path(
    session: AcquisitionSession,
    run_id: str,
) -> Path:
    binding = _binding(session)
    return binding.artifacts_directory / _artifact_name(run_id)


def artifact_path(session: AcquisitionSession, run_id: str) -> Path:
    path = expected_artifact_path(session, run_id)
    try:
        _safe_private_file(path)
    except Exception:
        raise _artifact_error() from None
    return path


def control_database_path(session: AcquisitionSession) -> Path:
    return _binding(session).control_path


def shadow_database_path(session: AcquisitionSession) -> Path:
    return shadow._session_binding(_binding(session).shadow_session).database_path


def _rows(session: AcquisitionSession, query: str, values: tuple[Any, ...]) -> list[dict[str, Any]]:
    binding = _binding(session)
    conn = _connect_control(binding.control_path)
    try:
        return [dict(row) for row in conn.execute(query, values).fetchall()]
    finally:
        conn.close()


def request_ledger(
    session: AcquisitionSession,
    run_id: str,
) -> list[dict[str, Any]]:
    return _rows(
        session,
        """
        SELECT run_id, request_id, attempt_ordinal, operation, provider,
               competition_id, competition_name, requested_season,
               budget_maximum, intent_recorded_at,
               dispatch_state, response_receipt_state, terminal_outcome,
               response_sha256, response_size, artifact_sha256,
               artifact_size, staging_name
        FROM request_attempt WHERE run_id=?
        ORDER BY attempt_ordinal
        """,
        (_run_id(run_id),),
    )


def artifact_ledger(
    session: AcquisitionSession,
    run_id: str,
) -> list[dict[str, Any]]:
    return _rows(
        session,
        """
        SELECT run_id, attempt_ordinal, artifact_name, sha256, size
        FROM response_artifact WHERE run_id=?
        """,
        (_run_id(run_id),),
    )


def _ensure_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    operation: str,
    observed_at: str,
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM acquisition_run WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if row is None:
        conn.execute(
            """
            INSERT INTO acquisition_run(run_id, operation, observed_at, phase)
            VALUES(?, ?, ?, 'NEW')
            """,
            (run_id, operation, observed_at),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM acquisition_run WHERE run_id=?",
            (run_id,),
        ).fetchone()
    if (
        row is None
        or row["operation"] != operation
        or row["observed_at"] != observed_at
    ):
        raise _policy_error() from None
    return row


def _next_or_existing_attempt(
    conn: sqlite3.Connection,
    *,
    binding: _Binding,
    run_id: str,
    operation: str,
) -> tuple[sqlite3.Row | None, str | None]:
    latest = conn.execute(
        """
        SELECT * FROM request_attempt
        WHERE run_id=? ORDER BY attempt_ordinal DESC LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    if latest is not None and latest["dispatch_state"] in {
        "INTENT_RECORDED",
        "RESPONSE_STAGED",
        "ARTIFACT_BOUND",
    }:
        return latest, None
    if latest is not None and latest["dispatch_state"] == "DISPATCH_STARTED":
        conn.execute(
            """
            UPDATE request_attempt
            SET dispatch_state='OUTCOME_UNKNOWN',
                response_receipt_state='UNKNOWN',
                terminal_outcome='OUTCOME_UNKNOWN'
            WHERE run_id=? AND attempt_ordinal=?
            """,
            (run_id, latest["attempt_ordinal"]),
        )
        conn.execute(
            "UPDATE acquisition_run SET phase='OUTCOME_UNKNOWN' WHERE run_id=?",
            (run_id,),
        )
        conn.commit()
        return None, "OUTCOME_UNKNOWN"
    used = 0 if latest is None else int(latest["attempt_ordinal"])
    if used >= binding.config.budget_max:
        return None, "BUDGET_EXHAUSTED"
    ordinal = used + 1
    conn.execute(
        """
        INSERT INTO request_attempt(
            run_id, request_id, attempt_ordinal, operation, provider,
            competition_id, competition_name, requested_season,
            budget_maximum, intent_recorded_at,
            dispatch_state, response_receipt_state
        ) VALUES(
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            'INTENT_RECORDED', 'NOT_RECORDED'
        )
        """,
        (
            run_id,
            f"{run_id}:{ordinal}",
            ordinal,
            operation,
            binding.config.provider,
            binding.config.competition_id,
            binding.config.competition_name,
            binding.config.requested_season,
            binding.config.budget_max,
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        ),
    )
    conn.commit()
    return conn.execute(
        """
        SELECT * FROM request_attempt
        WHERE run_id=? AND attempt_ordinal=?
        """,
        (run_id, ordinal),
    ).fetchone(), None


def _write_response_stage(path: Path, raw: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def _file_digest(path: Path) -> tuple[str, int]:
    raw, _ = shadow._read_regular_file_once(
        path,
        maximum_bytes=MAX_ACQUISITION_RESPONSE_BYTES,
        required_mode=0o600,
    )
    return _sha256(raw), len(raw)


def _bind_artifact(
    conn: sqlite3.Connection,
    binding: _Binding,
    attempt: sqlite3.Row,
    run_id: str,
    fault_point: str | None,
) -> tuple[Path, str, int]:
    final_path = binding.artifacts_directory / _artifact_name(run_id)
    expected_sha = attempt["response_sha256"]
    expected_size = attempt["response_size"]
    staging_name = attempt["staging_name"]
    if (
        not isinstance(expected_sha, str)
        or not isinstance(expected_size, int)
        or not isinstance(staging_name, str)
        or Path(staging_name).name != staging_name
    ):
        raise _artifact_error() from None
    stage_path = binding.artifacts_directory / staging_name
    if final_path.exists():
        actual_sha, actual_size = _file_digest(final_path)
        if actual_sha != expected_sha or actual_size != expected_size:
            raise _artifact_error() from None
    else:
        actual_sha, actual_size = _file_digest(stage_path)
        if actual_sha != expected_sha or actual_size != expected_size:
            raise _artifact_error() from None
        os.replace(stage_path, final_path)
        _fsync_directory(binding.artifacts_directory)
        if fault_point == FAULT_AFTER_ARTIFACT_RENAME_BEFORE_LEDGER:
            raise AcquisitionInjectedCrash(
                "acquisition injected crash"
            ) from None
    conn.execute(
        """
        INSERT INTO response_artifact(
            run_id, attempt_ordinal, artifact_name, sha256, size
        ) VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO NOTHING
        """,
        (
            run_id,
            attempt["attempt_ordinal"],
            final_path.name,
            expected_sha,
            expected_size,
        ),
    )
    artifact_row = conn.execute(
        "SELECT * FROM response_artifact WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if (
        artifact_row is None
        or artifact_row["attempt_ordinal"] != attempt["attempt_ordinal"]
        or artifact_row["artifact_name"] != final_path.name
        or artifact_row["sha256"] != expected_sha
        or artifact_row["size"] != expected_size
    ):
        conn.rollback()
        raise _artifact_error() from None
    conn.execute(
        """
        UPDATE request_attempt
        SET dispatch_state='ARTIFACT_BOUND',
            terminal_outcome='SUCCEEDED',
            artifact_sha256=?,
            artifact_size=?
        WHERE run_id=? AND attempt_ordinal=?
        """,
        (
            expected_sha,
            expected_size,
            run_id,
            attempt["attempt_ordinal"],
        ),
    )
    conn.execute(
        "UPDATE acquisition_run SET phase='ARTIFACT_BOUND' WHERE run_id=?",
        (run_id,),
    )
    conn.commit()
    return final_path, expected_sha, expected_size


def _existing_artifact(
    conn: sqlite3.Connection,
    binding: _Binding,
    run_id: str,
) -> tuple[Path, str, int] | None:
    row = conn.execute(
        "SELECT * FROM response_artifact WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    path = binding.artifacts_directory / row["artifact_name"]
    actual_sha, actual_size = _file_digest(path)
    if actual_sha != row["sha256"] or actual_size != row["size"]:
        raise _artifact_error() from None
    return path, row["sha256"], row["size"]


def _completeness_evidence(
    binding: _Binding,
) -> dict[str, Any]:
    return {
        "competition_identity_verified": True,
        "competition_name": binding.config.competition_name,
        "competition_class": binding.config.competition_class,
        "competition_class_verified": True,
        "returned_season": binding.config.requested_season,
        "fixture_schema_valid": True,
        "fixture_count": binding.config.expected_fixture_count,
        "pagination_status": "NOT_DETECTED",
        "pagination_detected_evidence": [],
        "pagination_unresolved_evidence": [],
        "pagination_unknown_evidence": [],
        "observation_time_provenance": (
            "caller_supplied_offline_transport_receipt_time"
        ),
    }


def _validated_envelope(
    *,
    binding: _Binding,
    artifact: Path,
    artifact_sha256: str,
    operation: str,
    observed_at: str,
) -> shadow.ArtifactEnvelope:
    envelope: shadow.ArtifactEnvelope | None = None
    failed = False
    try:
        candidate = shadow.load_artifact_envelope(
            artifact,
            expected_sha256=artifact_sha256,
            provider=binding.config.provider,
            source_operation=operation,
            competition_id=binding.config.competition_id,
            requested_season=binding.config.requested_season,
            observed_at=observed_at,
            artifact_schema_version=binding.config.artifact_schema_version,
            completeness_status="COMPLETE",
            completeness_evidence=_completeness_evidence(binding),
        )
        shadow.validate_artifact_envelope(candidate)
        envelope = candidate
    except Exception:
        failed = True
    if failed or envelope is None:
        raise _artifact_error() from None
    return envelope


def _result_document(result: Mapping[str, Any]) -> dict[str, Any]:
    state_summary = result["state_summary"]
    feature_summary = result["feature_summary"]
    return {
        **dict(result),
        "identity_count": state_summary["identity_verified"],
        "snapshot_count": state_summary["snapshot_verified"],
        "feature_count": feature_summary["feature_verified"],
        "lineage_input_count": feature_summary["lineage_input_verified"],
    }


def _run_acquisition_locked(
    session: AcquisitionSession,
    transport: Any,
    *,
    run_id: str,
    operation: str,
    observed_at: str,
    fault_point: str | None,
) -> dict[str, Any]:
    binding = _binding(session)
    config = binding.config
    if operation not in config.allowed_operations:
        raise _policy_error() from None
    if fault_point is not None and fault_point not in _LOCAL_FAULTS | _SHADOW_FAULTS:
        raise _policy_error() from None
    run_id_value = _run_id(run_id)
    observed_at_value = schedule._utc(observed_at)
    conn = _connect_control(binding.control_path)
    try:
        run = _ensure_run(
            conn,
            run_id=run_id_value,
            operation=operation,
            observed_at=observed_at_value,
        )
        if run["phase"] == "COMPLETED":
            artifact = _existing_artifact(conn, binding, run_id_value)
            if artifact is None:
                raise _artifact_error() from None
            artifact_path_value, artifact_sha256, _ = artifact
            envelope = _validated_envelope(
                binding=binding,
                artifact=artifact_path_value,
                artifact_sha256=artifact_sha256,
                operation=operation,
                observed_at=observed_at_value,
            )
            replay_result: dict[str, Any] | None = None
            replay_failed = False
            try:
                replay_result = _result_document(
                    shadow.run_shadow_ingestion(
                        binding.shadow_session,
                        envelope,
                        run_id=run_id_value,
                    )
                )
            except Exception:
                replay_failed = True
            cached_result: Any = None
            try:
                cached_result = json.loads(run["result_json"])
            except Exception:
                replay_failed = True
            if (
                replay_failed
                or replay_result is None
                or cached_result != replay_result
            ):
                raise _artifact_error() from None
            return replay_result
        if run["phase"] == "OUTCOME_UNKNOWN":
            raise _outcome_unknown_error() from None

        artifact = _existing_artifact(conn, binding, run_id_value)
        while artifact is None:
            attempt, terminal = _next_or_existing_attempt(
                conn,
                binding=binding,
                run_id=run_id_value,
                operation=operation,
            )
            if terminal == "OUTCOME_UNKNOWN":
                raise _outcome_unknown_error() from None
            if terminal == "BUDGET_EXHAUSTED" or attempt is None:
                raise _budget_error() from None
            if attempt["dispatch_state"] == "INTENT_RECORDED":
                if fault_point == FAULT_AFTER_INTENT_BEFORE_TRANSPORT:
                    raise AcquisitionInjectedCrash(
                        "acquisition injected crash"
                    ) from None
                conn.execute(
                    """
                    UPDATE request_attempt SET dispatch_state='DISPATCH_STARTED'
                    WHERE run_id=? AND attempt_ordinal=?
                    """,
                    (run_id_value, attempt["attempt_ordinal"]),
                )
                conn.commit()
                raw: bytes | None = None
                safe_failure = False
                unknown_failure = False
                try:
                    raw = transport.request(
                        operation=operation,
                        competition_id=config.competition_id,
                        requested_season=config.requested_season,
                    )
                except FakeTransportFailure:
                    safe_failure = True
                except Exception:
                    unknown_failure = True
                if unknown_failure:
                    conn.execute(
                        """
                        UPDATE request_attempt
                        SET dispatch_state='OUTCOME_UNKNOWN',
                            response_receipt_state='UNKNOWN',
                            terminal_outcome='OUTCOME_UNKNOWN'
                        WHERE run_id=? AND attempt_ordinal=?
                        """,
                        (run_id_value, attempt["attempt_ordinal"]),
                    )
                    conn.execute(
                        """
                        UPDATE acquisition_run SET phase='OUTCOME_UNKNOWN'
                        WHERE run_id=?
                        """,
                        (run_id_value,),
                    )
                    conn.commit()
                    raise _outcome_unknown_error() from None
                if safe_failure:
                    conn.execute(
                        """
                        UPDATE request_attempt
                        SET dispatch_state='FAILED_SAFE',
                            response_receipt_state='NO_RESPONSE',
                            terminal_outcome='FAILED_SAFE'
                        WHERE run_id=? AND attempt_ordinal=?
                        """,
                        (run_id_value, attempt["attempt_ordinal"]),
                    )
                    conn.commit()
                    continue
                if fault_point == FAULT_AFTER_TRANSPORT_BEFORE_RECEIPT:
                    raise AcquisitionInjectedCrash(
                        "acquisition injected crash"
                    ) from None
                if (
                    not isinstance(raw, bytes)
                    or len(raw) == 0
                    or len(raw) > MAX_ACQUISITION_RESPONSE_BYTES
                ):
                    response_size = len(raw) if isinstance(raw, bytes) else None
                    response_sha = _sha256(raw) if isinstance(raw, bytes) else None
                    conn.execute(
                        """
                        UPDATE request_attempt
                        SET dispatch_state='RESPONSE_REJECTED',
                            response_receipt_state='RECORDED',
                            terminal_outcome='REJECTED',
                            response_sha256=?,
                            response_size=?
                        WHERE run_id=? AND attempt_ordinal=?
                        """,
                        (
                            response_sha,
                            response_size,
                            run_id_value,
                            attempt["attempt_ordinal"],
                        ),
                    )
                    conn.commit()
                    raise _artifact_error() from None
                digest = _sha256(raw)
                staging_name = (
                    f".{run_id_value}.response.tmp-"
                    f"{attempt['attempt_ordinal']}-{secrets.token_hex(8)}"
                )
                stage_path = binding.artifacts_directory / staging_name
                stage_failed = False
                try:
                    _write_response_stage(stage_path, raw)
                except Exception:
                    stage_failed = True
                if stage_failed:
                    raise _artifact_error() from None
                conn.execute(
                    """
                    UPDATE request_attempt
                    SET dispatch_state='RESPONSE_STAGED',
                        response_receipt_state='RECORDED',
                        response_sha256=?, response_size=?, staging_name=?
                    WHERE run_id=? AND attempt_ordinal=?
                    """,
                    (
                        digest,
                        len(raw),
                        staging_name,
                        run_id_value,
                        attempt["attempt_ordinal"],
                    ),
                )
                conn.commit()
                if fault_point == FAULT_AFTER_RECEIPT_BEFORE_ARTIFACT_RENAME:
                    raise AcquisitionInjectedCrash(
                        "acquisition injected crash"
                    ) from None
                attempt = conn.execute(
                    """
                    SELECT * FROM request_attempt
                    WHERE run_id=? AND attempt_ordinal=?
                    """,
                    (run_id_value, attempt["attempt_ordinal"]),
                ).fetchone()
            if attempt["dispatch_state"] == "RESPONSE_STAGED":
                artifact = _bind_artifact(
                    conn,
                    binding,
                    attempt,
                    run_id_value,
                    fault_point,
                )
            elif attempt["dispatch_state"] == "ARTIFACT_BOUND":
                artifact = _existing_artifact(conn, binding, run_id_value)
            else:
                raise _artifact_error() from None

        artifact_path_value, artifact_sha256, _ = artifact
        envelope = _validated_envelope(
            binding=binding,
            artifact=artifact_path_value,
            artifact_sha256=artifact_sha256,
            operation=operation,
            observed_at=observed_at_value,
        )
        conn.execute(
            """
            UPDATE acquisition_run
            SET phase='ARTIFACT_VALIDATED'
            WHERE run_id=?
            """,
            (run_id_value,),
        )
        conn.commit()
        if fault_point == FAULT_AFTER_ARTIFACT_VALIDATED_BEFORE_APPLY:
            raise AcquisitionInjectedCrash(
                "acquisition injected crash"
            ) from None

        shadow_result: dict[str, Any] | None = None
        shadow_crash = False
        shadow_failure = False
        try:
            shadow_result = shadow.run_shadow_ingestion(
                binding.shadow_session,
                envelope,
                run_id=run_id_value,
                fault_point=fault_point if fault_point in _SHADOW_FAULTS else None,
            )
        except shadow.ShadowInjectedCrash:
            shadow_crash = True
        except Exception:
            shadow_failure = True
        if shadow_crash:
            raise AcquisitionInjectedCrash(
                "acquisition injected crash"
            ) from None
        if shadow_failure or shadow_result is None:
            raise _artifact_error() from None
        result = _result_document(shadow_result)
        result_json = _canonical_json(result).decode("utf-8")
        conn.execute(
            """
            UPDATE acquisition_run
            SET phase='COMPLETED', result_json=?
            WHERE run_id=?
            """,
            (result_json, run_id_value),
        )
        conn.commit()
        return result
    finally:
        conn.close()


def run_acquisition(
    session: AcquisitionSession,
    transport: Any,
    *,
    run_id: str,
    operation: str,
    observed_at: str,
    fault_point: str | None = None,
) -> dict[str, Any]:
    """Run or resume one budgeted, offline-injected acquisition attempt."""

    result: dict[str, Any] | None = None
    failure: str | None = None
    try:
        with _acquisition_lock(session):
            result = _run_acquisition_locked(
                session,
                transport,
                run_id=run_id,
                operation=operation,
                observed_at=observed_at,
                fault_point=fault_point,
            )
    except AcquisitionPolicyError:
        failure = "policy"
    except AcquisitionBudgetError:
        failure = "budget"
    except AcquisitionInjectedCrash:
        failure = "crash"
    except AcquisitionOutcomeUnknownError:
        failure = "unknown"
    except AcquisitionArtifactError:
        failure = "artifact"
    except AcquisitionConcurrencyError:
        failure = "concurrency"
    except AcquisitionSessionError:
        failure = "session"
    except Exception:
        failure = "closed"
    if failure is None and result is not None:
        return result
    if failure == "policy":
        raise _policy_error() from None
    if failure == "budget":
        raise _budget_error() from None
    if failure == "crash":
        raise AcquisitionInjectedCrash("acquisition injected crash") from None
    if failure == "unknown":
        raise _outcome_unknown_error() from None
    if failure == "artifact":
        raise _artifact_error() from None
    if failure == "concurrency":
        raise AcquisitionConcurrencyError(
            "acquisition session is already active"
        ) from None
    if failure == "session":
        raise _session_error() from None
    raise AcquisitionError("acquisition failed closed") from None
