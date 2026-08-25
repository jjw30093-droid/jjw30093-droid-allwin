"""已完赛比赛精确开球时刻回填(FotMob 官方联赛端点,按分区批量)。

背景:dim_match 的 10,735 场已完赛比赛 kickoff_at_utc 全为 NULL——历史数据入库
(0c16a92, 2026-07-08)早于精确开球改造(cfe0272, 2026-07-20)12 天,而唯一读取
精确 kickoff 的 ingest_future_fixtures.py 又刻意跳过已完赛场次(其 :102 附近的
`if status == "Finish": continue`),所以之后从未补上。

方案:FotMob `api/data/leagues?id=&season=` 对**已完赛**比赛同样返回
`fixtures.allMatches[].status.utcTime`(磁盘上已有的原始响应实证:
Championship 2020-21 完赛 557/557 带 utcTime;PL 2024-25 完赛 380/380 且与
dim_match 零日期不一致)。已完赛比赛按 (League_ID, Season) 正好 30 个分区,
每分区一个请求即可覆盖全部——不是按场次 10,735 个请求。

纪律(CLAUDE.md §6.2.1):
- 只更新 kickoff_at_utc IS NULL 的行,绝不覆盖已有精确时间;
- utcTime 缺失/不可解析 → 保持 NULL,不补 00:00;
- 逐行校验 utcTime 的 UTC 日期与 dim_match.Date 相差 ≤1 天,超出拒绝并记 review;
- provenance 如实:kickoff_precision='exact'、kickoff_source='fotmob:leagues';
- 响应赛季身份校验复用 ingest_future_fixtures._verify_season_identity,
  details.id/selectedSeason 与请求不一致直接拒绝该分区。

用法:
  python -m backend.cli.backfill_kickoff_from_fotmob --dry-run          # 只列分区,不联网
  python -m backend.cli.backfill_kickoff_from_fotmob --live             # 全部分区
  python -m backend.cli.backfill_kickoff_from_fotmob --live --league 47 --season 2020/2021
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import time
from datetime import date as date_cls
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.db.util import normalize_utc_iso


# 2026-08-25 收敛(CLAUDE.md §6.3):赛季回声校验统一到
# backend/ingest/season_identity.py(该模块无脚本式旧导入,可安全包导入——
# 此前"内联同款校验"的理由随之消失)。同名 re-export 兼容既有测试引用。
from backend.ingest.season_identity import (  # noqa: F401 — re-export 兼容
    SeasonIdentityError,
    verify_season_echo as _verify_season_identity,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORE_DB = _REPO_ROOT / "data" / "allwin.db"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def list_partitions(conn: sqlite3.Connection) -> list[dict]:
    """已完赛且 kickoff_at_utc IS NULL 的 (League_ID, Season) 分区及行数。"""
    rows = conn.execute(
        """SELECT League_ID, Season, COUNT(*) AS n
           FROM dim_match
           WHERE status='Finish' AND kickoff_at_utc IS NULL
           GROUP BY League_ID, Season ORDER BY League_ID, Season"""
    ).fetchall()
    return [{"league_id": r[0], "season": r[1], "null_kickoff_rows": r[2]} for r in rows]


def extract_utc_times(payload: dict) -> dict[int, str]:
    """fixtures.allMatches[] → {match_id: kickoff_at_utc};utcTime 缺失/不可解析的跳过。"""
    out: dict[int, str] = {}
    for m in (payload.get("fixtures", {}) or {}).get("allMatches") or []:
        mid = m.get("id")
        utc = (m.get("status", {}) or {}).get("utcTime")
        normalized = normalize_utc_iso(utc)
        if mid is None or normalized is None:
            continue
        try:
            out[int(mid)] = normalized
        except (TypeError, ValueError):
            continue
    return out


def _dates_close(utc_iso: str, dim_date: str | None) -> bool:
    """utcTime 的 UTC 日期与 dim_match.Date 相差 ≤1 天。Date 缺失时放行(无可校验)。"""
    if not dim_date:
        return True
    try:
        a = date_cls.fromisoformat(utc_iso[:10])
        b = date_cls.fromisoformat(str(dim_date)[:10])
    except ValueError:
        return False
    return abs((a - b).days) <= 1


def apply_league_payload(
    conn: sqlite3.Connection,
    payload: dict,
    league_id: int,
    season: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """把一份 league_matches 响应应用到该分区的 NULL kickoff 行。

    返回 {updated, missing_in_payload, date_mismatch_review:[...]};
    dry_run=True 时只统计不写。调用方负责事务与身份校验。
    """
    utc_map = extract_utc_times(payload)
    targets = conn.execute(
        """SELECT Match_ID, Date FROM dim_match
           WHERE status='Finish' AND kickoff_at_utc IS NULL
             AND League_ID=? AND Season=?""",
        (league_id, season),
    ).fetchall()

    updated = 0
    missing = 0
    review: list[dict] = []
    for match_id, dim_date in targets:
        ko = utc_map.get(int(match_id))
        if ko is None:
            missing += 1          # 来源无该场或无 utcTime → 保持 NULL,不补 00:00
            continue
        if not _dates_close(ko, dim_date):
            review.append({
                "match_id": match_id, "league_id": league_id, "season": season,
                "utc_time": ko, "dim_date": dim_date,
                "reason": "utcTime 日期与 dim_match.Date 相差 >1 天",
            })
            continue
        if not dry_run:
            conn.execute(
                """UPDATE dim_match
                   SET kickoff_at_utc=?, kickoff_precision='exact',
                       kickoff_source='fotmob:leagues'
                   WHERE Match_ID=? AND kickoff_at_utc IS NULL""",
                (ko, match_id),
            )
        updated += 1
    return {
        "league_id": league_id,
        "season": season,
        "targets": len(targets),
        "updated": updated,
        "missing_in_payload": missing,
        "date_mismatch_review": review,
    }


def backup_db(db_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_name(f"{db_path.name}.backup-pre-kickoff-backfill-{stamp}")
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(str(backup_path))
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    return backup_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="只列分区计划,不联网不写库")
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_CORE_DB)
    parser.add_argument("--league", type=int, default=None, help="只处理该联赛")
    parser.add_argument("--season", default=None, help='只处理该赛季(如 "2020/2021")')
    parser.add_argument("--skip-backup", action="store_true", help="仅供测试用临时库使用")
    parser.add_argument("--sleep-min", type=float, default=3.0)
    parser.add_argument("--sleep-max", type=float, default=6.0)
    args = parser.parse_args(argv)

    conn = sqlite3.connect(str(args.db_path), timeout=30, isolation_level=None)
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        partitions = list_partitions(conn)
        if args.league is not None:
            partitions = [p for p in partitions if p["league_id"] == args.league]
        if args.season is not None:
            partitions = [p for p in partitions if p["season"] == args.season]

        if args.dry_run:
            print(json.dumps({
                "mode": "DRY_RUN",
                "partition_count": len(partitions),
                "total_null_kickoff_rows": sum(p["null_kickoff_rows"] for p in partitions),
                "partitions": partitions,
            }, indent=2, ensure_ascii=False))
            return 0

        if not args.skip_backup:
            backup_path = backup_db(args.db_path)
            print(json.dumps({"event": "backup_done", "path": str(backup_path)}), flush=True)

        from backend.fotmob_client import FotMobClient  # 延迟导入:dry-run 不需要

        client = FotMobClient()
        reports: list[dict] = []
        failed: list[dict] = []
        for i, part in enumerate(partitions):
            league_id, season = part["league_id"], part["season"]
            try:
                payload = client.league_matches(league_id, season)
                _verify_season_identity(payload, league_id, season)
            except SeasonIdentityError as exc:
                failed.append({"league_id": league_id, "season": season,
                               "error": f"season_identity: {exc}"})
                print(json.dumps(failed[-1], ensure_ascii=False), flush=True)
                continue
            except Exception as exc:  # 网络/解码失败:记录后继续其余分区,可单独重跑
                failed.append({"league_id": league_id, "season": season,
                               "error": f"{exc.__class__.__name__}: {exc}"})
                print(json.dumps(failed[-1], ensure_ascii=False), flush=True)
                continue

            conn.execute("BEGIN")
            try:
                rep = apply_league_payload(conn, payload, league_id, season)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            reports.append(rep)
            print(json.dumps({k: v for k, v in rep.items() if k != "date_mismatch_review"}
                             | {"review_count": len(rep["date_mismatch_review"])},
                             ensure_ascii=False), flush=True)
            if i < len(partitions) - 1:
                time.sleep(random.uniform(args.sleep_min, args.sleep_max))

        all_review = [r for rep in reports for r in rep["date_mismatch_review"]]
        summary = {
            "mode": "LIVE",
            "partitions_processed": len(reports),
            "partitions_failed": failed,
            "total_updated": sum(r["updated"] for r in reports),
            "total_missing_in_payload": sum(r["missing_in_payload"] for r in reports),
            "date_mismatch_review": all_review,
            "finished_at": _utc_now(),
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0 if not failed else 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
