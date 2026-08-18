"""多联赛完赛增量(数据管道重建 Phase 6 去硬编码;到期条件 PIPELINE_REDESIGN_V2 P4)。

取代 worker 旧 `fotmob_incremental` 任务的单联赛硬编码
(`import scheduler; scheduler.step1_ingest_newly_finished(47, '2026/2027')`):
遍历 LEAGUE_META 中**库里真实存在 NotStarted 场次**的 (联赛, 赛季) 组合,
逐组调用同一条 `scheduler.step1_ingest_newly_finished`(与历史赛季完全同一条
全量抓取路径,不另写第二套增量逻辑)。

到期条件(事件驱动 + 6h 兜底,PIPELINE_REDESIGN_V2 P4 3.3,取代旧的纯 6h/
联赛节流):联赛里存在至少一场"该结束却仍未结束"的比赛(status != 'Finish'
且 kickoff + 2.5h < now,见 poll_windows.league_stale_unresolved_match_ids)
即刻到期,不必等满 6h;没有这种比赛时仍保留 poll_state 6h 兜底档,防止某个
联赛长期"永远不 stale"却也从不被刷新。配合 allwin-postmatch 30 分钟一次的
tick,完赛数据最迟约 kickoff+3h 落地。

单比赛重试上限(同一 phase 的安全要求,见 backend/ingest/postmatch_retry.py):
FotMob 侧永远不翻 Finish 的比赛(数据源缺陷)如果不设上限,会在事件驱动
到期下变成永久重试——每次抓完仍未解决的 stale 比赛计数一次,达到上限即
停止让它继续单独触发到期、记录失败原因、告警恰好一次;比赛期间由旧到期
判据发现的其它同联赛 stale 比赛或 6h 兜底档仍会连带覆盖到它。

赛季来源:不硬编码、不推断——只处理 dim_match 里该联赛真实存在的 NotStarted
赛季串(J1 同时有 '2026' 与 '2026/2027' 时两者都查)。库里没有未开赛场次的
联赛没有"待完赛"可言,直接跳过(赛程由 sync_fixtures_window 先行落库)。

用法:
  python -m backend.cli.fotmob_incremental_multi --due        # 到期联赛才抓(worker 默认)
  python -m backend.cli.fotmob_incremental_multi --all        # 无视节流全部
  python -m backend.cli.fotmob_incremental_multi --league-id 47
"""

import argparse
import json
import sys

from backend.db.connections import connect_ro, connect_rw
from backend.db.paths import PROJECT_ROOT
from backend.db.util import new_uuid, utc_now_iso
from backend.ingest import postmatch_retry
from backend.ingest.poll_windows import (
    INCREMENTAL_SYNC_INTERVAL_SECONDS,
    SOURCE_FOTMOB_INCREMENTAL,
    is_due,
    league_stale_unresolved_match_ids,
    mark_polled,
)
from backend.queries.leagues import LEAGUE_META


def _league_season_targets(conn_core) -> dict[int, list[str]]:
    """{league_id: [season, ...]}——只取 LEAGUE_META 联赛里真实有 NotStarted 行的赛季。"""
    rows = conn_core.execute(
        "SELECT DISTINCT League_ID, Season FROM dim_match "
        "WHERE status='NotStarted' AND Season IS NOT NULL"
    ).fetchall()
    targets: dict[int, list[str]] = {}
    for r in rows:
        lid = int(r["League_ID"])
        if lid in LEAGUE_META:
            targets.setdefault(lid, []).append(str(r["Season"]))
    return {lid: sorted(seasons) for lid, seasons in targets.items()}


