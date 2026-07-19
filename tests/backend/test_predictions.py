"""P0.5 测试:预测登记簿生命周期、导入口径、结算+指标、track record 样本口径、manifest。"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.commands.predictions import (
    PredictionError,
    build_daily_manifest,
    get_or_create_model_version,
    lock_snapshot,
    publish_snapshot,
    register_snapshot,
    retract_snapshot,
    settle_outcomes,
    supersede_snapshot,
)
from backend.db.connections import connect_ro, connect_rw
from backend.eval.metrics import evaluate_all, rps
from backend.queries.track_record import evaluation_samples, official_samples


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _future(days=1):
    return _iso(datetime.now(timezone.utc) + timedelta(days=days))


def _past(days=1):
    return _iso(datetime.now(timezone.utc) - timedelta(days=days))


@pytest.fixture
def platform(data_dir):
    conn = connect_rw("platform")
    get_or_create_model_version(conn, "m-test", "dixon-coles")
    yield conn
    conn.close()


def _register(conn, match_id=1, kickoff=None, probs=(0.5, 0.3, 0.2), **kw):
    return register_snapshot(
        conn,
        match_id=match_id,
        kickoff_at_utc=kickoff or _future(),
        model_version_id="m-test",
        home_win=probs[0], draw=probs[1], away_win=probs[2],
        **kw,
    )


class TestLifecycle:
    def test_draft_publish_lock(self, platform):
        sid = _register(platform)
        publish_snapshot(platform, sid, actor=None)
        lock_snapshot(platform, sid, actor=None)
        row = platform.execute("SELECT * FROM prediction_snapshots WHERE id=?", (sid,)).fetchone()
        assert row["status"] == "locked" and row["is_official"] == 1
        assert row["published_at"] and row["locked_at"]

    def test_publish_after_kickoff_refused(self, platform):
        sid = _register(platform, kickoff=_past())
        with pytest.raises(PredictionError) as ei:
            publish_snapshot(platform, sid, actor=None)
        assert ei.value.reason == "post_kickoff"

    def test_lock_requires_published(self, platform):
        sid = _register(platform)
        with pytest.raises(PredictionError) as ei:
            lock_snapshot(platform, sid, actor=None)
        assert ei.value.reason == "bad_state"

    def test_retract_keeps_row(self, platform):
        sid = _register(platform)
        publish_snapshot(platform, sid, actor=None)
        lock_snapshot(platform, sid, actor=None)
        retract_snapshot(platform, sid, actor=None, reason="导入重复")
        row = platform.execute("SELECT status, home_win FROM prediction_snapshots WHERE id=?", (sid,)).fetchone()
        assert row["status"] == "retracted" and row["home_win"] == 0.5

    def test_supersede_links_and_keeps_old(self, platform):
        old = _register(platform)
        publish_snapshot(platform, old, actor=None)
        lock_snapshot(platform, old, actor=None)
        new = supersede_snapshot(
            platform, old, actor=None,
            home_win=0.4, draw=0.35, away_win=0.25, status="draft",
        )
        old_row = platform.execute("SELECT superseded_by, home_win FROM prediction_snapshots WHERE id=?", (old,)).fetchone()
        assert old_row["superseded_by"] == new
        assert old_row["home_win"] == 0.5   # 旧版本原样保留

    def test_bad_probabilities_rejected(self, platform):
        with pytest.raises(PredictionError) as ei:
            _register(platform, probs=(0.5, 0.3, 0.5))
        assert ei.value.reason == "bad_probabilities"


def _seed_core_tables(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS dim_match (
            Match_ID INTEGER PRIMARY KEY, Season TEXT, League_ID INTEGER, Date TEXT,
            Home_Team_ID INTEGER, Away_Team_ID INTEGER, Home_Team_Name TEXT, Away_Team_Name TEXT,
            home_score INTEGER, away_score INTEGER, status TEXT, Match_Round TEXT)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS gold_wdl_predictions (
            match_id INTEGER PRIMARY KEY, league_id INTEGER, season TEXT,
            lambda_home REAL, lambda_away REAL,
            lambda_home_is_fallback INTEGER, lambda_away_is_fallback INTEGER,
            p_home REAL, p_draw REAL, p_away REAL,
            calibrated INTEGER, updated_at TEXT, confidence TEXT, reason TEXT)"""
    )


class TestImportAdapter:
    def test_import_split_and_idempotency(self, data_dir):
        from backend.cli.import_gold_predictions import import_gold

        core = connect_rw("core")
        _seed_core_tables(core)
        # 2025/2026:比赛已完,gold 写入在赛后 → legacy_unverified
        core.execute("INSERT INTO dim_match (Match_ID, Season, League_ID, Date, status, home_score, away_score) VALUES (101,'2025/2026',47,'2026-05-01','Finish',2,1)")
        core.execute("INSERT INTO gold_wdl_predictions (match_id, league_id, season, lambda_home, lambda_away, p_home, p_draw, p_away, calibrated, updated_at) VALUES (101,47,'2025/2026',1.5,1.1,0.5,0.3,0.2,1,'2026-07-09 19:42:15')")
        # 2026/2027:未开球 → draft
        core.execute("INSERT INTO dim_match (Match_ID, Season, League_ID, Date, status) VALUES (201,'2026/2027',47,'2027-05-01','NotStarted')")
        core.execute("INSERT INTO gold_wdl_predictions (match_id, league_id, season, lambda_home, lambda_away, p_home, p_draw, p_away, calibrated, updated_at, confidence) VALUES (201,47,'2026/2027',1.4,1.2,0.45,0.28,0.27,1,'2026-07-09 23:40:05','normal')")
        core.commit()

        platform = connect_rw("platform")
        stats = import_gold(platform, core)
        assert stats == {"total": 2, "skipped": 0, "legacy_unverified": 1, "draft": 1}

        rows = {r["match_id"]: r for r in platform.execute("SELECT * FROM prediction_snapshots")}
        legacy, draft = rows[101], rows[201]
        assert legacy["status"] == "legacy_unverified" and legacy["is_official"] == 0
        assert legacy["visibility"] == "internal"
        assert legacy["generated_at"] == "2026-07-09T19:42:15Z"
        assert draft["status"] == "draft" and draft["kickoff_at_utc"] == "2027-05-01T00:00:00Z"

        # 幂等
        stats2 = import_gold(platform, core)
        assert stats2["skipped"] == 2 and stats2["draft"] == 0
        core.close()
        platform.close()

    def test_legacy_cannot_become_official(self, platform):
        with pytest.raises(PredictionError) as ei:
            _register(platform, status="legacy_unverified", is_official=1)
        assert ei.value.reason == "bad_status"


