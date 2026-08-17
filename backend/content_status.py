"""Read the small public projection of the durable daily-content run status."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backend.db.paths import data_dir


FRESHNESS_GRACE = {
    "LOW_FREQUENCY": timedelta(minutes=30),
    "T_MINUS_72H_TO_2H": timedelta(minutes=5),
    "T_MINUS_2H_TO_KICKOFF": timedelta(minutes=2),
}
FRESHNESS_CADENCE = {
    "LOW_FREQUENCY": timedelta(hours=24),
    "T_MINUS_72H_TO_2H": timedelta(minutes=15),
    "T_MINUS_2H_TO_KICKOFF": timedelta(minutes=5),
}

PUBLIC_STATUS_FIELDS = (
    "state",
    "data_updated_at",
    "last_success_sync_at",
    "next_planned_sync_at",
    "probability_source",
    "odds_observation_count",
)


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _freshness_phase(kickoff: datetime, now: datetime) -> str:
    seconds = (kickoff - now).total_seconds()
    if seconds <= 2 * 3600:
        return "T_MINUS_2H_TO_KICKOFF"
    if seconds <= 72 * 3600:
        return "T_MINUS_72H_TO_2H"
    return "LOW_FREQUENCY"


def project_freshness(
    status: dict[str, Any],
    *,
    kickoff_at_utc: str | None,
    now: datetime | None = None,
) -> str:
    """Project durable acquisition facts to one truthful public state.

    The persisted ``state`` remains a run fact. Public freshness is recomputed
    on every request so a once-successful run cannot remain FRESH forever.
    """

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    last_success = _parse_utc(status.get("last_success_sync_at"))
    kickoff = _parse_utc(kickoff_at_utc)
    if last_success is None or kickoff is None:
        return "UNAVAILABLE"
    if status.get("state") in {"STALE", "UNAVAILABLE"}:
        return str(status["state"])
    if last_success > current or kickoff <= current:
        return "STALE"

    phase = _freshness_phase(kickoff, current)
    grace = FRESHNESS_GRACE[phase]
    planned = _parse_utc(status.get("next_planned_sync_at"))
    deadlines: list[datetime] = []
    if phase == "LOW_FREQUENCY" and planned is not None:
        deadlines.append(planned + grace)
    else:
        deadlines.append(last_success + FRESHNESS_CADENCE[phase] + grace)
        if planned is not None:
            deadlines.append(planned + grace)
    if not deadlines:
        deadlines.append(last_success + FRESHNESS_CADENCE[phase] + grace)
    return "FRESH" if current <= min(deadlines) else "STALE"


def load_content_status(path: Path | None = None) -> dict[str, Any]:
    target = path or (data_dir() / "content_status.json")
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def public_status_for_match(
    match_id: int,
    *,
    kickoff_at_utc: str | None = None,
    now: datetime | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    status = load_content_status(path)
    if status.get("match_id") != match_id:
        return {}
    projected = {
        field: status[field]
        for field in PUBLIC_STATUS_FIELDS
        if status.get(field) is not None
    }
    projected["state"] = project_freshness(
        status,
        kickoff_at_utc=kickoff_at_utc,
        now=now,
    )
    return projected
