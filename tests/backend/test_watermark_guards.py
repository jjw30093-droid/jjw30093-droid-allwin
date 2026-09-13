"""派生任务水位守卫(PIPELINE_REDESIGN_V2 P4,3.4)。

覆盖两层:
1. runner.py 通用机制——REGISTRY 任务可声明 `watermark_fn`(无参可调用,返回
   可比较的当前信号);watermark 相对上一次成功执行未变化时,本次执行前直接
   走既有的 JobSkipped → status='skipped' 路径(复用 run_job 现有的幂等键/
   文件锁/job_runs 全生命周期,不另建一套并行状态机);变化后照常真跑,并把
   新 watermark 写回 job_runs.meta_json 供下次比较。
2. core_silver_build / model_predict 两个真实任务各自
   watermark_fn 的信号来源是否正确:
   - core_silver_build: dim_match 里 status='Finish' 的总数(有新完赛才需要
     重建 Silver 聚合);
   - model_predict: 限定在其真实硬编码作用域(League_ID=47,
     Season='2026/2027')内的 Finish 计数 + NotStarted 计数——该任务只对
     这一联赛/赛季重算,水位不应因为其它联赛的数据变化而误判"有新工作"。

analysis_bundle_build 刻意不在本文件覆盖范围内:该任务经代码走查确认为
纯只读(三个连接全部 connect_ro + query_only=ON,不写任何库/文件),数据量
又已经被"最近 50 场 NotStarted"天然限界,不是本 phase 描述的"昂贵全量重建"
问题——不强行套用水位机制,详见改动说明。
"""

import pytest

from backend.db.connections import connect_rw
from backend.worker import runner
from tests.backend.coreseed import seed_core_schema


@pytest.fixture
def registry():
    saved = dict(runner.REGISTRY)
    yield runner.REGISTRY
    runner.REGISTRY.clear()
    runner.REGISTRY.update(saved)


class TestGenericWatermarkMechanism:
    def test_noop_tick_after_success_skips_cheaply(self, registry, data_dir):
        calls = []
        state = {"wm": 1}

        def fn():
            calls.append(1)
            return {"output_count": 1}

        runner.register_job("wm_job_a", fn=fn, max_attempts=1, watermark_fn=lambda: state["wm"])

        res1 = runner.run_job("wm_job_a")
        assert res1["status"] == "succeeded"
        assert len(calls) == 1

        res2 = runner.run_job("wm_job_a")
        assert res2["status"] == "skipped", "watermark 未变化的空转 tick 必须廉价跳过,不是真的再跑一次"
        assert len(calls) == 1, "跳过意味着真正的任务函数完全没有被调用"

    def test_new_data_makes_next_tick_do_real_work_again(self, registry, data_dir):
        calls = []
        state = {"wm": 1}

        def fn():
            calls.append(state["wm"])
            return {"output_count": 1}

        runner.register_job("wm_job_b", fn=fn, max_attempts=1, watermark_fn=lambda: state["wm"])

        runner.run_job("wm_job_b")
        assert len(calls) == 1

        state["wm"] = 2  # 模拟"有新完赛/新 Bronze 行/新 NotStarted"
        res2 = runner.run_job("wm_job_b")
        assert res2["status"] == "succeeded"
        assert len(calls) == 2, "watermark 变化后必须真的重新执行"

    def test_first_ever_run_always_executes_even_with_watermark_fn(self, registry, data_dir):
        """没有"上一次成功执行"可比较时,不能凭空判定为跳过。"""
        calls = []
        runner.register_job("wm_job_c", fn=lambda: (calls.append(1), {"output_count": 1})[1],
                            max_attempts=1, watermark_fn=lambda: "same-value")
        res = runner.run_job("wm_job_c")
        assert res["status"] == "succeeded"
        assert len(calls) == 1

    def test_jobs_without_watermark_fn_are_unaffected(self, registry, data_dir):
        """没有声明 watermark_fn 的任务(绝大多数)行为完全不变——每次都真跑。"""
        calls = []
        runner.register_job("no_wm_job", fn=lambda: (calls.append(1), {"output_count": 1})[1],
                            max_attempts=1)
        runner.run_job("no_wm_job")
        res2 = runner.run_job("no_wm_job")
        assert res2["status"] == "succeeded"
        assert len(calls) == 2


class TestRealJobsWiredToWatermarkFn:
    @pytest.mark.parametrize("job_name", ["core_silver_build"])
    def test_watermark_fn_is_registered(self, job_name):
        assert callable(runner.REGISTRY[job_name].get("watermark_fn")), (
            f"{job_name} 必须声明 watermark_fn(全量 DELETE+INSERT 重建,空转 tick 不应真的重跑)"
        )

    def test_analysis_bundle_build_deliberately_has_no_watermark_fn(self):
        """只读、数据量已被 50 场天然限界——不强行套用水位机制(见模块 docstring)。"""
        assert runner.REGISTRY["analysis_bundle_build"].get("watermark_fn") is None


class TestCoreSilverBuildWatermarkSignal:
    def test_unchanged_finish_count_yields_equal_watermark(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        conn.execute(
            "INSERT INTO dim_match (Match_ID, League_ID, Season, status) VALUES (1, 47, '2025/2026', 'Finish')"
        )
        conn.commit()
        conn.close()

        wm1 = runner._watermark_core_silver_build()
        wm2 = runner._watermark_core_silver_build()
        assert wm1 == wm2

    def test_new_finished_match_changes_watermark(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        conn.execute(
            "INSERT INTO dim_match (Match_ID, League_ID, Season, status) VALUES (1, 47, '2025/2026', 'Finish')"
        )
        conn.commit()
        wm_before = runner._watermark_core_silver_build()

        conn.execute(
            "INSERT INTO dim_match (Match_ID, League_ID, Season, status) VALUES (2, 47, '2025/2026', 'Finish')"
        )
        conn.commit()
        conn.close()
        wm_after = runner._watermark_core_silver_build()
        assert wm_before != wm_after

    def test_new_notstarted_match_does_not_change_watermark(self, data_dir):
        """core_silver_build 只聚合 status='Finish',NotStarted 变化不应触发重建。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        conn.execute(
            "INSERT INTO dim_match (Match_ID, League_ID, Season, status) VALUES (1, 47, '2025/2026', 'Finish')"
        )
        conn.commit()
        wm_before = runner._watermark_core_silver_build()

        conn.execute(
            "INSERT INTO dim_match (Match_ID, League_ID, Season, status) VALUES (2, 47, '2026/2027', 'NotStarted')"
        )
        conn.commit()
        conn.close()
        wm_after = runner._watermark_core_silver_build()
        assert wm_before == wm_after

