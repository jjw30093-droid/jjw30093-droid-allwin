"""联赛积分榜(fact_league_table)迟到刷新:纯判断 + 落库/调用。

覆盖:
- due_refresh() 全部分支(2026-08-26 站长诊断/决定的场景);
- 任务函数(backend/cli/poll_standings.run_due)对 fixture 的端到端行为:
  到期即调用 ingest_season_tables 并落库状态;非到期不调用;非英超联赛
  即便时间上"到期"也完全不被触碰;新一场比赛完赛后旧的"已刷新"状态
  重新变为到期。
"""

import pytest

from backend.db.connections import connect_rw
from backend.ingest.standings_refresh_poll import (
    REFRESH_DELAY_HOURS,
    STANDINGS_LEAGUE_IDS,
    RefreshDecision,
    due_refresh,
)


# ── due_refresh() ───────────────────────────────────────────────────────

T_KICKOFF = "2026-08-01T12:00:00Z"  # +6h due_at = 2026-08-01T18:00:00Z


class TestDueRefresh:
    def test_no_finished_match_never_due(self):
        d = due_refresh(None, None, "2026-08-05T00:00:00Z")
        assert d.due is False
        assert d.reason == "no_finished_match"
        assert d.due_at is None

    def test_never_refreshed_before_due_at_not_due(self):
        d = due_refresh(T_KICKOFF, None, "2026-08-01T17:59:59Z")
        assert d.due is False
        assert d.reason == "not_yet_due"
        assert d.due_at == "2026-08-01T18:00:00Z"

    def test_never_refreshed_at_due_at_is_due(self):
        d = due_refresh(T_KICKOFF, None, "2026-08-01T18:00:00Z")
        assert d.due is True
        assert d.reason == "due"

    def test_never_refreshed_after_due_at_is_due(self):
        d = due_refresh(T_KICKOFF, None, "2026-08-02T00:00:00Z")
        assert d.due is True

    def test_refreshed_after_due_at_not_due(self):
        d = due_refresh(T_KICKOFF, "2026-08-01T19:00:00Z", "2026-08-01T20:00:00Z")
        assert d.due is False
        assert d.reason == "already_refreshed_since_due"

    def test_refreshed_exactly_at_due_at_not_due(self):
        d = due_refresh(T_KICKOFF, "2026-08-01T18:00:00Z", "2026-08-01T18:00:00Z")
        assert d.due is False
        assert d.reason == "already_refreshed_since_due"

    def test_refreshed_before_due_at_still_due(self):
        """上次刷新发生在旧的到期点之前(例如更早一场比赛触发的刷新)——
        新的到期点尚未被覆盖,仍然 due。"""
        d = due_refresh(T_KICKOFF, "2026-08-01T10:00:00Z", "2026-08-01T18:00:00Z")
        assert d.due is True
        assert d.reason == "due"

    def test_new_match_finishes_clears_due_again(self):
        """场景:第一场比赛完赛+6h 已经刷新过一次;随后又有新比赛完赛,
        "最近一场完赛比赛开球时间"前移,到期点也随之前移,应重新变为 due。"""
        first_kickoff = "2026-08-01T12:00:00Z"
        first_due_at = "2026-08-01T18:00:00Z"
        refreshed_at = first_due_at  # 第一次刷新恰好发生在第一个到期点

        # 用第一场的数据看仍然 not due(已覆盖)
        d1 = due_refresh(first_kickoff, refreshed_at, "2026-08-01T19:00:00Z")
        assert d1.due is False

        # 新比赛在之后完赛,新的到期点晚于上次刷新时间 → due again
        second_kickoff = "2026-08-08T12:00:00Z"
        second_due_at = "2026-08-08T18:00:00Z"
        d2 = due_refresh(second_kickoff, refreshed_at, "2026-08-08T18:00:00Z")
        assert d2.due is True
        assert d2.due_at == second_due_at

    def test_refresh_delay_is_six_hours(self):
        assert REFRESH_DELAY_HOURS == 6.0

    def test_returns_refresh_decision_dataclass(self):
        d = due_refresh(T_KICKOFF, None, "2026-08-01T18:00:00Z")
        assert isinstance(d, RefreshDecision)


# ── 任务函数(端到端,fixture 数据) ─────────────────────────────────────

EPL = 47
NON_EPL = 48  # 英冠:不在 STANDINGS_LEAGUE_IDS 范围内


def _seed_match(conn, match_id, league_id, kickoff_at_utc, season, status="Finish"):
    conn.execute(
        """INSERT INTO dim_match
             (Match_ID, Season, League_ID, Date, Home_Team_ID, Away_Team_ID,
              Home_Team_Name, Away_Team_Name, home_score, away_score, status,
              kickoff_at_utc, kickoff_precision, kickoff_source)
           VALUES (?, ?, ?, ?, 111, 222, 'Home', 'Away', 1, 0, ?, ?, 'exact', 'fotmob')""",
        (match_id, season, league_id, kickoff_at_utc[:10], status, kickoff_at_utc),
    )
    conn.commit()


