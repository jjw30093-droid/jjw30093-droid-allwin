"""赛果时间窗(2026-08-19,「赛果」入口 + 向过去的时间窗)。

真实缺陷:`_window_bounds` 里 `3d`/`7d` 是 `[now, now+N)` 严格向未来,所以
`status=finished` 配任何非 `all` 的窗口恒为 0 场——"近 7 天赛果"这个最自然
的用法根本表达不出来,用户只能"全部 13126 场"或按单日翻(生产实测)。

本文件钉死新增的三个向过去的窗:
- `yesterday` 走**北京自然日**分支(与 today/tomorrow 对称),不是滚动 24h
  ——两者结果真的不同:生产实测按 UTC 滚动 24h 是 0 场,按北京自然日 8/18
  是 5 场,选错一个用户就看不到昨天的比赛;
- `past3d`/`past7d` 是滚动 `[now-N, now)`,与 `3d`/`7d` 在 now 处严格互补;
- 既有五个窗口的行为一个字节都不许变(回归钉子,见 test_existing_*)。

`today` 刻意不新增对应值:它本来就是北京自然日 `[今天00:00, 明天00:00)`,
天然双向,"今天赛果" = `window=today&status=finished` 早就正确。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from backend.queries.matches import _window_bounds, list_matches


# 北京 = UTC+8 且无夏令时,所以 NOW 对应北京 2026-07-30 08:00。
# 取 08:00 而不是 00:00,是为了让"北京自然日"与"UTC 自然日"不重合——
# 若实现偷懒按 UTC 切自然日,yesterday 的断言会立刻失败。
NOW = datetime(2026, 7, 30, 0, 0, tzinfo=timezone.utc)
LEAGUE = 59


def _core() -> sqlite3.Connection:
    """最小 core 布景。与 test_five_critical_product_fixes.py::_core 同款——
    list_matches 只读 dim_match + dim_team_i18n(team_display_map),不需要
    完整 schema,也不走真实数据库文件。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE dim_match (
          Match_ID INTEGER PRIMARY KEY,
          Season TEXT NOT NULL,
          League_ID INTEGER NOT NULL,
          Date TEXT NOT NULL,
          kickoff_at_utc TEXT,
          Match_Round TEXT,
          status TEXT NOT NULL,
          Home_Team_ID INTEGER,
          Away_Team_ID INTEGER,
          Home_Team_Name TEXT,
          Away_Team_Name TEXT,
          home_score INTEGER,
          away_score INTEGER
        );
        CREATE TABLE dim_team_i18n (
          Team_ID INTEGER PRIMARY KEY,
          name_en TEXT,
          name_zh TEXT,
          source TEXT,
          updated_at TEXT
        );
        """
    )
    return conn


def _match(
    conn: sqlite3.Connection,
    match_id: int,
    kickoff: str,
    *,
    status: str = "Finish",
    home_score: int | None = 1,
    away_score: int | None = 0,
) -> None:
    conn.execute(
        """INSERT INTO dim_match
           (Match_ID,Season,League_ID,Date,kickoff_at_utc,Match_Round,status,
            Home_Team_ID,Away_Team_ID,Home_Team_Name,Away_Team_Name,
            home_score,away_score)
           VALUES (?,'2026',?,substr(?,1,10),?,'1',?,?,?,'Home','Away',?,?)""",
        (
            match_id, LEAGUE, kickoff, kickoff, status,
            match_id, match_id + 1000, home_score, away_score,
        ),
    )


class TestWindowBoundsPast:
    def test_yesterday_is_a_beijing_calendar_day_not_a_rolling_24h(self):
        """NOW = 北京 7/30 08:00 → yesterday = 北京 7/29 整天
        = [2026-07-28T16:00Z, 2026-07-29T16:00Z)。

        必须额外断言它**不等于** [now-24h, now):这两种实现在真实数据上给出
        不同的比赛集合(生产实测 0 场 vs 5 场),不是等价的风格选择。"""
        start, end = _window_bounds("yesterday", NOW)
        assert start == "2026-07-28T16:00:00Z"
        assert end == "2026-07-29T16:00:00Z"
        assert (start, end) != ("2026-07-29T00:00:00Z", "2026-07-30T00:00:00Z")

    def test_yesterday_today_tomorrow_are_contiguous_and_non_overlapping(self):
        """三段北京自然日首尾严格相接,无缝也无叠——重叠会让同一场比赛在两个
        窗口里各出现一次,留缝会让某些比赛任何窗口都选不到。"""
        y_start, y_end = _window_bounds("yesterday", NOW)
        t_start, t_end = _window_bounds("today", NOW)
        m_start, m_end = _window_bounds("tomorrow", NOW)
        assert y_end == t_start
        assert t_end == m_start
        assert y_start < y_end < m_end

    @pytest.mark.parametrize(
        ("window", "expected_start"),
        [("past3d", "2026-07-27T00:00:00Z"), ("past7d", "2026-07-23T00:00:00Z")],
    )
    def test_past_rolling_windows_end_at_now_not_in_the_future(self, window, expected_start):
        start, end = _window_bounds(window, NOW)
        assert start == expected_start
        assert end == "2026-07-30T00:00:00Z"  # 上界是 now,不是未来

    @pytest.mark.parametrize(("past", "future"), [("past3d", "3d"), ("past7d", "7d")])
    def test_past_and_future_rolling_windows_are_exactly_complementary_at_now(self, past, future):
        """past3d 与 3d 在 now 处严格互补:前者的上界 == 后者的下界。
        边界半开 [start, end),所以开球时刻恰好 == now 的比赛只属于未来窗,
        不会被两个窗口同时选中。"""
        _, past_end = _window_bounds(past, NOW)
        future_start, _ = _window_bounds(future, NOW)
        assert past_end == future_start

    def test_unsupported_window_still_raises(self):
        with pytest.raises(ValueError):
            _window_bounds("past999d", NOW)

    # ── 回归钉子:既有五个窗口一个字节都不许变 ──────────────────────

    @pytest.mark.parametrize(
        ("window", "expected"),
        [
            (None, (None, None)),
            ("all", (None, None)),
            ("today", ("2026-07-29T16:00:00Z", "2026-07-30T16:00:00Z")),
            ("tomorrow", ("2026-07-30T16:00:00Z", "2026-07-31T16:00:00Z")),
            ("3d", ("2026-07-30T00:00:00Z", "2026-08-02T00:00:00Z")),
            ("7d", ("2026-07-30T00:00:00Z", "2026-08-06T00:00:00Z")),
        ],
    )
    def test_existing_windows_unchanged(self, window, expected):
        assert _window_bounds(window, NOW) == expected


class TestListMatchesFinishedPastWindows:
    def test_finished_past7d_returns_only_finished_rows_inside_the_window_newest_first(self):
        conn = _core()
        try:
            _match(conn, 700, "2026-07-29T12:00:00Z")   # 窗内,最近
            _match(conn, 701, "2026-07-25T12:00:00Z")   # 窗内,较早
            _match(conn, 702, "2026-07-20T12:00:00Z")   # 窗外(past7d 之前)
            _match(conn, 703, "2026-08-01T12:00:00Z")   # 窗外(未来)
            _match(                                      # 窗内但未开赛 → 不是赛果
                conn, 704, "2026-07-28T12:00:00Z",
                status="NotStarted", home_score=None, away_score=None,
            )

            got = list_matches(conn, {LEAGUE}, status="finished", window="past7d", now=NOW)
            assert [r["match_id"] for r in got["matches"]] == [700, 701]
            assert got["total"] == 2
        finally:
            conn.close()

    def test_finished_yesterday_uses_beijing_calendar_day(self):
        """北京 7/29 = [2026-07-28T16:00Z, 2026-07-29T16:00Z)。
        刻意放一场 2026-07-29T20:00Z(北京 7/30 凌晨 04:00)——它在 UTC 自然日
        7/29 里,但在北京自然日属于**今天**,不该出现在"昨天"。"""
        conn = _core()
        try:
            _match(conn, 800, "2026-07-29T12:00:00Z")  # 北京 7/29 20:00 → 昨天 ✓
            _match(conn, 801, "2026-07-28T20:00:00Z")  # 北京 7/29 04:00 → 昨天 ✓
            _match(conn, 802, "2026-07-29T20:00:00Z")  # 北京 7/30 04:00 → 今天 ✗
            _match(conn, 803, "2026-07-28T12:00:00Z")  # 北京 7/28 20:00 → 前天 ✗

            got = list_matches(conn, {LEAGUE}, status="finished", window="yesterday", now=NOW)
            assert [r["match_id"] for r in got["matches"]] == [800, 801]
        finally:
            conn.close()

    def test_finished_past_window_is_not_polluted_by_zombie_fixtures(self):
        """数据源偶尔不把已开球比赛翻成 Finish(生产实测 0 条,但代码路径存在)。
        赛果窗口靠 status='Finish' 过滤,这类"未开赛但开球时间已过"的行绝不能
        以"无比分"的形态混进赛果列表。"""
        conn = _core()
        try:
            _match(conn, 900, "2026-07-29T12:00:00Z")
            _match(
                conn, 901, "2026-07-29T13:00:00Z",
                status="NotStarted", home_score=None, away_score=None,
            )
            got = list_matches(conn, {LEAGUE}, status="finished", window="past7d", now=NOW)
            assert [r["match_id"] for r in got["matches"]] == [900]
        finally:
            conn.close()

    def test_past_window_filters_on_exact_kickoff_only_not_coalesced_date(self):
        """只有自然日粒度(kickoff_at_utc IS NULL)的历史比赛不得被"精确窗口"
        收进来——CLAUDE.md §6.2.1 不得把粗粒度时间伪装成落在精确窗口内。
        生产实测 13126 场已完赛 0 场缺 kickoff,所以这条今天零损失,但必须
        写成显式测试而不是默认它成立。"""
        conn = _core()
        try:
            _match(conn, 950, "2026-07-29T12:00:00Z")
            conn.execute(
                """INSERT INTO dim_match
                   (Match_ID,Season,League_ID,Date,kickoff_at_utc,Match_Round,status,
                    Home_Team_ID,Away_Team_ID,Home_Team_Name,Away_Team_Name,
                    home_score,away_score)
                   VALUES (951,'2026',?,'2026-07-29',NULL,'1','Finish',
                           951,1951,'Home','Away',2,1)""",
                (LEAGUE,),
            )
            got = list_matches(conn, {LEAGUE}, status="finished", window="past7d", now=NOW)
            assert [r["match_id"] for r in got["matches"]] == [950]
            # 但 window=all 不加时间谓词,它必须仍然可见(不是被永久丢弃)
            everything = list_matches(conn, {LEAGUE}, status="finished", window="all", now=NOW)
            assert {r["match_id"] for r in everything["matches"]} == {950, 951}
        finally:
            conn.close()
