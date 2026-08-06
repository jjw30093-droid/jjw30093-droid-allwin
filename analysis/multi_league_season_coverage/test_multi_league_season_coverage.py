from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from analysis.multi_league_season_coverage.multi_league_season_coverage import (
    COMPETITIONS,
    PILOT_KEYS,
    REQUIRED_ARTIFACTS,
    ArtifactValidationError,
    CompetitionSpec,
    CoverageProbe,
    CoverageProbeError,
    DurableArtifactStore,
    RequestBudgetExhausted,
    SeasonKind,
    deterministic_samples,
    discover_a_league_id,
    extract_available_seasons,
    extract_match_coverage,
    main,
    safe_start_rows,
    season_kind_for_advertised_label,
    validate_schedule,
    verify_competition_identity,
)
from backend.season_resolver import (
    SeasonResolutionError,
    SeasonVerification,
    canonicalize_provider_season,
    provider_season_from_canonical,
    resolve_current_season,
    resolve_provider_season,
)


def _seasons(spec: CompetitionSpec) -> list[str]:
    if spec.season_kind is SeasonKind.CALENDAR_YEAR:
        return ["2024", "2025"]
    return ["2023/2024", "2024/2025"]


def _fixture(match_id: int, day: int) -> dict:
    return {
        "id": match_id,
        "home": {"id": match_id + 1000, "name": f"Home {match_id}"},
        "away": {"id": match_id + 2000, "name": f"Away {match_id}"},
        "status": {
            "utcTime": f"2025-01-{day:02d}T12:00:00Z",
            "finished": True,
            "cancelled": False,
            "status": "FT",
        },
        "stage": "Regular Season",
        "round": f"Round {day}",
        "group": None,
    }


def _schedule(spec: CompetitionSpec, season: str) -> dict:
    competition_id = spec.competition_id or 1001
    return {
        "details": {
            "id": competition_id,
            "name": spec.expected_name,
            "selectedSeason": season,
            "allAvailableSeasons": _seasons(spec),
        },
        "fixtures": {
            "allMatches": [
                _fixture(competition_id * 10000 + index, index)
                for index in range(1, 7)
            ]
        },
    }


def _discovery(spec: CompetitionSpec) -> dict:
    value = _schedule(spec, _seasons(spec)[-1])
    value["details"].pop("selectedSeason")
    return value


def _match_details(match_id: int) -> dict:
    return {
        "general": {
            "matchId": match_id,
            "homeTeam": {"id": match_id + 1000},
            "awayTeam": {"id": match_id + 2000},
        },
        "content": {
            "stats": {
                "Periods": {
                    "All": {
                        "stats": [
                            {
                                "title": "Top stats",
                                "stats": [
                                    {
                                        "key": "expected_goals",
                                        "title": "Expected goals",
                                        "stats": [1.2, 0.8],
                                    },
                                    {
                                        "key": "BallPossession",
                                        "stats": ["55%", "45%"],
                                    },
                                    {
                                        "key": "AccuratePasses",
                                        "stats": ["400 (85%)", "310 (78%)"],
                                    },
                                    {"key": "BigChances", "stats": [3, 1]},
                                    {"key": "Corners", "stats": [5, 2]},
                                    {"key": "Clearances", "stats": [12, 18]},
                                ],
                            }
                        ]
                    }
                }
            },
            "shotmap": {
                "shots": [
                    {
                        "id": 1,
                        "expectedGoals": 0.25,
                        "expectedGoalsOnTarget": 0.4,
                        "eventType": "AttemptSaved",
                    },
                    {
                        "id": 2,
                        "expectedGoals": 0.1,
                        "expectedGoalsOnTarget": None,
                        "eventType": "Miss",
                    },
                ]
            },
            "matchFacts": {"events": [{"type": "Goal", "time": 30}]},
            "lineup": {"lineup": [{"players": [{"id": 9, "position": "FW"}]}]},
            "playerStats": {
                "9": {
                    "position": "FW",
                    "minutesPlayed": 90,
                    "stats": [
                        {
                            "title": "Top stats",
                            "stats": [
                                {"key": "rating_title", "stat": {"value": 8.1}}
                            ],
                        }
                    ],
                }
            },
        },
    }


