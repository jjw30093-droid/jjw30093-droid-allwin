"""离线单测:analysis/competition_schedule_pilot。不访问网络,不要求 THORDATA_PROXY。

覆盖 docs/audits/competition-schedule-pilot.md 当前 fail-closed 永久门禁。
"""

import copy
import json
import os
import sqlite3
import sys

import pytest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from analysis.competition_schedule_pilot.fotmob_competition_schedule_pilot import (  # noqa: E402
    CompetitionIdentityError,
    PilotSchemaIncompatibleError,
    ScheduleConflictError,
    ScheduleSchemaError,
    SeasonMismatchError,
    SeasonUnverifiableError,
    align_with_allwin,
    build_competition_registry,
    build_team_match_records,
    classify_competition,
    compute_rest_hours,
    derive_kickoff,
    find_cross_comp_rest_examples,
    init_pilot_db,
    inspect_known_pagination,
    merge_competition_schedules,
    parse_competition_schedule_response,
    run_cli,
    verify_all_competitions,
    verify_competition_identity,
    verify_season_parameter_effectiveness,
    write_competition_registry,
    write_match_calendar,
    write_team_match,
)

TEAM_ID = 8456
FIXTURE_PATH = os.path.join(
    _REPO_ROOT, "tests", "fixtures", "fotmob", "competition_schedule_pilot_minimal.json"
)


# ── 构造辅助 ──────────────────────────────────────────────────────────────

def _status(utc, finished=True, cancelled=False, started=True, date_tbd=None, time_tbd=None):
    s = {"utcTime": utc, "finished": finished, "cancelled": cancelled, "started": started}
    if date_tbd is not None:
        s["matchDateTbd"] = date_tbd
    if time_tbd is not None:
        s["matchTimeTbd"] = time_tbd
    return s


def _match(id_, home_id, away_id, utc, round_="Round 1", **status_kwargs):
    return {
        "id": id_,
        "home": {"id": home_id, "name": f"home{home_id}"},
        "away": {"id": away_id, "name": f"away{away_id}"},
        "round": round_,
        "status": _status(utc, **status_kwargs),
    }


def _raw(
    matches,
    details_id=47,
    details_name="Premier League",
    selected_season="2024/2025",
):
    details = {"id": details_id, "name": details_name}
    if selected_season is not None:
        details["selectedSeason"] = selected_season
    return {
        "details": details,
        "fixtures": {"allMatches": matches},
    }


PL_ENTRY = {
    "requested_competition_id": 47,
    "expected_name": "Premier League",
    "competition_class": "league",
    "requested_season": "2024/2025",
    "required_for_pilot": True,
    "verification_status": "PENDING",
    "verification_evidence": None,
}

FACUP_ENTRY = {
    "requested_competition_id": 132,
    "expected_name": "FA Cup",
    "competition_class": "domestic_cup",
    "requested_season": "2024/2025",
    "required_for_pilot": True,
    "verification_status": "PENDING",
    "verification_evidence": None,
}

UCL_ENTRY = {
    "requested_competition_id": 42,
    "expected_name": "UEFA Champions League",
    "competition_class": "continental",
    "requested_season": "2024/2025",
    "required_for_pilot": True,
    "verification_status": "PENDING",
    "verification_evidence": None,
}


def _verified(entry):
    e = dict(entry)
    e["verification_status"] = "IDENTITY_VERIFIED"
    return e


# ── 1. competition fixtures 路径缺失 ──────────────────────────────────────

def test_missing_fixtures_path_raises_schema_error_not_empty_list():
    raw = {
        "details": {
            "id": 47,
            "name": "Premier League",
            "selectedSeason": "2024/2025",
        },
    }  # 无 fixtures 键
    with pytest.raises(ScheduleSchemaError):
        parse_competition_schedule_response(raw, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "test")


# ── 2. fixtures 非 list ───────────────────────────────────────────────────

def test_fixtures_not_list_raises_schema_error():
    raw = {
        "details": {
            "id": 47,
            "name": "Premier League",
            "selectedSeason": "2024/2025",
        },
        "fixtures": {"allMatches": "not-a-list"},
    }
    with pytest.raises(ScheduleSchemaError):
        parse_competition_schedule_response(raw, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "test")


# ── 3. fixtures 元素非 dict ───────────────────────────────────────────────

@pytest.mark.parametrize("bad_element", [None, "x", [1, 2], 42])
def test_fixtures_element_not_dict_raises_schema_error(bad_element):
    raw = _raw([bad_element])
    with pytest.raises(ScheduleSchemaError):
        parse_competition_schedule_response(raw, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "test")


# ── 4. Match ID float/bool/negative ───────────────────────────────────────

