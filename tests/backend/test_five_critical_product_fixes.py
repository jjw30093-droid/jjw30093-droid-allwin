"""FIVE_CRITICAL_PRODUCT_FIXES_V1 的后端产品契约。

全部数据均为内存 SQLite 或 pytest tmp_path，不读取/写入真实数据库。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.commands.predictions import (
    get_or_create_model_version,
    publish_snapshot,
    register_snapshot,
)
from backend.content_status import project_freshness, public_status_for_match
from backend.db.connections import connect_rw
from backend.queries.matches import list_matches, standings
from backend.queries.teams import display_name_for_team, team_display_map
from tests.backend.authflow import wechat_scan_login
from tests.backend.coreseed import insert_match, seed_basic_core


NOW = datetime(2026, 7, 30, 0, 0, tzinfo=timezone.utc)


def _status(
    *,
    last_success: str | None,
    next_planned: str | None,
    state: str = "LIVE",
) -> dict:
    return {
        "state": state,
        "last_success_sync_at": last_success,
        "next_planned_sync_at": next_planned,
    }


@pytest.mark.parametrize(
    ("kickoff", "last_success", "next_planned", "expected"),
    [
        # T>72h:初始低频快照在下一计划时间前仍可用。
        ("2026-08-05T12:00:00Z", "2026-07-29T12:00:00Z", "2026-08-02T12:00:00Z", "FRESH"),
        # T-72h 至 T-2h:15 分钟 cadence + 集中定义的 grace。
        ("2026-08-01T12:00:00Z", "2026-07-29T23:43:00Z", "2026-07-29T23:58:00Z", "FRESH"),
        ("2026-08-01T12:00:00Z", "2026-07-29T23:30:00Z", "2026-07-29T23:45:00Z", "STALE"),
        # T-2h 至开球:5 分钟 cadence + grace。
        ("2026-07-30T01:00:00Z", "2026-07-29T23:54:00Z", "2026-07-29T23:59:00Z", "FRESH"),
        ("2026-07-30T01:00:00Z", "2026-07-29T23:50:00Z", "2026-07-29T23:55:00Z", "STALE"),
        # 从未成功、开球后分别不可用/过期。
        ("2026-08-01T12:00:00Z", None, None, "UNAVAILABLE"),
        ("2026-07-29T23:59:00Z", "2026-07-29T23:55:00Z", None, "STALE"),
    ],
)
def test_freshness_projection_has_fixed_clock_and_polling_windows(
    kickoff: str,
    last_success: str | None,
    next_planned: str | None,
    expected: str,
) -> None:
    assert (
        project_freshness(
            _status(last_success=last_success, next_planned=next_planned),
            kickoff_at_utc=kickoff,
            now=NOW,
        )
        == expected
    )


def test_explicit_failure_stays_stale_and_old_live_marker_cannot_override_deadline() -> None:
    failed = _status(
        state="STALE",
        last_success="2026-07-29T23:59:00Z",
        next_planned="2026-07-30T00:04:00Z",
    )
    assert (
        project_freshness(
            failed,
            kickoff_at_utc="2026-07-30T01:00:00Z",
            now=NOW,
        )
        == "STALE"
    )

    historical_counterexample = _status(
        last_success="2026-07-28T07:36:00Z",
        next_planned="2026-07-29T15:36:00Z",
    )
    assert (
        project_freshness(
            historical_counterexample,
            kickoff_at_utc="2026-07-31T17:00:00Z",
            now=NOW,
        )
        == "STALE"
    )


def test_public_projection_uses_same_freshness_for_every_caller(tmp_path) -> None:
    status_path = tmp_path / "content_status.json"
    status_path.write_text(
        json.dumps(
            {
                "match_id": 5104968,
                "state": "LIVE",
                "last_success_sync_at": "2026-07-28T07:36:00Z",
                "next_planned_sync_at": "2026-07-29T15:36:00Z",
                "probability_source": "MARKET_BASELINE",
            }
        ),
        encoding="utf-8",
    )
    projected = public_status_for_match(
        5104968,
        kickoff_at_utc="2026-07-31T17:00:00Z",
        now=NOW,
        path=status_path,
    )
    assert projected["state"] == "STALE"
    assert "LIVE" not in projected.values()


def _core() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE dim_match (
          Match_ID INTEGER PRIMARY KEY,
          Season TEXT NOT NULL,
          League_ID INTEGER NOT NULL,
          Date TEXT NOT NULL,
          kickoff_at_utc TEXT,
          Match_Round TEXT,
          status TEXT NOT NULL,
          Home_Team_ID INTEGER,
          Away_Team_ID INTEGER,
          Home_Team_Name TEXT,
          Away_Team_Name TEXT,
          home_score INTEGER,
          away_score INTEGER
        );
        CREATE TABLE dim_team_i18n (
          Team_ID INTEGER PRIMARY KEY,
          name_en TEXT,
          name_zh TEXT,
          source TEXT,
          updated_at TEXT
        );
        CREATE TABLE fact_league_table (
          League_ID INTEGER,
          Season TEXT,
          table_type TEXT,
          Team_ID INTEGER,
          Team_Name TEXT,
          position INTEGER,
          played INTEGER,
          wins INTEGER,
          draws INTEGER,
          losses INTEGER,
          goals_for INTEGER,
          goals_against INTEGER,
          goal_diff INTEGER,
          points INTEGER,
          qual_color TEXT,
          xg REAL,
          xg_conceded REAL,
          x_points REAL,
          x_position INTEGER,
          extra_json TEXT
        );
        """
    )
    return conn


