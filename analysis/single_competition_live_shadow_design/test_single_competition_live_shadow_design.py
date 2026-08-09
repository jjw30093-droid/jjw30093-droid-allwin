"""Offline permanent gates for a future single-competition acquisition job."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import stat
import subprocess
import sys
import traceback
import urllib.request
from pathlib import Path

import pytest

from analysis.schedule_state_migration_trial import (
    schedule_state_migration_trial as trial,
)
from analysis.schedule_shadow_ingestion import (
    schedule_shadow_ingestion as shadow,
)
from analysis.single_competition_live_shadow_design import (
    single_competition_live_shadow_design as design,
)
from backend.db import migrate


REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_FIXTURE = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "fotmob"
    / "cwc_2025_competition_schedule_raw.json"
)
RAW_SHA256 = (
    "b2852c04cdcfd812a92164e482309cdca634c3a38dca4973adcf94a3ebbc67fc"
)
T0 = "2026-07-28T00:00:00.000000Z"
T1 = "2026-07-28T00:05:00.000000Z"
T_EARLY = "2026-07-27T23:55:00.000000Z"


def _config(*, budget_max: int = 1) -> design.AcquisitionConfig:
    return design.AcquisitionConfig(
        provider="fotmob",
        competition_id=78,
        competition_name="FIFA Club World Cup",
        requested_season="2025",
        allowed_operations=("league_matches",),
        budget_max=budget_max,
        expected_fixture_count=66,
        competition_class="international_club",
        artifact_schema_version="cwc_schedule_raw_projection_v1",
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


def _session(
    tmp_path: Path,
    *,
    budget_max: int = 1,
) -> design.AcquisitionSession:
    tmp_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    source = _create_v1_source(tmp_path / "source-v1.db", tmp_path)
    prepared = trial.prepare_trial_copy(source, temp_root=tmp_path)
    return design.prepare_acquisition_session(
        prepared,
        _config(budget_max=budget_max),
    )


def _safe_surfaces(exc: BaseException) -> str:
    return "\n".join(
        (
            str(exc),
            repr(exc),
            repr(exc.args),
            "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ),
        )
    )


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


def test_operation_allowlist_rejects_before_transport(tmp_path):
    session = _session(tmp_path)
    transport = design.FakeTransport([RAW_FIXTURE.read_bytes()])
    with pytest.raises(design.AcquisitionPolicyError):
        design.run_acquisition(
            session,
            transport,
            run_id="allowlist",
            operation="daily_matches",
            observed_at=T0,
        )
    assert transport.calls == []
    assert design.request_ledger(session, "allowlist") == []


def test_budget_exhaustion_and_retry_attempts_are_durable(tmp_path):
    session = _session(tmp_path, budget_max=2)
    transport = design.FakeTransport(
        [
            design.FakeTransportFailure("UNSAFE_TRANSPORT_MARKER_42"),
            RAW_FIXTURE.read_bytes(),
        ]
    )
    result = design.run_acquisition(
        session,
        transport,
        run_id="retry",
        operation="league_matches",
        observed_at=T0,
    )
    assert result["phase"] == "COMPLETED"
    assert len(transport.calls) == 2
    ledger = design.request_ledger(session, "retry")
    assert [row["attempt_ordinal"] for row in ledger] == [1, 2]
    assert [row["terminal_outcome"] for row in ledger] == [
        "FAILED_SAFE",
        "SUCCEEDED",
    ]
    assert [row["request_id"] for row in ledger] == ["retry:1", "retry:2"]
    assert [row["response_receipt_state"] for row in ledger] == [
        "NO_RESPONSE",
        "RECORDED",
    ]
    assert all(row["intent_recorded_at"].endswith("Z") for row in ledger)
    assert all(row["provider"] == "fotmob" for row in ledger)
    assert all(row["competition_id"] == 78 for row in ledger)
    assert all(
        row["competition_name"] == "FIFA Club World Cup" for row in ledger
    )
    assert ledger[0]["artifact_sha256"] is None
    assert ledger[1]["artifact_sha256"] == RAW_SHA256
    assert ledger[1]["artifact_size"] == len(RAW_FIXTURE.read_bytes())
    assert all(row["budget_maximum"] == 2 for row in ledger)
    assert all("UNSAFE_TRANSPORT_MARKER_42" not in repr(row) for row in ledger)
    assert transport.calls == [
        {
            "operation": "league_matches",
            "competition_id": 78,
            "requested_season": "2025",
        },
        {
            "operation": "league_matches",
            "competition_id": 78,
            "requested_season": "2025",
        },
    ]

    exhausted = _session(tmp_path / "exhausted", budget_max=1)
    first = design.FakeTransport([design.FakeTransportFailure("FIRST")])
    with pytest.raises(design.AcquisitionBudgetError):
        design.run_acquisition(
            exhausted,
            first,
            run_id="budget",
            operation="league_matches",
            observed_at=T0,
        )
    assert len(first.calls) == 1
    second = design.FakeTransport([RAW_FIXTURE.read_bytes()])
    with pytest.raises(design.AcquisitionBudgetError):
        design.run_acquisition(
            exhausted,
            second,
            run_id="budget",
            operation="league_matches",
            observed_at=T0,
        )
    assert second.calls == []


def test_dispatch_crash_becomes_outcome_unknown_without_retry(tmp_path):
    session = _session(tmp_path)
    transport = design.FakeTransport([RAW_FIXTURE.read_bytes()])
    with pytest.raises(design.AcquisitionInjectedCrash):
        design.run_acquisition(
            session,
            transport,
            run_id="unknown",
            operation="league_matches",
            observed_at=T0,
            fault_point=design.FAULT_AFTER_TRANSPORT_BEFORE_RECEIPT,
        )
    assert len(transport.calls) == 1
    retry = design.FakeTransport([RAW_FIXTURE.read_bytes()])
    with pytest.raises(design.AcquisitionOutcomeUnknownError):
        design.run_acquisition(
            session,
            retry,
            run_id="unknown",
            operation="league_matches",
            observed_at=T0,
        )
    assert retry.calls == []
    row = design.request_ledger(session, "unknown")[0]
    assert row["dispatch_state"] == "OUTCOME_UNKNOWN"
    assert row["terminal_outcome"] == "OUTCOME_UNKNOWN"


def test_artifact_is_atomic_private_and_bound_to_ledger(tmp_path):
    session = _session(tmp_path)
    result = design.run_acquisition(
        session,
        design.FakeTransport([RAW_FIXTURE.read_bytes()]),
        run_id="artifact",
        operation="league_matches",
        observed_at=T0,
    )
    artifact = design.artifact_path(session, "artifact")
    metadata = artifact.lstat()
    assert stat.S_ISREG(metadata.st_mode)
    assert metadata.st_uid == os.getuid()
    assert metadata.st_nlink == 1
    assert stat.S_IMODE(metadata.st_mode) == 0o600
    assert artifact.read_bytes() == RAW_FIXTURE.read_bytes()
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == RAW_SHA256
    assert result["artifact_sha256"] == RAW_SHA256
    assert not list(artifact.parent.glob("*.tmp-*"))
    artifact_rows = design.artifact_ledger(session, "artifact")
    assert len(artifact_rows) == 1
    assert artifact_rows[0]["sha256"] == RAW_SHA256
    assert artifact_rows[0]["size"] == len(RAW_FIXTURE.read_bytes())


def test_artifact_tamper_rejects_before_offline_apply(tmp_path):
    session = _session(tmp_path)
    with pytest.raises(design.AcquisitionInjectedCrash):
        design.run_acquisition(
            session,
            design.FakeTransport([RAW_FIXTURE.read_bytes()]),
            run_id="tamper",
            operation="league_matches",
            observed_at=T0,
            fault_point=design.FAULT_AFTER_ARTIFACT_RENAME_BEFORE_LEDGER,
        )
    artifact = design.expected_artifact_path(session, "tamper")
    artifact.write_bytes(b'{"tampered":true}')
    artifact.chmod(0o600)
    with pytest.raises(design.AcquisitionArtifactError):
        design.run_acquisition(
            session,
            design.FakeTransport([]),
            run_id="tamper",
            operation="league_matches",
            observed_at=T0,
        )
    assert all(
        value == 0
        for value in _business_counts(
            design.shadow_database_path(session)
        ).values()
    )


def test_completed_replay_reverifies_artifact_and_database_truth(tmp_path):
    session = _session(tmp_path)
    design.run_acquisition(
        session,
        design.FakeTransport([RAW_FIXTURE.read_bytes()]),
        run_id="completed-tamper",
        operation="league_matches",
        observed_at=T0,
    )
    artifact = design.artifact_path(session, "completed-tamper")
    artifact.write_bytes(b'{"tampered":true}')
    artifact.chmod(0o600)
    replay = design.FakeTransport([])
    with pytest.raises(design.AcquisitionArtifactError):
        design.run_acquisition(
            session,
            replay,
            run_id="completed-tamper",
            operation="league_matches",
            observed_at=T0,
        )
    assert replay.calls == []


@pytest.mark.parametrize(
    "mutator",
    [
        lambda raw: b'{"x":1,"x":1}',
        lambda raw: raw.replace(
            b'"allMatches": [',
            b'"hasMore": true, "allMatches": [',
            1,
        ),
    ],
    ids=["duplicate-json", "pagination-detected"],
)
def test_existing_strict_json_and_pagination_gate_is_used(tmp_path, mutator):
    raw = mutator(RAW_FIXTURE.read_bytes())
    session = _session(tmp_path)
    with pytest.raises(design.AcquisitionArtifactError):
        design.run_acquisition(
            session,
            design.FakeTransport([raw]),
            run_id="strict-gate",
            operation="league_matches",
            observed_at=T0,
        )
    assert all(
        value == 0
        for value in _business_counts(
            design.shadow_database_path(session)
        ).values()
    )


def test_real_offline_handoff_exact_replay_and_later_observation(tmp_path):
    session = _session(tmp_path, budget_max=3)
    first_transport = design.FakeTransport([RAW_FIXTURE.read_bytes()])
    first = design.run_acquisition(
        session,
        first_transport,
        run_id="run-one",
        operation="league_matches",
        observed_at=T0,
    )
    assert first["identity_count"] == 66
    assert first["snapshot_count"] == 66
    assert first["feature_count"] == 126
    assert first["lineage_input_count"] == 334

    replay = design.FakeTransport([])
    assert design.run_acquisition(
        session,
        replay,
        run_id="run-one",
        operation="league_matches",
        observed_at=T0,
    ) == first
    assert replay.calls == []

    later = design.run_acquisition(
        session,
        design.FakeTransport([RAW_FIXTURE.read_bytes()]),
        run_id="run-two",
        operation="league_matches",
        observed_at=T1,
    )
    assert later["snapshot_count"] == 66
    counts = _business_counts(design.shadow_database_path(session))
    assert counts["schedule_match_identity"] == 66
    assert counts["schedule_match_state_snapshot"] == 66
    assert counts["schedule_observation_event"] == 2
    assert counts["schedule_match_observation"] == 132
    assert counts["schedule_rest_feature"] == 126


def test_out_of_order_observation_does_not_regress_current(tmp_path):
    session = _session(tmp_path, budget_max=2)
    for run_id, observed_at in (("later", T1), ("earlier", T_EARLY)):
        design.run_acquisition(
            session,
            design.FakeTransport([RAW_FIXTURE.read_bytes()]),
            run_id=run_id,
            operation="league_matches",
            observed_at=observed_at,
        )
    conn = sqlite3.connect(design.shadow_database_path(session))
    try:
        current = conn.execute(
            """
            SELECT observed_at
            FROM current_schedule_match_state
            WHERE provider='fotmob' AND provider_match_id='4685744'
            """
        ).fetchone()[0]
    finally:
        conn.close()
    assert current == T1


def _mutated_raw(mutator) -> bytes:
    document = json.loads(RAW_FIXTURE.read_text(encoding="utf-8"))
    mutator(document)
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_status_matrix_rejection_remains_before_business_apply(tmp_path):
    raw = _mutated_raw(
        lambda document: document["fixtures"]["allMatches"][0]["status"].update(
            {
                "started": True,
                "finished": True,
                "cancelled": True,
            }
        )
    )
    session = _session(tmp_path)
    with pytest.raises(design.AcquisitionArtifactError):
        design.run_acquisition(
            session,
            design.FakeTransport([raw]),
            run_id="invalid-status",
            operation="league_matches",
            observed_at=T0,
        )
    assert all(
        value == 0
        for value in _business_counts(
            design.shadow_database_path(session)
        ).values()
    )


def test_changed_state_versions_snapshot_and_retains_point_in_time_lineage(
    tmp_path,
):
    session = _session(tmp_path, budget_max=2)
    design.run_acquisition(
        session,
        design.FakeTransport([RAW_FIXTURE.read_bytes()]),
        run_id="state-before",
        operation="league_matches",
        observed_at=T0,
    )

    def change_kickoff(document):
        target = next(
            row
            for row in document["fixtures"]["allMatches"]
            if int(row["id"]) == 4685746
        )
        target["status"]["utcTime"] = "2025-06-23T20:00:00Z"

    design.run_acquisition(
        session,
        design.FakeTransport([_mutated_raw(change_kickoff)]),
        run_id="state-after",
        operation="league_matches",
        observed_at=T1,
    )
    conn = sqlite3.connect(design.shadow_database_path(session))
    conn.row_factory = sqlite3.Row
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_match_state_snapshot"
        ).fetchone()[0] == 67
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_rest_feature"
        ).fetchone()[0] > 126
        identity_id = conn.execute(
            """
            SELECT id FROM schedule_match_identity
            WHERE provider='fotmob' AND provider_match_id='4685746'
            """
        ).fetchone()[0]
        before = conn.execute(
            """
            SELECT kickoff_at_utc
            FROM schedule_match_state_snapshot AS snapshot
            JOIN schedule_match_observation AS observation
              ON observation.snapshot_id=snapshot.id
            WHERE snapshot.match_identity_id=? AND observation.observed_at=?
            """,
            (identity_id, T0),
        ).fetchone()[0]
        current = conn.execute(
            """
            SELECT kickoff_at_utc FROM current_schedule_match_state
            WHERE match_identity_id=?
            """,
            (identity_id,),
        ).fetchone()[0]
        assert before == "2025-06-23T01:00:00.000000Z"
        assert current == "2025-06-23T20:00:00.000000Z"
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
    finally:
        conn.close()


def test_feature_transaction_retry_uses_bound_artifact_not_transport(tmp_path):
    session = _session(tmp_path)
    with pytest.raises(design.AcquisitionArtifactError):
        design.run_acquisition(
            session,
            design.FakeTransport([RAW_FIXTURE.read_bytes()]),
            run_id="feature-retry",
            operation="league_matches",
            observed_at=T0,
            fault_point=shadow.FAULT_FEATURE_TRANSACTION_MID,
        )
    counts = _business_counts(design.shadow_database_path(session))
    assert counts["schedule_match_state_snapshot"] == 66
    assert counts["schedule_rest_feature"] == 0
    retry = design.FakeTransport([])
    result = design.run_acquisition(
        session,
        retry,
        run_id="feature-retry",
        operation="league_matches",
        observed_at=T0,
    )
    assert retry.calls == []
    assert result["feature_count"] == 126
    assert result["lineage_input_count"] == 334


def test_absent_fixture_is_not_inferred_cancelled_or_deleted(tmp_path):
    session = _session(tmp_path, budget_max=2)
    design.run_acquisition(
        session,
        design.FakeTransport([RAW_FIXTURE.read_bytes()]),
        run_id="absence-before",
        operation="league_matches",
        observed_at=T0,
    )

    def remove_match(document):
        document["fixtures"]["allMatches"] = [
            row
            for row in document["fixtures"]["allMatches"]
            if int(row["id"]) != 4685744
        ]

    with pytest.raises(design.AcquisitionArtifactError):
        design.run_acquisition(
            session,
            design.FakeTransport([_mutated_raw(remove_match)]),
            run_id="absence-after",
            operation="league_matches",
            observed_at=T1,
        )
    conn = sqlite3.connect(design.shadow_database_path(session))
    try:
        row = conn.execute(
            """
            SELECT cancelled, observed_at
            FROM current_schedule_match_state
            WHERE provider='fotmob' AND provider_match_id='4685744'
            """
        ).fetchone()
        assert row == (0, T0)
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_match_identity"
        ).fetchone()[0] == 66
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_observation_event"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_session_requires_prepared_copy_not_raw_path(tmp_path):
    source = _create_v1_source(tmp_path / "source.db", tmp_path)
    with pytest.raises(design.AcquisitionSessionError):
        design.prepare_acquisition_session(source, _config())


def test_fixed_safe_exception_surface_and_no_secret_persistence(tmp_path):
    marker = "SECRET_AUTH_URL_MARKER_42"
    session = _session(tmp_path)
    with pytest.raises(design.AcquisitionBudgetError) as captured:
        design.run_acquisition(
            session,
            design.FakeTransport(
                [design.FakeTransportFailure(f"https://user:pass@host/{marker}")]
            ),
            run_id="secret",
            operation="league_matches",
            observed_at=T0,
        )
    assert marker not in _safe_surfaces(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert marker.encode() not in design.control_database_path(session).read_bytes()
    assert marker.encode() not in session.descriptor_path.read_bytes()


def test_network_is_hard_blocked_while_fake_transport_succeeds(
    tmp_path,
    monkeypatch,
):
    def blocked(*args, **kwargs):
        raise AssertionError("network forbidden")

    original_socket = socket.socket

    def guarded_socket(family=socket.AF_INET, *args, **kwargs):
        if family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("network forbidden")
        return original_socket(family, *args, **kwargs)

    monkeypatch.setattr(socket, "socket", guarded_socket)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    try:
        import requests

        monkeypatch.setattr(requests.sessions.Session, "request", blocked)
    except ImportError:
        pass
    try:
        from curl_cffi import requests as cffi_requests

        monkeypatch.setattr(cffi_requests, "get", blocked)
    except ImportError:
        pass

    session = _session(tmp_path)
    result = design.run_acquisition(
        session,
        design.FakeTransport([RAW_FIXTURE.read_bytes()]),
        run_id="network-block",
        operation="league_matches",
        observed_at=T0,
    )
    assert result["phase"] == "COMPLETED"


def test_response_size_limit_fails_closed_after_one_attempt(tmp_path):
    session = _session(tmp_path)
    raw = b"{" + (b" " * design.MAX_ACQUISITION_RESPONSE_BYTES) + b"}"
    transport = design.FakeTransport([raw])
    with pytest.raises(design.AcquisitionArtifactError):
        design.run_acquisition(
            session,
            transport,
            run_id="oversize",
            operation="league_matches",
            observed_at=T0,
        )
    assert len(transport.calls) == 1
    assert design.artifact_ledger(session, "oversize") == []


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
        timeout=90,
        check=False,
    )


@pytest.mark.parametrize(
    ("fault_point", "first_calls", "resume_calls", "terminal"),
    [
        (design.FAULT_AFTER_INTENT_BEFORE_TRANSPORT, 0, 1, "COMPLETED"),
        (
            design.FAULT_AFTER_TRANSPORT_BEFORE_RECEIPT,
            1,
            0,
            "OUTCOME_UNKNOWN",
        ),
        (design.FAULT_AFTER_RECEIPT_BEFORE_ARTIFACT_RENAME, 1, 0, "COMPLETED"),
        (design.FAULT_AFTER_ARTIFACT_RENAME_BEFORE_LEDGER, 1, 0, "COMPLETED"),
        (design.FAULT_AFTER_ARTIFACT_VALIDATED_BEFORE_APPLY, 1, 0, "COMPLETED"),
        (
            shadow.FAULT_AFTER_STATE_COMMIT_BEFORE_MANIFEST,
            1,
            0,
            "COMPLETED",
        ),
        (
            shadow.FAULT_AFTER_FEATURE_COMMIT_BEFORE_MANIFEST,
            1,
            0,
            "COMPLETED",
        ),
    ],
)
def test_real_subprocess_recovery_boundaries(
    tmp_path,
    fault_point,
    first_calls,
    resume_calls,
    terminal,
):
    source = _create_v1_source(tmp_path / "source.db", tmp_path)
    first_code = """
