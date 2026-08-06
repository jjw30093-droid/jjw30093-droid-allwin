"""One-time, resumable NowGoal top-five historical odds backfill.

This runner is intentionally separate from production ingestion.  It writes
immutable research artifacts only, keeps pre-match and in-play observations
separated, and reuses the previously validated archive transport, request
ledger, checksum, parser, and company identity helpers.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from analysis.nowgoal_historical_capability_probe.nowgoal_historical_capability_probe import (  # noqa: E402
    ArchiveLeagueSpec,
    ArchiveMatch,
    ArtifactValidationError,
    DirectNowGoalTransport,
    DurableArtifactStore,
    EURO_ARCHIVE_COMPANIES,
    HistoryProbeError,
    HistoryProbeWAFBlocked,
    ProbeTransport,
    RequestBudgetExhausted,
    TOP5_ARCHIVE_LEAGUES,
    _parse_utc,
    _safe_key,
    _sha256,
    _utc_now,
    _write_json,
    _write_private,
    parse_archive_season,
    parse_company_catalog,
    parse_euro_catalog,
    resolve_archive_companies,
    resolve_euro_companies,
)


RUN_ID = "nowgoal-top5-history-backfill-v1"
DEFAULT_OUTPUT_ROOT = _REPO_ROOT / "runtime" / "research" / RUN_ID
DEFAULT_SEASONS = tuple(f"{year}-{year + 1}" for year in range(2020, 2026))
MAX_ATTEMPTS = 60_000
MAX_OUTPUT_BYTES = 20 * 1024 * 1024 * 1024
PRE_MATCH = "PRE_MATCH"
IN_PLAY_EXCLUDED = "IN_PLAY_EXCLUDED"
UNVERIFIED = "UNVERIFIED_OR_UNAVAILABLE"


@dataclass(frozen=True)
class MixCompany:
    key: str
    company_id: str
    company_name: str


@dataclass(frozen=True)
class EuroCompany:
    key: str
    company_id: str
    company_name: str


@dataclass(frozen=True)
class RawArtifact:
    relative_path: str
    sha256: str
    size: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackfillConfig:
    seasons: tuple[str, ...]
    leagues: tuple[ArchiveLeagueSpec, ...]
    sample_per_league_season: int
    verify_company_catalogs: bool
    max_attempts: int
    max_output_bytes: int
    concurrency: int
    min_sleep_seconds: float
    max_sleep_seconds: float
    resume: bool
    preflight_only: bool
    match_allowlist: frozenset[str] | None = None
    match_allowlist_rows: tuple[dict[str, Any], ...] | None = None
    skip_archive_discovery: bool = False
    proxy_used: bool = False
    retry_backoff_seconds: float = 0.0


MIX_COMPANIES = (
    MixCompany("bet365", "8", "Bet365"),
    MixCompany("macauslot", "1", "Macauslot"),
)
EURO_COMPANIES = tuple(
    EuroCompany(key, str(company_id), aliases[0])
    for key, company_id, aliases in EURO_ARCHIVE_COMPANIES
)
UNVERIFIED_MARKETS = (
    {
        "company_key": "pinnacle",
        "company_id": None,
        "company_name": "Pinnacle",
        "market": "ah",
        "status": UNVERIFIED,
        "reason": "Pinnacle AH history company id is unverified for archive mix-history endpoint",
    },
    {
        "company_key": "pinnacle",
        "company_id": None,
        "company_name": "Pinnacle",
        "market": "ou",
        "status": UNVERIFIED,
        "reason": "Pinnacle OU history company id is unverified for archive mix-history endpoint",
    },
)


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _append_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    materialized = list(rows)
    if not materialized:
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        for row in materialized:
            os.write(fd, _json_bytes(row))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise ArtifactValidationError("progress artifact is invalid") from None
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            raise ArtifactValidationError("jsonl artifact is invalid") from None
        if not isinstance(value, dict):
            raise ArtifactValidationError("jsonl artifact is invalid") from None
        rows.append(value)
    return rows


def _parse_json_payload(data: bytes, message: str) -> Any:
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        raise HistoryProbeError(message) from None


def _phase(observed_at: str, kickoff_utc: str) -> str:
    return PRE_MATCH if _parse_utc(observed_at) < _parse_utc(kickoff_utc) else IN_PLAY_EXCLUDED


def _epoch_to_utc(timestamp: int) -> str:
    return (
        datetime.fromtimestamp(timestamp, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _safe_failure(error: BaseException) -> str:
    if isinstance(error, HistoryProbeWAFBlocked):
        return "NowGoal direct request was blocked"
    if isinstance(error, RequestBudgetExhausted):
        return "request budget exhausted"
    if isinstance(error, ArtifactValidationError):
        return str(error)
    return str(error) or "NowGoal direct request failed"


def _match_key(match: ArchiveMatch) -> str:
    return ".".join(
        (
            _safe_key(match.season),
            _safe_key(match.competition),
            _safe_key(match.titan_id),
        )
    )


def _request_key(*parts: str) -> str:
    return ".".join(_safe_key(str(part)) for part in parts)


def _select_matches(
    matches: Iterable[ArchiveMatch],
    sample_per_league_season: int,
) -> list[ArchiveMatch]:
    rows = sorted(matches, key=lambda row: (row.kickoff_utc, row.titan_id))
    if sample_per_league_season <= 0 or sample_per_league_season >= len(rows):
        return rows
    if sample_per_league_season == 1:
        return [rows[len(rows) // 2]]
    selected_indexes = {
        round(index * (len(rows) - 1) / (sample_per_league_season - 1))
        for index in range(sample_per_league_season)
    }
    return [rows[index] for index in sorted(selected_indexes)]


def load_match_allowlist(path: Path) -> frozenset[str]:
    """gap-manifest 分片清单(JSONL,每行至少含 "titan_id")。空文件或一个合法
    titan_id 都没有 → 立即报错——不产出一个"跑了但什么都没做"的伪成功分片。"""
    titan_ids: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            raise HistoryProbeError(
                f"match allowlist line {line_number} is not valid JSON"
            ) from None
        if not isinstance(row, Mapping) or not row.get("titan_id"):
            raise HistoryProbeError(
                f"match allowlist line {line_number} is missing titan_id"
            ) from None
        titan_ids.add(str(row["titan_id"]))
    if not titan_ids:
        raise HistoryProbeError("match allowlist is empty") from None
    return frozenset(titan_ids)


def _select_by_allowlist(
    matches: Iterable[ArchiveMatch],
    allowlist: frozenset[str],
) -> list[ArchiveMatch]:
    rows = sorted(matches, key=lambda row: (row.kickoff_utc, row.titan_id))
    selected = [row for row in rows if row.titan_id in allowlist]
    if len(selected) != len(allowlist):
        found = {row.titan_id for row in selected}
        missing = sorted(allowlist - found)
        raise HistoryProbeError(
            f"match allowlist count mismatch: {len(allowlist)} requested, "
            f"{len(selected)} found in this season/league scope "
            f"(missing e.g. {missing[:5]})"
        ) from None
    return selected


def load_match_allowlist_rows(path: Path) -> tuple[dict[str, Any], ...]:
    """``--skip-archive-discovery`` 专用:清单本身要带足够字段直接构造
    ``ArchiveMatch``(titan_id / home_team_name_en / away_team_name_en /
    nowgoal_kickoff_local),不再依赖重新抓一次赛季目录去发现这些字段。

    与 ``load_match_allowlist`` 是两个独立入口:后者只要 titan_id、用于"先抓目录
    再按清单过滤"的既有路径;这里要的是"清单本身就是全部真相"，任何一行缺字段、
    或者出现重复 titan_id，都必须在发出任何请求之前硬失败——不能让爬虫带着不完整
    或有歧义的清单跑到一半才出错。
    """
    rows: list[dict[str, Any]] = []
    seen_titans: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            raise HistoryProbeError(
                f"match allowlist line {line_number} is not valid JSON"
            ) from None
        if not isinstance(row, Mapping) or not row.get("titan_id"):
            raise HistoryProbeError(
                f"match allowlist line {line_number} is missing titan_id"
            ) from None
        if not row.get("nowgoal_kickoff_local"):
            raise HistoryProbeError(
                f"match allowlist line {line_number} is missing nowgoal_kickoff_local "
                "(required by --skip-archive-discovery to build a precise kickoff_utc "
                "without re-fetching the archive season catalog)"
            ) from None
        titan_id = str(row["titan_id"])
        if titan_id in seen_titans:
            raise HistoryProbeError(
                f"match allowlist line {line_number} repeats titan_id {titan_id!r}"
            ) from None
        seen_titans.add(titan_id)
        rows.append(dict(row))
    if not rows:
        raise HistoryProbeError("match allowlist is empty") from None
    return tuple(rows)


def _beijing_local_to_utc(beijing_local: str) -> str:
    """NowGoal 侧的开球时刻是北京时("YYYY-MM-DD HH:MM"，无夏令时)。转成 UTC ISO
    字符串给 ``ArchiveMatch.kickoff_utc`` 用——绝不能拿 FotMob 的自然日代替，
    那没有精确到时分，会让 pre-match/in-play 切分算错(CLAUDE.md §6.2.1)。"""
    try:
        naive = datetime.strptime(beijing_local.strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        raise HistoryProbeError(
            f"invalid nowgoal_kickoff_local: {beijing_local!r}"
        ) from None
    beijing = naive.replace(tzinfo=timezone(timedelta(hours=8)))
    return beijing.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _matches_from_allowlist_rows(
    rows: tuple[dict[str, Any], ...],
    spec: ArchiveLeagueSpec,
    season: str,
) -> list[ArchiveMatch]:
    matches = [
        ArchiveMatch(
            competition=spec.key,
            league_id=spec.league_id,
            season=season,
            titan_id=str(row["titan_id"]),
            home_name=str(row.get("home_team_name_en") or ""),
            away_name=str(row.get("away_team_name_en") or ""),
            kickoff_utc=_beijing_local_to_utc(str(row["nowgoal_kickoff_local"])),
        )
        for row in rows
    ]
    matches.sort(key=lambda m: (m.kickoff_utc, m.titan_id))
    return matches


def _structured_raw_path(
    output_root: Path,
    season: str,
    league_key: str,
    titan_id: str,
    endpoint: str,
    data: bytes,
) -> RawArtifact:
    digest = _sha256(data)
    path = (
        output_root
        / "raw"
        / _safe_key(season)
        / _safe_key(league_key)
        / _safe_key(titan_id)
        / f"{_safe_key(endpoint)}.{digest}.raw"
    )
    if path.exists():
        if path.read_bytes() != data:
            raise ArtifactValidationError("structured raw artifact conflict") from None
    else:
        _write_private(path, data)
    return RawArtifact(
        relative_path=str(path.relative_to(output_root)),
        sha256=digest,
        size=len(data),
    )


def _extract_mix_market_rows(
    payload: Mapping[str, Any],
    *,
    market: str,
    match: ArchiveMatch,
    company: MixCompany,
    artifact: RawArtifact,
    proxy_used: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if payload.get("ErrCode") != 0 or not isinstance(payload.get("Data"), Mapping):
        raise HistoryProbeError("historical odds response is invalid") from None
    provider_key = {"ah": "ah", "ou": "ou"}[market]
    rows = payload["Data"].get(provider_key)
    if not isinstance(rows, list):
        raise HistoryProbeError("historical odds response is invalid") from None
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise HistoryProbeError("historical odds response is invalid") from None
        timestamp = row.get("mt")
        if not isinstance(timestamp, int) or isinstance(timestamp, bool):
            raise HistoryProbeError("historical odds timestamp is invalid") from None
        observed_at = _epoch_to_utc(timestamp)
        normalized.append(
            {
                "schema_version": 1,
                "season": match.season,
                "league_key": match.competition,
                "league_id": match.league_id,
                "titan_id": match.titan_id,
                "home_name": match.home_name,
                "away_name": match.away_name,
                "kickoff_utc": match.kickoff_utc,
                "company_key": company.key,
                "company_id": company.company_id,
                "company_name": company.company_name,
                "market": market,
                "source_endpoint": "nowgoal_mix_history",
                "observed_at": observed_at,
                "phase": _phase(observed_at, match.kickoff_utc),
                "source_row_index": index,
                "values": {str(key): value for key, value in row.items() if key != "mt"},
                "raw_artifact": artifact.as_dict(),
                "residential_proxy_used": proxy_used,
            }
        )
    pre_match_rows = [
        row for row in normalized if row["phase"] == PRE_MATCH
    ]
    final = (
        max(
            pre_match_rows,
            key=lambda row: (row["observed_at"], int(row["source_row_index"])),
        )
        if pre_match_rows
        else None
    )
    return normalized, final


def _parse_euro_observed(value: Any) -> str:
    if not isinstance(value, str):
        raise HistoryProbeError("1x2 history response is invalid") from None
    try:
        parsed = datetime.strptime(value, "%Y,%m,%d,%H,%M,%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        raise HistoryProbeError("1x2 history response is invalid") from None
    return parsed.isoformat().replace("+00:00", "Z")


def _extract_euro_rows(
    payload: Any,
    *,
    match: ArchiveMatch,
    company: EuroCompany,
    artifact: RawArtifact,
    proxy_used: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if not isinstance(payload, list):
        raise HistoryProbeError("1x2 history response is invalid") from None
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(payload):
        if not isinstance(row, Mapping):
            raise HistoryProbeError("1x2 history response is invalid") from None
        values: dict[str, str] = {}
        for source_key, target_key in (
            ("HomeWin", "home_win"),
            ("Standoff", "draw"),
            ("GuestWin", "away_win"),
        ):
            try:
                float(row[source_key])
            except (KeyError, TypeError, ValueError):
                raise HistoryProbeError("1x2 history response is invalid") from None
            values[target_key] = str(row[source_key])
        observed_at = _parse_euro_observed(row.get("TimeShow"))
        normalized.append(
            {
                "schema_version": 1,
                "season": match.season,
                "league_key": match.competition,
                "league_id": match.league_id,
                "titan_id": match.titan_id,
                "home_name": match.home_name,
                "away_name": match.away_name,
                "kickoff_utc": match.kickoff_utc,
                "company_key": company.key,
                "company_id": company.company_id,
                "company_name": company.company_name,
                "market": "1x2",
                "source_endpoint": "nowgoal_1x2_history",
                "observed_at": observed_at,
                "phase": _phase(observed_at, match.kickoff_utc),
                "source_row_index": index,
                "values": values,
                "raw_artifact": artifact.as_dict(),
                "residential_proxy_used": proxy_used,
            }
        )
    pre_match_rows = [
        row for row in normalized if row["phase"] == PRE_MATCH
    ]
    final = (
        max(
            pre_match_rows,
            key=lambda row: (row["observed_at"], int(row["source_row_index"])),
        )
        if pre_match_rows
        else None
    )
    return normalized, final


def _availability_from_rows(
    *,
    match: ArchiveMatch,
    company_key: str,
    company_id: str | None,
    company_name: str,
    market: str,
    proxy_used: bool = False,
    rows: list[dict[str, Any]],
    final: dict[str, Any] | None,
    artifact: RawArtifact | None,
    status: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    if status is None:
        pre_match_rows = sum(row["phase"] == PRE_MATCH for row in rows)
        status = (
            "EMPTY"
            if not rows
            else "PRE_MATCH_HISTORY_AVAILABLE"
            if pre_match_rows > 0
            else "NO_PRE_MATCH_HISTORY"
        )
    return {
        "schema_version": 1,
        "season": match.season,
        "league_key": match.competition,
        "league_id": match.league_id,
        "titan_id": match.titan_id,
        "home_name": match.home_name,
        "away_name": match.away_name,
        "kickoff_utc": match.kickoff_utc,
        "company_key": company_key,
        "company_id": company_id,
        "company_name": company_name,
        "market": market,
        "status": status,
        "reason": reason,
        "rows": len(rows),
        "pre_match_rows": sum(row["phase"] == PRE_MATCH for row in rows),
        "in_play_excluded_rows": sum(
            row["phase"] == IN_PLAY_EXCLUDED for row in rows
        ),
        "first_observed_at": min((row["observed_at"] for row in rows), default=None),
        "last_observed_at": max((row["observed_at"] for row in rows), default=None),
        "final_pre_match_observed_at": (
            final["observed_at"] if final is not None else None
        ),
        "raw_artifact": artifact.as_dict() if artifact is not None else None,
        "residential_proxy_used": proxy_used,
    }


class NowGoalTop5HistoryBackfill:
    def __init__(
        self,
        *,
        output_root: Path,
        store: DurableArtifactStore,
        transport: ProbeTransport | None,
        config: BackfillConfig,
    ) -> None:
        self.output_root = output_root
        self.store = store
        self.transport = transport
        self.config = config
        self.progress_path = output_root / "progress.json"
        self.availability_path = output_root / "availability.jsonl"
        self.failures_path = output_root / "failures.jsonl"
        self.normalized_path = output_root / "normalized" / "odds-history.jsonl"
        self.final_path = output_root / "normalized" / "final-pre-match.jsonl"

    def _progress(self) -> dict[str, Any]:
        value = _read_json(
            self.progress_path,
            {
                "schema_version": 1,
                "completed_match_keys": [],
                "started_at": _utc_now(),
                "updated_at": None,
                "counts": {},
            },
        )
        if not isinstance(value, dict) or not isinstance(
            value.get("completed_match_keys"), list
        ):
            raise ArtifactValidationError("progress artifact is invalid") from None
        return value

    def _write_progress(self, progress: Mapping[str, Any]) -> None:
        _write_json(self.progress_path, dict(progress) | {"updated_at": _utc_now()})

    def _sleep_before_live_fetch(self) -> None:
        if self.store.mode == "live" and self.transport is not None:
            time.sleep(
                random.uniform(
                    self.config.min_sleep_seconds,
                    self.config.max_sleep_seconds,
                )
            )

    def _acquire(
        self,
        request_key: str,
        operation: str,
        fetch: Callable[[], bytes],
    ) -> bytes:
        def delayed_fetch() -> bytes:
            self._sleep_before_live_fetch()
            return fetch()

        return self.store.acquire(
            request_key,
            operation,
            delayed_fetch,
            retry_backoff_seconds=self.config.retry_backoff_seconds,
        )

    def _fetch_archive_season(
        self,
        spec: ArchiveLeagueSpec,
        season: str,
    ) -> tuple[list[ArchiveMatch], RawArtifact]:
        request_key = _request_key("archive", season, spec.key)
        if self.transport is None:
            data = self._acquire(request_key, "archive_season", lambda: b"")
        else:
            data = self._acquire(
                request_key,
                "archive_season",
                lambda spec=spec, season=season: self.transport.archive_season(
                    spec.league_id, season
                ),
            )
        payload = _parse_json_payload(data, "archive season response is invalid")
        if not isinstance(payload, Mapping):
            raise HistoryProbeError("archive season response is invalid") from None
        matches = parse_archive_season(payload, spec, season)
        artifact = _structured_raw_path(
            self.output_root,
            season,
            spec.key,
            "season",
            "season-catalog",
            data,
        )
        return matches, artifact

    def _verify_companies(self, match: ArchiveMatch) -> None:
        if self.transport is None:
            odds_bytes = self._acquire(
                _request_key("odds", match.season, match.competition, match.titan_id),
                "nowgoal_odds",
                lambda: b"",
            )
            euro_bytes = self._acquire(
                _request_key(
                    "euro-catalog", match.season, match.competition, match.titan_id
                ),
                "nowgoal_1x2_catalog",
                lambda: b"",
            )
        else:
            odds_bytes = self._acquire(
                _request_key("odds", match.season, match.competition, match.titan_id),
                "nowgoal_odds",
                lambda match=match: self.transport.odds(match.titan_id),
            )
            euro_bytes = self._acquire(
                _request_key(
                    "euro-catalog", match.season, match.competition, match.titan_id
                ),
                "nowgoal_1x2_catalog",
                lambda match=match: self.transport.euro_catalog(match.titan_id),
            )
        odds_payload = _parse_json_payload(odds_bytes, "odds company response is invalid")
        if not isinstance(odds_payload, Mapping):
            raise HistoryProbeError("odds company response is invalid") from None
        mix = resolve_archive_companies(parse_company_catalog(odds_payload))
        expected_mix = {"bet365": "8", "macauslot": "1"}
        for key, expected_id in expected_mix.items():
            selected = mix.get(key)
            if selected is None or str(selected.get("company_id")) != expected_id:
                raise HistoryProbeError("company identity mismatch") from None
        euro = resolve_euro_companies(parse_euro_catalog(euro_bytes))
        expected_euro = {"bet365": "281", "macauslot": "80", "pinnacle": "177"}
        for key, expected_id in expected_euro.items():
            selected = euro.get(key)
            if selected is None or str(selected.get("company_id")) != expected_id:
                raise HistoryProbeError("company identity mismatch") from None

    def _fetch_mix_history(
        self,
        match: ArchiveMatch,
        company: MixCompany,
    ) -> tuple[Mapping[str, Any], RawArtifact]:
        request_key = _request_key(
            "mix-history",
            match.season,
            match.competition,
            match.titan_id,
            company.company_id,
        )
        if self.transport is None:
            data = self._acquire(request_key, "nowgoal_odds_history", lambda: b"")
        else:
            data = self._acquire(
                request_key,
                "nowgoal_odds_history",
                lambda match=match, company=company: self.transport.odds_history(
                    match.titan_id, company.company_id
                ),
            )
        payload = _parse_json_payload(data, "historical odds response is invalid")
        if not isinstance(payload, Mapping):
            raise HistoryProbeError("historical odds response is invalid") from None
        artifact = _structured_raw_path(
            self.output_root,
            match.season,
            match.competition,
            match.titan_id,
            f"mix-history-{company.key}-{company.company_id}",
            data,
        )
        return payload, artifact

    def _fetch_euro_history(
        self,
        match: ArchiveMatch,
        company: EuroCompany,
    ) -> tuple[Any, RawArtifact]:
        request_key = _request_key(
            "euro-history",
            match.season,
            match.competition,
            match.titan_id,
            company.company_id,
        )
        if self.transport is None:
            data = self._acquire(request_key, "nowgoal_1x2_history", lambda: b"")
        else:
            data = self._acquire(
                request_key,
                "nowgoal_1x2_history",
                lambda match=match, company=company: self.transport.euro_history(
                    match.titan_id, company.company_id
                ),
            )
        payload = _parse_json_payload(data, "1x2 history response is invalid")
        artifact = _structured_raw_path(
            self.output_root,
            match.season,
            match.competition,
            match.titan_id,
            f"euro-history-{company.key}-{company.company_id}",
            data,
        )
        return payload, artifact

    def _write_preflight(
        self,
        *,
        catalog_count: int,
        selected_match_count: int,
        full_match_count: int,
    ) -> dict[str, Any]:
        per_match_requests = 5 + (2 if self.config.verify_company_catalogs else 0)
        estimated_requests = catalog_count + selected_match_count * per_match_requests
        ledger_sizes = [
            row.get("size")
            for row in getattr(self.store, "_rows", [])
            if row.get("phase") == "SUCCEEDED"
            and isinstance(row.get("size"), int)
            and row.get("size") > 0
        ]
        average_bytes = (
            int(sum(ledger_sizes) / len(ledger_sizes)) if ledger_sizes else 32_768
        )
        estimated_bytes = estimated_requests * average_bytes
        preflight = {
            "schema_version": 1,
            "generated_at": _utc_now(),
            "seasons": list(self.config.seasons),
            "leagues": [spec.key for spec in self.config.leagues],
            "catalog_requests": catalog_count,
            "selected_match_count": selected_match_count,
            "full_match_count": full_match_count,
            "per_match_history_requests": 5,
            "per_match_catalog_verification_requests": (
                2 if self.config.verify_company_catalogs else 0
            ),
            "estimated_requests": estimated_requests,
            "max_attempts": self.config.max_attempts,
            "average_success_response_bytes": average_bytes,
            "estimated_direct_download_bytes": estimated_bytes,
            "max_output_bytes": self.config.max_output_bytes,
            "residential_proxy_used": self.config.proxy_used,
            "concurrency": self.config.concurrency,
            "request_sleep_seconds": [
                self.config.min_sleep_seconds,
                self.config.max_sleep_seconds,
            ],
        }
        _write_json(self.output_root / "preflight-estimate.json", preflight)
        if estimated_requests > self.config.max_attempts:
            raise RequestBudgetExhausted("request budget exhausted") from None
        if estimated_bytes > self.config.max_output_bytes:
            raise RequestBudgetExhausted("output budget exhausted") from None
        return preflight

    def _process_match(self, match: ArchiveMatch) -> None:
        normalized_rows: list[dict[str, Any]] = []
        final_rows: list[dict[str, Any]] = []
        availability_rows: list[dict[str, Any]] = []
        failure_rows: list[dict[str, Any]] = []

        if self.config.verify_company_catalogs:
            self._verify_companies(match)

        for company in MIX_COMPANIES:
            try:
                payload, artifact = self._fetch_mix_history(match, company)
                for market in ("ah", "ou"):
                    rows, final = _extract_mix_market_rows(
                        payload,
                        market=market,
                        match=match,
                        company=company,
                        artifact=artifact,
                        proxy_used=self.config.proxy_used,
                    )
                    normalized_rows.extend(rows)
                    if final is not None:
                        final_rows.append(final)
                    availability_rows.append(
                        _availability_from_rows(
                            match=match,
                            company_key=company.key,
                            company_id=company.company_id,
                            company_name=company.company_name,
                            market=market,
                            rows=rows,
                            final=final,
                            artifact=artifact,
                            proxy_used=self.config.proxy_used,
                        )
                    )
            except HistoryProbeWAFBlocked:
                raise
            except Exception as error:
                failure = self._failure_row(
                    match,
                    company_key=company.key,
                    company_id=company.company_id,
                    company_name=company.company_name,
                    market="ah_ou",
                    error=error,
                )
                failure_rows.append(failure)
                for market in ("ah", "ou"):
                    availability_rows.append(
                        _availability_from_rows(
                            match=match,
                            company_key=company.key,
                            company_id=company.company_id,
                            company_name=company.company_name,
                            market=market,
                            rows=[],
                            final=None,
                            artifact=None,
                            status="REQUEST_FAILED",
                            reason=failure["error"],
                            proxy_used=self.config.proxy_used,
                        )
                    )

        for company in EURO_COMPANIES:
            try:
                payload, artifact = self._fetch_euro_history(match, company)
                rows, final = _extract_euro_rows(
                    payload,
                    match=match,
                    company=company,
                    artifact=artifact,
                    proxy_used=self.config.proxy_used,
                )
                normalized_rows.extend(rows)
                if final is not None:
                    final_rows.append(final)
                availability_rows.append(
                    _availability_from_rows(
                        match=match,
                        company_key=company.key,
                        company_id=company.company_id,
                        company_name=company.company_name,
                        market="1x2",
                        rows=rows,
                        final=final,
                        artifact=artifact,
                        proxy_used=self.config.proxy_used,
                    )
                )
            except HistoryProbeWAFBlocked:
                raise
            except Exception as error:
                failure = self._failure_row(
                    match,
                    company_key=company.key,
                    company_id=company.company_id,
                    company_name=company.company_name,
                    market="1x2",
                    error=error,
                )
                failure_rows.append(failure)
                availability_rows.append(
                    _availability_from_rows(
                        match=match,
                        company_key=company.key,
                        company_id=company.company_id,
                        company_name=company.company_name,
                        market="1x2",
                        rows=[],
                        final=None,
                        artifact=None,
                        status="REQUEST_FAILED",
                        reason=failure["error"],
                        proxy_used=self.config.proxy_used,
                    )
                )

        for row in UNVERIFIED_MARKETS:
            availability_rows.append(
                _availability_from_rows(
                    match=match,
                    company_key=str(row["company_key"]),
                    company_id=None,
                    company_name=str(row["company_name"]),
                    market=str(row["market"]),
                    rows=[],
                    final=None,
                    artifact=None,
                    status=str(row["status"]),
                    reason=str(row["reason"]),
                    proxy_used=self.config.proxy_used,
                )
            )

        _append_jsonl(self.normalized_path, normalized_rows)
        _append_jsonl(self.final_path, final_rows)
        _append_jsonl(self.availability_path, availability_rows)
        _append_jsonl(self.failures_path, failure_rows)

    def _failure_row(
        self,
        match: ArchiveMatch,
        *,
        company_key: str,
        company_id: str | None,
        company_name: str,
        market: str,
        error: BaseException,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "at": _utc_now(),
            "season": match.season,
            "league_key": match.competition,
            "league_id": match.league_id,
            "titan_id": match.titan_id,
            "company_key": company_key,
            "company_id": company_id,
            "company_name": company_name,
            "market": market,
            "error": _safe_failure(error),
            "diagnostic": getattr(error, "diagnostic", None),
            "residential_proxy_used": self.config.proxy_used,
        }

    def _write_match_manifest(self, matches: Iterable[ArchiveMatch]) -> None:
        rows = [match.as_dict() | {"schema_version": 1} for match in matches]
        path = self.output_root / "match-manifest.jsonl"
        _write_private(path, b"")
        _append_jsonl(path, rows)

    def _write_coverage(self) -> dict[str, Any]:
        availability = _read_jsonl(self.availability_path)
        by_league_season: dict[str, Counter[str]] = defaultdict(Counter)
        by_company_market: dict[str, Counter[str]] = defaultdict(Counter)
        for row in availability:
            league_key = f"{row.get('season')}::{row.get('league_key')}"
            market_key = (
                f"{row.get('company_key')}::{row.get('company_id')}::"
                f"{row.get('market')}"
            )
            status = str(row.get("status") or "UNKNOWN")
            by_league_season[league_key][status] += 1
            by_company_market[market_key][status] += 1
        league_payload = {
            key: dict(counter) for key, counter in sorted(by_league_season.items())
        }
        company_payload = {
            key: dict(counter) for key, counter in sorted(by_company_market.items())
        }
        _write_json(
            self.output_root / "normalized" / "coverage-by-league-season.json",
            league_payload,
        )
        _write_json(
            self.output_root / "normalized" / "coverage-by-company-market.json",
            company_payload,
        )
        return {
            "league_season": league_payload,
            "company_market": company_payload,
        }

    def run(self) -> dict[str, Any]:
        if self.config.concurrency not in (1, 2):
            raise HistoryProbeError("invalid concurrency") from None
        if self.config.concurrency != 1:
            raise HistoryProbeError("concurrency greater than one is not enabled") from None
        if self.config.min_sleep_seconds < 0 or (
            self.config.max_sleep_seconds < self.config.min_sleep_seconds
        ):
            raise HistoryProbeError("invalid request sleep") from None
        if self.config.match_allowlist is not None:
            # 一个分片 = 一个联赛 x 一个赛季(见 gap-manifest/<league>-<season>.jsonl 的
            # 目录约定);清单要跟"这一个分片的全部候选"精确对上,多联赛/多赛季会让
            # "长度不符"这条硬失败规则失去意义(缺口天然来自多个分片,合并跑会永远对不上)。
            if len(self.config.seasons) != 1 or len(self.config.leagues) != 1:
                raise HistoryProbeError(
                    "match allowlist requires exactly one --seasons and one --league"
                ) from None
        if self.config.skip_archive_discovery:
            if self.config.match_allowlist_rows is None:
                raise HistoryProbeError(
                    "--skip-archive-discovery requires --match-allowlist"
                ) from None
            if self.config.verify_company_catalogs:
                # _verify_companies() 会打 nowgoal.ODDS_URL(www. 域名,type=14&t=1)
                # 和 1x2.nowgoal26.com(euro_catalog)——跳过发现正是因为 archive
                # 目录所在的域名连不上,这两个域名同样靠不住,组合起来大概率会在
                # 校验这一步就失败,不如现在就说清楚而不是让它跑到一半才炸。
                raise HistoryProbeError(
                    "--skip-archive-discovery cannot be combined with "
                    "--verify-company-catalogs (both hit endpoints this mode "
                    "was specifically built to avoid)"
                ) from None
        self.output_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.output_root, 0o700)
        if self.store.mode == "dry-run":
            preflight = {
                "schema_version": 1,
                "generated_at": _utc_now(),
                "seasons": list(self.config.seasons),
                "leagues": [spec.key for spec in self.config.leagues],
                "catalog_requests": len(self.config.seasons)
                * len(self.config.leagues),
                "selected_match_count": 0,
                "full_match_count": 0,
                "estimated_requests": 0,
                "max_attempts": self.config.max_attempts,
                "estimated_direct_download_bytes": 0,
                "max_output_bytes": self.config.max_output_bytes,
                "residential_proxy_used": self.config.proxy_used,
                "concurrency": self.config.concurrency,
                "request_sleep_seconds": [
                    self.config.min_sleep_seconds,
                    self.config.max_sleep_seconds,
                ],
            }
            _write_json(self.output_root / "preflight-estimate.json", preflight)
            summary = self._run_summary(
                selected_matches=[],
                preflight=preflight,
                verdict="DRY_RUN_ONLY",
            )
            _write_json(self.output_root / "run-summary.json", summary)
            return summary

        selected_matches: list[ArchiveMatch] = []
        full_match_count = 0
        season_manifest: list[dict[str, Any]] = []
        for season in self.config.seasons:
            if not re.fullmatch(r"\d{4}-\d{4}", season):
                raise HistoryProbeError("invalid archive season") from None
            for spec in self.config.leagues:
                if self.config.skip_archive_discovery:
                    # 清单本身就是全部真相:不发 archive_season 请求(那个域名连不上
                    # 才需要这条路径),直接从清单的 titan_id/队名/北京时间开球时刻
                    # 构造 ArchiveMatch。清单里有多少行,选中的就是多少行——没有
                    # "先抓目录再过滤"那一步,自然也没有数量对不上的可能。
                    matches = _matches_from_allowlist_rows(
                        self.config.match_allowlist_rows, spec, season
                    )
                    full_match_count += len(matches)
                    selected = matches
                    artifact_dict = None
                else:
                    matches, artifact = self._fetch_archive_season(spec, season)
                    full_match_count += len(matches)
                    if self.config.match_allowlist is not None:
                        selected = _select_by_allowlist(matches, self.config.match_allowlist)
                    else:
                        selected = _select_matches(
                            matches, self.config.sample_per_league_season
                        )
                    artifact_dict = artifact.as_dict()
                selected_matches.extend(selected)
                season_manifest.append(
                    {
                        "schema_version": 1,
                        "season": season,
                        "league_key": spec.key,
                        "league_id": spec.league_id,
                        "expected_name": spec.expected_name,
                        "full_match_count": len(matches),
                        "selected_match_count": len(selected),
                        "raw_artifact": artifact_dict,
                        "discovery_skipped": self.config.skip_archive_discovery,
                    }
                )
        _write_json(
            self.output_root / "season-manifest.json",
            {
                "schema_version": 1,
                "generated_at": _utc_now(),
                "seasons": season_manifest,
            },
        )
        self._write_match_manifest(selected_matches)
        preflight = self._write_preflight(
            catalog_count=0 if self.config.skip_archive_discovery else (
                len(self.config.seasons) * len(self.config.leagues)
            ),
            selected_match_count=len(selected_matches),
            full_match_count=full_match_count,
        )
        if self.config.preflight_only or self.store.mode == "dry-run":
            summary = self._run_summary(
                selected_matches=selected_matches,
                preflight=preflight,
                verdict="PREFLIGHT_ONLY",
            )
            _write_json(self.output_root / "run-summary.json", summary)
            return summary

        progress = self._progress()
        completed = set(str(key) for key in progress["completed_match_keys"])
        if completed and not self.config.resume:
            raise ArtifactValidationError("resume flag is required") from None
        total = len(selected_matches)
        for index, match in enumerate(selected_matches, start=1):
            key = _match_key(match)
            if key in completed:
                print(
                    json.dumps(
                        {
                            "event": "match_skipped",
                            "index": index,
                            "total": total,
                            "match_key": key,
                            "attempts": self.store.attempt_count,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                continue
            self._process_match(match)
            completed.add(key)
            progress["completed_match_keys"] = sorted(completed)
            progress["counts"] = {
                "completed_matches": len(completed),
                "selected_matches": total,
                "transport_attempts": self.store.attempt_count,
            }
            self._write_progress(progress)
            print(
                json.dumps(
                    {
                        "event": "match_completed",
                        "index": index,
                        "total": total,
                        "match_key": key,
                        "attempts": self.store.attempt_count,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        verdict = (
            "TOP5_HISTORICAL_ODDS_ARTIFACT_BACKFILL_COMPLETE"
            if len(completed) >= len(selected_matches)
            else "TOP5_HISTORICAL_ODDS_ARTIFACT_BACKFILL_PARTIAL"
        )
        coverage = self._write_coverage()
        summary = self._run_summary(
            selected_matches=selected_matches,
            preflight=preflight,
            verdict=verdict,
            coverage=coverage,
        )
        _write_json(self.output_root / "run-summary.json", summary)
        return summary

    def _run_summary(
        self,
        *,
        selected_matches: Iterable[ArchiveMatch],
        preflight: Mapping[str, Any],
        verdict: str,
        coverage: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        availability = _read_jsonl(self.availability_path)
        failures = _read_jsonl(self.failures_path)
        normalized_count = len(_read_jsonl(self.normalized_path))
        final_count = len(_read_jsonl(self.final_path))
        progress = self._progress()
        selected_rows = list(selected_matches)
        statuses = Counter(str(row.get("status") or "UNKNOWN") for row in availability)
        return {
            "schema_version": 1,
            "generated_at": _utc_now(),
            "mode": self.store.mode,
            "run_id": self.output_root.name,
            "selected_match_count": len(selected_rows),
            "completed_match_count": len(progress.get("completed_match_keys") or []),
            "availability_rows": len(availability),
            "availability_status_counts": dict(sorted(statuses.items())),
            "failure_rows": len(failures),
            "normalized_history_rows": normalized_count,
            "final_pre_match_rows": final_count,
            "transport_attempts": self.store.attempt_count,
            "preflight": dict(preflight),
            "coverage": dict(coverage or {}),
            "residential_proxy_used": self.config.proxy_used,
            "production_database_written": False,
            "pinnacle_ah_ou_policy": UNVERIFIED,
            "verdict": verdict,
        }


def _parse_seasons(value: str | None) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_SEASONS
    seasons = tuple(part.strip() for part in value.split(",") if part.strip())
    if not seasons or any(not re.fullmatch(r"\d{4}-\d{4}", season) for season in seasons):
        raise HistoryProbeError("invalid archive season") from None
    return seasons


def _parse_leagues(values: list[str] | None) -> tuple[ArchiveLeagueSpec, ...]:
    if not values:
        return TOP5_ARCHIVE_LEAGUES
    by_key = {spec.key: spec for spec in TOP5_ARCHIVE_LEAGUES}
    selected: list[ArchiveLeagueSpec] = []
    for value in values:
        if value not in by_key:
            raise HistoryProbeError("invalid archive league") from None
        selected.append(by_key[value])
    return tuple(selected)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--replay", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--seasons")
    parser.add_argument("--league", action="append")
    parser.add_argument("--sample-per-league-season", type=int, default=0)
    parser.add_argument("--verify-company-catalogs", action="store_true")
    parser.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS)
    parser.add_argument("--max-output-bytes", type=int, default=MAX_OUTPUT_BYTES)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--min-sleep", type=float, default=0.8)
    parser.add_argument("--max-sleep", type=float, default=1.5)
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=0.0,
        help=(
            "单次请求失败后,重试前额外等待这么多秒(在原有 --min-sleep/"
            "--max-sleep 抖动之上叠加,只作用于重试那次,不影响首次尝试)。"
            "默认0保持原行为(重试紧跟失败,只有原有抖动)。"
        ),
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument(
        "--match-allowlist",
        type=Path,
        default=None,
        help=(
            "gap-manifest 分片清单(JSONL,每行至少含 titan_id)。给定后只抓清单里的"
            "比赛,忽略 --sample-per-league-season;要求恰好一个 --seasons 和一个 --league;"
            "清单为空、或清单里的 titan_id 在该联赛/赛季里找不全,直接硬失败,不产出部分成功。"
        ),
    )
    parser.add_argument(
        "--skip-archive-discovery",
        action="store_true",
        help=(
            "跳过 archive_season 请求(那个域名连不上时用),直接从 --match-allowlist "
            "清单构造比赛列表——清单每行必须额外带 home_team_name_en/away_team_name_en/"
            "nowgoal_kickoff_local(北京时间开球时刻,精确到分钟)。不能与 "
            "--verify-company-catalogs 同时使用。"
        ),
    )
    parser.add_argument(
        "--proxy-env",
        type=str,
        default=None,
        help=(
            "读取这个环境变量的值作为显式代理 URL(例如 THORDATA_PROXY),仅用于 "
            "--live 模式。不给这个 flag 就直连,不读取任何 ambient 代理配置"
            "(trust_env=False 恒成立)。给了但变量未设置/为空,直接硬失败退出,"
            "不会静默退回直连。"
        ),
    )
    return parser


def _resolve_proxy(env_var: str | None) -> str | None:
    if env_var is None:
        return None
    value = os.environ.get(env_var)
    if not value:
        from dotenv import load_dotenv

        load_dotenv()
        value = os.environ.get(env_var)
    if not value:
        raise HistoryProbeError(f"--proxy-env {env_var} is not set or empty") from None
    return value


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    mode = "live" if args.live else "replay" if args.replay else "dry-run"
    output_root = args.output_root.resolve()
    match_allowlist = None
    match_allowlist_rows = None
    if args.match_allowlist is not None:
        if args.skip_archive_discovery:
            match_allowlist_rows = load_match_allowlist_rows(args.match_allowlist)
            match_allowlist = frozenset(str(r["titan_id"]) for r in match_allowlist_rows)
        else:
            match_allowlist = load_match_allowlist(args.match_allowlist)
    try:
        resolved_proxy = _resolve_proxy(args.proxy_env)
    except HistoryProbeError as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    config = BackfillConfig(
        seasons=_parse_seasons(args.seasons),
        leagues=_parse_leagues(args.league),
        sample_per_league_season=args.sample_per_league_season,
        verify_company_catalogs=args.verify_company_catalogs,
        max_attempts=args.max_attempts,
        max_output_bytes=args.max_output_bytes,
        concurrency=args.concurrency,
        min_sleep_seconds=args.min_sleep,
        max_sleep_seconds=args.max_sleep,
        resume=args.resume,
        preflight_only=args.preflight_only,
        match_allowlist=match_allowlist,
        match_allowlist_rows=match_allowlist_rows,
        skip_archive_discovery=args.skip_archive_discovery,
        proxy_used=resolved_proxy is not None,
        retry_backoff_seconds=args.retry_backoff_seconds,
    )
    store = DurableArtifactStore(
        output_root.parent,
        output_root.name,
        mode=mode,
        max_attempts=config.max_attempts,
    )
    transport = DirectNowGoalTransport(proxy=resolved_proxy) if mode == "live" else None
    try:
        result = NowGoalTop5HistoryBackfill(
            output_root=output_root,
            store=store,
            transport=transport,
            config=config,
        ).run()
    finally:
        if transport is not None:
            transport.close()
    print(
        json.dumps(
            {
                "mode": result["mode"],
                "selected_match_count": result["selected_match_count"],
                "completed_match_count": result["completed_match_count"],
                "availability_rows": result["availability_rows"],
                "failure_rows": result["failure_rows"],
                "transport_attempts": result["transport_attempts"],
                "residential_proxy_used": resolved_proxy is not None,
                "verdict": result["verdict"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if not str(result["verdict"]).endswith("_PARTIAL") else 1


if __name__ == "__main__":
    raise SystemExit(main())
