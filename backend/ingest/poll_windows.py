"""采集窗口与到期判断(CLAUDE.md §6.3)。

三档节奏:
- **首次发现即采**:该 (source, subject) 在 poll_state 无任何记录时,不论距开球
  多远都立即采集一次(2026-08-11 新增,解决 T+7 新发现比赛在进入 72h 窗口前
  0% 覆盖的问题);小联赛可能要到赛前 4-5 天才真的有盘口,这一枪拿不到数据
  属正常,不是失败告警,72h 窗口内的第二档会自然重试;
- 距开球 2–72 小时:同一 (source, subject) 最小间隔 900s;
- 距开球 0–2 小时:最小间隔 300s。

只有通过统一验证器 normalize_exact_kickoff(kickoff_precision=='exact' ∧ 有非空真实
来源 ∧ 时间带显式时区可解析)、状态 NotStarted 的比赛才进入采集窗口——不再只判断
kickoff_at_utc 是否非空或字符串是否含 'T'。已开球不再属于赛前采集窗口(in-play
采集是显式的另一件事,本模块不伪装)。到期状态持久化于 odds.db 的 poll_state
(进程重启不得无界重复采集)。

Worker/systemd 每 5 分钟触发一次到期判断;真正是否请求数据源由本模块决定。
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend.db.connections import tx
from backend.db.util import normalize_exact_kickoff, utc_now_iso

WINDOW_HOURS = 72
NEAR_HOURS = 2
INTERVAL_FAR_SECONDS = 900    # 2–72h:15 分钟(CADENCE_LEGACY,§6.3 下限)
INTERVAL_NEAR_SECONDS = 300   # 0–2h:5 分钟

SOURCE_NOWGOAL_ODDS = "nowgoal_odds"
SOURCE_NOWGOAL_SCHEDULE = "nowgoal_schedule"
SOURCE_FOTMOB_SNAPSHOT = "fotmob_snapshot"
SOURCE_FOTMOB_FIXTURES = "fotmob_fixtures"       # T+7 赛程同步节流键(subject=league_id)
SOURCE_FOTMOB_INCREMENTAL = "fotmob_incremental"  # 完赛增量节流键(subject=league_id)

# T+7 赛程刷新间隔:每联赛 6 小时。复用 is_due/mark_polled 的 poll_state 节流,
# 使 15 分钟 chain 反复触发赛程 job 也不会真去多打 FotMob(16 联赛 × 4 次/天)。
FIXTURE_SYNC_INTERVAL_SECONDS = 21600
# 完赛增量(scheduler.step1)同一节流:每联赛 6 小时,替代旧"单联赛被 15 分钟
# chain 反复触发约 96 次/天"的行为(数据管道重建 Phase 6 去硬编码)。
INCREMENTAL_SYNC_INTERVAL_SECONDS = 21600


@dataclass(frozen=True)
class PollCadence:
    """按来源声明的分级采集节奏(CLAUDE.md §6.3:数值是下限,各来源可在其上分级)。

    tiers:升序 ((upper_hours, interval_seconds), ...),取第一个 upper_hours*3600 >= s 的间隔。
    last_call_seconds:临近开球强制补一枪的窗口(距开球 ≤ 此值且尚未在窗内轮询过 → 强制)。
    """
    name: str
    window_hours: int
    tiers: tuple[tuple[float, int], ...]
    last_call_seconds: int | None = None


# 旧两档:2–72h 每 15 分钟、0–2h 每 5 分钟。字节等价于历史行为,供 FotMob 快照/日程沿用。
CADENCE_LEGACY = PollCadence(
    "legacy", WINDOW_HOURS, ((NEAR_HOURS, INTERVAL_NEAR_SECONDS), (WINDOW_HOURS, INTERVAL_FAR_SECONDS)))

# NowGoal 赔率五段递进(数据管道重建 Phase 4,用户确认的节奏):
#   72/48/24/12h 检查点 → 12h起每小时 → 3h起每20分 → 1h起每10分 → T-15min last_call。
#   逐档 ≥ §6.3 下限(2–72h≥900s、0–2h≥300s),无需改 §6.3 数值。
CADENCE_NOWGOAL_ODDS = PollCadence(
    "nowgoal_odds", WINDOW_HOURS,
    tiers=((1, 600), (3, 1200), (12, 3600), (24, 43200), (48, 86400), (72, 86400)),
    last_call_seconds=900,
)

CADENCE_BY_SOURCE = {
    SOURCE_NOWGOAL_ODDS: CADENCE_NOWGOAL_ODDS,
    SOURCE_FOTMOB_SNAPSHOT: CADENCE_LEGACY,      # 阵容/伤停快照:节奏一字不改
    SOURCE_NOWGOAL_SCHEDULE: CADENCE_LEGACY,
}


def _tier_interval(cadence: PollCadence, seconds_to_kickoff: float) -> int:
    for upper_hours, interval in cadence.tiers:
        if seconds_to_kickoff <= upper_hours * 3600:
            return interval
    return cadence.tiers[-1][1]


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


@dataclass(frozen=True)
class PollDecision:
    due: bool
    tier: str | None       # 命中的档("le_1h"/"le_3h"/.../"checkpoint_72h"/"last_call"/None)
    reason: str


_NOT_DUE_OUT_OF_WINDOW = PollDecision(False, None, "out_of_window_or_kicked_off")


def poll_decision(
    source: str,
    kickoff_at_utc: str | None,
    kickoff_precision: str | None,
    kickoff_source: str | None,
    last_polled_at: str | None,
    now_iso: str,
) -> PollDecision:
    """按来源节奏(CADENCE_BY_SOURCE)判定是否到期,含临场 last_call 强制。

    重启安全:仅依据持久化的 last_polled_at 与 kickoff 重算,不加列、不存内存状态。
    同一 now_iso 重放:第一次 due 后 mark_polled 写入 last_polled_at,第二次必 not_due。
    """
    cadence = CADENCE_BY_SOURCE.get(source, CADENCE_LEGACY)
    normalized = normalize_exact_kickoff(kickoff_at_utc, kickoff_precision, kickoff_source)
    if normalized is None:
        return PollDecision(False, None, "no_exact_kickoff")
    kickoff = _parse_iso(normalized)
    s = (kickoff - _parse_iso(now_iso)).total_seconds()
    if s <= 0:
        return PollDecision(False, None, "kicked_off")          # 不做 in-play
    if s > cadence.window_hours * 3600:
        # T+7 首次发现即采(CLAUDE.md §6.3 三档之一):该 (source, subject) 在
        # poll_state 里还没有任何记录时,不论距开球多远都先采一次——用户确认
        # 的行为是"小联赛可能要到赛前 4-5 天才有盘口,没有就没有,但发现即采
        # 这一枪不能省"。只发生一次:mark_polled 落库后 last_polled_at 非空,
        # 之后仍然按常规窗口(2-72h/72h checkpoint 等)节流,不会无界重复采集。
        if last_polled_at is None:
            return PollDecision(True, "first_discovery", "first_discovery_out_of_window")
        return _NOT_DUE_OUT_OF_WINDOW

    interval = _tier_interval(cadence, s)
    tier = _tier_name(cadence, s)

    # last_call:跨进 T-15min 窗口而窗内尚未轮询过 → 强制补一枪(硬保证 FINAL ≤ 该窗口)
    lc = cadence.last_call_seconds
    if lc is not None and s <= lc:
        if last_polled_at is None:
            return PollDecision(True, "last_call", "last_call_never_polled")
        s_last = (kickoff - _parse_iso(last_polled_at)).total_seconds()
        if s_last > lc:
            return PollDecision(True, "last_call", "last_call_crossed_window")

    if last_polled_at is None:
        return PollDecision(True, tier, "first_poll")
    elapsed = (_parse_iso(now_iso) - _parse_iso(last_polled_at)).total_seconds()
    if elapsed >= interval:
        return PollDecision(True, tier, f"interval_{interval}s_elapsed")
    return PollDecision(False, tier, f"throttled_{interval}s")


def _tier_name(cadence: PollCadence, s: float) -> str:
    for upper_hours, _ in cadence.tiers:
        if s <= upper_hours * 3600:
            return f"le_{int(upper_hours)}h" if upper_hours < 4 else f"checkpoint_{int(upper_hours)}h"
    return "far"


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
    tier: str | None = None,
    ok: bool = True,
) -> None:
    """记录一次真实采集尝试(成功与否都算,防止失败风暴打爆来源)。

    同事务写两处:poll_state(最后一次,节流判定用)+ poll_attempt_log
    (append-only 全量,migration odds/0007)。后者是"节奏本身可验证"的唯一
    证据——hash-diff 落库意味着"没有新快照" ≠ "没有轮询",只有 attempt log
    能证明五段节流真的在跑。tier 记录命中的节流档(poll_decision 返回值),
    ok=False 表示该轮采集失败(照样计入节流,防失败风暴)。
    """
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
        conn_odds.execute(
            """INSERT INTO poll_attempt_log (source, subject, attempted_at, tier, ok, poll_run_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (source, str(subject), now_iso, tier, 1 if ok else 0, poll_run_id),
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
