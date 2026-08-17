"""backend/commands/reco_auto_settle.py 测试:自动结算「每日精选」published 单里
已经正式完赛且全部由真实溯源腿(entry_type='provenance_bound')组成的推荐单。

设计原则(CLAUDE.md「宁可 fail-closed,也不能猜一个可能算错的结果」):
- 只结算全部腿都能被 resolve_leg_result() 判定的单——一张单里只要有一条腿
  暂不可判定(比赛未完赛/角球数据缺失/含 legacy_manual 腿),整单本轮都不结算;
- settle_source='manual' 的单永远不覆盖(用真实分叉的人工结果验证:手动结算的
  值必须与"如果自动结算会算出的值"不同,才能证明是真的没被覆盖,不是巧合一致);
- 判定 + 写入包在同一个 tx() 事务里,一张单原子生效;
- 幂等:候选查询天然只挑 reco_legs.result IS NULL 的腿,重复触发不重复结算。
"""

from backend.cli.create_admin import create_admin
from backend.commands import reco as cmd
from backend.commands.reco_auto_settle import run_auto_settle
from backend.db.connections import connect_rw, tx

from .coreseed import insert_match, seed_core_schema


def _admin(conn_platform, suffix="a"):
    return create_admin(conn_platform, f"auto-settle-admin-{suffix}", "auto-settle-pw-1")


def _slip(conn_platform, actor, leg_inputs, slip_date="2026-08-16", title="auto-settle test"):
    with tx(conn_platform):
        sid = cmd.create_slip(
            conn_platform, slip_date=slip_date, title=title, legs=leg_inputs,
            note=None, actor=actor,
        )
        cmd.publish_slip(conn_platform, sid, actor=actor)
    leg_ids = [
        r["id"] for r in conn_platform.execute(
            "SELECT id FROM reco_legs WHERE slip_id=? ORDER BY sort_order", (sid,)
        ).fetchall()
    ]
    return sid, leg_ids


def _provenance_leg(match_id, market, selection, side, *, source_odds=1.9,
                     odds_format="decimal", line=None, snapshot_ref="sr-1",
                     match_desc="X vs Y"):
    return cmd.LegInput(
        match_desc=match_desc, market=market, selection=selection,
        odds=source_odds if odds_format == "decimal" else source_odds + 1.0,
        match_id=match_id, source_odds=source_odds, odds_format=odds_format,
        provider="nowgoal", company_id="8", company_name="Bet365",
        snapshot_ref=snapshot_ref, observed_at="2026-08-15T12:00:00Z",
        line=line, side=side, payload_hash="hash-" + snapshot_ref,
    )


def _slip_row(conn_platform, sid):
    return conn_platform.execute("SELECT * FROM reco_slips WHERE id=?", (sid,)).fetchone()


def _leg_rows(conn_platform, sid):
    return conn_platform.execute(
        "SELECT * FROM reco_legs WHERE slip_id=? ORDER BY sort_order", (sid,)
    ).fetchall()


class TestFullPositivePath:
    def test_all_legs_provenance_bound_and_finished_settles_auto(self, data_dir):
        conn_core = connect_rw("core")
        seed_core_schema(conn_core)
        insert_match(conn_core, 8001, status="Finish", home_score=2, away_score=0)
        insert_match(conn_core, 8002, status="Finish", home_score=0, away_score=1)
        conn_core.commit()

        conn_platform = connect_rw("platform")
        actor = _admin(conn_platform)
        legs = [
            _provenance_leg(8001, "1x2", "主胜", "home", snapshot_ref="sr-8001"),
            _provenance_leg(8002, "1x2", "客胜", "away", snapshot_ref="sr-8002"),
        ]
        sid, leg_ids = _slip(conn_platform, actor, legs)

        out = run_auto_settle(conn_platform, conn_core, actor="system")
        assert out["settled_slips"] == 1
        assert out["skipped_legs"] == 0
        assert out["errors"] == []

        slip = _slip_row(conn_platform, sid)
        assert slip["status"] == "settled"
        assert slip["result"] == "win"
        assert slip["return_units"] == 1.9 * 1.9
        assert slip["settle_source"] == "auto"

        legs_after = _leg_rows(conn_platform, sid)
        assert {l["result"] for l in legs_after} == {"win"}

        logs = conn_platform.execute(
            "SELECT * FROM audit_logs WHERE action='reco.settle' AND target_id=?", (sid,)
        ).fetchall()
        assert len(logs) == 1
        assert logs[0]["actor_type"] == "system"
        assert logs[0]["actor_user_id"] is None


