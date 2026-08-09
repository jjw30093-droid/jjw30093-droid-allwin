"""Offline proof for production schedule-state schema v1."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sqlite3
import traceback
from pathlib import Path

import pytest

from backend.db import migrate
from backend.schedules import state as schedule


T0 = "2026-07-26T00:00:00Z"
T1 = "2026-07-26T00:05:00Z"
T2 = "2026-07-26T00:10:00Z"
FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "fotmob"
    / "cwc_2025_competition_schedule_canonical.json"
)


def _hash(value) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _state(**changes) -> dict:
    value = {
        "kickoff_at_utc": "2026-08-01T12:00:00Z",
        "kickoff_precision": "exact",
        "status": "NS",
        "finished": False,
        "cancelled": False,
        "home_team_id": 101,
        "home_team_name": "Home",
        "away_team_id": 202,
        "away_team_name": "Away",
        "competition_id": "test-competition",
        "season_label": "2026",
        "round_label": "1",
        "stage_label": "group",
        "competition_class": "international_club",
        "competition_verified": True,
    }
    value.update(changes)
    return value


def _open(db_path: Path) -> sqlite3.Connection:
    schedule.apply_schedule_state_schema_v1(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA recursive_triggers = ON")
    return conn


def _record(
    conn: sqlite3.Connection,
    *,
    match_id: int | str = 9001,
    state: dict | None = None,
    observed_at: str = T0,
    ingested_at: str | None = None,
    payload_label: str | None = None,
    canonical_match_id: int | None = None,
    source_updated_at: str | None = None,
) -> dict:
    return schedule.record_match_state(
        conn,
        provider="synthetic",
        provider_match_id=match_id,
        canonical_match_id=canonical_match_id,
        identity_created_at=T0,
        identity_provenance="offline-test",
        state=state or _state(),
        source_updated_at=source_updated_at,
        snapshot_provenance="offline-test-state",
        source="offline-fixture",
        competition_scope="test-competition",
        season_scope="2026",
        observed_at=observed_at,
        poll_run_id=f"poll-{observed_at}",
        payload_hash=_hash(payload_label or {"match": match_id, "at": observed_at}),
        ingested_at=ingested_at or observed_at,
    )


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "schedule_match_identity",
            "schedule_match_state_snapshot",
            "schedule_match_observation",
            "schedule_rest_feature",
            "schedule_rest_feature_input",
        )
    }


def _staged_core_migrations(tmp_path: Path, names: tuple[str, ...]) -> Path:
    directory = tmp_path / "core-migrations"
    directory.mkdir()
    source = migrate.MIGRATIONS_ROOT / "core"
    for name in names:
        shutil.copyfile(source / name, directory / name)
    return directory


def test_fresh_migration_exact_schema_and_rerun_are_idempotent(tmp_path):
    db_path = tmp_path / "fresh.db"
    assert schedule.apply_schedule_state_schema_v1(db_path) == 3
    assert schedule.apply_schedule_state_schema_v1(db_path) == 0
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA recursive_triggers = ON")
        schedule.assert_schedule_state_schema(conn)
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall() == [
            (1, "0001_dim_match_kickoff.sql"),
            (2, "0002_kickoff_provenance.sql"),
            (3, "0003_schedule_state_v1.sql"),
        ]
    finally:
        conn.close()


def test_legacy_core_upgrade_preserves_dim_match_columns_and_rows(tmp_path):
    staged = _staged_core_migrations(
        tmp_path,
        ("0001_dim_match_kickoff.sql", "0002_kickoff_provenance.sql"),
    )
    db_path = tmp_path / "legacy.db"
    migrate.apply_all("core", db_file=db_path, migrations_dir=staged, quiet=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO dim_match "
        "(Match_ID, Season, League_ID, Date, status, kickoff_precision) "
        "VALUES (7001, '2026', 47, '2026-08-01', 'NotStarted', 'date_only')"
    )
    before_columns = conn.execute("PRAGMA table_info(dim_match)").fetchall()
    conn.commit()
    conn.close()

    assert schedule.apply_schedule_state_schema_v1(db_path) == 1
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("PRAGMA table_info(dim_match)").fetchall() == before_columns
        assert conn.execute(
            "SELECT Match_ID, Season, League_ID, Date, status, kickoff_precision "
            "FROM dim_match"
        ).fetchall() == [
            (7001, "2026", 47, "2026-08-01", "NotStarted", "date_only")
        ]
    finally:
        conn.close()


def test_current_real_v1_shape_upgrades_through_0002_and_0003_in_tmp(tmp_path):
    staged = _staged_core_migrations(
        tmp_path,
        ("0001_dim_match_kickoff.sql",),
    )
    db_path = tmp_path / "real-v1-shape.db"
    migrate.apply_all("core", db_file=db_path, migrations_dir=staged, quiet=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO dim_match "
        "(Match_ID, Season, League_ID, Date, status) "
        "VALUES (7002, '2026', 47, '2026-08-02', 'NotStarted')"
    )
    conn.commit()
    conn.close()

    assert schedule.apply_schedule_state_schema_v1(db_path) == 2
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA recursive_triggers = ON")
        schedule.assert_schedule_state_schema(conn)
        assert conn.execute(
            "SELECT kickoff_precision, kickoff_source FROM dim_match "
            "WHERE Match_ID=7002"
        ).fetchone() == ("date_only", None)
        assert conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,)]
    finally:
        conn.close()


def test_partial_same_name_table_fails_closed_without_masking(tmp_path):
    staged = _staged_core_migrations(
        tmp_path,
        ("0001_dim_match_kickoff.sql", "0002_kickoff_provenance.sql"),
    )
    db_path = tmp_path / "partial.db"
    migrate.apply_all("core", db_file=db_path, migrations_dir=staged, quiet=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE schedule_match_identity "
        "(id INTEGER PRIMARY KEY, marker TEXT)"
    )
    conn.execute(
        "INSERT INTO schedule_match_identity VALUES (1, 'keep-existing')"
    )
    conn.commit()
    conn.close()

    with pytest.raises(
        schedule.ScheduleStateMigrationError,
        match="schedule state schema migration failed",
    ):
        schedule.apply_schedule_state_schema_v1(db_path)
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute(
            "SELECT marker FROM schedule_match_identity"
        ).fetchone()[0] == "keep-existing"
        assert conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,)]
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE name='schedule_match_state_snapshot'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_wrong_same_name_view_fails_closed(tmp_path):
    staged = _staged_core_migrations(
        tmp_path,
        ("0001_dim_match_kickoff.sql", "0002_kickoff_provenance.sql"),
    )
    db_path = tmp_path / "wrong-object.db"
    migrate.apply_all("core", db_file=db_path, migrations_dir=staged, quiet=True)
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE VIEW schedule_match_identity AS SELECT 1 AS id")
    conn.commit()
    conn.close()
    with pytest.raises(schedule.ScheduleStateMigrationError):
        schedule.apply_schedule_state_schema_v1(db_path)
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute(
            "SELECT type FROM sqlite_master WHERE name='schedule_match_identity'"
        ).fetchone()[0] == "view"
        assert conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,)]
    finally:
        conn.close()


def test_mid_migration_failure_rolls_back_all_v1_objects(tmp_path):
    staged = _staged_core_migrations(
        tmp_path,
        (
            "0001_dim_match_kickoff.sql",
            "0002_kickoff_provenance.sql",
            "0003_schedule_state_v1.sql",
        ),
    )
    migration = staged / "0003_schedule_state_v1.sql"
    migration.write_text(
        migration.read_text(encoding="utf-8")
        + "\nINSERT INTO missing_mid_failure_table VALUES (1);\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "mid-failure.db"
    with pytest.raises(schedule.ScheduleStateMigrationError):
        schedule.apply_schedule_state_schema_v1(
            db_path,
            migrations_dir=staged,
        )
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,)]
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE name GLOB 'schedule_*' "
            "OR name GLOB 'idx_schedule_*' "
            "OR name GLOB 'trg_schedule_*'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_schedule_schema_wrapper_rejects_missing_0002_before_new_db(tmp_path):
    staged = _staged_core_migrations(
        tmp_path,
        ("0001_dim_match_kickoff.sql", "0003_schedule_state_v1.sql"),
    )
    db_path = tmp_path / "missing-0002.db"
    with pytest.raises(
        schedule.ScheduleStateMigrationError,
        match="schedule state schema migration failed",
    ):
        schedule.apply_schedule_state_schema_v1(
            db_path,
            migrations_dir=staged,
        )
    assert not db_path.exists()


def test_schema_drift_extra_object_is_rejected(tmp_path):
    db_path = tmp_path / "drift.db"
    conn = _open(db_path)
    try:
        conn.execute("CREATE TABLE schedule_unreviewed_extra (id INTEGER)")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(
        schedule.ScheduleStateSchemaError,
        match="schedule state schema validation failed",
    ):
        schedule.apply_schedule_state_schema_v1(db_path)


def test_weakened_constraint_catalog_is_rejected(tmp_path):
    db_path = tmp_path / "weakened.db"
    conn = _open(db_path)
    try:
        conn.execute("PRAGMA writable_schema = ON")
        conn.execute(
            "UPDATE sqlite_master "
            "SET sql=replace(sql, "
            "'CHECK (finished IN (0,1))', '') "
            "WHERE type='table' AND name='schedule_match_state_snapshot'"
        )
        conn.execute("PRAGMA writable_schema = OFF")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(schedule.ScheduleStateSchemaError):
        schedule.apply_schedule_state_schema_v1(db_path)


def test_migration_failure_message_does_not_echo_path_or_payload(tmp_path):
    marker = "SYNTH_MIGRATION_SECRET_9F41"
    staged = _staged_core_migrations(
        tmp_path,
        (
            "0001_dim_match_kickoff.sql",
            "0002_kickoff_provenance.sql",
            "0003_schedule_state_v1.sql",
        ),
    )
    migration = staged / "0003_schedule_state_v1.sql"
    migration.write_text(
        migration.read_text(encoding="utf-8")
        + f"\nINSERT INTO missing_{marker} VALUES (1);\n",
        encoding="utf-8",
    )
    with pytest.raises(schedule.ScheduleStateMigrationError) as caught:
        schedule.apply_schedule_state_schema_v1(
            tmp_path / f"{marker}.db",
            migrations_dir=staged,
        )
    rendered = "".join(
        traceback.format_exception(
            type(caught.value),
            caught.value,
            caught.value.__traceback__,
        )
    )
    assert str(caught.value) == "schedule state schema migration failed"
    assert marker not in rendered
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_foreign_key_guards_reject_cross_identity_observation(tmp_path):
    conn = _open(tmp_path / "foreign-key.db")
    try:
        first = _record(conn, match_id=1)
        second = _record(conn, match_id=2)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO schedule_match_observation "
                "(observation_event_id, match_identity_id, snapshot_id, "
                " observed_at) VALUES (?, ?, ?, ?)",
                (
                    first["observation_event_id"],
                    first["identity_id"],
                    second["snapshot_id"],
                    schedule._utc(T0),
                ),
            )
    finally:
        conn.close()


def test_canonical_match_id_reuses_existing_dim_match_without_rebinding(tmp_path):
    conn = _open(tmp_path / "canonical.db")
    try:
        conn.execute("INSERT INTO dim_match (Match_ID) VALUES (8801)")
        conn.commit()
        first = _record(conn, match_id=8801, canonical_match_id=8801)
        assert conn.execute(
            "SELECT canonical_match_id FROM schedule_match_identity WHERE id=?",
            (first["identity_id"],),
        ).fetchone()[0] == 8801
        with pytest.raises(schedule.ScheduleStateConflictError):
            _record(conn, match_id=8801, canonical_match_id=None)
        assert _counts(conn)["schedule_match_identity"] == 1
    finally:
        conn.close()


def test_record_match_identity_supports_identity_only_backfill(tmp_path):
    conn = _open(tmp_path / "identity-only.db")
    try:
        conn.execute("INSERT INTO dim_match (Match_ID) VALUES (8801)")
        conn.commit()

        first = schedule.record_match_identity(
            conn,
            provider="fotmob",
            provider_match_id=8801,
            canonical_match_id=8801,
            identity_created_at=T0,
            identity_provenance=(
                "legacy_dim_match:repository_verified_fotmob_match_id"
            ),
        )
        second = schedule.record_match_identity(
            conn,
            provider=" FOTMOB ",
            provider_match_id="0008801",
            canonical_match_id=8801,
            identity_created_at=T1,
            identity_provenance=(
                "legacy_dim_match:repository_verified_fotmob_match_id"
            ),
        )
        earlier = schedule.record_match_identity(
            conn,
            provider="FotMob",
            provider_match_id=" 8801 ",
            canonical_match_id=8801,
            identity_created_at="2026-07-25T00:00:00Z",
            identity_provenance=(
                "legacy_dim_match:repository_verified_fotmob_match_id"
            ),
        )

        assert first["inserted"] is True
        assert second == {
            "identity_id": first["identity_id"],
            "inserted": False,
        }
        assert earlier == second
        assert tuple(
            conn.execute(
                "SELECT provider, provider_match_id, created_at "
                "FROM schedule_match_identity"
            ).fetchone()
        ) == (
            "fotmob",
            "8801",
            schedule._utc(T0),
        )
        assert _counts(conn) == {
            "schedule_match_identity": 1,
            "schedule_match_state_snapshot": 0,
            "schedule_match_observation": 0,
            "schedule_rest_feature": 0,
            "schedule_rest_feature_input": 0,
        }
        with pytest.raises(schedule.ScheduleStateConflictError):
            schedule.record_match_identity(
                conn,
                provider="fotmob",
                provider_match_id=8801,
                canonical_match_id=None,
                identity_created_at=T1,
                identity_provenance=(
                    "legacy_dim_match:repository_verified_fotmob_match_id"
                ),
            )
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("fotmob", "fotmob"),
        (" FOTMOB ", "fotmob"),
        ("FotMob", "fotmob"),
        ("provider_1-test", "provider_1-test"),
    ],
)
def test_provider_normalization_contract(raw, expected):
    assert schedule.normalize_provider(raw) == expected


@pytest.mark.parametrize(
    "invalid",
    [
        "",
        " ",
        None,
        1,
        "bad/provider",
        "bad.provider",
        "bad provider",
        "provider\nname",
        "éxample",
        "a" * 33,
    ],
)
def test_provider_normalization_rejects_invalid_values(invalid):
    with pytest.raises(schedule.ScheduleStateDataError):
        schedule.normalize_provider(invalid)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1, "1"),
        ("1", "1"),
        (" 001 ", "1"),
        ("Match.A_1:2026-7", "Match.A_1:2026-7"),
    ],
)
def test_provider_match_id_normalization_contract(raw, expected):
    assert schedule.normalize_provider_match_id(raw) == expected


@pytest.mark.parametrize(
    "invalid",
    [
        -1,
        0,
        True,
        False,
        None,
        [],
        {},
        1.5,
        "",
        " ",
        "-1",
        "0",
        "/tmp/id",
        "id value",
        "比赛1",
        "a" * 129,
    ],
)
def test_provider_match_id_rejects_invalid_values(invalid):
    with pytest.raises(schedule.ScheduleStateDataError):
        schedule.normalize_provider_match_id(invalid)


@pytest.mark.parametrize(
    ("provider", "provider_match_id"),
    [
        ("FOTMOB", "1"),
        ("", "1"),
        ("bad/provider", "1"),
        ("fotmob", ""),
        ("fotmob", "0"),
        ("fotmob", "-1"),
        ("fotmob", "[]"),
        ("fotmob", "{}"),
        ("fotmob", "bad/id"),
    ],
)
def test_direct_sql_identity_constraints_reject_noncanonical_values(
    tmp_path,
    provider,
    provider_match_id,
):
    conn = _open(tmp_path / "identity-direct-constraints.db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO schedule_match_identity "
                "(provider, provider_match_id, created_at, identity_provenance) "
                "VALUES (?, ?, ?, 'direct-test')",
                (provider, provider_match_id, schedule._utc(T0)),
            )
    finally:
        conn.close()


def test_direct_sql_identity_constraints_accept_canonical_alphanumeric_id(
    tmp_path,
):
    conn = _open(tmp_path / "identity-direct-valid.db")
    try:
        conn.execute(
            "INSERT INTO schedule_match_identity "
            "(provider, provider_match_id, created_at, identity_provenance) "
            "VALUES ('opta_feed', 'Match.A_1:2026-7', ?, 'direct-test')",
            (schedule._utc(T0),),
        )
        assert tuple(
            conn.execute(
                "SELECT provider, provider_match_id "
                "FROM schedule_match_identity"
            ).fetchone()
        ) == ("opta_feed", "Match.A_1:2026-7")
    finally:
        conn.close()


@pytest.mark.parametrize(
    "changed",
    [
        {"status": "FT", "finished": True},
        {"kickoff_at_utc": "2026-08-01T15:00:00Z"},
        {"kickoff_at_utc": "2026-08-01T09:00:00Z"},
        {"status": "Postponed"},
        {"status": "Cancelled", "cancelled": True},
        {"home_team_id": 303, "home_team_name": "Concrete Home"},
        {"round_label": "2"},
        {"stage_label": "knockout"},
    ],
    ids=(
        "ns-to-ft",
        "rescheduled-later",
        "rescheduled-earlier",
        "postponed",
        "cancelled",
        "tbd-to-concrete",
        "round-correction",
        "stage-correction",
    ),
)
def test_state_change_appends_snapshot_and_preserves_as_of(tmp_path, changed):
    conn = _open(tmp_path / "transition.db")
    try:
        initial = _state(
            home_team_id=None,
            home_team_name="TBD",
        ) if "home_team_id" in changed else _state()
        first = _record(conn, state=initial, observed_at=T0)
        updated = copy.deepcopy(initial)
        updated.update(changed)
        second = _record(conn, state=updated, observed_at=T1)
        assert second["snapshot_id"] != first["snapshot_id"]
        assert _counts(conn) == {
            "schedule_match_identity": 1,
            "schedule_match_state_snapshot": 2,
            "schedule_match_observation": 2,
            "schedule_rest_feature": 0,
            "schedule_rest_feature_input": 0,
        }
        assert schedule.get_match_state_as_of(
            conn, first["identity_id"], T0
        )["snapshot_id"] == first["snapshot_id"]
        assert schedule.get_current_match_state(
            conn, first["identity_id"]
        )["snapshot_id"] == second["snapshot_id"]
    finally:
        conn.close()


def test_cancelled_to_scheduled_reuses_prior_business_snapshot(tmp_path):
    conn = _open(tmp_path / "cancel-recovery.db")
    try:
        scheduled = _state()
        first = _record(conn, state=scheduled, observed_at=T0)
        _record(
            conn,
            state=_state(status="Cancelled", cancelled=True),
            observed_at=T1,
        )
        recovered = _record(conn, state=scheduled, observed_at=T2)
        assert recovered["snapshot_id"] == first["snapshot_id"]
        assert recovered["snapshot_inserted"] is False
        assert _counts(conn)["schedule_match_state_snapshot"] == 2
        assert _counts(conn)["schedule_match_observation"] == 3
        assert schedule.get_current_match_state(
            conn, first["identity_id"]
        )["status"] == "NS"
    finally:
        conn.close()


def test_same_business_state_later_and_earlier_append_only_observations(tmp_path):
    conn = _open(tmp_path / "observation-order.db")
    try:
        first = _record(conn, observed_at=T1, payload_label="same-state")
        later = _record(conn, observed_at=T2, payload_label="same-state")
        earlier = _record(conn, observed_at=T0, payload_label="same-state")
        assert first["snapshot_id"] == later["snapshot_id"] == earlier["snapshot_id"]
        assert _counts(conn)["schedule_match_state_snapshot"] == 1
        assert _counts(conn)["schedule_match_observation"] == 3
        current = schedule.get_current_match_state(conn, first["identity_id"])
        assert current["observed_at"] == schedule._utc(T2)
        assert current["snapshot_id"] == first["snapshot_id"]
    finally:
        conn.close()


def test_earlier_different_state_does_not_regress_current_projection(tmp_path):
    conn = _open(tmp_path / "out-of-order-state.db")
    try:
        latest = _record(
            conn,
            state=_state(status="FT", finished=True),
            observed_at=T2,
            ingested_at=T0,
        )
        earlier = _record(
            conn,
            state=_state(),
            observed_at=T0,
            ingested_at=T1,
        )
        assert latest["snapshot_id"] != earlier["snapshot_id"]
        current = schedule.get_current_match_state(conn, latest["identity_id"])
        assert current["snapshot_id"] == latest["snapshot_id"]
        assert current["status"] == "FT"
        as_of = schedule.get_match_state_as_of(
            conn,
            latest["identity_id"],
            T0,
        )
        assert as_of["snapshot_id"] == earlier["snapshot_id"]
        assert as_of["status"] == "NS"
    finally:
        conn.close()


def test_source_updated_time_change_does_not_duplicate_business_state(tmp_path):
    conn = _open(tmp_path / "source-update.db")
    try:
        first = _record(
            conn,
            observed_at=T0,
            payload_label="same",
            source_updated_at="2026-07-25T23:00:00Z",
        )
        second = _record(
            conn,
            observed_at=T1,
            payload_label="same",
            source_updated_at="2026-07-25T23:30:00Z",
        )
        assert second["snapshot_id"] == first["snapshot_id"]
        assert _counts(conn)["schedule_match_state_snapshot"] == 1
        assert _counts(conn)["schedule_match_observation"] == 2
    finally:
        conn.close()


def test_exact_repeated_poll_is_fully_idempotent(tmp_path):
    conn = _open(tmp_path / "repeated-poll.db")
    try:
        first = _record(conn, observed_at=T0)
        second = _record(conn, observed_at=T0)
        assert second | {
            "identity_inserted": first["identity_inserted"],
            "snapshot_inserted": first["snapshot_inserted"],
            "observation_inserted": first["observation_inserted"],
        } == first
        assert second["identity_inserted"] is False
        assert second["snapshot_inserted"] is False
        assert second["observation_inserted"] is False
        assert _counts(conn) == {
            "schedule_match_identity": 1,
            "schedule_match_state_snapshot": 1,
            "schedule_match_observation": 1,
            "schedule_rest_feature": 0,
            "schedule_rest_feature_input": 0,
        }
    finally:
        conn.close()


def test_same_event_time_different_business_state_rolls_back(tmp_path):
    conn = _open(tmp_path / "same-time-conflict.db")
    try:
        first = _record(conn, observed_at=T0)
        before = _counts(conn)
        with pytest.raises(
            schedule.ScheduleStateConflictError,
            match="schedule state observation conflict",
        ):
            _record(
                conn,
                state=_state(status="FT", finished=True),
                observed_at=T0,
            )
        assert _counts(conn) == before
        assert schedule.get_current_match_state(
            conn, first["identity_id"]
        )["status"] == "NS"
    finally:
        conn.close()


def test_forced_transaction_failure_leaves_no_partial_rows(tmp_path):
    conn = _open(tmp_path / "transaction-failure.db")
    try:
        conn.execute(
            "CREATE TRIGGER fail_test_observation "
            "BEFORE INSERT ON schedule_match_observation "
            "BEGIN SELECT RAISE(ABORT, 'forced test failure'); END"
        )
        with pytest.raises(schedule.ScheduleStateConflictError) as caught:
            _record(conn, match_id=9999)
        assert str(caught.value) == "schedule state write conflict"
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert caught.value.__suppress_context__ is True
        assert _counts(conn) == {
            "schedule_match_identity": 0,
            "schedule_match_state_snapshot": 0,
            "schedule_match_observation": 0,
            "schedule_rest_feature": 0,
            "schedule_rest_feature_input": 0,
        }
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("table", "seed"),
    [
        (
            "schedule_match_identity",
            "UPDATE schedule_match_identity SET provider='changed' WHERE id=1",
        ),
        (
            "schedule_match_state_snapshot",
            "UPDATE schedule_match_state_snapshot SET status='changed' WHERE id=1",
        ),
        (
            "schedule_observation_event",
            "UPDATE schedule_observation_event SET source='changed' WHERE id=1",
        ),
        (
            "schedule_match_observation",
            "UPDATE schedule_match_observation SET snapshot_id=snapshot_id WHERE id=1",
        ),
    ],
)
def test_identity_state_and_observation_history_reject_update_delete(
    tmp_path,
    table,
    seed,
):
    conn = _open(tmp_path / "append-only.db")
    try:
        _record(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(seed)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"DELETE FROM {table}")
    finally:
        conn.close()


def test_lineage_set_input_and_final_feature_reject_update_delete(tmp_path):
    conn = _open(tmp_path / "lineage-append-only.db")
    try:
        state_row = _record(conn)
        schedule.record_rest_feature(
            conn,
            team_id=101,
            target_snapshot_id=state_row["snapshot_id"],
            feature_definition=schedule.REST_FEATURE_DEFINITION,
            feature_version=schedule.REST_FEATURE_VERSION,
            as_of_observed_at=T0,
            input_snapshot_ids=(state_row["snapshot_id"],),
            feature_value={"gap": None},
            computation_status="computed",
            provenance=schedule.REST_FEATURE_PROVENANCE,
            computed_at=T0,
        )
        for table, update in (
            (
                "schedule_rest_lineage_set",
                "UPDATE schedule_rest_lineage_set "
                "SET feature_definition=feature_definition",
            ),
            (
                "schedule_rest_lineage_input",
                "UPDATE schedule_rest_lineage_input "
                "SET input_ordinal=input_ordinal",
            ),
            (
                "schedule_rest_feature",
                "UPDATE schedule_rest_feature "
                "SET computation_status=computation_status",
            ),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(update)
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(f"DELETE FROM {table}")
    finally:
        conn.close()


def _cwc_document() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _cwc_state(row: dict) -> dict:
    status = row["status"]
    return {
        "kickoff_at_utc": status["utcTime"],
        "kickoff_precision": "exact",
        "status": status["short"],
        "finished": status["finished"],
        "cancelled": status["cancelled"],
        "home_team_id": row["home"]["id"],
        "home_team_name": row["home"]["name"],
        "away_team_id": row["away"]["id"],
        "away_team_name": row["away"]["name"],
        "competition_id": "78",
        "season_label": "2025",
        "round_label": row["round"],
        "stage_label": None,
        "competition_class": "international_club",
        "competition_verified": True,
    }


def _load_cwc(
    conn: sqlite3.Connection,
    *,
    observed_at: str = T0,
    document: dict | None = None,
) -> dict[int, dict]:
    rows = (document or _cwc_document())["fixtures"]
    for row in rows:
        conn.execute(
            "INSERT OR IGNORE INTO dim_match (Match_ID) VALUES (?)",
            (row["id"],),
        )
    conn.commit()
    results = {}
    for row in rows:
        results[row["id"]] = schedule.record_match_state(
            conn,
            provider="fotmob",
            provider_match_id=row["id"],
            canonical_match_id=row["id"],
            identity_created_at=T0,
            identity_provenance="fotmob:canonical-match-id",
            state=_cwc_state(row),
            source_updated_at=None,
            snapshot_provenance="trimmed-validated-cwc-fixture",
            source="fotmob.league_matches.saved_response",
            competition_scope="78",
            season_scope="2025",
            observed_at=observed_at,
            poll_run_id=f"cwc-{observed_at}",
            payload_hash=_hash(row),
            ingested_at=observed_at,
        )
    return results


def _feature_values(
    conn: sqlite3.Connection,
    *,
    team_id: int,
) -> dict[int, list[dict]]:
    rows = conn.execute(
        """
        SELECT target.canonical_match_id, feature.*
        FROM schedule_rest_feature AS feature
        JOIN schedule_match_identity AS target
          ON target.id = feature.target_match_identity_id
        WHERE feature.team_id=?
        ORDER BY target.canonical_match_id, feature.id
        """,
        (team_id,),
    ).fetchall()
    out: dict[int, list[dict]] = {}
    for row in rows:
        value = dict(row)
        value["feature_value"] = json.loads(value["feature_value_json"])
        out.setdefault(int(row["canonical_match_id"]), []).append(value)
    return out


def test_cwc_canonical_counts_current_projection_and_rest_lineage(tmp_path):
    conn = _open(tmp_path / "cwc.db")
    try:
        _load_cwc(conn)
        result = schedule.build_observed_rest_features_as_of(
            conn,
            as_of_observed_at=T0,
            computed_at=T0,
        )
        assert result == {"inserted": 126, "skipped": 0}
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_match_identity"
        ).fetchone()[0] == 66
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_match_state_snapshot"
        ).fetchone()[0] == 66
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_match_observation"
        ).fetchone()[0] == 66
        assert conn.execute(
            "SELECT COUNT(*) FROM current_schedule_match_state"
        ).fetchone()[0] == 66
        assert conn.execute(
            "SELECT COUNT(*) FROM current_schedule_match_state "
            "WHERE cancelled=1"
        ).fetchone()[0] == 3
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_rest_feature"
        ).fetchone()[0] == 126

        city = _feature_values(conn, team_id=8456)
        assert list(city) == [4685744, 4685746, 4685748, 4685772]
        assert [
            city[match_id][0]["feature_value"]["kickoff_gap_hours"]
            for match_id in city
        ] == [None, 105.0, 90.0, 102.0]
        assert conn.execute(
            "SELECT status FROM current_schedule_match_state "
            "WHERE canonical_match_id=4685772"
        ).fetchone()[0] == "AET"
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_rest_feature_input AS input "
            "JOIN schedule_match_state_snapshot AS snapshot "
            "ON snapshot.id=input.input_snapshot_id "
            "WHERE snapshot.cancelled=1"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_cwc_later_same_content_adds_only_observations_and_reuses_features(
    tmp_path,
):
    conn = _open(tmp_path / "cwc-repeat.db")
    try:
        _load_cwc(conn, observed_at=T0)
        schedule.build_observed_rest_features_as_of(
            conn,
            as_of_observed_at=T0,
            computed_at=T0,
        )
        original_hashes = conn.execute(
            "SELECT id, input_set_hash, feature_payload_hash "
            "FROM schedule_rest_feature ORDER BY id"
        ).fetchall()
        _load_cwc(conn, observed_at=T1)
        repeat = schedule.build_observed_rest_features_as_of(
            conn,
            as_of_observed_at=T1,
            computed_at=T1,
        )
        assert repeat == {"inserted": 0, "skipped": 126}
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_match_state_snapshot"
        ).fetchone()[0] == 66
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_match_observation"
        ).fetchone()[0] == 132
        assert conn.execute(
            "SELECT id, input_set_hash, feature_payload_hash "
            "FROM schedule_rest_feature ORDER BY id"
        ).fetchall() == original_hashes
    finally:
        conn.close()


def test_cwc_final_change_affects_only_final_city_lineage(tmp_path):
    conn = _open(tmp_path / "cwc-final-change.db")
    try:
        _load_cwc(conn)
        schedule.build_observed_rest_features_as_of(
            conn,
            as_of_observed_at=T0,
            computed_at=T0,
        )
        before = _feature_values(conn, team_id=8456)
        document = _cwc_document()
        changed = next(row for row in document["fixtures"] if row["id"] == 4685772)
        changed["status"]["utcTime"] = "2025-07-01T02:00:00Z"
        schedule.record_match_state(
            conn,
            provider="fotmob",
            provider_match_id=changed["id"],
            canonical_match_id=changed["id"],
            identity_created_at=T0,
            identity_provenance="fotmob:canonical-match-id",
            state=_cwc_state(changed),
            source_updated_at=None,
            snapshot_provenance="trimmed-validated-cwc-fixture",
            source="fotmob.league_matches.saved_response",
            competition_scope="78",
            season_scope="2025",
            observed_at=T1,
            poll_run_id="cwc-final-change",
            payload_hash=_hash(changed),
            ingested_at=T1,
        )
        schedule.build_observed_rest_features_as_of(
            conn,
            as_of_observed_at=T1,
            computed_at=T1,
        )
        after = _feature_values(conn, team_id=8456)
        for match_id in (4685744, 4685746, 4685748):
            assert len(after[match_id]) == 1
            assert (
                after[match_id][0]["input_set_hash"]
                == before[match_id][0]["input_set_hash"]
            )
        assert len(after[4685772]) == 2
        assert (
            after[4685772][0]["input_set_hash"]
            != after[4685772][1]["input_set_hash"]
        )
    finally:
        conn.close()


def test_cwc_second_change_affects_second_and_downstream_city_lineage(tmp_path):
    conn = _open(tmp_path / "cwc-second-change.db")
    try:
        _load_cwc(conn)
        schedule.build_observed_rest_features_as_of(
            conn,
            as_of_observed_at=T0,
            computed_at=T0,
        )
        document = _cwc_document()
        changed = next(row for row in document["fixtures"] if row["id"] == 4685746)
        changed["status"]["utcTime"] = "2025-06-23T20:00:00Z"
        schedule.record_match_state(
            conn,
            provider="fotmob",
            provider_match_id=changed["id"],
            canonical_match_id=changed["id"],
            identity_created_at=T0,
            identity_provenance="fotmob:canonical-match-id",
            state=_cwc_state(changed),
            source_updated_at=None,
            snapshot_provenance="trimmed-validated-cwc-fixture",
            source="fotmob.league_matches.saved_response",
            competition_scope="78",
            season_scope="2025",
            observed_at=T1,
            poll_run_id="cwc-second-change",
            payload_hash=_hash(changed),
            ingested_at=T1,
        )
        schedule.build_observed_rest_features_as_of(
            conn,
            as_of_observed_at=T1,
            computed_at=T1,
        )
        city = _feature_values(conn, team_id=8456)
        assert len(city[4685744]) == 1
        assert all(len(city[match_id]) == 2 for match_id in (4685746, 4685748, 4685772))
        assert all(
            city[match_id][0]["input_set_hash"]
            != city[match_id][1]["input_set_hash"]
            for match_id in (4685746, 4685748, 4685772)
        )
    finally:
        conn.close()


def test_feature_input_contract_rejects_future_match_in_early_lineage(tmp_path):
    conn = _open(tmp_path / "future-lineage.db")
    try:
        first = _record(
            conn,
            match_id=1,
            state=_state(kickoff_at_utc="2026-08-01T12:00:00Z"),
        )
        later = _record(
            conn,
            match_id=2,
            state=_state(kickoff_at_utc="2026-08-02T12:00:00Z"),
        )
        with pytest.raises(schedule.ScheduleStateDataError):
            schedule.record_rest_feature(
                conn,
                team_id=101,
                target_snapshot_id=first["snapshot_id"],
                feature_definition=schedule.REST_FEATURE_DEFINITION,
                feature_version=schedule.REST_FEATURE_VERSION,
                as_of_observed_at=T0,
                input_snapshot_ids=(
                    first["snapshot_id"],
                    later["snapshot_id"],
                ),
                feature_value={"gap": None},
                computation_status="computed",
                provenance=schedule.REST_FEATURE_PROVENANCE,
                computed_at=T0,
            )
        assert _counts(conn)["schedule_rest_feature"] == 0
    finally:
        conn.close()


def _insert_raw_lineage_set(
    conn: sqlite3.Connection,
    *,
    team_id: int,
    target: dict,
    input_count: int,
) -> int:
    cursor = conn.execute(
        "INSERT INTO schedule_rest_lineage_set "
        "(team_id, target_match_identity_id, target_snapshot_id, "
        " feature_definition, feature_version, as_of_observed_at, "
        " input_set_hash, expected_input_count) "
        "VALUES (?, ?, ?, 'raw-test', 'v1', ?, ?, ?)",
        (
            team_id,
            target["identity_id"],
            target["snapshot_id"],
            schedule._utc(T0),
            _hash("raw-input"),
            input_count,
        ),
    )
    return int(cursor.lastrowid)


def test_database_input_trigger_rejects_non_participating_team_with_tbd_null(
    tmp_path,
):
    conn = _open(tmp_path / "db-team-guard.db")
    try:
        target = _record(
            conn,
            state=_state(home_team_id=None, home_team_name="TBD"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_raw_lineage_set(
                conn,
                team_id=999,
                target=target,
                input_count=1,
            )
    finally:
        conn.close()


def test_database_input_trigger_rejects_future_snapshot_for_early_target(
    tmp_path,
):
    conn = _open(tmp_path / "db-future-guard.db")
    try:
        early = _record(
            conn,
            match_id=1,
            state=_state(kickoff_at_utc="2026-08-01T12:00:00Z"),
        )
        later = _record(
            conn,
            match_id=2,
            state=_state(kickoff_at_utc="2026-08-02T12:00:00Z"),
        )
        lineage_set_id = _insert_raw_lineage_set(
            conn,
            team_id=101,
            target=early,
            input_count=2,
        )
        conn.execute(
            "INSERT INTO schedule_rest_lineage_input "
            "(lineage_set_id, input_ordinal, input_match_identity_id, "
            " input_snapshot_id) VALUES (?, 0, ?, ?)",
            (lineage_set_id, early["identity_id"], early["snapshot_id"]),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO schedule_rest_lineage_input "
                "(lineage_set_id, input_ordinal, input_match_identity_id, "
                " input_snapshot_id) VALUES (?, 1, ?, ?)",
                (lineage_set_id, later["identity_id"], later["snapshot_id"]),
            )
    finally:
        conn.close()


def test_feature_and_input_history_reject_update_delete(tmp_path):
    conn = _open(tmp_path / "feature-append-only.db")
    try:
        state_row = _record(conn)
        schedule.record_rest_feature(
            conn,
            team_id=101,
            target_snapshot_id=state_row["snapshot_id"],
            feature_definition=schedule.REST_FEATURE_DEFINITION,
            feature_version=schedule.REST_FEATURE_VERSION,
            as_of_observed_at=T0,
            input_snapshot_ids=(state_row["snapshot_id"],),
            feature_value={"gap": None},
            computation_status="computed",
            provenance=schedule.REST_FEATURE_PROVENANCE,
            computed_at=T0,
        )
        for sql in (
            "UPDATE schedule_rest_feature SET computation_status='failed'",
            "DELETE FROM schedule_rest_feature",
            "UPDATE schedule_rest_feature_input SET input_ordinal=1",
            "DELETE FROM schedule_rest_feature_input",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql)
    finally:
        conn.close()


def test_feature_same_input_is_idempotent_across_computation_time(tmp_path):
    conn = _open(tmp_path / "feature-idempotent.db")
    try:
        state_row = _record(conn)
        first = schedule.record_rest_feature(
            conn,
            team_id=101,
            target_snapshot_id=state_row["snapshot_id"],
            feature_definition=schedule.REST_FEATURE_DEFINITION,
            feature_version=schedule.REST_FEATURE_VERSION,
            as_of_observed_at=T0,
            input_snapshot_ids=(state_row["snapshot_id"],),
            feature_value={"gap": None},
            computation_status="computed",
            provenance=schedule.REST_FEATURE_PROVENANCE,
            computed_at=T0,
        )
        second = schedule.record_rest_feature(
            conn,
            team_id=101,
            target_snapshot_id=state_row["snapshot_id"],
            feature_definition=schedule.REST_FEATURE_DEFINITION,
            feature_version=schedule.REST_FEATURE_VERSION,
            as_of_observed_at=T1,
            input_snapshot_ids=(state_row["snapshot_id"],),
            feature_value={"gap": None},
            computation_status="computed",
            provenance=schedule.REST_FEATURE_PROVENANCE,
            computed_at=T2,
        )
        assert first["feature_id"] == second["feature_id"]
        assert first["input_set_hash"] == second["input_set_hash"]
        assert second["inserted"] is False
        assert _counts(conn)["schedule_rest_feature"] == 1
    finally:
        conn.close()


def test_feature_build_input_finalize_failure_rolls_back_every_layer(tmp_path):
    conn = _open(tmp_path / "feature-finalize-rollback.db")
    try:
        first = _record(
            conn,
            match_id=1,
            state=_state(
                kickoff_at_utc="2026-08-01T12:00:00Z",
                status="FT",
                finished=True,
            ),
        )
        second = _record(
            conn,
            match_id=2,
            state=_state(
                kickoff_at_utc="2026-08-02T12:00:00Z",
                status="FT",
                finished=True,
            ),
        )
        conn.execute(
            "CREATE TRIGGER force_second_lineage_input_failure "
            "BEFORE INSERT ON schedule_rest_lineage_input "
            "WHEN NEW.input_ordinal=1 "
            "BEGIN SELECT RAISE(ABORT, 'forced lineage failure'); END"
        )
        conn.commit()

        with pytest.raises(schedule.ScheduleStateConflictError) as caught:
            schedule.record_rest_feature(
                conn,
                team_id=101,
                target_snapshot_id=second["snapshot_id"],
                feature_definition=schedule.REST_FEATURE_DEFINITION,
                feature_version=schedule.REST_FEATURE_VERSION,
                as_of_observed_at=T0,
                input_snapshot_ids=(
                    first["snapshot_id"],
                    second["snapshot_id"],
                ),
                feature_value={"gap": 24.0},
                computation_status="computed",
                provenance=schedule.REST_FEATURE_PROVENANCE,
                computed_at=T0,
            )
        assert str(caught.value) == "schedule rest feature write conflict"
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert caught.value.__suppress_context__ is True
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_rest_lineage_set"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_rest_lineage_input"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_rest_feature"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_schema_module_has_no_production_integration_references():
    repo = Path(__file__).resolve().parents[2]
    state_source = (repo / "backend" / "schedules" / "state.py").read_text(
        encoding="utf-8"
    )
    assert "analysis." not in state_source
    migration = (
        repo / "backend" / "migrations" / "core" / "0003_schedule_state_v1.sql"
    ).read_text(encoding="utf-8")
    assert "ALTER TABLE dim_match" not in migration
    assert "UPDATE dim_match" not in migration
    assert "dim_match_xref" not in migration
    assert "poll_state" not in migration

    integration_roots = (
        repo / "backend" / "worker",
        repo / "backend" / "api",
        repo / "deploy" / "systemd",
        repo / "frontend",
    )
    for root in integration_roots:
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in {".py", ".ts", ".tsx", ".service"}:
                text = path.read_text(encoding="utf-8", errors="ignore")
                assert "current_schedule_match_state" not in text
                assert "schedule_match_state_snapshot" not in text


# ── Independent direct-SQL closure gates ───────────────────────────────────

CANONICAL_T0 = "2026-07-26T00:00:00.000000Z"


def _raw_snapshot_values(
    conn: sqlite3.Connection,
    identity_id: int,
    *,
    kickoff: str,
    state_hash: str,
) -> tuple:
    del conn
    return (
        identity_id,
        state_hash,
        kickoff,
        "exact",
        "NS",
        0,
        0,
        101,
        "Home",
        202,
        "Away",
        "test-competition",
        "2026",
        "1",
        "group",
        "international_club",
        1,
        None,
        CANONICAL_T0,
        CANONICAL_T0,
        "direct-sql-test",
    )


RAW_SNAPSHOT_INSERT = """
    INSERT INTO schedule_match_state_snapshot (
      match_identity_id, state_content_hash, kickoff_at_utc,
      kickoff_precision, status, finished, cancelled,
      home_team_id, home_team_name, away_team_id, away_team_name,
      competition_id, season_label, round_label, stage_label,
      competition_class, competition_verified, source_updated_at,
      first_observed_at, ingested_at, provenance
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


@pytest.mark.parametrize(
    "invalid_timestamp",
    [
        "2026-08-01T12:00:00Z",
        "2026-08-01T12:00:00+00:00",
        "2026-08-01T13:00:00+01:00",
        "2026-08-01T12:00:00",
        "2026-08-01T12:00:00.123Z",
        "2026-08-01T12:00:00.000000Z ",
        "2026-02-30T12:00:00.000000Z",
    ],
    ids=(
        "z-without-fixed-microseconds",
        "explicit-offset",
        "non-utc-offset",
        "naive",
        "short-fraction",
        "trailing-space",
        "invalid-calendar-date",
    ),
)
def test_direct_sql_rejects_noncanonical_kickoff_timestamp(
    tmp_path,
    invalid_timestamp,
):
    conn = _open(tmp_path / "timestamp-direct.db")
    try:
        identity = _record(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                RAW_SNAPSHOT_INSERT,
                _raw_snapshot_values(
                    conn,
                    identity["identity_id"],
                    kickoff=invalid_timestamp,
                    state_hash=_hash(invalid_timestamp),
                ),
            )
    finally:
        conn.close()


def test_service_uses_fixed_width_utc_and_fractional_current_order_is_correct(
    tmp_path,
):
    conn = _open(tmp_path / "timestamp-order.db")
    try:
        first = _record(
            conn,
            match_id=9100,
            state=_state(status="NS"),
            observed_at="2026-07-26T00:00:00Z",
        )
        second = _record(
            conn,
            match_id=9100,
            state=_state(status="FT", finished=True),
            observed_at="2026-07-26T00:00:00.500000Z",
        )
        assert [
            row[0]
            for row in conn.execute(
                "SELECT observed_at FROM schedule_match_observation ORDER BY id"
            )
        ] == [
            "2026-07-26T00:00:00.000000Z",
            "2026-07-26T00:00:00.500000Z",
        ]
        current = schedule.get_current_match_state(conn, first["identity_id"])
        assert current["snapshot_id"] == second["snapshot_id"]
        assert current["status"] == "FT"
    finally:
        conn.close()


@pytest.mark.parametrize(
    "surface",
    [
        "identity-created",
        "snapshot-source-updated",
        "snapshot-first-observed",
        "snapshot-ingested",
        "event-observed",
        "event-ingested",
        "association-observed",
        "lineage-as-of",
    ],
)
def test_direct_sql_rejects_noncanonical_timestamp_on_every_order_surface(
    tmp_path,
    surface,
):
    conn = _open(tmp_path / f"timestamp-surface-{surface}.db")
    invalid = "2026-07-26T00:00:00Z"
    try:
        base = _record(conn)
        if surface == "identity-created":
            action = lambda: conn.execute(
                "INSERT INTO schedule_match_identity "
                "(provider, provider_match_id, created_at, identity_provenance) "
                "VALUES ('synthetic', 'new-id', ?, 'direct')",
                (invalid,),
            )
        elif surface.startswith("snapshot-"):
            values = list(
                _raw_snapshot_values(
                    conn,
                    base["identity_id"],
                    kickoff="2026-08-02T12:00:00.000000Z",
                    state_hash=_hash(surface),
                )
            )
            index = {
                "snapshot-source-updated": 17,
                "snapshot-first-observed": 18,
                "snapshot-ingested": 19,
            }[surface]
            values[index] = invalid
            action = lambda: conn.execute(RAW_SNAPSHOT_INSERT, tuple(values))
        elif surface.startswith("event-"):
            observed = invalid if surface == "event-observed" else CANONICAL_T0
            ingested = invalid if surface == "event-ingested" else CANONICAL_T0
            action = lambda: conn.execute(
                "INSERT INTO schedule_observation_event "
                "(provider, source, competition_scope, season_scope, "
                " observed_at, poll_run_id, payload_hash, ingested_at) "
                "VALUES ('synthetic', 'direct', 'c', 's', ?, 'run', ?, ?)",
                (observed, _hash(surface), ingested),
            )
        elif surface == "association-observed":
            action = lambda: conn.execute(
                "INSERT INTO schedule_match_observation "
                "(observation_event_id, match_identity_id, snapshot_id, "
                " observed_at) VALUES (?, ?, ?, ?)",
                (
                    base["observation_event_id"],
                    base["identity_id"],
                    base["snapshot_id"],
                    invalid,
                ),
            )
        else:
            action = lambda: conn.execute(
                "INSERT INTO schedule_rest_lineage_set "
                "(team_id, target_match_identity_id, target_snapshot_id, "
                " feature_definition, feature_version, as_of_observed_at, "
                " input_set_hash, expected_input_count) "
                "VALUES (101, ?, ?, 'gap', 'v1', ?, ?, 1)",
                (
                    base["identity_id"],
                    base["snapshot_id"],
                    invalid,
                    _hash(surface),
                ),
            )
        with pytest.raises(sqlite3.IntegrityError):
            action()
    finally:
        conn.close()


def test_direct_sql_rejects_noncanonical_final_feature_computed_at(tmp_path):
    conn = _open(tmp_path / "timestamp-final-feature.db")
    try:
        target = _record(conn)
        lineage_id = _insert_raw_lineage_set(
            conn,
            team_id=101,
            target=target,
            input_count=1,
        )
        conn.execute(
            "INSERT INTO schedule_rest_lineage_input "
            "(lineage_set_id, input_ordinal, input_match_identity_id, "
            " input_snapshot_id) VALUES (?, 0, ?, ?)",
            (lineage_id, target["identity_id"], target["snapshot_id"]),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO schedule_rest_feature "
                "(lineage_set_id, team_id, target_match_identity_id, "
                " target_snapshot_id, feature_definition, feature_version, "
                " as_of_observed_at, input_set_hash, input_count, "
                " feature_payload_hash, feature_value_json, "
                " computation_status, provenance, computed_at) "
                "SELECT id, team_id, target_match_identity_id, "
                "target_snapshot_id, feature_definition, feature_version, "
                "as_of_observed_at, input_set_hash, expected_input_count, "
                "?, '{}', 'computed', 'direct', ? "
                "FROM schedule_rest_lineage_set WHERE id=?",
                ("e" * 64, "2026-07-26T00:00:00Z", lineage_id),
            )
    finally:
        conn.close()


def test_one_observation_event_can_associate_multiple_matches(tmp_path):
    conn = _open(tmp_path / "multi-match-observation.db")
    try:
        common = {
            "provider": "synthetic",
            "canonical_match_id": None,
            "identity_created_at": T0,
            "identity_provenance": "offline-test",
            "source_updated_at": None,
            "snapshot_provenance": "offline-test-state",
            "source": "offline-fixture",
            "competition_scope": "test-competition",
            "season_scope": "2026",
            "observed_at": T0,
            "poll_run_id": "shared-poll",
            "payload_hash": _hash("shared-response"),
            "ingested_at": T0,
        }
        results = [
            schedule.record_match_state(
                conn,
                provider_match_id=match_id,
                state=_state(),
                **common,
            )
            for match_id in (9201, 9202)
        ]
        assert results[0]["observation_event_id"] == results[1][
            "observation_event_id"
        ]
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_observation_event"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_match_observation"
        ).fetchone()[0] == 2
    finally:
        conn.close()


