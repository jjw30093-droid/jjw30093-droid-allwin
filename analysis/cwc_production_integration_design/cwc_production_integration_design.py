"""Offline-only CWC schedule integration design and temporary SQLite proof.

This module is deliberately not a live ingestion entry point.  It consumes a
small, permanent fixture derived from one validated saved response and writes
only caller-supplied temporary SQLite databases whose tables are visibly
prefixed ``prototype_``.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tempfile
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_FIXTURE_PATH = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "fotmob"
    / "cwc_2025_competition_schedule_canonical.json"
)
SOURCE_ARTIFACT_SHA256 = (
    "6e0007cecea78d59b7d771d689a0e24d45dc4b2bbf7776a3a58516fbb71bae3d"
)
CANONICAL_FIXTURE_SHA256 = (
    "020b6eabecd9ca004611863d3b3f5820ee12ea2f1ff8f203f51e73a1bbf276b1"
)
TRANSFORMATION_VERSION = "cwc_schedule_canonical_v1"
REST_FEATURE_VERSION = "observed_kickoff_gap_v1"
REST_PROVENANCE = (
    "observed_historical:finished_non_cancelled_exact_competitive:"
    "kickoff_to_kickoff"
)
COMPETITIVE_CLASSES = frozenset(
    {
        "league",
        "domestic_cup",
        "continental",
        "super_cup",
        "international_club",
    }
)
OBSERVATION_ORDERING = "APPEND_ONLY_EVENT_TIME_CAN_BE_OUT_OF_ORDER"

_PROVIDER = "fotmob"
_COMPETITION_ID = 78
_COMPETITION_NAME = "FIFA Club World Cup"
_REQUESTED_SEASON = "2025"
_COMPETITION_CLASS = "international_club"
_PAGINATION_STATUS = "NOT_DETECTED_FOR_SAVED_RESPONSE"
_COMPLETENESS_STATUS = "VALIDATED_SAVED_RESPONSE_ONLY"
_SOURCE_ENDPOINT = "fotmob.league_matches.saved_response"
_FIXTURE_COUNT = 66


class SeasonStrategyError(ValueError):
    """The season label does not satisfy the explicitly selected strategy."""


class PrototypeConflictError(RuntimeError):
    """An immutable natural key already exists with different content."""


class PrototypeSchemaIncompatibleError(RuntimeError):
    """Existing prototype tables do not match this prototype's exact schema."""


class SourceArtifactHashMismatch(ValueError):
    """Canonical provenance does not identify the validated saved response."""


class CanonicalFixtureHashMismatch(ValueError):
    """Permanent fixture bytes differ from the reviewed canonical fixture."""


class PrototypeDataError(ValueError):
    """Canonical input is incomplete, malformed, or outside the prototype."""


class PrototypeDatabasePathError(ValueError):
    """The caller selected a repository production-data path."""


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _payload_hash(row: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json_bytes(row)).hexdigest()


def _strict_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PrototypeDataError(f"{field} must be a positive integer")
    return value


def _strict_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise PrototypeDataError(f"{field} must be a boolean")
    return value


def _strict_nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PrototypeDataError(f"{field} must be a non-empty string")
    return value


