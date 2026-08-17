"""GET /api/v1/status/freshness(首页「今日更新状态」聚合)。

只读三张已有表的最近成功时间戳,不新增写路径、不新增采集。覆盖:
匿名公开缓存(在 PUBLIC_ALLOWLIST 内)、带凭证强制 private、
三字段各自独立取 MAX、任一表为空时如实返回 null(不用当前时间顶替)、
fetch_failed/refused_* 不计入"赛程更新"(没有真正拿到新数据)。

首页粗糙度修复(2026-08-16):三个时间戳各自额外配一个 FRESH/STALE/UNAVAILABLE
三态字段(schedule_state/odds_state/reco_state),复用仓库统一词汇
(backend/queries/odds.py::classify_odds_freshness、backend/content_status.py::
project_freshness 同一套值)。阈值复用 backend/cli/ops_check.py 的
SOURCE_STALE_HOURS=6,不发明新数字。
"""

from datetime import datetime, timedelta, timezone

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
        # 2026-08-16 扩展:时间戳为 None 时对应状态字段必须是明确的
        # "UNAVAILABLE"(不是再一个 null 占位),用户界面才能区分"从未成功过"
        # 和"曾经成功但现在过期"。
        assert body == {
            "schedule_updated_at": None,
            "odds_updated_at": None,
            "reco_updated_at": None,
            "schedule_state": "UNAVAILABLE",
            "odds_state": "UNAVAILABLE",
            "reco_state": "UNAVAILABLE",
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
        # draft 不计入"发布",没有可用时间戳 -> 状态如实为 UNAVAILABLE
        # (不是伪装成从未发生过任何事的 STALE,也不是留白)。
        assert body["reco_state"] == "UNAVAILABLE"


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class TestFreshnessState:
    """三个信号各自的 FRESH/STALE/UNAVAILABLE 判定(2026-08-16 首页粗糙度修复)。

    时间戳为 None 的 UNAVAILABLE 情形已经由 TestFreshnessValues 的
    test_all_null_when_tables_empty / test_draft_reco_slip_does_not_count 覆盖,
    这里补 FRESH(距今很近)与 STALE(超过 6 小时阈值)两种情形,三个信号各
    至少覆盖一种,不做 3x3 全覆盖。
    """

    def test_schedule_state_fresh_when_synced_minutes_ago(self, app, data_dir):
        recent = _iso(datetime.now(timezone.utc) - timedelta(minutes=5))
        conn_odds = connect_rw("odds")
        try:
            _insert_ledger(conn_odds, recent, "written")
        finally:
            conn_odds.close()

        body = TestClient(app).get("/api/v1/status/freshness").json()
        assert body["schedule_updated_at"] == recent
        assert body["schedule_state"] == "FRESH"

    def test_odds_state_stale_when_older_than_six_hours(self, app, data_dir):
        old = _iso(datetime.now(timezone.utc) - timedelta(hours=10))
        conn_odds = connect_rw("odds")
        try:
            _insert_odds_snap(conn_odds, old)
        finally:
            conn_odds.close()

        body = TestClient(app).get("/api/v1/status/freshness").json()
        assert body["odds_updated_at"] == old
        assert body["odds_state"] == "STALE"

    def test_reco_state_fresh_when_published_minutes_ago(self, app, data_dir):
        recent = _iso(datetime.now(timezone.utc) - timedelta(minutes=1))
        conn_platform = connect_rw("platform")
        try:
            _insert_reco_slip(conn_platform, recent)
        finally:
            conn_platform.close()

        body = TestClient(app).get("/api/v1/status/freshness").json()
        assert body["reco_updated_at"] == recent
        assert body["reco_state"] == "FRESH"