def _insert_official(conn, match_id, probs, kickoff, published_offset_min=-60, status="locked"):
    """直接 SQL 造正式样本(kickoff 可在过去,用于结算/指标路径测试)。"""
    from backend.db.util import new_uuid

    pub = _iso(datetime.strptime(kickoff, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
               + timedelta(minutes=published_offset_min))
    sid = new_uuid()
    conn.execute(
        """INSERT INTO prediction_snapshots
           (id, match_id, kickoff_at_utc, model_version_id, generated_at, published_at, locked_at,
            prediction_hash, home_win, draw, away_win, visibility, status, is_official, created_at)
           VALUES (?, ?, ?, 'm-test', ?, ?, ?, 'h', ?, ?, ?, 'public', ?, 1, ?)""",
        (sid, match_id, kickoff, pub, pub, pub, probs[0], probs[1], probs[2], status, pub),
    )
    return sid


class TestSettlementAndMetrics:
    def test_settle_and_evaluate(self, data_dir, platform):
        core = connect_rw("core")
        _seed_core_tables(core)
        kickoff = _past(days=3)
        # A:主胜且预测主胜;B:平局但预测客胜
        _insert_official(platform, 301, (0.7, 0.2, 0.1), kickoff)
        _insert_official(platform, 302, (0.2, 0.3, 0.5), kickoff)
        core.execute("INSERT INTO dim_match (Match_ID, Season, League_ID, Date, status, home_score, away_score) VALUES (301,'2025/2026',47,?, 'Finish',3,1)", (kickoff[:10],))
        core.execute("INSERT INTO dim_match (Match_ID, Season, League_ID, Date, status, home_score, away_score) VALUES (302,'2025/2026',47,?, 'Finish',1,1)", (kickoff[:10],))
        core.commit()

        assert settle_outcomes(platform, core) == 2
        samples = evaluation_samples(platform)
        assert len(samples) == 2
        m = evaluate_all(samples)
        assert m["accuracy"] == 0.5
        assert abs(m["rps"] - 0.0975) < 1e-9
        assert abs(m["brier"] - 0.46) < 1e-9
        core.close()

    def test_perfect_and_uniform_rps(self):
        assert rps([((1.0, 0.0, 0.0), "home")]) == 0.0
        uniform = rps([((1 / 3, 1 / 3, 1 / 3), "home")])
        assert abs(uniform - (((1 / 3 - 1) ** 2 + (2 / 3 - 1) ** 2) / 2)) < 1e-9


class TestTrackRecordScope:
    def test_only_official_pre_kickoff_counted(self, platform):
        kickoff_past = _past(days=2)
        # 合格正式样本
        _insert_official(platform, 401, (0.6, 0.25, 0.15), kickoff_past)
        # 撤回的官方样本:在列表中、计入 retracted_count、不进指标样本
        _insert_official(platform, 402, (0.5, 0.3, 0.2), kickoff_past, status="retracted")
        # 开球后才发布 → 不合格
        _insert_official(platform, 403, (0.4, 0.3, 0.3), kickoff_past, published_offset_min=+30)
        # draft / legacy_unverified → 不合格
        _register(platform, match_id=404)
        _register(platform, match_id=405, status="legacy_unverified", visibility="internal")

        result = official_samples(platform)
        ids = {s["match_id"] for s in result["samples"]}
        assert ids == {401, 402}
        assert result["total"] == 2 and result["retracted_count"] == 1
        eval_ids = evaluation_samples(platform)
        assert len(eval_ids) == 0   # 未结算 → 不进指标

    def test_empty_state_is_honest(self, platform):
        result = official_samples(platform)
        assert result == {"total": 0, "retracted_count": 0, "samples": []}


class TestManifest:
    def test_manifest_stable_and_versioned(self, platform):
        kickoff = _future(days=3)
        s1 = _insert_official(platform, 501, (0.5, 0.3, 0.2), kickoff)
        date = platform.execute(
            "SELECT substr(published_at,1,10) FROM prediction_snapshots WHERE id=?", (s1,)
        ).fetchone()[0]
        r1 = build_daily_manifest(platform, date)
        assert r1["version"] == 1 and r1["changed"] is True and r1["entries"] == 1
        r2 = build_daily_manifest(platform, date)
        assert r2["version"] == 1 and r2["changed"] is False
        assert r2["manifest_hash"] == r1["manifest_hash"]
        _insert_official(platform, 502, (0.4, 0.3, 0.3), kickoff)
        r3 = build_daily_manifest(platform, date)
        assert r3["version"] == 2 and r3["manifest_hash"] != r1["manifest_hash"]
