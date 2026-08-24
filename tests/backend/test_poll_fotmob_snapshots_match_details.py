"""--write-match-details 回归(裁判/天气/场馆/图表配色写入路径,2026-08-20
补充场馆与天气描述,2026-08-24 补充主客配对配色四列)。

用真实赛前 payload 的离线 fixture(tests/fixtures/fotmob/prematch-5104961.json)
驱动 run_snapshot_poll,断言:
- 不开 --write-match-details 时行为与改动前完全一致,dim_match 不被触碰;
- 开启后只有 Referee/Temperature/Wind_Speed/Venue_Name/Venue_City/
  Venue_Country/Weather_Description/Home_Team_Color_Light/
  Home_Team_Color_Dark/Away_Team_Color_Light/Away_Team_Color_Dark 十一列被
  更新,status/kickoff/比分等其它列原封不动(不能因为顺带抓了这些字段就
  影响赛程判定);
- COALESCE 语义:新一轮解析对某个字段拿到 None 时,不覆盖已经写入的旧值。
"""

import json
import os

import pytest

from backend.cli.poll_fotmob_snapshots import run_snapshot_poll
from backend.db.connections import connect_rw

FIXTURE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "fixtures", "fotmob", "prematch-5104961.json",
)
MATCH_ID = 5104961


def _load_payload() -> dict:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _seed_match(
    conn,
    match_id=MATCH_ID,
    referee=None,
    temperature=None,
    wind_speed=None,
    venue_name=None,
    venue_city=None,
    venue_country=None,
    weather_description=None,
    home_color_light=None,
    home_color_dark=None,
    away_color_light=None,
    away_color_dark=None,
):
    conn.execute(
        """
        INSERT INTO dim_match
            (Match_ID, Season, League_ID, Date, Home_Team_ID, Away_Team_ID,
             Home_Team_Name, Away_Team_Name, status, Match_Round, Referee,
             Temperature, Wind_Speed, Venue_Name, Venue_City, Venue_Country,
             Weather_Description, Home_Team_Color_Light, Home_Team_Color_Dark,
             Away_Team_Color_Light, Away_Team_Color_Dark,
             kickoff_at_utc, kickoff_precision, kickoff_source)
        VALUES (?, '2026', 59, '2026-08-01', 100, 200, 'Home', 'Away', 'NotStarted', '17',
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '2026-08-01T14:00:00Z', 'exact', 'fotmob:fixtures')
        """,
        (
            match_id,
            referee,
            temperature,
            wind_speed,
            venue_name,
            venue_city,
            venue_country,
            weather_description,
            home_color_light,
            home_color_dark,
            away_color_light,
            away_color_dark,
        ),
    )
    conn.commit()


@pytest.fixture
def core_conn(data_dir):
    conn = connect_rw("core")
    yield conn
    conn.close()


def test_default_behavior_unchanged_without_flag(data_dir, core_conn):
    _seed_match(core_conn)
    payload = _load_payload()

    summary = run_snapshot_poll(
        now_iso="2026-07-31T00:00:00Z",
        offline_payloads={str(MATCH_ID): payload},
        match_ids=[MATCH_ID],
        write_match_details=False,
    )
    assert summary["match_details_written"] == 0

    row = core_conn.execute(
        "SELECT Referee, Temperature, Wind_Speed, Home_Team_Color_Light, Away_Team_Color_Light, "
        "status, kickoff_at_utc FROM dim_match WHERE Match_ID=?",
        (MATCH_ID,),
    ).fetchone()
    assert row["Referee"] is None
    assert row["Temperature"] is None
    assert row["Wind_Speed"] is None
    assert row["Home_Team_Color_Light"] is None
    assert row["Away_Team_Color_Light"] is None
    assert row["status"] == "NotStarted"
    assert row["kickoff_at_utc"] == "2026-08-01T14:00:00Z"


