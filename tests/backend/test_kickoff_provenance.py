"""P0-A:migration provenance + repair 工具 + 永久资格不变量(§6.1.11-14)。

用临时数据库验证:
- migration 保守回填(旧数据不默认 exact),幂等,不改 official/locked 集合;
- repair 只修未锁定的午夜占位,dry-run 无副作用,幂等,official/locked 不变;
- repair 绝不触碰 locked/official;
- import + repair 幂等。
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.cli.repair_kickoff_provenance import find_targets, repair
from backend.db.connections import connect_rw
from backend.db.util import new_uuid, utc_now_iso

GOLD_MODEL = "dc-baseline-1.M.2"


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _insert_snapshot(conn, *, match_id, kickoff, precision="unknown", source=None,
                     status="draft", is_official=0, locked=False, model=GOLD_MODEL):
    now = utc_now_iso()
    conn.execute("INSERT OR IGNORE INTO model_versions (id, algorithm, created_at) VALUES (?, 'dc', ?)",
                 (model, now))
    sid = new_uuid()
    conn.execute(
        """INSERT INTO prediction_snapshots
           (id, match_id, kickoff_at_utc, kickoff_precision, kickoff_source, model_version_id,
            generated_at, published_at, locked_at, prediction_hash, home_win, draw, away_win,
            status, is_official, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'h', 0.5, 0.3, 0.2, ?, ?, ?)""",
        (sid, match_id, kickoff, precision, source, model, now,
         now if is_official else None, now if locked else None, status, is_official, now),
    )
    conn.commit()
    return sid


@pytest.fixture
def platform(data_dir):
    conn = connect_rw("platform")
    yield conn
    conn.close()


def _official_locked(conn):
    return conn.execute(
        "SELECT COUNT(*) FROM prediction_snapshots WHERE is_official=1 AND locked_at IS NOT NULL"
    ).fetchone()[0]


class TestMigrationProvenance:
    def test_snapshot_columns_added_with_conservative_default(self, platform):
        cols = {r[1] for r in platform.execute("PRAGMA table_info(prediction_snapshots)")}
        assert {"kickoff_precision", "kickoff_source"} <= cols

    def test_core_dim_match_columns_added(self, data_dir):
        core = connect_rw("core")
        try:
            cols = {r[1] for r in core.execute("PRAGMA table_info(dim_match)")}
            assert {"kickoff_precision", "kickoff_source"} <= cols
        finally:
            core.close()

    def test_locked_provenance_now_editable(self, platform):
        """trg_pred_snap_locked_provenance_immutable 已被 migration 0007(2026-08-05)
        移除——锁定快照的 kickoff provenance 现在也允许直接编辑,与其它实质字段
        一致(见 test_migrations.py::test_locked_snapshot_editable_but_not_deletable_at_db_layer)。"""
        sid = _insert_snapshot(platform, match_id=1, kickoff="2099-01-01T14:00:00Z",
                               precision="exact", source="fotmob:fixtures",
                               status="locked", is_official=1, locked=True)
        platform.execute("UPDATE prediction_snapshots SET kickoff_precision='date_only' WHERE id=?",
                         (sid,))
        row = platform.execute("SELECT kickoff_precision FROM prediction_snapshots WHERE id=?", (sid,)).fetchone()
        assert row["kickoff_precision"] == "date_only"


class TestRepairTool:
    def test_dry_run_changes_nothing(self, platform):
        _insert_snapshot(platform, match_id=1, kickoff="2025-08-15T00:00:00Z", precision="unknown")
        r = repair(platform, dry_run=True)
        assert r["candidates"] == 1 and r["repaired"] == 0
        row = platform.execute("SELECT kickoff_precision, kickoff_at_utc FROM prediction_snapshots").fetchone()
        assert row["kickoff_precision"] == "unknown"    # 未改动
        assert row["kickoff_at_utc"] == "2025-08-15T00:00:00Z"

    def test_repair_reclassifies_and_idempotent(self, platform):
        _insert_snapshot(platform, match_id=1, kickoff="2025-08-15T00:00:00Z", precision="unknown")
        r1 = repair(platform, dry_run=False)
        assert r1["repaired"] == 1
        row = platform.execute("SELECT kickoff_at_utc, kickoff_precision, kickoff_source"
                               " FROM prediction_snapshots").fetchone()
        assert row["kickoff_at_utc"] is None
        assert row["kickoff_precision"] == "date_only"
        assert "gold_wdl_predictions" in row["kickoff_source"]
        # 幂等:重跑无候选
        r2 = repair(platform, dry_run=False)
        assert r2["candidates"] == 0 and r2["repaired"] == 0

    def test_repair_never_touches_locked_or_official(self, platform):
        # 一条已锁定 official 的午夜占位(理论上不该存在,但即便存在也不能被修复工具改)
        locked = _insert_snapshot(platform, match_id=1, kickoff="2025-08-15T00:00:00Z",
                                  precision="exact", source="fotmob:fixtures",
                                  status="locked", is_official=1, locked=True)
        # 一条未锁定占位(应被修复)
        draft = _insert_snapshot(platform, match_id=2, kickoff="2025-08-16T00:00:00Z", precision="unknown")
        before = _official_locked(platform)
        targets = {t["id"] for t in find_targets(platform)}
        assert draft in targets and locked not in targets
        r = repair(platform, dry_run=False)
        assert r["repaired"] == 1
        assert _official_locked(platform) == before      # official/locked 集合不变
        # 锁定行原样保留
        row = platform.execute("SELECT kickoff_at_utc, kickoff_precision FROM prediction_snapshots"
                               " WHERE id=?", (locked,)).fetchone()
        assert row["kickoff_at_utc"] == "2025-08-15T00:00:00Z" and row["kickoff_precision"] == "exact"

    def test_repair_official_locked_count_unchanged(self, platform):
        for i in range(3):
            _insert_snapshot(platform, match_id=100 + i, kickoff="2025-08-15T00:00:00Z", precision="unknown")
        _insert_snapshot(platform, match_id=200, kickoff="2099-01-01T14:00:00Z",
                         precision="exact", source="fotmob:fixtures",
                         status="locked", is_official=1, locked=True)
        before = _official_locked(platform)
        repair(platform, dry_run=False)
        assert _official_locked(platform) == before == 1


class TestImportRepairIdempotent:
    def test_import_then_repair_idempotent(self, data_dir):
        """§6.1.12:导入 + 修复命令幂等(端到端小样本)。"""
        from backend.cli.import_gold_predictions import import_gold

        core = connect_rw("core")
        # core 已由 migration 建骨架 dim_match(含 provenance 列);gold 表是 legacy init_db 表,测试自建
        core.execute("CREATE TABLE IF NOT EXISTS gold_wdl_predictions ("
                     " match_id INTEGER PRIMARY KEY, league_id INTEGER, season TEXT,"
                     " lambda_home REAL, lambda_away REAL, p_home REAL, p_draw REAL, p_away REAL,"
                     " calibrated INTEGER, updated_at TEXT, confidence TEXT, reason TEXT)")
        core.execute("INSERT INTO dim_match (Match_ID, Season, League_ID, Date, status, kickoff_precision)"
                     " VALUES (501,'2026/2027',47,'2027-05-01','NotStarted','date_only')")
        core.execute("INSERT INTO gold_wdl_predictions (match_id, league_id, season, lambda_home,"
                     " lambda_away, p_home, p_draw, p_away, calibrated, updated_at)"
                     " VALUES (501,47,'2026/2027',1.4,1.2,0.45,0.28,0.27,1,'2026-07-09 23:40:05')")
        core.commit()
        plat = connect_rw("platform")
        try:
            import_gold(plat, core)          # 导入(不伪造午夜)
            plat.commit()
            # 导入产物无午夜占位
            assert plat.execute("SELECT COUNT(*) FROM prediction_snapshots"
                                " WHERE kickoff_at_utc LIKE '%T00:00:00Z'").fetchone()[0] == 0
            r = repair(plat, dry_run=False)  # 无占位可修
            assert r["repaired"] == 0
            # 幂等重导
            import_gold(plat, core)
            plat.commit()
            assert plat.execute("SELECT COUNT(*) FROM prediction_snapshots").fetchone()[0] == 1
        finally:
            core.close()
            plat.close()


class TestImportNoFabrication:
    def _gold_core(self, data_dir, match_id, kickoff_at_utc, precision, source):
        core = connect_rw("core")
        core.execute("CREATE TABLE IF NOT EXISTS gold_wdl_predictions ("
                     " match_id INTEGER PRIMARY KEY, league_id INTEGER, season TEXT,"
                     " lambda_home REAL, lambda_away REAL, p_home REAL, p_draw REAL, p_away REAL,"
                     " calibrated INTEGER, updated_at TEXT, confidence TEXT, reason TEXT)")
        core.execute(
            "INSERT INTO dim_match (Match_ID, Season, League_ID, Date, status,"
            " kickoff_at_utc, kickoff_precision, kickoff_source)"
            " VALUES (?, '2026/2027', 47, ?, 'NotStarted', ?, ?, ?)",
            (match_id, (kickoff_at_utc or "2027-05-01")[:10], kickoff_at_utc, precision, source),
        )
        core.execute(
            "INSERT INTO gold_wdl_predictions (match_id, league_id, season, lambda_home,"
            " lambda_away, p_home, p_draw, p_away, calibrated, updated_at)"
            " VALUES (?,47,'2026/2027',1.4,1.2,0.45,0.28,0.27,1,'2026-07-09 23:40:05')",
            (match_id,),
        )
        core.commit()
        return core

    def test_exact_claim_without_source_downgrades_to_unknown_honestly(self, data_dir):
        """11. canonical 声称 kickoff_precision='exact' 但 kickoff_source 为空——
        旧代码曾拼造 'core:dim_match' 充当来源以通过精确性检查,这是伪造 provenance。
        新代码必须诚实降级为 unknown,kickoff_at_utc=NULL,来源标注
        'legacy:gold_wdl_predictions:unknown',绝不出现编造的 'core:dim_match'。"""
        from backend.cli.import_gold_predictions import import_gold

        core = self._gold_core(data_dir, 601, "2027-05-01T14:00:00Z", "exact", None)
        plat = connect_rw("platform")
        try:
            import_gold(plat, core)
            plat.commit()
            row = plat.execute(
                "SELECT kickoff_at_utc, kickoff_precision, kickoff_source"
                " FROM prediction_snapshots WHERE match_id=601"
            ).fetchone()
            assert row["kickoff_at_utc"] is None
            assert row["kickoff_precision"] == "unknown"
            assert row["kickoff_source"] == "legacy:gold_wdl_predictions:unknown"
            assert row["kickoff_source"] != "core:dim_match"
        finally:
            core.close()
            plat.close()

    def test_exact_claim_with_real_source_passes_through_unfabricated(self, data_dir):
        """反例对照:canonical 真的是 exact 且有非空真实来源时,原样透传该来源
        (不覆盖、不替换成占位字符串)。"""
        from backend.cli.import_gold_predictions import import_gold

        core = self._gold_core(data_dir, 602, "2027-05-01T14:00:00Z", "exact", "fotmob:fixtures")
        plat = connect_rw("platform")
        try:
            import_gold(plat, core)
            plat.commit()
            row = plat.execute(
                "SELECT kickoff_at_utc, kickoff_precision, kickoff_source"
                " FROM prediction_snapshots WHERE match_id=602"
            ).fetchone()
            assert row["kickoff_at_utc"] == "2027-05-01T14:00:00Z"
            assert row["kickoff_precision"] == "exact"
            assert row["kickoff_source"] == "fotmob:fixtures"
        finally:
            core.close()
            plat.close()