def _batch_match(match_id: int) -> dict:
    return {
        "provider_match_id": match_id,
        "canonical_match_id": None,
        "state": _state(
            home_team_id=match_id * 10 + 1,
            away_team_id=match_id * 10 + 2,
        ),
        "source_updated_at": None,
        "snapshot_provenance": "offline-batch-state",
    }


def test_record_match_states_batch_is_one_event_and_exactly_replayable(
    tmp_path,
):
    conn = _open(tmp_path / "formal-batch.db")
    common = {
        "provider": "synthetic",
        "identity_created_at": T0,
        "identity_provenance": "offline-batch",
        "matches": (_batch_match(9301), _batch_match(9302)),
        "source": "offline-batch-fixture",
        "competition_scope": "test-competition",
        "season_scope": "2026",
        "observed_at": T0,
        "poll_run_id": "formal-batch",
        "payload_hash": _hash("formal-batch"),
        "ingested_at": T0,
    }
    try:
        first = schedule.record_match_states_batch(conn, **common)
        assert first == {
            "identity_inserted": 2,
            "identity_skipped": 0,
            "snapshot_inserted": 2,
            "snapshot_skipped": 0,
            "event_inserted": 1,
            "event_skipped": 0,
            "association_inserted": 2,
            "association_skipped": 0,
        }
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_observation_event"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_match_observation"
        ).fetchone()[0] == 2
        replay = schedule.record_match_states_batch(conn, **common)
        assert replay == {
            "identity_inserted": 0,
            "identity_skipped": 2,
            "snapshot_inserted": 0,
            "snapshot_skipped": 2,
            "event_inserted": 0,
            "event_skipped": 1,
            "association_inserted": 0,
            "association_skipped": 2,
        }
    finally:
        conn.close()


