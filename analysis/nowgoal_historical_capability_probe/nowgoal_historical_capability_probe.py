"""Direct-only, resumable NowGoal historical availability probe.

The probe deliberately does not use ``backend.providers.nowgoal._http_get``:
that production helper inherits ambient HTTP proxy variables through httpx.
This research transport constructs ``httpx.Client(trust_env=False)`` so it never
picks up whatever proxy happens to be sitting in HTTP_PROXY/HTTPS_PROXY —
by default that means a direct connection. A caller may still pass an explicit
``proxy=`` URL into ``DirectNowGoalTransport`` (e.g. the same THORDATA_PROXY
used for FotMob) when it wants one; that is always a visible, deliberate choice
at the call site, never an ambient leak.

Saved FotMob schedule artifacts are the only target source.  For each
historical competition the probe chooses one deterministic finished,
non-cancelled match from the early, middle, and late completed provider
seasons.  It then asks NowGoal for the corresponding Beijing calendar date,
requires a unique kickoff/team mapping, and only then requests the Titan odds
record.  Returned ``f``/``l`` values are described as initial/latest only:
they are never promoted to a complete historical timeline or verified closing
odds.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import threading
import time
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol
from zoneinfo import ZoneInfo

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from analysis.multi_league_season_coverage.multi_league_season_coverage import (  # noqa: E402
    COMPETITIONS,
    CompetitionSpec,
    _fixture_list,
    _positive_int,
    _returned_season,
    deterministic_samples,
    extract_available_seasons,
    provider_season_sort_key,
    validate_schedule,
)
from backend.providers import nowgoal  # noqa: E402


class HistoryProbeError(RuntimeError):
    """Fixed, credential-safe probe failure.

    ``diagnostic`` (optional attribute, not part of ``str(self)``) may carry
    a redacted description of the underlying cause — exception type/message
    or the real HTTP status code. It is never included in the message raised
    to callers (that stays the fixed, safe string); it exists purely so
    ``DurableArtifactStore.acquire()`` can log it into the internal 0600
    request ledger. Before this, every failure — timeout, connection reset,
    HTTP 429, WAF block that didn't match ``looks_blocked`` — collapsed into
    the same opaque "NowGoal direct request failed", making it impossible to
    tell rate-limiting from a genuine outage after the fact.
    """

    diagnostic: str | None = None


class HistoryProbeWAFBlocked(HistoryProbeError):
    """Direct NowGoal transport encountered a WAF response."""


class RequestBudgetExhausted(HistoryProbeError):
    """The durable request budget was exhausted before transport."""


class ArtifactValidationError(HistoryProbeError):
    """A saved artifact is absent, mutable, or invalid."""


class ProbeTransport(Protocol):
    def schedule(self, beijing_date: str) -> bytes:
        ...

    def odds(self, titan_id: str) -> bytes:
        ...

    def archive_season(self, league_id: int, season: str) -> bytes:
        ...

    def odds_history(self, titan_id: str, company_id: str) -> bytes:
        ...

    def euro_catalog(self, titan_id: str) -> bytes:
        ...

    def euro_history(self, titan_id: str, company_id: str) -> bytes:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True)
class HistoricalTarget:
    competition: str
    competition_id: int
    provider_season: str
    era: str
    fotmob_match_id: int
    home_name: str
    away_name: str
    kickoff_utc: str
    beijing_date: str
    source_artifact: str
    source_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArchiveLeagueSpec:
    key: str
    league_id: int
    expected_name: str


@dataclass(frozen=True)
class ArchiveMatch:
    competition: str
    league_id: int
    season: str
    titan_id: str
    home_name: str
    away_name: str
    kickoff_utc: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


TOP5_ARCHIVE_LEAGUES = (
    ArchiveLeagueSpec("premier_league", 36, "English Premier League"),
    ArchiveLeagueSpec("serie_a", 34, "Italy Serie A"),
    ArchiveLeagueSpec("la_liga", 31, "Spanish La Liga"),
    ArchiveLeagueSpec("bundesliga", 8, "German Bundesliga"),
    ArchiveLeagueSpec("ligue_1", 11, "France Ligue 1"),
)
ARCHIVE_COMPANIES = (
    ("bet365", "8", ("bet365",)),
    ("macauslot", "1", ("macauslot", "macau")),
    ("pinnacle", None, ("pinnacle", "pinnacle sports", "pinbet88")),
)
EURO_ARCHIVE_COMPANIES = (
    ("bet365", "281", ("bet 365", "bet365")),
    ("macauslot", "80", ("macauslot", "macau")),
    ("pinnacle", "177", ("pinnacle",)),
)


HISTORICAL_COMPETITION_KEYS = tuple(
    key for key, spec in COMPETITIONS.items() if spec.scope == "historical"
)
DEFAULT_SOURCE_RUN = (
    _REPO_ROOT
    / "runtime"
    / "research"
    / "league-coverage"
    / "multi-league-season-coverage-v1"
)
DEFAULT_OUTPUT_ROOT = (
    _REPO_ROOT / "runtime" / "research" / "nowgoal-historical-capability"
)
_SAFE_KEY_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")
_UTC = timezone.utc
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_MATCH_TOLERANCE_SECONDS = 1800
_NAME_THRESHOLD = 0.72


def _utc_now() -> str:
    return datetime.now(_UTC).isoformat().replace("+00:00", "Z")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_key(value: str) -> str:
    if not isinstance(value, str) or not _SAFE_KEY_RE.fullmatch(value):
        raise HistoryProbeError("invalid probe identifier") from None
    return value


_CREDENTIAL_IN_URL_RE = re.compile(r"(?i)://[^/\s@]+:[^/\s@]+@")
_DIAGNOSTIC_MAX_LEN = 200


def _redact_diagnostic(text: str) -> str:
    """``httpx`` proxy exceptions sometimes echo the failing URL, which can
    embed ``user:pass@`` from ``THORDATA_PROXY``. Strip that before this ever
    reaches a ledger file, then cap the length — this is a diagnostic hint,
    not a full traceback dump."""
    text = _CREDENTIAL_IN_URL_RE.sub("://[REDACTED]@", text)
    return text[:_DIAGNOSTIC_MAX_LEN]


# 实测证据(2026-08-06):对 ThorData 代理网关的原始 TCP 连接卡在 SYN_SENT 状态超过
# 5 分钟、从未收到 SYN-ACK,而 httpx.Client(timeout=nowgoal.FETCH_TIMEOUT_SECONDS)
# 配置的超时全程没有触发——httpx/httpcore 自身的超时机制在这种连接层面的挂起面前
# 不可信。下面这个看门狗是独立于 httpx 超时配置的硬性兜底:在单独线程里发起请求,
# 用 thread.join(timeout) 卡一个绝对的墙钟上限;超时就直接判定失败并继续,不等
# 那条线程(它可能永远卡着,daemon=True 保证不会拖住进程退出)。每次调用开一条全新
# 线程而不是共享线程池——共享池如果被多次挂起占满会重新引入同样的"排队等一个永远
# 不会释放的 worker"问题。
_HTTP_WATCHDOG_TIMEOUT_SECONDS = nowgoal.FETCH_TIMEOUT_SECONDS + 10.0


def _run_with_watchdog(fn: Callable[[], Any], timeout_seconds: float) -> Any:
    result: dict[str, Any] = {}

    def runner() -> None:
        try:
            result["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread below
            result["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        raise TimeoutError(
            f"no response after {timeout_seconds:.0f}s (watchdog, underlying call abandoned)"
        )
    if "error" in result:
        raise result["error"]
    return result["value"]


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


def _write_json(path: Path, value: Any) -> None:
    _write_private(
        path,
        (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        ).encode("utf-8"),
    )


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise HistoryProbeError("target kickoff is invalid") from None
    if parsed.tzinfo is None:
        raise HistoryProbeError("target kickoff is invalid") from None
    return parsed.astimezone(_UTC)


def _effective_spec(spec: CompetitionSpec, raw: Mapping[str, Any]) -> CompetitionSpec:
    if spec.competition_id is not None:
        return spec
    details = raw.get("details")
    competition_id = details.get("id") if isinstance(details, Mapping) else None
    if (
        not isinstance(competition_id, int)
        or isinstance(competition_id, bool)
        or competition_id <= 0
    ):
        raise HistoryProbeError("source competition identity is invalid") from None
    return CompetitionSpec(
        spec.key,
        competition_id,
        spec.expected_name,
        spec.season_kind,
        spec.structure,
        spec.earliest_year,
        spec.scope,
    )


def select_era_targets(
    candidates: Iterable[HistoricalTarget],
) -> list[HistoricalTarget]:
    """Select one deterministic middle fixture from three season eras."""

    by_competition: dict[str, dict[str, list[HistoricalTarget]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for candidate in candidates:
        by_competition[candidate.competition][
            candidate.provider_season
        ].append(candidate)

    result: list[HistoricalTarget] = []
    competition_order = {
        key: index for index, key in enumerate(HISTORICAL_COMPETITION_KEYS)
    }
    for competition in sorted(
        by_competition,
        key=lambda key: (competition_order.get(key, 10_000), key),
    ):
        seasons = sorted(
            by_competition[competition],
            key=provider_season_sort_key,
        )
        if not seasons:
            continue
        season_choices = (
            ("early", 0),
            ("middle", len(seasons) // 2),
            ("late", len(seasons) - 1),
        )
        seen_seasons: set[str] = set()
        for era, index in season_choices:
            season = seasons[index]
            if season in seen_seasons:
                continue
            seen_seasons.add(season)
            rows = sorted(
                by_competition[competition][season],
                key=lambda row: (row.kickoff_utc, row.fotmob_match_id),
            )
            selected = rows[len(rows) // 2]
            result.append(replace(selected, era=era))
    return result


def load_historical_targets(
    source_run: Path,
    selected_competitions: Iterable[str] | None = None,
) -> list[HistoricalTarget]:
    """Build targets from immutable, previously validated schedule artifacts."""

    source_run = source_run.resolve()
    raw_dir = source_run / "raw"
    if not raw_dir.is_dir():
        raise HistoryProbeError("validated source artifact directory is missing") from None
    selected = set(selected_competitions or HISTORICAL_COMPETITION_KEYS)
    if not selected or not selected <= set(HISTORICAL_COMPETITION_KEYS):
        raise HistoryProbeError("invalid competition selection") from None

    candidates: list[HistoricalTarget] = []
    for key in HISTORICAL_COMPETITION_KEYS:
        if key not in selected:
            continue
        spec = COMPETITIONS[key]
        schedule_paths = sorted(raw_dir.glob(f"{key}.season.*.json"))
        schedule_paths = [
            path for path in schedule_paths if ".match." not in path.name
        ]
        if not schedule_paths:
            raise HistoryProbeError("validated schedule artifacts are missing") from None
        for path in schedule_paths:
            data = path.read_bytes()
            try:
                raw = json.loads(data)
            except json.JSONDecodeError:
                raise HistoryProbeError("validated schedule artifact is invalid") from None
            if not isinstance(raw, dict):
                raise HistoryProbeError("validated schedule artifact is invalid") from None
            provider_season = _returned_season(raw)
            if provider_season is None:
                raise HistoryProbeError("validated schedule season is invalid") from None
            effective_spec = _effective_spec(spec, raw)
            try:
                fixtures, _, _ = validate_schedule(
                    raw,
                    effective_spec,
                    provider_season,
                    extract_available_seasons(raw),
                )
            except Exception:
                raise HistoryProbeError(
                    "validated schedule artifact failed revalidation"
                ) from None
            active_or_partial = (
                not deterministic_samples(fixtures)
                or any(
                    row["finished"] is not True
                    and row["cancelled"] is not True
                    for row in fixtures
                )
            )
            if active_or_partial:
                continue
            fixture_names: dict[int, tuple[str, str]] = {}
            for fixture in _fixture_list(raw):
                home = fixture.get("home")
                away = fixture.get("away")
                match_id = _positive_int(fixture.get("id"))
                if (
                    match_id is not None
                    and isinstance(home, Mapping)
                    and isinstance(away, Mapping)
                    and isinstance(home.get("name"), str)
                    and home.get("name").strip()
                    and isinstance(away.get("name"), str)
                    and away.get("name").strip()
                ):
                    fixture_names[match_id] = (
                        home["name"].strip(),
                        away["name"].strip(),
                    )
            source_relative = str(path.relative_to(source_run))
            source_digest = _sha256(data)
            for row in fixtures:
                if row["finished"] is not True or row["cancelled"] is True:
                    continue
                names = fixture_names.get(int(row["match_id"]))
                if names is None:
                    raise HistoryProbeError(
                        "validated schedule team names are missing"
                    ) from None
                kickoff = _parse_utc(str(row["kickoff_utc"]))
                candidates.append(
                    HistoricalTarget(
                        competition=key,
                        competition_id=int(effective_spec.competition_id or 0),
                        provider_season=provider_season,
                        era="",
                        fotmob_match_id=int(row["match_id"]),
                        home_name=names[0],
                        away_name=names[1],
                        kickoff_utc=kickoff.isoformat().replace("+00:00", "Z"),
                        beijing_date=kickoff.astimezone(_SHANGHAI).date().isoformat(),
                        source_artifact=source_relative,
                        source_sha256=source_digest,
                    )
                )
    targets = select_era_targets(candidates)
    counts: dict[str, int] = defaultdict(int)
    for target in targets:
        counts[target.competition] += 1
    if any(counts[key] != 3 for key in selected):
        raise HistoryProbeError("historical target selection is incomplete") from None
    return targets


def _normalized_tokens(value: str) -> tuple[str, ...]:
    folded = unicodedata.normalize("NFKD", str(value))
    ascii_text = "".join(
        character
        for character in folded
        if not unicodedata.combining(character)
    )
    return tuple(re.findall(r"[a-z0-9]+", ascii_text.casefold()))


def _name_similarity(left: str, right: str) -> float:
    left_tokens = _normalized_tokens(left)
    right_tokens = _normalized_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    removable = {"fc", "cf", "sc", "afc", "fk", "sk", "club", "calcio"}
    left_core = tuple(token for token in left_tokens if token not in removable)
    right_core = tuple(token for token in right_tokens if token not in removable)
    left_text = "".join(left_core or left_tokens)
    right_text = "".join(right_core or right_tokens)
    scores = [SequenceMatcher(None, left_text, right_text).ratio()]
    if set(left_core) and set(right_core):
        intersection = len(set(left_core) & set(right_core))
        scores.append(
            2 * intersection / (len(set(left_core)) + len(set(right_core)))
        )
    if len(left_core) >= 2 and len(right_core) >= 2:
        if left_core[-1] == right_core[-1]:
            left_prefix = "".join(token[0] for token in left_core[:-1])
            right_prefix = "".join(token[0] for token in right_core[:-1])
            if (
                left_prefix == right_core[0]
                or right_prefix == left_core[0]
            ):
                scores.append(0.9)
    return max(scores)


def _nowgoal_kickoff(value: Any) -> datetime | None:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2} \d{1,2}:\d{2}(:\d{2})?",
        value,
    ):
        return None
    try:
        return datetime.fromisoformat(value.replace(" ", "T")).replace(
            tzinfo=_UTC
        )
    except ValueError:
        return None


def map_nowgoal_match(
    target: HistoricalTarget,
    schedule_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Return one uniquely supported direct or inverted provider mapping."""

    target_kickoff = _parse_utc(target.kickoff_utc)
    scored: list[tuple[float, str, dict[str, Any]]] = []
    for row in schedule_rows:
        titan_id = str(row.get("titan_id") or "")
        provider_kickoff = _nowgoal_kickoff(row.get("kickoff"))
        if not titan_id.isdigit() or provider_kickoff is None:
            continue
        diff = abs((provider_kickoff - target_kickoff).total_seconds())
        if diff > _MATCH_TOLERANCE_SECONDS:
            continue
        direct = (
            _name_similarity(target.home_name, str(row.get("home_name") or "")),
            _name_similarity(target.away_name, str(row.get("away_name") or "")),
            False,
        )
        inverted = (
            _name_similarity(target.home_name, str(row.get("away_name") or "")),
            _name_similarity(target.away_name, str(row.get("home_name") or "")),
            True,
        )
        home_score, away_score, is_inverted = max(
            (direct, inverted),
            key=lambda value: (min(value[0], value[1]), value[0] + value[1]),
        )
        confidence = (
            (home_score + away_score) / 2
        ) * max(0.0, 1.0 - diff / _MATCH_TOLERANCE_SECONDS)
        if min(home_score, away_score) < _NAME_THRESHOLD:
            continue
        if confidence < _NAME_THRESHOLD:
            continue
        mapped = {
            "titan_id": titan_id,
            "provider_home_id": row.get("provider_home_id"),
            "provider_away_id": row.get("provider_away_id"),
            "provider_home_name": str(row.get("home_name") or ""),
            "provider_away_name": str(row.get("away_name") or ""),
            "provider_kickoff": provider_kickoff.isoformat().replace(
                "+00:00", "Z"
            ),
            "kickoff_diff_seconds": int(diff),
            "home_name_score": round(home_score, 6),
            "away_name_score": round(away_score, 6),
            "confidence": round(confidence, 6),
            "home_away_inverted": is_inverted,
        }
        scored.append((confidence, titan_id, mapped))
    scored.sort(key=lambda value: (-value[0], value[1]))
    if not scored:
        return None
    if len(scored) > 1 and abs(scored[0][0] - scored[1][0]) < 0.01:
        return None
    return scored[0][2]


