"""联赛积分榜(fact_league_table)迟到刷新:纯判断 + 落库/调用。

覆盖:
- due_refresh() 全部分支(2026-08-26 站长诊断/决定的场景);
- 任务函数(backend/cli/poll_standings.run_due)对 fixture 的端到端行为:
  到期即调用 ingest_season_tables 并落库状态;非到期不调用;新一场比赛
  完赛后旧的"已刷新"状态重新变为到期;
- 2026-08-26 扩到全联赛后新增的行为:范围派生自 LEAGUE_META(不再是另一份
  白名单)、非英超联赛到期同样会被刷新、每轮最多真正刷新 MAX_REFRESHES_PER_RUN
  个联赛且按 due_at 最旧优先、被上限挤掉的联赛记 deferred 顺延到下一轮而不是
  静默丢失、deferred 不让整轮失败、单联赛失败不拖垮其它联赛。
"""

import pytest

from backend.db.connections import connect_rw
from backend.ingest.standings_refresh_poll import (
    MAX_REFRESHES_PER_RUN,
    REFRESH_DELAY_HOURS,
    RefreshDecision,
    due_refresh,
)
from backend.queries.leagues import LEAGUE_META


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

    def test_max_refreshes_per_run_is_five(self):
        assert MAX_REFRESHES_PER_RUN == 5

    def test_returns_refresh_decision_dataclass(self):
        d = due_refresh(T_KICKOFF, None, "2026-08-01T18:00:00Z")
        assert isinstance(d, RefreshDecision)


# ── 任务函数(端到端,fixture 数据) ─────────────────────────────────────

EPL = 47
LALIGA = 87  # 用于"非英超联赛到期同样会被刷新"的回归断言


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


def _stub_ingest_season_tables(monkeypatch, fail_for=None):
    # 触发 poll_standings 模块顶部的 sys.path 桥接,确保 "ingest_league" 作为
    # 顶层模块名可被 import_module 解析到(与 poll_standings.py 内部
    # `from ingest_league import ingest_season_tables` 用的是同一个模块对象)。
    import backend.cli.poll_standings  # noqa: F401

    calls = []

    def fake(client, league_id, season):
        if fail_for is not None and league_id == fail_for:
            raise RuntimeError(f"boom for league {league_id}")
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
        assert result["refreshed"] == 1
        assert result["deferred"] == 0
        assert result["errors"] == 0
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
        assert result["refreshed"] == 0
        assert calls == []

    def test_non_epl_league_is_refreshed_when_due(self, conn_core, monkeypatch):
        """2026-08-26 回归护栏:此前非英超联赛即便到期也完全不被触碰
        (STANDINGS_LEAGUE_IDS={47});扩到全联赛后,任何 LEAGUE_META 里的
        联赛到期都应该被真正刷新——这是本次改动要验证的核心行为。"""
        match_id = 800003
        _seed_match(conn_core, match_id, LALIGA, T_KICKOFF, "2026/2027")
        calls = _stub_ingest_season_tables(monkeypatch)

        from backend.cli.poll_standings import run_due

        result = run_due(now_iso="2026-08-02T00:00:00Z")  # 早已过期
        assert calls == [(LALIGA, "2026/2027")]
        assert result["refreshed"] == 1

        row = conn_core.execute(
            "SELECT last_refreshed_at FROM standings_refresh_state"
            " WHERE league_id=? AND season=?",
            (LALIGA, "2026/2027"),
        ).fetchone()
        assert row is not None

    def test_no_finished_match_not_due(self, conn_core, monkeypatch):
        calls = _stub_ingest_season_tables(monkeypatch)

        from backend.cli.poll_standings import run_due

        result = run_due(now_iso="2026-08-02T00:00:00Z")
        assert result["refreshed"] == 0
        assert calls == []
        # 空库:LEAGUE_META 里每一个联赛都应该被评估到,且理由一致——不是
        # 只看第一条就断言过关,17 个联赛一个都不能漏评估。
        assert len(result["results"]) == len(LEAGUE_META)
        assert all(r["reason"] == "no_finished_match" for r in result["results"])

    def test_second_finished_match_triggers_refresh_again(self, conn_core, monkeypatch):
        """第一场完赛+6h 刷新过一次;随后第二场完赛,+6h 后应再次刷新
        (对应站长的"每有一场比赛结束,过 6 小时就要再刷新一次")。"""
        from backend.cli.poll_standings import run_due

        _seed_match(conn_core, 800004, EPL, "2026-08-01T12:00:00Z", "2026/2027")
        calls = _stub_ingest_season_tables(monkeypatch)
        result1 = run_due(now_iso="2026-08-01T18:00:00Z")
        assert result1["refreshed"] == 1
        assert calls == [(EPL, "2026/2027")]

        result2 = run_due(now_iso="2026-08-01T19:00:00Z")  # 没有新比赛完赛,仍不到期
        assert result2["refreshed"] == 0

        _seed_match(conn_core, 800005, EPL, "2026-08-08T12:00:00Z", "2026/2027")
        result3 = run_due(now_iso="2026-08-08T18:00:00Z")
        assert result3["refreshed"] == 1
        assert calls == [(EPL, "2026/2027"), (EPL, "2026/2027")]

        row = conn_core.execute(
            "SELECT last_finished_kickoff_at_utc FROM standings_refresh_state"
            " WHERE league_id=? AND season=?",
            (EPL, "2026/2027"),
        ).fetchone()
        assert row["last_finished_kickoff_at_utc"] == "2026-08-08T12:00:00Z"


