"""联赛积分榜(fact_league_table)迟到刷新:按"最近一场完赛比赛+6h"到期判断
调用 ingest_season_tables() 重新拉取。

纯判断逻辑在 backend/ingest/standings_refresh_poll.py(due_refresh,不做
I/O);本文件是唯一的调用方——负责发现每个候选联赛当前赛季、算出"最近一场
完赛比赛的开球时间",判断是否到期,到期则调用 ingest_season_tables() 触发
真正的整季重抓(DELETE+INSERT,幂等),并落库 standings_refresh_state。

范围与节奏(CLAUDE.md §6.3/§13,站长最终决定,不在此重新推导):
- 只处理 League_ID ∈ STANDINGS_LEAGUE_IDS(当前仅英超 47);
- "赛季"取该联赛当前 dim_match 里最新一场完赛比赛(status='Finish')自身
  的 Season 列——dim_match.Season 已经由 migration 0011 的触发器保证与
  (League_ID, Date) 推导一致,直接读它比重新调用 season_for_match()/
  resolve_current_season() 更直接,也天然避免了"当前赛季"和"最近一场完赛
  比赛所属赛季"在赛季交替边界上可能不一致的问题;
- 最近一场完赛比赛的 kickoff_at_utc + 6 小时到期,到期即调用一次
  ingest_season_tables(),不做检查点式的有限次重试(与 physical_stats_poll
  不同构,那是"有效值检查"语义,这里是纯粹的时间到期语义);
- 状态表 standings_refresh_state 落在 core(allwin.db)——理由见
  backend/migrations/core/0014_standings_refresh_state.sql 头注释。

用法:
  .venv/bin/python -m backend.cli.poll_standings --due
  .venv/bin/python -m backend.cli.poll_standings --due --now 2026-08-26T10:00:00Z
"""

import argparse
import json
import os
import sys

# ingest_league.py 是 script 风格模块(内部 `from db import ...`),不可直接
# 包导入 ingest_season_tables;沿用 backend/cli/backfill_season_tables.py
# 头部同款 sys.path 桥接,不复制其函数体。
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_BACKEND_DIR, os.path.join(_BACKEND_DIR, "ingest")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend.db.connections import connect_rw, tx
from backend.db.util import utc_now_iso
from backend.ingest.standings_refresh_poll import (
    STANDINGS_LEAGUE_IDS,
    due_refresh,
)


def _latest_finished_match(conn, league_id: int):
    """该联赛当前已知最新一场完赛比赛的 (season, kickoff_at_utc);
    没有任何完赛比赛时返回 None。按 Season 分组取每组最大 kickoff,再取
    全局最大的那一组——保证跨赛季边界时选到的是真正最新的一场,而不是
    某个固定字符串赛季下的最新一场。"""
    row = conn.execute(
        """
        SELECT Season AS season, MAX(kickoff_at_utc) AS kickoff_at_utc
        FROM dim_match
        WHERE League_ID = ? AND status = 'Finish' AND kickoff_at_utc IS NOT NULL
        GROUP BY Season
        ORDER BY MAX(kickoff_at_utc) DESC
        LIMIT 1
        """,
        (league_id,),
    ).fetchone()
    if row is None or not row["kickoff_at_utc"]:
        return None
    return {"season": row["season"], "kickoff_at_utc": row["kickoff_at_utc"]}


def _read_state(conn, league_id: int, season: str):
    row = conn.execute(
        "SELECT last_refreshed_at FROM standings_refresh_state"
        " WHERE league_id=? AND season=?",
        (league_id, season),
    ).fetchone()
    return row["last_refreshed_at"] if row else None


def _write_state(conn, league_id: int, season: str, kickoff_at_utc: str, now_iso: str) -> None:
    with tx(conn):
        conn.execute(
            """INSERT INTO standings_refresh_state
                 (league_id, season, last_refreshed_at, last_finished_kickoff_at_utc,
                  created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(league_id, season) DO UPDATE SET
                 last_refreshed_at=excluded.last_refreshed_at,
                 last_finished_kickoff_at_utc=excluded.last_finished_kickoff_at_utc,
                 updated_at=excluded.updated_at""",
            (league_id, season, now_iso, kickoff_at_utc, now_iso, now_iso),
        )


def _process_league(conn, league_id: int, now_iso: str) -> dict:
    latest = _latest_finished_match(conn, league_id)
    if latest is None:
        return {"league_id": league_id, "action": "not_due", "reason": "no_finished_match"}

    season = latest["season"]
    if not season:
        return {
            "league_id": league_id, "action": "skipped",
            "reason": "latest_finished_match_missing_season",
        }

    last_refreshed_at = _read_state(conn, league_id, season)
    decision = due_refresh(
        latest_finished_kickoff_at_utc=latest["kickoff_at_utc"],
        last_refreshed_at=last_refreshed_at,
        now_iso=now_iso,
    )
    if not decision.due:
        return {
            "league_id": league_id, "season": season, "action": "not_due",
            "reason": decision.reason, "due_at": decision.due_at,
        }

    from backend.fotmob_client import FotMobClient
    from ingest_league import ingest_season_tables

    client = FotMobClient()
    ingest_season_tables(client, league_id, season)
    _write_state(conn, league_id, season, latest["kickoff_at_utc"], now_iso)

    return {
        "league_id": league_id, "season": season, "action": "refreshed",
        "due_at": decision.due_at,
        "latest_finished_kickoff_at_utc": latest["kickoff_at_utc"],
    }


def run_due(now_iso: str | None = None) -> dict:
    now_iso = now_iso or utc_now_iso()
    conn = connect_rw("core")
    try:
        results = []
        for league_id in sorted(STANDINGS_LEAGUE_IDS):
            try:
                results.append(_process_league(conn, league_id, now_iso))
            except Exception as exc:  # noqa: BLE001 — 单联赛失败不拖垮整轮
                results.append({
                    "league_id": league_id, "action": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                })
        acted = [r for r in results if r["action"] not in ("not_due", "skipped")]
        return {"leagues": len(results), "acted": len(acted), "results": results}
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--due", action="store_true", help="按到期判断执行(worker 默认调用方式)")
    ap.add_argument("--now", default=None, help="覆盖当前时间(ISO UTC,测试用)")
    args = ap.parse_args(argv)

    if not args.due:
        ap.error("需要 --due")
        return 2

    result = run_due(now_iso=args.now)
    print(json.dumps(result, ensure_ascii=False, default=str))
    if any(r["action"] == "error" for r in result["results"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
