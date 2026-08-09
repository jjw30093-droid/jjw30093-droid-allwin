"""审计脚本自检:最小临时 SQLite fixture,验证 audit_top5_historical_core.py 的
核心检测函数真的能检出问题、且对干净数据不误报(§十五要求)。

只读约束同样适用于被审计对象以外的部分:本脚本自己创建的 fixture 数据库
只写在 pytest 的 tmp_path(/private/var/folders/.../T/pytest-of-*/…下,不是
真实 data/*.db,也不是 /tmp/allwin-data-quality-* 快照本身。

用法:
    .venv/bin/python -m pytest analysis/data_quality/test_audit_fixture.py -q
"""

import json
import sqlite3
from pathlib import Path

import pytest

from analysis.data_quality.audit_top5_historical_core import (
    common_columns,
    connect_ro,
    cross_table_consistency_rows,
    dim_match_anomalies,
    dim_player_name_diff_summary,
    dim_player_parity_rows,
    enrich_field_coverage_with_deltas,
    feature_readiness_rows,
    field_coverage_rows,
    finding_row,
    grain_report,
    lineup_sub_time_coverage_rows,
    merge_parity_for_table,
    orphan_and_referential_anomalies,
    partition_summary_rows,
    player_stats_anomalies,
    register_udfs,
    schema_snapshot,
    shotmap_anomalies,
    team_stats_anomalies,
    CORE_TABLES,
    CsvWriter,
    FEATURE_FAMILIES,
    FIELD_COVERAGE_FIELDNAMES,
    FINDING_FIELDS,
    MERGE_PARITY_FIELDNAMES,
    SEASONS,
    _classify_name_diff,
    _events_field_specs,
    _lineup_field_specs,
    _parity_row,
    _player_field_specs,
    _shotmap_field_specs,
)