def test_record_match_states_batch_fault_rolls_back_event_and_all_rows(
    tmp_path,
):
    conn = _open(tmp_path / "formal-batch-rollback.db")
    try:
        with pytest.raises(
            schedule.ScheduleStateDataError,
            match="schedule state batch interrupted",
        ):
            schedule.record_match_states_batch(
                conn,
                provider="synthetic",
                identity_created_at=T0,
                identity_provenance="offline-batch",
                matches=(
                    _batch_match(9401),
                    _batch_match(9402),
                    _batch_match(9403),
                ),
                source="offline-batch-fixture",
                competition_scope="test-competition",
                season_scope="2026",
                observed_at=T0,
                poll_run_id="formal-batch-rollback",
                payload_hash=_hash("formal-batch-rollback"),
                ingested_at=T0,
                _fault_after_associations=2,
            )
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_observation_event"
        ).fetchone()[0] == 0
        assert _counts(conn) == {
            "schedule_match_identity": 0,
            "schedule_match_state_snapshot": 0,
            "schedule_match_observation": 0,
            "schedule_rest_feature": 0,
            "schedule_rest_feature_input": 0,
        }
    finally:
        conn.close()


def test_record_match_states_batch_mid_batch_database_exception_rolls_back_all(
    tmp_path,
):
    conn = _open(tmp_path / "formal-batch-database-exception.db")
    try:
        conn.execute(
            """
            CREATE TRIGGER fail_second_batch_association
            BEFORE INSERT ON schedule_match_observation
            WHEN (
              SELECT COUNT(*) FROM schedule_match_observation
            ) = 1
            BEGIN
              SELECT RAISE(ABORT, 'synthetic batch database exception');
            END
            """
        )
        with pytest.raises(
            schedule.ScheduleStateConflictError,
            match="schedule state batch conflict",
        ) as captured:
            schedule.record_match_states_batch(
                conn,
                provider="synthetic",
                identity_created_at=T0,
                identity_provenance="offline-batch",
                matches=(
                    _batch_match(9501),
                    _batch_match(9502),
                    _batch_match(9503),
                ),
                source="offline-batch-fixture",
                competition_scope="test-competition",
                season_scope="2026",
                observed_at=T0,
                poll_run_id="formal-batch-database-exception",
                payload_hash=_hash("formal-batch-database-exception"),
                ingested_at=T0,
            )
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_observation_event"
        ).fetchone()[0] == 0
        assert _counts(conn) == {
            "schedule_match_identity": 0,
            "schedule_match_state_snapshot": 0,
            "schedule_match_observation": 0,
            "schedule_rest_feature": 0,
            "schedule_rest_feature_input": 0,
        }
    finally:
        conn.close()


