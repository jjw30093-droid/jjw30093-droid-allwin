"""Permanent closure gates for artifact trust and durable shadow recovery."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import traceback
from contextlib import contextmanager
from pathlib import Path

import pytest

from analysis.schedule_shadow_ingestion import schedule_shadow_ingestion as shadow
from analysis.schedule_state_migration_trial import (
    schedule_state_migration_trial as trial,
)
from backend.db import migrate
from backend.schedules import fotmob_schedule
from backend.schedules import state as schedule


REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_FIXTURE = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "fotmob"
    / "cwc_2025_competition_schedule_raw.json"
)
RAW_FIXTURE_SHA256 = (
    "b2852c04cdcfd812a92164e482309cdca634c3a38dca4973adcf94a3ebbc67fc"
)
CANONICAL_FIXTURE = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "fotmob"
    / "cwc_2025_competition_schedule_canonical.json"
)
CANONICAL_FIXTURE_SHA256 = (
    "020b6eabecd9ca004611863d3b3f5820ee12ea2f1ff8f203f51e73a1bbf276b1"
)
T0 = "2026-07-26T00:00:00Z"


def _evidence(
    *,
    count: int = 66,
    pagination_status: str = "NOT_DETECTED",
    detected: list[str] | None = None,
    unresolved: list[str] | None = None,
) -> dict[str, object]:
    return {
        "competition_identity_verified": True,
        "competition_name": "FIFA Club World Cup",
        "competition_class": "international_club",
        "competition_class_verified": True,
        "returned_season": "2025",
        "fixture_schema_valid": True,
        "fixture_count": count,
        "pagination_status": pagination_status,
        "pagination_detected_evidence": detected or [],
        "pagination_unresolved_evidence": unresolved or [],
        "pagination_unknown_evidence": [],
        "observation_time_provenance": (
            "caller_supplied_synthetic_offline_event_time"
        ),
    }


def _load(
    path: Path = RAW_FIXTURE,
    digest: str = RAW_FIXTURE_SHA256,
    *,
    evidence: dict[str, object] | None = None,
) -> shadow.ArtifactEnvelope:
    return shadow.load_artifact_envelope(
        path,
        expected_sha256=digest,
        provider="fotmob",
        source_operation="league_matches_saved_raw_fixture",
        competition_id=78,
        requested_season="2025",
        observed_at=T0,
        artifact_schema_version="cwc_schedule_raw_projection_v1",
        completeness_status="COMPLETE",
        completeness_evidence=_evidence() if evidence is None else evidence,
    )


def _load_legacy_canonical() -> shadow.ArtifactEnvelope:
    evidence = _evidence()
    evidence.pop("competition_name")
    return shadow.load_artifact_envelope(
        CANONICAL_FIXTURE,
        expected_sha256=CANONICAL_FIXTURE_SHA256,
        provider="fotmob",
        source_operation="league_matches_saved_fixture",
        competition_id=78,
        requested_season="2025",
        observed_at=T0,
        artifact_schema_version="cwc_schedule_canonical_v1",
        completeness_status="COMPLETE",
        completeness_evidence=evidence,
    )


def _write_private(path: Path, raw: bytes) -> str:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            assert written > 0
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return hashlib.sha256(raw).hexdigest()


def _write_document(path: Path, value: object) -> str:
    return _write_private(
        path,
        (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8"),
    )


def _load_bytes(tmp_path: Path, raw: bytes, name: str) -> shadow.ArtifactEnvelope:
    path = tmp_path / name
    digest = _write_private(path, raw)
    return _load(path, digest)


def _create_v1_source(path: Path, tmp_path: Path) -> Path:
    path.touch(mode=0o600)
    path.chmod(0o600)
    staged = tmp_path / f"{path.stem}-migrations"
    staged.mkdir(mode=0o700)
    source = migrate.MIGRATIONS_ROOT / "core"
    name = "0001_dim_match_kickoff.sql"
    (staged / name).write_bytes((source / name).read_bytes())
    assert migrate.apply_all(
        "core",
        db_file=path,
        migrations_dir=staged,
        quiet=True,
    ) == 1
    return path


def _session(tmp_path: Path) -> shadow.PreparedShadowSession:
    source = _create_v1_source(tmp_path / "source-v1.db", tmp_path)
    prepared = trial.prepare_trial_copy(source, temp_root=tmp_path)
    return shadow.open_shadow_session(prepared)


def _db_path(session: shadow.PreparedShadowSession) -> Path:
    return shadow._session_binding(session).database_path


def _business_counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(path)
    try:
        return {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "schedule_match_identity",
                "schedule_match_state_snapshot",
                "schedule_observation_event",
                "schedule_match_observation",
                "schedule_rest_lineage_set",
                "schedule_rest_lineage_input",
                "schedule_rest_feature",
            )
        }
    finally:
        conn.close()


def _assert_safe(exc: BaseException, markers: tuple[str, ...] = ()) -> None:
    surfaces = (
        str(exc),
        repr(exc),
        repr(exc.args),
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    )
    for marker in markers:
        assert all(marker not in surface for surface in surfaces)
    assert exc.__cause__ is None
    assert exc.__context__ is None


@pytest.mark.parametrize(
    "raw",
    [
        b'{"details":{},"details":{}}',
        b'{"details":{"id":78,"id":78}}',
        b'{"fixtures":{"allMatches":[{"id":1,"id":1}]}}',
        b'{"details":{"id":78,"id":79}}',
    ],
    ids=[
        "top-level-same",
        "nested-same",
        "fixture-id-same",
        "nested-conflict",
    ],
)
def test_strict_json_rejects_duplicate_keys_at_every_depth(tmp_path, raw):
    with pytest.raises(
        shadow.ShadowArtifactError,
        match="artifact validation failed",
    ) as captured:
        _load_bytes(tmp_path, raw, "duplicate.json")
    _assert_safe(captured.value)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b'{"value":-Infinity}',
        b"\xef\xbb\xbf{}",
        b"\xff\xfe{}",
        b"{} trailing",
        b"[]",
        (b'{"value":' + (b"[" * 80) + b"0" + (b"]" * 80) + b"}"),
    ],
    ids=[
        "nan",
        "infinity",
        "minus-infinity",
        "bom",
        "invalid-utf8",
        "trailing-json",
        "top-level-array",
        "nesting-budget",
    ],
)
def test_strict_json_fixed_error_surface(tmp_path, raw):
    marker = "INVALID_JSON_MARKER_42"
    with pytest.raises(
        shadow.ShadowArtifactError,
        match="artifact validation failed",
    ) as captured:
        _load_bytes(tmp_path, raw + marker.encode(), "invalid.json")
    _assert_safe(captured.value, (marker, str(tmp_path)))


def test_artifact_size_limit_accepts_exact_limit_and_rejects_limit_plus_one(
    tmp_path,
):
    limit = shadow.MAX_SHADOW_ARTIFACT_BYTES
    exact = b"{}" + (b" " * (limit - 2))
    envelope = _load_bytes(tmp_path, exact, "exact-limit.json")
    assert envelope.payload == {}
    with pytest.raises(shadow.ShadowArtifactError) as captured:
        _load_bytes(tmp_path, exact + b" ", "over-limit.json")
    _assert_safe(captured.value, (str(tmp_path),))


def test_artifact_rejects_symlink_and_hardlink_without_reading_target(tmp_path):
    target = tmp_path / "target.json"
    digest = _write_private(target, b"{}")
    symlink = tmp_path / "symlink.json"
    symlink.symlink_to(target)
    hardlink = tmp_path / "hardlink.json"
    os.link(target, hardlink)
    for path in (symlink, hardlink):
        with pytest.raises(shadow.ShadowArtifactError) as captured:
            _load(path, digest)
        _assert_safe(captured.value, (str(path),))


def test_artifact_hash_and_parser_consume_the_same_immutable_bytes(
    monkeypatch,
):
    raw = RAW_FIXTURE.read_bytes()
    parsed_sha256 = None
    original = shadow.json.loads

    def observed_loads(value, *args, **kwargs):
        nonlocal parsed_sha256
        parsed_sha256 = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return original(value, *args, **kwargs)

    monkeypatch.setattr(shadow.json, "loads", observed_loads)
    envelope = _load()
    assert envelope.artifact_sha256 == hashlib.sha256(raw).hexdigest()
    assert parsed_sha256 == envelope.artifact_sha256


def test_loaded_envelope_rejects_in_memory_payload_and_evidence_mutation():
    payload_mutated = _load()
    payload_mutated.payload["fixtures"]["allMatches"].reverse()
    with pytest.raises(shadow.ShadowArtifactError):
        shadow.validate_artifact_envelope(payload_mutated)

    evidence_mutated = _load()
    evidence_mutated.completeness_evidence["competition_name"] = (
        "Mutated Competition"
    )
    with pytest.raises(shadow.ShadowArtifactError):
        shadow.validate_artifact_envelope(evidence_mutated)


def test_raw_fixture_cross_validates_identity_season_count_and_pagination():
    validated = shadow.validate_artifact_envelope(_load())
    assert len(validated.rows) == 66
    assert {row.state["status"] for row in validated.rows} == {"FT", "AET", "Can"}


def test_raw_fixture_provenance_and_sanitized_surface_are_fixed():
    document = json.loads(RAW_FIXTURE.read_text(encoding="utf-8"))
    assert document["artifactProvenance"]["sourceArtifactSha256"] == (
        "6e0007cecea78d59b7d771d689a0e24d45dc4b2bbf7776a3a58516fbb71bae3d"
    )
    folded = RAW_FIXTURE.read_text(encoding="utf-8").casefold()
    for forbidden in (
        "http://",
        "https://",
        "authorization",
        "credential",
        "proxy",
        "api-key",
        "x-api-key",
    ):
        assert forbidden not in folded


@pytest.mark.parametrize(
    ("marker", "value"),
    [
        ("hasMore", True),
        ("page", 1),
        ("pageCount", 2),
        ("totalPages", 2),
    ],
)
def test_raw_pagination_cannot_be_cleared_by_envelope_claim(
    tmp_path,
    marker,
    value,
):
    document = json.loads(RAW_FIXTURE.read_text(encoding="utf-8"))
    document["fixtures"][marker] = value
    path = tmp_path / f"pagination-{marker}.json"
    digest = _write_document(path, document)
    envelope = _load(path, digest, evidence=_evidence())
    with pytest.raises(shadow.ShadowArtifactError):
        shadow.validate_artifact_envelope(envelope)


@pytest.mark.parametrize(
    "fields",
    [
        {"page": 1, "totalPages": 2},
        {"page": 1, "pageCount": 2},
        {"page": 1, "PAGE": 1},
        {"currentPage": 1, "pageCount": 2},
        {"page": 1, "totalPages": 2, "pageCount": 1},
    ],
    ids=[
        "page-total",
        "page-count",
        "casefold-collision",
        "orphan",
        "dialect-conflict",
    ],
)
def test_all_raw_pagination_dialects_and_conflicts_fail_closed(tmp_path, fields):
    document = json.loads(RAW_FIXTURE.read_text(encoding="utf-8"))
    document["fixtures"].update(fields)
    path = tmp_path / "pagination.json"
    envelope = _load(path, _write_document(path, document))
    with pytest.raises(shadow.ShadowArtifactError):
        shadow.validate_artifact_envelope(envelope)


@pytest.mark.parametrize(
    ("short", "started", "finished", "cancelled"),
    [
        ("NS", True, True, False),
        ("FT", False, False, False),
        ("Can", True, True, False),
        ("UNKNOWN", False, False, False),
        ("LIVE", True, False, False),
    ],
)
def test_status_flag_matrix_rejects_contradictions_and_unsupported_live_states(
    short,
    started,
    finished,
    cancelled,
):
    payload = json.loads(CANONICAL_FIXTURE.read_text(encoding="utf-8"))
    payload["fixtures"][0]["status"].update(
        {
            "short": short,
            "started": started,
            "finished": finished,
            "cancelled": cancelled,
        }
    )
    with pytest.raises(fotmob_schedule.FotMobScheduleDataError):
        fotmob_schedule.normalize_canonical_schedule_payload(
            payload,
            expected_competition_id=78,
            requested_season="2025",
            competition_class="international_club",
            competition_class_verified=True,
            artifact_schema_version="cwc_schedule_canonical_v1",
        )


def test_supported_status_flag_matrix_is_explicit_and_exact():
    assert fotmob_schedule.SUPPORTED_STATUS_FLAG_MATRIX == {
        "NS": (False, False, False),
        "FT": (True, True, False),
        "AET": (True, True, False),
        "Pen": (True, True, False),
        "Can": (False, False, True),
    }


def test_status_flags_reject_integer_boolean_substitutes():
    payload = json.loads(RAW_FIXTURE.read_text(encoding="utf-8"))
    payload["fixtures"]["allMatches"][0]["status"]["started"] = 1
    with pytest.raises(fotmob_schedule.FotMobScheduleDataError):
        fotmob_schedule.normalize_raw_schedule_payload(
            payload,
            expected_competition_id=78,
            expected_competition_name="FIFA Club World Cup",
            requested_season="2025",
            competition_class="international_club",
            competition_class_verified=True,
            artifact_schema_version="cwc_schedule_raw_projection_v1",
        )


def test_forged_completed_manifest_never_returns_forged_result(tmp_path):
    session = _session(tmp_path)
    envelope = _load_legacy_canonical()
    path = shadow._manifest_path(session, "forged-completed")
    forged = {
        **shadow._manifest_identity(envelope, "forged-completed"),
        "status": "COMPLETED",
        "started": True,
        "completed": True,
        "failed": False,
        "resume_count": 0,
        "phase": "COMPLETED",
        "last_successful_phase": "COMPLETED",
        "history": [
            "ARTIFACT_VALIDATED",
            "STATE_APPLIED",
            "FEATURES_APPLIED",
            "COMPLETED",
        ],
        "normalized_match_count": 66,
        "result": {"forged": True},
    }
    shadow._write_manifest(path, forged)
    with pytest.raises(shadow.ShadowError):
        shadow.run_shadow_ingestion(
            session,
            envelope,
            run_id="forged-completed",
        )
    assert all(value == 0 for value in _business_counts(_db_path(session)).values())


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("NEW", "STATE_APPLIED"),
        ("NEW", "COMPLETED"),
        ("ARTIFACT_VALIDATED", "FEATURES_APPLIED"),
        ("ARTIFACT_VALIDATED", "COMPLETED"),
        ("STATE_APPLIED", "COMPLETED"),
        ("FEATURES_APPLIED", "STATE_APPLIED"),
        ("COMPLETED", "FAILED"),
        ("COMPLETED", "COMPLETED"),
    ],
)
def test_phase_transition_table_rejects_every_jump_regression_and_overwrite(
    current,
    target,
):
    with pytest.raises(shadow.ShadowSessionError):
        shadow._validate_phase_transition(current, target)


_PHASES = (
    "NEW",
    "ARTIFACT_VALIDATED",
    "STATE_APPLIED",
    "FEATURES_APPLIED",
    "COMPLETED",
    "FAILED",
)
_ALLOWED_PHASE_PAIRS = {
    ("NEW", "ARTIFACT_VALIDATED"),
    ("ARTIFACT_VALIDATED", "STATE_APPLIED"),
    ("STATE_APPLIED", "FEATURES_APPLIED"),
    ("FEATURES_APPLIED", "COMPLETED"),
    ("NEW", "FAILED"),
    ("ARTIFACT_VALIDATED", "FAILED"),
    ("STATE_APPLIED", "FAILED"),
    ("FEATURES_APPLIED", "FAILED"),
}


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (current, target)
        for current in _PHASES
        for target in _PHASES
        if (current, target) not in _ALLOWED_PHASE_PAIRS
    ],
)
def test_phase_transition_table_rejects_all_other_phase_pairs(current, target):
    with pytest.raises(shadow.ShadowSessionError):
        shadow._validate_phase_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    sorted(_ALLOWED_PHASE_PAIRS),
)
def test_phase_transition_table_accepts_only_declared_forward_pairs(
    current,
    target,
):
    shadow._validate_phase_transition(current, target)


def test_manifest_parser_rejects_duplicate_keys(tmp_path):
    session = _session(tmp_path)
    path = shadow._manifest_path(session, "duplicate-manifest")
    raw = b'{"phase":"ARTIFACT_VALIDATED","phase":"COMPLETED"}'
    _write_private(path, raw)
    with pytest.raises(shadow.ShadowSessionError) as captured:
        shadow.read_shadow_run_manifest(session, "duplicate-manifest")
    _assert_safe(captured.value, (str(path),))


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_manifest_rejects_symlink_and_hardlink(link_kind, tmp_path):
    session = _session(tmp_path)
    path = shadow._manifest_path(session, f"{link_kind}-manifest")
    target = tmp_path / f"{link_kind}-target.json"
    _write_private(target, b"{}")
    if link_kind == "symlink":
        path.symlink_to(target)
    else:
        os.link(target, path)
    before = target.read_bytes()
    with pytest.raises(shadow.ShadowSessionError) as captured:
        shadow.read_shadow_run_manifest(session, f"{link_kind}-manifest")
    _assert_safe(captured.value, (str(path),))
    assert target.read_bytes() == before


def test_manifest_atomic_write_fsyncs_file_and_parent_directory(
    tmp_path,
    monkeypatch,
):
    session = _session(tmp_path)
    path = shadow._manifest_path(session, "fsync-boundary")
    observed_types: list[int] = []
    original = shadow.os.fsync

    def observed_fsync(descriptor):
        observed_types.append(stat.S_IFMT(os.fstat(descriptor).st_mode))
        return original(descriptor)

    monkeypatch.setattr(shadow.os, "fsync", observed_fsync)
    shadow._write_manifest(path, {"run_id": "fsync-boundary"})
    assert stat.S_IFREG in observed_types
    assert stat.S_IFDIR in observed_types
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(path.parent.glob("*.tmp-*"))


def test_manifest_ahead_of_empty_database_fails_closed(tmp_path):
    session = _session(tmp_path)
    envelope = _load()
    with pytest.raises(shadow.ShadowInjectedCrash):
        shadow.run_shadow_ingestion(
            session,
            envelope,
            run_id="manifest-ahead",
            fault_point=shadow.FAULT_AFTER_ARTIFACT_VALIDATED,
        )
    manifest = shadow.read_shadow_run_manifest(session, "manifest-ahead")
    shadow._advance_manifest(
        session,
        manifest,
        "STATE_APPLIED",
        state_apply_summary={"forged": True},
    )
    before = _business_counts(_db_path(session))
    with pytest.raises(shadow.ShadowIngestionError):
        shadow.run_shadow_ingestion(
            session,
            envelope,
            run_id="manifest-ahead",
        )
    assert _business_counts(_db_path(session)) == before
    assert all(value == 0 for value in before.values())


def test_completed_cached_result_tamper_is_rejected_by_database_truth(tmp_path):
    session = _session(tmp_path)
    envelope = _load()
    shadow.run_shadow_ingestion(
        session,
        envelope,
        run_id="completed-cache-tamper",
    )
    before = _business_counts(_db_path(session))
    manifest = shadow.read_shadow_run_manifest(
        session,
        "completed-cache-tamper",
    )
    manifest["result"] = {"forged": True}
    shadow._write_manifest(
        shadow._manifest_path(session, "completed-cache-tamper"),
        manifest,
    )
    with pytest.raises(shadow.ShadowIngestionError):
        shadow.run_shadow_ingestion(
            session,
            envelope,
            run_id="completed-cache-tamper",
        )
    assert _business_counts(_db_path(session)) == before


def _subprocess_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPYCACHEPREFIX"] = os.environ["PYTHONPYCACHEPREFIX"]
    environment["PYTHONPATH"] = str(REPO_ROOT)
    return environment


def _run_process(code: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code, *arguments],
        cwd=REPO_ROOT,
        env=_subprocess_environment(),
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize(
    "fault_point",
    [
        shadow.FAULT_AFTER_STATE_COMMIT_BEFORE_MANIFEST,
        shadow.FAULT_AFTER_FEATURE_COMMIT_BEFORE_MANIFEST,
    ],
)
def test_real_cross_process_restart_recovers_from_db_truth(tmp_path, fault_point):
    source = _create_v1_source(tmp_path / "source.db", tmp_path)
    first_code = """