def _build_fixture_db(path: Path, *, inject_bugs: bool) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE dim_match (
            Match_ID INTEGER PRIMARY KEY, Season TEXT, League_ID INTEGER, Date TEXT,
            Home_Team_ID INTEGER, Away_Team_ID INTEGER, Home_Team_Name TEXT, Away_Team_Name TEXT,
            home_score INTEGER, away_score INTEGER, status TEXT, Referee TEXT, Match_Round TEXT,
            Temperature TEXT, Wind_Speed TEXT, Who_Lost_On_Penalties TEXT
        );
        CREATE TABLE dim_player (Player_ID TEXT PRIMARY KEY, Player_Name TEXT);
        CREATE TABLE fact_shotmap (
            Match_ID INTEGER, Player_ID TEXT, Team_ID INTEGER, Minute INTEGER, Period TEXT,
            X_Coord REAL, Y_Coord REAL, xG REAL, xGOT REAL, Situation TEXT, Outcome TEXT, Shot_Type TEXT
        );
        CREATE TABLE fact_player_match_stats (
            Match_ID INTEGER, Player_ID TEXT, Player_Opta_ID TEXT, Team_ID INTEGER,
            is_goalkeeper INTEGER, is_captain INTEGER, shirt_number TEXT, position_id INTEGER,
            usual_position TEXT, rating_title REAL, minutes_played INTEGER, player_name TEXT,
            goals REAL, assists REAL, expected_goals REAL, expected_assists REAL, xg_and_xa REAL,
            expected_goals_on_target_variant REAL, expected_goals_non_penalty REAL,
            ShotsOnTarget REAL, ShotsOffTarget REAL, shot_accuracy REAL, blocked_shots REAL,
            big_chance_missed_title REAL, shots_woodwork REAL, missed_penalty REAL, shotmap TEXT,
            accurate_passes REAL, chances_created REAL, big_chance_created_team_title REAL,
            passes_into_final_third REAL, accurate_crosses REAL, long_balls_accurate REAL,
            touches REAL, touches_opp_box REAL, dispossessed REAL, dribbles_succeeded REAL,
            "matchstats.headers.tackles" REAL, shot_blocks REAL, clearances REAL,
            headed_clearance REAL, interceptions REAL, recoveries REAL, dribbled_past REAL,
            ground_duels_won REAL, aerials_won REAL, defensive_actions REAL, last_man_tackle REAL,
            clearance_off_the_line REAL, duel_won REAL, duel_lost REAL, fouls REAL, was_fouled REAL,
            penalties_won REAL, conceded_penalties REAL, errors_led_to_goal REAL, corners REAL,
            Offsides REAL, owngoal REAL, fantasy_points REAL, saves REAL, goals_conceded REAL,
            expected_goals_on_target_faced REAL, goals_prevented REAL, keeper_diving_save REAL,
            saves_inside_box REAL, keeper_sweeper REAL, punches REAL, player_throws REAL,
            keeper_high_claim REAL, saved_penalties REAL, saved_penalties_in_shootout REAL,
            physical_metrics_topspeed REAL, physical_metrics_distance_covered REAL,
            physical_metrics_walking REAL, physical_metrics_running REAL,
            physical_metrics_sprinting REAL, physical_metrics_number_of_sprints REAL
        );
        CREATE TABLE fact_team_match_stats (
            Match_ID INTEGER, Team_ID INTEGER, Period TEXT, Goals REAL, extra_json TEXT
        );
        CREATE TABLE fact_match_events (
            Match_ID INTEGER, event_index INTEGER, event_type TEXT, minute INTEGER,
            overload_time INTEGER, is_home INTEGER, home_score INTEGER, away_score INTEGER,
            player_id TEXT, player_name TEXT, card_type TEXT, assist_player_id TEXT,
            assist_player_name TEXT, sub_in_player_id TEXT, sub_in_player_name TEXT,
            sub_out_player_id TEXT, sub_out_player_name TEXT, minutes_added INTEGER,
            event_id TEXT, extra_json TEXT
        );
        CREATE TABLE fact_match_lineup (
            Match_ID INTEGER, Team_ID INTEGER, is_home INTEGER, formation TEXT, Player_ID TEXT,
            player_name TEXT, shirt_number TEXT, position_id INTEGER, usual_position_id INTEGER,
            is_starter INTEGER, is_captain INTEGER, country_code TEXT, market_value INTEGER,
            rating REAL, sub_in_time INTEGER, sub_out_time INTEGER, extra_json TEXT
        );
        """
    )

    # 两场干净的完赛比赛(47=EPL 基准联赛,53=Ligue1)
    conn.execute(
        "INSERT INTO dim_match VALUES (1,'2020/2021',47,'2020-08-01',100,200,'Home A','Away B',2,1,'Finish',NULL,'1',NULL,NULL,NULL)"
    )
    conn.execute(
        "INSERT INTO dim_match VALUES (2,'2020/2021',53,'2020-08-02',300,400,'Home C','Away D',1,1,'Finish',NULL,'1',NULL,NULL,NULL)"
    )
    conn.execute("INSERT INTO dim_player VALUES ('p1','Player One')")
    conn.execute("INSERT INTO dim_player VALUES ('p2','Player Two')")
    conn.execute("INSERT INTO dim_player VALUES ('p3','Player Three')")
    conn.execute("INSERT INTO dim_player VALUES ('p4','Player Four')")
    # 干净 shotmap:两场各一次射门,xG 合法
    conn.execute("INSERT INTO fact_shotmap VALUES (1,'p1',100,10,'FirstHalf',50.0,50.0,0.3,0.2,'RegularPlay','Goal','RightFoot')")
    conn.execute("INSERT INTO fact_shotmap VALUES (2,'p3',300,20,'FirstHalf',40.0,40.0,0.1,0.05,'RegularPlay','Miss','LeftFoot')")
    # 干净 team_match_stats(Period='All',两队,Goals 与 dim_match 对齐,合法 JSON)
    conn.execute(
        "INSERT INTO fact_team_match_stats VALUES (1,100,'All',2,'{\"BallPossesion\":55.0,\"expected_goals\":0.3}')"
    )
    conn.execute(
        "INSERT INTO fact_team_match_stats VALUES (1,200,'All',1,'{\"BallPossesion\":45.0,\"expected_goals\":0.5}')"
    )

    if inject_bugs:
        # bug 1: dim_match 里出现主客队 Team_ID 相同(应被 dim_match_anomalies 检出)
        conn.execute(
            "INSERT INTO dim_match VALUES (99,'2020/2021',47,'2020-09-01',500,500,'Same X','Same X',0,0,'Finish',NULL,'2',NULL,NULL,NULL)"
        )
        # bug 2: fact_shotmap 孤儿 Match_ID(不存在于 dim_match)
        conn.execute("INSERT INTO fact_shotmap VALUES (9999,'p1',100,10,'FirstHalf',50.0,50.0,0.1,0.1,'RegularPlay','Miss','RightFoot')")
        # bug 3: fact_shotmap 的 Team_ID 不属于比赛 1 的主客队(串场)
        conn.execute("INSERT INTO fact_shotmap VALUES (1,'p1',999,15,'FirstHalf',50.0,50.0,0.1,0.1,'RegularPlay','Miss','RightFoot')")
        # bug 4: xG 超出 [0,1]
        conn.execute("INSERT INTO fact_shotmap VALUES (2,'p4',400,30,'SecondHalf',60.0,60.0,1.7,1.5,'RegularPlay','Goal','Header')")
        # bug 5: 重复候选键(fact_player_match_stats 同 Match_ID+Player_ID+Team_ID 出现两行)
        insert_p2 = (
            "INSERT INTO fact_player_match_stats "
            "(Match_ID, Player_ID, Team_ID, is_goalkeeper, is_captain, shirt_number, "
            " position_id, usual_position, rating_title, minutes_played, player_name) "
            "VALUES (1, 'p2', 100, 0, 0, '9', 1, 'MF', 7.0, 90, 'Player Two')"
        )
        conn.execute(insert_p2)
        conn.execute(insert_p2)  # 重复
        # bug 6: fact_team_match_stats extra_json 非法 JSON
        conn.execute("INSERT INTO fact_team_match_stats VALUES (2,300,'All',1,'{not valid json')")
        conn.execute("INSERT INTO fact_team_match_stats VALUES (2,400,'All',1,'{\"BallPossesion\":50.0}')")
        # bug 7: 全 NULL 字段(is_captain 整个 fixture 里从未赋值——用于验证"全 NULL 检测")
        # (已通过上面 base 列表里 is_captain 传 0 部分覆盖,这里额外插一行 Offsides 全 NULL 的 player)
    else:
        # 干净对照:同样插一条 fact_player_match_stats,但不重复、不孤儿、不串场
        conn.execute(
            "INSERT INTO fact_player_match_stats "
            "(Match_ID, Player_ID, Team_ID, is_goalkeeper, is_captain, shirt_number, "
            " position_id, usual_position, rating_title, minutes_played, player_name) "
            "VALUES (1, 'p2', 100, 0, 0, '9', 1, 'MF', 7.0, 90, 'Player Two')"
        )
        conn.execute("INSERT INTO fact_team_match_stats VALUES (2,300,'All',1,'{\"BallPossesion\":55.0}')")
        conn.execute("INSERT INTO fact_team_match_stats VALUES (2,400,'All',1,'{\"BallPossesion\":45.0}')")

    conn.commit()
    conn.close()


@pytest.fixture
def buggy_db(tmp_path: Path) -> Path:
    p = tmp_path / "buggy.db"
    _build_fixture_db(p, inject_bugs=True)
    return p


@pytest.fixture
def clean_db(tmp_path: Path) -> Path:
    p = tmp_path / "clean.db"
    _build_fixture_db(p, inject_bugs=False)
    return p


def test_duplicate_key_detected(buggy_db: Path):
    conn = connect_ro(buggy_db, "fixture")
    report = grain_report(conn, "fact_player_match_stats", "League_ID IN (47,53)")
    assert report["duplicate_groups"] >= 1, "重复候选键必须被检出"
    conn.close()


def test_duplicate_key_not_flagged_when_clean(clean_db: Path):
    conn = connect_ro(clean_db, "fixture")
    report = grain_report(conn, "fact_player_match_stats", "League_ID IN (47,53)")
    assert report["duplicate_groups"] == 0, "干净数据不应误报重复"
    conn.close()


def test_orphan_match_id_detected(buggy_db: Path):
    conn = connect_ro(buggy_db, "fixture")
    findings = orphan_and_referential_anomalies(conn, "fixture", "m.League_ID IN (47,53)")
    orphan_findings = [f for f in findings if f.category == "fact.orphan_match_id"]
    assert orphan_findings, "孤儿 Match_ID 必须被检出"
    assert orphan_findings[0].count >= 1
    conn.close()


def test_team_not_in_match_detected(buggy_db: Path):
    conn = connect_ro(buggy_db, "fixture")
    findings = orphan_and_referential_anomalies(conn, "fixture", "m.League_ID IN (47,53)")
    wrong_team = [f for f in findings if f.category == "fact.team_not_in_match"]
    assert wrong_team, "Team_ID 不属于该场比赛主客队必须被检出(串场)"
    conn.close()


def test_same_team_home_away_detected(buggy_db: Path):
    conn = connect_ro(buggy_db, "fixture")
    findings = dim_match_anomalies(conn, "fixture", "League_ID IN (47,53)")
    same_team = [f for f in findings if f.category == "dim_match.same_team"]
    assert same_team, "主客队 Team_ID 相同必须被检出"
    conn.close()


def test_xg_out_of_range_detected(buggy_db: Path):
    conn = connect_ro(buggy_db, "fixture")
    findings = shotmap_anomalies(conn, "fixture", "m.League_ID IN (47,53)")
    xg_findings = [f for f in findings if f.category == "shotmap.xg_out_of_range"]
    assert xg_findings, "xG 超出 [0,1] 必须被检出"
    conn.close()


def test_invalid_json_detected(buggy_db: Path):
    conn = connect_ro(buggy_db, "fixture")
    findings = team_stats_anomalies(conn, "fixture", "m.League_ID IN (47,53)")
    invalid = [f for f in findings if f.category == "team_stats.invalid_json"]
    assert invalid, "非法 JSON 必须被检出"
    conn.close()


def test_clean_data_no_critical_or_high_false_positive(clean_db: Path):
    """正常干净数据不应触发 CRITICAL/HIGH 误报(允许 LOW/MEDIUM 观察性提示,
    如样本量太小导致的分位数噪声)。"""
    conn = connect_ro(clean_db, "fixture")
    lf_bare = "League_ID IN (47,53)"
    lf_m = "m.League_ID IN (47,53)"
    findings = (
        dim_match_anomalies(conn, "fixture", lf_bare)
        + orphan_and_referential_anomalies(conn, "fixture", lf_m)
        + shotmap_anomalies(conn, "fixture", lf_m)
        + team_stats_anomalies(conn, "fixture", lf_m)
    )
    bad = [f for f in findings if f.severity in ("CRITICAL", "HIGH")]
    assert not bad, f"干净数据不应产生 CRITICAL/HIGH 误报,实际: {[(f.category, f.count) for f in bad]}"
    conn.close()


def test_readonly_connection_rejects_missing_file(tmp_path: Path):
    with pytest.raises(SystemExit):
        connect_ro(tmp_path / "does_not_exist.db", "missing")


def test_schema_snapshot_reads_real_columns(clean_db: Path):
    conn = connect_ro(clean_db, "fixture")
    snap = schema_snapshot(conn, CORE_TABLES)
    assert "Match_ID" in [c["name"] for c in snap["dim_match"]]
    assert "extra_json" in [c["name"] for c in snap["fact_team_match_stats"]]
    conn.close()


# ── merge_parity_for_table mutation 矩阵(§四,直接调用正式函数,不重新实现比较器) ──
#
# 两个结构相同的最小 fixture 库(core/verify),对 verify 一侧做单点注入,
# core 一侧保持不变,验证真实的 merge_parity_for_table() 是否检出差异。
# League_ID=47(EPL)/Match_ID=1 用作两库共同比较的目标场次。


def _paired_dbs(tmp_path: Path):
    core_path = tmp_path / "paired_core.db"
    verify_path = tmp_path / "paired_verify.db"
    _build_fixture_db(core_path, inject_bugs=False)
    _build_fixture_db(verify_path, inject_bugs=False)
    return core_path, verify_path


def _mutate(db_path: Path, sql: str, params: tuple = ()) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def _run_parity(core_path: Path, verify_path: Path, table: str, league_id: int = 47):
    core = connect_ro(core_path, "core")
    verify = connect_ro(verify_path, "verify")
    register_udfs(core)
    register_udfs(verify)
    core_schema = schema_snapshot(core, CORE_TABLES)
    verify_schema = schema_snapshot(verify, CORE_TABLES)
    cols = common_columns(core_schema, verify_schema, table)
    rows = merge_parity_for_table(core, verify, table, cols, [league_id], True)
    core.close()
    verify.close()
    row = next(r for r in rows if r["league_id"] == league_id and r["season"] == "2020/2021")
    return row


def test_mutation_01_dim_match_home_score_change_detected(tmp_path: Path):
    core_path, verify_path = _paired_dbs(tmp_path)
    _mutate(verify_path, "UPDATE dim_match SET home_score = home_score + 5 WHERE Match_ID = 1")
    row = _run_parity(core_path, verify_path, "dim_match")
    assert row["classification"] == "CONTENT_MISMATCH", (
        f"dim_match 只改 home_score,保持 Match_ID/行数不变,必须 CONTENT_MISMATCH,实际: {row['classification']}"
    )


def test_mutation_02_dim_match_home_team_id_change_detected(tmp_path: Path):
    core_path, verify_path = _paired_dbs(tmp_path)
    _mutate(verify_path, "UPDATE dim_match SET Home_Team_ID = 999999 WHERE Match_ID = 1")
    row = _run_parity(core_path, verify_path, "dim_match")
    assert row["classification"] == "CONTENT_MISMATCH", (
        f"dim_match 只改 Home_Team_ID 必须 CONTENT_MISMATCH,实际: {row['classification']}"
    )


def test_mutation_03_extra_json_value_change_detected(tmp_path: Path):
    core_path, verify_path = _paired_dbs(tmp_path)
    _mutate(
        verify_path,
        "UPDATE fact_team_match_stats SET extra_json = '{\"BallPossesion\":87.0,\"expected_goals\":0.3}' "
        "WHERE Match_ID = 1 AND Team_ID = 100",
    )
    row = _run_parity(core_path, verify_path, "fact_team_match_stats")
    assert row["classification"] == "CONTENT_MISMATCH", (
        f"extra_json 数值真变化必须 CONTENT_MISMATCH,实际: {row['classification']}"
    )


def test_mutation_04_extra_json_key_reorder_whitespace_is_semantically_equal(tmp_path: Path):
    core_path, verify_path = _paired_dbs(tmp_path)
    # 用 json.dumps(indent=2) 从反序字典生成——保证是带真实换行/缩进的合法 JSON,
    # 避免手写转义字符串时把 "\n" 误写成字面反斜杠+n(那样反而会生成非法 JSON)。
    reordered = json.dumps({"expected_goals": 0.3, "BallPossesion": 55.0}, indent=2)
    _mutate(
        verify_path,
        "UPDATE fact_team_match_stats SET extra_json = ? WHERE Match_ID = 1 AND Team_ID = 100",
        (reordered,),
    )
    row = _run_parity(core_path, verify_path, "fact_team_match_stats")
    assert row["classification"] == "SEMANTICALLY_EQUAL", (
        f"仅 JSON 键顺序/空白变化,语义不变,必须 SEMANTICALLY_EQUAL(不得 EXACT),实际: {row['classification']}"
    )


def test_mutation_05_valid_json_to_invalid_json_detected(tmp_path: Path):
    core_path, verify_path = _paired_dbs(tmp_path)
    _mutate(
        verify_path,
        "UPDATE fact_team_match_stats SET extra_json = '{not valid json,,,' WHERE Match_ID = 1 AND Team_ID = 100",
    )
    row = _run_parity(core_path, verify_path, "fact_team_match_stats")
    assert row["classification"] == "CONTENT_MISMATCH", (
        f"合法 JSON 变非法 JSON 必须 CONTENT_MISMATCH,实际: {row['classification']}"
    )


def test_mutation_06_null_to_empty_object_detected(tmp_path: Path):
    core_path, verify_path = _paired_dbs(tmp_path)
    _mutate(
        verify_path,
        "UPDATE fact_team_match_stats SET extra_json = '{}' WHERE Match_ID = 1 AND Team_ID = 100",
    )
    row = _run_parity(core_path, verify_path, "fact_team_match_stats")
    assert row["classification"] == "CONTENT_MISMATCH", (
        f"非 NULL 的 extra_json 改成 '{{}}' 内容已变化,必须 CONTENT_MISMATCH,实际: {row['classification']}"
    )
    # 额外验证 NULL 与 '{}' 本身不等价(单独一行两种取值不应被当成同一 key)
    _mutate(
        verify_path,
        "UPDATE fact_team_match_stats SET extra_json = NULL WHERE Match_ID = 1 AND Team_ID = 100",
    )
    row2 = _run_parity(core_path, verify_path, "fact_team_match_stats")
    assert row2["classification"] == "CONTENT_MISMATCH", (
        f"非 NULL 的 extra_json 改成 NULL 必须 CONTENT_MISMATCH,实际: {row2['classification']}"
    )


def test_mutation_07_deleted_fact_row_detected(tmp_path: Path):
    core_path, verify_path = _paired_dbs(tmp_path)
    _mutate(verify_path, "DELETE FROM fact_team_match_stats WHERE Match_ID = 1 AND Team_ID = 200")
    row = _run_parity(core_path, verify_path, "fact_team_match_stats")
    assert row["classification"] in ("MISSING_FROM_ALLWIN", "CONTENT_MISMATCH"), (
        f"删除一行事实数据必须 MISSING_FROM_ALLWIN 或 CONTENT_MISMATCH,实际: {row['classification']}"
    )
    assert row["verify_row_count"] != row["allwin_row_count"] or row["classification"] != "EXACT"


def test_mutation_08_added_fact_row_detected(tmp_path: Path):
    core_path, verify_path = _paired_dbs(tmp_path)
    _mutate(
        core_path,
        "INSERT INTO fact_team_match_stats VALUES (1,100,'FirstHalf',1,'{\"BallPossesion\":50.0}')",
    )
    row = _run_parity(core_path, verify_path, "fact_team_match_stats")
    assert row["classification"] in ("EXTRA_IN_ALLWIN", "CONTENT_MISMATCH"), (
        f"allwin 新增一行事实数据必须 EXTRA_IN_ALLWIN 或 CONTENT_MISMATCH,实际: {row['classification']}"
    )


def test_mutation_09_exact_duplicate_row_multiplicity_detected(tmp_path: Path):
    core_path, verify_path = _paired_dbs(tmp_path)
    _mutate(
        verify_path,
        "INSERT INTO fact_team_match_stats VALUES (1,100,'All',2,'{\"BallPossesion\":55.0,\"expected_goals\":0.3}')",
    )
    row = _run_parity(core_path, verify_path, "fact_team_match_stats")
    assert row["classification"] == "CONTENT_MISMATCH", (
        f"verify 一侧出现完全重复行(多重集合计数不同)必须被检出为 CONTENT_MISMATCH,实际: {row['classification']}"
    )
    assert row["verify_row_count"] == row["allwin_row_count"] + 1


def test_mutation_10_real_field_small_change_detected(tmp_path: Path):
    core_path, verify_path = _paired_dbs(tmp_path)
    _mutate(verify_path, "UPDATE fact_shotmap SET xG = 0.31 WHERE Match_ID = 1 AND Player_ID = 'p1'")
    row = _run_parity(core_path, verify_path, "fact_shotmap")
    assert row["classification"] == "CONTENT_MISMATCH", (
        f"REAL 字段微小真实变化(0.3->0.31)必须被检出,实际: {row['classification']}"
    )


def test_mutation_11_clean_identical_dbs_are_exact(tmp_path: Path):
    core_path, verify_path = _paired_dbs(tmp_path)
    row_dm = _run_parity(core_path, verify_path, "dim_match")
    row_ts = _run_parity(core_path, verify_path, "fact_team_match_stats")
    row_sm = _run_parity(core_path, verify_path, "fact_shotmap")
    assert row_dm["classification"] == "EXACT"
    assert row_ts["classification"] == "EXACT"
    assert row_sm["classification"] == "EXACT"


def test_mutation_12_same_semantic_different_format_is_never_exact(tmp_path: Path):
    """同语义不同 JSON 格式只能判 SEMANTICALLY_EQUAL,不得判 EXACT(与 04 同一场景,
    单独作为不得 EXACT 的显式反例保留)。"""
    core_path, verify_path = _paired_dbs(tmp_path)
    _mutate(
        verify_path,
        "UPDATE fact_team_match_stats SET extra_json = "
        "'{\"expected_goals\":0.30,\"BallPossesion\":55.0}' WHERE Match_ID = 1 AND Team_ID = 100",
    )
    row = _run_parity(core_path, verify_path, "fact_team_match_stats")
    assert row["classification"] != "EXACT"
    assert row["classification"] == "SEMANTICALLY_EQUAL"


# ══════════════════════════════════════════════════════════════════════
# §十 20 条永久反例测试(独立复核修复轮新增)
# ══════════════════════════════════════════════════════════════════════

def _minimal_conn(tmp_path: Path, name: str, ddl: str) -> sqlite3.Connection:
    """自建最小 schema 的临时可写库(仅含当前测试需要的列),测试完直接用
    connect_ro 只读重开。不复用生产 78 列大表结构,避免为单个字段测试维护
    大段无关列。"""
    p = tmp_path / name
    conn = sqlite3.connect(p)
    conn.executescript(ddl)
    conn.commit()
    conn.close()
    return connect_ro(p, "fixture")


DIM_MATCH_DDL = """
    CREATE TABLE dim_match (
        Match_ID INTEGER PRIMARY KEY, Season TEXT, League_ID INTEGER, Date TEXT,
        Home_Team_ID INTEGER, Away_Team_ID INTEGER, Home_Team_Name TEXT, Away_Team_Name TEXT,
        home_score INTEGER, away_score INTEGER, status TEXT, Referee TEXT, Match_Round TEXT,
        Temperature TEXT, Wind_Speed TEXT, Who_Lost_On_Penalties TEXT
    );
