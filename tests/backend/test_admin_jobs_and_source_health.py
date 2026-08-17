"""P1.5:GET /api/v1/admin/jobs 与 GET /api/v1/admin/source-health(只读)。

覆盖:
- 门禁:匿名 401 / 非 admin 403(不是 404——证明真的做了 require_admin)。
- admin 200,响应体含 "jobs" / "source_health" 键,是列表。
- Cache-Control: private, no-store。
- limit/offset 分页正确(插入超过一页的假行)。
- error_summary 脱敏——直接复用 backend.cli.ops_check._sanitize_summary,
  不重新实现脱敏逻辑,断言产出与该函数一致。

job_runs / source_health 列名对齐 backend/migrations/platform 与
backend/migrations/odds 的真实 schema(见任务审计,已用 sqlite3 .schema 核对),
不是凭记忆猜测。
"""

from fastapi.testclient import TestClient

from backend.cli.ops_check import _sanitize_summary
from backend.db.connections import connect_rw, tx
from backend.db.util import new_uuid

from .authflow import wechat_scan_login

ORIGIN = {"Origin": "http://localhost:3000"}


def _login_admin(app, ip, username="ops-admin"):
    from backend.cli.create_admin import create_admin

    conn = connect_rw("platform")
    try:
        create_admin(conn, username, "admin-pass-123", reset=True)
    finally:
        conn.close()
    admin = TestClient(app)
    r = admin.post(
        "/api/v1/auth/password/login",
        json={"username": username, "password": "admin-pass-123"},
        headers={"x-real-ip": ip},
    )
    assert r.status_code == 200
    return admin


def _insert_job_run(
    created_at,
    id_=None,
    job_name="fotmob_incremental",
    status="succeeded",
    attempt=1,
    max_attempts=1,
    input_count=10,
    output_count=8,
    error_summary=None,
):
    conn = connect_rw("platform")
    try:
        with tx(conn):
            conn.execute(
                """INSERT INTO job_runs
                   (id, job_name, idempotency_key, status, attempt, max_attempts,
                    started_at, finished_at, input_count, output_count,
                    error_summary, meta_json, created_at)
                   VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?)""",
                (
                    id_ or new_uuid(),
                    job_name,
                    status,
                    attempt,
                    max_attempts,
                    created_at,
                    created_at,
                    input_count,
                    output_count,
                    error_summary,
                    created_at,
                ),
            )
    finally:
        conn.close()


def _insert_source_health(
    checked_at,
    source="nowgoal",
    ok=1,
    latency_ms=120,
    error_summary=None,
):
    conn = connect_rw("odds")
    try:
        with tx(conn):
            conn.execute(
                """INSERT INTO source_health (source, checked_at, ok, latency_ms, error_summary, meta_json)
                   VALUES (?, ?, ?, ?, ?, '{}')""",
                (source, checked_at, ok, latency_ms, error_summary),
            )
    finally:
        conn.close()


# ── 门禁 ─────────────────────────────────────────────────────────────

class TestJobsGate:
    def test_anonymous_401(self, app, data_dir):
        anon = TestClient(app)
        r = anon.get("/api/v1/admin/jobs")
        assert r.status_code == 401

    def test_non_admin_403(self, app, data_dir, fresh_ip):
        member = TestClient(app)
        wechat_scan_login(member, ip=fresh_ip)
        r = member.get("/api/v1/admin/jobs")
        assert r.status_code == 403


class TestSourceHealthGate:
    def test_anonymous_401(self, app, data_dir):
        anon = TestClient(app)
        r = anon.get("/api/v1/admin/source-health")
        assert r.status_code == 401

    def test_non_admin_403(self, app, data_dir, fresh_ip):
        member = TestClient(app)
        wechat_scan_login(member, ip=fresh_ip)
        r = member.get("/api/v1/admin/source-health")
        assert r.status_code == 403


# ── /admin/jobs ──────────────────────────────────────────────────────