class FakeTransport:
    def __init__(self, *, failures: int = 0, mismatch: set[str] | None = None):
        self.calls: list[tuple[str, object]] = []
        self.failures = failures
        self.mismatch = mismatch or set()

    def league_matches(self, competition_id: int, season: str = "") -> dict:
        self.calls.append(("league", (competition_id, season)))
        if self.failures:
            self.failures -= 1
            raise RuntimeError("synthetic provider failure with /tmp/private and URL")
        spec = next(
            (
                value
                for value in COMPETITIONS.values()
                if value.competition_id == competition_id
            ),
            COMPETITIONS["a_league"],
        )
        raw = _schedule(spec, season) if season else _discovery(spec)
        if spec.key in self.mismatch:
            raw["details"]["name"] = "Wrong competition"
        return raw

    def match_details(self, match_id: int) -> dict:
        self.calls.append(("match", match_id))
        return _match_details(match_id)

    def search_competitions(self, term: str) -> dict:
        self.calls.append(("search", term))
        return {
            "suggestions": [
                {"type": "league", "name": "A-League Men", "id": 1001}
            ]
        }


class ExplodingTransport:
    def league_matches(self, competition_id: int, season: str = "") -> dict:
        raise AssertionError("replay attempted network")

    def match_details(self, match_id: int) -> dict:
        raise AssertionError("replay attempted network")

    def search_competitions(self, term: str) -> dict:
        raise AssertionError("replay attempted network")


def _store(tmp_path: Path, mode: str = "live", budget: int = 100) -> DurableArtifactStore:
    return DurableArtifactStore(
        tmp_path, "run-1", max_attempts=budget, mode=mode
    )


def test_competition_registry_contains_required_scope() -> None:
    assert set(PILOT_KEYS) == {"mls", "championship", "champions_league"}
    assert {
        "j1", "k_league_1", "a_league", "eredivisie", "liga_portugal",
        "brazil_serie_a", "europa_league", "conference_league",
        "premier_league_control", "eliteserien_control", "allsvenskan_control",
    } <= set(COMPETITIONS)
    assert COMPETITIONS["conference_league"].earliest_year == 2021
    assert COMPETITIONS["a_league"].competition_id is None
    assert COMPETITIONS["a_league"].expected_name == "A-League"
    assert COMPETITIONS["brazil_serie_a"].expected_name == "Serie A"


@pytest.mark.parametrize(
    ("kind", "provider", "canonical"),
    [
        (SeasonKind.CALENDAR_YEAR, "2025", "2025"),
        (SeasonKind.CROSS_YEAR, "2024/2025", "2024/25"),
        (SeasonKind.TOURNAMENT_SEASON, "2024/2025", "2024/2025"),
        (SeasonKind.TOURNAMENT_SEASON, "2025", "2025"),
    ],
)
def test_season_label_conversion_is_deterministic(
    kind: SeasonKind, provider: str, canonical: str
) -> None:
    assert canonicalize_provider_season(kind, provider) == canonical
    assert provider_season_from_canonical(kind, canonical) == provider


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        (SeasonKind.CALENDAR_YEAR, "2024/2025"),
        (SeasonKind.CROSS_YEAR, "2024"),
        (SeasonKind.CROSS_YEAR, "2024/2026"),
        (SeasonKind.TOURNAMENT_SEASON, "season-current"),
    ],
)
def test_invalid_season_labels_fail_with_fixed_safe_error(
    kind: SeasonKind, value: str
) -> None:
    with pytest.raises(
        SeasonResolutionError, match=r"^invalid season resolution input$"
    ):
        canonicalize_provider_season(kind, value)


def test_current_cross_year_season_is_never_guessed_from_month() -> None:
    for month in (1, 7, 12):
        result = resolve_current_season(
            competition_id=48,
            season_kind=SeasonKind.CROSS_YEAR,
            now=datetime(2026, month, 1, tzinfo=timezone.utc),
        )
        assert result.verification_status is SeasonVerification.SEASON_UNVERIFIED
        assert result.provider_season_parameter is None


def test_current_calendar_season_preserves_eliteserien_behavior() -> None:
    result = resolve_current_season(
        competition_id=59,
        season_kind=SeasonKind.CALENDAR_YEAR,
        now=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )
    assert result.verification_status is SeasonVerification.VERIFIED
    assert result.provider_season_parameter == "2026"
    assert result.canonical_season_key == "2026"