"""


# ── 1-4: readiness 粒度/公式 ─────────────────────────────────────────

def test_readiness_output_includes_season_column():
    rows = feature_readiness_rows([], [], [47], ["standard_match"], {})
    assert rows, "至少应产出一行(NOT_APPLICABLE 兜底分支)"
    assert "season" in rows[0], "readiness 行必须包含 season 列"


def test_readiness_row_count_matches_league_x_season_x_family():
    league_ids = [47, 53]
    families = ["standard_match", "shot_xg"]
    rows = feature_readiness_rows([], [], league_ids, families, {})
    assert len(rows) == len(league_ids) * len(SEASONS) * len(families), (
        f"应为 League×Season×family = {len(league_ids)}×{len(SEASONS)}×{len(families)} 行,实际 {len(rows)}"
    )
    assert len({(r["league_id"], r["season"], r["feature_family"]) for r in rows}) == len(rows), "不能有重复分区"


def test_readiness_completed_matches_use_same_season_not_six_season_sum():
    """§五修复的核心反例:completed_match_count 必须是该 League×Season 单季值,
    不能是六个赛季相加(旧 bug:EPL 2280=380×6,covered 却只用单季 380)。"""
    completed = {(47, "2020/2021"): 380, (47, "2021/2022"): 999}
    field_cov = [{
        "table": "fact_shotmap", "field": "xG", "family": "shot_xg", "applicability": "REQUIRED",
        "league_id": 47, "season": "2020/2021", "applicable_rows": 100, "non_null_rows": 100,
        "covered_matches": 380, "not_applicable": False,
    }]
    rows = feature_readiness_rows(field_cov, [], [47], ["shot_xg"], completed)
    row_2021 = next(r for r in rows if r["season"] == "2020/2021")
    assert row_2021["completed_match_count"] == 380, "必须等于该赛季自身的 completed,不能混入其它赛季"
    row_2122 = next(r for r in rows if r["season"] == "2021/2022")
    assert row_2122["completed_match_count"] == 999, "另一赛季必须用自己的 completed,证明未被跨赛季求和污染"


def test_readiness_match_coverage_rate_within_0_100():
    field_cov = [{
        "table": "fact_shotmap", "field": "xG", "family": "shot_xg", "applicability": "REQUIRED",
        "league_id": 47, "season": s, "applicable_rows": 10, "non_null_rows": 10,
        "covered_matches": 380, "not_applicable": False,
    } for s in SEASONS]
    completed = {(47, s): 380 for s in SEASONS}
    rows = feature_readiness_rows(field_cov, [], [47], ["shot_xg"], completed)
    for r in rows:
        rate = r["match_coverage_rate_pct"]
        if rate != "":
            assert 0.0 <= rate <= 100.0, f"match_coverage_rate_pct 越界: {rate}"


# ── 5-10: 条件字段适用分母 ────────────────────────────────────────────

EVENTS_LINEUP_DDL = DIM_MATCH_DDL + """
    CREATE TABLE fact_match_events (
        Match_ID INTEGER, event_index INTEGER, event_type TEXT, minute INTEGER,
        overload_time INTEGER, is_home INTEGER, home_score INTEGER, away_score INTEGER,
        player_id TEXT, player_name TEXT, card_type TEXT, assist_player_id TEXT,
        assist_player_name TEXT, sub_in_player_id TEXT, sub_in_player_name TEXT,
        sub_out_player_id TEXT, sub_out_player_name TEXT, minutes_added INTEGER,
        event_id TEXT, extra_json TEXT
    );
    CREATE TABLE fact_match_lineup (
        Match_ID INTEGER, Team_ID INTEGER, is_home INTEGER, formation TEXT, Player_ID TEXT,
        player_name TEXT, shirt_number TEXT, position_id INTEGER, usual_position_id INTEGER,
        is_starter INTEGER, is_captain INTEGER, country_code TEXT, market_value INTEGER,
        rating REAL, sub_in_time INTEGER, sub_out_time INTEGER, extra_json TEXT
    );
