"""Worker 接入「每日精选」自动结算(P2.D):验证 job_name='reco_auto_settle'
真实注册进 REGISTRY/DEFAULT_CHAIN,且 `_job_reco_auto_settle()` 正确开关
platform/core 连接、调用 backend.commands.reco_auto_settle.run_auto_settle()、
把统计写进 job_runs.output_count/meta_json——只验证"接线"这一层,不重新实现
结算算术(那是 backend/commands/reco_settlement_math.py 的职责,见
tests/backend/test_reco_auto_settle.py)。

与 postmatch_settle(模型预测评估)彻底独立:不同 job_name、不同注册函数、
不共享连接生命周期(CLAUDE.md §9.1 人工推荐板块与模型预测登记簿分开)。
"""

from backend.commands import reco as cmd
from backend.db.connections import connect_rw, tx
from backend.worker import runner

from .coreseed import insert_match, seed_core_schema


def _admin(conn_platform):
    from backend.cli.create_admin import create_admin

    return create_admin(conn_platform, "worker-reco-settle-admin", "worker-reco-settle-pw-1")


def _published_provenance_slip(conn_platform, actor, match_id, snapshot_ref):
    leg = cmd.LegInput(
        match_desc="X vs Y", market="1x2", selection="主胜", side="home",
        odds=1.9, match_id=match_id, source_odds=1.9, odds_format="decimal",
        provider="nowgoal", company_id="8", company_name="Bet365",
        snapshot_ref=snapshot_ref, observed_at="2026-08-15T12:00:00Z",
        payload_hash="hash-" + snapshot_ref,
    )
    with tx(conn_platform):
        sid = cmd.create_slip(
            conn_platform, slip_date="2026-08-16", title="worker-integration",
            legs=[leg], note=None, actor=actor,
        )
        cmd.publish_slip(conn_platform, sid, actor=actor)
    return sid


class TestJobRegistration:
    def test_reco_auto_settle_has_own_job_name_and_fn(self):
        """独立注册(2026-08-25:曾经对照的 postmatch_settle/model_predict 已
        随 WDL 模型与正式预测登记簿一并删除,reco_auto_settle 本身的注册
        不受影响)。"""
        assert "reco_auto_settle" in runner.REGISTRY
        assert "reco_auto_settle" in runner.DEFAULT_CHAIN
        spec = runner.REGISTRY["reco_auto_settle"]
        assert spec.get("kind") == "fn"
        assert callable(spec.get("fn"))


class TestJobFunctionEndToEnd:
    def test_run_job_settles_finished_slip_via_registry(self, data_dir):
        conn_core = connect_rw("core")
        seed_core_schema(conn_core)
        insert_match(conn_core, 9401, status="Finish", home_score=2, away_score=0)
        conn_core.commit()
        conn_core.close()

        conn_platform = connect_rw("platform")
        actor = _admin(conn_platform)
        sid = _published_provenance_slip(conn_platform, actor, 9401, "sr-9401")
        conn_platform.close()

        res = runner.run_job("reco_auto_settle")
        assert res["status"] == "succeeded", res
        assert res["output_count"] == 1
        assert res["meta"]["settled_slips"] == 1
        assert res["meta"]["errors"] == []

        conn_check = connect_rw("platform")
        try:
            slip = conn_check.execute(
                "SELECT status, result, settle_source FROM reco_slips WHERE id=?", (sid,)
            ).fetchone()
        finally:
            conn_check.close()
        assert slip["status"] == "settled"
        assert slip["result"] == "win"
        assert slip["settle_source"] == "auto"

    def test_run_job_skips_unfinished_match_without_error(self, data_dir):
        conn_core = connect_rw("core")
        seed_core_schema(conn_core)
        insert_match(conn_core, 9402, status="NotStarted")
        conn_core.commit()
        conn_core.close()

        conn_platform = connect_rw("platform")
        actor = _admin(conn_platform)
        sid = _published_provenance_slip(conn_platform, actor, 9402, "sr-9402")
        conn_platform.close()

        res = runner.run_job("reco_auto_settle")
        assert res["status"] == "succeeded", res
        assert res["output_count"] == 0
        assert res["meta"]["settled_slips"] == 0
        assert any(str(9402) in r for r in res["meta"]["skip_reasons"])

        conn_check = connect_rw("platform")
        try:
            slip = conn_check.execute(
                "SELECT status FROM reco_slips WHERE id=?", (sid,)
            ).fetchone()
        finally:
            conn_check.close()
        assert slip["status"] == "published"