def test_advertised_j1_cross_transition_is_not_guessed_or_discarded() -> None:
    assert season_kind_for_advertised_label(
        SeasonKind.CALENDAR_YEAR, "2026"
    ) is SeasonKind.CALENDAR_YEAR
    assert season_kind_for_advertised_label(
        SeasonKind.CALENDAR_YEAR, "2026/2027"
    ) is SeasonKind.TOURNAMENT_SEASON
    spec = COMPETITIONS["j1"]
    raw = _schedule(spec, "2026/2027")
    raw["details"]["allAvailableSeasons"] = ["2026", "2026/2027"]
    fixtures, season, _ = validate_schedule(
        raw, spec, "2026/2027", ["2026", "2026/2027"]
    )
    assert len(fixtures) == 6
    assert season["season_kind"] == "tournament_season"
    assert season["verification_status"] == "VERIFIED"


def test_cross_year_requires_advertised_or_reviewed_mapping() -> None:
    unresolved = resolve_current_season(
        competition_id=48,
        season_kind=SeasonKind.CROSS_YEAR,
        now=datetime(2026, 7, 30, tzinfo=timezone.utc),
        explicit_canonical_season="2025/26",
        advertised_provider_seasons=["2024/2025"],
    )
    assert unresolved.verification_status is SeasonVerification.SEASON_UNVERIFIED
    verified = resolve_current_season(
        competition_id=48,
        season_kind=SeasonKind.CROSS_YEAR,
        now=datetime(2026, 7, 30, tzinfo=timezone.utc),
        explicit_canonical_season="2025/26",
        advertised_provider_seasons=["2025/2026"],
    )
    assert verified.verification_status is SeasonVerification.VERIFIED
    assert verified.provider_season_parameter == "2025/2026"


def test_returned_season_mismatch_fails_closed() -> None:
    result = resolve_provider_season(
        competition_id=48,
        season_kind=SeasonKind.CROSS_YEAR,
        canonical_season="2024/25",
        provider_season="2024/2025",
        returned_season="2023/2024",
        available_provider_seasons=["2024/2025"],
    )
    assert result.verification_status is SeasonVerification.SEASON_MISMATCH


def test_extract_available_seasons_uses_explicit_provider_shapes() -> None:
    raw = {
        "details": {
            "allAvailableSeasons": [
                {"name": "2023/2024"},
                {"season": "2024/2025"},
                "2022/2023",
                {"unrelated": "ignored"},
            ]
        }
    }
    assert extract_available_seasons(raw) == (
        "2022/2023", "2023/2024", "2024/2025"
    )


def test_identity_is_strict_on_id_and_name() -> None:
    spec = COMPETITIONS["mls"]
    raw = _schedule(spec, "2025")
    assert verify_competition_identity(raw, spec)["status"] == "IDENTITY_VERIFIED"
    raw["details"]["id"] = 999
    assert verify_competition_identity(raw, spec)["status"] == "IDENTITY_MISMATCH"
    raw["details"]["id"] = 130
    raw["details"]["name"] = "Not MLS"
    assert verify_competition_identity(raw, spec)["status"] == "IDENTITY_MISMATCH"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda raw: raw["details"].update({"selectedSeason": "2024"}),
         "season identity is not verified"),
        (lambda raw: raw["fixtures"].update({"hasMore": True}),
         "schedule completeness is not verified"),
        (lambda raw: raw["fixtures"].update({"currentPage": 1, "pageCount": 9}),
         "schedule completeness is not verified"),
        (lambda raw: raw["fixtures"]["allMatches"].clear(),
         "schedule fixtures are unavailable"),
        (lambda raw: raw["fixtures"]["allMatches"][1].update(
            {"id": raw["fixtures"]["allMatches"][0]["id"]}
        ), "schedule fixture is invalid"),
        (lambda raw: raw["fixtures"]["allMatches"][0]["status"].update(
            {"utcTime": "invalid"}
        ), "schedule fixture is invalid"),
        (lambda raw: raw["fixtures"]["allMatches"][0]["home"].update({"id": None}),
         "schedule fixture is invalid"),
        (lambda raw: raw["fixtures"]["allMatches"][0]["status"].update(
            {"finished": True, "cancelled": True}
        ), "schedule fixture is invalid"),
    ],
)
def test_schedule_gate_fails_closed(mutation, message: str) -> None:
    spec = COMPETITIONS["mls"]
    raw = _schedule(spec, "2025")
    mutation(raw)
    with pytest.raises(CoverageProbeError, match=f"^{message}$"):
        validate_schedule(raw, spec, "2025", ["2024", "2025"])


