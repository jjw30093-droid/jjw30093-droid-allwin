"""P0.7 测试:NowGoal 快照管线(hash-diff 落库 / 实体解析 / moves / 共现 / admin xref)。

core(allwin.db)在生产是只读 Bronze;这里在临时数据目录里造一个最小假 core
(dim_match / dim_team_i18n 子集,列名与真库一致)供实体解析读取。
fixture 数据按旧项目代码审计的格式构造,真实端点未验证(UNVERIFIED)。
"""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.db.connections import connect_rw
from backend.ingest.entity_resolution import resolve_match, seed_team_aliases
from backend.ingest.odds_snapshots import (
    ingest_lineup_snapshot,
    ingest_odds_records,
    ingest_sideline_snapshot,
    record_source_health,
)
from backend.providers import nowgoal
from backend.silver.odds_moves import build_cooccurrence, build_event_moves, build_odds_moves

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "nowgoal"

ORIGIN = {"Origin": "http://localhost:3000"}

QUERY_DATE = (date.today() + timedelta(days=7)).isoformat()
NEXT_DATE = (date.today() + timedelta(days=8)).isoformat()

# (Match_ID, Date, home_id, home_name, away_id, away_name)
CORE_MATCHES = [
    (9001, QUERY_DATE, 9825, "Arsenal", 8455, "Chelsea"),
    (9002, QUERY_DATE, 8456, "Manchester City", 8650, "Liverpool"),
    (9003, NEXT_DATE, 8668, "Everton", 8654, "West Ham United"),
    # 同队重复对阵(±1 天内两场)→ 制造映射歧义
    (9004, QUERY_DATE, 10261, "Newcastle United", 9879, "Fulham"),
    (9005, NEXT_DATE, 10261, "Newcastle United", 9879, "Fulham"),
]

CORE_I18N = [
    (9825, "Arsenal", "阿森纳"),
    (8455, "Chelsea", "切尔西"),
    (8456, "Manchester City", "曼城"),
    (8650, "Liverpool", "利物浦"),
]


@pytest.fixture
def core_db(data_dir):
    """在临时 core.db 里建最小 Bronze 子集(仅测试;生产 core 只读)。"""
    conn = connect_rw("core")
    try:
        conn.execute(
            """CREATE TABLE dim_match (
                   "Match_ID" INTEGER PRIMARY KEY, "Season" TEXT, "League_ID" INTEGER,
                   "Date" TEXT, "Home_Team_ID" INTEGER, "Away_Team_ID" INTEGER,
                   "Home_Team_Name" TEXT, "Away_Team_Name" TEXT, "status" TEXT)"""
        )
        conn.execute(
            """CREATE TABLE dim_team_i18n (
                   "Team_ID" INTEGER PRIMARY KEY, "name_en" TEXT, "name_zh" TEXT,
                   "source" TEXT, "updated_at" TEXT)"""
        )
        for mid, d, hid, hname, aid, aname in CORE_MATCHES:
            conn.execute(
                "INSERT INTO dim_match VALUES (?, '2026/2027', 47, ?, ?, ?, ?, ?, 'NotStarted')",
                (mid, d, hid, aid, hname, aname),
            )
        for tid, en, zh in CORE_I18N:
            conn.execute(
                "INSERT INTO dim_team_i18n VALUES (?, ?, ?, 'test', '2026-07-19')",
                (tid, en, zh),
            )
        conn.commit()
        yield conn
    finally:
        conn.close()


@pytest.fixture
def odds_conn(data_dir):
    conn = connect_rw("odds")
    try:
        yield conn
    finally:
        conn.close()


def _odds_records():
    payload = json.loads((FIXTURES / "odds_sample.json").read_text(encoding="utf-8"))
    return [nowgoal.normalize_for_inversion(r, False) for r in nowgoal.parse_odds(payload)]


# ── hash-diff:payload 不变不重复落库 ────────────────────────────────────