def _match(
    conn: sqlite3.Connection,
    match_id: int,
    kickoff: str,
    home_id: int,
    away_id: int,
    home: str,
    away: str,
) -> None:
    conn.execute(
        """INSERT INTO dim_match
           (Match_ID,Season,League_ID,Date,kickoff_at_utc,Match_Round,status,
            Home_Team_ID,Away_Team_ID,Home_Team_Name,Away_Team_Name)
           VALUES (?,'2026',59,substr(?,1,10),?,'1','NotStarted',?,?,?,?)""",
        (match_id, kickoff, kickoff, home_id, away_id, home, away),
    )


def test_match_query_uses_exact_kickoff_stable_order_windows_and_search() -> None:
    conn = _core()
    try:
        _match(conn, 5104962, "2026-08-01T03:00:00Z", 1, 2, "Rosenborg", "Brann")
        _match(conn, 5104968, "2026-08-01T01:00:00Z", 3, 4, "Valerenga", "HamKam")
        _match(conn, 5104967, "2026-08-01T01:00:00Z", 5, 6, "Viking", "Molde")
        _match(conn, 5104999, "2026-08-10T01:00:00Z", 7, 8, "A", "B")
        conn.execute(
            "INSERT INTO dim_team_i18n VALUES (3,'Valerenga','瓦勒伦加','reviewed','')"
        )

        result = list_matches(
            conn,
            {59},
            status="upcoming",
            window="7d",
            now=NOW,
            limit=20,
        )
        assert [row["match_id"] for row in result["matches"]] == [
            5104967,
            5104968,
            5104962,
        ]
        assert result["total"] == 3
        prioritized = list_matches(
            conn,
            {59},
            status="upcoming",
            window="7d",
            now=NOW,
            priority_match_ids={5104968, 5104962},
            limit=20,
        )
        assert [row["match_id"] for row in prioritized["matches"]] == [
            5104968,
            5104962,
            5104967,
        ]

        by_zh = list_matches(conn, {59}, status="upcoming", query="瓦勒伦加", now=NOW)
        by_en = list_matches(conn, {59}, status="upcoming", query="valerenga", now=NOW)
        by_alias = list_matches(
            conn,
            {59},
            status="upcoming",
            query="vif",
            query_team_ids={3},
            now=NOW,
        )
        assert [row["match_id"] for row in by_zh["matches"]] == [5104968]
        assert [row["match_id"] for row in by_en["matches"]] == [5104968]
        assert [row["match_id"] for row in by_alias["matches"]] == [5104968]
    finally:
        conn.close()


