from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from analysis.nowgoal_historical_capability_probe.nowgoal_historical_capability_probe import (
    ArchiveHistoryCapabilityProbe,
    ArchiveLeagueSpec,
    ArtifactValidationError,
    DirectNowGoalTransport,
    DurableArtifactStore,
    HistoricalTarget,
    HistoryCapabilityProbe,
    HistoryProbeError,
    HistoryProbeWAFBlocked,
    RequestBudgetExhausted,
    _redact_diagnostic,
    load_historical_targets,
    map_nowgoal_match,
    parse_archive_season,
    parse_company_catalog,
    parse_euro_catalog,
    resolve_archive_companies,
    resolve_euro_companies,
    select_era_targets,
    summarize_history,
    summarize_euro_history,
)


def _target(
    match_id: int,
    season: str,
    kickoff: str,
    *,
    competition: str = "mls",
    home: str = "New York City FC",
    away: str = "LA Galaxy",
) -> HistoricalTarget:
    return HistoricalTarget(
        competition=competition,
        competition_id=130,
        provider_season=season,
        era="",
        fotmob_match_id=match_id,
        home_name=home,
        away_name=away,
        kickoff_utc=kickoff,
        beijing_date=kickoff[:10],
        source_artifact=f"raw/{competition}.season.{season}.json",
        source_sha256="a" * 64,
    )


def _schedule_text(
    titan_id: str = "900001",
    *,
    home: str = "New York City FC",
    away: str = "LA Galaxy",
    kickoff_tuple: str = "2020,2,1,12,00,00",
) -> bytes:
    return (
        f"A[1]=[{titan_id},3,101,202,'{home}','{away}',"
        f"'{kickoff_tuple}'];"
    ).encode()


def _odds_bytes() -> bytes:
    return json.dumps(
        {
            "ErrCode": 0,
            "Data": {
                "mixodds": [
                    {
                        "cid": "8",
                        "cn": "Bet365",
                        "euro": {
                            "f": {"u": "2.10", "g": "3.40", "d": "3.50"},
                            "l": {"u": "2.05", "g": "3.45", "d": "3.60"},
                        },
                        "ah": {
                            "f": {"u": "0.95", "g": "-0.5", "d": "0.93"},
                            "l": {"u": "0.90", "g": "-0.5", "d": "0.98"},
                        },
                        "ou": {
                            "f": {"u": "0.90", "g": "2.5", "d": "0.96"},
                            "l": {"u": "0.85", "g": "2.75", "d": "1.01"},
                        },
                    }
                ]
            },
            "MatchState": -1,
        },
        separators=(",", ":"),
    ).encode()


