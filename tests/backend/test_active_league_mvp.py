from __future__ import annotations

import inspect
import json

import pytest

from backend.active_league_mvp import (
    ActiveLeagueMVPError,
    _fixture_row,
    _market_probabilities,
    _read_json,
    apply_saved_artifacts,
)


def test_market_probabilities_are_de_vigged_and_identified_by_company() -> None:
    records = [
        {
            "market": "1x2",
            "company_id": "8",
            "company_name": "Bet365",
            "initial": {"home": 1.73, "draw": 3.8, "away": 4.2},
            "latest": {"home": 1.65, "draw": 3.9, "away": 4.5},
        }
    ]

    probabilities, selected = _market_probabilities(records)

    assert selected["company_name"] == "Bet365"
    assert sum(probabilities.values()) == pytest.approx(1.0)
    assert probabilities == pytest.approx(
        {"home": 0.558739255, "draw": 0.236389685, "away": 0.20487106}
    )


def test_market_probabilities_fail_closed_without_real_1x2() -> None:
    with pytest.raises(ActiveLeagueMVPError, match="real 1x2 market is unavailable"):
        _market_probabilities(
            [
                {
                    "market": "asian_handicap",
                    "company_id": "8",
                    "latest": {"home": 0.83, "line": 0.75, "away": 0.98},
                }
            ]
        )


def test_fixture_normalization_requires_exact_aware_kickoff() -> None:
    base = {
        "id": 5104968,
        "home": {"id": 8007, "name": "Vålerenga"},
        "away": {"id": 8448, "name": "Hamarkameratene"},
        "tournament": {"leagueId": 59},
        "status": {"utcTime": "2026-07-31T17:00:00Z"},
    }

    row = _fixture_row(base)
    assert row is not None
    assert row["kickoff_at_utc"] == "2026-07-31T17:00:00Z"
    assert row["kickoff_precision"] == "exact"
    other = _fixture_row(base, default_league=67, season="2027")
    assert other is not None
    assert other["League_ID"] == 59  # response identity remains authoritative
    assert other["Season"] == "2027"

    invalid = json.loads(json.dumps(base))
    invalid["status"]["utcTime"] = "2026-07-31T17:00:00"
    assert _fixture_row(invalid) is None


def test_artifact_reader_reports_only_safe_basename(tmp_path) -> None:
    path = tmp_path / "provider-response.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(
        ActiveLeagueMVPError,
        match=r"^invalid MVP artifact: provider-response\.json$",
    ) as exc_info:
        _read_json(path)

    assert str(tmp_path) not in str(exc_info.value)


def test_artifact_apply_does_not_retain_sample_identity_constants() -> None:
    source = inspect.getsource(apply_saved_artifacts)
    for constant in (
        "MATCH_ID",
        "NOWGOAL_MATCH_ID",
        "HOME_ID",
        "AWAY_ID",
        "LEAGUE_ID",
        "LEAGUE_NAME",
        "SEASON",
        "TEAM_ZH",
    ):
        assert constant not in source
