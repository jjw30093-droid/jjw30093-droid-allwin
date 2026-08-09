from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from backend.cli.daily_content import main
from backend.content_pipeline import (
    LEAGUES,
    DailyContentError,
    ImmutableArtifactRun,
    RequestBudget,
    _build_assembly_context,
    _discover_nowgoal_match,
    _fixture_candidates,
    _poll_decision,
    load_runtime_config,
    mark_fresh_failure,
)


def _odds_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE poll_state (
             source TEXT NOT NULL,
             subject TEXT NOT NULL,
             last_polled_at TEXT NOT NULL,
             last_poll_run_id TEXT,
             updated_at TEXT NOT NULL,
             PRIMARY KEY(source,subject))"""
    )
    return conn


def test_league_configs_keep_content_and_polling_windows_separate() -> None:
    eliteserien = LEAGUES["eliteserien"]
    assert eliteserien.fotmob_competition_id == 59
    assert eliteserien.expected_competition_name == "Eliteserien"
    assert eliteserien.content_horizon_days == 7
    assert eliteserien.odds_high_frequency_hours == 72
    assert eliteserien.odds_far_interval_seconds == 900
    assert eliteserien.odds_near_interval_seconds == 300
    assert {"allsvenskan", "mls"} <= set(LEAGUES)
    assert all(config.identity_evidence for config in LEAGUES.values())


def test_runtime_defaults_are_durable_repo_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "ALLWIN_DATA_DIR",
        "ALLWIN_ARTIFACT_DIR",
        "ALLWIN_STUDIO_OUTPUT_DIR",
        "ALLWIN_CONTENT_HORIZON_DAYS",
        "ALLWIN_MAX_REQUEST_ATTEMPTS",
        "ALLWIN_API_BASE",
    ):
        monkeypatch.delenv(name, raising=False)
    config = load_runtime_config()
    assert config.data_dir.name == "data"
    assert config.data_dir.parent.name == "runtime"
    assert config.artifact_dir.parent.name == "runtime"
    assert config.studio_output_dir.parent.name == "runtime"
    assert config.content_horizon_days == 7


def test_request_budget_is_a_hard_transport_ceiling() -> None:
    budget = RequestBudget(2)
    budget.consume("league")
    budget.consume("odds")
    with pytest.raises(DailyContentError, match="budget exhausted"):
        budget.consume("team")
    assert budget.attempts == 2


def test_content_selection_prefers_72h_but_accepts_full_7_day_horizon() -> None:
    payload = {
        "fixtures": {
            "allMatches": [
                {
                    "id": 11,
                    "round": 4,
                    "home": {"id": 1, "name": "A"},
                    "away": {"id": 2, "name": "B"},
                    "status": {"utcTime": "2026-08-03T12:00:00Z"},
                },
                {
                    "id": 12,
                    "round": 5,
                    "home": {"id": 3, "name": "C"},
                    "away": {"id": 4, "name": "D"},
                    "status": {"utcTime": "2026-07-30T12:00:00Z"},
                },
            ]
        }
    }
    rows = _fixture_candidates(
        payload,
        config=LEAGUES["eliteserien"],
        now=datetime(2026, 7, 28, 0, 0, tzinfo=timezone.utc),
        horizon_days=7,
    )
    assert [row["match_id"] for row in rows] == [12, 11]
    assert rows[0]["within_72h"] is True
    assert rows[1]["within_72h"] is False


def test_verified_nowgoal_mapping_requires_direction_and_exact_kickoff() -> None:
    candidate = {
        "match_id": 5104968,
        "kickoff_at_utc": "2026-07-31T17:00:00Z",
        "home_id": 8007,
        "away_id": 8448,
        "home_name": "Vålerenga",
        "away_name": "Hamarkameratene",
    }
    rows = [
        {
            "titan_id": "2912857",
            "home_name": "Valerenga",
            "away_name": "Ham-Kam",
            "kickoff": "2026-07-31 17:00:00",
        }
    ]
    result = _discover_nowgoal_match(candidate, rows, LEAGUES["eliteserien"])
    assert result is not None
    assert result["confidence"] == 1.0
    assert result["kickoff_diff_seconds"] == 0

    rows[0]["home_name"], rows[0]["away_name"] = rows[0]["away_name"], rows[0]["home_name"]
    assert _discover_nowgoal_match(candidate, rows, LEAGUES["eliteserien"]) is None


def test_other_league_fixture_falls_back_to_live_candidate_team_names() -> None:
    candidate = {
        "match_id": 5104999,
        "kickoff_at_utc": "2026-08-02T15:00:00Z",
        "home_id": 91001,
        "away_id": 91002,
        "home_name": "Bodø/Glimt",
        "away_name": "Lillestrøm",
    }
    rows = [
        {
            "titan_id": "2999999",
            "home_name": "Bodo/Glimt",
            "away_name": "Lillestrom",
            "kickoff": "2026-08-02 15:00:00",
        }
    ]
    result = _discover_nowgoal_match(candidate, rows, LEAGUES["eliteserien"])
    assert result is not None
    assert result["titan_id"] == "2999999"
    assert result["kickoff_diff_seconds"] == 0

    rows[0]["home_name"], rows[0]["away_name"] = rows[0]["away_name"], rows[0]["home_name"]
    assert _discover_nowgoal_match(candidate, rows, LEAGUES["eliteserien"]) is None


def test_assembly_context_uses_selected_match_not_mvp_sample_constants() -> None:
    selected = {
        "match_id": 5104999,
        "home_id": 91001,
        "away_id": 91002,
        "home_name": "Bodø/Glimt",
        "away_name": "Lillestrøm",
    }
    mapping = {
        "titan_id": "2999999",
        "provider_home_id": "701",
        "provider_away_id": "702",
    }
    context = _build_assembly_context(
        LEAGUES["eliteserien"],
        season="2026",
        selected=selected,
        mapping=mapping,
    )
    assert context.match_id == 5104999
    assert context.nowgoal_match_id == "2999999"
    assert context.home_id == 91001
    assert context.away_id == 91002
    assert context.home_name_zh == "Bodø/Glimt"
    assert context.away_name_zh == "Lillestrøm"
    assert context.nowgoal_home_id == "701"
    assert context.nowgoal_away_id == "702"


@pytest.mark.parametrize(
    ("kickoff", "has_observation", "expected_due", "phase", "interval"),
    [
        ("2026-08-05T00:00:00Z", False, True, "INITIAL_LOW_FREQUENCY", None),
        ("2026-08-05T00:00:00Z", True, False, "WAITING_FOR_72H_WINDOW", None),
        ("2026-07-30T00:00:00Z", True, True, "T_MINUS_72H_TO_2H", 900),
        ("2026-07-28T01:00:00Z", True, True, "T_MINUS_2H_TO_KICKOFF", 300),
        ("2026-07-27T23:59:00Z", True, False, "CLOSED", None),
    ],
)
def test_odds_due_policy(
    kickoff: str,
    has_observation: bool,
    expected_due: bool,
    phase: str,
    interval: int | None,
) -> None:
    conn = _odds_connection()
    try:
        result = _poll_decision(
            kickoff,
            now_iso="2026-07-28T00:00:00Z",
            has_observation=has_observation,
            conn=conn,
            match_id=5104968,
        )
    finally:
        conn.close()
    assert result["due"] is expected_due
    assert result["phase"] == phase
    assert result.get("interval_seconds") == interval


def test_immutable_artifact_writer_refuses_overwrite(tmp_path) -> None:
    run = ImmutableArtifactRun(tmp_path, "eliteserien", "2026-07-28T00:00:00Z")
    path = run.write_bytes("response.json", b'{"ok":true}', role="raw")
    assert path.stat().st_mode & 0o777 == 0o440
    with pytest.raises(FileExistsError):
        run.write_bytes("response.json", b"changed", role="raw")


def test_failure_status_keeps_previous_success_and_marks_stale(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "content_status.json").write_text(
        json.dumps(
            {
                "state": "LIVE",
                "last_success_sync_at": "2026-07-27T00:00:00Z",
                "run_id": "successful-run",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ALLWIN_DATA_DIR", str(data))
    monkeypatch.setenv("ALLWIN_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("ALLWIN_STUDIO_OUTPUT_DIR", str(tmp_path / "studio"))
    runtime = load_runtime_config()
    status = mark_fresh_failure(
        runtime, league_key="eliteserien", failure_type="FotMobTransportError"
    )
    assert status["state"] == "STALE"
    assert status["last_success_sync_at"] == "2026-07-27T00:00:00Z"
    assert status["run_id"] == "successful-run"


def test_due_dry_run_on_empty_runtime_never_needs_network(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("ALLWIN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ALLWIN_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("ALLWIN_STUDIO_OUTPUT_DIR", str(tmp_path / "studio"))
    assert main(["--league", "eliteserien", "--due", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert '"network_requests": 0' in output


def test_modes_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        main(["--league", "eliteserien", "--fresh", "--replay"])