class FakeTransport:
    def __init__(self, *, fail_schedule: int = 0, waf: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_schedule = fail_schedule
        self.waf = waf

    def schedule(self, beijing_date: str) -> bytes:
        self.calls.append(("schedule", beijing_date))
        if self.waf:
            raise HistoryProbeWAFBlocked("NowGoal direct request was blocked")
        if self.fail_schedule:
            self.fail_schedule -= 1
            raise HistoryProbeError("NowGoal direct request failed")
        return _schedule_text()

    def odds(self, titan_id: str) -> bytes:
        self.calls.append(("odds", titan_id))
        return _odds_bytes()

    def archive_season(self, league_id: int, season: str) -> bytes:
        self.calls.append(("archive", f"{league_id}:{season}"))
        return _archive_bytes(league_id=league_id, season=season)

    def odds_history(self, titan_id: str, company_id: str) -> bytes:
        self.calls.append(("history", f"{titan_id}:{company_id}"))
        return _history_bytes()

    def euro_catalog(self, titan_id: str) -> bytes:
        self.calls.append(("euro_catalog", titan_id))
        return _euro_catalog_bytes()

    def euro_history(self, titan_id: str, company_id: str) -> bytes:
        self.calls.append(("euro_history", f"{titan_id}:{company_id}"))
        return _euro_history_bytes()

    def close(self) -> None:
        return None


def _archive_bytes(
    *,
    league_id: int = 36,
    season: str = "2020-2021",
    name: str = "English Premier League",
) -> bytes:
    return json.dumps(
        {
            "LeagueInfo": [league_id, name, season],
            "TeamInfo": [
                [101, "Arsenal"],
                [102, "Brighton Hove Albion"],
                [103, "Chelsea"],
            ],
            "ScheduleList": {
                "R_1": [
                    [
                        1900001,
                        league_id,
                        -1,
                        "2020-09-12 19:30",
                        101,
                        102,
                        "2-0",
                        "1-0",
                    ],
                    [
                        1900002,
                        league_id,
                        -1,
                        "2021-01-01 20:00",
                        102,
                        103,
                        "1-1",
                        "0-0",
                    ],
                    [
                        1900003,
                        league_id,
                        -1,
                        "2021-05-23 23:00",
                        103,
                        101,
                        "0-1",
                        "0-0",
                    ],
                ]
            },
        },
        separators=(",", ":"),
    ).encode()


def _catalog_bytes() -> bytes:
    return json.dumps(
        {
            "ErrCode": 0,
            "Data": {
                "mixodds": [
                    {"cid": "8", "cn": "Bet365"},
                    {"cid": "1", "cn": "Macauslot"},
                    {"cid": "88", "cn": "Pinnacle"},
                ]
            },
        },
        separators=(",", ":"),
    ).encode()


def _history_bytes() -> bytes:
    kickoff = int(datetime(2021, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp())
    return json.dumps(
        {
            "ErrCode": 0,
            "Data": {
                "ah": [
                    {"mt": kickoff - 3600, "odds": {"u": "1", "g": "0", "d": "1"}},
                    {"mt": kickoff + 60, "odds": {"u": "1", "g": "0", "d": "1"}},
                ],
                "op": [{"mt": kickoff - 7200, "odds": {"u": "2", "g": "3", "d": "4"}}],
                "ou": [],
            },
        },
        separators=(",", ":"),
    ).encode()


def _euro_catalog_bytes() -> bytes:
    return (
        '\ufeffvar game=Array("281|a|Bet 365|x",'
        '"80|b|Macauslot|x","177|c|Pinnacle|x");'
    ).encode()


def _euro_history_bytes() -> bytes:
    return json.dumps(
        [
            {
                "HomeWin": "2.10",
                "Standoff": "3.20",
                "GuestWin": "3.40",
                "TimeShow": "2021,01,01,10,00,00",
            },
            {
                "HomeWin": "2.00",
                "Standoff": "3.30",
                "GuestWin": "3.50",
                "TimeShow": "2021,01,01,12,01,00",
            },
        ],
        separators=(",", ":"),
    ).encode()


def test_selects_one_match_from_early_middle_late_completed_seasons() -> None:
    candidates = []
    for season_index, season in enumerate(
        ("2018", "2019", "2020", "2021", "2022")
    ):
        for match_index in range(3):
            candidates.append(
                _target(
                    season_index * 10 + match_index + 1,
                    season,
                    f"{season}-03-0{match_index + 1}T12:00:00Z",
                )
            )
    selected = select_era_targets(candidates)
    assert [(row.era, row.provider_season) for row in selected] == [
        ("early", "2018"),
        ("middle", "2020"),
        ("late", "2022"),
    ]
    assert [row.fotmob_match_id for row in selected] == [2, 22, 42]
    assert select_era_targets(list(reversed(candidates))) == selected


def test_load_targets_rejoins_names_from_validated_raw_fixture(
    tmp_path: Path,
) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    seasons = ("2018", "2019", "2020")
    for season in seasons:
        fixtures = []
        for index in range(3):
            fixtures.append(
                {
                    "id": int(season) * 10 + index,
                    "home": {"id": 100 + index, "name": f"Home {season}"},
                    "away": {"id": 200 + index, "name": f"Away {season}"},
                    "status": {
                        "finished": True,
                        "cancelled": False,
                        "utcTime": f"{season}-03-0{index + 1}T12:00:00Z",
                    },
                }
            )
        payload = {
            "details": {
                "id": 130,
                "name": "MLS",
                "selectedSeason": season,
                "allAvailableSeasons": list(seasons),
            },
            "fixtures": {"allMatches": fixtures},
        }
        (raw_dir / f"mls.season.{season}.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
    targets = load_historical_targets(tmp_path, ["mls"])
    assert [target.home_name for target in targets] == [
        "Home 2018",
        "Home 2019",
        "Home 2020",
    ]
    assert [target.away_name for target in targets] == [
        "Away 2018",
        "Away 2019",
        "Away 2020",
    ]


def test_mapping_requires_kickoff_and_both_team_names() -> None:
    target = _target(1, "2020", "2020-03-01T12:00:00Z")
    rows = [
        {
            "titan_id": "900001",
            "home_name": "New York City",
            "away_name": "Los Angeles Galaxy",
            "kickoff": "2020-03-01 12:00:00",
        },
        {
            "titan_id": "900002",
            "home_name": "New York City",
            "away_name": "Wrong Team",
            "kickoff": "2020-03-01 12:00:00",
        },
    ]
    result = map_nowgoal_match(target, rows)
    assert result is not None
    assert result["titan_id"] == "900001"
    assert result["kickoff_diff_seconds"] == 0
    assert result["home_away_inverted"] is False


def test_mapping_records_explicit_home_away_inversion() -> None:
    target = _target(1, "2020", "2020-03-01T12:00:00Z")
    result = map_nowgoal_match(
        target,
        [
            {
                "titan_id": "900001",
                "home_name": "LA Galaxy",
                "away_name": "New York City FC",
                "kickoff": "2020-03-01 12:00:00",
            }
        ],
    )
    assert result is not None
    assert result["home_away_inverted"] is True


def test_mapping_rejects_bad_kickoff_and_ambiguous_candidates() -> None:
    target = _target(1, "2020", "2020-03-01T12:00:00Z")
    assert (
        map_nowgoal_match(
            target,
            [
                {
                    "titan_id": "900001",
                    "home_name": target.home_name,
                    "away_name": target.away_name,
                    "kickoff": "2020-03-01 13:00:00",
                }
            ],
        )
        is None
    )
    duplicate = {
        "home_name": target.home_name,
        "away_name": target.away_name,
        "kickoff": "2020-03-01 12:00:00",
    }
    assert (
        map_nowgoal_match(
            target,
            [
                duplicate | {"titan_id": "900001"},
                duplicate | {"titan_id": "900002"},
            ],
        )
        is None
    )


def test_direct_transport_disables_environment_proxy(monkeypatch) -> None:
    observed: dict[str, object] = {}

    class StubClient:
        def __init__(self, **kwargs) -> None:
            observed.update(kwargs)

        def close(self) -> None:
            observed["closed"] = True

    monkeypatch.setattr("httpx.Client", StubClient)
    transport = DirectNowGoalTransport()
    transport.close()
    assert observed["trust_env"] is False
    assert observed["follow_redirects"] is True
    assert observed["closed"] is True


def test_direct_transport_hides_system_exception_chain(monkeypatch) -> None:
    class FailingClient:
        def __init__(self, **kwargs) -> None:
            return None

        def get(self, *args, **kwargs):
            raise RuntimeError(
                "https://user:secret@example.invalid /tmp/private"
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr("httpx.Client", FailingClient)
    transport = DirectNowGoalTransport()
    with pytest.raises(
        HistoryProbeError, match=r"^NowGoal direct request failed$"
    ) as caught:
        transport.schedule("2020-03-01")
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    # 外部可见的错误消息保持固定不变(不泄漏细节),但 .diagnostic 属性(只进
    # 内部 0600 ledger,不进 str(error))应该带上真实异常类型,且已脱敏。
    assert caught.value.diagnostic is not None
    assert caught.value.diagnostic.startswith("RuntimeError:")
    assert "user:secret" not in caught.value.diagnostic
    assert "[REDACTED]" in caught.value.diagnostic


def test_direct_transport_captures_http_status_diagnostic(monkeypatch) -> None:
    class FakeResponse:
        status_code = 429
        text = "rate limited"

    class RateLimitedClient:
        def __init__(self, **kwargs) -> None:
            return None

        def get(self, *args, **kwargs):
            return FakeResponse()

        def close(self) -> None:
            return None

    monkeypatch.setattr("httpx.Client", RateLimitedClient)
    transport = DirectNowGoalTransport()
    with pytest.raises(
        HistoryProbeError, match=r"^NowGoal direct request failed$"
    ) as caught:
        transport.schedule("2020-03-01")
    assert caught.value.diagnostic == "http_status=429"


def test_direct_transport_watchdog_fires_on_hung_connect(monkeypatch) -> None:
    monkeypatch.setattr(
        "analysis.nowgoal_historical_capability_probe."
        "nowgoal_historical_capability_probe._HTTP_WATCHDOG_TIMEOUT_SECONDS",
        0.05,
    )

    class HangingClient:
        def __init__(self, **kwargs) -> None:
            return None

        def get(self, *args, **kwargs):
            # 模拟卡在 SYN_SENT、永远不返回的连接;daemon 线程,测试进程退出时
            # 不会被这条线程拖住。
            threading.Event().wait()

        def close(self) -> None:
            return None

    monkeypatch.setattr("httpx.Client", HangingClient)
    transport = DirectNowGoalTransport()
    with pytest.raises(
        HistoryProbeError, match=r"^NowGoal direct request failed$"
    ) as caught:
        transport.schedule("2020-03-01")
    assert caught.value.diagnostic is not None
    assert caught.value.diagnostic.startswith("TimeoutError:")
    assert "watchdog" in caught.value.diagnostic


def test_direct_transport_watchdog_does_not_interfere_with_success(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        text = "{}"

    class FastClient:
        def __init__(self, **kwargs) -> None:
            return None

        def get(self, *args, **kwargs):
            return FakeResponse()

        def close(self) -> None:
            return None

    monkeypatch.setattr("httpx.Client", FastClient)
    transport = DirectNowGoalTransport()
    assert transport.schedule("2020-03-01") == b"{}"


def test_odds_history_alt_host_requests_nowgoal_net(monkeypatch) -> None:
    observed: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        text = "{}"

    class RecordingClient:
        def __init__(self, **kwargs) -> None:
            return None

        def get(self, url, params=None, headers=None):
            observed["url"] = url
            observed["params"] = params
            observed["headers"] = headers
            return FakeResponse()

        def close(self) -> None:
            return None

    monkeypatch.setattr("httpx.Client", RecordingClient)
    transport = DirectNowGoalTransport()
    transport.odds_history_alt_host("2027353", "8")
    assert observed["url"] == "https://www.nowgoal.net/ajax/soccerajax"
    assert observed["params"]["id"] == "2027353"
    assert observed["params"]["cid"] == "8"
    assert observed["params"]["t"] == "20"
    assert "live10.nowgoal26.com" not in observed["url"]
    assert observed["headers"]["Referer"] == "https://www.nowgoal.net/oddscomp/2027353"
    assert observed["headers"]["X-Requested-With"] == "XMLHttpRequest"


def test_euro_history_alt_host_requests_nowgoal_net(monkeypatch) -> None:
    observed: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        text = "{}"

    class RecordingClient:
        def __init__(self, **kwargs) -> None:
            return None

        def get(self, url, params=None, headers=None):
            observed["url"] = url
            observed["params"] = params
            observed["headers"] = headers
            return FakeResponse()

        def close(self) -> None:
            return None

    monkeypatch.setattr("httpx.Client", RecordingClient)
    transport = DirectNowGoalTransport()
    transport.euro_history_alt_host("2027353", "281")
    assert observed["url"] == "https://www.nowgoal.net/football/geteurooddshistorydetail"
    assert observed["params"] == {"sid": "2027353", "cid": "281"}
    assert "live10.nowgoal26.com" not in observed["url"]
    assert observed["headers"]["Referer"] == "https://www.nowgoal.net/1x2-odds/2027353"


def test_alt_host_methods_reject_non_numeric_ids() -> None:
    transport = DirectNowGoalTransport.__new__(DirectNowGoalTransport)
    with pytest.raises(HistoryProbeError):
        transport.odds_history_alt_host("abc", "8")
    with pytest.raises(HistoryProbeError):
        transport.euro_history_alt_host("2027353", "abc")


class TestRedactDiagnostic:
    def test_strips_url_credentials(self) -> None:
        redacted = _redact_diagnostic("ProxyError: http://td-customer-x:hunter2@proxy.example:9999 timed out")
        assert "hunter2" not in redacted
        assert "td-customer-x" not in redacted
        assert "[REDACTED]" in redacted

    def test_caps_length(self) -> None:
        redacted = _redact_diagnostic("x" * 500)
        assert len(redacted) <= 200

    def test_passthrough_when_no_credentials(self) -> None:
        assert _redact_diagnostic("ReadTimeout: timed out") == "ReadTimeout: timed out"


def test_acquire_ledger_records_diagnostic_on_failure(tmp_path: Path) -> None:
    store = DurableArtifactStore(tmp_path, "run-1", mode="live", max_attempts=10)

    def failing_fetch() -> bytes:
        err = HistoryProbeError("NowGoal direct request failed")
        err.diagnostic = "ReadTimeout: timed out after 15s"
        raise err

    with pytest.raises(HistoryProbeError):
        store.acquire("k1", "odds_history", failing_fetch, retry_once=False)

    ledger_path = tmp_path / "run-1" / "request-ledger.jsonl"
    rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    failed_rows = [r for r in rows if r["phase"] == "FAILED"]
    assert len(failed_rows) == 1
    assert failed_rows[0]["error"] == "NowGoal direct request failed"
    assert failed_rows[0]["diagnostic"] == "ReadTimeout: timed out after 15s"


def test_acquire_ledger_diagnostic_none_when_absent(tmp_path: Path) -> None:
    store = DurableArtifactStore(tmp_path, "run-1", mode="live", max_attempts=10)

    def failing_fetch() -> bytes:
        raise RuntimeError("no diagnostic attribute set")

    with pytest.raises(HistoryProbeError):
        store.acquire("k1", "odds_history", failing_fetch, retry_once=False)

    ledger_path = tmp_path / "run-1" / "request-ledger.jsonl"
    rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    failed_rows = [r for r in rows if r["phase"] == "FAILED"]
    assert failed_rows[0]["diagnostic"] is None


def test_budget_rejects_before_transport(tmp_path: Path) -> None:
    store = DurableArtifactStore(
        tmp_path, "run-1", mode="live", max_attempts=0
    )
    calls = 0

    def fetch() -> bytes:
        nonlocal calls
        calls += 1
        return b"unused"

    with pytest.raises(
        RequestBudgetExhausted, match=r"^request budget exhausted$"
    ):
        store.acquire("schedule.2020-03-01", "schedule", fetch)
    assert calls == 0
    assert store.attempt_count == 0


def test_retry_resume_and_replay_are_durable(tmp_path: Path) -> None:
    transport = FakeTransport(fail_schedule=1)
    store = DurableArtifactStore(
        tmp_path, "run-1", mode="live", max_attempts=10
    )
    value = store.acquire(
        "schedule.2020-03-01",
        "schedule",
        lambda: transport.schedule("2020-03-01"),
    )
    assert value == _schedule_text()
    assert store.attempt_count == 2
    before = list(transport.calls)
    assert (
        store.acquire(
            "schedule.2020-03-01",
            "schedule",
            lambda: transport.schedule("2020-03-01"),
        )
        == value
    )
    assert transport.calls == before

    replay = DurableArtifactStore(
        tmp_path, "run-1", mode="replay", max_attempts=10
    )
    assert (
        replay.acquire(
            "schedule.2020-03-01",
            "schedule",
            lambda: pytest.fail("replay attempted transport"),
        )
        == value
    )


def test_replay_detects_artifact_tampering(tmp_path: Path) -> None:
    store = DurableArtifactStore(
        tmp_path, "run-1", mode="live", max_attempts=10
    )
    store.acquire("schedule.2020-03-01", "schedule", _schedule_text)
    artifact = store.raw_dir / "schedule.2020-03-01.bin"
    artifact.write_bytes(b"tampered")
    replay = DurableArtifactStore(
        tmp_path, "run-1", mode="replay", max_attempts=10
    )
    with pytest.raises(
        ArtifactValidationError,
        match=r"^saved artifact checksum mismatch$",
    ):
        replay.acquire("schedule.2020-03-01", "schedule", _schedule_text)


def test_live_probe_records_initial_latest_without_claiming_timeline(
    tmp_path: Path,
) -> None:
    target = _target(1, "2020", "2020-03-01T12:00:00Z")
    target = HistoricalTarget(
        **(target.as_dict() | {"beijing_date": "2020-03-01"})
    )
    store = DurableArtifactStore(
        tmp_path, "run-1", mode="live", max_attempts=10
    )
    transport = FakeTransport()
    result = HistoryCapabilityProbe(store, transport).run(
        [target], ["2020-03-02"], ["999999"]
    )
    assert result["counts"] == {
        "targets": 1,
        "schedule_available": 1,
        "mapped": 1,
        "odds_available": 1,
        "with_1x2": 1,
        "with_ah": 1,
        "with_ou": 1,
        "control_dates": 1,
        "control_dates_available": 1,
        "control_titan_ids": 1,
        "control_odds_available": 1,
    }
    assert result["controls"][0]["status"] == "AVAILABLE"
    assert result["odds_controls"][0]["status"] == "AVAILABLE"
    row = result["results"][0]
    assert row["status"] == "ODDS_AVAILABLE"
    assert row["odds_semantics"] == "INITIAL_AND_LATEST_ONLY"
    assert row["historical_timeline_verified"] is False
    assert row["closing_odds_verified"] is False
    assert row["source_timestamp"] is None
    assert transport.calls == [
        ("schedule", "2020-03-02"),
        ("odds", "999999"),
        ("schedule", "2020-03-01"),
        ("odds", "900001"),
    ]
    ledger_before = store.ledger_path.read_bytes()
    replay_store = DurableArtifactStore(
        tmp_path, "run-1", mode="replay", max_attempts=10
    )
    replay = HistoryCapabilityProbe(replay_store, None).run(
        [target], ["2020-03-02"], ["999999"]
    )
    assert replay["counts"] == result["counts"]
    assert replay_store.ledger_path.read_bytes() == ledger_before


def test_waf_stops_probe_and_error_is_fixed_safe(tmp_path: Path) -> None:
    target = _target(1, "2020", "2020-03-01T12:00:00Z")
    store = DurableArtifactStore(
        tmp_path, "run-1", mode="live", max_attempts=10
    )
    with pytest.raises(
        HistoryProbeWAFBlocked,
        match=r"^NowGoal direct request was blocked$",
    ) as caught:
        HistoryCapabilityProbe(store, FakeTransport(waf=True)).run([target])
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_dry_run_uses_zero_transport(tmp_path: Path) -> None:
    target = _target(1, "2020", "2020-03-01T12:00:00Z")
    store = DurableArtifactStore(
        tmp_path, "run-1", mode="dry-run", max_attempts=10
    )
    transport = FakeTransport()
    result = HistoryCapabilityProbe(store, transport).run([target])
    assert result["mode"] == "dry-run"
    assert result["counts"]["targets"] == 1
    assert store.attempt_count == 0
    assert transport.calls == []


def test_errors_do_not_expose_payload_url_or_paths(tmp_path: Path) -> None:
    store = DurableArtifactStore(
        tmp_path, "run-1", mode="live", max_attempts=10
    )

    def explode() -> bytes:
        raise RuntimeError(
            "https://user:secret@example.invalid private payload /tmp/secret"
        )

    with pytest.raises(
        HistoryProbeError, match=r"^NowGoal direct request failed$"
    ) as caught:
        store.acquire("schedule.2020-03-01", "schedule", explode)
    rendered = str(caught.value)
    assert "secret" not in rendered
    assert "http" not in rendered
    assert "/tmp" not in rendered
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_archive_season_identity_and_deterministic_middle_sample() -> None:
    spec = ArchiveLeagueSpec("premier_league", 36, "English Premier League")
    matches = parse_archive_season(
        json.loads(_archive_bytes()), spec, "2020-2021"
    )
    assert [row.titan_id for row in matches] == [
        "1900001",
        "1900002",
        "1900003",
    ]
    assert matches[0].kickoff_utc == "2020-09-12T11:30:00Z"
    from analysis.nowgoal_historical_capability_probe.nowgoal_historical_capability_probe import (
        select_archive_sample,
    )

    assert select_archive_sample(matches).titan_id == "1900002"
    wrapped = json.loads(_archive_bytes())
    wrapped["ScheduleList"] = {"sub_100": wrapped["ScheduleList"]}
    assert len(parse_archive_season(wrapped, spec, "2020-2021")) == 3
    with pytest.raises(
        HistoryProbeError, match=r"^archive season identity is invalid$"
    ):
        parse_archive_season(
            json.loads(_archive_bytes(name="Wrong League")),
            spec,
            "2020-2021",
        )


def test_company_catalog_uses_real_names_and_never_guesses_pinnacle() -> None:
    catalog = parse_company_catalog(json.loads(_catalog_bytes()))
    resolved = resolve_archive_companies(catalog)
    assert resolved["bet365"] == {
        "company_id": "8",
        "company_name": "Bet365",
    }
    assert resolved["macauslot"] == {
        "company_id": "1",
        "company_name": "Macauslot",
    }
    assert resolved["pinnacle"] == {
        "company_id": "88",
        "company_name": "Pinnacle",
    }
    absent = resolve_archive_companies(
        parse_company_catalog(
            {"Data": {"mixodds": [{"cid": "8", "cn": "Bet365"}]}}
        )
    )
    assert absent["pinnacle"] is None


def test_history_summary_separates_pre_match_from_in_play() -> None:
    summary = summarize_history(
        json.loads(_history_bytes()),
        "2021-01-01T12:00:00Z",
    )
    assert summary["pre_match_rows"] == 2
    assert summary["in_play_rows"] == 1
    assert summary["markets"]["ah"]["pre_match_rows"] == 1
    assert summary["markets"]["ah"]["in_play_rows"] == 1
    assert summary["markets"]["1x2"]["pre_match_rows"] == 1


def test_euro_catalog_and_history_use_distinct_verified_company_ids() -> None:
    catalog = parse_euro_catalog(_euro_catalog_bytes())
    assert resolve_euro_companies(catalog) == {
        "bet365": {"company_id": "281", "company_name": "Bet 365"},
        "macauslot": {"company_id": "80", "company_name": "Macauslot"},
        "pinnacle": {"company_id": "177", "company_name": "Pinnacle"},
    }
    summary = summarize_euro_history(
        json.loads(_euro_history_bytes()),
        "2021-01-01T12:00:00Z",
    )
    assert summary["rows"] == 2
    assert summary["pre_match_rows"] == 1
    assert summary["in_play_rows"] == 1


def test_archive_probe_fetches_one_season_catalog_and_real_company_histories(
    tmp_path: Path,
) -> None:
    class ArchiveTransport(FakeTransport):
        def odds(self, titan_id: str) -> bytes:
            self.calls.append(("odds", titan_id))
            return json.dumps(
                {
                    "ErrCode": 0,
                    "Data": {
                        "mixodds": [
                            {"cid": "8", "cn": "Bet365"},
                            {"cid": "1", "cn": "Macauslot"},
                        ]
                    },
                }
            ).encode()

    spec = ArchiveLeagueSpec("premier_league", 36, "English Premier League")
    transport = ArchiveTransport()
    store = DurableArtifactStore(
        tmp_path, "archive-run", mode="live", max_attempts=8
    )
    result = ArchiveHistoryCapabilityProbe(store, transport).run(
        "2020-2021", [spec]
    )
    assert result["verdict"] == "TOP5_ARCHIVE_SAMPLE_AVAILABLE"
    assert result["counts"] == {
        "competitions": 1,
        "samples": 1,
        "bet365_pre_match_available": 1,
        "macauslot_pre_match_available": 1,
        "pinnacle_pre_match_available": 1,
    }
    row = result["results"][0]
    assert row["titan_id"] == "1900002"
    assert row["season_match_count"] == 3
    assert row["companies"]["bet365"]["pre_match_rows"] == 3
    assert row["companies"]["pinnacle"]["markets"]["1x2"]["pre_match_rows"] == 1
    assert transport.calls == [
        ("archive", "36:2020-2021"),
        ("odds", "1900002"),
        ("history", "1900002:8"),
        ("history", "1900002:1"),
        ("euro_catalog", "1900002"),
        ("euro_history", "1900002:281"),
        ("euro_history", "1900002:80"),
        ("euro_history", "1900002:177"),
    ]

    before = store.ledger_path.read_bytes()
    replay_store = DurableArtifactStore(
        tmp_path, "archive-run", mode="replay", max_attempts=8
    )
    replay = ArchiveHistoryCapabilityProbe(replay_store, None).run(
        "2020-2021", [spec]
    )
    assert replay["counts"] == result["counts"]
    assert replay_store.ledger_path.read_bytes() == before


def test_archive_probe_requires_all_three_requested_companies(
    tmp_path: Path,
) -> None:
    class MissingPinnacleTransport(FakeTransport):
        def odds(self, titan_id: str) -> bytes:
            self.calls.append(("odds", titan_id))
            return json.dumps(
                {
                    "ErrCode": 0,
                    "Data": {
                        "mixodds": [
                            {"cid": "8", "cn": "Bet365"},
                            {"cid": "1", "cn": "Macauslot"},
                        ]
                    },
                }
            ).encode()

        def euro_catalog(self, titan_id: str) -> bytes:
            self.calls.append(("euro_catalog", titan_id))
            return (
                '\ufeffvar game=Array("281|a|Bet 365|x",'
                '"80|b|Macauslot|x");'
            ).encode()

    spec = ArchiveLeagueSpec("premier_league", 36, "English Premier League")
    result = ArchiveHistoryCapabilityProbe(
        DurableArtifactStore(
            tmp_path, "archive-missing-pinnacle", mode="live", max_attempts=8
        ),
        MissingPinnacleTransport(),
    ).run("2020-2021", [spec])

    assert result["counts"]["bet365_pre_match_available"] == 1
    assert result["counts"]["macauslot_pre_match_available"] == 1
    assert result["counts"]["pinnacle_pre_match_available"] == 0
    assert result["verdict"] == "TOP5_ARCHIVE_SAMPLE_INCOMPLETE"


def test_acquire_retry_backoff_only_before_retry(tmp_path: Path, monkeypatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "analysis.nowgoal_historical_capability_probe.nowgoal_historical_capability_probe.time.sleep",
        lambda seconds: sleep_calls.append(seconds),
    )
    store = DurableArtifactStore(tmp_path, "run-1", mode="live", max_attempts=10)

    attempts = {"n": 0}

    def flaky_fetch() -> bytes:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("first attempt fails")
        return b"ok"

    result = store.acquire("k1", "op", flaky_fetch, retry_backoff_seconds=7.5)
    assert result == b"ok"
    assert sleep_calls == [7.5]  # 只在第2次尝试前睡,第1次不睡


def test_acquire_no_backoff_by_default(tmp_path: Path, monkeypatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "analysis.nowgoal_historical_capability_probe.nowgoal_historical_capability_probe.time.sleep",
        lambda seconds: sleep_calls.append(seconds),
    )
    store = DurableArtifactStore(tmp_path, "run-1", mode="live", max_attempts=10)

    attempts = {"n": 0}

    def flaky_fetch() -> bytes:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("first attempt fails")
        return b"ok"

    result = store.acquire("k2", "op", flaky_fetch)  # retry_backoff_seconds 默认 0
    assert result == b"ok"
    assert sleep_calls == []  # 默认行为不变,不引入任何新的sleep
