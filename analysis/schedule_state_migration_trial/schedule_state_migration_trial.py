"""Strictly offline helpers for a guarded temporary database copy.

This module has no network transport and never opens its source database
writable.  A migration can consume only a :class:`PreparedTrialCopy` created
by this process through :func:`prepare_trial_copy`; arbitrary destination
paths are not migration inputs.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from analysis.cwc_production_integration_design import (
    cwc_production_integration_design as cwc,
)
from backend.schedules import state as schedule


IDENTITY_PROVENANCE = (
    "legacy_dim_match:repository_verified_fotmob_match_id"
)
CWC_IDENTITY_PROVENANCE = "cwc_canonical_fixture:fotmob_match_id"
CWC_SNAPSHOT_PROVENANCE = "cwc_canonical_fixture:validated_offline"
CWC_OBSERVATION_SOURCE = "trial_synthetic_observation:canonical_fixture"
CWC_COMPETITION_SCOPE = "78"
CWC_SEASON_SCOPE = "2025"
CWC_IDENTITY_CREATED_AT = "2026-07-26T00:00:00Z"


class TrialSafetyError(RuntimeError):
    """The caller selected a path outside the isolated trial boundary."""


class TrialDataError(RuntimeError):
    """Legacy or fixture evidence does not satisfy the trial contract."""


@dataclass(frozen=True)
class FileFingerprint:
    path: Path
    device: int
    inode: int
    owner_uid: int
    mode: int
    link_count: int
    size: int
    mtime_ns: int
    sha256: str


@dataclass(frozen=True)
class SourceFingerprint:
    path: Path
    main: FileFingerprint
    wal: FileFingerprint | None
    shm: FileFingerprint | None


@dataclass(frozen=True)
class SQLiteSidecarSetFingerprint:
    wal: FileFingerprint | None
    shm: FileFingerprint | None
    journal: FileFingerprint | None


@dataclass(frozen=True)
class PreparedTrialCopy:
    run_id: str
    run_directory: Path
    run_directory_device: int
    run_directory_inode: int
    source: SourceFingerprint
    destination: Path
    destination_device: int
    destination_inode: int
    destination_sha256: str
    destination_companion_names: tuple[str, ...]
    destination_sidecars: SQLiteSidecarSetFingerprint
    recovery_image: Path
    recovery_device: int
    recovery_inode: int
    recovery_sha256: str
    recovery_companion_names: tuple[str, ...]
    recovery_sidecars: SQLiteSidecarSetFingerprint
    creator_pid: int
    creator_uid: int


_PREPARED_HANDLES: dict[str, PreparedTrialCopy] = {}


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def file_sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _temp_roots() -> frozenset[Path]:
    roots: set[Path] = set()
    for raw in (Path("/tmp"), Path(tempfile.gettempdir())):
        try:
            roots.add(raw.resolve(strict=True))
        except OSError:
            continue
    return frozenset(roots)


def _is_below_temp_root(path: Path) -> bool:
    return any(path == root or root in path.parents for root in _temp_roots())


def _fingerprint_regular_file(
    path: Path | str,
    *,
    require_current_owner: bool = False,
    require_mode: int | None = None,
    require_single_link: bool = False,
) -> FileFingerprint:
    candidate = Path(path)
    try:
        link_metadata = candidate.lstat()
        if stat.S_ISLNK(link_metadata.st_mode):
            raise TrialSafetyError("trial database safety boundary rejected")
        resolved = candidate.resolve(strict=True)
        metadata = resolved.stat()
    except (OSError, RuntimeError):
        raise TrialSafetyError("trial database safety boundary rejected") from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or (require_current_owner and metadata.st_uid != os.getuid())
        or (
            require_mode is not None
            and stat.S_IMODE(metadata.st_mode) != require_mode
        )
        or (require_single_link and metadata.st_nlink != 1)
    ):
        raise TrialSafetyError("trial database safety boundary rejected")
    return FileFingerprint(
        path=resolved,
        device=int(metadata.st_dev),
        inode=int(metadata.st_ino),
        owner_uid=int(metadata.st_uid),
        mode=stat.S_IMODE(metadata.st_mode),
        link_count=int(metadata.st_nlink),
        size=int(metadata.st_size),
        mtime_ns=int(metadata.st_mtime_ns),
        sha256=file_sha256(resolved),
    )


def _optional_sidecar_fingerprint(
    path: Path,
    *,
    require_current_owner: bool = False,
    require_mode: int | None = None,
    require_single_link: bool = False,
) -> FileFingerprint | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    return _fingerprint_regular_file(
        path,
        require_current_owner=require_current_owner,
        require_mode=require_mode,
        require_single_link=require_single_link,
    )


_BOUND_SQLITE_COMPANION_SUFFIXES = ("-wal", "-shm", "-journal")


def _scan_trial_companion_names(path: Path) -> tuple[str, ...]:
    allowed_names = {
        f"{path.name}{suffix}"
        for suffix in _BOUND_SQLITE_COMPANION_SUFFIXES
    }
    names: tuple[str, ...] = ()
    unbound_entries: list[tuple[str, int, int, int, int, int, int]] = []
    scan_failed = False
    try:
        with os.scandir(path.parent) as entries:
            matching_entries = [
                entry
                for entry in entries
                if entry.name.startswith(path.name)
                and entry.name != path.name
            ]
            names = tuple(sorted(entry.name for entry in matching_entries))
            for entry in matching_entries:
                if entry.name not in allowed_names:
                    metadata = entry.stat(follow_symlinks=False)
                    unbound_entries.append(
                        (
                            entry.name,
                            stat.S_IFMT(metadata.st_mode),
                            int(metadata.st_dev),
                            int(metadata.st_ino),
                            int(metadata.st_uid),
                            stat.S_IMODE(metadata.st_mode),
                            int(metadata.st_nlink),
                        )
                    )
    except OSError:
        scan_failed = True
    if scan_failed:
        raise TrialSafetyError(
            "prepared trial companion pathset safety boundary rejected"
        ) from None
    if unbound_entries:
        raise TrialSafetyError(
            "prepared trial unbound companion safety boundary rejected"
        ) from None
    return names


def _trial_sidecar_set(
    path: Path,
) -> tuple[tuple[str, ...], SQLiteSidecarSetFingerprint]:
    names_before = _scan_trial_companion_names(path)

    def capture(suffix: str) -> FileFingerprint | None:
        return _optional_sidecar_fingerprint(
            Path(f"{path}{suffix}"),
            require_current_owner=True,
            require_mode=0o600,
            require_single_link=True,
        )

    sidecars = SQLiteSidecarSetFingerprint(
        wal=capture("-wal"),
        shm=capture("-shm"),
        journal=capture("-journal"),
    )
    if sidecars.wal is not None and sidecars.wal.size != 0:
        raise TrialSafetyError(
            "prepared trial WAL safety boundary rejected"
        )
    if sidecars.journal is not None:
        raise TrialSafetyError(
            "prepared trial rollback journal safety boundary rejected"
        )
    names_after = _scan_trial_companion_names(path)
    if names_after != names_before:
        raise TrialSafetyError(
            "prepared trial companion pathset changed"
        )
    return names_after, sidecars


def _same_file_except_mtime(
    left: FileFingerprint | None,
    right: FileFingerprint | None,
) -> bool:
    if left is None or right is None:
        return left is right
    return (
        left.path,
        left.device,
        left.inode,
        left.owner_uid,
        left.mode,
        left.link_count,
        left.size,
        left.sha256,
    ) == (
        right.path,
        right.device,
        right.inode,
        right.owner_uid,
        right.mode,
        right.link_count,
        right.size,
        right.sha256,
    )


def _companion_state_matches_after_readonly_integrity(
    actual: tuple[tuple[str, ...], SQLiteSidecarSetFingerprint],
    expected: tuple[tuple[str, ...], SQLiteSidecarSetFingerprint],
) -> bool:
    # SQLite takes WAL read locks through the SHM mapping even for a read-only
    # integrity check.  That can advance only the SHM mtime; inode, ownership,
    # mode, link count, size, and content remain bound.
    actual_names, actual_sidecars = actual
    expected_names, expected_sidecars = expected
    return (
        actual_names == expected_names
        and actual_sidecars.wal == expected_sidecars.wal
        and _same_file_except_mtime(
            actual_sidecars.shm,
            expected_sidecars.shm,
        )
        and actual_sidecars.journal == expected_sidecars.journal
    )


def _source_fingerprint(path: Path | str) -> SourceFingerprint:
    candidate = Path(path)
    main = _fingerprint_regular_file(candidate)
    wal = _optional_sidecar_fingerprint(Path(f"{main.path}-wal"))
    shm = _optional_sidecar_fingerprint(Path(f"{main.path}-shm"))
    if wal is not None and wal.size != 0:
        raise TrialSafetyError("trial source WAL safety boundary rejected")
    return SourceFingerprint(path=main.path, main=main, wal=wal, shm=shm)


def _validate_temp_parent(path: Path | str | None) -> Path:
    candidate = (
        Path(tempfile.gettempdir()) if path is None else Path(path)
    )
    try:
        if candidate.is_symlink():
            raise TrialSafetyError("trial workspace safety boundary rejected")
        resolved = candidate.resolve(strict=True)
        metadata = resolved.stat()
    except (OSError, RuntimeError):
        raise TrialSafetyError("trial workspace safety boundary rejected") from None
    if (
        not _is_below_temp_root(resolved)
        or not stat.S_ISDIR(metadata.st_mode)
    ):
        raise TrialSafetyError("trial workspace safety boundary rejected")
    if resolved not in _temp_roots():
        if (
            metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise TrialSafetyError("trial workspace safety boundary rejected")
    return resolved


def _directory_identity(path: Path) -> tuple[int, int]:
    try:
        link_metadata = path.lstat()
        metadata = path.stat()
    except OSError:
        raise TrialSafetyError("trial workspace safety boundary rejected") from None
    if (
        stat.S_ISLNK(link_metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or not _is_below_temp_root(path.resolve(strict=True))
    ):
        raise TrialSafetyError("trial workspace safety boundary rejected")
    return int(metadata.st_dev), int(metadata.st_ino)


def _exclusive_create_file(path: Path) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise OSError("unsafe exclusive trial file")
        return descriptor
    except OSError:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise TrialSafetyError("trial file creation boundary rejected") from None


def _copy_source_bytes(source_path: Path, destination_fd: int) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        source_fd = os.open(source_path, flags)
        try:
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                view = memoryview(chunk)
                while view:
                    written = os.write(destination_fd, view)
                    if written <= 0:
                        raise OSError("trial copy made no progress")
                    view = view[written:]
        finally:
            os.close(source_fd)
        os.fsync(destination_fd)
    except OSError:
        raise TrialSafetyError("trial source copy boundary rejected") from None


def _discard_created_workspace(
    run_directory: Path,
    created_files: Sequence[Path],
) -> None:
    for path in reversed(tuple(created_files)):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        run_directory.rmdir()
    except OSError:
        pass


def _integrity_check_readonly(path: Path) -> str:
    failed = False
    result = ""
    try:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        try:
            result = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        finally:
            conn.close()
    except Exception:
        failed = True
    if failed or result != "ok":
        raise TrialSafetyError("trial copy integrity boundary rejected") from None
    return result


def prepare_trial_copy(
    source: Path | str,
    *,
    temp_root: Path | str | None = None,
) -> PreparedTrialCopy:
    """Create a new private exact copy and recovery image under a fresh run dir."""

    source_before = _source_fingerprint(source)
    parent = _validate_temp_parent(temp_root)
    try:
        run_directory = Path(
            tempfile.mkdtemp(prefix="allwin-schedule-trial-", dir=parent)
        )
        run_directory.chmod(0o700)
    except OSError:
        raise TrialSafetyError("trial workspace creation boundary rejected") from None
    created_files: list[Path] = []
    try:
        run_device, run_inode = _directory_identity(run_directory)
        destination = run_directory / "trial.db"
        recovery = run_directory / "recovery.db"

        destination_fd = _exclusive_create_file(destination)
        created_files.append(destination)
        try:
            _copy_source_bytes(source_before.path, destination_fd)
        finally:
            os.close(destination_fd)
        if _source_fingerprint(source_before.path) != source_before:
            raise TrialSafetyError("trial source changed during copy")

        recovery_fd = _exclusive_create_file(recovery)
        created_files.append(recovery)
        try:
            _copy_source_bytes(source_before.path, recovery_fd)
        finally:
            os.close(recovery_fd)
        if _source_fingerprint(source_before.path) != source_before:
            raise TrialSafetyError("trial source changed during copy")

        destination_meta = _fingerprint_regular_file(
            destination,
            require_current_owner=True,
            require_mode=0o600,
            require_single_link=True,
        )
        recovery_meta = _fingerprint_regular_file(
            recovery,
            require_current_owner=True,
            require_mode=0o600,
            require_single_link=True,
        )
        if (
            destination_meta.sha256 != source_before.main.sha256
            or recovery_meta.sha256 != source_before.main.sha256
            or (
                destination_meta.device,
                destination_meta.inode,
            )
            == (
                source_before.main.device,
                source_before.main.inode,
            )
            or (
                recovery_meta.device,
                recovery_meta.inode,
            )
            == (
                source_before.main.device,
                source_before.main.inode,
            )
        ):
            raise TrialSafetyError("trial exact copy boundary rejected")
        _integrity_check_readonly(destination)
        _integrity_check_readonly(recovery)
        destination_after_integrity = _fingerprint_regular_file(
            destination,
            require_current_owner=True,
            require_mode=0o600,
            require_single_link=True,
        )
        recovery_after_integrity = _fingerprint_regular_file(
            recovery,
            require_current_owner=True,
            require_mode=0o600,
            require_single_link=True,
        )
        if (
            destination_after_integrity != destination_meta
            or recovery_after_integrity != recovery_meta
        ):
            raise TrialSafetyError("trial exact copy boundary rejected")
        destination_companions = _trial_sidecar_set(destination)
        recovery_companions = _trial_sidecar_set(recovery)
        if _source_fingerprint(source_before.path) != source_before:
            raise TrialSafetyError("trial source changed during copy")
        if (
            _fingerprint_regular_file(
                destination,
                require_current_owner=True,
                require_mode=0o600,
                require_single_link=True,
            )
            != destination_after_integrity
            or _fingerprint_regular_file(
                recovery,
                require_current_owner=True,
                require_mode=0o600,
                require_single_link=True,
            )
            != recovery_after_integrity
            or _trial_sidecar_set(destination) != destination_companions
            or _trial_sidecar_set(recovery) != recovery_companions
        ):
            raise TrialSafetyError("trial exact copy boundary rejected")
        destination_names, destination_sidecars = destination_companions
        recovery_names, recovery_sidecars = recovery_companions
        handle = PreparedTrialCopy(
            run_id=run_directory.name,
            run_directory=run_directory,
            run_directory_device=run_device,
            run_directory_inode=run_inode,
            source=source_before,
            destination=destination,
            destination_device=destination_meta.device,
            destination_inode=destination_meta.inode,
            destination_sha256=destination_meta.sha256,
            destination_companion_names=destination_names,
            destination_sidecars=destination_sidecars,
            recovery_image=recovery,
            recovery_device=recovery_meta.device,
            recovery_inode=recovery_meta.inode,
            recovery_sha256=recovery_meta.sha256,
            recovery_companion_names=recovery_names,
            recovery_sidecars=recovery_sidecars,
            creator_pid=os.getpid(),
            creator_uid=os.getuid(),
        )
        _PREPARED_HANDLES[handle.run_id] = handle
        return handle
    except Exception:
        _discard_created_workspace(run_directory, created_files)
        raise


def _validate_prepared_trial_copy(handle: PreparedTrialCopy) -> Path:
    if (
        not isinstance(handle, PreparedTrialCopy)
        or _PREPARED_HANDLES.get(handle.run_id) is not handle
        or handle.creator_pid != os.getpid()
        or handle.creator_uid != os.getuid()
    ):
        raise TrialSafetyError("prepared trial handle boundary rejected")
    run_device, run_inode = _directory_identity(handle.run_directory)
    if (
        run_device != handle.run_directory_device
        or run_inode != handle.run_directory_inode
    ):
        raise TrialSafetyError("prepared trial workspace changed")
    if _source_fingerprint(handle.source.path) != handle.source:
        raise TrialSafetyError("prepared trial source changed")
    destination = _fingerprint_regular_file(
        handle.destination,
        require_current_owner=True,
        require_mode=0o600,
        require_single_link=True,
    )
    if (
        (destination.device, destination.inode)
        != (handle.destination_device, handle.destination_inode)
        or destination.sha256 != handle.destination_sha256
        or destination.sha256 != handle.source.main.sha256
    ):
        raise TrialSafetyError("prepared trial files changed")
    destination_companions = _trial_sidecar_set(handle.destination)
    if (
        destination_companions
        != (
            handle.destination_companion_names,
            handle.destination_sidecars,
        )
    ):
        raise TrialSafetyError("prepared trial sidecars changed")
    recovery = _fingerprint_regular_file(
        handle.recovery_image,
        require_current_owner=True,
        require_mode=0o600,
        require_single_link=True,
    )
    if (
        (recovery.device, recovery.inode)
        != (handle.recovery_device, handle.recovery_inode)
        or recovery.sha256 != handle.recovery_sha256
        or recovery.sha256 != handle.source.main.sha256
    ):
        raise TrialSafetyError("prepared trial files changed")
    recovery_companions = _trial_sidecar_set(handle.recovery_image)
    if (
        recovery_companions
        != (
            handle.recovery_companion_names,
            handle.recovery_sidecars,
        )
    ):
        raise TrialSafetyError("prepared trial sidecars changed")
    _integrity_check_readonly(handle.destination)
    destination_after_integrity = _fingerprint_regular_file(
        handle.destination,
        require_current_owner=True,
        require_mode=0o600,
        require_single_link=True,
    )
    companions_after_integrity = _trial_sidecar_set(handle.destination)
    if (
        destination_after_integrity != destination
        or not _companion_state_matches_after_readonly_integrity(
            companions_after_integrity,
            (
                handle.destination_companion_names,
                handle.destination_sidecars,
            ),
        )
    ):
        raise TrialSafetyError("prepared trial files changed")
    if _source_fingerprint(handle.source.path) != handle.source:
        raise TrialSafetyError("prepared trial source changed")
    if (
        _fingerprint_regular_file(
            handle.destination,
            require_current_owner=True,
            require_mode=0o600,
            require_single_link=True,
        )
        != destination_after_integrity
        or _trial_sidecar_set(handle.destination)
        != companions_after_integrity
    ):
        raise TrialSafetyError("prepared trial files changed")
    return handle.destination


def assert_temporary_database(path: Path | str) -> Path:
    """Read-only evidence helper for a private regular 0600 temp file."""

    resolved = Path(path).resolve(strict=True)
    if not _is_below_temp_root(resolved):
        raise TrialSafetyError("trial database safety boundary rejected")
    return _fingerprint_regular_file(
        resolved,
        require_current_owner=True,
        require_mode=0o600,
        require_single_link=True,
    ).path


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _typed_value(value: Any) -> Any:
    if value is None:
        return ["null", None]
    if isinstance(value, bytes):
        return ["blob", value.hex()]
    if isinstance(value, int):
        return ["integer", str(value)]
    if isinstance(value, float):
        return ["real", value.hex()]
    if isinstance(value, str):
        return ["text", value]
    raise TrialDataError("unsupported legacy SQLite value")


def _table_columns(
    conn: sqlite3.Connection,
    table: str,
) -> list[dict[str, Any]]:
    return [
        {
            "cid": int(row[0]),
            "name": str(row[1]),
            "type": str(row[2]),
            "notnull": int(row[3]),
            "default": row[4],
            "pk": int(row[5]),
        }
        for row in conn.execute(
            f"PRAGMA table_info({_quote_identifier(table)})"
        )
    ]


def _table_indexes(
    conn: sqlite3.Connection,
    table: str,
) -> list[dict[str, Any]]:
    indexes: list[dict[str, Any]] = []
    for row in conn.execute(
        f"PRAGMA index_list({_quote_identifier(table)})"
    ):
        name = str(row[1])
        sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
            (name,),
        ).fetchone()
        indexes.append(
            {
                "name": name,
                "unique": int(row[2]),
                "origin": str(row[3]),
                "partial": int(row[4]),
                "columns": [
                    item[2]
                    for item in conn.execute(
                        f"PRAGMA index_info({_quote_identifier(name)})"
                    )
                ],
                "sql": None if sql_row is None else sql_row[0],
            }
        )
    return sorted(indexes, key=lambda item: item["name"])


def _content_evidence(
    conn: sqlite3.Connection,
    table: str,
    columns: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    names = [str(column["name"]) for column in columns]
    pk_names = [
        str(column["name"])
        for column in sorted(columns, key=lambda item: int(item["pk"]))
        if int(column["pk"]) > 0
    ]
    quoted_columns = ", ".join(_quote_identifier(name) for name in names)
    if pk_names:
        order_clause = ", ".join(
            _quote_identifier(name) for name in pk_names
        )
        range_expression = ", ".join(
            _quote_identifier(name) for name in pk_names
        )
        key_kind = "primary_key"
    else:
        order_clause = "rowid"
        range_expression = "rowid"
        key_kind = "rowid"

    digest = hashlib.sha256()
    first_key: list[Any] | None = None
    last_key: list[Any] | None = None
    row_count = 0
    query = (
        f"SELECT {quoted_columns}, {range_expression} "
        f"FROM {_quote_identifier(table)} ORDER BY {order_clause}"
    )
    key_width = len(pk_names) if pk_names else 1
    for row in conn.execute(query):
        business = [_typed_value(value) for value in row[: len(names)]]
        keys = [_typed_value(value) for value in row[-key_width:]]
        digest.update(_canonical_json(business))
        digest.update(b"\n")
        if first_key is None:
            first_key = keys
        last_key = keys
        row_count += 1
    return {
        "row_count": row_count,
        "content_sha256": digest.hexdigest(),
        "key_kind": key_kind,
        "first_key": first_key,
        "last_key": last_key,
    }


def capture_legacy_baseline(
    conn: sqlite3.Connection,
    *,
    table_names: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Capture deterministic legacy schema/index/content evidence."""

    if table_names is None:
        table_names = tuple(
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' "
                "AND name NOT IN ('schema_migrations', 'sqlite_sequence') "
                "AND name NOT GLOB 'schedule_*' "
                "ORDER BY name"
            )
        )
    tables: dict[str, Any] = {}
    for table in table_names:
        schema_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if schema_row is None:
            raise TrialDataError("legacy table is missing")
        columns = _table_columns(conn, table)
        tables[table] = {
            "schema_sql": schema_row[0],
            "columns": columns,
            "indexes": _table_indexes(conn, table),
            **_content_evidence(conn, table, columns),
        }
    return {"tables": tables}