"""


def _build_events_lineup_fixture(tmp_path: Path) -> sqlite3.Connection:
    p = tmp_path / "events_lineup.db"
    conn = sqlite3.connect(p)
    conn.executescript(EVENTS_LINEUP_DDL)
    conn.execute("INSERT INTO dim_match VALUES (1,'2020/2021',47,'2020-08-01',100,200,'A','B',2,1,'Finish',NULL,'1',NULL,NULL,NULL)")
    events = [
        # event_index, event_type, card_type, player_id, assist_player_id, sub_in, sub_out, minutes_added, is_home
        (1, "Card", "Yellow", "p1", None, None, None, None, 1),
        (2, "Goal", None, "p1", "p2", None, None, None, 1),          # 有助攻的进球
        (3, "Goal", None, "p3", None, None, None, None, 0),          # 无助攻的进球(合法 NULL)
        (4, "Substitution", None, None, None, "p4", "p1", None, 1),
        (5, "AddedTime", None, None, None, None, None, 3, None),
        (6, "Half", None, None, None, None, None, None, None),       # 结构性事件,无球员归属
    ]
    for idx, etype, ctype, pid, apid, sin, sout, madd, ih in events:
        conn.execute(
            "INSERT INTO fact_match_events "
            "(Match_ID,event_index,event_type,card_type,player_id,assist_player_id,"
            " sub_in_player_id,sub_out_player_id,minutes_added,is_home) VALUES (1,?,?,?,?,?,?,?,?,?)",
            (idx, etype, ctype, pid, apid, sin, sout, madd, ih),
        )
    # lineup: p1/p2/p3 首发,p4 替补(被换上,event 4 sub_in_player_id=p4)->sub_in_time 应有值
    # p5 替补但整场未上场(未出现在任何 Substitution 事件)-> sub_in_time 合法 NULL,不算缺失
    lineup = [
        (100, 1, "p1", 1, 10, None),
        (100, 1, "p2", 1, None, None),
        (200, 0, "p3", 1, None, None),
        (100, 1, "p4", 0, None, 60),   # 换上,sub_in_time=60
        (100, 1, "p5", 0, None, None),  # 全场未上场
    ]
    for tid, ih, pid, starter, sub_out, sub_in in lineup:
        conn.execute(
            "INSERT INTO fact_match_lineup (Match_ID,Team_ID,is_home,Player_ID,is_starter,sub_out_time,sub_in_time) "
            "VALUES (1,?,?,?,?,?,?)",
            (tid, ih, pid, starter, sub_out, sub_in),
        )
    conn.commit()
    conn.close()
    return connect_ro(p, "fixture")


def test_card_type_denominator_is_card_events_not_all_events():
    def _run(tmp_path):
        conn = _build_events_lineup_fixture(tmp_path)
        spec = next(s for s in _events_field_specs() if s.field == "card_type")
        rows = field_coverage_rows(conn, spec, "m.League_ID IN (47)")
        conn.close()
        return rows
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        rows = _run(Path(td))
    assert rows, "应产出覆盖率行"
    r = rows[0]
    assert r["applicable_rows"] == 1, f"card_type 分母必须只算 Card 事件(1 条),实际 {r['applicable_rows']}"
    assert r["non_null_rows"] == 1


def test_substitution_fields_denominator_is_substitution_events():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        conn = _build_events_lineup_fixture(Path(td))
        spec = next(s for s in _events_field_specs() if s.field == "sub_in_player_id")
        rows = field_coverage_rows(conn, spec, "m.League_ID IN (47)")
        conn.close()
    r = rows[0]
    assert r["applicable_rows"] == 1, f"sub_in_player_id 分母必须只算 Substitution 事件(1 条),实际 {r['applicable_rows']}"
    assert r["non_null_rows"] == 1, "该 Substitution 事件确实有 sub_in_player_id,不应判缺失"


def test_assist_player_id_no_assist_goal_is_legitimate_null_not_missing():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        conn = _build_events_lineup_fixture(Path(td))
        spec = next(s for s in _events_field_specs() if s.field == "assist_player_id")
        rows = field_coverage_rows(conn, spec, "m.League_ID IN (47)")
        conn.close()
    r = rows[0]
    assert r["applicable_rows"] == 2, f"assist_player_id 分母应为全部 Goal 事件(2 条),实际 {r['applicable_rows']}"
    assert r["non_null_rows"] == 1, "两个 Goal 里只有 1 个有助攻,另一个 NULL 是合法值,不能算所有 Goal 都缺失"


def test_lineup_bench_never_subbed_in_null_not_counted_as_missing():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        conn = _build_events_lineup_fixture(Path(td))
        rows = lineup_sub_time_coverage_rows(conn, [47])
        conn.close()
    sub_in_rows = [r for r in rows if r["field"] == "sub_in_time"]
    assert sub_in_rows, "应产出 sub_in_time 覆盖率行"
    r = sub_in_rows[0]
    # 正确分母 = 实际在 Substitution 事件里作为 sub_in_player_id 出现的球员(仅 p4),
    # p5(全场未上场的替补)不应计入分母。
    assert r["applicable_rows"] == 1, f"分母必须只含实际换上的球员(1 名 p4),不含未出场替补 p5,实际 {r['applicable_rows']}"
    assert r["non_null_rows"] == 1


def test_xgot_applicable_denominator_excludes_off_target_shots():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "shotmap_xgot.db"
        conn = sqlite3.connect(p)
        conn.executescript(DIM_MATCH_DDL + """
            CREATE TABLE fact_shotmap (
                Match_ID INTEGER, Player_ID TEXT, Team_ID INTEGER, Minute INTEGER, Period TEXT,
                X_Coord REAL, Y_Coord REAL, xG REAL, xGOT REAL, Situation TEXT, Outcome TEXT, Shot_Type TEXT
            );
        """)
        conn.execute("INSERT INTO dim_match VALUES (1,'2020/2021',47,'2020-08-01',100,200,'A','B',1,0,'Finish',NULL,'1',NULL,NULL,NULL)")
        conn.execute("INSERT INTO fact_shotmap VALUES (1,'p1',100,10,'FirstHalf',90,34,0.3,0.25,'RegularPlay','Goal','RightFoot')")
        conn.execute("INSERT INTO fact_shotmap VALUES (1,'p1',100,20,'FirstHalf',90,34,0.1,NULL,'RegularPlay','Miss','RightFoot')")
        conn.commit(); conn.close()
        ro = connect_ro(p, "fixture")
        spec = next(s for s in _shotmap_field_specs() if s.field == "xGOT")
        rows = field_coverage_rows(ro, spec, "m.League_ID IN (47)")
        ro.close()
    r = rows[0]
    assert r["applicable_rows"] == 1, f"xGOT 正确分母只含射正/进球(1 条 Goal),不含 Miss,实际 {r['applicable_rows']}"
    assert r["non_null_rows"] == 1


# ── 11-12: Unicode 姓名差异分类 ──────────────────────────────────────

def test_accent_normalization_classified_separately_from_real_name_change():
    assert _classify_name_diff("Luis Diaz", "Luis Díaz") == "ACCENT_NORMALIZATION_ONLY"
    assert _classify_name_diff("Martin Zubimendi", "Martín Zubimendi") == "ACCENT_NORMALIZATION_ONLY"


def test_truly_different_name_not_misclassified_as_accent():
    result = _classify_name_diff("Florentino Luis", "Florentino")
    assert result == "TRULY_DIFFERENT_NAME", f"真实姓名形式变化不能被误判为重音规范化,实际: {result}"
    result2 = _classify_name_diff("Destiny Udogie", "Iyenoma Udogie")
    assert result2 == "TRULY_DIFFERENT_NAME"


# ── 13: playoff 不计作常规双循环缺口 ─────────────────────────────────

def test_extra_playoff_match_not_treated_as_error_note():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "playoff.db"
        conn = sqlite3.connect(p)
        conn.executescript(DIM_MATCH_DDL + """
            CREATE TABLE fact_shotmap (Match_ID INTEGER, Player_ID TEXT, Team_ID INTEGER,
                Minute INTEGER, Period TEXT, X_Coord REAL, Y_Coord REAL, xG REAL, xGOT REAL,
                Situation TEXT, Outcome TEXT, Shot_Type TEXT);
            CREATE TABLE fact_player_match_stats (Match_ID INTEGER, Player_ID TEXT, Team_ID INTEGER);
            CREATE TABLE fact_team_match_stats (Match_ID INTEGER, Team_ID INTEGER, Period TEXT, Goals REAL, extra_json TEXT);
            CREATE TABLE fact_match_events (Match_ID INTEGER, event_index INTEGER);
            CREATE TABLE fact_match_lineup (Match_ID INTEGER, Team_ID INTEGER, Player_ID TEXT);
        """)
        # 3 支球队,常规双循环期望 3*2=6 场;这里插入 7 场(含 1 场附加赛),
        # 不应抛异常/断言失败,只应在 volume_gap_note 里给出"超出双循环,需人工复核"提示。
        rows = [(1, 10, 20), (2, 10, 30), (3, 20, 10), (4, 20, 30), (5, 30, 10), (6, 30, 20), (7, 10, 30)]
        for mid, h, a in rows:
            conn.execute(
                "INSERT INTO dim_match VALUES (?,'2020/2021',47,'2020-08-01',?,?,'H','A',1,0,'Finish',NULL,'1',NULL,NULL,NULL)",
                (mid, h, a),
            )
        conn.commit(); conn.close()
        ro = connect_ro(p, "fixture")
        rows_out = partition_summary_rows(ro, [47])
        ro.close()
    assert len(rows_out) == 1
    r = rows_out[0]
    assert r["match_count"] == 7
    assert r["derived_double_round_robin_expected"] == 6
    assert "EXCEED" in r["volume_gap_note"], "多出的比赛必须标记为需复核,不能抛异常也不能静默忽略"


# ── 14: own-goal 解释与 unexplained 残差分开 ─────────────────────────

def test_owngoal_reconciliation_separates_explained_from_unexplained():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "owngoal.db"
        conn = sqlite3.connect(p)
        conn.executescript(DIM_MATCH_DDL + """
            CREATE TABLE fact_shotmap (Match_ID INTEGER, Player_ID TEXT, Team_ID INTEGER,
                Minute INTEGER, Period TEXT, X_Coord REAL, Y_Coord REAL, xG REAL, xGOT REAL,
                Situation TEXT, Outcome TEXT, Shot_Type TEXT);
            CREATE TABLE fact_player_match_stats (Match_ID INTEGER, Player_ID TEXT, Team_ID INTEGER, goals REAL, owngoal REAL, minutes_played INTEGER);
            CREATE TABLE fact_team_match_stats (Match_ID INTEGER, Team_ID INTEGER, Period TEXT, Goals REAL, extra_json TEXT);
            CREATE TABLE fact_match_events (Match_ID INTEGER, event_index INTEGER, event_type TEXT, is_home INTEGER, home_score INTEGER, away_score INTEGER);
            CREATE TABLE fact_match_lineup (Match_ID INTEGER, Team_ID INTEGER, Player_ID TEXT, is_starter INTEGER);
        """)
        # Match 1: home_score=2(1 个正常进球 + 1 个 away 乌龙),应被乌龙球解释
        conn.execute("INSERT INTO dim_match VALUES (1,'2020/2021',47,'2020-08-01',100,200,'A','B',2,0,'Finish',NULL,'1',NULL,NULL,NULL)")
        conn.execute("INSERT INTO fact_player_match_stats VALUES (1,'p1',100,1,0,90)")
        conn.execute("INSERT INTO fact_player_match_stats VALUES (1,'p2',200,0,1,90)")  # away 队 p2 乌龙,记在 away 队名下
        # Match 2: home_score=3,但球员进球只有 1、无乌龙 -> 真实未解释残差
        conn.execute("INSERT INTO dim_match VALUES (2,'2020/2021',47,'2020-08-02',100,200,'A','B',3,0,'Finish',NULL,'1',NULL,NULL,NULL)")
        conn.execute("INSERT INTO fact_player_match_stats VALUES (2,'p3',100,1,0,90)")
        conn.execute("INSERT INTO fact_player_match_stats VALUES (2,'p4',200,0,0,90)")
        conn.commit(); conn.close()
        ro = connect_ro(p, "fixture")
        rows = cross_table_consistency_rows(ro, [47])
        ro.close()
    r = next(x for x in rows if x["season"] == "2020/2021")
    assert r["player_goals_mismatch_count"] == 2, f"两场都应判定为不符,实际 {r['player_goals_mismatch_count']}"
    assert r["player_goals_mismatch_explained_by_owngoal_count"] == 1, "只有 Match 1 应被乌龙球解释"
    assert r["player_goals_mismatch_unexplained_count"] == 1, "Match 2 必须留在 unexplained,不能被乌龙球逻辑误吞"
    assert "2" in r["player_goals_mismatch_unexplained_representative_ids"]


# ── 15-16: 坐标契约(105×68 米制球场) ─────────────────────────────────

def test_coordinates_within_105x68_pitch_do_not_trigger_anomaly():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "coord_ok.db"
        conn = sqlite3.connect(p)
        conn.executescript(DIM_MATCH_DDL + """
            CREATE TABLE fact_shotmap (Match_ID INTEGER, Player_ID TEXT, Team_ID INTEGER,
                Minute INTEGER, Period TEXT, X_Coord REAL, Y_Coord REAL, xG REAL, xGOT REAL,
                Situation TEXT, Outcome TEXT, Shot_Type TEXT);
        """)
        conn.execute("INSERT INTO dim_match VALUES (1,'2020/2021',47,'2020-08-01',100,200,'A','B',1,0,'Finish',NULL,'1',NULL,NULL,NULL)")
        # 真实球场范围内的坐标(含边角 104.9/0.2,均在 105x68 之内)
        for x, y in [(0.38, 0.20), (104.91, 67.87), (52.5, 34.0)]:
            conn.execute("INSERT INTO fact_shotmap VALUES (1,'p1',100,10,'FirstHalf',?,?,0.1,0.1,'RegularPlay','Miss','RightFoot')", (x, y))
        conn.commit(); conn.close()
        ro = connect_ro(p, "fixture")
        findings = shotmap_anomalies(ro, "fixture", "m.League_ID IN (47)", 47, "2020/2021")
        ro.close()
    coord_findings = [f for f in findings if f.category == "shotmap.coord_out_of_pitch"]
    assert not coord_findings, f"105×68 范围内的真实坐标不应报越界: {[(f.count, f.description) for f in coord_findings]}"


def test_coordinates_genuinely_outside_105x68_pitch_triggers_alarm():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "coord_bad.db"
        conn = sqlite3.connect(p)
        conn.executescript(DIM_MATCH_DDL + """
            CREATE TABLE fact_shotmap (Match_ID INTEGER, Player_ID TEXT, Team_ID INTEGER,
                Minute INTEGER, Period TEXT, X_Coord REAL, Y_Coord REAL, xG REAL, xGOT REAL,
                Situation TEXT, Outcome TEXT, Shot_Type TEXT);
        """)
        conn.execute("INSERT INTO dim_match VALUES (1,'2020/2021',47,'2020-08-01',100,200,'A','B',1,0,'Finish',NULL,'1',NULL,NULL,NULL)")
        conn.execute("INSERT INTO fact_shotmap VALUES (1,'p1',100,10,'FirstHalf',52.5,34.0,0.1,0.1,'RegularPlay','Miss','RightFoot')")
        conn.execute("INSERT INTO fact_shotmap VALUES (1,'p2',100,20,'FirstHalf',200.0,34.0,0.1,0.1,'RegularPlay','Miss','RightFoot')")  # 真实越界
        conn.commit(); conn.close()
        ro = connect_ro(p, "fixture")
        findings = shotmap_anomalies(ro, "fixture", "m.League_ID IN (47)", 47, "2020/2021")
        ro.close()
    coord_findings = [f for f in findings if f.category == "shotmap.coord_out_of_pitch"]
    assert coord_findings, "真实越出 105×68 球场的坐标必须被检出"
    assert coord_findings[0].count == 1


# ── 17: shot_accuracy 冗余检测 ────────────────────────────────────────

def test_shot_accuracy_equal_to_shots_on_target_no_anomaly(clean_db: Path):
    # clean_db(_build_fixture_db)里 p1 的 shot_accuracy 从未赋值(默认 NULL),
    # 直接写入一行 shot_accuracy == ShotsOnTarget 的球员,验证冗余关系成立时不报警。
    conn = sqlite3.connect(clean_db)
    conn.execute(
        "UPDATE fact_player_match_stats SET shot_accuracy=2.0, \"ShotsOnTarget\"=2.0 WHERE Match_ID=1 AND Player_ID='p2'"
    )
    conn.commit(); conn.close()
    ro = connect_ro(clean_db, "fixture")
    findings = player_stats_anomalies(ro, "fixture", "m.League_ID IN (47,53)", 47, "2020/2021")
    ro.close()
    bad = [f for f in findings if f.category == "player_stats.shot_accuracy_not_redundant"]
    assert not bad, "shot_accuracy == ShotsOnTarget 时不应报警(已确认冗余关系成立)"


def test_shot_accuracy_diverging_from_shots_on_target_triggers_anomaly(clean_db: Path):
    conn = sqlite3.connect(clean_db)
    conn.execute(
        "UPDATE fact_player_match_stats SET shot_accuracy=5.0, \"ShotsOnTarget\"=2.0 WHERE Match_ID=1 AND Player_ID='p2'"
    )
    conn.commit(); conn.close()
    ro = connect_ro(clean_db, "fixture")
    findings = player_stats_anomalies(ro, "fixture", "m.League_ID IN (47,53)", 47, "2020/2021")
    ro.close()
    bad = [f for f in findings if f.category == "player_stats.shot_accuracy_not_redundant"]
    assert bad, "shot_accuracy 与 ShotsOnTarget 出现真实分歧时必须报警"


# ── 18: anomalies 携带 league/season ─────────────────────────────────

def test_anomaly_findings_carry_league_and_season_when_partitioned(buggy_db: Path):
    conn = connect_ro(buggy_db, "fixture")
    findings = dim_match_anomalies(conn, "fixture", "League_ID=47 AND Season='2020/2021'", 47, "2020/2021")
    conn.close()
    assert findings, "buggy_db 在 EPL 2020/2021 分区应产出异常(同队同队等)"
    for f in findings:
        assert f.league_id == 47, f"分区调用必须把 league_id 写入 Finding,实际 {f.league_id}"
        assert f.season == "2020/2021", f"分区调用必须把 season 写入 Finding,实际 {f.season}"


# ── 19: WAL/SHM 快照完整性 ────────────────────────────────────────────

def test_checkpointed_db_with_no_wal_reads_full_data(tmp_path: Path):
    """审计脚本要求"先确认 -wal/-shm 不存在再快照读取"。这里验证:一个已经
    checkpoint 干净、没有残留 -wal 文件的库,只读连接能看到全部已提交数据——
    证明"无 -wal 残留"这个前置条件在正常场景下确实等价于数据完整。"""
    p = tmp_path / "wal_check.db"
    conn = sqlite3.connect(p)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(DIM_MATCH_DDL)
    conn.execute("INSERT INTO dim_match VALUES (1,'2020/2021',47,'2020-08-01',100,200,'A','B',1,0,'Finish',NULL,'1',NULL,NULL,NULL)")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # 强制 checkpoint,清空 -wal
    conn.close()
    assert not (tmp_path / "wal_check.db-wal").exists() or (tmp_path / "wal_check.db-wal").stat().st_size == 0, (
        "TRUNCATE checkpoint 后不应残留非空 -wal 文件"
    )
    ro = connect_ro(p, "fixture")
    n = ro.execute("SELECT COUNT(*) FROM dim_match").fetchone()[0]
    ro.close()
    assert n == 1, "checkpoint 干净后,只读连接必须能看到已提交的数据"


# ── 20: 输出 CSV/JSON schema contract ────────────────────────────────

def test_merge_parity_row_matches_declared_csv_fieldnames():
    row = _parity_row("dim_match", 47, "2020/2021", "EXACT", 10, 10, 0, 0, 10, 0, "", 0, "", "")
    missing = set(MERGE_PARITY_FIELDNAMES) - set(row)
    extra = set(row) - set(MERGE_PARITY_FIELDNAMES)
    assert not missing, f"_parity_row 输出缺少声明的列: {missing}"
    assert not extra, f"_parity_row 输出多出未声明的列: {extra}"
    # CsvWriter 必须能无异常写出(验证 schema contract 不是只在纸面成立)
    w = CsvWriter(Path("/tmp") / "does_not_matter.csv", MERGE_PARITY_FIELDNAMES)
    w.add(row)


def test_field_coverage_row_matches_declared_csv_fieldnames():
    raw = {
        "table": "fact_shotmap", "field": "xG", "family": "shot_xg", "applicability": "REQUIRED",
        "league_id": 47, "season": "2020/2021", "applicable_rows": 10, "non_null_rows": 10,
        "blank_string_rows": 0, "zero_rows": 0, "covered_matches": 5, "total_matches_in_partition": 5,
        "not_applicable": False,
    }
    enriched = enrich_field_coverage_with_deltas([raw])
    assert enriched, "enrich_field_coverage_with_deltas 应产出至少一行"
    missing = set(FIELD_COVERAGE_FIELDNAMES) - set(enriched[0])
    extra = set(enriched[0]) - set(FIELD_COVERAGE_FIELDNAMES)
    assert not missing, f"enrich_field_coverage_with_deltas 输出缺少声明的列: {missing}"
    assert not extra, f"enrich_field_coverage_with_deltas 输出多出未声明的列: {extra}"


def test_finding_row_matches_declared_csv_fieldnames():
    from analysis.data_quality.audit_top5_historical_core import Finding
    f = Finding("LOW", "HIGH", "test.category", "dim_match", "Match_ID", 47, "2020/2021",
                "desc", 1, 10, 10.0, None, None, "1", "risk", "cause", "rec")
    row = finding_row(f)
    missing = set(FINDING_FIELDS) - set(row)
    extra = set(row) - set(FINDING_FIELDS)
    assert not missing, f"finding_row 输出缺少声明的列: {missing}"
    assert not extra, f"finding_row 输出多出未声明的列: {extra}"


# ── 补充回归测试:orphan 检查必须真正限定到当前分区,不能读到其它分区的全局数字 ──

def test_orphan_player_id_finding_scoped_to_current_partition_only():
    """回归测试:orphan_and_referential_anomalies 按分区调用时,某一分区的孤儿计数
    /首发计数不能被其它分区(甚至其它联赛)的全局数据污染——曾经真实出现过的 bug:
    starters/played 子查询未加 league_filter_sql,导致全库任一分区有首发孤儿,
    就会让所有分区都被判 HIGH。"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        p1 = tmp_path / "scope.db"
        conn = sqlite3.connect(p1)
        conn.executescript(DIM_MATCH_DDL + """
            CREATE TABLE dim_player (Player_ID TEXT PRIMARY KEY, Player_Name TEXT);
            CREATE TABLE fact_shotmap (Match_ID INTEGER, Player_ID TEXT, Team_ID INTEGER);
            CREATE TABLE fact_player_match_stats (Match_ID INTEGER, Player_ID TEXT, Team_ID INTEGER, minutes_played INTEGER);
            CREATE TABLE fact_team_match_stats (Match_ID INTEGER, Team_ID INTEGER, Period TEXT, Goals REAL, extra_json TEXT);
            CREATE TABLE fact_match_events (Match_ID INTEGER, event_index INTEGER);
            CREATE TABLE fact_match_lineup (Match_ID INTEGER, Team_ID INTEGER, Player_ID TEXT, is_starter INTEGER);
        """)
        # League 47(有一个孤儿首发,应判 HIGH)
        conn.execute("INSERT INTO dim_match VALUES (1,'2020/2021',47,'2020-08-01',100,200,'A','B',1,0,'Finish',NULL,'1',NULL,NULL,NULL)")
        conn.execute("INSERT INTO fact_match_lineup VALUES (1,100,'orphan_starter',1)")  # 孤儿且首发
        # League 53(只有替补孤儿,未出场,应判 LOW——不能被 League 47 的首发孤儿污染)
        conn.execute("INSERT INTO dim_match VALUES (2,'2020/2021',53,'2020-08-02',300,400,'C','D',1,0,'Finish',NULL,'1',NULL,NULL,NULL)")
        conn.execute("INSERT INTO fact_match_lineup VALUES (2,300,'orphan_bench',0)")  # 孤儿但替补未出场
        conn.commit(); conn.close()

        ro = connect_ro(p1, "fixture")
        findings_47 = orphan_and_referential_anomalies(ro, "fixture", "m.League_ID=47 AND m.Season='2020/2021'", 47, "2020/2021")
        findings_53 = orphan_and_referential_anomalies(ro, "fixture", "m.League_ID=53 AND m.Season='2020/2021'", 53, "2020/2021")
        ro.close()

    f47 = [f for f in findings_47 if f.category == "fact.orphan_player_id" and f.table == "fact_match_lineup"]
    f53 = [f for f in findings_53 if f.category == "fact.orphan_player_id" and f.table == "fact_match_lineup"]
    assert f47 and f47[0].severity == "HIGH", "League 47 自己就有首发孤儿,必须是 HIGH"
    assert f53 and f53[0].severity == "LOW", (
        "League 53 只有未出场替补孤儿,即使 League 47 同时存在首发孤儿,也不能被跨联赛污染成 HIGH"
    )
    assert f53[0].count == 1, f"League 53 的孤儿计数必须只算自己分区的 1 条,不能读到 League 47 的数据,实际 {f53[0].count}"


