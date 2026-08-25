"""体能统计(physical_metrics_*)迟到补采:有限次检查点驱动 ingest_match 回查。

纯判断逻辑在 backend/ingest/physical_stats_poll.py(due_checkpoint /
is_valid_distance,不做 I/O);本文件是唯一的调用方——负责发现候选比赛、
落库 physical_stats_poll_state、调用 ingest_match() 触发真实重抓、以及
三次检查点耗尽后的原子告警。

范围与节奏(CLAUDE.md §6.3,站长最终决定,不在此重新推导):
- 只处理 League_ID ∈ PHYSICAL_STATS_LEAGUE_IDS(当前仅英超 47);
- status='Finish' 且 kickoff_at_utc 非空的比赛才进候选池;
- kickoff+6h / +12h / +24h 三个检查点,命中即查一次(调用 ingest_match 强制
  重抓整场,复用它已有的幂等 DELETE+INSERT);
- 主客两队 physical_metrics_distance_covered 都 ≥ 50000 米 → resolved,停止;
- 三次检查点后仍未达标 → exhausted + 一次 CRITICAL 告警(与
  backend/ingest/postmatch_retry.py::mark_exhausted_and_alert 同构的
  `WHERE exhausted_at IS NULL` 原子声明,保证并发调用下也恰好一次)。

状态表 physical_stats_poll_state 落在 core(allwin.db)——理由见
backend/migrations/core/0013_physical_stats_poll_state.sql 头注释(要回查、
要校验的目标数据本身就在 core,这个任务完全不碰 odds.db)。因此本文件用
单个 connect_rw("core") 连接完成候选发现 + 状态更新,不像 postmatch_retry
那样需要跨 core/odds 两库协作。

新比赛状态行的创建时机:采用"候选发现时按需创建"(不是预创建全部 Finish
EPL 比赛的行)——candidate 查询本身用 LEFT JOIN 覆盖"还没有状态行"的比赛,
第一次判定 due 时才 INSERT 一行 checks_done=0;这样状态表只包含"真的被这个
任务处理过至少一次"的比赛,不会为距 kickoff 不到 6 小时、永远轮不到检查的
新赛程行预先占用表空间,语义也更直接(有行 = 至少 due 过一次)。

用法:
  .venv/bin/python -m backend.cli.poll_physical_stats --due
  .venv/bin/python -m backend.cli.poll_physical_stats --due --now 2026-08-21T10:00:00Z
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

from backend.db.connections import connect_rw, tx
from backend.db.util import utc_now_iso
from backend.ingest.physical_stats_poll import (
    CANDIDATE_WINDOW_HOURS,
    PHYSICAL_STATS_LEAGUE_IDS,
    due_checkpoint,
    is_valid_distance,
)

ALERT_SOURCE = "physical_stats_poll_exhausted"


def _candidate_rows(conn, now_iso: str) -> list:
    """Finish + 联赛范围内 + 有精确 kickoff + 开球时间落在候选窗口内 +
    尚未 resolved/exhausted 的比赛,含还没有状态行的(LEFT JOIN 两侧皆 NULL
    时天然满足"未 resolved 未 exhausted")。

    2026-08-25 真实生产事故修复:这里曾经没有任何按 kickoff 时间的过滤,
    首次上线时把库里全部历史 Finish 英超比赛(2020 年至今)当成候选,批量
    对多年前的比赛触发 ingest_match() 重抓(见 backend/ingest/
    physical_stats_poll.py::CANDIDATE_WINDOW_HOURS 的详细事故记录)。现在
    SQL 层先用 kickoff_at_utc 落在 [now-CANDIDATE_WINDOW_HOURS, now] 内做
    一次硬过滤,再交给 Python 层的 due_checkpoint()(该函数内部也有等价的
    上限校验,双保险)。

    边界值特意在 Python 里算好再作为字符串传入,不用 SQLite 的 datetime()——
    kickoff_at_utc 存储格式是 ISO 'T'/'Z'(如 "2026-08-24T19:00:00Z"),而
    datetime() 产出的是空格分隔、无时区后缀的格式(如 "2026-08-23 18:05:21");
    两种格式做 TEXT 比较时,'T'(0x54)恒大于空格(0x20),会导致任何 ISO 'T'
    格式的时间戳都被判定"大于"任何 datetime() 输出——这正是
    tests/backend/test_data_hygiene_gates.py::
    test_timestamp_hygiene_after_migration 已经钉住过的同一类字符串比较
    陷阱,这里不能重蹈覆辙。"""
    window_start_iso = (
        _parse_now(now_iso) - timedelta(hours=CANDIDATE_WINDOW_HOURS)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    placeholders = ",".join("?" for _ in PHYSICAL_STATS_LEAGUE_IDS)
    rows = conn.execute(
        f"""
        SELECT dm.Match_ID AS match_id, dm.League_ID AS league_id,
               dm.kickoff_at_utc AS kickoff_at_utc,
               dm.Home_Team_ID AS home_team_id, dm.Away_Team_ID AS away_team_id,
               s.checks_done AS checks_done, s.resolved_at AS resolved_at,
               s.exhausted_at AS exhausted_at
        FROM dim_match dm
        LEFT JOIN physical_stats_poll_state s ON s.match_id = dm.Match_ID
        WHERE dm.status = 'Finish'
          AND dm.League_ID IN ({placeholders})
          AND dm.kickoff_at_utc IS NOT NULL
          AND dm.kickoff_at_utc >= ?
          AND dm.kickoff_at_utc <= ?
          AND s.resolved_at IS NULL
          AND s.exhausted_at IS NULL
        """,
        (*PHYSICAL_STATS_LEAGUE_IDS, window_start_iso, now_iso),
    ).fetchall()
    return rows


def _parse_now(now_iso: str) -> datetime:
    return datetime.fromisoformat(now_iso.replace("Z", "+00:00")).astimezone(timezone.utc)


def _ensure_state_row(conn, match_id: int, league_id: int, kickoff_at_utc: str, now_iso: str) -> None:
    with tx(conn):
        conn.execute(
            """INSERT INTO physical_stats_poll_state
                 (match_id, league_id, kickoff_at_utc, checks_done, created_at, updated_at)
               VALUES (?, ?, ?, 0, ?, ?)
               ON CONFLICT(match_id) DO NOTHING""",
            (match_id, league_id, kickoff_at_utc, now_iso, now_iso),
        )


def _read_distances(conn, match_id: int, home_team_id, away_team_id) -> tuple:
    """从 fact_team_match_stats(Period='All')的 extra_json 里读两队
    physical_metrics_distance_covered;缺行/缺字段一律 None,不当 0。"""

    def _one(team_id):
        if team_id is None:
            return None
        row = conn.execute(
            "SELECT extra_json FROM fact_team_match_stats"
            " WHERE Match_ID=? AND Team_ID=? AND Period='All'",
            (match_id, team_id),
        ).fetchone()
        if row is None or not row["extra_json"]:
            return None
        try:
            extra = json.loads(row["extra_json"])
        except ValueError:
            return None
        val = extra.get("physical_metrics_distance_covered")
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    return _one(home_team_id), _one(away_team_id)


def _mark_exhausted_and_alert(conn, match_id: int, league_id: int, now_iso: str) -> dict:
    """原子声明"我是第一个把这场比赛标记为耗尽的调用者"(与
    backend/ingest/postmatch_retry.py::mark_exhausted_and_alert 同构):
    `WHERE exhausted_at IS NULL` 条件 UPDATE + rowcount,不依赖 notify() 自身
    的 24h dedup 单独保证恰好一次。"""
    reason = (
        f"match_id={match_id}: 三次检查点(kickoff+6h/12h/24h)后"
        f"physical_metrics_distance_covered 仍未双队达标(阈值 50000 米),"
        f"已停止自动重试,需人工核实数据源状态"
    )
    with tx(conn):
        cur = conn.execute(
            "UPDATE physical_stats_poll_state SET exhausted_at=?, fail_reason=?, updated_at=?"
            " WHERE match_id=? AND exhausted_at IS NULL",
            (now_iso, reason, now_iso, match_id),
        )
        claimed = cur.rowcount == 1
    if not claimed:
        return {"persisted": False, "notified": False, "result": "already_claimed", "alert_id": None}

    from backend import notify as notify_mod

    return notify_mod.notify(
        level="CRITICAL",
        source=ALERT_SOURCE,
        title=f"体能统计三次检查点耗尽: match_id={match_id}",
        body=f"league_id={league_id}\nmatch_id={match_id}\n{reason}",
        dedup_key=f"{ALERT_SOURCE}:{match_id}",
    )


def _process_one(conn, row, now_iso: str) -> dict:
    match_id = int(row["match_id"])
    league_id = int(row["league_id"])
    kickoff_at_utc = row["kickoff_at_utc"]
    checks_done = int(row["checks_done"] or 0)

    decision = due_checkpoint(
        kickoff_at_utc=kickoff_at_utc,
        checks_done=checks_done,
        resolved=row["resolved_at"] is not None,
        exhausted=row["exhausted_at"] is not None,
        now_iso=now_iso,
    )
    if not decision.due:
        return {"match_id": match_id, "action": "not_due", "reason": decision.reason}

    _ensure_state_row(conn, match_id, league_id, kickoff_at_utc, now_iso)

    from backend.ingest.ingest_match import ingest_match

    ingest_match(match_id, league_id=league_id)

    home_dist, away_dist = _read_distances(conn, match_id, row["home_team_id"], row["away_team_id"])
    valid = is_valid_distance(home_dist, away_dist)
    checks_done_new = checks_done + 1

    with tx(conn):
        conn.execute(
            """UPDATE physical_stats_poll_state
               SET checks_done=?, last_checkpoint=?, last_checked_at=?, updated_at=?
                   {resolved_clause}
               WHERE match_id=?""".format(
                resolved_clause=", resolved_at=?" if valid else ""
            ),
            (
                checks_done_new, decision.checkpoint, now_iso, now_iso,
                *((now_iso,) if valid else ()),
                match_id,
            ),
        )

    result = {
        "match_id": match_id, "checkpoint": decision.checkpoint,
        "checks_done": checks_done_new, "home_distance": home_dist, "away_distance": away_dist,
    }
    if valid:
        result["action"] = "resolved"
        return result
    if checks_done_new >= 3:
        alert = _mark_exhausted_and_alert(conn, match_id, league_id, now_iso)
        result["action"] = "exhausted"
        result["alert"] = alert
        return result
    result["action"] = "checked_still_invalid"
    return result


def run_due(now_iso: str | None = None) -> dict:
    now_iso = now_iso or utc_now_iso()
    conn = connect_rw("core")
    try:
        candidates = _candidate_rows(conn, now_iso)
        results = []
        for row in candidates:
            try:
                results.append(_process_one(conn, row, now_iso))
            except Exception as exc:  # noqa: BLE001 — 单场失败不拖垮整轮
                results.append({
                    "match_id": int(row["match_id"]), "action": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                })
        acted = [r for r in results if r["action"] not in ("not_due",)]
        return {
            "candidates": len(candidates),
            "acted": len(acted),
            "results": results,
        }
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--due", action="store_true", help="按检查点到期判断执行(worker 默认调用方式)")
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