def parse_archive_season(
    raw: Mapping[str, Any],
    spec: ArchiveLeagueSpec,
    season: str,
) -> list[ArchiveMatch]:
    """Validate one public league-season JSON and return finished matches."""

    league_info = raw.get("LeagueInfo")
    schedule = raw.get("ScheduleList")
    teams = raw.get("TeamInfo")
    if (
        not isinstance(league_info, list)
        or len(league_info) < 3
        or league_info[0] != spec.league_id
        or league_info[1] != spec.expected_name
        or league_info[2] != season
        or not isinstance(schedule, Mapping)
        or not isinstance(teams, list)
    ):
        raise HistoryProbeError("archive season identity is invalid") from None
    team_names: dict[int, str] = {}
    for team in teams:
        if (
            isinstance(team, list)
            and len(team) >= 2
            and isinstance(team[0], int)
            and isinstance(team[1], str)
            and team[1].strip()
        ):
            team_names[team[0]] = team[1].strip()
    matches: list[ArchiveMatch] = []
    seen: set[str] = set()
    round_groups: list[Mapping[str, Any]] = [schedule]
    direct_rounds = any(
        isinstance(key, str) and key.startswith("R_") for key in schedule
    )
    if not direct_rounds:
        round_groups = [
            value
            for key, value in schedule.items()
            if isinstance(key, str)
            and key.startswith("sub_")
            and isinstance(value, Mapping)
        ]
        if not round_groups:
            raise HistoryProbeError("archive season schedule is invalid") from None
    for round_key, round_rows in (
        item for group in round_groups for item in group.items()
    ):
        if not isinstance(round_key, str) or not round_key.startswith("R_"):
            continue
        if not isinstance(round_rows, list):
            raise HistoryProbeError("archive season schedule is invalid") from None
        for row in round_rows:
            if not isinstance(row, list) or len(row) < 8 or row[2] != -1:
                continue
            titan_id = str(row[0])
            if not titan_id.isdigit() or titan_id in seen:
                raise HistoryProbeError("archive season match identity is invalid") from None
            home = team_names.get(row[4])
            away = team_names.get(row[5])
            if home is None or away is None or not isinstance(row[3], str):
                raise HistoryProbeError("archive season match is invalid") from None
            try:
                local_kickoff = datetime.strptime(
                    row[3], "%Y-%m-%d %H:%M"
                ).replace(tzinfo=_SHANGHAI)
            except ValueError:
                raise HistoryProbeError("archive season kickoff is invalid") from None
            seen.add(titan_id)
            matches.append(
                ArchiveMatch(
                    competition=spec.key,
                    league_id=spec.league_id,
                    season=season,
                    titan_id=titan_id,
                    home_name=home,
                    away_name=away,
                    kickoff_utc=local_kickoff.astimezone(_UTC)
                    .isoformat()
                    .replace("+00:00", "Z"),
                )
            )
    if not matches:
        raise HistoryProbeError("archive season has no finished matches") from None
    return sorted(matches, key=lambda row: (row.kickoff_utc, row.titan_id))


