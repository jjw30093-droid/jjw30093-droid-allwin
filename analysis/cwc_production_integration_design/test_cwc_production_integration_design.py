"""Permanent offline gates for the CWC production-integration design prototype."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import sqlite3
import traceback
from datetime import datetime, timezone
from pathlib import Path

import pytest

from analysis.cwc_production_integration_design import (
    cwc_production_integration_design as design,
)


OBSERVED_AT = "2026-07-25T12:00:00Z"
MAN_CITY_ID = 8456
CANCELLED_IDS = {4685727, 4685729, 4685730}
MAN_CITY_MATCH_IDS = [4685744, 4685746, 4685748, 4685772]
SAFE_TIMESTAMP_ERROR = "invalid UTC timestamp"


def _document() -> dict:
    return design.load_canonical_fixture()


def _batch(document: dict | None = None) -> dict:
    return design.parse_canonical_fixture(
        document or _document(),
        observed_at=OBSERVED_AT,
    )


def _manchester_city_features(batch: dict) -> dict[int, dict]:
    return {
        row["provider_match_id"]: row
        for row in batch["rest_features"]
        if row["team_id"] == MAN_CITY_ID
    }


def _lineage_hashes(feature: dict) -> tuple[str, str]:
    return feature["input_set_hash"], feature["payload_hash"]


def _assert_safe_timestamp_failure(
    action,
    *,
    marker: str,
    capsys,
    caplog,
) -> None:
    caplog.clear()
    with pytest.raises(design.PrototypeDataError) as caught:
        action()

    captured = capsys.readouterr()
    exc = caught.value
    rendered_traceback = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    surfaces = (
        str(exc),
        repr(exc),
        repr(exc.args),
        rendered_traceback,
        captured.out,
        captured.err,
        caplog.text,
        repr(exc.__cause__),
        repr(exc.__context__),
    )

    assert str(exc) == SAFE_TIMESTAMP_ERROR
    assert exc.args == (SAFE_TIMESTAMP_ERROR,)
    assert exc.__cause__ is None
    assert exc.__context__ is None
    assert exc.__suppress_context__ is True
    assert all(marker not in surface for surface in surfaces)


@pytest.mark.parametrize(
    ("value", "marker"),
    [
        (
            "not-a-timestamp-SYNTH_TS_PLAIN_7F37",
            "SYNTH_TS_PLAIN_7F37",
        ),
        (
            "/tmp/SYNTH_TS_PATH_8C41/private-payload.json",
            "SYNTH_TS_PATH_8C41",
        ),
        (
            "http://offline-user:SYNTH_TS_PROXY_5A29@proxy.invalid:8080",
            "SYNTH_TS_PROXY_5A29",
        ),
        (
            "Authorization: Basic SYNTH_TS_BASIC_4D13",
            "SYNTH_TS_BASIC_4D13",
        ),
        (
            "Authorization: Bearer SYNTH_TS_BEARER_6E52",
            "SYNTH_TS_BEARER_6E52",
        ),
        (
            "token=SYNTH_TS_TOKEN_9B74",
            "SYNTH_TS_TOKEN_9B74",
        ),
        (
            '{"body":{"secret":"SYNTH_TS_BODY_3A68"}}',
            "SYNTH_TS_BODY_3A68",
        ),
        (
            "2025-01-01T00:00:00+99:99-SYNTH_TS_ZONE_2F85",
            "SYNTH_TS_ZONE_2F85",
        ),
        (
            {"timestamp": "SYNTH_TS_OBJECT_1C96"},
            "SYNTH_TS_OBJECT_1C96",
        ),
        (
            "2025-01-01T00:00:00Z\x00☃SYNTH_TS_UNICODE_0D47",
            "SYNTH_TS_UNICODE_0D47",
        ),
    ],
    ids=(
        "plain-invalid",
        "absolute-path",
        "proxy-credential-url",
        "basic-authorization",
        "bearer-authorization",
        "token",
        "json-body",
        "invalid-timezone",
        "non-string",
        "unicode-invalid-character",
    ),
)
def test_timestamp_error_boundary_direct_marker_matrix(
    value,
    marker,
    capsys,
    caplog,
):
    _assert_safe_timestamp_failure(
        lambda: design._parse_utc(value, "kickoff_at_utc"),
        marker=marker,
        capsys=capsys,
        caplog=caplog,
    )


def test_timestamp_error_boundary_helper_kickoff_is_sanitized(
    capsys,
    caplog,
):
    marker = "SYNTH_TS_HELPER_BEARER_7D21"
    timeline, match_by_id = _manchester_city_lineage_inputs()
    changed_matches = copy.deepcopy(match_by_id)
    changed_matches[timeline[0]["provider_match_id"]]["kickoff_at_utc"] = (
        f"Authorization: Bearer {marker}"
    )

    _assert_safe_timestamp_failure(
        lambda: design.build_feature_input_set_hash(
            timeline[:1],
            changed_matches,
        ),
        marker=marker,
        capsys=capsys,
        caplog=caplog,
    )


def test_timestamp_error_boundary_canonical_parser_kickoff_is_sanitized(
    capsys,
    caplog,
):
    marker = "SYNTH_TS_CANONICAL_PATH_4E83"
    document = _document()
    document["fixtures"][0]["status"]["utcTime"] = (
        f"/tmp/{marker}/canonical-secret.json"
    )

    _assert_safe_timestamp_failure(
        lambda: design.parse_canonical_fixture(
            document,
            observed_at=OBSERVED_AT,
        ),
        marker=marker,
        capsys=capsys,
        caplog=caplog,
    )


def test_timestamp_error_boundary_accepts_z_and_explicit_utc_offset():
    expected = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    assert design._parse_utc(
        "2025-01-02T03:04:05Z",
        "kickoff_at_utc",
    ) == expected
    assert design._parse_utc(
        "2025-01-02T03:04:05+00:00",
        "kickoff_at_utc",
    ) == expected


@pytest.mark.parametrize(
    "value",
    [
        "2025-01-02T03:04:05",
        "2025-01-02T03:04:05+08:00",
    ],
    ids=("naive", "non-utc-offset"),
)
def test_timestamp_error_boundary_rejects_non_utc_semantics_without_chain(
    value,
    capsys,
    caplog,
):
    marker = "SYNTH_TS_SEMANTIC_FIELD_5B62"
    _assert_safe_timestamp_failure(
        lambda: design._parse_utc(value, marker),
        marker=marker,
        capsys=capsys,
        caplog=caplog,
    )


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "prototype_competition_registry",
            "prototype_match_calendar",
            "prototype_team_match",
            "prototype_team_rest_feature",
            "prototype_schedule_observation",
        )
    }


def test_canonical_fixture_sha_provenance_and_redaction():
    path = design.CANONICAL_FIXTURE_PATH
    raw = path.read_bytes()
    document = json.loads(raw)

    assert hashlib.sha256(raw).hexdigest() == design.CANONICAL_FIXTURE_SHA256
    assert document["provenance"] == {
        "description": "trimmed from validated saved response",
        "pagination_status": "NOT_DETECTED_FOR_SAVED_RESPONSE",
        "source_artifact_sha256": design.SOURCE_ARTIFACT_SHA256,
        "transformation_version": design.TRANSFORMATION_VERSION,
    }
    assert len(document["fixtures"]) == 66
    assert "/tmp/" not in raw.decode("utf-8").casefold()
    assert design.credential_shape_findings(raw.decode("utf-8")) == []


def test_canonical_fixture_identity_and_only_public_shape():
    document = _document()
    assert document["competition"] == {
        "id": 78,
        "name": "FIFA Club World Cup",
        "selectedSeason": "2025",
    }
    assert set(document) == {"competition", "fixtures", "provenance"}
    assert all(
        set(row) == {"away", "home", "id", "round", "status"}
        and set(row["home"]) == {"id", "name"}
        and set(row["away"]) == {"id", "name"}
        and set(row["status"])
        == {"cancelled", "finished", "short", "started", "utcTime"}
        for row in document["fixtures"]
    )


@pytest.mark.parametrize(
    ("strategy", "label", "explicit_label", "expected"),
    [
        ("calendar_year", "2025", None, "2025"),
        ("split_year", "2024/2025", None, "2024/2025"),
        ("explicit", "CWC-2025", "CWC-2025", "CWC-2025"),
    ],
)
def test_season_strategy_accepts_only_explicit_valid_shape(
    strategy,
    label,
    explicit_label,
    expected,
):
    assert (
        design.validate_season_strategy(
            strategy,
            label,
            explicit_label=explicit_label,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("strategy", "label", "explicit_label"),
    [
        ("calendar_year", True, None),
        ("calendar_year", "2024/2025", None),
        ("split_year", "2025/2025", None),
        ("split_year", "2025/2027", None),
        ("split_year", "2025", None),
        ("explicit", "CWC-2025", None),
        ("explicit", "CWC-2025", "OTHER"),
        ("guessed_from_name", "2025", None),
    ],
)
def test_season_strategy_fails_closed(strategy, label, explicit_label):
    with pytest.raises(design.SeasonStrategyError):
        design.validate_season_strategy(
            strategy,
            label,
            explicit_label=explicit_label,
        )


def test_registry_is_fixed_to_validated_cwc_response():
    row = _batch()["registry"]
    assert row == {
        "provider": "fotmob",
        "competition_id": 78,
        "expected_name": "FIFA Club World Cup",
        "observed_name": "FIFA Club World Cup",
        "competition_class": "international_club",
        "requested_season": "2025",
        "returned_season": "2025",
        "season_strategy": "calendar_year",
        "identity_verified": 1,
        "season_verified": 1,
        "pagination_status": "NOT_DETECTED_FOR_SAVED_RESPONSE",
        "fixture_count": 66,
        "source_artifact_sha256": design.SOURCE_ARTIFACT_SHA256,
        "completeness_status": "VALIDATED_SAVED_RESPONSE_ONLY",
    }


def test_all_66_matches_and_132_team_relationships_are_preserved():
    batch = _batch()
    assert len(batch["matches"]) == 66
    assert len(batch["team_matches"]) == 132
    assert len({row["provider_match_id"] for row in batch["matches"]}) == 66
    assert sum(not row["cancelled"] for row in batch["matches"]) == 63
    assert sum(row["cancelled"] for row in batch["matches"]) == 3
    assert {
        row["provider_match_id"]
        for row in batch["matches"]
        if row["cancelled"]
    } == CANCELLED_IDS


def test_cancelled_matches_are_retained_but_excluded_from_observed_load():
    batch = _batch()
    cancelled_team_rows = [
        row
        for row in batch["team_matches"]
        if row["provider_match_id"] in CANCELLED_IDS
    ]
    assert len(cancelled_team_rows) == 6
    assert all(row["cancelled"] is True for row in cancelled_team_rows)
    assert all(row["eligible_for_load"] is False for row in cancelled_team_rows)
    assert all(row["exclusion_reason"] == "cancelled" for row in cancelled_team_rows)

    features = batch["rest_features"]
    assert len(features) == 126
    assert not CANCELLED_IDS & {
        row["provider_match_id"] for row in features
    }
    assert not CANCELLED_IDS & {
        row["previous_match_id"]
        for row in features
        if row["previous_match_id"] is not None
    }


def test_manchester_city_observed_timeline_and_kickoff_gaps():
    batch = _batch()
    matches = {
        row["provider_match_id"]: row
        for row in batch["matches"]
    }
    team_rows = [
        row for row in batch["team_matches"] if row["team_id"] == MAN_CITY_ID
    ]
    features = sorted(
        (
            row
            for row in batch["rest_features"]
            if row["team_id"] == MAN_CITY_ID
        ),
        key=lambda row: row["kickoff_at_utc"],
    )

    assert [row["provider_match_id"] for row in team_rows] == MAN_CITY_MATCH_IDS
    assert all(matches[mid]["finished"] is True for mid in MAN_CITY_MATCH_IDS)
    assert all(matches[mid]["cancelled"] is False for mid in MAN_CITY_MATCH_IDS)
    assert [row["provider_match_id"] for row in features] == MAN_CITY_MATCH_IDS
    assert [row["kickoff_gap_hours"] for row in features] == [
        None,
        105.0,
        90.0,
        102.0,
    ]
    assert matches[4685772]["status"] == "AET"
    assert features[-1]["kickoff_gap_hours"] == 102.0
    assert features[-1]["provenance"] == design.REST_PROVENANCE


def test_future_last_match_kickoff_does_not_change_first_feature_lineage():
    document = _document()
    original = design.parse_canonical_fixture(
        document,
        observed_at=OBSERVED_AT,
    )
    changed = copy.deepcopy(document)
    target = next(
        row for row in changed["fixtures"] if int(row["id"]) == 4685772
    )
    target["status"]["utcTime"] = "2025-07-01T02:00:00Z"
    regenerated = design.parse_canonical_fixture(
        changed,
        observed_at="2026-07-25T12:05:00Z",
    )

    original_features = _manchester_city_features(original)
    regenerated_features = _manchester_city_features(regenerated)
    original_first = original_features[4685744]
    regenerated_first = regenerated_features[4685744]
    assert {
        key: value
        for key, value in original_first.items()
        if key not in {"input_set_hash", "payload_hash"}
    } == {
        key: value
        for key, value in regenerated_first.items()
        if key not in {"input_set_hash", "payload_hash"}
    }
    for match_id in MAN_CITY_MATCH_IDS[:3]:
        assert _lineage_hashes(
            original_features[match_id]
        ) == _lineage_hashes(regenerated_features[match_id])
    assert _lineage_hashes(
        original_features[4685772]
    ) != _lineage_hashes(regenerated_features[4685772])


def test_feature_input_hash_rejects_a_future_match_before_current():
    batch = _batch()
    match_by_id = {
        row["provider_match_id"]: row
        for row in batch["matches"]
    }
    manchester_city_rows = {
        row["provider_match_id"]: row
        for row in batch["team_matches"]
        if row["team_id"] == MAN_CITY_ID and row["eligible_for_load"]
    }

    with pytest.raises(
        design.PrototypeDataError,
        match="future match",
    ):
        design.build_feature_input_set_hash(
            [
                manchester_city_rows[4685772],
                manchester_city_rows[4685744],
            ],
            match_by_id,
        )


def _manchester_city_lineage_inputs() -> tuple[list[dict], dict[int, dict]]:
    batch = _batch()
    match_by_id = {
        row["provider_match_id"]: row
        for row in batch["matches"]
    }
    timeline = sorted(
        (
            row
            for row in batch["team_matches"]
            if row["team_id"] == MAN_CITY_ID and row["eligible_for_load"]
        ),
        key=lambda row: (
            match_by_id[row["provider_match_id"]]["kickoff_at_utc"],
            row["provider_match_id"],
        ),
    )
    return timeline, match_by_id


def test_feature_input_contract_valid_prefix_hash_is_stable():
    timeline, match_by_id = _manchester_city_lineage_inputs()
    prefix = timeline[:3]

    first = design.build_feature_input_set_hash(prefix, match_by_id)
    second = design.build_feature_input_set_hash(prefix, match_by_id)
    canonical_feature = _manchester_city_features(_batch())[4685748]

    assert first == second
    assert first == canonical_feature["input_set_hash"]


def test_feature_input_contract_rejects_reordered_historical_prefix():
    timeline, match_by_id = _manchester_city_lineage_inputs()

    with pytest.raises(
        design.PrototypeDataError,
        match="strictly increasing kickoff times",
    ):
        design.build_feature_input_set_hash(
            [timeline[1], timeline[0], timeline[2]],
            match_by_id,
        )


@pytest.mark.parametrize(
    "prefix_indexes",
    [
        (0, 0),
        (0, 1, 0),
    ],
    ids=["adjacent", "non-adjacent"],
)
def test_feature_input_contract_rejects_duplicate_match_ids(prefix_indexes):
    timeline, match_by_id = _manchester_city_lineage_inputs()

    with pytest.raises(
        design.PrototypeDataError,
        match="duplicate match id",
    ):
        design.build_feature_input_set_hash(
            [timeline[index] for index in prefix_indexes],
            match_by_id,
        )


def test_feature_input_contract_rejects_equal_kickoff():
    timeline, match_by_id = _manchester_city_lineage_inputs()
    equal_kickoff_matches = copy.deepcopy(match_by_id)
    first_match_id = timeline[0]["provider_match_id"]
    second_match_id = timeline[1]["provider_match_id"]
    equal_kickoff_matches[second_match_id]["kickoff_at_utc"] = (
        equal_kickoff_matches[first_match_id]["kickoff_at_utc"]
    )

    with pytest.raises(
        design.PrototypeDataError,
        match="strictly increasing kickoff times",
    ):
        design.build_feature_input_set_hash(
            timeline[:2],
            equal_kickoff_matches,
        )


def test_feature_input_contract_rejects_empty_prefix():
    with pytest.raises(
        design.PrototypeDataError,
        match="timeline prefix must not be empty",
    ):
        design.build_feature_input_set_hash([], {})


@pytest.mark.parametrize(
    (
        "competition_class",
        "finished",
        "cancelled",
        "kickoff_precision",
        "kickoff_at_utc",
        "eligible",
        "reason",
    ),
    [
        ("international_club", True, False, "exact", "2025-01-01T00:00:00Z", True, None),
        ("unknown", True, False, "exact", "2025-01-01T00:00:00Z", False, "competition_class_unverified"),
        ("friendly", True, False, "exact", "2025-01-01T00:00:00Z", False, "friendly"),
        ("international_club", True, False, "date_only", None, False, "kickoff_not_exact"),
        ("international_club", False, False, "exact", "2025-01-01T00:00:00Z", False, "unfinished"),
        ("international_club", False, True, "exact", "2025-01-01T00:00:00Z", False, "cancelled"),
    ],
)
def test_observed_load_eligibility_is_explicit(
    competition_class,
    finished,
    cancelled,
    kickoff_precision,
    kickoff_at_utc,
    eligible,
    reason,
):
    assert design.observed_load_eligibility(
        competition_class=competition_class,
        registry_verified=True,
        finished=finished,
        cancelled=cancelled,
        kickoff_precision=kickoff_precision,
        kickoff_at_utc=kickoff_at_utc,
    ) == (eligible, reason)


def test_temp_db_full_chain_counts_and_schema(tmp_path):
    db_path = tmp_path / "cwc-prototype.db"
    result = design.run_prototype(
        db_path,
        observed_at=OBSERVED_AT,
    )
    assert result["status"] == "DESIGN_VALIDATED_WITH_TEMP_DB"
    assert result["writes"] == {
        "registry": {"inserted": 1, "skipped": 0},
        "calendar": {"inserted": 66, "skipped": 0},
        "team_match": {"inserted": 132, "skipped": 0},
        "rest_feature": {"inserted": 126, "skipped": 0},
        "observation": {"inserted": 1, "skipped": 0},
    }

    conn = sqlite3.connect(db_path)
    try:
        assert _counts(conn) == {
            "prototype_competition_registry": 1,
            "prototype_match_calendar": 66,
            "prototype_team_match": 132,
            "prototype_team_rest_feature": 126,
            "prototype_schedule_observation": 1,
        }
        assert conn.execute(
            "SELECT COUNT(*) FROM prototype_match_calendar WHERE cancelled=1"
        ).fetchone()[0] == 3
        assert conn.execute(
            "SELECT COUNT(*) FROM prototype_team_match "
            "WHERE cancelled=1 AND eligible_for_load=0 "
            "AND exclusion_reason='cancelled'"
        ).fetchone()[0] == 6
    finally:
        conn.close()


def test_identical_full_chain_rerun_is_idempotent(tmp_path):
    db_path = tmp_path / "idempotent.db"
    design.run_prototype(db_path, observed_at=OBSERVED_AT)
    second = design.run_prototype(db_path, observed_at=OBSERVED_AT)
    assert second["writes"] == {
        "registry": {"inserted": 0, "skipped": 1},
        "calendar": {"inserted": 0, "skipped": 66},
        "team_match": {"inserted": 0, "skipped": 132},
        "rest_feature": {"inserted": 0, "skipped": 126},
        "observation": {"inserted": 0, "skipped": 1},
    }


def test_cancelled_mutation_is_a_payload_conflict(tmp_path):
    db_path = tmp_path / "cancelled-conflict.db"
    design.run_prototype(db_path, observed_at=OBSERVED_AT)
    document = _document()
    target = next(
        row for row in document["fixtures"] if int(row["id"]) == 4685727
    )
    target["status"]["cancelled"] = False

    conn = sqlite3.connect(db_path)
    try:
        before = _counts(conn)
        with pytest.raises(design.PrototypeConflictError):
            design.ingest_document(
                conn,
                document,
                observed_at=OBSERVED_AT,
            )
        assert _counts(conn) == before
    finally:
        conn.close()


def test_conflict_rolls_back_the_entire_batch(tmp_path):
    db_path = tmp_path / "rollback.db"
    conn = sqlite3.connect(db_path)
    try:
        design.init_prototype_db(conn)
        batch = _batch()
        final_row = batch["matches"][-1]
        conn.execute(
            """INSERT INTO prototype_match_calendar
               (provider, provider_match_id, competition_id, requested_season,
                competition_class, kickoff_at_utc, kickoff_precision,
                home_team_id, home_team_name, away_team_id, away_team_name,
                status, finished, cancelled, round, source_endpoint,
                source_artifact_sha256, payload_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                final_row["provider"],
                final_row["provider_match_id"],
                final_row["competition_id"],
                final_row["requested_season"],
                final_row["competition_class"],
                final_row["kickoff_at_utc"],
                final_row["kickoff_precision"],
                final_row["home_team_id"] + 1,
                final_row["home_team_name"],
                final_row["away_team_id"],
                final_row["away_team_name"],
                final_row["status"],
                int(final_row["finished"]),
                int(final_row["cancelled"]),
                final_row["round"],
                final_row["source_endpoint"],
                final_row["source_artifact_sha256"],
                "sentinel-conflict",
            ),
        )
        conn.commit()

        with pytest.raises(design.PrototypeConflictError):
            design.ingest_document(
                conn,
                _document(),
                observed_at=OBSERVED_AT,
            )
        assert _counts(conn) == {
            "prototype_competition_registry": 0,
            "prototype_match_calendar": 1,
            "prototype_team_match": 0,
            "prototype_team_rest_feature": 0,
            "prototype_schedule_observation": 0,
        }
    finally:
        conn.close()


