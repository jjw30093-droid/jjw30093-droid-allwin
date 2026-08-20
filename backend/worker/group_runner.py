"""PIPELINE_REDESIGN_V2 P3 起,`allwin-postmatch`/`allwin-derive`/
`allwin-maintenance` 三个多任务定时器共用的"任务组顺序执行"包装:组内任务
彼此独立(不是 run_chain() 那种按位置级联跳过的依赖链),某个任务失败不得
阻止组内其余任务被尝试,整体退出码只在组内出现 failed/locked 时非零,
skipped 不计入失败。

前身是只服务 allwin-poll.service(nowgoal_snapshot + fotmob_snapshot 两个
采集任务)的 poll_wrapper.py。拆分七定时器后 nowgoal_snapshot/fotmob_snapshot
各自有了独立的单任务定时器(allwin-odds/allwin-lineup,直接
`runner --job <name>`,不再需要包装),旧文件名已经名不副实,于是改名并把
"顺序执行 N 个独立任务、汇总退出码"这段已经被 tests/backend/
test_poll_scheduling.py 验证过的行为参数化保留,而不是重新发明一套机制。

单任务定时器(allwin-odds/allwin-lineup/allwin-fixtures/allwin-gates)不经过
这个模块,直接 `python -m backend.worker.runner --job <name>`。

用法:python -m backend.worker.group_runner --group postmatch
"""

import argparse
import sys

from .runner import run_job

# 单一真源:四个单任务定时器各自对应哪个 job(见同名 deploy/systemd/allwin-*.
# service)。这里声明只是为了让 tests/backend/test_job_order.py 能验证"7 个
# 新定时器合起来恰好覆盖 DEFAULT_CHAIN 的每个任务一次,不多不少"这条不变量
# ——PERIODIC_CHAIN_EXCLUDE 曾经用排除法在两个定时器之间打补丁想保证的就是
# 这件事,现在每个任务只有一个定时器,不需要排除,只需要证明没有遗漏/重复。
SINGLE_JOB_TIMERS: dict[str, str] = {
    "allwin-odds": "nowgoal_snapshot",
    "allwin-lineup": "fotmob_snapshot",
    "allwin-fixtures": "schedule_sync_multi",
    "allwin-gates": "pipeline_gates",
}

JOB_GROUPS: dict[str, tuple[str, ...]] = {
    "postmatch": (
        "fotmob_incremental_multi",
        "core_silver_build",
        "model_predict",
        "prediction_register",
        "postmatch_settle",
        "reco_auto_settle",
    ),
    "derive": (
        "odds_silver_build",
        "analysis_bundle_build",
    ),
    "maintenance": (
        "entity_resolution",
        "metrics_rebuild",
    ),
}


def run_group(group_name: str) -> tuple[int, list[dict]]:
    """执行 JOB_GROUPS[group_name] 中的全部任务(每个都真实尝试,不因前一个
    失败而跳过),返回 (汇总退出码, 各任务结果列表)。"""
    jobs = JOB_GROUPS[group_name]
    results = []
    overall_rc = 0
    for job_name in jobs:
        res = run_job(job_name)
        results.append({"job": job_name, **res})
        if res["status"] not in ("succeeded", "skipped"):
            overall_rc = 1
    return overall_rc, results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="allwin 任务组顺序执行(组内任务互相独立,一个失败不阻止其余被尝试)"
    )
    ap.add_argument("--group", required=True, choices=sorted(JOB_GROUPS), help="要执行的任务组")
    args = ap.parse_args(argv)
    rc, results = run_group(args.group)
    for res in results:
        print(f"[{res['job']}] status={res['status']} error_summary={res.get('error_summary')}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
