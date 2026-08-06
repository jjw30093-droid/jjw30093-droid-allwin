"""Repeatable, resumable FotMob season-coverage research probe.

This module is deliberately outside the production ingestion path.  Live mode
uses the repository's existing ``FotMobClient`` transport, while replay mode
only reads immutable artifacts from one private runtime directory.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.fotmob_client import FotMobClient  # noqa: E402
from backend.schedules.pagination import inspect_known_pagination  # noqa: E402
from backend.season_resolver import (  # noqa: E402
    SeasonKind,
    SeasonVerification,
    canonicalize_provider_season,
    resolve_provider_season,
)


class CoverageProbeError(RuntimeError):
    """Fixed, credential-safe coverage-probe failure."""


class RequestBudgetExhausted(CoverageProbeError):
    """Raised before transport when the durable request budget is exhausted."""


class ArtifactValidationError(CoverageProbeError):
    """Raised when a saved artifact cannot be safely replayed."""


class ProbeTransport(Protocol):
    def league_matches(self, competition_id: int, season: str = "") -> dict:
        ...

    def match_details(self, match_id: int) -> dict:
        ...

    def search_competitions(self, term: str) -> dict:
        ...


@dataclass(frozen=True)
class CompetitionSpec:
    key: str
    competition_id: int | None
    expected_name: str
    season_kind: SeasonKind
    structure: str
    earliest_year: int
    scope: str = "historical"


COMPETITIONS: dict[str, CompetitionSpec] = {
    "mls": CompetitionSpec(
        "mls", 130, "MLS", SeasonKind.CALENDAR_YEAR, "CALENDAR", 2015
    ),
    "j1": CompetitionSpec(
        "j1", 223, "J. League", SeasonKind.CALENDAR_YEAR,
        "CALENDAR_WITH_PHASES", 2015,
    ),
    "k_league_1": CompetitionSpec(
        "k_league_1", 9080, "K League 1", SeasonKind.CALENDAR_YEAR,
        "CALENDAR_WITH_SPLIT", 2015,
    ),
    "a_league": CompetitionSpec(
        "a_league", None, "A-League", SeasonKind.CROSS_YEAR,
        "CROSS_WITH_PLAYOFFS", 2015,
    ),
    "eredivisie": CompetitionSpec(
        "eredivisie", 57, "Eredivisie", SeasonKind.CROSS_YEAR, "CROSS", 2015
    ),
    "championship": CompetitionSpec(
        "championship", 48, "Championship", SeasonKind.CROSS_YEAR, "CROSS", 2015
    ),
    "liga_portugal": CompetitionSpec(
        "liga_portugal", 61, "Liga Portugal", SeasonKind.CROSS_YEAR, "CROSS", 2015
    ),
    "brazil_serie_a": CompetitionSpec(
        "brazil_serie_a", 268, "Serie A",
        SeasonKind.CALENDAR_YEAR, "CALENDAR", 2015,
    ),
    "champions_league": CompetitionSpec(
        "champions_league", 42, "Champions League",
        SeasonKind.TOURNAMENT_SEASON, "CROSS_WITH_STAGES", 2015,
    ),
    "europa_league": CompetitionSpec(
        "europa_league", 73, "Europa League",
        SeasonKind.TOURNAMENT_SEASON, "CROSS_WITH_STAGES", 2015,
    ),
    "conference_league": CompetitionSpec(
        "conference_league", 10216, "Conference League",
        SeasonKind.TOURNAMENT_SEASON, "CROSS_WITH_STAGES", 2021,
    ),
    "premier_league_control": CompetitionSpec(
        "premier_league_control", 47, "Premier League",
        SeasonKind.CROSS_YEAR, "CONTROL_SAFE_SEASON", 2024, "control",
    ),
    "eliteserien_control": CompetitionSpec(
        "eliteserien_control", 59, "Eliteserien",
        SeasonKind.CALENDAR_YEAR, "CURRENT_ONLY", 2026, "control",
    ),
    "allsvenskan_control": CompetitionSpec(
        "allsvenskan_control", 67, "Allsvenskan",
        SeasonKind.CALENDAR_YEAR, "CURRENT_STRUCTURE_ONLY", 2026, "control",
    ),
}

PILOT_KEYS = ("mls", "championship", "champions_league")
REQUIRED_ARTIFACTS = (
    "manifest.json",
    "request-ledger.jsonl",
    "competition-season-summary.csv",
    "sampled-match-coverage.csv",
    "field-coverage.csv",
    "season-structure.csv",
    "safe-start-seasons.csv",
    "anomalies.csv",
    "coverage-report.json",
    "coverage-report.md",
)

_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")
_STATUS_VALUES = {
    "started", "finished", "cancelled", "ongoing", "afterpenalties",
    "afterextratime", "postponed", "scheduled", "notstarted", "ft", "aet",
    "ap", "ns", "ppd", "canc", "abandoned",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_slug(value: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value):
        raise CoverageProbeError("invalid probe identifier") from None
    return value


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


def _write_json(path: Path, value: Any) -> None:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    _write_private(path, encoded + b"\n")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_json_bytes(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ArtifactValidationError("saved artifact is invalid") from None
    if not isinstance(value, dict):
        raise ArtifactValidationError("saved artifact is invalid") from None
    return value


def _details(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    details = raw.get("details")
    return details if isinstance(details, Mapping) else {}


def extract_available_seasons(raw: Mapping[str, Any]) -> tuple[str, ...]:
    """Read only explicit provider-advertised season collections."""

    candidates: list[Any] = []
    for container in (raw, _details(raw)):
        for key in ("allAvailableSeasons", "availableSeasons", "seasons"):
            value = container.get(key)
            if isinstance(value, list):
                candidates.extend(value)
    normalized: set[str] = set()
    for value in candidates:
        if isinstance(value, Mapping):
            value = next(
                (
                    value.get(key)
                    for key in ("name", "season", "label", "value")
                    if value.get(key) is not None
                ),
                None,
            )
        if isinstance(value, str) and value.strip():
            normalized.add(value.strip())
    return tuple(sorted(normalized, key=provider_season_sort_key))


def provider_season_sort_key(label: str) -> tuple[int, str]:
    match = re.match(r"^(20\d{2})", label)
    return (int(match.group(1)) if match else -1, label)


def season_kind_for_advertised_label(
    default_kind: SeasonKind, provider_season: str
) -> SeasonKind:
    """Classify an exact advertised label without consulting wall-clock month."""

    try:
        canonicalize_provider_season(default_kind, provider_season)
    except Exception:
        try:
            canonicalize_provider_season(
                SeasonKind.TOURNAMENT_SEASON, provider_season
            )
        except Exception:
            raise CoverageProbeError("season identity is not verified") from None
        return SeasonKind.TOURNAMENT_SEASON
    return default_kind


def _returned_season(raw: Mapping[str, Any]) -> str | None:
    value = _details(raw).get("selectedSeason")
    return value.strip() if isinstance(value, str) and value.strip() else None


def verify_competition_identity(
    raw: Mapping[str, Any], spec: CompetitionSpec
) -> dict[str, Any]:
    details = _details(raw)
    observed_id = _positive_int(details.get("id"))
    observed_name = details.get("name")
    name_match = (
        isinstance(observed_name, str)
        and observed_name.strip().casefold() == spec.expected_name.casefold()
    )
    id_match = spec.competition_id is not None and observed_id == spec.competition_id
    if id_match and name_match:
        status = "IDENTITY_VERIFIED"
    elif observed_id is None or not isinstance(observed_name, str):
        status = "IDENTITY_UNVERIFIED"
    else:
        status = "IDENTITY_MISMATCH"
    return {
        "status": status,
        "observed_id": observed_id,
        "observed_name": observed_name,
    }


def _fixture_list(raw: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    fixtures = raw.get("fixtures")
    all_matches = fixtures.get("allMatches") if isinstance(fixtures, Mapping) else None
    if not isinstance(all_matches, list) or not all_matches:
        raise CoverageProbeError("schedule fixtures are unavailable") from None
    if any(not isinstance(item, Mapping) for item in all_matches):
        raise CoverageProbeError("schedule fixtures are invalid") from None
    return all_matches


def _kickoff(fixture: Mapping[str, Any]) -> str | None:
    status = fixture.get("status")
    value = status.get("utcTime") if isinstance(status, Mapping) else None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_schedule(
    raw: Mapping[str, Any],
    spec: CompetitionSpec,
    requested_provider_season: str,
    advertised: Iterable[str],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    identity = verify_competition_identity(raw, spec)
    if identity["status"] != "IDENTITY_VERIFIED":
        raise CoverageProbeError("competition identity is not verified") from None
    returned = _returned_season(raw)
    try:
        effective_season_kind = season_kind_for_advertised_label(
            spec.season_kind, requested_provider_season
        )
        canonical = canonicalize_provider_season(
            effective_season_kind, requested_provider_season
        )
        season = resolve_provider_season(
            competition_id=spec.competition_id or 0,
            season_kind=effective_season_kind,
            canonical_season=canonical,
            provider_season=requested_provider_season,
            returned_season=returned,
            available_provider_seasons=advertised,
        )
    except Exception:
        raise CoverageProbeError("season identity is not verified") from None
    if season.verification_status is not SeasonVerification.VERIFIED:
        raise CoverageProbeError("season identity is not verified") from None
    pagination = inspect_known_pagination(raw)
    if pagination["status"] != "NOT_DETECTED":
        raise CoverageProbeError("schedule completeness is not verified") from None

    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for fixture in _fixture_list(raw):
        match_id = _positive_int(fixture.get("id"))
        home = fixture.get("home")
        away = fixture.get("away")
        status = fixture.get("status")
        home_id = _positive_int(home.get("id")) if isinstance(home, Mapping) else None
        away_id = _positive_int(away.get("id")) if isinstance(away, Mapping) else None
        kickoff = _kickoff(fixture)
        if (
            match_id is None
            or match_id in seen
            or home_id is None
            or away_id is None
            or home_id == away_id
            or kickoff is None
            or not isinstance(status, Mapping)
        ):
            raise CoverageProbeError("schedule fixture is invalid") from None
        seen.add(match_id)
        finished = status.get("finished") is True
        cancelled = status.get("cancelled") is True
        cancellation_reason = status.get("reason")
        has_explicit_cancellation_reason = (
            isinstance(cancellation_reason, Mapping)
            and any(
                isinstance(cancellation_reason.get(key), str)
                and cancellation_reason.get(key).strip()
                for key in ("long", "longKey", "short", "shortKey")
            )
        )
        if finished and cancelled and not has_explicit_cancellation_reason:
            raise CoverageProbeError("schedule fixture is invalid") from None
        raw_status = status.get("status")
        if (
            raw_status is not None
            and (
                not isinstance(raw_status, str)
                or raw_status.replace(" ", "").casefold() not in _STATUS_VALUES
            )
        ):
            raise CoverageProbeError("schedule fixture is invalid") from None
        rows.append(
            {
                "match_id": match_id,
                "kickoff_utc": kickoff,
                "home_team_id": home_id,
                "away_team_id": away_id,
                "finished": finished,
                "cancelled": cancelled,
                "stage": fixture.get("stage"),
                "round": fixture.get("round"),
                "group": fixture.get("group"),
            }
        )
    return rows, season.as_dict(), pagination


def deterministic_samples(
    fixtures: Iterable[Mapping[str, Any]], count: int = 3
) -> tuple[dict[str, Any], ...]:
    eligible = sorted(
        (
            dict(row)
            for row in fixtures
            if row.get("finished") is True and row.get("cancelled") is not True
        ),
        key=lambda row: (str(row["kickoff_utc"]), int(row["match_id"])),
    )
    if len(eligible) < count:
        return ()
    if count == 1:
        indexes = (len(eligible) // 2,)
    else:
        indexes = tuple(
            round(index * (len(eligible) - 1) / (count - 1))
            for index in range(count)
        )
    return tuple(eligible[index] for index in indexes)


def _walk(value: Any, prefix: str = "$") -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield from _walk(child, f"{prefix}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{prefix}[{index}]")
    else:
        yield prefix, value


_FIELD_SPECS: dict[str, tuple[str, tuple[str, ...]]] = {
    "team_xg": ("core_xg", ("expectedgoals",)),
    "team_xga": ("core_xg", ("expectedgoals",)),
    "shot_xg": ("shot", ("expectedgoals",)),
    "shot_xgot": ("shot", ("expectedgoalsontarget", "xgot")),
    "shots_on_target": ("shot", ("shotstarget", "shotsontarget")),
    "shots_off_target": ("shot", ("shotsofftarget",)),
    "possession": (
        "team_style", ("ballpossession", "ballpossesion", "possession")
    ),
    "accurate_passes": ("team_style", ("accuratepasses",)),
    "box_touches": (
        "team_style", ("touchesoppositionbox", "touchesoppbox", "boxtouches")
    ),
    "big_chances": ("team_style", ("bigchances", "bigchance")),
    "corners": ("team_style", ("corners", "cornerswon")),
    "accurate_crosses": ("team_style", ("accuratecrosses",)),
    "set_piece_goals": ("team_style", ("setpiecegoals",)),
    "tackles": ("team_style", ("tackleswon", "tackles")),
    "clearances": ("team_style", ("clearances",)),
    "clean_sheets": ("team_style", ("cleansheets",)),
    "high_turnovers": ("team_style", ("highturnovers",)),
    "final_third_wins": ("team_style", ("finalthirdwins",)),
    "events": ("match_context", (".events",)),
    "lineups": ("match_context", (".lineup",)),
    "player_rating": ("player", ("rating", "fotmobrating")),
    "player_position": ("player", ("position",)),
    "player_minutes": ("player", ("minutesplayed", "minutes")),
    "top_speed": ("physical", ("topspeed",)),
    "distance_covered": ("physical", ("distancecovered",)),
}


def _normalized_path(path: str) -> str:
    return re.sub(r"[^a-z0-9.]", "", path.casefold())


def _classify_values(values: list[Any], field: str) -> dict[str, int]:
    result = {
        "non_null": 0,
        "literal_zero": 0,
        "positive": 0,
        "invalid": 0,
        "string": 0,
        "percent": 0,
        "rank": 0,
    }
    if field in {"events", "lineups"}:
        result["non_null"] = sum(value is not None for value in values)
        return result
    for value in values:
        if value is None:
            continue
        result["non_null"] += 1
        if isinstance(value, bool):
            result["invalid"] += 1
        elif isinstance(value, (int, float)):
            if value == 0:
                result["literal_zero"] += 1
            elif value > 0:
                result["positive"] += 1
            else:
                result["invalid"] += 1
        elif isinstance(value, str):
            result["string"] += 1
            if "%" in value:
                result["percent"] += 1
            if re.fullmatch(r"\s*\d+(?:st|nd|rd|th)\s*", value, re.IGNORECASE):
                result["rank"] += 1
            if field in {"events", "lineups", "player_position"}:
                continue
            cleaned = value.strip().replace("%", "").split("(", 1)[0].strip()
            try:
                number = float(cleaned)
            except ValueError:
                if cleaned:
                    result["invalid"] += 1
            else:
                if number == 0:
                    result["literal_zero"] += 1
                elif number > 0:
                    result["positive"] += 1
                else:
                    result["invalid"] += 1
        else:
            result["invalid"] += 1
    return result


def extract_match_coverage(
    page_props: Mapping[str, Any],
    *,
    competition_key: str,
    provider_season: str,
    match_id: int,
) -> list[dict[str, Any]]:
    content = page_props.get("content")
    if not isinstance(content, Mapping):
        content = {}
    flattened = list(_walk(content, "$.content"))
    stats = content.get("stats")
    periods = stats.get("Periods") if isinstance(stats, Mapping) else None
    if isinstance(periods, Mapping):
        all_period = periods.get("All")
        categories = all_period.get("stats") if isinstance(all_period, Mapping) else None
        if isinstance(categories, list):
            for category_index, category in enumerate(categories):
                items = category.get("stats") if isinstance(category, Mapping) else None
                if not isinstance(items, list):
                    continue
                for item_index, item in enumerate(items):
                    if not isinstance(item, Mapping):
                        continue
                    raw_key = item.get("key") or item.get("title")
                    values = item.get("stats")
                    if isinstance(raw_key, str) and isinstance(values, list):
                        key = re.sub(r"[^a-zA-Z0-9_]", "", raw_key)
                        flattened.extend(
                            (
                                (
                                    "$.content.stats.Periods.All.stats"
                                    f"[{category_index}].stats[{item_index}].{key}[{index}]"
                                ),
                                value,
                            )
                            for index, value in enumerate(values)
                        )
    shotmap = content.get("shotmap")
    shots = shotmap.get("shots") if isinstance(shotmap, Mapping) else None
    if isinstance(shots, list):
        for index, shot in enumerate(shots):
            if not isinstance(shot, Mapping):
                continue
            event_type = shot.get("eventType")
            if isinstance(event_type, str):
                normalized_event = event_type.casefold()
                if normalized_event in {"goal", "attemptsaved"}:
                    flattened.append(
                        (f"$.content.shotmap.shots[{index}].shotsOnTarget", 1)
                    )
                elif normalized_event in {
                    "miss", "post", "blocked", "attemptblocked"
                }:
                    flattened.append(
                        (f"$.content.shotmap.shots[{index}].shotsOffTarget", 1)
                    )
    rows: list[dict[str, Any]] = []
    for field, (category, aliases) in _FIELD_SPECS.items():
        if field == "events":
            event_value = (
                content.get("matchFacts", {}).get("events")
                if isinstance(content.get("matchFacts"), Mapping)
                else None
            )
            matches = (
                [("$.content.matchFacts.events", event_value)]
                if event_value
                else []
            )
        elif field == "lineups":
            lineup_value = content.get("lineup")
            matches = [("$.content.lineup", lineup_value)] if lineup_value else []
        else:
            matches = [
                (path, value)
                for path, value in flattened
                if any(alias in _normalized_path(path) for alias in aliases)
                and not _normalized_path(path).endswith(
                    (".key", ".title", ".name", ".type", ".total")
                )
            ]
        if category in {"core_xg", "team_style"}:
            matches = [
                (path, value)
                for path, value in matches
                if ".content.stats.periods.all." in _normalized_path(path)
            ]
        elif category == "shot":
            matches = [
                (path, value)
                for path, value in matches
                if ".content.shotmap." in _normalized_path(path)
            ]
        elif category in {"player", "physical"}:
            matches = [
                (path, value)
                for path, value in matches
                if ".content.playerstats." in _normalized_path(path)
                or ".content.lineup." in _normalized_path(path)
            ]
        if field == "shot_xg":
            matches = [
                (path, value)
                for path, value in matches
                if ".shotmap." in _normalized_path(path)
                and "expectedgoalsontarget" not in _normalized_path(path)
            ]
        elif field == "team_xg":
            matches = [
                (path, value)
                for path, value in matches
                if ".stats.periods.all." in _normalized_path(path)
                and "expectedgoalsagainst" not in _normalized_path(path)
                and "expectedgoalsontarget" not in _normalized_path(path)
            ]
        elif field == "team_xga":
            matches = [
                (path, value)
                for path, value in matches
                if ".stats.periods.all." in _normalized_path(path)
                and "expectedgoalsontarget" not in _normalized_path(path)
            ]
        values = [value for _, value in matches]
        counts = _classify_values(values, field)
        rows.append(
            {
                "competition": competition_key,
                "provider_season": provider_season,
                "match_id": match_id,
                "category": category,
                "segment": {
                    "core_xg": "All",
                    "team_style": "All",
                    "shot": "shotmap",
                    "player": "playerStats",
                    "match_context": "content",
                    "physical": "playerStats",
                }[category],
                "field": field,
                "applicable_count": 1,
                "present_count": 1 if matches else 0,
                **counts,
                "path": matches[0][0] if matches else "",
            }
        )
    return rows


class DurableArtifactStore:
    def __init__(
        self,
        root: Path,
        run_id: str,
        *,
        max_attempts: int,
        mode: str,
    ) -> None:
        if mode not in {"live", "replay", "dry-run"}:
            raise CoverageProbeError("invalid probe mode") from None
        if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 0:
            raise CoverageProbeError("invalid request budget") from None
        self.run_dir = root / _safe_slug(run_id)
        self.raw_dir = self.run_dir / "raw"
        self.run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.raw_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.run_dir, 0o700)
        os.chmod(self.raw_dir, 0o700)
        self.max_attempts = max_attempts
        self.mode = mode
        self.ledger_path = self.run_dir / "request-ledger.jsonl"
        if not self.ledger_path.exists():
            _write_private(self.ledger_path, b"")

    def ledger(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                raise ArtifactValidationError("request ledger is invalid") from None
            if not isinstance(value, dict):
                raise ArtifactValidationError("request ledger is invalid") from None
            rows.append(value)
        return rows

    @property
    def attempts(self) -> int:
        return sum(1 for row in self.ledger() if row.get("phase") == "STARTED")

    def _append(self, row: Mapping[str, Any]) -> None:
        data = (
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        fd = os.open(self.ledger_path, os.O_WRONLY | os.O_APPEND, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(self.ledger_path, 0o600)

    def _artifact_path(self, request_key: str) -> Path:
        return self.raw_dir / f"{_safe_slug(request_key)}.json"

    def _completed(self, request_key: str) -> dict[str, Any] | None:
        succeeded = [
            row
            for row in self.ledger()
            if row.get("request_key") == request_key
            and row.get("phase") == "SUCCEEDED"
        ]
        if not succeeded:
            return None
        row = succeeded[-1]
        path = self._artifact_path(request_key)
        if not path.is_file():
            raise ArtifactValidationError("saved artifact is missing") from None
        data = path.read_bytes()
        if _sha256(data) != row.get("sha256"):
            raise ArtifactValidationError("saved artifact checksum mismatch") from None
        return _load_json_bytes(data)

    def acquire(
        self,
        request_key: str,
        operation: str,
        fetch: Callable[[], dict[str, Any]],
        *,
        retry_once: bool = True,
    ) -> dict[str, Any]:
        cached = self._completed(request_key)
        if cached is not None:
            return cached
        if self.mode == "replay":
            raise ArtifactValidationError("replay artifact is unavailable") from None
        if self.mode == "dry-run":
            raise CoverageProbeError("dry-run does not acquire artifacts") from None
        attempts = 2 if retry_once else 1
        for ordinal in range(1, attempts + 1):
            if self.attempts >= self.max_attempts:
                raise RequestBudgetExhausted("request budget exhausted") from None
            self._append(
                {
                    "phase": "STARTED",
                    "request_key": request_key,
                    "operation": operation,
                    "attempt": ordinal,
                    "at": _utc_now(),
                }
            )
            try:
                value = fetch()
                if not isinstance(value, dict):
                    raise CoverageProbeError("provider response is invalid")
                encoded = json.dumps(
                    value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
                _load_json_bytes(encoded)
                _write_private(self._artifact_path(request_key), encoded)
                digest = _sha256(encoded)
                self._append(
                    {
                        "phase": "SUCCEEDED",
                        "request_key": request_key,
                        "operation": operation,
                        "attempt": ordinal,
                        "sha256": digest,
                        "size": len(encoded),
                        "at": _utc_now(),
                    }
                )
                return value
            except Exception:
                self._append(
                    {
                        "phase": "FAILED",
                        "request_key": request_key,
                        "operation": operation,
                        "attempt": ordinal,
                        "error": "provider request failed",
                        "at": _utc_now(),
                    }
                )
                if ordinal == attempts:
                    raise CoverageProbeError("provider request failed") from None
        raise CoverageProbeError("provider request failed") from None


class LiveFotMobTransport:
    """Thin adapter around the already-hardened repository client."""

    def __init__(self) -> None:
        self.client = FotMobClient(max_retries=1)

    def league_matches(self, competition_id: int, season: str = "") -> dict:
        return self.client.league_matches(competition_id, season)

    def match_details(self, match_id: int) -> dict:
        return self.client.match_details(match_id)

    def search_competitions(self, term: str) -> dict:
        from urllib.parse import quote

        url = (
            "https://www.fotmob.com/api/data/search/suggest?"
            f"term={quote(term)}&lang=en&country=US"
        )
        response = self.client._get(url, headers={"Accept": "application/json"})
        decoded = self.client._decode_json_response(
            response, "league_matches"
        )
        if isinstance(decoded, list):
            return {"suggestions": decoded}
        if isinstance(decoded, dict):
            return decoded
        raise CoverageProbeError("provider response is invalid") from None


def discover_a_league_id(raw: Mapping[str, Any]) -> int | None:
    exact: set[int] = set()
    for path, value in _walk(raw):
        if not path.endswith(".name") or not isinstance(value, str):
            continue
        if value.strip().casefold() not in {"a-league men", "a-league"}:
            continue
        parent_path = path.rsplit(".", 1)[0]
        for candidate_path, candidate in _walk(raw):
            if (
                candidate_path == f"{parent_path}.id"
                and (competition_id := _positive_int(candidate)) is not None
            ):
                exact.add(competition_id)
    return next(iter(exact)) if len(exact) == 1 else None


def _control_seasons(spec: CompetitionSpec, discovered: tuple[str, ...]) -> tuple[str, ...]:
    if spec.key == "premier_league_control":
        return tuple(value for value in discovered if value == "2024/2025")
    if spec.scope == "control":
        return discovered[-1:] if discovered else ()
    eligible: list[str] = []
    for value in discovered:
        year, _ = provider_season_sort_key(value)
        if year >= spec.earliest_year:
            eligible.append(value)
    return tuple(eligible)


def _csv_write(path: Path, rows: list[Mapping[str, Any]], fields: tuple[str, ...]) -> None:
    from io import StringIO

    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    _write_private(path, buffer.getvalue().encode("utf-8"))


def _field_summary(sample_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in sample_rows:
        grouped.setdefault(
            (row["competition"], row["provider_season"], row["field"]), []
        ).append(row)
    result: list[dict[str, Any]] = []
    for (competition, season, field), rows in sorted(grouped.items()):
        applicable = sum(int(row["applicable_count"]) for row in rows)
        present = sum(int(row["present_count"]) for row in rows)
        result.append(
            {
                "competition": competition,
                "provider_season": season,
                "field": field,
                "category": rows[0]["category"],
                "sample_count": len(rows),
                "applicable_count": applicable,
                "present_count": present,
                "non_null_count": sum(int(row["non_null"]) for row in rows),
                "literal_zero_count": sum(int(row["literal_zero"]) for row in rows),
                "zero_count": sum(int(row["literal_zero"]) for row in rows),
                "positive_count": sum(int(row["positive"]) for row in rows),
                "invalid_count": sum(int(row["invalid"]) for row in rows),
                "string_count": sum(int(row["string"]) for row in rows),
                "percent_count": sum(int(row["percent"]) for row in rows),
                "rank_count": sum(int(row["rank"]) for row in rows),
                "segment": rows[0]["segment"],
                "coverage": round(present / applicable, 6) if applicable else None,
                "sample_match_ids": "|".join(str(row["match_id"]) for row in rows),
                "paths": "|".join(sorted({row["path"] for row in rows if row["path"]})),
            }
        )
    return result


def safe_start_rows(field_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_comp_field: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in field_rows:
        by_comp_field.setdefault((row["competition"], row["field"]), []).append(row)
    result: list[dict[str, Any]] = []
    for (competition, field), rows in sorted(by_comp_field.items()):
        ordered = sorted(rows, key=lambda row: provider_season_sort_key(row["provider_season"]))
        if field in {"team_xg", "team_xga", "shot_xg"}:
            threshold = 1.0
        elif rows[0]["category"] == "team_style":
            threshold = 0.9
        else:
            threshold = 1.0
        usable = [row for row in ordered if (row["coverage"] or 0.0) >= threshold]
        first_seen = next((row["provider_season"] for row in ordered if row["present_count"]), "")
        first_usable = usable[0]["provider_season"] if usable else ""
        first_contiguous = ""
        confidence = "UNVERIFIED"
        reason = "no sampled usable season"
        for index, candidate in enumerate(ordered):
            tail = ordered[index:]
            if (
                len(tail) >= 2
                and all((item["coverage"] or 0.0) >= threshold for item in tail)
            ):
                first_contiguous = candidate["provider_season"]
                confidence = "SAMPLED_SAFE"
                reason = "at least two consecutive sampled completed seasons"
                break
        if not first_contiguous and len(usable) == 1:
            confidence = "PROVISIONAL"
            reason = "only one sampled usable season"
        result.append(
            {
                "competition": competition,
                "field": field,
                "first_seen": first_seen,
                "first_usable": first_usable,
                "first_contiguous_safe": first_contiguous,
                "latest": ordered[-1]["provider_season"] if ordered else "",
                "confidence": confidence,
                "reason": reason,
                "breaks": "|".join(
                    row["provider_season"]
                    for row in ordered
                    if (row["coverage"] or 0.0) < threshold
                ),
            }
        )
    return result


def coverage_anomalies(field_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in field_rows:
        if row["invalid_count"]:
            result.append(
                {
                    "competition": row["competition"],
                    "provider_season": row["provider_season"],
                    "severity": "REVIEW",
                    "code": "INVALID_OR_MIXED_STAT_VALUE",
                    "detail": (
                        f"{row['field']} invalid_count={row['invalid_count']} "
                        f"path={row['paths']}"
                    ),
                }
            )
        if row["rank_count"]:
            result.append(
                {
                    "competition": row["competition"],
                    "provider_season": row["provider_season"],
                    "severity": "REVIEW",
                    "code": "RANK_ENCODING_OBSERVED",
                    "detail": f"{row['field']} rank_count={row['rank_count']}",
                }
            )

    by_comp_xgot: dict[str, list[dict[str, Any]]] = {}
    for row in field_rows:
        if row["field"] == "shot_xgot":
            by_comp_xgot.setdefault(row["competition"], []).append(row)
    for competition, rows in by_comp_xgot.items():
        ordered = sorted(rows, key=lambda item: provider_season_sort_key(item["provider_season"]))
        ratios = [
            (
                row["literal_zero_count"] / row["non_null_count"]
                if row["non_null_count"]
                else 0.0
            )
            for row in ordered
        ]
        if ratios and max(ratios) - min(ratios) >= 0.4:
            result.append(
                {
                    "competition": competition,
                    "provider_season": (
                        f"{ordered[0]['provider_season']}.."
                        f"{ordered[-1]['provider_season']}"
                    ),
                    "severity": "REVIEW",
                    "code": "XGOT_NULL_ZERO_ENCODING_SHIFT",
                    "detail": "sampled literal-zero ratio changed by at least 0.4",
                }
            )
    return result


class CoverageProbe:
    def __init__(self, store: DurableArtifactStore, transport: ProbeTransport | None) -> None:
        self.store = store
        self.transport = transport

    def _fetch(
        self, request_key: str, operation: str, callback: Callable[[], dict[str, Any]]
    ) -> dict[str, Any]:
        if self.transport is None and self.store.mode == "live":
            raise CoverageProbeError("live transport is unavailable") from None
        return self.store.acquire(request_key, operation, callback)

    def _competition_id(self, spec: CompetitionSpec) -> int:
        if spec.competition_id is not None:
            return spec.competition_id
        for request_key, term in (
            (f"{spec.key}.identity-search", "A-League Men"),
            (f"{spec.key}.identity-search.a-league", "A-League"),
        ):
            raw = self._fetch(
                request_key,
                "competition_search",
                lambda query=term: self.transport.search_competitions(query),  # type: ignore[union-attr]
            )
            discovered = discover_a_league_id(raw)
            if discovered is not None:
                return discovered
        raise CoverageProbeError("competition identity is not verified") from None

    def run(
        self,
        selected_keys: Iterable[str],
        selected_seasons: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        season_filter = set(selected_seasons or ())
        if self.store.mode == "dry-run":
            result = {
                "mode": "dry-run",
                "selected_competitions": list(selected_keys),
                "selected_seasons": sorted(season_filter),
                "attempts": self.store.attempts,
                "would_request": True,
            }
            _write_json(self.store.run_dir / "manifest.json", result)
            return result

        summaries: list[dict[str, Any]] = []
        sampled_rows: list[dict[str, Any]] = []
        structures: list[dict[str, Any]] = []
        anomalies: list[dict[str, Any]] = []
        per_competition: dict[str, Any] = {}

        for key in selected_keys:
            if key not in COMPETITIONS:
                raise CoverageProbeError("unknown competition") from None
            spec = COMPETITIONS[key]
            try:
                competition_id = self._competition_id(spec)
                effective_spec = CompetitionSpec(
                    spec.key, competition_id, spec.expected_name, spec.season_kind,
                    spec.structure, spec.earliest_year, spec.scope,
                )
                discovery = self._fetch(
                    f"{key}.discovery",
                    "league_schedule",
                    lambda cid=competition_id: self.transport.league_matches(cid, ""),  # type: ignore[union-attr]
                )
                identity = verify_competition_identity(discovery, effective_spec)
                if identity["status"] != "IDENTITY_VERIFIED":
                    raise CoverageProbeError("competition identity is not verified")
                seasons = _control_seasons(
                    spec, extract_available_seasons(discovery)
                )
                if season_filter:
                    seasons = tuple(
                        season for season in seasons if season in season_filter
                    )
                if not seasons:
                    raise CoverageProbeError("provider seasons are unavailable")
                comp_seasons: list[str] = []
                for provider_season in seasons:
                    schedule = self._fetch(
                        f"{key}.season.{provider_season.replace('/', '-')}",
                        "league_schedule",
                        lambda cid=competition_id, season=provider_season:
                        self.transport.league_matches(cid, season),  # type: ignore[union-attr]
                    )
                    fixtures, season_resolution, pagination = validate_schedule(
                        schedule, effective_spec, provider_season, seasons
                    )
                    samples = deterministic_samples(fixtures)
                    active_partial = (
                        not samples
                        or any(
                            row["finished"] is not True
                            and row["cancelled"] is not True
                            for row in fixtures
                        )
                    )
                    kickoff_values = sorted(
                        str(row["kickoff_utc"]) for row in fixtures
                    )
                    summaries.append(
                        {
                            "competition": key,
                            "competition_id": competition_id,
                            "provider_season": provider_season,
                            "canonical_season": season_resolution["canonical_season_key"],
                            "season_status": season_resolution["verification_status"],
                            "season_kind": season_resolution["season_kind"],
                            "fixture_count": len(fixtures),
                            "sample_count": len(samples),
                            "active_partial": active_partial,
                            "season_start": kickoff_values[0],
                            "season_end": kickoff_values[-1],
                            "pagination": pagination["status"],
                        }
                    )
                    structures.append(
                        {
                            "competition": key,
                            "provider_season": provider_season,
                            "declared_structure": spec.structure,
                            "stage_values": "|".join(
                                sorted({str(row["stage"]) for row in fixtures if row["stage"]})
                            ),
                            "round_values": "|".join(
                                sorted({str(row["round"]) for row in fixtures if row["round"]})[:30]
                            ),
                            "group_values": "|".join(
                                sorted({str(row["group"]) for row in fixtures if row["group"]})
                            ),
                        }
                    )
                    if (
                        season_resolution["season_kind"]
                        != spec.season_kind.value
                    ):
                        anomalies.append(
                            {
                                "competition": key,
                                "provider_season": provider_season,
                                "severity": "REVIEW",
                                "code": "SEASON_REGIME_TRANSITION",
                                "detail": (
                                    f"default={spec.season_kind.value};"
                                    f"advertised={season_resolution['season_kind']}"
                                ),
                            }
                        )
                    if active_partial:
                        anomalies.append(
                            {
                                "competition": key,
                                "provider_season": provider_season,
                                "severity": "INFO",
                                "code": "INSUFFICIENT_COMPLETED_SAMPLE",
                                "detail": "season excluded from sampled continuity",
                            }
                        )
                        continue
                    comp_seasons.append(provider_season)
                    for sample in samples:
                        match_id = int(sample["match_id"])
                        page_props = self._fetch(
                            f"{key}.season.{provider_season.replace('/', '-')}.match.{match_id}",
                            "match_details",
                            lambda mid=match_id: self.transport.match_details(mid),  # type: ignore[union-attr]
                        )
                        sampled_rows.extend(
                            extract_match_coverage(
                                page_props,
                                competition_key=key,
                                provider_season=provider_season,
                                match_id=match_id,
                            )
                        )
                per_competition[key] = {
                    "status": "COMPLETED",
                    "competition_id": competition_id,
                    "advertised_season_count": len(seasons),
                    "sampled_seasons": comp_seasons,
                }
            except CoverageProbeError as exc:
                per_competition[key] = {
                    "status": "FAILED",
                    "error": str(exc),
                }
                anomalies.append(
                    {
                        "competition": key,
                        "provider_season": "",
                        "severity": "BLOCKING",
                        "code": "COMPETITION_PROBE_FAILED",
                        "detail": str(exc),
                    }
                )

        field_rows = _field_summary(sampled_rows)
        anomalies.extend(coverage_anomalies(field_rows))
        safe_rows = safe_start_rows(field_rows)
        self._write_outputs(
            summaries, sampled_rows, field_rows, structures, safe_rows,
            anomalies, per_competition,
        )
        return {
            "mode": self.store.mode,
            "attempts": self.store.attempts,
            "competitions": per_competition,
            "p0": 0,
            "p1": sum(
                1 for value in per_competition.values()
                if value["status"] != "COMPLETED"
            ),
        }

    def _write_outputs(
        self,
        summaries: list[dict[str, Any]],
        sampled: list[dict[str, Any]],
        fields: list[dict[str, Any]],
        structures: list[dict[str, Any]],
        safe: list[dict[str, Any]],
        anomalies: list[dict[str, Any]],
        competitions: Mapping[str, Any],
    ) -> None:
        _csv_write(
            self.store.run_dir / "competition-season-summary.csv", summaries,
            (
                "competition", "competition_id", "provider_season",
                "canonical_season", "season_status", "fixture_count",
                "season_kind", "sample_count", "active_partial", "season_start",
                "season_end", "pagination",
            ),
        )
        _csv_write(
            self.store.run_dir / "sampled-match-coverage.csv", sampled,
            (
                "competition", "provider_season", "match_id", "category",
                "segment", "field",
                "applicable_count", "present_count", "non_null", "literal_zero",
                "positive", "invalid", "string", "percent", "rank", "path",
            ),
        )
        _csv_write(
            self.store.run_dir / "field-coverage.csv", fields,
            (
                "competition", "provider_season", "field", "category",
                "segment",
                "sample_count", "applicable_count", "present_count",
                "non_null_count", "literal_zero_count", "zero_count",
                "positive_count", "invalid_count", "string_count",
                "percent_count", "rank_count", "coverage",
                "sample_match_ids", "paths",
            ),
        )
        _csv_write(
            self.store.run_dir / "season-structure.csv", structures,
            (
                "competition", "provider_season", "declared_structure",
                "stage_values", "round_values", "group_values",
            ),
        )
        _csv_write(
            self.store.run_dir / "safe-start-seasons.csv", safe,
            (
                "competition", "field", "first_seen", "first_usable",
                "first_contiguous_safe", "latest", "confidence", "reason", "breaks",
            ),
        )
        _csv_write(
            self.store.run_dir / "anomalies.csv", anomalies,
            (
                "competition", "provider_season", "severity", "code", "detail",
            ),
        )
        report = {
            "schema_version": 1,
            "generated_at": _utc_now(),
            "mode": self.store.mode,
            "transport_attempts": self.store.attempts,
            "competitions": competitions,
            "season_count": len(summaries),
            "sampled_match_count": len({row["match_id"] for row in sampled}),
            "anomaly_count": len(anomalies),
            "safe_start": safe,
        }
        _write_json(self.store.run_dir / "coverage-report.json", report)
        markdown = [
            "# Multi-league season coverage probe",
            "",
            f"- Mode: `{self.store.mode}`",
            f"- Transport attempts: {self.store.attempts}",
            f"- Seasons validated: {len(summaries)}",
            f"- Sampled matches: {report['sampled_match_count']}",
            f"- Anomalies: {len(anomalies)}",
            "",
            "## Competition results",
            "",
        ]
        for key, value in competitions.items():
            markdown.append(f"- {key}: `{value['status']}`")
        markdown.extend(
            [
                "",
                "Three deterministic matches per completed season support only "
                "`SAMPLED_SAFE`; they are not a proof of full-season completeness.",
                "",
            ]
        )
        _write_private(
            self.store.run_dir / "coverage-report.md",
            "\n".join(markdown).encode("utf-8"),
        )
        manifest = {
            "schema_version": 1,
            "mode": self.store.mode,
            "generated_at": _utc_now(),
            "artifacts": {},
        }
        for name in REQUIRED_ARTIFACTS:
            if name == "manifest.json":
                continue
            path = self.store.run_dir / name
            data = path.read_bytes()
            manifest["artifacts"][name] = {
                "sha256": _sha256(data),
                "size": len(data),
            }
        _write_json(self.store.run_dir / "manifest.json", manifest)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--replay", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=_REPO_ROOT / "runtime" / "research" / "league-coverage",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--competition", action="append", choices=tuple(COMPETITIONS))
    parser.add_argument("--season", action="append")
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--max-attempts", type=int, default=800)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    mode = "live" if args.live else "replay" if args.replay else "dry-run"
    selected = (
        tuple(args.competition)
        if args.competition
        else PILOT_KEYS if args.pilot else tuple(COMPETITIONS)
    )
    store = DurableArtifactStore(
        args.output_root, args.run_id, max_attempts=args.max_attempts, mode=mode
    )
    transport = LiveFotMobTransport() if mode == "live" else None
    result = CoverageProbe(store, transport).run(selected, args.season)
    print(
        json.dumps(
            {
                "mode": result["mode"],
                "attempts": result.get("attempts", 0),
                "competition_count": len(result.get("competitions", {})),
                "p1": result.get("p1", 0),
            },
            sort_keys=True,
        )
    )
    return 0 if result.get("p1", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