@pytest.mark.parametrize(
    "kind",
    [
        "identity",
        "snapshot",
        "observation-event",
        "observation",
        "lineage-set",
        "lineage-input",
        "feature",
    ],
)
def test_insert_or_replace_natural_key_cannot_overwrite_append_only_history(
    tmp_path,
    kind,
):
    conn = _open(tmp_path / f"replace-{kind}.db")
    try:
        base = _record(conn)
        feature = schedule.record_rest_feature(
            conn,
            team_id=101,
            target_snapshot_id=base["snapshot_id"],
            feature_definition=schedule.REST_FEATURE_DEFINITION,
            feature_version=schedule.REST_FEATURE_VERSION,
            as_of_observed_at=T0,
            input_snapshot_ids=(base["snapshot_id"],),
            feature_value={"gap": None},
            computation_status="computed",
            provenance=schedule.REST_FEATURE_PROVENANCE,
            computed_at=T0,
        )
        conn.commit()
        conn.execute("PRAGMA recursive_triggers = OFF")
        assert conn.execute("PRAGMA recursive_triggers").fetchone()[0] == 0
        if kind == "identity":
            sql = (
                "INSERT OR REPLACE INTO schedule_match_identity "
                "(provider, provider_match_id, canonical_match_id, created_at, "
                " identity_provenance) "
                "VALUES ('synthetic', '9001', NULL, ?, 'rewritten')"
            )
            params = (CANONICAL_T0,)
        elif kind == "snapshot":
            original = conn.execute(
                "SELECT * FROM schedule_match_state_snapshot WHERE id=?",
                (base["snapshot_id"],),
            ).fetchone()
            columns = [column for column in original.keys() if column != "id"]
            values = [original[column] for column in columns]
            values[columns.index("status")] = "REWRITTEN"
            sql = (
                "INSERT OR REPLACE INTO schedule_match_state_snapshot "
                f"({', '.join(columns)}) VALUES "
                f"({', '.join('?' for _ in columns)})"
            )
            params = tuple(values)
        elif kind == "observation-event":
            original = conn.execute(
                "SELECT * FROM schedule_observation_event WHERE id=?",
                (base["observation_event_id"],),
            ).fetchone()
            columns = [column for column in original.keys() if column != "id"]
            values = [original[column] for column in columns]
            values[columns.index("ingested_at")] = (
                "2026-07-26T00:00:01.000000Z"
            )
            sql = (
                "INSERT OR REPLACE INTO schedule_observation_event "
                f"({', '.join(columns)}) VALUES "
                f"({', '.join('?' for _ in columns)})"
            )
            params = tuple(values)
        elif kind == "observation":
            original = conn.execute(
                "SELECT * FROM schedule_match_observation WHERE id=?",
                (base["observation_id"],),
            ).fetchone()
            columns = [column for column in original.keys() if column != "id"]
            values = [original[column] for column in columns]
            values[columns.index("snapshot_id")] = base["snapshot_id"]
            sql = (
                "INSERT OR REPLACE INTO schedule_match_observation "
                f"({', '.join(columns)}) VALUES "
                f"({', '.join('?' for _ in columns)})"
            )
            params = tuple(values)
        elif kind == "lineage-set":
            original = conn.execute(
                "SELECT * FROM schedule_rest_lineage_set "
                "WHERE id=(SELECT lineage_set_id FROM schedule_rest_feature "
                "WHERE id=?)",
                (feature["feature_id"],),
            ).fetchone()
            columns = [column for column in original.keys() if column != "id"]
            values = [original[column] for column in columns]
            values[columns.index("as_of_observed_at")] = (
                "2026-07-26T00:00:01.000000Z"
            )
            sql = (
                "INSERT OR REPLACE INTO schedule_rest_lineage_set "
                f"({', '.join(columns)}) VALUES "
                f"({', '.join('?' for _ in columns)})"
            )
            params = tuple(values)
        elif kind == "lineage-input":
            original = conn.execute(
                "SELECT * FROM schedule_rest_lineage_input "
                "WHERE lineage_set_id=(SELECT lineage_set_id "
                "FROM schedule_rest_feature WHERE id=?)",
                (feature["feature_id"],),
            ).fetchone()
            columns = list(original.keys())
            values = [original[column] for column in columns]
            sql = (
                "INSERT OR REPLACE INTO schedule_rest_lineage_input "
                f"({', '.join(columns)}) VALUES "
                f"({', '.join('?' for _ in columns)})"
            )
            params = tuple(values)
        else:
            original = conn.execute(
                "SELECT * FROM schedule_rest_feature WHERE id=?",
                (feature["feature_id"],),
            ).fetchone()
            columns = [column for column in original.keys() if column != "id"]
            values = [original[column] for column in columns]
            values[columns.index("feature_value_json")] = '{"rewritten":true}'
            sql = (
                "INSERT OR REPLACE INTO schedule_rest_feature "
                f"({', '.join(columns)}) VALUES "
                f"({', '.join('?' for _ in columns)})"
            )
            params = tuple(values)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql, params)
        if kind != "lineage-input":
            table, row_id = {
                "identity": (
                    "schedule_match_identity",
                    base["identity_id"],
                ),
                "snapshot": (
                    "schedule_match_state_snapshot",
                    base["snapshot_id"],
                ),
                "observation-event": (
                    "schedule_observation_event",
                    base["observation_event_id"],
                ),
                "observation": (
                    "schedule_match_observation",
                    base["observation_id"],
                ),
                "lineage-set": (
                    "schedule_rest_lineage_set",
                    conn.execute(
                        "SELECT lineage_set_id FROM schedule_rest_feature "
                        "WHERE id=?",
                        (feature["feature_id"],),
                    ).fetchone()[0],
                ),
                "feature": (
                    "schedule_rest_feature",
                    feature["feature_id"],
                ),
            }[kind]
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    f"INSERT OR REPLACE INTO {table} "
                    f"SELECT * FROM {table} WHERE id=?",
                    (row_id,),
                )
    finally:
        conn.close()