def test_valid_schedule_records_structure_and_no_pagination() -> None:
    spec = COMPETITIONS["championship"]
    fixtures, season, pagination = validate_schedule(
        _schedule(spec, "2024/2025"),
        spec,
        "2024/2025",
        ["2023/2024", "2024/2025"],
    )
    assert len(fixtures) == 6
    assert season["canonical_season_key"] == "2024/25"
    assert season["verification_status"] == "VERIFIED"
    assert pagination["status"] == "NOT_DETECTED"
    assert fixtures[0]["stage"] == "Regular Season"
    assert fixtures[0]["round"] == "Round 1"


def test_provider_abandoned_finished_cancelled_is_accepted_but_not_sampled() -> None:
    spec = COMPETITIONS["championship"]
    raw = _schedule(spec, "2024/2025")
    raw["fixtures"]["allMatches"][0]["status"].update(
        {
            "finished": True,
            "cancelled": True,
            "reason": {
                "long": "Abandoned",
                "longKey": "aborted",
                "short": "Ab",
                "shortKey": "aborted_short",
            },
        }
    )
    fixtures, _, _ = validate_schedule(
        raw, spec, "2024/2025", ["2023/2024", "2024/2025"]
    )
    sampled_ids = {row["match_id"] for row in deterministic_samples(fixtures)}
    assert fixtures[0]["cancelled"] is True
    assert fixtures[0]["match_id"] not in sampled_ids


def test_deterministic_sample_is_early_middle_late_and_order_independent() -> None:
    rows = [
        {
            "match_id": index,
            "kickoff_utc": f"2025-01-{index:02d}T00:00:00Z",
            "finished": True,
            "cancelled": False,
        }
        for index in range(1, 10)
    ]
    expected = (1, 5, 9)
    assert tuple(row["match_id"] for row in deterministic_samples(rows)) == expected
    assert tuple(
        row["match_id"] for row in deterministic_samples(reversed(rows))
    ) == expected


def test_cancelled_and_unfinished_matches_are_excluded_from_samples() -> None:
    rows = [
        {"match_id": 1, "kickoff_utc": "2025-01-01T00:00:00Z",
         "finished": True, "cancelled": False},
        {"match_id": 2, "kickoff_utc": "2025-01-02T00:00:00Z",
         "finished": True, "cancelled": True},
        {"match_id": 3, "kickoff_utc": "2025-01-03T00:00:00Z",
         "finished": False, "cancelled": False},
        {"match_id": 4, "kickoff_utc": "2025-01-04T00:00:00Z",
         "finished": True, "cancelled": False},
        {"match_id": 5, "kickoff_utc": "2025-01-05T00:00:00Z",
         "finished": True, "cancelled": False},
    ]
    assert tuple(row["match_id"] for row in deterministic_samples(rows)) == (1, 4, 5)


def test_match_coverage_separates_presence_zero_positive_and_physical() -> None:
    rows = extract_match_coverage(
        _match_details(42),
        competition_key="mls",
        provider_season="2025",
        match_id=42,
    )
    by_field = {row["field"]: row for row in rows}
    assert by_field["team_xg"]["present_count"] == 1
    assert by_field["team_xga"]["present_count"] == 1
    assert by_field["shot_xg"]["positive"] == 2
    assert by_field["shot_xgot"]["positive"] == 1
    assert by_field["possession"]["positive"] == 2
    assert by_field["lineups"]["present_count"] == 1
    assert by_field["events"]["present_count"] == 1
    assert by_field["top_speed"]["present_count"] == 0
    assert by_field["distance_covered"]["present_count"] == 0


