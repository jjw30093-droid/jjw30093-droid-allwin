"""P0.5 测试:预测登记簿生命周期、导入口径、结算+指标、track record 样本口径、manifest。"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.commands.predictions import (
    PredictionError,
    build_daily_manifest,
    edit_snapshot,
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

from .authflow import wechat_scan_login


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _future(days=1):
    return _iso(datetime.now(timezone.utc) + timedelta(days=days))


def _past(days=1):
    return _iso(datetime.now(timezone.utc) - timedelta(days=days))


ORIGIN = {"Origin": "http://localhost:3000"}


def _login_admin(app, ip):
    from fastapi.testclient import TestClient

    from backend.cli.create_admin import create_admin

    conn = connect_rw("platform")
    try:
        create_admin(conn, "boss", "admin-pass-123", reset=True)
    finally:
        conn.close()
    admin = TestClient(app)
    r = admin.post("/api/v1/auth/password/login",
                   json={"username": "boss", "password": "admin-pass-123"},
                   headers={"x-real-ip": ip})
    assert r.status_code == 200
    return admin


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("allwin_csrf"), **ORIGIN}


@pytest.fixture
def platform(data_dir):
    conn = connect_rw("platform")
    get_or_create_model_version(conn, "m-test", "dixon-coles")
    yield conn
    conn.close()


def _register(conn, match_id=1, kickoff=None, probs=(0.5, 0.3, 0.2),
              kickoff_precision="exact", kickoff_source="test:fixture", **kw):
    """默认构造一个"精确 + 有来源"的合法赛前快照(exact provenance)。

    需要测试 date_only/unknown/缺来源等不合格情形时,显式传 kickoff_precision/
    kickoff_source 覆盖。
    """
    return register_snapshot(
        conn,
        match_id=match_id,
        kickoff_at_utc=kickoff or _future(),
        kickoff_precision=kickoff_precision,
        kickoff_source=kickoff_source,
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


class TestEditSnapshot:
    """2026-08-05:用户明确要求正式预测"随时可以更改"——edit_snapshot 不受
    draft/published/locked/retracted 或开球前/后门禁约束,但每次真正产生变化的
    修正都必须写 prediction_snapshot_edits 留痕(见 CLAUDE.md §9.1 / migration 0007)。
    """

    def test_edit_draft_row(self, platform):
        sid = _register(platform)
        result = edit_snapshot(platform, sid, actor=None, reason="调整", home_win=0.6, draw=0.25, away_win=0.15)
        assert result["changed_fields"] == ["away_win", "draw", "home_win"]
        assert result["edit_count"] == 1

    def test_edit_locked_row_succeeds(self, platform):
        """核心场景:锁定后仍可编辑——这是本次改动的直接目的。"""
        sid = _register(platform)
        publish_snapshot(platform, sid, actor=None)
        lock_snapshot(platform, sid, actor=None)
        result = edit_snapshot(platform, sid, actor=None, reason="真实数据修正", home_win=0.7, draw=0.2, away_win=0.1)
        assert result["edit_count"] == 1
        row = platform.execute("SELECT home_win, draw, away_win, status, locked_at FROM prediction_snapshots WHERE id=?", (sid,)).fetchone()
        assert (row["home_win"], row["draw"], row["away_win"]) == (0.7, 0.2, 0.1)
        assert row["status"] == "locked" and row["locked_at"]  # 编辑不改变状态机

    def test_edit_retracted_row_succeeds(self, platform):
        sid = _register(platform)
        publish_snapshot(platform, sid, actor=None)
        lock_snapshot(platform, sid, actor=None)
        retract_snapshot(platform, sid, actor=None, reason="test")
        result = edit_snapshot(platform, sid, actor=None, reason="撤回后仍修正", confidence="low")
        assert result["changed_fields"] == ["confidence"]

    def test_edit_after_kickoff_succeeds(self, platform):
        """"随时"包含开球后——publish/lock/supersede 的 _require_pre_kickoff 门禁
        不适用于 edit_snapshot(这是有意的设计差异,不是遗漏)。"""
        sid = _insert_official(platform, 991, (0.5, 0.3, 0.2), _past(days=1))
        result = edit_snapshot(platform, sid, actor=None, reason="赛后修正", home_win=0.55, draw=0.25, away_win=0.2)
        assert result["edit_count"] == 1

    def test_bad_probabilities_rejected(self, platform):
        sid = _register(platform)
        publish_snapshot(platform, sid, actor=None)
        lock_snapshot(platform, sid, actor=None)
        with pytest.raises(PredictionError) as ei:
            edit_snapshot(platform, sid, actor=None, reason="改坏", home_win=0.9)
        assert ei.value.reason == "bad_probabilities"
        # 校验失败不能留下部分写入的痕迹
        row = platform.execute("SELECT home_win, edit_count FROM prediction_snapshots WHERE id=?", (sid,)).fetchone()
        assert row["home_win"] == 0.5 and row["edit_count"] == 0

    def test_probability_merge_uses_current_values_for_untouched_fields(self, platform):
        """只传 home_win 时,必须用快照当前的 draw/away_win 合并校验,不是假设它们为 0。"""
        sid = _register(platform, probs=(0.5, 0.3, 0.2))
        with pytest.raises(PredictionError) as ei:
            edit_snapshot(platform, sid, actor=None, reason="只改一个概率,和不为1", home_win=0.9)
        assert ei.value.reason == "bad_probabilities"
        # 合法的单字段概率修正(和依然为 1)必须成功
        result = edit_snapshot(platform, sid, actor=None, reason="合法单字段修正", home_win=0.5, draw=0.3, away_win=0.2)
        assert result["changed_fields"] == []  # 值和原来相同,no-op

    def test_empty_reason_rejected(self, platform):
        sid = _register(platform)
        with pytest.raises(PredictionError) as ei:
            edit_snapshot(platform, sid, actor=None, reason="", home_win=0.6, draw=0.25, away_win=0.15)
        assert ei.value.reason == "bad_reason"
        with pytest.raises(PredictionError):
            edit_snapshot(platform, sid, actor=None, reason="   ", home_win=0.6, draw=0.25, away_win=0.15)

    def test_noop_edit_does_not_write_history_or_increment_count(self, platform):
        sid = _register(platform, probs=(0.5, 0.3, 0.2))
        result = edit_snapshot(platform, sid, actor=None, reason="没有真实变化", home_win=0.5, draw=0.3, away_win=0.2)
        assert result == {"edit_id": None, "changed_fields": [], "edit_count": 0}
        count = platform.execute(
            "SELECT COUNT(*) FROM prediction_snapshot_edits WHERE snapshot_id=?", (sid,)
        ).fetchone()[0]
        assert count == 0

    def test_multiple_edits_accumulate_history_and_count(self, platform):
        sid = _register(platform, probs=(0.5, 0.3, 0.2))
        edit_snapshot(platform, sid, actor=None, reason="第一次修正", home_win=0.55, draw=0.25, away_win=0.2)
        edit_snapshot(platform, sid, actor=None, reason="第二次修正", home_win=0.6, draw=0.25, away_win=0.15)
        row = platform.execute("SELECT edit_count, last_edited_at, home_win FROM prediction_snapshots WHERE id=?", (sid,)).fetchone()
        assert row["edit_count"] == 2
        assert row["last_edited_at"] is not None
        assert row["home_win"] == 0.6
        rows = platform.execute(
            "SELECT reason FROM prediction_snapshot_edits WHERE snapshot_id=? ORDER BY edited_at", (sid,)
        ).fetchall()
        assert [r["reason"] for r in rows] == ["第一次修正", "第二次修正"]

    def test_before_json_only_contains_changed_fields(self, platform):
        sid = _register(platform, probs=(0.5, 0.3, 0.2), confidence="normal")
        edit_snapshot(platform, sid, actor=None, reason="只改一个字段", confidence="low")
        row = platform.execute(
            "SELECT before_json, after_json FROM prediction_snapshot_edits WHERE snapshot_id=?", (sid,)
        ).fetchone()
        import json as _json
        assert _json.loads(row["before_json"]) == {"confidence": "normal"}
        assert _json.loads(row["after_json"]) == {"confidence": "low"}

    def test_edit_recomputes_prediction_hash(self, platform):
        sid = _register(platform, probs=(0.5, 0.3, 0.2))
        old_hash = platform.execute("SELECT prediction_hash FROM prediction_snapshots WHERE id=?", (sid,)).fetchone()["prediction_hash"]
        edit_snapshot(platform, sid, actor=None, reason="改概率", home_win=0.6, draw=0.25, away_win=0.15)
        new_hash = platform.execute("SELECT prediction_hash FROM prediction_snapshots WHERE id=?", (sid,)).fetchone()["prediction_hash"]
        assert old_hash != new_hash

    def test_edit_writes_audit_log(self, platform):
        sid = _register(platform)
        edit_snapshot(platform, sid, actor=None, reason="审计留痕验证", home_win=0.6, draw=0.25, away_win=0.15)
        row = platform.execute(
            "SELECT action, target_id FROM audit_logs WHERE action='prediction.edit' AND target_id=?", (sid,)
        ).fetchone()
        assert row is not None

    def test_edit_history_table_is_append_only(self, platform):
        import sqlite3 as _sqlite3

        sid = _register(platform)
        edit_snapshot(platform, sid, actor=None, reason="生成一条历史", home_win=0.6, draw=0.25, away_win=0.15)
        edit_id = platform.execute(
            "SELECT id FROM prediction_snapshot_edits WHERE snapshot_id=?", (sid,)
        ).fetchone()["id"]
        with pytest.raises(_sqlite3.IntegrityError, match="append-only"):
            platform.execute("UPDATE prediction_snapshot_edits SET reason='改过了' WHERE id=?", (edit_id,))
        with pytest.raises(_sqlite3.IntegrityError, match="append-only"):
            platform.execute("DELETE FROM prediction_snapshot_edits WHERE id=?", (edit_id,))

    def test_editing_does_not_change_already_generated_manifest(self, platform):
        """编辑发生在 manifest 生成之后:已有 manifest 是历史时刻快照,不追溯改写;
        重新生成才会用新值。"""
        kickoff = _future(days=2)
        sid = _insert_official(platform, 992, (0.5, 0.3, 0.2), kickoff, published_offset_min=-2880)
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        r1 = build_daily_manifest(platform, date)

        edit_snapshot(platform, sid, actor=None, reason="生成清单后修正", home_win=0.6, draw=0.25, away_win=0.15)

        row = platform.execute(
            "SELECT manifest_json FROM prediction_manifests WHERE manifest_date=? AND version=?",
            (date, r1["version"]),
        ).fetchone()
        import json as _json
        entry = next(e for e in _json.loads(row["manifest_json"])["entries"] if e["id"] == sid)
        assert entry["home_win"] == 0.5  # 旧 manifest 内容不变

        r2 = build_daily_manifest(platform, date)
        assert r2["changed"] is True and r2["version"] == r1["version"] + 1
        row2 = platform.execute(
            "SELECT manifest_json FROM prediction_manifests WHERE manifest_date=? AND version=?",
            (date, r2["version"]),
        ).fetchone()
        entry2 = next(e for e in _json.loads(row2["manifest_json"])["entries"] if e["id"] == sid)
        assert entry2["home_win"] == 0.6  # 新 manifest 用编辑后的值

    def test_editing_does_not_affect_official_sample_membership(self, platform):
        """公开样本口径不变量(CLAUDE.md §9.1):编辑内容不影响该快照是否属于正式样本集合。"""
        sid = _insert_official(platform, 993, (0.5, 0.3, 0.2), _past(days=1))
        before = official_samples(platform)["total"]
        edit_snapshot(platform, sid, actor=None, reason="编辑不影响样本资格", home_win=0.6, draw=0.25, away_win=0.15)
        after = official_samples(platform)["total"]
        assert before == after

    def test_admin_edit_endpoint_requires_csrf_and_writes_history(self, data_dir, platform, client):
        """POST /api/v1/admin/predictions/{id}/edit:需要 admin+CSRF,成功后落库 +
        写 prediction_snapshot_edits,与 publish/lock/retract 走同一套门禁。"""
        sid = _insert_official(platform, 994, (0.5, 0.3, 0.2), _past(days=1))
        platform.commit()

        # 无 CSRF token 直接 POST 被拒绝(与 publish/lock/retract 同款保护)
        admin = _login_admin(client.app, "10.9.9.10")
        no_csrf = admin.post(
            f"/api/v1/admin/predictions/{sid}/edit",
            json={"reason": "缺 CSRF", "home_win": 0.6, "draw": 0.25, "away_win": 0.15},
            headers=ORIGIN,
        )
        assert no_csrf.status_code == 403

        r = admin.post(
            f"/api/v1/admin/predictions/{sid}/edit",
            json={"reason": "真实数据修正", "home_win": 0.6, "draw": 0.25, "away_win": 0.15},
            headers=_csrf(admin),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["changed_fields"] == ["away_win", "draw", "home_win"]
        assert body["edit_count"] == 1

        row = platform.execute(
            "SELECT home_win, draw, away_win, edit_count FROM prediction_snapshots WHERE id=?", (sid,)
        ).fetchone()
        assert (row["home_win"], row["draw"], row["away_win"]) == (0.6, 0.25, 0.15)
        assert row["edit_count"] == 1

        # 非法概率之和被拒(409,PredictionError 转换)
        bad = admin.post(
            f"/api/v1/admin/predictions/{sid}/edit",
            json={"reason": "改坏", "home_win": 0.9},
            headers=_csrf(admin),
        )
        assert bad.status_code == 409
        assert bad.json()["code"] == "bad_probabilities"

    def test_admin_edit_endpoint_rejects_non_admin(self, data_dir, platform, client, fresh_ip):
        """普通登录用户(非 admin)调用 edit 端点必须被拒绝,不能靠隐藏路由绕过权限
        (CLAUDE.md §8.3:Admin 不能只靠隐藏路由)。"""
        sid = _insert_official(platform, 995, (0.5, 0.3, 0.2), _past(days=1))
        platform.commit()

        wechat_scan_login(client, ip=fresh_ip)
        assert client.get("/api/v1/me").json()["authenticated"]

        resp = client.post(
            f"/api/v1/admin/predictions/{sid}/edit",
            json={"reason": "无权限尝试", "home_win": 0.6, "draw": 0.25, "away_win": 0.15},
            headers=_csrf(client),
        )
        assert resp.status_code in (401, 403)


class TestUtcNormalizationAtWrite:
    """12. register_snapshot 对带偏移的 exact kickoff 在写入时规范化为 UTC 'Z',
    不保留调用方原始的 +08:00/-05:00 等偏移格式。"""

    def test_positive_offset_normalized_to_z(self, platform):
        sid = _register(platform, kickoff="2099-06-01T22:00:00+08:00")
        row = platform.execute(
            "SELECT kickoff_at_utc FROM prediction_snapshots WHERE id=?", (sid,)
        ).fetchone()
        assert row["kickoff_at_utc"] == "2099-06-01T14:00:00Z"

    def test_negative_offset_normalized_to_z(self, platform):
        sid = _register(platform, kickoff="2099-06-01T09:00:00-05:00")
        row = platform.execute(
            "SELECT kickoff_at_utc FROM prediction_snapshots WHERE id=?", (sid,)
        ).fetchone()
        assert row["kickoff_at_utc"] == "2099-06-01T14:00:00Z"

    def test_publish_and_lock_succeed_after_offset_normalization(self, platform):
        """规范化后的记录本身仍能正常走完 publish/lock 流程(不因规范化引入回归)。"""
        sid = _register(platform, kickoff=_future(days=2).replace("Z", "+00:00"))
        publish_snapshot(platform, sid, actor=None)
        lock_snapshot(platform, sid, actor=None)
        row = platform.execute(
            "SELECT status, kickoff_at_utc FROM prediction_snapshots WHERE id=?", (sid,)
        ).fetchone()
        assert row["status"] == "locked" and row["kickoff_at_utc"].endswith("Z")


class TestMixedTimezoneJuliandayDefense:
    """13/14:用户给出的具体反例——kickoff_at_utc 用 '-05:00' 偏移格式写入(代表
    未经 register_snapshot 规范化的历史/外部写入路径),published_at 是 UTC 'Z'。
    按时间语义,published(23:00Z)比 kickoff(20:00-05:00 = 次日 01:00Z)早 2 小时,
    是合法的赛前发布;但裸文本比较会因为 '23' 在字典序上大于 '20' 而错误地判定
    "published_at 不早于 kickoff_at_utc",导致这条本应合格的官方记录被 track
    record/evaluation/manifest 悄悄漏掉。julianday() 比较必须给出正确结论。"""

    KICKOFF = "2099-01-01T20:00:00-05:00"    # = 2099-01-02T01:00:00Z
    PUBLISHED = "2099-01-01T23:00:00Z"        # 比 kickoff 早 2 小时

    def test_text_comparison_would_be_wrong_precondition(self):
        """先证明这不是伪造的边界情形:裸文本比较确实会给出错误结论。"""
        assert not (self.PUBLISHED < self.KICKOFF)   # 文本比较误判"不早于"

    def _insert_legacy_offset_official(self, platform, match_id):
        from backend.db.util import new_uuid

        sid = new_uuid()
        platform.execute(
            """INSERT INTO prediction_snapshots
               (id, match_id, kickoff_at_utc, kickoff_precision, kickoff_source,
                model_version_id, generated_at, published_at, locked_at,
                prediction_hash, home_win, draw, away_win, visibility, status,
                is_official, created_at)
               VALUES (?, ?, ?, 'exact', 'legacy:import', 'm-test', ?, ?, ?,
                       'h', 0.5, 0.3, 0.2, 'public', 'locked', 1, ?)""",
            (sid, match_id, self.KICKOFF, self.PUBLISHED, self.PUBLISHED,
             self.PUBLISHED, self.PUBLISHED),
        )
        platform.commit()
        return sid

    def test_reaches_official_samples(self, platform):
        self._insert_legacy_offset_official(platform, 501)
        official = official_samples(platform)
        assert 501 in {s["match_id"] for s in official["samples"]}
        assert official["total"] == 1

    def test_reaches_evaluation_samples_once_settled(self, platform):
        self._insert_legacy_offset_official(platform, 502)
        platform.execute(
            "INSERT INTO prediction_outcomes (match_id, home_goals, away_goals, outcome, settled_at)"
            " VALUES (502, 2, 0, 'home', ?)",
            (self.PUBLISHED,),
        )
        platform.commit()
        samples = evaluation_samples(platform)
        assert len(samples) == 1
        assert samples[0][1] == "home"

    def test_reaches_daily_manifest(self, platform):
        self._insert_legacy_offset_official(platform, 503)
        manifest = build_daily_manifest(platform, "2099-01-01")
        assert manifest["entries"] == 1


def _seed_core_tables(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS dim_match (
            Match_ID INTEGER PRIMARY KEY, Season TEXT, League_ID INTEGER, Date TEXT,
            Home_Team_ID INTEGER, Away_Team_ID INTEGER, Home_Team_Name TEXT, Away_Team_Name TEXT,
            home_score INTEGER, away_score INTEGER, status TEXT, Match_Round TEXT,
            kickoff_at_utc TEXT, kickoff_precision TEXT, kickoff_source TEXT)"""
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
        # 2026/2027:未开球、canonical 只有日期(无精确 kickoff)→ draft + date_only
        core.execute("INSERT INTO dim_match (Match_ID, Season, League_ID, Date, status, kickoff_precision) VALUES (201,'2026/2027',47,'2027-05-01','NotStarted','date_only')")
        core.execute("INSERT INTO gold_wdl_predictions (match_id, league_id, season, lambda_home, lambda_away, p_home, p_draw, p_away, calibrated, updated_at, confidence) VALUES (201,47,'2026/2027',1.4,1.2,0.45,0.28,0.27,1,'2026-07-09 23:40:05','normal')")
        # 2026/2027:canonical 有精确 kickoff(exact + 来源)→ draft + exact 透传
        core.execute("INSERT INTO dim_match (Match_ID, Season, League_ID, Date, status, kickoff_at_utc, kickoff_precision, kickoff_source) VALUES (202,'2026/2027',47,'2027-05-02','NotStarted','2027-05-02T14:30:00Z','exact','fotmob:fixtures')")
        core.execute("INSERT INTO gold_wdl_predictions (match_id, league_id, season, lambda_home, lambda_away, p_home, p_draw, p_away, calibrated, updated_at, confidence) VALUES (202,47,'2026/2027',1.3,1.3,0.4,0.3,0.3,1,'2026-07-09 23:40:05','normal')")
        core.commit()

        platform = connect_rw("platform")
        stats = import_gold(platform, core)
        assert stats["total"] == 3 and stats["legacy_unverified"] == 1 and stats["draft"] == 2

        rows = {r["match_id"]: r for r in platform.execute("SELECT * FROM prediction_snapshots")}
        legacy, draft_dateonly, draft_exact = rows[101], rows[201], rows[202]
        assert legacy["status"] == "legacy_unverified" and legacy["is_official"] == 0
        assert legacy["visibility"] == "internal"
        assert legacy["generated_at"] == "2026-07-09T19:42:15Z"
        # 修复缺陷:日期-only 的比赛不再被拼成 '2027-05-01T00:00:00Z' 伪精确
        assert draft_dateonly["status"] == "draft"
        assert draft_dateonly["kickoff_at_utc"] is None
        assert draft_dateonly["kickoff_precision"] == "date_only"
        assert "gold_wdl_predictions" in draft_dateonly["kickoff_source"]
        # 有精确 kickoff 的比赛按 exact 透传,来源承接 canonical 的真实来源(不覆盖)
        assert draft_exact["kickoff_at_utc"] == "2027-05-02T14:30:00Z"
        assert draft_exact["kickoff_precision"] == "exact"
        assert draft_exact["kickoff_source"] == "fotmob:fixtures"

        # 幂等
        stats2 = import_gold(platform, core)
        assert stats2["skipped"] == 3 and stats2["draft"] == 0
        core.close()
        platform.close()

    def test_legacy_cannot_become_official(self, platform):
        with pytest.raises(PredictionError) as ei:
            _register(platform, status="legacy_unverified", is_official=1)
        assert ei.value.reason == "bad_status"


