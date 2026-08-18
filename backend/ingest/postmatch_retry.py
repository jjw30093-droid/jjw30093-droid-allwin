"""赛后完赛增量单比赛重试上限(PIPELINE_REDESIGN_V2 P4,3.3 安全要求)。

fotmob_incremental_multi 的到期条件从"6h/联赛"节流改为事件驱动
(league_stale_unresolved_match_ids:kickoff+2.5h 仍未 Finish)后,若某场
比赛的 status 永远不会被 FotMob 翻成 Finish(数据源本身缺陷,不是我们能修
的),该比赛会让所在联赛永久到期——每一轮 tick 都重新去打一次数据源。

本模块给每个 match_id 计数"检查过仍未解决"的次数,持久化在 odds.db 的
postmatch_retry_state(migration odds/0010,与同一 job 的 poll_state/
poll_attempt_log 同库,不另起第三处存放位置)。达到 MAX_ATTEMPTS 后:
  1. 停止对该比赛继续计数——调用方(fotmob_incremental_multi)应据此把它
     从"事件驱动到期"的判据里排除,不再"专为这一场"反复打数据源(联赛仍会
     被 6h 兜底档连带覆盖到);
  2. 记录 exhausted_at + fail_reason,不静默丢弃;
  3. 经 backend.notify 推一次 CRITICAL 告警,dedup_key 按 match_id 区分
     ——notify() 自带的 24h 去重保证同一场比赛不会每个 tick 都重推。

MAX_ATTEMPTS=20 的取值理由:allwin-postmatch 定时器每 30 分钟触发一次
(PIPELINE_REDESIGN_V2 P3),一场比赛进入"stale"(kickoff+2.5h 仍未 Finish)
状态后,若每次到期检查都计一次攻击,20 次 ≈ 10 小时——即放弃时点约为
kickoff+12.5h。这远超正常比赛+赛后数据处理所需时间(90 分钟比赛 + 合理
的数据源处理延迟,即使加时/点球大战/极端延误也很难达到这个量级),同时
把当前库里已经"卡住"长达数天的比赛(见本 phase 审计,最长 3.9 天)从
"永远到期"收敛为"一天以内必定告警并停止重试"。
"""

import sqlite3

from backend.db.connections import tx
from backend.db.util import utc_now_iso

MAX_ATTEMPTS = 20
ALERT_SOURCE = "postmatch_match_retry_exhausted"


def tracked_match_ids(conn_odds: sqlite3.Connection, league_id: int) -> set[int]:
    rows = conn_odds.execute(
        "SELECT match_id FROM postmatch_retry_state WHERE league_id=?", (league_id,)
    ).fetchall()
    return {int(r["match_id"]) for r in rows}


def is_exhausted(conn_odds: sqlite3.Connection, match_id: int) -> bool:
    row = conn_odds.execute(
        "SELECT 1 FROM postmatch_retry_state WHERE match_id=? AND exhausted_at IS NOT NULL",
        (match_id,),
    ).fetchone()
    return row is not None


def exhausted_match_ids(conn_odds: sqlite3.Connection, league_id: int | None = None) -> set[int]:
    if league_id is None:
        rows = conn_odds.execute(
            "SELECT match_id FROM postmatch_retry_state WHERE exhausted_at IS NOT NULL"
        ).fetchall()
    else:
        rows = conn_odds.execute(
            "SELECT match_id FROM postmatch_retry_state WHERE league_id=? AND exhausted_at IS NOT NULL",
            (league_id,),
        ).fetchall()
    return {int(r["match_id"]) for r in rows}


