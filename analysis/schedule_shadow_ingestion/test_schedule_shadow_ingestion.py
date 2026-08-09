"""Permanent offline gates for OFFLINE_SCHEDULE_SHADOW_INGESTION_V1."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import socket
import sqlite3
import stat
import traceback
import urllib.request
from dataclasses import replace
from pathlib import Path

import pytest

from analysis.schedule_shadow_ingestion import schedule_shadow_ingestion as shadow
from analysis.schedule_state_migration_trial import (
    schedule_state_migration_trial as trial,
)
from backend.db import migrate
from backend.schedules import state as schedule


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "fotmob"
    / "cwc_2025_competition_schedule_raw.json"
)
FIXTURE_SHA256 = (
    "b2852c04cdcfd812a92164e482309cdca634c3a38dca4973adcf94a3ebbc67fc"
)
T0 = "2026-07-26T00:00:00Z"
T1 = "2026-07-26T00:05:00Z"
T2 = "2026-07-26T00:10:00Z"
CITY_ID = 8456
CITY_MATCH_IDS = (4685744, 4685746, 4685748, 4685772)
CANCELLED_IDS = {4685727, 4685729, 4685730}


def _complete_evidence(count: int = 66) -> dict[str, object]:
    return {
        "competition_identity_verified": True,
        "competition_name": "FIFA Club World Cup",
        "competition_class": "international_club",
        "competition_class_verified": True,
        "returned_season": "2025",
        "fixture_schema_valid": True,
        "fixture_count": count,
        "pagination_status": "NOT_DETECTED",
        "pagination_detected_evidence": [],
        "pagination_unresolved_evidence": [],
        "pagination_unknown_evidence": [],
        "observation_time_provenance": (
            "caller_supplied_synthetic_offline_event_time"
        ),
    }


def _load_envelope(
    *,
    path: Path = FIXTURE_PATH,
    artifact_sha256: str = FIXTURE_SHA256,
    provider: str = " FOTMOB ",
    observed_at: str = T0,
    source_operation: str = "league_matches_saved_fixture",
    competition_id: int = 78,
    requested_season: str = "2025",
    completeness_status: str = "COMPLETE",
    evidence: dict[str, object] | None = None,
) -> shadow.ArtifactEnvelope:
    return shadow.load_artifact_envelope(
        path,
        expected_sha256=artifact_sha256,
        provider=provider,
        source_operation=source_operation,
        competition_id=competition_id,
        requested_season=requested_season,
        observed_at=observed_at,
        artifact_schema_version="cwc_schedule_raw_projection_v1",
        completeness_status=completeness_status,
        completeness_evidence=(
            _complete_evidence() if evidence is None else evidence
        ),
    )


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


def _database_path(session: shadow.PreparedShadowSession) -> Path:
    return shadow._session_binding(session).database_path


def _connect(session: shadow.PreparedShadowSession) -> sqlite3.Connection:
    database_path = _database_path(session)
    conn = sqlite3.connect(
        f"{database_path.as_uri()}?mode=ro&immutable=1",
        uri=True,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA recursive_triggers = ON")
    return conn


def _counts(session: shadow.PreparedShadowSession) -> dict[str, int]:
    conn = _connect(session)
    try:
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
            name: int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
            for name in names
        }
    finally:
        conn.close()


def _write_derived_artifact(
    tmp_path: Path,
    document: dict,
    *,
    name: str,
) -> tuple[Path, str]:
    raw = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path = tmp_path / name
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, raw)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    return path, hashlib.sha256(raw).hexdigest()


def _derived_envelope(
    tmp_path: Path,
    document: dict,
    *,
    name: str,
    observed_at: str,
    source_operation: str,
    competition_id: int = 78,
    requested_season: str = "2025",
    evidence: dict[str, object] | None = None,
) -> shadow.ArtifactEnvelope:
    path, digest = _write_derived_artifact(tmp_path, document, name=name)
    fixture_count = (
        len(document["fixtures"]["allMatches"])
        if (
            isinstance(document.get("fixtures"), dict)
            and isinstance(document["fixtures"].get("allMatches"), list)
        )
        else 66
    )
    return _load_envelope(
        path=path,
        artifact_sha256=digest,
        observed_at=observed_at,
        source_operation=source_operation,
        competition_id=competition_id,
        requested_season=requested_season,
        evidence=(
            _complete_evidence(fixture_count)
            if evidence is None
            else evidence
        ),
    )


def _safe_exception_surfaces(exc: BaseException) -> tuple[str, ...]:
    return (
        str(exc),
        repr(exc),
        repr(exc.args),
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        repr(exc.__cause__),
        repr(exc.__context__),
    )


def _block_network(monkeypatch) -> None:
    original_socket = socket.socket

    def guarded_socket(
        family=socket.AF_INET,
        type=socket.SOCK_STREAM,
        proto=0,
        fileno=None,
    ):
        if family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("network socket is forbidden")
        return original_socket(family, type, proto, fileno)

    monkeypatch.setattr(socket, "socket", guarded_socket)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: pytest.fail("DNS is forbidden"),
    )
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *args, **kwargs: pytest.fail("urllib is forbidden"),
    )
    try:
        import requests
    except ImportError:
        requests = None
    if requests is not None:
        monkeypatch.setattr(
            requests.sessions.Session,
            "request",
            lambda *args, **kwargs: pytest.fail("requests is forbidden"),
        )
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        cffi_requests = None
    if cffi_requests is not None:
        monkeypatch.setattr(
            cffi_requests,
            "get",
            lambda *args, **kwargs: pytest.fail("curl_cffi is forbidden"),
        )
    from backend import fotmob_client

    monkeypatch.setattr(
        fotmob_client.FotMobClient,
        "__init__",
        lambda *args, **kwargs: pytest.fail("FotMobClient is forbidden"),
    )


def test_artifact_sha_is_checked_before_json_parse(monkeypatch):
    parse_calls = 0

    def parse_probe(*args, **kwargs):
        nonlocal parse_calls
        parse_calls += 1
        raise AssertionError("JSON parsing must not run after a hash mismatch")

    monkeypatch.setattr(shadow.json, "loads", parse_probe)
    with pytest.raises(
        shadow.ShadowArtifactError,
        match="artifact validation failed",
    ) as captured:
        _load_envelope(artifact_sha256="0" * 64)
    assert parse_calls == 0
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_envelope_normalizes_provider_operation_and_canonical_utc():
    envelope = shadow.load_artifact_envelope(
        FIXTURE_PATH,
        expected_sha256=FIXTURE_SHA256,
        provider=" FOTMOB ",
        source_operation=" LEAGUE_MATCHES_SAVED_FIXTURE ",
        competition_id=78,
        requested_season="2025",
        observed_at="2026-07-26T08:00:00+00:00",
        artifact_schema_version="cwc_schedule_raw_projection_v1",
        completeness_status="COMPLETE",
        completeness_evidence=_complete_evidence(),
    )
    assert envelope.provider == "fotmob"
    assert envelope.source_operation == "league_matches_saved_fixture"
    assert envelope.observed_at == "2026-07-26T08:00:00.000000Z"
    assert set(envelope.payload) == {
        "artifactProvenance",
        "details",
        "fixtures",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "competition-id-mismatch",
        "season-mismatch",
        "empty-fixtures",
        "missing-fixtures",
        "pagination-detected",
        "pagination-unresolved",
        "pagination-unknown",
        "fixture-count",
        "schema-unverified",
        "duplicate-match-conflicting-content",
        "missing-team-id",
        "invalid-status-type",
        "invalid-status-combination",
        "invalid-kickoff",
    ],
)
def test_envelope_and_completeness_fail_closed_before_business_writes(
    tmp_path,
    mutation,
):
    session = _session(tmp_path)
    document = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    evidence = _complete_evidence()
    competition_id = 78
    requested_season = "2025"
    if mutation == "competition-id-mismatch":
        competition_id = 79
    elif mutation == "season-mismatch":
        requested_season = "2024"
    elif mutation == "empty-fixtures":
        document["fixtures"]["allMatches"] = []
        evidence = _complete_evidence(0)
    elif mutation == "missing-fixtures":
        document.pop("fixtures")
    elif mutation == "pagination-detected":
        evidence["pagination_status"] = "DETECTED"
        evidence["pagination_detected_evidence"] = ["fixtures.next"]
    elif mutation == "pagination-unresolved":
        evidence["pagination_status"] = "UNRESOLVED"
        evidence["pagination_unresolved_evidence"] = ["fixtures.page"]
    elif mutation == "pagination-unknown":
        evidence["pagination_unknown_evidence"] = ["fixtures.marker"]
    elif mutation == "fixture-count":
        evidence["fixture_count"] = 65
    elif mutation == "schema-unverified":
        evidence["fixture_schema_valid"] = False
    elif mutation == "duplicate-match-conflicting-content":
        duplicate = copy.deepcopy(document["fixtures"]["allMatches"][0])
        duplicate["home"]["name"] = "Conflicting Team Name"
        document["fixtures"]["allMatches"].append(duplicate)
        evidence = _complete_evidence(67)
    elif mutation == "missing-team-id":
        document["fixtures"]["allMatches"][0]["home"].pop("id")
    elif mutation == "invalid-status-type":
        document["fixtures"]["allMatches"][0]["status"]["finished"] = "yes"
    elif mutation == "invalid-status-combination":
        document["fixtures"]["allMatches"][0]["status"].update(
            {
                "started": True,
                "finished": True,
                "cancelled": True,
            }
        )
    else:
        document["fixtures"]["allMatches"][0]["status"][
            "utcTime"
        ] = "not-a-timestamp"

    replacement = _derived_envelope(
        tmp_path,
        document,
        name=f"invalid-{mutation}.json",
        observed_at=T0,
        source_operation=f"synthetic_invalid_{mutation}",
        competition_id=competition_id,
        requested_season=requested_season,
        evidence=evidence,
    )

    with pytest.raises(shadow.ShadowIngestionError) as captured:
        shadow.run_shadow_ingestion(
            session,
            replacement,
            run_id=f"invalid-{mutation}",
        )
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert _counts(session) == {
        "schedule_match_identity": 0,
        "schedule_match_state_snapshot": 0,
        "schedule_observation_event": 0,
        "schedule_match_observation": 0,
        "schedule_rest_lineage_set": 0,
        "schedule_rest_lineage_input": 0,
        "schedule_rest_feature": 0,
    }


def test_artifact_sha_mismatch_fails_before_parse_and_business_writes(
    tmp_path,
    monkeypatch,
):
    session = _session(tmp_path)
    parse_calls = 0

    def parse_probe(*args, **kwargs):
        nonlocal parse_calls
        parse_calls += 1
        raise AssertionError("JSON parse must not run")

    monkeypatch.setattr(shadow.json, "loads", parse_probe)
    with pytest.raises(
        shadow.ShadowArtifactError,
        match="artifact validation failed",
    ) as captured:
        _load_envelope(artifact_sha256="0" * 64)
    assert parse_calls == 0
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    monkeypatch.undo()
    assert all(count == 0 for count in _counts(session).values())


def test_same_content_duplicate_match_is_rejected_independent_of_input_order(
    tmp_path,
):
    session = _session(tmp_path)
    canonical = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    duplicate = copy.deepcopy(canonical["fixtures"]["allMatches"][0])
    variants = []
    for index, fixtures in enumerate(
        (
            [
                *canonical["fixtures"]["allMatches"],
                copy.deepcopy(duplicate),
            ],
            [
                copy.deepcopy(duplicate),
                *canonical["fixtures"]["allMatches"],
            ],
        )
    ):
        document = copy.deepcopy(canonical)
        document["fixtures"]["allMatches"] = fixtures
        variants.append(
            _derived_envelope(
                tmp_path,
                document,
                name=f"same-content-duplicate-{index}.json",
                observed_at=T0,
                source_operation=f"synthetic_duplicate_order_{index}",
                evidence=_complete_evidence(67),
            )
        )

    for index, envelope in enumerate(variants):
        with pytest.raises(
            shadow.ShadowIngestionError,
            match="offline shadow ingestion failed",
        ):
            shadow.run_shadow_ingestion(
                session,
                envelope,
                run_id=f"same-content-duplicate-{index}",
            )
        assert all(count == 0 for count in _counts(session).values())


def test_loaded_envelope_rejects_non_plain_payload_tamper_before_writes(
    tmp_path,
):
    session = _session(tmp_path)
    envelope = _load_envelope()
    tampered = replace(envelope, payload={"fixtures": (1, 2, 3)})
    with pytest.raises(shadow.ShadowIngestionError):
        shadow.run_shadow_ingestion(
            session,
            tampered,
            run_id="non-plain-payload-tamper",
        )
    assert all(count == 0 for count in _counts(session).values())


def test_artifact_failure_message_never_echoes_payload_path_url_or_marker(
    tmp_path,
):
    marker = "SYNTHETIC_SHADOW_MARKER_42"
    document = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    document["fixtures"]["allMatches"][0]["status"]["utcTime"] = (
        f"http://offline-user:{marker}@proxy.invalid/private.json"
    )
    path, digest = _write_derived_artifact(
        tmp_path,
        document,
        name=f"{marker}.json",
    )
    envelope = _load_envelope(
        path=path,
        artifact_sha256=digest,
        source_operation="synthetic_bad_timestamp",
    )
    session = _session(tmp_path)
    with pytest.raises(shadow.ShadowIngestionError) as captured:
        shadow.run_shadow_ingestion(
            session,
            envelope,
            run_id="safe-error",
        )
    assert all(marker not in surface for surface in _safe_exception_surfaces(captured.value))
    assert all(str(tmp_path) not in surface for surface in _safe_exception_surfaces(captured.value))
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_session_factory_accepts_only_process_bound_prepared_trial_copy(
    tmp_path,
):
    source = _create_v1_source(tmp_path / "source-v1.db", tmp_path)
    with pytest.raises(shadow.ShadowSessionError):
        shadow.open_shadow_session(source)
    prepared = trial.prepare_trial_copy(source, temp_root=tmp_path)
    session = shadow.open_shadow_session(prepared)
    with pytest.raises(shadow.ShadowSessionError):
        shadow.open_shadow_session(prepared)
    assert isinstance(session, shadow.PreparedShadowSession)


def test_run1_canonical_full_shadow_chain_and_private_manifest(tmp_path):
    session = _session(tmp_path)
    result = shadow.run_shadow_ingestion(
        session,
        _load_envelope(),
        run_id="cwc-t0",
    )
    assert result["status"] == "COMPLETED"
    assert result["phase"] == "COMPLETED"
    assert result["state_summary"] == {
        "identity_verified": 66,
        "snapshot_verified": 66,
        "event_verified": 1,
        "association_verified": 66,
    }
    assert result["feature_summary"] == {
        "feature_verified": 126,
        "lineage_input_verified": 334,
    }
    assert _counts(session) == {
        "schedule_match_identity": 66,
        "schedule_match_state_snapshot": 66,
        "schedule_observation_event": 1,
        "schedule_match_observation": 66,
        "schedule_rest_lineage_set": 126,
        "schedule_rest_lineage_input": 334,
        "schedule_rest_feature": 126,
    }
    manifest_path = shadow._manifest_path(session, "cwc-t0")
    workspace = manifest_path.parent.parent
    assert stat.S_IMODE(workspace.stat().st_mode) == 0o700
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
    manifest_text = manifest_path.read_text(encoding="utf-8")
    assert "fixtures" not in manifest_text
    assert "authorization" not in manifest_text.casefold()
    assert "http://" not in manifest_text.casefold()
    assert "https://" not in manifest_text.casefold()
    assert not list(manifest_path.parent.glob("*.tmp-*"))
    manifest = shadow.read_shadow_run_manifest(session, "cwc-t0")
    assert manifest["state_apply_summary"] == {
        "identity_inserted": 66,
        "identity_skipped": 0,
        "snapshot_inserted": 66,
        "snapshot_skipped": 0,
        "event_inserted": 1,
        "event_skipped": 0,
        "association_inserted": 66,
        "association_skipped": 0,
    }
    assert manifest["feature_apply_summary"] == {
        "inserted": 126,
        "skipped": 0,
    }
    assert manifest["started"] is True
    assert manifest["completed"] is True
    assert manifest["failed"] is False
    assert manifest["resume_count"] == 0
    assert manifest["history"] == [
        "ARTIFACT_VALIDATED",
        "STATE_APPLIED",
        "FEATURES_APPLIED",
        "COMPLETED",
    ]

    conn = _connect(session)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM current_schedule_match_state"
        ).fetchone()[0] == 66
        assert conn.execute(
            "SELECT COUNT(*) FROM current_schedule_match_state "
            "WHERE cancelled=1"
        ).fetchone()[0] == 3
        assert {
            row[0]
            for row in conn.execute(
                "SELECT canonical_match_id FROM current_schedule_match_state "
                "WHERE cancelled=1"
            )
        } == {None}
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_match_state_snapshot "
            "WHERE source_updated_at IS NOT NULL"
        ).fetchone()[0] == 0
        city = [
            json.loads(row[0])["kickoff_gap_hours"]
            for row in conn.execute(
                "SELECT feature.feature_value_json "
                "FROM schedule_rest_feature AS feature "
                "JOIN schedule_match_state_snapshot AS snapshot "
                "ON snapshot.id=feature.target_snapshot_id "
                "WHERE feature.team_id=? "
                "ORDER BY snapshot.kickoff_at_utc",
                (CITY_ID,),
            )
        ]
        assert city == [None, 105.0, 90.0, 102.0]
        assert conn.execute(
            "SELECT status FROM current_schedule_match_state "
            "WHERE provider_match_id='4685772'"
        ).fetchone()[0] == "AET"
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_rest_feature_input AS input "
            "JOIN schedule_match_state_snapshot AS snapshot "
            "ON snapshot.id=input.input_snapshot_id "
            "WHERE snapshot.cancelled=1"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_run2_same_content_later_observation_adds_only_event_and_associations(
    tmp_path,
):
    session = _session(tmp_path)
    first = shadow.run_shadow_ingestion(
        session,
        _load_envelope(observed_at=T0),
        run_id="cwc-t0",
    )
    first_manifest = shadow._manifest_path(session, "cwc-t0").read_bytes()
    second = shadow.run_shadow_ingestion(
        session,
        _load_envelope(observed_at=T1),
        run_id="cwc-t1",
    )
    assert second["state_summary"] == {
        "identity_verified": 66,
        "snapshot_verified": 66,
        "event_verified": 1,
        "association_verified": 66,
    }
    assert second["feature_summary"] == {
        "feature_verified": 126,
        "lineage_input_verified": 334,
    }
    second_manifest = shadow.read_shadow_run_manifest(session, "cwc-t1")
    assert second_manifest["state_apply_summary"] == {
        "identity_inserted": 0,
        "identity_skipped": 66,
        "snapshot_inserted": 0,
        "snapshot_skipped": 66,
        "event_inserted": 1,
        "event_skipped": 0,
        "association_inserted": 66,
        "association_skipped": 0,
    }
    assert second_manifest["feature_apply_summary"] == {
        "inserted": 0,
        "skipped": 126,
    }
    assert _counts(session) == {
        "schedule_match_identity": 66,
        "schedule_match_state_snapshot": 66,
        "schedule_observation_event": 2,
        "schedule_match_observation": 132,
        "schedule_rest_lineage_set": 126,
        "schedule_rest_lineage_input": 334,
        "schedule_rest_feature": 126,
    }
    assert shadow._manifest_path(session, "cwc-t0").read_bytes() == first_manifest
    replay = shadow.run_shadow_ingestion(
        session,
        _load_envelope(observed_at=T1),
        run_id="cwc-t1",
    )
    assert replay == second
    assert first["artifact_sha256"] == second["artifact_sha256"]


def test_run3_one_mutable_state_adds_one_snapshot_and_related_lineage_only(
    tmp_path,
):
    session = _session(tmp_path)
    shadow.run_shadow_ingestion(
        session,
        _load_envelope(observed_at=T0),
        run_id="cwc-t0",
    )
    conn = _connect(session)
    try:
        before_features = {
            (
                row["team_id"],
                row["provider_match_id"],
                row["input_set_hash"],
            )
            for row in conn.execute(
                "SELECT feature.team_id, identity.provider_match_id, "
                "feature.input_set_hash "
                "FROM schedule_rest_feature AS feature "
                "JOIN schedule_match_identity AS identity "
                "ON identity.id=feature.target_match_identity_id"
            )
        }
        changed_match = conn.execute(
            "SELECT home_team_id, away_team_id FROM current_schedule_match_state "
            "WHERE provider_match_id='4685746'"
        ).fetchone()
        related_teams = {changed_match[0], changed_match[1]}
    finally:
        conn.close()

    document = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    target = next(
        row
        for row in document["fixtures"]["allMatches"]
        if int(row["id"]) == 4685746
    )
    target["status"]["utcTime"] = "2025-06-23T20:00:00Z"
    changed = _derived_envelope(
        tmp_path,
        document,
        name="cwc-synthetic-state-change.json",
        observed_at=T2,
        source_operation="synthetic_shadow_state_change",
    )
    result = shadow.run_shadow_ingestion(
        session,
        changed,
        run_id="cwc-state-change",
    )
    assert result["state_summary"]["snapshot_verified"] == 66
    changed_manifest = shadow.read_shadow_run_manifest(
        session,
        "cwc-state-change",
    )
    assert changed_manifest["state_apply_summary"]["snapshot_inserted"] == 1
    assert changed_manifest["state_apply_summary"]["association_inserted"] == 66
    assert changed_manifest["feature_apply_summary"]["inserted"] > 0

    conn = _connect(session)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_match_state_snapshot"
        ).fetchone()[0] == 67
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_observation_event"
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_match_observation"
        ).fetchone()[0] == 132
        identity_id = conn.execute(
            "SELECT id FROM schedule_match_identity "
            "WHERE provider='fotmob' AND provider_match_id='4685746'"
        ).fetchone()[0]
        assert schedule.get_match_state_as_of(
            conn,
            identity_id,
            T0,
        )["kickoff_at_utc"] == "2025-06-23T01:00:00.000000Z"
        assert schedule.get_current_match_state(
            conn,
            identity_id,
        )["kickoff_at_utc"] == "2025-06-23T20:00:00.000000Z"
        after_features = {
            (
                row["team_id"],
                row["provider_match_id"],
                row["input_set_hash"],
            )
            for row in conn.execute(
                "SELECT feature.team_id, identity.provider_match_id, "
                "feature.input_set_hash "
                "FROM schedule_rest_feature AS feature "
                "JOIN schedule_match_identity AS identity "
                "ON identity.id=feature.target_match_identity_id"
            )
        }
        added = after_features - before_features
        assert added
        assert {team_id for team_id, _, _ in added} <= related_teams
        assert before_features <= after_features
        assert sum(
            1
            for team_id, match_id, _ in added
            if team_id == CITY_ID and int(match_id) in CITY_MATCH_IDS[1:]
        ) == 3
        assert not any(
            team_id == CITY_ID and int(match_id) == CITY_MATCH_IDS[0]
            for team_id, match_id, _ in added
        )
    finally:
        conn.close()


def test_input_order_does_not_change_normalized_rows_or_state_hashes(tmp_path):
    original = _load_envelope(observed_at=T0)
    reversed_document = copy.deepcopy(original.payload)
    reversed_document["fixtures"]["allMatches"].reverse()
    reversed_envelope = _derived_envelope(
        tmp_path,
        reversed_document,
        name="cwc-reversed.json",
        observed_at=T1,
        source_operation="synthetic_shadow_reordered_input",
    )
    first_batch = shadow.validate_artifact_envelope(original)
    second_batch = shadow.validate_artifact_envelope(reversed_envelope)
    assert first_batch.rows == second_batch.rows
    assert all(
        row.state_content_hash == schedule.build_state_content_hash(row.state)
        for row in first_batch.rows
    )

    session = _session(tmp_path)
    shadow.run_shadow_ingestion(session, original, run_id="ordered")
    conn = _connect(session)
    try:
        before_hashes = conn.execute(
            "SELECT provider_match_id, state_content_hash "
            "FROM schedule_match_identity AS identity "
            "JOIN schedule_match_state_snapshot AS snapshot "
            "ON snapshot.match_identity_id=identity.id "
            "ORDER BY provider_match_id"
        ).fetchall()
    finally:
        conn.close()
    second = shadow.run_shadow_ingestion(
        session,
        reversed_envelope,
        run_id="reordered",
    )
    assert second["state_summary"]["snapshot_verified"] == 66
    assert shadow.read_shadow_run_manifest(
        session,
        "reordered",
    )["state_apply_summary"]["snapshot_inserted"] == 0
    conn = _connect(session)
    try:
        assert conn.execute(
            "SELECT provider_match_id, state_content_hash "
            "FROM schedule_match_identity AS identity "
            "JOIN schedule_match_state_snapshot AS snapshot "
            "ON snapshot.match_identity_id=identity.id "
            "ORDER BY provider_match_id"
        ).fetchall() == before_hashes
    finally:
        conn.close()


def test_out_of_order_observation_appends_history_without_regressing_current(
    tmp_path,
):
    session = _session(tmp_path)
    document = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    target = next(
        row
        for row in document["fixtures"]["allMatches"]
        if int(row["id"]) == 4685746
    )
    target["status"]["utcTime"] = "2025-06-23T20:00:00Z"
    later_changed = _derived_envelope(
        tmp_path,
        document,
        name="cwc-later-state-first.json",
        observed_at=T2,
        source_operation="synthetic_shadow_later_state_first",
    )
    shadow.run_shadow_ingestion(
        session,
        later_changed,
        run_id="later-state-first",
    )
    earlier = shadow.run_shadow_ingestion(
        session,
        _load_envelope(observed_at=T1),
        run_id="earlier-state-late-ingestion",
    )
    assert earlier["state_summary"]["snapshot_verified"] == 66
    earlier_manifest = shadow.read_shadow_run_manifest(
        session,
        "earlier-state-late-ingestion",
    )
    assert earlier_manifest["state_apply_summary"]["snapshot_inserted"] == 1
    assert earlier_manifest["state_apply_summary"]["association_inserted"] == 66

    conn = _connect(session)
    try:
        identity_id = conn.execute(
            "SELECT id FROM schedule_match_identity "
            "WHERE provider='fotmob' AND provider_match_id='4685746'"
        ).fetchone()[0]
        assert schedule.get_match_state_as_of(
            conn,
            identity_id,
            T1,
        )["kickoff_at_utc"] == "2025-06-23T01:00:00.000000Z"
        current = schedule.get_current_match_state(conn, identity_id)
        assert current["observed_at"] == schedule._utc(T2)
        assert current["kickoff_at_utc"] == "2025-06-23T20:00:00.000000Z"
    finally:
        conn.close()


def test_absent_match_is_not_inferred_cancelled_or_deleted(tmp_path):
    session = _session(tmp_path)
    shadow.run_shadow_ingestion(
        session,
        _load_envelope(observed_at=T0),
        run_id="full",
    )
    document = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    missing_id = 4685744
    document["fixtures"]["allMatches"] = [
        row
        for row in document["fixtures"]["allMatches"]
        if int(row["id"]) != missing_id
    ]
    envelope = _derived_envelope(
        tmp_path,
        document,
        name="cwc-synthetic-absence.json",
        observed_at=T1,
        source_operation="synthetic_shadow_absence",
    )
    result = shadow.run_shadow_ingestion(session, envelope, run_id="absence")
    assert result["state_summary"]["association_verified"] == 65
    absence_manifest = shadow.read_shadow_run_manifest(session, "absence")
    assert absence_manifest["state_apply_summary"]["association_inserted"] == 65
    assert absence_manifest["state_apply_summary"]["snapshot_inserted"] == 0
    conn = _connect(session)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_match_identity"
        ).fetchone()[0] == 66
        row = conn.execute(
            "SELECT cancelled, observed_at FROM current_schedule_match_state "
            "WHERE provider_match_id=?",
            (str(missing_id),),
        ).fetchone()
        assert tuple(row) == (0, schedule._utc(T0))
    finally:
        conn.close()


def test_same_observation_business_conflict_rolls_back_complete_batch(tmp_path):
    session = _session(tmp_path)
    shadow.run_shadow_ingestion(
        session,
        _load_envelope(observed_at=T0),
        run_id="initial",
    )
    before = _counts(session)
    document = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    target = next(
        row
        for row in document["fixtures"]["allMatches"]
        if int(row["id"]) == 4685746
    )
    target["status"]["utcTime"] = "2025-06-23T20:00:00Z"
    conflicting = _derived_envelope(
        tmp_path,
        document,
        name="cwc-same-time-conflict.json",
        observed_at=T0,
        source_operation="synthetic_shadow_same_time_conflict",
    )
    with pytest.raises(shadow.ShadowIngestionError):
        shadow.run_shadow_ingestion(
            session,
            conflicting,
            run_id="conflict",
        )
    assert _counts(session) == before
    manifest = shadow.read_shadow_run_manifest(session, "conflict")
    assert manifest["phase"] == "FAILED"
    assert manifest["last_successful_phase"] == "ARTIFACT_VALIDATED"
    assert manifest["failed"] is True
    assert manifest["safe_error_code"] == "STATE_APPLY_FAILED"


@pytest.mark.parametrize(
    (
        "fault_point",
        "expected_phase",
        "expected_state_rows",
        "expected_feature_rows",
        "raises_injected_crash",
    ),
    [
        (
            shadow.FAULT_AFTER_ARTIFACT_VALIDATED,
            "ARTIFACT_VALIDATED",
            0,
            0,
            True,
        ),
        (
            shadow.FAULT_STATE_TRANSACTION_MID,
            "FAILED",
            0,
            0,
            False,
        ),
        (
            shadow.FAULT_AFTER_STATE_COMMIT_BEFORE_MANIFEST,
            "ARTIFACT_VALIDATED",
            66,
            0,
            True,
        ),
        (
            shadow.FAULT_AFTER_STATE_APPLIED,
            "STATE_APPLIED",
            66,
            0,
            True,
        ),
        (
            shadow.FAULT_FEATURE_TRANSACTION_MID,
            "FAILED",
            66,
            0,
            False,
        ),
        (
            shadow.FAULT_AFTER_FEATURE_COMMIT_BEFORE_MANIFEST,
            "STATE_APPLIED",
            66,
            126,
            True,
        ),
    ],
)
def test_crash_safe_resume_at_all_six_boundaries(
    tmp_path,
    fault_point,
    expected_phase,
    expected_state_rows,
    expected_feature_rows,
    raises_injected_crash,
):
    session = _session(tmp_path)
    expected_error = (
        shadow.ShadowInjectedCrash
        if raises_injected_crash
        else shadow.ShadowIngestionError
    )
    with pytest.raises(expected_error):
        shadow.run_shadow_ingestion(
            session,
            _load_envelope(),
            run_id=f"fault-{fault_point.casefold()}",
            fault_point=fault_point,
        )
    manifest = shadow.read_shadow_run_manifest(
        session,
        f"fault-{fault_point.casefold()}",
    )
    assert manifest["phase"] == expected_phase
    counts = _counts(session)
    assert counts["schedule_match_state_snapshot"] == expected_state_rows
    assert counts["schedule_rest_feature"] == expected_feature_rows
    if fault_point == shadow.FAULT_FEATURE_TRANSACTION_MID:
        assert counts["schedule_rest_lineage_set"] == 0
        assert counts["schedule_rest_lineage_input"] == 0

    completed = shadow.run_shadow_ingestion(
        session,
        _load_envelope(),
        run_id=f"fault-{fault_point.casefold()}",
    )
    assert completed["phase"] == "COMPLETED"
    completed_manifest = shadow.read_shadow_run_manifest(
        session,
        f"fault-{fault_point.casefold()}",
    )
    assert completed_manifest["resume_count"] == 1
    assert _counts(session) == {
        "schedule_match_identity": 66,
        "schedule_match_state_snapshot": 66,
        "schedule_observation_event": 1,
        "schedule_match_observation": 66,
        "schedule_rest_lineage_set": 126,
        "schedule_rest_lineage_input": 334,
        "schedule_rest_feature": 126,
    }


def test_feature_retry_preserves_committed_state_and_completes_lineage(
    tmp_path,
):
    session = _session(tmp_path)
    envelope = _load_envelope()
    with pytest.raises(shadow.ShadowIngestionError):
        shadow.run_shadow_ingestion(
            session,
            envelope,
            run_id="feature-retry",
            fault_point=shadow.FAULT_FEATURE_TRANSACTION_MID,
        )
    failed_counts = _counts(session)
    assert failed_counts["schedule_match_state_snapshot"] == 66
    assert failed_counts["schedule_match_observation"] == 66
    assert failed_counts["schedule_rest_lineage_set"] == 0
    assert failed_counts["schedule_rest_lineage_input"] == 0
    assert failed_counts["schedule_rest_feature"] == 0
    failed_manifest = shadow.read_shadow_run_manifest(
        session,
        "feature-retry",
    )
    assert failed_manifest["last_successful_phase"] == "STATE_APPLIED"
    assert failed_manifest["safe_error_code"] == "FEATURE_APPLY_FAILED"

    completed = shadow.run_shadow_ingestion(
        session,
        envelope,
        run_id="feature-retry",
    )
    assert completed["phase"] == "COMPLETED"
    assert completed["feature_summary"] == {
        "feature_verified": 126,
        "lineage_input_verified": 334,
    }
    assert shadow.read_shadow_run_manifest(
        session,
        "feature-retry",
    )["feature_apply_summary"] == {"inserted": 126, "skipped": 0}
    assert _counts(session)["schedule_rest_feature"] == 126
    assert shadow.read_shadow_run_manifest(
        session,
        "feature-retry",
    )["resume_count"] == 1


def test_point_in_time_feature_lineage_has_no_future_or_ineligible_inputs(
    tmp_path,
):
    session = _session(tmp_path)
    shadow.run_shadow_ingestion(
        session,
        _load_envelope(),
        run_id="point-in-time-lineage",
    )
    conn = _connect(session)
    try:
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM schedule_rest_lineage_input AS input
            JOIN schedule_match_state_snapshot AS input_snapshot
              ON input_snapshot.id=input.input_snapshot_id
            JOIN schedule_rest_lineage_set AS lineage
              ON lineage.id=input.lineage_set_id
            JOIN schedule_match_state_snapshot AS target_snapshot
              ON target_snapshot.id=lineage.target_snapshot_id
            WHERE input_snapshot.kickoff_at_utc
                    > target_snapshot.kickoff_at_utc
               OR input_snapshot.finished<>1
               OR input_snapshot.cancelled<>0
               OR NOT EXISTS (
                    SELECT 1
                    FROM schedule_match_observation AS observation
                    WHERE observation.snapshot_id=input.input_snapshot_id
                      AND observation.observed_at
                            <= lineage.as_of_observed_at
               )
            """
        ).fetchone()[0] == 0
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM (
              SELECT lineage_set_id, input_match_identity_id
              FROM schedule_rest_lineage_input
              GROUP BY lineage_set_id, input_match_identity_id
              HAVING COUNT(*)<>1
            )
            """
        ).fetchone()[0] == 0
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM schedule_rest_lineage_set AS lineage
            WHERE (
              SELECT COUNT(*)
              FROM schedule_rest_lineage_input AS input
              WHERE input.lineage_set_id=lineage.id
            ) <> lineage.expected_input_count
            """
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_completed_run_history_is_never_overwritten(tmp_path):
    session = _session(tmp_path)
    envelope = _load_envelope()
    first = shadow.run_shadow_ingestion(session, envelope, run_id="completed")
    manifest_path = shadow._manifest_path(session, "completed")
    before = manifest_path.read_bytes()
    second = shadow.run_shadow_ingestion(session, envelope, run_id="completed")
    assert second == first
    assert manifest_path.read_bytes() == before
    with pytest.raises(shadow.ShadowRunConflictError):
        shadow.run_shadow_ingestion(
            session,
            replace(envelope, observed_at=schedule._utc(T1)),
            run_id="completed",
        )
    assert manifest_path.read_bytes() == before


