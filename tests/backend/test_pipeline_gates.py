"""质量门测试(数据管道重建 Phase 6,全离线,告警经 NOTIFY_ENABLED=0 落库验证)。

覆盖:干净库全 OK、G1 赛程窗口不一致、G2 反退化拒写、G3 kickoff 精度、
G4 实体解析归零/降级、G7 比分不回退、G8 公司口径、G9 WAF;违反的门在
pipeline_alerts 落行(suppressed:disabled,不发网络);样本不足时如实 skipped。
"""

import pytest

from backend.cli import pipeline_gates as pg
from backend.db.connections import connect_ro, connect_rw
from backend.db.util import utc_now_iso

NOW = "2026-08-10T00:00:00Z"


@pytest.fixture(autouse=True)
def _notify_disabled(monkeypatch):
    monkeypatch.setenv("NOTIFY_ENABLED", "0")
    monkeypatch.delenv("SERVERCHAN_SENDKEY", raising=False)


def _seed_match(conn, mid, league_id=48, kickoff="2026-08-12T12:00:00Z",
                status="NotStarted", precision="exact",
                source="fotmob:fixtures", home_score=None, away_score=None):
    conn.execute(
        """INSERT INTO dim_match
           (Match_ID, Season, League_ID, Date, Home_Team_ID, Away_Team_ID,
            Home_Team_Name, Away_Team_Name, home_score, away_score, status,
            kickoff_at_utc, kickoff_precision, kickoff_source)
           VALUES (?, '2026/2027', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (mid, league_id, (kickoff or "2026-08-12")[:10], mid * 10, mid * 10 + 1,
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
