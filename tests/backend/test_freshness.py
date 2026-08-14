"""GET /api/v1/status/freshness(首页「今日更新状态」聚合)。

只读三张已有表的最近成功时间戳,不新增写路径、不新增采集。覆盖:
匿名公开缓存(在 PUBLIC_ALLOWLIST 内)、带凭证强制 private、
三字段各自独立取 MAX、任一表为空时如实返回 null(不用当前时间顶替)、
fetch_failed/refused_* 不计入"赛程更新"(没有真正拿到新数据)。
"""

from fastapi.testclient import TestClient

from backend.db.connections import connect_rw, tx

STRICT_NO_STORE = "private, no-store"


def _insert_ledger(conn_odds, run_at: str, verdict: str, league_id: int = 47) -> None:
    with tx(conn_odds):
        conn_odds.execute(
            """INSERT INTO fixture_sync_ledger
                 (run_at, poll_run_id, league_id, season, provider_selected_season,
                  fallback_season_used, fetched_rows, horizon7_rows, written_rows,
                  prev_fetched_rows, verdict, detail)
               VALUES (?, 'run-1', ?, '2026/2027', '2026/2027', 0, 10, 2, 10, NULL, ?, NULL)""",
            (run_at, league_id, verdict),
        )


def _insert_odds_snap(conn_odds, observed_at: str) -> None:
    with tx(conn_odds):
        conn_odds.execute(
            """INSERT INTO bronze_ng_odds_snap
                 (provider_match_id, market, company_id, company_name, market_phase,
                  payload_json, payload_hash, source_updated_at, observed_at,
                  ingested_at, poll_run_id)
               VALUES ('pm-1', '1x2', 'c1', 'Bet365', 'pre_match',
                       '{}', 'hash-1', NULL, ?, ?, 'run-1')""",
            (observed_at, observed_at),
        )


def _insert_reco_slip(
    conn_platform, published_at: str, status: str = "published", suffix: str = "a"
) -> None:
    from backend.cli.create_admin import create_admin
    from backend.commands.reco import LegInput, create_slip, publish_slip

    # created_by/actor 是真实外键(auth users),不能塞任意字符串
    actor = create_admin(conn_platform, f"freshness-admin-{suffix}", "freshness-pw-12345")
    with tx(conn_platform):
        slip_id = create_slip(
            conn_platform,
            slip_date=published_at[:10],
            title="测试推荐",
            note=None,
            legs=[LegInput(match_desc="A vs B", market="1x2", selection="主胜", odds=1.9)],
            actor=actor,
        )
    if status != "draft":
        with tx(conn_platform):
            publish_slip(conn_platform, slip_id, actor=actor)
        # published_at 由 publish_slip 写入 utc_now();测试需要控制具体值时直接改写
        with tx(conn_platform):
            conn_platform.execute(
                "UPDATE reco_slips SET published_at=? WHERE id=?", (published_at, slip_id)
            )
    return slip_id


class TestFreshnessCache:
    def test_anonymous_gets_shared_public_cache(self, app, data_dir):
        r = TestClient(app).get("/api/v1/status/freshness")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "public, s-maxage=60, stale-while-revalidate=30"
        assert "set-cookie" not in {k.lower() for k in r.headers.keys()}

    def test_cookie_forces_private(self, app, data_dir):
        r = TestClient(app).get(
            "/api/v1/status/freshness",
            headers={"Cookie": "allwin_session=totally-bogus-token"},
        )
        assert r.headers["cache-control"] == STRICT_NO_STORE


class TestFreshnessValues:
    def test_all_null_when_tables_empty(self, app, data_dir):
        r = TestClient(app).get("/api/v1/status/freshness")
        assert r.status_code == 200
        body = r.json()
        assert body == {
            "schedule_updated_at": None,
            "odds_updated_at": None,
            "reco_updated_at": None,
        }

    def test_reports_max_of_each_independent_source(self, app, data_dir):
        conn_odds = connect_rw("odds")
        conn_platform = connect_rw("platform")
        try:
            _insert_ledger(conn_odds, "2026-08-10T09:00:00Z", "written")
            _insert_ledger(conn_odds, "2026-08-10T23:53:18Z", "written", league_id=87)
            _insert_odds_snap(conn_odds, "2026-08-10T06:00:24Z")
            _insert_odds_snap(conn_odds, "2026-08-09T01:00:00Z")
            _insert_reco_slip(conn_platform, "2026-08-09T21:10:23Z")
        finally:
            conn_odds.close()
            conn_platform.close()

        body = TestClient(app).get("/api/v1/status/freshness").json()
        assert body["schedule_updated_at"] == "2026-08-10T23:53:18Z"
        assert body["odds_updated_at"] == "2026-08-10T06:00:24Z"
        assert body["reco_updated_at"] == "2026-08-09T21:10:23Z"

    def test_fetch_failed_and_refused_do_not_count_as_schedule_update(self, app, data_dir):
        """网络失败/反退化拒写没有真正拿到新数据,不得算作"赛程更新"——
        否则一次偶发 SSL 失败会让首页显示虚假的"刚刚更新"。"""
        conn_odds = connect_rw("odds")
        try:
            _insert_ledger(conn_odds, "2026-08-11T05:00:00Z", "fetch_failed")
            _insert_ledger(conn_odds, "2026-08-11T05:05:00Z", "refused_regression")
            _insert_ledger(conn_odds, "2026-08-11T05:10:00Z", "refused_downgrade")
            _insert_ledger(conn_odds, "2026-08-11T05:15:00Z", "refused_identity")
        finally:
            conn_odds.close()

        body = TestClient(app).get("/api/v1/status/freshness").json()
        assert body["schedule_updated_at"] is None

    def test_draft_reco_slip_does_not_count(self, app, data_dir):
        conn_platform = connect_rw("platform")
        try:
            _insert_reco_slip(conn_platform, "2026-08-11T00:00:00Z", status="draft")
        finally:
            conn_platform.close()

        body = TestClient(app).get("/api/v1/status/freshness").json()
        assert body["reco_updated_at"] is None
