"""赛后完赛增量事件驱动到期条件(PIPELINE_REDESIGN_V2 P4,3.3)。

取代旧的纯"6h/联赛"节流:联赛存在至少一场 status != 'Finish' 且
kickoff + 2.5h < now 的比赛(该结束却仍未结束)即视为到期;没有这种比赛时,
仍保留 6h 兜底档(is_due 6h 节流)防止长期无更新的联赛完全不再被检查。

只覆盖本 phase 新增的判定逻辑本身,不重复 P1/P2/P3 已测试过的 mark_polled/
poll_attempt_log 写入行为。
"""

from backend.db.connections import connect_ro, connect_rw
from backend.ingest import poll_windows
from tests.backend.coreseed import seed_core_schema

T_NOW = "2026-08-17T13:47:28Z"


def _insert(conn, match_id, league_id, status, kickoff_at_utc,
            precision="exact", source="fotmob"):
    conn.execute(
        "INSERT OR REPLACE INTO dim_match (Match_ID, Season, League_ID, Date, status,"
        " kickoff_at_utc, kickoff_precision, kickoff_source)"
        " VALUES (?, '2026/2027', ?, '2026-08-14', ?, ?, ?, ?)",
        (match_id, league_id, status, kickoff_at_utc, precision, source),
    )


