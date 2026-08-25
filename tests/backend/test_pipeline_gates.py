"""质量门测试(数据管道重建 Phase 6,全离线,告警经 NOTIFY_ENABLED=0 落库验证)。

覆盖:干净库全 OK、G1 赛程窗口不一致、G2 反退化拒写、G3 kickoff 精度、
G4 实体解析归零/降级、G7 比分不回退、G8 公司口径、G9 WAF;违反的门在
pipeline_alerts 落行(suppressed:disabled,不发网络);样本不足时如实 skipped。
"""

import pytest

from backend.cli import pipeline_gates as pg
from backend.db.connections import connect_ro, connect_rw
from backend.db.util import utc_now_iso
from tests.backend.coreseed import seed_core_schema

NOW = "2026-08-10T00:00:00Z"


@pytest.fixture(autouse=True)
def _notify_disabled(monkeypatch):
    monkeypatch.setenv("NOTIFY_ENABLED", "0")
    monkeypatch.delenv("SERVERCHAN_SENDKEY", raising=False)


def _derived_season(conn, league_id, kickoff):
    """种子行赛季按 (联赛, 日期) 推导——测试大量用相对 now 的动态 kickoff,
    硬编码赛季会随现实时间漂移撞上 0011 赛季触发器(2026-08-25)。"""
    from backend.season_regime import season_for_match

    return season_for_match(conn, league_id, (kickoff or "2026-08-12")[:10])


