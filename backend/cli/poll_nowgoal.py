"""单轮 NowGoal 采集 CLI(实际调度归 worker/systemd,不进 FastAPI 进程)。

流程:--date 抓日程 → entity resolution → 对已映射(auto_ok/confirmed)比赛抓赔率
→ hash-diff 落库(market_phase 按 core dim_match 日期判 pre_match)→ 写 source_health。
网络失败优雅:单场失败继续其余,末尾汇总打印;core(allwin.db)只读。

用法:
  .venv/bin/python -m backend.cli.poll_nowgoal --date 2026-07-19
  .venv/bin/python -m backend.cli.poll_nowgoal --date 2026-07-19 --offline-fixture f.json

--offline-fixture JSON 格式(无网测试;按旧代码审计的格式构造,真实端点 UNVERIFIED):
  {
    "schedule_data": "<type=6 Data 文本,A[ 行>",
    "odds": {"<titan_id>": <type=14 payload dict>}
  }
"""

import argparse
import json
import sys
import time
from datetime import date as date_cls
from datetime import datetime, timezone
from pathlib import Path

from backend.db.connections import connect_ro, connect_rw
from backend.db.util import new_uuid, utc_now_iso
from backend.ingest.entity_resolution import resolve_match, seed_team_aliases
from backend.ingest.odds_snapshots import ingest_odds_records, record_source_health
from backend.providers import nowgoal

POLLABLE_STATUSES = ("auto_ok", "confirmed")


def _market_phase(conn_core, fotmob_match_id: int) -> str:
    """按 core dim_match 日期粗判:比赛日尚未过去 → pre_match,否则 unknown。

    dim_match 只有日期没有开球时间,无法精确判断 in_play,不硬猜。
    """
    row = conn_core.execute(
        "SELECT Date FROM dim_match WHERE Match_ID=?", (fotmob_match_id,)
    ).fetchone()
    if row is None or not row["Date"]:
        return "unknown"
    today = datetime.now(timezone.utc).date().isoformat()
    return "pre_match" if row["Date"] >= today else "unknown"


def _load_fixture(path: str) -> dict:
    fixture = json.loads(Path(path).read_text(encoding="utf-8"))
    if "schedule_data" not in fixture:
        raise ValueError("offline fixture 缺少 schedule_data 字段")
    return fixture