def test_top_priority_match_ids_outranks_plain_priority_and_stays_backward_compatible() -> None:
    """首页重点位确定性选场(2026-08-16):单层 priority_match_ids 只能表达
    "这批比赛都排最前"——当这批比赛数量超过 limit 时,里面真正更值得优先的
    一小撮(比如"免费且已发布概率"的比赛)仍可能被同一大类里开球更早的其它
    比赛挤出分页截断线之外。新增 top_priority_match_ids 是比 priority_match_ids
    更强的第二档,专门解决这个问题。

    未传 top_priority_match_ids 时必须与改动前逐字节同一行为(向后兼容,
    不影响任何既有调用方——见上面 test_match_query_uses_exact_kickoff_...
    里那个只传 priority_match_ids 的既有断言)。"""
    conn = _core()
    try:
        _match(conn, 6200001, "2026-07-31T01:00:00Z", 1, 2, "A", "B")
        _match(conn, 6200002, "2026-07-31T01:00:00Z", 3, 4, "C", "D")
        _match(conn, 6200003, "2026-07-31T03:00:00Z", 5, 6, "E", "F")
        _match(conn, 6200004, "2026-08-04T01:00:00Z", 7, 8, "G", "H")

        # top_priority_match_ids 缺省:与改动前的单层 priority_match_ids 语义
        # 完全一致(6200002/6200003 排最前,组内按开球时间;其余按开球时间
        # 跟在后面——6200004 虽未被优先,但仍在 7 天窗口内,不会从结果里消失)。
        legacy = list_matches(
            conn,
            {59},
            status="upcoming",
            window="7d",
            now=NOW,
            priority_match_ids={6200002, 6200003},
            limit=20,
        )
        assert [row["match_id"] for row in legacy["matches"]] == [
            6200002,
            6200003,
            6200001,
            6200004,
        ]

        # top_priority_match_ids={6200004}:即使 6200004 开球最晚、且不在
        # priority_match_ids 里,也必须排在 priority_match_ids 那一档之前——
        # 这正是首页"免费且已发布概率"的比赛必须挤进 limit 截断线以内所需要的
        # 更强优先级。
        boosted = list_matches(
            conn,
            {59},
            status="upcoming",
            window="7d",
            now=NOW,
            priority_match_ids={6200002, 6200003},
            top_priority_match_ids={6200004},
            limit=20,
        )
        assert [row["match_id"] for row in boosted["matches"]] == [
            6200004,
            6200002,
            6200003,
            6200001,
        ]

        # limit 截断场景:即便 top tier 只有一个名额,也必须挤进第一页——
        # 这正是首页 limit=8 请求依赖的真实行为。
        truncated = list_matches(
            conn,
            {59},
            status="upcoming",
            window="7d",
            now=NOW,
            priority_match_ids={6200002, 6200003},
            top_priority_match_ids={6200004},
            limit=1,
        )
        assert [row["match_id"] for row in truncated["matches"]] == [6200004]
    finally:
        conn.close()


def test_team_projection_prefers_reviewed_name_then_provider_then_safe_fallback() -> None:
    conn = _core()
    try:
        _match(conn, 1, "2026-08-01T01:00:00Z", 8402, 8478, "Bodø/Glimt", "Brann")
        conn.execute(
            "INSERT INTO dim_team_i18n VALUES (8402,'Bodo/Glimt','博德闪耀','reviewed','')"
        )
        display = team_display_map(conn)
        assert display_name_for_team(8402, display=display) == "博德闪耀"
        assert display_name_for_team(8478, display=display) == "Brann"
        assert display_name_for_team(9999, provider_name="Team 9999", display=display) == "球队名称待同步"
    finally:
        conn.close()