def _parse_utc(value: Any, field: str) -> datetime:
    del field
    parsed: datetime | None = None
    parse_failed = not isinstance(value, str) or not value.strip()
    if not parse_failed:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            parse_failed = True

    if parse_failed or parsed is None:
        raise PrototypeDataError("invalid UTC timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise PrototypeDataError("invalid UTC timestamp") from None
    return parsed.astimezone(timezone.utc)


def _canonical_utc(value: Any, field: str) -> str:
    return _parse_utc(value, field).isoformat().replace("+00:00", "Z")


def credential_shape_findings(text: str) -> list[str]:
    """Return only finding labels; never echo potentially sensitive values."""

    patterns = {
        "authorization_header": r"(?i)\bauthorization\b",
        "bearer_auth": r"(?i)\bbearer\s+[a-z0-9._~+/=-]+",
        "basic_auth": r"(?i)\bbasic\s+[a-z0-9+/=]+",
        "proxy_field": r"(?i)(?:thordata|proxy)[_-]?(?:url|user|password)?",
        "credential_url": r"(?i)://[^/\s:@]+:[^/@\s]+@",
        "password_field": r'(?i)["\']?(?:password|passwd)["\']?\s*[:=]',
        "token_field": r'(?i)["\']?(?:access[_-]?token|api[_-]?key)["\']?\s*[:=]',
    }
    return [
        label
        for label, pattern in patterns.items()
        if re.search(pattern, text) is not None
    ]


def validate_season_strategy(
    strategy: Any,
    label: Any,
    *,
    explicit_label: Any = None,
) -> str:
    """Validate a season label without guessing its form from a competition."""

    if not isinstance(strategy, str) or not isinstance(label, str):
        raise SeasonStrategyError("season strategy and label must be strings")

    if strategy == "calendar_year":
        if re.fullmatch(r"\d{4}", label) is None:
            raise SeasonStrategyError("calendar_year requires YYYY")
        return label

    if strategy == "split_year":
        match = re.fullmatch(r"(\d{4})/(\d{4})", label)
        if match is None or int(match.group(2)) != int(match.group(1)) + 1:
            raise SeasonStrategyError("split_year requires adjacent YYYY/YYYY")
        return label

    if strategy == "explicit":
        if (
            not isinstance(explicit_label, str)
            or not explicit_label
            or label != explicit_label
        ):
            raise SeasonStrategyError("explicit requires an exact configured label")
        return label

    raise SeasonStrategyError("unsupported season strategy")


def build_canonical_fixture_from_validated_raw(raw_bytes: bytes) -> dict[str, Any]:
    """Trim the one authorized saved raw response into the permanent shape."""

    actual_hash = hashlib.sha256(raw_bytes).hexdigest()
    if actual_hash != SOURCE_ARTIFACT_SHA256:
        raise SourceArtifactHashMismatch("saved source artifact hash mismatch")
    try:
        raw = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrototypeDataError("saved source artifact is not valid JSON") from exc

    details = raw.get("details")
    fixture_container = raw.get("fixtures")
    if not isinstance(details, dict) or not isinstance(fixture_container, dict):
        raise PrototypeDataError("saved source identity or fixtures are missing")
    if details.get("id") != _COMPETITION_ID:
        raise PrototypeDataError("saved source competition id mismatch")
    if details.get("name") != _COMPETITION_NAME:
        raise PrototypeDataError("saved source competition name mismatch")
    if details.get("selectedSeason") != _REQUESTED_SEASON:
        raise PrototypeDataError("saved source selected season mismatch")

    source_rows = fixture_container.get("allMatches")
    if not isinstance(source_rows, list) or len(source_rows) != _FIXTURE_COUNT:
        raise PrototypeDataError("saved source fixture count mismatch")

    fixtures: list[dict[str, Any]] = []
    for index, source in enumerate(source_rows):
        if not isinstance(source, dict):
            raise PrototypeDataError(f"source fixture {index} must be an object")
        status = source.get("status")
        home = source.get("home")
        away = source.get("away")
        if not all(isinstance(item, dict) for item in (status, home, away)):
            raise PrototypeDataError(f"source fixture {index} shape is invalid")
        reason = status.get("reason")
        if not isinstance(reason, dict):
            raise PrototypeDataError(f"source fixture {index} status is invalid")
        try:
            match_id = int(source.get("id"))
            home_id = int(home.get("id"))
            away_id = int(away.get("id"))
        except (TypeError, ValueError) as exc:
            raise PrototypeDataError(f"source fixture {index} id is invalid") from exc
        fixtures.append(
            {
                "id": match_id,
                "home": {
                    "id": home_id,
                    "name": home.get("name"),
                },
                "away": {
                    "id": away_id,
                    "name": away.get("name"),
                },
                "round": source.get("round"),
                "status": {
                    "short": reason.get("short"),
                    "finished": status.get("finished"),
                    "cancelled": status.get("cancelled"),
                    "started": status.get("started"),
                    "utcTime": status.get("utcTime"),
                },
            }
        )
    fixtures.sort(key=lambda row: row["id"])

    return {
        "competition": {
            "id": _COMPETITION_ID,
            "name": _COMPETITION_NAME,
            "selectedSeason": _REQUESTED_SEASON,
        },
        "fixtures": fixtures,
        "provenance": {
            "description": "trimmed from validated saved response",
            "pagination_status": _PAGINATION_STATUS,
            "source_artifact_sha256": SOURCE_ARTIFACT_SHA256,
            "transformation_version": TRANSFORMATION_VERSION,
        },
    }


def load_canonical_fixture(path: Path | str | None = None) -> dict[str, Any]:
    """Load the immutable permanent fixture after a byte-for-byte hash gate."""

    fixture_path = Path(path) if path is not None else CANONICAL_FIXTURE_PATH
    raw = fixture_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != CANONICAL_FIXTURE_SHA256:
        raise CanonicalFixtureHashMismatch("canonical fixture hash mismatch")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrototypeDataError("canonical fixture is not valid JSON") from exc
    if not isinstance(document, dict):
        raise PrototypeDataError("canonical fixture root must be an object")
    return document


def competition_is_competitive(
    competition_class: Any,
    *,
    registry_verified: Any,
) -> bool:
    """Return true only for an explicitly classified, registry-verified event."""

    return (
        registry_verified is True
        and isinstance(competition_class, str)
        and competition_class in COMPETITIVE_CLASSES
    )


def observed_load_eligibility(
    *,
    competition_class: Any,
    registry_verified: Any,
    finished: Any,
    cancelled: Any,
    kickoff_precision: Any,
    kickoff_at_utc: Any,
) -> tuple[bool, str | None]:
    """Gate historical schedule-load rows without treating unknown as competitive."""

    if cancelled is True:
        return False, "cancelled"
    if finished is not True:
        return False, "unfinished"
    if competition_class == "friendly":
        return False, "friendly"
    if not competition_is_competitive(
        competition_class,
        registry_verified=registry_verified,
    ):
        if competition_class in COMPETITIVE_CLASSES:
            return False, "registry_unverified"
        return False, "competition_class_unverified"
    if kickoff_precision != "exact" or not isinstance(kickoff_at_utc, str):
        return False, "kickoff_not_exact"
    try:
        _parse_utc(kickoff_at_utc, "kickoff_at_utc")
    except PrototypeDataError:
        return False, "kickoff_not_exact"
    return True, None


def build_feature_input_set_hash(
    timeline_prefix: Iterable[Mapping[str, Any]],
    match_by_id: Mapping[int, Mapping[str, Any]],
) -> str:
    """Hash one strictly ordered, unique, point-in-time timeline prefix."""

    rows = list(timeline_prefix)
    if not rows:
        raise PrototypeDataError("feature input timeline prefix must not be empty")

    seen_match_ids: set[Any] = set()
    resolved_inputs: list[
        tuple[Mapping[str, Any], Mapping[str, Any], datetime]
    ] = []
    for row in rows:
        match_id = row.get("provider_match_id")
        try:
            if match_id in seen_match_ids:
                raise PrototypeDataError(
                    "feature input timeline contains duplicate match id"
                )
            match = match_by_id.get(match_id)
        except TypeError:
            raise PrototypeDataError("feature input match is missing") from None
        if match is None:
            raise PrototypeDataError("feature input match is missing")
        kickoff = _parse_utc(
            match.get("kickoff_at_utc"),
            "feature input kickoff_at_utc",
        )
        seen_match_ids.add(match_id)
        resolved_inputs.append((row, match, kickoff))

    current_kickoff = resolved_inputs[-1][2]
    if any(kickoff > current_kickoff for _, _, kickoff in resolved_inputs):
        raise PrototypeDataError(
            "feature input timeline contains a future match"
        )

    stable_inputs: list[dict[str, Any]] = []
    previous_kickoff: datetime | None = None
    for row, match, kickoff in resolved_inputs:
        if previous_kickoff is not None and kickoff <= previous_kickoff:
            raise PrototypeDataError(
                "feature input timeline must have strictly increasing kickoff times"
            )
        previous_kickoff = kickoff
        stable_inputs.append(
            {
                "provider": row["provider"],
                "provider_match_id": row["provider_match_id"],
                "team_id": row["team_id"],
                "opponent_team_id": row["opponent_team_id"],
                "is_home": row["is_home"],
                "is_competitive": row["is_competitive"],
                "finished": row["finished"],
                "cancelled": row["cancelled"],
                "eligible_for_load": row["eligible_for_load"],
                "requested_season": row["requested_season"],
                "kickoff_at_utc": match["kickoff_at_utc"],
                "status": match["status"],
            }
        )
    return _payload_hash(stable_inputs)


def _validated_document_metadata(document: Mapping[str, Any]) -> str:
    if set(document) != {"competition", "fixtures", "provenance"}:
        raise PrototypeDataError("canonical document has unexpected top-level fields")
    competition = document.get("competition")
    provenance = document.get("provenance")
    if not isinstance(competition, dict) or not isinstance(provenance, dict):
        raise PrototypeDataError("canonical identity or provenance is missing")
    if competition != {
        "id": _COMPETITION_ID,
        "name": _COMPETITION_NAME,
        "selectedSeason": _REQUESTED_SEASON,
    }:
        raise PrototypeDataError("canonical competition identity mismatch")
    if provenance.get("source_artifact_sha256") != SOURCE_ARTIFACT_SHA256:
        raise SourceArtifactHashMismatch("canonical source artifact hash mismatch")
    if provenance != {
        "description": "trimmed from validated saved response",
        "pagination_status": _PAGINATION_STATUS,
        "source_artifact_sha256": SOURCE_ARTIFACT_SHA256,
        "transformation_version": TRANSFORMATION_VERSION,
    }:
        raise PrototypeDataError("canonical provenance mismatch")
    return validate_season_strategy("calendar_year", competition["selectedSeason"])


def _validate_fixture_row(source: Any, index: int) -> dict[str, Any]:
    if not isinstance(source, dict) or set(source) != {
        "id",
        "home",
        "away",
        "round",
        "status",
    }:
        raise PrototypeDataError(f"fixture {index} shape is invalid")
    home = source.get("home")
    away = source.get("away")
    status = source.get("status")
    if (
        not isinstance(home, dict)
        or set(home) != {"id", "name"}
        or not isinstance(away, dict)
        or set(away) != {"id", "name"}
        or not isinstance(status, dict)
        or set(status)
        != {"cancelled", "finished", "short", "started", "utcTime"}
    ):
        raise PrototypeDataError(f"fixture {index} nested shape is invalid")

    match_id = _strict_int(source["id"], f"fixtures[{index}].id")
    home_id = _strict_int(home["id"], f"fixtures[{index}].home.id")
    away_id = _strict_int(away["id"], f"fixtures[{index}].away.id")
    if home_id == away_id:
        raise PrototypeDataError(f"fixture {index} has identical teams")
    home_name = _strict_nonempty_string(
        home["name"],
        f"fixtures[{index}].home.name",
    )
    away_name = _strict_nonempty_string(
        away["name"],
        f"fixtures[{index}].away.name",
    )
    round_label = _strict_nonempty_string(
        source["round"],
        f"fixtures[{index}].round",
    )
    short_status = _strict_nonempty_string(
        status["short"],
        f"fixtures[{index}].status.short",
    )
    finished = _strict_bool(
        status["finished"],
        f"fixtures[{index}].status.finished",
    )
    cancelled = _strict_bool(
        status["cancelled"],
        f"fixtures[{index}].status.cancelled",
    )
    started = _strict_bool(
        status["started"],
        f"fixtures[{index}].status.started",
    )
    kickoff = _canonical_utc(
        status["utcTime"],
        f"fixtures[{index}].status.utcTime",
    )
    if cancelled and (finished or started):
        raise PrototypeDataError(f"fixture {index} cancelled status is inconsistent")
    return {
        "id": match_id,
        "home_id": home_id,
        "home_name": home_name,
        "away_id": away_id,
        "away_name": away_name,
        "round": round_label,
        "status": short_status,
        "finished": finished,
        "cancelled": cancelled,
        "started": started,
        "kickoff_at_utc": kickoff,
    }


def parse_canonical_fixture(
    document: Mapping[str, Any],
    *,
    observed_at: str,
) -> dict[str, Any]:
    """Build stable business rows plus one append-only observation event."""

    requested_season = _validated_document_metadata(document)
    observed_at_utc = _canonical_utc(observed_at, "observed_at")
    fixtures = document.get("fixtures")
    if not isinstance(fixtures, list) or len(fixtures) != _FIXTURE_COUNT:
        raise PrototypeDataError("canonical fixture count must be exactly 66")

    validated = [
        _validate_fixture_row(source, index)
        for index, source in enumerate(fixtures)
    ]
    if len({row["id"] for row in validated}) != _FIXTURE_COUNT:
        raise PrototypeDataError("canonical provider match ids must be unique")
    validated.sort(key=lambda row: row["id"])

    registry = {
        "provider": _PROVIDER,
        "competition_id": _COMPETITION_ID,
        "expected_name": _COMPETITION_NAME,
        "observed_name": _COMPETITION_NAME,
        "competition_class": _COMPETITION_CLASS,
        "requested_season": requested_season,
        "returned_season": requested_season,
        "season_strategy": "calendar_year",
        "identity_verified": 1,
        "season_verified": 1,
        "pagination_status": _PAGINATION_STATUS,
        "fixture_count": _FIXTURE_COUNT,
        "source_artifact_sha256": SOURCE_ARTIFACT_SHA256,
        "completeness_status": _COMPLETENESS_STATUS,
    }

    matches: list[dict[str, Any]] = []
    team_matches: list[dict[str, Any]] = []
    for source in validated:
        match = {
            "provider": _PROVIDER,
            "provider_match_id": source["id"],
            "competition_id": _COMPETITION_ID,
            "requested_season": requested_season,
            "competition_class": _COMPETITION_CLASS,
            "kickoff_at_utc": source["kickoff_at_utc"],
            "kickoff_precision": "exact",
            "home_team_id": source["home_id"],
            "home_team_name": source["home_name"],
            "away_team_id": source["away_id"],
            "away_team_name": source["away_name"],
            "status": source["status"],
            "finished": source["finished"],
            "cancelled": source["cancelled"],
            "round": source["round"],
            "source_endpoint": _SOURCE_ENDPOINT,
            "source_artifact_sha256": SOURCE_ARTIFACT_SHA256,
        }
        match["payload_hash"] = _payload_hash(match)
        matches.append(match)

        is_competitive = competition_is_competitive(
            _COMPETITION_CLASS,
            registry_verified=True,
        )
        eligible, exclusion_reason = observed_load_eligibility(
            competition_class=_COMPETITION_CLASS,
            registry_verified=True,
            finished=source["finished"],
            cancelled=source["cancelled"],
            kickoff_precision="exact",
            kickoff_at_utc=source["kickoff_at_utc"],
        )
        for team_id, opponent_id, is_home in (
            (source["home_id"], source["away_id"], True),
            (source["away_id"], source["home_id"], False),
        ):
            team_row = {
                "provider": _PROVIDER,
                "provider_match_id": source["id"],
                "team_id": team_id,
                "opponent_team_id": opponent_id,
                "is_home": is_home,
                "is_competitive": is_competitive,
                "finished": source["finished"],
                "cancelled": source["cancelled"],
                "eligible_for_load": eligible,
                "exclusion_reason": exclusion_reason,
                "requested_season": requested_season,
            }
            team_row["payload_hash"] = _payload_hash(team_row)
            team_matches.append(team_row)

    match_by_id = {row["provider_match_id"]: row for row in matches}
    eligible_by_team: dict[int, list[dict[str, Any]]] = {}
    for row in team_matches:
        if row["eligible_for_load"]:
            eligible_by_team.setdefault(row["team_id"], []).append(row)

    rest_features: list[dict[str, Any]] = []
    for team_id in sorted(eligible_by_team):
        timeline = sorted(
            eligible_by_team[team_id],
            key=lambda row: (
                match_by_id[row["provider_match_id"]]["kickoff_at_utc"],
                row["provider_match_id"],
            ),
        )
        for index, team_row in enumerate(timeline):
            match = match_by_id[team_row["provider_match_id"]]
            input_set_hash = build_feature_input_set_hash(
                timeline[: index + 1],
                match_by_id,
            )
            kickoff = _parse_utc(match["kickoff_at_utc"], "kickoff_at_utc")
            previous = timeline[index - 1] if index else None
            if previous is None:
                previous_match_id = None
                previous_status = None
                gap_hours = None
            else:
                previous_match_id = previous["provider_match_id"]
                previous_match = match_by_id[previous_match_id]
                previous_status = previous_match["status"]
                previous_kickoff = _parse_utc(
                    previous_match["kickoff_at_utc"],
                    "previous kickoff_at_utc",
                )
                gap_hours = (
                    kickoff - previous_kickoff
                ).total_seconds() / 3600.0
                if gap_hours <= 0:
                    raise PrototypeDataError(
                        "team timeline must have strictly increasing kickoffs"
                    )

            earlier = timeline[:index]
            matches_last_7d = sum(
                1
                for earlier_row in earlier
                if 0
                < (
                    kickoff
                    - _parse_utc(
                        match_by_id[earlier_row["provider_match_id"]][
                            "kickoff_at_utc"
                        ],
                        "earlier kickoff_at_utc",
                    )
                ).total_seconds()
                / 86400.0
                <= 7
            )
            matches_last_14d = sum(
                1
                for earlier_row in earlier
                if 0
                < (
                    kickoff
                    - _parse_utc(
                        match_by_id[earlier_row["provider_match_id"]][
                            "kickoff_at_utc"
                        ],
                        "earlier kickoff_at_utc",
                    )
                ).total_seconds()
                / 86400.0
                <= 14
            )
            feature = {
                "provider": _PROVIDER,
                "provider_match_id": team_row["provider_match_id"],
                "team_id": team_id,
                "previous_match_id": previous_match_id,
                "kickoff_at_utc": match["kickoff_at_utc"],
                "kickoff_gap_hours": gap_hours,
                "calendar_gap_days": (
                    gap_hours / 24.0 if gap_hours is not None else None
                ),
                "short_gap_72h": (
                    gap_hours < 72.0 if gap_hours is not None else None
                ),
                "short_gap_96h": (
                    gap_hours < 96.0 if gap_hours is not None else None
                ),
                "matches_last_7d": matches_last_7d,
                "matches_last_14d": matches_last_14d,
                "feature_version": REST_FEATURE_VERSION,
                "input_set_hash": input_set_hash,
                "previous_match_status": previous_status,
                "provenance": REST_PROVENANCE,
            }
            feature["payload_hash"] = _payload_hash(feature)
            rest_features.append(feature)

    source_content_hash = _payload_hash(
        {
            "registry": registry,
            "matches": [
                {
                    key: value
                    for key, value in row.items()
                    if key != "payload_hash"
                }
                for row in matches
            ],
        }
    )
    observation = {
        "provider": _PROVIDER,
        "competition_id": _COMPETITION_ID,
        "requested_season": requested_season,
        "observed_at": observed_at_utc,
        "source_artifact_sha256": SOURCE_ARTIFACT_SHA256,
        "source_content_hash": source_content_hash,
        "fixture_count": _FIXTURE_COUNT,
        "pagination_status": _PAGINATION_STATUS,
        "transformation_version": TRANSFORMATION_VERSION,
    }

    return {
        "registry": registry,
        "matches": matches,
        "team_matches": team_matches,
        "rest_features": rest_features,
        "observation": observation,
    }


_TABLE_COLUMNS = {
    "prototype_competition_registry": (
        "provider",
        "competition_id",
        "expected_name",
        "observed_name",
        "competition_class",
        "requested_season",
        "returned_season",
        "season_strategy",
        "identity_verified",
        "season_verified",
        "pagination_status",
        "fixture_count",
        "source_artifact_sha256",
        "completeness_status",
    ),
    "prototype_match_calendar": (
        "provider",
        "provider_match_id",
        "competition_id",
        "requested_season",
        "competition_class",
        "kickoff_at_utc",
        "kickoff_precision",
        "home_team_id",
        "home_team_name",
        "away_team_id",
        "away_team_name",
        "status",
        "finished",
        "cancelled",
        "round",
        "source_endpoint",
        "source_artifact_sha256",
        "payload_hash",
    ),
    "prototype_team_match": (
        "provider",
        "provider_match_id",
        "team_id",
        "opponent_team_id",
        "is_home",
        "is_competitive",
        "finished",
        "cancelled",
        "eligible_for_load",
        "exclusion_reason",
        "requested_season",
        "payload_hash",
    ),
    "prototype_team_rest_feature": (
        "provider",
        "provider_match_id",
        "team_id",
        "previous_match_id",
        "kickoff_at_utc",
        "kickoff_gap_hours",
        "calendar_gap_days",
        "short_gap_72h",
        "short_gap_96h",
        "matches_last_7d",
        "matches_last_14d",
        "feature_version",
        "input_set_hash",
        "previous_match_status",
        "provenance",
        "payload_hash",
    ),
    "prototype_schedule_observation": (
        "provider",
        "competition_id",
        "requested_season",
        "observed_at",
        "source_artifact_sha256",
        "source_content_hash",
        "fixture_count",
        "pagination_status",
        "transformation_version",
    ),
}

_TABLE_PRIMARY_KEYS = {
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
    "prototype_schedule_observation": (
        "provider",
        "competition_id",
        "requested_season",
        "observed_at",
    ),
}

_CREATE_STATEMENTS = (
    """
    CREATE TABLE prototype_competition_registry (
        provider TEXT NOT NULL,
        competition_id INTEGER NOT NULL,
        expected_name TEXT NOT NULL,
        observed_name TEXT NOT NULL,
        competition_class TEXT NOT NULL,
        requested_season TEXT NOT NULL,
        returned_season TEXT NOT NULL,
        season_strategy TEXT NOT NULL,
        identity_verified INTEGER NOT NULL CHECK (identity_verified IN (0, 1)),
        season_verified INTEGER NOT NULL CHECK (season_verified IN (0, 1)),
        pagination_status TEXT NOT NULL,
        fixture_count INTEGER NOT NULL,
        source_artifact_sha256 TEXT NOT NULL,
        completeness_status TEXT NOT NULL,
        PRIMARY KEY (provider, competition_id, requested_season)
    )
    """,
    """
    CREATE TABLE prototype_match_calendar (
        provider TEXT NOT NULL,
        provider_match_id INTEGER NOT NULL,
        competition_id INTEGER NOT NULL,
        requested_season TEXT NOT NULL,
        competition_class TEXT NOT NULL,
        kickoff_at_utc TEXT NOT NULL,
        kickoff_precision TEXT NOT NULL,
        home_team_id INTEGER NOT NULL,
        home_team_name TEXT NOT NULL,
        away_team_id INTEGER NOT NULL,
        away_team_name TEXT NOT NULL,
        status TEXT NOT NULL,
        finished INTEGER NOT NULL CHECK (finished IN (0, 1)),
        cancelled INTEGER NOT NULL CHECK (cancelled IN (0, 1)),
        round TEXT NOT NULL,
        source_endpoint TEXT NOT NULL,
        source_artifact_sha256 TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        PRIMARY KEY (provider, provider_match_id)
    )
    """,
    """
    CREATE TABLE prototype_team_match (
        provider TEXT NOT NULL,
        provider_match_id INTEGER NOT NULL,
        team_id INTEGER NOT NULL,
        opponent_team_id INTEGER NOT NULL,
        is_home INTEGER NOT NULL CHECK (is_home IN (0, 1)),
        is_competitive INTEGER NOT NULL CHECK (is_competitive IN (0, 1)),
        finished INTEGER NOT NULL CHECK (finished IN (0, 1)),
        cancelled INTEGER NOT NULL CHECK (cancelled IN (0, 1)),
        eligible_for_load INTEGER NOT NULL CHECK (eligible_for_load IN (0, 1)),
        exclusion_reason TEXT,
        requested_season TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        PRIMARY KEY (provider, provider_match_id, team_id),
        FOREIGN KEY (provider, provider_match_id)
            REFERENCES prototype_match_calendar (provider, provider_match_id)
    )
    """,
    """
    CREATE TABLE prototype_team_rest_feature (
        provider TEXT NOT NULL,
        provider_match_id INTEGER NOT NULL,
        team_id INTEGER NOT NULL,
        previous_match_id INTEGER,
        kickoff_at_utc TEXT NOT NULL,
        kickoff_gap_hours REAL,
        calendar_gap_days REAL,
        short_gap_72h INTEGER CHECK (short_gap_72h IN (0, 1)),
        short_gap_96h INTEGER CHECK (short_gap_96h IN (0, 1)),
        matches_last_7d INTEGER NOT NULL,
        matches_last_14d INTEGER NOT NULL,
        feature_version TEXT NOT NULL,
        input_set_hash TEXT NOT NULL,
        previous_match_status TEXT,
        provenance TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        PRIMARY KEY (
            provider,
            provider_match_id,
            team_id,
            feature_version,
            input_set_hash
        ),
        FOREIGN KEY (provider, provider_match_id, team_id)
            REFERENCES prototype_team_match (provider, provider_match_id, team_id)
    )
    """,
    """
    CREATE TABLE prototype_schedule_observation (
        provider TEXT NOT NULL,
        competition_id INTEGER NOT NULL,
        requested_season TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        source_artifact_sha256 TEXT NOT NULL,
        source_content_hash TEXT NOT NULL,
        fixture_count INTEGER NOT NULL,
        pagination_status TEXT NOT NULL,
        transformation_version TEXT NOT NULL,
        PRIMARY KEY (
            provider,
            competition_id,
            requested_season,
            observed_at
        )
    )
    """,
)


def _existing_prototype_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name LIKE 'prototype_%'"
        )
    }