def run_poll(date: str, offline_fixture: str | None = None) -> dict:
    """执行一轮采集,返回汇总 dict(供 CLI 打印与测试断言)。"""
    poll_run_id = new_uuid()
    observed_at = utc_now_iso()
    summary = {
        "date": date,
        "poll_run_id": poll_run_id,
        "schedule_rows": 0,
        "resolved_auto_ok": 0,
        "resolved_needs_review": 0,
        "unresolved": 0,
        "odds_polled": 0,
        "snapshots_inserted": 0,
        "snapshots_skipped": 0,
        "failures": [],
    }

    fixture = _load_fixture(offline_fixture) if offline_fixture else None
    conn_odds = connect_rw("odds")
    conn_core = connect_ro("core")
    try:
        # ── 日程 ────────────────────────────────────────────────────────
        t0 = time.monotonic()
        try:
            if fixture is not None:
                schedule = nowgoal.parse_schedule(fixture["schedule_data"])
            else:
                schedule = nowgoal.fetch_schedule(date)
        except Exception as exc:
            record_source_health(
                conn_odds, "nowgoal", ok=False,
                latency_ms=int((time.monotonic() - t0) * 1000),
                error_summary=f"{type(exc).__name__}: {exc}"[:500],
                meta={"stage": "schedule", "date": date, "poll_run_id": poll_run_id},
            )
            summary["failures"].append(f"schedule: {type(exc).__name__}: {exc}")
            return summary
        record_source_health(
            conn_odds, "nowgoal", ok=True,
            latency_ms=int((time.monotonic() - t0) * 1000),
            meta={"stage": "schedule", "date": date, "rows": len(schedule),
                  "poll_run_id": poll_run_id, "offline": fixture is not None},
        )
        summary["schedule_rows"] = len(schedule)

        # ── 实体解析(先幂等补种别名) ─────────────────────────────────
        seed_team_aliases(conn_odds, conn_core)
        pollable: list[dict] = []
        for row in schedule:
            row = dict(row)
            row["date"] = date
            try:
                result = resolve_match(conn_odds, conn_core, row)
            except Exception as exc:
                summary["failures"].append(
                    f"resolve titan_id={row['titan_id']}: {type(exc).__name__}: {exc}"
                )
                continue
            status = result.get("review_status")
            if status == "auto_ok" or status == "confirmed":
                summary["resolved_auto_ok"] += 1
                pollable.append({"schedule": row, "xref": result})
            elif status == "needs_review":
                summary["resolved_needs_review"] += 1
            else:
                summary["unresolved"] += 1

        # ── 赔率(仅已映射;单场失败继续其余) ─────────────────────────
        odds_failures = 0
        t1 = time.monotonic()
        for item in pollable:
            titan_id = item["schedule"]["titan_id"]
            xref = item["xref"]
            try:
                if fixture is not None:
                    payload = (fixture.get("odds") or {}).get(str(titan_id))
                    records = nowgoal.parse_odds(payload) if payload else []
                else:
                    records = nowgoal.fetch_odds(titan_id)
                records = [
                    nowgoal.normalize_for_inversion(r, bool(xref["home_away_inverted"]))
                    for r in records
                ]
                result = ingest_odds_records(
                    conn_odds,
                    provider_match_id=str(titan_id),
                    records=records,
                    observed_at=observed_at,
                    poll_run_id=poll_run_id,
                    market_phase=_market_phase(conn_core, xref["fotmob_match_id"]),
                )
                summary["odds_polled"] += 1
                summary["snapshots_inserted"] += result["inserted"]
                summary["snapshots_skipped"] += result["skipped"]
            except Exception as exc:
                odds_failures += 1
                summary["failures"].append(
                    f"odds titan_id={titan_id}: {type(exc).__name__}: {exc}"
                )
        if pollable:
            record_source_health(
                conn_odds, "nowgoal_odds", ok=odds_failures == 0,
                latency_ms=int((time.monotonic() - t1) * 1000),
                error_summary=(f"{odds_failures}/{len(pollable)} 场赔率抓取失败"
                               if odds_failures else None),
                meta={"stage": "odds", "date": date, "polled": summary["odds_polled"],
                      "failed": odds_failures, "poll_run_id": poll_run_id},
            )
        return summary
    finally:
        conn_core.close()
        conn_odds.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="NowGoal 单轮采集(日程→映射→赔率快照)")
    ap.add_argument("--date", default=date_cls.today().isoformat(),
                    help="采集日期 YYYY-MM-DD(默认今天)")
    ap.add_argument("--offline-fixture", default=None,
                    help="离线模式:从 JSON 文件读日程/赔率样例,不发网络请求")
    args = ap.parse_args(argv)

    summary = run_poll(args.date, offline_fixture=args.offline_fixture)

    print(f"[poll_nowgoal] date={summary['date']} run={summary['poll_run_id']}")
    print(f"  日程行: {summary['schedule_rows']}")
    print(f"  映射: auto_ok/confirmed={summary['resolved_auto_ok']}"
          f" needs_review={summary['resolved_needs_review']} 未解析={summary['unresolved']}")
    print(f"  赔率: 已抓 {summary['odds_polled']} 场,"
          f"落库 {summary['snapshots_inserted']} 条,hash 未变跳过 {summary['snapshots_skipped']} 条")
    if summary["failures"]:
        print(f"  失败 {len(summary['failures'])} 项:")
        for f in summary["failures"]:
            print(f"    - {f}")
    # 日程整体失败(没有任何行且有 schedule 失败)→ 非零退出;单场失败不算致命
    schedule_failed = any(f.startswith("schedule:") for f in summary["failures"])
    return 1 if schedule_failed else 0


if __name__ == "__main__":
    sys.exit(main())