def test_incompatible_prototype_schema_is_rejected_before_any_write(tmp_path):
    db_path = tmp_path / "old-schema.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE prototype_competition_registry "
            "(competition_id INTEGER PRIMARY KEY, marker TEXT)"
        )
        conn.execute(
            "INSERT INTO prototype_competition_registry VALUES (78, 'keep-me')"
        )
        conn.commit()
        with pytest.raises(design.PrototypeSchemaIncompatibleError):
            design.init_prototype_db(conn)
        assert conn.execute(
            "SELECT marker FROM prototype_competition_registry"
        ).fetchone()[0] == "keep-me"
        assert {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            if row[0].startswith("prototype_")
        } == {"prototype_competition_registry"}
    finally:
        conn.close()


def test_matching_columns_with_weakened_constraint_are_rejected(tmp_path):
    conn = sqlite3.connect(tmp_path / "weakened-schema.db")
    try:
        for statement in design._CREATE_STATEMENTS:
            weakened = statement.replace(
                " CHECK (identity_verified IN (0, 1))",
                "",
            )
            conn.execute(weakened)
        conn.commit()
        with pytest.raises(design.PrototypeSchemaIncompatibleError):
            design.init_prototype_db(conn)
        assert _counts(conn) == {
            "prototype_competition_registry": 0,
            "prototype_match_calendar": 0,
            "prototype_team_match": 0,
            "prototype_team_rest_feature": 0,
            "prototype_schedule_observation": 0,
        }
    finally:
        conn.close()


