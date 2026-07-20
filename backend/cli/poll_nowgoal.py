"""NowGoal 采集 CLI(实际调度归 worker/systemd,不进 FastAPI 进程)。

两种模式:
- --date:单日单轮(日程 → entity resolution → 已映射比赛抓赔率 → hash-diff 落库);
- --due(worker 默认):按采集窗口做到期判断(CLAUDE.md §6.3)——
  未来 72h 内有精确 kickoff_at_utc 的 NotStarted 比赛,
  2–72h 每场最小间隔 15 分钟,0–2h 每 5 分钟;
  日程发现(未映射比赛)按日期粒度节流(15 分钟);
  到期状态持久化 odds.db poll_state,进程重启不重复采集。

market_phase 判定(CLAUDE.md §6.3):由来源状态 + 精确 kickoff + 观察时间共同判定,
信息不足一律 unknown,不按自然日粗判、不按字符串是否含 'T' 粗判:
- core.status='NotStarted' 且 kickoff 通过统一验证器 normalize_exact_kickoff
  (exact ∧ 真实非空来源 ∧ 显式时区可解析)且 now < kickoff(datetime 比较,非字符串
  比较)→ pre_match;
- core.status='InPlay' → in_play;
- 其余(含无精确 kickoff、缺来源、naive/非法时间)→ unknown。

网络失败优雅:单场失败继续其余,末尾汇总;core(allwin.db)只读。

用法:
  .venv/bin/python -m backend.cli.poll_nowgoal --date 2026-07-19
  .venv/bin/python -m backend.cli.poll_nowgoal --due
  .venv/bin/python -m backend.cli.poll_nowgoal --due --now 2026-08-21T10:00:00Z --offline-fixture f.json

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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.db.connections import connect_ro, connect_rw
from backend.db.util import new_uuid, normalize_exact_kickoff, parse_strict_utc, utc_now_iso
from backend.ingest.entity_resolution import resolve_match, seed_team_aliases
from backend.ingest.odds_snapshots import ingest_odds_records, record_source_health
from backend.ingest.poll_windows import (
    SOURCE_NOWGOAL_ODDS,
    SOURCE_NOWGOAL_SCHEDULE,
    INTERVAL_FAR_SECONDS,
    is_due,
    mark_polled,
    required_interval_seconds,
    upcoming_precise_matches,
)
from backend.providers import nowgoal

POLLABLE_STATUSES = ("auto_ok", "confirmed")
NOWGOAL_TZ_OFFSET_HOURS = 8   # 日程请求带 timezone=8,日期参数按北京时间取


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def _row_get(row, key, default=None):
    """兼容取列:某些测试布景的 dim_match 可能没有 provenance 列。"""
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def _market_phase(conn_core, fotmob_match_id: int, now_iso: str) -> str:
    """来源状态 + 统一验证过的精确 kickoff + 观察时间共同判定;信息不足 → unknown。

    唯一真源 normalize_exact_kickoff——不再判断字符串是否含 'T',也不做裸字符串比较。
    """
    try:
        row = conn_core.execute(
            "SELECT status, kickoff_at_utc, kickoff_precision, kickoff_source"
            " FROM dim_match WHERE Match_ID=?",
            (fotmob_match_id,),
        ).fetchone()
    except Exception:
        return "unknown"
    if row is None:
        return "unknown"
    if row["status"] == "InPlay":
        return "in_play"
    if row["status"] != "NotStarted":
        return "unknown"
    normalized = normalize_exact_kickoff(
        row["kickoff_at_utc"], _row_get(row, "kickoff_precision"), _row_get(row, "kickoff_source")
    )
    if normalized is None:
        return "unknown"
    now_dt = parse_strict_utc(now_iso)
    kickoff_dt = parse_strict_utc(normalized)
    if now_dt is not None and kickoff_dt is not None and now_dt < kickoff_dt:
        return "pre_match"
    return "unknown"


def _load_fixture(path: str) -> dict:
    fixture = json.loads(Path(path).read_text(encoding="utf-8"))
    if "schedule_data" not in fixture:
        raise ValueError("offline fixture 缺少 schedule_data 字段")
    return fixture


def _fetch_schedule(fixture, date: str):
    if fixture is not None:
        return nowgoal.parse_schedule(fixture["schedule_data"])
    return nowgoal.fetch_schedule(date)


def _fetch_odds(fixture, titan_id):
    if fixture is not None:
        payload = (fixture.get("odds") or {}).get(str(titan_id))
        return nowgoal.parse_odds(payload) if payload else []
    return nowgoal.fetch_odds(titan_id)


def _poll_odds_for(
    conn_odds, conn_core, item: dict, observed_at: str, poll_run_id: str, summary: dict
) -> bool:
    """抓单场赔率并落库;返回是否成功。item = {"titan_id", "xref"}。"""
    titan_id = item["titan_id"]
    xref = item["xref"]
    fixture = item.get("fixture")
    try:
        records = _fetch_odds(fixture, titan_id)
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
            market_phase=_market_phase(conn_core, xref["fotmob_match_id"], observed_at),
        )
        summary["odds_polled"] += 1
        summary["snapshots_inserted"] += result["inserted"]
        summary["snapshots_skipped"] += result["skipped"]
        return True
    except Exception as exc:  # noqa: BLE001 — 采集边界:单场失败不拖垮整轮
        summary["failures"].append(f"odds titan_id={titan_id}: {type(exc).__name__}: {exc}")
        return False


def _resolve_schedule_rows(
    conn_odds, conn_core, schedule: list, date: str, summary: dict
) -> list[dict]:
    """对日程行做实体解析,返回可采集(auto_ok/confirmed)的 {titan_id, xref} 列表。"""
    pollable: list[dict] = []
    for row in schedule:
        row = dict(row)
        row["date"] = date
        try:
            result = resolve_match(conn_odds, conn_core, row)
        except Exception as exc:  # noqa: BLE001
            summary["failures"].append(
                f"resolve titan_id={row['titan_id']}: {type(exc).__name__}: {exc}"
            )
            continue
        status = result.get("review_status")
        if status in POLLABLE_STATUSES:
            summary["resolved_auto_ok"] += 1
            pollable.append({"titan_id": row["titan_id"], "xref": result})
        elif status == "needs_review":
            summary["resolved_needs_review"] += 1
        else:
            summary["unresolved"] += 1
    return pollable


# ── 单日单轮模式(--date) ────────────────────────────────────────────


def run_poll(date: str, offline_fixture: str | None = None) -> dict:
    """执行一轮指定日期采集,返回汇总 dict(供 CLI 打印与测试断言)。"""
    poll_run_id = new_uuid()
    observed_at = utc_now_iso()
    summary = {
        "mode": "date",
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
        t0 = time.monotonic()
        try:
            schedule = _fetch_schedule(fixture, date)
        except Exception as exc:  # noqa: BLE001
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
        mark_polled(conn_odds, SOURCE_NOWGOAL_SCHEDULE, date, observed_at, poll_run_id)

        seed_team_aliases(conn_odds, conn_core)
        pollable = _resolve_schedule_rows(conn_odds, conn_core, schedule, date, summary)

        odds_failures = 0
        t1 = time.monotonic()
        for item in pollable:
            item["fixture"] = fixture
            ok = _poll_odds_for(conn_odds, conn_core, item, observed_at, poll_run_id, summary)
            mark_polled(conn_odds, SOURCE_NOWGOAL_ODDS,
                        item["xref"]["fotmob_match_id"], observed_at, poll_run_id)
            if not ok:
                odds_failures += 1
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


# ── 窗口到期模式(--due,worker 默认) ────────────────────────────────


def _beijing_dates_for(matches) -> list[str]:
    """候选比赛的北京时间日期集合(NowGoal 日程按 timezone=8 出日期)。"""
    dates = set()
    for m in matches:
        ko = _parse_iso(m["kickoff_at_utc"]) + timedelta(hours=NOWGOAL_TZ_OFFSET_HOURS)
        dates.add(ko.date().isoformat())
    return sorted(dates)


def run_due_poll(now_iso: str | None = None, offline_fixture: str | None = None) -> dict:
    """窗口到期采集:只处理未来 72h 内有精确 kickoff 的比赛,按 poll_state 节流。"""
    now = now_iso or utc_now_iso()
    poll_run_id = new_uuid()
    summary = {
        "mode": "due",
        "now": now,
        "poll_run_id": poll_run_id,
        "window_candidates": 0,
        "schedule_fetches": 0,
        "schedule_rows": 0,
        "resolved_auto_ok": 0,
        "resolved_needs_review": 0,
        "unresolved": 0,
        "due_matches": 0,
        "odds_polled": 0,
        "snapshots_inserted": 0,
        "snapshots_skipped": 0,
        "not_due_skipped": 0,
        "failures": [],
    }
    fixture = _load_fixture(offline_fixture) if offline_fixture else None
    conn_odds = connect_rw("odds")
    conn_core = connect_ro("core")
    try:
        candidates = upcoming_precise_matches(conn_core, now)
        summary["window_candidates"] = len(candidates)
        if not candidates:
            return summary

        xref_by_match: dict[int, dict] = {}
        for r in conn_odds.execute(
            "SELECT * FROM dim_match_xref WHERE provider='nowgoal'"
        ):
            xref_by_match[int(r["fotmob_match_id"])] = dict(r)

        # ── 日程发现:窗口内未映射比赛 → 按北京日期节流抓日程并解析 ──
        unmapped = [c for c in candidates if int(c["Match_ID"]) not in xref_by_match]
        if unmapped:
            seed_team_aliases(conn_odds, conn_core)
            for date in _beijing_dates_for(unmapped):
                if not is_due(conn_odds, SOURCE_NOWGOAL_SCHEDULE, date,
                              INTERVAL_FAR_SECONDS, now):
                    continue
                t0 = time.monotonic()
                try:
                    schedule = _fetch_schedule(fixture, date)
                except Exception as exc:  # noqa: BLE001
                    record_source_health(
                        conn_odds, "nowgoal", ok=False,
                        latency_ms=int((time.monotonic() - t0) * 1000),
                        error_summary=f"{type(exc).__name__}: {exc}"[:500],
                        meta={"stage": "schedule", "date": date, "poll_run_id": poll_run_id},
                    )
                    summary["failures"].append(f"schedule {date}: {type(exc).__name__}: {exc}")
                    mark_polled(conn_odds, SOURCE_NOWGOAL_SCHEDULE, date, now, poll_run_id)
                    continue
                summary["schedule_fetches"] += 1
                summary["schedule_rows"] += len(schedule)
                record_source_health(
                    conn_odds, "nowgoal", ok=True,
                    latency_ms=int((time.monotonic() - t0) * 1000),
                    meta={"stage": "schedule", "date": date, "rows": len(schedule),
                          "poll_run_id": poll_run_id, "offline": fixture is not None},
                )
                mark_polled(conn_odds, SOURCE_NOWGOAL_SCHEDULE, date, now, poll_run_id)
                _resolve_schedule_rows(conn_odds, conn_core, schedule, date, summary)
            # 重新装载映射
            xref_by_match = {}
            for r in conn_odds.execute(
                "SELECT * FROM dim_match_xref WHERE provider='nowgoal'"
            ):
                xref_by_match[int(r["fotmob_match_id"])] = dict(r)

        # ── 赔率:已映射(auto_ok/confirmed)且到期的比赛 ─────────────
        odds_failures = 0
        t1 = time.monotonic()
        for c in candidates:
            mid = int(c["Match_ID"])
            xref = xref_by_match.get(mid)
            if xref is None or xref.get("review_status") not in POLLABLE_STATUSES:
                continue
            interval = required_interval_seconds(
                c["kickoff_at_utc"], c["kickoff_precision"], c["kickoff_source"], now
            )
            if interval is None:
                continue
            if not is_due(conn_odds, SOURCE_NOWGOAL_ODDS, mid, interval, now):
                summary["not_due_skipped"] += 1
                continue
            summary["due_matches"] += 1
            item = {"titan_id": xref["provider_match_id"], "xref": xref, "fixture": fixture}
            ok = _poll_odds_for(conn_odds, conn_core, item, now, poll_run_id, summary)
            mark_polled(conn_odds, SOURCE_NOWGOAL_ODDS, mid, now, poll_run_id)
            if not ok:
                odds_failures += 1
        if summary["due_matches"]:
            record_source_health(
                conn_odds, "nowgoal_odds", ok=odds_failures == 0,
                latency_ms=int((time.monotonic() - t1) * 1000),
                error_summary=(f"{odds_failures}/{summary['due_matches']} 场赔率抓取失败"
                               if odds_failures else None),
                meta={"stage": "odds", "mode": "due", "polled": summary["odds_polled"],
                      "failed": odds_failures, "poll_run_id": poll_run_id},
            )
        return summary
    finally:
        conn_core.close()
        conn_odds.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="NowGoal 采集(--date 单日 / --due 窗口到期)")
    ap.add_argument("--date", default=None, help="单日模式:采集日期 YYYY-MM-DD")
    ap.add_argument("--due", action="store_true", help="窗口到期模式(worker 默认用这个)")
    ap.add_argument("--now", default=None, help="覆盖当前时间(测试用,UTC ISO8601)")
    ap.add_argument("--offline-fixture", default=None,
                    help="离线模式:从 JSON 文件读日程/赔率样例,不发网络请求")
    args = ap.parse_args(argv)

    if args.due:
        summary = run_due_poll(now_iso=args.now, offline_fixture=args.offline_fixture)
        print(f"[poll_nowgoal --due] now={summary['now']} run={summary['poll_run_id']}")
        print(f"  窗口候选: {summary['window_candidates']},到期: {summary['due_matches']},"
              f"未到期跳过: {summary['not_due_skipped']}")
        print(f"  日程抓取: {summary['schedule_fetches']} 次 {summary['schedule_rows']} 行;"
              f"映射 auto_ok={summary['resolved_auto_ok']} needs_review={summary['resolved_needs_review']}")
        print(f"  赔率: 已抓 {summary['odds_polled']} 场,落库 {summary['snapshots_inserted']} 条,"
              f"hash 未变跳过 {summary['snapshots_skipped']} 条")
    else:
        date = args.date or date_cls.today().isoformat()
        summary = run_poll(date, offline_fixture=args.offline_fixture)
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
    schedule_failed = any(f.startswith("schedule") for f in summary["failures"])
    return 1 if (schedule_failed and summary.get("schedule_rows", 0) == 0
                 and summary.get("odds_polled", 0) == 0) else 0


if __name__ == "__main__":
    sys.exit(main())