def test_runtime_network_hard_block_and_static_transport_absence(
    tmp_path,
    monkeypatch,
):
    _block_network(monkeypatch)
    session = _session(tmp_path)
    result = shadow.run_shadow_ingestion(
        session,
        _load_envelope(),
        run_id="network-blocked",
    )
    assert result["phase"] == "COMPLETED"
    source = (
        REPO_ROOT
        / "analysis"
        / "schedule_shadow_ingestion"
        / "schedule_shadow_ingestion.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "fotmob_client",
        "FotMobClient",
        "curl_cffi",
        "requests.",
        "urllib",
        "socket.",
        "run_live",
        "resume_live",
    ):
        assert forbidden not in source


def test_no_worker_systemd_api_frontend_or_new_migration_registration():
    from backend.worker import runner

    assert not any("shadow" in name.casefold() for name in runner.REGISTRY)
    integration_roots = (
        REPO_ROOT / "backend" / "api",
        REPO_ROOT / "deploy" / "systemd",
        REPO_ROOT / "frontend",
    )
    for root in integration_roots:
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in {".py", ".ts", ".tsx", ".service"}:
                text = path.read_text(encoding="utf-8", errors="ignore")
                assert "schedule_shadow_ingestion" not in text
    migration_names = {
        path.name
        for root in (
            REPO_ROOT / "backend" / "migrations" / "core",
            REPO_ROOT / "backend" / "migrations" / "platform",
            REPO_ROOT / "backend" / "migrations" / "odds",
        )
        for path in root.glob("*.sql")
    }
    assert not any("shadow" in name.casefold() for name in migration_names)