def test_write_match_details_updates_only_eleven_columns(data_dir, core_conn):
    _seed_match(core_conn)
    payload = _load_payload()

    summary = run_snapshot_poll(
        now_iso="2026-07-31T00:00:00Z",
        offline_payloads={str(MATCH_ID): payload},
        match_ids=[MATCH_ID],
        write_match_details=True,
    )
    assert summary["match_details_written"] == 1

    row = core_conn.execute(
        "SELECT Referee, Temperature, Wind_Speed, Venue_Name, Venue_City, Venue_Country, "
        "Weather_Description, Home_Team_Color_Light, Home_Team_Color_Dark, "
        "Away_Team_Color_Light, Away_Team_Color_Dark, status, kickoff_at_utc, "
        "kickoff_precision, kickoff_source, Match_Round, home_score, away_score "
        "FROM dim_match WHERE Match_ID=?",
        (MATCH_ID,),
    ).fetchone()
    assert row["Referee"] == "Mischa Kellerhals"
    assert row["Temperature"] == "18"
    assert row["Wind_Speed"] == "9"
    assert row["Venue_Name"] == "Nye Fredrikstad Stadion"
    assert row["Venue_City"] == "Fredrikstad"
    assert row["Venue_Country"] == "Norway"
    assert row["Weather_Description"] == "Partly Cloudy/Wind"
    assert row["Home_Team_Color_Light"] == "#f13c26"
    assert row["Home_Team_Color_Dark"] == "#f13c26"
    assert row["Away_Team_Color_Light"] == "#104070"
    assert row["Away_Team_Color_Dark"] == "#035db8"
    # 其余列必须原封不动——不能因为顺带抓了这些字段就影响赛程判定。
    assert row["status"] == "NotStarted"
    assert row["kickoff_at_utc"] == "2026-08-01T14:00:00Z"
    assert row["kickoff_precision"] == "exact"
    assert row["kickoff_source"] == "fotmob:fixtures"
    assert row["Match_Round"] == "17"
    assert row["home_score"] is None
    assert row["away_score"] is None


def test_real_prematch_fixture_lands_coach_and_bench_in_bronze(data_dir, core_conn):
    """真实赛前 payload(prematch-5104961.json,非合成)端到端落库,读回主教练与
    9 名替补——这是唯一用真实抓取产物覆盖 Fix 2(教练提取)与窗口放宽后"远端
    比赛也有替补"这条路径的测试。非 ASCII 教练名(Røjkjær)顺带覆盖 canonical
    JSON 的 UTF-8 往返(2026-08-18 Stage E)。"""
    _seed_match(core_conn)
    payload = _load_payload()

    summary = run_snapshot_poll(
        now_iso="2026-07-31T00:00:00Z",
        offline_payloads={str(MATCH_ID): payload},
        match_ids=[MATCH_ID],
    )
    assert summary["failures"] == []

    conn_odds = connect_rw("odds")
    try:
        row = conn_odds.execute(
            "SELECT payload_json, lineup_type FROM bronze_fm_lineup_snap"
            " WHERE fotmob_match_id=? ORDER BY id DESC LIMIT 1",
            (MATCH_ID,),
        ).fetchone()
    finally:
        conn_odds.close()
    assert row is not None
    assert row["lineup_type"] == "lastStarting11"

    snap = json.loads(row["payload_json"])
    assert snap["lineup_type"] == "lastStarting11"
    assert snap["home"]["coach"]["name"] == "Casper Røjkjær"
    assert snap["away"]["coach"]["name"] == "Andreas Tegström"
    assert len(snap["home"]["starters"]) == 11
    assert len(snap["home"]["subs"]) == 9
    assert len(snap["away"]["starters"]) == 11
    assert len(snap["away"]["subs"]) == 9


def test_write_match_details_never_regresses_known_value_to_null(data_dir, core_conn):
    """已知裁判/天气/场馆 + 一次拿到空值的解析(合成 payload,无
    content.weather/referee/matchFacts) → 旧值必须原样保留,不能被这次的
    None 覆盖成未知。"""
    _seed_match(
        core_conn,
        referee="Old Referee",
        temperature="20",
        wind_speed="5",
        venue_name="Old Stadium",
        venue_city="Old City",
        venue_country="Old Country",
        weather_description="Old Weather",
        home_color_light="#aaaaaa",
        home_color_dark="#bbbbbb",
        away_color_light="#cccccc",
        away_color_dark="#dddddd",
    )
    empty_payload = {"general": {}, "header": {}, "content": {}}

    summary = run_snapshot_poll(
        now_iso="2026-07-31T00:00:00Z",
        offline_payloads={str(MATCH_ID): empty_payload},
        match_ids=[MATCH_ID],
        write_match_details=True,
    )
    assert summary["match_details_written"] == 0   # 这一轮十一个字段全 None,不算"写入"

    row = core_conn.execute(
        "SELECT Referee, Temperature, Wind_Speed, Venue_Name, Venue_City, Venue_Country, "
        "Weather_Description, Home_Team_Color_Light, Home_Team_Color_Dark, "
        "Away_Team_Color_Light, Away_Team_Color_Dark FROM dim_match WHERE Match_ID=?",
        (MATCH_ID,),
    ).fetchone()
    assert row["Referee"] == "Old Referee"
    assert row["Temperature"] == "20"
    assert row["Wind_Speed"] == "5"
    assert row["Venue_Name"] == "Old Stadium"
    assert row["Venue_City"] == "Old City"
    assert row["Venue_Country"] == "Old Country"
    assert row["Weather_Description"] == "Old Weather"
    assert row["Home_Team_Color_Light"] == "#aaaaaa"
    assert row["Home_Team_Color_Dark"] == "#bbbbbb"
    assert row["Away_Team_Color_Light"] == "#cccccc"
    assert row["Away_Team_Color_Dark"] == "#dddddd"


