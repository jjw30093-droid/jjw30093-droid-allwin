"""FotMob 阵容/伤停快照采集(--due 窗口调度;--offline-payload 离线测试)。

纪律(CLAUDE.md §6.3):
- 同一比赛同一轮只抓一次 match payload,再从中提取阵容 + 两队伤停,
  三条快照共用同一 observed_at 与 poll_run_id;
- payload 不变(hash 相同)不重复落库(ingest 层 hash-diff);
- FotMob 不声明阵容/伤停更新时间 → source_updated_at 恒 NULL;
- 采集窗口与频率同赔率(2–72h 15min / 0–2h 5min),poll_state 持久化节流;
- 真实抓取需 THORDATA_PROXY;离线用 --offline-payload 验证同一条代码链路。

用法:
  .venv/bin/python -m backend.cli.poll_fotmob_snapshots --due
  .venv/bin/python -m backend.cli.poll_fotmob_snapshots --due --now 2026-08-21T10:00:00Z \
      --offline-payload payloads.json      # {"<match_id>": <pageProps dict>, ...}
  .venv/bin/python -m backend.cli.poll_fotmob_snapshots --match-id 5795363   # 单场(忽略窗口)
"""

import argparse
import json
import sys
import time
from pathlib import Path

from backend.db.connections import connect_ro, connect_rw
from backend.db.util import new_uuid, utc_now_iso
from backend.ingest.odds_snapshots import (
    ingest_lineup_snapshot,
    ingest_sideline_snapshot,
    record_source_health,
)
from backend.ingest.poll_windows import (
    SOURCE_FOTMOB_SNAPSHOT,
    is_due,
    mark_polled,
    required_interval_seconds,
    upcoming_precise_matches,
)
from backend.providers.fotmob_snapshots import (
    extract_lineup_snapshot,
    extract_sideline_snapshot,
    fetch_match_payload,
)


def _snapshot_one(
    conn_odds,
    match_row,
    payload: dict,
    observed_at: str,
    poll_run_id: str,
) -> dict:
    """单场:payload → 阵容 + 两队伤停,共用 observed_at/poll_run_id。"""
    mid = int(match_row["Match_ID"])
    counts = {"inserted": 0, "skipped": 0}
    lineup = extract_lineup_snapshot(payload)
    r = ingest_lineup_snapshot(conn_odds, mid, lineup, observed_at, poll_run_id)
    counts["inserted"] += r["inserted"]
    counts["skipped"] += r["skipped"]
    for team_key in ("Home_Team_ID", "Away_Team_ID"):
        team_id = match_row[team_key]
        if team_id is None:
            continue
        sideline = extract_sideline_snapshot(payload, int(team_id))
        r = ingest_sideline_snapshot(
            conn_odds, mid, int(team_id), sideline, observed_at, poll_run_id
        )
        counts["inserted"] += r["inserted"]
        counts["skipped"] += r["skipped"]
    return counts