@pytest.fixture
def conn_core(data_dir):
    conn = connect_rw("core")
    yield conn
    conn.close()


def _stub_ingest_season_tables(monkeypatch):
    # 触发 poll_standings 模块顶部的 sys.path 桥接,确保 "ingest_league" 作为
    # 顶层模块名可被 import_module 解析到(与 poll_standings.py 内部
    # `from ingest_league import ingest_season_tables` 用的是同一个模块对象)。
    import backend.cli.poll_standings  # noqa: F401

    calls = []

    def fake(client, league_id, season):
        calls.append((league_id, season))

    monkeypatch.setattr("ingest_league.ingest_season_tables", fake)
    monkeypatch.setattr(
        "backend.fotmob_client.FotMobClient", lambda: object(), raising=True
    )
    return calls


class TestRunDue:
    def test_due_league_gets_refreshed_and_state_written(self, conn_core, monkeypatch):
        match_id = 800001
        _seed_match(conn_core, match_id, EPL, T_KICKOFF, "2026/2027")
        calls = _stub_ingest_season_tables(monkeypatch)

        from backend.cli.poll_standings import run_due

        result = run_due(now_iso="2026-08-01T18:00:00Z")
        assert result["acted"] == 1
        assert calls == [(EPL, "2026/2027")]

        row = conn_core.execute(
            "SELECT last_refreshed_at, last_finished_kickoff_at_utc"
            " FROM standings_refresh_state WHERE league_id=? AND season=?",
            (EPL, "2026/2027"),
        ).fetchone()
        assert row is not None
        assert row["last_refreshed_at"] == "2026-08-01T18:00:00Z"
        assert row["last_finished_kickoff_at_utc"] == T_KICKOFF

    def test_not_yet_due_does_not_call_ingest(self, conn_core, monkeypatch):
        match_id = 800002
        _seed_match(conn_core, match_id, EPL, T_KICKOFF, "2026/2027")
        calls = _stub_ingest_season_tables(monkeypatch)

        from backend.cli.poll_standings import run_due

        result = run_due(now_iso="2026-08-01T17:00:00Z")  # kickoff+5h,未到期
        assert result["acted"] == 0
        assert calls == []

    def test_non_epl_league_never_touched_even_if_due(self, conn_core, monkeypatch):
        match_id = 800003
        _seed_match(conn_core, match_id, NON_EPL, T_KICKOFF, "2026/2027")
        calls = _stub_ingest_season_tables(monkeypatch)

        from backend.cli.poll_standings import run_due

        result = run_due(now_iso="2026-08-02T00:00:00Z")  # 早已过期,但不在范围内
        assert calls == []
        # NON_EPL 联赛没有任何一条 result(不在 STANDINGS_LEAGUE_IDS 范围内)
        assert all(r["league_id"] != NON_EPL for r in result["results"])

    def test_no_finished_match_not_due(self, conn_core, monkeypatch):
        calls = _stub_ingest_season_tables(monkeypatch)

        from backend.cli.poll_standings import run_due

        result = run_due(now_iso="2026-08-02T00:00:00Z")
        assert result["acted"] == 0
        assert calls == []
        assert result["results"][0]["reason"] == "no_finished_match"

    def test_second_finished_match_triggers_refresh_again(self, conn_core, monkeypatch):
        """第一场完赛+6h 刷新过一次;随后第二场完赛,+6h 后应再次刷新
        (对应站长的"每有一场英超比赛结束,过 6 小时就要再刷新一次")。"""
        from backend.cli.poll_standings import run_due

        _seed_match(conn_core, 800004, EPL, "2026-08-01T12:00:00Z", "2026/2027")
        calls = _stub_ingest_season_tables(monkeypatch)
        result1 = run_due(now_iso="2026-08-01T18:00:00Z")
        assert result1["acted"] == 1
        assert calls == [(EPL, "2026/2027")]

        result2 = run_due(now_iso="2026-08-01T19:00:00Z")  # 没有新比赛完赛,仍不到期
        assert result2["acted"] == 0

        _seed_match(conn_core, 800005, EPL, "2026-08-08T12:00:00Z", "2026/2027")
        result3 = run_due(now_iso="2026-08-08T18:00:00Z")
        assert result3["acted"] == 1
        assert calls == [(EPL, "2026/2027"), (EPL, "2026/2027")]

        row = conn_core.execute(
            "SELECT last_finished_kickoff_at_utc FROM standings_refresh_state"
            " WHERE league_id=? AND season=?",
            (EPL, "2026/2027"),
        ).fetchone()
        assert row["last_finished_kickoff_at_utc"] == "2026-08-08T12:00:00Z"
