"""按 match_id 强制重新全量落库(2026-08-24)。

背景:全仓此前没有任何"给定一批 match_id,强制重新跑一遍 ingest_match"的
CLI——`backend/cli/backfill_*.py` 只覆盖 kickoff/schedule identity/season
table 这几个窄字段,真正的比赛级明细回填只能靠手动 `python -c` 单个调用
`ingest.ingest_match.ingest_match(...)`,或者临时脚本。这次修 InPlay 永久
零数据的 bug(见 backend/scheduler.py 顶部同日期注释)时需要把已经卡住的
21 场比赛捞回来,顺手把这个能力做成一个可复用的正式工具,而不是再写一个
用完就扔的脚本。

`ingest_match` 本身是幂等的(先 DELETE 该 match_id 在各事实表的旧行,再整批
重新 INSERT),所以对已经是 Finish 的比赛重跑一次也是安全的——不会产生
重复行,只是把 shots/player_stats/team_stats/events/lineup/momentum 全部
按当前 FotMob 页面重新抓一遍。

用法:
  python -m backend.cli.reingest_matches --match-ids 5795371,5868022   # dry-run
  python -m backend.cli.reingest_matches --match-ids 5795371 --commit   # 真正执行

League_ID/Season 从 core 库现有的 dim_match 行读取(与 backend/scheduler.py
调用 ingest_match 时的参数来源一致),不需要手动指定;不在库里的 match_id
会如实报告 skipped_not_in_db,不猜。
"""

from __future__ import annotations

import argparse
import sys

from backend.db.connections import connect_ro
from backend.ingest.ingest_match import ingest_match


def _lookup(match_ids: list[int]) -> list[dict]:
    conn = connect_ro("core")
    try:
        targets = []
        for mid in match_ids:
            row = conn.execute(
                "SELECT Match_ID, League_ID, Season, status, Home_Team_Name,"
                " Away_Team_Name, kickoff_at_utc FROM dim_match WHERE Match_ID=?",
                (mid,),
            ).fetchone()
            if row is None:
                targets.append({"match_id": mid, "found": False})
                continue
            targets.append({
                "match_id": mid,
                "found": True,
                "league_id": row["League_ID"],
                "season": row["Season"],
                "status_before": row["status"],
                "teams": f"{row['Home_Team_Name']} vs {row['Away_Team_Name']}",
                "kickoff_at_utc": row["kickoff_at_utc"],
            })
        return targets
    finally:
        conn.close()


def reingest(match_ids: list[int], commit: bool = False) -> dict:
    targets = _lookup(match_ids)
    if not commit:
        return {"mode": "dry-run", "targets": targets}

    results = []
    # 每场独立 try/except,不因一场失败中止整批——这是手动补采工具,操作者
    # 要看到全部结果自己判断,不是 scheduler.py 那种链式任务的"遇错即停"。
    for t in targets:
        if not t["found"]:
            results.append({**t, "result": "skipped_not_in_db"})
            continue
        try:
            ingest_match(t["match_id"], league_id=t["league_id"], season=t["season"])
            results.append({**t, "result": "ok"})
        except Exception as exc:  # noqa: BLE001 — 手动补采工具:单场失败要如实报告,不中止批次
            results.append({**t, "result": f"failed: {type(exc).__name__}: {exc}"})
    return {"mode": "commit", "results": results}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--match-ids", required=True, help="逗号分隔的 Match_ID 列表")
    ap.add_argument("--commit", action="store_true", help="真正执行(默认 dry-run 不写库)")
    args = ap.parse_args(argv)

    match_ids = [int(x.strip()) for x in args.match_ids.split(",") if x.strip()]
    result = reingest(match_ids, commit=args.commit)

    tag = "[dry-run] " if result["mode"] == "dry-run" else ""
    print(f"{tag}reingest_matches: {len(match_ids)} 个 match_id")
    for row in result.get("targets", result.get("results", [])):
        if not row.get("found"):
            print(f"  match_id={row['match_id']}: 不在 core 库,跳过")
            continue
        extra = f" -> {row['result']}" if "result" in row else ""
        print(f"  match_id={row['match_id']} ({row['teams']}, league={row['league_id']},"
              f" season={row['season']}, status_before={row['status_before']},"
              f" kickoff={row['kickoff_at_utc']}){extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
