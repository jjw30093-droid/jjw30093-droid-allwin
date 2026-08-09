"""Offline analysis for the completed FIFA Club World Cup pilot artifacts.

The one-time live budget was fully consumed and both former live entry points
are permanently sealed.  The pure response-analysis functions and injected
``execute_pilot()`` harness remain available for offline fixtures only; this
module is not a production collector.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from analysis.competition_schedule_pilot.fotmob_competition_schedule_pilot import (  # noqa: E402
    ScheduleConflictError,
    ScheduleSchemaError,
    SeasonMismatchError,
    SeasonUnverifiableError,
    _extract_kickoff_dates,
    _fixtures_list,
    _normalize_season_label,
    _parse_strict_positive_int,
    _returned_season_value,
    extract_team_matches_from_daily_response,
    inspect_known_pagination,
    parse_competition_schedule_response,
    verify_season_parameter_effectiveness,
)
from backend import fotmob_client as fotmob_module  # noqa: E402


TARGET_TEAM_ID = 8456
DISCOVERY_DATE = "20250619"
PRIMARY_SEASON = "2025"
COMPARISON_SEASON = "2023"
REFERENCE_TOTAL_FIXTURES = 63
REFERENCE_TEAM_FIXTURES = 4
MAX_HTTP_REQUESTS = 3

GO = "GO_SINGLE_COMPETITION_DATA_VALIDATED"
NO_GO = "NO_GO"
UNVERIFIED = "UNVERIFIED"
LIVE_RUNNER_SEALED = "LIVE_RUNNER_SEALED"
RUNNER_PERMANENTLY_SEALED = "PERMANENTLY_SEALED"
SEALED_EXIT_CODE = 2


class RequestBudgetExceeded(RuntimeError):
    """A transport call was rejected before reaching the network."""


class PilotDataError(RuntimeError):
    """A saved response violates a required pilot gate."""


def _safe_exception_class_name(exc: BaseException) -> str:
    name = type(exc).__name__
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) else "Exception"


class RequestBudgetGuard:
    """Count and cap actual calls to ``cffi_requests.get``.

    The wrapper deliberately retains only counters.  It never stores, logs, or
    formats positional arguments, keyword arguments, URLs, headers, or proxy
    configuration.
    """

    def __init__(self, limit: int = MAX_HTTP_REQUESTS):
        self.limit = limit
        self.attempt_count = 0
        self.forwarded_count = 0
        self._original = None
        self._lock = threading.Lock()

    def __enter__(self) -> "RequestBudgetGuard":
        if self._original is not None:
            raise RuntimeError("request budget guard is not re-entrant")
        self._original = fotmob_module.cffi_requests.get
        original = self._original

        def guarded_get(*args, **kwargs):
            with self._lock:
                self.attempt_count += 1
                if self.attempt_count > self.limit:
                    raise RequestBudgetExceeded(
                        "FotMob request budget exceeded before transport call"
                    )
                self.forwarded_count += 1
            return original(*args, **kwargs)

        fotmob_module.cffi_requests.get = guarded_get
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._original is not None:
            fotmob_module.cffi_requests.get = self._original
            self._original = None
        return False


_USERINFO_URL_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@",
    re.IGNORECASE,
)
_PROXY_HOST_RE = re.compile(
    r"\b(?:[A-Za-z0-9-]*thordata[A-Za-z0-9-]*|proxy)"
    r"(?:\.[A-Za-z0-9-]+)+\b",
    re.IGNORECASE,
)
_AUTH_RE = re.compile(r"\b(?:Authorization\s*:\s*)?(?:Basic|Bearer)\s+\S+", re.IGNORECASE)
_PROXY_ASSIGNMENT_RE = re.compile(r"\bTHORDATA_PROXY\s*=", re.IGNORECASE)


def redaction_findings(text: str) -> list[str]:
    """Return fixed finding codes without echoing matched content."""
    findings = []
    if _USERINFO_URL_RE.search(text):
        findings.append("URL_USERINFO")
    if _PROXY_ASSIGNMENT_RE.search(text):
        findings.append("PROXY_ASSIGNMENT")
    if _AUTH_RE.search(text):
        findings.append("AUTHORIZATION")
    if _PROXY_HOST_RE.search(text):
        findings.append("PROXY_HOST")
    return sorted(set(findings))


def assert_redaction_safe(text: str) -> None:
    if redaction_findings(text):
        raise RuntimeError("REDACTION_GATE_FAILED")


def normalize_competition_name(value: Any) -> Optional[str]:
    """Conservative public-name normalization.

    It normalizes Unicode, case, punctuation, and whitespace.  FotMob daily
    competition buckets can append a terminal group-stage qualifier such as
    ``Grp. G``; only that structural two-token suffix is removed.  Semantic
    tokens such as ``FIFA`` are never removed, so ``FIFA Club World Cup`` and
    ``Club World Cup`` intentionally remain different.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    folded = unicodedata.normalize("NFKC", value).casefold()
    tokens = re.findall(r"[^\W_]+", folded, flags=re.UNICODE)
    if (
        len(tokens) >= 2
        and tokens[-2] in {"grp", "group"}
        and len(tokens[-1]) == 1
        and tokens[-1].isalnum()
    ):
        tokens = tokens[:-2]
    return " ".join(tokens) or None