class TestHashDiff:
    def test_same_payload_ingested_once(self, odds_conn):
        records = _odds_records()
        r1 = ingest_odds_records(odds_conn, "555", records, "2026-07-19T10:00:00Z", "run1", "pre_match")
        assert r1 == {"inserted": 4, "skipped": 0}
        r2 = ingest_odds_records(odds_conn, "555", records, "2026-07-19T10:15:00Z", "run2", "pre_match")
        assert r2 == {"inserted": 0, "skipped": 4}
        n = odds_conn.execute("SELECT COUNT(*) FROM bronze_ng_odds_snap").fetchone()[0]
        assert n == 4

    def test_changed_payload_appends_new_snapshot(self, odds_conn):
        records = _odds_records()
        ingest_odds_records(odds_conn, "555", records, "2026-07-19T10:00:00Z", "run1", "pre_match")
        changed = [dict(r) for r in records]
        target = next(r for r in changed if r["company_id"] == "8" and r["market"] == "1x2")
        target["latest"] = dict(target["latest"]) | {"home": 1.95}
        r2 = ingest_odds_records(odds_conn, "555", changed, "2026-07-19T10:15:00Z", "run2", "pre_match")
        assert r2 == {"inserted": 1, "skipped": 3}
        rows = odds_conn.execute(
            "SELECT observed_at, source_updated_at, poll_run_id FROM bronze_ng_odds_snap"
            " WHERE market='1x2' AND company_id='8' ORDER BY observed_at"
        ).fetchall()
        assert len(rows) == 2
        # 时间戳纪律:NowGoal 不声明来源更新时间 → source_updated_at 恒 NULL
        assert all(r["source_updated_at"] is None for r in rows)
        assert rows[1]["poll_run_id"] == "run2"

    def test_lineup_and_sideline_hash_diff(self, odds_conn):
        snap = {"home": {"team_id": 1, "formation": "4-3-3", "starters": [], "subs": []},
                "away": {"team_id": 2, "formation": "4-4-2", "starters": [], "subs": []}}
        assert ingest_lineup_snapshot(odds_conn, 9001, snap, "2026-07-19T10:00:00Z", "r1")["inserted"] == 1
        assert ingest_lineup_snapshot(odds_conn, 9001, snap, "2026-07-19T10:05:00Z", "r2")["skipped"] == 1
        changed = json.loads(json.dumps(snap))
        changed["home"]["formation"] = "4-2-3-1"
        assert ingest_lineup_snapshot(odds_conn, 9001, changed, "2026-07-19T10:10:00Z", "r3")["inserted"] == 1

        side = {"team_id": 1, "sidelined": []}
        assert ingest_sideline_snapshot(odds_conn, 9001, 1, side, "2026-07-19T10:00:00Z", "r1")["inserted"] == 1
        assert ingest_sideline_snapshot(odds_conn, 9001, 1, side, "2026-07-19T10:05:00Z", "r2")["skipped"] == 1
        # 另一队独立序列,不互相干扰
        assert ingest_sideline_snapshot(odds_conn, 9001, 2, side | {"team_id": 2},
                                        "2026-07-19T10:05:00Z", "r2")["inserted"] == 1


# ── 实体解析 ─────────────────────────────────────────────────────────────


