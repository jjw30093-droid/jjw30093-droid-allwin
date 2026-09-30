"""模拟器参数每日导出:每天北京时间 12:00 导出一次(docs/simulator-launch-plan.md §3.1)。

由 allwin-simparams.timer 每 30 分钟经 worker 任务 simulator_params_export 调用(`--due`),
每次只做"到期判断",真正导出每天最多一次:

  1. 今天(北京日期)的参数已发布 → 不到期;
  2. 北京时间早于 12:00 → 不到期(2026-09-30 站长定:固定中午导出,欧洲晚场——含西甲北京时间凌晨
     三四点开球的场次——此时早已完赛落库;不再"采集完成即导出");
  3. 已到 12:00 → 导出。采集是否完成只用来标注:最近一次 fotmob_incremental_multi 运行成功,且五大联赛
     没有"开球已超 2.5 小时仍未完赛落库"的比赛(与 postmatch 同一判据 league_stale_unresolved_match_ids,
     扣除已耗尽重试的场次)→ trigger=scheduled;否则 trigger=incomplete,meta 列出未就绪场次并发一条 WARN。

判断全部只读(connect_ro);导出本身由 scripts/simulator/export_params.py --publish 完成(同样只读打开数据库),
失败时非零退出 → worker 记 failed → CRITICAL 告警。

用法:
  python -m backend.cli.simulator_params_export --due      # 定时任务
  python -m backend.cli.simulator_params_export --force    # 人工:跳过判断立即导出(trigger=manual)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from backend.db.connections import connect_ro
from backend.db.paths import data_dir
from backend.ingest.poll_windows import league_stale_unresolved_match_ids
from backend.ingest.postmatch_retry import exhausted_match_ids

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPORT_SCRIPT = PROJECT_ROOT / "scripts" / "simulator" / "export_params.py"
# 与 scripts/simulator/params_core.py::CALIBRATED_LEAGUES 一致(tests/backend/test_simulator_params_export.py 断言)
CALIBRATED_LEAGUES = (47, 87, 55, 54, 53)
BJ = timezone(timedelta(hours=8))
EXPORT_AT = time(12, 0)
COLLECTION_JOB = "fotmob_incremental_multi"
DEFAULT_OUT_DIR = "/opt/allwin/shared/exports/simulator/daily"
DEFAULT_KEEP = 7


def published_name(now_utc: datetime) -> str:
    return f"simulator_params_{now_utc.astimezone(BJ).strftime('%Y%m%d')}.json"


def decide(now_utc: datetime, published_today: bool, last_collection: dict | None, unresolved: list[int]) -> dict:
    """纯函数:到期判断。返回 {"due": bool, "trigger"/"reason": ..., ...}。"""
    bj = now_utc.astimezone(BJ)
    base = {"checked_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "beijing_time": bj.strftime("%Y-%m-%d %H:%M"),
            "last_collection_run": last_collection, "unresolved_match_ids": sorted(unresolved)}
    if published_today:
        return {"due": False, "reason": "today_published", **base}
    if bj.time() < EXPORT_AT:
        return {"due": False, "reason": "before_window", **base}
    collection_ok = bool(last_collection) and last_collection.get("status") == "succeeded"
    return {"due": True, "trigger": "scheduled" if collection_ok and not unresolved else "incomplete", **base}


def last_collection_run() -> dict | None:
    conn = connect_ro("platform")
    try:
        row = conn.execute(
            "SELECT id, status, started_at, finished_at FROM job_runs WHERE job_name=? "
            "ORDER BY started_at DESC LIMIT 1",
            (COLLECTION_JOB,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def unresolved_matches(now_iso: str) -> list[int]:
    core = connect_ro("core")
    odds = connect_ro("odds")
    try:
        out: set[int] = set()
        for lid in CALIBRATED_LEAGUES:
            out |= league_stale_unresolved_match_ids(core, lid, now_iso) - exhausted_match_ids(odds, lid)
        return sorted(out)
    finally:
        core.close()
        odds.close()


def run_export(out_dir: Path, keep: int, trigger: dict) -> int:
    argv = [sys.executable, str(EXPORT_SCRIPT), "--data-dir", str(data_dir()), "--out-dir", str(out_dir),
            "--publish", "--keep", str(keep), "--trigger-json", json.dumps(trigger, ensure_ascii=False)]
    return subprocess.run(argv, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="模拟器参数每日导出(每天北京时间 12:00)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--due", action="store_true", help="到期判断,到期才导出(定时任务)")
    mode.add_argument("--force", action="store_true", help="跳过判断立即导出(人工)")
    ap.add_argument("--out-dir", default=os.environ.get("SIMULATOR_PARAMS_DIR") or DEFAULT_OUT_DIR)
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    out_dir = Path(args.out_dir)
    if args.force:
        decision = {"due": True, "trigger": "manual", "checked_at": now_iso}
    else:
        decision = decide(now, (out_dir / published_name(now)).exists(), last_collection_run(), unresolved_matches(now_iso))
    print(json.dumps(decision, ensure_ascii=False, default=str))
    if not decision["due"]:
        return 0
    trigger = {k: v for k, v in decision.items() if k != "due"}
    rc = run_export(out_dir, args.keep, trigger)
    if rc == 0 and decision.get("trigger") == "incomplete":
        from backend import notify

        notify.notify(
            level="WARNING",
            source="simulator_params_export",
            title="模拟器参数:12:00 导出时仍有未完赛落库的比赛",
            body=f"未就绪场次:{decision['unresolved_match_ids']}\n最近一次采集:{decision['last_collection_run']}",
            dedup_key=f"simulator_params_export:incomplete:{now.astimezone(BJ).strftime('%Y-%m-%d')}",
        )
    return rc


if __name__ == "__main__":
    sys.exit(main())