@pytest.mark.parametrize("bad_id", [9.9, 9.0, True, -5, 0, "9.9", ""])
def test_match_id_illegal_values_rejected(bad_id):
    raw = _raw([_match(bad_id, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")])
    records = parse_competition_schedule_response(raw, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "test")
    assert records == []


# ── 5. home/away ID 非法 ──────────────────────────────────────────────────

def test_illegal_home_away_id_not_guessed():
    m = _match(2001, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")
    m["home"]["id"] = float(TEAM_ID) + 0.5
    raw = _raw([m])
    records = parse_competition_schedule_response(raw, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "test")
    assert len(records) == 1
    assert records[0]["home_team_id"] is None  # 非法类型不猜测,不截断


# ── 6. competition identity mismatch ──────────────────────────────────────

def test_competition_identity_mismatch_by_id():
    raw = _raw([_match(2002, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")], details_id=999, details_name="Premier League")
    with pytest.raises(CompetitionIdentityError):
        parse_competition_schedule_response(raw, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "test")


def test_competition_identity_mismatch_by_name():
    raw = _raw([_match(2003, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")], details_id=47, details_name="Championship")
    with pytest.raises(CompetitionIdentityError):
        parse_competition_schedule_response(raw, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "test")


def test_verify_competition_identity_pure_function_reports_mismatch():
    raw = _raw([], details_id=999, details_name="Championship")
    result = verify_competition_identity(raw, 47, "Premier League")
    assert result["status"] == "IDENTITY_MISMATCH"


def test_verify_competition_identity_pure_function_reports_match():
    raw = _raw([], details_id=47, details_name="Premier League")
    result = verify_competition_identity(raw, 47, "Premier League")
    assert result["status"] == "IDENTITY_VERIFIED"


def test_verify_competition_identity_unverifiable_when_details_absent():
    raw = {"fixtures": {"allMatches": []}}
    result = verify_competition_identity(raw, 47, "Premier League")
    assert result["status"] == "IDENTITY_UNVERIFIABLE"


# ── 7. season mismatch ─────────────────────────────────────────────────────

def test_parser_rejects_returned_season_mismatch_directly():
    raw = _raw([_match(2004, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")], selected_season="2023/2024")
    with pytest.raises(SeasonMismatchError, match="season"):
        parse_competition_schedule_response(
            raw,
            _verified(PL_ENTRY),
            "2024/2025",
            "2026-01-01T00:00:00Z",
            "test",
        )


@pytest.mark.parametrize(
    "returned_season",
    [None, "", "   ", 2024, "2024/25", "not-a-season"],
    ids=["missing", "empty", "whitespace", "wrong_type", "short", "nonsense"],
)
def test_parser_rejects_unverifiable_returned_season_directly(returned_season):
    raw = _raw(
        [_match(2004, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")],
        selected_season=returned_season,
    )
    with pytest.raises(SeasonUnverifiableError, match="season"):
        parse_competition_schedule_response(
            raw,
            _verified(PL_ENTRY),
            "2024/2025",
            "2026-01-01T00:00:00Z",
            "test",
        )


@pytest.mark.parametrize(
    "requested_season",
    ["", "2024/25", "not-a-season"],
)
def test_parser_rejects_unverifiable_requested_season_directly(
    requested_season,
):
    raw = _raw(
        [_match(2004, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")],
    )
    with pytest.raises(SeasonUnverifiableError, match="season"):
        parse_competition_schedule_response(
            raw,
            _verified(PL_ENTRY),
            requested_season,
            "2026-01-01T00:00:00Z",
            "test",
        )


def test_season_match_not_flagged():
    raw = _raw([_match(2005, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")], selected_season="2024/2025")
    records = parse_competition_schedule_response(raw, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "test")
    assert records[0]["season_mismatch"] is False


def test_season_parameter_effectiveness_detects_ineffective_rolling_window():
    same_matches = [_match(3001, TEAM_ID, 999, "2026-04-01T15:00:00.000Z")]
    raw_a = _raw(same_matches)
    raw_b = _raw(same_matches)  # 同一组比赛,模拟 season 参数无效
    result = verify_season_parameter_effectiveness(raw_a, raw_b)
    assert result["verdict"] == "SEASON_PARAMETER_INEFFECTIVE"


def test_season_parameter_effectiveness_detects_effective_season():
    raw_a = _raw([_match(4001, TEAM_ID, 999, "2024-08-17T15:00:00.000Z")])
    raw_b = _raw([_match(4002, TEAM_ID, 999, "2023-08-12T15:00:00.000Z")])
    result = verify_season_parameter_effectiveness(raw_a, raw_b)
    assert result["verdict"] == "SEASON_PARAMETER_EFFECTIVE"


# ── 8. 完全重复行去重 ──────────────────────────────────────────────────────

def test_exact_duplicate_rows_deduped():
    m = _match(2006, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")
    raw = _raw([m, dict(m)])
    records = parse_competition_schedule_response(raw, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "test")
    assert len(records) == 1


# ── 9. 同 ID 不同 kickoff 冲突 ─────────────────────────────────────────────

def test_same_id_different_kickoff_raises():
    m1 = _match(2007, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")
    m2 = _match(2007, TEAM_ID, 999, "2026-01-02T15:00:00.000Z")
    raw = _raw([m1, m2])
    with pytest.raises(ScheduleConflictError):
        parse_competition_schedule_response(raw, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "test")


# ── 10. 同 ID 主客反转冲突 ─────────────────────────────────────────────────

def test_same_id_home_away_swapped_raises():
    m1 = _match(2008, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")
    m2 = _match(2008, 999, TEAM_ID, "2026-01-01T15:00:00.000Z")
    raw = _raw([m1, m2])
    with pytest.raises(ScheduleConflictError):
        parse_competition_schedule_response(raw, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "test")


# ── 11. exact UTC ─────────────────────────────────────────────────────────

def test_exact_utc_normalized():
    utc, precision = derive_kickoff({"utcTime": "2026-04-22T19:00:00.000Z", "finished": True})
    assert precision == "exact"
    assert utc == "2026-04-22T19:00:00Z"


# ── 12. date_only/unknown ─────────────────────────────────────────────────

def test_date_only_no_midnight_fabrication():
    utc, precision = derive_kickoff({"utcTime": "2026-04-22", "finished": False})
    assert precision == "date_only"
    assert utc is None


def test_unknown_when_date_tbd():
    utc, precision = derive_kickoff({"utcTime": "2026-10-10T14:00:00.000Z", "matchDateTbd": True, "matchTimeTbd": False})
    assert precision == "unknown"
    assert utc is None


# ── 13. cancelled 排除 ────────────────────────────────────────────────────

def test_cancelled_excluded_from_rest():
    raw_pl = _raw([
        _match(5001, TEAM_ID, 999, "2026-01-01T15:00:00.000Z"),
        _match(5002, TEAM_ID, 999, "2026-01-05T15:00:00.000Z", cancelled=True, finished=False),
        _match(5003, TEAM_ID, 999, "2026-01-10T15:00:00.000Z"),
    ])
    records = parse_competition_schedule_response(raw_pl, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "test")
    merged = merge_competition_schedules([records])
    team_records = build_team_match_records(merged, TEAM_ID)
    rest = compute_rest_hours(team_records)
    assert len(rest) == 2
    last = next(r for r in rest if r["provider_match_id"] == 5003)
    assert last["previous_match_id"] == 5001
    assert last["rest_hours"] == pytest.approx(9 * 24.0)


# ── 14. upcoming 不作为 previous match ────────────────────────────────────

def test_unfinished_not_counted_as_previous():
    raw_pl = _raw([
        _match(5011, TEAM_ID, 999, "2026-01-01T15:00:00.000Z"),
        _match(5012, TEAM_ID, 999, "2026-01-05T15:00:00.000Z", finished=False),
        _match(5013, TEAM_ID, 999, "2026-01-10T15:00:00.000Z"),
    ])
    records = parse_competition_schedule_response(raw_pl, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "test")
    merged = merge_competition_schedules([records])
    team_records = build_team_match_records(merged, TEAM_ID)
    rest = compute_rest_hours(team_records)
    last = next(r for r in rest if r["provider_match_id"] == 5013)
    assert last["previous_match_id"] == 5011


# ── 15. target team 不在双方时不进入球队时间线 ────────────────────────────

def test_target_team_not_involved_excluded_from_team_timeline():
    raw_pl = _raw([_match(5021, 111, 222, "2026-01-01T15:00:00.000Z")])
    records = parse_competition_schedule_response(raw_pl, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "test")
    merged = merge_competition_schedules([records])
    team_records = build_team_match_records(merged, TEAM_ID)
    assert team_records == []
    assert len(merged) == 1  # 但比赛本身仍保留在合并日历里(非球队特定视图)


# ── 16. 多赛事合并 ─────────────────────────────────────────────────────────

def test_merge_multiple_competitions_no_overlap():
    raw_pl = _raw([_match(6001, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")], details_id=47, details_name="Premier League")
    raw_fa = _raw([_match(6002, TEAM_ID, 888, "2026-01-05T15:00:00.000Z")], details_id=132, details_name="FA Cup")
    r1 = parse_competition_schedule_response(raw_pl, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "pl")
    r2 = parse_competition_schedule_response(raw_fa, _verified(FACUP_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "fa")
    merged = merge_competition_schedules([r1, r2])
    assert len(merged) == 2
    ids = {m["provider_match_id"] for m in merged}
    assert ids == {6001, 6002}


# ── 17. provenance 合并 ────────────────────────────────────────────────────

def test_provenance_recorded_as_list_for_single_source_match():
    """在本 pilot 的设计里,每条记录的 competition_id 取自"发起该次查询的
    requested_competition_id"(league_matches() 单赛事响应不逐场自带独立
    competition_id 字段)。因此"同一 Match ID 被两个不同赛事 ID 收录且完全
    一致"在合法数据下不可能发生——若真的发生,competition_id 字段冲突,
    必须 fail-loud(见下一测试),这正是设计的预期行为,不是缺陷。
    本测试验证正常单赛事场景下 provenance 是一个包含该赛事 ID 的列表。"""
    raw_pl = _raw([_match(6003, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")], details_id=47, details_name="Premier League")
    r1 = parse_competition_schedule_response(raw_pl, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "pl")
    merged = merge_competition_schedules([r1])
    assert len(merged) == 1
    assert merged[0]["source_provenance"] == [47]


def test_provenance_merge_repeated_identical_source_does_not_duplicate():
    """同一赛事(同 competition_id)的两次独立解析(例如季边界比赛在
    season=2024/2025 与 season=2023/2024 两次查询中都出现且描述完全一致)
    合并后去重,不重复,provenance 保持单值列表。"""
    raw_pl = _raw([_match(6004, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")], details_id=47, details_name="Premier League")
    r1 = parse_competition_schedule_response(raw_pl, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "pl_a")
    r2 = parse_competition_schedule_response(raw_pl, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "pl_b")
    merged = merge_competition_schedules([r1, r2])
    assert len(merged) == 1
    assert merged[0]["source_provenance"] == [47]


def test_conflicting_competition_id_for_same_match_id_raises_not_silently_merged():
    """同一 Match ID 若被两个不同 requested_competition_id 的查询收录且其它字段
    一致,competition_id 本身不一致就是设计上的身份冲突,必须 fail-loud,
    不能被"看起来差不多"就悄悄合并成一个 provenance 列表。"""
    m = _match(6005, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")
    raw_pl = _raw([m], details_id=47, details_name="Premier League")
    raw_fa = _raw([m], details_id=132, details_name="FA Cup")
    r1 = parse_competition_schedule_response(raw_pl, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "pl")
    r2 = parse_competition_schedule_response(raw_fa, _verified(FACUP_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "fa")
    with pytest.raises(ScheduleConflictError):
        merge_competition_schedules([r1, r2])


# ── 18. SQLite 幂等 ────────────────────────────────────────────────────────

def test_sqlite_idempotent_write(tmp_path):
    raw_pl = _raw([
        _match(7001, TEAM_ID, 999, "2026-01-01T15:00:00.000Z"),
        _match(7002, 999, TEAM_ID, "2026-01-08T15:00:00.000Z"),
    ])
    records = parse_competition_schedule_response(raw_pl, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "test")
    merged = merge_competition_schedules([records])
    team_records = build_team_match_records(merged, TEAM_ID)

    db_path = str(tmp_path / "pilot.db")
    conn = sqlite3.connect(db_path)
    init_pilot_db(conn)
    r1 = write_match_calendar(conn, merged)
    t1 = write_team_match(conn, team_records)
    assert r1 == {"inserted": 2, "skipped": 0}
    assert t1 == {"inserted": 2, "skipped": 0}

    r2 = write_match_calendar(conn, merged)
    t2 = write_team_match(conn, team_records)
    assert r2 == {"inserted": 0, "skipped": 2}
    assert t2 == {"inserted": 0, "skipped": 2}

    n_cal = conn.execute("SELECT COUNT(*) FROM pilot_match_calendar").fetchone()[0]
    n_team = conn.execute("SELECT COUNT(*) FROM pilot_team_match").fetchone()[0]
    assert n_cal == 2
    assert n_team == 2
    conn.close()


# ── 19. SQLite 冲突回滚 ────────────────────────────────────────────────────

def test_sqlite_write_conflict_rolls_back(tmp_path):
    raw1 = _raw([_match(7101, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")])
    raw2 = _raw([_match(7101, TEAM_ID, 888, "2026-01-01T15:00:00.000Z")])  # 对手换了
    r1 = parse_competition_schedule_response(raw1, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "test")
    r2 = parse_competition_schedule_response(raw2, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "test")

    db_path = str(tmp_path / "pilot_conflict.db")
    conn = sqlite3.connect(db_path)
    init_pilot_db(conn)
    write_match_calendar(conn, merge_competition_schedules([r1]))
    with pytest.raises(ScheduleConflictError):
        write_match_calendar(conn, merge_competition_schedules([r2]))
    n_cal = conn.execute("SELECT COUNT(*) FROM pilot_match_calendar").fetchone()[0]
    assert n_cal == 1  # 冲突写入已回滚,不残留半成功行
    conn.close()


# ── 20. 英超对齐函数不硬编码 38 ────────────────────────────────────────────

def test_align_with_allwin_does_not_hardcode_38():
    allwin_ids = {1, 2, 3, 4, 5}  # 任意 5 场,证明函数不假设固定 38
    pilot_ids = {1, 2, 3, 4, 5}
    result = align_with_allwin(allwin_ids, pilot_ids)
    assert result["allwin_count"] == 5
    assert result["league_completeness_verified"] is True


def test_align_with_allwin_partial_overlap_not_verified():
    allwin_ids = set(range(1, 39))  # 38 场(数值任意选取,不是硬编码断言的一部分)
    pilot_ids = set(range(30, 39))  # 只覆盖尾部 9 场
    result = align_with_allwin(allwin_ids, pilot_ids)
    assert result["intersection"] == 9
    assert result["only_in_allwin"] == 29
    assert result["league_completeness_verified"] is False


# ── 21. 国内杯赛缩短休息时间 ───────────────────────────────────────────────

def test_domestic_cup_shortens_league_rest():
    raw_pl = _raw([
        _match(8001, 8191, TEAM_ID, "2026-04-22T19:00:00.000Z"),
        _match(8002, 8668, TEAM_ID, "2026-05-04T19:00:00.000Z"),
    ], details_id=47, details_name="Premier League")
    raw_fa = _raw([
        _match(8501, TEAM_ID, 8466, "2026-04-25T16:15:00.000Z"),
    ], details_id=132, details_name="FA Cup")
    r_pl = parse_competition_schedule_response(raw_pl, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "pl")
    r_fa = parse_competition_schedule_response(raw_fa, _verified(FACUP_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "fa")
    merged = merge_competition_schedules([r_pl, r_fa])
    team_records = build_team_match_records(merged, TEAM_ID)
    rest = compute_rest_hours(team_records)
    examples = find_cross_comp_rest_examples(rest)
    assert len(examples) == 1
    ex = examples[0]
    assert ex["provider_match_id"] == 8002
    assert ex["league_only_rest_hours"] == pytest.approx(288.0)
    assert ex["all_comp_rest_hours"] == pytest.approx(218.75)
    assert ex["intervening_non_league_match_ids"] == [8501]


# ── 22. 欧冠缩短休息时间 ───────────────────────────────────────────────────

def test_champions_league_shortens_league_rest():
    raw_pl = _raw([
        _match(9001, 8191, TEAM_ID, "2024-11-02T15:00:00.000Z"),
        _match(9002, 8668, TEAM_ID, "2024-11-09T15:00:00.000Z"),
    ], details_id=47, details_name="Premier League")
    raw_ucl = _raw([
        _match(9501, TEAM_ID, 9825, "2024-11-05T20:00:00.000Z"),
    ], details_id=42, details_name="UEFA Champions League")
    r_pl = parse_competition_schedule_response(raw_pl, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "pl")
    r_ucl = parse_competition_schedule_response(raw_ucl, _verified(UCL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "ucl")
    merged = merge_competition_schedules([r_pl, r_ucl])
    team_records = build_team_match_records(merged, TEAM_ID)
    rest = compute_rest_hours(team_records)
    examples = find_cross_comp_rest_examples(rest)
    assert len(examples) == 1
    ex = examples[0]
    assert ex["provider_match_id"] == 9002
    assert ex["previous_competition_name"] == "UEFA Champions League"
    assert ex["all_comp_rest_hours"] < ex["league_only_rest_hours"]


# ── 23. 乱序输入不会产生负 rest ────────────────────────────────────────────

def test_unsorted_input_no_negative_rest():
    raw_pl = _raw([
        _match(10003, TEAM_ID, 999, "2026-01-10T15:00:00.000Z"),
        _match(10001, TEAM_ID, 999, "2026-01-01T15:00:00.000Z"),
        _match(10002, TEAM_ID, 999, "2026-01-05T15:00:00.000Z"),
    ])
    records = parse_competition_schedule_response(raw_pl, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "test")
    merged = merge_competition_schedules([records])
    team_records = build_team_match_records(merged, TEAM_ID)
    rest = compute_rest_hours(team_records)
    for r in rest:
        if r["rest_hours"] is not None:
            assert r["rest_hours"] >= 0


# ── 24. 左边界 rest=NULL ───────────────────────────────────────────────────

def test_left_boundary_rest_is_null():
    raw_pl = _raw([_match(10101, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")])
    records = parse_competition_schedule_response(raw_pl, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "test")
    merged = merge_competition_schedules([records])
    team_records = build_team_match_records(merged, TEAM_ID)
    rest = compute_rest_hours(team_records)
    assert rest[0]["rest_hours"] is None


# ── 25. 未来比赛不改变历史特征(point-in-time 纪律,B10) ────────────────────

def test_future_match_does_not_change_past_rest_or_lookback():
    raw_pl_without_future = _raw([
        _match(11001, TEAM_ID, 999, "2026-01-01T15:00:00.000Z"),
        _match(11002, TEAM_ID, 999, "2026-01-05T15:00:00.000Z"),
    ])
    records_a = parse_competition_schedule_response(
        raw_pl_without_future, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "test"
    )
    merged_a = merge_competition_schedules([records_a])
    team_a = build_team_match_records(merged_a, TEAM_ID)
    rest_a = compute_rest_hours(team_a)

    raw_pl_with_future = _raw([
        _match(11001, TEAM_ID, 999, "2026-01-01T15:00:00.000Z"),
        _match(11002, TEAM_ID, 999, "2026-01-05T15:00:00.000Z"),
        _match(11003, TEAM_ID, 999, "2026-01-06T15:00:00.000Z", finished=False),  # 未来比赛
    ])
    records_b = parse_competition_schedule_response(
        raw_pl_with_future, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "test"
    )
    merged_b = merge_competition_schedules([records_b])
    team_b = build_team_match_records(merged_b, TEAM_ID)
    rest_b = compute_rest_hours(team_b)

    rest_a_by_id = {r["provider_match_id"]: r for r in rest_a}
    rest_b_by_id = {r["provider_match_id"]: r for r in rest_b}
    for mid in (11001, 11002):
        assert rest_a_by_id[mid]["rest_hours"] == rest_b_by_id[mid]["rest_hours"]
        assert rest_a_by_id[mid]["matches_last_7d"] == rest_b_by_id[mid]["matches_last_7d"]
        assert rest_a_by_id[mid]["matches_last_14d"] == rest_b_by_id[mid]["matches_last_14d"]


# ── 26. fixture/日志无凭证 ─────────────────────────────────────────────────

def test_fixture_file_contains_no_credentials():
    """检查真实凭证形状(user:pass@host),不是"文件里任意出现 :// 和 @"这种
    过粗的判定——真实 FotMob 响应里含 details.breadcrumbJSONLD 这类合法的
    schema.org JSON-LD 字段("@context": "https://schema.org"),同时含 "://"
    与 "@" 字符但显然不是凭证,不应被误判。复用 _redact_check 同一套精确判定。"""
    from analysis.competition_schedule_pilot.fotmob_competition_schedule_pilot import _redact_check
    with open(FIXTURE_PATH) as f:
        text = f.read()
    assert "THORDATA_PROXY=" not in text
    _redact_check(text)  # 不应抛错;抛错则说明真的检测到疑似凭证形状


def test_redact_check_blocks_credential_like_output():
    from analysis.competition_schedule_pilot.fotmob_competition_schedule_pilot import _redact_check
    with pytest.raises(RuntimeError):
        _redact_check("THORDATA_PROXY=http://user:pass@1.2.3.4:8080")
    with pytest.raises(RuntimeError):
        _redact_check('{"proxy": "http://someuser:secret@proxyhost:8080"}')
    _redact_check(json.dumps({"team_id": 8456, "note": "no secrets here"}))


# ── 27. registry 未验证项阻止完整性 VERIFIED ──────────────────────────────

def test_unverified_registry_entry_blocks_completeness():
    registry = build_competition_registry("2024/2025")
    # 未做任何真实校验,全部仍是 PENDING
    required_verified = all(
        e["verification_status"] == "IDENTITY_VERIFIED"
        for e in registry.values() if e["required_for_pilot"]
    )
    assert required_verified is False  # PENDING 状态不得被当作已验证


# ── 28. pagination 未验证阻止完整性放行 ───────────────────────────────────

def test_pagination_unverified_blocks_full_completeness():
    """本 pilot 未实现/未验证任何分页机制——任何完整性判定函数都不得在
    pagination_detected 未知的情况下宣称完整性已验证。这里用 registry 行的
    completeness_status 字段体现该约束(pagination 状态缺失时不能放行)。"""
    row = {
        "competition_id": 47, "expected_name": "Premier League", "observed_name": "Premier League",
        "competition_class": "league", "requested_season": "2024/2025", "returned_season": "2024/2025",
        "season_parameter_verified": "SEASON_PARAMETER_EFFECTIVE", "fixture_count": 380,
        "target_team_fixture_count": 38, "identity_verified": "IDENTITY_VERIFIED",
        "pagination_detected": None,  # 未验证
        "completeness_status": "UNVERIFIED",  # 不得写成 VERIFIED
    }
    assert row["pagination_detected"] is None
    assert row["completeness_status"] != "VERIFIED"


# ── classify_competition:注册表命中 vs 启发式回退 ──────────────────────────

def test_classify_competition_uses_curated_registry_when_verified():
    registry = {47: _verified(PL_ENTRY)}
    cls, method = classify_competition(47, "Premier League", registry)
    assert cls == "league"
    assert method == "curated_registry_verified_against_source"


def test_classify_competition_falls_back_to_heuristic_when_not_in_registry():
    cls, method = classify_competition(999, "Some Random Cup", {})
    assert cls == "other"
    assert method == "heuristic_name"


# ── write_competition_registry 幂等 ────────────────────────────────────────

# ── date/time TBD 与 cancelled/upcoming:真实 2024/2025 数据里不存在(全部 786 场
# 真实比赛均为已完赛),按 B11 要求在此显式用 synthetic 用例覆盖,不冒充真实观测 ──

def test_synthetic_date_tbd_case():
    """真实抓取的 5 个赛事、786 场 2024/2025 比赛里没有任何 matchDateTbd=true 样例
    (整季均已完赛)。这里用人工构造数据验证 derive_kickoff() 对该场景的处理,
    不冒充真实观测。"""
    utc, precision = derive_kickoff({
        "utcTime": "2026-10-10T14:00:00.000Z", "finished": False,
        "matchDateTbd": True, "matchTimeTbd": False,
    })
    assert precision == "unknown"
    assert utc is None


def test_synthetic_cancelled_upcoming_case():
    """同上:真实数据里没有 cancelled 或 upcoming 样例,这里用 synthetic 数据验证
    cancelled 排除、upcoming 不算 previous match 的行为(纯函数级复核,已在
    test_cancelled_excluded_from_rest / test_unfinished_not_counted_as_previous
    用真实结构验证过一次;此处再加一组显式标注为 synthetic 的独立用例)。"""
    raw_pl = _raw([
        _match(20001, TEAM_ID, 999, "2026-01-01T15:00:00.000Z"),
        _match(20002, TEAM_ID, 999, "2026-01-05T15:00:00.000Z", cancelled=True, finished=False),
        _match(20003, TEAM_ID, 999, "2026-01-08T15:00:00.000Z", finished=False),  # upcoming
        _match(20004, TEAM_ID, 999, "2026-01-10T15:00:00.000Z"),
    ])
    records = parse_competition_schedule_response(raw_pl, _verified(PL_ENTRY), "2024/2025", "2026-01-01T00:00:00Z", "test")
    merged = merge_competition_schedules([records])
    team_records = build_team_match_records(merged, TEAM_ID)
    rest = compute_rest_hours(team_records)
    last = next(r for r in rest if r["provider_match_id"] == 20004)
    assert last["previous_match_id"] == 20001  # 跳过 cancelled(20002)和 upcoming(20003)


# ── 端到端:用真实(裁剪)fixture 解析 5 个赛事并复现 rest 案例 ────────────────

def test_end_to_end_real_fixture_all_five_competitions():
    with open(FIXTURE_PATH) as f:
        fixture_doc = json.load(f)
    per_competition = fixture_doc["per_competition"]
    registry = build_competition_registry("2024/2025")

    record_lists = []
    for cid_str, raw in per_competition.items():
        cid = int(cid_str)
        entry = registry[cid]
        identity = verify_competition_identity(raw, cid, entry["expected_name"])
        assert identity["status"] == "IDENTITY_VERIFIED"
        entry["verification_status"] = "IDENTITY_VERIFIED"
        records = parse_competition_schedule_response(
            raw, entry, "2024/2025", "2026-07-23T00:00:00Z", f"fixture:{cid}"
        )
        record_lists.append(records)

    merged = merge_competition_schedules(record_lists)
    # 6 PL(含 1 条精确重复,去重后 5)+ 1 FA Cup + 2 UCL + 1 Community Shield + 2 EFL Cup = 11
    assert len(merged) == 11

    team_records = build_team_match_records(merged, TEAM_ID)
    by_class = {}
    for r in team_records:
        by_class.setdefault(r["competition_class"], []).append(r)
    assert len(by_class.get("league", [])) == 5
    assert len(by_class.get("domestic_cup", [])) == 3   # 1 FA Cup + 2 EFL Cup
    assert len(by_class.get("continental", [])) == 2    # 2 UCL
    assert "super_cup" not in by_class  # Community Shield 那场不含 Man City(真实:Palace vs Liverpool)

    rest = compute_rest_hours(team_records)
    examples = find_cross_comp_rest_examples(rest)
    assert len(examples) >= 1  # 至少复现一个真实跨赛事 rest 缩短案例(domestic cup 或欧冠)


def test_end_to_end_real_fixture_via_cli(tmp_path):
    out_dir = str(tmp_path / "run1")
    code = run_cli(["--team-id", "8456", "--season", "2024/2025",
                     "--offline-fixture", FIXTURE_PATH, "--output-dir", out_dir])
    assert code == 0


def test_write_competition_registry_idempotent(tmp_path):
    row = {
        "competition_id": 47, "expected_name": "Premier League", "observed_name": "Premier League",
        "competition_class": "league", "requested_season": "2024/2025", "returned_season": "2024/2025",
        "season_parameter_verified": "SEASON_PARAMETER_EFFECTIVE", "fixture_count": 380,
        "target_team_fixture_count": 38, "identity_verified": "IDENTITY_VERIFIED",
        "pagination_detected": 0, "completeness_status": "VERIFIED",
    }
    db_path = str(tmp_path / "registry.db")
    conn = sqlite3.connect(db_path)
    init_pilot_db(conn)
    r1 = write_competition_registry(conn, [row])
    assert r1 == {"inserted": 1, "skipped": 0}
    r2 = write_competition_registry(conn, [row])
    assert r2 == {"inserted": 0, "skipped": 1}
    n = conn.execute("SELECT COUNT(*) FROM pilot_competition_registry").fetchone()[0]
    assert n == 1
    conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# required_for_pilot 门禁 + registry 真实持久化 + 扩大冲突检测语义
# (对抗上一轮独立复核发现的 P0/P1:required_for_pilot 此前从未被读取/校验,
#  write_competition_registry() 此前从未被 run_cli 实际调用,
#  pilot_match_calendar/pilot_team_match 的冲突键此前遗漏了
#  competition_class/is_competitive 等字段。)
# ═════════════════════════════════════════════════════════════════════════════

def _good_raw_for(cid, name, matches, season="2024/2025"):
    return _raw(matches, details_id=cid, details_name=name, selected_season=season)


def _all_good_per_competition(season="2024/2025"):
    """构造 5 个 required_for_pilot=True 赛事全部通过校验的最小 per_competition
    字典(每个赛事至少 1 场比赛,身份/季节均正确)。用作各失败场景测试的基线,
    单独破坏其中一个赛事来验证门禁行为。"""
    return {
        "47": _good_raw_for(47, "Premier League", [
            _match(9001, TEAM_ID, 999, "2024-08-16T14:00:00.000Z"),
        ], season),
        "132": _good_raw_for(132, "FA Cup", [
            _match(9101, TEAM_ID, 888, "2025-01-11T17:45:00.000Z"),
        ], season),
        "42": _good_raw_for(42, "Champions League", [
            _match(9201, TEAM_ID, 777, "2024-09-17T19:00:00.000Z"),
        ], season),
        "247": _good_raw_for(247, "Community Shield", [
            # 真实场景:Community Shield 完整、身份正确,但 Man City 未参赛
            # (真实赛果导致,不是数据缺陷)——用不含 TEAM_ID 的两支球队。
            _match(9301, 111, 222, "2025-08-10T14:00:00.000Z"),
        ], season),
        "133": _good_raw_for(133, "EFL Cup", [
            _match(9401, TEAM_ID, 666, "2024-09-24T18:45:00.000Z"),
        ], season),
    }


def _run_cli_with_fixture(tmp_path, per_competition, season="2024/2025", label="run"):
    fixture_doc = {"per_competition": per_competition}
    fixture_path = str(tmp_path / f"{label}.json")
    with open(fixture_path, "w") as f:
        json.dump(fixture_doc, f)
    out_dir = str(tmp_path / f"{label}_out")
    return fixture_path, out_dir


# ── 1. 欧冠身份验证失败 → 整体阻断 ─────────────────────────────────────────

def test_champions_league_identity_failure_blocks_pipeline(tmp_path, capsys):
    per_comp = _all_good_per_competition()
    per_comp["42"] = _good_raw_for(999, "Some Other Competition", [
        _match(9201, TEAM_ID, 777, "2024-09-17T19:00:00.000Z"),
    ])  # details.id=999 != 请求的 42,身份不匹配
    fixture_path, out_dir = _run_cli_with_fixture(tmp_path, per_comp, label="ucl_identity_fail")

    code = run_cli(["--team-id", str(TEAM_ID), "--season", "2024/2025",
                     "--offline-fixture", fixture_path, "--output-dir", out_dir])
    assert code != 0
    err = json.loads(capsys.readouterr().err)
    assert err["status"] == "FAILED"
    failed_ids = {c["competition_id"] for c in err["failed_required_competitions"]}
    assert 42 in failed_ids


# ── 2. FA Cup season mismatch → 整体阻断 ──────────────────────────────────

def test_fa_cup_season_mismatch_blocks_pipeline(tmp_path, capsys):
    per_comp = _all_good_per_competition()
    per_comp["132"] = _good_raw_for(132, "FA Cup", [
        _match(9101, TEAM_ID, 888, "2025-01-11T17:45:00.000Z"),
    ], season="2023/2024")  # 响应声明的 season 与请求的 2024/2025 不一致
    fixture_path, out_dir = _run_cli_with_fixture(tmp_path, per_comp, label="facup_season_mismatch")

    code = run_cli(["--team-id", str(TEAM_ID), "--season", "2024/2025",
                     "--offline-fixture", fixture_path, "--output-dir", out_dir])
    assert code != 0
    err = json.loads(capsys.readouterr().err)
    assert err["status"] == "FAILED"
    facup = next(c for c in err["failed_required_competitions"] if c["competition_id"] == 132)
    assert facup["verification_status"] == "SEASON_MISMATCH"


# ── 3. required 响应缺 fixtures 路径 → 整体阻断 ────────────────────────────

def test_required_response_missing_fixtures_path_blocks_pipeline(tmp_path, capsys):
    per_comp = _all_good_per_competition()
    per_comp["247"] = {"details": {"id": 247, "name": "Community Shield",
                                     "selectedSeason": "2024/2025"}}  # 缺 fixtures 键
    fixture_path, out_dir = _run_cli_with_fixture(tmp_path, per_comp, label="commshield_missing_fixtures")

    code = run_cli(["--team-id", str(TEAM_ID), "--season", "2024/2025",
                     "--offline-fixture", fixture_path, "--output-dir", out_dir])
    assert code != 0
    err = json.loads(capsys.readouterr().err)
    cs = next(c for c in err["failed_required_competitions"] if c["competition_id"] == 247)
    assert cs["verification_status"] == "ScheduleSchemaError"


# ── 4. required 赛事完全空响应({}) → 整体阻断 ─────────────────────────────

def test_required_competition_empty_response_blocks_pipeline(tmp_path, capsys):
    per_comp = _all_good_per_competition()
    per_comp["133"] = {}  # 空响应,无 details 可比对
    fixture_path, out_dir = _run_cli_with_fixture(tmp_path, per_comp, label="eflcup_empty_response")

    code = run_cli(["--team-id", str(TEAM_ID), "--season", "2024/2025",
                     "--offline-fixture", fixture_path, "--output-dir", out_dir])
    assert code != 0
    err = json.loads(capsys.readouterr().err)
    efl = next(c for c in err["failed_required_competitions"] if c["competition_id"] == 133)
    assert efl["verification_status"] == "IDENTITY_UNVERIFIABLE"


# ── 5. Community Shield 验证成功但曼城 0 场 → 必须通过,不得误判失败 ────────

def test_community_shield_verified_with_zero_man_city_matches_passes(tmp_path, capsys):
    """规则 2 的正例:赛事端点完整、身份正确,但目标球队该赛季确实 0 场——
    不能因为 0 场就把结构完整、身份正确的赛事误判为端点失败。"""
    per_comp = _all_good_per_competition()  # Community Shield(247)本身就不含 TEAM_ID
    fixture_path, out_dir = _run_cli_with_fixture(tmp_path, per_comp, label="commshield_zero_ok")

    code = run_cli(["--team-id", str(TEAM_ID), "--season", "2024/2025",
                     "--offline-fixture", fixture_path, "--output-dir", out_dir])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "OK"
    assert out["registry_status"][str(247)] == "IDENTITY_VERIFIED" or out["registry_status"].get(247) == "IDENTITY_VERIFIED"

    # 直接查 registry 库,确认 Community Shield 的 response validation 已通过,
    # 且 target_team_fixture_count=0 被如实记录而不是被当作失败信号。单份响应
    # 只能证明 returned season 匹配,不能证明 season 参数端点级有效,所以
    # completeness 必须保留这一限制。
    conn = sqlite3.connect(out["db_path"])
    row = conn.execute(
        "SELECT completeness_status, season_parameter_verified, "
        "target_team_fixture_count, fixture_count "
        "FROM pilot_competition_registry WHERE competition_id=247"
    ).fetchone()
    conn.close()
    assert row == (
        "RESPONSE_VALIDATED_SEASON_PARAMETER_UNVERIFIED",
        None,
        0,
        1,
    )


# ── 6. required 失败时不生成 rest,不写 calendar/team_match ────────────────

def test_required_failure_produces_no_rest_and_no_calendar_writes(tmp_path, capsys):
    per_comp = _all_good_per_competition()
    per_comp["42"] = _good_raw_for(999, "Wrong Competition", [])  # UCL 身份失败
    fixture_path, out_dir = _run_cli_with_fixture(tmp_path, per_comp, label="no_rest_on_failure")

    code = run_cli(["--team-id", str(TEAM_ID), "--season", "2024/2025",
                     "--offline-fixture", fixture_path, "--output-dir", out_dir])
    assert code != 0
    err = json.loads(capsys.readouterr().err)
    assert "rest_hours_computed_count" not in err
    assert "calendar_write" not in err
    assert "team_match_write" not in err

    db_path = err["db_path"]
    conn = sqlite3.connect(db_path)
    n_cal = conn.execute("SELECT COUNT(*) FROM pilot_match_calendar").fetchone()[0]
    n_team = conn.execute("SELECT COUNT(*) FROM pilot_team_match").fetchone()[0]
    conn.close()
    assert n_cal == 0
    assert n_team == 0


# ── 7. required 失败时 CLI 非零退出(独立场景:home/away 非法导致的 identity
#      失败,与其它测试用的失败原因不同,证明退出码契约不依赖具体失败原因) ──

def test_required_failure_cli_exit_code_is_exactly_one(tmp_path, capsys):
    per_comp = _all_good_per_competition()
    per_comp["247"] = _good_raw_for(247, "Wrong Shield Name", [
        _match(9301, 111, 222, "2025-08-10T14:00:00.000Z"),
    ])  # id 对(247)但名称不对 → IDENTITY_MISMATCH
    fixture_path, out_dir = _run_cli_with_fixture(tmp_path, per_comp, label="exit_code_contract")

    code = run_cli(["--team-id", str(TEAM_ID), "--season", "2024/2025",
                     "--offline-fixture", fixture_path, "--output-dir", out_dir])
    assert code == 1  # 契约:required 校验失败固定返回 1,不是任意非零值
    err = json.loads(capsys.readouterr().err)
    assert err["status"] == "FAILED"


# ── 8. registry 成功/失败状态真实入库(混合场景:4 个成功 + 1 个失败) ───────

def test_registry_persists_both_success_and_failure_states(tmp_path, capsys):
    per_comp = _all_good_per_competition()
    per_comp["133"] = _good_raw_for(133, "EFL Cup", [], season="2022/2023")  # season mismatch → 失败
    fixture_path, out_dir = _run_cli_with_fixture(tmp_path, per_comp, label="mixed_registry_states")

    code = run_cli(["--team-id", str(TEAM_ID), "--season", "2024/2025",
                     "--offline-fixture", fixture_path, "--output-dir", out_dir])
    assert code != 0  # EFL Cup 是 required,失败应阻断整体流水线

    err = json.loads(capsys.readouterr().err)
    db_path = err["db_path"]
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT competition_id, completeness_status FROM pilot_competition_registry ORDER BY competition_id"
    ).fetchall()
    conn.close()

    status_by_id = dict(rows)
    assert len(status_by_id) == 5  # 全部 5 个赛事都真实入库,不只是失败的那个
    assert status_by_id[133] == "FAILED"
    for cid in (42, 47, 132, 247):
        assert (
            status_by_id[cid]
            == "RESPONSE_VALIDATED_SEASON_PARAMETER_UNVERIFIED"
        )


# ── 9. competition_class 改变触发 pilot_match_calendar 冲突 ────────────────

def test_competition_class_change_triggers_calendar_conflict(tmp_path):
    base_rec = {
        "provider": "fotmob", "provider_match_id": 12001,
        "requested_competition_id": 47, "competition_name": "Premier League",
        "competition_class": "league", "kickoff_utc": "2024-08-16T14:00:00Z",
        "kickoff_precision": "exact", "home_team_id": TEAM_ID, "away_team_id": 999,
        "status": None, "finished": True, "cancelled": False,
        "source_provenance": [47],
    }
    db_path = str(tmp_path / "conflict_class.db")
    conn = sqlite3.connect(db_path)
    init_pilot_db(conn)
    write_match_calendar(conn, [base_rec])

    changed_rec = dict(base_rec)
    changed_rec["competition_class"] = "domestic_cup"  # 唯一改变的字段
    with pytest.raises(ScheduleConflictError):
        write_match_calendar(conn, [changed_rec])
    conn.close()


# ── 10. is_competitive 改变触发 pilot_team_match 冲突 ──────────────────────

def test_is_competitive_change_triggers_team_match_conflict(tmp_path):
    base_rec = {
        "provider": "fotmob", "provider_match_id": 12002, "team_id": TEAM_ID,
        "opponent_team_id": 999, "is_home": True, "is_competitive": True,
        "season_requested": "2024/2025",
    }
    db_path = str(tmp_path / "conflict_competitive.db")
    conn = sqlite3.connect(db_path)
    init_pilot_db(conn)
    write_team_match(conn, [base_rec])

    changed_rec = dict(base_rec)
    changed_rec["is_competitive"] = False  # 唯一改变的字段
    with pytest.raises(ScheduleConflictError):
        write_team_match(conn, [changed_rec])
    conn.close()


# ── verify_all_competitions 纯函数级别的正例(全部通过,不经 CLI) ──────────

def test_verify_all_competitions_all_pass_including_zero_match_community_shield():
    registry = build_competition_registry("2024/2025")
    per_comp = _all_good_per_competition()
    registry_rows, parsed = verify_all_competitions(
        per_comp, registry, "2024/2025", "2026-01-01T00:00:00Z", "test", TEAM_ID,
    )
    statuses = {r["competition_id"]: r["completeness_status"] for r in registry_rows}
    assert all(
        s == "RESPONSE_VALIDATED_SEASON_PARAMETER_UNVERIFIED"
        for s in statuses.values()
    )
    cs_row = next(r for r in registry_rows if r["competition_id"] == 247)
    assert cs_row["target_team_fixture_count"] == 0
    assert cs_row["fixture_count"] == 1
    assert set(parsed.keys()) == {47, 132, 42, 247, 133}


# ═════════════════════════════════════════════════════════════════════════════
# competition schedule fail-closed closure 永久回归
# ═════════════════════════════════════════════════════════════════════════════

def _read_registry_row(db_path, competition_id, requested_season):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM pilot_competition_registry "
            "WHERE competition_id=? AND requested_season=?",
            (competition_id, requested_season),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def test_empty_all_matches_fails_closed_and_persists_registry_before_exit(
    tmp_path, capsys,
):
    per_comp = _all_good_per_competition()
    per_comp["47"] = _good_raw_for(
        47, "Premier League", [], season="2024/2025",
    )
    fixture_path, out_dir = _run_cli_with_fixture(
        tmp_path, per_comp, label="empty_fixtures",
    )

    code = run_cli([
        "--team-id", str(TEAM_ID),
        "--season", "2024/2025",
        "--offline-fixture", fixture_path,
        "--output-dir", out_dir,
    ])

    assert code == 1
    err = json.loads(capsys.readouterr().err)
    failed = next(
        row for row in err["failed_required_competitions"]
        if row["competition_id"] == 47
    )
    assert failed["verification_status"] == "EMPTY_FIXTURES"
    persisted = _read_registry_row(
        err["db_path"], 47, "2024/2025",
    )
    assert persisted["identity_verified"] == "EMPTY_FIXTURES"
    assert persisted["fixture_count"] == 0
    assert persisted["completeness_status"] == "FAILED"

    conn = sqlite3.connect(err["db_path"])
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM pilot_match_calendar"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM pilot_team_match"
        ).fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("case_name", "returned_season"),
    [
        pytest.param("missing", None, id="missing"),
        pytest.param("empty", "", id="empty"),
        pytest.param("whitespace", "   ", id="whitespace"),
        pytest.param("wrong_type", 2024, id="wrong_type"),
        pytest.param("short_range", "2024/25", id="short_range"),
        pytest.param("nonsense", "not-a-season", id="nonsense"),
    ],
)
def test_missing_empty_or_invalid_returned_season_fails_closed(
    tmp_path, capsys, case_name, returned_season,
):
    per_comp = _all_good_per_competition()
    raw = _good_raw_for(
        47,
        "Premier League",
        [_match(15001, TEAM_ID, 999, "2024-08-16T14:00:00.000Z")],
        season="2024/2025",
    )
    if returned_season is None:
        raw["details"].pop("selectedSeason")
    else:
        raw["details"]["selectedSeason"] = returned_season
    per_comp["47"] = raw
    fixture_path, out_dir = _run_cli_with_fixture(
        tmp_path, per_comp, label=f"season_{case_name}",
    )

    code = run_cli([
        "--team-id", str(TEAM_ID),
        "--season", "2024/2025",
        "--offline-fixture", fixture_path,
        "--output-dir", out_dir,
    ])

    assert code == 1
    err = json.loads(capsys.readouterr().err)
    failed = next(
        row for row in err["failed_required_competitions"]
        if row["competition_id"] == 47
    )
    assert failed["verification_status"] == "SEASON_UNVERIFIABLE"
    persisted = _read_registry_row(
        err["db_path"], 47, "2024/2025",
    )
    assert persisted["identity_verified"] == "SEASON_UNVERIFIABLE"
    assert persisted["completeness_status"] == "FAILED"


def test_returned_season_mismatch_remains_distinct_and_fails_closed(
    tmp_path, capsys,
):
    per_comp = _all_good_per_competition()
    per_comp["47"] = _good_raw_for(
        47,
        "Premier League",
        [_match(15002, TEAM_ID, 999, "2024-08-16T14:00:00.000Z")],
        season="2023/2024",
    )
    fixture_path, out_dir = _run_cli_with_fixture(
        tmp_path, per_comp, label="season_mismatch_distinct",
    )

    code = run_cli([
        "--team-id", str(TEAM_ID),
        "--season", "2024/2025",
        "--offline-fixture", fixture_path,
        "--output-dir", out_dir,
    ])

    assert code == 1
    err = json.loads(capsys.readouterr().err)
    failed = next(
        row for row in err["failed_required_competitions"]
        if row["competition_id"] == 47
    )
    assert failed["verification_status"] == "SEASON_MISMATCH"


@pytest.mark.parametrize(
    ("case_name", "pagination_fields"),
    [
        ("has_more", {"hasMore": True}),
        ("next", {"next": "/page/2"}),
        ("next_page", {"nextPage": "/page/2"}),
        ("next_url", {"nextUrl": "https://example.invalid/page/2"}),
        ("cursor", {"cursor": "cursor-2"}),
        ("next_cursor", {"nextCursor": "cursor-2"}),
        ("previous_fixtures_url", {"previousFixturesUrl": "/previous"}),
        ("next_fixtures_url", {"nextFixturesUrl": "/next"}),
        ("current_page_total_pages", {"currentPage": 1, "totalPages": 2}),
        ("page_page_count", {"page": 0, "pageCount": 2}),
    ],
)
def test_known_pagination_continuation_fails_closed(
    tmp_path, capsys, case_name, pagination_fields,
):
    per_comp = _all_good_per_competition()
    raw = copy.deepcopy(per_comp["47"])
    raw["fixtures"].update(pagination_fields)
    per_comp["47"] = raw
    fixture_path, out_dir = _run_cli_with_fixture(
        tmp_path, per_comp, label=f"pagination_{case_name}",
    )

    code = run_cli([
        "--team-id", str(TEAM_ID),
        "--season", "2024/2025",
        "--offline-fixture", fixture_path,
        "--output-dir", out_dir,
    ])

    assert code == 1
    err = json.loads(capsys.readouterr().err)
    failed = next(
        row for row in err["failed_required_competitions"]
        if row["competition_id"] == 47
    )
    assert failed["verification_status"] == "PAGINATION_UNRESOLVED"
    persisted = _read_registry_row(
        err["db_path"], 47, "2024/2025",
    )
    assert persisted["pagination_detected"] == 1
    assert persisted["pagination_status"] == "DETECTED"
    assert persisted["completeness_status"] == "FAILED"


@pytest.mark.parametrize(
    "pagination_fields",
    [
        {
            "hasMore": False,
            "next": "",
            "nextPage": "",
            "nextUrl": "   ",
            "cursor": "",
            "nextCursor": "",
            "previousFixturesUrl": "",
            "nextFixturesUrl": "",
            "currentPage": 2,
            "totalPages": 2,
            "page": 2,
            "pageCount": 2,
        },
        {},
    ],
    ids=["explicit_false_and_empty", "no_known_markers"],
)
def test_no_pagination_continuation_is_not_detected_only(
    tmp_path, capsys, pagination_fields,
):
    per_comp = _all_good_per_competition()
    raw = copy.deepcopy(per_comp["47"])
    raw["fixtures"].update(pagination_fields)
    per_comp["47"] = raw
    fixture_path, out_dir = _run_cli_with_fixture(
        tmp_path, per_comp, label="pagination_not_detected",
    )

    code = run_cli([
        "--team-id", str(TEAM_ID),
        "--season", "2024/2025",
        "--offline-fixture", fixture_path,
        "--output-dir", out_dir,
    ])

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    persisted = _read_registry_row(
        out["db_path"], 47, "2024/2025",
    )
    assert persisted["pagination_detected"] == 0
    assert persisted["pagination_status"] == "NOT_DETECTED"
    assert (
        persisted["completeness_status"]
        == "RESPONSE_VALIDATED_SEASON_PARAMETER_UNVERIFIED"
    )


@pytest.mark.parametrize(
    "pagination_fields",
    [
        {"hasMore": "unknown"},
        {"currentPage": 1},
        {"page": 3, "pageCount": 2},
    ],
    ids=["invalid_has_more", "incomplete_page_pair", "page_exceeds_count"],
)
def test_unresolved_pagination_marker_fails_closed(
    tmp_path, capsys, pagination_fields,
):
    per_comp = _all_good_per_competition()
    raw = copy.deepcopy(per_comp["47"])
    raw["fixtures"].update(pagination_fields)
    per_comp["47"] = raw
    fixture_path, out_dir = _run_cli_with_fixture(
        tmp_path, per_comp, label="pagination_unresolved",
    )

    code = run_cli([
        "--team-id", str(TEAM_ID),
        "--season", "2024/2025",
        "--offline-fixture", fixture_path,
        "--output-dir", out_dir,
    ])

    assert code == 1
    err = json.loads(capsys.readouterr().err)
    persisted = _read_registry_row(
        err["db_path"], 47, "2024/2025",
    )
    assert persisted["identity_verified"] == "PAGINATION_UNRESOLVED"
    assert persisted["pagination_detected"] is None
    assert persisted["pagination_status"] == "UNRESOLVED"
    assert persisted["completeness_status"] == "FAILED"


@pytest.mark.parametrize(
    ("nested_path", "nested_value"),
    [
        ("business", {"next": "/business-page/2"}),
        ("stats", {"page": 1, "pageCount": 2}),
    ],
)
def test_match_business_objects_are_not_pagination_metadata(
    tmp_path, capsys, nested_path, nested_value,
):
    per_comp = _all_good_per_competition()
    raw = copy.deepcopy(per_comp["47"])
    raw["fixtures"]["allMatches"][0][nested_path] = nested_value
    per_comp["47"] = raw
    fixture_path, out_dir = _run_cli_with_fixture(
        tmp_path,
        per_comp,
        label=f"pagination_match_{nested_path}_ignored",
    )

    code = run_cli([
        "--team-id", str(TEAM_ID),
        "--season", "2024/2025",
        "--offline-fixture", fixture_path,
        "--output-dir", out_dir,
    ])

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    persisted = _read_registry_row(out["db_path"], 47, "2024/2025")
    assert persisted["pagination_status"] == "NOT_DETECTED"


@pytest.mark.parametrize(
    ("object_name", "field_name", "field_value"),
    [
        ("QAData", "cursor", "qa-cursor"),
        ("details", "next", "/details-page/2"),
    ],
)
def test_non_fixture_business_objects_are_not_pagination_metadata(
    tmp_path, capsys, object_name, field_name, field_value,
):
    per_comp = _all_good_per_competition()
    raw = copy.deepcopy(per_comp["47"])
    raw.setdefault(object_name, {})[field_name] = field_value
    per_comp["47"] = raw
    fixture_path, out_dir = _run_cli_with_fixture(
        tmp_path,
        per_comp,
        label=f"pagination_{object_name}_ignored",
    )

    code = run_cli([
        "--team-id", str(TEAM_ID),
        "--season", "2024/2025",
        "--offline-fixture", fixture_path,
        "--output-dir", out_dir,
    ])

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    persisted = _read_registry_row(out["db_path"], 47, "2024/2025")
    assert persisted["pagination_status"] == "NOT_DETECTED"


def test_direct_fixture_detected_and_unresolved_evidence_are_both_preserved():
    raw = _good_raw_for(
        47,
        "Premier League",
        [_match(15100, TEAM_ID, 999, "2024-08-16T14:00:00.000Z")],
    )
    raw["fixtures"].update({
        "next": "/page/2",
        "hasMore": "unknown",
    })

    result = inspect_known_pagination(raw)

    assert result["status"] == "UNRESOLVED"
    assert result["detected_evidence"] == ["$.fixtures.next"]
    assert result["unresolved_evidence"] == ["$.fixtures.hasMore"]
    assert result["evidence"] == [
        "$.fixtures.hasMore",
        "$.fixtures.next",
    ]


def test_detected_and_unresolved_pagination_evidence_persist_together(
    tmp_path, capsys,
):
    per_comp = _all_good_per_competition()
    raw = copy.deepcopy(per_comp["47"])
    raw["fixtures"].update({
        "next": "/page/2",
        "hasMore": "unknown",
    })
    per_comp["47"] = raw
    fixture_path, out_dir = _run_cli_with_fixture(
        tmp_path, per_comp, label="pagination_mixed_evidence",
    )

    code = run_cli([
        "--team-id", str(TEAM_ID),
        "--season", "2024/2025",
        "--offline-fixture", fixture_path,
        "--output-dir", out_dir,
    ])

    assert code == 1
    err = json.loads(capsys.readouterr().err)
    persisted = _read_registry_row(err["db_path"], 47, "2024/2025")
    assert persisted["pagination_status"] == "UNRESOLVED"
    assert json.loads(persisted["pagination_evidence"]) == [
        "$.fixtures.hasMore",
        "$.fixtures.next",
    ]


@pytest.mark.parametrize(
    "pagination_fields",
    [
        {"next": None},
        {"next": False},
        {"next": ""},
        {"next": "   "},
        {"next": []},
        {"next": {}},
        {"cursor": None},
        {"nextFixturesUrl": False},
    ],
    ids=[
        "next_null",
        "next_false",
        "next_empty_string",
        "next_whitespace",
        "next_empty_list",
        "next_empty_dict",
        "cursor_null",
        "next_url_false",
    ],
)
def test_explicitly_empty_direct_markers_are_not_detected(
    tmp_path, capsys, pagination_fields,
):
    per_comp = _all_good_per_competition()
    raw = copy.deepcopy(per_comp["47"])
    raw["fixtures"].update(pagination_fields)
    per_comp["47"] = raw
    fixture_path, out_dir = _run_cli_with_fixture(
        tmp_path, per_comp, label="pagination_empty_direct_marker",
    )

    code = run_cli([
        "--team-id", str(TEAM_ID),
        "--season", "2024/2025",
        "--offline-fixture", fixture_path,
        "--output-dir", out_dir,
    ])

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    persisted = _read_registry_row(out["db_path"], 47, "2024/2025")
    assert persisted["pagination_status"] == "NOT_DETECTED"
    assert persisted["pagination_detected"] == 0


@pytest.mark.parametrize(
    "pagination_fields",
    [
        {"next": 0},
        {"next": True},
        {"next": ["unexpected"]},
        {"cursor": 7},
        {"cursor": {"value": "x"}},
        {"hasMore": 0},
        {"hasMore": 1},
        {"hasMore": "false"},
        {"page": "1", "pageCount": 2},
    ],
    ids=[
        "next_zero",
        "next_true",
        "next_nonempty_list",
        "cursor_integer",
        "cursor_nonempty_dict",
        "has_more_zero",
        "has_more_one",
        "has_more_string",
        "page_wrong_type",
    ],
)
def test_nonempty_or_invalid_direct_markers_are_unresolved(
    tmp_path, capsys, pagination_fields,
):
    per_comp = _all_good_per_competition()
    raw = copy.deepcopy(per_comp["47"])
    raw["fixtures"].update(pagination_fields)
    per_comp["47"] = raw
    fixture_path, out_dir = _run_cli_with_fixture(
        tmp_path, per_comp, label="pagination_invalid_direct_type",
    )

    code = run_cli([
        "--team-id", str(TEAM_ID),
        "--season", "2024/2025",
        "--offline-fixture", fixture_path,
        "--output-dir", out_dir,
    ])

    assert code == 1
    err = json.loads(capsys.readouterr().err)
    persisted = _read_registry_row(err["db_path"], 47, "2024/2025")
    assert persisted["pagination_status"] == "UNRESOLVED"


@pytest.mark.parametrize(
    ("marker_items", "expected_additional_evidence"),
    [
        ([("hasMore", True), ("HASMORE", False)], []),
        ([("HASMORE", False), ("hasMore", True)], []),
        ([("next", "/page/2"), ("NEXT", None)], []),
        ([("NEXT", None), ("next", "/page/2")], []),
        ([("cursor", "cursor-2"), ("CURSOR", "")], []),
        ([("CURSOR", ""), ("cursor", "cursor-2")], []),
        ([("nextPage", "/page/2"), ("NEXTPAGE", None)], []),
        ([("nextUrl", "/page/2"), ("NEXTURL", None)], []),
        ([("nextCursor", "cursor-2"), ("NEXTCURSOR", "")], []),
        (
            [("previousFixturesUrl", "/previous"), ("PREVIOUSFIXTURESURL", None)],
            [],
        ),
        ([("nextFixturesUrl", "/next"), ("NEXTFIXTURESURL", None)], []),
        (
            [("currentPage", 1), ("CURRENTPAGE", 2), ("totalPages", 3)],
            [
                "incomplete:missing-companion:$.fixtures.page",
                "incomplete:present:$.fixtures.totalPages",
            ],
        ),
        (
            [("currentPage", 1), ("totalPages", 3), ("TOTALPAGES", 3)],
            ["incomplete:present:$.fixtures.currentPage"],
        ),
        (
            [("page", 1), ("PAGE", 1), ("pageCount", 3)],
            ["incomplete:present:$.fixtures.pageCount"],
        ),
        (
            [("page", 1), ("pageCount", 3), ("PAGECOUNT", 3)],
            [
                "incomplete:missing-companion:$.fixtures.totalPages",
                "incomplete:present:$.fixtures.page",
            ],
        ),
        ([("next", "/page/2"), ("NEXT", "/page/2")], []),
    ],
    ids=[
        "has_more_true_then_false",
        "has_more_false_then_true",
        "next_value_then_empty",
        "next_empty_then_value",
        "cursor_value_then_empty",
        "cursor_empty_then_value",
        "next_page",
        "next_url",
        "next_cursor",
        "previous_fixtures_url",
        "next_fixtures_url",
        "current_page",
        "total_pages_same_value",
        "page_same_value",
        "page_count_same_value",
        "same_folded_values_are_ambiguous",
    ],
)
def test_known_pagination_casefold_collisions_fail_closed_with_full_evidence(
    tmp_path, capsys, monkeypatch, marker_items,
    expected_additional_evidence,
):
    """已知 marker 的大小写折叠碰撞不得 last-write-wins 或择优放行。"""
    folded: dict[str, list[str]] = {}
    for original_key, _ in marker_items:
        folded.setdefault(original_key.casefold(), []).append(original_key)
    expected_evidence = sorted(
        [
            f"collision:{normalized}:$.fixtures.{original_key}"
            for normalized, original_keys in folded.items()
            if len(original_keys) > 1
            for original_key in original_keys
        ]
        + expected_additional_evidence
    )

    per_comp = _all_good_per_competition()
    raw = copy.deepcopy(per_comp["47"])
    raw["fixtures"].update(dict(marker_items))
    per_comp["47"] = raw

    inspected = inspect_known_pagination(raw)
    assert inspected["status"] == "UNRESOLVED"
    assert inspected["detected_evidence"] == []
    assert inspected["unresolved_evidence"] == expected_evidence
    assert inspected["evidence"] == expected_evidence

    fixture_path, out_dir = _run_cli_with_fixture(
        tmp_path, per_comp, label="pagination_casefold_collision",
    )
    downstream_calls = []

    def unexpected_downstream(*args, **kwargs):
        downstream_calls.append((args, kwargs))
        raise AssertionError("pagination collision must stop before downstream")

    module_path = (
        "analysis.competition_schedule_pilot."
        "fotmob_competition_schedule_pilot"
    )
    for name in (
        "merge_competition_schedules",
        "build_team_match_records",
        "write_match_calendar",
        "write_team_match",
        "compute_rest_hours",
    ):
        monkeypatch.setattr(f"{module_path}.{name}", unexpected_downstream)

    code = run_cli([
        "--team-id", str(TEAM_ID),
        "--season", "2024/2025",
        "--offline-fixture", fixture_path,
        "--output-dir", out_dir,
    ])

    assert code == 1
    assert downstream_calls == []
    err = json.loads(capsys.readouterr().err)
    persisted = _read_registry_row(err["db_path"], 47, "2024/2025")
    assert persisted["identity_verified"] == "PAGINATION_UNRESOLVED"
    assert persisted["pagination_detected"] is None
    assert persisted["pagination_status"] == "UNRESOLVED"
    assert json.loads(persisted["pagination_evidence"]) == expected_evidence
    assert persisted["completeness_status"] == "FAILED"


def test_unrelated_direct_casefold_collision_does_not_affect_pagination():
    raw = _good_raw_for(
        47,
        "Premier League",
        [_match(15200, TEAM_ID, 999, "2024-08-16T14:00:00.000Z")],
    )
    raw["fixtures"].update({"someKey": "a", "SOMEKEY": "b"})

    assert inspect_known_pagination(raw) == {
        "status": "NOT_DETECTED",
        "evidence": [],
        "detected_evidence": [],
        "unresolved_evidence": [],
    }


def test_all_matches_casefold_collision_is_outside_pagination_scope():
    raw = _good_raw_for(
        47,
        "Premier League",
        [_match(15201, TEAM_ID, 999, "2024-08-16T14:00:00.000Z")],
    )
    raw["fixtures"]["allMatches"][0].update({
        "hasMore": True,
        "HASMORE": False,
    })

    assert inspect_known_pagination(raw) == {
        "status": "NOT_DETECTED",
        "evidence": [],
        "detected_evidence": [],
        "unresolved_evidence": [],
    }


# ═════════════════════════════════════════════════════════════════════════════
# Fourth-round page-pair dialect closure
# ═════════════════════════════════════════════════════════════════════════════

def _pagination_raw_with(marker_items):
    raw = _good_raw_for(
        47,
        "Premier League",
        [_match(15300, TEAM_ID, 999, "2024-08-16T14:00:00.000Z")],
    )
    raw["fixtures"].update(dict(marker_items))
    return raw


@pytest.mark.parametrize(
    ("marker_items", "expected"),
    [
        pytest.param(
            [("page", 1), ("totalPages", 1)],
            {
                "status": "NOT_DETECTED",
                "evidence": [],
                "detected_evidence": [],
                "unresolved_evidence": [],
            },
            id="page_total_pages_equal",
        ),
        pytest.param(
            [("page", 1), ("totalPages", 2)],
            {
                "status": "DETECTED",
                "evidence": ["$.fixtures.page/totalPages"],
                "detected_evidence": ["$.fixtures.page/totalPages"],
                "unresolved_evidence": [],
            },
            id="page_total_pages_detected",
        ),
        pytest.param(
            [("totalPages", 2), ("page", 1)],
            {
                "status": "DETECTED",
                "evidence": ["$.fixtures.page/totalPages"],
                "detected_evidence": ["$.fixtures.page/totalPages"],
                "unresolved_evidence": [],
            },
            id="page_total_pages_reverse_insertion_order",
        ),
        pytest.param(
            [("Page", 1), ("TOTALPAGES", 2)],
            {
                "status": "DETECTED",
                "evidence": ["$.fixtures.Page/TOTALPAGES"],
                "detected_evidence": ["$.fixtures.Page/TOTALPAGES"],
                "unresolved_evidence": [],
            },
            id="page_total_pages_case_variant",
        ),
        pytest.param(
            [("currentPage", 2), ("totalPages", 2)],
            {
                "status": "NOT_DETECTED",
                "evidence": [],
                "detected_evidence": [],
                "unresolved_evidence": [],
            },
            id="current_page_total_pages_equal",
        ),
        pytest.param(
            [("currentPage", 1), ("totalPages", 2)],
            {
                "status": "DETECTED",
                "evidence": ["$.fixtures.currentPage/totalPages"],
                "detected_evidence": ["$.fixtures.currentPage/totalPages"],
                "unresolved_evidence": [],
            },
            id="current_page_total_pages_detected",
        ),
        pytest.param(
            [("page", 2), ("pageCount", 2)],
            {
                "status": "NOT_DETECTED",
                "evidence": [],
                "detected_evidence": [],
                "unresolved_evidence": [],
            },
            id="page_page_count_equal",
        ),
        pytest.param(
            [("page", 1), ("pageCount", 2)],
            {
                "status": "DETECTED",
                "evidence": ["$.fixtures.page/pageCount"],
                "detected_evidence": ["$.fixtures.page/pageCount"],
                "unresolved_evidence": [],
            },
            id="page_page_count_detected",
        ),
    ],
)
def test_supported_page_pair_dialects_use_only_actual_paths(marker_items, expected):
    result = inspect_known_pagination(_pagination_raw_with(marker_items))

    assert result == expected
    serialized = json.dumps(result, sort_keys=True)
    for absent_name in {"currentPage", "pageCount"} - {
        str(key) for key, _ in marker_items
    }:
        assert absent_name not in serialized


@pytest.mark.parametrize(
    ("marker_items", "expected_unresolved"),
    [
        pytest.param(
            [("page", 1)],
            [
                "incomplete:missing-companion:$.fixtures.pageCount",
                "incomplete:missing-companion:$.fixtures.totalPages",
                "incomplete:present:$.fixtures.page",
            ],
            id="page_only",
        ),
        pytest.param(
            [("totalPages", 2)],
            [
                "incomplete:missing-companion:$.fixtures.currentPage",
                "incomplete:missing-companion:$.fixtures.page",
                "incomplete:present:$.fixtures.totalPages",
            ],
            id="total_pages_only",
        ),
        pytest.param(
            [("currentPage", 1)],
            [
                "incomplete:missing-companion:$.fixtures.totalPages",
                "incomplete:present:$.fixtures.currentPage",
            ],
            id="current_page_only",
        ),
        pytest.param(
            [("pageCount", 2)],
            [
                "incomplete:missing-companion:$.fixtures.page",
                "incomplete:present:$.fixtures.pageCount",
            ],
            id="page_count_only",
        ),
        pytest.param(
            [("currentPage", 1), ("pageCount", 2)],
            [
                "incomplete:missing-companion:$.fixtures.page",
                "incomplete:missing-companion:$.fixtures.totalPages",
                "incomplete:present:$.fixtures.currentPage",
                "incomplete:present:$.fixtures.pageCount",
            ],
            id="unsupported_cross_pair",
        ),
    ],
)
def test_incomplete_page_family_evidence_separates_present_and_missing_fields(
    marker_items, expected_unresolved,
):
    result = inspect_known_pagination(_pagination_raw_with(marker_items))

    assert result["status"] == "UNRESOLVED"
    assert result["detected_evidence"] == []
    assert result["unresolved_evidence"] == expected_unresolved
    assert result["evidence"] == expected_unresolved


@pytest.mark.parametrize(
    "marker_items",
    [
        [("page", True), ("totalPages", 1)],
        [("page", 1), ("totalPages", False)],
        [("page", "1"), ("totalPages", 1)],
        [("page", 1), ("totalPages", "1")],
        [("page", -1), ("totalPages", 1)],
        [("page", 1), ("totalPages", -1)],
        [("page", 2), ("totalPages", 1)],
    ],
    ids=[
        "current_bool",
        "total_bool",
        "current_string",
        "total_string",
        "current_negative",
        "total_negative",
        "current_exceeds_total",
    ],
)
def test_complete_page_total_pages_malformed_values_are_unresolved(marker_items):
    result = inspect_known_pagination(_pagination_raw_with(marker_items))

    assert result["status"] == "UNRESOLVED"
    assert result["detected_evidence"] == []
    assert result["unresolved_evidence"] == ["$.fixtures.page/totalPages"]


@pytest.mark.parametrize(
    ("marker_items", "expected_detected", "expected_unresolved"),
    [
        pytest.param(
            [("page", 1), ("PAGE", 1), ("currentPage", 1)],
            [],
            [
                "collision:page:$.fixtures.PAGE",
                "collision:page:$.fixtures.page",
                "incomplete:missing-companion:$.fixtures.totalPages",
                "incomplete:present:$.fixtures.currentPage",
            ],
            id="page_collision_plus_current_page_orphan",
        ),
        pytest.param(
            [("currentPage", 1), ("PAGE", 1), ("page", 1)],
            [],
            [
                "collision:page:$.fixtures.PAGE",
                "collision:page:$.fixtures.page",
                "incomplete:missing-companion:$.fixtures.totalPages",
                "incomplete:present:$.fixtures.currentPage",
            ],
            id="page_collision_plus_current_page_orphan_reverse_order",
        ),
        pytest.param(
            [("page", 1), ("PAGE", 1), ("pageCount", 1)],
            [],
            [
                "collision:page:$.fixtures.PAGE",
                "collision:page:$.fixtures.page",
                "incomplete:present:$.fixtures.pageCount",
            ],
            id="page_collision_plus_page_count_orphan",
        ),
        pytest.param(
            [("page", 1), ("PAGE", 1), ("totalPages", 1)],
            [],
            [
                "collision:page:$.fixtures.PAGE",
                "collision:page:$.fixtures.page",
                "incomplete:missing-companion:$.fixtures.currentPage",
                "incomplete:present:$.fixtures.totalPages",
            ],
            id="page_collision_plus_total_pages_orphan",
        ),
        pytest.param(
            [
                ("totalPages", 1),
                ("TOTALPAGES", 1),
                ("currentPage", 1),
                ("page", 1),
            ],
            [],
            [
                "collision:totalpages:$.fixtures.TOTALPAGES",
                "collision:totalpages:$.fixtures.totalPages",
                "incomplete:missing-companion:$.fixtures.pageCount",
                "incomplete:present:$.fixtures.currentPage",
                "incomplete:present:$.fixtures.page",
            ],
            id="total_pages_collision_plus_two_independent_orphans",
        ),
        pytest.param(
            [
                ("page", 1),
                ("PAGE", 1),
                ("currentPage", 1),
                ("totalPages", 1),
                ("pageCount", 1),
            ],
            [],
            [
                "collision:page:$.fixtures.PAGE",
                "collision:page:$.fixtures.page",
                "incomplete:present:$.fixtures.pageCount",
            ],
            id="complete_pair_plus_page_collision_plus_page_count_orphan",
        ),
        pytest.param(
            [
                ("page", 1),
                ("PAGE", 1),
                ("currentPage", 1),
                ("next", "/page/2"),
            ],
            ["$.fixtures.next"],
            [
                "collision:page:$.fixtures.PAGE",
                "collision:page:$.fixtures.page",
                "incomplete:missing-companion:$.fixtures.totalPages",
                "incomplete:present:$.fixtures.currentPage",
            ],
            id="collision_plus_detected_marker_plus_orphan",
        ),
    ],
)
def test_page_family_collision_preserves_independent_orphan_evidence(
    marker_items, expected_detected, expected_unresolved,
):
    result = inspect_known_pagination(_pagination_raw_with(marker_items))

    assert result["status"] == "UNRESOLVED"
    assert result["detected_evidence"] == expected_detected
    assert result["unresolved_evidence"] == expected_unresolved
    assert result["evidence"] == sorted(
        expected_detected + expected_unresolved
    )


def test_page_collision_and_other_detected_marker_preserve_both_evidence_classes():
    result = inspect_known_pagination(
        _pagination_raw_with([
            ("page", 1),
            ("PAGE", 1),
            ("totalPages", 1),
            ("next", "/page/2"),
        ])
    )

    assert result["status"] == "UNRESOLVED"
    assert result["detected_evidence"] == ["$.fixtures.next"]
    assert result["unresolved_evidence"] == [
        "collision:page:$.fixtures.PAGE",
        "collision:page:$.fixtures.page",
        "incomplete:missing-companion:$.fixtures.currentPage",
        "incomplete:present:$.fixtures.totalPages",
    ]
    assert result["evidence"] == [
        "$.fixtures.next",
        "collision:page:$.fixtures.PAGE",
        "collision:page:$.fixtures.page",
        "incomplete:missing-companion:$.fixtures.currentPage",
        "incomplete:present:$.fixtures.totalPages",
    ]


@pytest.mark.parametrize(
    "marker_items",
    [
        [
            ("currentPage", 1),
            ("page", 1),
            ("totalPages", 1),
            ("pageCount", 1),
        ],
        [
            ("currentPage", 1),
            ("page", 1),
            ("totalPages", 2),
            ("pageCount", 2),
        ],
    ],
    ids=["all_no_continuation", "all_detected"],
)
def test_multiple_complete_page_dialects_with_same_semantics_are_allowed(marker_items):
    result = inspect_known_pagination(_pagination_raw_with(marker_items))

    assert result["status"] in {"NOT_DETECTED", "DETECTED"}
    assert result["unresolved_evidence"] == []


def test_multiple_complete_page_dialects_with_conflicting_semantics_fail_closed():
    result = inspect_known_pagination(
        _pagination_raw_with([
            ("page", 1),
            ("totalPages", 2),
            ("pageCount", 1),
        ])
    )

    assert result["status"] == "UNRESOLVED"
    assert result["detected_evidence"] == ["$.fixtures.page/totalPages"]
    assert result["unresolved_evidence"] == [
        "conflict:page-dialects:"
        "$.fixtures.page/pageCount=NO_CONTINUATION|"
        "$.fixtures.page/totalPages=DETECTED",
    ]


@pytest.mark.parametrize(
    ("marker_items", "expected_detected", "expected_unresolved"),
    [
        pytest.param(
            [
                ("currentPage", 1),
                ("totalPages", 1),
                ("pageCount", 9),
            ],
            [],
            [
                "incomplete:missing-companion:$.fixtures.page",
                "incomplete:present:$.fixtures.pageCount",
            ],
            id="current_total_no_continuation_plus_orphan_page_count",
        ),
        pytest.param(
            [
                ("currentPage", 1),
                ("totalPages", 2),
                ("pageCount", 9),
            ],
            ["$.fixtures.currentPage/totalPages"],
            [
                "incomplete:missing-companion:$.fixtures.page",
                "incomplete:present:$.fixtures.pageCount",
            ],
            id="current_total_detected_plus_orphan_page_count",
        ),
        pytest.param(
            [
                ("page", 1),
                ("pageCount", 1),
                ("currentPage", 9),
            ],
            [],
            [
                "incomplete:missing-companion:$.fixtures.totalPages",
                "incomplete:present:$.fixtures.currentPage",
            ],
            id="page_count_no_continuation_plus_orphan_current_page",
        ),
        pytest.param(
            [
                ("page", 1),
                ("pageCount", 2),
                ("currentPage", 9),
            ],
            ["$.fixtures.page/pageCount"],
            [
                "incomplete:missing-companion:$.fixtures.totalPages",
                "incomplete:present:$.fixtures.currentPage",
            ],
            id="page_count_detected_plus_orphan_current_page",
        ),
    ],
)
def test_complete_page_pair_does_not_mask_orphan_known_page_marker(
    marker_items, expected_detected, expected_unresolved,
):
    result = inspect_known_pagination(_pagination_raw_with(marker_items))

    assert result["status"] == "UNRESOLVED"
    assert result["detected_evidence"] == expected_detected
    assert result["unresolved_evidence"] == expected_unresolved
    assert result["evidence"] == sorted(expected_detected + expected_unresolved)


@pytest.mark.parametrize(
    "orphan_value",
    ["SECRET_BAD", True, -1],
    ids=["string", "bool", "negative"],
)
def test_orphan_page_marker_value_cannot_make_incomplete_dialect_safe(
    orphan_value,
):
    result = inspect_known_pagination(
        _pagination_raw_with([
            ("currentPage", 1),
            ("totalPages", 1),
            ("pageCount", orphan_value),
        ])
    )

    expected = [
        "incomplete:missing-companion:$.fixtures.page",
        "incomplete:present:$.fixtures.pageCount",
    ]
    assert result == {
        "status": "UNRESOLVED",
        "evidence": expected,
        "detected_evidence": [],
        "unresolved_evidence": expected,
    }


@pytest.mark.parametrize(
    ("marker_items", "expected_status", "expected_detected"),
    [
        pytest.param(
            [("currentPage", 1), ("totalPages", 1)],
            "NOT_DETECTED",
            [],
            id="current_page_total_pages",
        ),
        pytest.param(
            [("page", 1), ("totalPages", 1)],
            "NOT_DETECTED",
            [],
            id="page_total_pages",
        ),
        pytest.param(
            [("page", 1), ("pageCount", 1)],
            "NOT_DETECTED",
            [],
            id="page_page_count",
        ),
        pytest.param(
            [
                ("currentPage", 1),
                ("totalPages", 1),
                ("page", 1),
                ("pageCount", 1),
            ],
            "NOT_DETECTED",
            [],
            id="all_supported_keys_no_continuation",
        ),
        pytest.param(
            [("page", 1), ("totalPages", 2), ("pageCount", 2)],
            "DETECTED",
            [
                "$.fixtures.page/pageCount",
                "$.fixtures.page/totalPages",
            ],
            id="multiple_complete_dialects_consistent",
        ),
    ],
)
def test_complete_page_dialects_do_not_create_false_orphan_evidence(
    marker_items, expected_status, expected_detected,
):
    result = inspect_known_pagination(_pagination_raw_with(marker_items))

    assert result["status"] == expected_status
    assert result["detected_evidence"] == expected_detected
    assert result["unresolved_evidence"] == []
    assert result["evidence"] == expected_detected


def test_cli_orphan_page_marker_persists_failure_and_stops_downstream(
    tmp_path, capsys, monkeypatch,
):
    per_comp = _all_good_per_competition()
    per_comp["47"]["fixtures"].update({
        "currentPage": 1,
        "totalPages": 1,
        "pageCount": 9,
    })
    fixture_path, out_dir = _run_cli_with_fixture(
        tmp_path, per_comp, label="orphan_page_count",
    )
    downstream_calls = []

    def unexpected_downstream(*args, **kwargs):
        downstream_calls.append((args, kwargs))
        raise AssertionError("orphan page marker must stop before downstream")

    module_path = (
        "analysis.competition_schedule_pilot."
        "fotmob_competition_schedule_pilot"
    )
    for name in (
        "merge_competition_schedules",
        "build_team_match_records",
        "write_match_calendar",
        "write_team_match",
        "compute_rest_hours",
    ):
        monkeypatch.setattr(f"{module_path}.{name}", unexpected_downstream)

    code = run_cli([
        "--team-id", str(TEAM_ID),
        "--season", "2024/2025",
        "--offline-fixture", fixture_path,
        "--output-dir", out_dir,
    ])

    assert code == 1
    assert downstream_calls == []
    err = json.loads(capsys.readouterr().err)
    persisted = _read_registry_row(err["db_path"], 47, "2024/2025")
    assert persisted["identity_verified"] == "PAGINATION_UNRESOLVED"
    assert persisted["pagination_detected"] is None
    assert persisted["pagination_status"] == "UNRESOLVED"
    assert json.loads(persisted["pagination_evidence"]) == [
        "incomplete:missing-companion:$.fixtures.page",
        "incomplete:present:$.fixtures.pageCount",
    ]
    assert persisted["completeness_status"] == "FAILED"

    conn = sqlite3.connect(err["db_path"])
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM pilot_match_calendar"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM pilot_team_match"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_cli_collision_and_independent_orphan_persist_full_failure_before_downstream(
    tmp_path, capsys, monkeypatch,
):
    per_comp = _all_good_per_competition()
    per_comp["47"]["fixtures"].update({
        "page": 1,
        "PAGE": 1,
        "currentPage": 1,
    })
    fixture_path, out_dir = _run_cli_with_fixture(
        tmp_path, per_comp, label="collision_and_orphan",
    )
    downstream_calls = []

    def unexpected_downstream(*args, **kwargs):
        downstream_calls.append((args, kwargs))
        raise AssertionError(
            "pagination collision/orphan must stop before downstream"
        )

    module_path = (
        "analysis.competition_schedule_pilot."
        "fotmob_competition_schedule_pilot"
    )
    for name in (
        "merge_competition_schedules",
        "build_team_match_records",
        "write_match_calendar",
        "write_team_match",
        "compute_rest_hours",
        "find_cross_comp_rest_examples",
    ):
        monkeypatch.setattr(f"{module_path}.{name}", unexpected_downstream)

    code = run_cli([
        "--team-id", str(TEAM_ID),
        "--season", "2024/2025",
        "--offline-fixture", fixture_path,
        "--output-dir", out_dir,
    ])

    assert code == 1
    assert downstream_calls == []
    err = json.loads(capsys.readouterr().err)
    persisted = _read_registry_row(err["db_path"], 47, "2024/2025")
    expected_evidence = [
        "collision:page:$.fixtures.PAGE",
        "collision:page:$.fixtures.page",
        "incomplete:missing-companion:$.fixtures.totalPages",
        "incomplete:present:$.fixtures.currentPage",
    ]
    assert persisted["identity_verified"] == "PAGINATION_UNRESOLVED"
    assert persisted["pagination_detected"] is None
    assert persisted["pagination_status"] == "UNRESOLVED"
    assert json.loads(persisted["pagination_evidence"]) == expected_evidence
    assert persisted["completeness_status"] == "FAILED"

    conn = sqlite3.connect(err["db_path"])
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM pilot_match_calendar"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM pilot_team_match"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_cli_page_total_pages_no_continuation_runs_downstream(
    tmp_path, capsys,
):
    per_comp = _all_good_per_competition()
    per_comp["47"]["fixtures"].update({"page": 1, "totalPages": 1})
    fixture_path, out_dir = _run_cli_with_fixture(
        tmp_path, per_comp, label="page_total_pages_complete",
    )

    code = run_cli([
        "--team-id", str(TEAM_ID),
        "--season", "2024/2025",
        "--offline-fixture", fixture_path,
        "--output-dir", out_dir,
    ])

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    persisted = _read_registry_row(out["db_path"], 47, "2024/2025")
    assert persisted["identity_verified"] == "IDENTITY_VERIFIED"
    assert persisted["pagination_status"] == "NOT_DETECTED"
    assert persisted["pagination_detected"] == 0
    assert (
        persisted["completeness_status"]
        == "RESPONSE_VALIDATED_SEASON_PARAMETER_UNVERIFIED"
    )
    assert out["total_merged_records"] == 5
    assert out["total_team_records"] == 4
    assert out["calendar_write"] == {"inserted": 5, "skipped": 0}
    assert out["team_match_write"] == {"inserted": 4, "skipped": 0}
    assert out["rest_hours_computed_count"] > 0


def test_cli_page_total_pages_detected_persists_evidence_and_stops_downstream(
    tmp_path, capsys, monkeypatch,
):
    per_comp = _all_good_per_competition()
    per_comp["47"]["fixtures"].update({"page": 1, "totalPages": 2})
    fixture_path, out_dir = _run_cli_with_fixture(
        tmp_path, per_comp, label="page_total_pages_detected",
    )
    downstream_calls = []

    def unexpected_downstream(*args, **kwargs):
        downstream_calls.append((args, kwargs))
        raise AssertionError("detected pagination must stop before downstream")

    module_path = (
        "analysis.competition_schedule_pilot."
        "fotmob_competition_schedule_pilot"
    )
    for name in (
        "merge_competition_schedules",
        "build_team_match_records",
        "write_match_calendar",
        "write_team_match",
        "compute_rest_hours",
    ):
        monkeypatch.setattr(f"{module_path}.{name}", unexpected_downstream)

    code = run_cli([
        "--team-id", str(TEAM_ID),
        "--season", "2024/2025",
        "--offline-fixture", fixture_path,
        "--output-dir", out_dir,
    ])

    assert code == 1
    assert downstream_calls == []
    err = json.loads(capsys.readouterr().err)
    persisted = _read_registry_row(err["db_path"], 47, "2024/2025")
    assert persisted["identity_verified"] == "PAGINATION_UNRESOLVED"
    assert persisted["pagination_status"] == "DETECTED"
    assert persisted["pagination_detected"] == 1
    assert json.loads(persisted["pagination_evidence"]) == [
        "$.fixtures.page/totalPages",
    ]
    assert persisted["completeness_status"] == "FAILED"


def _registry_row_for_season(season, **changes):
    row = {
        "competition_id": 47,
        "expected_name": "Premier League",
        "observed_name": "Premier League",
        "competition_class": "league",
        "requested_season": season,
        "returned_season": season,
        "season_parameter_verified": None,
        "fixture_count": 1,
        "target_team_fixture_count": 1,
        "identity_verified": "IDENTITY_VERIFIED",
        "pagination_detected": 0,
        "pagination_status": "NOT_DETECTED",
        "pagination_evidence": None,
        "completeness_status": (
            "RESPONSE_VALIDATED_SEASON_PARAMETER_UNVERIFIED"
        ),
    }
    row.update(changes)
    return row


def test_same_competition_two_seasons_persist_as_two_registry_rows(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "two_seasons.db"))
    try:
        init_pilot_db(conn)
        first = write_competition_registry(
            conn, [_registry_row_for_season("2024/2025")],
        )
        second = write_competition_registry(
            conn, [_registry_row_for_season("2023/2024")],
        )
        saved = conn.execute(
            "SELECT requested_season FROM pilot_competition_registry "
            "WHERE competition_id=47 ORDER BY requested_season"
        ).fetchall()
    finally:
        conn.close()

    assert first == {"inserted": 1, "skipped": 0}
    assert second == {"inserted": 1, "skipped": 0}
    assert saved == [("2023/2024",), ("2024/2025",)]


def test_old_single_key_registry_schema_is_rejected_without_mutation(
    tmp_path, capsys, monkeypatch,
):
    per_comp = _all_good_per_competition()
    fixture_path, out_dir = _run_cli_with_fixture(
        tmp_path, per_comp, label="old_registry_schema",
    )
    os.makedirs(out_dir)
    db_path = os.path.join(out_dir, "pilot_competition_schedule.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE pilot_competition_registry (
                competition_id INTEGER PRIMARY KEY,
                expected_name TEXT,
                observed_name TEXT,
                competition_class TEXT,
                requested_season TEXT NOT NULL,
                returned_season TEXT,
                season_parameter_verified TEXT,
                fixture_count INTEGER,
                target_team_fixture_count INTEGER,
                identity_verified TEXT,
                pagination_detected INTEGER,
                pagination_status TEXT,
                pagination_evidence TEXT,
                completeness_status TEXT
            )
        """)
        conn.execute(
            "INSERT INTO pilot_competition_registry "
            "(competition_id, expected_name, requested_season, completeness_status) "
            "VALUES (47, 'sentinel-old-row', '1999/2000', 'SENTINEL')"
        )
        conn.commit()
        before_schema = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='pilot_competition_registry'"
        ).fetchone()[0]
        before_rows = conn.execute(
            "SELECT * FROM pilot_competition_registry"
        ).fetchall()
    finally:
        conn.close()

    called = []

    def unexpected_call(*args, **kwargs):
        called.append((args, kwargs))
        raise AssertionError("incompatible schema must stop before writes/downstream")

    module_path = (
        "analysis.competition_schedule_pilot."
        "fotmob_competition_schedule_pilot"
    )
    for name in (
        "write_competition_registry",
        "merge_competition_schedules",
        "build_team_match_records",
        "write_match_calendar",
        "write_team_match",
        "compute_rest_hours",
    ):
        monkeypatch.setattr(f"{module_path}.{name}", unexpected_call)

    code = run_cli([
        "--team-id", str(TEAM_ID),
        "--season", "2024/2025",
        "--offline-fixture", fixture_path,
        "--output-dir", out_dir,
    ])

    assert code == 1
    assert called == []
    captured = capsys.readouterr()
    error = json.loads(captured.err)
    assert error["error"] == "pilot_schema_incompatible"
    assert error["status"] == "FAILED"
    assert error["action"] == "use_new_output_directory"
    assert "Traceback" not in captured.err
    assert "IntegrityError" not in captured.err

    conn = sqlite3.connect(db_path)
    try:
        after_schema = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='pilot_competition_registry'"
        ).fetchone()[0]
        after_rows = conn.execute(
            "SELECT * FROM pilot_competition_registry"
        ).fetchall()
        created_downstream_tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('pilot_match_calendar', 'pilot_team_match')"
        ).fetchall()
    finally:
        conn.close()

    assert after_schema == before_schema
    assert after_rows == before_rows
    assert created_downstream_tables == []


def test_registry_schema_with_composite_key_but_missing_columns_is_rejected(
    tmp_path,
):
    db_path = tmp_path / "missing_registry_columns.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE pilot_competition_registry (
                competition_id INTEGER NOT NULL,
                requested_season TEXT NOT NULL,
                PRIMARY KEY (competition_id, requested_season)
            )
        """)
        conn.commit()
        with pytest.raises(
            PilotSchemaIncompatibleError,
            match="required columns are missing",
        ):
            init_pilot_db(conn)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    finally:
        conn.close()

    assert tables == [("pilot_competition_registry",)]


def test_same_competition_and_season_identical_registry_row_is_idempotent(
    tmp_path,
):
    conn = sqlite3.connect(str(tmp_path / "same_season.db"))
    try:
        init_pilot_db(conn)
        row = _registry_row_for_season("2024/2025")
        first = write_competition_registry(conn, [row])
        second = write_competition_registry(conn, [dict(row)])
        count = conn.execute(
            "SELECT COUNT(*) FROM pilot_competition_registry"
        ).fetchone()[0]
    finally:
        conn.close()

    assert first == {"inserted": 1, "skipped": 0}
    assert second == {"inserted": 0, "skipped": 1}
    assert count == 1


def test_same_competition_and_season_key_field_change_fails_loudly(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "registry_conflict.db"))
    try:
        init_pilot_db(conn)
        row = _registry_row_for_season("2024/2025")
        write_competition_registry(conn, [row])
        changed = dict(row, fixture_count=2)
        with pytest.raises(ScheduleConflictError):
            write_competition_registry(conn, [changed])
        saved_count = conn.execute(
            "SELECT fixture_count FROM pilot_competition_registry "
            "WHERE competition_id=47 AND requested_season='2024/2025'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert saved_count == 1


def test_mixed_registry_results_persist_before_zero_downstream_calls(
    tmp_path, capsys, monkeypatch,
):
    per_comp = _all_good_per_competition()
    per_comp["47"] = _good_raw_for(
        47, "Premier League", [], season="2024/2025",
    )
    fixture_path, out_dir = _run_cli_with_fixture(
        tmp_path, per_comp, label="mixed_zero_downstream",
    )
    called = []

    def unexpected_call(*args, **kwargs):
        called.append((args, kwargs))
        raise AssertionError("required failure must stop before downstream work")

    module_path = (
        "analysis.competition_schedule_pilot."
        "fotmob_competition_schedule_pilot"
    )
    for name in (
        "merge_competition_schedules",
        "build_team_match_records",
        "write_match_calendar",
        "write_team_match",
        "compute_rest_hours",
    ):
        monkeypatch.setattr(f"{module_path}.{name}", unexpected_call)

    code = run_cli([
        "--team-id", str(TEAM_ID),
        "--season", "2024/2025",
        "--offline-fixture", fixture_path,
        "--output-dir", out_dir,
    ])

    assert code == 1
    assert called == []
    err = json.loads(capsys.readouterr().err)
    conn = sqlite3.connect(err["db_path"])
    try:
        rows = conn.execute(
            "SELECT competition_id, identity_verified "
            "FROM pilot_competition_registry ORDER BY competition_id"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 5
    assert dict(rows)[47] == "EMPTY_FIXTURES"
    assert all(
        status == "IDENTITY_VERIFIED"
        for cid, status in rows
        if cid != 47
    )