def test_source_artifact_hash_mismatch_is_rejected():
    document = _document()
    document["provenance"]["source_artifact_sha256"] = "0" * 64
    with pytest.raises(design.SourceArtifactHashMismatch):
        design.parse_canonical_fixture(
            document,
            observed_at=OBSERVED_AT,
        )


def test_canonical_fixture_file_hash_mismatch_is_rejected(tmp_path):
    document = _document()
    document["fixtures"][0]["round"] = "tampered"
    changed = tmp_path / "changed.json"
    changed.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    with pytest.raises(design.CanonicalFixtureHashMismatch):
        design.load_canonical_fixture(changed)


def test_invalid_team_id_rejects_whole_document_without_db_rows(tmp_path):
    document = _document()
    document["fixtures"][-1]["away"]["id"] = True
    conn = sqlite3.connect(tmp_path / "invalid.db")
    try:
        design.init_prototype_db(conn)
        with pytest.raises(design.PrototypeDataError):
            design.ingest_document(
                conn,
                document,
                observed_at=OBSERVED_AT,
            )
        assert _counts(conn) == {
            "prototype_competition_registry": 0,
            "prototype_match_calendar": 0,
            "prototype_team_match": 0,
            "prototype_team_rest_feature": 0,
            "prototype_schedule_observation": 0,
        }
    finally:
        conn.close()