def test_match_calendar_windows_and_pagination_are_complete() -> None:
    conn = _core()
    try:
        rows = [
            (100, "2026-07-30T10:00:00Z"),
            (101, "2026-07-30T20:00:00Z"),
            (102, "2026-08-01T10:00:00Z"),
            (103, "2026-08-05T10:00:00Z"),
        ]
        for match_id, kickoff in rows:
            _match(conn, match_id, kickoff, match_id, match_id + 1000, "Home", "Away")

        today = list_matches(conn, {59}, status="upcoming", window="today", now=NOW)
        tomorrow = list_matches(
            conn, {59}, status="upcoming", window="tomorrow", now=NOW
        )
        three_days = list_matches(conn, {59}, status="upcoming", window="3d", now=NOW)
        seven_days = list_matches(conn, {59}, status="upcoming", window="7d", now=NOW)
        assert [row["match_id"] for row in today["matches"]] == [100]
        assert [row["match_id"] for row in tomorrow["matches"]] == [101]
        assert [row["match_id"] for row in three_days["matches"]] == [100, 101, 102]
        assert [row["match_id"] for row in seven_days["matches"]] == [100, 101, 102, 103]

        page_1 = list_matches(
            conn, {59}, status="upcoming", window="7d", now=NOW, limit=2, offset=0
        )
        page_2 = list_matches(
            conn, {59}, status="upcoming", window="7d", now=NOW, limit=2, offset=2
        )
        ids = [row["match_id"] for row in page_1["matches"] + page_2["matches"]]
        assert ids == [100, 101, 102, 103]
        assert len(ids) == len(set(ids))
    finally:
        conn.close()


def test_standings_never_projects_numeric_team_placeholder() -> None:
    conn = _core()
    try:
        for position in range(1, 17):
            team_id = 8400 + position
            provider_name = f"Provider Club {position}"
            conn.execute(
                """INSERT INTO fact_league_table
                   (League_ID,Season,table_type,Team_ID,Team_Name,position,played,
                    wins,draws,losses,goals_for,goals_against,goal_diff,points,qual_color)
                   VALUES (59,'2026','all',?,?,?,10,5,2,3,20,15,5,17,NULL)""",
                (team_id, provider_name, position),
            )
        result = standings(conn, 59, "2026")
        names = [row["team"]["name"] for row in result["rows"]]
        assert len(names) == 16
        assert all(not name.startswith("Team ") for name in names)
        assert names[0] == "Provider Club 1"
    finally:
        conn.close()