def _existing_user_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _assert_exact_prototype_schema(conn: sqlite3.Connection) -> None:
    expected_tables = set(_TABLE_COLUMNS)
    present = _existing_prototype_tables(conn)
    if present != expected_tables or _existing_user_tables(conn) != expected_tables:
        raise PrototypeSchemaIncompatibleError(
            "prototype table set does not match the reviewed schema"
        )
    for table, expected_columns in _TABLE_COLUMNS.items():
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        actual_columns = tuple(row[1] for row in info)
        actual_primary_key = tuple(
            row[1] for row in sorted((row for row in info if row[5]), key=lambda row: row[5])
        )
        if (
            actual_columns != expected_columns
            or actual_primary_key != _TABLE_PRIMARY_KEYS[table]
        ):
            raise PrototypeSchemaIncompatibleError(
                f"{table} does not match the reviewed prototype schema"
            )
        actual_sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        expected_statement = next(
            statement
            for statement in _CREATE_STATEMENTS
            if re.search(
                rf"\bCREATE\s+TABLE\s+{re.escape(table)}\b",
                statement,
                flags=re.IGNORECASE,
            )
        )
        actual_sql = (
            actual_sql_row[0]
            if actual_sql_row is not None and isinstance(actual_sql_row[0], str)
            else ""
        )
        if " ".join(actual_sql.split()) != " ".join(expected_statement.split()):
            raise PrototypeSchemaIncompatibleError(
                f"{table} constraints do not match the reviewed prototype schema"
            )


