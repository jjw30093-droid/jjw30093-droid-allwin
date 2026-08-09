"""Permanent synthetic tests for the temporary-copy migration trial."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from analysis.cwc_production_integration_design import (
    cwc_production_integration_design as cwc,
)
from analysis.schedule_state_migration_trial import (
    schedule_state_migration_trial as trial,
)
from backend.db import migrate
from backend.schedules import state as schedule


T0 = "2026-07-26T00:00:00Z"
T1 = "2026-07-26T00:05:00Z"
T_MINUS_1D = "2026-07-25T00:00:00Z"
T_PLUS_1D = "2026-07-27T00:00:00Z"


def _create_legacy_database(path: Path) -> sqlite3.Connection:
    path.touch(mode=0o600)
    path.chmod(0o600)
    assert migrate.apply_all("core", db_file=path, quiet=True) == 3
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA recursive_triggers = ON")
    return conn


def _fixture() -> dict:
    return cwc.load_canonical_fixture()


def _create_source_database(path: Path) -> Path:
    path.touch(mode=0o600)
    path.chmod(0o600)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE source_marker (value TEXT NOT NULL)")
        conn.execute("INSERT INTO source_marker VALUES ('stable')")
        conn.commit()
    finally:
        conn.close()
    return path


def _create_v1_source_database(path: Path, tmp_path: Path) -> Path:
    path.touch(mode=0o600)
    path.chmod(0o600)
    staged = tmp_path / f"{path.stem}-migrations"
    staged.mkdir()
    source = migrate.MIGRATIONS_ROOT / "core"
    name = "0001_dim_match_kickoff.sql"
    (staged / name).write_bytes((source / name).read_bytes())
    assert migrate.apply_all(
        "core", db_file=path, migrations_dir=staged, quiet=True
    ) == 1
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO dim_match "
            "(Match_ID, Season, League_ID, Date, Home_Team_ID, Away_Team_ID, "
            " Home_Team_Name, Away_Team_Name, status) "
            "VALUES (1, '2025', 78, '2026-08-01', 101, 202, "
            " 'Home', 'Away', 'NotStarted')"
        )
        conn.commit()
    finally:
        conn.close()
    return path


def _replace_sidecar(path: Path, content: bytes = b"") -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    path.write_bytes(content)
    path.chmod(0o600)


def _main_file_identity(path: Path) -> tuple[object, ...]:
    metadata = path.stat()
    return (
        trial.file_sha256(path),
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode & 0o777,
        metadata.st_nlink,
    )


def _path_entry_evidence(path: Path) -> tuple[object, ...]:
    metadata = path.lstat()
    file_type = stat.S_IFMT(metadata.st_mode)
    if stat.S_ISREG(metadata.st_mode):
        content_evidence: object = trial.file_sha256(path)
    elif stat.S_ISLNK(metadata.st_mode):
        content_evidence = os.readlink(path)
    elif stat.S_ISDIR(metadata.st_mode):
        content_evidence = tuple(sorted(item.name for item in path.iterdir()))
    else:
        content_evidence = None
    return (
        file_type,
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_uid,
        stat.S_IMODE(metadata.st_mode),
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        content_evidence,
    )


def _create_companion_entry(
    path: Path,
    kind: str,
    *,
    anchor_directory: Path,
) -> None:
    if kind == "regular":
        path.write_bytes(b"ordinary-companion-evidence")
        path.chmod(0o600)
    elif kind == "symlink":
        anchor = anchor_directory / "symlink-companion-anchor"
        anchor.write_bytes(b"symlink-anchor")
        anchor.chmod(0o600)
        path.symlink_to(anchor)
    elif kind == "hardlink":
        anchor = anchor_directory / "hardlink-companion-anchor"
        anchor.write_bytes(b"hardlink-anchor")
        anchor.chmod(0o600)
        os.link(anchor, path)
    elif kind == "directory":
        path.mkdir(mode=0o700)
    elif kind == "fifo":
        os.mkfifo(path, mode=0o600)
    else:
        raise AssertionError(f"unsupported companion test kind: {kind}")


def _assert_v1_schema_unchanged(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        assert conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,)]
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE name GLOB 'schedule_*' "
            "OR name GLOB 'idx_schedule_*' "
            "OR name GLOB 'trg_schedule_*' "
            "OR name GLOB 'uq_schedule_*' "
            "OR name='current_schedule_match_state'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_legacy_digest_uses_original_columns_across_0002_0003(tmp_path):
    path = tmp_path / "legacy.db"
    path.touch(mode=0o600)
    path.chmod(0o600)
    staged = tmp_path / "migrations"
    staged.mkdir()
    source = migrate.MIGRATIONS_ROOT / "core"
    for name in ("0001_dim_match_kickoff.sql",):
        (staged / name).write_bytes((source / name).read_bytes())
    assert migrate.apply_all(
        "core", db_file=path, migrations_dir=staged, quiet=True
    ) == 1
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO dim_match "
        "(Match_ID, Season, League_ID, Date, Home_Team_ID, Away_Team_ID, "
        " Home_Team_Name, Away_Team_Name, status) "
        "VALUES (1, '2025', 47, '2025-06-15', 101, 202, "
        " 'Home', 'Away', 'Finish')"
    )
    conn.commit()
    baseline = trial.capture_legacy_baseline(conn)
    conn.close()

    prepared = trial.prepare_trial_copy(path, temp_root=tmp_path)
    migration = trial.migrate_prepared_trial_copy(prepared)
    assert migration["applied"] == 2
    assert migration["legacy"]["valid"] is True
    assert migration["integrity_check"] == "ok"
    assert migration["foreign_key_issues"] == []
    assert migration["noop"]["applied"] == 0
    assert migration["noop"]["content_unchanged"] is True
    assert migration["noop"]["metadata_unchanged"] is True
    conn = sqlite3.connect(prepared.destination)
    try:
        comparison = trial.compare_legacy_baseline(conn, baseline)
        assert comparison["valid"] is True
        row = conn.execute(
            "SELECT kickoff_at_utc, kickoff_precision, kickoff_source "
            "FROM dim_match WHERE Match_ID=1"
        ).fetchone()
        assert row == (None, "date_only", None)
    finally:
        conn.close()


def test_identity_audit_and_backfill_do_not_invent_state(tmp_path):
    conn = _create_legacy_database(tmp_path / "identity.db")
    try:
        conn.execute(
            "INSERT INTO dim_match "
            "(Match_ID, Season, League_ID, Date, Home_Team_ID, Away_Team_ID, "
            " Home_Team_Name, Away_Team_Name, status, kickoff_precision) "
            "VALUES (1, '2025', 47, '2025-06-15', 101, 202, "
            " 'Home', 'Away', 'Finish', 'date_only')"
        )
        conn.commit()
        audit = trial.audit_legacy_dim_match(
            conn, repository_provenance_verified=True
        )
        assert audit["stable_identity_eligible"] == 1
        assert audit["state_snapshot_eligible"] == 0
        first = trial.backfill_legacy_identities(
            conn, audit=audit, identity_created_at=T0
        )
        second = trial.backfill_legacy_identities(
            conn, audit=audit, identity_created_at=T0
        )
        later = trial.backfill_legacy_identities(
            conn, audit=audit, identity_created_at=T_PLUS_1D
        )
        earlier = trial.backfill_legacy_identities(
            conn, audit=audit, identity_created_at=T_MINUS_1D
        )
        assert first["inserted"] == 1
        assert second["skipped"] == 1
        assert later["skipped"] == 1
        assert earlier["skipped"] == 1
        assert conn.execute(
            "SELECT created_at FROM schedule_match_identity"
        ).fetchone()[0] == schedule._utc(T0)
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_match_state_snapshot"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_match_observation"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT kickoff_at_utc FROM dim_match WHERE Match_ID=1"
        ).fetchone()[0] is None
    finally:
        conn.close()


def test_identity_gate_closes_without_repository_provenance(tmp_path):
    conn = _create_legacy_database(tmp_path / "identity-closed.db")
    try:
        conn.execute("INSERT INTO dim_match (Match_ID) VALUES (1)")
        conn.commit()
        audit = trial.audit_legacy_dim_match(
            conn, repository_provenance_verified=False
        )
        assert audit["stable_identity_eligible"] == 0
        with pytest.raises(
            trial.TrialDataError,
            match="legacy identity provenance gate is closed",
        ):
            trial.backfill_legacy_identities(
                conn, audit=audit, identity_created_at=T0
            )
    finally:
        conn.close()


def test_identity_backfill_replays_across_separate_connections_and_run_times(
    tmp_path,
):
    path = tmp_path / "identity-cross-run.db"
    conn = _create_legacy_database(path)
    conn.execute(
        "INSERT INTO dim_match "
        "(Match_ID, Season, League_ID, Date, Home_Team_ID, Away_Team_ID, "
        " status, kickoff_precision) "
        "VALUES (1, '2025', 47, '2025-06-15', 101, 202, "
        " 'Finish', 'date_only')"
    )
    conn.commit()
    audit = trial.audit_legacy_dim_match(
        conn, repository_provenance_verified=True
    )
    first = trial.backfill_legacy_identities(
        conn, audit=audit, identity_created_at=T0
    )
    conn.close()

    reopened = sqlite3.connect(path)
    reopened.row_factory = sqlite3.Row
    try:
        second_audit = trial.audit_legacy_dim_match(
            reopened, repository_provenance_verified=True
        )
        second = trial.backfill_legacy_identities(
            reopened,
            audit=second_audit,
            identity_created_at=T_PLUS_1D,
        )
        assert first["inserted"] == 1
        assert second["skipped"] == 1
        assert tuple(
            reopened.execute(
                "SELECT COUNT(*), MIN(created_at), MAX(created_at) "
                "FROM schedule_match_identity"
            ).fetchone()
        ) == (1, schedule._utc(T0), schedule._utc(T0))
        assert reopened.execute(
            "SELECT COUNT(*) FROM schedule_match_state_snapshot"
        ).fetchone()[0] == 0
        assert reopened.execute(
            "SELECT COUNT(*) FROM schedule_match_observation"
        ).fetchone()[0] == 0
    finally:
        reopened.close()


def test_cwc_formal_import_replay_later_observation_and_features(tmp_path):
    conn = _create_legacy_database(tmp_path / "cwc.db")
    document = _fixture()
    try:
        first = trial.import_cwc_formal_schema(
            conn,
            document=document,
            observed_at=T0,
            ingested_at=T0,
            computed_at=T0,
            poll_run_id="trial-cwc-t0",
        )
        first_counts = trial.schedule_counts(conn)
        first_feature_rows = conn.execute(
            "SELECT id, feature_payload_hash, computed_at "
            "FROM schedule_rest_feature ORDER BY id"
        ).fetchall()
        replay = trial.import_cwc_formal_schema(
            conn,
            document=document,
            observed_at=T0,
            ingested_at=T0,
            computed_at=T1,
            poll_run_id="trial-cwc-t0",
        )
        later = trial.import_cwc_formal_schema(
            conn,
            document=document,
            observed_at=T1,
            ingested_at=T1,
            computed_at=T1,
            poll_run_id="trial-cwc-t1",
        )
        final_counts = trial.schedule_counts(conn)
        final_feature_rows = conn.execute(
            "SELECT id, feature_payload_hash, computed_at "
            "FROM schedule_rest_feature ORDER BY id"
        ).fetchall()

        assert first["identity_inserted"] == 66
        assert first["snapshot_inserted"] == 66
        assert first["observation_inserted"] == 66
        assert first["feature_inserted"] == 126
        assert replay["observation_skipped"] == 66
        assert replay["feature_skipped"] == 126
        assert later["identity_inserted"] == 0
        assert later["snapshot_inserted"] == 0
        assert later["observation_inserted"] == 66
        assert later["feature_skipped"] == 126
        assert final_counts["schedule_match_identity"] == 66
        assert final_counts["schedule_match_state_snapshot"] == 66
        assert final_counts["schedule_observation_event"] == 2
        assert final_counts["schedule_match_observation"] == 132
        assert final_counts["schedule_rest_feature"] == 126
        assert first_feature_rows == final_feature_rows
        assert first_counts["schedule_rest_feature"] == 126

        evidence = trial.cwc_business_evidence(conn)
        assert evidence == {
            "current_count": 66,
            "non_cancelled": 63,
            "cancelled": 3,
            "team_relationships_expressed_in_snapshots": 132,
            "aet_count": 3,
            "city_feature_count": 4,
            "city_gap_hours": [None, 105.0, 90.0, 102.0],
        }
        identity_id = conn.execute(
            "SELECT id FROM schedule_match_identity "
            "WHERE provider='fotmob' AND provider_match_id='4685708'"
        ).fetchone()[0]
        assert schedule.get_match_state_as_of(
            conn, identity_id, T0
        )["observed_at"] == schedule._utc(T0)
        assert schedule.get_current_match_state(
            conn, identity_id
        )["observed_at"] == schedule._utc(T1)
    finally:
        conn.close()


def test_prepare_trial_copy_creates_owned_private_workspace_and_files(tmp_path):
    source = _create_source_database(tmp_path / "source.db")
    prepared = trial.prepare_trial_copy(source, temp_root=tmp_path)

    assert prepared.source.path == source.resolve()
    assert prepared.creator_pid == os.getpid()
    assert prepared.creator_uid == os.getuid()
    for path, expected_mode in (
        (prepared.run_directory, 0o700),
        (prepared.destination, 0o600),
        (prepared.recovery_image, 0o600),
    ):
        metadata = path.stat()
        assert metadata.st_uid == os.getuid()
        assert metadata.st_mode & 0o777 == expected_mode
        assert not path.is_symlink()
    assert prepared.destination.stat().st_nlink == 1
    assert prepared.recovery_image.stat().st_nlink == 1
    assert trial.file_sha256(prepared.destination) == trial.file_sha256(source)
    assert trial.file_sha256(prepared.recovery_image) == trial.file_sha256(
        source
    )


def test_prepare_trial_copy_default_system_temp_root_is_safe(tmp_path):
    source = _create_source_database(tmp_path / "source.db")
    prepared = trial.prepare_trial_copy(source)
    assert prepared.run_directory.parent.resolve() in trial._temp_roots()
    assert prepared.run_directory.stat().st_mode & 0o777 == 0o700


def test_prepare_trial_copy_rejects_symlink_source_and_parent(tmp_path):
    source = _create_source_database(tmp_path / "source.db")
    source_alias = tmp_path / "source-alias.db"
    source_alias.symlink_to(source)
    with pytest.raises(trial.TrialSafetyError):
        trial.prepare_trial_copy(source_alias, temp_root=tmp_path)

    real_parent = tmp_path / "real-parent"
    real_parent.mkdir(mode=0o700)
    parent_alias = tmp_path / "parent-alias"
    parent_alias.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(trial.TrialSafetyError):
        trial.prepare_trial_copy(source, temp_root=parent_alias)


def test_prepare_trial_copy_rejects_world_writable_parent(tmp_path):
    source = _create_source_database(tmp_path / "source.db")
    unsafe_parent = tmp_path / "unsafe-parent"
    unsafe_parent.mkdir(mode=0o777)
    unsafe_parent.chmod(0o777)
    with pytest.raises(trial.TrialSafetyError):
        trial.prepare_trial_copy(source, temp_root=unsafe_parent)


def test_exclusive_trial_file_creation_rejects_existing_targets(tmp_path):
    existing_destination = tmp_path / "trial.db"
    existing_destination.write_bytes(b"existing-regular-file")
    existing_destination.chmod(0o600)
    existing_recovery = tmp_path / "recovery.db"
    recovery_conn = sqlite3.connect(existing_recovery)
    recovery_conn.execute("CREATE TABLE existing_evidence (value TEXT)")
    recovery_conn.execute("INSERT INTO existing_evidence VALUES ('preserve')")
    recovery_conn.commit()
    recovery_conn.close()
    existing_recovery.chmod(0o600)
    for target in (existing_destination, existing_recovery):
        before = target.read_bytes()
        with pytest.raises(trial.TrialSafetyError):
            trial._exclusive_create_file(target)
        assert target.read_bytes() == before

    symlink_target = tmp_path / "trial-symlink.db"
    symlink_target.symlink_to(existing_destination)
    with pytest.raises(trial.TrialSafetyError):
        trial._exclusive_create_file(symlink_target)
    assert symlink_target.is_symlink()

    hardlink_target = tmp_path / "trial-hardlink.db"
    os.link(existing_destination, hardlink_target)
    with pytest.raises(trial.TrialSafetyError):
        trial._exclusive_create_file(hardlink_target)
    assert hardlink_target.stat().st_ino == existing_destination.stat().st_ino


def test_prepare_trial_copy_rejects_nonempty_source_wal(tmp_path):
    source = _create_source_database(tmp_path / "source.db")
    Path(f"{source}-wal").write_bytes(b"synthetic-uncheckpointed-wal")
    with pytest.raises(trial.TrialSafetyError):
        trial.prepare_trial_copy(source, temp_root=tmp_path)


def test_prepare_trial_copy_detects_source_change_during_copy(
    tmp_path,
    monkeypatch,
):
    source = _create_source_database(tmp_path / "source.db")
    original_copy = trial._copy_source_bytes

    def mutate_after_copy(source_path, destination_fd):
        original_copy(source_path, destination_fd)
        with source_path.open("ab") as handle:
            handle.write(b"changed-during-copy")

    monkeypatch.setattr(trial, "_copy_source_bytes", mutate_after_copy)
    with pytest.raises(trial.TrialSafetyError):
        trial.prepare_trial_copy(source, temp_root=tmp_path)


def test_migration_rejects_source_change_after_copy(tmp_path):
    source = _create_source_database(tmp_path / "source.db")
    prepared = trial.prepare_trial_copy(source, temp_root=tmp_path)
    with source.open("ab") as handle:
        handle.write(b"changed-before-migration")
    with pytest.raises(trial.TrialSafetyError):
        trial.migrate_prepared_trial_copy(prepared)


def test_migration_rejects_destination_inode_replacement(tmp_path):
    source = _create_source_database(tmp_path / "source.db")
    prepared = trial.prepare_trial_copy(source, temp_root=tmp_path)
    replacement = prepared.run_directory / "replacement.db"
    replacement.write_bytes(source.read_bytes())
    replacement.chmod(0o600)
    prepared.destination.unlink()
    replacement.rename(prepared.destination)
    with pytest.raises(trial.TrialSafetyError):
        trial.migrate_prepared_trial_copy(prepared)


def test_migration_rejects_run_directory_inode_replacement(tmp_path):
    source = _create_source_database(tmp_path / "source.db")
    prepared = trial.prepare_trial_copy(source, temp_root=tmp_path)
    moved = prepared.run_directory.with_name(
        f"{prepared.run_directory.name}-moved"
    )
    prepared.run_directory.rename(moved)
    prepared.run_directory.mkdir(mode=0o700)
    with pytest.raises(trial.TrialSafetyError):
        trial.migrate_prepared_trial_copy(prepared)


def test_migration_rejects_destination_hardlink_and_mode_change(tmp_path):
    source = _create_source_database(tmp_path / "source.db")
    prepared = trial.prepare_trial_copy(source, temp_root=tmp_path)
    os.link(prepared.destination, prepared.run_directory / "hardlink.db")
    with pytest.raises(trial.TrialSafetyError):
        trial.migrate_prepared_trial_copy(prepared)

    second = trial.prepare_trial_copy(source, temp_root=tmp_path)
    second.destination.chmod(0o640)
    with pytest.raises(trial.TrialSafetyError):
        trial.migrate_prepared_trial_copy(second)


def test_public_migration_rejects_committed_destination_wal_mutation(
    tmp_path,
    monkeypatch,
):
    source = _create_v1_source_database(tmp_path / "source-v1.db", tmp_path)
    prepared = trial.prepare_trial_copy(source, temp_root=tmp_path)
    main_before = _main_file_identity(prepared.destination)
    writer = sqlite3.connect(prepared.destination)
    try:
        assert writer.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute(
            "UPDATE dim_match SET Date='2099-12-31' WHERE Match_ID=1"
        )
        writer.commit()

        destination_wal = Path(f"{prepared.destination}-wal")
        assert destination_wal.is_file()
        assert destination_wal.stat().st_size > 0
        wal_before = _path_entry_evidence(destination_wal)
        assert _main_file_identity(prepared.destination) == main_before
        reader = sqlite3.connect(prepared.destination)
        try:
            assert reader.execute(
                "SELECT Date FROM dim_match WHERE Match_ID=1"
            ).fetchone()[0] == "2099-12-31"
        finally:
            reader.close()

        migration_calls = 0

        def migration_probe(candidate):
            nonlocal migration_calls
            migration_calls += 1
            return {"applied": 2}

        monkeypatch.setattr(
            trial, "_migrate_verified_trial_copy", migration_probe
        )
        with pytest.raises(
            trial.TrialSafetyError,
            match="prepared trial WAL safety boundary rejected",
        ) as captured:
            trial.migrate_prepared_trial_copy(prepared)
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None
        assert migration_calls == 0
        assert _path_entry_evidence(destination_wal) == wal_before
        assert _main_file_identity(prepared.destination) == main_before
        assert writer.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0] == 1
        assert writer.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE name='schedule_match_identity'"
        ).fetchone()[0] == 0
        assert writer.execute(
            "SELECT Date FROM dim_match WHERE Match_ID=1"
        ).fetchone()[0] == "2099-12-31"
    finally:
        writer.close()

    source_reader = sqlite3.connect(source)
    try:
        assert source_reader.execute(
            "SELECT Date FROM dim_match WHERE Match_ID=1"
        ).fetchone()[0] == "2026-08-01"
    finally:
        source_reader.close()


@pytest.mark.parametrize(
    ("suffix", "entry_kind"),
    [
        ("-wal2", "regular"),
        ("-mj ABCDEF12", "regular"),
        ("-journal.extra", "regular"),
        ("-unknown", "regular"),
        (".backup", "regular"),
        (".tmp", "regular"),
        ("-any-arbitrary-name", "regular"),
        ("-prefix-symlink", "symlink"),
        ("-prefix-hardlink", "hardlink"),
        ("-prefix-directory", "directory"),
        ("-prefix-fifo", "fifo"),
    ],
)
def test_public_migration_rejects_unbound_destination_companion_pathset(
    tmp_path,
    monkeypatch,
    suffix,
    entry_kind,
):
    source = _create_v1_source_database(tmp_path / "source-v1.db", tmp_path)
    prepared = trial.prepare_trial_copy(source, temp_root=tmp_path)
    main_before = _main_file_identity(prepared.destination)
    companion = Path(f"{prepared.destination}{suffix}")
    _create_companion_entry(
        companion,
        entry_kind,
        anchor_directory=prepared.run_directory,
    )
    companion_before = _path_entry_evidence(companion)
    migration_calls = 0

    def migration_probe(candidate):
        nonlocal migration_calls
        migration_calls += 1
        return {"applied": 2}

    monkeypatch.setattr(trial, "_migrate_verified_trial_copy", migration_probe)
    with pytest.raises(
        trial.TrialSafetyError,
        match="prepared trial unbound companion safety boundary rejected",
    ) as captured:
        trial.migrate_prepared_trial_copy(prepared)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert migration_calls == 0
    assert _path_entry_evidence(companion) == companion_before
    assert _main_file_identity(prepared.destination) == main_before
    _assert_v1_schema_unchanged(prepared.destination)


@pytest.mark.parametrize(
    ("suffix", "entry_kind"),
    [
        ("-wal2", "regular"),
        ("-mj ABCDEF12", "regular"),
        ("-journal.extra", "regular"),
        ("-unknown", "regular"),
        (".backup", "regular"),
        (".tmp", "regular"),
        ("-any-arbitrary-name", "regular"),
        ("-prefix-symlink", "symlink"),
        ("-prefix-hardlink", "hardlink"),
        ("-prefix-directory", "directory"),
        ("-prefix-fifo", "fifo"),
    ],
)
def test_public_migration_rejects_unbound_recovery_companion_pathset(
    tmp_path,
    monkeypatch,
    suffix,
    entry_kind,
):
    source = _create_v1_source_database(tmp_path / "source-v1.db", tmp_path)
    prepared = trial.prepare_trial_copy(source, temp_root=tmp_path)
    main_before = _main_file_identity(prepared.destination)
    recovery_before = _main_file_identity(prepared.recovery_image)
    companion = Path(f"{prepared.recovery_image}{suffix}")
    _create_companion_entry(
        companion,
        entry_kind,
        anchor_directory=prepared.run_directory,
    )
    companion_before = _path_entry_evidence(companion)
    migration_calls = 0

    def migration_probe(candidate):
        nonlocal migration_calls
        migration_calls += 1
        return {"applied": 2}

    monkeypatch.setattr(trial, "_migrate_verified_trial_copy", migration_probe)
    with pytest.raises(
        trial.TrialSafetyError,
        match="prepared trial unbound companion safety boundary rejected",
    ) as captured:
        trial.migrate_prepared_trial_copy(prepared)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert migration_calls == 0
    assert _path_entry_evidence(companion) == companion_before
    assert _main_file_identity(prepared.destination) == main_before
    assert _main_file_identity(prepared.recovery_image) == recovery_before
    _assert_v1_schema_unchanged(prepared.destination)


def test_prepared_zero_wal_and_shm_are_bound_and_migrate_normally(tmp_path):
    source = _create_v1_source_database(tmp_path / "source-v1.db", tmp_path)
    conn = sqlite3.connect(source)
    try:
        assert conn.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
    finally:
        conn.close()

    prepared = trial.prepare_trial_copy(source, temp_root=tmp_path)
    assert prepared.destination_sidecars.wal is not None
    assert prepared.destination_sidecars.wal.size == 0
    assert prepared.destination_sidecars.shm is not None
    assert prepared.destination_sidecars.journal is None
    assert prepared.destination_companion_names == (
        f"{prepared.destination.name}-shm",
        f"{prepared.destination.name}-wal",
    )
    assert prepared.recovery_sidecars.wal is not None
    assert prepared.recovery_sidecars.wal.size == 0
    assert prepared.recovery_sidecars.shm is not None
    assert prepared.recovery_sidecars.journal is None
    assert prepared.recovery_companion_names == (
        f"{prepared.recovery_image.name}-shm",
        f"{prepared.recovery_image.name}-wal",
    )

    result = trial.migrate_prepared_trial_copy(prepared)
    assert result["applied"] == 2
    assert result["integrity_check"] == "ok"
    assert result["legacy"]["valid"] is True
    expected_final_names = {
        f"{prepared.destination.name}-wal",
        f"{prepared.destination.name}-shm",
    }
    assert set(result["after_apply_companion_names"]) <= expected_final_names
    assert set(result["final_companion_names"]) <= expected_final_names


@pytest.mark.parametrize(
    ("target_name", "sidecar_suffix", "mutation_kind"),
    [
        ("destination", "-wal", "zero"),
        ("destination", "-shm", "shm"),
        ("destination", "-journal", "zero"),
        ("destination", "-wal", "symlink"),
        ("destination", "-wal", "hardlink"),
        ("recovery_image", "-wal", "nonempty"),
    ],
)
def test_migration_rejects_destination_or_recovery_sidecar_change(
    tmp_path,
    monkeypatch,
    target_name,
    sidecar_suffix,
    mutation_kind,
):
    source = _create_source_database(tmp_path / "source.db")
    prepared = trial.prepare_trial_copy(source, temp_root=tmp_path)
    target = getattr(prepared, target_name)
    sidecar = Path(f"{target}{sidecar_suffix}")
    try:
        sidecar.unlink()
    except FileNotFoundError:
        pass

    if mutation_kind == "symlink":
        anchor = tmp_path / "sidecar-anchor"
        anchor.write_bytes(b"")
        anchor.chmod(0o600)
        sidecar.symlink_to(anchor)
    elif mutation_kind == "hardlink":
        anchor = tmp_path / "sidecar-anchor"
        anchor.write_bytes(b"")
        anchor.chmod(0o600)
        os.link(anchor, sidecar)
    elif mutation_kind == "shm":
        _replace_sidecar(sidecar, b"\0" * 32768)
    elif mutation_kind == "nonempty":
        _replace_sidecar(sidecar, b"pending-recovery-wal")
    else:
        _replace_sidecar(sidecar)

    migration_calls = 0

    def migration_probe(candidate):
        nonlocal migration_calls
        migration_calls += 1
        return {"candidate": str(candidate)}

    monkeypatch.setattr(trial, "_migrate_verified_trial_copy", migration_probe)
    with pytest.raises(trial.TrialSafetyError):
        trial.migrate_prepared_trial_copy(prepared)
    assert migration_calls == 0


def test_migration_rechecks_destination_sidecars_after_integrity_probe(
    tmp_path,
    monkeypatch,
):
    source = _create_source_database(tmp_path / "source.db")
    prepared = trial.prepare_trial_copy(source, temp_root=tmp_path)
    destination_journal = Path(f"{prepared.destination}-journal")
    original_integrity = trial._integrity_check_readonly

    def inject_last_moment_sidecar(path):
        result = original_integrity(path)
        _replace_sidecar(destination_journal)
        return result

    migration_calls = 0

    def migration_probe(candidate):
        nonlocal migration_calls
        migration_calls += 1
        return {"candidate": str(candidate)}

    monkeypatch.setattr(
        trial, "_integrity_check_readonly", inject_last_moment_sidecar
    )
    monkeypatch.setattr(trial, "_migrate_verified_trial_copy", migration_probe)
    with pytest.raises(trial.TrialSafetyError):
        trial.migrate_prepared_trial_copy(prepared)
    assert migration_calls == 0


def test_migration_rechecks_unknown_companion_after_integrity_probe(
    tmp_path,
    monkeypatch,
):
    source = _create_v1_source_database(tmp_path / "source-v1.db", tmp_path)
    prepared = trial.prepare_trial_copy(source, temp_root=tmp_path)
    main_before = _main_file_identity(prepared.destination)
    companion = Path(f"{prepared.destination}-wal2")
    original_integrity = trial._integrity_check_readonly
    companion_before: tuple[object, ...] | None = None

    def inject_last_moment_companion(path):
        nonlocal companion_before
        result = original_integrity(path)
        if path == prepared.destination and not companion.exists():
            _create_companion_entry(
                companion,
                "regular",
                anchor_directory=prepared.run_directory,
            )
            companion_before = _path_entry_evidence(companion)
        return result

    migration_calls = 0

    def migration_probe(candidate):
        nonlocal migration_calls
        migration_calls += 1
        return {"applied": 2}

    monkeypatch.setattr(
        trial, "_integrity_check_readonly", inject_last_moment_companion
    )
    monkeypatch.setattr(trial, "_migrate_verified_trial_copy", migration_probe)
    with pytest.raises(
        trial.TrialSafetyError,
        match="prepared trial unbound companion safety boundary rejected",
    ) as captured:
        trial.migrate_prepared_trial_copy(prepared)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert migration_calls == 0
    assert companion_before is not None
    assert _path_entry_evidence(companion) == companion_before
    assert _main_file_identity(prepared.destination) == main_before
    _assert_v1_schema_unchanged(prepared.destination)


def test_migration_rejects_nonempty_destination_sidecar_after_completion(
    tmp_path,
    monkeypatch,
):
    source = _create_source_database(tmp_path / "source.db")
    prepared = trial.prepare_trial_copy(source, temp_root=tmp_path)

    def migration_probe(candidate):
        _replace_sidecar(Path(f"{candidate}-wal"), b"post-migration-wal")
        return {"applied": 2}

    monkeypatch.setattr(trial, "_migrate_verified_trial_copy", migration_probe)
    with pytest.raises(trial.TrialSafetyError):
        trial.migrate_prepared_trial_copy(prepared)


def test_migration_rejects_unknown_companion_after_completion(
    tmp_path,
    monkeypatch,
):
    source = _create_v1_source_database(tmp_path / "source-v1.db", tmp_path)
    prepared = trial.prepare_trial_copy(source, temp_root=tmp_path)
    main_before = _main_file_identity(prepared.destination)
    companion = Path(f"{prepared.destination}-wal2")
    companion_before: tuple[object, ...] | None = None
    migration_calls = 0

    def migration_probe(candidate):
        nonlocal migration_calls, companion_before
        migration_calls += 1
        _create_companion_entry(
            companion,
            "regular",
            anchor_directory=prepared.run_directory,
        )
        companion_before = _path_entry_evidence(companion)
        return {"applied": 2}

    monkeypatch.setattr(trial, "_migrate_verified_trial_copy", migration_probe)
    with pytest.raises(
        trial.TrialSafetyError,
        match="prepared trial unbound companion safety boundary rejected",
    ) as captured:
        trial.migrate_prepared_trial_copy(prepared)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert migration_calls == 1
    assert companion_before is not None
    assert _path_entry_evidence(companion) == companion_before
    assert _main_file_identity(prepared.destination) == main_before
    _assert_v1_schema_unchanged(prepared.destination)


@pytest.mark.parametrize(
    ("suffix", "content"),
    [
        ("-journal", b"synthetic-recovery-journal"),
        ("-shm", b"\1" * 32768),
    ],
)
def test_migration_rejects_recovery_journal_or_shm_drift_without_mutation(
    tmp_path,
    monkeypatch,
    suffix,
    content,
):
    source = _create_v1_source_database(tmp_path / "source-v1.db", tmp_path)
    prepared = trial.prepare_trial_copy(source, temp_root=tmp_path)
    main_before = _main_file_identity(prepared.destination)
    recovery_before = _main_file_identity(prepared.recovery_image)
    companion = Path(f"{prepared.recovery_image}{suffix}")
    _replace_sidecar(companion, content)
    companion_before = _path_entry_evidence(companion)
    migration_calls = 0

    def migration_probe(candidate):
        nonlocal migration_calls
        migration_calls += 1
        return {"applied": 2}

    monkeypatch.setattr(trial, "_migrate_verified_trial_copy", migration_probe)
    with pytest.raises(trial.TrialSafetyError) as captured:
        trial.migrate_prepared_trial_copy(prepared)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert migration_calls == 0
    assert _path_entry_evidence(companion) == companion_before
    assert _main_file_identity(prepared.destination) == main_before
    assert _main_file_identity(prepared.recovery_image) == recovery_before
    _assert_v1_schema_unchanged(prepared.destination)


def test_migration_rejects_owner_mismatch_probe(tmp_path, monkeypatch):
    source = _create_source_database(tmp_path / "source.db")
    prepared = trial.prepare_trial_copy(source, temp_root=tmp_path)
    actual_uid = os.getuid()
    monkeypatch.setattr(trial.os, "getuid", lambda: actual_uid + 1)
    with pytest.raises(trial.TrialSafetyError):
        trial.migrate_prepared_trial_copy(prepared)


def test_prepare_and_migration_reject_nonregular_or_raw_paths(tmp_path):
    fifo = tmp_path / "source.fifo"
    os.mkfifo(fifo, mode=0o600)
    with pytest.raises(trial.TrialSafetyError):
        trial.prepare_trial_copy(fifo, temp_root=tmp_path)

    raw = _create_source_database(tmp_path / "raw.db")
    raw_before = trial.file_sha256(raw)
    with pytest.raises(trial.TrialSafetyError):
        trial.migrate_exact_copy(raw)
    assert trial.file_sha256(raw) == raw_before
    raw_hardlink = tmp_path / "raw-hardlink.db"
    os.link(raw, raw_hardlink)
    with pytest.raises(trial.TrialSafetyError):
        trial.migrate_exact_copy(raw_hardlink)
    assert trial.file_sha256(raw_hardlink) == raw_before

    prepared = trial.prepare_trial_copy(raw, temp_root=tmp_path)
    prepared.destination.unlink()
    os.mkfifo(prepared.destination, mode=0o600)
    with pytest.raises(trial.TrialSafetyError):
        trial.migrate_prepared_trial_copy(prepared)


def test_payload_hash_is_fixture_content_not_observation_time():
    document = _fixture()
    assert trial._fixture_payload_hash(document) == trial._fixture_payload_hash(
        json.loads(json.dumps(document))
    )
