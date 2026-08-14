"""backend.cli.purge_unusable_predictions 回归测试。

覆盖:安全闸门在检测到 is_official=1 行时拒绝执行、dry-run 不写库、
commit 只删 is_official=0 行且数量精确、正式(locked+official)快照物理
删除仍被数据库触发器拒绝(CLAUDE.md §9.1 红线的双重保护)、
prediction_outcomes 不受影响。
"""

import sqlite3

import pytest

from backend.cli import purge_unusable_predictions as cli
from backend.commands.predictions import (
    get_or_create_model_version,
    lock_snapshot,
    publish_snapshot,
    register_snapshot,
)
from backend.db.connections import connect_ro, connect_rw


def _seed_unofficial(conn, n_draft=2, n_legacy=1):
    mv = get_or_create_model_version(conn, "m-purge-test", "dixon-coles")
    for i in range(n_draft):
        register_snapshot(
            conn, match_id=9000 + i, kickoff_at_utc="2099-01-01T00:00:00Z",
            kickoff_precision="exact", kickoff_source="test",
            model_version_id=mv, home_win=0.4, draw=0.3, away_win=0.3,
            status="draft",
        )
    for i in range(n_legacy):
        register_snapshot(
            conn, match_id=9100 + i, kickoff_at_utc="2099-01-01T00:00:00Z",
            kickoff_precision="exact", kickoff_source="test",
            model_version_id=mv, home_win=0.4, draw=0.3, away_win=0.3,
            status="legacy_unverified",
        )
    return mv


def _seed_official_locked(conn):
    mv = get_or_create_model_version(conn, "m-purge-official", "dixon-coles")
    sid = register_snapshot(
        conn, match_id=9200, kickoff_at_utc="2099-01-01T00:00:00Z",
        kickoff_precision="exact", kickoff_source="test",
        model_version_id=mv, home_win=0.5, draw=0.3, away_win=0.2,
        status="draft",
    )
    publish_snapshot(conn, sid, actor=None)
    lock_snapshot(conn, sid, actor=None)
    return sid


class TestPurgeUnusablePredictions:
    def test_dry_run_reports_counts_without_writing(self, data_dir):
        conn = connect_rw("platform")
        try:
            _seed_unofficial(conn, n_draft=2, n_legacy=1)
        finally:
            conn.close()

        result = cli.purge(commit=False)

        assert result["mode"] == "dry-run"
        assert result["audit"]["official_count"] == 0
        assert result["would_delete"] == 3

        conn = connect_ro("platform")
        try:
            count = conn.execute("SELECT COUNT(*) FROM prediction_snapshots").fetchone()[0]
        finally:
            conn.close()
        assert count == 3, "dry-run 绝不能删除任何行"

    def test_commit_deletes_unofficial_rows(self, data_dir):
        conn = connect_rw("platform")
        try:
            _seed_unofficial(conn, n_draft=2, n_legacy=1)
        finally:
            conn.close()

        result = cli.purge(commit=True)

        assert result["mode"] == "commit"
        assert result["deleted"] == 3

        conn = connect_ro("platform")
        try:
            count = conn.execute("SELECT COUNT(*) FROM prediction_snapshots").fetchone()[0]
        finally:
            conn.close()
        assert count == 0

    def test_commit_is_idempotent(self, data_dir):
        conn = connect_rw("platform")
        try:
            _seed_unofficial(conn, n_draft=1, n_legacy=1)
        finally:
            conn.close()

        first = cli.purge(commit=True)
        assert first["deleted"] == 2
        second = cli.purge(commit=True)
        assert second["deleted"] == 0

    def test_safety_gate_aborts_entirely_when_any_official_row_exists(self, data_dir):
        """本脚本是"确认当前全表都是垃圾数据"的一次性清理动作,不是通用的
        "只删非官方行"过滤器——库里只要存在任何 is_official=1 的行(哪怕
        与待删的非官方行完全无关),就整体中止、一行都不删,交给
        retract/supersede 那套流程处理正式样本(CLAUDE.md §9.1)。"""
        conn = connect_rw("platform")
        try:
            _seed_unofficial(conn, n_draft=1, n_legacy=0)
        finally:
            conn.close()
        _seed_official_locked(connect_rw("platform"))

        with pytest.raises(RuntimeError, match="正式预测"):
            cli.purge(commit=True)

        conn = connect_ro("platform")
        try:
            count = conn.execute("SELECT COUNT(*) FROM prediction_snapshots").fetchone()[0]
        finally:
            conn.close()
        assert count == 2, "中止时一行都不能删,包括非官方行"

    def test_locked_official_snapshot_cannot_be_physically_deleted(self, data_dir):
        """数据库触发器红线(trg_pred_snap_no_delete)本身——即使有人绕过
        本脚本直接手写 DELETE,锁定的正式样本依然物理删不掉。"""
        conn = connect_rw("platform")
        official_sid = _seed_official_locked(conn)
        conn.close()

        conn = connect_rw("platform")
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "DELETE FROM prediction_snapshots WHERE id=?", (official_sid,)
                )
        finally:
            conn.close()

    def test_prediction_outcomes_untouched(self, data_dir):
        """prediction_outcomes 是真实赛果(不依赖 snapshot 的 FK),
        清理预测快照不得影响它。"""
        conn = connect_rw("platform")
        try:
            _seed_unofficial(conn, n_draft=1, n_legacy=0)
            conn.execute(
                "INSERT INTO prediction_outcomes"
                " (match_id, home_goals, away_goals, outcome, settled_at, source)"
                " VALUES (9000, 2, 1, 'home', '2026-01-01T00:00:00Z', 'fotmob')"
            )
            conn.commit()
        finally:
            conn.close()

        cli.purge(commit=True)

        conn = connect_ro("platform")
        try:
            count = conn.execute("SELECT COUNT(*) FROM prediction_outcomes").fetchone()[0]
        finally:
            conn.close()
        assert count == 1