@pytest.fixture
def product_seeded(data_dir):
    # 开球时刻动态取"执行时 +3 天":登记簿正确拒绝开球后发布的预测,写死日期
    # 会在日历越过它之后让 publish_snapshot 永远失败(2026-08-01 硬编码在
    # 2026-08-04 起即为时间炸弹)。与 test_api_v1.seeded 同款动态布景。
    kickoff_dt = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0)
    kickoff_at_utc = kickoff_dt.isoformat().replace("+00:00", "Z")
    match_date = kickoff_dt.date().isoformat()

    seed_basic_core(data_dir)
    core = connect_rw("core")
    insert_match(
        core,
        5104968,
        league_id=59,
        season="2026",
        date=match_date,
        home_id=8402,
        away_id=8478,
        home="Bodø/Glimt",
        away="Brann",
        kickoff_at_utc=kickoff_at_utc,
    )
    for position in range(1, 17):
        team_id = 8400 + position
        core.execute(
            """INSERT INTO fact_league_table
               (League_ID,Season,table_type,Team_ID,Team_Name,position,played,wins,
                draws,losses,goals_for,goals_against,goal_diff,points,qual_color)
               VALUES (59,'2026','all',?,?,?,10,5,2,3,20,15,5,17,NULL)""",
            (team_id, f"Provider Club {position}", position),
        )
    core.commit()
    core.close()

    platform = connect_rw("platform")
    get_or_create_model_version(
        platform,
        "product-fix-model",
        "market-baseline",
        applicable_league_ids=[59],
    )
    snapshot = register_snapshot(
        platform,
        match_id=5104968,
        kickoff_at_utc=kickoff_at_utc,
        kickoff_precision="exact",
        kickoff_source="test:product-fix",
        model_version_id="product-fix-model",
        league_id=59,
        home_win=0.4,
        draw=0.3,
        away_win=0.3,
        status="draft",
    )
    publish_snapshot(platform, snapshot, actor=None)
    platform.close()

    odds = connect_rw("odds")
    odds.execute(
        """INSERT INTO dim_match_xref
           (fotmob_match_id,provider,provider_match_id,confidence,verified,method,
            review_status,created_at,updated_at)
           VALUES (5104968,'nowgoal','2912857',1,1,'manual','confirmed',
                   '2026-07-29T00:00:00Z','2026-07-29T00:00:00Z')"""
    )
    odds.execute(
        """INSERT INTO bronze_ng_odds_snap
           (provider_match_id,market,company_id,company_name,market_phase,payload_json,
            payload_hash,observed_at,ingested_at)
           VALUES ('2912857','1x2','3','Pinnacle','pre_match','{}','hash',
                   '2026-07-29T00:00:00Z','2026-07-29T00:00:00Z')"""
    )
    odds.close()

    (data_dir / "content_status.json").write_text(
        json.dumps(
            {
                "match_id": 5104968,
                "league_id": 59,
                "state": "LIVE",
                "last_success_sync_at": "2026-07-28T07:36:00Z",
                "next_planned_sync_at": "2026-07-29T15:36:00Z",
                "probability_source": "MARKET_BASELINE",
            }
        ),
        encoding="utf-8",
    )
    return data_dir


def test_league_catalog_and_match_content_filters_are_data_driven(
    app, product_seeded
) -> None:
    client = TestClient(app)
    leagues = client.get("/api/v1/leagues").json()
    eliteserien = next(row for row in leagues if row["league_id"] == 59)
    assert eliteserien["name_zh"] == "挪威超"
    assert eliteserien["current_season"] == "2026"
    assert eliteserien["data_status"] == "AVAILABLE"
    # 2026-08-16 产品权限口径修正:除"每日精选"外全站比赛内容全部免费,
    # 包括匿名——挪超(原 league:lottery)不再需要登录,LeagueInfo 也不再
    # 暴露 accessible 字段(该字段已从响应模型整体删除)。这条断言正是要
    # 推翻的旧规则(此前匿名不可访问,accessible 恒为 False)。
    assert "accessible" not in eliteserien

    analysis = client.get(
        "/api/v1/matches?league_id=59&status=upcoming&content=analysis"
    ).json()
    odds = client.get(
        "/api/v1/matches?league_id=59&status=upcoming&content=odds"
    ).json()
    assert [row["match_id"] for row in analysis["matches"]] == [5104968]
    assert [row["match_id"] for row in odds["matches"]] == [5104968]


def test_list_detail_and_standings_share_truthful_public_projection(
    app, product_seeded
) -> None:
    client = TestClient(app)
    # 挪超(59)2026-08-16 起匿名即可访问(登录与内容分层已解耦);这里登录
    # 只是为了同时覆盖"登录用户看到与匿名一致内容"这条路径,不是访问前提。
    wechat_scan_login(client, ip="203.0.113.202")
    listed = client.get(
        "/api/v1/matches?league_id=59&status=upcoming"
    ).json()["matches"][0]
    detail = client.get("/api/v1/matches/5104968").json()["match"]
    assert listed["sync_state"] == detail["sync_state"] == "STALE"
    assert listed["home"]["name"] == detail["home"]["name"] == "Bodø/Glimt"

    rows = client.get("/api/v1/leagues/59/standings").json()["rows"]
    assert len(rows) == 16
    assert all(not row["team"]["name"].startswith("Team ") for row in rows)