def select_archive_sample(matches: Iterable[ArchiveMatch]) -> ArchiveMatch:
    rows = sorted(matches, key=lambda row: (row.kickoff_utc, row.titan_id))
    if not rows:
        raise HistoryProbeError("archive season has no finished matches") from None
    return rows[len(rows) // 2]


def parse_company_catalog(payload: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    data = payload.get("Data")
    companies = data.get("mixodds") if isinstance(data, Mapping) else None
    if not isinstance(companies, list):
        return {}
    catalog: dict[str, dict[str, str]] = {}
    for company in companies:
        if not isinstance(company, Mapping):
            continue
        cid = str(company.get("cid") or "").strip()
        name = str(company.get("cn") or "").strip()
        if cid.isdigit() and name:
            catalog[cid] = {"company_id": cid, "company_name": name}
    return catalog


def resolve_archive_companies(
    catalog: Mapping[str, Mapping[str, str]],
) -> dict[str, dict[str, str] | None]:
    result: dict[str, dict[str, str] | None] = {}
    for key, fixed_cid, aliases in ARCHIVE_COMPANIES:
        selected = catalog.get(fixed_cid) if fixed_cid is not None else None
        if selected is None:
            for company in catalog.values():
                normalized = str(company.get("company_name") or "").strip().casefold()
                if normalized in aliases:
                    selected = company
                    break
        result[key] = dict(selected) if selected is not None else None
    return result


def summarize_history(
    payload: Mapping[str, Any],
    kickoff_utc: str,
) -> dict[str, Any]:
    if payload.get("ErrCode") != 0 or not isinstance(payload.get("Data"), Mapping):
        raise HistoryProbeError("historical odds response is invalid") from None
    kickoff_epoch = int(_parse_utc(kickoff_utc).timestamp())
    markets: dict[str, dict[str, Any]] = {}
    for provider_key, market in (("ah", "ah"), ("op", "1x2"), ("ou", "ou")):
        rows = payload["Data"].get(provider_key)
        if not isinstance(rows, list):
            raise HistoryProbeError("historical odds response is invalid") from None
        timestamps: list[int] = []
        pre_match = 0
        in_play = 0
        for row in rows:
            if not isinstance(row, Mapping):
                raise HistoryProbeError("historical odds response is invalid") from None
            timestamp = row.get("mt")
            if not isinstance(timestamp, int) or isinstance(timestamp, bool):
                raise HistoryProbeError("historical odds timestamp is invalid") from None
            timestamps.append(timestamp)
            if timestamp < kickoff_epoch:
                pre_match += 1
            else:
                in_play += 1
        markets[market] = {
            "rows": len(rows),
            "pre_match_rows": pre_match,
            "in_play_rows": in_play,
            "first_observed_at": (
                datetime.fromtimestamp(min(timestamps), _UTC)
                .isoformat()
                .replace("+00:00", "Z")
                if timestamps
                else None
            ),
            "last_observed_at": (
                datetime.fromtimestamp(max(timestamps), _UTC)
                .isoformat()
                .replace("+00:00", "Z")
                if timestamps
                else None
            ),
        }
    return {
        "markets": markets,
        "pre_match_rows": sum(row["pre_match_rows"] for row in markets.values()),
        "in_play_rows": sum(row["in_play_rows"] for row in markets.values()),
    }


def parse_euro_catalog(data: bytes) -> dict[str, dict[str, str]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HistoryProbeError("1x2 company response is invalid") from None
    match = re.search(r"\bgame=Array\((.*?)\);", text, re.DOTALL)
    if match is None:
        raise HistoryProbeError("1x2 company response is invalid") from None
    try:
        entries = json.loads(f"[{match.group(1)}]")
    except json.JSONDecodeError:
        raise HistoryProbeError("1x2 company response is invalid") from None
    if not isinstance(entries, list):
        raise HistoryProbeError("1x2 company response is invalid") from None
    catalog: dict[str, dict[str, str]] = {}
    for entry in entries:
        if not isinstance(entry, str):
            raise HistoryProbeError("1x2 company response is invalid") from None
        fields = entry.split("|")
        if len(fields) < 3:
            raise HistoryProbeError("1x2 company response is invalid") from None
        cid = fields[0].strip()
        name = fields[2].strip()
        if cid.isdigit() and name:
            catalog[cid] = {"company_id": cid, "company_name": name}
    return catalog


def resolve_euro_companies(
    catalog: Mapping[str, Mapping[str, str]],
) -> dict[str, dict[str, str] | None]:
    result: dict[str, dict[str, str] | None] = {}
    for key, expected_cid, aliases in EURO_ARCHIVE_COMPANIES:
        selected = catalog.get(expected_cid)
        if selected is not None:
            normalized = str(selected.get("company_name") or "").strip().casefold()
            if normalized not in aliases:
                raise HistoryProbeError("1x2 company identity is invalid") from None
            result[key] = dict(selected)
            continue
        discovered = None
        for company in catalog.values():
            normalized = str(company.get("company_name") or "").strip().casefold()
            if normalized in aliases:
                discovered = dict(company)
                break
        result[key] = discovered
    return result


def summarize_euro_history(
    payload: Any,
    kickoff_utc: str,
) -> dict[str, Any]:
    if not isinstance(payload, list):
        raise HistoryProbeError("1x2 history response is invalid") from None
    kickoff = _parse_utc(kickoff_utc)
    timestamps: list[datetime] = []
    pre_match = 0
    in_play = 0
    for row in payload:
        if not isinstance(row, Mapping):
            raise HistoryProbeError("1x2 history response is invalid") from None
        for key in ("HomeWin", "Standoff", "GuestWin"):
            try:
                float(row[key])
            except (KeyError, TypeError, ValueError):
                raise HistoryProbeError("1x2 history response is invalid") from None
        time_show = row.get("TimeShow")
        if not isinstance(time_show, str):
            raise HistoryProbeError("1x2 history response is invalid") from None
        try:
            observed = datetime.strptime(
                time_show, "%Y,%m,%d,%H,%M,%S"
            ).replace(tzinfo=_UTC)
        except ValueError:
            raise HistoryProbeError("1x2 history response is invalid") from None
        timestamps.append(observed)
        if observed < kickoff:
            pre_match += 1
        else:
            in_play += 1
    return {
        "rows": len(payload),
        "pre_match_rows": pre_match,
        "in_play_rows": in_play,
        "first_observed_at": (
            min(timestamps).isoformat().replace("+00:00", "Z")
            if timestamps
            else None
        ),
        "last_observed_at": (
            max(timestamps).isoformat().replace("+00:00", "Z")
            if timestamps
            else None
        ),
    }


class DurableArtifactStore:
    """Small append-only request ledger plus immutable response artifacts."""

    def __init__(
        self,
        root: Path,
        run_id: str,
        *,
        mode: str,
        max_attempts: int,
    ) -> None:
        if mode not in {"live", "replay", "dry-run"}:
            raise HistoryProbeError("invalid probe mode") from None
        if (
            not isinstance(max_attempts, int)
            or isinstance(max_attempts, bool)
            or max_attempts < 0
        ):
            raise HistoryProbeError("invalid request budget") from None
        self.mode = mode
        self.max_attempts = max_attempts
        self.run_dir = root / _safe_key(run_id)
        self.raw_dir = self.run_dir / "raw"
        self.run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.raw_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.run_dir, 0o700)
        os.chmod(self.raw_dir, 0o700)
        self.ledger_path = self.run_dir / "request-ledger.jsonl"
        if not self.ledger_path.exists():
            _write_private(self.ledger_path, b"")
        self._rows = self._read_ledger()
        self._success = {
            str(row["request_key"]): row
            for row in self._rows
            if row.get("phase") == "SUCCEEDED"
            and isinstance(row.get("request_key"), str)
        }
        self._attempt_count = sum(
            row.get("phase") == "STARTED" for row in self._rows
        )

    def _read_ledger(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                raise ArtifactValidationError("request ledger is invalid") from None
            if not isinstance(row, dict):
                raise ArtifactValidationError("request ledger is invalid") from None
            rows.append(row)
        return rows

    @property
    def attempt_count(self) -> int:
        return self._attempt_count

    def _append(self, row: Mapping[str, Any]) -> None:
        data = (
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        fd = os.open(self.ledger_path, os.O_WRONLY | os.O_APPEND, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(self.ledger_path, 0o600)
        self._rows.append(dict(row))

    def _path(self, request_key: str) -> Path:
        return self.raw_dir / f"{_safe_key(request_key)}.bin"

    def _cached(self, request_key: str) -> bytes | None:
        row = self._success.get(request_key)
        if row is None:
            return None
        path = self._path(request_key)
        if not path.is_file():
            raise ArtifactValidationError("saved artifact is missing") from None
        data = path.read_bytes()
        if _sha256(data) != row.get("sha256"):
            raise ArtifactValidationError(
                "saved artifact checksum mismatch"
            ) from None
        if len(data) != row.get("size"):
            raise ArtifactValidationError("saved artifact size mismatch") from None
        return data

    def acquire(
        self,
        request_key: str,
        operation: str,
        fetch: Callable[[], bytes],
        *,
        retry_once: bool = True,
        retry_backoff_seconds: float = 0.0,
    ) -> bytes:
        """``retry_backoff_seconds``: extra sleep inserted before the *retry*
        attempt only (ordinal > 1), on top of whatever pacing ``fetch``
        already does on its own before every attempt. Default 0 preserves
        the exact previous behavior (retry fires back-to-back with only the
        caller's own jitter). A same-request retry that fires immediately
        after a failure is the same "hammer the same host right away"
        pattern as a too-fast batch; giving the retry its own longer,
        separate pause is a cheap mitigation without needing to know the
        real trigger (rate-limit, WAF, or something else server-side)."""
        request_key = _safe_key(request_key)
        cached = self._cached(request_key)
        if cached is not None:
            return cached
        if self.mode == "replay":
            raise ArtifactValidationError("replay artifact is unavailable") from None
        if self.mode != "live":
            raise HistoryProbeError("dry-run cannot acquire artifacts") from None
        for ordinal in range(1, (2 if retry_once else 1) + 1):
            if ordinal > 1 and retry_backoff_seconds > 0:
                time.sleep(retry_backoff_seconds)
            if self._attempt_count >= self.max_attempts:
                raise RequestBudgetExhausted("request budget exhausted") from None
            self._attempt_count += 1
            self._append(
                {
                    "phase": "STARTED",
                    "request_key": request_key,
                    "operation": operation,
                    "attempt": ordinal,
                    "at": _utc_now(),
                }
            )
            waf_blocked = False
            request_failed = False
            try:
                data = fetch()
                if not isinstance(data, bytes) or not data:
                    raise HistoryProbeError("NowGoal direct response is invalid")
            except HistoryProbeWAFBlocked:
                self._append(
                    {
                        "phase": "FAILED",
                        "request_key": request_key,
                        "operation": operation,
                        "attempt": ordinal,
                        "error": "NowGoal direct request was blocked",
                        "at": _utc_now(),
                    }
                )
                waf_blocked = True
            except Exception as exc:
                self._append(
                    {
                        "phase": "FAILED",
                        "request_key": request_key,
                        "operation": operation,
                        "attempt": ordinal,
                        "error": "NowGoal direct request failed",
                        "diagnostic": getattr(exc, "diagnostic", None),
                        "at": _utc_now(),
                    }
                )
                request_failed = True
            if waf_blocked:
                raise HistoryProbeWAFBlocked(
                    "NowGoal direct request was blocked"
                ) from None
            if request_failed:
                if ordinal == (2 if retry_once else 1):
                    raise HistoryProbeError(
                        "NowGoal direct request failed"
                    ) from None
                continue
            path = self._path(request_key)
            if path.exists():
                if path.read_bytes() != data:
                    raise ArtifactValidationError(
                        "saved artifact conflicts with response"
                    ) from None
            else:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    os.write(fd, data)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.chmod(path, 0o600)
            success = {
                "phase": "SUCCEEDED",
                "request_key": request_key,
                "operation": operation,
                "attempt": ordinal,
                "sha256": _sha256(data),
                "size": len(data),
                "at": _utc_now(),
            }
            self._append(success)
            self._success[request_key] = success
            return data
        raise HistoryProbeError("NowGoal direct request failed") from None


class DirectNowGoalTransport:
    """Transport that refuses all *ambient* proxy configuration (``trust_env=False``
    means HTTP_PROXY/HTTPS_PROXY/NO_PROXY env vars are always ignored, regardless of
    ``proxy``). An explicit ``proxy`` URL may still be passed in by the caller —
    that is a deliberate, visible choice at the call site, not an accidental leak
    of whatever proxy happens to be configured in the environment."""

    def __init__(self, *, proxy: str | None = None) -> None:
        import httpx

        self._client = httpx.Client(
            trust_env=False,
            timeout=nowgoal.FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
            proxy=proxy,
            # 一个持久连接池会在这批顺序请求里反复复用同一条 TCP 连接(在有代理时
            # 就是同一个代理出口)。实测证据:对同一批曾经失败的 titan_id 单独发起
            # 全新的 curl 连接(不管是否走代理)100% 成功,但用同一个 httpx.Client
            # 连续处理二十几场比赛(上百次请求复用同一条连接)会在某个点开始大范围
            # 失败。禁用 keep-alive 池,强制每次请求都是全新连接,用连接开销换稳定性
            # ——反正每次请求前已经有 0.8-1.5s 的随机 sleep,新建连接的额外耗时占比很小。
            limits=httpx.Limits(max_keepalive_connections=0, keepalive_expiry=0),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Referer": nowgoal.BASE_URL + "/",
                "Accept-Encoding": "gzip, deflate, br",
            },
        )

    def _get(
        self,
        url: str,
        params: Mapping[str, str],
        *,
        referer: str | None = None,
        xhr: bool = False,
    ) -> bytes:
        request_failed = False
        diagnostic: str | None = None
        try:
            request_headers: dict[str, str] = {}
            if referer is not None:
                request_headers["Referer"] = referer
            if xhr:
                request_headers["X-Requested-With"] = "XMLHttpRequest"
            response = _run_with_watchdog(
                lambda: self._client.get(
                    url,
                    params=dict(params),
                    headers=request_headers or None,
                ),
                _HTTP_WATCHDOG_TIMEOUT_SECONDS,
            )
            text = response.text
        except Exception as exc:
            request_failed = True
            diagnostic = _redact_diagnostic(f"{type(exc).__name__}: {exc}")
        if request_failed:
            err = HistoryProbeError("NowGoal direct request failed")
            err.diagnostic = diagnostic
            raise err from None
        if nowgoal.looks_blocked(text):
            raise HistoryProbeWAFBlocked(
                "NowGoal direct request was blocked"
            ) from None
        if response.status_code != 200:
            err = HistoryProbeError("NowGoal direct request failed")
            err.diagnostic = f"http_status={response.status_code}"
            raise err from None
        return text.encode("utf-8")

    def schedule(self, beijing_date: str) -> bytes:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", beijing_date):
            raise HistoryProbeError("invalid NowGoal schedule date") from None
        return self._get(
            nowgoal.SCHEDULE_URL,
            {
                "type": "6",
                "date": beijing_date,
                "order": "time",
                "timezone": "8",
                "flesh": nowgoal._flesh(),
            },
        )

    def odds(self, titan_id: str) -> bytes:
        if not str(titan_id).isdigit():
            raise HistoryProbeError("invalid NowGoal match identifier") from None
        return self._get(
            nowgoal.ODDS_URL,
            {
                "type": "14",
                "t": "1",
                "id": str(titan_id),
                "h": "0",
                "s": "0",
                "flesh": nowgoal._flesh(),
            },
        )

    def archive_season(self, league_id: int, season: str) -> bytes:
        if (
            not isinstance(league_id, int)
            or isinstance(league_id, bool)
            or league_id <= 0
            or not re.fullmatch(r"\d{4}-\d{4}", season)
        ):
            raise HistoryProbeError("invalid archive season request") from None
        return self._get(
            (
                "https://football.nowgoal26.com/jsData/matchResult/json/"
                f"{season}/s{league_id}_en.json"
            ),
            {},
            referer=(
                "https://football.nowgoal26.com/league/"
                f"{season}/{league_id}"
            ),
        )

    def odds_history(self, titan_id: str, company_id: str) -> bytes:
        if not str(titan_id).isdigit() or not str(company_id).isdigit():
            raise HistoryProbeError("invalid historical odds request") from None
        return self._get(
            "https://live10.nowgoal26.com/ajax/soccerajax",
            {
                "type": "14",
                "id": str(titan_id),
                "t": "20",
                "cid": str(company_id),
                "h": "0",
                "r1": "0",
                "r2": "0",
                "r3": "0",
            },
            referer=f"https://live10.nowgoal26.com/oddscomp/{titan_id}",
            xhr=True,
        )

    def euro_catalog(self, titan_id: str) -> bytes:
        if not str(titan_id).isdigit():
            raise HistoryProbeError("invalid 1x2 company request") from None
        return self._get(
            f"https://1x2.nowgoal26.com/{titan_id}.js",
            {},
            referer=f"https://live10.nowgoal26.com/1x2-odds/{titan_id}",
        )

    def euro_history(self, titan_id: str, company_id: str) -> bytes:
        if not str(titan_id).isdigit() or not str(company_id).isdigit():
            raise HistoryProbeError("invalid 1x2 history request") from None
        return self._get(
            (
                "https://live10.nowgoal26.com/football/"
                "geteurooddshistorydetail"
            ),
            {"sid": str(titan_id), "cid": str(company_id)},
            referer=f"https://live10.nowgoal26.com/1x2-odds/{titan_id}",
            xhr=True,
        )

    def odds_history_alt_host(self, titan_id: str, company_id: str) -> bytes:
        """Same data as ``odds_history()``, served from ``www.nowgoal.net``
        instead of ``live10.nowgoal26.com``. Verified: nowgoal.net shares
        the same titan_id namespace and returned an equivalent mix-history
        payload for a known-good match during diagnosis; not verified at
        volume. Unlike the primary host, this one puts the endpoint directly
        on the main domain (no ``live10.`` subdomain — that subdomain exists
        on nowgoal.net's DNS but serves an expired certificate for an
        unrelated site and 404s; do not use it). Intended as a manual,
        selective fallback for matches that keep failing on the primary host
        after retry/backoff — not wired into the default request path, since
        its own capacity under sustained load is unverified and blindly
        routing everything here just moves the same risk to a different
        domain."""
        if not str(titan_id).isdigit() or not str(company_id).isdigit():
            raise HistoryProbeError("invalid historical odds request") from None
        return self._get(
            "https://www.nowgoal.net/ajax/soccerajax",
            {
                "type": "14",
                "id": str(titan_id),
                "t": "20",
                "cid": str(company_id),
                "h": "0",
                "r1": "0",
                "r2": "0",
                "r3": "0",
                "flesh": nowgoal._flesh(),
            },
            referer=f"https://www.nowgoal.net/oddscomp/{titan_id}",
            xhr=True,
        )

    def euro_history_alt_host(self, titan_id: str, company_id: str) -> bytes:
        """``www.nowgoal.net`` equivalent of ``euro_history()`` — see
        ``odds_history_alt_host`` docstring for verification/usage notes."""
        if not str(titan_id).isdigit() or not str(company_id).isdigit():
            raise HistoryProbeError("invalid 1x2 history request") from None
        return self._get(
            "https://www.nowgoal.net/football/geteurooddshistorydetail",
            {"sid": str(titan_id), "cid": str(company_id)},
            referer=f"https://www.nowgoal.net/1x2-odds/{titan_id}",
            xhr=True,
        )

    def close(self) -> None:
        self._client.close()


class HistoryCapabilityProbe:
    def __init__(
        self,
        store: DurableArtifactStore,
        transport: ProbeTransport | None,
    ) -> None:
        self.store = store
        self.transport = transport

    def _schedule(self, beijing_date: str) -> bytes:
        if self.transport is None:
            return self.store.acquire(
                f"schedule.{beijing_date}",
                "nowgoal_schedule",
                lambda: b"",
            )
        return self.store.acquire(
            f"schedule.{beijing_date}",
            "nowgoal_schedule",
            lambda: self.transport.schedule(beijing_date),
        )

    def _odds(self, titan_id: str) -> bytes:
        if self.transport is None:
            return self.store.acquire(
                f"odds.{titan_id}",
                "nowgoal_odds",
                lambda: b"",
            )
        return self.store.acquire(
            f"odds.{titan_id}",
            "nowgoal_odds",
            lambda: self.transport.odds(titan_id),
        )

    def run(
        self,
        targets: Iterable[HistoricalTarget],
        control_dates: Iterable[str] = (),
        control_titan_ids: Iterable[str] = (),
    ) -> dict[str, Any]:
        target_rows = sorted(
            list(targets),
            key=lambda row: (
                row.beijing_date,
                row.competition,
                provider_season_sort_key(row.provider_season),
                row.fotmob_match_id,
            ),
        )
        _write_json(
            self.store.run_dir / "targets.json",
            {
                "schema_version": 1,
                "selection": "one_middle_match_from_early_middle_late_completed_seasons",
                "targets": [row.as_dict() for row in target_rows],
            },
        )
        if self.store.mode == "dry-run":
            result = {
                "schema_version": 1,
                "mode": "dry-run",
                "generated_at": _utc_now(),
                "counts": {"targets": len(target_rows)},
                "control_dates": sorted(set(control_dates)),
                "control_titan_ids": sorted(set(control_titan_ids)),
                "controls": [],
                "odds_controls": [],
                "results": [],
                "transport_attempts": self.store.attempt_count,
                "residential_proxy_used": False,
                "historical_timeline_verified": False,
                "closing_odds_verified": False,
                "verdict": "DRY_RUN_ONLY",
            }
            self._write_outputs(result)
            return result

        controls: list[dict[str, Any]] = []
        for control_date in sorted(set(control_dates)):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", control_date):
                raise HistoryProbeError("invalid control date") from None
            try:
                control_bytes = self._schedule(control_date)
                control_text = control_bytes.decode("utf-8")
                control_rows = nowgoal.parse_schedule(
                    nowgoal._extract_data_field(control_text)
                )
                kickoff_values = sorted(
                    str(row["kickoff"])
                    for row in control_rows
                    if row.get("kickoff")
                )
                controls.append(
                    {
                        "beijing_date": control_date,
                        "status": (
                            "AVAILABLE" if control_rows else "EMPTY"
                        ),
                        "match_count": len(control_rows),
                        "first_provider_kickoff": (
                            kickoff_values[0] if kickoff_values else None
                        ),
                        "last_provider_kickoff": (
                            kickoff_values[-1] if kickoff_values else None
                        ),
                    }
                )
            except HistoryProbeWAFBlocked:
                raise
            except Exception:
                controls.append(
                    {
                        "beijing_date": control_date,
                        "status": "REQUEST_FAILED",
                        "match_count": 0,
                        "first_provider_kickoff": None,
                        "last_provider_kickoff": None,
                    }
                )

        odds_controls: list[dict[str, Any]] = []
        for titan_id in sorted(set(str(value) for value in control_titan_ids)):
            if not titan_id.isdigit():
                raise HistoryProbeError(
                    "invalid control match identifier"
                ) from None
            try:
                control_odds_bytes = self._odds(titan_id)
                control_payload = json.loads(control_odds_bytes)
                if not isinstance(control_payload, dict):
                    raise ValueError
                control_records = nowgoal.parse_odds(control_payload)
                control_markets = sorted(
                    {
                        str(record["market"])
                        for record in control_records
                        if record.get("market")
                    }
                )
                odds_controls.append(
                    {
                        "titan_id": titan_id,
                        "status": (
                            "AVAILABLE"
                            if "1x2" in control_markets
                            else "NO_1X2"
                        ),
                        "markets": control_markets,
                        "selected_company_count": len(
                            {
                                str(record["company_id"])
                                for record in control_records
                                if record.get("company_id")
                            }
                        ),
                        "has_initial": any(
                            isinstance(record.get("initial"), dict)
                            for record in control_records
                        ),
                        "has_latest": any(
                            isinstance(record.get("latest"), dict)
                            for record in control_records
                        ),
                    }
                )
            except HistoryProbeWAFBlocked:
                raise
            except Exception:
                odds_controls.append(
                    {
                        "titan_id": titan_id,
                        "status": "REQUEST_FAILED",
                        "markets": [],
                        "selected_company_count": 0,
                        "has_initial": False,
                        "has_latest": False,
                    }
                )

        by_date: dict[str, list[HistoricalTarget]] = defaultdict(list)
        for target in target_rows:
            by_date[target.beijing_date].append(target)
        results: list[dict[str, Any]] = []
        for beijing_date in sorted(by_date):
            try:
                schedule_bytes = self._schedule(beijing_date)
                schedule_text = schedule_bytes.decode("utf-8")
                schedule_rows = nowgoal.parse_schedule(
                    nowgoal._extract_data_field(schedule_text)
                )
                schedule_available = bool(schedule_rows)
            except HistoryProbeWAFBlocked:
                raise
            except Exception:
                schedule_rows = []
                schedule_available = False
            for target in by_date[beijing_date]:
                base = target.as_dict() | {
                    "schedule_available": schedule_available,
                    "mapped": False,
                    "titan_id": None,
                    "mapping_confidence": None,
                    "kickoff_diff_seconds": None,
                    "home_away_inverted": None,
                    "selected_company_count": 0,
                    "markets": [],
                    "has_initial": False,
                    "has_latest": False,
                    "odds_semantics": "INITIAL_AND_LATEST_ONLY",
                    "historical_timeline_verified": False,
                    "closing_odds_verified": False,
                    "source_timestamp": None,
                }
                if not schedule_available:
                    results.append(base | {"status": "SCHEDULE_UNAVAILABLE"})
                    continue
                mapping = map_nowgoal_match(target, schedule_rows)
                if mapping is None:
                    results.append(base | {"status": "UNMAPPED"})
                    continue
                mapped = base | {
                    "mapped": True,
                    "titan_id": mapping["titan_id"],
                    "mapping_confidence": mapping["confidence"],
                    "kickoff_diff_seconds": mapping["kickoff_diff_seconds"],
                    "home_away_inverted": mapping["home_away_inverted"],
                    "provider_home_name": mapping["provider_home_name"],
                    "provider_away_name": mapping["provider_away_name"],
                }
                try:
                    odds_bytes = self._odds(mapping["titan_id"])
                    payload = json.loads(odds_bytes)
                    if not isinstance(payload, dict):
                        raise ValueError
                except HistoryProbeWAFBlocked:
                    raise
                except Exception:
                    results.append(mapped | {"status": "ODDS_UNAVAILABLE"})
                    continue
                records = nowgoal.parse_odds(payload)
                markets = sorted(
                    {
                        str(record["market"])
                        for record in records
                        if record.get("market")
                    }
                )
                cids = {
                    str(record["company_id"])
                    for record in records
                    if record.get("company_id")
                }
                has_initial = any(
                    isinstance(record.get("initial"), dict)
                    for record in records
                )
                has_latest = any(
                    isinstance(record.get("latest"), dict)
                    for record in records
                )
                status = (
                    "ODDS_AVAILABLE"
                    if "1x2" in markets
                    else "ODDS_NO_1X2"
                )
                results.append(
                    mapped
                    | {
                        "status": status,
                        "selected_company_count": len(cids),
                        "markets": markets,
                        "has_initial": has_initial,
                        "has_latest": has_latest,
                    }
                )
        counts = {
            "targets": len(results),
            "schedule_available": sum(
                bool(row["schedule_available"]) for row in results
            ),
            "mapped": sum(bool(row["mapped"]) for row in results),
            "odds_available": sum(
                row["status"] == "ODDS_AVAILABLE" for row in results
            ),
            "with_1x2": sum("1x2" in row["markets"] for row in results),
            "with_ah": sum("ah" in row["markets"] for row in results),
            "with_ou": sum("ou" in row["markets"] for row in results),
            "control_dates": len(controls),
            "control_dates_available": sum(
                row["status"] == "AVAILABLE" for row in controls
            ),
            "control_titan_ids": len(odds_controls),
            "control_odds_available": sum(
                row["status"] == "AVAILABLE" for row in odds_controls
            ),
        }
        per_competition: dict[str, dict[str, int]] = {}
        for key in sorted({row["competition"] for row in results}):
            subset = [row for row in results if row["competition"] == key]
            per_competition[key] = {
                "targets": len(subset),
                "mapped": sum(bool(row["mapped"]) for row in subset),
                "odds_available": sum(
                    row["status"] == "ODDS_AVAILABLE" for row in subset
                ),
            }
        p1 = sum(
            value["odds_available"] == 0
            for value in per_competition.values()
        )
        result = {
            "schema_version": 1,
            "mode": self.store.mode,
            "generated_at": _utc_now(),
            "counts": counts,
            "per_competition": per_competition,
            "control_dates": sorted(set(control_dates)),
            "controls": controls,
            "control_titan_ids": sorted(
                set(str(value) for value in control_titan_ids)
            ),
            "odds_controls": odds_controls,
            "results": results,
            "transport_attempts": self.store.attempt_count,
            "residential_proxy_used": False,
            "historical_timeline_verified": False,
            "closing_odds_verified": False,
            "p1": p1,
            "verdict": (
                "SAMPLED_HISTORICAL_INITIAL_LATEST_AVAILABLE"
                if counts["odds_available"] > 0
                else "HISTORICAL_ODDS_AVAILABILITY_NOT_ESTABLISHED"
            ),
        }
        self._write_outputs(result)
        return result

    def _write_outputs(self, result: Mapping[str, Any]) -> None:
        _write_json(self.store.run_dir / "results.json", result)
        rows = list(result.get("results") or [])
        if rows:
            fields = (
                "competition",
                "provider_season",
                "era",
                "fotmob_match_id",
                "home_name",
                "away_name",
                "kickoff_utc",
                "beijing_date",
                "status",
                "schedule_available",
                "mapped",
                "titan_id",
                "mapping_confidence",
                "kickoff_diff_seconds",
                "home_away_inverted",
                "selected_company_count",
                "markets",
                "has_initial",
                "has_latest",
                "odds_semantics",
                "historical_timeline_verified",
                "closing_odds_verified",
            )
            path = self.store.run_dir / "results.csv"
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for row in rows:
                    projected = {field: row.get(field) for field in fields}
                    projected["markets"] = "|".join(row.get("markets") or [])
                    writer.writerow(projected)
            os.chmod(path, 0o600)
        markdown = [
            "# NowGoal sampled historical capability probe",
            "",
            f"- Mode: `{result['mode']}`",
            f"- Targets: {result['counts']['targets']}",
            f"- Transport attempts: {result['transport_attempts']}",
            f"- Residential proxy used: `{result['residential_proxy_used']}`",
            (
                "- Current control dates available: "
                f"{result['counts'].get('control_dates_available', 0)}"
                f"/{result['counts'].get('control_dates', 0)}"
            ),
            (
                "- Current odds controls available: "
                f"{result['counts'].get('control_odds_available', 0)}"
                f"/{result['counts'].get('control_titan_ids', 0)}"
            ),
            (
                "- Historical initial/latest with 1X2: "
                f"{result['counts'].get('odds_available', 0)}"
            ),
            "- Complete historical timeline verified: `False`",
            "- Closing odds verified: `False`",
            f"- Verdict: `{result['verdict']}`",
            "",
            "The result is a three-era sample, not proof of complete historical coverage.",
            "",
        ]
        _write_private(
            self.store.run_dir / "report.md",
            "\n".join(markdown).encode("utf-8"),
        )
        artifact_names = [
            "targets.json",
            "results.json",
            "report.md",
            "request-ledger.jsonl",
        ]
        if (self.store.run_dir / "results.csv").exists():
            artifact_names.append("results.csv")
        manifest = {
            "schema_version": 1,
            "generated_at": _utc_now(),
            "artifacts": {},
        }
        for name in artifact_names:
            data = (self.store.run_dir / name).read_bytes()
            manifest["artifacts"][name] = {
                "sha256": _sha256(data),
                "size": len(data),
            }
        _write_json(self.store.run_dir / "manifest.json", manifest)


class ArchiveHistoryCapabilityProbe:
    """Five-league archive discovery plus timestamped company history."""

    def __init__(
        self,
        store: DurableArtifactStore,
        transport: ProbeTransport | None,
    ) -> None:
        self.store = store
        self.transport = transport

    def _acquire(
        self,
        request_key: str,
        operation: str,
        fetch: Callable[[], bytes],
    ) -> bytes:
        return self.store.acquire(request_key, operation, fetch)

    def run(
        self,
        season: str,
        specs: Iterable[ArchiveLeagueSpec] = TOP5_ARCHIVE_LEAGUES,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"\d{4}-\d{4}", season):
            raise HistoryProbeError("invalid archive season") from None
        selected_specs = list(specs)
        if not selected_specs:
            raise HistoryProbeError("archive competition selection is empty") from None
        if self.store.mode == "dry-run":
            result = {
                "schema_version": 2,
                "mode": "dry-run",
                "season": season,
                "counts": {"competitions": len(selected_specs), "samples": 0},
                "results": [],
                "transport_attempts": self.store.attempt_count,
                "residential_proxy_used": False,
                "verdict": "DRY_RUN_ONLY",
            }
            _write_json(self.store.run_dir / "archive-results.json", result)
            return result

        results: list[dict[str, Any]] = []
        for spec in selected_specs:
            season_key = f"archive.{spec.key}.{season}"
            if self.transport is None:
                season_bytes = self._acquire(season_key, "archive_season", lambda: b"")
            else:
                season_bytes = self._acquire(
                    season_key,
                    "archive_season",
                    lambda spec=spec: self.transport.archive_season(
                        spec.league_id, season
                    ),
                )
            try:
                season_payload = json.loads(season_bytes)
            except json.JSONDecodeError:
                raise HistoryProbeError("archive season response is invalid") from None
            if not isinstance(season_payload, Mapping):
                raise HistoryProbeError("archive season response is invalid") from None
            matches = parse_archive_season(season_payload, spec, season)
            sample = select_archive_sample(matches)
            odds_key = f"odds.{sample.titan_id}"
            if self.transport is None:
                odds_bytes = self._acquire(odds_key, "nowgoal_odds", lambda: b"")
            else:
                odds_bytes = self._acquire(
                    odds_key,
                    "nowgoal_odds",
                    lambda sample=sample: self.transport.odds(sample.titan_id),
                )
            try:
                odds_payload = json.loads(odds_bytes)
            except json.JSONDecodeError:
                raise HistoryProbeError("odds company response is invalid") from None
            if not isinstance(odds_payload, Mapping):
                raise HistoryProbeError("odds company response is invalid") from None
            companies = resolve_archive_companies(
                parse_company_catalog(odds_payload)
            )
            company_results: dict[str, Any] = {}
            for company_key, company in companies.items():
                if company is None:
                    company_results[company_key] = {
                        "status": "COMPANY_UNAVAILABLE",
                        "company_id": None,
                        "company_name": None,
                        "markets": {},
                        "pre_match_rows": 0,
                        "in_play_rows": 0,
                    }
                    continue
                company_id = company["company_id"]
                history_key = f"history.{sample.titan_id}.{company_id}"
                if self.transport is None:
                    history_bytes = self._acquire(
                        history_key, "nowgoal_odds_history", lambda: b""
                    )
                else:
                    history_bytes = self._acquire(
                        history_key,
                        "nowgoal_odds_history",
                        lambda sample=sample, company_id=company_id: (
                            self.transport.odds_history(
                                sample.titan_id, company_id
                            )
                        ),
                    )
                try:
                    history_payload = json.loads(history_bytes)
                except json.JSONDecodeError:
                    raise HistoryProbeError(
                        "historical odds response is invalid"
                    ) from None
                if not isinstance(history_payload, Mapping):
                    raise HistoryProbeError(
                        "historical odds response is invalid"
                    ) from None
                summary = summarize_history(
                    history_payload, sample.kickoff_utc
                )
                company_results[company_key] = {
                    "status": (
                        "PRE_MATCH_HISTORY_AVAILABLE"
                        if summary["pre_match_rows"] > 0
                        else "NO_PRE_MATCH_HISTORY"
                    ),
                    "company_id": company_id,
                    "company_name": company["company_name"],
                    **summary,
                }
            euro_catalog_key = f"euro-catalog.{sample.titan_id}"
            if self.transport is None:
                euro_catalog_bytes = self._acquire(
                    euro_catalog_key, "nowgoal_1x2_catalog", lambda: b""
                )
            else:
                euro_catalog_bytes = self._acquire(
                    euro_catalog_key,
                    "nowgoal_1x2_catalog",
                    lambda sample=sample: self.transport.euro_catalog(
                        sample.titan_id
                    ),
                )
            euro_companies = resolve_euro_companies(
                parse_euro_catalog(euro_catalog_bytes)
            )
            for company_key, euro_company in euro_companies.items():
                company_result = company_results[company_key]
                if euro_company is None:
                    company_result["euro_company_id"] = None
                    company_result["euro_company_name"] = None
                    continue
                euro_company_id = euro_company["company_id"]
                euro_history_key = (
                    f"euro-history.{sample.titan_id}.{euro_company_id}"
                )
                if self.transport is None:
                    euro_history_bytes = self._acquire(
                        euro_history_key,
                        "nowgoal_1x2_history",
                        lambda: b"",
                    )
                else:
                    euro_history_bytes = self._acquire(
                        euro_history_key,
                        "nowgoal_1x2_history",
                        lambda sample=sample, euro_company_id=euro_company_id: (
                            self.transport.euro_history(
                                sample.titan_id, euro_company_id
                            )
                        ),
                    )
                try:
                    euro_history_payload = json.loads(euro_history_bytes)
                except json.JSONDecodeError:
                    raise HistoryProbeError(
                        "1x2 history response is invalid"
                    ) from None
                euro_summary = summarize_euro_history(
                    euro_history_payload, sample.kickoff_utc
                )
                company_result["euro_company_id"] = euro_company_id
                company_result["euro_company_name"] = euro_company["company_name"]
                company_result["markets"]["1x2"] = euro_summary
                company_result["pre_match_rows"] += euro_summary[
                    "pre_match_rows"
                ]
                company_result["in_play_rows"] += euro_summary["in_play_rows"]
                company_result["status"] = (
                    "PRE_MATCH_HISTORY_AVAILABLE"
                    if company_result["pre_match_rows"] > 0
                    else "NO_PRE_MATCH_HISTORY"
                )
            results.append(
                sample.as_dict()
                | {
                    "season_match_count": len(matches),
                    "companies": company_results,
                }
            )

        counts = {
            "competitions": len(selected_specs),
            "samples": len(results),
            "bet365_pre_match_available": sum(
                row["companies"]["bet365"]["status"]
                == "PRE_MATCH_HISTORY_AVAILABLE"
                for row in results
            ),
            "macauslot_pre_match_available": sum(
                row["companies"]["macauslot"]["status"]
                == "PRE_MATCH_HISTORY_AVAILABLE"
                for row in results
            ),
            "pinnacle_pre_match_available": sum(
                row["companies"]["pinnacle"]["status"]
                == "PRE_MATCH_HISTORY_AVAILABLE"
                for row in results
            ),
        }
        core_ok = (
            counts["bet365_pre_match_available"] == len(selected_specs)
            and counts["macauslot_pre_match_available"] == len(selected_specs)
            and counts["pinnacle_pre_match_available"] == len(selected_specs)
        )
        result = {
            "schema_version": 2,
            "mode": self.store.mode,
            "generated_at": _utc_now(),
            "season": season,
            "counts": counts,
            "results": results,
            "transport_attempts": self.store.attempt_count,
            "residential_proxy_used": False,
            "pinnacle_company_identity_verified": (
                counts["pinnacle_pre_match_available"] > 0
            ),
            "verdict": (
                "TOP5_ARCHIVE_SAMPLE_AVAILABLE" if core_ok
                else "TOP5_ARCHIVE_SAMPLE_INCOMPLETE"
            ),
        }
        _write_json(self.store.run_dir / "archive-results.json", result)
        return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--replay", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    parser.add_argument("--source-run", type=Path, default=DEFAULT_SOURCE_RUN)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--competition",
        action="append",
        choices=HISTORICAL_COMPETITION_KEYS,
    )
    parser.add_argument("--control-date", action="append", default=[])
    parser.add_argument("--control-titan-id", action="append", default=[])
    parser.add_argument("--max-attempts", type=int, default=100)
    parser.add_argument(
        "--archive-season",
        help="Use the public league archive path, for example 2020-2021",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    mode = "live" if args.live else "replay" if args.replay else "dry-run"
    store = DurableArtifactStore(
        args.output_root,
        args.run_id,
        mode=mode,
        max_attempts=args.max_attempts,
    )
    transport = DirectNowGoalTransport() if mode == "live" else None
    try:
        if args.archive_season:
            result = ArchiveHistoryCapabilityProbe(store, transport).run(
                args.archive_season
            )
        else:
            targets = load_historical_targets(
                args.source_run,
                args.competition,
            )
            result = HistoryCapabilityProbe(store, transport).run(
                targets,
                args.control_date,
                args.control_titan_id,
            )
    finally:
        if transport is not None:
            transport.close()
    summary = {
        "mode": result["mode"],
        "targets": result["counts"].get(
            "targets", result["counts"].get("samples", 0)
        ),
        "attempts": result["transport_attempts"],
        "p1": result.get("p1", 0),
        "residential_proxy_used": False,
        "verdict": result["verdict"],
    }
    if args.archive_season:
        summary["company_samples_available"] = {
            company: result["counts"][f"{company}_pre_match_available"]
            for company in ("bet365", "macauslot", "pinnacle")
        }
    else:
        summary["odds_available"] = result["counts"].get("odds_available", 0)
    print(json.dumps(summary, sort_keys=True))
    if args.archive_season:
        return 0 if result["verdict"] == "TOP5_ARCHIVE_SAMPLE_AVAILABLE" else 1
    return 0 if result.get("p1", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
