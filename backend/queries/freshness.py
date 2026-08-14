"""首页「今日更新状态」聚合查询(2026-08-11)。

只读三张已有表的最近成功时间戳,不新增采集、不新增写路径:
- 赛程:fixture_sync_ledger(odds.db)最近一次结论性成功(written/off_season,
  即拿到了来源的确定性答案;fetch_failed/refused_* 不算"更新",因为没有
  真正拿到新数据);
- 赔率:bronze_ng_odds_snap(odds.db)最近一次观测(observed_at);
- 推荐:reco_slips(platform.db)最近一次发布(published_at,非 draft)。

任一为 NULL 如实返回 NULL(如实展示"尚无成功记录"),不用当前时间或
上次页面渲染时间顶替。
"""

import sqlite3

_SCHEDULE_OK_VERDICTS = ("written", "off_season")


def latest_schedule_sync(conn_odds: sqlite3.Connection) -> str | None:
    row = conn_odds.execute(
        f"SELECT MAX(run_at) FROM fixture_sync_ledger"
        f" WHERE verdict IN ({','.join('?' for _ in _SCHEDULE_OK_VERDICTS)})",
        _SCHEDULE_OK_VERDICTS,
    ).fetchone()
    return row[0] if row else None


def latest_odds_observation(conn_odds: sqlite3.Connection) -> str | None:
    row = conn_odds.execute("SELECT MAX(observed_at) FROM bronze_ng_odds_snap").fetchone()
    return row[0] if row else None


def latest_reco_publish(conn_platform: sqlite3.Connection) -> str | None:
    row = conn_platform.execute(
        "SELECT MAX(published_at) FROM reco_slips WHERE status != 'draft'"
    ).fetchone()
    return row[0] if row else None