# ══════════════════════════════════════════════════════════════════════
# §二D position_id 适用性反例测试(修复轮 v3:starter-only REQUIRED,
# bench 单独观察指标,fact_player_match_stats.position_id 降为 OPTIONAL)
# ══════════════════════════════════════════════════════════════════════

def _position_id_specs():
    lineup_specs = {s.field: s for s in _lineup_field_specs() if s.table == "fact_match_lineup"}
    player_specs = {s.field: s for s in _player_field_specs() if s.table == "fact_player_match_stats"}
    return lineup_specs, player_specs


def test_position_id_starter_missing_lowers_required_coverage():
    """fact_match_lineup.position_id 仍是 REQUIRED,但分母限定 is_starter=1——
    首发缺失 position_id 必须真实拉低该字段的覆盖率(不能被 applicable_sql 误配
    成"全部行都不适用"从而永远 100%)。"""
    lineup_specs, _ = _position_id_specs()
    spec = lineup_specs["position_id"]
    assert spec.applicability == "REQUIRED"
    assert spec.applicable_sql == "is_starter=1"
    # 用假 field_cov 行模拟"首发里有缺失"的分区,确认 readiness 的
    # minimum_required_field_coverage_pct 会被拉低(不是被跳过)。
    field_cov = [{
        "table": "fact_match_lineup", "field": "position_id", "family": "standard_match",
        "applicability": "REQUIRED", "league_id": 47, "season": "2020/2021",
        "applicable_rows": 100, "non_null_rows": 60, "covered_matches": 10,
        "total_matches_in_partition": 10, "not_applicable": False,
    }]
    rows = feature_readiness_rows(field_cov, [], [47], ["standard_match"], {(47, "2020/2021"): 10})
    row = next(r for r in rows if r["season"] == "2020/2021")
    assert row["minimum_required_field_coverage_pct"] == 60.0, (
        f"首发 position_id 缺失(60/100)必须真实拉低 REQUIRED 最低覆盖率,实际 {row['minimum_required_field_coverage_pct']}"
    )
    assert row["readiness"] in ("READY_WITH_FILTERS", "NOT_READY")