import json, sys
from pathlib import Path
from analysis.schedule_state_migration_trial import schedule_state_migration_trial as trial
from analysis.single_competition_live_shadow_design import single_competition_live_shadow_design as d
p = trial.prepare_trial_copy(Path(sys.argv[1]), temp_root=Path(sys.argv[2]))
c = d.AcquisitionConfig(
    provider="fotmob", competition_id=78,
    competition_name="FIFA Club World Cup", requested_season="2025",
    allowed_operations=("league_matches",), budget_max=1,
    expected_fixture_count=66, competition_class="international_club",
    artifact_schema_version="cwc_schedule_raw_projection_v1",
)
s = d.prepare_acquisition_session(p, c)
t = d.FakeTransport([Path(sys.argv[3]).read_bytes()])
try:
    d.run_acquisition(
        s, t, run_id="restart", operation="league_matches",
        observed_at=sys.argv[4], fault_point=sys.argv[5],
    )
except d.AcquisitionInjectedCrash:
    pass
print(json.dumps({
    "descriptor": str(s.descriptor_path),
    "capability": s.capability,
    "calls": len(t.calls),
}))
"""
    first = _run_process(
        first_code,
        str(source),
        str(tmp_path),
        str(RAW_FIXTURE),
        T0,
        fault_point,
    )
    assert first.returncode == 0, first.stderr
    handoff = json.loads(first.stdout.strip())
    assert handoff["calls"] == first_calls

    second_code = """