def test_safe_start_requires_two_consecutive_seasons() -> None:
    rows = [
        {
            "competition": "mls", "field": "shot_xg", "category": "shot",
            "provider_season": "2022", "coverage": 0.0, "present_count": 0,
        },
        {
            "competition": "mls", "field": "shot_xg", "category": "shot",
            "provider_season": "2023", "coverage": 1.0, "present_count": 3,
        },
        {
            "competition": "mls", "field": "shot_xg", "category": "shot",
            "provider_season": "2024", "coverage": 1.0, "present_count": 3,
        },
    ]
    result = safe_start_rows(rows)[0]
    assert result["first_seen"] == "2023"
    assert result["first_usable"] == "2023"
    assert result["first_contiguous_safe"] == "2023"
    assert result["confidence"] == "SAMPLED_SAFE"
    assert result["breaks"] == "2022"


def test_one_usable_season_is_only_provisional() -> None:
    rows = [
        {
            "competition": "mls", "field": "shot_xg", "category": "shot",
            "provider_season": "2024", "coverage": 0.0, "present_count": 0,
        },
        {
            "competition": "mls", "field": "shot_xg", "category": "shot",
            "provider_season": "2025", "coverage": 1.0, "present_count": 3,
        },
    ]
    result = safe_start_rows(rows)[0]
    assert result["first_contiguous_safe"] == ""
    assert result["confidence"] == "PROVISIONAL"


def test_a_league_discovery_requires_one_exact_name_and_id() -> None:
    assert discover_a_league_id(
        {"items": [{"name": "A-League Men", "id": 1001}]}
    ) == 1001
    assert discover_a_league_id(
        {
            "items": [
                {"name": "A-League Men", "id": 1001},
                {"name": "A-League Men", "id": 1002},
            ]
        }
    ) is None
    assert discover_a_league_id(
        {"items": [{"name": "A-League Women", "id": 1001}]}
    ) is None
    assert discover_a_league_id(
        {"items": [{"name": "A-League", "id": 1001}]}
    ) == 1001


def test_request_budget_rejects_before_transport(tmp_path: Path) -> None:
    transport = FakeTransport()
    store = _store(tmp_path, budget=0)
    with pytest.raises(RequestBudgetExhausted, match=r"^request budget exhausted$"):
        store.acquire("request-1", "league_schedule", lambda: transport.league_matches(130))
    assert transport.calls == []
    assert store.attempts == 0


def test_retry_is_durable_and_safe_error_hides_original_exception(tmp_path: Path) -> None:
    transport = FakeTransport(failures=2)
    store = _store(tmp_path)
    with pytest.raises(CoverageProbeError, match=r"^provider request failed$") as caught:
        store.acquire("request-1", "league_schedule", lambda: transport.league_matches(130))
    assert "/tmp/private" not in str(caught.value)
    assert "URL" not in str(caught.value)
    rows = store.ledger()
    assert [row["phase"] for row in rows] == [
        "STARTED", "FAILED", "STARTED", "FAILED"
    ]
    assert all(row.get("error") in (None, "provider request failed") for row in rows)


def test_successful_retry_and_resume_reuses_artifact(tmp_path: Path) -> None:
    transport = FakeTransport(failures=1)
    store = _store(tmp_path)
    raw = store.acquire(
        "mls.discovery", "league_schedule",
        lambda: transport.league_matches(130),
    )
    assert raw["details"]["id"] == 130
    assert store.attempts == 2
    call_count = len(transport.calls)
    resumed = store.acquire(
        "mls.discovery", "league_schedule",
        lambda: transport.league_matches(130),
    )
    assert resumed == raw
    assert len(transport.calls) == call_count


def test_replay_never_calls_transport_and_detects_tampering(tmp_path: Path) -> None:
    live = _store(tmp_path)
    live.acquire("mls.discovery", "league_schedule", lambda: _discovery(COMPETITIONS["mls"]))
    replay = _store(tmp_path, mode="replay")
    value = replay.acquire(
        "mls.discovery", "league_schedule",
        lambda: ExplodingTransport().league_matches(130),
    )
    assert value["details"]["id"] == 130
    artifact = replay.raw_dir / "mls.discovery.json"
    artifact.write_text("{}", encoding="utf-8")
    with pytest.raises(
        ArtifactValidationError, match=r"^saved artifact checksum mismatch$"
    ):
        replay.acquire("mls.discovery", "league_schedule", lambda: {})