def test_position_id_bench_missing_does_not_lower_required_coverage():
    """position_id_bench 是 OPTIONAL 观察指标——即使替补 position_id 100% 缺失,
    也不能拉低 family 的 REQUIRED 最低覆盖率。"""
    lineup_specs, _ = _position_id_specs()
    bench_spec = lineup_specs["position_id_bench"]
    assert bench_spec.applicability == "OPTIONAL"
    assert bench_spec.applicable_sql == "is_starter=0"
    field_cov = [
        {  # 首发 position_id 100%(真正 REQUIRED)
            "table": "fact_match_lineup", "field": "position_id", "family": "standard_match",
            "applicability": "REQUIRED", "league_id": 47, "season": "2024/2025",
            "applicable_rows": 100, "non_null_rows": 100, "covered_matches": 10,
            "total_matches_in_partition": 10, "not_applicable": False,
        },
        {  # 替补 position_id 100% 缺失(OPTIONAL,不应拖累 REQUIRED 最低值)
            "table": "fact_match_lineup", "field": "position_id_bench", "family": "standard_match",
            "applicability": "OPTIONAL", "league_id": 47, "season": "2024/2025",
            "applicable_rows": 80, "non_null_rows": 0, "covered_matches": 10,
            "total_matches_in_partition": 10, "not_applicable": False,
        },
    ]
    rows = feature_readiness_rows(field_cov, [], [47], ["standard_match"], {(47, "2024/2025"): 10})
    row = next(r for r in rows if r["season"] == "2024/2025")
    assert row["minimum_required_field_coverage_pct"] == 100.0, (
        f"替补 position_id 100% 缺失不应拖累 REQUIRED 最低覆盖率,实际 {row['minimum_required_field_coverage_pct']}"
    )
    assert row["optional_field_count"] == 1
    assert row["required_field_count"] == 1