class TestLeagueStaleUnresolvedMatchIds:
    """poll_windows.league_stale_unresolved_match_ids 的边界:kickoff + 2.5h < now。"""

    def test_exactly_2_5h_boundary_is_not_yet_stale(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        # kickoff 恰好 2.5h 前:elapsed == threshold,不满足严格 ">" 条件
        _insert(conn, 1, 47, "NotStarted", "2026-08-17T11:17:28Z")
        conn.commit()
        conn.close()

        ro = connect_ro("core")
        try:
            stale = poll_windows.league_stale_unresolved_match_ids(ro, 47, T_NOW)
        finally:
            ro.close()
        assert 1 not in stale, "恰好 2.5h 不应判定为到期(必须严格 kickoff+2.5h < now)"

    def test_just_after_threshold_is_stale(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        # kickoff 2.5h + 1 分钟前
        _insert(conn, 2, 47, "NotStarted", "2026-08-17T11:16:28Z")
        conn.commit()
        conn.close()

        ro = connect_ro("core")
        try:
            stale = poll_windows.league_stale_unresolved_match_ids(ro, 47, T_NOW)
        finally:
            ro.close()
        assert 2 in stale

    def test_just_before_threshold_is_not_stale(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        # kickoff 2.5h - 1 分钟前(距开球才 2h29m)
        _insert(conn, 3, 47, "NotStarted", "2026-08-17T11:18:28Z")
        conn.commit()
        conn.close()

        ro = connect_ro("core")
        try:
            stale = poll_windows.league_stale_unresolved_match_ids(ro, 47, T_NOW)
        finally:
            ro.close()
        assert 3 not in stale

    def test_finished_match_never_stale_regardless_of_kickoff_age(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _insert(conn, 4, 47, "Finish", "2026-08-10T00:00:00Z")
        conn.commit()
        conn.close()

        ro = connect_ro("core")
        try:
            stale = poll_windows.league_stale_unresolved_match_ids(ro, 47, T_NOW)
        finally:
            ro.close()
        assert 4 not in stale

    def test_non_exact_kickoff_precision_never_counted(self, data_dir):
        """CLAUDE.md §6.2.1:没有精确开球时间的比赛不得参与赛前节奏判定。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        _insert(conn, 5, 47, "NotStarted", "2026-08-10T00:00:00Z", precision="date_only")
        conn.commit()
        conn.close()

        ro = connect_ro("core")
        try:
            stale = poll_windows.league_stale_unresolved_match_ids(ro, 47, T_NOW)
        finally:
            ro.close()
        assert 5 not in stale

    def test_other_league_not_included(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _insert(conn, 6, 87, "NotStarted", "2026-08-10T00:00:00Z")
        conn.commit()
        conn.close()

        ro = connect_ro("core")
        try:
            stale = poll_windows.league_stale_unresolved_match_ids(ro, 47, T_NOW)
        finally:
            ro.close()
        assert stale == set()


class TestFotmobIncrementalMultiDueComposition:
    """fotmob_incremental_multi.run(--due):事件驱动 OR 6h 兜底。"""

    def _seed_league_47(self, kickoff_at_utc, status="NotStarted"):
        conn = connect_rw("core")
        seed_core_schema(conn)
        _insert(conn, 101, 47, status, kickoff_at_utc)
        conn.commit()
        conn.close()

    def test_stale_match_triggers_fetch_even_if_recently_polled(self, data_dir, monkeypatch):
        """事件驱动到期必须能突破 6h 节流窗口(不用等满 6h)。"""
        from backend.cli import fotmob_incremental_multi as mod

        self._seed_league_47("2026-08-17T10:00:00Z")  # kickoff 3h47m 前,已过 2.5h 阈值

        conn_odds = connect_rw("odds")
        poll_windows.mark_polled(conn_odds, poll_windows.SOURCE_FOTMOB_INCREMENTAL, "47",
                                  "2026-08-17T13:40:00Z", "prior-run", tier="incremental", ok=True)
        conn_odds.close()

        calls = []

        class FakeScheduler:
            @staticmethod
            def step1_ingest_newly_finished(league_id, season):
                calls.append((league_id, season))
                return []

        monkeypatch.setattr(mod, "_load_scheduler", lambda: FakeScheduler())

        summary = mod.run(due_only=True, only_league=47, now_iso=T_NOW)
        assert calls == [(47, "2026/2027")], (
            "联赛存在到期未解决的比赛时,即使距上次轮询不到 6h,也必须真的去抓一次"
        )
        league_result = summary["leagues"][0]
        assert league_result["verdict"] == "ok"

    def test_no_stale_match_and_recently_polled_is_not_due(self, data_dir, monkeypatch):
        from backend.cli import fotmob_incremental_multi as mod

        # kickoff 只过去 1 小时,未过 2.5h 阈值
        self._seed_league_47("2026-08-17T12:47:28Z")

        conn_odds = connect_rw("odds")
        poll_windows.mark_polled(conn_odds, poll_windows.SOURCE_FOTMOB_INCREMENTAL, "47",
                                  "2026-08-17T13:40:00Z", "prior-run", tier="incremental", ok=True)
        conn_odds.close()

        calls = []

        class FakeScheduler:
            @staticmethod
            def step1_ingest_newly_finished(league_id, season):
                calls.append((league_id, season))
                return []

        monkeypatch.setattr(mod, "_load_scheduler", lambda: FakeScheduler())

        summary = mod.run(due_only=True, only_league=47, now_iso=T_NOW)
        assert calls == []
        assert summary["leagues"][0]["verdict"] == "not_due"

    def test_fallback_ceiling_fires_after_6h_with_no_stale_match(self, data_dir, monkeypatch):
        """没有任何到期未解决的比赛时,仍必须保留 6h 兜底档周期性刷新。"""
        from backend.cli import fotmob_incremental_multi as mod

        # kickoff 只过去 1 小时,未过 2.5h 阈值 —— 不构成事件驱动到期
        self._seed_league_47("2026-08-17T12:47:28Z")

        conn_odds = connect_rw("odds")
        # 上次轮询 6h01m 前 → 6h 兜底档到期
        poll_windows.mark_polled(conn_odds, poll_windows.SOURCE_FOTMOB_INCREMENTAL, "47",
                                  "2026-08-17T07:46:28Z", "prior-run", tier="incremental", ok=True)
        conn_odds.close()

        calls = []

        class FakeScheduler:
            @staticmethod
            def step1_ingest_newly_finished(league_id, season):
                calls.append((league_id, season))
                return []

        monkeypatch.setattr(mod, "_load_scheduler", lambda: FakeScheduler())

        summary = mod.run(due_only=True, only_league=47, now_iso=T_NOW)
        assert calls == [(47, "2026/2027")], "6h 兜底档到期时即使没有 stale 比赛也必须刷新"
        assert summary["leagues"][0]["due_reason"] == "fallback_ceiling"