class TestUnfinishedMatchSkipped:
    def test_not_started_or_cancelled_match_leg_skipped_traceably(self, data_dir):
        conn_core = connect_rw("core")
        seed_core_schema(conn_core)
        insert_match(conn_core, 8101, status="NotStarted")
        insert_match(conn_core, 8102, status="Cancelled")
        conn_core.commit()

        conn_platform = connect_rw("platform")
        actor = _admin(conn_platform)

        sids = {}
        for match_id, suffix in ((8101, "ns"), (8102, "cx")):
            legs = [_provenance_leg(match_id, "1x2", "主胜", "home", snapshot_ref=f"sr-{suffix}")]
            sids[match_id], _ = _slip(conn_platform, actor, legs, title=f"未完赛-{suffix}")

        out = run_auto_settle(conn_platform, conn_core, actor="system")
        assert out["settled_slips"] == 0
        assert out["skipped_legs"] == 2
        for match_id in (8101, 8102):
            assert any(str(match_id) in reason for reason in out["skip_reasons"]), out["skip_reasons"]

        for match_id, sid in sids.items():
            slip = _slip_row(conn_platform, sid)
            assert slip["status"] == "published"
            assert slip["settle_source"] is None
            legs_after = _leg_rows(conn_platform, sid)
            assert all(l["result"] is None for l in legs_after)


class TestManualSettleNeverOverwritten:
    """settle_slip() 把 status='settled' 与 settle_source='manual' 绑在同一条
    UPDATE 里原子写入(见 backend/commands/reco.py),因此人工结算过的单必然
    status != 'published' 且全部腿 result 非 NULL——run_auto_settle 第一步的
    候选查询(status='published' AND result IS NULL)已经天然、结构性地永远
    不会再摸到这张单,这是比"跑到 settle_source 检查再跳过"更强的保证(根本
    不会被选中,不存在"看到了但决定不碰"的窗口)。"""

    def test_manual_settle_result_untouched_even_if_auto_would_differ(self, data_dir):
        conn_core = connect_rw("core")
        seed_core_schema(conn_core)
        # 主队 2-0 大胜:如果走自动判定,两条腿都应该是 home win -> "win"。
        insert_match(conn_core, 8201, status="Finish", home_score=2, away_score=0)
        insert_match(conn_core, 8202, status="Finish", home_score=2, away_score=0)
        conn_core.commit()

        conn_platform = connect_rw("platform")
        actor = _admin(conn_platform)
        legs = [
            _provenance_leg(8201, "1x2", "主胜", "home", snapshot_ref="sr-8201"),
            _provenance_leg(8202, "1x2", "主胜", "home", snapshot_ref="sr-8202"),
        ]
        sid, leg_ids = _slip(conn_platform, actor, legs)

        # 人工故意结算成与真实比分相反的结果(制造与自动判定的真实分叉,
        # 证明"不覆盖"不是巧合一致)。
        with tx(conn_platform):
            manual_out = cmd.settle_slip(
                conn_platform, sid, {leg_ids[0]: "lose", leg_ids[1]: "lose"}, actor=actor,
            )
        assert manual_out["result"] == "lose"
        assert manual_out["return_units"] == 0.0

        out = run_auto_settle(conn_platform, conn_core, actor="system")
        assert out["settled_slips"] == 0
        assert out["skipped_legs"] == 0     # 已结算单不再是候选,不计入"待处理"
        assert out["skip_reasons"] == []    # 已结算单不会被摸到,自然没有跳过原因

        slip = _slip_row(conn_platform, sid)
        assert slip["status"] == "settled"
        assert slip["settle_source"] == "manual"
        assert slip["result"] == "lose"
        assert slip["return_units"] == 0.0
        legs_after = _leg_rows(conn_platform, sid)
        assert {l["result"] for l in legs_after} == {"lose"}

        logs = conn_platform.execute(
            "SELECT * FROM audit_logs WHERE action='reco.settle' AND target_id=?", (sid,)
        ).fetchall()
        assert len(logs) == 1   # 自动任务没有再写一条 reco.settle

    def test_settle_source_manual_guard_skips_even_if_reached(self, data_dir):
        """防御性分支的直接单测:即便一张 published 单以某种异常方式带上了
        settle_source='manual'(正常业务流程不会产生这种组合,见上方类
        docstring),run_auto_settle 的显式 settle_source 检查也必须拒绝碰它,
        不依赖"根本不会被选中"这一层结构性保证单独兜底。"""
        conn_core = connect_rw("core")
        seed_core_schema(conn_core)
        insert_match(conn_core, 8203, status="Finish", home_score=1, away_score=0)
        conn_core.commit()

        conn_platform = connect_rw("platform")
        actor = _admin(conn_platform)
        legs = [_provenance_leg(8203, "1x2", "主胜", "home", snapshot_ref="sr-8203")]
        sid, leg_ids = _slip(conn_platform, actor, legs)
        with tx(conn_platform):
            conn_platform.execute(
                "UPDATE reco_slips SET settle_source='manual' WHERE id=?", (sid,)
            )

        out = run_auto_settle(conn_platform, conn_core, actor="system")
        assert out["settled_slips"] == 0
        assert any("manual" in r or "人工" in r for r in out["skip_reasons"]), out["skip_reasons"]

        slip = _slip_row(conn_platform, sid)
        assert slip["status"] == "published"
        assert slip["result"] is None
        legs_after = _leg_rows(conn_platform, sid)
        assert all(l["result"] is None for l in legs_after)