def test_private_run_files_are_not_group_or_world_readable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.acquire("mls.discovery", "league_schedule", lambda: _discovery(COMPETITIONS["mls"]))
    assert os.stat(store.run_dir).st_mode & 0o077 == 0
    assert os.stat(store.raw_dir).st_mode & 0o077 == 0
    assert os.stat(store.ledger_path).st_mode & 0o077 == 0
    assert os.stat(store.raw_dir / "mls.discovery.json").st_mode & 0o077 == 0


def test_pilot_run_outputs_all_artifacts_and_replay_is_zero_network(
    tmp_path: Path,
) -> None:
    transport = FakeTransport()
    live_store = _store(tmp_path, budget=100)
    live_result = CoverageProbe(live_store, transport).run(PILOT_KEYS)
    assert live_result["p1"] == 0
    assert live_store.attempts == 27
    assert all((live_store.run_dir / name).is_file() for name in REQUIRED_ARTIFACTS)
    ledger_before = live_store.ledger_path.read_bytes()
    replay_store = _store(tmp_path, mode="replay", budget=100)
    replay_result = CoverageProbe(replay_store, ExplodingTransport()).run(PILOT_KEYS)
    assert replay_result["p1"] == 0
    assert replay_store.ledger_path.read_bytes() == ledger_before
    assert replay_store.attempts == 27


def test_single_season_filter_limits_requests_and_output(tmp_path: Path) -> None:
    transport = FakeTransport()
    store = _store(tmp_path)
    result = CoverageProbe(store, transport).run(["mls"], ["2025"])
    assert result["p1"] == 0
    with (store.run_dir / "competition-season-summary.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert [row["provider_season"] for row in rows] == ["2025"]
    assert store.attempts == 5


def test_one_competition_failure_does_not_stop_the_next(tmp_path: Path) -> None:
    transport = FakeTransport(mismatch={"mls"})
    store = _store(tmp_path)
    result = CoverageProbe(store, transport).run(["mls", "championship"])
    assert result["p1"] == 1
    assert result["competitions"]["mls"]["status"] == "FAILED"
    assert result["competitions"]["championship"]["status"] == "COMPLETED"


def test_a_league_identity_is_discovered_then_reverified(tmp_path: Path) -> None:
    transport = FakeTransport()
    store = _store(tmp_path)
    result = CoverageProbe(store, transport).run(["a_league"])
    assert result["p1"] == 0
    assert result["competitions"]["a_league"]["competition_id"] == 1001
    assert transport.calls[0] == ("search", "A-League Men")


def test_manifest_hashes_every_report_artifact(tmp_path: Path) -> None:
    store = _store(tmp_path)
    CoverageProbe(store, FakeTransport()).run(["mls"], ["2025"])
    manifest = json.loads(
        (store.run_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert set(manifest["artifacts"]) == set(REQUIRED_ARTIFACTS) - {"manifest.json"}
    assert all(
        len(value["sha256"]) == 64 and value["size"] > 0
        for value in manifest["artifacts"].values()
    )


def test_dry_run_is_zero_network_and_records_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_transport():
        raise AssertionError("dry-run constructed live transport")

    monkeypatch.setattr(
        "analysis.multi_league_season_coverage.multi_league_season_coverage.LiveFotMobTransport",
        forbidden_transport,
    )
    exit_code = main(
        [
            "--dry-run", "--output-root", str(tmp_path), "--run-id", "dry-1",
            "--competition", "mls", "--season", "2025",
        ]
    )
    assert exit_code == 0
    manifest = json.loads(
        (tmp_path / "dry-1" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["selected_competitions"] == ["mls"]
    assert manifest["selected_seasons"] == ["2025"]
    assert manifest["attempts"] == 0


def test_no_test_escape_hatches_are_embedded_in_module() -> None:
    source = Path(__file__).with_name(
        "multi_league_season_coverage.py"
    ).read_text(encoding="utf-8")
    assert "pytest.skip" not in source
    assert "pytest.mark.xfail" not in source
    assert "filterwarnings" not in source
    assert "THORDATA_PROXY" not in source