def record_attempt(conn_odds: sqlite3.Connection, match_id: int, league_id: int, now_iso: str) -> int:
    """本轮该比赛仍未解决 → attempts+1(首次插入 attempts=1)。返回新的 attempts。"""
    with tx(conn_odds):
        conn_odds.execute(
            """INSERT INTO postmatch_retry_state
                 (match_id, league_id, attempts, first_attempt_at, last_attempt_at)
               VALUES (?, ?, 1, ?, ?)
               ON CONFLICT(match_id) DO UPDATE SET
                 attempts=attempts+1, last_attempt_at=excluded.last_attempt_at,
                 league_id=excluded.league_id""",
            (match_id, league_id, now_iso, now_iso),
        )
        row = conn_odds.execute(
            "SELECT attempts FROM postmatch_retry_state WHERE match_id=?", (match_id,)
        ).fetchone()
    return int(row["attempts"])


def clear_retry_state(conn_odds: sqlite3.Connection, match_id: int) -> None:
    """比赛已解决(不再处于 stale 集合)→ 清空该比赛的重试计数,不留痕迹继续
    占用 exhausted 判定;若它以后又意外重新进入 stale(不应发生,Finish 是
    终态),应该从头计数,不沿用旧计数。"""
    with tx(conn_odds):
        conn_odds.execute("DELETE FROM postmatch_retry_state WHERE match_id=?", (match_id,))


def mark_exhausted_and_alert(
    conn_odds: sqlite3.Connection, match_id: int, league_id: int, attempts: int, now_iso: str
) -> dict:
    """原子声明"我是第一个把这场比赛标记为耗尽的调用者"——不能靠 notify() 的
    24h dedup 保证恰好一次:dedup 先看 NOTIFY_ENABLED(测试/降级场景直接跳过
    dedup 判定),即便真实推送也要等对方的 ServerChan 网络调用(~10s)完成、
    notify_result='sent' 落库后才生效,窗口内两个几乎同时判定"已耗尽"的
    调用者(如 systemd 定时调用与旁路直跑 fotmob_incremental_multi.py 的
    main(),后者不经过 runner._acquire_lock)都可能通过对方前置检查。用
    `WHERE exhausted_at IS NULL` 的条件 UPDATE + rowcount 判断谁先声明成功,
    只有声明成功的那个调用者才真正调用 notify()。
    """
    reason = (
        f"match_id={match_id} 连续 {attempts} 次到期检查后仍未从 status 翻转为 Finish,"
        f"已停止自动重试(上限 {MAX_ATTEMPTS} 次),需要人工核实数据源状态"
    )
    with tx(conn_odds):
        cur = conn_odds.execute(
            "UPDATE postmatch_retry_state SET exhausted_at=?, fail_reason=?"
            " WHERE match_id=? AND exhausted_at IS NULL",
            (now_iso, reason, match_id),
        )
        claimed = cur.rowcount == 1
    if not claimed:
        return {"persisted": False, "notified": False, "result": "already_claimed", "alert_id": None}

    from backend import notify as notify_mod

    return notify_mod.notify(
        level="CRITICAL",
        source=ALERT_SOURCE,
        title=f"赛后完赛增量重试耗尽: match_id={match_id}",
        body=f"league_id={league_id}\nmatch_id={match_id}\nattempts={attempts}\n{reason}",
        dedup_key=f"{ALERT_SOURCE}:{match_id}",
    )


def process_match_attempt(
    conn_odds: sqlite3.Connection, match_id: int, league_id: int, now_iso: str
) -> dict:
    """一次"仍未解决"检查的入口:已耗尽的比赛直接跳过(不再计数、不再告警);
    否则计数 +1,首次达到 MAX_ATTEMPTS 时标记 exhausted 并告警恰好一次。
    调用方(fotmob_incremental_multi)应只对当前判定为 stale 的比赛调用本函数。
    """
    now_iso = now_iso or utc_now_iso()
    if is_exhausted(conn_odds, match_id):
        return {"attempts": None, "exhausted": True, "newly_exhausted": False}
    attempts = record_attempt(conn_odds, match_id, league_id, now_iso)
    newly_exhausted = attempts >= MAX_ATTEMPTS
    if newly_exhausted:
        mark_exhausted_and_alert(conn_odds, match_id, league_id, attempts, now_iso)
    return {"attempts": attempts, "exhausted": newly_exhausted, "newly_exhausted": newly_exhausted}