class TestJobsEndpoint:
    def test_admin_sees_jobs_list_no_store(self, app, data_dir, fresh_ip):
        _insert_job_run("2026-08-10T00:00:00Z", id_="job-solo")
        admin = _login_admin(app, fresh_ip)
        r = admin.get("/api/v1/admin/jobs?limit=100")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "private, no-store"
        body = r.json()
        assert "jobs" in body
        assert isinstance(body["jobs"], list)
        assert len(body["jobs"]) == 1

        job = body["jobs"][0]
        assert job["id"] == "job-solo"
        assert job["job_name"] == "fotmob_incremental"
        assert job["status"] == "succeeded"
        assert job["attempt"] == 1
        assert job["max_attempts"] == 1
        assert job["input_count"] == 10
        assert job["output_count"] == 8
        assert job["started_at"] == "2026-08-10T00:00:00Z"
        assert job["finished_at"] == "2026-08-10T00:00:00Z"
        assert job["created_at"] == "2026-08-10T00:00:00Z"
        assert job["error_summary"] is None

    def test_pagination(self, app, data_dir, fresh_ip):
        _insert_job_run("2026-08-01T00:00:00Z", id_="job-1")
        _insert_job_run("2026-08-02T00:00:00Z", id_="job-2")
        _insert_job_run("2026-08-03T00:00:00Z", id_="job-3")
        admin = _login_admin(app, fresh_ip)

        page1 = admin.get("/api/v1/admin/jobs?limit=2&offset=0")
        page2 = admin.get("/api/v1/admin/jobs?limit=2&offset=2")
        assert page1.status_code == 200 and page2.status_code == 200

        ids1 = [j["id"] for j in page1.json()["jobs"]]
        ids2 = [j["id"] for j in page2.json()["jobs"]]
        assert ids1 == ["job-3", "job-2"]  # created_at DESC,最新在前
        assert ids2 == ["job-1"]
        assert set(ids1) & set(ids2) == set()  # 两页不重叠

    def test_error_summary_sanitized(self, app, data_dir, fresh_ip):
        raw = "SELECT * FROM users WHERE password='hunter2'"
        _insert_job_run(
            "2026-08-05T00:00:00Z", id_="job-bad", status="failed", error_summary=raw
        )
        admin = _login_admin(app, fresh_ip)
        r = admin.get("/api/v1/admin/jobs")
        job = next(j for j in r.json()["jobs"] if j["id"] == "job-bad")
        expected = _sanitize_summary(raw)
        assert expected == "[SQL_REDACTED]"
        assert job["error_summary"] == expected
        assert "SELECT" not in job["error_summary"]
        assert "hunter2" not in job["error_summary"]


# ── /admin/source-health ─────────────────────────────────────────────

class TestSourceHealthEndpoint:
    def test_admin_sees_source_health_list_no_store(self, app, data_dir, fresh_ip):
        _insert_source_health("2026-08-10T00:00:00Z", source="nowgoal", ok=1, latency_ms=250)
        admin = _login_admin(app, fresh_ip)
        r = admin.get("/api/v1/admin/source-health?limit=100")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "private, no-store"
        body = r.json()
        assert "source_health" in body
        assert isinstance(body["source_health"], list)
        assert len(body["source_health"]) == 1

        entry = body["source_health"][0]
        assert entry["source"] == "nowgoal"
        assert entry["checked_at"] == "2026-08-10T00:00:00Z"
        assert entry["ok"] == 1
        assert entry["latency_ms"] == 250
        assert entry["error_summary"] is None

    def test_pagination(self, app, data_dir, fresh_ip):
        _insert_source_health("2026-08-01T00:00:00Z", source="nowgoal")
        _insert_source_health("2026-08-02T00:00:00Z", source="fotmob_lineup")
        _insert_source_health("2026-08-03T00:00:00Z", source="fotmob_sideline")
        admin = _login_admin(app, fresh_ip)

        page1 = admin.get("/api/v1/admin/source-health?limit=2&offset=0")
        page2 = admin.get("/api/v1/admin/source-health?limit=2&offset=2")
        assert page1.status_code == 200 and page2.status_code == 200

        src1 = [e["source"] for e in page1.json()["source_health"]]
        src2 = [e["source"] for e in page2.json()["source_health"]]
        assert src1 == ["fotmob_sideline", "fotmob_lineup"]  # checked_at DESC
        assert src2 == ["nowgoal"]
        assert set(src1) & set(src2) == set()

    def test_error_summary_sanitized(self, app, data_dir, fresh_ip):
        raw = "SELECT token FROM wechat_access_token_cache"
        _insert_source_health(
            "2026-08-05T00:00:00Z", source="nowgoal", ok=0, latency_ms=None, error_summary=raw
        )
        admin = _login_admin(app, fresh_ip)
        r = admin.get("/api/v1/admin/source-health")
        entry = r.json()["source_health"][0]
        expected = _sanitize_summary(raw)
        assert expected == "[SQL_REDACTED]"
        assert entry["error_summary"] == expected
        assert "SELECT" not in entry["error_summary"]
