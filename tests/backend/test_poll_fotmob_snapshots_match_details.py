"""--write-match-details 回归(本轮任务 §4:周末赛前裁判/天气实测的写入路径)。

用真实赛前 payload 的离线 fixture(tests/fixtures/fotmob/prematch-5104961.json)
驱动 run_snapshot_poll,断言:
- 不开 --write-match-details 时行为与改动前完全一致,dim_match 不被触碰;
- 开启后只有 Referee/Temperature/Wind_Speed 三列被更新,status/kickoff/比分等其它
  列原封不动(不能因为顺带抓了裁判/天气就影响赛程判定);
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


def _seed_match(conn, match_id=MATCH_ID, referee=None, temperature=None, wind_speed=None):
    conn.execute(
        """
        INSERT INTO dim_match
            (Match_ID, Season, League_ID, Date, Home_Team_ID, Away_Team_ID,
             Home_Team_Name, Away_Team_Name, status, Match_Round, Referee,
             Temperature, Wind_Speed, kickoff_at_utc, kickoff_precision, kickoff_source)
        VALUES (?, '2026', 59, '2026-08-01', 100, 200, 'Home', 'Away', 'NotStarted', '17',
                ?, ?, ?, '2026-08-01T14:00:00Z', 'exact', 'fotmob:fixtures')
        """,
        (match_id, referee, temperature, wind_speed),
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
        "SELECT Referee, Temperature, Wind_Speed, status, kickoff_at_utc FROM dim_match WHERE Match_ID=?",
        (MATCH_ID,),
    ).fetchone()
    assert row["Referee"] is None
    assert row["Temperature"] is None
    assert row["Wind_Speed"] is None
    assert row["status"] == "NotStarted"
    assert row["kickoff_at_utc"] == "2026-08-01T14:00:00Z"


def test_write_match_details_updates_only_three_columns(data_dir, core_conn):
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
        "SELECT Referee, Temperature, Wind_Speed, status, kickoff_at_utc, kickoff_precision, "
        "kickoff_source, Match_Round, home_score, away_score FROM dim_match WHERE Match_ID=?",
        (MATCH_ID,),
    ).fetchone()
    assert row["Referee"] == "Mischa Kellerhals"
    assert row["Temperature"] == "18"
    assert row["Wind_Speed"] == "9"
    # 其余列必须原封不动——不能因为顺带抓了裁判/天气就影响赛程判定。
    assert row["status"] == "NotStarted"
    assert row["kickoff_at_utc"] == "2026-08-01T14:00:00Z"
    assert row["kickoff_precision"] == "exact"
    assert row["kickoff_source"] == "fotmob:fixtures"
    assert row["Match_Round"] == "17"
    assert row["home_score"] is None
    assert row["away_score"] is None


def test_write_match_details_never_regresses_known_value_to_null(data_dir, core_conn):
    """已知裁判/天气 + 一次拿到空值的解析(合成 payload,无 content.weather/referee)
    → 旧值必须原样保留,不能被这次的 None 覆盖成未知。"""
    _seed_match(core_conn, referee="Old Referee", temperature="20", wind_speed="5")
    empty_payload = {"general": {}, "header": {}, "content": {}}

    summary = run_snapshot_poll(
        now_iso="2026-07-31T00:00:00Z",
        offline_payloads={str(MATCH_ID): empty_payload},
        match_ids=[MATCH_ID],
        write_match_details=True,
    )
    assert summary["match_details_written"] == 0   # 这一轮三个字段全 None,不算"写入"

    row = core_conn.execute(
        "SELECT Referee, Temperature, Wind_Speed FROM dim_match WHERE Match_ID=?",
        (MATCH_ID,),
    ).fetchone()
    assert row["Referee"] == "Old Referee"
    assert row["Temperature"] == "20"
    assert row["Wind_Speed"] == "5"