# ── 全联赛 + 每轮上限(2026-08-26 扩容) ───────────────────────────────────

# 7 个真实存在于 LEAGUE_META 的联赛,kickoff 依次错开 1 小时,due_at 因此
# 严格递增:42→16:00, 47→17:00, 48→18:00, 53→19:00, 54→20:00, 55→21:00, 57→22:00。
_SEVEN_LEAGUES = [42, 47, 48, 53, 54, 55, 57]


def _seed_seven_due_leagues(conn):
    for i, league_id in enumerate(_SEVEN_LEAGUES):
        kickoff = f"2026-08-01T{10 + i:02d}:00:00Z"
        _seed_match(conn, 900000 + league_id, league_id, kickoff, "2026/2027")


class TestAllLeaguesAndCap:
    def test_all_league_meta_leagues_are_evaluated(self, conn_core, monkeypatch):
        """范围派生自 LEAGUE_META,不是另一份手维护的白名单——加一个联赛到
        LEAGUE_META,这个任务应该自动跟着扩大,不需要再改这个任务的代码。"""
        _stub_ingest_season_tables(monkeypatch)

        from backend.cli.poll_standings import run_due

        result = run_due(now_iso="2026-08-02T00:00:00Z")
        assert {r["league_id"] for r in result["results"]} == set(LEAGUE_META)
        assert result["leagues"] == len(LEAGUE_META)

    def test_multiple_due_leagues_all_refreshed_under_cap(self, conn_core, monkeypatch):
        for i, league_id in enumerate([EPL, 53, 55]):
            _seed_match(
                conn_core, 900100 + league_id, league_id,
                f"2026-08-01T1{i}:00:00Z", "2026/2027",
            )
        calls = _stub_ingest_season_tables(monkeypatch)

        from backend.cli.poll_standings import run_due

        result = run_due(now_iso="2026-08-02T00:00:00Z")
        assert result["refreshed"] == 3
        assert result["deferred"] == 0
        assert len(calls) == 3

    def test_cap_limits_refreshes_and_reports_deferred(self, conn_core, monkeypatch):
        """7 个联赛同时到期,上限 5——超出的 2 个必须如实报 deferred,
        而不是从摘要里悄悄消失(CLAUDE.md 禁止静默截断)。"""
        _seed_seven_due_leagues(conn_core)
        calls = _stub_ingest_season_tables(monkeypatch)

        from backend.cli.poll_standings import run_due

        result = run_due(now_iso="2026-08-02T00:00:00Z")
        assert len(calls) == MAX_REFRESHES_PER_RUN
        assert result["refreshed"] == 5
        assert result["deferred"] == 2
        assert result["cap"] == MAX_REFRESHES_PER_RUN

        deferred_results = [r for r in result["results"] if r["action"] == "deferred"]
        assert len(deferred_results) == 2
        assert all(r["reason"] == "per_run_cap" for r in deferred_results)
        assert sorted(result["deferred_league_ids"]) == sorted(
            r["league_id"] for r in deferred_results
        )

        # 被 defer 的联赛不能写 state——它们根本没有被真正刷新过。
        for r in deferred_results:
            row = conn_core.execute(
                "SELECT 1 FROM standings_refresh_state WHERE league_id=?",
                (r["league_id"],),
            ).fetchone()
            assert row is None

    def test_oldest_due_first_wins_the_budget(self, conn_core, monkeypatch):
        """按 due_at 最旧优先选出前 5 个真正刷新——不能按 league_id 排序,
        否则低 id 联赛永远优先、高 id 可能被持续饿死。7 个联赛里 due_at
        最新的两个(55、57)应该被 defer。"""
        _seed_seven_due_leagues(conn_core)
        calls = _stub_ingest_season_tables(monkeypatch)

        from backend.cli.poll_standings import run_due

        result = run_due(now_iso="2026-08-02T00:00:00Z")
        refreshed_ids = {lid for lid, _season in calls}
        assert refreshed_ids == {42, 47, 48, 53, 54}
        assert sorted(result["deferred_league_ids"]) == [55, 57]

    def test_deferred_league_is_refreshed_on_next_run(self, conn_core, monkeypatch):
        """被上限挤掉的联赛不会被持续饿死——同一个到期窗口内,下一轮应该
        补齐所有到期联赛,且没有任何联赛被重复刷新。"""
        _seed_seven_due_leagues(conn_core)
        calls = _stub_ingest_season_tables(monkeypatch)

        from backend.cli.poll_standings import run_due

        result1 = run_due(now_iso="2026-08-02T00:00:00Z")
        assert result1["deferred"] == 2

        result2 = run_due(now_iso="2026-08-02T00:05:00Z")
        assert result2["refreshed"] == 2  # 正好补齐上一轮被 defer 的 2 个
        assert result2["deferred"] == 0

        assert sorted(lid for lid, _s in calls) == sorted(_SEVEN_LEAGUES)
        assert len(calls) == len(_SEVEN_LEAGUES)  # 没有任何联赛被刷新两次

    def test_cap_counts_only_refreshed_not_checked(self, conn_core, monkeypatch):
        """上限只约束"真正刷新",不约束"检查了但未到期/无比赛"——5 个到期
        联赛之外还有一堆无比赛的联赛,便宜的检查不能占用刷新预算。"""
        for i, league_id in enumerate([42, 47, 48, 53, 54]):
            _seed_match(
                conn_core, 900200 + league_id, league_id,
                f"2026-08-01T1{i}:00:00Z", "2026/2027",
            )
        calls = _stub_ingest_season_tables(monkeypatch)

        from backend.cli.poll_standings import run_due

        result = run_due(now_iso="2026-08-02T00:00:00Z")
        assert result["refreshed"] == 5
        assert result["deferred"] == 0
        assert len(calls) == 5
        # 其余 LEAGUE_META 联赛(无比赛)应该是 not_due,不占刷新预算。
        no_match_count = sum(
            1 for r in result["results"] if r.get("reason") == "no_finished_match"
        )
        assert no_match_count == len(LEAGUE_META) - 5

    def test_one_league_failure_does_not_block_others(self, conn_core, monkeypatch):
        _seed_match(conn_core, 900301, EPL, "2026-08-01T10:00:00Z", "2026/2027")
        _seed_match(conn_core, 900302, 53, "2026-08-01T11:00:00Z", "2026/2027")
        calls = _stub_ingest_season_tables(monkeypatch, fail_for=EPL)

        from backend.cli.poll_standings import main, run_due

        result = run_due(now_iso="2026-08-02T00:00:00Z")
        assert result["errors"] == 1
        assert result["refreshed"] == 1
        assert calls == [(53, "2026/2027")]

        error_results = [r for r in result["results"] if r["action"] == "error"]
        assert len(error_results) == 1
        assert error_results[0]["league_id"] == EPL

        exit_code = main(["--due", "--now", "2026-08-02T00:00:00Z"])
        assert exit_code == 1

    def test_deferred_does_not_fail_the_run(self, conn_core, monkeypatch):
        """正常排空积压期间(存在 deferred 但没有 error)不能让 systemd
        把这一轮标红——deferred 是预期状态,不是失败。"""
        _seed_seven_due_leagues(conn_core)
        _stub_ingest_season_tables(monkeypatch)

        from backend.cli.poll_standings import main

        exit_code = main(["--due", "--now", "2026-08-02T00:00:00Z"])
        assert exit_code == 0