class TestKickoffProvenanceIntegrity:
    """P0-A §6.1:精确开球时间来源(provenance)门禁——不再靠字符串形状。"""

    def _publishable(self, conn, sid):
        publish_snapshot(conn, sid, actor=None)
        lock_snapshot(conn, sid, actor=None)

    def test_date_only_precision_cannot_publish(self, platform):
        """1. 精度 date_only(即使 kickoff_at_utc 为空)不能 publish。"""
        sid = _register(platform, match_id=1, kickoff="2099-08-21",
                        kickoff_precision="date_only", kickoff_source="core:dim_match")
        with pytest.raises(PredictionError) as ei:
            publish_snapshot(platform, sid, actor=None)
        assert ei.value.reason in ("imprecise_kickoff",)

    def test_midnight_string_with_date_only_provenance_cannot_publish(self, platform):
        """2. `<date>T00:00:00Z` 但 provenance=date_only 不能 publish(核心:形状像精确也不行)。"""
        sid = _register(platform, match_id=2, kickoff="2099-08-21T00:00:00Z",
                        kickoff_precision="date_only", kickoff_source="legacy:date_only")
        with pytest.raises(PredictionError) as ei:
            publish_snapshot(platform, sid, actor=None)
        assert ei.value.reason == "imprecise_kickoff"

    def test_midnight_string_with_unknown_provenance_cannot_lock(self, platform):
        """3. 同样(unknown 精度)也不能 lock。"""
        sid = _register(platform, match_id=3, kickoff="2099-08-21T00:00:00Z",
                        kickoff_precision="unknown", kickoff_source=None)
        # 直接置 published(未锁定,触发器不拦),再验证 lock 拒绝
        platform.execute("UPDATE prediction_snapshots SET status='published', published_at=? WHERE id=?",
                         (_past(days=0), sid))
        with pytest.raises(PredictionError) as ei:
            lock_snapshot(platform, sid, actor=None)
        assert ei.value.reason in ("imprecise_kickoff", "no_kickoff_source")

    def test_invalid_format_with_T_cannot_publish(self, platform):
        """4. 含 'T' 但格式非法不能 publish。"""
        sid = _register(platform, match_id=4, kickoff="2099-13-99T99:99:99Z",
                        kickoff_precision="exact", kickoff_source="test")
        with pytest.raises(PredictionError) as ei:
            publish_snapshot(platform, sid, actor=None)
        assert ei.value.reason == "imprecise_kickoff"

    def test_naive_datetime_cannot_publish(self, platform):
        """5. 无时区的 datetime(naive)不能 publish。"""
        sid = _register(platform, match_id=5, kickoff="2099-08-21T14:00:00",
                        kickoff_precision="exact", kickoff_source="test")
        with pytest.raises(PredictionError) as ei:
            publish_snapshot(platform, sid, actor=None)
        assert ei.value.reason == "naive_kickoff"

    def test_exact_precision_missing_source_cannot_publish(self, platform):
        """6. precision=exact 但 kickoff_source 缺失不能 publish。"""
        sid = _register(platform, match_id=6, kickoff=_future(days=1),
                        kickoff_precision="exact", kickoff_source=None)
        with pytest.raises(PredictionError) as ei:
            publish_snapshot(platform, sid, actor=None)
        assert ei.value.reason == "no_kickoff_source"

    def test_real_exact_with_source_can_publish_and_lock(self, platform):
        """7. 真实 exact UTC kickoff + 合法来源可以 publish 且 lock。"""
        sid = _register(platform, match_id=7, kickoff=_future(days=1),
                        kickoff_precision="exact", kickoff_source="fotmob:fixtures")
        self._publishable(platform, sid)
        row = platform.execute(
            "SELECT status, is_official, kickoff_precision, kickoff_source"
            " FROM prediction_snapshots WHERE id=?", (sid,)).fetchone()
        assert row["status"] == "locked" and row["is_official"] == 1
        # provenance 已冻结在快照上
        assert row["kickoff_precision"] == "exact" and row["kickoff_source"] == "fotmob:fixtures"

    def test_post_kickoff_even_if_exact_cannot_publish(self, platform):
        """8. 已过 kickoff,即使 exact 也不能 publish/lock。"""
        sid = _register(platform, match_id=8, kickoff=_past(days=1),
                        kickoff_precision="exact", kickoff_source="fotmob:fixtures")
        with pytest.raises(PredictionError) as ei:
            publish_snapshot(platform, sid, actor=None)
        assert ei.value.reason == "post_kickoff"

    def test_bulk_publish_upcoming_fails_unqualified_per_record(self, data_dir, platform, client):
        """9. bulk publish-upcoming 对不合格记录逐条失败并返回明确 reason,不 official。"""
        from .coreseed import seed_basic_core
        seed_basic_core(data_dir)
        good = _register(platform, match_id=8801, kickoff=_future(days=2),
                         kickoff_precision="exact", kickoff_source="fotmob:fixtures")
        bad = _register(platform, match_id=8802, kickoff=_future(days=2),
                        kickoff_precision="date_only", kickoff_source="legacy:date_only")
        platform.commit()
        admin = _login_admin(client.app, "10.9.9.9")
        r = admin.post("/api/v1/admin/predictions/publish-upcoming",
                       json={"lock": True}, headers=_csrf(admin))
        assert r.status_code == 200
        body = r.json()
        assert body["published"] == 1
        assert any(f["id"] == bad and f["reason"] == "imprecise_kickoff" for f in body["failed"])
        # 不合格记录未 official
        row = platform.execute("SELECT is_official, status FROM prediction_snapshots WHERE id=?",
                               (bad,)).fetchone()
        assert row["is_official"] == 0 and row["status"] == "draft"
        assert platform.execute("SELECT is_official FROM prediction_snapshots WHERE id=?",
                               (good,)).fetchone()["is_official"] == 1

    def test_supersede_cannot_bypass_precision(self, platform):
        """10. supersede 不能绕过 kickoff 精度约束最终形成新的 official。

        register_snapshot 现在对 date_only/unknown 记录强制 kickoff_at_utc=NULL(§5.2),
        所以"date_only 但仍有可解析 kickoff"这类不一致状态不再能通过 register_snapshot
        产生——但历史遗留数据(repair 工具处理前、或本轮修复前的旧代码写入)仍可能是这种
        形状,直接用 raw SQL 构造,验证 supersede 面对这类既有数据同样不会洗白精度。
        """
        from backend.db.util import new_uuid, utc_now_iso
        old_id = new_uuid()
        now = utc_now_iso()
        platform.execute(
            """INSERT INTO prediction_snapshots
               (id, match_id, kickoff_at_utc, kickoff_precision, kickoff_source, model_version_id,
                generated_at, prediction_hash, home_win, draw, away_win, status, is_official, created_at)
               VALUES (?, 8901, ?, 'date_only', 'legacy:date_only', 'm-test', ?, 'h', 0.5, 0.3, 0.2,
                       'draft', 0, ?)""",
            (old_id, _future(days=2), now, now),
        )
        platform.commit()
        new = supersede_snapshot(platform, old_id, actor=None,
                                 home_win=0.4, draw=0.3, away_win=0.3, status="draft")
        # 新版继承了 date_only provenance(register_snapshot 强制其 kickoff_at_utc=NULL),
        # publish 被拒
        with pytest.raises(PredictionError) as ei:
            publish_snapshot(platform, new, actor=None)
        assert ei.value.reason == "imprecise_kickoff"
        new_row = platform.execute(
            "SELECT kickoff_at_utc FROM prediction_snapshots WHERE id=?", (new,)
        ).fetchone()
        assert new_row["kickoff_at_utc"] is None

    def test_precision_frozen_at_register(self, platform):
        """provenance 在 register 时冻结在快照行上,可事后审计。"""
        sid = _register(platform, match_id=8910, kickoff=_future(days=1),
                        kickoff_precision="exact", kickoff_source="fotmob:match_details")
        row = platform.execute(
            "SELECT kickoff_precision, kickoff_source FROM prediction_snapshots WHERE id=?",
            (sid,)).fetchone()
        assert row["kickoff_precision"] == "exact"
        assert row["kickoff_source"] == "fotmob:match_details"


