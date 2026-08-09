"""One-command fresh/replay/due workflow for daily active-league content."""

from __future__ import annotations

import argparse
import json

from backend.active_league_mvp import ActiveLeagueMVPError
from backend.content_pipeline import (
    LEAGUES,
    DailyContentError,
    due_status,
    load_runtime_config,
    mark_fresh_failure,
    run_fresh,
    run_replay,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="同步真实联赛数据并生成每日内容素材")
    parser.add_argument("--league", choices=tuple(LEAGUES), default="eliteserien")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fresh", action="store_true", help="从 provider 获取最新数据")
    mode.add_argument("--replay", action="store_true", help="完全离线重放 latest-success")
    mode.add_argument("--due", action="store_true", help="只检查赔率任务到期状态")
    parser.add_argument("--dry-run", action="store_true", help="只允许与 --due 一起使用")
    parser.add_argument("--match-id", type=int, default=None)
    parser.add_argument(
        "--free-outcome",
        choices=("home", "draw", "away"),
        default="home",
        help="免费接口唯一公开的概率项；默认稳定为 home",
    )
    args = parser.parse_args(argv)
    if args.dry_run and not args.due:
        parser.error("--dry-run 只能与 --due 一起使用")
    if args.due and not args.dry_run:
        parser.error("--due 必须显式搭配 --dry-run，避免误解为会发送请求")

    runtime = load_runtime_config()
    try:
        if args.fresh:
            summary = run_fresh(
                args.league,
                match_id=args.match_id,
                free_outcome=args.free_outcome,
                runtime=runtime,
            )
        elif args.replay:
            summary = run_replay(
                args.league,
                free_outcome=args.free_outcome,
                runtime=runtime,
            )
        else:
            summary = due_status(
                args.league,
                match_id=args.match_id,
                runtime=runtime,
            )
    except (DailyContentError, ActiveLeagueMVPError) as exc:
        if args.fresh:
            status = mark_fresh_failure(
                runtime,
                league_key=args.league,
                failure_type=type(exc).__name__,
            )
            print(
                json.dumps(
                    {
                        "mode": "fresh",
                        "status": "FAILED",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "serving_state": status["state"],
                        "last_success_sync_at": status.get("last_success_sync_at"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(
                json.dumps(
                    {
                        "mode": "replay" if args.replay else "due",
                        "status": "FAILED",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        return 1
    except Exception as exc:  # provider boundary: never print external exception text
        if args.fresh:
            status = mark_fresh_failure(
                runtime,
                league_key=args.league,
                failure_type=type(exc).__name__,
            )
            print(
                json.dumps(
                    {
                        "mode": "fresh",
                        "status": "FAILED",
                        "error_type": type(exc).__name__,
                        "error": "provider acquisition failed safely",
                        "serving_state": status["state"],
                        "last_success_sync_at": status.get("last_success_sync_at"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(
                json.dumps(
                    {
                        "mode": "replay" if args.replay else "due",
                        "status": "FAILED",
                        "error_type": type(exc).__name__,
                        "error": "daily content operation failed safely",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        return 1

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.due:
        print("成功: dry-run 完成，外部请求 0 次")
    else:
        print("成功: 赛程/积分榜/近期数据/赔率/analysis bundle/Studio 素材已刷新")
        print("跳过: 相同赔率快照和相同预测版本按现有幂等规则跳过")
        print(f"下一次到期: {summary.get('next_due_at') or '赛前采集已关闭'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
