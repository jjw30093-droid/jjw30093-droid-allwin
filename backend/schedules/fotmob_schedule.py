"""Pure FotMob schedule normalization with no provider transport."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from backend.schedules.pagination import inspect_known_pagination
from backend.schedules import state as schedule


class FotMobScheduleDataError(RuntimeError):
    """Saved schedule data does not satisfy the production state contract."""


SUPPORTED_STATUS_FLAG_MATRIX = {
    "NS": (False, False, False),
    "FT": (True, True, False),
    "AET": (True, True, False),
    "Pen": (True, True, False),
    "Can": (False, False, True),
}


@dataclass(frozen=True)
class NormalizedScheduleRow:
    provider_match_id: str
    canonical_match_id: int | None
    state: Mapping[str, Any]
    state_content_hash: str
    source_updated_at: str | None
    snapshot_provenance: str

    def as_state_input(self) -> dict[str, Any]:
        return {
            "provider_match_id": self.provider_match_id,
            "canonical_match_id": self.canonical_match_id,
            "state": dict(self.state),
            "source_updated_at": self.source_updated_at,
            "snapshot_provenance": self.snapshot_provenance,
        }


def _exact_keys(value: Any, keys: frozenset[str]) -> bool:
    return isinstance(value, dict) and frozenset(value) == keys


def _positive_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise FotMobScheduleDataError(
            "saved schedule normalization failed"
        ) from None
    return value


def _provider_positive_id(value: Any) -> int:
    if isinstance(value, bool):
        raise FotMobScheduleDataError(
            "saved schedule normalization failed"
        ) from None
    if isinstance(value, int):
        return _positive_id(value)
    if (
        not isinstance(value, str)
        or not value
        or not value.isascii()
        or not value.isdecimal()
    ):
        raise FotMobScheduleDataError(
            "saved schedule normalization failed"
        ) from None
    return _positive_id(int(value))


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FotMobScheduleDataError(
            "saved schedule normalization failed"
        ) from None
    return value.strip()


def _boolean(value: Any) -> bool:
    if not isinstance(value, bool):
        raise FotMobScheduleDataError(
            "saved schedule normalization failed"
        ) from None
    return value


def _status_flags(
    *,
    short: Any,
    started: Any,
    finished: Any,
    cancelled: Any,
) -> tuple[str, bool, bool, bool]:
    short_value = _text(short)
    started_value = _boolean(started)
    finished_value = _boolean(finished)
    cancelled_value = _boolean(cancelled)
    if SUPPORTED_STATUS_FLAG_MATRIX.get(short_value) != (
        started_value,
        finished_value,
        cancelled_value,
    ):
        raise FotMobScheduleDataError(
            "saved schedule normalization failed"
        ) from None
    return (
        short_value,
        started_value,
        finished_value,
        cancelled_value,
    )


def _normalized_row(
    *,
    match_id: int,
    round_value: Any,
    home: Any,
    away: Any,
    short: Any,
    started: Any,
    finished: Any,
    cancelled: Any,
    kickoff: Any,
    expected_competition_id: int,
    requested_season: str,
    competition_class: str,
    competition_class_verified: bool,
    snapshot_provenance: str,
) -> NormalizedScheduleRow:
    if (
        not _exact_keys(home, frozenset({"id", "name"}))
        or not _exact_keys(away, frozenset({"id", "name"}))
    ):
        raise FotMobScheduleDataError(
            "saved schedule normalization failed"
        ) from None
    home_id = _provider_positive_id(home["id"])
    away_id = _provider_positive_id(away["id"])
    if home_id == away_id:
        raise FotMobScheduleDataError(
            "saved schedule normalization failed"
        ) from None
    short_value, _, finished_value, cancelled_value = _status_flags(
        short=short,
        started=started,
        finished=finished,
        cancelled=cancelled,
    )
    state = {
        "kickoff_at_utc": schedule._utc(kickoff),
        "kickoff_precision": "exact",
        "status": short_value,
        "finished": finished_value,
        "cancelled": cancelled_value,
        "home_team_id": home_id,
        "home_team_name": _text(home["name"]),
        "away_team_id": away_id,
        "away_team_name": _text(away["name"]),
        "competition_id": str(expected_competition_id),
        "season_label": _text(requested_season),
        "round_label": _text(round_value),
        "stage_label": None,
        "competition_class": competition_class,
        "competition_verified": competition_class_verified,
    }
    return NormalizedScheduleRow(
        provider_match_id=str(match_id),
        canonical_match_id=None,
        state=state,
        state_content_hash=schedule.build_state_content_hash(state),
        source_updated_at=None,
        snapshot_provenance=snapshot_provenance,
    )


def normalize_canonical_schedule_payload(
    payload: Any,
    *,
    expected_competition_id: int,
    requested_season: str,
    competition_class: str,
    competition_class_verified: bool,
    artifact_schema_version: str,
) -> tuple[NormalizedScheduleRow, ...]:
    """Validate the reviewed canonical shape and return stable sorted rows.

    赛季回声(selectedSeason == requested_season)与联赛 id 校验的统一实现在
    backend/ingest/season_identity.py(2026-08-25 收敛,CLAUDE.md §6.3);本
    函数(及下方 normalize_raw_schedule_payload)是同一判定嵌在整块结构化
    校验里的单行比较,不是那 30 行函数的拷贝——保留内联以维持本子系统
    "一次性判定 + 结构化拒绝"的既有形状,判定口径与统一实现一致。
    """

    failed = False
    rows: list[NormalizedScheduleRow] = []
    try:
        if not _exact_keys(
            payload,
            frozenset({"competition", "fixtures", "provenance"}),
        ):
            raise FotMobScheduleDataError(
                "saved schedule normalization failed"
            )
        competition = payload["competition"]
        provenance = payload["provenance"]
        fixtures = payload["fixtures"]
        if (
            not _exact_keys(
                competition,
                frozenset({"id", "name", "selectedSeason"}),
            )
            or not _exact_keys(
                provenance,
                frozenset(
                    {
                        "description",
                        "pagination_status",
                        "source_artifact_sha256",
                        "transformation_version",
                    }
                ),
            )
            or not isinstance(fixtures, list)
            or not fixtures
            or _positive_id(expected_competition_id) != competition["id"]
            or _text(competition["name"]) == ""
            or _text(requested_season) != _text(competition["selectedSeason"])
            or _text(artifact_schema_version)
            != _text(provenance["transformation_version"])
            or provenance["pagination_status"]
            != "NOT_DETECTED_FOR_SAVED_RESPONSE"
            or _text(provenance["description"]) == ""
            or len(_text(provenance["source_artifact_sha256"])) != 64
            or any(
                character not in "0123456789abcdef"
                for character in provenance["source_artifact_sha256"]
            )
            or competition_class not in schedule.COMPETITIVE_CLASSES
            or competition_class_verified is not True
        ):
            raise FotMobScheduleDataError(
                "saved schedule normalization failed"
            )

        seen: set[int] = set()
        for raw in fixtures:
            if not _exact_keys(
                raw,
                frozenset({"id", "round", "home", "away", "status"}),
            ):
                raise FotMobScheduleDataError(
                    "saved schedule normalization failed"
                )
            match_id = _positive_id(raw["id"])
            if match_id in seen:
                raise FotMobScheduleDataError(
                    "saved schedule normalization failed"
                )
            seen.add(match_id)
            home = raw["home"]
            away = raw["away"]
            status = raw["status"]
            if (
                not _exact_keys(home, frozenset({"id", "name"}))
                or not _exact_keys(away, frozenset({"id", "name"}))
                or not _exact_keys(
                    status,
                    frozenset(
                        {
                            "cancelled",
                            "finished",
                            "short",
                            "started",
                            "utcTime",
                        }
                    ),
                )
            ):
                raise FotMobScheduleDataError(
                    "saved schedule normalization failed"
                )
            rows.append(
                _normalized_row(
                    match_id=match_id,
                    round_value=raw["round"],
                    home=home,
                    away=away,
                    short=status["short"],
                    started=status["started"],
                    finished=status["finished"],
                    cancelled=status["cancelled"],
                    kickoff=status["utcTime"],
                    expected_competition_id=expected_competition_id,
                    requested_season=requested_season,
                    competition_class=competition_class,
                    competition_class_verified=competition_class_verified,
                    snapshot_provenance=(
                        "offline_saved_artifact:validated_canonical_schedule"
                    ),
                )
            )
        rows.sort(key=lambda item: int(item.provider_match_id))
    except (FotMobScheduleDataError, schedule.ScheduleStateError):
        failed = True
    except Exception:
        failed = True
    if failed:
        raise FotMobScheduleDataError(
            "saved schedule normalization failed"
        ) from None
    return tuple(rows)


def normalize_raw_schedule_payload(
    payload: Any,
    *,
    expected_competition_id: int,
    expected_competition_name: str,
    requested_season: str,
    competition_class: str,
    competition_class_verified: bool,
    artifact_schema_version: str,
) -> tuple[NormalizedScheduleRow, ...]:
    """Validate the saved raw-provider projection and return stable rows."""

    failed = False
    rows: list[NormalizedScheduleRow] = []
    try:
        if not _exact_keys(
            payload,
            frozenset({"artifactProvenance", "details", "fixtures"}),
        ):
            raise FotMobScheduleDataError(
                "saved schedule normalization failed"
            )
        provenance = payload["artifactProvenance"]
        details = payload["details"]
        fixtures_container = payload["fixtures"]
        if (
            not _exact_keys(
                provenance,
                frozenset(
                    {
                        "description",
                        "sourceArtifactSha256",
                        "transformationVersion",
                    }
                ),
            )
            or not _exact_keys(
                details,
                frozenset({"id", "name", "selectedSeason"}),
            )
            or not isinstance(fixtures_container, dict)
            or "allMatches" not in fixtures_container
            or _provider_positive_id(details["id"])
            != _positive_id(expected_competition_id)
            or _text(details["name"]) != _text(expected_competition_name)
            or _text(details["selectedSeason"]) != _text(requested_season)
            or _text(provenance["transformationVersion"])
            != _text(artifact_schema_version)
            or _text(provenance["description"]) == ""
            or len(_text(provenance["sourceArtifactSha256"])) != 64
            or any(
                character not in "0123456789abcdef"
                for character in provenance["sourceArtifactSha256"]
            )
            or competition_class not in schedule.COMPETITIVE_CLASSES
            or competition_class_verified is not True
            or inspect_known_pagination(payload)["status"] != "NOT_DETECTED"
        ):
            raise FotMobScheduleDataError(
                "saved schedule normalization failed"
            )
        fixtures = fixtures_container["allMatches"]
        if not isinstance(fixtures, list) or not fixtures:
            raise FotMobScheduleDataError(
                "saved schedule normalization failed"
            )
        seen: set[int] = set()
        for raw in fixtures:
            if not _exact_keys(
                raw,
                frozenset({"id", "round", "home", "away", "status"}),
            ):
                raise FotMobScheduleDataError(
                    "saved schedule normalization failed"
                )
            match_id = _provider_positive_id(raw["id"])
            if match_id in seen:
                raise FotMobScheduleDataError(
                    "saved schedule normalization failed"
                )
            seen.add(match_id)
            status = raw["status"]
            if (
                not _exact_keys(
                    status,
                    frozenset(
                        {
                            "cancelled",
                            "finished",
                            "reason",
                            "started",
                            "utcTime",
                        }
                    ),
                )
                or not _exact_keys(
                    status["reason"],
                    frozenset({"short"}),
                )
            ):
                raise FotMobScheduleDataError(
                    "saved schedule normalization failed"
                )
            rows.append(
                _normalized_row(
                    match_id=match_id,
                    round_value=raw["round"],
                    home=raw["home"],
                    away=raw["away"],
                    short=status["reason"]["short"],
                    started=status["started"],
                    finished=status["finished"],
                    cancelled=status["cancelled"],
                    kickoff=status["utcTime"],
                    expected_competition_id=expected_competition_id,
                    requested_season=requested_season,
                    competition_class=competition_class,
                    competition_class_verified=competition_class_verified,
                    snapshot_provenance=(
                        "offline_saved_artifact:validated_raw_schedule"
                    ),
                )
            )
        rows.sort(key=lambda item: int(item.provider_match_id))
    except (FotMobScheduleDataError, schedule.ScheduleStateError):
        failed = True
    except Exception:
        failed = True
    if failed:
        raise FotMobScheduleDataError(
            "saved schedule normalization failed"
        ) from None
    return tuple(rows)