# ── 0010 迁移新列(场地明细/天气枚举/裁判信息卡,2026-08-24)────────────────


def test_write_match_details_lands_0010_venue_and_weather_columns(data_dir, core_conn):
    """真实 fixture 驱动:Stadium 的 capacity/surface/lat/long 与 weather 的
    localizedKey/iconCode 落进 0010 新列。该 fixture 的 Referee(挪超)没有
    id/stats——对应列必须如实 NULL,不编造。"""
    _seed_match(core_conn)
    payload = _load_payload()

    run_snapshot_poll(
        now_iso="2026-07-31T00:00:00Z",
        offline_payloads={str(MATCH_ID): payload},
        match_ids=[MATCH_ID],
        write_match_details=True,
    )

    row = core_conn.execute(
        "SELECT Venue_Capacity, Venue_Surface, Venue_Lat, Venue_Long, "
        "Weather_Localized_Key, Weather_Icon_Code, Referee_ID, Referee_Country, "
        "Referee_Country_Code, Referee_Stats_Json FROM dim_match WHERE Match_ID=?",
        (MATCH_ID,),
    ).fetchone()
    assert row["Venue_Capacity"] == 12565
    assert row["Venue_Surface"] == "artificial turf"
    assert row["Venue_Lat"] == pytest.approx(59.21306014)
    assert row["Venue_Long"] == pytest.approx(10.928134918)
    assert row["Weather_Localized_Key"] == "weather_condition_windy"
    assert row["Weather_Icon_Code"] == 24
    # 该 fixture 的 Referee 没有 id/stats(挪超),country/countryCode 有值。
    assert row["Referee_ID"] is None
    assert row["Referee_Country"] == "Norway"
    assert row["Referee_Country_Code"] == "NOR"
    assert row["Referee_Stats_Json"] is None


def test_write_match_details_lands_referee_stats_json(data_dir, core_conn):
    """合成 stats 齐全的裁判(实网 LaLiga 形状,含 perMatch 两项的联赛均值与
    服务端评级)→ Referee_Stats_Json 原样落库,不加工数值。"""
    _seed_match(core_conn)
    stats = [
        {"type": "matches", "value": 38, "valueType": "total"},
        {"type": "yellowCards", "value": 4.11, "valueType": "perMatch",
         "average": 4.49, "total": 156, "averageType": "below",
         "fillPercentage": 37.07, "averagePercentage": 50},
        {"type": "fouls", "value": 24.74, "valueType": "perMatch",
         "average": 25.05, "total": 767, "averageType": "average",
         "fillPercentage": 45.49, "averagePercentage": 50},
    ]
    payload = {
        "general": {}, "header": {},
        "content": {"matchFacts": {"infoBox": {"Referee": {
            "text": "Victor García Verdura", "id": 1001072330,
            "country": "Spain", "countryCode": "ESP", "stats": stats,
        }}}},
    }

    run_snapshot_poll(
        now_iso="2026-07-31T00:00:00Z",
        offline_payloads={str(MATCH_ID): payload},
        match_ids=[MATCH_ID],
        write_match_details=True,
    )

    row = core_conn.execute(
        "SELECT Referee, Referee_ID, Referee_Stats_Json FROM dim_match WHERE Match_ID=?",
        (MATCH_ID,),
    ).fetchone()
    assert row["Referee"] == "Victor García Verdura"
    assert row["Referee_ID"] == 1001072330
    stored = json.loads(row["Referee_Stats_Json"])
    assert stored == stats  # 原样落库,零加工——averageType 是服务端评级
