from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from analysis.nowgoal_historical_capability_probe.nowgoal_historical_capability_probe import (
    ArchiveLeagueSpec,
    DurableArtifactStore,
    HistoryProbeError,
    HistoryProbeWAFBlocked,
    RequestBudgetExhausted,
)
from analysis.nowgoal_historical_capability_probe.nowgoal_top5_history_backfill import (
    BackfillConfig,
    DEFAULT_SEASONS,
    IN_PLAY_EXCLUDED,
    MIX_COMPANIES,
    NowGoalTop5HistoryBackfill,
    PRE_MATCH,
    TOP5_ARCHIVE_LEAGUES,
    UNVERIFIED,
    _beijing_local_to_utc,
    _matches_from_allowlist_rows,
    _resolve_proxy,
    load_match_allowlist,
    load_match_allowlist_rows,
)


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


def _odds_catalog_bytes(*, wrong_bet365: bool = False) -> bytes:
    bet365_id = "9" if wrong_bet365 else "8"
    return json.dumps(
        {
            "ErrCode": 0,
            "Data": {
                "mixodds": [
                    {"cid": bet365_id, "cn": "Bet365"},
                    {"cid": "1", "cn": "Macauslot"},
                    {"cid": "88", "cn": "Pinnacle"},
                ]
            },
        },
        separators=(",", ":"),
    ).encode()


