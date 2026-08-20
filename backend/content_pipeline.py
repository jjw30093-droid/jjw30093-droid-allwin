"""Durable one-command acquisition for the active-league content MVP.

The pipeline deliberately composes the existing FotMob client, NowGoal parser,
poll-state tables, active-league assembler, and Studio bundle.  It does not
introduce a second provider or schedule-state implementation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from backend.active_league_mvp import (
    DEFAULT_CONTEXT,
    ActiveLeagueContext,
    apply_saved_artifacts,
)
from backend.db import migrate
from backend.db.connections import connect_rw
from backend.db.util import utc_now_iso
from backend.ingest.poll_windows import (
    CADENCE_NOWGOAL_ODDS,
    SOURCE_NOWGOAL_ODDS,
    SOURCE_NOWGOAL_SCHEDULE,
    mark_polled,
    poll_decision,
)
from backend.providers import nowgoal
from backend.schedules.pagination import inspect_known_pagination
from backend.season_resolver import (
    SeasonKind,
    SeasonVerification,
    resolve_current_season,
)

if TYPE_CHECKING:
    from backend.fotmob_client import FotMobClient


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNTIME_DIR = PROJECT_ROOT / "runtime"
MAX_TRANSPORT_ATTEMPTS = 50
DEFAULT_CONTENT_HORIZON_DAYS = 7
DEFAULT_MAX_REQUEST_ATTEMPTS = 20


class DailyContentError(RuntimeError):
    """Fixed, credential-safe product-pipeline failure."""


@dataclass(frozen=True)
class LeagueConfig:
    key: str
    fotmob_competition_id: int
    expected_competition_name: str
    name_zh: str
    timezone: str
    season_rule: str
    nowgoal_discovery: str
    nowgoal_league_name: str
    identity_verified: bool
    identity_evidence: str
    content_horizon_days: int
    capabilities: tuple[str, ...]
    fresh_enabled: bool
    verified_match_xrefs: dict[int, str]
    verified_team_aliases: dict[int, tuple[str, ...]]
    verified_team_names_zh: dict[int, str]


LEAGUES: dict[str, LeagueConfig] = {
    "eliteserien": LeagueConfig(
        key="eliteserien",
        fotmob_competition_id=59,
        expected_competition_name="Eliteserien",
        name_zh="挪威超",
        timezone="Europe/Oslo",
        season_rule="calendar_year",
        nowgoal_discovery="beijing_date_then_exact_team_and_kickoff",
        nowgoal_league_name="Norway Eliteserien",
        identity_verified=True,
        identity_evidence="live FotMob 2026 response and exact NowGoal match mapping",
        content_horizon_days=7,
        capabilities=("fixtures", "standings", "team_form", "xg", "1x2", "ah", "ou"),
        fresh_enabled=True,
        verified_match_xrefs={5104968: "2912857"},
        verified_team_aliases={
            8007: ("Vålerenga", "Valerenga"),
            8448: ("Hamarkameratene", "Ham-Kam", "HamKam"),
        },
        verified_team_names_zh={8007: "瓦勒伦加", 8448: "汉坎"},
    ),
    "allsvenskan": LeagueConfig(
        key="allsvenskan",
        fotmob_competition_id=67,
        expected_competition_name="Allsvenskan",
        name_zh="瑞典超",
        timezone="Europe/Stockholm",
        season_rule="calendar_year",
        nowgoal_discovery="beijing_date_then_exact_team_and_kickoff",
        nowgoal_league_name="Swedish Allsvenskan",
        identity_verified=True,
        identity_evidence="live FotMob 2026 response previously retained in the MVP audit",
        content_horizon_days=7,
        capabilities=("fixtures", "standings", "team_form", "xg", "1x2", "ah", "ou"),
        fresh_enabled=False,
        verified_match_xrefs={},
        verified_team_aliases={},
        verified_team_names_zh={},
    ),
    "mls": LeagueConfig(
        key="mls",
        fotmob_competition_id=130,
        expected_competition_name="MLS",
        name_zh="美职联",
        timezone="America/New_York",
        season_rule="calendar_year",
        nowgoal_discovery="beijing_date_then_exact_team_and_kickoff",
        nowgoal_league_name="USA Major League Soccer",
        identity_verified=True,
        identity_evidence="live FotMob 2026 response previously retained in the MVP audit",
        content_horizon_days=7,
        capabilities=("fixtures", "standings", "team_form", "xg", "1x2", "ah", "ou"),
        fresh_enabled=False,
        verified_match_xrefs={},
        verified_team_aliases={},
        verified_team_names_zh={},
    ),
}


@dataclass(frozen=True)
class RuntimeConfig:
    data_dir: Path
    artifact_dir: Path
    studio_output_dir: Path
    content_horizon_days: int
    max_request_attempts: int
    api_base: str


def _positive_int(name: str, value: str, *, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise DailyContentError(f"{name} must be a positive integer") from None
    if parsed <= 0 or (maximum is not None and parsed > maximum):
        limit = f" no greater than {maximum}" if maximum is not None else ""
        raise DailyContentError(f"{name} must be a positive integer{limit}")
    return parsed


def load_runtime_config() -> RuntimeConfig:
    runtime = DEFAULT_RUNTIME_DIR
    data_dir = Path(os.environ.get("ALLWIN_DATA_DIR", runtime / "data")).expanduser()
    artifact_dir = Path(
        os.environ.get("ALLWIN_ARTIFACT_DIR", runtime / "artifacts")
    ).expanduser()
    studio_output_dir = Path(
        os.environ.get("ALLWIN_STUDIO_OUTPUT_DIR", runtime / "studio")
    ).expanduser()
    horizon = _positive_int(
        "ALLWIN_CONTENT_HORIZON_DAYS",
        os.environ.get("ALLWIN_CONTENT_HORIZON_DAYS", str(DEFAULT_CONTENT_HORIZON_DAYS)),
        maximum=31,
    )
    attempts = _positive_int(
        "ALLWIN_MAX_REQUEST_ATTEMPTS",
        os.environ.get("ALLWIN_MAX_REQUEST_ATTEMPTS", str(DEFAULT_MAX_REQUEST_ATTEMPTS)),
        maximum=MAX_TRANSPORT_ATTEMPTS,
    )
    return RuntimeConfig(
        data_dir=data_dir.resolve(),
        artifact_dir=artifact_dir.resolve(),
        studio_output_dir=studio_output_dir.resolve(),
        content_horizon_days=horizon,
        max_request_attempts=attempts,
        api_base=os.environ.get("ALLWIN_API_BASE", "http://127.0.0.1:8000").rstrip("/"),
    )


def ensure_runtime_databases(config: RuntimeConfig) -> dict[str, int]:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["ALLWIN_DATA_DIR"] = str(config.data_dir)
    from backend.init_db import init_db

    init_db(config.data_dir / "allwin.db", quiet=True)
    return {
        name: migrate.apply_all(
            name,
            db_file=config.data_dir / migrate.db_path(name).name,
            quiet=True,
        )
        for name in migrate.DATABASES
    }


class RequestBudget:
    def __init__(self, maximum: int):
        self.maximum = maximum
        self.attempts = 0
        self.operations: list[str] = []

    def consume(self, operation: str) -> None:
        if self.attempts >= self.maximum:
            raise DailyContentError("external request attempt budget exhausted")
        self.attempts += 1
        self.operations.append(operation)


class ImmutableArtifactRun:
    def __init__(self, root: Path, league_key: str, observed_at: str):
        compact = observed_at.replace("-", "").replace(":", "").replace("T", "-").replace("Z", "")
        self.run_id = f"{compact}-{uuid.uuid4().hex[:12]}"
        self.root = root
        self.run_dir = root / "runs" / league_key / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.files: dict[str, dict[str, Any]] = {}

    def write_bytes(self, name: str, payload: bytes, *, role: str) -> Path:
        if not name or Path(name).name != name:
            raise DailyContentError("invalid artifact filename")
        path = self.run_dir / name
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        path.chmod(0o440)
        self.files[name] = {
            "path": str(path.relative_to(self.root)),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
            "role": role,
        }
        return path

    def write_json(self, name: str, value: dict, *, role: str) -> Path:
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return self.write_bytes(name, payload, role=role)


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    with tmp.open("x", encoding="utf-8") as handle:
        handle.write(payload)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise DailyContentError("provider kickoff is not timezone aware")
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _fixture_candidates(
    payload: dict,
    *,
    config: LeagueConfig,
    now: datetime,
    horizon_days: int,
) -> list[dict]:
    fixtures = payload.get("fixtures")
    rows = fixtures.get("allMatches") if isinstance(fixtures, dict) else None
    if not isinstance(rows, list):
        raise DailyContentError("FotMob fixtures are missing")
    horizon = now + timedelta(days=horizon_days)
    candidates: list[dict] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        status = item.get("status") if isinstance(item.get("status"), dict) else {}
        if status.get("started") or status.get("finished") or status.get("cancelled"):
            continue
        raw_kickoff = status.get("utcTime")
        if not isinstance(raw_kickoff, str):
            continue
        try:
            kickoff = _parse_utc(raw_kickoff)
        except (ValueError, DailyContentError):
            continue
        if not now < kickoff <= horizon:
            continue
        home = item.get("home") if isinstance(item.get("home"), dict) else {}
        away = item.get("away") if isinstance(item.get("away"), dict) else {}
        if not item.get("id") or not home.get("id") or not away.get("id"):
            continue
        candidates.append(
            {
                "match_id": int(item["id"]),
                "kickoff_at_utc": _utc_iso(kickoff),
                "home_id": int(home["id"]),
                "away_id": int(away["id"]),
                "home_name": str(home.get("name") or ""),
                "away_name": str(away.get("name") or ""),
                "round": item.get("round"),
                "within_72h": (kickoff - now).total_seconds() <= 72 * 3600,
                "completeness": sum(
                    bool(value)
                    for value in (
                        home.get("id"),
                        away.get("id"),
                        home.get("name"),
                        away.get("name"),
                        item.get("round"),
                    )
                ),
            }
        )
    candidates.sort(
        key=lambda row: (
            0 if row["within_72h"] else 1,
            -row["completeness"],
            row["kickoff_at_utc"],
            row["match_id"],
        )
    )
    return candidates


def _normalize_name(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value)
    ascii_text = "".join(char for char in folded if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]", "", ascii_text.casefold())


def _alias_score(
    name: str,
    canonical_id: int,
    config: LeagueConfig,
    *,
    fallback_name: str,
) -> float:
    normalized = _normalize_name(name)
    aliases = config.verified_team_aliases.get(canonical_id, ())
    if not aliases:
        aliases = (fallback_name,)
    return max(
        SequenceMatcher(None, normalized, _normalize_name(alias)).ratio()
        for alias in aliases
    )


def _row_kickoff(row: dict) -> datetime | None:
    value = row.get("kickoff")
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace(" ", "T")).replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _discover_nowgoal_match(
    candidate: dict,
    rows: list[dict],
    config: LeagueConfig,
) -> dict | None:
    verified_id = config.verified_match_xrefs.get(candidate["match_id"])
    considered = (
        [row for row in rows if str(row.get("titan_id")) == verified_id]
        if verified_id
        else rows
    )
    kickoff = _parse_utc(candidate["kickoff_at_utc"])
    scored: list[tuple[float, dict]] = []
    for row in considered:
        provider_kickoff = _row_kickoff(row)
        if provider_kickoff is None:
            continue
        diff = abs((provider_kickoff - kickoff).total_seconds())
        if diff > 1800:
            continue
        home_score = _alias_score(
            row.get("home_name", ""),
            candidate["home_id"],
            config,
            fallback_name=candidate["home_name"],
        )
        away_score = _alias_score(
            row.get("away_name", ""),
            candidate["away_id"],
            config,
            fallback_name=candidate["away_name"],
        )
        confidence = ((home_score + away_score) / 2) * max(0.0, 1.0 - diff / 1800)
        if verified_id and str(row.get("titan_id")) == verified_id and diff == 0:
            confidence = 1.0 if min(home_score, away_score) >= 0.72 else confidence
        if min(home_score, away_score) >= 0.72 and confidence >= 0.72:
            scored.append((confidence, row))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("titan_id"))))
    if not scored:
        return None
    if len(scored) > 1 and abs(scored[0][0] - scored[1][0]) < 0.01:
        return None
    return dict(scored[0][1]) | {
        "confidence": round(scored[0][0], 6),
        "kickoff_diff_seconds": int(
            abs((_row_kickoff(scored[0][1]) - kickoff).total_seconds())
        ),
    }


def _build_assembly_context(
    league: LeagueConfig,
    *,
    season: str,
    selected: dict,
    mapping: dict,
) -> ActiveLeagueContext:
    return ActiveLeagueContext(
        league_id=league.fotmob_competition_id,
        league_code=league.key,
        league_name=league.expected_competition_name,
        league_name_zh=league.name_zh,
        season=season,
        match_id=selected["match_id"],
        nowgoal_match_id=str(mapping["titan_id"]),
        home_id=selected["home_id"],
        away_id=selected["away_id"],
        home_name_en=selected["home_name"],
        away_name_en=selected["away_name"],
        home_name_zh=league.verified_team_names_zh.get(
            selected["home_id"], selected["home_name"]
        ),
        away_name_zh=league.verified_team_names_zh.get(
            selected["away_id"], selected["away_name"]
        ),
        nowgoal_home_id=(
            str(mapping["provider_home_id"])
            if mapping.get("provider_home_id")
            else None
        ),
        nowgoal_away_id=(
            str(mapping["provider_away_id"])
            if mapping.get("provider_away_id")
            else None
        ),
    )


def _artifact_path(root: Path, entry: dict) -> Path:
    path = (root / entry["path"]).resolve()
    if root.resolve() not in path.parents:
        raise DailyContentError("artifact manifest path escapes configured root")
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
        raise DailyContentError("artifact checksum mismatch")
    return path


def _read_latest_manifest(config: RuntimeConfig, league_key: str) -> dict:
    path = config.artifact_dir / f"latest-{league_key}.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise DailyContentError("no successful artifact set is available for replay") from None
    if not isinstance(value, dict) or value.get("status") != "SUCCEEDED":
        raise DailyContentError("latest artifact manifest is invalid")
    return value


def _paths_from_manifest(config: RuntimeConfig, manifest: dict) -> dict[str, Path]:
    required = ("league", "match", "home", "away", "odds")
    files = manifest.get("content_files")
    if not isinstance(files, dict) or any(key not in files for key in required):
        raise DailyContentError("latest artifact manifest is incomplete")
    return {
        key: _artifact_path(config.artifact_dir, files[key])
        for key in required
    }


def _fotmob_json(
    client: FotMobClient,
    budget: RequestBudget,
    artifacts: ImmutableArtifactRun,
    *,
    url: str,
    operation: str,
    raw_name: str,
    content_name: str,
) -> tuple[dict, Path]:
    budget.consume(operation)
    response = client._get(url, headers={"Accept": "application/json"})
    try:
        raw = bytes(response.content)
    except Exception:
        raise DailyContentError(f"{operation} response bytes are unavailable") from None
    artifacts.write_bytes(raw_name, raw, role="immutable_provider_response")
    value = client._decode_json_response(response, operation)
    if not isinstance(value, dict):
        raise DailyContentError(f"{operation} response is not an object")
    path = artifacts.write_json(content_name, value, role="validated_content_projection")
    return value, path


def _fotmob_match(
    client: FotMobClient,
    budget: RequestBudget,
    artifacts: ImmutableArtifactRun,
    match_id: int,
) -> tuple[dict, Path]:
    budget.consume("fotmob_match")
    response = client._get(
        f"https://www.fotmob.com/match/{match_id}",
        headers={"Accept": "text/html"},
    )
    html = client._read_response_text(response, "match_details")
    artifacts.write_bytes(
        f"fotmob-match-{match_id}.raw.html",
        html.encode("utf-8"),
        role="immutable_provider_response",
    )
    next_data = client._extract_next_data(html, "match_details")
    props = next_data.get("props") if isinstance(next_data, dict) else None
    page_props = props.get("pageProps") if isinstance(props, dict) else None
    if not isinstance(page_props, dict) or not isinstance(page_props.get("general"), dict):
        raise DailyContentError("FotMob match payload is incomplete")
    path = artifacts.write_json(
        f"fotmob-match-{match_id}.json",
        page_props,
        role="validated_content_projection",
    )
    return page_props, path


def _nowgoal_schedule(
    budget: RequestBudget,
    artifacts: ImmutableArtifactRun,
    date: str,
) -> list[dict]:
    budget.consume("nowgoal_schedule")
    text = nowgoal._http_get(
        nowgoal.SCHEDULE_URL,
        {
            "type": "6",
            "date": date,
            "order": "time",
            "timezone": "8",
            "flesh": nowgoal._flesh(),
        },
    )
    artifacts.write_bytes(
        f"nowgoal-schedule-{date}.raw.txt",
        text.encode("utf-8"),
        role="immutable_provider_response",
    )
    rows = nowgoal.parse_schedule(nowgoal._extract_data_field(text))
    if not rows:
        raise DailyContentError("NowGoal schedule contains no usable matches")
    return rows


def _nowgoal_odds(
    budget: RequestBudget,
    artifacts: ImmutableArtifactRun,
    titan_id: str,
) -> tuple[list[dict], Path]:
    budget.consume("nowgoal_odds")
    text = nowgoal._http_get(
        nowgoal.ODDS_URL,
        {
            "type": "14",
            "t": "1",
            "id": titan_id,
            "h": "0",
            "s": "0",
            "flesh": nowgoal._flesh(),
        },
    )
    raw = text.encode("utf-8")
    artifacts.write_bytes(
        f"nowgoal-odds-{titan_id}.raw.json",
        raw,
        role="immutable_provider_response",
    )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raise DailyContentError("NowGoal odds response is not strict JSON") from None
    if not isinstance(payload, dict):
        raise DailyContentError("NowGoal odds response is not an object")
    records = nowgoal.parse_odds(payload)
    if not any(
        row.get("market") == "1x2" and isinstance(row.get("latest"), dict)
        for row in records
    ):
        raise DailyContentError("NowGoal real 1x2 odds are unavailable")
    path = artifacts.write_json(
        f"nowgoal-odds-{titan_id}.json",
        payload,
        role="validated_content_projection",
    )
    return records, path


_ODDS_TIER_PHASE = {
    "checkpoint_72h": "T_MINUS_72H_TO_24H",
    "checkpoint_24h": "T_MINUS_24H_TO_6H",
    "checkpoint_6h": "T_MINUS_6H_TO_KICKOFF",
    "last_call": "LAST_CALL",
}


def _poll_decision(
    kickoff_at_utc: str,
    *,
    now_iso: str,
    conn: sqlite3.Connection,
    match_id: int,
) -> dict:
    """薄封装:唯一决策权威是 poll_windows.poll_decision(),不再自行重算窗口/
    间隔(PIPELINE_REDESIGN_V2 P1,收敛掉本文件曾经独立的两档到期逻辑)。"""
    row = conn.execute(
        "SELECT last_polled_at FROM poll_state WHERE source=? AND subject=?",
        (SOURCE_NOWGOAL_ODDS, str(match_id)),
    ).fetchone()
    last_polled_at = row["last_polled_at"] if row is not None else None
    decision = poll_decision(
        SOURCE_NOWGOAL_ODDS, kickoff_at_utc, "exact", "fotmob:league_fixture",
        last_polled_at, now_iso,
    )
    if decision.reason in ("kicked_off", "no_exact_kickoff"):
        return {"due": False, "phase": "CLOSED", "next_due_at": None}
    if decision.reason == "first_discovery_out_of_window":
        return {"due": True, "phase": "INITIAL_LOW_FREQUENCY", "next_due_at": now_iso}
    if decision.reason == "out_of_window_or_kicked_off":
        kickoff = _parse_utc(kickoff_at_utc)
        return {
            "due": False,
            "phase": "WAITING_FOR_72H_WINDOW",
            "next_due_at": _utc_iso(kickoff - timedelta(hours=72)),
        }
    next_due_at = now_iso
    if not decision.due:
        next_due = _parse_utc(last_polled_at) + timedelta(seconds=decision.interval_seconds)
        next_due_at = _utc_iso(next_due)
    return {
        "due": decision.due,
        "phase": _ODDS_TIER_PHASE[decision.tier],
        "interval_seconds": decision.interval_seconds,
        "next_due_at": next_due_at,
    }


def due_status(
    league_key: str,
    *,
    match_id: int | None = None,
    runtime: RuntimeConfig | None = None,
    now_iso: str | None = None,
) -> dict:
    config = runtime or load_runtime_config()
    league = LEAGUES[league_key]
    now = now_iso or utc_now_iso()
    db_path = config.data_dir / "allwin.db"
    odds_path = config.data_dir / "odds.db"
    if not db_path.exists() or not odds_path.exists():
        return {
            "league": league_key,
            "network_requests": 0,
            "due": [],
            "next_due_at": None,
            "state": "UNAVAILABLE",
        }
    core = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    core.row_factory = sqlite3.Row
    odds = sqlite3.connect(f"file:{odds_path}?mode=ro", uri=True)
    odds.row_factory = sqlite3.Row
    try:
        params: list[Any] = [league.fotmob_competition_id, now]
        match_clause = ""
        if match_id is not None:
            match_clause = " AND Match_ID=?"
            params.append(match_id)
        horizon = _utc_iso(_parse_utc(now) + timedelta(days=config.content_horizon_days))
        params.append(horizon)
        rows = core.execute(
            """SELECT Match_ID,kickoff_at_utc FROM dim_match
               WHERE League_ID=? AND status='NotStarted'
                 AND julianday(kickoff_at_utc)>julianday(?)
                 AND julianday(kickoff_at_utc)<=julianday(?)"""
            + match_clause
            + " ORDER BY julianday(kickoff_at_utc),Match_ID",
            tuple(params if not match_clause else params[:2] + [horizon, params[2]]),
        ).fetchall()
        decisions = []
        for row in rows:
            # provider='nowgoal':下游查 bronze_ng_odds_snap(nowgoal 形状的表),
            # 不过滤会让轮询决策可能读到 kbisai xref 的 provider_match_id,
            # 查不到 nowgoal 快照就误判成"从未观测到"、造成无界重复轮询
            # (CLAUDE.md §6.3)。
            xref = odds.execute(
                "SELECT provider_match_id FROM dim_match_xref WHERE fotmob_match_id=? AND provider='nowgoal'",
                (row["Match_ID"],),
            ).fetchone()
            count = 0
            if xref is not None:
                count = odds.execute(
                    """SELECT COUNT(DISTINCT observed_at) FROM bronze_ng_odds_snap
                       WHERE provider_match_id=? AND market='1x2'""",
                    (xref["provider_match_id"],),
                ).fetchone()[0]
            decision = _poll_decision(
                row["kickoff_at_utc"],
                now_iso=now,
                conn=odds,
                match_id=row["Match_ID"],
            )
            decisions.append(
                {
                    "match_id": row["Match_ID"],
                    "kickoff_at_utc": row["kickoff_at_utc"],
                    "observation_points": count,
                    **decision,
                }
            )
        next_values = [row["next_due_at"] for row in decisions if row.get("next_due_at")]
        return {
            "league": league_key,
            "network_requests": 0,
            "due": [row for row in decisions if row["due"]],
            "matches": decisions,
            "next_due_at": min(next_values) if next_values else None,
            "state": "FRESH" if decisions else "UNAVAILABLE",
        }
    finally:
        core.close()
        odds.close()


def _write_content_status(config: RuntimeConfig, value: dict) -> None:
    _atomic_json(config.data_dir / "content_status.json", value)


def _status_after_success(
    config: RuntimeConfig,
    summary: dict,
    *,
    observed_at: str,
    next_due_at: str | None,
    free_outcome: str,
    request_attempts: int,
    run_id: str,
    mode: str,
) -> dict:
    return {
        "state": "FRESH",
        "league": summary["league"]["name"],
        "league_id": summary["league"]["id"],
        "match_id": summary["match"]["fotmob_id"],
        "last_attempt_at": observed_at,
        "last_success_sync_at": observed_at,
        "data_updated_at": observed_at,
        "next_planned_sync_at": next_due_at,
        "probability_source": "MARKET_BASELINE",
        "odds_observation_count": summary["odds_observation_count"],
        "free_outcome": free_outcome,
        "request_attempts": request_attempts,
        "run_id": run_id,
        "mode": mode,
    }


def mark_fresh_failure(
    config: RuntimeConfig,
    *,
    league_key: str,
    failure_type: str,
) -> dict:
    path = config.data_dir / "content_status.json"
    current: dict[str, Any] = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            current = loaded
    except (OSError, json.JSONDecodeError):
        pass
    now = utc_now_iso()
    current.update(
        {
            "state": "STALE" if current.get("last_success_sync_at") else "UNAVAILABLE",
            "league_key": league_key,
            "last_attempt_at": now,
            "last_failure_type": failure_type,
        }
    )
    _write_content_status(config, current)
    return current


def _publish_manifest(config: RuntimeConfig, manifest: dict) -> None:
    _atomic_json(
        config.artifact_dir / f"latest-{manifest['league_key']}.json",
        manifest,
    )


def run_fresh(
    league_key: str,
    *,
    match_id: int | None = None,
    free_outcome: str = "home",
    runtime: RuntimeConfig | None = None,
    now_iso: str | None = None,
) -> dict:
    config = runtime or load_runtime_config()
    league = LEAGUES[league_key]
    if not league.fresh_enabled:
        raise DailyContentError(
            f"{league_key} is configured but fresh content promotion is not enabled"
        )
    if not league.identity_verified:
        raise DailyContentError("league identity requires a live identity check")
    migrations = ensure_runtime_databases(config)
    config.artifact_dir.mkdir(parents=True, exist_ok=True)
    config.studio_output_dir.mkdir(parents=True, exist_ok=True)
    observed_at = now_iso or utc_now_iso()
    now = _parse_utc(observed_at)
    budget = RequestBudget(config.max_request_attempts)
    artifacts = ImmutableArtifactRun(config.artifact_dir, league_key, observed_at)
    from backend.fotmob_client import FotMobClient

    client = FotMobClient(max_retries=1)
    season_resolution = resolve_current_season(
        competition_id=league.fotmob_competition_id,
        season_kind=SeasonKind(league.season_rule),
        now=now,
    )
    if (
        season_resolution.verification_status
        is not SeasonVerification.VERIFIED
        or season_resolution.provider_season_parameter is None
    ):
        raise DailyContentError("FotMob season is unverified")
    requested_provider_season = (
        season_resolution.provider_season_parameter
    )

    league_payload, league_path = _fotmob_json(
        client,
        budget,
        artifacts,
        url=(
            "https://www.fotmob.com/api/data/leagues"
            f"?id={league.fotmob_competition_id}"
            f"&season={requested_provider_season}"
        ),
        operation="league_matches",
        raw_name=f"fotmob-league-{league.fotmob_competition_id}.raw.json",
        content_name=f"fotmob-league-{league.fotmob_competition_id}.json",
    )
    details = league_payload.get("details")
    if not isinstance(details, dict):
        raise DailyContentError("FotMob league identity is missing")
    returned_season = str(details.get("selectedSeason") or "")
    if (
        int(details.get("id") or 0) != league.fotmob_competition_id
        or details.get("name") != league.expected_competition_name
        or returned_season != requested_provider_season
    ):
        raise DailyContentError("FotMob competition name or season mismatch")
    pagination = inspect_known_pagination(league_payload)
    if pagination["status"] != "NOT_DETECTED":
        raise DailyContentError("FotMob schedule pagination is not complete")

    candidates = _fixture_candidates(
        league_payload,
        config=league,
        now=now,
        horizon_days=config.content_horizon_days,
    )
    if match_id is not None:
        candidates = [row for row in candidates if row["match_id"] == match_id]
    if not candidates:
        raise DailyContentError("no real match is available in the content horizon")

    selected = None
    mapping = None
    selected_records = None
    odds_path = None
    odds_observed_at = observed_at
    schedule_dates: dict[str, list[dict]] = {}
    odds_polled = False
    next_due_at = None
    latest = None
    try:
        latest = _read_latest_manifest(config, league_key)
    except DailyContentError:
        latest = None

    for candidate in candidates:
        kickoff = _parse_utc(candidate["kickoff_at_utc"])
        beijing_date = kickoff.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        if beijing_date not in schedule_dates:
            schedule_dates[beijing_date] = _nowgoal_schedule(
                budget, artifacts, beijing_date
            )
        mapped = _discover_nowgoal_match(candidate, schedule_dates[beijing_date], league)
        if mapped is None:
            continue
        with connect_rw("odds") as odds_conn:
            decision = _poll_decision(
                candidate["kickoff_at_utc"],
                now_iso=observed_at,
                conn=odds_conn,
                match_id=candidate["match_id"],
            )
            next_due_at = decision.get("next_due_at")
            if decision["due"] or latest is None:
                records, candidate_odds_path = _nowgoal_odds(
                    budget, artifacts, str(mapped["titan_id"])
                )
                mark_polled(
                    odds_conn,
                    SOURCE_NOWGOAL_ODDS,
                    str(candidate["match_id"]),
                    observed_at,
                    artifacts.run_id,
                )
                odds_polled = True
            else:
                previous_files = _paths_from_manifest(config, latest)
                candidate_odds_path = previous_files["odds"]
                payload = json.loads(candidate_odds_path.read_text(encoding="utf-8"))
                records = nowgoal.parse_odds(payload)
                odds_observed_at = latest.get(
                    "odds_observed_at", latest["observed_at"]
                )
            mark_polled(
                odds_conn,
                SOURCE_NOWGOAL_SCHEDULE,
                beijing_date,
                observed_at,
                artifacts.run_id,
            )
        if any(
            row.get("market") == "1x2" and isinstance(row.get("latest"), dict)
            for row in records
        ):
            selected = candidate
            mapping = mapped
            selected_records = records
            odds_path = candidate_odds_path
            break
    if selected is None or mapping is None or selected_records is None or odds_path is None:
        raise DailyContentError("no FotMob and NowGoal match with real 1x2 was found")

    match_payload, match_path = _fotmob_match(
        client, budget, artifacts, selected["match_id"]
    )
    general = match_payload.get("general")
    if (
        not isinstance(general, dict)
        or int(general.get("matchId") or 0) != selected["match_id"]
        or int((general.get("homeTeam") or {}).get("id") or 0) != selected["home_id"]
        or int((general.get("awayTeam") or {}).get("id") or 0) != selected["away_id"]
    ):
        raise DailyContentError("FotMob match detail identity mismatch")
    assembly_context = _build_assembly_context(
        league,
        season=returned_season,
        selected=selected,
        mapping=mapping,
    )
    _, home_path = _fotmob_json(
        client,
        budget,
        artifacts,
        url=f"https://www.fotmob.com/api/data/teams?id={selected['home_id']}",
        operation="team_data",
        raw_name=f"fotmob-team-{selected['home_id']}.raw.json",
        content_name=f"fotmob-team-{selected['home_id']}.json",
    )
    _, away_path = _fotmob_json(
        client,
        budget,
        artifacts,
        url=f"https://www.fotmob.com/api/data/teams?id={selected['away_id']}",
        operation="team_data",
        raw_name=f"fotmob-team-{selected['away_id']}.raw.json",
        content_name=f"fotmob-team-{selected['away_id']}.json",
    )

    content_files = {
        "league": {
            "path": str(league_path.relative_to(config.artifact_dir)),
            "sha256": hashlib.sha256(league_path.read_bytes()).hexdigest(),
        },
        "match": {
            "path": str(match_path.relative_to(config.artifact_dir)),
            "sha256": hashlib.sha256(match_path.read_bytes()).hexdigest(),
        },
        "home": {
            "path": str(home_path.relative_to(config.artifact_dir)),
            "sha256": hashlib.sha256(home_path.read_bytes()).hexdigest(),
        },
        "away": {
            "path": str(away_path.relative_to(config.artifact_dir)),
            "sha256": hashlib.sha256(away_path.read_bytes()).hexdigest(),
        },
        "odds": (
            {
                "path": str(odds_path.relative_to(config.artifact_dir)),
                "sha256": hashlib.sha256(odds_path.read_bytes()).hexdigest(),
            }
            if config.artifact_dir in odds_path.parents
            else latest["content_files"]["odds"]
        ),
    }
    artifact_paths = {
        "league": league_path,
        "match": match_path,
        "home": home_path,
        "away": away_path,
        "odds": odds_path,
    }
    summary = apply_saved_artifacts(
        artifacts.run_dir,
        config.studio_output_dir,
        artifact_paths=artifact_paths,
        observed_at=odds_observed_at,
        style_cutoff_at=observed_at,
        free_outcome=free_outcome,
        context=assembly_context,
    )
    due_after = due_status(
        league_key,
        match_id=selected["match_id"],
        runtime=config,
        now_iso=observed_at,
    )
    next_due_at = due_after.get("next_due_at") or next_due_at
    manifest = {
        "manifest_version": 1,
        "status": "SUCCEEDED",
        "league_key": league_key,
        "league_config": asdict(league),
        "run_id": artifacts.run_id,
        "observed_at": observed_at,
        "odds_observed_at": odds_observed_at,
        "request_attempts": budget.attempts,
        "request_operations": budget.operations,
        "identity": {
            "competition_id": league.fotmob_competition_id,
            "competition_name": details["name"],
            "season": returned_season,
        },
        "selection": selected,
        "mapping": {
            "fotmob_match_id": selected["match_id"],
            "nowgoal_match_id": str(mapping["titan_id"]),
            "confidence": mapping["confidence"],
            "kickoff_diff_seconds": mapping["kickoff_diff_seconds"],
        },
        "assembly_context": asdict(assembly_context),
        "odds_polled": odds_polled,
        "content_files": content_files,
        "all_artifacts": artifacts.files,
        "summary": summary,
    }
    artifacts.write_json(
        "run-manifest.json",
        manifest,
        role="immutable_run_manifest",
    )
    _publish_manifest(config, manifest)
    status = _status_after_success(
        config,
        summary,
        observed_at=observed_at,
        next_due_at=next_due_at,
        free_outcome=free_outcome,
        request_attempts=budget.attempts,
        run_id=artifacts.run_id,
        mode="fresh",
    )
    _write_content_status(config, status)
    return {
        **summary,
        "mode": "fresh",
        "run_id": artifacts.run_id,
        "request_attempts": budget.attempts,
        "request_operations": budget.operations,
        "artifact_manifest": str(
            config.artifact_dir / f"latest-{league_key}.json"
        ),
        "content_horizon_days": config.content_horizon_days,
        "odds_policy": {
            "over_72h": "one initial observation only",
            "t_minus_72h_to_24h_seconds": CADENCE_NOWGOAL_ODDS.tiers[2][1],
            "t_minus_24h_to_6h_seconds": CADENCE_NOWGOAL_ODDS.tiers[1][1],
            "t_minus_6h_to_kickoff_seconds": CADENCE_NOWGOAL_ODDS.tiers[0][1],
            "last_call_seconds": CADENCE_NOWGOAL_ODDS.last_call_seconds,
            "in_play": "excluded",
        },
        "odds_polled": odds_polled,
        "next_due_at": next_due_at,
        "status": status,
        "migrations_applied": migrations,
    }


def run_replay(
    league_key: str,
    *,
    free_outcome: str = "home",
    runtime: RuntimeConfig | None = None,
) -> dict:
    config = runtime or load_runtime_config()
    migrations = ensure_runtime_databases(config)
    manifest = _read_latest_manifest(config, league_key)
    artifact_paths = _paths_from_manifest(config, manifest)
    context_payload = manifest.get("assembly_context")
    try:
        assembly_context = (
            ActiveLeagueContext(**context_payload)
            if isinstance(context_payload, dict)
            else DEFAULT_CONTEXT
        )
    except (TypeError, ValueError):
        raise DailyContentError("latest artifact assembly context is invalid") from None
    summary = apply_saved_artifacts(
        config.artifact_dir,
        config.studio_output_dir,
        artifact_paths=artifact_paths,
        observed_at=manifest.get("odds_observed_at", manifest["observed_at"]),
        style_cutoff_at=manifest["observed_at"],
        free_outcome=free_outcome,
        context=assembly_context,
    )
    due = due_status(
        league_key,
        match_id=summary["match"]["fotmob_id"],
        runtime=config,
    )
    status = _status_after_success(
        config,
        summary,
        observed_at=manifest["observed_at"],
        next_due_at=due.get("next_due_at"),
        free_outcome=free_outcome,
        request_attempts=0,
        run_id=manifest["run_id"],
        mode="replay",
    )
    _write_content_status(config, status)
    return {
        **summary,
        "mode": "replay",
        "run_id": manifest["run_id"],
        "request_attempts": 0,
        "network": "disabled",
        "next_due_at": due.get("next_due_at"),
        "status": status,
        "migrations_applied": migrations,
    }