import json, sys
from pathlib import Path
from analysis.single_competition_live_shadow_design import single_competition_live_shadow_design as d
s = d.reopen_acquisition_session(Path(sys.argv[1]), sys.argv[2])
t = d.FakeTransport([Path(sys.argv[3]).read_bytes()] if sys.argv[4] == "1" else [])
try:
    result = d.run_acquisition(
        s, t, run_id="restart", operation="league_matches",
        observed_at=sys.argv[5],
    )
    terminal = result["phase"]
except d.AcquisitionOutcomeUnknownError:
    terminal = "OUTCOME_UNKNOWN"
print(json.dumps({"terminal": terminal, "calls": len(t.calls)}))
"""
    second = _run_process(
        second_code,
        handoff["descriptor"],
        handoff["capability"],
        str(RAW_FIXTURE),
        str(resume_calls),
        T0,
    )
    assert second.returncode == 0, second.stderr
    resumed = json.loads(second.stdout.strip())
    assert resumed == {"terminal": terminal, "calls": resume_calls}


def test_cross_process_lock_rejects_before_transport(tmp_path):
    session = _session(tmp_path)
    holder_code = """
import sys
from pathlib import Path
from analysis.single_competition_live_shadow_design import single_competition_live_shadow_design as d
s = d.reopen_acquisition_session(Path(sys.argv[1]), sys.argv[2])
with d._acquisition_lock(s):
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
        transport = design.FakeTransport([RAW_FIXTURE.read_bytes()])
        with pytest.raises(design.AcquisitionConcurrencyError):
            design.run_acquisition(
                session,
                transport,
                run_id="locked",
                operation="league_matches",
                observed_at=T0,
            )
        assert transport.calls == []
    finally:
        if holder.stdin is not None:
            holder.stdin.write("\n")
            holder.stdin.flush()
        holder.wait(timeout=10)
        for stream in (holder.stdin, holder.stdout, holder.stderr):
            if stream is not None:
                stream.close()