def _insert_official(conn, match_id, probs, kickoff, published_offset_min=-60, status="locked"):
    """直接 SQL 造正式样本(kickoff 可在过去,用于结算/指标路径测试)。"""
    from backend.db.util import new_uuid

    pub = _iso(datetime.strptime(kickoff, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
               + timedelta(minutes=published_offset_min))
    sid = new_uuid()
    conn.execute(
        """INSERT INTO prediction_snapshots
           (id, match_id, kickoff_at_utc, kickoff_precision, kickoff_source,
            model_version_id, generated_at, published_at, locked_at,
            prediction_hash, home_win, draw, away_win, visibility, status, is_official, created_at)
           VALUES (?, ?, ?, 'exact', 'test:fixture', 'm-test', ?, ?, ?, 'h', ?, ?, ?, 'public', ?, 1, ?)""",
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
        assert result == {"total": 0, "retracted_count": 0, "superseded_count": 0, "samples": []}


class TestPermanentEligibility:
    """CLAUDE.md §9.1 永久资格不变量:official+曾锁定+赛前发布的快照永不退出
    公开列表、manifest 与评估分母;retract/supersede 只是标注。"""

    def _settle_match(self, platform, match_id, kickoff, score=(3, 1)):
        core = connect_rw("core")
        _seed_core_tables(core)
        core.execute(
            "INSERT INTO dim_match (Match_ID, Season, League_ID, Date, status, home_score, away_score)"
            " VALUES (?,'2025/2026',47,?,'Finish',?,?)",
            (match_id, kickoff[:10], score[0], score[1]),
        )
        core.commit()
        settled = settle_outcomes(platform, core)
        core.close()
        return settled

    def test_retract_after_settlement_keeps_public_total_and_eval_denominator(
        self, data_dir, platform
    ):
        kickoff = _past(days=2)
        sid = _insert_official(platform, 601, (0.7, 0.2, 0.1), kickoff)
        assert self._settle_match(platform, 601, kickoff) == 1
        before = official_samples(platform)
        ev_before = evaluation_samples(platform)
        assert before["total"] == 1 and len(ev_before) == 1

        retract_snapshot(platform, sid, actor=None, reason="赛后撤回(测试)")

        after = official_samples(platform)
        ev_after = evaluation_samples(platform)
        assert after["total"] == 1                       # 公开 total 不变
        assert after["retracted_count"] == 1             # 撤回只是透明标注
        assert ev_after == ev_before and len(ev_after) == 1   # 评估分母不变
        assert after["samples"][0]["status"] == "retracted"

    def test_superseded_old_sample_still_public_and_in_evaluation(self, data_dir, platform):
        kickoff = _past(days=2)
        old = _insert_official(platform, 611, (0.6, 0.25, 0.15), kickoff)
        assert self._settle_match(platform, 611, kickoff) == 1
        assert len(evaluation_samples(platform)) == 1
        # 开球后 supersede 被命令层拒绝,这里模拟"已存在的修正链"(superseded_by
        # 是赛前合法创建的):直接 SQL 设链,验证查询层不会因此剔除旧样本。
        other = _register(platform, match_id=611)        # draft,不是正式样本
        platform.execute(
            "UPDATE prediction_snapshots SET superseded_by=? WHERE id=?", (other, old)
        )

        result = official_samples(platform)
        assert result["total"] == 1                      # 旧样本仍公开
        assert result["superseded_count"] == 1
        assert result["samples"][0]["superseded_by"] == other
        assert len(evaluation_samples(platform)) == 1    # 仍进评估分母

    def test_supersede_after_kickoff_refused(self, data_dir, platform):
        old = _insert_official(platform, 621, (0.5, 0.3, 0.2), _past(days=1))
        with pytest.raises(PredictionError) as ei:
            supersede_snapshot(
                platform, old, actor=None,
                home_win=0.4, draw=0.35, away_win=0.25, status="draft",
            )
        assert ei.value.reason == "post_kickoff"
        row = platform.execute(
            "SELECT superseded_by FROM prediction_snapshots WHERE id=?", (old,)
        ).fetchone()
        assert row["superseded_by"] is None              # 链未被写入
        count = platform.execute(
            "SELECT COUNT(*) FROM prediction_snapshots WHERE match_id=621"
        ).fetchone()[0]
        assert count == 1                                # 没有偷偷追加新版本

    def test_correction_chain_old_and_new_both_listed(self, data_dir, platform):
        kickoff = _future(days=2)
        # 旧版:official+locked,published 在今天(kickoff-2 天)
        old = _insert_official(platform, 631, (0.55, 0.25, 0.2), kickoff,
                               published_offset_min=-2880)
        new = supersede_snapshot(
            platform, old, actor=None,
            home_win=0.45, draw=0.3, away_win=0.25, status="draft",
        )
        publish_snapshot(platform, new, actor=None)
        lock_snapshot(platform, new, actor=None)

        result = official_samples(platform)
        assert result["total"] == 2                      # 旧版新版同时可查
        by_id = {s["id"]: s for s in result["samples"]}
        assert by_id[old]["superseded_by"] == new
        assert by_id[new]["correction_of"] == old
        assert by_id[old]["home_win"] == 0.55            # 旧版内容原样保留
        assert by_id[new]["home_win"] == 0.45
        assert result["superseded_count"] == 1

        # 2026-08-05 起:锁定行的内容允许直接编辑(用户明确要求"随时可以更改"),
        # 但必须经 edit_snapshot 留痕,不是静默 UPDATE;物理删除仍然被拒绝(未变)。
        result = edit_snapshot(
            platform, old, actor=None, reason="真实数据修正",
            home_win=0.9, draw=0.05, away_win=0.05,
        )
        assert result["changed_fields"] == ["away_win", "draw", "home_win"]
        assert result["edit_count"] == 1
        row = platform.execute(
            "SELECT home_win, draw, away_win, edit_count FROM prediction_snapshots WHERE id=?",
            (old,),
        ).fetchone()
        assert (row["home_win"], row["draw"], row["away_win"]) == (0.9, 0.05, 0.05)
        assert row["edit_count"] == 1
        edit_row = platform.execute(
            "SELECT before_json, after_json, reason FROM prediction_snapshot_edits WHERE snapshot_id=?",
            (old,),
        ).fetchone()
        assert edit_row["reason"] == "真实数据修正"

        import sqlite3 as _sqlite3
        with pytest.raises(_sqlite3.IntegrityError, match="cannot be deleted"):
            platform.execute("DELETE FROM prediction_snapshots WHERE id=?", (old,))

    def test_manifest_covers_all_versions_of_correction_chain(self, data_dir, platform):
        import json as _json

        kickoff = _future(days=2)
        old = _insert_official(platform, 641, (0.5, 0.3, 0.2), kickoff,
                               published_offset_min=-2880)   # published ≈ 今天
        new = supersede_snapshot(
            platform, old, actor=None,
            home_win=0.42, draw=0.33, away_win=0.25, status="draft",
        )
        publish_snapshot(platform, new, actor=None)          # published_at = 今天
        lock_snapshot(platform, new, actor=None)

        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        r1 = build_daily_manifest(platform, date)
        row = platform.execute(
            "SELECT manifest_json FROM prediction_manifests WHERE manifest_date=? AND version=?",
            (date, r1["version"]),
        ).fetchone()
        ids = {e["id"] for e in _json.loads(row["manifest_json"])["entries"]}
        assert {old, new} <= ids                         # 修正链全部版本都在清单里

        # 赛后 retract 不改变 manifest 内容与 hash(实质字段不含 status)
        retract_snapshot(platform, old, actor=None, reason="测试")
        r2 = build_daily_manifest(platform, date)
        assert r2["changed"] is False and r2["manifest_hash"] == r1["manifest_hash"]

    def test_publish_lock_require_precise_kickoff(self, platform):
        # 日期-only kickoff(不含 'T')→ publish 拒绝
        sid = _register(platform, match_id=651, kickoff="2099-08-21")
        with pytest.raises(PredictionError) as ei:
            publish_snapshot(platform, sid, actor=None)
        assert ei.value.reason == "imprecise_kickoff"
        # 绕过 publish 直接置 published(未锁定,触发器不拦)→ lock 同样拒绝
        platform.execute(
            "UPDATE prediction_snapshots SET status='published', published_at=? WHERE id=?",
            (_past(days=0), sid),
        )
        with pytest.raises(PredictionError) as ei:
            lock_snapshot(platform, sid, actor=None)
        assert ei.value.reason == "imprecise_kickoff"
        # 精确 kickoff(含 'T')→ 放行
        ok = _register(platform, match_id=652, kickoff=_future(days=1))
        publish_snapshot(platform, ok, actor=None)
        lock_snapshot(platform, ok, actor=None)
        row = platform.execute(
            "SELECT status, is_official FROM prediction_snapshots WHERE id=?", (ok,)
        ).fetchone()
        assert row["status"] == "locked" and row["is_official"] == 1


class TestTrackRecordApiPermanence:
    """API 层:/api/v1/track-record 返回修正链新旧版本与撤回样本(不剔除)。"""

    def test_api_lists_chain_and_retracted(self, data_dir, platform, client):
        from .coreseed import seed_basic_core

        # platform 与 client 共享同一 data_dir(function-scoped fixture 复用)
        seed_basic_core(data_dir)
        kickoff = _future(days=2)
        old = _insert_official(platform, 9001, (0.55, 0.25, 0.2), kickoff,
                               published_offset_min=-2880)
        new = supersede_snapshot(
            platform, old, actor=None,
            home_win=0.45, draw=0.3, away_win=0.25, status="draft",
        )
        publish_snapshot(platform, new, actor=None)
        lock_snapshot(platform, new, actor=None)
        retract_snapshot(platform, new, actor=None, reason="测试撤回新版")

        body = client.get("/api/v1/track-record").json()
        assert body["total"] == 2
        assert body["retracted_count"] == 1
        assert body["superseded_count"] == 1
        by_snap = {s["snapshot_id"]: s for s in body["samples"]}
        assert set(by_snap) == {old, new}
        assert by_snap[old]["superseded_by"] == new
        assert by_snap[old]["superseded_note"] is not None
        assert by_snap[new]["correction_of"] == old
        assert by_snap[new]["status"] == "retracted"     # 撤回版仍在列表,透明标注
        assert by_snap[old]["home_probability"] == 0.55
        assert by_snap[new]["home_probability"] == 0.45


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


class TestUnifiedOfficialScope:
    """§9 manifest(11-15):track record / evaluation / daily manifest 三处对同一批样本
    的正式资格集合必须一致。同时构造合格与不合格记录,比较三个集合(不只证明"有记录")。

    构造(published 全部落在 2099-05-01,便于单一 manifest 对比):
      A(701)合法:locked pre-kickoff official           → 三者均含
      B(702)不合格:published 晚于 kickoff(开球后发布)→ 三者均无
      C(703)不合格:official 但未 locked(locked_at NULL)→ 三者均无
      D(704)合法但 retracted                            → 三者均含
      E(705)合法但 superseded                           → 三者均含
      F(706)合法且混合时区(kickoff '-05:00' / pub 'Z')→ 三者均含(julianday 正确)
    """

    def _ins(self, conn, mid, kickoff, published, *, locked=True, official=1,
             status="locked", superseded_by=None):
        from backend.db.util import new_uuid

        sid = new_uuid()
        conn.execute(
            """INSERT INTO prediction_snapshots
               (id, match_id, kickoff_at_utc, kickoff_precision, kickoff_source, model_version_id,
                generated_at, published_at, locked_at, prediction_hash, home_win, draw, away_win,
                visibility, status, is_official, superseded_by, created_at)
               VALUES (?, ?, ?, 'exact', 'test:fixture', 'm-test', ?, ?, ?, 'h', 0.5, 0.3, 0.2,
                       'public', ?, ?, ?, ?)""",
            (sid, mid, kickoff, published, published, (published if locked else None),
             status, official, superseded_by, published),
        )
        return sid

    def test_official_scope_consistent_across_track_eval_manifest(self, platform):
        import json as _json
        from backend.db.util import new_uuid

        PUB = "2099-05-01T12:00:00Z"
        FUT_KO = "2099-06-01T14:00:00Z"
        self._ins(platform, 701, FUT_KO, PUB)                                   # A 合法
        self._ins(platform, 702, "2099-05-01T10:00:00Z", PUB)                   # B 开球后发布
        self._ins(platform, 703, FUT_KO, PUB, locked=False)                     # C 未锁定
        self._ins(platform, 704, FUT_KO, PUB, status="retracted")              # D retracted
        target = new_uuid()                                                     # E 的修正目标(draft)
        platform.execute(
            """INSERT INTO prediction_snapshots
               (id, match_id, kickoff_at_utc, kickoff_precision, kickoff_source, model_version_id,
                generated_at, prediction_hash, home_win, draw, away_win, status, is_official, created_at)
               VALUES (?, 705, ?, 'exact', 'test:fixture', 'm-test', ?, 'h', 0.4, 0.3, 0.3,
                       'draft', 0, ?)""",
            (target, FUT_KO, PUB, PUB),
        )
        self._ins(platform, 705, FUT_KO, PUB, superseded_by=target)           # E superseded
        # F:混合时区——kickoff '-05:00'(=2099-05-02T01:00Z),published 'Z';
        # 裸文本比较会误判 published 不早于 kickoff('23' > '20'),julianday 才正确。
        self._ins(platform, 706, "2099-05-01T20:00:00-05:00", "2099-05-01T23:00:00Z")

        # 所有 official 都给结算结果(含不合格 B/C,以证明它们被资格判据而非缺结算排除)
        from backend.db.util import utc_now_iso

        now = utc_now_iso()
        for mid in (701, 702, 703, 704, 705, 706):
            platform.execute(
                "INSERT INTO prediction_outcomes (match_id, home_goals, away_goals, outcome, settled_at)"
                " VALUES (?, 2, 0, 'home', ?)", (mid, now),
            )
        platform.commit()

        qualified = {701, 704, 705, 706}

        # 1) track record
        tr = official_samples(platform)
        tr_ids = {s["match_id"] for s in tr["samples"]}
        assert tr_ids == qualified
        assert tr["total"] == 4
        assert tr["retracted_count"] == 1 and tr["superseded_count"] == 1

        # 2) evaluation(不含 match_id,比较计数:合格且已结算 = 4)
        ev = evaluation_samples(platform)
        assert len(ev) == 4

        # 3) manifest(2099-05-01)
        mf = build_daily_manifest(platform, "2099-05-01")
        row = platform.execute(
            "SELECT manifest_json FROM prediction_manifests WHERE manifest_date='2099-05-01'"
            " ORDER BY version DESC LIMIT 1"
        ).fetchone()
        mf_ids = {e["match_id"] for e in _json.loads(row["manifest_json"])["entries"]}
        assert mf["entries"] == 4
        assert mf_ids == qualified

        # 三集合一致(资格口径统一)
        assert tr_ids == mf_ids == qualified
        assert len(ev) == len(qualified)


class TestModelLeagueScope:
    """瑞典超接入新增:模型适用范围保护(CLAUDE.md §9)。

    校验必须是 opt-in——快照不显式冻结 league_id 时行为完全不变(历史/既有
    调用方零回归,TestLifecycle 等既有用例已经覆盖这一点);一旦显式提供
    league_id,模型必须在 applicable_league_ids 显式声明包含该联赛,否则
    publish/lock 一律拒绝,且拒绝原因是稳定的 'model_unvalidated_for_league'
    (供 admin/Studio 识别为统一错误码)。
    """

    def test_no_scope_declared_model_rejected_for_any_league(self, platform):
        """模型从未声明任何适用范围(如既有 m-test)→ 不隐式放行,禁止发布到 league 67。"""
        sid = _register(platform, league_id=67)
        with pytest.raises(PredictionError) as ei:
            publish_snapshot(platform, sid, actor=None)
        assert ei.value.reason == "model_unvalidated_for_league"

    def test_scoped_to_other_league_rejected(self, platform):
        """模型只声明适用 EPL(47)→ 禁止发布/锁定到瑞典超(67)。"""
        get_or_create_model_version(
            platform, "m-epl-only", "dixon-coles", applicable_league_ids=[47]
        )
        sid = register_snapshot(
            platform, match_id=2, kickoff_at_utc=_future(), kickoff_precision="exact",
            kickoff_source="test:fixture", model_version_id="m-epl-only",
            home_win=0.5, draw=0.3, away_win=0.2, league_id=67,
        )
        with pytest.raises(PredictionError) as ei:
            publish_snapshot(platform, sid, actor=None)
        assert ei.value.reason == "model_unvalidated_for_league"

    def test_scoped_including_target_league_can_publish_and_lock(self, platform):
        """模型显式声明适用瑞典超(67)→ 正常走完 publish/lock。"""
        get_or_create_model_version(
            platform, "m-allsvenskan", "dixon-coles", applicable_league_ids=[47, 67]
        )
        sid = register_snapshot(
            platform, match_id=3, kickoff_at_utc=_future(), kickoff_precision="exact",
            kickoff_source="test:fixture", model_version_id="m-allsvenskan",
            home_win=0.5, draw=0.3, away_win=0.2, league_id=67,
        )
        publish_snapshot(platform, sid, actor=None)
        lock_snapshot(platform, sid, actor=None)
        row = platform.execute("SELECT status, league_id FROM prediction_snapshots WHERE id=?", (sid,)).fetchone()
        assert row["status"] == "locked" and row["league_id"] == 67

    def test_no_league_id_unaffected_by_scope_check(self, platform):
        """不显式传 league_id(默认 None)→ 即便模型只声明了 EPL,也不触发校验
        (向后兼容:既有/历史调用方从不传 league_id,行为必须与本次改动前完全一致)。"""
        get_or_create_model_version(
            platform, "m-epl-only-2", "dixon-coles", applicable_league_ids=[47]
        )
        sid = register_snapshot(
            platform, match_id=4, kickoff_at_utc=_future(), kickoff_precision="exact",
            kickoff_source="test:fixture", model_version_id="m-epl-only-2",
            home_win=0.5, draw=0.3, away_win=0.2,
        )
        publish_snapshot(platform, sid, actor=None)
        lock_snapshot(platform, sid, actor=None)
        row = platform.execute("SELECT status, league_id FROM prediction_snapshots WHERE id=?", (sid,)).fetchone()
        assert row["status"] == "locked" and row["league_id"] is None

    def test_lock_also_enforced_independent_of_publish(self, platform):
        """防御性纵深:直接构造一条已 published 但联赛越界的行(绕过 publish_snapshot
        的校验路径),证明 lock_snapshot 自己也拦截,不依赖 publish 已经拦过一次。"""
        from backend.db.util import new_uuid

        get_or_create_model_version(
            platform, "m-epl-only-3", "dixon-coles", applicable_league_ids=[47]
        )
        sid = new_uuid()
        platform.execute(
            """INSERT INTO prediction_snapshots
               (id, match_id, kickoff_at_utc, kickoff_precision, kickoff_source, league_id,
                model_version_id, generated_at, published_at, prediction_hash,
                home_win, draw, away_win, visibility, status, is_official, created_at)
               VALUES (?, 5, ?, 'exact', 'test:fixture', 67, 'm-epl-only-3', ?, ?, 'h',
                       0.5, 0.3, 0.2, 'public', 'published', 0, ?)""",
            (sid, _future(), _future(), _future(), _future()),
        )
        platform.commit()
        with pytest.raises(PredictionError) as ei:
            lock_snapshot(platform, sid, actor=None)
        assert ei.value.reason == "model_unvalidated_for_league"

    def test_supersede_inherits_league_id_and_enforcement_still_applies(self, platform):
        """supersede 继承 league_id;新版本若指向不适用模型,仍必须在 publish 时被拒绝
        (不能借"修正"绕过联赛范围保护)。"""
        get_or_create_model_version(
            platform, "m-allsvenskan-2", "dixon-coles", applicable_league_ids=[67]
        )
        get_or_create_model_version(
            platform, "m-epl-only-4", "dixon-coles", applicable_league_ids=[47]
        )
        old = register_snapshot(
            platform, match_id=6, kickoff_at_utc=_future(), kickoff_precision="exact",
            kickoff_source="test:fixture", model_version_id="m-allsvenskan-2",
            home_win=0.5, draw=0.3, away_win=0.2, league_id=67,
        )
        publish_snapshot(platform, old, actor=None)
        lock_snapshot(platform, old, actor=None)
        new = supersede_snapshot(
            platform, old, actor=None,
            home_win=0.4, draw=0.35, away_win=0.25, status="draft",
            model_version_id="m-epl-only-4",
        )
        row = platform.execute("SELECT league_id FROM prediction_snapshots WHERE id=?", (new,)).fetchone()
        assert row["league_id"] == 67
        with pytest.raises(PredictionError) as ei:
            publish_snapshot(platform, new, actor=None)
        assert ei.value.reason == "model_unvalidated_for_league"
