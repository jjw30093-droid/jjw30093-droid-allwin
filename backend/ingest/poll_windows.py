"""采集窗口与到期判断(CLAUDE.md §6.3)。

- 只有通过统一验证器 normalize_exact_kickoff(kickoff_precision=='exact' ∧ 有非空真实
  来源 ∧ 时间带显式时区可解析)、状态 NotStarted、开球落在 [now, now+72h] 的比赛才进入
  采集窗口——不再只判断 kickoff_at_utc 是否非空或字符串是否含 'T';
- 距开球 2–72 小时:同一 (source, subject) 最小间隔 900s;0–2 小时:300s;
- 已开球不再属于赛前采集窗口(in-play 采集是显式的另一件事,本模块不伪装);
- 到期状态持久化于 odds.db 的 poll_state(进程重启不得无界重复采集)。

Worker/systemd 每 5 分钟触发一次到期判断;真正是否请求数据源由本模块决定。
"""

import sqlite3
from datetime import datetime, timedelta, timezone

from backend.db.connections import tx
from backend.db.util import normalize_exact_kickoff, utc_now_iso

WINDOW_HOURS = 72
NEAR_HOURS = 2
INTERVAL_FAR_SECONDS = 900    # 2–72h:15 分钟
INTERVAL_NEAR_SECONDS = 300   # 0–2h:5 分钟

SOURCE_NOWGOAL_ODDS = "nowgoal_odds"
SOURCE_NOWGOAL_SCHEDULE = "nowgoal_schedule"
SOURCE_FOTMOB_SNAPSHOT = "fotmob_snapshot"


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def required_interval_seconds(
    kickoff_at_utc: str | None,
    kickoff_precision: str | None,
    kickoff_source: str | None,
    now_iso: str,
) -> int | None:
    """该比赛当前所需的最小采集间隔;不满足统一精确性验证 / 已开球 / >72h → None。

    唯一真源 normalize_exact_kickoff——不再自行判断字符串是否含 'T'。
    """
    normalized = normalize_exact_kickoff(kickoff_at_utc, kickoff_precision, kickoff_source)
    if normalized is None:
        return None
    delta = _parse_iso(normalized) - _parse_iso(now_iso)
    seconds = delta.total_seconds()
    if seconds <= 0:
        return None                      # 已开球:赛前采集停止
    if seconds > WINDOW_HOURS * 3600:
        return None
    if seconds <= NEAR_HOURS * 3600:
        return INTERVAL_NEAR_SECONDS
    return INTERVAL_FAR_SECONDS


def is_due(
    conn_odds: sqlite3.Connection,
    source: str,
    subject: str,
    interval_seconds: int,
    now_iso: str,
) -> bool:
    """距上次(持久化的)采集是否已满最小间隔;从未采集过 → due。"""
    row = conn_odds.execute(
        "SELECT last_polled_at FROM poll_state WHERE source=? AND subject=?",
        (source, str(subject)),
    ).fetchone()
    if row is None:
        return True
    elapsed = (_parse_iso(now_iso) - _parse_iso(row["last_polled_at"])).total_seconds()
    return elapsed >= interval_seconds


def mark_polled(
    conn_odds: sqlite3.Connection,
    source: str,
    subject: str,
    now_iso: str,
    poll_run_id: str | None = None,
) -> None:
    """记录一次真实采集尝试(成功与否都算,防止失败风暴打爆来源)。"""
    with tx(conn_odds):
        conn_odds.execute(
            """INSERT INTO poll_state (source, subject, last_polled_at, last_poll_run_id, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(source, subject) DO UPDATE SET
                 last_polled_at=excluded.last_polled_at,
                 last_poll_run_id=excluded.last_poll_run_id,
                 updated_at=excluded.updated_at""",
            (source, str(subject), now_iso, poll_run_id, utc_now_iso()),
        )


def upcoming_precise_matches(
    conn_core: sqlite3.Connection,
    now_iso: str,
    window_hours: int = WINDOW_HOURS,
) -> list[sqlite3.Row]:
    """未来 window_hours 内、开球时间通过统一精确性验证的 NotStarted 比赛(不限联赛)。

    SQL 先做粗筛(status/precision/source),窗口边界用 `julianday()` 比较而非裸文本范围
    ——裸文本比较对带时区偏移的合法 kickoff(如 '...-05:00' / '...+08:00')会给出错误
    结论,可能把本应在窗口内的比赛错误排除。再用 normalize_exact_kickoff 对每行做真正的
    精确性验证(拒绝 naive/非法/纯日期形状的午夜占位)——不再只判断字符串范围
    (CLAUDE.md §6.2.1)。SQLite 单机约万行,不需为性能保留错误的字符串比较。
    """
    hi = (_parse_iso(now_iso) + timedelta(hours=window_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn_core.execute(
        """SELECT Match_ID, League_ID, Date, kickoff_at_utc, kickoff_precision, kickoff_source,
                  status, Home_Team_ID, Away_Team_ID, Home_Team_Name, Away_Team_Name
           FROM dim_match
           WHERE status='NotStarted'
             AND kickoff_precision='exact'
             AND kickoff_source IS NOT NULL
             AND kickoff_at_utc IS NOT NULL
             AND julianday(kickoff_at_utc) > julianday(?)
             AND julianday(kickoff_at_utc) <= julianday(?)
           ORDER BY julianday(kickoff_at_utc), Match_ID""",
        (now_iso, hi),
    ).fetchall()
    return [
        r for r in rows
        if normalize_exact_kickoff(r["kickoff_at_utc"], r["kickoff_precision"], r["kickoff_source"])
        is not None
    ]
