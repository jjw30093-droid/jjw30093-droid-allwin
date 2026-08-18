"""FotMob 阵容/伤停快照采集(--due 窗口调度;--offline-payload 离线测试)。

纪律(CLAUDE.md §6.3):
- 同一比赛同一轮只抓一次 match payload,再从中提取阵容 + 两队伤停(+ 可选的
  裁判/天气,见下),全部共用同一 observed_at 与 poll_run_id;
- payload 不变(hash 相同)不重复落库(ingest 层 hash-diff);
- FotMob 不声明阵容/伤停更新时间 → source_updated_at 恒 NULL;
- 采集窗口与到期判断走 poll_windows.poll_decision()/cadence_fotmob_lineup(按
  league_id 查表):cadence 窗口 72h,T-72h..T-24h 每 24h,T-24h..watch-window-start
  每 12h,watch-window-start..T-0 每 5 分钟到底(BIG-5=T-1.5h,其余联赛=T-1.25h),
  poll_state 持久化节流;候选池宽度独立于 cadence 窗口,为
  FOTMOB_LINEUP_CANDIDATE_WINDOW_HOURS(168h),只为让"首次发现即采"结构上可达;
- 真实抓取需 THORDATA_PROXY;离线用 --offline-payload 验证同一条代码链路。

--write-match-details(裁判/天气赛前数据能力实测,本轮任务):
- 用同一份已经抓到的 payload 定向 UPDATE dim_match 的 Referee/Temperature/
  Wind_Speed 三列,不新发第二次请求,不碰 status/kickoff/比分等其它 16 列;
- 新值为空时不覆盖已知旧值(COALESCE),不用一次解析缺失把已知数据抹成 NULL;
- 不会写 lineupType='predicted' 的预测阵容进 fact_match_lineup(那是
  ingest_match.py 的职责边界,赛前跑它会把预测阵容与赛后确认阵容混为一谈)。

用法:
  .venv/bin/python -m backend.cli.poll_fotmob_snapshots --due
  .venv/bin/python -m backend.cli.poll_fotmob_snapshots --due --now 2026-08-21T10:00:00Z \
      --offline-payload payloads.json      # {"<match_id>": <pageProps dict>, ...}
  .venv/bin/python -m backend.cli.poll_fotmob_snapshots --match-id 5795363   # 单场(忽略窗口)
  .venv/bin/python -m backend.cli.poll_fotmob_snapshots --write-match-details \
      --match-id 5104970 --match-id 5104971    # 单场(可重复)+ 顺带写裁判/天气
"""

import argparse
import json
import sys
import time
from pathlib import Path

from backend.db.connections import connect_ro, connect_rw, tx
from backend.db.util import new_uuid, utc_now_iso
from backend.ingest.odds_snapshots import (
    ingest_lineup_snapshot,
    ingest_sideline_snapshot,
    record_source_health,
)
from backend.ingest.poll_windows import (
    FOTMOB_LINEUP_CANDIDATE_WINDOW_HOURS,
    SOURCE_FOTMOB_SNAPSHOT,
    mark_polled,
    poll_decision,
    upcoming_precise_matches,
)
from backend.providers.fotmob_snapshots import (
    extract_lineup_snapshot,
    extract_prematch_details,
    extract_sideline_snapshot,
    fetch_match_payload,
)


def _write_match_details(conn_core_rw, match_id: int, details: dict) -> bool:
    """COALESCE 式定向更新:只在新值非空时覆盖 Referee/Temperature/Wind_Speed,
    绝不覆盖其它列。返回是否至少有一个字段是非空值(供上层汇总统计)。"""
    with tx(conn_core_rw):
        conn_core_rw.execute(
            """
            UPDATE dim_match
            SET Referee = COALESCE(?, Referee),
                Temperature = COALESCE(?, Temperature),
                Wind_Speed = COALESCE(?, Wind_Speed)
            WHERE Match_ID = ?
            """,
            (details.get("Referee"), details.get("Temperature"), details.get("Wind_Speed"), match_id),
        )
    return any(details.get(k) is not None for k in ("Referee", "Temperature", "Wind_Speed"))


def _snapshot_one(
    conn_odds,
    match_row,
    payload: dict,
    observed_at: str,
    poll_run_id: str,
    conn_core_rw=None,
) -> dict:
    """单场:payload → 阵容 + 两队伤停,共用 observed_at/poll_run_id。

    conn_core_rw 非 None 时,额外用同一份 payload 定向更新 dim_match 的
    Referee/Temperature/Wind_Speed(--write-match-details,见模块 docstring)。
    """
    mid = int(match_row["Match_ID"])
    counts = {"inserted": 0, "skipped": 0, "match_details_written": False}
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
    if conn_core_rw is not None:
        details = extract_prematch_details(payload, mid)
        counts["match_details_written"] = _write_match_details(conn_core_rw, mid, details)
    return counts