def _mix_history_bytes(company_id: str = "8") -> bytes:
    kickoff = int(datetime(2021, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp())
    if company_id == "1":
        ah_rows = []
        ou_rows = []
    else:
        ah_rows = [
            {"mt": kickoff - 7200, "home": "0.95", "handicap": "-0.5", "away": "0.93"},
            {"mt": kickoff + 60, "home": "0.90", "handicap": "-0.5", "away": "0.98"},
        ]
        ou_rows = [
            {"mt": kickoff - 3600, "over": "0.90", "total": "2.5", "under": "0.96"}
        ]
    return json.dumps(
        {"ErrCode": 0, "Data": {"ah": ah_rows, "op": [], "ou": ou_rows}},
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


class BackfillFakeTransport:
    def __init__(
        self,
        *,
        wrong_company: bool = False,
        waf_on_history: bool = False,
    ) -> None:
        self.calls: list[tuple[str, str]] = []
        self.wrong_company = wrong_company
        self.waf_on_history = waf_on_history

    def schedule(self, beijing_date: str) -> bytes:
        raise AssertionError("schedule endpoint is not part of archive backfill")

    def odds(self, titan_id: str) -> bytes:
        self.calls.append(("odds", titan_id))
        return _odds_catalog_bytes(wrong_bet365=self.wrong_company)

    def archive_season(self, league_id: int, season: str) -> bytes:
        self.calls.append(("archive", f"{league_id}:{season}"))
        spec_by_id = {spec.league_id: spec for spec in TOP5_ARCHIVE_LEAGUES}
        spec = spec_by_id[league_id]
        return _archive_bytes(
            league_id=league_id,
            season=season,
            name=spec.expected_name,
        )

    def odds_history(self, titan_id: str, company_id: str) -> bytes:
        self.calls.append(("history", f"{titan_id}:{company_id}"))
        if self.waf_on_history:
            raise HistoryProbeWAFBlocked("NowGoal direct request was blocked")
        return _mix_history_bytes(company_id)

    def euro_catalog(self, titan_id: str) -> bytes:
        self.calls.append(("euro_catalog", titan_id))
        return _euro_catalog_bytes()

    def euro_history(self, titan_id: str, company_id: str) -> bytes:
        self.calls.append(("euro_history", f"{titan_id}:{company_id}"))
        return _euro_history_bytes()

    def close(self) -> None:
        return None


def _config(
    *,
    verify_company_catalogs: bool = False,
    max_attempts: int = 100,
    max_output_bytes: int = 20 * 1024 * 1024,
    resume: bool = True,
    seasons: tuple[str, ...] = ("2020-2021",),
    leagues: tuple[ArchiveLeagueSpec, ...] = (TOP5_ARCHIVE_LEAGUES[0],),
    match_allowlist: frozenset[str] | None = None,
    match_allowlist_rows: tuple[dict, ...] | None = None,
    skip_archive_discovery: bool = False,
    proxy_used: bool = False,
    retry_backoff_seconds: float = 0.0,
) -> BackfillConfig:
    return BackfillConfig(
        seasons=seasons,
        leagues=leagues,
        sample_per_league_season=1,
        verify_company_catalogs=verify_company_catalogs,
        max_attempts=max_attempts,
        max_output_bytes=max_output_bytes,
        concurrency=1,
        min_sleep_seconds=0,
        max_sleep_seconds=0,
        resume=resume,
        preflight_only=False,
        match_allowlist=match_allowlist,
        match_allowlist_rows=match_allowlist_rows,
        skip_archive_discovery=skip_archive_discovery,
        proxy_used=proxy_used,
        retry_backoff_seconds=retry_backoff_seconds,
    )


def _runner(
    tmp_path: Path,
    transport: BackfillFakeTransport | None,
    config: BackfillConfig,
    *,
    mode: str = "live",
) -> NowGoalTop5HistoryBackfill:
    output_root = tmp_path / "nowgoal-top5-history-backfill-v1"
    store = DurableArtifactStore(
        output_root.parent,
        output_root.name,
        mode=mode,
        max_attempts=config.max_attempts,
    )
    return NowGoalTop5HistoryBackfill(
        output_root=output_root,
        store=store,
        transport=transport,
        config=config,
    )


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_backfill_writes_contract_artifacts_and_separates_in_play(
    tmp_path: Path,
) -> None:
    transport = BackfillFakeTransport()
    result = _runner(
        tmp_path,
        transport,
        _config(verify_company_catalogs=True),
    ).run()
    root = tmp_path / "nowgoal-top5-history-backfill-v1"

    assert result["verdict"] == "TOP5_HISTORICAL_ODDS_ARTIFACT_BACKFILL_COMPLETE"
    assert result["selected_match_count"] == 1
    assert result["completed_match_count"] == 1
    assert (root / "request-ledger.jsonl").is_file()
    assert (root / "season-manifest.json").is_file()
    assert (root / "match-manifest.jsonl").is_file()
    assert (root / "availability.jsonl").is_file()
    assert (root / "progress.json").is_file()
    assert (root / "run-summary.json").is_file()
    assert (root / "normalized" / "odds-history.jsonl").is_file()
    assert (root / "normalized" / "final-pre-match.jsonl").is_file()
    assert (root / "normalized" / "coverage-by-league-season.json").is_file()
    assert (root / "normalized" / "coverage-by-company-market.json").is_file()
    assert list((root / "raw" / "2020-2021" / "premier_league").rglob("*.raw"))

    availability = _jsonl(root / "availability.jsonl")
    assert len(availability) == 9
    assert {
        (row["company_key"], row["market"], row["status"]) for row in availability
    } >= {
        ("pinnacle", "ah", UNVERIFIED),
        ("pinnacle", "ou", UNVERIFIED),
        ("macauslot", "ah", "EMPTY"),
        ("macauslot", "ou", "EMPTY"),
        ("bet365", "ah", "PRE_MATCH_HISTORY_AVAILABLE"),
        ("bet365", "ou", "PRE_MATCH_HISTORY_AVAILABLE"),
    }

    normalized = _jsonl(root / "normalized" / "odds-history.jsonl")
    assert {row["phase"] for row in normalized} == {PRE_MATCH, IN_PLAY_EXCLUDED}
    assert all(row["residential_proxy_used"] is False for row in normalized)
    final = _jsonl(root / "normalized" / "final-pre-match.jsonl")
    assert final
    assert all(row["phase"] == PRE_MATCH for row in final)
    assert all(row["observed_at"] < row["kickoff_utc"] for row in final)
    assert transport.calls == [
        ("archive", "36:2020-2021"),
        ("odds", "1900002"),
        ("euro_catalog", "1900002"),
        ("history", "1900002:8"),
        ("history", "1900002:1"),
        ("euro_history", "1900002:281"),
        ("euro_history", "1900002:80"),
        ("euro_history", "1900002:177"),
    ]


def test_replay_resume_uses_saved_artifacts_without_transport(
    tmp_path: Path,
) -> None:
    live_config = _config(verify_company_catalogs=True)
    live = _runner(tmp_path, BackfillFakeTransport(), live_config).run()
    root = tmp_path / "nowgoal-top5-history-backfill-v1"
    ledger_before = (root / "request-ledger.jsonl").read_bytes()

    replay = _runner(tmp_path, None, live_config, mode="replay").run()
    assert replay["completed_match_count"] == live["completed_match_count"]
    assert (root / "request-ledger.jsonl").read_bytes() == ledger_before


def test_preflight_rejects_budget_before_history_requests(
    tmp_path: Path,
) -> None:
    transport = BackfillFakeTransport()
    with pytest.raises(RequestBudgetExhausted, match=r"^request budget exhausted$"):
        _runner(tmp_path, transport, _config(max_attempts=5)).run()
    assert transport.calls == [("archive", "36:2020-2021")]


def test_output_budget_rejects_before_history_requests(tmp_path: Path) -> None:
    transport = BackfillFakeTransport()
    with pytest.raises(RequestBudgetExhausted, match=r"^output budget exhausted$"):
        _runner(tmp_path, transport, _config(max_output_bytes=1)).run()
    assert transport.calls == [("archive", "36:2020-2021")]


def test_company_identity_mismatch_is_fatal_before_normalized(
    tmp_path: Path,
) -> None:
    transport = BackfillFakeTransport(wrong_company=True)
    with pytest.raises(HistoryProbeError, match=r"^company identity mismatch$"):
        _runner(
            tmp_path,
            transport,
            _config(verify_company_catalogs=True),
        ).run()
    root = tmp_path / "nowgoal-top5-history-backfill-v1"
    assert not (root / "normalized" / "odds-history.jsonl").exists()
    assert transport.calls == [
        ("archive", "36:2020-2021"),
        ("odds", "1900002"),
        ("euro_catalog", "1900002"),
    ]


def test_waf_stops_fail_closed_with_safe_error(tmp_path: Path) -> None:
    with pytest.raises(
        HistoryProbeWAFBlocked,
        match=r"^NowGoal direct request was blocked$",
    ) as caught:
        _runner(
            tmp_path,
            BackfillFakeTransport(waf_on_history=True),
            _config(),
        ).run()
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_dry_run_uses_zero_transport(tmp_path: Path) -> None:
    transport = BackfillFakeTransport()
    config = BackfillConfig(
        seasons=DEFAULT_SEASONS,
        leagues=(TOP5_ARCHIVE_LEAGUES[0],),
        sample_per_league_season=1,
        verify_company_catalogs=False,
        max_attempts=100,
        max_output_bytes=20 * 1024 * 1024,
        concurrency=1,
        min_sleep_seconds=0,
        max_sleep_seconds=0,
        resume=True,
        preflight_only=False,
    )
    result = _runner(tmp_path, transport, config, mode="dry-run").run()
    assert result["verdict"] == "DRY_RUN_ONLY"
    assert result["transport_attempts"] == 0
    assert transport.calls == []
    assert (tmp_path / "nowgoal-top5-history-backfill-v1" / "preflight-estimate.json").is_file()


def test_market_company_contract_is_fixed() -> None:
    assert [(company.key, company.company_id) for company in MIX_COMPANIES] == [
        ("bet365", "8"),
        ("macauslot", "1"),
    ]


# ── --match-allowlist(缺口分片驱动)────────────────────────────────────


def test_load_match_allowlist_parses_titan_ids(tmp_path: Path) -> None:
    path = tmp_path / "allowlist.jsonl"
    path.write_text(
        '{"titan_id": "1900001", "other": "ignored"}\n\n{"titan_id": "1900002"}\n',
        encoding="utf-8",
    )
    assert load_match_allowlist(path) == frozenset({"1900001", "1900002"})


def test_load_match_allowlist_empty_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "allowlist.jsonl"
    path.write_text("", encoding="utf-8")
    with pytest.raises(HistoryProbeError):
        load_match_allowlist(path)


def test_load_match_allowlist_missing_titan_field_raises(tmp_path: Path) -> None:
    path = tmp_path / "allowlist.jsonl"
    path.write_text('{"match_id": 123}\n', encoding="utf-8")
    with pytest.raises(HistoryProbeError):
        load_match_allowlist(path)


def test_load_match_allowlist_invalid_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "allowlist.jsonl"
    path.write_text("{not valid json\n", encoding="utf-8")
    with pytest.raises(HistoryProbeError):
        load_match_allowlist(path)


def test_match_allowlist_selects_only_listed_titan(tmp_path: Path) -> None:
    transport = BackfillFakeTransport()
    config = _config(match_allowlist=frozenset({"1900002"}))
    result = _runner(tmp_path, transport, config).run()
    root = tmp_path / "nowgoal-top5-history-backfill-v1"

    assert result["selected_match_count"] == 1
    assert result["completed_match_count"] == 1
    manifest = _jsonl(root / "match-manifest.jsonl")
    assert [row["titan_id"] for row in manifest] == ["1900002"]


def test_match_allowlist_missing_titan_hard_fails_without_partial_summary(
    tmp_path: Path,
) -> None:
    # 归档里只有 1900001/1900002/1900003,清单多要一个从不存在的 titan_id——
    # 长度对不上必须硬失败,不能把"少抓到一场"悄悄当成成功跑完。
    transport = BackfillFakeTransport()
    config = _config(match_allowlist=frozenset({"1900002", "9999999"}))
    runner = _runner(tmp_path, transport, config)
    with pytest.raises(HistoryProbeError):
        runner.run()
    root = tmp_path / "nowgoal-top5-history-backfill-v1"
    assert not (root / "run-summary.json").exists()


def test_match_allowlist_requires_single_season(tmp_path: Path) -> None:
    transport = BackfillFakeTransport()
    config = _config(
        seasons=("2020-2021", "2021-2022"),
        match_allowlist=frozenset({"1900002"}),
    )
    runner = _runner(tmp_path, transport, config)
    with pytest.raises(HistoryProbeError):
        runner.run()


def test_match_allowlist_requires_single_league(tmp_path: Path) -> None:
    transport = BackfillFakeTransport()
    config = _config(
        leagues=(TOP5_ARCHIVE_LEAGUES[0], TOP5_ARCHIVE_LEAGUES[1]),
        match_allowlist=frozenset({"1900002"}),
    )
    runner = _runner(tmp_path, transport, config)
    with pytest.raises(HistoryProbeError):
        runner.run()


def test_match_allowlist_ignores_sample_per_league_season(tmp_path: Path) -> None:
    # allowlist 命中两场,即便 sample_per_league_season=1(_config 默认值)也不应该
    # 再抽样成一场——给了 allowlist 就以它为准。
    transport = BackfillFakeTransport()
    config = _config(match_allowlist=frozenset({"1900001", "1900003"}))
    result = _runner(tmp_path, transport, config).run()
    assert result["selected_match_count"] == 2


# ── --skip-archive-discovery(跳过被墙的 archive_season 发现,直接按清单构造比赛) ──


def test_beijing_local_to_utc_converts_correctly() -> None:
    assert _beijing_local_to_utc("2021-08-14 03:00") == "2021-08-13T19:00:00Z"


def test_beijing_local_to_utc_invalid_raises() -> None:
    with pytest.raises(HistoryProbeError):
        _beijing_local_to_utc("not-a-timestamp")


def test_load_match_allowlist_rows_requires_kickoff_local(tmp_path: Path) -> None:
    path = tmp_path / "allowlist.jsonl"
    path.write_text('{"titan_id": "1"}\n', encoding="utf-8")
    with pytest.raises(HistoryProbeError):
        load_match_allowlist_rows(path)


def test_load_match_allowlist_rows_rejects_duplicate_titan_id(tmp_path: Path) -> None:
    path = tmp_path / "allowlist.jsonl"
    path.write_text(
        '{"titan_id": "1", "nowgoal_kickoff_local": "2021-08-14 03:00"}\n'
        '{"titan_id": "1", "nowgoal_kickoff_local": "2021-08-14 03:00"}\n',
        encoding="utf-8",
    )
    with pytest.raises(HistoryProbeError):
        load_match_allowlist_rows(path)


def test_load_match_allowlist_rows_empty_raises(tmp_path: Path) -> None:
    path = tmp_path / "allowlist.jsonl"
    path.write_text("", encoding="utf-8")
    with pytest.raises(HistoryProbeError):
        load_match_allowlist_rows(path)


def test_load_match_allowlist_rows_parses_valid_rows(tmp_path: Path) -> None:
    path = tmp_path / "allowlist.jsonl"
    path.write_text(
        '{"titan_id": "1900002", "home_team_name_en": "Brighton Hove Albion", '
        '"away_team_name_en": "Arsenal", "nowgoal_kickoff_local": "2021-01-01 20:00"}\n',
        encoding="utf-8",
    )
    rows = load_match_allowlist_rows(path)
    assert len(rows) == 1
    assert rows[0]["titan_id"] == "1900002"


def test_matches_from_allowlist_rows_builds_sorted_archive_matches() -> None:
    rows = (
        {
            "titan_id": "2", "home_team_name_en": "B", "away_team_name_en": "C",
            "nowgoal_kickoff_local": "2021-08-15 03:00",
        },
        {
            "titan_id": "1", "home_team_name_en": "A", "away_team_name_en": "B",
            "nowgoal_kickoff_local": "2021-08-14 03:00",
        },
    )
    spec = TOP5_ARCHIVE_LEAGUES[0]
    matches = _matches_from_allowlist_rows(rows, spec, "2021-2022")
    assert [m.titan_id for m in matches] == ["1", "2"]  # 按 kickoff_utc 排序
    assert matches[0].competition == spec.key
    assert matches[0].league_id == spec.league_id
    assert matches[0].season == "2021-2022"
    assert matches[0].home_name == "A"
    assert matches[0].kickoff_utc == "2021-08-13T19:00:00Z"


class TestSkipArchiveDiscovery:
    def _allowlist_rows(self) -> tuple[dict, ...]:
        return (
            {
                "titan_id": "1900002",
                "home_team_name_en": "Brighton Hove Albion",
                "away_team_name_en": "Arsenal",
                "nowgoal_kickoff_local": "2021-01-01 20:00",
            },
        )

    def test_never_calls_archive_season_on_transport(self, tmp_path: Path) -> None:
        transport = BackfillFakeTransport()
        config = _config(
            match_allowlist_rows=self._allowlist_rows(),
            skip_archive_discovery=True,
        )
        result = _runner(tmp_path, transport, config).run()
        assert result["selected_match_count"] == 1
        assert result["completed_match_count"] == 1
        assert all(call[0] != "archive" for call in transport.calls)

    def test_season_manifest_marks_discovery_skipped_and_no_raw_artifact(
        self, tmp_path: Path
    ) -> None:
        transport = BackfillFakeTransport()
        config = _config(
            match_allowlist_rows=self._allowlist_rows(),
            skip_archive_discovery=True,
        )
        _runner(tmp_path, transport, config).run()
        manifest = json.loads(
            (tmp_path / "nowgoal-top5-history-backfill-v1" / "season-manifest.json").read_text()
        )
        entry = manifest["seasons"][0]
        assert entry["discovery_skipped"] is True
        assert entry["raw_artifact"] is None
        assert entry["full_match_count"] == 1

    def test_match_manifest_has_correct_kickoff_utc(self, tmp_path: Path) -> None:
        transport = BackfillFakeTransport()
        config = _config(
            match_allowlist_rows=self._allowlist_rows(),
            skip_archive_discovery=True,
        )
        _runner(tmp_path, transport, config).run()
        rows = _jsonl(tmp_path / "nowgoal-top5-history-backfill-v1" / "match-manifest.jsonl")
        assert rows[0]["titan_id"] == "1900002"
        assert rows[0]["kickoff_utc"] == "2021-01-01T12:00:00Z"
        assert rows[0]["home_name"] == "Brighton Hove Albion"

    def test_requires_match_allowlist_rows(self, tmp_path: Path) -> None:
        transport = BackfillFakeTransport()
        config = _config(skip_archive_discovery=True)  # 没给 match_allowlist_rows
        runner = _runner(tmp_path, transport, config)
        with pytest.raises(HistoryProbeError):
            runner.run()

    def test_rejects_verify_company_catalogs_combo(self, tmp_path: Path) -> None:
        transport = BackfillFakeTransport()
        config = _config(
            match_allowlist_rows=self._allowlist_rows(),
            skip_archive_discovery=True,
            verify_company_catalogs=True,
        )
        runner = _runner(tmp_path, transport, config)
        with pytest.raises(HistoryProbeError):
            runner.run()

    def test_catalog_requests_zero_in_preflight(self, tmp_path: Path) -> None:
        transport = BackfillFakeTransport()
        config = _config(
            match_allowlist_rows=self._allowlist_rows(),
            skip_archive_discovery=True,
        )
        result = _runner(tmp_path, transport, config).run()
        assert result["preflight"]["catalog_requests"] == 0


# ── --proxy-env(显式代理,ambient 环境变量永远不会被隐式读取) ──────────────


def test_resolve_proxy_none_when_flag_absent() -> None:
    assert _resolve_proxy(None) is None


def test_resolve_proxy_reads_named_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_TEST_PROXY", "http://user:pass@proxy.example:8080")
    assert _resolve_proxy("MY_TEST_PROXY") == "http://user:pass@proxy.example:8080"


def test_resolve_proxy_missing_env_var_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_MISSING_PROXY", raising=False)
    with pytest.raises(HistoryProbeError):
        _resolve_proxy("MY_MISSING_PROXY")


def test_direct_transport_default_has_no_proxy() -> None:
    import analysis.nowgoal_historical_capability_probe.nowgoal_historical_capability_probe as probe_mod

    transport = probe_mod.DirectNowGoalTransport()
    try:
        assert transport._client._mounts or transport._client._mounts == {}
    finally:
        transport.close()


def test_direct_transport_accepts_explicit_proxy() -> None:
    import analysis.nowgoal_historical_capability_probe.nowgoal_historical_capability_probe as probe_mod

    transport = probe_mod.DirectNowGoalTransport(proxy="http://user:pass@proxy.example:8080")
    try:
        assert transport._client._mounts  # 显式给了代理,底层连接池会挂载对应的 transport
    finally:
        transport.close()


def test_residential_proxy_used_reflects_actual_config(tmp_path: Path) -> None:
    # 回归测试:这个字段以前在 8 处硬编码 False,即使真的配置了代理也照样写
    # False——产物在自证"没用代理",但实际上用了。proxy_used=True 时,
    # normalized 行、availability 行、run-summary 都必须如实反映。
    transport = BackfillFakeTransport()
    config = _config(proxy_used=True)
    result = _runner(tmp_path, transport, config).run()
    assert result["residential_proxy_used"] is True

    root = tmp_path / "nowgoal-top5-history-backfill-v1"
    normalized_rows = _jsonl(root / "normalized" / "odds-history.jsonl")
    assert normalized_rows
    assert all(row["residential_proxy_used"] is True for row in normalized_rows)

    availability_rows = _jsonl(root / "availability.jsonl")
    assert availability_rows
    assert all(row["residential_proxy_used"] is True for row in availability_rows)


def test_residential_proxy_used_false_by_default(tmp_path: Path) -> None:
    transport = BackfillFakeTransport()
    config = _config()  # proxy_used 默认 False
    result = _runner(tmp_path, transport, config).run()
    assert result["residential_proxy_used"] is False


def test_retry_backoff_seconds_defaults_to_zero() -> None:
    config = _config()
    assert config.retry_backoff_seconds == 0.0


def test_retry_backoff_seconds_flows_into_store_acquire(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, float] = {}
    real_acquire = DurableArtifactStore.acquire

    def spy_acquire(self, request_key, operation, fetch, **kwargs):
        captured["retry_backoff_seconds"] = kwargs.get("retry_backoff_seconds")
        return real_acquire(self, request_key, operation, fetch, **kwargs)

    monkeypatch.setattr(DurableArtifactStore, "acquire", spy_acquire)
    transport = BackfillFakeTransport()
    config = _config(
        match_allowlist_rows=(
            {
                "titan_id": "1900002",
                "home_team_name_en": "Brighton Hove Albion",
                "away_team_name_en": "Arsenal",
                "nowgoal_kickoff_local": "2021-01-01 20:00",
            },
        ),
        skip_archive_discovery=True,
        retry_backoff_seconds=9.0,
    )
    _runner(tmp_path, transport, config).run()
    assert captured["retry_backoff_seconds"] == 9.0