def test_prototype_never_uses_live_runner_network_or_tmp_artifact(
    monkeypatch,
    tmp_path,
):
    from analysis.club_world_cup_single_pilot import (
        club_world_cup_single_pilot as sealed,
    )

    monkeypatch.setattr(
        sealed,
        "run_live",
        lambda: pytest.fail("sealed live runner must not be called"),
    )
    monkeypatch.setattr(
        sealed,
        "resume_live_from_saved_daily",
        lambda *args, **kwargs: pytest.fail("sealed resume must not be called"),
    )
    monkeypatch.setattr(
        sealed.fotmob_module.cffi_requests,
        "get",
        lambda *args, **kwargs: pytest.fail("transport must not be called"),
    )

    result = design.run_prototype(
        tmp_path / "offline-only.db",
        observed_at=OBSERVED_AT,
    )
    source = inspect.getsource(design)
    assert result["network_request_count"] == 0
    assert "/tmp/allwin-cwc-single-pilot" not in source
    assert "run_live(" not in source
    assert "resume_live_from_saved_daily(" not in source


def test_no_worker_registration_or_formal_migration():
    from backend.worker import runner

    assert not any("cwc" in name.casefold() for name in runner.REGISTRY)
    migration_names = {
        path.name
        for db_name in ("core", "platform", "odds")
        for path in (design.REPO_ROOT / "backend" / "migrations" / db_name).glob(
            "*.sql"
        )
    }
    assert not any("cwc" in name.casefold() for name in migration_names)