def _private_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def _public_team_names(
    daily_raw: dict,
    requested_team_id: int,
) -> dict[tuple[int, Optional[int]], list[dict]]:
    names: dict[tuple[int, Optional[int]], list[dict]] = {}
    leagues = daily_raw.get("leagues") if isinstance(daily_raw, dict) else None
    if not isinstance(leagues, list):
        raise PilotDataError("daily response leagues is not a list")
    for league in leagues:
        if not isinstance(league, dict):
            raise PilotDataError("daily response league is not an object")
        competition_id = _parse_strict_positive_int(
            league.get("primaryId") or league.get("id")
        )
        matches = league.get("matches") or []
        if not isinstance(matches, list):
            raise PilotDataError("daily response matches is not a list")
        for match in matches:
            if not isinstance(match, dict):
                raise PilotDataError("daily response match is not an object")
            match_id = _parse_strict_positive_int(match.get("id"))
            home = match.get("home") or {}
            away = match.get("away") or {}
            if not isinstance(home, dict) or not isinstance(away, dict):
                raise PilotDataError("daily response team is not an object")
            home_id = _parse_strict_positive_int(home.get("id"))
            away_id = _parse_strict_positive_int(away.get("id"))
            if (
                match_id is None
                or requested_team_id not in (home_id, away_id)
            ):
                continue
            names.setdefault((match_id, competition_id), []).append({
                "home_team_name": home.get("name"),
                "away_team_name": away.get("name"),
            })
    return names


def extract_discovery_candidates(
    daily_raw: dict,
    requested_team_id: int = TARGET_TEAM_ID,
) -> list[dict]:
    candidates = extract_team_matches_from_daily_response(
        daily_raw, requested_team_id,
    )
    public_names = _public_team_names(daily_raw, requested_team_id)
    enriched = []
    consumed: dict[tuple[int, Optional[int]], int] = {}
    for candidate in candidates:
        key = (
            candidate["provider_match_id"],
            candidate["competition_id"],
        )
        index = consumed.get(key, 0)
        name_rows = public_names.get(key, [])
        names = name_rows[index] if index < len(name_rows) else {}
        consumed[key] = index + 1
        enriched.append({**candidate, **names})
    return enriched