def test_position_id_bench_observation_metric_reports_zero_not_dropped(tmp_path: Path):
    """替补 position_id 观察指标本身必须真实计算并可报告 0%,不能因为标了
    OPTIONAL 就被静默跳过或永远显示为空。"""
    p = tmp_path / "bench_pos.db"
    conn = sqlite3.connect(p)
    conn.executescript(DIM_MATCH_DDL + """
        CREATE TABLE fact_match_lineup (
            Match_ID INTEGER, Team_ID INTEGER, is_home INTEGER, Player_ID TEXT,
            is_starter INTEGER, position_id INTEGER
        );
    """)
    conn.execute("INSERT INTO dim_match VALUES (1,'2024/2025',47,'2024-08-01',100,200,'A','B',1,0,'Finish',NULL,'1',NULL,NULL,NULL)")
    conn.execute("INSERT INTO fact_match_lineup VALUES (1,100,1,'p1',1,5)")   # 首发,有 position_id
    conn.execute("INSERT INTO fact_match_lineup VALUES (1,100,1,'p2',0,NULL)")  # 替补,position_id 缺失
    conn.commit(); conn.close()
    ro = connect_ro(p, "fixture")
    lineup_specs, _ = _position_id_specs()
    bench_rows = field_coverage_rows(ro, lineup_specs["position_id_bench"], "m.League_ID IN (47)")
    ro.close()
    assert bench_rows, "替补观察指标必须真实产出行,不能被跳过"
    r = bench_rows[0]
    assert r["applicable_rows"] == 1, f"分母应只含替补(1 行),实际 {r['applicable_rows']}"
    assert r["non_null_rows"] == 0, "该替补 position_id 为 NULL,必须如实报告非空数为 0"


def test_usual_position_id_stays_required_for_full_roster():
    """usual_position_id 对全体球员(不分首发/替补)保持 REQUIRED,无 applicable_sql 限制——
    是跨赛季稳定的位置语义替代字段。"""
    lineup_specs, _ = _position_id_specs()
    spec = lineup_specs["usual_position_id"]
    assert spec.applicability == "REQUIRED"
    assert spec.applicable_sql is None, "usual_position_id 不应限定首发/替补,应对全名单适用"


def test_player_stats_position_id_no_longer_required():
    """fact_player_match_stats.position_id 即使限定到实际出场球员仍逐季下降
    (§五B 独立复核),不再作为 standard_match 的 REQUIRED 最低值来源。"""
    _, player_specs = _position_id_specs()
    spec = player_specs["position_id"]
    assert spec.applicability != "REQUIRED", (
        f"fact_player_match_stats.position_id 不应再是 REQUIRED,实际 {spec.applicability}"
    )
    # usual_position 必须仍是 REQUIRED(稳定替代字段)
    usual_spec = player_specs["usual_position"]
    assert usual_spec.applicability == "REQUIRED"