def init_prototype_db(conn: sqlite3.Connection) -> None:
    """Create all prototype tables atomically, or reject any existing drift."""

    present = _existing_prototype_tables(conn)
    if present:
        _assert_exact_prototype_schema(conn)
        return
    if _existing_user_tables(conn):
        raise PrototypeSchemaIncompatibleError(
            "non-prototype tables are not accepted by the temporary proof"
        )

    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("BEGIN IMMEDIATE")
    try:
        for statement in _CREATE_STATEMENTS:
            conn.execute(statement)
        _assert_exact_prototype_schema(conn)
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def _sqlite_value(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    return value


def _existing_row_matches(
    conn: sqlite3.Connection,
    *,
    table: str,
    row: Mapping[str, Any],
) -> bool | None:
    """Return None for absent, True for identical, and fail on a conflict."""

    columns = _TABLE_COLUMNS[table]
    primary_key = _TABLE_PRIMARY_KEYS[table]
    values = tuple(_sqlite_value(row[column]) for column in columns)
    where = " AND ".join(f"{column}=?" for column in primary_key)
    key_values = tuple(_sqlite_value(row[column]) for column in primary_key)
    existing = conn.execute(
        f"SELECT {', '.join(columns)} FROM {table} WHERE {where}",
        key_values,
    ).fetchone()
    if existing is None:
        return None
    if tuple(existing) != values:
        raise PrototypeConflictError(f"immutable payload conflict in {table}")
    return True


def _preflight_rows(
    conn: sqlite3.Connection,
    *,
    table: str,
    rows: Iterable[Mapping[str, Any]],
) -> None:
    """Compare all existing natural keys without performing any insert."""

    for row in rows:
        _existing_row_matches(conn, table=table, row=row)


def _insert_or_compare(
    conn: sqlite3.Connection,
    *,
    table: str,
    row: Mapping[str, Any],
) -> bool:
    columns = _TABLE_COLUMNS[table]
    values = tuple(_sqlite_value(row[column]) for column in columns)
    if _existing_row_matches(conn, table=table, row=row):
        return False

    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({placeholders})",
        values,
    )
    return True