def _seed_match(conn, mid, league_id=48, kickoff="2026-08-12T12:00:00Z",
                status="NotStarted", precision="exact",
                source="fotmob:fixtures", home_score=None, away_score=None):
    conn.execute(
        """INSERT INTO dim_match
           (Match_ID, Season, League_ID, Date, Home_Team_ID, Away_Team_ID,
            Home_Team_Name, Away_Team_Name, home_score, away_score, status,
            kickoff_at_utc, kickoff_precision, kickoff_source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (mid, _derived_season(conn, league_id, kickoff), league_id,
         (kickoff or "2026-08-12")[:10], mid * 10, mid * 10 + 1,
         f"Home{mid}", f"Away{mid}", home_score, away_score, status,
         kickoff, precision, source),
    )


def _seed_ledger(conn_odds, league_id, verdict, horizon7=5, detail=None):
    conn_odds.execute(
        "INSERT INTO fixture_sync_ledger (run_at, league_id, season, fetched_rows,"
        " horizon7_rows, written_rows, verdict, detail)"
        " VALUES (?, ?, '2026/2027', 10, ?, ?, ?, ?)",
        (utc_now_iso(), league_id, horizon7,
         horizon7 if verdict == "written" else 0, verdict, detail),
    )


def _seed_xref(conn_odds, fotmob_id, titan_id, review_status="auto_ok"):
    conn_odds.execute(
        "INSERT INTO dim_match_xref (fotmob_match_id, provider, provider_match_id,"
        " review_status, created_at, updated_at) VALUES (?, 'nowgoal', ?, ?, ?, ?)",
        (fotmob_id, str(titan_id), review_status, utc_now_iso(), utc_now_iso()),
    )


def _seed_snap(conn_odds, titan_id, observed_at, company_id="8", phase="pre_match"):
    conn_odds.execute(
        "INSERT INTO bronze_ng_odds_snap (provider_match_id, market, company_id,"
        " market_phase, payload_json, payload_hash, observed_at, ingested_at)"
        " VALUES (?, '1x2', ?, ?, '{}', ?, ?, ?)",
        (str(titan_id), company_id, phase, f"h-{titan_id}-{observed_at}-{company_id}",
         observed_at, utc_now_iso()),
    )


def _gate(report, name):
    return next(g for g in report["gates"] if g["gate"] == name)


def _alert_rows():
    conn = connect_ro("platform")
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM pipeline_alerts ORDER BY id")]
    finally:
        conn.close()


class TestCleanDatabase:
    def test_all_gates_ok_on_empty_dbs(self, data_dir):
        report = pg.run(now_iso=NOW)
        assert report["level"] == "OK"
        assert all(g["level"] == "OK" for g in report["gates"])
        assert _alert_rows() == [], "全 OK 不得产生任何告警记录"


class TestFixturesWindow:
    def test_written_ledger_but_empty_core_is_critical(self, data_dir):
        conn_odds = connect_rw("odds")
        _seed_ledger(conn_odds, 48, "written", horizon7=5)
        conn_odds.commit()
        conn_odds.close()
        report = pg.run(now_iso=NOW)
        g = _gate(report, "fixtures_window_empty")
        assert g["level"] == "CRITICAL"
        assert g["violations"][0]["league_id"] == 48
        rows = _alert_rows()
        assert any(r["source"] == "fixtures_window_empty" and r["level"] == "CRITICAL"
                   for r in rows)

    def test_offseason_league_not_flagged(self, data_dir):
        """季外联赛(off_season)7 天无赛程是合法的,不得误报(前身项目教训)。"""
        conn_odds = connect_rw("odds")
        _seed_ledger(conn_odds, 42, "off_season", horizon7=0)
        conn_odds.commit()
        conn_odds.close()
        assert _gate(pg.run(now_iso=NOW), "fixtures_window_empty")["level"] == "OK"

    def test_written_with_matching_core_rows_ok(self, data_dir):
        conn_core = connect_rw("core")
        _seed_match(conn_core, 1, league_id=48, kickoff="2026-08-12T12:00:00Z")
        conn_core.commit()
        conn_core.close()
        conn_odds = connect_rw("odds")
        _seed_ledger(conn_odds, 48, "written", horizon7=1)
        conn_odds.commit()
        conn_odds.close()
        assert _gate(pg.run(now_iso=NOW), "fixtures_window_empty")["level"] == "OK"


class TestCoverageRegression:
    def test_refused_regression_is_critical(self, data_dir):
        conn_odds = connect_rw("odds")
        _seed_ledger(conn_odds, 57, "refused_regression", detail="骤降 40→10")
        conn_odds.commit()
        conn_odds.close()
        g = _gate(pg.run(now_iso=NOW), "league_coverage_regression")
        assert g["level"] == "CRITICAL"
        assert g["violations"][0]["league_id"] == 57
        assert any(r["source"] == "league_coverage_regression" for r in _alert_rows())


class TestKickoffPrecision:
    def test_non_exact_in_window_warns(self, data_dir):
        conn_core = connect_rw("core")
        _seed_match(conn_core, 1, kickoff="2026-08-12T12:00:00Z")           # exact
        _seed_match(conn_core, 2, kickoff=None, precision="date_only", source=None)
        conn_core.execute("UPDATE dim_match SET Date='2026-08-13' WHERE Match_ID=2")
        conn_core.commit()
        conn_core.close()
        g = _gate(pg.run(now_iso=NOW), "kickoff_precision")
        assert g["level"] == "WARNING"
        assert g["non_exact_in_window"] == 1


class TestEntityResolution:
    def _seed_window(self, n, resolved):
        conn_core = connect_rw("core")
        for i in range(1, n + 1):
            _seed_match(conn_core, i, league_id=61, kickoff="2026-08-11T12:00:00Z")
        conn_core.commit()
        conn_core.close()
        conn_odds = connect_rw("odds")
        for i in range(1, resolved + 1):
            _seed_xref(conn_odds, i, 9000 + i)
        conn_odds.commit()
        conn_odds.close()

    def test_zero_resolution_is_critical_and_names_league(self, data_dir):
        self._seed_window(3, resolved=0)
        g = _gate(pg.run(now_iso=NOW), "entity_resolution_degraded")
        assert g["level"] == "CRITICAL"
        assert g["violations"][0]["league_id"] == 61
        assert "葡超" in g["violations"][0]["league"]
        assert any(r["source"] == "entity_resolution_degraded" for r in _alert_rows())

    def test_below_60pct_warns(self, data_dir):
        self._seed_window(4, resolved=2)   # 50%
        assert _gate(pg.run(now_iso=NOW), "entity_resolution_degraded")["level"] == "WARNING"

    def test_small_sample_skipped(self, data_dir):
        self._seed_window(2, resolved=0)   # <3 场不判
        assert _gate(pg.run(now_iso=NOW), "entity_resolution_degraded")["level"] == "OK"


class TestXrefUnmappedUpcoming:
    """G11(2026-08-24 新增):全局聚合版实体解析门,修 G4 的三个结构性盲点——
    36 场比赛摊到 16 个联赛时,G4 按联赛判 <3 场直接跳过,事故整整一周没有任何
    告警;G11 不按联赛拆分,且距开球 ≤48h 仍不可采直接 CRITICAL(不像 G4 止步
    WARNING,WARNING 会被 notify 的每日配额+24h 去重压掉)。"""

    def test_single_league_two_matches_not_skipped(self, data_dir):
        """G4 会因为 <3 场跳过这批;G11 全局聚合不受单联赛样本量限制。"""
        conn_core = connect_rw("core")
        _seed_match(conn_core, 1, league_id=61, kickoff="2026-08-11T12:00:00Z")  # 36h,近
        _seed_match(conn_core, 2, league_id=61, kickoff="2026-08-11T18:00:00Z")  # 42h,近
        conn_core.commit()
        conn_core.close()
        report = pg.run(now_iso=NOW)
        assert _gate(report, "entity_resolution_degraded")["level"] == "OK"       # G4 跳过
        g = _gate(report, "xref_unmapped_upcoming")                               # G11 不跳过
        assert g["level"] == "CRITICAL"
        assert g["near_48h_unpollable"] == 2

    def test_near_48h_unpollable_is_critical(self, data_dir):
        conn_core = connect_rw("core")
        _seed_match(conn_core, 1, league_id=48, kickoff="2026-08-11T12:00:00Z")  # 36h
        conn_core.commit()
        conn_core.close()
        g = _gate(pg.run(now_iso=NOW), "xref_unmapped_upcoming")
        assert g["level"] == "CRITICAL"
        assert g["near_48h_unpollable"] == 1
        assert any(r["source"] == "xref_unmapped_upcoming" and r["level"] == "CRITICAL"
                   for r in _alert_rows())

    def test_far_unpollable_beyond_48h_is_warning_not_critical(self, data_dir):
        conn_core = connect_rw("core")
        _seed_match(conn_core, 1, league_id=48, kickoff="2026-08-14T08:00:00Z")  # 104h,远
        conn_core.commit()
        conn_core.close()
        g = _gate(pg.run(now_iso=NOW), "xref_unmapped_upcoming")
        assert g["level"] == "WARNING"
        assert g["near_48h_unpollable"] == 0
        assert g["far_unpollable"] == 1

    def test_pollable_match_not_flagged(self, data_dir):
        conn_core = connect_rw("core")
        _seed_match(conn_core, 1, league_id=48, kickoff="2026-08-11T12:00:00Z")
        conn_core.commit()
        conn_core.close()
        conn_odds = connect_rw("odds")
        _seed_xref(conn_odds, 1, 9001, review_status="confirmed")
        conn_odds.commit()
        conn_odds.close()
        assert _gate(pg.run(now_iso=NOW), "xref_unmapped_upcoming")["level"] == "OK"

    def test_beyond_168h_window_not_counted(self, data_dir):
        conn_core = connect_rw("core")
        _seed_match(conn_core, 1, league_id=48, kickoff="2026-08-20T00:00:00Z")  # 240h,超窗口
        conn_core.commit()
        conn_core.close()
        g = _gate(pg.run(now_iso=NOW), "xref_unmapped_upcoming")
        assert g["level"] == "OK"
        assert g["detail"] == "no_candidates"

    def test_no_upcoming_matches_ok(self, data_dir):
        assert _gate(pg.run(now_iso=NOW), "xref_unmapped_upcoming")["level"] == "OK"


class TestScoreRegression:
    def test_notstarted_with_score_is_critical(self, data_dir):
        conn_core = connect_rw("core")
        _seed_match(conn_core, 1, home_score=2, away_score=1)
        conn_core.commit()
        conn_core.close()
        g = _gate(pg.run(now_iso=NOW), "score_regression")
        assert g["level"] == "CRITICAL"
        assert g["notstarted_with_score"] == 1


class TestCompanyScope:
    def test_unexpected_cid_is_critical(self, data_dir):
        conn_odds = connect_rw("odds")
        _seed_snap(conn_odds, 9001, "2026-08-09T20:00:00Z", company_id="8")
        _seed_snap(conn_odds, 9001, "2026-08-09T20:05:00Z", company_id="31")  # Sbobet:目标外
        conn_odds.commit()
        conn_odds.close()
        g = _gate(pg.run(now_iso=NOW), "company_scope")
        assert g["level"] == "CRITICAL"
        assert g["unexpected_cids"] == ["31"]

    def test_target_three_companies_ok(self, data_dir):
        conn_odds = connect_rw("odds")
        for cid in ("8", "1", "3"):
            _seed_snap(conn_odds, 9001, "2026-08-09T20:00:00Z", company_id=cid)
        conn_odds.commit()
        conn_odds.close()
        assert _gate(pg.run(now_iso=NOW), "company_scope")["level"] == "OK"


class TestSourceWaf:
    def test_waf_hit_last_hour_is_critical(self, data_dir):
        conn_odds = connect_rw("odds")
        conn_odds.execute(
            "INSERT INTO source_health (source, checked_at, ok, error_summary)"
            " VALUES ('nowgoal', ?, 0, 'WAFBlockedError: NowGoal WAF 拦截(HTTP 403)')",
            ("2026-08-09T23:30:00Z",))
        conn_odds.commit()
        conn_odds.close()
        g = _gate(pg.run(now_iso=NOW), "source_waf_blocked")
        assert g["level"] == "CRITICAL"
        assert any(r["source"] == "source_waf_blocked" for r in _alert_rows())

    def test_old_waf_hit_outside_window_ok(self, data_dir):
        conn_odds = connect_rw("odds")
        conn_odds.execute(
            "INSERT INTO source_health (source, checked_at, ok, error_summary)"
            " VALUES ('nowgoal', '2026-08-09T10:00:00Z', 0, 'WAFBlockedError: x')")
        conn_odds.commit()
        conn_odds.close()
        assert _gate(pg.run(now_iso=NOW), "source_waf_blocked")["level"] == "OK"


def _seed_box_team_match(conn_core, mid, team_id, *, box_shots_coord, box_shots_official,
                          kickoff="2026-08-05T12:00:00Z"):
    """一个队场:coord 侧插 box_shots_coord 脚禁区内坐标射门(X>=88.5,
    Y in [13.84,54.16])+ 1 脚禁区外的(验证几何过滤本身生效),official 侧
    写 fact_team_match_stats.extra_json.shots_inside_box=box_shots_official。"""
    import json as _json
    for i in range(box_shots_coord):
        conn_core.execute(
            "INSERT INTO fact_shotmap (Match_ID, Player_ID, Team_ID, Minute, Period,"
            " X_Coord, Y_Coord, xG, Situation, Outcome, Shot_Type)"
            " VALUES (?, ?, ?, 10, 'FirstHalf', 95.0, 34.0, 0.1, 'RegularPlay', 'Miss', 'RightFoot')",
            (mid, f"p{team_id}-{i}", team_id),
        )
    # 禁区外一脚,确认几何过滤没把它算进去
    conn_core.execute(
        "INSERT INTO fact_shotmap (Match_ID, Player_ID, Team_ID, Minute, Period,"
        " X_Coord, Y_Coord, xG, Situation, Outcome, Shot_Type)"
        " VALUES (?, ?, ?, 20, 'FirstHalf', 40.0, 34.0, 0.05, 'RegularPlay', 'Miss', 'RightFoot')",
        (mid, f"p{team_id}-out", team_id),
    )
    conn_core.execute(
        "INSERT INTO fact_team_match_stats (Match_ID, Team_ID, Period, Goals, extra_json)"
        " VALUES (?, ?, 'All', 0, ?)",
        (mid, team_id, _json.dumps({"shots_inside_box": box_shots_official})),
    )


class TestBoxShotGeometryDrift:
    def test_matching_coordinates_and_official_count_are_ok(self, data_dir):
        conn_core = connect_rw("core")
        seed_core_schema(conn_core)
        for i in range(pg.G10_MIN_TEAM_MATCHES):
            mid = 5000 + i
            _seed_match(conn_core, mid, status="Finish", kickoff="2026-08-05T12:00:00Z",
                        home_score=1, away_score=0)
            _seed_box_team_match(conn_core, mid, mid * 10, box_shots_coord=3, box_shots_official=3.0)
        conn_core.commit()
        conn_core.close()
        g = _gate(pg.run(now_iso=NOW), "box_shot_geometry_drift")
        assert g["level"] == "OK"
        assert g["team_matches"] == pg.G10_MIN_TEAM_MATCHES
        assert g["mismatched"] == 0

    def test_systematic_mismatch_above_threshold_is_warning(self, data_dir):
        """>1% 队场坐标法算出的禁区内射门数与官方计数不相等——坐标系可能
        漂移(采集端换了坐标约定、球场朝向反了),不是正常的压线/缺失噪音。"""
        conn_core = connect_rw("core")
        seed_core_schema(conn_core)
        for i in range(pg.G10_MIN_TEAM_MATCHES):
            mid = 5100 + i
            _seed_match(conn_core, mid, status="Finish", kickoff="2026-08-05T12:00:00Z",
                        home_score=1, away_score=0)
            # 坐标法算出 3 脚,官方计数固定写 5——全部队场都对不上
            _seed_box_team_match(conn_core, mid, mid * 10, box_shots_coord=3, box_shots_official=5.0)
        conn_core.commit()
        conn_core.close()
        g = _gate(pg.run(now_iso=NOW), "box_shot_geometry_drift")
        assert g["level"] == "WARNING"
        assert g["mismatched"] == pg.G10_MIN_TEAM_MATCHES
        assert g["mismatch_rate"] == 1.0

    def test_below_min_sample_is_skipped_not_warning(self, data_dir):
        conn_core = connect_rw("core")
        seed_core_schema(conn_core)
        mid = 5200
        _seed_match(conn_core, mid, status="Finish", kickoff="2026-08-05T12:00:00Z",
                    home_score=1, away_score=0)
        _seed_box_team_match(conn_core, mid, mid * 10, box_shots_coord=3, box_shots_official=5.0)
        conn_core.commit()
        conn_core.close()
        g = _gate(pg.run(now_iso=NOW), "box_shot_geometry_drift")
        assert g["level"] == "OK"
        assert g["detail"] == "skipped_insufficient_sample"

    def test_outside_30_day_window_excluded(self, data_dir):
        conn_core = connect_rw("core")
        seed_core_schema(conn_core)
        for i in range(pg.G10_MIN_TEAM_MATCHES):
            mid = 5300 + i
            _seed_match(conn_core, mid, status="Finish", kickoff="2026-01-01T12:00:00Z",
                        home_score=1, away_score=0)
            _seed_box_team_match(conn_core, mid, mid * 10, box_shots_coord=3, box_shots_official=5.0)
        conn_core.commit()
        conn_core.close()
        g = _gate(pg.run(now_iso=NOW), "box_shot_geometry_drift")
        assert g["level"] == "OK"
        assert g["detail"] == "skipped_insufficient_sample"


class TestNotifySuppression:
    def test_no_notify_flag_skips_alerts(self, data_dir):
        conn_odds = connect_rw("odds")
        _seed_ledger(conn_odds, 57, "refused_regression")
        conn_odds.commit()
        conn_odds.close()
        report = pg.run(now_iso=NOW, notify_alerts=False)
        assert report["level"] == "CRITICAL"
        assert _alert_rows() == []


class TestWorkerJobShape:
    def test_gate_findings_do_not_fail_the_job(self, data_dir):
        """质量门"发现问题"≠任务失败:runner 的 pipeline_gates 任务应 succeeded,
        违反数进 output_count/meta——避免再触发一条 pipeline_step_failure 重复告警。"""
        conn_core = connect_rw("core")
        _seed_match(conn_core, 1, home_score=2, away_score=1)   # G7 CRITICAL
        conn_core.commit()
        conn_core.close()
        from backend.worker import runner
        res = runner.run_job("pipeline_gates")
        assert res["status"] == "succeeded"
        assert res["output_count"] >= 1
        assert res["meta"]["level"] == "CRITICAL"