def analyze_discovery(daily_raw: dict) -> dict:
    try:
        candidates = extract_discovery_candidates(daily_raw)
    except (AttributeError, TypeError, PilotDataError) as exc:
        return {
            "ok": False,
            "verdict": UNVERIFIED,
            "status": "DISCOVERY_RESPONSE_UNVERIFIABLE",
            "error_class": _safe_exception_class_name(exc),
        }

    if not candidates:
        return {
            "ok": False,
            "verdict": UNVERIFIED,
            "status": "NO_DISCOVERY_CANDIDATE",
            "candidate_count": 0,
        }
    if len(candidates) != 1:
        return {
            "ok": False,
            "verdict": UNVERIFIED,
            "status": "AMBIGUOUS_DISCOVERY",
            "candidate_count": len(candidates),
        }

    candidate = candidates[0]
    competition_name = candidate.get("competition_name")
    if (
        candidate.get("competition_id") is None
        or normalize_competition_name(competition_name) is None
    ):
        return {
            "ok": False,
            "verdict": UNVERIFIED,
            "status": "COMPETITION_IDENTITY_MISSING",
            "candidate_count": 1,
        }

    if candidate["home_team_id"] == TARGET_TEAM_ID:
        target_name = candidate.get("home_team_name")
        opponent_name = candidate.get("away_team_name")
    else:
        target_name = candidate.get("away_team_name")
        opponent_name = candidate.get("home_team_name")
    normalized_target = normalize_competition_name(target_name)
    normalized_opponent = normalize_competition_name(opponent_name)
    if normalized_target not in {"manchester city", "man city"} or (
        normalized_opponent is None
        or "wydad" not in normalized_opponent.split()
    ):
        return {
            "ok": False,
            "verdict": NO_GO,
            "status": "DISCOVERY_TARGET_MISMATCH",
            "candidate_count": 1,
        }
    if candidate.get("kickoff_utc") is None:
        return {
            "ok": False,
            "verdict": UNVERIFIED,
            "status": "DISCOVERY_KICKOFF_UNVERIFIABLE",
            "candidate_count": 1,
        }
    if candidate.get("cancelled") or not candidate.get("finished"):
        return {
            "ok": False,
            "verdict": NO_GO,
            "status": "DISCOVERY_STATUS_MISMATCH",
            "candidate_count": 1,
        }

    return {
        "ok": True,
        "status": "UNIQUE_DISCOVERY",
        "candidate_count": 1,
        "match": candidate,
    }


def _identity_summary(raw: dict, discovery: dict) -> dict:
    details = raw.get("details") if isinstance(raw, dict) else None
    if not isinstance(details, dict):
        details = {}
    observed_id = _parse_strict_positive_int(details.get("id"))
    observed_name = details.get("name")
    discovered_name = discovery["competition_name"]
    normalized_observed = normalize_competition_name(observed_name)
    normalized_discovered = normalize_competition_name(discovered_name)
    return {
        "observed_id": observed_id,
        "observed_name": observed_name,
        "id_match": observed_id == discovery["competition_id"],
        "name_match": (
            normalized_observed is not None
            and normalized_observed == normalized_discovered
        ),
        "normalization_rule": (
            "NFKC_CASEFOLD_ALNUM_TOKENS_DROP_TERMINAL_GROUP_STAGE"
        ),
    }