def compare_legacy_baseline(
    conn: sqlite3.Connection,
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare pre-migration legacy columns without hashing new columns."""

    violations: list[str] = []
    checked: dict[str, Any] = {}
    for table, expected_raw in baseline["tables"].items():
        expected = dict(expected_raw)
        actual_columns = _table_columns(conn, table)
        baseline_columns = list(expected["columns"])
        baseline_names = [column["name"] for column in baseline_columns]
        actual_prefix = actual_columns[: len(baseline_columns)]
        if actual_prefix != baseline_columns:
            violations.append(f"{table}:legacy_columns_changed")
        actual_content = _content_evidence(conn, table, baseline_columns)
        for field in (
            "row_count",
            "content_sha256",
            "key_kind",
            "first_key",
            "last_key",
        ):
            if actual_content[field] != expected[field]:
                violations.append(f"{table}:{field}_changed")
        actual_indexes = _table_indexes(conn, table)
        baseline_index_names = {
            item["name"] for item in expected["indexes"]
        }
        retained_indexes = [
            item
            for item in actual_indexes
            if item["name"] in baseline_index_names
        ]
        if retained_indexes != expected["indexes"]:
            violations.append(f"{table}:legacy_indexes_changed")
        schema_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if table != "dim_match" and schema_row[0] != expected["schema_sql"]:
            violations.append(f"{table}:schema_sql_changed")
        checked[table] = {
            "row_count": actual_content["row_count"],
            "content_sha256": actual_content["content_sha256"],
            "legacy_column_count": len(baseline_columns),
            "post_column_count": len(actual_columns),
        }
    return {"valid": not violations, "violations": violations, "tables": checked}


def _migrate_verified_trial_copy(candidate: Path) -> dict[str, Any]:
    """Apply the formal manifest to an isolated exact copy and verify legacy."""

    before_file = database_file_evidence(candidate)
    before_conn = sqlite3.connect(candidate)
    try:
        baseline = capture_legacy_baseline(before_conn)
        before_ledger = before_conn.execute(
            "SELECT version, name, checksum FROM schema_migrations "
            "ORDER BY version"
        ).fetchall()
    finally:
        before_conn.close()

    started = time.perf_counter()
    applied = schedule.apply_schedule_state_schema_v1(candidate)
    duration = time.perf_counter() - started
    post_conn = sqlite3.connect(candidate)
    post_conn.row_factory = sqlite3.Row
    post_conn.execute("PRAGMA foreign_keys = ON")
    post_conn.execute("PRAGMA recursive_triggers = ON")
    try:
        comparison = compare_legacy_baseline(post_conn, baseline)
        baseline_dim_columns = baseline["tables"]["dim_match"]["columns"]
        expected_dim_columns = [
            *baseline_dim_columns,
            {
                "cid": len(baseline_dim_columns),
                "name": "kickoff_precision",
                "type": "TEXT",
                "notnull": 0,
                "default": None,
                "pk": 0,
            },
            {
                "cid": len(baseline_dim_columns) + 1,
                "name": "kickoff_source",
                "type": "TEXT",
                "notnull": 0,
                "default": None,
                "pk": 0,
            },
        ]
        if _table_columns(post_conn, "dim_match") != expected_dim_columns:
            comparison["violations"].append(
                "dim_match:migration_columns_not_exact"
            )
            comparison["valid"] = False
        ledger = [
            dict(row)
            for row in post_conn.execute(
                "SELECT version, name, checksum, applied_at "
                "FROM schema_migrations ORDER BY version"
            )
        ]
        integrity = post_conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_issues = [
            tuple(row) for row in post_conn.execute("PRAGMA foreign_key_check")
        ]
        schedule_objects = [
            tuple(row)
            for row in post_conn.execute(
                "SELECT type, name FROM sqlite_master "
                "WHERE name GLOB 'schedule_*' "
                "OR name GLOB 'idx_schedule_*' "
                "OR name GLOB 'trg_schedule_*' "
                "OR name GLOB 'uq_schedule_*' "
                "OR name='current_schedule_match_state' "
                "ORDER BY type, name"
            )
        ]
        precision_counts = {
            str(precision): int(count)
            for precision, count in post_conn.execute(
                "SELECT kickoff_precision, COUNT(*) FROM dim_match "
                "GROUP BY kickoff_precision"
            )
        }
        source_non_null = int(
            post_conn.execute(
                "SELECT COUNT(*) FROM dim_match "
                "WHERE kickoff_source IS NOT NULL"
            ).fetchone()[0]
        )
    finally:
        post_conn.close()
    after_apply_file = database_file_evidence(candidate)

    # The first schema application owns a normal SQLite sidecar lifecycle.
    # Rebind only the exact known names after all connections close, and
    # immediately validate that state before the internal idempotency run.
    after_apply_companions = _trial_sidecar_set(candidate)
    noop_before = database_file_evidence(candidate)
    noop_started = time.perf_counter()
    noop_applied = schedule.apply_schedule_state_schema_v1(candidate)
    noop_duration = time.perf_counter() - noop_started
    noop_after = database_file_evidence(candidate)
    return {
        "applied": applied,
        "duration_seconds": duration,
        "before_file": before_file,
        "after_apply_file": after_apply_file,
        "after_apply_companion_names": list(after_apply_companions[0]),
        "before_ledger": [list(row) for row in before_ledger],
        "ledger": ledger,
        "legacy": comparison,
        "integrity_check": integrity,
        "foreign_key_issues": foreign_key_issues,
        "schedule_object_count": len(schedule_objects),
        "schedule_objects": schedule_objects,
        "kickoff_precision_counts": precision_counts,
        "kickoff_source_non_null": source_non_null,
        "noop": {
            "applied": noop_applied,
            "duration_seconds": noop_duration,
            "before": noop_before,
            "after": noop_after,
            "content_unchanged": (
                noop_before["sha256"] == noop_after["sha256"]
                and noop_before["size"] == noop_after["size"]
            ),
            "metadata_unchanged": (
                noop_before["mtime_ns"] == noop_after["mtime_ns"]
            ),
        },
    }


def migrate_prepared_trial_copy(
    handle: PreparedTrialCopy,
) -> dict[str, Any]:
    """Migrate only an unchanged handle prepared by this process."""

    candidate = _validate_prepared_trial_copy(handle)
    _PREPARED_HANDLES.pop(handle.run_id, None)
    result = _migrate_verified_trial_copy(candidate)
    destination_after = _fingerprint_regular_file(
        candidate,
        require_current_owner=True,
        require_mode=0o600,
        require_single_link=True,
    )
    destination_companions = _trial_sidecar_set(candidate)
    recovery = _fingerprint_regular_file(
        handle.recovery_image,
        require_current_owner=True,
        require_mode=0o600,
        require_single_link=True,
    )
    recovery_companions = _trial_sidecar_set(handle.recovery_image)
    if (
        (recovery.device, recovery.inode)
        != (handle.recovery_device, handle.recovery_inode)
        or recovery.sha256 != handle.recovery_sha256
        or recovery_companions
        != (
            handle.recovery_companion_names,
            handle.recovery_sidecars,
        )
    ):
        raise TrialSafetyError("prepared trial recovery image changed")
    result["destination_after"] = {
        "device": destination_after.device,
        "inode": destination_after.inode,
        "owner_uid": destination_after.owner_uid,
        "mode": destination_after.mode,
        "link_count": destination_after.link_count,
        "size": destination_after.size,
        "mtime_ns": destination_after.mtime_ns,
        "sha256": destination_after.sha256,
    }
    result["final_companion_names"] = list(destination_companions[0])
    return result


def migrate_exact_copy(handle: PreparedTrialCopy) -> dict[str, Any]:
    """Compatibility name that deliberately rejects arbitrary raw paths."""

    if not isinstance(handle, PreparedTrialCopy):
        raise TrialSafetyError("prepared trial handle boundary rejected")
    return migrate_prepared_trial_copy(handle)


def audit_legacy_dim_match(
    conn: sqlite3.Connection,
    *,
    repository_provenance_verified: bool,
) -> dict[str, Any]:
    """Classify identity-safe fields separately from unsafe legacy state."""

    row = conn.execute(
        """
        SELECT
          COUNT(*) AS total,
          SUM(Match_ID IS NOT NULL) AS non_null,
          COUNT(DISTINCT Match_ID) AS unique_count,
          SUM(
            Match_ID IS NULL
            OR typeof(Match_ID) <> 'integer'
            OR Match_ID <= 0
          ) AS invalid,
          SUM(League_ID IS NULL) AS missing_competition,
          SUM(Home_Team_ID IS NULL OR Away_Team_ID IS NULL) AS missing_team_xref,
          SUM(kickoff_at_utc IS NOT NULL) AS kickoff_present,
          SUM(Date IS NOT NULL) AS date_present
        FROM dim_match
        """
    ).fetchone()
    total = int(row[0])
    non_null = int(row[1])
    unique_count = int(row[2])
    invalid = int(row[3])
    duplicate_count = total - unique_count
    status_counts = {
        str(status): int(count)
        for status, count in conn.execute(
            "SELECT COALESCE(status, '<NULL>'), COUNT(*) "
            "FROM dim_match GROUP BY status ORDER BY status"
        )
    }
    identity_gate = (
        repository_provenance_verified
        and total > 0
        and non_null == total
        and unique_count == total
        and invalid == 0
    )
    return {
        "total": total,
        "non_null_match_ids": non_null,
        "unique_match_ids": unique_count,
        "duplicate_match_ids": duplicate_count,
        "invalid_match_ids": invalid,
        "missing_competition_xref": int(row[4]),
        "missing_team_xref": int(row[5]),
        "kickoff_at_utc_present": int(row[6]),
        "date_present": int(row[7]),
        "status_counts": status_counts,
        "provider_provenance": (
            "repository_verified_fotmob_ingestion"
            if repository_provenance_verified
            else "unverified"
        ),
        "stable_identity_eligible": total if identity_gate else 0,
        "state_snapshot_eligible": 0,
        "requires_state_recollection": total,
        "identity_gate_passed": identity_gate,
        "state_exclusion_reasons": [
            "kickoff_at_utc_and_kickoff_source_are_unproven",
            "legacy_date_is_date_only_not_exact_kickoff",
            "legacy_status_does_not_prove_finished_or_cancelled",
            "no_source_observation_time",
        ],
    }


def backfill_legacy_identities(
    conn: sqlite3.Connection,
    *,
    audit: Mapping[str, Any],
    identity_created_at: str,
) -> dict[str, Any]:
    if not audit.get("identity_gate_passed"):
        raise TrialDataError("legacy identity provenance gate is closed")
    before_dim_match = conn.execute(
        "SELECT COUNT(*) FROM dim_match"
    ).fetchone()[0]
    before_snapshots = conn.execute(
        "SELECT COUNT(*) FROM schedule_match_state_snapshot"
    ).fetchone()[0]
    inserted = 0
    skipped = 0
    started = time.perf_counter()
    for (match_id,) in conn.execute(
        "SELECT Match_ID FROM dim_match ORDER BY Match_ID"
    ).fetchall():
        result = schedule.record_match_identity(
            conn,
            provider="fotmob",
            provider_match_id=int(match_id),
            canonical_match_id=int(match_id),
            identity_created_at=identity_created_at,
            identity_provenance=IDENTITY_PROVENANCE,
        )
        if result["inserted"]:
            inserted += 1
        else:
            skipped += 1
    duration = time.perf_counter() - started
    if inserted + skipped != int(audit["stable_identity_eligible"]):
        raise TrialDataError("legacy identity population changed after audit")
    if conn.execute("SELECT COUNT(*) FROM dim_match").fetchone()[0] != before_dim_match:
        raise TrialDataError("legacy dim_match changed during identity backfill")
    if (
        conn.execute(
            "SELECT COUNT(*) FROM schedule_match_state_snapshot"
        ).fetchone()[0]
        != before_snapshots
    ):
        raise TrialDataError("identity backfill invented state snapshots")
    return {
        "expected": int(audit["stable_identity_eligible"]),
        "inserted": inserted,
        "skipped": skipped,
        "conflicted": 0,
        "duration_seconds": duration,
    }


def _fixture_payload_hash(document: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(document)).hexdigest()


def import_cwc_formal_schema(
    conn: sqlite3.Connection,
    *,
    document: Mapping[str, Any],
    observed_at: str,
    ingested_at: str,
    computed_at: str,
    poll_run_id: str,
    identity_created_at: str = CWC_IDENTITY_CREATED_AT,
) -> dict[str, Any]:
    """Import the validated fixture using only the formal state service."""

    batch = cwc.parse_canonical_fixture(document, observed_at=observed_at)
    payload_hash = _fixture_payload_hash(document)
    identity_inserted = 0
    snapshot_inserted = 0
    observation_inserted = 0
    import_started = time.perf_counter()
    for row in batch["matches"]:
        result = schedule.record_match_state(
            conn,
            provider="fotmob",
            provider_match_id=row["provider_match_id"],
            canonical_match_id=None,
            identity_created_at=identity_created_at,
            identity_provenance=CWC_IDENTITY_PROVENANCE,
            state={
                "kickoff_at_utc": row["kickoff_at_utc"],
                "kickoff_precision": row["kickoff_precision"],
                "status": row["status"],
                "finished": bool(row["finished"]),
                "cancelled": bool(row["cancelled"]),
                "home_team_id": row["home_team_id"],
                "home_team_name": row["home_team_name"],
                "away_team_id": row["away_team_id"],
                "away_team_name": row["away_team_name"],
                "competition_id": str(row["competition_id"]),
                "season_label": row["requested_season"],
                "round_label": row["round"],
                "stage_label": None,
                "competition_class": row["competition_class"],
                "competition_verified": True,
            },
            source_updated_at=None,
            snapshot_provenance=CWC_SNAPSHOT_PROVENANCE,
            source=CWC_OBSERVATION_SOURCE,
            competition_scope=CWC_COMPETITION_SCOPE,
            season_scope=CWC_SEASON_SCOPE,
            observed_at=observed_at,
            poll_run_id=poll_run_id,
            payload_hash=payload_hash,
            ingested_at=ingested_at,
        )
        identity_inserted += int(result["identity_inserted"])
        snapshot_inserted += int(result["snapshot_inserted"])
        observation_inserted += int(result["observation_inserted"])
    import_duration = time.perf_counter() - import_started

    feature_started = time.perf_counter()
    feature_result = schedule.build_observed_rest_features_as_of(
        conn,
        as_of_observed_at=observed_at,
        computed_at=computed_at,
    )
    feature_duration = time.perf_counter() - feature_started
    return {
        "identity_inserted": identity_inserted,
        "identity_skipped": len(batch["matches"]) - identity_inserted,
        "snapshot_inserted": snapshot_inserted,
        "snapshot_skipped": len(batch["matches"]) - snapshot_inserted,
        "observation_inserted": observation_inserted,
        "observation_skipped": len(batch["matches"]) - observation_inserted,
        "feature_inserted": feature_result["inserted"],
        "feature_skipped": feature_result["skipped"],
        "import_duration_seconds": import_duration,
        "feature_duration_seconds": feature_duration,
        "payload_hash": payload_hash,
    }


def schedule_counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = (
        "schedule_match_identity",
        "schedule_match_state_snapshot",
        "schedule_observation_event",
        "schedule_match_observation",
        "schedule_rest_lineage_set",
        "schedule_rest_lineage_input",
        "schedule_rest_feature",
    )
    return {
        name: int(
            conn.execute(
                f"SELECT COUNT(*) FROM {_quote_identifier(name)}"
            ).fetchone()[0]
        )
        for name in names
    }


def cwc_business_evidence(conn: sqlite3.Connection) -> dict[str, Any]:
    current = conn.execute(
        "SELECT * FROM current_schedule_match_state "
        "WHERE competition_id='78' AND season_label='2025'"
    ).fetchall()
    city_rows = conn.execute(
        """
        SELECT feature.feature_value_json
        FROM schedule_rest_feature AS feature
        JOIN schedule_match_identity AS identity
          ON identity.id = feature.target_match_identity_id
        JOIN schedule_match_state_snapshot AS snapshot
          ON snapshot.id = feature.target_snapshot_id
        WHERE feature.team_id=8456
          AND snapshot.competition_id='78'
          AND snapshot.season_label='2025'
        ORDER BY snapshot.kickoff_at_utc, identity.provider_match_id
        """
    ).fetchall()
    city_gaps = [
        json.loads(row[0])["kickoff_gap_hours"] for row in city_rows
    ]
    return {
        "current_count": len(current),
        "non_cancelled": sum(row["cancelled"] == 0 for row in current),
        "cancelled": sum(row["cancelled"] == 1 for row in current),
        "team_relationships_expressed_in_snapshots": 2 * len(current),
        "aet_count": sum(row["status"] == "AET" for row in current),
        "city_feature_count": len(city_rows),
        "city_gap_hours": city_gaps,
    }


def query_plan_evidence(
    conn: sqlite3.Connection,
    *,
    identity_id: int,
    as_of_observed_at: str,
) -> dict[str, Any]:
    current_plan = [
        row[3]
        for row in conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT * FROM current_schedule_match_state "
            "WHERE match_identity_id=?",
            (identity_id,),
        )
    ]
    as_of_plan = [
        row[3]
        for row in conn.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT association.id
            FROM schedule_match_observation AS association
            WHERE association.match_identity_id=?
              AND association.observed_at<=?
            ORDER BY association.observed_at DESC, association.id DESC
            LIMIT 1
            """,
            (identity_id, schedule._utc(as_of_observed_at)),
        )
    ]
    identity_plan = [
        row[3]
        for row in conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT id FROM schedule_match_identity "
            "WHERE provider=? AND provider_match_id=?",
            ("fotmob", "4685708"),
        )
    ]
    return {
        "current": current_plan,
        "as_of": as_of_plan,
        "identity": identity_plan,
    }


def database_file_evidence(path: Path | str) -> dict[str, Any]:
    candidate = assert_temporary_database(path)
    stat_result = candidate.stat()
    conn = sqlite3.connect(f"{candidate.as_uri()}?mode=ro", uri=True)
    try:
        integrity_check = conn.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]
    finally:
        conn.close()
    return {
        "path": str(candidate),
        "sha256": file_sha256(candidate),
        "size": stat_result.st_size,
        "mode": oct(stat_result.st_mode & 0o777),
        "mtime_ns": stat_result.st_mtime_ns,
        "integrity_check": integrity_check,
    }
