"""赛季制度表 + 推导 + 0011 触发器(2026-08-25,CLAUDE.md §6.3 事故收口)。

三层各自钉住:
- season_for_match():跨年/自然年/日职换制/未登记 fail-closed;
- migration 0011 的触发器与 Python 推导**行为等价**(触发器 SQL 是
  derived_season_sql 的静态拷贝,等价性只能靠行为测试钉,不能靠肉眼);
- REGIME_SEED 与 migration 种子一字不差(两处必须同步改)。
"""

import sqlite3

import pytest

from backend.db.connections import connect_rw
from backend.season_regime import REGIME_SEED, season_for_match
from tests.backend.coreseed import seed_core_schema


@pytest.fixture
def core(data_dir):
    conn = connect_rw("core")
    seed_core_schema(conn)
    yield conn
    conn.close()


class TestSeedParity:
    def test_migration_seed_matches_python_constant(self, core):
        rows = core.execute(
            "SELECT league_id, effective_from, season_kind, cutover_month, note"
            " FROM dim_league_season_regime ORDER BY league_id, effective_from"
        ).fetchall()
        got = {(r[0], r[1], r[2], r[3]) for r in rows}
        want = {(l, e, k, c) for (l, e, k, c, _n) in REGIME_SEED}
        assert got == want

    def test_j1_has_two_effective_dated_regimes(self, core):
        rows = core.execute(
            "SELECT effective_from, season_kind FROM dim_league_season_regime"
            " WHERE league_id=223 ORDER BY effective_from"
        ).fetchall()
        assert [(r[0], r[1]) for r in rows] == [
            ("1900-01-01", "calendar_year"),
            ("2026-07-01", "cross_year"),
        ]


class TestSeasonForMatch:
    @pytest.mark.parametrize("league_id,date,want", [
        (47, "2026-08-21", "2026/2027"),   # 揭幕轮(事故场景)
        (47, "2026-05-01", "2025/2026"),   # 赛季末
        (47, "2027-06-30", "2026/2027"),   # 6 月仍属旧赛季
        (42, "2026-07-08", "2026/2027"),   # 欧冠 7 月资格赛属新赛季
        (59, "2026-07-11", "2026"),        # 自然年
        (268, "2026-01-15", "2026"),
        (223, "2026-03-01", "2026"),       # 日职换制前:自然年
        (223, "2026-08-07", "2026/2027"),  # 换制后:跨年
        (223, "2025-11-01", "2025"),
    ])
    def test_derivation(self, core, league_id, date, want):
        assert season_for_match(core, league_id, date) == want

    def test_unregistered_league_returns_none(self, core):
        assert season_for_match(core, 424242, "2026-08-21") is None

    def test_invalid_date_returns_none(self, core):
        assert season_for_match(core, 47, None) is None
        assert season_for_match(core, 47, "bad") is None


class TestTriggerParity:
    """触发器(SQL 静态拷贝)与 season_for_match(Python)必须给出同一答案:
    合法组合能写入、非法组合被 ABORT,逐一对齐上面的推导用例。"""

    def _insert(self, conn, mid, league_id, season, date):
        conn.execute(
            "INSERT INTO dim_match (Match_ID, Season, League_ID, Date, status)"
            " VALUES (?, ?, ?, ?, 'Finish')",
            (mid, season, league_id, date),
        )

    def test_correct_season_accepted_wrong_rejected(self, core):
        cases = [
            (47, "2026-08-21"), (47, "2026-05-01"), (59, "2026-07-11"),
            (223, "2026-03-01"), (223, "2026-08-07"),
        ]
        for i, (lid, d) in enumerate(cases):
            derived = season_for_match(core, lid, d)
            self._insert(core, 90000 + i, lid, derived, d)   # 必须成功
            with pytest.raises(sqlite3.IntegrityError, match="season guard"):
                self._insert(core, 91000 + i, lid, "1999/2000", d)
        core.rollback()

    def test_unregistered_league_aborts(self, core):
        with pytest.raises(sqlite3.IntegrityError, match="not registered"):
            self._insert(core, 92000, 424242, "2026/2027", "2026-08-21")
        core.rollback()

    def test_null_league_placeholder_row_allowed(self, core):
        """canonical 身份层(0003)的占位行只有 Match_ID——League_ID 为 NULL
        必须放行(它没有赛季可校验),这是触发器有意留的口子。"""
        core.execute("INSERT INTO dim_match (Match_ID) VALUES (93000)")
        core.rollback()

    def test_null_season_allowed_and_update_guarded(self, core):
        self._insert(core, 94000, 47, None, "2026-08-21")
        with pytest.raises(sqlite3.IntegrityError, match="season guard"):
            core.execute(
                "UPDATE dim_match SET Season='2019/2020' WHERE Match_ID=94000"
            )
        core.execute("UPDATE dim_match SET Season='2026/2027' WHERE Match_ID=94000")
        core.rollback()

    def test_existing_wrong_rows_untouched_by_migration(self, data_dir):
        """存量只报不改:0011 之前已存在的错标行,migration 重放(触发器
        DROP+CREATE)不碰它、后续无关列的 UPDATE 也不碰它。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        conn.execute("DROP TRIGGER IF EXISTS trg_dim_match_season_insert")
        conn.execute(
            "INSERT INTO dim_match (Match_ID, Season, League_ID, Date, status)"
            " VALUES (95000, '2025/2026', 47, '2026-08-21', 'Finish')"
        )
        from backend.db.migrate import MIGRATIONS_ROOT, split_statements
        sql = (MIGRATIONS_ROOT / "core" / "0011_season_integrity.sql").read_text(
            encoding="utf-8")
        for stmt in split_statements(sql):
            conn.execute(stmt)
        # 无关列 UPDATE(status)不触发赛季触发器
        conn.execute("UPDATE dim_match SET status='Cancelled' WHERE Match_ID=95000")
        row = conn.execute(
            "SELECT Season, status FROM dim_match WHERE Match_ID=95000"
        ).fetchone()
        assert (row["Season"], row["status"]) == ("2025/2026", "Cancelled")
        conn.rollback()
        conn.close()