def test_upsert_do_update_cannot_overwrite_any_append_only_layer(tmp_path):
    conn = _open(tmp_path / "upsert-all-layers.db")
    try:
        base = _record(conn)
        feature = schedule.record_rest_feature(
            conn,
            team_id=101,
            target_snapshot_id=base["snapshot_id"],
            feature_definition=schedule.REST_FEATURE_DEFINITION,
            feature_version=schedule.REST_FEATURE_VERSION,
            as_of_observed_at=T0,
            input_snapshot_ids=(base["snapshot_id"],),
            feature_value={"gap": None},
            computation_status="computed",
            provenance=schedule.REST_FEATURE_PROVENANCE,
            computed_at=T0,
        )
        statements = (
            (
                "INSERT INTO schedule_match_identity "
                "(provider, provider_match_id, created_at, identity_provenance) "
                "VALUES ('synthetic', '9001', ?, 'upsert') "
                "ON CONFLICT DO UPDATE SET identity_provenance='changed'",
                (CANONICAL_T0,),
            ),
            (
                "INSERT INTO schedule_match_state_snapshot "
                "SELECT * FROM schedule_match_state_snapshot WHERE id=? "
                "ON CONFLICT DO UPDATE SET status='changed'",
                (base["snapshot_id"],),
            ),
            (
                "INSERT INTO schedule_observation_event "
                "SELECT * FROM schedule_observation_event WHERE id=? "
                "ON CONFLICT DO UPDATE SET source='changed'",
                (base["observation_event_id"],),
            ),
            (
                "INSERT INTO schedule_match_observation "
                "SELECT * FROM schedule_match_observation WHERE id=? "
                "ON CONFLICT DO UPDATE SET snapshot_id=excluded.snapshot_id",
                (base["observation_id"],),
            ),
            (
                "INSERT INTO schedule_rest_lineage_set "
                "SELECT * FROM schedule_rest_lineage_set "
                "WHERE id=(SELECT lineage_set_id FROM schedule_rest_feature "
                "WHERE id=?) "
                "ON CONFLICT DO UPDATE SET feature_definition='changed'",
                (feature["feature_id"],),
            ),
            (
                "INSERT INTO schedule_rest_lineage_input "
                "SELECT * FROM schedule_rest_lineage_input "
                "WHERE lineage_set_id=(SELECT lineage_set_id "
                "FROM schedule_rest_feature WHERE id=?) "
                "ON CONFLICT DO UPDATE SET input_ordinal=excluded.input_ordinal",
                (feature["feature_id"],),
            ),
            (
                "INSERT INTO schedule_rest_feature "
                "SELECT * FROM schedule_rest_feature WHERE id=? "
                "ON CONFLICT DO UPDATE SET computation_status='failed'",
                (feature["feature_id"],),
            ),
        )
        for sql, params in statements:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql, params)
    finally:
        conn.close()