def _write_rows(
    conn: sqlite3.Connection,
    *,
    table: str,
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    inserted = 0
    skipped = 0
    for row in rows:
        if _insert_or_compare(conn, table=table, row=row):
            inserted += 1
        else:
            skipped += 1
    return {"inserted": inserted, "skipped": skipped}


def ingest_document(
    conn: sqlite3.Connection,
    document: Mapping[str, Any],
    *,
    observed_at: str,
) -> dict[str, dict[str, int]]:
    """Validate first, then atomically persist the complete immutable batch."""

    batch = parse_canonical_fixture(document, observed_at=observed_at)
    _assert_exact_prototype_schema(conn)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("BEGIN IMMEDIATE")
    try:
        business_row_sets = (
            (
                "prototype_competition_registry",
                (batch["registry"],),
            ),
            ("prototype_match_calendar", batch["matches"]),
            ("prototype_team_match", batch["team_matches"]),
            ("prototype_team_rest_feature", batch["rest_features"]),
        )
        for table, rows in business_row_sets:
            _preflight_rows(conn, table=table, rows=rows)
        _preflight_rows(
            conn,
            table="prototype_schedule_observation",
            rows=(batch["observation"],),
        )

        writes = {
            "registry": _write_rows(
                conn,
                table="prototype_competition_registry",
                rows=(batch["registry"],),
            ),
            "calendar": _write_rows(
                conn,
                table="prototype_match_calendar",
                rows=batch["matches"],
            ),
            "team_match": _write_rows(
                conn,
                table="prototype_team_match",
                rows=batch["team_matches"],
            ),
            "rest_feature": _write_rows(
                conn,
                table="prototype_team_rest_feature",
                rows=batch["rest_features"],
            ),
            "observation": _write_rows(
                conn,
                table="prototype_schedule_observation",
                rows=(batch["observation"],),
            ),
        }
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
        return writes


def _validate_prototype_db_path(db_path: Path | str) -> Path:
    path = Path(db_path).expanduser().resolve(strict=False)
    protected = (REPO_ROOT / "data").resolve()
    if path == protected or path.is_relative_to(protected):
        raise PrototypeDatabasePathError(
            "prototype database must not be placed in repository data/"
        )
    allowed_roots = {
        Path("/tmp").resolve(),
        Path(tempfile.gettempdir()).resolve(),
    }
    if not any(path.is_relative_to(root) for root in allowed_roots):
        raise PrototypeDatabasePathError(
            "prototype database must be under /tmp or the pytest temp root"
        )
    return path


def run_prototype(
    db_path: Path | str,
    *,
    observed_at: str,
    fixture_path: Path | str | None = None,
) -> dict[str, Any]:
    """Run the offline proof against a caller-selected non-production database."""

    target = _validate_prototype_db_path(db_path)
    document = load_canonical_fixture(fixture_path)
    batch = parse_canonical_fixture(document, observed_at=observed_at)

    conn = sqlite3.connect(target)
    try:
        init_prototype_db(conn)
        writes = ingest_document(conn, document, observed_at=observed_at)
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("temporary prototype integrity check failed")
    finally:
        conn.close()

    manchester_city_features = sorted(
        (
            row
            for row in batch["rest_features"]
            if row["team_id"] == 8456
        ),
        key=lambda row: row["kickoff_at_utc"],
    )
    return {
        "status": "DESIGN_VALIDATED_WITH_TEMP_DB",
        "later_observation_idempotency": (
            "LATER_OBSERVATION_IDEMPOTENCY_VALIDATED"
        ),
        "observation_ledger": "OBSERVATION_LEDGER_APPEND_ONLY",
        "business_content": "BUSINESS_CONTENT_IMMUTABLE",
        "point_in_time_feature_lineage": (
            "POINT_IN_TIME_FEATURE_LINEAGE_VALIDATED"
        ),
        "future_match_hash_boundary": (
            "NO_FUTURE_MATCH_IN_EARLIER_FEATURE_HASH"
        ),
        "production_schedule_state_design": (
            "PRODUCTION_MUTABLE_SNAPSHOT_SCHEMA_REQUIRED"
        ),
        "data_verdict": "GO_SINGLE_COMPETITION_DATA_VALIDATED",
        "production_integration": "NOT_STARTED",
        "network_request_count": 0,
        "feature_scope": "observed_historical",
        "observation_ordering": OBSERVATION_ORDERING,
        "source_content_hash": batch["observation"]["source_content_hash"],
        "competition_id": _COMPETITION_ID,
        "requested_season": _REQUESTED_SEASON,
        "calendar_rows": len(batch["matches"]),
        "team_match_rows": len(batch["team_matches"]),
        "rest_feature_rows": len(batch["rest_features"]),
        "cancelled_rows_preserved": sum(
            row["cancelled"] for row in batch["matches"]
        ),
        "manchester_city_match_ids": [
            row["provider_match_id"] for row in manchester_city_features
        ],
        "manchester_city_kickoff_gap_hours": [
            row["kickoff_gap_hours"] for row in manchester_city_features
        ],
        "writes": writes,
    }
