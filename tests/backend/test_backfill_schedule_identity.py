"""backend.cli.backfill_schedule_identity 回归测试(Canonical v2 Phase 1)。

覆盖:安全闸门通过/拒绝、dry-run 不写库、commit 真正写入且数量精确匹配
dim_match、幂等(重复运行 0 新增)、绝不触碰 dim_match 本身、不产生任何
state snapshot(身份回填不伪造历史观测)。
"""

from backend.cli import backfill_schedule_identity as cli
from backend.db.connections import connect_ro, connect_rw

from .coreseed import seed_basic_core


def _dim_match_snapshot(data_dir):
    conn = connect_ro("core")
    try:
        rows = conn.execute("SELECT Match_ID FROM dim_match ORDER BY Match_ID").fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


class TestBackfillScheduleIdentity:
    def test_dry_run_does_not_write(self, data_dir):
        seed_basic_core(data_dir)
        before = _dim_match_snapshot(data_dir)

        result = cli.backfill(commit=False)

        assert result["mode"] == "dry-run"
        assert result["audit"]["identity_gate_passed"] is True
        assert result["would_insert"] == len(before)
        assert result["would_skip"] == 0

        conn = connect_ro("core")
        try:
            count = conn.execute("SELECT COUNT(*) FROM schedule_match_identity").fetchone()[0]
        finally:
            conn.close()
        assert count == 0, "dry-run 绝不能写入 schedule_match_identity"
        assert _dim_match_snapshot(data_dir) == before, "dim_match 必须原样未变"

    def test_commit_inserts_exact_count_and_no_state(self, data_dir):
        seed_basic_core(data_dir)
        match_ids = _dim_match_snapshot(data_dir)

        result = cli.backfill(commit=True)

        assert result["mode"] == "commit"
        assert result["inserted"] == len(match_ids)
        assert result["skipped"] == 0

        conn = connect_ro("core")
        try:
            identity_rows = conn.execute(
                "SELECT provider, provider_match_id, canonical_match_id FROM schedule_match_identity"
                " ORDER BY canonical_match_id"
            ).fetchall()
            snapshot_count = conn.execute(
                "SELECT COUNT(*) FROM schedule_match_state_snapshot"
            ).fetchone()[0]
            observation_count = conn.execute(
                "SELECT COUNT(*) FROM schedule_match_observation"
            ).fetchone()[0]
        finally:
            conn.close()

        assert [r["canonical_match_id"] for r in identity_rows] == match_ids
        assert all(r["provider"] == "fotmob" for r in identity_rows)
        assert [int(r["provider_match_id"]) for r in identity_rows] == match_ids
        # 身份回填绝不伪造历史观测:state snapshot/observation 必须保持 0 行
        assert snapshot_count == 0
        assert observation_count == 0
        assert _dim_match_snapshot(data_dir) == match_ids, "dim_match 必须原样未变"

    def test_rerun_is_idempotent(self, data_dir):
        seed_basic_core(data_dir)
        first = cli.backfill(commit=True)
        second = cli.backfill(commit=True)

        assert second["inserted"] == 0
        assert second["skipped"] == first["inserted"]

        conn = connect_ro("core")
        try:
            count = conn.execute("SELECT COUNT(*) FROM schedule_match_identity").fetchone()[0]
        finally:
            conn.close()
        assert count == first["inserted"]

    def test_gate_rejects_when_dim_match_empty(self, data_dir):
        # 未 seed 任何比赛:total=0,闸门必须拒绝,不静默把 0 行当成"成功回填 0 行"
        try:
            cli.backfill(commit=False)
            assert False, "空 dim_match 应当被闸门拒绝(SystemExit),不能静默通过"
        except SystemExit as e:
            assert "拒绝回填" in str(e)

    def test_audit_matches_real_counts(self, data_dir):
        seed_basic_core(data_dir)
        conn = connect_ro("core")
        try:
            audit = cli.audit_dim_match(conn)
        finally:
            conn.close()
        assert audit["identity_gate_passed"] is True
        assert audit["invalid_match_ids"] == 0
        assert audit["total"] == audit["non_null_match_ids"] == audit["unique_match_ids"]