def _load_scheduler():
    """加载 backend/scheduler.py(它自带 sys.path 处理,内部 import db/fotmob_client)。"""
    backend_dir = str(PROJECT_ROOT / "backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    import scheduler  # noqa: PLC0415 — 重依赖(模型/抓取)按需加载,不在 CLI import 期拖入
    return scheduler


def _due_decision(conn_core, conn_odds, lid: int, now: str) -> tuple[bool, str, set[int]]:
    """事件驱动(排除已重试耗尽的比赛)OR 6h 兜底。返回 (due, reason, active_stale)。"""
    stale_ids = league_stale_unresolved_match_ids(conn_core, lid, now)
    exhausted_ids = postmatch_retry.exhausted_match_ids(conn_odds, lid)
    active_stale = stale_ids - exhausted_ids
    if active_stale:
        return True, "stale_unresolved_match", active_stale
    if is_due(conn_odds, SOURCE_FOTMOB_INCREMENTAL, str(lid), INCREMENTAL_SYNC_INTERVAL_SECONDS, now):
        return True, "fallback_ceiling", active_stale
    return False, "not_due", active_stale


def _record_match_retry_attempts(conn_core, conn_odds, league_id: int, now: str) -> dict:
    """League 抓取完成后:已不再 stale 的比赛清空重试计数(判定为已解决);
    仍 stale 且未耗尽的比赛计数 +1,新耗尽的记录失败原因并告警一次
    (backend.ingest.postmatch_retry)。"""
    current_stale = league_stale_unresolved_match_ids(conn_core, league_id, now)
    tracked = postmatch_retry.tracked_match_ids(conn_odds, league_id)
    for mid in tracked - current_stale:
        postmatch_retry.clear_retry_state(conn_odds, mid)

    newly_exhausted = []
    for mid in sorted(current_stale):
        result = postmatch_retry.process_match_attempt(conn_odds, mid, league_id, now)
        if result["newly_exhausted"]:
            newly_exhausted.append(mid)
    return {"stale_unresolved": len(current_stale), "newly_exhausted": newly_exhausted}


def run(*, due_only: bool, only_league: int | None = None,
        now_iso: str | None = None) -> dict:
    now = now_iso or utc_now_iso()
    poll_run_id = new_uuid()
    summary = {"poll_run_id": poll_run_id, "now": now, "leagues": [], "failures": []}

    conn_core = connect_ro("core")
    conn_odds = connect_rw("odds")
    scheduler = None
    try:
        targets = _league_season_targets(conn_core)
        if only_league is not None:
            targets = {only_league: targets.get(only_league, [])}

        for lid in sorted(targets):
            seasons = targets[lid]
            if not seasons:
                summary["leagues"].append({"league_id": lid, "verdict": "no_notstarted_rows"})
                continue

            due_reason = "forced"
            if due_only:
                due, due_reason, _ = _due_decision(conn_core, conn_odds, lid, now)
                if not due:
                    summary["leagues"].append({"league_id": lid, "verdict": "not_due"})
                    continue

            if scheduler is None:
                scheduler = _load_scheduler()
            league_ok = True
            ingested: list = []
            for season in seasons:
                try:
                    result = scheduler.step1_ingest_newly_finished(lid, season)
                    ingested.extend(result or [])
                except Exception as exc:  # noqa: BLE001 — 单联赛失败不拖垮其余联赛
                    league_ok = False
                    summary["failures"].append(
                        f"league {lid} season {season}: {type(exc).__name__}: {exc}")
            mark_polled(conn_odds, SOURCE_FOTMOB_INCREMENTAL, str(lid), now, poll_run_id,
                        tier="incremental", ok=league_ok)
            retry_summary = _record_match_retry_attempts(conn_core, conn_odds, lid, now)
            summary["leagues"].append({
                "league_id": lid, "seasons": seasons,
                "verdict": "ok" if league_ok else "failed",
                "newly_finished_ingested": len(ingested),
                "due_reason": due_reason,
                **retry_summary,
            })
    finally:
        conn_core.close()
        conn_odds.close()
    summary["by_verdict"] = {}
    for r in summary["leagues"]:
        summary["by_verdict"][r["verdict"]] = summary["by_verdict"].get(r["verdict"], 0) + 1
    return summary


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--due", action="store_true", help="只抓到期联赛(stale 比赛事件驱动 + 6h 兜底)")
    p.add_argument("--all", action="store_true", help="无视节流全部抓")
    p.add_argument("--league-id", type=int, default=None)
    p.add_argument("--now", type=str, default=None)
    args = p.parse_args(argv)

    summary = run(due_only=args.due and not args.all,
                  only_league=args.league_id, now_iso=args.now)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 1 if summary["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