def analyze_primary_response(
    raw: dict,
    discovery: dict,
    observed_at: str,
) -> dict:
    identity = _identity_summary(raw, discovery)
    if not identity["id_match"] or not identity["name_match"]:
        return {
            "ok": False,
            "verdict": NO_GO,
            "status": "COMPETITION_IDENTITY_MISMATCH",
            "identity": identity,
        }

    returned_season = _normalize_season_label(_returned_season_value(raw))
    if returned_season is None:
        return {
            "ok": False,
            "verdict": UNVERIFIED,
            "status": "SEASON_PARAMETER_UNVERIFIED",
            "identity": identity,
            "returned_season": None,
        }
    if returned_season != PRIMARY_SEASON:
        return {
            "ok": False,
            "verdict": NO_GO,
            "status": "SEASON_MISMATCH",
            "identity": identity,
            "returned_season": returned_season,
        }

    try:
        fixtures = _fixtures_list(raw)
    except ScheduleSchemaError as exc:
        return {
            "ok": False,
            "verdict": UNVERIFIED,
            "status": "FIXTURES_SCHEMA_UNVERIFIABLE",
            "identity": identity,
            "returned_season": returned_season,
            "error_class": _safe_exception_class_name(exc),
        }
    if not fixtures:
        return {
            "ok": False,
            "verdict": UNVERIFIED,
            "status": "EMPTY_FIXTURES",
            "identity": identity,
            "returned_season": returned_season,
        }

    pagination = inspect_known_pagination(raw)
    if pagination["status"] in {"DETECTED", "UNRESOLVED"}:
        return {
            "ok": False,
            "verdict": NO_GO,
            "status": "PAGINATION_UNRESOLVED",
            "identity": identity,
            "returned_season": returned_season,
            "pagination": pagination,
        }

    fixture_ids = []
    for index, fixture in enumerate(fixtures):
        if not isinstance(fixture, dict):
            return {
                "ok": False,
                "verdict": NO_GO,
                "status": "FIXTURE_ELEMENT_INVALID",
                "fixture_index": index,
                "pagination": pagination,
            }
        match_id = _parse_strict_positive_int(fixture.get("id"))
        if match_id is None:
            return {
                "ok": False,
                "verdict": NO_GO,
                "status": "MATCH_ID_INVALID",
                "fixture_index": index,
                "pagination": pagination,
            }
        fixture_ids.append(match_id)
    if len(fixture_ids) != len(set(fixture_ids)):
        return {
            "ok": False,
            "verdict": NO_GO,
            "status": "DUPLICATE_MATCH_ID",
            "pagination": pagination,
        }

    parser_entry = {
        "requested_competition_id": discovery["competition_id"],
        "expected_name": identity["observed_name"],
        "competition_class": "international_club",
        "requested_season": PRIMARY_SEASON,
        "required_for_pilot": True,
        "verification_status": "IDENTITY_VERIFIED",
        "verification_evidence": "single-competition live discovery",
    }
    try:
        records = parse_competition_schedule_response(
            raw,
            parser_entry,
            PRIMARY_SEASON,
            observed_at,
            "fotmob:league_matches",
        )
    except (
        ScheduleConflictError,
        ScheduleSchemaError,
        SeasonMismatchError,
        SeasonUnverifiableError,
    ) as exc:
        return {
            "ok": False,
            "verdict": NO_GO,
            "status": "PARSER_GATE_FAILED",
            "error_class": _safe_exception_class_name(exc),
            "pagination": pagination,
        }
    if len(records) != len(fixtures):
        return {
            "ok": False,
            "verdict": NO_GO,
            "status": "PARSER_DROPPED_FIXTURES",
            "pagination": pagination,
        }

    discovered_id = discovery["provider_match_id"]
    linked = [record for record in records if record["provider_match_id"] == discovered_id]
    if len(linked) != 1:
        return {
            "ok": False,
            "verdict": NO_GO,
            "status": "DISCOVERED_MATCH_NOT_CROSSLINKED",
            "pagination": pagination,
        }
    linked_match = linked[0]
    if (
        linked_match["home_team_id"] != discovery["home_team_id"]
        or linked_match["away_team_id"] != discovery["away_team_id"]
    ):
        return {
            "ok": False,
            "verdict": NO_GO,
            "status": "CROSSLINK_HOME_AWAY_MISMATCH",
            "pagination": pagination,
        }

    team_count = sum(
        TARGET_TEAM_ID in (record["home_team_id"], record["away_team_id"])
        for record in records
    )
    finished_count = sum(record["finished"] for record in records)
    cancelled_count = sum(record["cancelled"] for record in records)
    non_cancelled_count = sum(not record["cancelled"] for record in records)
    dates = _extract_kickoff_dates(raw)
    total_count = len(records)
    count_alignment = (
        "COUNT_ALIGNED_WITH_OFFICIAL_FORMAT"
        if (
            total_count == REFERENCE_TOTAL_FIXTURES
            and team_count == REFERENCE_TEAM_FIXTURES
        )
        else "COUNT_DIFFERS_FROM_OFFICIAL_FORMAT_REFERENCE"
    )
    return {
        "ok": True,
        "status": "PRIMARY_RESPONSE_VALIDATED",
        "identity": identity,
        "returned_season": returned_season,
        "fixture_count": total_count,
        "target_team_fixture_count": team_count,
        "finished_fixture_count": finished_count,
        "cancelled_fixture_count": cancelled_count,
        "non_cancelled_fixture_count": non_cancelled_count,
        "date_range": [min(dates), max(dates)] if dates else [None, None],
        "pagination": pagination,
        "crosslink": {
            "match_id": discovered_id,
            "present": True,
            "home_away_match": True,
        },
        "count_alignment": count_alignment,
        "reference": {
            "fixture_count": REFERENCE_TOTAL_FIXTURES,
            "target_team_fixture_count": REFERENCE_TEAM_FIXTURES,
            "fixture_count_delta": (
                total_count - REFERENCE_TOTAL_FIXTURES
            ),
            "target_team_fixture_count_delta": (
                team_count - REFERENCE_TEAM_FIXTURES
            ),
        },
    }