def run_snapshot_poll(
    now_iso: str | None = None,
    offline_payloads: dict | None = None,
    match_ids: list[int] | None = None,
) -> dict:
    """窗口到期采集(或 match_ids 指定单场)。返回汇总 dict。"""
    now = now_iso or utc_now_iso()
    poll_run_id = new_uuid()
    summary = {
        "mode": "due" if match_ids is None else "match_ids",
        "now": now,
        "poll_run_id": poll_run_id,
        "window_candidates": 0,
        "due_matches": 0,
        "not_due_skipped": 0,
        "snapshots_inserted": 0,
        "snapshots_skipped": 0,
        "failures": [],
    }
    conn_odds = connect_rw("odds")
    conn_core = connect_ro("core")
    try:
        if match_ids is not None:
            rows = [
                conn_core.execute(
                    "SELECT Match_ID, kickoff_at_utc, status, Home_Team_ID, Away_Team_ID"
                    " FROM dim_match WHERE Match_ID=?",
                    (mid,),
                ).fetchone()
                for mid in match_ids
            ]
            targets = [(r, True) for r in rows if r is not None]
        else:
            candidates = upcoming_precise_matches(conn_core, now)
            summary["window_candidates"] = len(candidates)
            targets = []
            for c in candidates:
                interval = required_interval_seconds(
                    c["kickoff_at_utc"], c["kickoff_precision"], c["kickoff_source"], now
                )
                if interval is None:
                    continue
                due = is_due(conn_odds, SOURCE_FOTMOB_SNAPSHOT, c["Match_ID"], interval, now)
                if not due:
                    summary["not_due_skipped"] += 1
                    continue
                targets.append((c, True))

        failures = 0
        t0 = time.monotonic()
        for row, _ in targets:
            mid = int(row["Match_ID"])
            summary["due_matches"] += 1
            try:
                if offline_payloads is not None:
                    payload = offline_payloads.get(str(mid))
                    if payload is None:
                        raise KeyError(f"offline payload 缺少 match_id={mid}")
                else:
                    payload = fetch_match_payload(mid)
                counts = _snapshot_one(conn_odds, row, payload, now, poll_run_id)
                summary["snapshots_inserted"] += counts["inserted"]
                summary["snapshots_skipped"] += counts["skipped"]
            except Exception as exc:  # noqa: BLE001 — 采集边界:单场失败继续其余
                failures += 1
                summary["failures"].append(f"match {mid}: {type(exc).__name__}: {exc}")
            finally:
                mark_polled(conn_odds, SOURCE_FOTMOB_SNAPSHOT, mid, now, poll_run_id)
        if targets:
            record_source_health(
                conn_odds, "fotmob_snapshot", ok=failures == 0,
                latency_ms=int((time.monotonic() - t0) * 1000),
                error_summary=(f"{failures}/{len(targets)} 场快照抓取失败" if failures else None),
                meta={"mode": summary["mode"], "polled": summary["due_matches"],
                      "failed": failures, "poll_run_id": poll_run_id,
                      "offline": offline_payloads is not None},
            )
        return summary
    finally:
        conn_core.close()
        conn_odds.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FotMob 阵容/伤停快照采集")
    ap.add_argument("--due", action="store_true", help="窗口到期模式(worker 默认)")
    ap.add_argument("--match-id", type=int, action="append", default=None,
                    help="指定单场(可多次;忽略窗口判定)")
    ap.add_argument("--now", default=None, help="覆盖当前时间(测试用,UTC ISO8601)")
    ap.add_argument("--offline-payload", default=None,
                    help="离线模式:JSON 文件 {match_id: pageProps},不发网络请求")
    args = ap.parse_args(argv)

    if not args.due and not args.match_id:
        ap.error("需要 --due 或 --match-id")

    payloads = None
    if args.offline_payload:
        payloads = json.loads(Path(args.offline_payload).read_text(encoding="utf-8"))

    summary = run_snapshot_poll(
        now_iso=args.now, offline_payloads=payloads, match_ids=args.match_id
    )
    print(f"[poll_fotmob_snapshots] mode={summary['mode']} now={summary['now']}")
    print(f"  窗口候选: {summary['window_candidates']},本轮抓取: {summary['due_matches']},"
          f"未到期跳过: {summary['not_due_skipped']}")
    print(f"  快照: 落库 {summary['snapshots_inserted']} 条,hash 未变跳过 {summary['snapshots_skipped']} 条")
    if summary["failures"]:
        print(f"  失败 {len(summary['failures'])} 项:")
        for f in summary["failures"]:
            print(f"    - {f}")
    # 全部目标都失败才算整轮失败
    all_failed = summary["due_matches"] > 0 and len(summary["failures"]) >= summary["due_matches"]
    return 1 if all_failed else 0


if __name__ == "__main__":
    sys.exit(main())
