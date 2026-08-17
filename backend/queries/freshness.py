"""首页「今日更新状态」聚合查询(2026-08-11)。

只读三张已有表的最近成功时间戳,不新增采集、不新增写路径:
- 赛程:fixture_sync_ledger(odds.db)最近一次结论性成功(written/off_season,
  即拿到了来源的确定性答案;fetch_failed/refused_* 不算"更新",因为没有
  真正拿到新数据);
- 赔率:bronze_ng_odds_snap(odds.db)最近一次观测(observed_at);
- 推荐:reco_slips(platform.db)最近一次发布(published_at,非 draft)。

任一为 NULL 如实返回 NULL(如实展示"尚无成功记录"),不用当前时间或
上次页面渲染时间顶替。

2026-08-16 首页粗糙度修复:只有裸时间戳(HH:mm)用户判断不出"这是今天还是
好几天前",也看不出某个数据源当前是不是已经失败。classify_freshness() 给
每个时间戳配一个仓库统一的 FRESH/STALE/UNAVAILABLE 三态(与
backend/content_status.py::project_freshness、backend/queries/odds.py::
classify_odds_freshness、routes_public.py 的 sync_state 同一套值),供路由层
拼进响应。三个信号统一复用 backend/cli/ops_check.py 的 SOURCE_STALE_HOURS=6
(与 backend/queries/odds.py::ODDS_FRESHNESS_STALE_HOURS 同一出处、同一个数字),
不为其中某个信号另外发明新阈值——三者都是"数据源多久没更新算异常"这同一个
问题,拆成不同数字反而制造出没有依据的差异。backend/queries 是查询层,不
反向依赖 backend/cli(CLI 层),因此复制常量值而不 import。
"""

import sqlite3
from datetime import datetime, timedelta, timezone

_SCHEDULE_OK_VERDICTS = ("written", "off_season")

FRESHNESS_STALE_HOURS = 6


def classify_freshness(timestamp: str | None, *, now: datetime | None = None) -> str:
    """把一个 ISO8601 时间戳分类为仓库统一的 FRESH/STALE/UNAVAILABLE 三态。

    None(尚无成功记录)-> "UNAVAILABLE";否则按 (now - timestamp) 是否超过
    FRESHNESS_STALE_HOURS 分 FRESH/STALE。`now` 仅供测试注入固定时间,生产
    路径用真实当前时间。
    """
    if not timestamp:
        return "UNAVAILABLE"
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return "UNAVAILABLE"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if current - parsed > timedelta(hours=FRESHNESS_STALE_HOURS):
        return "STALE"
    return "FRESH"


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