@pytest.mark.parametrize(
    "target",
    [
        "snapshot",
        "observation-association",
        "lineage-set",
        "lineage-input",
        "final-feature",
    ],
)
def test_fk_off_direct_sql_still_rejects_business_orphans(tmp_path, target):
    conn = _open(tmp_path / f"fk-off-{target}.db")
    try:
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        if target == "snapshot":
            action = lambda: conn.execute(
                RAW_SNAPSHOT_INSERT,
                _raw_snapshot_values(
                    conn,
                    999001,
                    kickoff="2026-08-01T12:00:00.000000Z",
                    state_hash="a" * 64,
                ),
            )
        elif target == "observation-association":
            action = lambda: conn.execute(
                "INSERT INTO schedule_match_observation "
                "(observation_event_id, match_identity_id, snapshot_id, "
                " observed_at) VALUES (999001, 999002, 999003, ?)",
                (CANONICAL_T0,),
            )
        elif target == "lineage-set":
            action = lambda: conn.execute(
                "INSERT INTO schedule_rest_lineage_set "
                "(team_id, target_match_identity_id, target_snapshot_id, "
                " feature_definition, feature_version, as_of_observed_at, "
                " input_set_hash, expected_input_count) "
                "VALUES (101, 999001, 999002, 'gap', 'v1', ?, ?, 1)",
                (CANONICAL_T0, "b" * 64),
            )
        elif target == "lineage-input":
            action = lambda: conn.execute(
                "INSERT INTO schedule_rest_lineage_input "
                "(lineage_set_id, input_ordinal, input_match_identity_id, "
                " input_snapshot_id) VALUES (999001, 0, 999002, 999003)"
            )
        else:
            action = lambda: conn.execute(
                "INSERT INTO schedule_rest_feature "
                "(lineage_set_id, team_id, target_match_identity_id, "
                " target_snapshot_id, feature_definition, feature_version, "
                " as_of_observed_at, input_set_hash, input_count, "
                " feature_payload_hash, feature_value_json, "
                " computation_status, provenance, computed_at) "
                "VALUES (999001, 101, 999002, 999003, 'gap', 'v1', ?, ?, "
                "1, ?, '{}', 'computed', 'direct', ?)",
                (CANONICAL_T0, "b" * 64, "c" * 64, CANONICAL_T0),
            )
        with pytest.raises(sqlite3.IntegrityError):
            action()
    finally:
        conn.close()