def analyze_season_comparison(
    raw_primary: dict,
    raw_comparison: dict,
    discovery: dict,
) -> dict:
    identity = _identity_summary(raw_comparison, discovery)
    returned_season = _normalize_season_label(
        _returned_season_value(raw_comparison)
    )
    pagination = inspect_known_pagination(raw_comparison)
    dates = _extract_kickoff_dates(raw_comparison)
    try:
        fixtures = _fixtures_list(raw_comparison)
    except ScheduleSchemaError:
        fixtures = []
    ids = []
    invalid_ids = False
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            invalid_ids = True
            continue
        match_id = _parse_strict_positive_int(fixture.get("id"))
        if match_id is None:
            invalid_ids = True
        else:
            ids.append(match_id)

    primary_ids = {
        _parse_strict_positive_int(fixture.get("id"))
        for fixture in _fixtures_list(raw_primary)
        if isinstance(fixture, dict)
    }
    primary_ids.discard(None)
    base = {
        "identity": identity,
        "returned_season": returned_season,
        "fixture_count": len(fixtures),
        "match_id_count": len(set(ids)),
        "match_id_overlap_count": len(primary_ids & set(ids)),
        "date_range": [min(dates), max(dates)] if dates else [None, None],
        "pagination": pagination,
    }

    if pagination["status"] in {"DETECTED", "UNRESOLVED"}:
        return {**base, "status": "PAGINATION_UNRESOLVED"}
    if not identity["id_match"] or not identity["name_match"]:
        return {**base, "status": "SEASON_PARAMETER_UNVERIFIED"}
    if not fixtures or invalid_ids:
        return {**base, "status": "SEASON_PARAMETER_UNVERIFIED"}
    if returned_season == PRIMARY_SEASON:
        return {**base, "status": "SEASON_PARAMETER_INEFFECTIVE"}
    if returned_season != COMPARISON_SEASON:
        return {**base, "status": "SEASON_PARAMETER_UNVERIFIED"}

    effectiveness = verify_season_parameter_effectiveness(
        raw_primary, raw_comparison,
    )
    return {
        **base,
        "status": effectiveness["verdict"],
        "effectiveness": effectiveness,
    }


def _request_record(number: int, operation: str, result: str) -> dict:
    return {
        "request_number": number,
        "request_budget": MAX_HTTP_REQUESTS,
        "operation": operation,
        "result": result,
    }


def _execute_from_daily(
    client: Any,
    output_dir: Path,
    daily_raw: dict,
    initial_requests: list[dict],
) -> dict:
    """Continue from one in-memory or previously saved daily response."""
    raw_dir = output_dir / "raw"
    observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    summary: dict[str, Any] = {
        "verdict": UNVERIFIED,
        "requests": list(initial_requests),
        "output_dir": str(output_dir),
        "pagination_followed": False,
        "production_integration": False,
    }
    discovery = analyze_discovery(daily_raw)
    summary["discovery"] = discovery
    if not discovery["ok"]:
        summary["verdict"] = discovery["verdict"]
        summary["status"] = discovery["status"]
        return summary

    match = discovery["match"]
    competition_id = match["competition_id"]
    try:
        primary_raw = client.league_matches(competition_id, PRIMARY_SEASON)
        summary["requests"].append(
            _request_record(2, "league_matches_2025", "RESPONSE_RECEIVED")
        )
    except Exception as exc:
        summary["requests"].append(
            _request_record(
                2,
                "league_matches_2025",
                f"SAFE_ERROR_{_safe_exception_class_name(exc)}",
            )
        )
        summary["status"] = "PRIMARY_REQUEST_FAILED"
        return summary
    _private_json_write(
        raw_dir / f"competition_{competition_id}_2025.json",
        primary_raw,
    )
    primary = analyze_primary_response(primary_raw, match, observed_at)
    summary["primary"] = primary
    if not primary["ok"]:
        summary["verdict"] = primary["verdict"]
        summary["status"] = primary["status"]
        return summary

    summary["verdict"] = GO
    summary["status"] = "PRIMARY_RESPONSE_VALIDATED"
    try:
        comparison_raw = client.league_matches(
            competition_id, COMPARISON_SEASON,
        )
        summary["requests"].append(
            _request_record(3, "league_matches_2023", "RESPONSE_RECEIVED")
        )
    except Exception as exc:
        summary["requests"].append(
            _request_record(
                3,
                "league_matches_2023",
                f"SAFE_ERROR_{_safe_exception_class_name(exc)}",
            )
        )
        summary["comparison"] = {
            "status": "SEASON_PARAMETER_UNVERIFIED",
            "error_class": _safe_exception_class_name(exc),
        }
        return summary
    _private_json_write(
        raw_dir / f"competition_{competition_id}_2023.json",
        comparison_raw,
    )
    comparison = analyze_season_comparison(
        primary_raw, comparison_raw, match,
    )
    summary["comparison"] = comparison
    if comparison["status"] == "PAGINATION_UNRESOLVED":
        summary["verdict"] = NO_GO
        summary["status"] = "COMPARISON_PAGINATION_UNRESOLVED"
    return summary