import json, sys
from pathlib import Path
from analysis.schedule_state_migration_trial import schedule_state_migration_trial as trial
from analysis.schedule_shadow_ingestion import schedule_shadow_ingestion as shadow
p = trial.prepare_trial_copy(Path(sys.argv[1]), temp_root=Path(sys.argv[2]))
s = shadow.open_shadow_session(p)
e = shadow.load_artifact_envelope(
    Path(sys.argv[3]), expected_sha256=sys.argv[4], provider="fotmob",
    source_operation="league_matches_saved_raw_fixture", competition_id=78,
    requested_season="2025", observed_at=sys.argv[5],
    artifact_schema_version="cwc_schedule_raw_projection_v1",
    completeness_status="COMPLETE",
    completeness_evidence=json.loads(sys.argv[6]),
)
try:
    shadow.run_shadow_ingestion(s, e, run_id="restart", fault_point=sys.argv[7])
except shadow.ShadowInjectedCrash:
    pass
print(json.dumps({"descriptor": str(s.descriptor_path), "nonce": s.capability}))
"""
    first = _run_process(
        first_code,
        str(source),
        str(tmp_path),
        str(RAW_FIXTURE),
        RAW_FIXTURE_SHA256,
        T0,
        json.dumps(_evidence()),
        fault_point,
    )
    assert first.returncode == 0, first.stderr
    capability = json.loads(first.stdout.strip())
    second_code = """