def test_finalized_feature_rejects_incomplete_lineage_header(tmp_path):
    conn = _open(tmp_path / "incomplete-lineage.db")
    try:
        target = _record(conn)
        lineage_id = conn.execute(
            "INSERT INTO schedule_rest_lineage_set "
            "(team_id, target_match_identity_id, target_snapshot_id, "
            " feature_definition, feature_version, as_of_observed_at, "
            " input_set_hash, expected_input_count) "
            "VALUES (101, ?, ?, 'gap', 'v1', ?, ?, 2)",
            (
                target["identity_id"],
                target["snapshot_id"],
                CANONICAL_T0,
                "c" * 64,
            ),
        ).lastrowid
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO schedule_rest_feature "
                "(lineage_set_id, team_id, target_match_identity_id, "
                " target_snapshot_id, feature_definition, feature_version, "
                " as_of_observed_at, input_set_hash, input_count, "
                " feature_payload_hash, feature_value_json, "
                " computation_status, provenance, computed_at) "
                "VALUES (?, 101, ?, ?, 'gap', 'v1', ?, ?, 2, ?, '{}', "
                " 'computed', 'direct', ?)",
                (
                    lineage_id,
                    target["identity_id"],
                    target["snapshot_id"],
                    CANONICAL_T0,
                    "c" * 64,
                    "d" * 64,
                    CANONICAL_T0,
                ),
            )
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_rest_feature"
        ).fetchone()[0] == 0
    finally:
        conn.close()