class TestEntityResolution:
    def test_seed_aliases_idempotent(self, odds_conn, core_db):
        added = seed_team_aliases(odds_conn, core_db)
        assert added > 0
        assert seed_team_aliases(odds_conn, core_db) == 0
        row = odds_conn.execute(
            "SELECT canonical_team_id FROM dim_team_alias WHERE alias='阿森纳'"
        ).fetchone()
        assert row[0] == 9825

    def test_exact_match_auto_ok(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        result = resolve_match(odds_conn, core_db, {
            "titan_id": "700001", "home_name": "Arsenal", "away_name": "Chelsea",
            "date": QUERY_DATE,
        })
        assert result["resolved"] and result["created"]
        assert result["fotmob_match_id"] == 9001
        assert result["confidence"] == 1.0
        assert result["home_away_inverted"] == 0
        assert result["review_status"] == "auto_ok"
        # 绝不静默 verified=1
        row = odds_conn.execute("SELECT verified, method FROM dim_match_xref WHERE id=?",
                                (result["xref_id"],)).fetchone()
        assert row["verified"] == 0 and row["method"] == "auto"

    def test_chinese_alias_and_inverted_direction(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        # NowGoal 主客与 FotMob 相反:利物浦(客)列为主
        result = resolve_match(odds_conn, core_db, {
            "titan_id": "700002", "home_name": "利物浦", "away_name": "曼城",
            "date": QUERY_DATE,
        })
        assert result["fotmob_match_id"] == 9002
        assert result["home_away_inverted"] == 1
        assert result["review_status"] == "auto_ok"

    def test_partial_match_needs_review(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        result = resolve_match(odds_conn, core_db, {
            "titan_id": "700003", "home_name": "Everton", "away_name": "Unknown Town",
            "date": NEXT_DATE,
        })
        assert result["fotmob_match_id"] == 9003
        assert result["confidence"] == 0.5
        assert result["review_status"] == "needs_review"

    def test_ambiguous_candidates_needs_review(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        # ±1 天内两场 Newcastle vs Fulham → 两个候选同分 → 不自动通过
        result = resolve_match(odds_conn, core_db, {
            "titan_id": "700004", "home_name": "Newcastle United", "away_name": "Fulham",
            "date": QUERY_DATE,
        })
        assert result["confidence"] == 1.0
        assert result["review_status"] == "needs_review"

    def test_no_match_writes_nothing(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        result = resolve_match(odds_conn, core_db, {
            "titan_id": "700005", "home_name": "Real Madrid", "away_name": "Barcelona",
            "date": QUERY_DATE,
        })
        assert result["resolved"] is False
        n = odds_conn.execute(
            "SELECT COUNT(*) FROM dim_match_xref WHERE provider_match_id='700005'"
        ).fetchone()[0]
        assert n == 0

    def test_existing_xref_returned_not_duplicated(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        row = {"titan_id": "700006", "home_name": "Arsenal", "away_name": "Chelsea",
               "date": QUERY_DATE}
        first = resolve_match(odds_conn, core_db, row)
        again = resolve_match(odds_conn, core_db, row)
        assert again["created"] is False
        assert again["xref_id"] == first["xref_id"]


# ── moves + 共现 ─────────────────────────────────────────────────────────


def _insert_xref(conn, titan_id, fotmob_match_id, status="auto_ok"):
    conn.execute(
        """INSERT INTO dim_match_xref
           (fotmob_match_id, provider, provider_match_id, home_away_inverted, confidence,
            verified, method, kickoff_diff_seconds, review_status, created_at, updated_at)
           VALUES (?, 'nowgoal', ?, 0, 1.0, 0, 'auto', NULL, ?, '2026-07-19T00:00:00Z',
                   '2026-07-19T00:00:00Z')""",
        (fotmob_match_id, titan_id, status),
    )
    conn.commit()


class TestMovesAndCooccurrence:
    T0, T1, T2, T3 = ("2026-07-19T10:00:00Z", "2026-07-19T10:30:00Z",
                      "2026-07-19T10:40:00Z", "2026-07-19T11:30:00Z")

    def _seed_snapshots(self, conn):
        _insert_xref(conn, "555", 9001)
        records = _odds_records()
        ingest_odds_records(conn, "555", records, self.T0, "r1", "pre_match")
        changed = [dict(r) for r in records]
        target = next(r for r in changed if r["company_id"] == "8" and r["market"] == "1x2")
        target["latest"] = dict(target["latest"]) | {"home": 1.95}  # 只动 1x2 的 home
        ingest_odds_records(conn, "555", changed, self.T1, "r2", "pre_match")

        lineup_a = {"home": {"team_id": 9825, "formation": "4-3-3",
                             "starters": [{"id": 10, "name": "A"}], "subs": []},
                    "away": {"team_id": 8455, "formation": "4-4-2", "starters": [], "subs": []}}
        lineup_b = json.loads(json.dumps(lineup_a))
        lineup_b["home"]["formation"] = "4-2-3-1"
        lineup_b["home"]["starters"] = [{"id": 20, "name": "B"}]
        lineup_c = json.loads(json.dumps(lineup_b))
        lineup_c["away"]["formation"] = "3-5-2"
        ingest_lineup_snapshot(conn, 9001, lineup_a, self.T0, "r1")
        ingest_lineup_snapshot(conn, 9001, lineup_b, self.T2, "r2")   # 距赔率变化 600s → 窗口内
        ingest_lineup_snapshot(conn, 9001, lineup_c, self.T3, "r3")   # 距赔率变化 3600s → 窗口外

    def test_odds_moves_field_level_diff(self, odds_conn):
        self._seed_snapshots(odds_conn)
        inserted = build_odds_moves(odds_conn)
        assert inserted == 1   # 只有 1x2.home 变了
        move = odds_conn.execute("SELECT * FROM silver_odds_moves").fetchone()
        assert move["fotmob_match_id"] == 9001
        assert move["market"] == "1x2" and move["field"] == "home"
        assert move["prev_value"] == "2.05" and move["new_value"] == "1.95"
        assert move["moved_at"] == self.T1
        # 幂等
        assert build_odds_moves(odds_conn) == 0

    def test_unmapped_series_produces_no_moves(self, odds_conn):
        records = _odds_records()
        ingest_odds_records(odds_conn, "999", records, self.T0, "r1", "pre_match")
        changed = [dict(r) for r in records]
        changed[0]["latest"] = dict(changed[0]["latest"]) | {"home": 1.5}
        ingest_odds_records(odds_conn, "999", changed, self.T1, "r2", "pre_match")
        assert build_odds_moves(odds_conn) == 0

    def test_event_moves_with_detail(self, odds_conn):
        self._seed_snapshots(odds_conn)
        side_a = {"team_id": 9825, "sidelined": []}
        side_b = {"team_id": 9825,
                  "sidelined": [{"id": 90, "name": "Injured One", "reason": "injury",
                                 "expected_return": None}]}
        ingest_sideline_snapshot(odds_conn, 9001, 9825, side_a, self.T0, "r1")
        ingest_sideline_snapshot(odds_conn, 9001, 9825, side_b, self.T2, "r2")

        inserted = build_event_moves(odds_conn)
        assert inserted == 3   # lineup 两次变化 + sideline 一次
        lineup_move = odds_conn.execute(
            "SELECT * FROM silver_event_moves WHERE event_type='lineup_change'"
            " ORDER BY moved_at LIMIT 1"
        ).fetchone()
        detail = json.loads(lineup_move["detail_json"])
        assert detail["home"]["formation"] == {"prev": "4-3-3", "new": "4-2-3-1"}
        assert detail["home"]["starters_added"][0]["name"] == "B"
        side_move = odds_conn.execute(
            "SELECT * FROM silver_event_moves WHERE event_type='sideline_change'"
        ).fetchone()
        assert json.loads(side_move["detail_json"])["sidelined_added"][0]["name"] == "Injured One"
        # 幂等
        assert build_event_moves(odds_conn) == 0

    def test_cooccurrence_window(self, odds_conn):
        self._seed_snapshots(odds_conn)
        build_odds_moves(odds_conn)
        build_event_moves(odds_conn)
        inserted = build_cooccurrence(odds_conn, window_seconds=900)
        # 赔率变化 10:30;lineup 变化 10:40(600s,窗口内)与 11:30(3600s,窗口外)
        assert inserted == 1
        row = odds_conn.execute("SELECT * FROM gold_move_cooccurrence").fetchone()
        assert row["fotmob_match_id"] == 9001
        assert row["delta_seconds"] == -600
        assert row["window_seconds"] == 900
        # 幂等
        assert build_cooccurrence(odds_conn, window_seconds=900) == 0


# ── source_health ────────────────────────────────────────────────────────


class TestSourceHealth:
    def test_append_only(self, odds_conn):
        record_source_health(odds_conn, "nowgoal", ok=True, latency_ms=120)
        record_source_health(odds_conn, "nowgoal", ok=False, error_summary="WAF 拦截")
        rows = odds_conn.execute(
            "SELECT ok, error_summary FROM source_health WHERE source='nowgoal' ORDER BY id"
        ).fetchall()
        assert [r["ok"] for r in rows] == [1, 0]
        assert rows[0]["error_summary"] is None   # 失败不覆盖成功记录


# ── CLI 离线单轮采集(端到端) ────────────────────────────────────────────


class TestPollCliOffline:
    def test_offline_poll_end_to_end(self, odds_conn, core_db, capsys):
        from backend.cli.poll_nowgoal import main

        rc = main(["--date", QUERY_DATE, "--offline-fixture",
                   str(FIXTURES / "poll_fixture.json")])
        assert rc == 0
        out = capsys.readouterr().out
        assert "日程行: 3" in out

        xrefs = {r["provider_match_id"]: r for r in odds_conn.execute(
            "SELECT * FROM dim_match_xref").fetchall()}
        assert xrefs["3001001"]["review_status"] == "auto_ok"
        assert xrefs["3001001"]["home_away_inverted"] == 0
        assert xrefs["3001002"]["review_status"] == "auto_ok"
        assert xrefs["3001002"]["home_away_inverted"] == 1   # Liverpool 列主 → 反转
        assert xrefs["3001003"]["review_status"] == "needs_review"

        # needs_review 不抓赔率;已映射两场各 2 市场
        snaps = odds_conn.execute(
            "SELECT provider_match_id, market, market_phase, payload_json"
            " FROM bronze_ng_odds_snap ORDER BY provider_match_id, market"
        ).fetchall()
        assert {s["provider_match_id"] for s in snaps} == {"3001001", "3001002"}
        assert all(s["market_phase"] == "pre_match" for s in snaps)

        # 反转归一:3001002 的 AH 双边交换 + 线取负(fixture 原始 line=0.25)
        ah = next(s for s in snaps
                  if s["provider_match_id"] == "3001002" and s["market"] == "ah")
        payload = json.loads(ah["payload_json"])
        assert payload["initial"] == {"home": 0.90, "line": -0.25, "away": 0.98}

        health = odds_conn.execute(
            "SELECT source, ok FROM source_health ORDER BY id").fetchall()
        assert ("nowgoal", 1) in [(h["source"], h["ok"]) for h in health]

        # 第二轮同 fixture → 全部 hash 未变跳过,不重复落库
        rc2 = main(["--date", QUERY_DATE, "--offline-fixture",
                    str(FIXTURES / "poll_fixture.json")])
        assert rc2 == 0
        n = odds_conn.execute("SELECT COUNT(*) FROM bronze_ng_odds_snap").fetchone()[0]
        assert n == len(snaps)


# ── admin xref 审核端点 ──────────────────────────────────────────────────


def _login_user(client, ip):
    r1 = client.get("/api/v1/auth/wechat/oa/start?next=/", follow_redirects=False,
                    headers={"x-real-ip": ip})
    client.get(r1.headers["location"], follow_redirects=False)
    assert client.get("/api/v1/me").json()["authenticated"]


def _login_admin(app, ip):
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


class TestAdminXrefEndpoints:
    def _seed_xrefs(self, data_dir):
        conn = connect_rw("odds")
        try:
            _insert_xref(conn, "800001", 9001, status="needs_review")
            _insert_xref(conn, "800002", 9002, status="auto_ok")
            _insert_xref(conn, "800003", 9003, status="confirmed")
        finally:
            conn.close()

    def test_list_pending_includes_needs_review(self, app, data_dir, fresh_ip):
        self._seed_xrefs(data_dir)
        admin = _login_admin(app, fresh_ip)
        r = admin.get("/api/v1/admin/xref")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "private, no-store"
        body = r.json()
        statuses = {x["provider_match_id"]: x["review_status"] for x in body["xrefs"]}
        assert statuses == {"800001": "needs_review", "800002": "auto_ok"}
        assert body["counts"]["needs_review"] == 1
        # 精确过滤
        only = admin.get("/api/v1/admin/xref?status=confirmed").json()["xrefs"]
        assert [x["provider_match_id"] for x in only] == ["800003"]

    def test_confirm_and_reject_with_audit(self, app, data_dir, fresh_ip):
        self._seed_xrefs(data_dir)
        admin = _login_admin(app, fresh_ip)
        pending = admin.get("/api/v1/admin/xref").json()["xrefs"]
        target = next(x for x in pending if x["provider_match_id"] == "800001")

        r = admin.post(f"/api/v1/admin/xref/{target['id']}/confirm", headers=_csrf(admin))
        assert r.status_code == 200
        conn = connect_rw("odds")
        try:
            row = conn.execute("SELECT * FROM dim_match_xref WHERE id=?", (target["id"],)).fetchone()
        finally:
            conn.close()
        assert row["review_status"] == "confirmed"
        assert row["verified"] == 1 and row["method"] == "manual"

        other = next(x for x in pending if x["provider_match_id"] == "800002")
        r2 = admin.post(f"/api/v1/admin/xref/{other['id']}/reject", headers=_csrf(admin))
        assert r2.status_code == 200

        logs = admin.get("/api/v1/admin/audit-logs").json()["logs"]
        actions = [l["action"] for l in logs]
        assert "xref.confirm" in actions and "xref.reject" in actions
        confirm_log = next(l for l in logs if l["action"] == "xref.confirm")
        assert json.loads(confirm_log["detail_json"])["provider_match_id"] == "800001"

        r404 = admin.post("/api/v1/admin/xref/99999/confirm", headers=_csrf(admin))
        assert r404.status_code == 404

    def test_permissions(self, app, client, data_dir, fresh_ip):
        self._seed_xrefs(data_dir)
        _login_user(client, fresh_ip)   # 普通用户
        assert client.get("/api/v1/admin/xref").status_code == 403
        assert client.post("/api/v1/admin/xref/1/confirm",
                           headers=_csrf(client)).status_code == 403
        anon = TestClient(app)
        assert anon.get("/api/v1/admin/xref").status_code == 401
        # 管理员但无 CSRF/Origin → 403
        admin = _login_admin(app, fresh_ip)
        assert admin.post("/api/v1/admin/xref/1/confirm").status_code == 403