import json, sys
from pathlib import Path
from analysis.schedule_shadow_ingestion import schedule_shadow_ingestion as shadow
s = shadow.reopen_shadow_session(Path(sys.argv[1]), sys.argv[2])
e = shadow.load_artifact_envelope(
    Path(sys.argv[3]), expected_sha256=sys.argv[4], provider="fotmob",
    source_operation="league_matches_saved_raw_fixture", competition_id=78,
    requested_season="2025", observed_at=sys.argv[5],
    artifact_schema_version="cwc_schedule_raw_projection_v1",
    completeness_status="COMPLETE",
    completeness_evidence=json.loads(sys.argv[6]),
)
print(json.dumps(shadow.run_shadow_ingestion(s, e, run_id="restart"), sort_keys=True))
"""
    second = _run_process(
        second_code,
        capability["descriptor"],
        capability["nonce"],
        str(RAW_FIXTURE),
        RAW_FIXTURE_SHA256,
        T0,
        json.dumps(_evidence()),
    )
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["phase"] == "COMPLETED"


def test_durable_descriptor_rejects_corruption_wrong_nonce_and_dead_creator_pid(
    tmp_path,
):
    session = _session(tmp_path)
    descriptor = session.descriptor_path
    original = descriptor.read_bytes()
    with pytest.raises(shadow.ShadowSessionError):
        shadow.reopen_shadow_session(descriptor, "0" * 64)
    reopened = shadow.reopen_shadow_session(descriptor, session.capability)
    assert reopened.session_id == session.session_id
    changed = json.loads(original)
    changed["creator_pid"] = 999_999_999
    descriptor.write_text(
        json.dumps(changed, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    descriptor.chmod(0o600)
    with pytest.raises(shadow.ShadowSessionError):
        shadow.reopen_shadow_session(descriptor, session.capability)
    descriptor.write_bytes(original)
    descriptor.chmod(0o600)
    descriptor.write_bytes(original[:-1] + b"!")
    descriptor.chmod(0o600)
    with pytest.raises(shadow.ShadowSessionError):
        shadow.reopen_shadow_session(descriptor, session.capability)


def test_durable_descriptor_rejects_signed_database_redirection(tmp_path):
    session = _session(tmp_path)
    binding = shadow._session_binding(session)
    alternate = tmp_path / "alternate.db"
    shutil.copy2(binding.database_path, alternate)
    alternate.chmod(0o600)
    descriptor = json.loads(session.descriptor_path.read_bytes())
    descriptor["database"] = shadow._database_binding(alternate)
    descriptor["signature"] = shadow._sign_document(
        descriptor,
        session.capability,
    )
    session.descriptor_path.write_bytes(
        json.dumps(
            descriptor,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    session.descriptor_path.chmod(0o600)
    with pytest.raises(shadow.ShadowSessionError):
        shadow.reopen_shadow_session(
            session.descriptor_path,
            session.capability,
        )


def test_durable_descriptor_rejects_unknown_workspace_entry(tmp_path):
    session = _session(tmp_path)
    binding = shadow._session_binding(session)
    unknown = binding.workspace / "unexpected-entry"
    _write_private(unknown, b"UNKNOWN_WORKSPACE_MARKER_42")
    before = (
        unknown.stat().st_ino,
        unknown.stat().st_size,
        hashlib.sha256(unknown.read_bytes()).hexdigest(),
    )
    with pytest.raises(shadow.ShadowSessionError):
        shadow.reopen_shadow_session(
            session.descriptor_path,
            session.capability,
        )
    assert (
        unknown.stat().st_ino,
        unknown.stat().st_size,
        hashlib.sha256(unknown.read_bytes()).hexdigest(),
    ) == before


@pytest.mark.parametrize(
    ("name_suffix", "content"),
    [
        ("-wal", b"COMMITTED_WAL_MARKER_42"),
        ("-unknown", b"UNKNOWN_COMPANION_MARKER_42"),
    ],
)
def test_durable_descriptor_rejects_committed_wal_and_unknown_companion(
    tmp_path,
    name_suffix,
    content,
):
    session = _session(tmp_path)
    database_path = _db_path(session)
    companion = database_path.with_name(database_path.name + name_suffix)
    if companion.exists():
        companion.unlink()
    _write_private(companion, content)
    before = (
        companion.stat().st_ino,
        companion.stat().st_size,
        hashlib.sha256(companion.read_bytes()).hexdigest(),
    )
    with pytest.raises(shadow.ShadowSessionError):
        shadow.reopen_shadow_session(
            session.descriptor_path,
            session.capability,
        )
    assert (
        companion.stat().st_ino,
        companion.stat().st_size,
        hashlib.sha256(companion.read_bytes()).hexdigest(),
    ) == before


def test_cross_process_lock_rejects_second_runner_and_releases_cleanly(tmp_path):
    session = _session(tmp_path)
    holder_code = """
