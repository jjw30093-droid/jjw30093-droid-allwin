"""allwin-poll.service 调度包装:确保 nowgoal_snapshot 与 fotmob_snapshot
两个采集任务总是都被尝试,不因为其中一个失败就让另一个被跳过。

背景(真实探测确认,不是假设):systemd 的多条 ExecStart= 顺序执行,一条失败
(非零退出且没有 `-` 前缀)会阻止后续 ExecStart= 执行——如果直接给
nowgoal_snapshot/fotmob_snapshot 各写一条 ExecStart=,NowGoal 失败会导致
FotMob 那一步完全不被尝试,与 allwin-poll.service 自身的注释"两个采集任务
独立执行"矛盾。这里用一个 Python 包装脚本代替两条 ExecStart=:顺序执行两个
任务、汇总最终退出码,而不是简单在 ExecStart 前加 `-` 让 service 假装总是
成功(那样会丢失"确实有一个任务失败了"这个真实信号)。

两个任务各自的真实结果仍然完整写入 job_runs(status/error_summary 互不覆盖),
这里只汇总退出码给 systemd 用于判断 service 本身是否成功。

用法:python -m backend.worker.poll_wrapper
退出码:两个任务都是 succeeded/skipped → 0;任一为 failed/locked → 1。
"""

import sys

from .runner import run_job

JOBS = ("nowgoal_snapshot", "fotmob_snapshot")


def run_all() -> tuple[int, list[dict]]:
    """执行 JOBS 中的全部任务(每个都真实尝试,不因前一个失败而跳过),
    返回 (汇总退出码, 各任务结果列表)。"""
    results = []
    overall_rc = 0
    for job_name in JOBS:
        res = run_job(job_name)
        results.append({"job": job_name, **res})
        if res["status"] not in ("succeeded", "skipped"):
            overall_rc = 1
    return overall_rc, results


def main(argv=None) -> int:
    rc, results = run_all()
    for res in results:
        print(f"[{res['job']}] status={res['status']} error_summary={res.get('error_summary')}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