def execute_pilot(client: Any, output_dir: Path) -> dict:
    """Run the fixed sequence with an injected offline fake client.

    This is a fixture harness, not an authorized live entry point.  Callers
    must supply a fake client and an isolated temporary output directory.
    """
    output_dir.mkdir(parents=True, exist_ok=False)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(mode=0o700)
    try:
        daily_raw = client.daily_matches(DISCOVERY_DATE)
        requests = [
            _request_record(1, "daily_matches", "RESPONSE_RECEIVED"),
        ]
    except Exception as exc:
        return {
            "verdict": UNVERIFIED,
            "requests": [
                _request_record(
                    1,
                    "daily_matches",
                    f"SAFE_ERROR_{_safe_exception_class_name(exc)}",
                ),
            ],
            "output_dir": str(output_dir),
            "pagination_followed": False,
            "production_integration": False,
            "status": "DISCOVERY_REQUEST_FAILED",
        }
    _private_json_write(raw_dir / "daily_20250619.json", daily_raw)
    return _execute_from_daily(
        client,
        output_dir,
        daily_raw,
        requests,
    )


def _new_live_output_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = Path(f"/tmp/allwin-cwc-single-pilot-{stamp}")
    if not base.exists():
        return base
    for suffix in range(1, 100):
        candidate = Path(f"{base}-{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("unable to allocate pilot output directory")


def _artifact_redaction_findings(output_dir: Path, summary: dict) -> list[str]:
    candidate_text = json.dumps(
        summary, ensure_ascii=False, indent=2, sort_keys=True,
    )
    findings = redaction_findings(candidate_text)
    raw_dir = output_dir / "raw"
    artifacts = sorted(raw_dir.glob("*.json")) if raw_dir.exists() else []
    for artifact in artifacts:
        findings.extend(redaction_findings(artifact.read_text("utf-8")))
    return sorted(set(findings))


def _sealed_live_summary() -> dict:
    """Return the fixed public state without inspecting environment or files."""
    return {
        "actual_http_request_count": 0,
        "data_verdict": GO,
        "production_integration": False,
        "runner_verdict": RUNNER_PERMANENTLY_SEALED,
        "status": LIVE_RUNNER_SEALED,
        "transport_attempt_count": 0,
    }


def run_live() -> tuple[int, dict]:
    """Fail closed before client construction, environment reads, or I/O."""
    return SEALED_EXIT_CODE, _sealed_live_summary()


def resume_live_from_saved_daily(
    output_dir: Path,
    prior_http_request_count: int = 1,
) -> tuple[int, dict]:
    """Fail closed before reading the saved artifact or touching transport."""
    return SEALED_EXIT_CODE, _sealed_live_summary()


def run_cli(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Club World Cup single-competition pilot",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="report the permanently sealed state; never performs live I/O",
    )
    args = parser.parse_args(argv)
    if not args.live:
        print(json.dumps({
            "verdict": UNVERIFIED,
            "status": "LIVE_FLAG_REQUIRED",
        }, sort_keys=True))
        return 2
    code, summary = run_live()
    text = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
    assert_redaction_safe(text)
    print(text)
    return code


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