import sys
from pathlib import Path
from analysis.schedule_shadow_ingestion import schedule_shadow_ingestion as shadow
s = shadow.reopen_shadow_session(Path(sys.argv[1]), sys.argv[2])
with shadow._session_lock(s):
    print("LOCKED", flush=True)
    sys.stdin.readline()
"""
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            holder_code,
            str(session.descriptor_path),
            session.capability,
        ],
        cwd=REPO_ROOT,
        env=_subprocess_environment(),
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "LOCKED"
        runner_code = """
import json, sys
from pathlib import Path
from analysis.schedule_shadow_ingestion import schedule_shadow_ingestion as shadow
s = shadow.reopen_shadow_session(Path(sys.argv[1]), sys.argv[2])
e = shadow.load_artifact_envelope(
    Path(sys.argv[3]), expected_sha256=sys.argv[4], provider="fotmob",
    source_operation="league_matches_saved_raw_fixture", competition_id=78,
    requested_season="2025", observed_at=sys.argv[5],
    artifact_schema_version="cwc_schedule_raw_projection_v1",
    completeness_status="COMPLETE",
    completeness_evidence=json.loads(sys.argv[6]),
)
try:
    shadow.run_shadow_ingestion(s, e, run_id="locked")
except shadow.ShadowSessionError as exc:
    print(type(exc).__name__ + ":" + str(exc))
    raise SystemExit(23)
raise SystemExit(0)
"""
        blocked = _run_process(
            runner_code,
            str(session.descriptor_path),
            session.capability,
            str(RAW_FIXTURE),
            RAW_FIXTURE_SHA256,
            T0,
            json.dumps(_evidence()),
        )
        assert blocked.returncode == 23
        assert blocked.stdout.strip() == (
            "ShadowSessionError:shadow session is already active"
        )
        assert all(value == 0 for value in _business_counts(_db_path(session)).values())
    finally:
        if holder.stdin is not None:
            holder.stdin.write("\n")
            holder.stdin.flush()
        holder.wait(timeout=10)
        for stream in (holder.stdin, holder.stdout, holder.stderr):
            if stream is not None:
                stream.close()
    reopened = shadow.reopen_shadow_session(
        session.descriptor_path,
        session.capability,
    )
    assert shadow.run_shadow_ingestion(
        reopened,
        _load(),
        run_id="locked",
    )["phase"] == "COMPLETED"
