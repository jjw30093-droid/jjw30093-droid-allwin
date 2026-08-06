"""Deterministic FotMob season identity resolution.

The resolver deliberately separates the product's compact canonical key from
the provider parameter.  Cross-year and tournament seasons are never inferred
from the current month: they require a provider-advertised season or an
explicitly reviewed mapping.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Mapping


class SeasonResolutionError(RuntimeError):
    """Fixed, caller-safe season resolution failure."""


class SeasonKind(str, Enum):
    CALENDAR_YEAR = "calendar_year"
    CROSS_YEAR = "cross_year"
    TOURNAMENT_SEASON = "tournament_season"


class SeasonVerification(str, Enum):
    VERIFIED = "VERIFIED"
    SEASON_UNVERIFIED = "SEASON_UNVERIFIED"
    SEASON_MISMATCH = "SEASON_MISMATCH"


@dataclass(frozen=True)
class SeasonResolution:
    competition_id: int
    season_kind: SeasonKind
    canonical_season_key: str | None
    provider_season_parameter: str | None
    returned_season: str | None
    season_start: str | None
    season_end: str | None
    verification_status: SeasonVerification
    evidence: str

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["season_kind"] = self.season_kind.value
        value["verification_status"] = self.verification_status.value
        return value


_CALENDAR_RE = re.compile(r"^(?P<year>20\d{2})$")
_CROSS_PROVIDER_RE = re.compile(
    r"^(?P<start>20\d{2})/(?P<end>20\d{2})$"
)
_CROSS_CANONICAL_RE = re.compile(
    r"^(?P<start>20\d{2})/(?P<end>\d{2})$"
)


def _competition_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SeasonResolutionError("invalid season resolution input") from None
    return value


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SeasonResolutionError("invalid season resolution input") from None
    return value.strip()


def canonicalize_provider_season(
    season_kind: SeasonKind | str,
    provider_season: str,
) -> str:
    """Convert one validated provider label to the stable canonical key."""

    try:
        kind = SeasonKind(season_kind)
    except (TypeError, ValueError):
        raise SeasonResolutionError("invalid season resolution input") from None
    provider = _text(provider_season)
    if kind is SeasonKind.CALENDAR_YEAR:
        match = _CALENDAR_RE.fullmatch(provider or "")
        if match is None:
            raise SeasonResolutionError("invalid season resolution input") from None
        return match.group("year")
    if kind is SeasonKind.CROSS_YEAR:
        match = _CROSS_PROVIDER_RE.fullmatch(provider or "")
        if match is None:
            raise SeasonResolutionError("invalid season resolution input") from None
        start = int(match.group("start"))
        end = int(match.group("end"))
        if end != start + 1:
            raise SeasonResolutionError("invalid season resolution input") from None
        return f"{start:04d}/{end % 100:02d}"
    if (
        _CALENDAR_RE.fullmatch(provider or "") is None
        and _CROSS_PROVIDER_RE.fullmatch(provider or "") is None
    ):
        raise SeasonResolutionError("invalid season resolution input") from None
    return provider or ""


def provider_season_from_canonical(
    season_kind: SeasonKind | str,
    canonical_season: str,
) -> str:
    """Convert a canonical key to a provider parameter without date guessing."""

    try:
        kind = SeasonKind(season_kind)
    except (TypeError, ValueError):
        raise SeasonResolutionError("invalid season resolution input") from None
    canonical = _text(canonical_season)
    if kind is SeasonKind.CALENDAR_YEAR:
        if _CALENDAR_RE.fullmatch(canonical or "") is None:
            raise SeasonResolutionError("invalid season resolution input") from None
        return canonical or ""
    if kind is SeasonKind.CROSS_YEAR:
        match = _CROSS_CANONICAL_RE.fullmatch(canonical or "")
        if match is None:
            raise SeasonResolutionError("invalid season resolution input") from None
        start = int(match.group("start"))
        end = 2000 + int(match.group("end"))
        if end != start + 1:
            raise SeasonResolutionError("invalid season resolution input") from None
        return f"{start:04d}/{end:04d}"
    if (
        _CALENDAR_RE.fullmatch(canonical or "") is None
        and _CROSS_PROVIDER_RE.fullmatch(canonical or "") is None
    ):
        raise SeasonResolutionError("invalid season resolution input") from None
    return canonical or ""


def normalize_available_provider_seasons(
    values: Iterable[Any] | None,
) -> tuple[str, ...]:
    """Normalize the small set of season-list shapes observed from FotMob."""

    normalized: set[str] = set()
    for raw in values or ():
        value: Any = raw
        if isinstance(raw, Mapping):
            for key in ("name", "season", "label", "value"):
                if key in raw:
                    value = raw[key]
                    break
        if isinstance(value, str) and value.strip():
            normalized.add(value.strip())
    return tuple(sorted(normalized))


def resolve_provider_season(
    *,
    competition_id: int,
    season_kind: SeasonKind | str,
    canonical_season: str,
    provider_season: str,
    returned_season: str | None,
    available_provider_seasons: Iterable[Any] | None,
    verified_mapping: Mapping[str, str] | None = None,
    season_start: str | None = None,
    season_end: str | None = None,
) -> SeasonResolution:
    """Verify a requested provider season against independent source evidence."""

    cid = _competition_id(competition_id)
    try:
        kind = SeasonKind(season_kind)
        canonical = _text(canonical_season)
        provider = _text(provider_season)
        returned = _text(returned_season)
        expected_provider = provider_season_from_canonical(kind, canonical or "")
        canonical_from_provider = canonicalize_provider_season(kind, provider or "")
    except SeasonResolutionError:
        raise
    if expected_provider != provider or canonical_from_provider != canonical:
        return SeasonResolution(
            cid,
            kind,
            canonical,
            provider,
            returned,
            season_start,
            season_end,
            SeasonVerification.SEASON_UNVERIFIED,
            "canonical/provider season mapping is invalid",
        )
    if returned != provider:
        return SeasonResolution(
            cid,
            kind,
            canonical,
            provider,
            returned,
            season_start,
            season_end,
            SeasonVerification.SEASON_MISMATCH,
            "returned season does not equal requested provider season",
        )
    advertised = normalize_available_provider_seasons(
        available_provider_seasons
    )
    mapped = (
        verified_mapping is not None
        and verified_mapping.get(canonical or "") == provider
    )
    if provider not in advertised and not mapped:
        return SeasonResolution(
            cid,
            kind,
            canonical,
            provider,
            returned,
            season_start,
            season_end,
            SeasonVerification.SEASON_UNVERIFIED,
            "provider season is not advertised and has no verified mapping",
        )
    evidence = (
        "provider season advertised and returned exactly"
        if provider in advertised
        else "explicit verified canonical/provider mapping and exact return"
    )
    return SeasonResolution(
        cid,
        kind,
        canonical,
        provider,
        returned,
        season_start,
        season_end,
        SeasonVerification.VERIFIED,
        evidence,
    )


def resolve_current_season(
    *,
    competition_id: int,
    season_kind: SeasonKind | str,
    now: datetime,
    advertised_provider_seasons: Iterable[Any] | None = None,
    explicit_canonical_season: str | None = None,
    verified_mapping: Mapping[str, str] | None = None,
) -> SeasonResolution:
    """Resolve a current season without ever guessing a cross-year boundary."""

    cid = _competition_id(competition_id)
    try:
        kind = SeasonKind(season_kind)
    except (TypeError, ValueError):
        raise SeasonResolutionError("invalid season resolution input") from None
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise SeasonResolutionError("invalid season resolution input") from None

    if kind is SeasonKind.CALENDAR_YEAR:
        provider = str(now.year)
        return SeasonResolution(
            cid,
            kind,
            provider,
            provider,
            None,
            f"{now.year:04d}-01-01",
            f"{now.year:04d}-12-31",
            SeasonVerification.VERIFIED,
            "calendar-year rule",
        )

    if explicit_canonical_season is None:
        return SeasonResolution(
            cid,
            kind,
            None,
            None,
            None,
            None,
            None,
            SeasonVerification.SEASON_UNVERIFIED,
            "cross-year/tournament season requires provider evidence",
        )
    provider = provider_season_from_canonical(
        kind, explicit_canonical_season
    )
    advertised = normalize_available_provider_seasons(
        advertised_provider_seasons
    )
    mapped = (
        verified_mapping is not None
        and verified_mapping.get(explicit_canonical_season) == provider
    )
    if provider not in advertised and not mapped:
        return SeasonResolution(
            cid,
            kind,
            explicit_canonical_season,
            provider,
            None,
            None,
            None,
            SeasonVerification.SEASON_UNVERIFIED,
            "cross-year/tournament season lacks verified provider evidence",
        )
    return SeasonResolution(
        cid,
        kind,
        explicit_canonical_season,
        provider,
        None,
        None,
        None,
        SeasonVerification.VERIFIED,
        (
            "provider-advertised explicit current season"
            if provider in advertised
            else "explicit reviewed current-season mapping"
        ),
    )