def test_standard_match_not_not_ready_solely_from_bench_position_id_gap():
    """回归本轮核心目标:除 position_id 外全部 REQUIRED 字段均高覆盖时,
    standard_match 不应仅因(已被正确排除的)替补 position_id 缺口被误判 NOT_READY。"""
    field_cov = [
        {"table": "fact_match_lineup", "field": "position_id", "family": "standard_match",
         "applicability": "REQUIRED", "league_id": 47, "season": "2024/2025",
         "applicable_rows": 100, "non_null_rows": 100, "covered_matches": 10,
         "total_matches_in_partition": 10, "not_applicable": False},
        {"table": "fact_match_lineup", "field": "position_id_bench", "family": "standard_match",
         "applicability": "OPTIONAL", "league_id": 47, "season": "2024/2025",
         "applicable_rows": 80, "non_null_rows": 0, "covered_matches": 10,
         "total_matches_in_partition": 10, "not_applicable": False},
        {"table": "fact_match_lineup", "field": "formation", "family": "standard_match",
         "applicability": "REQUIRED", "league_id": 47, "season": "2024/2025",
         "applicable_rows": 100, "non_null_rows": 100, "covered_matches": 10,
         "total_matches_in_partition": 10, "not_applicable": False},
        {"table": "fact_player_match_stats", "field": "position_id", "family": "standard_match",
         "applicability": "OPTIONAL", "league_id": 47, "season": "2024/2025",
         "applicable_rows": 100, "non_null_rows": 72, "covered_matches": 10,
         "total_matches_in_partition": 10, "not_applicable": False},
        {"table": "fact_player_match_stats", "field": "usual_position", "family": "standard_match",
         "applicability": "REQUIRED", "league_id": 47, "season": "2024/2025",
         "applicable_rows": 100, "non_null_rows": 99, "covered_matches": 10,
         "total_matches_in_partition": 10, "not_applicable": False},
    ]
    rows = feature_readiness_rows(field_cov, [], [47], ["standard_match"], {(47, "2024/2025"): 10})
    row = next(r for r in rows if r["season"] == "2024/2025")
    assert row["readiness"] == "READY", (
        f"全部真正 REQUIRED 字段均 >=99% 时不应因替补 position_id 缺口判 NOT_READY,实际 {row['readiness']} "
        f"(min_req={row['minimum_required_field_coverage_pct']})"
    )


def test_standard_match_still_degrades_on_real_required_field_gap():
    """反向验证:引擎没有被"修好到失去检测能力"——如果存在一个真正的 REQUIRED
    字段缺口(与 position_id 无关),readiness 仍必须正确降级。"""
    field_cov = [
        {"table": "fact_match_lineup", "field": "position_id", "family": "standard_match",
         "applicability": "REQUIRED", "league_id": 47, "season": "2024/2025",
         "applicable_rows": 100, "non_null_rows": 100, "covered_matches": 10,
         "total_matches_in_partition": 10, "not_applicable": False},
        {"table": "fact_match_lineup", "field": "country_code", "family": "standard_match",
         "applicability": "REQUIRED", "league_id": 47, "season": "2024/2025",
         "applicable_rows": 100, "non_null_rows": 40, "covered_matches": 10,  # 真实缺口
         "total_matches_in_partition": 10, "not_applicable": False},
    ]
    rows = feature_readiness_rows(field_cov, [], [47], ["standard_match"], {(47, "2024/2025"): 10})
    row = next(r for r in rows if r["season"] == "2024/2025")
    assert row["readiness"] == "NOT_READY", (
        f"country_code 真实缺口(40%)必须让 readiness 降级,实际 {row['readiness']}"
    )
    assert row["minimum_required_field_coverage_pct"] == 40.0


# ══════════════════════════════════════════════════════════════════════
# §四 dim_player 唯一球员计数(不把硬编码数字塞进报告,必须从脚本真实计算)
# ══════════════════════════════════════════════════════════════════════

def test_dim_player_name_diff_summary_dedupes_across_seasons():
    """同一 Player_ID 跨多个赛季出现同一类型差异,不应被误报为多个唯一球员;
    分区出现次数与唯一球员数必须分别统计、互不覆盖。"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        core_p = tmp_path / "core.db"
        verify_p = tmp_path / "verify.db"
        for p in (core_p, verify_p):
            conn = sqlite3.connect(p)
            conn.executescript(DIM_MATCH_DDL + """
                CREATE TABLE dim_player (Player_ID TEXT PRIMARY KEY, Player_Name TEXT);
                CREATE TABLE fact_player_match_stats (Match_ID INTEGER, Player_ID TEXT, Team_ID INTEGER);
                CREATE TABLE fact_match_lineup (Match_ID INTEGER, Team_ID INTEGER, Player_ID TEXT);
            """)
            conn.execute("INSERT INTO dim_match VALUES (1,'2020/2021',47,'2020-08-01',100,200,'A','B',1,0,'Finish',NULL,'1',NULL,NULL,NULL)")
            conn.execute("INSERT INTO dim_match VALUES (2,'2021/2022',47,'2021-08-01',100,200,'A','B',1,0,'Finish',NULL,'1',NULL,NULL,NULL)")
            conn.execute("INSERT INTO fact_player_match_stats VALUES (1,'p_same',100)")
            conn.execute("INSERT INTO fact_player_match_stats VALUES (2,'p_same',100)")  # 同一球员,跨两个赛季
            conn.commit(); conn.close()
        # verify 一侧:重音版本;core 一侧:去重音版本——同一现实球员,两个赛季各出现一次差异
        wv = sqlite3.connect(verify_p); wv.execute("INSERT INTO dim_player VALUES ('p_same','José')"); wv.commit(); wv.close()
        wc = sqlite3.connect(core_p); wc.execute("INSERT INTO dim_player VALUES ('p_same','Jose')"); wc.commit(); wc.close()

        core_conn = connect_ro(core_p, "core")
        verify_conn = connect_ro(verify_p, "verify")
        summary = dim_player_name_diff_summary(core_conn, verify_conn, [47])
        core_conn.close(); verify_conn.close()

    assert summary["unique_players"]["total"] == 1, (
        f"同一 Player_ID 跨 2 个赛季只能算 1 个唯一球员,实际 {summary['unique_players']['total']}"
    )
    assert summary["unique_players"]["appearing_in_multiple_partitions"] == 1, (
        "该球员跨 2 个赛季出现,必须被识别为「跨多分区重复」"
    )
    assert summary["unique_players"]["accent_only"] == 1
    assert summary["unique_players"]["truly_different"] == 0
    assert summary["missing_from_allwin"] == 0
    assert summary["blank_in_allwin"] == 0


# ══════════════════════════════════════════════════════════════════════
# §四/§七 schema contract:dim_player_name_differences JSON 块 + method version
# ══════════════════════════════════════════════════════════════════════

def test_dim_player_partition_occurrences_matches_declared_shape():
    from analysis.data_quality.audit_top5_historical_core import dim_player_partition_occurrences
    rows = [
        {"table_name": "dim_player", "mismatch_types": "ACCENT_NORMALIZATION_ONLY:2;TRULY_DIFFERENT_NAME:1"},
        {"table_name": "dim_player", "mismatch_types": "CASE_OR_WHITESPACE_ONLY:1"},
        {"table_name": "dim_player", "mismatch_types": ""},
    ]
    occ = dim_player_partition_occurrences(rows)
    assert set(occ) == {"accent_only", "truly_different"}
    assert occ["accent_only"] == 3  # 2 ACCENT + 1 CASE_OR_WHITESPACE
    assert occ["truly_different"] == 1


def test_dim_player_name_diff_summary_shape_matches_json_contract():
    """summary["dim_player_name_differences"] 的结构必须与 main() 里实际写入
    JSON 的形状一致(partition_occurrences + unique_players + missing/blank),
    防止字段改名后 JSON 契约与代码脱节。"""
    from analysis.data_quality.audit_top5_historical_core import dim_player_partition_occurrences
    occ = dim_player_partition_occurrences([])
    assert occ == {"accent_only": 0, "truly_different": 0}
    fake_unique = {
        "unique_players": {"total": 0, "accent_only": 0, "truly_different": 0, "conflict": 0, "appearing_in_multiple_partitions": 0},
        "missing_from_allwin": 0, "blank_in_allwin": 0,
    }
    block = {
        "partition_occurrences": occ,
        "unique_players": fake_unique["unique_players"],
        "missing_from_allwin": fake_unique["missing_from_allwin"],
        "blank_in_allwin": fake_unique["blank_in_allwin"],
    }
    assert set(block) == {"partition_occurrences", "unique_players", "missing_from_allwin", "blank_in_allwin"}
    assert set(block["unique_players"]) == {"total", "accent_only", "truly_different", "conflict", "appearing_in_multiple_partitions"}


def test_audit_method_version_bumped_for_this_round():
    """本轮修改了字段适用性分类(position_id)和输出 schema(新增
    dim_player_name_differences),必须升级 method version,避免旧 readiness(v2)
    与新 readiness(v3)在同一批产物里被误当同一口径混用。"""
    src = Path(__file__).parent / "audit_top5_historical_core.py"
    text = src.read_text(encoding="utf-8")
    assert '"audit_method_version": 3' in text, "audit_method_version 必须升级到 3"
    assert '"output_schema_version": 3' in text, "output_schema_version 必须升级到 3"
