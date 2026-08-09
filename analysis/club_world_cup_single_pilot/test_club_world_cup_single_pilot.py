"""Permanent offline gates for the Club World Cup single-competition pilot."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import stat

import pytest

from analysis.club_world_cup_single_pilot import club_world_cup_single_pilot as pilot


TEAM_ID = pilot.TARGET_TEAM_ID
WYDAD_ID = 10180
COMPETITION_ID = 9999
MATCH_ID = 5000001


def _match(
    match_id=MATCH_ID,
    *,
    home_id=TEAM_ID,
    away_id=WYDAD_ID,
    home_name="Manchester City",
    away_name="Wydad AC",
    utc="2025-06-18T16:00:00.000Z",
):
    return {
        "id": match_id,
        "home": {"id": home_id, "name": home_name},
        "away": {"id": away_id, "name": away_name},
        "status": {
            "utcTime": utc,
            "finished": True,
            "cancelled": False,
            "started": True,
        },
        "round": "1",
    }


def _daily(*matches, competition_id=COMPETITION_ID, name="Club World Cup"):
    return {
        "date": "2025-06-19",
        "leagues": [{
            "primaryId": competition_id,
            "name": name,
            "matches": list(matches or (_match(),)),
        }],
    }


def _competition(
    season,
    *matches,
    competition_id=COMPETITION_ID,
    name="Club World Cup",
    fixture_metadata=None,
):
    fixtures = {"allMatches": list(matches or (_match(),))}
    fixtures.update(fixture_metadata or {})
    return {
        "details": {
            "id": competition_id,
            "name": name,
            "selectedSeason": season,
        },
        "fixtures": fixtures,
    }


def _discovery_match():
    result = pilot.analyze_discovery(_daily(_match()))
    assert result["ok"] is True
    return result["match"]


class FakeClient:
    def __init__(self, daily, primary, comparison):
        self.daily = daily
        self.primary = primary
        self.comparison = comparison
        self.calls = []

    def check_ip(self):
        raise AssertionError("check_ip must never be called")

    def daily_matches(self, date):
        self.calls.append(("daily_matches", date))
        if isinstance(self.daily, BaseException):
            raise self.daily
        return self.daily

    def league_matches(self, competition_id, season):
        self.calls.append(("league_matches", competition_id, season))
        value = self.primary if season == "2025" else self.comparison
        if isinstance(value, BaseException):
            raise value
        return value


def _artifact_snapshot(root):
    snapshot = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        file_stat = path.stat()
        snapshot[str(path.relative_to(root))] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": file_stat.st_size,
            "mode": stat.S_IMODE(file_stat.st_mode),
            "mtime_ns": file_stat.st_mtime_ns,
        }
    return snapshot


def _write_completed_artifacts(output_dir):
    pilot._private_json_write(
        output_dir / "raw" / "daily_20250619.json",
        _daily(
            _match(home_name="Man City", away_name="Wydad Casablanca"),
            name="FIFA Club World Cup Grp. G",
        ),
    )
    pilot._private_json_write(
        output_dir / "raw" / "competition_9999_2025.json",
        _competition("2025", _match()),
    )
    pilot._private_json_write(
        output_dir / "raw" / "competition_9999_2023.json",
        _competition(
            "2023",
            _match(match_id=MATCH_ID - 1, utc="2023-06-18T16:00:00Z"),
        ),
    )
    pilot._private_json_write(
        output_dir / "summary.json",
        {"status": "DISCOVERY_TARGET_MISMATCH"},
    )
    pilot._private_json_write(
        output_dir / "summary-resumed.json",
        {"status": "PRIMARY_RESPONSE_VALIDATED"},
    )


def test_runner_seal_run_live_precedes_factory_transport_and_output_creation(
    monkeypatch,
    tmp_path,
):
    calls = {
        "allocator": 0,
        "dotenv": 0,
        "environment": 0,
        "factory": 0,
        "transport": 0,
    }
    output_dir = tmp_path / "must-not-exist"

    def fake_allocator():
        calls["allocator"] += 1
        return output_dir

    def fake_transport(*args, **kwargs):
        calls["transport"] += 1
        return _daily(_match())

    def forbidden_dotenv(*args, **kwargs):
        calls["dotenv"] += 1
        raise AssertionError("sealed runner must not load dotenv")

    def forbidden_getenv(*args, **kwargs):
        calls["environment"] += 1
        raise AssertionError("sealed runner must not read environment")

    class ReplayClient:
        def daily_matches(self, date):
            return pilot.fotmob_module.cffi_requests.get("synthetic-daily")

        def league_matches(self, competition_id, season):
            pilot.fotmob_module.cffi_requests.get("synthetic-competition")
            return _competition(season, _match())

    def fake_factory(*args, **kwargs):
        calls["factory"] += 1
        return ReplayClient()

    monkeypatch.setattr(pilot, "_new_live_output_dir", fake_allocator)
    monkeypatch.delenv("THORDATA_PROXY", raising=False)
    monkeypatch.setattr(pilot.os, "getenv", forbidden_getenv)
    monkeypatch.setattr(
        pilot.fotmob_module,
        "load_dotenv",
        forbidden_dotenv,
    )
    monkeypatch.setattr(pilot.fotmob_module, "FotMobClient", fake_factory)
    monkeypatch.setattr(
        pilot.fotmob_module.cffi_requests,
        "get",
        fake_transport,
    )

    code, summary = pilot.run_live()

    assert code != 0
    assert summary["status"] == "LIVE_RUNNER_SEALED"
    assert summary["actual_http_request_count"] == 0
    assert summary["transport_attempt_count"] == 0
    assert calls == {
        "allocator": 0,
        "dotenv": 0,
        "environment": 0,
        "factory": 0,
        "transport": 0,
    }
    assert not output_dir.exists()


def test_runner_seal_completed_resume_preserves_every_artifact(
    monkeypatch,
    tmp_path,
):
    output_dir = tmp_path / "completed"
    _write_completed_artifacts(output_dir)
    before = _artifact_snapshot(output_dir)
    before_paths = sorted(
        str(path.relative_to(output_dir))
        for path in output_dir.rglob("*")
    )
    calls = {"factory": 0, "transport": 0}

    def fake_transport(*args, **kwargs):
        calls["transport"] += 1
        return _competition("2025", _match())

    class ReplayClient:
        def league_matches(self, competition_id, season):
            pilot.fotmob_module.cffi_requests.get("synthetic-competition")
            return _competition(season, _match())

    def fake_factory(*args, **kwargs):
        calls["factory"] += 1
        return ReplayClient()

    monkeypatch.setattr(pilot.fotmob_module, "FotMobClient", fake_factory)
    monkeypatch.setattr(
        pilot.fotmob_module.cffi_requests,
        "get",
        fake_transport,
    )

    code, summary = pilot.resume_live_from_saved_daily(output_dir)

    assert code != 0
    assert summary["status"] == "LIVE_RUNNER_SEALED"
    assert summary["actual_http_request_count"] == 0
    assert summary["transport_attempt_count"] == 0
    assert calls == {"factory": 0, "transport": 0}
    assert _artifact_snapshot(output_dir) == before
    assert sorted(
        str(path.relative_to(output_dir))
        for path in output_dir.rglob("*")
    ) == before_paths


def test_runner_seal_daily_only_resume_reads_nothing_and_uses_no_transport(
    monkeypatch,
    tmp_path,
):
    output_dir = tmp_path / "daily-only"
    pilot._private_json_write(
        output_dir / "raw" / "daily_20250619.json",
        _daily(_match()),
    )
    before = _artifact_snapshot(output_dir)
    calls = {"read": 0, "factory": 0, "transport": 0}

    def forbidden_read(*args, **kwargs):
        calls["read"] += 1
        raise AssertionError("sealed resume must not read artifacts")

    def forbidden_factory(*args, **kwargs):
        calls["factory"] += 1
        raise AssertionError("sealed resume must not construct a client")

    def forbidden_transport(*args, **kwargs):
        calls["transport"] += 1
        raise AssertionError("sealed resume must not call transport")

    monkeypatch.setattr(type(output_dir), "read_text", forbidden_read)
    monkeypatch.setattr(
        pilot.fotmob_module,
        "FotMobClient",
        forbidden_factory,
    )
    monkeypatch.setattr(
        pilot.fotmob_module.cffi_requests,
        "get",
        forbidden_transport,
    )

    code, summary = pilot.resume_live_from_saved_daily(output_dir)

    assert code != 0
    assert summary["status"] == "LIVE_RUNNER_SEALED"
    assert summary["actual_http_request_count"] == 0
    assert summary["transport_attempt_count"] == 0
    assert calls == {"read": 0, "factory": 0, "transport": 0}
    assert _artifact_snapshot(output_dir) == before


def test_runner_seal_live_cli_is_fixed_public_json(
    monkeypatch,
    tmp_path,
    capsys,
):
    calls = {"allocator": 0, "factory": 0, "transport": 0}

    def forbidden_allocator():
        calls["allocator"] += 1
        raise AssertionError("sealed CLI must not allocate an output directory")

    def forbidden_factory(*args, **kwargs):
        calls["factory"] += 1
        raise AssertionError("sealed CLI must not construct a client")

    def forbidden_transport(*args, **kwargs):
        calls["transport"] += 1
        raise AssertionError("sealed CLI must not call transport")

    monkeypatch.setattr(pilot, "_new_live_output_dir", forbidden_allocator)
    monkeypatch.setattr(
        pilot.fotmob_module,
        "FotMobClient",
        forbidden_factory,
    )
    monkeypatch.setattr(
        pilot.fotmob_module.cffi_requests,
        "get",
        forbidden_transport,
    )

    code = pilot.run_cli(["--live"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    rendered = captured.out.casefold()

    assert code != 0
    assert payload == {
        "actual_http_request_count": 0,
        "data_verdict": pilot.GO,
        "production_integration": False,
        "runner_verdict": "PERMANENTLY_SEALED",
        "status": "LIVE_RUNNER_SEALED",
        "transport_attempt_count": 0,
    }
    assert captured.err == ""
    assert calls == {"allocator": 0, "factory": 0, "transport": 0}
    assert not any(
        marker in rendered
        for marker in (
            "http://",
            "https://",
            "proxy",
            "authorization",
            "basic ",
            "bearer ",
            str(tmp_path).casefold(),
        )
    )


def test_request_budget_rejects_fourth_call_before_transport(monkeypatch):
    forwarded = []

    def fake_get(*args, **kwargs):
        forwarded.append("called")
        return object()

    monkeypatch.setattr(pilot.fotmob_module.cffi_requests, "get", fake_get)
    with pilot.RequestBudgetGuard(limit=3) as guard:
        pilot.fotmob_module.cffi_requests.get("one")
        pilot.fotmob_module.cffi_requests.get("two")
        pilot.fotmob_module.cffi_requests.get("three")
        with pytest.raises(pilot.RequestBudgetExceeded):
            pilot.fotmob_module.cffi_requests.get("four")

    assert guard.attempt_count == 4
    assert guard.forwarded_count == 3
    assert forwarded == ["called", "called", "called"]
    assert pilot.fotmob_module.cffi_requests.get is fake_get


def test_transport_failure_has_no_hidden_retry(monkeypatch, caplog):
    marker = "RAW_TRANSPORT_SECRET"
    forwarded = []

    def fail_get(*args, **kwargs):
        forwarded.append("called")
        raise RuntimeError(marker)

    monkeypatch.setattr(pilot.fotmob_module.cffi_requests, "get", fail_get)
    client = pilot.fotmob_module.FotMobClient(
        proxy="",
        max_retries=1,
        retry_delay=0,
    )
    with pilot.RequestBudgetGuard(limit=3) as guard:
        with caplog.at_level(logging.WARNING):
            with pytest.raises(pilot.fotmob_module.FotMobTransportError):
                client.daily_matches(pilot.DISCOVERY_DATE)

    assert forwarded == ["called"]
    assert guard.forwarded_count == 1
    assert marker not in caplog.text


def test_request_counter_does_not_emit_or_retain_call_details(
    monkeypatch, capsys,
):
    monkeypatch.setattr(
        pilot.fotmob_module.cffi_requests,
        "get",
        lambda *args, **kwargs: object(),
    )
    with pilot.RequestBudgetGuard(limit=3) as guard:
        pilot.fotmob_module.cffi_requests.get(
            "http://USER:PASSWORD@proxy.invalid",
            headers={"Authorization": "Bearer TOKEN"},
        )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert guard.attempt_count == 1
    assert guard.forwarded_count == 1
    assert not hasattr(guard, "args")
    assert not hasattr(guard, "kwargs")


def test_name_normalization_does_not_remove_semantic_tokens():
    assert (
        pilot.normalize_competition_name(" Club—World   Cup ")
        == "club world cup"
    )
    assert (
        pilot.normalize_competition_name("FIFA Club World Cup Grp. G")
        == "fifa club world cup"
    )
    assert (
        pilot.normalize_competition_name("FIFA Club World Cup")
        != pilot.normalize_competition_name("Club World Cup")
    )


def test_unique_daily_candidate_records_public_fields():
    result = pilot.analyze_discovery(_daily(_match()))
    assert result["ok"] is True
    assert result["status"] == "UNIQUE_DISCOVERY"
    assert result["match"] == {
        "provider_match_id": MATCH_ID,
        "home_team_id": TEAM_ID,
        "away_team_id": WYDAD_ID,
        "competition_id": COMPETITION_ID,
        "competition_name": "Club World Cup",
        "kickoff_utc": "2025-06-18T16:00:00Z",
        "kickoff_precision": "exact",
        "finished": True,
        "cancelled": False,
        "home_team_name": "Manchester City",
        "away_team_name": "Wydad AC",
    }


def test_realistic_source_display_aliases_still_identify_authoritative_team_id():
    result = pilot.analyze_discovery(_daily(
        _match(
            home_name="Man City",
            away_name="Wydad Casablanca",
        ),
        name="FIFA Club World Cup Grp. G",
    ))
    assert result["ok"] is True
    assert result["match"]["home_team_id"] == TEAM_ID
    assert result["match"]["home_team_name"] == "Man City"
    assert result["match"]["away_team_name"] == "Wydad Casablanca"


def test_zero_daily_candidate_stops_unverified():
    other = _match(home_id=1, away_id=2, home_name="A", away_name="B")
    result = pilot.analyze_discovery(_daily(other))
    assert result["ok"] is False
    assert result["verdict"] == pilot.UNVERIFIED
    assert result["status"] == "NO_DISCOVERY_CANDIDATE"


def test_multiple_daily_candidates_stop_ambiguous():
    result = pilot.analyze_discovery(
        _daily(_match(), _match(match_id=MATCH_ID + 1)),
    )
    assert result["ok"] is False
    assert result["verdict"] == pilot.UNVERIFIED
    assert result["status"] == "AMBIGUOUS_DISCOVERY"


@pytest.mark.parametrize(
    ("changes", "expected_status"),
    [
        ({"competition_id": COMPETITION_ID + 1}, "COMPETITION_IDENTITY_MISMATCH"),
        ({"name": "FIFA Club World Cup"}, "COMPETITION_IDENTITY_MISMATCH"),
        ({"season": "2024"}, "SEASON_MISMATCH"),
    ],
)
def test_primary_identity_and_season_fail_closed(changes, expected_status):
    kwargs = {
        "season": "2025",
        "competition_id": COMPETITION_ID,
        "name": "Club World Cup",
    }
    kwargs.update(changes)
    raw = _competition(
        kwargs.pop("season"),
        _match(),
        **kwargs,
    )
    result = pilot.analyze_primary_response(
        raw, _discovery_match(), "2026-07-25T00:00:00Z",
    )
    assert result["ok"] is False
    assert result["status"] == expected_status


def test_primary_empty_fixtures_stops_unverified():
    raw = _competition("2025")
    raw["fixtures"]["allMatches"] = []
    result = pilot.analyze_primary_response(
        raw, _discovery_match(), "2026-07-25T00:00:00Z",
    )
    assert result["ok"] is False
    assert result["verdict"] == pilot.UNVERIFIED
    assert result["status"] == "EMPTY_FIXTURES"


@pytest.mark.parametrize(
    "metadata",
    [
        {"hasMore": True},
        {"page": 1},
        {"page": 1, "totalPages": "2"},
    ],
)
def test_primary_detected_or_unresolved_pagination_is_no_go(metadata):
    raw = _competition("2025", _match(), fixture_metadata=metadata)
    result = pilot.analyze_primary_response(
        raw, _discovery_match(), "2026-07-25T00:00:00Z",
    )
    assert result["ok"] is False
    assert result["verdict"] == pilot.NO_GO
    assert result["status"] == "PAGINATION_UNRESOLVED"
    assert result["pagination"]["status"] in {"DETECTED", "UNRESOLVED"}


def test_primary_duplicate_match_id_is_no_go():
    raw = _competition("2025", _match(), _match())
    result = pilot.analyze_primary_response(
        raw, _discovery_match(), "2026-07-25T00:00:00Z",
    )
    assert result["ok"] is False
    assert result["status"] == "DUPLICATE_MATCH_ID"


def test_primary_crosslink_match_id_required():
    raw = _competition("2025", _match(match_id=MATCH_ID + 1))
    result = pilot.analyze_primary_response(
        raw, _discovery_match(), "2026-07-25T00:00:00Z",
    )
    assert result["ok"] is False
    assert result["status"] == "DISCOVERED_MATCH_NOT_CROSSLINKED"


def test_primary_crosslink_home_away_must_match():
    raw = _competition(
        "2025",
        _match(
            home_id=WYDAD_ID,
            away_id=TEAM_ID,
            home_name="Wydad AC",
            away_name="Manchester City",
        ),
    )
    result = pilot.analyze_primary_response(
        raw, _discovery_match(), "2026-07-25T00:00:00Z",
    )
    assert result["ok"] is False
    assert result["status"] == "CROSSLINK_HOME_AWAY_MISMATCH"


def test_primary_validates_crosslink_counts_and_no_pagination():
    raw = _competition("2025", _match())
    result = pilot.analyze_primary_response(
        raw, _discovery_match(), "2026-07-25T00:00:00Z",
    )
    assert result["ok"] is True
    assert result["fixture_count"] == 1
    assert result["target_team_fixture_count"] == 1
    assert result["finished_fixture_count"] == 1
    assert result["cancelled_fixture_count"] == 0
    assert result["non_cancelled_fixture_count"] == 1
    assert result["pagination"]["status"] == "NOT_DETECTED"
    assert result["crosslink"]["home_away_match"] is True
    assert result["count_alignment"] == "COUNT_DIFFERS_FROM_OFFICIAL_FORMAT_REFERENCE"


def test_season_comparison_effective():
    primary = _competition("2025", _match())
    comparison = _competition(
        "2023",
        _match(match_id=MATCH_ID - 100, utc="2023-06-18T16:00:00Z"),
    )
    result = pilot.analyze_season_comparison(
        primary, comparison, _discovery_match(),
    )
    assert result["status"] == "SEASON_PARAMETER_EFFECTIVE"
    assert result["match_id_overlap_count"] == 0


@pytest.mark.parametrize(
    "comparison",
    [
        _competition("2025", _match()),
        _competition("2023", _match()),
    ],
)
def test_season_comparison_ineffective(comparison):
    primary = _competition("2025", _match())
    result = pilot.analyze_season_comparison(
        primary, comparison, _discovery_match(),
    )
    assert result["status"] == "SEASON_PARAMETER_INEFFECTIVE"


@pytest.mark.parametrize(
    "comparison",
    [
        {"details": {}, "fixtures": {"allMatches": [_match()]}},
        _competition("2023", competition_id=COMPETITION_ID + 1),
        _competition("2023", name="FIFA Club World Cup"),
        _competition("2023"),
    ],
)
def test_season_comparison_unverified(comparison):
    if comparison.get("fixtures", {}).get("allMatches") == [_match()]:
        comparison["fixtures"]["allMatches"] = []
    result = pilot.analyze_season_comparison(
        _competition("2025", _match()),
        comparison,
        _discovery_match(),
    )
    assert result["status"] == "SEASON_PARAMETER_UNVERIFIED"


def test_request_two_failure_prevents_request_three_and_pagination_following(
    tmp_path,
):
    primary = _competition(
        "2025",
        _match(),
        fixture_metadata={"hasMore": True, "next": "/must-not-follow"},
    )
    client = FakeClient(
        _daily(_match()),
        primary,
        AssertionError("request 3 must not run"),
    )
    summary = pilot.execute_pilot(client, tmp_path / "pilot")
    assert summary["verdict"] == pilot.NO_GO
    assert summary["status"] == "PAGINATION_UNRESOLVED"
    assert client.calls == [
        ("daily_matches", "20250619"),
        ("league_matches", COMPETITION_ID, "2025"),
    ]
    assert summary["pagination_followed"] is False


def test_full_offline_workflow_uses_only_three_fixed_operations(tmp_path):
    client = FakeClient(
        _daily(_match()),
        _competition("2025", _match()),
        _competition(
            "2023",
            _match(match_id=MATCH_ID - 1, utc="2023-06-18T16:00:00Z"),
        ),
    )
    output_dir = tmp_path / "pilot"
    summary = pilot.execute_pilot(client, output_dir)
    assert summary["verdict"] == pilot.GO
    assert client.calls == [
        ("daily_matches", "20250619"),
        ("league_matches", COMPETITION_ID, "2025"),
        ("league_matches", COMPETITION_ID, "2023"),
    ]
    assert summary["pagination_followed"] is False
    assert summary["production_integration"] is False
    assert [row["operation"] for row in summary["requests"]] == [
        "daily_matches",
        "league_matches_2025",
        "league_matches_2023",
    ]
    for path in (output_dir / "raw").glob("*.json"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_historical_live_factory_is_never_constructed_after_seal(
    monkeypatch,
    tmp_path,
):
    seen = {}

    def fake_factory(**kwargs):
        seen.update(kwargs)
        raise AssertionError("permanently sealed runner constructed a client")

    monkeypatch.setattr(pilot.fotmob_module, "FotMobClient", fake_factory)
    monkeypatch.setattr(
        pilot,
        "_new_live_output_dir",
        lambda: pytest.fail("permanently sealed runner allocated output"),
    )
    code, summary = pilot.run_live()
    assert code != 0
    assert summary["status"] == "LIVE_RUNNER_SEALED"
    assert summary["runner_verdict"] == "PERMANENTLY_SEALED"
    assert summary["data_verdict"] == pilot.GO
    assert seen == {}
    assert not (tmp_path / "live").exists()


def test_historical_resume_entry_is_sealed_even_with_valid_saved_daily(
    monkeypatch, tmp_path,
):
    output_dir = tmp_path / "resume"
    (output_dir / "raw").mkdir(parents=True)
    pilot._private_json_write(
        output_dir / "raw" / "daily_20250619.json",
        _daily(
            _match(home_name="Man City", away_name="Wydad Casablanca"),
            name="FIFA Club World Cup Grp. G",
        ),
    )
    client = FakeClient(
        AssertionError("daily request must not be replayed"),
        _competition(
            "2025",
            _match(home_name="Man City", away_name="Wydad Casablanca"),
            name="FIFA Club World Cup",
        ),
        _competition(
            "2023",
            _match(
                match_id=MATCH_ID - 1,
                home_name="Man City",
                away_name="Wydad Casablanca",
                utc="2023-06-18T16:00:00Z",
            ),
            name="FIFA Club World Cup",
        ),
    )
    monkeypatch.setattr(
        pilot.fotmob_module,
        "FotMobClient",
        lambda **kwargs: client,
    )
    code, summary = pilot.resume_live_from_saved_daily(output_dir)
    assert code != 0
    assert summary["status"] == "LIVE_RUNNER_SEALED"
    assert summary["runner_verdict"] == "PERMANENTLY_SEALED"
    assert summary["data_verdict"] == pilot.GO
    assert client.calls == []
    assert sorted(
        str(path.relative_to(output_dir))
        for path in output_dir.rglob("*")
    ) == ["raw", "raw/daily_20250619.json"]


@pytest.mark.parametrize(
    "unsafe",
    [
        "THORDATA_PROXY=http://example",
        "http://USER:PASSWORD@proxy.invalid",
        "Authorization: Basic SECRET",
        "Bearer SECRET",
        "residential.thordata.com",
    ],
)
def test_redaction_gate_blocks_secret_shapes(unsafe):
    assert pilot.redaction_findings(unsafe)
    with pytest.raises(RuntimeError, match="REDACTION_GATE_FAILED"):
        pilot.assert_redaction_safe(unsafe)


def test_redaction_gate_allows_public_pilot_summary():
    safe = json.dumps({
        "operation": "league_matches_2025",
        "competition_name": "Club World Cup",
        "fixture_count": 63,
    })
    assert pilot.redaction_findings(safe) == []
    pilot.assert_redaction_safe(safe)


def test_cli_without_live_makes_no_client(monkeypatch, capsys):
    monkeypatch.setattr(
        pilot.fotmob_module,
        "FotMobClient",
        lambda *a, **k: pytest.fail("client must not be constructed"),
    )
    assert pilot.run_cli([]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "LIVE_FLAG_REQUIRED"