def test_public_summary_contains_no_credential_shape(tmp_path):
    summary = design.run_prototype(
        tmp_path / "safe-summary.db",
        observed_at=OBSERVED_AT,
    )
    rendered = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    assert design.credential_shape_findings(rendered) == []
    assert summary["production_integration"] == "NOT_STARTED"
    assert summary["network_request_count"] == 0
    assert summary["feature_scope"] == "observed_historical"
    assert summary["later_observation_idempotency"] == (
        "LATER_OBSERVATION_IDEMPOTENCY_VALIDATED"
    )
    assert summary["observation_ledger"] == "OBSERVATION_LEDGER_APPEND_ONLY"
    assert summary["business_content"] == "BUSINESS_CONTENT_IMMUTABLE"
    assert summary["point_in_time_feature_lineage"] == (
        "POINT_IN_TIME_FEATURE_LINEAGE_VALIDATED"
    )
    assert summary["future_match_hash_boundary"] == (
        "NO_FUTURE_MATCH_IN_EARLIER_FEATURE_HASH"
    )
    assert summary["production_schedule_state_design"] == (
        "PRODUCTION_MUTABLE_SNAPSHOT_SCHEMA_REQUIRED"
    )


def test_prototype_rejects_repository_database_paths_before_connect(monkeypatch):
    connect_calls = 0

    def fail_if_connected(*args, **kwargs):
        nonlocal connect_calls
        connect_calls += 1
        pytest.fail("a repository database path must be rejected before connect")

    monkeypatch.setattr(design.sqlite3, "connect", fail_if_connected)
    with pytest.raises(design.PrototypeDatabasePathError):
        design.run_prototype(
            design.REPO_ROOT / "data" / "cwc-prototype.db",
            observed_at=OBSERVED_AT,
        )
    assert connect_calls == 0
    assert not (design.REPO_ROOT / "data" / "cwc-prototype.db").exists()


