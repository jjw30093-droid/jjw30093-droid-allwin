"""backend.cli.schema_baseline 回归测试(Canonical v2 Phase 0 安全网)。

覆盖:dump 在临时库上可重复运行且结果稳定、compare 对同一份快照自比零差异、
新增表被识别为 added_tables 而非误判 regression、行数减少与 DDL 变化被
正确判定为硬失败。
"""

from backend.cli import schema_baseline as cli
from backend.db.connections import connect_rw

from .coreseed import seed_basic_core


class TestSchemaBaselineDump:
    def test_dump_all_lists_expected_tables(self, data_dir):
        snapshot = cli.dump_all()
        assert set(snapshot["databases"]) == {"core", "platform", "odds"}
        core_tables = snapshot["databases"]["core"]["tables"]
        assert "dim_match" in core_tables
        assert "schedule_match_identity" in core_tables
        assert core_tables["dim_match"]["row_count"] == 0

    def test_dump_reflects_real_row_counts(self, data_dir):
        seed_basic_core(data_dir)
        snapshot = cli.dump_all()
        assert snapshot["databases"]["core"]["tables"]["dim_match"]["row_count"] == 3

    def test_key_column_null_rates_present_for_dim_match(self, data_dir):
        seed_basic_core(data_dir)
        snapshot = cli.dump_all()
        rates = snapshot["databases"]["core"]["tables"]["dim_match"]["key_column_null_rates"]
        assert set(rates) == {"kickoff_at_utc", "kickoff_precision", "kickoff_source"}
        assert rates["kickoff_at_utc"]["total"] == 3


class TestSchemaBaselineCompare:
    def test_self_compare_is_clean(self, data_dir):
        seed_basic_core(data_dir)
        snapshot = cli.dump_all()
        diff = cli.compare(snapshot, snapshot)
        assert diff == {
            "regressions": [],
            "schema_drifts": [],
            "added_tables": [],
            "null_rate_changes": [],
        }

    def test_row_count_decrease_is_a_regression(self, data_dir):
        seed_basic_core(data_dir)
        old = cli.dump_all()
        conn = connect_rw("core")
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM dim_match WHERE Match_ID=9001")
            conn.execute("COMMIT")
        finally:
            conn.close()
        new = cli.dump_all()
        diff = cli.compare(old, new)
        assert any("dim_match" in r for r in diff["regressions"])
        assert diff["schema_drifts"] == []

    def test_new_table_is_added_not_regression(self, data_dir):
        seed_basic_core(data_dir)
        old = cli.dump_all()
        conn = connect_rw("core")
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("CREATE TABLE canonical_probe (id INTEGER PRIMARY KEY)")
            conn.execute("COMMIT")
        finally:
            conn.close()
        new = cli.dump_all()
        diff = cli.compare(old, new)
        assert diff["regressions"] == []
        assert diff["schema_drifts"] == []
        assert "core.canonical_probe" in diff["added_tables"]

    def test_removed_table_is_a_regression(self, data_dir):
        seed_basic_core(data_dir)
        conn = connect_rw("core")
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("CREATE TABLE to_be_dropped (id INTEGER PRIMARY KEY)")
            conn.execute("COMMIT")
        finally:
            conn.close()
        old = cli.dump_all()
        conn = connect_rw("core")
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DROP TABLE to_be_dropped")
            conn.execute("COMMIT")
        finally:
            conn.close()
        new = cli.dump_all()
        diff = cli.compare(old, new)
        assert any("to_be_dropped" in r and "消失" in r for r in diff["regressions"])
