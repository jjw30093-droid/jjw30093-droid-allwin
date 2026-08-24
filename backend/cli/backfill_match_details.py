"""按范围批量回填 dim_match 的 match-details 列(场馆/天气/配色/裁判信息卡)。

背景(2026-08-24 真实事故):--write-match-details 通道早已实现,但
backend/worker/runner.py 注册 fotmob_snapshot 任务的 argv 漏了这个开关,
导致场馆/天气/配色三类数据在 16934 行 dim_match 里非空数为 0/0/1。开关
已修(runner.py + tests/backend/test_worker_argv.py),**新抓的比赛**从此
自动有数据;本 CLI 负责把**历史比赛**按范围补回来——三个库都没有存整份
match_details 原始 payload(bronze_fm_* 只存 lineup/sidelined 裁剪子树),
历史数据只能重新抓取,不能靠重跑解析拿回。

口径:
- 复用 providers/fotmob_snapshots 的 fetch_match_payload +
  extract_prematch_details 与 cli/poll_fotmob_snapshots 的
  _write_match_details(COALESCE 窄 UPDATE,只碰 MATCH_DETAILS_COLUMNS,
  绝不覆盖 status/kickoff/比分)——对已完赛比赛安全,零第二套解析代码。
- 幂等/断点续跑:默认只选目标列全空的行(--only-missing,默认开),跑到
  一半中断后重跑同一条命令会自动跳过已回填的场次,不需要单独的进度文件。
- 节流:请求间 sleep --interval 秒(默认 3),失败不中断整批,结尾汇总。
- dry-run 优先:不带 --commit 只打印将要处理的场次数,不发任何请求。

用法:
  .venv/bin/python -m backend.cli.backfill_match_details --season 2026 --limit 5 --commit
  .venv/bin/python -m backend.cli.backfill_match_details --season 2026/2027 --league 55 --commit
  .venv/bin/python -m backend.cli.backfill_match_details --date-from 2026-08-01 --date-to 2026-08-24 --commit
"""

from __future__ import annotations

import argparse
import sys
import time

from backend.db.connections import connect_ro, connect_rw
from backend.cli.poll_fotmob_snapshots import _write_match_details
from backend.providers.fotmob_snapshots import (
    MATCH_DETAILS_COLUMNS,
    extract_prematch_details,
    fetch_match_payload,
)

# "目标列全空"判据只看这三组代表列——Referee/Temperature 是老基线列(70%/16%
# 已有值),用它们判空会把绝大多数历史场次误判为"已回填"。判据取本次事故
# 真正丢失的三类各一个代表:场馆名 / 配色 / 裁判统计 JSON。
_MISSING_COND = (
    "(Venue_Name IS NULL AND Home_Team_Color_Light IS NULL"
    " AND Referee_Stats_Json IS NULL)"
)


def _select_targets(args) -> list[dict]:
    cond = ["1=1"]
    params: list = []
    if args.season:
        # Season 有 "2026" 与 "2026/2027" 两种真实格式,精确匹配用户给的值,
        # 不做前缀猜测——猜错会把别的赛季也抓进来。
        cond.append("Season = ?")
        params.append(args.season)
    if args.league:
        cond.append("League_ID = ?")
        params.append(args.league)
    if args.date_from:
        cond.append("Date >= ?")
        params.append(args.date_from)
    if args.date_to:
        cond.append("Date <= ?")
        params.append(args.date_to)
    if args.finished_only:
        cond.append("status = 'Finish'")
    if args.only_missing:
        cond.append(_MISSING_COND)
    sql = (
        "SELECT Match_ID, Date, status, Home_Team_Name, Away_Team_Name"
        f" FROM dim_match WHERE {' AND '.join(cond)}"
        " ORDER BY Date DESC, Match_ID"
    )
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    conn = connect_ro("core")
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", help="精确匹配 dim_match.Season(如 2026 或 2026/2027)")
    ap.add_argument("--league", type=int, help="League_ID")
    ap.add_argument("--date-from", help="Date >= (ISO 日期)")
    ap.add_argument("--date-to", help="Date <= (ISO 日期)")
    ap.add_argument("--limit", type=int, help="最多处理场次数")
    ap.add_argument("--interval", type=float, default=3.0, help="请求间隔秒(默认 3)")
    ap.add_argument(
        "--all-status", dest="finished_only", action="store_false", default=True,
        help="默认只回填已完赛(Finish);未完赛比赛由修好的定时任务自动覆盖",
    )
    ap.add_argument(
        "--include-filled", dest="only_missing", action="store_false", default=True,
        help="默认跳过已回填场次(断点续跑);带此开关强制全部重抓",
    )
    ap.add_argument("--commit", action="store_true", help="真正抓取并写库;缺省 dry-run")
    args = ap.parse_args(argv)

    targets = _select_targets(args)
    print(f"[backfill_match_details] 目标场次: {len(targets)}")
    if not targets:
        return 0
    if not args.commit:
        for t in targets[:10]:
            print(f"  {t['Match_ID']} {t['Date']} {t['status']} "
                  f"{t['Home_Team_Name']} vs {t['Away_Team_Name']}")
        if len(targets) > 10:
            print(f"  ... 及另外 {len(targets) - 10} 场")
        print("dry-run:未发任何请求。加 --commit 执行。")
        return 0

    conn_rw = connect_rw("core")
    ok = wrote_nothing = failed = 0
    failures: list[tuple[int, str]] = []
    try:
        for i, t in enumerate(targets):
            mid = int(t["Match_ID"])
            try:
                payload = fetch_match_payload(mid)
                details = extract_prematch_details(payload, mid)
                if _write_match_details(conn_rw, mid, details):
                    ok += 1
                else:
                    # 请求成功但目标字段全空——来源真没有(老比赛 FotMob 页面
                    # 也可能不带 infoBox),如实计数,不算失败也不算成功回填。
                    wrote_nothing += 1
                got = sum(1 for k in MATCH_DETAILS_COLUMNS if details.get(k) is not None)
                print(f"  [{i+1}/{len(targets)}] {mid} → {got}/{len(MATCH_DETAILS_COLUMNS)} 字段非空")
            except Exception as e:  # noqa: BLE001 —— 单场失败不中断整批
                failed += 1
                failures.append((mid, str(e)[:200]))
                print(f"  [{i+1}/{len(targets)}] {mid} FAILED: {e}", file=sys.stderr)
            if i + 1 < len(targets):
                time.sleep(args.interval)
    finally:
        conn_rw.close()

    print(f"[backfill_match_details] 完成: 回填 {ok},来源无数据 {wrote_nothing},失败 {failed}")
    for mid, err in failures:
        print(f"  失败 {mid}: {err}", file=sys.stderr)
    return 1 if failed and not ok else 0


if __name__ == "__main__":
    raise SystemExit(main())