# ── Observation ledger / stable-content closure ────────────────────────────

_BUSINESS_TABLE_KEYS = {
    "prototype_competition_registry": (
        "provider",
        "competition_id",
        "requested_season",
    ),
    "prototype_match_calendar": ("provider", "provider_match_id"),
    "prototype_team_match": ("provider", "provider_match_id", "team_id"),
    "prototype_team_rest_feature": (
        "provider",
        "provider_match_id",
        "team_id",
        "feature_version",
        "input_set_hash",
    ),
}


def _business_rows(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    return {
        table: conn.execute(
            f"SELECT * FROM {table} ORDER BY {', '.join(key)}"
        ).fetchall()
        for table, key in _BUSINESS_TABLE_KEYS.items()
    }


def test_stable_business_content_is_independent_of_observed_at():
    first = design.parse_canonical_fixture(
        _document(),
        observed_at=OBSERVED_AT,
    )
    later = design.parse_canonical_fixture(
        _document(),
        observed_at="2026-07-25T12:05:00Z",
    )

    for row_set in ("registry", "matches", "team_matches", "rest_features"):
        assert first[row_set] == later[row_set]
    assert first["observation"]["observed_at"] == OBSERVED_AT
    assert later["observation"]["observed_at"] == "2026-07-25T12:05:00Z"
    assert (
        first["observation"]["source_content_hash"]
        == later["observation"]["source_content_hash"]
    )


def test_same_fixture_later_observation_appends_only_ledger_event(tmp_path):
    db_path = tmp_path / "later-observation.db"
    design.run_prototype(db_path, observed_at=OBSERVED_AT)
    conn = sqlite3.connect(db_path)
    try:
        before_business = _business_rows(conn)
        first_observation = conn.execute(
            "SELECT * FROM prototype_schedule_observation"
        ).fetchall()
    finally:
        conn.close()

    second = design.run_prototype(
        db_path,
        observed_at="2026-07-25T12:05:00Z",
    )

    assert second["writes"] == {
        "registry": {"inserted": 0, "skipped": 1},
        "calendar": {"inserted": 0, "skipped": 66},
        "team_match": {"inserted": 0, "skipped": 132},
        "rest_feature": {"inserted": 0, "skipped": 126},
        "observation": {"inserted": 1, "skipped": 0},
    }
    conn = sqlite3.connect(db_path)
    try:
        assert _business_rows(conn) == before_business
        observations = conn.execute(
            "SELECT * FROM prototype_schedule_observation "
            "ORDER BY observed_at"
        ).fetchall()
    finally:
        conn.close()
    assert len(first_observation) == 1
    assert len(observations) == 2
    assert observations[0] == first_observation[0]


def test_same_observation_and_content_is_fully_idempotent(tmp_path):
    db_path = tmp_path / "same-observation.db"
    design.run_prototype(db_path, observed_at=OBSERVED_AT)
    second = design.run_prototype(db_path, observed_at=OBSERVED_AT)

    assert second["writes"]["observation"] == {"inserted": 0, "skipped": 1}
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM prototype_schedule_observation"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_earlier_observation_is_an_unambiguous_append_only_event(tmp_path):
    db_path = tmp_path / "out-of-order-observation.db"
    design.run_prototype(db_path, observed_at=OBSERVED_AT)
    result = design.run_prototype(
        db_path,
        observed_at="2026-07-25T11:55:00Z",
    )

    assert result["observation_ordering"] == (
        "APPEND_ONLY_EVENT_TIME_CAN_BE_OUT_OF_ORDER"
    )
    assert result["writes"]["observation"] == {"inserted": 1, "skipped": 0}
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute(
            "SELECT observed_at FROM prototype_schedule_observation "
            "ORDER BY observed_at"
        ).fetchall() == [
            ("2026-07-25T11:55:00Z",),
            (OBSERVED_AT,),
        ]
    finally:
        conn.close()


def test_later_content_conflict_rolls_back_new_observation_and_all_tables(
    tmp_path,
):
    db_path = tmp_path / "later-conflict.db"
    design.run_prototype(db_path, observed_at=OBSERVED_AT)
    document = _document()
    target = next(
        row for row in document["fixtures"] if int(row["id"]) == 4685727
    )
    target["status"]["cancelled"] = False

    conn = sqlite3.connect(db_path)
    try:
        before_business = _business_rows(conn)
        before_observations = conn.execute(
            "SELECT * FROM prototype_schedule_observation ORDER BY observed_at"
        ).fetchall()
        with pytest.raises(design.PrototypeConflictError):
            design.ingest_document(
                conn,
                document,
                observed_at="2026-07-25T12:05:00Z",
            )
        assert _business_rows(conn) == before_business
        assert conn.execute(
            "SELECT * FROM prototype_schedule_observation ORDER BY observed_at"
        ).fetchall() == before_observations
    finally:
        conn.close()


def test_rest_input_set_hash_changes_without_overwriting_prior_features(
    tmp_path,
):
    document = _document()
    first = design.parse_canonical_fixture(document, observed_at=OBSERVED_AT)
    changed = copy.deepcopy(document)
    target = next(
        row for row in changed["fixtures"] if int(row["id"]) == 4685746
    )
    target["status"]["utcTime"] = "2025-06-23T20:00:00Z"
    later = design.parse_canonical_fixture(
        changed,
        observed_at="2026-07-25T12:05:00Z",
    )

    first_features = _manchester_city_features(first)
    later_features = _manchester_city_features(later)
    assert _lineage_hashes(first_features[4685744]) == _lineage_hashes(
        later_features[4685744]
    )
    for match_id in MAN_CITY_MATCH_IDS[1:]:
        assert _lineage_hashes(first_features[match_id]) != _lineage_hashes(
            later_features[match_id]
        )

    db_path = tmp_path / "rest-input-conflict.db"
    design.run_prototype(db_path, observed_at=OBSERVED_AT)
    conn = sqlite3.connect(db_path)
    try:
        before = _business_rows(conn)
        with pytest.raises(design.PrototypeConflictError):
            design.ingest_document(
                conn,
                changed,
                observed_at="2026-07-25T12:05:00Z",
            )
        assert _business_rows(conn) == before
    finally:
        conn.close()


@pytest.mark.parametrize("mutation", ["finished", "cancelled"])
def test_second_match_status_mutation_never_changes_earlier_lineage(
    mutation,
):
    document = _document()
    original = design.parse_canonical_fixture(
        document,
        observed_at=OBSERVED_AT,
    )
    changed = copy.deepcopy(document)
    target = next(
        row for row in changed["fixtures"] if int(row["id"]) == 4685746
    )
    target["status"]["finished"] = False
    target["status"]["started"] = False
    if mutation == "cancelled":
        target["status"]["cancelled"] = True
        target["status"]["short"] = "Cancelled"
    else:
        target["status"]["short"] = "NS"

    regenerated = design.parse_canonical_fixture(
        changed,
        observed_at="2026-07-25T12:05:00Z",
    )
    original_features = _manchester_city_features(original)
    regenerated_features = _manchester_city_features(regenerated)

    assert _lineage_hashes(
        original_features[4685744]
    ) == _lineage_hashes(regenerated_features[4685744])
    assert 4685746 not in regenerated_features
    for match_id in (4685748, 4685772):
        assert _lineage_hashes(
            original_features[match_id]
        ) != _lineage_hashes(regenerated_features[match_id])


@pytest.mark.parametrize(
    ("competition_class", "registry_verified", "expected"),
    [
        ("league", True, True),
        ("domestic_cup", True, True),
        ("continental", True, True),
        ("super_cup", True, True),
        ("international_club", True, True),
        ("friendly", True, False),
        ("unknown", True, False),
        ("other", True, False),
        ("league", False, False),
    ],
)
def test_competitive_class_policy_requires_verified_registry(
    competition_class,
    registry_verified,
    expected,
):
    assert design.competition_is_competitive(
        competition_class,
        registry_verified=registry_verified,
    ) is expected
    eligible, _ = design.observed_load_eligibility(
        competition_class=competition_class,
        registry_verified=registry_verified,
        finished=True,
        cancelled=False,
        kickoff_precision="exact",
        kickoff_at_utc="2025-01-01T00:00:00Z",
    )
    assert eligible is expected