def run_snapshot_poll(
    now_iso: str | None = None,
    offline_payloads: dict | None = None,
    match_ids: list[int] | None = None,
    write_match_details: bool = False,
) -> dict:
    """窗口到期采集(或 match_ids 指定单场)。返回汇总 dict。

    write_match_details=True 时额外定向更新 dim_match 的 Referee/Temperature/
    Wind_Speed(见模块 docstring 的 --write-match-details 说明);默认 False,
    行为与改动前完全一致。
    """
    now = now_iso or utc_now_iso()
    poll_run_id = new_uuid()
    summary = {
        "mode": "due" if match_ids is None else "match_ids",
        "now": now,
        "poll_run_id": poll_run_id,
        "window_candidates": 0,
        "due_matches": 0,
        "not_due_skipped": 0,
        "out_of_window_skipped": 0,
        "snapshots_inserted": 0,
        "snapshots_skipped": 0,
        "match_details_written": 0,
        "failures": [],
    }
    conn_odds = connect_rw("odds")
    conn_core = connect_ro("core")
    conn_core_rw = connect_rw("core") if write_match_details else None
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
            targets = [(r, None) for r in rows if r is not None]
        else:
            candidates = upcoming_precise_matches(
                conn_core, now, window_hours=FOTMOB_LINEUP_CANDIDATE_WINDOW_HOURS
            )
            summary["window_candidates"] = len(candidates)
            targets = []
            for c in candidates:
                state_row = conn_odds.execute(
                    "SELECT last_polled_at FROM poll_state WHERE source=? AND subject=?",
                    (SOURCE_FOTMOB_SNAPSHOT, str(c["Match_ID"])),
                ).fetchone()
                last_polled_at = state_row["last_polled_at"] if state_row is not None else None
                decision = poll_decision(
                    SOURCE_FOTMOB_SNAPSHOT, c["kickoff_at_utc"], c["kickoff_precision"],
                    c["kickoff_source"], last_polled_at, now, league_id=c["League_ID"],
                )
                if not decision.due:
                    # 候选池(168h)比 cadence 窗口(72h)宽,窗外未到"首次发现即采"
                    # 的场次(tier is None)与窗内被正常节流的场次(tier 非 None)
                    # 是两件不同的事,必须分开计数——否则 168h 池下大多数窗外
                    # 场次会无处归属(window_candidates ≈ 99,due ≈ 2,
                    # not_due ≈ 5,~92 场既不 due 也不计入 not_due_skipped)。
                    if decision.tier is not None:
                        summary["not_due_skipped"] += 1
                    else:
                        summary["out_of_window_skipped"] += 1
                    continue
                targets.append((c, decision.tier))

        failures = 0
        t0 = time.monotonic()
        for row, tier in targets:
            mid = int(row["Match_ID"])
            summary["due_matches"] += 1
            ok = True
            try:
                if offline_payloads is not None:
                    payload = offline_payloads.get(str(mid))
                    if payload is None:
                        raise KeyError(f"offline payload 缺少 match_id={mid}")
                else:
                    payload = fetch_match_payload(mid)
                counts = _snapshot_one(
                    conn_odds, row, payload, now, poll_run_id, conn_core_rw=conn_core_rw
                )
                summary["snapshots_inserted"] += counts["inserted"]
                summary["snapshots_skipped"] += counts["skipped"]
                if counts["match_details_written"]:
                    summary["match_details_written"] += 1
            except Exception as exc:  # noqa: BLE001 — 采集边界:单场失败继续其余
                failures += 1
                ok = False
                summary["failures"].append(f"match {mid}: {type(exc).__name__}: {exc}")
            finally:
                mark_polled(conn_odds, SOURCE_FOTMOB_SNAPSHOT, mid, now, poll_run_id,
                            tier=tier, ok=ok)
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
        if conn_core_rw is not None:
            conn_core_rw.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FotMob 阵容/伤停快照采集")
    ap.add_argument("--due", action="store_true", help="窗口到期模式(worker 默认)")
    ap.add_argument("--match-id", type=int, action="append", default=None,
                    help="指定单场(可多次;忽略窗口判定)")
    ap.add_argument("--now", default=None, help="覆盖当前时间(测试用,UTC ISO8601)")
    ap.add_argument("--offline-payload", default=None,
                    help="离线模式:JSON 文件 {match_id: pageProps},不发网络请求")
    ap.add_argument("--write-match-details", action="store_true",
                    help="同时用同一份 payload 定向更新 dim_match 的裁判/天气三列(见模块 docstring)")
    args = ap.parse_args(argv)

    if not args.due and not args.match_id:
        ap.error("需要 --due 或 --match-id")

    payloads = None
    if args.offline_payload:
        payloads = json.loads(Path(args.offline_payload).read_text(encoding="utf-8"))

    summary = run_snapshot_poll(
        now_iso=args.now, offline_payloads=payloads, match_ids=args.match_id,
        write_match_details=args.write_match_details,
    )
    print(f"[poll_fotmob_snapshots] mode={summary['mode']} now={summary['now']}")
    print(f"  窗口候选: {summary['window_candidates']},本轮抓取: {summary['due_matches']},"
          f"窗内节流跳过: {summary['not_due_skipped']},窗外未到发现跳过: "
          f"{summary['out_of_window_skipped']}")
    print(f"  快照: 落库 {summary['snapshots_inserted']} 条,hash 未变跳过 {summary['snapshots_skipped']} 条")
    if args.write_match_details:
        print(f"  裁判/天气: {summary['match_details_written']}/{summary['due_matches']} 场写入了至少一个非空字段")
    if summary["failures"]:
        print(f"  失败 {len(summary['failures'])} 项:")
        for f in summary["failures"]:
            print(f"    - {f}")
    # 全部目标都失败才算整轮失败
    all_failed = summary["due_matches"] > 0 and len(summary["failures"]) >= summary["due_matches"]
    return 1 if all_failed else 0


if __name__ == "__main__":
    sys.exit(main())
