"""任务链依赖顺序的可执行检查(数据管道重建 Phase 6)。

前身项目事故 #6 的元教训:"文档承诺 ≠ 代码落地"——依赖顺序只写在注释里等于
没有。写成 pytest(而不是 CLI)保证 CI 必跑。
"""

from backend.worker import poll_wrapper, runner


def _idx(name: str) -> int:
    return runner.DEFAULT_CHAIN.index(name)


class TestChainOrder:
    def test_schedule_before_entity_resolution_before_odds_silver(self):
        """写在读之前:赛程先落库,实体解析才有候选;xref 先解析,odds silver 才有产出。"""
        assert _idx("schedule_sync_multi") < _idx("entity_resolution") < _idx("odds_silver_build")

    def test_schedule_before_nowgoal_snapshot(self):
        assert _idx("schedule_sync_multi") < _idx("nowgoal_snapshot")

    def test_postmatch_settle_before_reco_auto_settle(self):
        """「每日精选」推荐单自动结算依赖"比赛已经正式完赛"这个前提,和
        postmatch_settle(模型预测评估)依赖的前提相同,顺序上紧跟其后
        (不需要等 metrics_rebuild);两者是彼此独立的两个任务/两个 job_name,
        不共享同一个注册函数。"""
        assert _idx("postmatch_settle") < _idx("reco_auto_settle")
        assert runner.REGISTRY["reco_auto_settle"]["fn"] is not runner.REGISTRY["postmatch_settle"]["fn"]

    def test_pipeline_gates_is_last(self):
        """质量门必须排最后:否则门 CRITICAL 会 cascade-skip 掉真活。"""
        assert runner.DEFAULT_CHAIN[-1] == "pipeline_gates"

    def test_chain_names_all_registered(self):
        for name in runner.DEFAULT_CHAIN:
            assert name in runner.REGISTRY, f"链上任务 {name} 未注册"


class TestNoOrphanJobs:
    def test_every_registered_job_is_chained_or_whitelisted(self):
        """杜绝"注册了却永远不被调度":每个 REGISTRY 任务要么在链上,
        要么在显式的 NON_CHAIN_JOBS 白名单里(别名/独立 timer 任务)。"""
        orphans = set(runner.REGISTRY) - set(runner.DEFAULT_CHAIN) - runner.NON_CHAIN_JOBS
        assert not orphans, f"孤儿任务(未调度且不在白名单): {orphans}"

    def test_whitelist_entries_are_real(self):
        for name in runner.NON_CHAIN_JOBS:
            assert name in runner.REGISTRY, f"白名单条目 {name} 不存在于 REGISTRY"
            assert name not in runner.DEFAULT_CHAIN, f"{name} 已在链上,不应再进白名单"


class TestPeriodicExcludeInvariant:
    def test_periodic_exclude_equals_pollwrapper_chain_intersection(self):
        """runner.py 注释里的锁竞争不变量,变成可执行断言:
        被 allwin-poll.timer(poll_wrapper)独立调度、又在默认链上的任务,
        恰好就是 --periodic 要排除的集合——多排会漏跑,少排会锁竞争级联 skip。"""
        assert runner.PERIODIC_CHAIN_EXCLUDE == set(poll_wrapper.JOBS) & set(runner.DEFAULT_CHAIN)


class TestJobSpecCompleteness:
    def test_every_chain_job_has_timeout_attempts_description(self):
        for name in runner.DEFAULT_CHAIN:
            spec = runner.REGISTRY[name]
            assert int(spec.get("timeout_seconds", 0)) > 0, f"{name} 缺 timeout_seconds"
            assert int(spec.get("max_attempts", 0)) >= 1, f"{name} 缺 max_attempts"
            assert (spec.get("description") or "").strip(), f"{name} 缺 description"

    def test_no_hardcoded_single_league_in_chain_jobs(self):
        """Phase 6 去硬编码的回归钉:链上任务的 argv 不得再出现 --league-id 47
        或单联赛赛季串(多联赛由 LEAGUE_META/数据库驱动)。"""
        for name in runner.DEFAULT_CHAIN:
            argv = runner.REGISTRY[name].get("argv") or []
            joined = " ".join(str(a) for a in argv)
            assert "--league-id" not in joined, f"{name} 仍硬编码联赛: {joined}"
            assert "2026/2027" not in joined, f"{name} 仍硬编码赛季: {joined}"