class TestMixedLegacyManualSkipsWholeSlip:
    def test_slip_with_legacy_manual_leg_not_auto_settled(self, data_dir):
        conn_core = connect_rw("core")
        seed_core_schema(conn_core)
        insert_match(conn_core, 8301, status="Finish", home_score=1, away_score=0)
        conn_core.commit()

        conn_platform = connect_rw("platform")
        actor = _admin(conn_platform)
        legs = [
            _provenance_leg(8301, "1x2", "主胜", "home", snapshot_ref="sr-8301"),
            cmd.LegInput(match_desc="站外赛事 A vs B", market="1x2", selection="主胜", odds=2.1),
        ]
        sid, leg_ids = _slip(conn_platform, actor, legs)

        out = run_auto_settle(conn_platform, conn_core, actor="system")
        assert out["settled_slips"] == 0
        assert any("legacy_manual" in r for r in out["skip_reasons"]), out["skip_reasons"]

        slip = _slip_row(conn_platform, sid)
        assert slip["status"] == "published"
        legs_after = _leg_rows(conn_platform, sid)
        assert all(l["result"] is None for l in legs_after)


class TestIdempotentRerun:
    def test_second_run_settles_nothing_new(self, data_dir):
        conn_core = connect_rw("core")
        seed_core_schema(conn_core)
        insert_match(conn_core, 8401, status="Finish", home_score=3, away_score=1)
        conn_core.commit()

        conn_platform = connect_rw("platform")
        actor = _admin(conn_platform)
        legs = [_provenance_leg(8401, "1x2", "主胜", "home", snapshot_ref="sr-8401")]
        sid, _ = _slip(conn_platform, actor, legs)

        first = run_auto_settle(conn_platform, conn_core, actor="system")
        assert first["settled_slips"] == 1

        second = run_auto_settle(conn_platform, conn_core, actor="system")
        assert second["settled_slips"] == 0
        assert second["skipped_legs"] == 0
        assert second["skip_reasons"] == []

        logs = conn_platform.execute(
            "SELECT * FROM audit_logs WHERE action='reco.settle' AND target_id=?", (sid,)
        ).fetchall()
        assert len(logs) == 1   # 第二轮没有产生重复结算记录

        slip = _slip_row(conn_platform, sid)
        assert slip["status"] == "settled"
        assert slip["settle_source"] == "auto"


class TestPartialLegUnresolvableBlocksWholeSlip:
    """三腿单:两腿(1x2)可判定,一腿(corners_ou)因角球统计缺失不可判定——
    整单本轮不结算,不能因为 2/3 腿能判定就强行给第三腿猜一个结果。"""

    def test_missing_corner_stats_blocks_entire_three_leg_slip(self, data_dir):
        conn_core = connect_rw("core")
        seed_core_schema(conn_core)
        insert_match(conn_core, 8501, status="Finish", home_score=1, away_score=0)
        insert_match(conn_core, 8502, status="Finish", home_score=0, away_score=2)
        # 8503 完赛但故意不写 fact_team_match_stats(角球统计缺失)。
        insert_match(conn_core, 8503, status="Finish", home_score=1, away_score=1)
        conn_core.commit()

        conn_platform = connect_rw("platform")
        actor = _admin(conn_platform)
        legs = [
            _provenance_leg(8501, "1x2", "主胜", "home", snapshot_ref="sr-8501"),
            _provenance_leg(8502, "1x2", "客胜", "away", snapshot_ref="sr-8502"),
            _provenance_leg(8503, "corners_ou", "大9.5", "over", odds_format="hk",
                             source_odds=0.9, line=9.5, snapshot_ref="sr-8503"),
        ]
        sid, leg_ids = _slip(conn_platform, actor, legs)

        out = run_auto_settle(conn_platform, conn_core, actor="system")
        assert out["settled_slips"] == 0
        assert out["skipped_legs"] == 3   # 整单三条腿都算未结算,不是"结算了2条"
        assert any("8503" in r for r in out["skip_reasons"]), out["skip_reasons"]

        slip = _slip_row(conn_platform, sid)
        assert slip["status"] == "published"
        legs_after = _leg_rows(conn_platform, sid)
        # 关键断言:两条本可判定的腿(1x2)也绝不能被单独结算——一张单要么整单
        # 一起结算,要么整单都不结算。
        assert all(l["result"] is None for l in legs_after)
